//! `query` — the L5 read command. Thin shell over `ovp_query::KnowledgeView`:
//! load the read model, run one read op, print text or `--json`. Read-only —
//! no writes, no apply. Exits non-zero only on a load error (corrupt store).

use std::path::PathBuf;

use ovp_query::{KnowledgeView, QueryError};
use serde::Serialize;

use crate::CliError;

/// Which read to perform.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueryKind {
    List,
    Get,
    Search,
    Backlinks,
    Stats,
}

pub struct QueryArgs {
    pub vault_root: PathBuf,
    pub canonical_root: PathBuf,
    pub kind: QueryKind,
    /// Slug (get/backlinks) or substring (search). Ignored for list/stats.
    pub term: Option<String>,
    pub json: bool,
}

/// A concept as printed by the query CLI (a flat, stable shape).
#[derive(Serialize)]
struct ConceptOut<'a> {
    slug: &'a str,
    title: &'a str,
    evergreen_path: &'a str,
    provenance_source_url: &'a str,
}

impl<'a> From<&'a ovp_domain::CanonicalConcept> for ConceptOut<'a> {
    fn from(c: &'a ovp_domain::CanonicalConcept) -> Self {
        Self {
            slug: &c.slug,
            title: &c.title,
            evergreen_path: &c.evergreen_path,
            provenance_source_url: &c.provenance_source_url,
        }
    }
}

pub fn run(args: QueryArgs) -> Result<(), CliError> {
    let view = KnowledgeView::load(&args.vault_root, &args.canonical_root)
        .map_err(|e: QueryError| CliError::Io(format!("query load: {e}")))?;

    match args.kind {
        QueryKind::List => {
            let concepts: Vec<ConceptOut> = view.concepts().iter().map(ConceptOut::from).collect();
            emit_concepts(&concepts, args.json);
        }
        QueryKind::Get => {
            let slug = require_term(&args, "get")?;
            match view.get(&slug) {
                Some(c) => emit_concepts(&[ConceptOut::from(c)], args.json),
                None => {
                    if args.json {
                        println!("null");
                    } else {
                        println!("(no concept with slug `{slug}`)");
                    }
                }
            }
        }
        QueryKind::Search => {
            let needle = require_term(&args, "search")?;
            let concepts: Vec<ConceptOut> =
                view.search(&needle).into_iter().map(ConceptOut::from).collect();
            emit_concepts(&concepts, args.json);
        }
        QueryKind::Backlinks => {
            let slug = require_term(&args, "backlinks")?;
            let links = view.backlinks(&slug);
            if args.json {
                println!(
                    "{}",
                    serde_json::to_string_pretty(links)
                        .map_err(|e| CliError::Io(format!("serializing: {e}")))?
                );
            } else if links.is_empty() {
                println!("(no backlinks for `{slug}`)");
            } else {
                for l in links {
                    println!("{l}");
                }
            }
        }
        QueryKind::Stats => {
            let stats = view.stats();
            if args.json {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&stats)
                        .map_err(|e| CliError::Io(format!("serializing: {e}")))?
                );
            } else {
                println!("concepts:                  {}", stats.concept_count);
                println!("index present:             {}", stats.index_present);
                println!("total backlinks:           {}", stats.total_backlinks);
                println!("concepts without backlinks:{}", stats.concepts_without_backlinks);
            }
        }
    }
    Ok(())
}

fn require_term(args: &QueryArgs, op: &str) -> Result<String, CliError> {
    args.term
        .clone()
        .ok_or_else(|| CliError::Io(format!("`query {op}` requires a TERM argument")))
}

fn emit_concepts(concepts: &[ConceptOut], json: bool) {
    if json {
        match serde_json::to_string_pretty(concepts) {
            Ok(s) => println!("{s}"),
            Err(e) => eprintln!("error: serializing: {e}"),
        }
        return;
    }
    if concepts.is_empty() {
        println!("(no concepts)");
        return;
    }
    for c in concepts {
        println!("{}  —  {}", c.slug, c.title);
    }
}
