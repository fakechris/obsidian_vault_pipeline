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

/// How many rejected exchanges to keep, newest first.
///
/// A bound against unbounded growth, NOT a retention policy: these are for
/// diagnosing a failure you are looking at now, and their value drops off
/// fast. A failing run writes a handful, so this holds many runs of history
/// while stopping a pathological loop from filling the disk with vault text.
const KEEP: usize = 200;

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
    // Collision-safe: a second rejection of the SAME stage+request is a
    // second data point, not a correction of the first. Overwriting cost us
    // a real reply once already.
    let stem = format!("{safe_stage}-{}", &key[..key.len().min(12)]);
    let mut path = dir.join(format!("{stem}.json"));
    for n in 1..1000 {
        if !path.exists() {
            break;
        }
        path = dir.join(format!("{stem}-{n}.json"));
    }
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
            prune(&dir);
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

/// Which records to drop, oldest first, keeping the newest `keep`.
///
/// Pure so the test does not depend on filesystem timestamp resolution —
/// a cleanup test that races the clock is a flake waiting to happen.
/// Ordered by mtime rather than by name: the name carries a request hash,
/// which says nothing about age.
fn victims(mut files: Vec<(std::time::SystemTime, PathBuf)>, keep: usize) -> Vec<PathBuf> {
    if files.len() <= keep {
        return Vec::new();
    }
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let drop_n = files.len() - keep;
    files.into_iter().take(drop_n).map(|(_, p)| p).collect()
}

/// Drop the oldest records beyond [`KEEP`]. Best-effort: a log that cannot
/// tidy itself is still a useful log.
fn prune(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let files: Vec<(std::time::SystemTime, PathBuf)> = entries
        .filter_map(|e| e.ok())
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| {
            let m = e.metadata().ok()?.modified().ok()?;
            Some((m, e.path()))
        })
        .collect();
    for path in victims(files, KEEP) {
        let _ = std::fs::remove_file(path);
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
    fn a_second_rejection_does_not_overwrite_the_first() {
        // This is not hypothetical. A caller recorded a repair failure with an
        // empty reply after `call_and_parse` had already recorded the same
        // stage+request WITH the real reply — and the empty one won, deleting
        // the evidence inside the change that exists to keep it.
        let d = tempfile::tempdir().unwrap();
        let r = req("x/v1", "u");
        let key = request_key(&r);
        let stem = format!("s-{}", &key[..12]);
        std::fs::write(d.path().join(format!("{stem}.json")), "{\"reply\":\"the real one\"}")
            .unwrap();
        // Second record must pick a fresh name.
        let mut path = d.path().join(format!("{stem}.json"));
        for n in 1..1000 {
            if !path.exists() {
                break;
            }
            path = d.path().join(format!("{stem}-{n}.json"));
        }
        std::fs::write(&path, "{}").unwrap();
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 2);
        let first =
            std::fs::read_to_string(d.path().join(format!("{stem}.json"))).unwrap();
        assert!(first.contains("the real one"), "the first record survived");
    }

    fn at(secs: u64, name: &str) -> (std::time::SystemTime, PathBuf) {
        (
            std::time::SystemTime::UNIX_EPOCH + std::time::Duration::from_secs(secs),
            PathBuf::from(name),
        )
    }

    #[test]
    fn pruning_drops_the_oldest_and_keeps_the_newest() {
        // Deliberately out of name order: age comes from mtime, and the name
        // is a request hash that says nothing about it.
        let files = vec![at(30, "c.json"), at(10, "a.json"), at(20, "b.json")];
        assert_eq!(victims(files, 2), vec![PathBuf::from("a.json")]);
    }

    #[test]
    fn pruning_leaves_a_small_directory_alone() {
        // The common case is a handful of files; churning them would be noise.
        let files = vec![at(10, "a.json"), at(20, "b.json")];
        assert!(victims(files.clone(), KEEP).is_empty());
        assert!(victims(files, 2).is_empty(), "exactly at the cap keeps everything");
    }

    #[test]
    fn the_cap_is_actually_enforced_on_disk() {
        let d = tempfile::tempdir().unwrap();
        for i in 0..(KEEP + 5) {
            std::fs::write(d.path().join(format!("s-{i:04}.json")), "{}").unwrap();
        }
        prune(d.path());
        let n = std::fs::read_dir(d.path()).unwrap().count();
        assert!(n <= KEEP, "{n} files left, cap is {KEEP}");
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
