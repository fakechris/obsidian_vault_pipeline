//! `ask` — retrieval-augmented Q&A against OVP product state.
//!
//! Reads the JSON index, retrieves matching context, queries the LLM,
//! and prints a cited answer. Optionally saves to `.ovp/chats/`.

use std::path::PathBuf;

use ovp_index::{read_evidence, read_index};
use ovp_memory::ask::{AskArgs, ask_with_optional_evidence};

use crate::CliError;
use crate::commands::client::{ClientKind, build_client};

pub struct AskCliArgs {
    pub vault_root: PathBuf,
    pub question: String,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub save: bool,
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
    eprintln!("({} context hits)", result.context_hits);

    Ok(())
}
