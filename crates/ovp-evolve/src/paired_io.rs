use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn hash_file(path: &Path) -> Result<String, String> {
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = [0; 65536];
    loop {
        let n = file.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

pub fn read_json<T: DeserializeOwned>(path: &Path) -> Result<T, String> {
    if fs::metadata(path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .len()
        > 32 * 1024 * 1024
    {
        return Err("JSON input exceeds 32 MiB".into());
    }
    serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|e| format!("{}: {e}", path.display()))
}

pub fn snapshot(root: &Path) -> Result<BTreeMap<PathBuf, String>, String> {
    fn visit(
        root: &Path,
        path: &Path,
        out: &mut BTreeMap<PathBuf, String>,
        bytes: &mut u64,
    ) -> Result<(), String> {
        let meta = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
        if meta.file_type().is_symlink() {
            return Err("fixture symlinks are unsupported".into());
        }
        if meta.is_dir() {
            for item in fs::read_dir(path).map_err(|e| e.to_string())? {
                visit(root, &item.map_err(|e| e.to_string())?.path(), out, bytes)?;
            }
        } else if meta.is_file() {
            *bytes = bytes
                .checked_add(meta.len())
                .ok_or("fixture size overflow")?;
            if *bytes > 256 * 1024 * 1024 || out.len() >= 10000 {
                return Err(
                    "fixture exceeds 256 MiB / 10000 files; curate a bounded experiment".into(),
                );
            }
            out.insert(
                path.strip_prefix(root)
                    .map_err(|e| e.to_string())?
                    .to_path_buf(),
                hash_file(path)?,
            );
        } else {
            return Err("fixture contains a non-regular file".into());
        }
        Ok(())
    }
    let mut out = BTreeMap::new();
    visit(root, root, &mut out, &mut 0)?;
    Ok(out)
}

pub fn copy_snapshot(
    root: &Path,
    dest: &Path,
    files: &BTreeMap<PathBuf, String>,
) -> Result<(), String> {
    fs::create_dir(dest).map_err(|e| e.to_string())?;
    for (rel, expected) in files {
        let target = dest.join(rel);
        fs::create_dir_all(target.parent().ok_or("fixture path has no parent")?)
            .map_err(|e| e.to_string())?;
        fs::copy(root.join(rel), &target).map_err(|e| e.to_string())?;
        if &hash_file(&target)? != expected {
            return Err("fixture changed while snapshotting".into());
        }
    }
    Ok(())
}

pub fn new_output(path: &Path) -> Result<PathBuf, String> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(path)
    };
    let parent = absolute.parent().ok_or("output requires a parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let parent = parent.canonicalize().map_err(|e| e.to_string())?;
    if !parent
        .components()
        .any(|c| c.as_os_str() == ".run" || c.as_os_str() == ".ovp")
        || parent.starts_with("/tmp")
        || parent.starts_with("/private/tmp")
    {
        return Err("output must be a new directory under durable .run or .ovp".into());
    }
    let out = parent.join(
        absolute
            .file_name()
            .ok_or("output directory name missing")?,
    );
    fs::create_dir(&out).map_err(|e| format!("output must not exist: {e}"))?;
    Ok(out)
}

pub fn write_json(path: &Path, value: &Value) -> Result<(), String> {
    let pending = path.with_extension("pending");
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&pending)
        .map_err(|e| e.to_string())?;
    f.write_all(&serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    f.sync_all().map_err(|e| e.to_string())?;
    drop(f);
    fs::rename(&pending, path).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    fs::File::open(path.parent().ok_or("missing output parent")?)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn code_identity() -> Result<Value, String> {
    let git = |args: &[&str]| -> Result<Vec<u8>, String> {
        let out = Command::new("git")
            .args(args)
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err("git provenance unavailable; run from the source checkout".into());
        }
        Ok(out.stdout)
    };
    let sha = String::from_utf8(git(&["rev-parse", "HEAD"])?).map_err(|e| e.to_string())?;
    let diff = git(&["diff", "HEAD", "--binary"])?;
    let untracked = git(&["ls-files", "--others", "--exclude-standard", "-z"])?;
    let mut untracked_hashes = BTreeMap::new();
    for raw in untracked.split(|b| *b == 0).filter(|p| !p.is_empty()) {
        let path = std::str::from_utf8(raw).map_err(|e| e.to_string())?;
        if fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_symlink()
        {
            return Err(
                "untracked symlink prevents source provenance; exclude local tool state".into(),
            );
        }
        untracked_hashes.insert(path.to_string(), hash_file(Path::new(path))?);
    }
    Ok(
        json!({"git_sha": sha.trim(), "tracked_diff_sha256": hash(&diff),
        "tracked_diff": String::from_utf8_lossy(&diff), "untracked_files": untracked_hashes}),
    )
}

/// Calls only the existing offline retrieval subcommand, without a shell.
/// No provider, LLM, background service or arbitrary command adapter is used.
pub fn execute(
    executable: &Path,
    dest: &Path,
    frozen: &Path,
    mode: &str,
    k: usize,
    timeout: u64,
) -> Result<Value, String> {
    let out_path = dest.join("report.json");
    let stdout_path = dest.join("stdout.log");
    let stderr_path = dest.join("stderr.log");
    let stdout = fs::File::create(&stdout_path).map_err(|e| e.to_string())?;
    let stderr = fs::File::create(&stderr_path).map_err(|e| e.to_string())?;
    let tmp = dest.join("tmp");
    fs::create_dir(&tmp).map_err(|e| e.to_string())?;
    let args = vec![
        "retrieval-eval".to_string(),
        "--vault-root".into(),
        frozen.join("vault").display().to_string(),
        "--qrels".into(),
        frozen.join("qrels").display().to_string(),
        "--k".into(),
        k.to_string(),
        "--gold-only".into(),
        "--query-mode".into(),
        mode.into(),
        "--out".into(),
        out_path.display().to_string(),
    ];
    let start = Instant::now();
    let mut child = Command::new(executable)
        .args(&args)
        .env("TMPDIR", &tmp)
        .env("TMP", &tmp)
        .env("TEMP", &tmp)
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
        .map_err(|e| format!("launch retrieval arm: {e}"))?;
    let status = loop {
        let bounded = [&stdout_path, &stderr_path, &out_path]
            .iter()
            .any(|p| fs::metadata(p).is_ok_and(|m| m.len() > 32 * 1024 * 1024));
        if bounded || start.elapsed() > Duration::from_secs(timeout) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(if bounded {
                "arm exceeded output budget"
            } else {
                "arm timed out"
            }
            .into());
        }
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        std::thread::sleep(Duration::from_millis(25));
    };
    if !status.success() {
        return Err(format!("retrieval arm exited {status}; see stderr.log"));
    }
    Ok(
        json!({"status": "completed", "args": args, "exit_code": status.code(),
        "elapsed_ms": start.elapsed().as_millis(), "report_sha256": hash_file(&out_path)?,
        "stdout_sha256": hash_file(&stdout_path)?, "stderr_sha256": hash_file(&stderr_path)?}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn temp() -> tempfile::TempDir {
        let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.run/evolve-io-tests");
        fs::create_dir_all(&base).unwrap();
        tempfile::tempdir_in(base).unwrap()
    }
    #[test]
    fn snapshot_copy_binds_content_and_refuses_existing_output() {
        let dir = temp();
        let source = dir.path().join("source");
        fs::create_dir(&source).unwrap();
        fs::write(source.join("one"), "original").unwrap();
        let original = snapshot(&source).unwrap();
        let copy = dir.path().join("copy");
        copy_snapshot(&source, &copy, &original).unwrap();
        assert_eq!(snapshot(&copy).unwrap(), original);
        fs::write(copy.join("one"), "changed").unwrap();
        assert_ne!(snapshot(&copy).unwrap(), original);
        let out = dir.path().join("result");
        new_output(&out).unwrap();
        assert!(new_output(&out).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn symlink_fixtures_are_rejected_and_timed_out_child_is_reaped() {
        use std::os::unix::{fs::PermissionsExt, fs::symlink};
        let dir = temp();
        let fixture = dir.path().join("fixture");
        fs::create_dir(&fixture).unwrap();
        symlink(&fixture, fixture.join("loop")).unwrap();
        assert!(snapshot(&fixture).is_err());
        let script = dir.path().join("slow");
        fs::write(&script, "#!/bin/sh\nexec /bin/sleep 5\n").unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o700)).unwrap();
        let dest = dir.path().join("arm");
        fs::create_dir(&dest).unwrap();
        assert!(
            execute(&script, &dest, &fixture, "verbatim", 1, 1)
                .unwrap_err()
                .contains("timed out")
        );
    }
}
