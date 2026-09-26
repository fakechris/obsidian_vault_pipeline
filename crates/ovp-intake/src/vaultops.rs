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

/// `create_dir_all` with durability: after creating, fsync the parent of
/// every directory this call actually created. Syncing a file (or a
/// directory's contents) does not persist the *entry* naming it in its
/// parent — a power loss can otherwise erase a freshly created `.ovp/` chain
/// even though the write inside it returned `Ok`. The missing suffix is
/// collected BEFORE creation so only genuinely new entries pay an fsync.
pub fn create_dirs_synced(dir: &Path) -> Result<(), String> {
    // An empty path means the current directory (a bare relative filename's
    // `parent()` is `Some("")`) — `exists()`/`File::open` both fail on "".
    let dir = if dir.as_os_str().is_empty() { Path::new(".") } else { dir };
    let mut missing: Vec<PathBuf> = Vec::new();
    let mut probe = dir.to_path_buf();
    while !probe.exists() {
        missing.push(probe.clone());
        match probe.parent() {
            Some(p) if !p.as_os_str().is_empty() => probe = p.to_path_buf(),
            _ => break,
        }
    }
    std::fs::create_dir_all(dir).map_err(|e| format!("creating {}: {e}", dir.display()))?;
    for created in &missing {
        // A relative chain's top-level component has the EMPTY path as its
        // parent — `File::open("")` fails after the dirs already exist,
        // turning a successful create into a spurious error. Empty means
        // the current directory.
        let parent = match created.parent() {
            Some(p) if !p.as_os_str().is_empty() => p,
            _ => Path::new("."),
        };
        sync_dir(parent)?;
    }
    Ok(())
}

/// Append one serialized record as a JSONL line (creating parent dirs on
/// first use), fsynced before returning. `flush()` on a bare `File` is a
/// no-op — every ledger goes through here, and without `sync_data` a power
/// loss can drop the tail of any of them (intake dedup state, daily attempts,
/// the crystal ledger). When this append CREATES the ledger, the parent
/// directory is fsynced too: syncing file contents does not persist the new
/// directory entry, and losing it would silently erase the whole ledger the
/// caller was just told is durable. One fsync per appended record is cheap
/// at ledger write rates.
pub fn append_jsonl<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let parent = ledger_parent(path);
    create_dirs_synced(parent)?;
    let line = serde_json::to_string(value).map_err(|e| format!("serializing record: {e}"))?;
    // Creation is derived from the ATOMIC open, not an exists() probe — a
    // concurrent creator between probe and open would otherwise skip the
    // directory fsync exactly when a new entry needed it (TOCTOU).
    let (mut f, created) = match OpenOptions::new().create_new(true).append(true).open(path) {
        Ok(f) => (f, true),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            let f = OpenOptions::new()
                .append(true)
                .open(path)
                .map_err(|e| format!("opening {}: {e}", path.display()))?;
            (f, false)
        }
        Err(e) => return Err(format!("opening {}: {e}", path.display())),
    };
    ovp_domain::jsonl::append_line(&mut f, path, &line)
        .map_err(|e| format!("appending to {}: {e}", path.display()))?;
    f.sync_data().map_err(|e| format!("syncing {}: {e}", path.display()))?;
    if created {
        sync_dir(parent)?;
    }
    Ok(())
}

/// Persist directory-entry metadata where the host exposes directory fsync.
///
/// Windows rejects `File::open(directory)` with `ERROR_ACCESS_DENIED`, and its
/// file APIs do not expose the Unix directory-fsync durability contract. File
/// contents are still flushed before this helper is reached; skipping only
/// the unsupported directory metadata flush keeps atomic writes usable there.
pub fn sync_dir(dir: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        std::fs::File::open(dir)
            .and_then(|d| d.sync_all())
            .map_err(|e| format!("syncing directory {}: {e}", dir.display()))
    }
    #[cfg(not(unix))]
    {
        let _ = dir;
        Ok(())
    }
}

/// Parent directory of a ledger path for creation/sync purposes. A bare
/// relative filename has `Some("")` as its parent — which `create_dir_all`,
/// `exists()`, and `File::open` all reject — and an empty path means the
/// current directory.
fn ledger_parent(path: &Path) -> &Path {
    match path.parent() {
        Some(p) if !p.as_os_str().is_empty() => p,
        _ => Path::new("."),
    }
}

/// Read a whole JSONL ledger. Missing file → empty (first run). A torn final
/// record left by a power loss is skipped with a warning; any other malformed
/// line is a hard error naming the line.
pub fn read_jsonl<T: DeserializeOwned>(path: &Path) -> Result<Vec<T>, String> {
    let raw = match std::fs::read(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    ovp_domain::jsonl::parse_ledger(
        &raw,
        ovp_domain::jsonl::TornLines::Skip(|line| {
            eprintln!(
                "ovp: skipping torn record at {} line {line} (an append interrupted by power loss)",
                path.display()
            )
        }),
    )
    .map_err(|b| {
        format!("ledger {} line {}: malformed record: {}", path.display(), b.line, b.error)
    })
}

/// Like [`read_jsonl`] but a torn record is a hard error too
/// (`TornLines::Fail`). For ledgers that carry human decisions, such as the
/// crystal store's `ledger.jsonl` (`StoreEvent`, which includes review
/// decisions): a truncated line there may be an acknowledged decision cut
/// by a sync tool, so it must fail loud, not vanish.
pub fn read_jsonl_strict<T: DeserializeOwned>(path: &Path) -> Result<Vec<T>, String> {
    let raw = match std::fs::read(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    ovp_domain::jsonl::parse_ledger(&raw, ovp_domain::jsonl::TornLines::<fn(usize)>::Fail)
        .map_err(|b| {
            format!("ledger {} line {}: malformed record: {}", path.display(), b.line, b.error)
        })
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
/// full path. Thin alias for [`ovp_domain::vault_rel`], which owns the
/// separator convention (these strings are persisted, not just displayed).
pub fn rel_to(root: &Path, p: &Path) -> String {
    ovp_domain::vault_rel(root, p)
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
        Self::acquire_named(vault_root, "run.lock")
    }

    /// Acquire a named lock under `.ovp/<name>` (same stale-owner reclaim as
    /// [`acquire`]). Lets a caller hold a lock DISTINCT from the pipeline's
    /// `.ovp/run.lock` — e.g. the scheduler serializes its own dispatch with
    /// `scheduler.lock` while the `daily` child it spawns still takes
    /// `run.lock`, so the two never deadlock on the same file.
    pub fn acquire_named(vault_root: &Path, name: &str) -> Result<Self, String> {
        let path = vault_root.join(".ovp").join(name);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("creating {}: {e}", parent.display()))?;
        }
        match Self::try_create(&path) {
            Ok(lock) => {
                Self::clear_stale_guard(&path);
                Ok(lock)
            }
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
                if Self::owner_is_dead(&path)
                    && let Some(lock) = Self::reclaim_under_guard(&path) {
                        return Ok(lock);
                    }
                // Only when BOTH owners are dead: a live run.lock with a stale
                // guard is reachable (a reclaimer lost the create race, then
                // died holding the guard), and telling the operator to delete
                // a live lock would break mutual exclusion.
                let guard = Self::guard_path(&path);
                if Self::owner_is_dead(&path) && Self::owner_is_dead(&guard) {
                    return Err(format!(
                        "a previous OVP run died while reclaiming a stale lock, leaving \
                         {} and {}; if no run is active, delete both files and retry",
                        path.display(),
                        guard.display()
                    ));
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
        let guard = Self::guard_path(path);
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

    fn guard_path(lock: &Path) -> PathBuf {
        lock.with_extension("lock.reclaim")
    }

    /// Atomically claim the reclaim guard (PID-stamped like the lock). A
    /// guard that already exists is NEVER taken over here, even when its
    /// owner is dead: `remove_file` deletes by path, so two racers that both
    /// judged it stale could each delete the other's fresh guard and both
    /// enter the reclaim section — and then both reclaim run.lock
    /// (docs/tla/RunLock.tla, `RunLockLegacy.cfg`). A stale guard is cleared
    /// only by a process that holds the lock ([`clear_stale_guard`]).
    fn claim_guard(guard: &Path) -> bool {
        match OpenOptions::new().write(true).create_new(true).open(guard) {
            Ok(mut f) => {
                let _ = writeln!(f, "{}", std::process::id());
                true
            }
            Err(_) => false,
        }
    }

    /// Called right after winning run.lock through the ordinary `create_new`
    /// path. A guard left by a process that died mid-reclaim would otherwise
    /// wait to block the next stale-lock recovery. Safe to delete by path:
    /// with [`claim_guard`] never taking over a guard, a stale guard file is
    /// replaced only after someone deletes it, and only a lock holder does —
    /// of which there is one.
    fn clear_stale_guard(lock: &Path) {
        let guard = Self::guard_path(lock);
        if Self::owner_is_dead(&guard) {
            let _ = std::fs::remove_file(&guard);
        }
    }

    /// True only when the lock file names a PID that is verifiably no longer
    /// running. Conservative on every uncertainty (unreadable file, no PID,
    /// probe failure): treat the owner as alive and keep refusing — the
    /// manual-deletion instruction in the error still applies.
    fn owner_is_dead(path: &Path) -> bool {
        let Some(pid) = std::fs::read_to_string(path)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
            .filter(|p| *p > 0)
        else {
            return false;
        };
        probe_pid(pid) == Some(false)
    }
}

/// OS advisory lock (flock / LockFileEx via `File::try_lock`) on
/// `.ovp/<name>`, held for the guard's lifetime. The kernel releases it when
/// the holder dies, so there is no stale-owner reclaim step and PID reuse
/// cannot pin it. [`RunLock`]'s PID file can: after a reboot, a dead
/// holder's PID may name a live process, which reads as "still held". The file
/// is never deleted, because every contender must lock the same inode. The PID
/// written into it is for display only. Same primitive as the chat session
/// lock (ovp-memory agent_transcript.rs, docs/tla/SessionLock.tla).
#[derive(Debug)]
pub struct OsLock {
    key: PathBuf,
    file: Option<std::fs::File>,
}

impl OsLock {
    pub fn acquire_named(vault_root: &Path, name: &str) -> Result<Self, String> {
        let path = vault_root.join(".ovp").join(name);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("creating {}: {e}", parent.display()))?;
        }
        let mut opts = OpenOptions::new();
        opts.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            // The PID stamp truncates: never follow a planted symlink.
            opts.custom_flags(libc::O_NOFOLLOW);
        }
        #[cfg(not(unix))]
        if std::fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(format!("lock {}: is a symlink, refusing to use it", path.display()));
        }
        let mut file = opts
            .open(&path)
            .map_err(|e| format!("lock {}: {e} (a symlink is refused)", path.display()))?;
        if !file.metadata().is_ok_and(|m| m.is_file()) {
            return Err(format!("lock {}: not a regular file", path.display()));
        }
        let key = std::fs::canonicalize(&path).unwrap_or_else(|_| path.clone());
        // Reserve in the in-process set first (two handles in one process
        // must exclude each other even where the OS lock is per-process),
        // then retry without holding that set's mutex.
        if !os_locks_held().insert(key.clone()) {
            return Err(format!("{} is already held by this process", path.display()));
        }
        // Retry WouldBlock briefly: a child spawned by any thread holds a
        // copy of every fd between fork and exec, which delays a release for
        // those microseconds (see the session lock). A real holder keeps the
        // lock far longer.
        let mut attempt = 0;
        loop {
            match file.try_lock() {
                Ok(()) => break,
                Err(std::fs::TryLockError::WouldBlock) if attempt < 10 => {
                    attempt += 1;
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
                Err(std::fs::TryLockError::WouldBlock) => {
                    os_locks_held().remove(&key);
                    return Err(format!("{} is held by another process", path.display()));
                }
                Err(std::fs::TryLockError::Error(e)) => {
                    os_locks_held().remove(&key);
                    return Err(format!("lock {}: {e}", path.display()));
                }
            }
        }
        use std::io::Seek;
        let _ = file
            .set_len(0)
            .and_then(|_| file.seek(std::io::SeekFrom::Start(0)))
            .and_then(|_| write!(file, "{}", std::process::id()));
        Ok(Self { key, file: Some(file) })
    }

    /// True when THIS process holds the named lock. Answered from the
    /// in-process record, never from the PID in the file: a dead holder's
    /// stale PID may equal ours after PID reuse.
    pub fn held_by_this_process(vault_root: &Path, name: &str) -> bool {
        let path = vault_root.join(".ovp").join(name);
        let key = std::fs::canonicalize(&path).unwrap_or(path);
        os_locks_held().contains(&key)
    }
}

impl Drop for OsLock {
    fn drop(&mut self) {
        // Release the OS lock before leaving the in-process set.
        drop(self.file.take());
        os_locks_held().remove(&self.key);
    }
}

fn os_locks_held() -> std::sync::MutexGuard<'static, std::collections::BTreeSet<PathBuf>> {
    static HELD: std::sync::Mutex<std::collections::BTreeSet<PathBuf>> =
        std::sync::Mutex::new(std::collections::BTreeSet::new());
    HELD.lock().unwrap_or_else(|e| e.into_inner())
}

/// Three-valued process-liveness probe, shared by every stale-owner check in
/// the workspace (run lock, daily heartbeat, source-work queue, agent
/// transcript). It answers ONLY the OS question; each caller keeps its own
/// conservative default for `None`, because "assume alive" and "assume gone"
/// are the safe answers in different places.
///
/// - `Some(true)`  — the process exists.
/// - `Some(false)` — no process has that PID.
/// - `None`        — the probe could not tell (no `kill`, access denied, …).
///
/// PID reuse reads as alive, which is the conservative direction: at worst a
/// stale lock survives one more run and the operator deletes it by hand.
pub fn probe_pid(pid: u32) -> Option<bool> {
    if pid == 0 {
        return Some(false);
    }
    #[cfg(unix)]
    {
        // `kill -0` probes liveness without signaling; exit 0 = alive.
        // (A live process owned by another user also reports non-zero, but a
        // vault lock under $HOME is always same-user.)
        std::process::Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .ok()
            .map(|s| s.success())
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::{CloseHandle, ERROR_INVALID_PARAMETER, GetLastError};
        use windows_sys::Win32::System::Threading::{
            GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        };
        // STILL_ACTIVE (STATUS_PENDING). A process that genuinely exited with
        // code 259 reads as alive — same conservative direction as PID reuse.
        const STILL_ACTIVE: u32 = 259;
        // SAFETY: OpenProcess/GetExitCodeProcess/CloseHandle are called with a
        // valid pid, a stack-owned out-param, and a handle this function owns
        // and closes exactly once on every path.
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if handle.is_null() {
                // Only "no such process" is a definite answer. ACCESS_DENIED
                // means the process EXISTS and is simply out of reach.
                return (GetLastError() == ERROR_INVALID_PARAMETER).then_some(false);
            }
            let mut code: u32 = 0;
            let ok = GetExitCodeProcess(handle, &mut code);
            CloseHandle(handle);
            (ok != 0).then(|| code == STILL_ACTIVE)
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = pid;
        None
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
    fn ledger_parent_normalizes_bare_relative_filenames() {
        // A bare filename's parent() is Some("") — create_dir_all, exists(),
        // and File::open all reject "", so the sync path must use ".".
        assert_eq!(ledger_parent(Path::new("ledger.jsonl")), Path::new("."));
        assert_eq!(ledger_parent(Path::new("sub/ledger.jsonl")), Path::new("sub"));
        assert_eq!(
            ledger_parent(Path::new("/abs/ledger.jsonl")),
            Path::new("/abs")
        );
        // The empty-path guard in create_dirs_synced covers direct callers.
        create_dirs_synced(Path::new("")).expect("empty path means cwd");
    }

    #[test]
    fn append_jsonl_creates_deep_chain_and_appends() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("a/b/ledger.jsonl");
        append_jsonl(&ledger, &serde_json::json!({"n": 1})).expect("first append creates");
        append_jsonl(&ledger, &serde_json::json!({"n": 2})).expect("second append");
        let rows: Vec<serde_json::Value> = read_jsonl(&ledger).expect("read back");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1]["n"], 2);
    }

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

    /// A PID that is definitely gone: spawn the shortest-lived process the host
    /// has, reap it, and DROP the handle before returning.
    ///
    /// Dropping matters on Windows. While a `Child` is alive it holds an open
    /// process handle, so `OpenProcess` still succeeds against a process that
    /// has exited and `probe_pid` answers from the exit code instead of from
    /// `ERROR_INVALID_PARAMETER` — a different branch than the crashed-run case
    /// these tests exist to cover.
    fn reaped_dead_pid() -> u32 {
        #[cfg(windows)]
        let mut child = std::process::Command::new("cmd")
            .args(["/C", "exit", "0"])
            .spawn()
            .unwrap();
        #[cfg(not(windows))]
        let mut child = std::process::Command::new("true").spawn().unwrap();
        let pid = child.id();
        child.wait().unwrap();
        drop(child);
        pid
    }

    /// `probe_pid` is the single liveness primitive behind the run lock, the
    /// daily heartbeat, the source-work queue and the agent transcript, and its
    /// Windows implementation (`OpenProcess`/`GetExitCodeProcess`) shares no
    /// code with the Unix one. Until this test existed, both dead-process tests
    /// below were `cfg(unix)`-gated on `Command::new("true")`, so the Windows
    /// branch had NO coverage at all — a `None` there would mean every stale
    /// lock is kept forever, which looks exactly like a run that never ends.
    #[test]
    fn probe_pid_answers_definitively_for_live_dead_and_zero() {
        assert_eq!(probe_pid(0), Some(false), "pid 0 is never a process");
        assert_eq!(
            probe_pid(std::process::id()),
            Some(true),
            "this very process is alive"
        );
        assert_eq!(
            probe_pid(reaped_dead_pid()),
            Some(false),
            "a reaped child must read as GONE, not as `None`/unknown"
        );
    }

    #[test]
    fn run_lock_reclaims_stale_lock_from_dead_process() {
        let dir = tempfile::tempdir().unwrap();
        // Fabricate a crash: a lock file naming a process that has exited.
        let dead_pid = reaped_dead_pid();
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

    #[test]
    fn run_lock_refuses_to_take_over_a_stranded_reclaim_guard() {
        // Crash DURING a previous reclaim: both the lock and the reclaim
        // guard are stranded with dead owners. Taking the guard over is what
        // let two racers both hold run.lock (docs/tla/RunLockLegacy.cfg), so
        // acquire refuses and names BOTH files for the operator.
        let dir = tempfile::tempdir().unwrap();
        let dead_pid = reaped_dead_pid();
        let lock_path = dir.path().join(".ovp/run.lock");
        let guard_path = lock_path.with_extension("lock.reclaim");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        std::fs::write(&lock_path, format!("{dead_pid}\n")).unwrap();
        std::fs::write(&guard_path, format!("{dead_pid}\n")).unwrap();

        let err = RunLock::acquire(dir.path()).expect_err("stranded guard is not taken over");
        assert!(err.contains("run.lock.reclaim") && err.contains("delete both"), "got: {err}");
        assert!(lock_path.exists() && guard_path.exists(), "nothing deleted");

        // Following the instruction recovers.
        std::fs::remove_file(&lock_path).unwrap();
        std::fs::remove_file(&guard_path).unwrap();
        RunLock::acquire(dir.path()).expect("recovers after manual deletion");
    }

    #[test]
    fn run_lock_holder_clears_a_stale_reclaim_guard() {
        // A guard stranded while run.lock itself is free (the dead reclaimer
        // had already released or never created the lock) is cleared by the
        // next ordinary acquirer, so it cannot block a later stale-lock
        // recovery. A guard with a LIVE owner is left alone.
        let dir = tempfile::tempdir().unwrap();
        let guard_path = dir.path().join(".ovp/run.lock.reclaim");
        std::fs::create_dir_all(guard_path.parent().unwrap()).unwrap();

        std::fs::write(&guard_path, format!("{}\n", reaped_dead_pid())).unwrap();
        drop(RunLock::acquire(dir.path()).expect("free lock"));
        assert!(!guard_path.exists(), "stale guard cleared by the holder");

        std::fs::write(&guard_path, format!("{}\n", std::process::id())).unwrap();
        drop(RunLock::acquire(dir.path()).expect("free lock"));
        assert!(guard_path.exists(), "live guard untouched");
    }

    #[test]
    fn run_lock_live_owner_with_stale_guard_is_plain_busy() {
        // A live run.lock plus a dead-owner guard must NOT produce the
        // "delete both files" instruction: that lock belongs to a running process.
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join(".ovp/run.lock");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        std::fs::write(&lock_path, format!("{}\n", std::process::id())).unwrap();
        std::fs::write(
            lock_path.with_extension("lock.reclaim"),
            format!("{}\n", reaped_dead_pid()),
        )
        .unwrap();

        let err = RunLock::acquire(dir.path()).expect_err("live owner must refuse");
        assert!(!err.contains("delete both"), "got: {err}");
        assert!(err.contains("in progress"), "got: {err}");
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
    fn os_lock_excludes_ignores_leftover_files_and_reports_ownership() {
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join(".ovp/w.lock");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        // A leftover file with a (possibly reused) PID is not a held lock.
        std::fs::write(&lock_path, format!("{}\n", std::process::id())).unwrap();
        assert!(!OsLock::held_by_this_process(dir.path(), "w.lock"));
        let held = OsLock::acquire_named(dir.path(), "w.lock").expect("free");
        assert!(OsLock::held_by_this_process(dir.path(), "w.lock"));
        OsLock::acquire_named(dir.path(), "w.lock").expect_err("second holder refused");
        drop(held);
        assert!(!OsLock::held_by_this_process(dir.path(), "w.lock"));
        assert!(lock_path.exists(), "the lock file is never deleted");
        drop(OsLock::acquire_named(dir.path(), "w.lock").expect("free again"));
    }

    #[test]
    fn strict_reader_rejects_a_marked_torn_line_that_the_default_reader_skips() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ledger.jsonl");
        std::fs::write(&path, "{\"a\":1}\n{\"a\":").unwrap(); // torn tail
        append_jsonl(&path, &serde_json::json!({"a": 2})).unwrap(); // closes it with the marker
        let skipped: Vec<serde_json::Value> = read_jsonl(&path).unwrap();
        assert_eq!(skipped.len(), 2, "machine ledgers skip the proven torn line");
        let err = read_jsonl_strict::<serde_json::Value>(&path).unwrap_err();
        assert!(err.contains("line 2"), "human-decision ledgers fail loud: {err}");
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
