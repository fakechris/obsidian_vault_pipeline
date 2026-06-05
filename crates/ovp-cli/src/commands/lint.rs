//! `lint` — the L5 health command. Thin shell over `ovp_lint::Lint::check`:
//! run the read-only checks, print findings (text or `--json`), and exit
//! non-zero if any finding is at/above `--max-severity` (default `error`).

use std::path::PathBuf;

use ovp_lint::{Lint, Severity};

use crate::CliError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SeverityArg {
    Info,
    Warning,
    Error,
}

impl SeverityArg {
    fn to_lint(self) -> Severity {
        match self {
            SeverityArg::Info => Severity::Info,
            SeverityArg::Warning => Severity::Warning,
            SeverityArg::Error => Severity::Error,
        }
    }
}

pub struct LintArgs {
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub max_severity: SeverityArg,
    pub json: bool,
}

pub fn run(args: LintArgs) -> Result<(), CliError> {
    let report = Lint::check(&args.vault_root, &args.canonical_root);
    let threshold = args.max_severity.to_lint();

    if args.json {
        let json = serde_json::to_string_pretty(&report)
            .map_err(|e| CliError::Io(format!("serializing report: {e}")))?;
        println!("{json}");
    } else if report.findings.is_empty() {
        println!("clean: no findings");
    } else {
        for f in &report.findings {
            let loc = f.location.as_deref().unwrap_or("-");
            println!("[{}] {}  {}  ({})", f.severity.as_str(), f.code, f.detail, loc);
        }
        println!(
            "\n{} finding(s): {} error, {} warning, {} info",
            report.findings.len(),
            report.count(Severity::Error),
            report.count(Severity::Warning),
            report.count(Severity::Info),
        );
    }

    // Gate: fail (non-zero exit) if any finding is at/above the threshold.
    if !report.passed(threshold) {
        return Err(CliError::Io(format!(
            "lint failed: findings at or above `{}`",
            args.max_severity_str()
        )));
    }
    Ok(())
}

impl LintArgs {
    fn max_severity_str(&self) -> &'static str {
        self.max_severity.to_lint().as_str()
    }
}
