//! `ask` — retrieval-augmented Q&A against OVP product state.
//!
//! Reads the JSON index, retrieves matching context, queries the LLM,
//! and prints a cited answer. Optionally saves to `.ovp/chats/`.

use std::path::PathBuf;

use ovp_index::{read_evidence, read_index};
use ovp_memory::ask::{ask_with_optional_evidence, AskArgs};

use crate::commands::client::{build_client, ClientKind};
use crate::CliError;

pub struct AskCliArgs {
    pub vault_root: PathBuf,
    pub question: String,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub save: bool,
    pub strict_ask: bool,
}

pub fn run(args: AskCliArgs) -> Result<(), CliError> {
    let model = read_index(&args.vault_root).map_err(|e| CliError::Io(e.to_string()))?;
    let evidence = match read_evidence(&args.vault_root) {
        Ok(evidence) => Some(evidence),
        Err(e) => {
            eprintln!("warning: evidence sidecar unavailable; using claims-only ask context. {e}");
            None
        }
    };

    let cassette_root = args
        .cache_dir
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/ask"));
    let mut client = build_client(args.client_kind, &cassette_root)?;

    let ask_args = AskArgs {
        question: args.question,
        save_chat: args.save,
        ..Default::default()
    };

    let result = ask_with_optional_evidence(
        &model,
        evidence.as_ref(),
        client.as_mut(),
        &ask_args,
        &args.vault_root,
    )
    .map_err(CliError::Io)?;

    println!("{}", result.answer);

    if let Some(path) = &result.chat_file {
        let rel = path.strip_prefix(&args.vault_root).unwrap_or(path);
        eprintln!("\n(saved to {})", rel.display());
    }
    if let Some(report) = &result.verification {
        eprintln!("verified citations: {}/{}", report.verified, report.cited);
        if !report.missing.is_empty() {
            eprintln!("missing citations: {}", report.missing.join(", "));
        }
        if !report.warnings.is_empty() {
            eprintln!("citation warnings: {}", report.warnings.join(", "));
        }
        if args.strict_ask && (report.cited == 0 || report.verified < report.cited) {
            return Err(CliError::Gate(format!(
                "strict ask citation verification failed: verified {}/{}",
                report.verified, report.cited
            )));
        }
    }
    eprintln!("({} context hits)", result.context_hits);

    Ok(())
}
