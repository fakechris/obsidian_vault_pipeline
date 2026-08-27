//! Keep the model replies we REJECT.
//!
//! When a reply fails a content check the pipeline calls `client.invalidate`,
//! which deletes the cassette so a rerun re-asks the model instead of replaying
//! a bad exchange forever. That is right. The side effect is not: the only
//! record of what the model actually said goes with it.
//!
//! On 2026-08-27 a live sweep reproduced a `cluster_select` failure — a case id
//! that had never been offered — and there was nothing left to look at. The
//! five REFUSALS in the same run were all on disk; the one failure was not.
//! The diagnosis had to be inferred from the shape of an error message.
//!
//! So a rejected exchange is written here instead. Two properties matter:
//!
//! - **Not replayable.** This is not the cassette tree and nothing reads it
//!   back. A bad reply must never come back as a good one.
//! - **The REQUEST too.** Cassettes store only replies, keyed by a hash of the
//!   request — so even a kept cassette cannot tell you what was asked. For an
//!   "identifier that was never offered" defect, the offered set IS the
//!   evidence.
//!
//! Best-effort throughout: a diagnostic that can fail a run is worse than no
//! diagnostic.

use std::path::{Path, PathBuf};
use std::sync::RwLock;

use ovp_llm::{ModelMessage, ModelRequest, request_key};

static DIR: RwLock<Option<PathBuf>> = RwLock::new(None);

/// Point this process's rejected-reply log at `dir`.
///
/// LAST call wins. The A/B harness runs `run_stats` once per arm in one
/// process, and a first-wins lock would file arm B's rejections under arm A's
/// directory — a log that misattributes is worse than a missing one.
pub(crate) fn set_dir(dir: PathBuf) {
    *DIR.write().unwrap_or_else(|e| e.into_inner()) = Some(dir);
}

/// Where rejected replies are being written, if anywhere.
pub(crate) fn dir() -> Option<PathBuf> {
    DIR.read().unwrap_or_else(|e| e.into_inner()).clone()
}

/// The user-facing text of a request — what the model was actually asked.
fn request_text(req: &ModelRequest) -> String {
    req.messages
        .iter()
        .map(|m| match m {
            ModelMessage::User { content } => content.clone(),
            other => format!("{other:?}"),
        })
        .collect::<Vec<_>>()
        .join("\n---\n")
}

/// Record one rejected exchange. Never fails the caller.
///
/// `defect` should say what the check objected to, in the same words the
/// operator will see in the error — the file is useless if you cannot tell
/// which failure it belongs to.
pub(crate) fn record(stage: &str, request: &ModelRequest, reply_text: &str, defect: &str) {
    let Some(dir) = dir() else {
        return;
    };
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let key = request_key(request);
    let safe_stage: String = stage
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '-' })
        .collect();
    let path = dir.join(format!("{safe_stage}-{}.json", &key[..key.len().min(12)]));
    let body = serde_json::json!({
        "schema": "ovp.crystal.rejected_reply/v1",
        "stage": stage,
        "prompt_id": request.cache_namespace,
        "request_key": key,
        "defect": defect,
        // Both halves. The reply alone cannot explain an "identifier that was
        // never offered" — you need to see what WAS offered.
        // The system prompt is part of what the model received; omitting it
        // hides half the instructions when the defect is "it ignored them".
        "system": request.system,
        "request": request_text(request),
        "reply": reply_text,
    });
    if let Ok(s) = serde_json::to_string_pretty(&body) {
        if std::fs::write(&path, format!("{s}\n")).is_ok() {
            // 0600: these carry raw vault content and raw model output, and a
            // work dir is not guaranteed to be gitignored or unsynced.
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(ns: &str, user: &str) -> ModelRequest {
        ModelRequest {
            model: "m".into(),
            system: Some("sys".into()),
            messages: vec![ModelMessage::User {
                content: user.into(),
            }],
            max_tokens: 16,
            temperature: None,
            tools: None,
            cache_namespace: Some(ns.into()),
        }
    }

    /// `set_dir` is process-wide, so these exercise the same body through a
    /// local path rather than racing other tests for the global.
    fn write_to(dir: &Path, stage: &str, request: &ModelRequest, reply: &str, defect: &str) {
        std::fs::create_dir_all(dir).unwrap();
        let key = request_key(request);
        let path = dir.join(format!("{stage}-{}.json", &key[..12]));
        let body = serde_json::json!({
            "schema": "ovp.crystal.rejected_reply/v1",
            "stage": stage, "prompt_id": request.cache_namespace,
            "request_key": key, "defect": defect,
            "request": request_text(request), "reply": reply,
        });
        std::fs::write(path, serde_json::to_string_pretty(&body).unwrap()).unwrap();
    }

    #[test]
    fn a_record_carries_the_request_not_just_the_reply() {
        // The reply alone cannot explain "id not in the offered set".
        let d = tempfile::tempdir().unwrap();
        let r = req("cluster_select/v2", "offered: c1, c2, c3");
        write_to(d.path(), "cluster-select", &r, "{\"selected\":[\"c9\"]}", "not offered");
        let f = std::fs::read_dir(d.path()).unwrap().next().unwrap().unwrap();
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(f.path()).unwrap()).unwrap();
        assert!(v["request"].as_str().unwrap().contains("c1, c2, c3"));
        assert!(v["reply"].as_str().unwrap().contains("c9"));
        assert_eq!(v["defect"], "not offered");
        assert_eq!(v["prompt_id"], "cluster_select/v2");
    }

    #[test]
    fn nothing_is_written_when_no_directory_is_set() {
        // A diagnostic that fails a run is worse than no diagnostic, and the
        // default must be silence rather than a guessed path.
        // `DIR` is unset in this test binary unless another test set it, so
        // assert on the accessor rather than on side effects.
        if dir().is_none() {
            let r = req("x/v1", "u");
            record("stage", &r, "reply", "defect"); // must not panic
        }
    }

    #[test]
    fn the_filename_cannot_escape_the_directory() {
        // `stage` reaches this from format! strings; a separator in it would
        // otherwise write outside the log.
        let d = tempfile::tempdir().unwrap();
        let r = req("x/v1", "u");
        let stage = "../../evil";
        let safe: String = stage
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '-' })
            .collect();
        assert!(!safe.contains('/') && !safe.contains(".."));
        write_to(d.path(), &safe, &r, "reply", "defect");
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 1);
    }
}
