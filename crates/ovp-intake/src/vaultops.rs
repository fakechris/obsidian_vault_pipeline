//! Shared vault-state primitives for the M31 product loop: append-only JSONL
//! ledgers, the OVP_RULES write-log event, content hashing, and the
//! non-destructive `safe_move` used by every lifecycle transition.
//!
//! Invariants:
//! - Ledgers are append-only; a malformed line is a HARD error (authoritative
//!   state — silently skipping a line could re-run, and re-bill, everything it
//!   covered).
//! - `safe_move` never deletes and never overwrites: a name collision gets a
//!   numeric suffix, per OVP_RULES ("never delete; never overwrite").

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// sha256 of raw bytes as lowercase hex — the content-dedup identity used by
/// intake and the daily loop alike.
pub fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    for b in digest {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Append one serialized record as a JSONL line (creating parent dirs on
/// first use), flushed before returning.
pub fn append_jsonl<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    let line = serde_json::to_string(value).map_err(|e| format!("serializing record: {e}"))?;
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("opening {}: {e}", path.display()))?;
    writeln!(f, "{line}").map_err(|e| format!("appending to {}: {e}", path.display()))?;
    f.flush().map_err(|e| format!("flushing {}: {e}", path.display()))
}

/// Read a whole JSONL ledger. Missing file → empty (first run); a malformed
/// line is a hard error naming the line.
pub fn read_jsonl<T: DeserializeOwned>(path: &Path) -> Result<Vec<T>, String> {
    let raw = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    let mut records = Vec::new();
    for (i, line) in raw.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let rec: T = serde_json::from_str(line).map_err(|e| {
            format!("ledger {} line {}: malformed record: {e}", path.display(), i + 1)
        })?;
        records.push(rec);
    }
    Ok(records)
}

/// One `OVP_RULES.md` write-log event for `60-Logs/pipeline.jsonl`. The key is
/// `event_type` to match the legacy events already in that file, so vault-wide
/// queries on `.event_type` cover both generations.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PipelineLogEvent {
    pub event_type: String,
    pub target: String,
    pub reason: String,
    pub date: String,
    pub run_id: String,
}

/// Append one write-log event to the vault's pipeline log.
pub fn append_pipeline_event(path: &Path, event: &PipelineLogEvent) -> Result<(), String> {
    append_jsonl(path, event)
}

/// Move a file without ever overwriting: parents are created, and an existing
/// target name gets ` -2`, ` -3`, … suffixes (before the extension). Returns
/// the path actually written. Deliberately `fs::rename`-only — the vault is
/// one filesystem, and a cross-device copy+delete would violate the "never
/// delete" posture on failure.
pub fn safe_move(from: &Path, to: &Path) -> Result<PathBuf, String> {
    let target = collision_free(to)?;
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    std::fs::rename(from, &target).map_err(|e| {
        format!("moving {} -> {}: {e}", from.display(), target.display())
    })?;
    Ok(target)
}

/// Write a NEW file without ever overwriting (collision → numeric suffix).
/// Returns the path actually written.
pub fn write_new(to: &Path, contents: &str) -> Result<PathBuf, String> {
    let target = collision_free(to)?;
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    std::fs::write(&target, contents)
        .map_err(|e| format!("writing {}: {e}", target.display()))?;
    Ok(target)
}

fn collision_free(to: &Path) -> Result<PathBuf, String> {
    if !to.exists() {
        return Ok(to.to_path_buf());
    }
    let stem = to.file_stem().and_then(|s| s.to_str()).unwrap_or("file");
    let ext = to.extension().and_then(|s| s.to_str());
    for n in 2..100 {
        let name = match ext {
            Some(e) => format!("{stem} -{n}.{e}"),
            None => format!("{stem} -{n}"),
        };
        let candidate = to.with_file_name(name);
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err(format!("could not find a collision-free name for {}", to.display()))
}

/// Vault-relative display path: strip `root` when `p` is under it, else the
/// full path.
pub fn rel_to(root: &Path, p: &Path) -> String {
    p.strip_prefix(root)
        .map(|q| q.to_string_lossy().into_owned())
        .unwrap_or_else(|_| p.to_string_lossy().into_owned())
}

/// Single-writer guard for the vault's product state. Two overlapping runs
/// (cron + manual) would double-spend LLM calls, append duplicate records,
/// and race `safe_move`'s check-then-rename — so every mutating command takes
/// this lock first. `create_new` is atomic; the file holds the PID for
/// diagnosis. Released on drop; a crash leaves a stale lock that the error
/// message tells the operator how to clear.
#[derive(Debug)]
pub struct RunLock {
    path: PathBuf,
}

impl RunLock {
    pub fn acquire(vault_root: &Path) -> Result<Self, String> {
        let path = vault_root.join(".ovp/run.lock");
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("creating {}: {e}", parent.display()))?;
        }
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                let _ = writeln!(f, "{}", std::process::id());
                Ok(Self { path })
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Err(format!(
                "another OVP run appears to be in progress (lock file {}); \
                 if no run is active, delete the lock file and retry",
                path.display()
            )),
            Err(e) => Err(format!("acquiring {}: {e}", path.display())),
        }
    }
}

impl Drop for RunLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_sha256_is_stable() {
        assert_eq!(
            hex_sha256(b"already processed"),
            hex_sha256(b"already processed"),
        );
        assert_eq!(hex_sha256(b"x").len(), 64);
    }

    #[test]
    fn safe_move_never_overwrites() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.md");
        let b = dir.path().join("sub/target.md");
        std::fs::write(&a, "one").unwrap();
        let first = safe_move(&a, &b).unwrap();
        assert_eq!(first, b);

        std::fs::write(&a, "two").unwrap();
        let second = safe_move(&a, &b).unwrap();
        assert_eq!(second, dir.path().join("sub/target -2.md"));
        assert_eq!(std::fs::read_to_string(&b).unwrap(), "one", "original untouched");
        assert_eq!(std::fs::read_to_string(&second).unwrap(), "two");
    }

    #[test]
    fn write_new_suffixes_on_collision() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("note.md");
        assert_eq!(write_new(&p, "a").unwrap(), p);
        let q = write_new(&p, "b").unwrap();
        assert_eq!(q, dir.path().join("note -2.md"));
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "a");
    }

    #[test]
    fn run_lock_excludes_and_releases() {
        let dir = tempfile::tempdir().unwrap();
        let lock = RunLock::acquire(dir.path()).expect("first lock");
        let err = RunLock::acquire(dir.path()).expect_err("second lock must fail");
        assert!(err.contains("run.lock"), "got: {err}");
        drop(lock);
        let _again = RunLock::acquire(dir.path()).expect("released on drop");
    }

    #[test]
    fn jsonl_round_trip_and_malformed_line() {
        #[derive(Serialize, Deserialize, PartialEq, Debug)]
        struct R {
            x: u32,
        }
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("l.jsonl");
        append_jsonl(&path, &R { x: 1 }).unwrap();
        append_jsonl(&path, &R { x: 2 }).unwrap();
        let got: Vec<R> = read_jsonl(&path).unwrap();
        assert_eq!(got, vec![R { x: 1 }, R { x: 2 }]);

        std::fs::write(&path, "{bad}\n").unwrap();
        let err = read_jsonl::<R>(&path).unwrap_err();
        assert!(err.contains("line 1"), "got: {err}");
    }
}
