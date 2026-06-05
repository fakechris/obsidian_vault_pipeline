//! `copy-probe` — M14a.4 Step 1. Tests whether the model can verbatim-copy a
//! substring from rendered spans (no extraction). Thin shim over
//! `ovp_domain::units::run_copy_probe`, reusing the shared client wiring.

use std::path::PathBuf;

use ovp_domain::units::run_copy_probe;

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct CopyProbeArgs {
    pub input_path: PathBuf,
    pub out_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub client_kind: ClientKind,
    pub max_spans: usize,
}

pub fn run(args: CopyProbeArgs) -> Result<(), CliError> {
    let source = ovp_domain::units::read_source_from_path(&args.input_path)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.input_path.display())))?;
    let mut client = build_client(args.client_kind, &args.cache_dir)?;
    let (report, raw) = run_copy_probe(&source, client.as_mut(), args.max_spans)
        .map_err(|e| CliError::Io(format!("copy-probe call failed: {e}")))?;

    std::fs::create_dir_all(&args.out_dir)
        .map_err(|e| CliError::Io(format!("mkdir {}: {e}", args.out_dir.display())))?;
    let json = serde_json::to_string_pretty(&report).map_err(|e| CliError::Io(e.to_string()))?;
    std::fs::write(args.out_dir.join("copy-probe-report.json"), json)
        .map_err(|e| CliError::Io(e.to_string()))?;
    std::fs::write(args.out_dir.join("copy-probe-reply.txt"), &raw)
        .map_err(|e| CliError::Io(e.to_string()))?;

    println!("copy-probe: {}", source.title);
    println!(
        "  requested={} returned={} verbatim_ok={} copy_rate={:.1}%",
        report.requested,
        report.returned,
        report.verbatim_ok,
        report.copy_rate() * 100.0
    );
    for o in report.outcomes.iter().filter(|o| !o.verbatim).take(5) {
        println!("  NOT verbatim [{}]: {:?}", o.span_id, o.quote.chars().take(50).collect::<String>());
    }
    Ok(())
}
