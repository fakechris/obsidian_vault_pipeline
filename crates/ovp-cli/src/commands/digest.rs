//! `digest` — generate today's daily digest from the index read model.
//!
//! Produces `.ovp/digests/<date>.md` with a summary of today's activity:
//! new packs, crystal status, attention items, run results.
//! Optionally synthesized via LLM (with token budget), otherwise plain text.

use std::path::PathBuf;

use ovp_index::read_index;
use ovp_memory::digest::{collect_digest_data, render_plain_digest, write_digest};

use crate::CliError;

pub struct DigestArgs {
    pub vault_root: PathBuf,
    pub date: String,
    #[allow(dead_code)]
    pub no_llm: bool,
}

pub fn run(args: DigestArgs) -> Result<(), CliError> {
    let model = read_index(&args.vault_root).map_err(CliError::Io)?;
    let data = collect_digest_data(&model, &args.date);

    let content = render_plain_digest(&data);

    let path = write_digest(&args.vault_root, &args.date, &content)
        .map_err(CliError::Io)?;

    let rel = path
        .strip_prefix(&args.vault_root)
        .unwrap_or(&path)
        .display();

    println!("digest [{}]: {rel}", args.date);
    println!(
        "  packs_today={} durable={} caveated={} blocked={} queued={}",
        data.new_packs.len(),
        data.claims_durable,
        data.claims_caveated,
        data.sources_blocked,
        data.sources_queued
    );
    Ok(())
}
