//! Bounded subprocess execution and receipts binding the bytes we consume.
use crate::paired_io::{hash, hash_file};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use std::fs;
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

fn wait(
    child: &mut Child,
    paths: &[&Path],
    start: Instant,
    timeout: u64,
) -> (Option<ExitStatus>, Option<String>) {
    loop {
        let bounded = paths
            .iter()
            .any(|p| fs::metadata(p).is_ok_and(|m| m.len() > 32 * 1024 * 1024));
        let error = if bounded {
            Some("arm exceeded output budget".to_string())
        } else if start.elapsed() > Duration::from_secs(timeout) {
            Some("arm timed out".to_string())
        } else {
            match child.try_wait() {
                Ok(Some(status)) => return (Some(status), None),
                Ok(None) => None,
                Err(e) => Some(format!("wait failed: {e}")),
            }
        };
        if let Some(mut error) = error {
            if let Err(e) = child.kill() {
                error.push_str(&format!("; kill: {e}"));
            }
            return match child.wait() {
                Ok(status) => (Some(status), Some(error)),
                Err(e) => (None, Some(format!("{error}; reap: {e}"))),
            };
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

/// Calls the current Rust CLI with fixed arguments and no shell. Runtime errors
/// are returned as receipts, including launch failure, timeout and nonzero exit.
pub fn execute(
    executable: &Path,
    dest: &Path,
    frozen: &Path,
    mode: &str,
    k: usize,
    timeout: u64,
) -> Result<Value, String> {
    let out = dest.join("report.json");
    let stdout_path = dest.join("stdout.log");
    let stderr_path = dest.join("stderr.log");
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
        out.display().to_string(),
    ];
    let start = Instant::now();
    let launch = (|| {
        let stdout = fs::File::create(&stdout_path).map_err(|e| e.to_string())?;
        let stderr = fs::File::create(&stderr_path).map_err(|e| e.to_string())?;
        let tmp = dest.join("tmp");
        fs::create_dir(&tmp).map_err(|e| e.to_string())?;
        Command::new(executable)
            .args(&args)
            .env("TMPDIR", &tmp)
            .env("TMP", &tmp)
            .env("TEMP", &tmp)
            .stdin(Stdio::null())
            .stdout(stdout)
            .stderr(stderr)
            .spawn()
            .map_err(|e| format!("launch retrieval arm: {e}"))
    })();
    let (status, mut error) = match launch {
        Ok(mut child) => wait(
            &mut child,
            &[&out, &stdout_path, &stderr_path],
            start,
            timeout,
        ),
        Err(e) => (None, Some(e)),
    };
    if error.is_none() && status.is_some_and(|s| !s.success()) {
        error = Some(format!(
            "retrieval arm exited {}; see stderr.log",
            status.unwrap()
        ));
    }
    let mut receipt = json!({"status": "completed", "args": args,
        "exit_code": status.and_then(|s| s.code()), "exit_status": status.map(|s| s.to_string()),
        "elapsed_ms": start.elapsed().as_millis(), "error": error});
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        receipt["signal"] = json!(status.and_then(|s| s.signal()));
    }
    seal_receipt(&mut receipt, &out, &stdout_path, &stderr_path);
    Ok(receipt)
}

fn seal_receipt(receipt: &mut Value, out: &Path, stdout_path: &Path, stderr_path: &Path) {
    for (name, path) in [
        ("report", out),
        ("stdout", stdout_path),
        ("stderr", stderr_path),
    ] {
        match hash_file(path) {
            Ok(h) => receipt[format!("{name}_sha256")] = json!(h),
            Err(e) => {
                receipt[format!("{name}_sha256")] = Value::Null;
                if receipt["error"].is_null() {
                    receipt["error"] = json!(e);
                }
            }
        }
    }
    if !receipt["error"].is_null() {
        receipt["status"] = json!("failed");
    }
}

pub fn read_report<T: DeserializeOwned>(dest: &Path, receipt: &Value) -> Result<T, String> {
    use std::io::Read;
    let mut bytes = Vec::new();
    fs::File::open(dest.join("report.json"))
        .map_err(|e| e.to_string())?
        .take(32 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 32 * 1024 * 1024 || hash(&bytes) != receipt["report_sha256"] {
        return Err("executed report changed before consumption".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}

pub fn verify_evidence(dest: &Path, receipt: &Value) -> Result<(), String> {
    for (name, filename) in [
        ("report", "report.json"),
        ("stdout", "stdout.log"),
        ("stderr", "stderr.log"),
    ] {
        if hash_file(&dest.join(filename))? != receipt[format!("{name}_sha256")] {
            return Err(format!("preserved {name} evidence changed before decision"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn refuses_changed_consumed_or_preserved_report() {
        let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.run/evolve-process-tests");
        fs::create_dir_all(&base).unwrap();
        let dir = tempfile::tempdir_in(base).unwrap();
        fs::write(dir.path().join("report.json"), "{}").unwrap();
        fs::write(dir.path().join("stdout.log"), "").unwrap();
        fs::write(dir.path().join("stderr.log"), "").unwrap();
        let receipt = json!({"report_sha256": hash(b"{}"), "stdout_sha256": hash(b""), "stderr_sha256": hash(b"")});
        read_report::<Value>(dir.path(), &receipt).unwrap();
        verify_evidence(dir.path(), &receipt).unwrap();
        fs::write(dir.path().join("report.json"), "{\"changed\":true}").unwrap();
        assert!(read_report::<Value>(dir.path(), &receipt).is_err());
        assert!(verify_evidence(dir.path(), &receipt).is_err());
    }
    #[test]
    fn launch_failure_retains_arguments_and_logs() {
        let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.run/evolve-process-tests");
        fs::create_dir_all(&base).unwrap();
        let dir = tempfile::tempdir_in(base).unwrap();
        let r = execute(
            &dir.path().join("absent"),
            dir.path(),
            dir.path(),
            "terms",
            1,
            1,
        )
        .unwrap();
        assert_eq!(r["status"], "failed");
        assert_eq!(r["args"][0], "retrieval-eval");
        assert!(r["exit_code"].is_null());
        assert!(r["elapsed_ms"].is_number());
        assert_eq!(r["stderr_sha256"], hash(b""));
    }
}
