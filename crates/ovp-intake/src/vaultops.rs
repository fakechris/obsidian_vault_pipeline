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
/// this lock first. `create_new` is atomic; the file holds the owning PID.
/// Released on drop; a lock stranded by a crash (Drop never ran) is reclaimed
/// automatically once the owning process is verifiably gone.
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
        match Self::try_create(&path) {
            Ok(lock) => Ok(lock),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                // Stale-lock recovery: release is Drop-only, so a crash or
                // Ctrl-C (default SIGINT runs no destructors) strands the lock
                // and would block every later run until manual deletion. If
                // the recorded owner is verifiably dead, reclaim it — but ONLY
                // under the exclusive reclaim guard: every deletion of
                // run.lock happens with the guard held and after re-checking
                // staleness, so a racer that already re-created a fresh lock
                // can never have it deleted out from under it (the other
                // racer loses the guard and takes the in-progress error).
                if Self::owner_is_dead(&path) {
                    if let Some(lock) = Self::reclaim_under_guard(&path) {
                        return Ok(lock);
                    }
                }
                Err(format!(
                    "another OVP run appears to be in progress (lock file {}); \
                     if no run is active, delete the lock file and retry",
                    path.display()
                ))
            }
            Err(e) => Err(format!("acquiring {}: {e}", path.display())),
        }
    }

    /// One atomic `create_new` attempt, stamping this process's PID.
    fn try_create(path: &Path) -> std::io::Result<Self> {
        let mut f = OpenOptions::new().write(true).create_new(true).open(path)?;
        let _ = writeln!(f, "{}", std::process::id());
        Ok(Self { path: path.to_path_buf() })
    }

    /// Reclaim a stale lock while holding the exclusive reclaim guard. Returns
    /// `None` when the guard is contested or the lock turned out to be fresh
    /// on the re-check — the caller falls back to the in-progress error.
    fn reclaim_under_guard(path: &Path) -> Option<Self> {
        let guard = path.with_extension("lock.reclaim");
        if !Self::claim_guard(&guard) {
            return None;
        }
        // Re-check under the guard: the stale lock may have been reclaimed
        // and replaced by a live owner between our probe and winning the guard.
        let lock = if Self::owner_is_dead(path) {
            eprintln!(
                "ovp: reclaiming stale run lock {} (owning process is gone)",
                path.display()
            );
            let _ = std::fs::remove_file(path);
            Self::try_create(path).ok()
        } else {
            None
        };
        let _ = std::fs::remove_file(&guard);
        lock
    }

    /// Atomically claim the reclaim guard (PID-stamped like the lock). A guard
    /// stranded by a crash mid-reclaim is itself reclaimed by the same
    /// dead-owner rule: removal + ONE `create_new` retry — the create is
    /// atomic, so exactly one racer wins it.
    fn claim_guard(guard: &Path) -> bool {
        fn stamp(mut f: std::fs::File) -> bool {
            let _ = writeln!(f, "{}", std::process::id());
            true
        }
        match OpenOptions::new().write(true).create_new(true).open(guard) {
            Ok(f) => stamp(f),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                if !Self::owner_is_dead(guard) {
                    return false;
                }
                let _ = std::fs::remove_file(guard);
                match OpenOptions::new().write(true).create_new(true).open(guard) {
                    Ok(f) => stamp(f),
                    Err(_) => false,
                }
            }
            Err(_) => false,
        }
    }

    /// True only when the lock file names a PID that is verifiably no longer
    /// running. Conservative on every uncertainty (unreadable file, no PID,
    /// probe failure, non-unix): treat the owner as alive and keep refusing —
    /// the manual-deletion instruction in the error still applies.
    fn owner_is_dead(path: &Path) -> bool {
        let Some(pid) = std::fs::read_to_string(path)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
            .filter(|p| *p > 0)
        else {
            return false;
        };
        #[cfg(unix)]
        {
            // `kill -0` probes liveness without signaling; exit 0 = alive.
            // (A live process owned by another user also reports non-zero,
            // but a vault lock under $HOME is always same-user.)
            std::process::Command::new("kill")
                .arg("-0")
                .arg(pid.to_string())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .map(|s| !s.success())
                .unwrap_or(false)
        }
        #[cfg(not(unix))]
        {
            false
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

    #[cfg(unix)]
    #[test]
    fn run_lock_reclaims_stale_lock_from_dead_process() {
        let dir = tempfile::tempdir().unwrap();
        // Fabricate a crash: a lock file naming a process that has exited.
        let mut child = std::process::Command::new("true").spawn().unwrap();
        let dead_pid = child.id();
        child.wait().unwrap();
        let lock_path = dir.path().join(".ovp/run.lock");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        std::fs::write(&lock_path, format!("{dead_pid}\n")).unwrap();

        let lock = RunLock::acquire(dir.path()).expect("stale lock is reclaimed");
        assert!(
            !lock_path.with_extension("lock.reclaim").exists(),
            "reclaim guard is cleaned up"
        );
        drop(lock);
        assert!(!lock_path.exists(), "reclaimed lock still releases on drop");
    }

    #[cfg(unix)]
    #[test]
    fn run_lock_recovers_from_a_stranded_reclaim_guard() {
        // Crash DURING a previous reclaim: both the lock and the reclaim
        // guard are stranded with dead owners. Acquire must recover through
        // both layers (guard reclaimed by the same dead-owner rule).
        let dir = tempfile::tempdir().unwrap();
        let mut child = std::process::Command::new("true").spawn().unwrap();
        let dead_pid = child.id();
        child.wait().unwrap();
        let lock_path = dir.path().join(".ovp/run.lock");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        std::fs::write(&lock_path, format!("{dead_pid}\n")).unwrap();
        std::fs::write(lock_path.with_extension("lock.reclaim"), format!("{dead_pid}\n")).unwrap();

        let _lock = RunLock::acquire(dir.path()).expect("recovers through stale lock AND guard");
        assert!(!lock_path.with_extension("lock.reclaim").exists(), "guard cleaned up");
    }

    #[test]
    fn run_lock_refuses_live_owner_and_unreadable_pid() {
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join(".ovp/run.lock");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        // Owner alive (this very process) → refuse.
        std::fs::write(&lock_path, format!("{}\n", std::process::id())).unwrap();
        RunLock::acquire(dir.path()).expect_err("live owner must refuse");
        // Garbage content → conservative: refuse, never reclaim blindly.
        std::fs::write(&lock_path, "not-a-pid\n").unwrap();
        RunLock::acquire(dir.path()).expect_err("unreadable owner must refuse");
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
