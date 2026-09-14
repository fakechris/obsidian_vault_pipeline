//! `doctor` — health checks over OVP vault state. Exits non-zero if any
//! check FAILs (CI-friendly). `--fix` applies safe repairs only. The checks
//! themselves live in `ovp-doctor` so the portal and MCP run the same ones.

use std::path::PathBuf;

use ovp_doctor::{DoctorOptions, Severity};

use crate::CliError;

pub struct DoctorArgs {
    pub vault_root: PathBuf,
    pub fix: bool,
    pub json: bool,
    /// Emit the shared `DiagnosticReport` JSON instead of text.
    pub diagnostics: bool,
    /// Override for the run-recency staleness threshold (hours).
    pub since_hours: Option<u64>,
}

pub fn run(args: DoctorArgs) -> Result<(), CliError> {
    if !args.diagnostics {
        println!("doctor: {}", args.vault_root.display());
    }

    let findings = ovp_doctor::run_checks(
        &args.vault_root,
        &DoctorOptions {
            fix: args.fix,
            since_hours: args.since_hours,
        },
    );

    if args.diagnostics {
        let report = ovp_doctor::to_diagnostics(&findings);
        let s = serde_json::to_string_pretty(&report)
            .map_err(|e| CliError::Io(format!("serializing diagnostics: {e}")))?;
        println!("{s}");
    } else if args.json {
        let json_out: Vec<_> = findings
            .iter()
            .map(|f| {
                serde_json::json!({
                    "check": f.check,
                    "severity": format!("{}", f.severity),
                    "message": f.message,
                    "hint": f.hint,
                    "fixed": f.fixed,
                })
            })
            .collect();
        println!(
            "{}",
            serde_json::to_string_pretty(&json_out).unwrap_or_default()
        );
    } else {
        for f in &findings {
            let fix_tag = if f.fixed { " [FIXED]" } else { "" };
            println!("  [{}] {}: {}{}", f.severity, f.check, f.message, fix_tag);
            // Indented under its finding, and only when the operator still has
            // something to do — a hint next to a [FIXED] line is just noise.
            if let Some(hint) = f.hint.as_deref().filter(|_| !f.fixed) {
                println!("         -> {hint}");
            }
        }
    }

    let fails = findings
        .iter()
        .filter(|f| f.severity == Severity::Fail && !f.fixed)
        .count();
    let warns = findings
        .iter()
        .filter(|f| f.severity == Severity::Warn)
        .count();
    let infos = findings
        .iter()
        .filter(|f| f.severity == Severity::Info)
        .count();
    let passes = findings
        .iter()
        .filter(|f| f.severity == Severity::Pass)
        .count();

    if !args.diagnostics {
        println!("\n  summary: {passes} pass, {infos} info, {warns} warn, {fails} fail");
    }

    if fails > 0 {
        Err(CliError::Gate(format!("doctor: {fails} check(s) FAILED")))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn info_findings_do_not_fail_doctor_exit_code() {
        // The exit-code rule lives here, not in the engine: INFO never fails.
        let tmp = tempfile::tempdir().unwrap();
        let logs = tmp.path().join("60-Logs");
        std::fs::create_dir_all(&logs).unwrap();
        std::fs::write(logs.join("knowledge.db"), "").unwrap();
        let today = chrono::Local::now().date_naive().to_string();
        let model = ovp_index::build_index(tmp.path(), &today, None).expect("build index");
        ovp_index::write_index(tmp.path(), &model).expect("write index");

        let result = run(DoctorArgs {
            vault_root: tmp.path().to_path_buf(),
            fix: false,
            json: false,
            diagnostics: false,
            since_hours: None,
        });
        assert!(
            result.is_ok(),
            "INFO finding must not fail doctor: {result:?}"
        );
    }
}
