//! `rag` — the L6 read command. Thin shell over `ovp_rag`: load the corpus from
//! the L5 read model, retrieve → rank → build a bounded context, print text or
//! `--json`. Read-only — no writes, no apply, no run. Exits non-zero only on a
//! corrupt/unreadable read model (or an unreadable note body); a valid query
//! with zero results exits 0.

use std::path::PathBuf;

use ovp_rag::{ContextBuilder, RagContext, RagCorpus, Ranker, Retriever};

use crate::CliError;

pub struct RagArgs {
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub query: String,
    /// Max concepts to return (caps both the ranker and the context builder).
    pub limit: usize,
    pub json: bool,
}

pub fn run(args: RagArgs) -> Result<(), CliError> {
    let corpus = RagCorpus::load(&args.vault_root, &args.canonical_root)
        .map_err(|e| CliError::Io(format!("rag load: {e}")))?;

    let scored = Retriever::new().score(&corpus, &args.query);
    let ranked = Ranker::with_limit(args.limit).rank(scored);
    let ctx = ContextBuilder { max_concepts: args.limit, ..ContextBuilder::default() }
        .build(&corpus, &ranked, &args.query);

    if args.json {
        let json = serde_json::to_string_pretty(&ctx)
            .map_err(|e| CliError::Io(format!("serializing: {e}")))?;
        println!("{json}");
    } else {
        print_context(&ctx);
    }
    Ok(())
}

fn print_context(ctx: &RagContext) {
    println!("query: {}", ctx.query);
    if ctx.selected.is_empty() {
        println!("(no matching concepts)");
        return;
    }
    for (i, c) in ctx.selected.iter().enumerate() {
        println!();
        println!("{}. {}  [{}]  score={}", i + 1, c.title, c.slug, c.score);
        println!("   note: {}", c.evergreen_path);
        if let Some(snippet) = &c.snippet {
            println!("   snippet: {snippet}");
        }
        if !c.backlinks.is_empty() {
            println!("   backlinks: {}", c.backlinks.join(", "));
        }
        let why: Vec<String> = c
            .reasons
            .iter()
            .map(|r| format!("{:?}/{}(+{})", r.field, r.term, r.contribution))
            .collect();
        if !why.is_empty() {
            println!("   why: {}", why.join(", "));
        }
    }
}
