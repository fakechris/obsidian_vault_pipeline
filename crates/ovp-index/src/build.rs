//! Build the read model from product state. Full rebuild every time — at
//! vault scale (hundreds of sources) this is milliseconds, and a projection
//! that can always be regenerated from the ledgers needs no migration story:
//! `ovp-next index` IS the migration.
//!
//! Inputs (all optional except the vault root):
//! - `.ovp/daily-runs.jsonl` + `.ovp/intake.jsonl` + `.ovp/pinboard-sync.jsonl`
//! - `50-Inbox/01-Raw/` (files never seen by any ledger → queued)
//! - `40-Resources/Reader/*/` (run-status.json + cards.json)
//! - `.ovp/crystal/` (ledger.jsonl + review.json)
//! - `.ovp/reports/*.json`

use std::collections::HashMap;
use std::path::Path;

use ovp_daily::{read_daily_ledger, RunReport, RunStatus, MAX_FAILURES_BEFORE_BLOCKED};
use ovp_domain::crystal::{fold_ledger, CrystalStatus, ReviewEntry, StoreEvent};
use ovp_domain::units::read_source_from_path;
use ovp_domain::VaultLayout;
use ovp_intake::vaultops::{hex_sha256, read_jsonl, rel_to};
use ovp_intake::{read_intake_ledger, IntakeAction};
use serde::Deserialize;

use crate::model::{
    BlockedSource, ClaimRow, ClaimStatus, IndexModel, OpsState, PackRow, RunRow, RunStats,
    SourceRow, SourceStatus, Totals, INDEX_SCHEMA,
};

/// Build the full read model. `date`/`run_id` only stamp the header.
pub fn build_index(
    vault_root: &Path,
    date: &str,
    run_id: Option<&str>,
) -> Result<IndexModel, String> {
    let layout = VaultLayout::new();

    // Reports first: they carry the run rows AND the only durable record of
    // where the lifecycle phase moved each processed source (the ledger copy
    // is written before the move, deliberately).
    let reports = read_reports(vault_root, &layout)?;
    let runs = runs_from_reports(vault_root, &reports);
    let moved = moved_map(&reports);

    let mut sources = build_sources(vault_root, &layout, &moved)?;
    let packs = build_packs(vault_root, &layout, &sources)?;
    enrich_titles_from_packs(&mut sources, &packs);
    let claims = build_claims(vault_root, &layout)?;

    let totals = Totals {
        sources: sources.len(),
        queued: count(&sources, SourceStatus::Queued),
        processed: count(&sources, SourceStatus::Processed),
        failed: count(&sources, SourceStatus::Failed),
        blocked: count(&sources, SourceStatus::Blocked),
        needs_content: count(&sources, SourceStatus::NeedsContent),
        unparseable: count(&sources, SourceStatus::Unparseable),
        duplicates: count(&sources, SourceStatus::Duplicate),
        packs: packs.len(),
        claims_durable: claims.iter().filter(|c| c.status == ClaimStatus::Durable).count(),
        claims_caveated: claims.iter().filter(|c| c.status == ClaimStatus::Caveated).count(),
        runs: runs.len(),
    };

    let ops = build_ops_state(&sources, &runs, date);

    Ok(IndexModel {
        schema: INDEX_SCHEMA.into(),
        date: date.into(),
        run_id: run_id.map(String::from),
        totals,
        sources,
        packs,
        claims,
        runs,
        ops,
    })
}

/// Persist the model to `.ovp/index/index.json`. Overwrite is CORRECT here —
/// the index is derived, rebuildable state, not a ledger.
pub fn write_index(vault_root: &Path, model: &IndexModel) -> Result<String, String> {
    let layout = VaultLayout::new();
    let target = vault_root.join(layout.index_file());
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string_pretty(model)
        .map_err(|e| format!("serializing index: {e}"))?;
    std::fs::write(&target, format!("{body}\n"))
        .map_err(|e| format!("writing {}: {e}", target.display()))?;
    Ok(rel_to(vault_root, &target))
}

/// Load a persisted index (for `find`).
pub fn read_index(vault_root: &Path) -> Result<IndexModel, String> {
    let layout = VaultLayout::new();
    let path = vault_root.join(layout.index_file());
    let raw = std::fs::read_to_string(&path).map_err(|e| {
        format!(
            "reading {}: {e} (run `ovp-next index --vault-root …` to build it)",
            path.display()
        )
    })?;
    serde_json::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))
}

fn count(rows: &[SourceRow], status: SourceStatus) -> usize {
    rows.iter().filter(|r| r.status == status).count()
}

/// One row per content hash, folded across both ledgers + a raw-inbox scan.
/// `moved` is the report-derived processed-source destination map.
fn build_sources(
    vault_root: &Path,
    layout: &VaultLayout,
    moved: &HashMap<String, String>,
) -> Result<Vec<SourceRow>, String> {
    let daily = read_daily_ledger(&vault_root.join(layout.daily_ledger()))?;
    let intake = read_intake_ledger(&vault_root.join(layout.intake_ledger()))?;

    let mut rows: HashMap<String, SourceRow> = HashMap::new();

    // Intake dispositions first (earliest lifecycle stage).
    for rec in &intake {
        let status = match rec.action {
            IntakeAction::Ingested => SourceStatus::Queued,
            IntakeAction::Duplicate => SourceStatus::Duplicate,
            IntakeAction::NeedsContent => SourceStatus::NeedsContent,
            IntakeAction::Unparseable => SourceStatus::Unparseable,
        };
        // Precedence: a later Duplicate record for the same hash means another
        // COPY was parked — it must not mask the canonical copy still queued
        // in 01-Raw.
        if status == SourceStatus::Duplicate
            && rows.get(&rec.sha256).is_some_and(|r| r.status == SourceStatus::Queued)
        {
            continue;
        }
        rows.insert(rec.sha256.clone(), SourceRow {
            sha256: rec.sha256.clone(),
            status,
            title: rec.title.clone(),
            url: rec.url.clone(),
            rel_path: rec.to.clone().or_else(|| Some(rec.from.clone())),
            date: Some(rec.date.clone()),
            last_run_id: Some(rec.run_id.clone()),
            pack_dir: None,
            fail_count: 0,
            last_reason: rec.note.clone(),
        });
    }

    // Daily attempts override intake state (later lifecycle stage). Records
    // are in append order, so the last one per hash wins.
    let mut fail_counts: HashMap<String, usize> = HashMap::new();
    for rec in &daily {
        let entry = rows.entry(rec.source_sha256.clone()).or_insert_with(|| SourceRow {
            sha256: rec.source_sha256.clone(),
            status: SourceStatus::Queued,
            title: None,
            url: None,
            rel_path: None,
            date: None,
            last_run_id: None,
            pack_dir: None,
            fail_count: 0,
            last_reason: None,
        });
        entry.date = Some(rec.date.clone());
        entry.last_run_id = Some(rec.run_id.clone());
        match rec.status {
            RunStatus::Succeeded => {
                entry.status = SourceStatus::Processed;
                // The processed location comes from the run report (the ledger
                // record is durable BEFORE the lifecycle move, so its own
                // moved_to is None by design).
                entry.rel_path = moved
                    .get(&rec.source_sha256)
                    .cloned()
                    .or_else(|| rec.moved_to.clone())
                    .or_else(|| Some(rec.source_path.clone()));
                entry.pack_dir = rec.pack_dir.clone();
                entry.last_reason = None;
            }
            RunStatus::Failed => {
                let n = fail_counts.entry(rec.source_sha256.clone()).or_insert(0);
                *n += 1;
                entry.fail_count = *n;
                // A later failure never demotes an earlier success (same
                // content re-failing implies a re-run that the dedup gate
                // would normally prevent).
                if entry.status != SourceStatus::Processed {
                    entry.status = if *n >= MAX_FAILURES_BEFORE_BLOCKED {
                        SourceStatus::Blocked
                    } else {
                        SourceStatus::Failed
                    };
                    entry.rel_path = Some(rec.source_path.clone());
                    entry.last_reason = rec.reason.clone();
                }
            }
        }
    }

    // Raw-inbox files no ledger has seen yet (manually dropped in).
    let raw_dir = vault_root.join(layout.inbox_raw_dir());
    if raw_dir.is_dir() {
        for path in collect_markdown(&raw_dir)? {
            let bytes =
                std::fs::read(&path).map_err(|e| format!("reading {}: {e}", path.display()))?;
            let sha = hex_sha256(&bytes);
            rows.entry(sha.clone()).or_insert_with(|| {
                let (title, url) = match read_source_from_path(&path) {
                    Ok(doc) => (
                        Some(doc.title),
                        (!doc.source_url.is_empty()).then_some(doc.source_url),
                    ),
                    Err(_) => (None, None),
                };
                SourceRow {
                    sha256: sha,
                    status: SourceStatus::Queued,
                    title,
                    url,
                    rel_path: Some(rel_to(vault_root, &path)),
                    date: None,
                    last_run_id: None,
                    pack_dir: None,
                    fail_count: 0,
                    last_reason: None,
                }
            });
        }
    }

    // Ghost cleanup: ledgers are append-only and hash-keyed, so a file fixed
    // IN PLACE (enriched needs-content note, repaired frontmatter, edited
    // failed source) gets a NEW hash and a new row — the OLD hash's row would
    // otherwise sit in the attention feed forever, pointing at bytes that no
    // longer exist. A non-Processed row survives only while its recorded file
    // still exists with the recorded content. (Processed rows are history,
    // not work items, and their packs are the evidence — they stay.)
    let mut out: Vec<SourceRow> = rows
        .into_values()
        .filter(|row| {
            if row.status == SourceStatus::Processed {
                return true;
            }
            let Some(rel) = row.rel_path.as_deref() else { return true };
            match std::fs::read(vault_root.join(rel)) {
                Ok(bytes) => hex_sha256(&bytes) == row.sha256,
                Err(_) => false,
            }
        })
        .collect();
    out.sort_by(|a, b| {
        (a.status, a.title.as_deref().unwrap_or(""), a.sha256.as_str())
            .cmp(&(b.status, b.title.as_deref().unwrap_or(""), b.sha256.as_str()))
    });
    Ok(out)
}

/// All run reports, ordered oldest → newest. Collision-suffixed same-run-id
/// files (`<run_id> -2.json`) sort AFTER their base by (date, stem, seq) —
/// plain filename order would put `" -2"` before `".json"` and corrupt
/// "latest run".
fn read_reports(vault_root: &Path, layout: &VaultLayout) -> Result<Vec<(String, RunReport)>, String> {
    let dir = vault_root.join(layout.reports_dir());
    let mut reports = Vec::new();
    if !dir.is_dir() {
        return Ok(reports);
    }
    for entry in std::fs::read_dir(&dir).map_err(|e| format!("reading {}: {e}", dir.display()))? {
        let path = entry.map_err(|e| format!("reading {}: {e}", dir.display()))?.path();
        if path.extension().is_none_or(|e| e != "json") {
            continue;
        }
        let raw = std::fs::read_to_string(&path)
            .map_err(|e| format!("reading {}: {e}", path.display()))?;
        let report: RunReport =
            serde_json::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))?;
        reports.push((rel_to(vault_root, &path), report));
    }
    reports.sort_by_key(|(file, report)| {
        let stem = file.rsplit('/').next().unwrap_or(file).trim_end_matches(".json");
        let (base, seq) = match stem.rsplit_once(" -") {
            Some((b, n)) if n.bytes().all(|c| c.is_ascii_digit()) && !n.is_empty() => {
                (b.to_string(), n.parse::<u32>().unwrap_or(0))
            }
            _ => (stem.to_string(), 1),
        };
        (report.date.clone(), base, seq)
    });
    Ok(reports)
}

fn runs_from_reports(_vault_root: &Path, reports: &[(String, RunReport)]) -> Vec<RunRow> {
    reports
        .iter()
        .map(|(file, report)| RunRow {
            run_id: report.run_id.clone(),
            date: report.date.clone(),
            report_file: file.clone(),
            succeeded: report.reader.succeeded,
            failed: report.reader.failed,
            skipped: report.reader.skipped,
            blocked: report.reader.blocked,
            ingested: report.intake.as_ref().map(|i| i.ingested).unwrap_or(0),
            pinboard_new: report.pinboard.as_ref().map(|p| p.new_notes).unwrap_or(0),
            lifecycle_warnings: report.lifecycle_warnings.len(),
        })
        .collect()
}

/// sha256 → processed destination, folded oldest → newest so the latest
/// report wins.
fn moved_map(reports: &[(String, RunReport)]) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for (_, report) in reports {
        for rec in &report.records {
            if let Some(to) = &rec.moved_to {
                map.insert(rec.source_sha256.clone(), to.clone());
            }
        }
    }
    map
}

#[derive(Deserialize)]
struct RunStatusFile {
    #[serde(default)]
    source: String,
    #[serde(default)]
    accepted_units: usize,
    #[serde(default)]
    cards: usize,
    #[serde(default)]
    json_repaired: bool,
    #[serde(default)]
    parse_error: Option<String>,
}

#[derive(Deserialize)]
struct CardFile {
    #[serde(default)]
    title: String,
}

fn build_packs(
    vault_root: &Path,
    layout: &VaultLayout,
    sources: &[SourceRow],
) -> Result<Vec<PackRow>, String> {
    let reader_root = vault_root.join(layout.reader_root());
    let mut packs = Vec::new();
    if !reader_root.is_dir() {
        return Ok(packs);
    }
    let by_pack: HashMap<&str, &SourceRow> = sources
        .iter()
        .filter_map(|s| s.pack_dir.as_deref().map(|p| (p, s)))
        .collect();

    let mut dirs: Vec<_> = std::fs::read_dir(&reader_root)
        .map_err(|e| format!("reading {}: {e}", reader_root.display()))?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();

    for dir in dirs {
        let status_path = dir.join("run-status.json");
        if !status_path.exists() {
            continue; // not a pack
        }
        let status: RunStatusFile = serde_json::from_str(
            &std::fs::read_to_string(&status_path)
                .map_err(|e| format!("reading {}: {e}", status_path.display()))?,
        )
        .map_err(|e| format!("parsing {}: {e}", status_path.display()))?;
        // A failed attempt also leaves a pack dir (audit artifacts + the
        // fail-loud "pack written" semantics). Only card-bearing packs are
        // PRODUCT; the failure itself is on the source row, not here.
        if status.parse_error.is_some() || status.cards == 0 {
            continue;
        }
        let cards: Vec<CardFile> = std::fs::read_to_string(dir.join("cards.json"))
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default();

        let pack_rel = rel_to(vault_root, &dir);
        let dir_name = dir.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let date = dir_name
            .get(..10)
            .filter(|d| d.bytes().all(|b| b.is_ascii_digit() || b == b'-'))
            .map(String::from);
        packs.push(PackRow {
            title: if status.source.is_empty() { dir_name } else { status.source },
            date,
            units: status.accepted_units,
            cards: status.cards,
            json_repaired: status.json_repaired,
            card_titles: cards.into_iter().map(|c| c.title).collect(),
            source_sha256: by_pack.get(pack_rel.as_str()).map(|s| s.sha256.clone()),
            pack_dir: pack_rel,
        });
    }
    Ok(packs)
}

/// Processed sources carry no title in the daily ledger; the pack knows it.
fn enrich_titles_from_packs(sources: &mut [SourceRow], packs: &[PackRow]) {
    let by_pack: HashMap<&str, &PackRow> =
        packs.iter().map(|p| (p.pack_dir.as_str(), p)).collect();
    for s in sources.iter_mut() {
        if s.title.is_none() {
            if let Some(p) = s.pack_dir.as_deref().and_then(|d| by_pack.get(d)) {
                s.title = Some(p.title.clone());
            }
        }
    }
}

fn build_claims(vault_root: &Path, layout: &VaultLayout) -> Result<Vec<ClaimRow>, String> {
    let store = vault_root.join(layout.crystal_store_dir());
    let mut claims = Vec::new();

    let events: Vec<StoreEvent> = read_jsonl(&store.join("ledger.jsonl"))?;
    for rec in fold_ledger(&events) {
        let status = match rec.status {
            CrystalStatus::Active => ClaimStatus::Durable,
            CrystalStatus::Superseded => ClaimStatus::Superseded,
            CrystalStatus::Retracted => ClaimStatus::Retracted,
            _ => continue,
        };
        claims.push(ClaimRow {
            claim_id: rec.claim_id.clone(),
            claim: rec.claim.clone(),
            theme: (!rec.theme.is_empty()).then(|| rec.theme.clone()),
            status,
            sources: rec.source_cases.clone(),
            strength: enum_str(&rec.strength),
            run_id: Some(rec.run_id.clone()),
        });
    }

    #[derive(Deserialize)]
    struct ReviewFile {
        #[serde(default)]
        review: Vec<ReviewEntry>,
    }
    if let Ok(raw) = std::fs::read_to_string(store.join("review.json")) {
        let file: ReviewFile = serde_json::from_str(&raw)
            .map_err(|e| format!("parsing {}/review.json: {e}", store.display()))?;
        for entry in file.review {
            claims.push(ClaimRow {
                claim_id: entry.claim_id,
                claim: entry.claim,
                theme: (!entry.theme.is_empty()).then_some(entry.theme),
                status: ClaimStatus::Caveated,
                sources: Vec::new(),
                strength: enum_str(&entry.strength),
                run_id: None,
            });
        }
    }

    claims.sort_by(|a, b| (a.claim_id.as_str(), a.claim.as_str()).cmp(&(b.claim_id.as_str(), b.claim.as_str())));
    Ok(claims)
}

/// Stringify a serde snake_case enum without hand-maintaining a mapping.
fn enum_str<T: serde::Serialize>(v: &T) -> Option<String> {
    serde_json::to_value(v).ok().and_then(|j| j.as_str().map(String::from))
}

fn collect_markdown(dir: &Path) -> Result<Vec<std::path::PathBuf>, String> {
    let mut found = Vec::new();
    walk(dir, &mut found)?;
    found.sort();
    Ok(found)
}

fn walk(dir: &Path, out: &mut Vec<std::path::PathBuf>) -> Result<(), String> {
    let entries =
        std::fs::read_dir(dir).map_err(|e| format!("reading {}: {e}", dir.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("reading {}: {e}", dir.display()))?;
        let path = entry.path();
        if entry.file_name().to_string_lossy().starts_with('.') {
            continue;
        }
        if path.is_dir() {
            walk(&path, out)?;
        } else if path.extension().is_some_and(|e| e == "md") {
            out.push(path);
        }
    }
    Ok(())
}

fn build_ops_state(sources: &[SourceRow], runs: &[RunRow], today: &str) -> OpsState {
    let blocked_sources: Vec<BlockedSource> = sources
        .iter()
        .filter(|s| s.status == SourceStatus::Blocked)
        .map(|s| BlockedSource {
            sha256: s.sha256.clone(),
            title: s.title.clone(),
            fail_count: s.fail_count,
            last_reason: s.last_reason.clone(),
            last_attempt: s.date.clone(),
        })
        .collect();

    let queue_depth = sources.iter().filter(|s| s.status == SourceStatus::Queued).count();

    let run_stats = compute_run_stats(runs, today);

    OpsState {
        blocked_sources,
        queue_depth,
        run_stats,
    }
}

fn compute_run_stats(runs: &[RunRow], today: &str) -> Option<RunStats> {
    if runs.is_empty() {
        return None;
    }

    let window_days: usize = 30;
    let cutoff = subtract_days(today, window_days);

    let recent: Vec<&RunRow> = runs
        .iter()
        .filter(|r| r.date.as_str() >= cutoff.as_str())
        .collect();

    if recent.is_empty() {
        return None;
    }

    let total_runs = recent.len();
    let succeeded: usize = recent.iter().map(|r| r.succeeded).sum();
    let failed: usize = recent.iter().map(|r| r.failed).sum();
    let total_attempted = succeeded + failed;
    let success_rate_pct = if total_attempted > 0 {
        (succeeded as f64 / total_attempted as f64) * 100.0
    } else {
        0.0
    };
    let avg_processed_per_run = succeeded as f64 / total_runs as f64;

    Some(RunStats {
        window_days,
        total_runs,
        succeeded,
        failed,
        success_rate_pct,
        avg_processed_per_run,
    })
}

/// Simple date subtraction (YYYY-MM-DD format). Returns a best-effort ISO date
/// `days` before `today`. Ignores leap-second edge cases.
fn subtract_days(today: &str, days: usize) -> String {
    let parts: Vec<&str> = today.split('-').collect();
    if parts.len() != 3 {
        return String::new();
    }
    let (y, m, d) = match (
        parts[0].parse::<i32>(),
        parts[1].parse::<u32>(),
        parts[2].parse::<u32>(),
    ) {
        (Ok(y), Ok(m), Ok(d)) => (y, m, d),
        _ => return String::new(),
    };

    let mut total = to_days(y, m, d) as i64 - days as i64;
    if total < 0 {
        total = 0;
    }
    from_days(total as u32)
}

fn to_days(y: i32, m: u32, d: u32) -> u32 {
    let y = y as u32;
    let mut days = y * 365 + y / 4 - y / 100 + y / 400;
    let month_days = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    days += month_days[(m - 1) as usize];
    if m > 2 && is_leap(y) {
        days += 1;
    }
    days + d
}

fn from_days(total: u32) -> String {
    let mut y = total / 366;
    loop {
        let jan1 = to_days(y as i32, 1, 1);
        if jan1 > total {
            y -= 1;
        } else {
            break;
        }
    }
    let jan1 = to_days(y as i32, 1, 1);
    let mut rem = total - jan1 + 1;
    let leap = is_leap(y);
    let mdays = [
        31,
        if leap { 29 } else { 28 },
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    ];
    let mut m = 0;
    for (i, &md) in mdays.iter().enumerate() {
        if rem <= md {
            m = i + 1;
            break;
        }
        rem -= md;
    }
    if m == 0 {
        m = 12;
    }
    format!("{y:04}-{m:02}-{rem:02}")
}

fn is_leap(y: u32) -> bool {
    y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)
}
