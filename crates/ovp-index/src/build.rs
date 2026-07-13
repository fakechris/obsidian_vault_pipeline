//! Build the read model from product state. Full rebuild every time — at
//! vault scale (hundreds of sources) this is milliseconds, and a projection
//! that can always be regenerated from the ledgers needs no migration story:
//! `ovp2 index` IS the migration.
//!
//! Inputs (all optional except the vault root):
//! - `.ovp/daily-runs.jsonl` + `.ovp/intake.jsonl` + `.ovp/pinboard-sync.jsonl`
//! - `50-Inbox/01-Raw/` (files never seen by any ledger → queued)
//! - `40-Resources/Reader/*/` (run-status.json + cards.json)
//! - `.ovp/crystal/` (ledger.jsonl + review.json)
//! - `.ovp/reports/*.json`

use std::collections::HashMap;
use std::path::Path;

use ovp_daily::{MAX_FAILURES_BEFORE_BLOCKED, RunReport, RunStatus, read_daily_ledger};
use ovp_domain::VaultLayout;
use ovp_domain::crystal::themes::{ThemesFile, UNCLASSIFIED_THEME};
use ovp_domain::crystal::{CrystalStatus, ReviewEntry, StoreEvent, fold_ledger};
use ovp_domain::units::read_source_from_path;
use ovp_intake::vaultops::{hex_sha256, read_jsonl, rel_to};
use ovp_intake::{IntakeAction, read_intake_ledger};
use serde::Deserialize;

use crate::model::{
    BlockedSource, ClaimRow, ClaimStatus, INDEX_SCHEMA, IndexModel, LastRunModel, OpsState,
    PackRow, RecentSourceModel, RunRow, RunStats, SourceRow, SourceStatus, StuckSource, Totals,
};

/// UTC wall-clock instant as RFC3339 — the `built_at` stamp. The one
/// non-deterministic input to the index build; tests that need determinism
/// stamp `date` and use [`build_index_at`] to inject a fixed instant instead.
pub fn now_rfc3339() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let dur = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    chrono::DateTime::<chrono::Utc>::from(UNIX_EPOCH + dur)
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

/// Build the full read model. `date` stamps the deterministic day header;
/// `run_id` names which run produced this projection, falling back to an
/// `index-<built_at>` marker so the field is never silently `None`. `built_at`
/// is stamped unconditionally with the wall-clock instant so a stale
/// projection never renders identically to a fresh one.
pub fn build_index(
    vault_root: &Path,
    date: &str,
    run_id: Option<&str>,
) -> Result<IndexModel, String> {
    build_index_at_with_progress(vault_root, date, run_id, &now_rfc3339(), &mut |_| {})
}

/// [`build_index`] with an injected `built_at` instant so a test can assert the
/// stamp deterministically (P1's determinism seam). When `run_id` is `None`,
/// the projection synthesizes `index-<built_at>` so it always names a producer.
pub fn build_index_at(
    vault_root: &Path,
    date: &str,
    run_id: Option<&str>,
    built_at: &str,
) -> Result<IndexModel, String> {
    build_index_at_with_progress(vault_root, date, run_id, built_at, &mut |_| {})
}

/// [`build_index`] with a coarse phase callback. The projection is print-free
/// (this is a domain crate); the CLI passes a callback that renders a flushed
/// `"<phase> (N …)"` line per phase so `ovp2 index` on a large vault shows the
/// scan/hash/backfill boundaries instead of one silent pause. Callbacks fire
/// AFTER each phase completes, carrying its count for the operator.
pub fn build_index_with_progress(
    vault_root: &Path,
    date: &str,
    run_id: Option<&str>,
    on_phase: &mut dyn FnMut(&str),
) -> Result<IndexModel, String> {
    build_index_at_with_progress(vault_root, date, run_id, &now_rfc3339(), on_phase)
}

/// The real build. Composes P1's `built_at`/`run_id` stamping with P2's phase
/// callbacks: every phase boundary fires `on_phase`, and the projection is
/// stamped with `built_at` (wall-clock or injected) and a `run_id` that never
/// silently stays `None`. The three functions above are thin wrappers that pin
/// `built_at` and/or `on_phase` for their respective callers/tests.
pub fn build_index_at_with_progress(
    vault_root: &Path,
    date: &str,
    run_id: Option<&str>,
    built_at: &str,
    on_phase: &mut dyn FnMut(&str),
) -> Result<IndexModel, String> {
    let layout = VaultLayout::new();

    // Reports first: they carry the run rows AND the only durable record of
    // where the lifecycle phase moved each processed source (the ledger copy
    // is written before the move, deliberately).
    let reports = read_reports(vault_root, &layout)?;
    let runs = runs_from_reports(vault_root, &reports);
    let moved = moved_map(&reports);
    on_phase(&format!("read {} run report(s)", reports.len()));

    let mut sources = build_sources(vault_root, &layout, &moved)?;
    on_phase(&format!("scanned {} source(s)", sources.len()));
    let mut packs = build_packs(vault_root, &layout, &sources)?;
    backfill_corpus_packs(vault_root, &layout, &mut sources, &mut packs)?;
    on_phase(&format!("hashed {} pack(s)", packs.len()));
    enrich_titles_from_packs(&mut sources, &packs);
    let claims = build_claims(vault_root, &layout)?;
    on_phase(&format!("folded {} claim(s)", claims.len()));

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
        claims_durable: claims
            .iter()
            .filter(|c| c.status == ClaimStatus::Durable)
            .count(),
        claims_caveated: claims
            .iter()
            .filter(|c| c.status == ClaimStatus::Caveated)
            .count(),
        runs: runs.len(),
    };

    let mut ops = build_ops_state(&sources, &runs, &reports, date);
    ops.last_run = build_last_run(vault_root);

    // Never silently None: an ad-hoc `index`/`console` build with no run to
    // name still gets an `index-<built_at>` marker so every projection can be
    // traced to the moment it was built.
    let run_id = run_id
        .map(String::from)
        .unwrap_or_else(|| format!("index-{built_at}"));

    Ok(IndexModel {
        schema: INDEX_SCHEMA.into(),
        date: date.into(),
        built_at: Some(built_at.into()),
        run_id: Some(run_id),
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
    let body =
        serde_json::to_string_pretty(model).map_err(|e| format!("serializing index: {e}"))?;
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
            "reading {}: {e} (run `ovp2 index --vault-root …` to build it)",
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
            && rows
                .get(&rec.sha256)
                .is_some_and(|r| r.status == SourceStatus::Queued)
        {
            continue;
        }
        rows.insert(
            rec.sha256.clone(),
            SourceRow {
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
            },
        );
    }

    // Daily attempts override intake state (later lifecycle stage). Records
    // are in append order, so the last one per hash wins.
    let mut fail_counts: HashMap<String, usize> = HashMap::new();
    for rec in &daily {
        let entry = rows
            .entry(rec.source_sha256.clone())
            .or_insert_with(|| SourceRow {
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
            let Some(rel) = row.rel_path.as_deref() else {
                return true;
            };
            match std::fs::read(vault_root.join(rel)) {
                Ok(bytes) => hex_sha256(&bytes) == row.sha256,
                Err(_) => false,
            }
        })
        .collect();
    sort_sources(&mut out);
    Ok(out)
}

/// Canonical source-row order: status band, then title, then hash. Shared by
/// the ledger fold and the corpus backfill so appended rows keep the
/// projection deterministic.
fn sort_sources(rows: &mut [SourceRow]) {
    rows.sort_by(|a, b| {
        (
            a.status,
            a.title.as_deref().unwrap_or(""),
            a.sha256.as_str(),
        )
            .cmp(&(
                b.status,
                b.title.as_deref().unwrap_or(""),
                b.sha256.as_str(),
            ))
    });
}

/// All run reports, ordered oldest → newest. Collision-suffixed same-run-id
/// files (`<run_id> -2.json`) sort AFTER their base by (date, stem, seq) —
/// plain filename order would put `" -2"` before `".json"` and corrupt
/// "latest run".
fn read_reports(
    vault_root: &Path,
    layout: &VaultLayout,
) -> Result<Vec<(String, RunReport)>, String> {
    let dir = vault_root.join(layout.reports_dir());
    let mut reports = Vec::new();
    if !dir.is_dir() {
        return Ok(reports);
    }
    for entry in std::fs::read_dir(&dir).map_err(|e| format!("reading {}: {e}", dir.display()))? {
        let path = entry
            .map_err(|e| format!("reading {}: {e}", dir.display()))?
            .path();
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
        let stem = file
            .rsplit('/')
            .next()
            .unwrap_or(file)
            .trim_end_matches(".json");
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

impl RunStatusFile {
    /// The PRODUCT eligibility rule: a failed attempt also leaves a pack dir
    /// (audit artifacts + the fail-loud "pack written" semantics), marked by
    /// a `parse_error` or zero cards in run-status.json. Only card-bearing
    /// packs are product; the failure itself lives on the source row.
    fn is_product(&self) -> bool {
        self.parse_error.is_none() && self.cards > 0
    }
}

/// Does this reader dir hold a FAILED reader attempt — a run-status.json that
/// fails the index's product-pack rule ([`RunStatusFile::is_product`], the
/// predicate `build_packs` applies)? Dirs WITHOUT run-status.json are not
/// judged here (legacy corpus packs predate the file). An unreadable or
/// unparseable run-status.json counts as failed: non-index consumers of the
/// reader tree (e.g. `crystal-themes` input collection) must SKIP such dirs,
/// unlike the index build itself, which fails loud on them.
pub fn failed_reader_attempt(dir: &Path) -> bool {
    let status_path = dir.join("run-status.json");
    if !status_path.exists() {
        return false;
    }
    match std::fs::read_to_string(&status_path)
        .ok()
        .and_then(|raw| serde_json::from_str::<RunStatusFile>(&raw).ok())
    {
        Some(status) => !status.is_product(),
        None => true,
    }
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
        // Product eligibility — see `RunStatusFile::is_product` (shared with
        // `failed_reader_attempt` so other reader-tree consumers apply the
        // same rule).
        if !status.is_product() {
            continue;
        }
        let cards: Vec<CardFile> = std::fs::read_to_string(dir.join("cards.json"))
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default();

        let pack_rel = rel_to(vault_root, &dir);
        let dir_name = dir
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let date = dir_name
            .get(..10)
            .filter(|d| d.bytes().all(|b| b.is_ascii_digit() || b == b'-'))
            .map(String::from);
        packs.push(PackRow {
            title: if status.source.is_empty() {
                dir_name
            } else {
                status.source
            },
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
    let by_pack: HashMap<&str, &PackRow> = packs.iter().map(|p| (p.pack_dir.as_str(), p)).collect();
    for s in sources.iter_mut() {
        if s.title.is_none()
            && let Some(p) = s.pack_dir.as_deref().and_then(|d| by_pack.get(d)) {
                s.title = Some(p.title.clone());
            }
    }
}

/// Skip hashing anything larger than this during the corpus backfill — real
/// captures are small markdown files; hashing the odd huge export would
/// dominate the index build for no join value.
const MAX_BACKFILL_FILE_BYTES: u64 = 2 * 1024 * 1024;

/// One 8-hex-prefix bucket of the candidate-file hash map. Two files with
/// the same prefix but DIFFERENT full hashes make the prefix ambiguous:
/// packs pointing at it stay unjoined rather than guessed.
#[derive(Debug, PartialEq)]
enum PrefixHit {
    Unique { sha256: String, rel_path: String },
    Ambiguous,
}

/// Corpus reader packs predate the daily ledgers, so `build_packs` finds no
/// SourceRow to join them to — their Library rows, `/api/source/:sha` pages
/// and Ask citation links were dead. Their dir names carry the join key:
/// `<sha256-first-8-hex>-<date>_<title>`, where the 8-hex prefix hashes the
/// source md now living under `50-Inbox/03-Processed/` (raw inbox as
/// fallback). Hash the candidate files ONCE per build, join by prefix, and
/// synthesize a Processed SourceRow when the ledgers know nothing about the
/// hash. When the raw-inbox SCAN already made a queued row for the same
/// bytes (file still in 01-Raw, no ledger record), that row is promoted to
/// processed instead of duplicated. Backfilled rows are recognizable by
/// construction: `last_run_id` is None (every ledger-derived row has one)
/// and `pack_dir` points at the corpus pack they were joined to.
fn backfill_corpus_packs(
    vault_root: &Path,
    layout: &VaultLayout,
    sources: &mut Vec<SourceRow>,
    packs: &mut [PackRow],
) -> Result<(), String> {
    let unjoined: Vec<usize> = packs
        .iter()
        .enumerate()
        .filter(|(_, p)| p.source_sha256.is_none() && corpus_hash8(&p.pack_dir).is_some())
        .map(|(i, _)| i)
        .collect();
    if unjoined.is_empty() {
        return Ok(()); // no corpus candidates → skip the vault walk entirely
    }

    let by_prefix = hash_candidate_files(vault_root, layout)?;
    let mut by_sha: HashMap<String, usize> = sources
        .iter()
        .enumerate()
        .map(|(i, s)| (s.sha256.clone(), i))
        .collect();
    let mut changed = false;
    for i in unjoined {
        let Some(prefix) = corpus_hash8(&packs[i].pack_dir) else {
            continue;
        };
        let Some(PrefixHit::Unique { sha256, rel_path }) = by_prefix.get(&prefix) else {
            continue; // no candidate file, or an ambiguous prefix — never guess
        };
        packs[i].source_sha256 = Some(sha256.clone());
        match by_sha.get(sha256).copied() {
            None => {
                sources.push(SourceRow {
                    sha256: sha256.clone(),
                    status: SourceStatus::Processed,
                    title: Some(packs[i].title.clone()),
                    url: None,
                    rel_path: Some(rel_path.clone()),
                    date: corpus_date(&packs[i].pack_dir),
                    last_run_id: None, // no ledger record — the backfill provenance marker
                    pack_dir: Some(packs[i].pack_dir.clone()),
                    fail_count: 0,
                    last_reason: None,
                });
                by_sha.insert(sha256.clone(), sources.len() - 1);
                changed = true;
            }
            Some(at) => {
                // A raw-scan row (queued, no run id) is this same corpus file
                // seen by the 01-Raw sweep — promote it to processed rather
                // than leaving a pack-linked source stuck in the queue (its
                // corpus case would otherwise be underivable). Ledger-derived
                // rows (any last_run_id) stay untouched: the ledgers are the
                // authority on lifecycle.
                let row = &mut sources[at];
                if row.last_run_id.is_none() && row.status == SourceStatus::Queued {
                    row.status = SourceStatus::Processed;
                    row.pack_dir = Some(packs[i].pack_dir.clone());
                    // Preference rules: frontmatter title/url read by the raw
                    // scan are richer than pack metadata — keep them and fill
                    // only the gaps from the pack; rel_path stays (it points
                    // at the bytes that were actually hashed).
                    if row.title.is_none() {
                        row.title = Some(packs[i].title.clone());
                    }
                    if row.date.is_none() {
                        row.date = corpus_date(&packs[i].pack_dir);
                    }
                    changed = true;
                }
            }
        }
    }
    if changed {
        sort_sources(sources);
    }
    Ok(())
}

/// `…/00044cfd-2026-05-07_Title` → `00044cfd`: the pack dir basename must
/// start with EXACTLY 8 hex chars followed by `-`. Modern pack dirs
/// (`<date>_<title>-<hash8>`) never match — their fifth char is already `-`.
fn corpus_hash8(pack_dir: &str) -> Option<String> {
    let name = pack_dir.rsplit('/').next().unwrap_or(pack_dir);
    let bytes = name.as_bytes();
    if bytes.len() > 8 && bytes[8] == b'-' && bytes[..8].iter().all(|b| b.is_ascii_hexdigit()) {
        Some(name[..8].to_ascii_lowercase())
    } else {
        None
    }
}

/// Date segment of a corpus pack dir (`<hash8>-<YYYY-MM-DD>_…`) — the same
/// loose digits-and-dashes check `build_packs` applies to modern dir names.
fn corpus_date(pack_dir: &str) -> Option<String> {
    let name = pack_dir.rsplit('/').next().unwrap_or(pack_dir);
    name.get(9..19)
        .filter(|d| d.bytes().all(|b| b.is_ascii_digit() || b == b'-'))
        .map(String::from)
}

/// Hash every candidate source md (processed tree first, then the raw inbox)
/// into an 8-hex-prefix map. Built at most once per index build, and only
/// when at least one unjoined corpus pack exists. `collect_markdown` walks
/// in sorted order, so duplicate-content ties resolve deterministically.
fn hash_candidate_files(
    vault_root: &Path,
    layout: &VaultLayout,
) -> Result<HashMap<String, PrefixHit>, String> {
    let mut map: HashMap<String, PrefixHit> = HashMap::new();
    let roots = [
        vault_root.join(layout.processed_root()),
        vault_root.join(layout.inbox_raw_dir()),
    ];
    for root in roots {
        if !root.is_dir() {
            continue;
        }
        for path in collect_markdown(&root)? {
            let meta = std::fs::metadata(&path)
                .map_err(|e| format!("reading {}: {e}", path.display()))?;
            if meta.len() > MAX_BACKFILL_FILE_BYTES {
                continue;
            }
            let bytes =
                std::fs::read(&path).map_err(|e| format!("reading {}: {e}", path.display()))?;
            let sha = hex_sha256(&bytes);
            let prefix = sha[..8].to_string();
            insert_prefix_hit(&mut map, prefix, sha, rel_to(vault_root, &path));
        }
    }
    Ok(map)
}

/// Pure insert step, split out because an 8-hex prefix collision cannot be
/// forged in an on-disk fixture — the unit test drives this directly. Same
/// full hash twice is duplicate CONTENT (first path wins); different full
/// hashes poison the prefix as Ambiguous.
fn insert_prefix_hit(
    map: &mut HashMap<String, PrefixHit>,
    prefix: String,
    sha256: String,
    rel_path: String,
) {
    use std::collections::hash_map::Entry;
    match map.entry(prefix) {
        Entry::Vacant(v) => {
            v.insert(PrefixHit::Unique { sha256, rel_path });
        }
        Entry::Occupied(mut o) => {
            if let PrefixHit::Unique {
                sha256: existing, ..
            } = o.get()
                && existing != &sha256 {
                    o.insert(PrefixHit::Ambiguous);
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
            lane: None,
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
            let sources: Vec<String> = {
                let mut s: Vec<String> =
                    entry.citations.iter().map(|c| c.case_id.clone()).collect();
                s.sort_unstable();
                s.dedup();
                s
            };
            claims.push(ClaimRow {
                claim_id: entry.claim_id,
                claim: entry.claim,
                theme: (!entry.theme.is_empty()).then_some(entry.theme),
                status: ClaimStatus::Caveated,
                sources,
                strength: enum_str(&entry.strength),
                run_id: None,
                lane: enum_str(&entry.lane),
            });
        }
    }

    // Semantic theme PROJECTION (M-semantic-themes): when `themes.json`
    // exists, a claim's display theme is the majority community label among
    // its cited packs (ties → lexicographically first; nothing mapped →
    // "Unclassified"). The ledger keeps whatever theme synthesis stamped —
    // claims are never re-synthesized to re-theme; this overlay is rebuilt on
    // every index build. Without themes.json the ledger theme passes through.
    if let Some(themes) = ThemesFile::load(&store.join("themes.json"))? {
        for row in claims.iter_mut() {
            row.theme = Some(
                themes
                    .majority_label(&row.sources)
                    .unwrap_or_else(|| UNCLASSIFIED_THEME.to_string()),
            );
        }
    }

    claims.sort_by(|a, b| {
        (a.claim_id.as_str(), a.claim.as_str()).cmp(&(b.claim_id.as_str(), b.claim.as_str()))
    });
    Ok(claims)
}

/// Stringify a serde snake_case enum without hand-maintaining a mapping.
fn enum_str<T: serde::Serialize>(v: &T) -> Option<String> {
    serde_json::to_value(v)
        .ok()
        .and_then(|j| j.as_str().map(String::from))
}

fn collect_markdown(dir: &Path) -> Result<Vec<std::path::PathBuf>, String> {
    let mut found = Vec::new();
    walk(dir, &mut found)?;
    found.sort();
    Ok(found)
}

fn walk(dir: &Path, out: &mut Vec<std::path::PathBuf>) -> Result<(), String> {
    let entries = std::fs::read_dir(dir).map_err(|e| format!("reading {}: {e}", dir.display()))?;
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

fn build_ops_state(
    sources: &[SourceRow],
    runs: &[RunRow],
    reports: &[(String, RunReport)],
    today: &str,
) -> OpsState {
    let mut blocked_sources: Vec<BlockedSource> = sources
        .iter()
        .filter(|s| s.status == SourceStatus::Blocked)
        .map(|s| BlockedSource {
            sha256: s.sha256.clone(),
            title: s.title.clone(),
            fail_count: s.fail_count,
            last_reason: s.last_reason.clone(),
            last_attempt: s.date.clone(),
            // Aging: whole days since the last attempt. Chronic blocks (large
            // days_stuck) are what the console/portal escalate amber→red.
            days_stuck: s.date.as_deref().and_then(|d| days_between(d, today)),
        })
        .collect();
    // Most-stuck first so the render escalates the worst offenders at the top.
    blocked_sources.sort_by(|a, b| b.days_stuck.cmp(&a.days_stuck));

    let mut stuck_sources: Vec<StuckSource> = sources
        .iter()
        .filter(|s| s.status == SourceStatus::NeedsContent)
        .map(|s| StuckSource {
            sha256: s.sha256.clone(),
            title: s.title.clone(),
            first_seen: s.date.clone(),
            days_stuck: s.date.as_deref().and_then(|d| days_between(d, today)),
        })
        .collect();
    stuck_sources.sort_by(|a, b| b.days_stuck.cmp(&a.days_stuck));

    let queue_depth = sources
        .iter()
        .filter(|s| s.status == SourceStatus::Queued)
        .count();

    // "Backlog not draining" signal: the most recent run's capped count. Reports
    // are sorted ascending by (date, seq) in read_reports, so the last is newest.
    let capped = reports
        .last()
        .map(|(_, r)| r.reader.capped)
        .unwrap_or(0);

    let run_stats = compute_run_stats(runs, today);

    OpsState {
        blocked_sources,
        stuck_sources,
        queue_depth,
        capped,
        run_stats,
        last_run: None,
    }
}

/// Whole days from `from` to `to` (both YYYY-MM-DD). `None` on unparseable
/// input; `Some(0)` when equal or `to` precedes `from` (aging never goes
/// negative). The build date is `to`; the source's last activity is `from`.
fn days_between(from: &str, to: &str) -> Option<usize> {
    let parse = |s: &str| -> Option<u32> {
        let p: Vec<&str> = s.split('-').collect();
        if p.len() != 3 {
            return None;
        }
        match (p[0].parse::<i32>(), p[1].parse::<u32>(), p[2].parse::<u32>()) {
            (Ok(y), Ok(m), Ok(d)) if (1..=12).contains(&m) && (1..=31).contains(&d) => {
                Some(to_days(y, m, d))
            }
            _ => None,
        }
    };
    let (a, b) = (parse(from)?, parse(to)?);
    Some(b.saturating_sub(a) as usize)
}

/// Map a raw heartbeat record to the read-model shape. Pure; the string
/// status keeps the model self-describing for the static console pages and the
/// SPA alike.
pub fn last_run_to_model(hb: ovp_daily::LastRun) -> LastRunModel {
    let status = match hb.status {
        ovp_daily::LastRunStatus::Running => "running",
        ovp_daily::LastRunStatus::Completed => "completed",
        ovp_daily::LastRunStatus::Failed => "failed",
        ovp_daily::LastRunStatus::Aborted => "aborted",
    };
    LastRunModel {
        run_id: hb.run_id,
        started_at: hb.started_at,
        ended_at: hb.ended_at,
        status: status.into(),
        processed: hb.processed,
        failed: hb.failed,
        blocked: hb.blocked,
        capped: hb.capped,
        queued_after: hb.queued_after,
        processed_so_far: hb.processed_so_far,
        total_planned: hb.total_planned,
        current: hb.current,
        recent: hb
            .recent
            .into_iter()
            .map(|r| RecentSourceModel {
                seq: r.seq,
                title: r.title,
                status: r.status,
                units: r.units,
                cards: r.cards,
                reason: r.reason,
                at: r.at,
            })
            .collect(),
        error: hb.error,
    }
}

/// Read the run-liveness heartbeat (`.ovp/last-run.json`) LIVE into the
/// read-model shape, PRESERVING errors. This is the fail-loud reader the
/// server and `doctor` use so a corrupt/unreadable heartbeat is never silently
/// treated as absent (that would hide the very failed/aborted run this feature
/// exists to surface). `Ok(None)` = fresh vault (no file); `Err` = present but
/// unparseable.
pub fn read_last_run_model(vault_root: &Path) -> Result<Option<LastRunModel>, String> {
    Ok(ovp_daily::read_last_run(vault_root)?.map(last_run_to_model))
}

/// Read the heartbeat for the BAKED projection. The projection must always
/// build, so a corrupt file degrades to `None` here (the CLI/`doctor`/server
/// surface the corruption loudly via [`read_last_run_model`]); an absent file
/// on a fresh vault is `None` too. NOTE: the baked field is a build-time
/// SNAPSHOT — it can say `running` forever if the process died before the
/// phase-5 rebuild. Live surfaces (server `/api/*`, `doctor`, `schedule
/// status`) must read the sidecar fresh, never trust this field.
fn build_last_run(vault_root: &Path) -> Option<LastRunModel> {
    read_last_run_model(vault_root).ok().flatten()
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
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
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
    y.is_multiple_of(4) && (!y.is_multiple_of(100) || y.is_multiple_of(400))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn days_between_counts_whole_days() {
        assert_eq!(days_between("2026-07-01", "2026-07-08"), Some(7));
        // Same day → 0 (a source attempted today is not "stuck").
        assert_eq!(days_between("2026-07-08", "2026-07-08"), Some(0));
        // Across a month boundary (June has 30 days).
        assert_eq!(days_between("2026-06-28", "2026-07-02"), Some(4));
        // Across a leap-year Feb (2024-02-29 exists).
        assert_eq!(days_between("2024-02-27", "2024-03-01"), Some(3));
    }

    #[test]
    fn days_between_never_negative_and_none_on_garbage() {
        // `to` precedes `from` → clamped to 0, never underflows.
        assert_eq!(days_between("2026-07-08", "2026-07-01"), Some(0));
        // Unparseable inputs.
        assert_eq!(days_between("not-a-date", "2026-07-08"), None);
        assert_eq!(days_between("2026-07-08", ""), None);
        assert_eq!(days_between("2026-13-01", "2026-07-08"), None);
    }

    #[test]
    fn ops_state_ages_blocked_and_needs_content() {
        let src = |sha: &str, status: SourceStatus, date: &str| SourceRow {
            sha256: sha.into(),
            status,
            title: Some(format!("t-{sha}")),
            url: None,
            rel_path: None,
            date: Some(date.into()),
            last_run_id: None,
            pack_dir: None,
            fail_count: 3,
            last_reason: Some("boom".into()),
        };
        let sources = vec![
            src("aaaa", SourceStatus::Blocked, "2026-06-30"), // 8 days stuck
            src("bbbb", SourceStatus::Blocked, "2026-07-06"), // 2 days stuck
            src("cccc", SourceStatus::NeedsContent, "2026-07-01"), // 7 days
            src("dddd", SourceStatus::Queued, "2026-07-08"),  // queue only
        ];
        let ops = build_ops_state(&sources, &[], &[], "2026-07-08");

        assert_eq!(ops.queue_depth, 1);
        // Blocked: aged, most-stuck first.
        assert_eq!(ops.blocked_sources.len(), 2);
        assert_eq!(ops.blocked_sources[0].sha256, "aaaa");
        assert_eq!(ops.blocked_sources[0].days_stuck, Some(8));
        assert_eq!(ops.blocked_sources[1].days_stuck, Some(2));
        assert!(ops.blocked_sources[0].days_stuck >= Some(crate::model::DAYS_STUCK_RED));
        // Needs-content aged separately.
        assert_eq!(ops.stuck_sources.len(), 1);
        assert_eq!(ops.stuck_sources[0].sha256, "cccc");
        assert_eq!(ops.stuck_sources[0].days_stuck, Some(7));
    }

    #[test]
    fn last_run_none_when_no_heartbeat_file() {
        let tmp = tempfile::tempdir().unwrap();
        let model = build_index(tmp.path(), "2026-07-12", None).unwrap();
        assert!(model.ops.last_run.is_none(), "fresh vault → no last_run");
    }

    #[test]
    fn last_run_surfaces_completed_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        let counts = ovp_daily::RunCounts {
            processed: 8,
            failed: 0,
            blocked: 1,
            capped: 2,
            queued_after: 180,
        };
        let (guard, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "daily-2026-07-12");
        assert!(guard.finalize_completed(counts).is_none());

        let model = build_index(tmp.path(), "2026-07-12", None).unwrap();
        let lr = model.ops.last_run.expect("heartbeat surfaced");
        assert_eq!(lr.status, "completed");
        assert_eq!(lr.run_id, "daily-2026-07-12");
        assert_eq!(lr.processed, Some(8));
        assert_eq!(lr.queued_after, Some(180));
        assert!(lr.ended_at.is_some());
        assert!(lr.error.is_none());
    }

    #[test]
    fn last_run_surfaces_aborted_heartbeat() {
        let tmp = tempfile::tempdir().unwrap();
        {
            let (_g, _) = ovp_daily::HeartbeatGuard::start(tmp.path(), "r");
            // drop without finalize → aborted
        }
        let model = build_index(tmp.path(), "2026-07-12", None).unwrap();
        let lr = model.ops.last_run.expect("aborted heartbeat surfaced");
        assert_eq!(lr.status, "aborted");
        assert!(lr.error.is_some());
    }

    #[test]
    fn corpus_hash8_matches_only_the_legacy_prefix_shape() {
        // Legacy corpus dir: 8 hex + '-'.
        assert_eq!(
            corpus_hash8("40-Resources/Reader/00044cfd-2026-05-07_Claude_Code"),
            Some("00044cfd".into())
        );
        // Uppercase hex normalizes to the hex_sha256 lowercase alphabet.
        assert_eq!(
            corpus_hash8("40-Resources/Reader/00044CFD-2026-05-07_X"),
            Some("00044cfd".into())
        );
        // Modern pack dir (`<date>_<title>-<hash8>`): fifth char is '-'.
        assert_eq!(
            corpus_hash8("40-Resources/Reader/2026-06-09_Good Article-aaaa1111"),
            None
        );
        // Nine leading hex chars → char 8 is hex, not '-'.
        assert_eq!(corpus_hash8("40-Resources/Reader/00044cfd1-2026_X"), None);
        // Non-hex within the prefix.
        assert_eq!(corpus_hash8("40-Resources/Reader/00zz4cfd-2026_X"), None);
        // Too short.
        assert_eq!(corpus_hash8("00044cfd"), None);
    }

    #[test]
    fn corpus_date_reads_the_segment_after_the_prefix() {
        assert_eq!(
            corpus_date("40-Resources/Reader/00044cfd-2026-05-07_Title"),
            Some("2026-05-07".into())
        );
        assert_eq!(
            corpus_date("40-Resources/Reader/00044cfd-notadate12"),
            None
        );
        assert_eq!(corpus_date("40-Resources/Reader/00044cfd-"), None);
    }

    #[test]
    fn failed_reader_attempt_mirrors_the_product_pack_rule() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path();
        // No run-status.json → not judged (legacy pack).
        assert!(!failed_reader_attempt(dir));
        // Card-bearing, no parse error → product.
        std::fs::write(dir.join("run-status.json"), r#"{"cards": 3}"#).unwrap();
        assert!(!failed_reader_attempt(dir));
        // Zero cards → failed attempt.
        std::fs::write(dir.join("run-status.json"), r#"{"cards": 0}"#).unwrap();
        assert!(failed_reader_attempt(dir));
        // parse_error set → failed attempt even with cards.
        std::fs::write(
            dir.join("run-status.json"),
            r#"{"cards": 3, "parse_error": "bad reply"}"#,
        )
        .unwrap();
        assert!(failed_reader_attempt(dir));
        // Unparseable status → failed (non-index consumers skip, not crash).
        std::fs::write(dir.join("run-status.json"), "not json").unwrap();
        assert!(failed_reader_attempt(dir));
    }

    #[test]
    fn ambiguous_prefix_is_poisoned_and_duplicate_content_keeps_first_path() {
        let mut map = HashMap::new();
        // Two files, same content hash → duplicate content, first path wins.
        insert_prefix_hit(&mut map, "00044cfd".into(), "00044cfdaaaa".into(), "a.md".into());
        insert_prefix_hit(&mut map, "00044cfd".into(), "00044cfdaaaa".into(), "b.md".into());
        assert_eq!(
            map.get("00044cfd"),
            Some(&PrefixHit::Unique {
                sha256: "00044cfdaaaa".into(),
                rel_path: "a.md".into()
            })
        );
        // A DIFFERENT full hash on the same prefix → ambiguous, never guess.
        insert_prefix_hit(&mut map, "00044cfd".into(), "00044cfdbbbb".into(), "c.md".into());
        assert_eq!(map.get("00044cfd"), Some(&PrefixHit::Ambiguous));
        // Once poisoned, it stays poisoned.
        insert_prefix_hit(&mut map, "00044cfd".into(), "00044cfdaaaa".into(), "a.md".into());
        assert_eq!(map.get("00044cfd"), Some(&PrefixHit::Ambiguous));
    }
}
