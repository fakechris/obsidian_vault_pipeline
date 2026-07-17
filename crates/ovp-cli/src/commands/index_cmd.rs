//! `index` — rebuild the persistent read model (`.ovp/index/index.json`) from
//! product state, and `find` — query it. The projection is always a FULL
//! rebuild (deterministic, milliseconds at vault scale); rebuilding IS the
//! migration story.

use std::path::PathBuf;

use ovp_domain::tags::TagAliases;
use ovp_index::{
    build_evidence, build_index_with_progress, read_evidence, read_index, run_evidence_query,
    run_query, write_evidence, write_index, Query, QueryKind,
};

use crate::CliError;

pub struct IndexArgs {
    pub vault_root: PathBuf,
    pub date: String,
}

pub fn run_index(args: IndexArgs) -> Result<(), CliError> {
    // Coarse phase lines (flushed) so a large-vault rebuild shows the
    // scan/hash/fold boundaries instead of one silent pause under nohup.
    let mut on_phase = |phase: &str| sayln!("  {phase}");
    let model = build_index_with_progress(&args.vault_root, &args.date, None, &mut on_phase)
        .map_err(CliError::Io)?;
    let rel = write_index(&args.vault_root, &model).map_err(CliError::Io)?;
    let evidence = build_evidence(&args.vault_root, &args.date, &model).map_err(CliError::Io)?;
    let evidence_rel = write_evidence(&args.vault_root, &evidence).map_err(CliError::Io)?;
    let t = &model.totals;
    println!("index [{}]: {rel}", args.date);
    println!(
        "  evidence: {evidence_rel} (cards={} units={} warnings={})",
        evidence.cards.len(),
        evidence.units.len(),
        evidence.warnings.len()
    );
    println!(
        "  sources={} (queued={} processed={} failed={} blocked={} needs_content={} dup={})",
        t.sources, t.queued, t.processed, t.failed, t.blocked, t.needs_content, t.duplicates
    );
    println!(
        "  packs={} claims: durable={} caveated={} runs={}",
        t.packs, t.claims_durable, t.claims_caveated, t.runs
    );
    Ok(())
}

pub struct FindArgs {
    pub vault_root: PathBuf,
    pub term: Option<String>,
    pub kind: Option<String>,
    pub status: Option<String>,
    pub date: Option<String>,
    pub tag: Option<String>,
    pub entity: Option<String>,
    pub json: bool,
}

pub fn run_find(args: FindArgs) -> Result<(), CliError> {
    let model = read_index(&args.vault_root).map_err(CliError::Io)?;
    let kind = match args.kind.as_deref() {
        None => None,
        Some("sources") => Some(QueryKind::Sources),
        Some("packs") => Some(QueryKind::Packs),
        Some("claims") => Some(QueryKind::Claims),
        Some("runs") => Some(QueryKind::Runs),
        Some("cards") => Some(QueryKind::Cards),
        Some("units") => Some(QueryKind::Units),
        Some("tags") => Some(QueryKind::Tags),
        Some("entities") => Some(QueryKind::Entities),
        Some(other) => {
            return Err(CliError::Io(format!(
                "unknown --kind `{other}` (sources|packs|claims|runs|cards|units|tags|entities)"
            )))
        }
    };
    // A queried alias must find its canonical tag's sources, so the query
    // tag runs through the same normalize+alias pipe the index rows did.
    let tag = match args.tag.as_deref() {
        None => None,
        Some(raw) => {
            let aliases = TagAliases::load(&args.vault_root).map_err(CliError::Io)?;
            Some(aliases.resolve_raw(raw).ok_or_else(|| {
                CliError::Io(format!("--tag {raw:?} normalizes to nothing"))
            })?)
        }
    };
    let query = Query {
        kind,
        status: args.status,
        date: args.date,
        term: args.term,
        tag,
        entity: args.entity,
    };
    let hits = match kind {
        Some(QueryKind::Cards | QueryKind::Units) => {
            let evidence = read_evidence(&args.vault_root).map_err(CliError::Io)?;
            run_evidence_query(&evidence, &query, 200)
        }
        _ => run_query(&model, &query),
    };

    if args.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&hits).map_err(|e| CliError::Io(e.to_string()))?
        );
        return Ok(());
    }
    if hits.is_empty() {
        println!("find: no matches (index built {})", model.date);
        return Ok(());
    }
    for hit in &hits {
        match &hit.path {
            Some(p) => println!("[{:6}] {} — {p}", hit.kind, hit.line),
            None => println!("[{:6}] {}", hit.kind, hit.line),
        }
    }
    println!("{} match(es) (index built {})", hits.len(), model.date);
    Ok(())
}
