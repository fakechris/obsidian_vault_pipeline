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

/// Shadow-build the SQLite read-model (stage 3 of storage-read-model.md).
/// `write_shadow` verifies the CANDIDATE and promotes only on parity, so the
/// last-good generation survives any failure. Never fails the pipeline: the
/// JSON files remain the serving projection during the shadow soak — a
/// shadow failure is a LOUD warning to investigate, not a reason to lose a
/// daily run. Every projection write shadows, including mid-run refreshes —
/// a JSON-only refresh would leave the shadow stale for the rest of the run.
pub(crate) fn shadow_sqlite(
    vault_root: &std::path::Path,
    model: &ovp_index::IndexModel,
    evidence: &ovp_index::EvidenceModel,
) {
    match ovp_index::sqlite::write_shadow(vault_root, model, Some(evidence)) {
        Ok((path, p)) => println!(
            "  sqlite shadow: {} (sources={} packs={} claims={} cards={} units={} sampled={})",
            path.display(),
            p.sources,
            p.packs,
            p.claims,
            p.cards,
            p.units,
            p.sampled
        ),
        Err(e) => eprintln!("warning: sqlite shadow projection FAILED PARITY OR BUILD: {e}"),
    }
}

/// `ovp2 index --verify-sqlite` (stage 3b): re-verify the CURRENT shadow
/// generation against the CURRENT on-disk JSON projections. Read-only — no
/// rebuild; a divergence is a HARD error (non-zero exit) because this is the
/// soak check an operator or cron runs on purpose, unlike the best-effort
/// build-time shadow.
pub fn run_verify_sqlite(vault_root: &std::path::Path) -> Result<(), CliError> {
    let model = read_index(vault_root).map_err(CliError::Io)?;
    // A PRESENT evidence.json must parse — silently downgrading a corrupt
    // file to None would let an empty shadow print "parity OK" while the
    // evidence projection was never verified. Genuinely absent (pre-M31
    // vault) stays a legitimate None.
    let evidence = if ovp_index::evidence_path(vault_root).exists() {
        Some(read_evidence(vault_root).map_err(CliError::Io)?)
    } else {
        None
    };
    let parity = ovp_index::sqlite::verify_shadow(vault_root, &model, evidence.as_ref())
        .map_err(|e| CliError::Io(format!("sqlite shadow parity FAILED: {e}")))?;
    println!(
        "sqlite shadow parity OK (sources={} packs={} claims={} runs={} cards={} units={} sampled={})",
        parity.sources, parity.packs, parity.claims, parity.runs, parity.cards, parity.units,
        parity.sampled
    );
    Ok(())
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
    shadow_sqlite(&args.vault_root, &model, &evidence);
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
    /// Raw `key=value` pairs as typed; parsed and validated in `run_find`.
    pub meta: Vec<String>,
    pub json: bool,
}

/// Split `--meta key=value` on the FIRST `=`, so a value may itself contain
/// one (`x_src=a=b`). An empty key, or no `=` at all, is a usage error rather
/// than a filter that silently matches nothing — a typo that quietly returns
/// zero rows reads exactly like "no such source".
fn parse_meta_filters(raw: &[String]) -> Result<Vec<(String, String)>, CliError> {
    raw.iter()
        .map(|pair| match pair.split_once('=') {
            Some((k, v)) if !k.trim().is_empty() => Ok((k.trim().to_string(), v.to_string())),
            _ => Err(CliError::Io(format!(
                "--meta {pair:?} must be KEY=VALUE with a non-empty key"
            ))),
        })
        .collect()
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
        meta: parse_meta_filters(&args.meta)?,
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

#[cfg(test)]
mod meta_filter_tests {
    use super::parse_meta_filters;

    #[test]
    fn splits_on_the_first_equals_so_values_may_contain_one() {
        let got =
            parse_meta_filters(&["clipped_from=pinboard".to_string(), "x_src=a=b".to_string()])
                .unwrap();
        assert_eq!(
            got,
            vec![
                ("clipped_from".to_string(), "pinboard".to_string()),
                ("x_src".to_string(), "a=b".to_string()),
            ]
        );
    }

    /// An empty value is a legitimate filter (`x_note=`); an empty KEY is not.
    #[test]
    fn empty_value_is_allowed_empty_key_is_not() {
        assert_eq!(
            parse_meta_filters(&["x_note=".to_string()]).unwrap(),
            vec![("x_note".to_string(), String::new())]
        );
        assert!(parse_meta_filters(&["=v".to_string()]).is_err());
    }

    /// A missing `=` must be a usage error: silently matching nothing reads
    /// exactly like "no such source", which sends the operator hunting.
    #[test]
    fn missing_equals_is_a_usage_error() {
        let err = parse_meta_filters(&["clipped_from".to_string()]).unwrap_err();
        assert!(format!("{err:?}").contains("KEY=VALUE"), "{err:?}");
    }
}
