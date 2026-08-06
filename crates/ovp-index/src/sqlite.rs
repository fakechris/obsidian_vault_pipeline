//! SQLite shadow read-model (stage 3 of `docs/design/storage-read-model.md`).
//!
//! Same philosophy as the JSON projection: derived, rebuildable state — every
//! build produces a FRESH database file. There is no migration story (`ovp2
//! index` IS the migration). The JSON files remain the serving projection;
//! this shadow exists to be diffed against them until parity has soaked long
//! enough to switch endpoints over (stage 4).
//!
//! Placement: the database lives in a MACHINE-LOCAL cache directory, never
//! inside the vault — the vault syncs (Obsidian/iCloud/Dropbox), and a
//! rewritten multi-MB binary per run would thrash sync and conflict across
//! machines. `OVP_CACHE_DIR` overrides the platform default.
//!
//! Promotion contract: build into a unique tmp → fsync the file → verify
//! parity against the in-memory projections → only THEN atomically rename
//! over the previous generation (+ directory fsync). Any build/verify/sync
//! failure leaves the last-good generation untouched.
//!
//! Deliberately NOT here yet: incremental cursors (full rebuild is minutes at
//! 100x scale, measured), FTS tables (stage 3c), vector columns (stage 5).

use std::path::{Path, PathBuf};

use rusqlite::Connection;
use serde::Serialize;
use serde_json::{Value, json};

use crate::evidence::EvidenceModel;
use crate::model::IndexModel;

const SQLITE_FILE: &str = "read-model.sqlite";
const SCHEMA_VERSION: &str = "1";

/// Process-wide cache-base override, first call wins. For EMBEDDERS and
/// in-process tests: mutating `OVP_CACHE_DIR` via `set_var` is unsafe under
/// parallel test threads (the race is on the environment block itself), and
/// the env var only reliably covers child processes.
static CACHE_BASE_OVERRIDE: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

pub fn override_cache_base(path: PathBuf) {
    let _ = CACHE_BASE_OVERRIDE.set(path);
}

/// The machine-local cache root: the in-process override, else
/// `OVP_CACHE_DIR` (child processes, portable setups), else the platform
/// cache directory.
fn cache_base() -> Result<PathBuf, String> {
    if let Some(dir) = CACHE_BASE_OVERRIDE.get() {
        return Ok(dir.clone());
    }
    if let Some(dir) = std::env::var_os("OVP_CACHE_DIR") {
        return Ok(PathBuf::from(dir));
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var_os("HOME").ok_or("HOME is not set")?;
        Ok(PathBuf::from(home).join("Library/Caches"))
    }
    #[cfg(target_os = "windows")]
    {
        let local = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not set")?;
        Ok(PathBuf::from(local))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        if let Some(xdg) = std::env::var_os("XDG_CACHE_HOME") {
            return Ok(PathBuf::from(xdg));
        }
        let home = std::env::var_os("HOME").ok_or("HOME is not set")?;
        Ok(PathBuf::from(home).join(".cache"))
    }
}

/// Stable per-vault fingerprint: hash of the canonicalized root. Needs no
/// state file inside the vault; moving the vault just cold-starts a new
/// cache entry (the shadow is rebuildable by definition).
fn vault_fingerprint(vault_root: &Path) -> String {
    let canon = std::fs::canonicalize(vault_root).unwrap_or_else(|_| vault_root.to_path_buf());
    let hex = ovp_intake::vaultops::hex_sha256(canon.to_string_lossy().as_bytes());
    hex[..16].to_string()
}

/// Where this vault's shadow database lives on THIS machine.
pub fn sqlite_path(vault_root: &Path) -> Result<PathBuf, String> {
    Ok(sqlite_path_in(&cache_base()?, vault_root))
}

/// [`sqlite_path`] under an EXPLICIT cache base — no env/override resolution.
/// Lets a test that handed `OVP_CACHE_DIR` to a child process locate the
/// promoted database without mutating its own environment.
pub fn sqlite_path_in(cache_base: &Path, vault_root: &Path) -> PathBuf {
    cache_base
        .join("ovp")
        .join(vault_fingerprint(vault_root))
        .join(SQLITE_FILE)
}

/// serde's snake_case string for a status enum — the SAME strings the JSON
/// projection carries, so parity comparison is byte-for-byte.
fn enum_str<T: Serialize>(v: &T) -> String {
    serde_json::to_value(v)
        .ok()
        .and_then(|j| j.as_str().map(String::from))
        .unwrap_or_default()
}

const DDL: &str = "
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources(
  sha256 TEXT NOT NULL, status TEXT NOT NULL, title TEXT, author TEXT,
  url TEXT, origin TEXT, rel_path TEXT, date TEXT, content_date TEXT,
  captured_on TEXT, processed_on TEXT, last_run_id TEXT, pack_dir TEXT,
  fail_count INTEGER NOT NULL, last_reason TEXT);
CREATE INDEX idx_sources_sha ON sources(sha256);
CREATE INDEX idx_sources_status ON sources(status);
CREATE INDEX idx_sources_date ON sources(date);
CREATE TABLE source_tags(sha256 TEXT NOT NULL, tag TEXT NOT NULL, kind TEXT NOT NULL);
CREATE INDEX idx_source_tags ON source_tags(tag, sha256);
CREATE INDEX idx_source_tags_sha ON source_tags(sha256);
CREATE TABLE source_entities(sha256 TEXT NOT NULL, entity TEXT NOT NULL);
CREATE INDEX idx_source_entities ON source_entities(entity, sha256);
CREATE INDEX idx_source_entities_sha ON source_entities(sha256);
CREATE TABLE packs(
  pack_dir TEXT NOT NULL, title TEXT NOT NULL, date TEXT,
  units INTEGER NOT NULL, cards INTEGER NOT NULL,
  json_repaired INTEGER NOT NULL, source_sha256 TEXT);
CREATE INDEX idx_packs_dir ON packs(pack_dir);
CREATE INDEX idx_packs_source ON packs(source_sha256);
CREATE TABLE pack_card_titles(pack_dir TEXT NOT NULL, idx INTEGER NOT NULL, title TEXT NOT NULL);
CREATE INDEX idx_pack_card_titles ON pack_card_titles(pack_dir, idx);
CREATE TABLE claims(
  claim_id TEXT NOT NULL, claim_key TEXT, claim TEXT NOT NULL, theme TEXT,
  status TEXT NOT NULL, strength TEXT, run_id TEXT, run_date TEXT, lane TEXT);
CREATE INDEX idx_claims_id ON claims(claim_id);
CREATE INDEX idx_claims_status ON claims(status);
CREATE TABLE claim_sources(claim_id TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX idx_claim_sources ON claim_sources(claim_id);
CREATE INDEX idx_claim_sources_sha ON claim_sources(sha256);
CREATE TABLE runs(
  run_id TEXT NOT NULL, date TEXT NOT NULL, report_file TEXT NOT NULL,
  succeeded INTEGER NOT NULL, failed INTEGER NOT NULL, skipped INTEGER NOT NULL,
  blocked INTEGER NOT NULL, ingested INTEGER NOT NULL,
  pinboard_new INTEGER NOT NULL, lifecycle_warnings INTEGER NOT NULL);
CREATE TABLE cards(
  id TEXT NOT NULL, pack_dir TEXT NOT NULL, source_sha256 TEXT,
  source_title TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
  unit_type TEXT, cited_unit_ids TEXT NOT NULL);
CREATE INDEX idx_cards_id ON cards(id);
CREATE INDEX idx_cards_pack ON cards(pack_dir);
CREATE INDEX idx_cards_source ON cards(source_sha256);
CREATE TABLE units(
  id TEXT NOT NULL, pack_dir TEXT NOT NULL, source_sha256 TEXT,
  source_title TEXT NOT NULL, unit_id TEXT NOT NULL, text TEXT NOT NULL,
  quote TEXT NOT NULL, line INTEGER, attribution TEXT NOT NULL,
  modality TEXT NOT NULL);
CREATE INDEX idx_units_id ON units(id);
CREATE INDEX idx_units_pack ON units(pack_dir);
CREATE INDEX idx_units_source ON units(source_sha256);
";

/// Build a fresh shadow, verify it, and promote it — see the module docs for
/// the promotion contract. Returns the database path and the parity report.
/// Plain (non-unique) indexes throughout: this is a projection, and a
/// constraint abort on quirky data would kill the whole index build;
/// uniqueness is the parity checker's job.
pub fn write_shadow(
    vault_root: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<(PathBuf, ShadowParity), String> {
    let target = sqlite_path(vault_root)?;
    let parent = target
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", target.display()))?;
    std::fs::create_dir_all(parent)
        .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    // Sweep stale tmp generations from crashed builds. Scoped: only OUR
    // pid's leftover (a same-pid concurrent build cannot exist) plus foreign
    // tmps old enough to be certainly dead — `index`/`console` do not hold
    // daily's RunLock, so a blanket sweep could unlink a live concurrent
    // candidate mid-build and fail its verify for no reason.
    const FOREIGN_TMP_MAX_AGE: std::time::Duration = std::time::Duration::from_secs(24 * 3600);
    let own_tmp_name = format!("read-model.sqlite.tmp.{}", std::process::id());
    if let Ok(entries) = std::fs::read_dir(parent) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if !name.starts_with("read-model.sqlite.tmp.") {
                continue;
            }
            let dead_foreign = entry
                .metadata()
                .and_then(|m| m.modified())
                .ok()
                .and_then(|m| m.elapsed().ok())
                .is_some_and(|age| age > FOREIGN_TMP_MAX_AGE);
            if name == own_tmp_name || dead_foreign {
                let _ = std::fs::remove_file(entry.path());
            }
        }
    }
    let tmp = parent.join(own_tmp_name);

    let outcome = build_into(&tmp, model, evidence)
        // Flush the candidate BEFORE validating/promoting: the build runs
        // with synchronous=OFF, so without this a post-rename power loss
        // could expose torn pages behind a rename that itself survived.
        .and_then(|_| {
            std::fs::File::open(&tmp)
                .and_then(|f| f.sync_all())
                .map_err(|e| format!("syncing {}: {e}", tmp.display()))
        })
        // Verify the CANDIDATE — promotion only happens on parity, so a
        // failed build can never replace the last-good generation.
        .and_then(|_| verify_at(&tmp, model, evidence));
    let parity = match outcome {
        Ok(parity) => parity,
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            return Err(e);
        }
    };
    std::fs::rename(&tmp, &target).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("renaming {} into place: {e}", target.display())
    })?;
    crate::build::sync_dir(parent)?;
    Ok((target, parity))
}

/// Re-verify the CURRENT generation against in-memory projections (stage-3b
/// standalone check; `write_shadow` already verified the candidate it
/// promoted).
pub fn verify_shadow(
    vault_root: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<ShadowParity, String> {
    verify_at(&sqlite_path(vault_root)?, model, evidence)
}

fn build_into(
    path: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<(), String> {
    let mut conn = Connection::open(path).map_err(|e| format!("opening {}: {e}", path.display()))?;
    // The candidate is made durable by the explicit file sync + rename in
    // `write_shadow`, not by the journal — skip WAL/fsync during the build.
    conn.pragma_update(None, "journal_mode", "OFF")
        .and_then(|_| conn.pragma_update(None, "synchronous", "OFF"))
        .map_err(|e| format!("pragmas: {e}"))?;
    conn.execute_batch(DDL).map_err(|e| format!("schema: {e}"))?;

    let tx = conn.transaction().map_err(|e| format!("begin: {e}"))?;
    {
        let mut meta = tx
            .prepare("INSERT INTO meta(key, value) VALUES(?1, ?2)")
            .map_err(|e| format!("prepare meta: {e}"))?;
        for (k, v) in [
            ("schema_version", SCHEMA_VERSION.to_string()),
            ("index_schema", model.schema.clone()),
            ("date", model.date.clone()),
            ("built_at", model.built_at.clone().unwrap_or_default()),
            ("run_id", model.run_id.clone().unwrap_or_default()),
        ] {
            meta.execute((k, v)).map_err(|e| format!("meta {k}: {e}"))?;
        }

        let mut src = tx
            .prepare(
                "INSERT INTO sources(sha256, status, title, author, url, origin, rel_path,
                 date, content_date, captured_on, processed_on, last_run_id, pack_dir,
                 fail_count, last_reason)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15)",
            )
            .map_err(|e| format!("prepare sources: {e}"))?;
        let mut tag = tx
            .prepare("INSERT INTO source_tags(sha256, tag, kind) VALUES(?1,?2,?3)")
            .map_err(|e| format!("prepare source_tags: {e}"))?;
        let mut ent = tx
            .prepare("INSERT INTO source_entities(sha256, entity) VALUES(?1,?2)")
            .map_err(|e| format!("prepare source_entities: {e}"))?;
        for s in &model.sources {
            src.execute((
                &s.sha256,
                enum_str(&s.status),
                &s.title,
                &s.author,
                &s.url,
                &s.origin,
                &s.rel_path,
                &s.date,
                &s.content_date,
                &s.captured_on,
                &s.processed_on,
                &s.last_run_id,
                &s.pack_dir,
                s.fail_count as i64,
                &s.last_reason,
            ))
            .map_err(|e| format!("source {}: {e}", s.sha256))?;
            for (kind, list) in [
                ("tag", &s.tags),
                ("inferred", &s.tags_inferred),
                ("implied", &s.tags_implied),
            ] {
                for t in list {
                    tag.execute((&s.sha256, t, kind))
                        .map_err(|e| format!("tag {}: {e}", s.sha256))?;
                }
            }
            for entity in &s.entities {
                ent.execute((&s.sha256, entity))
                    .map_err(|e| format!("entity {}: {e}", s.sha256))?;
            }
        }

        let mut pack = tx
            .prepare(
                "INSERT INTO packs(pack_dir, title, date, units, cards, json_repaired,
                 source_sha256) VALUES(?1,?2,?3,?4,?5,?6,?7)",
            )
            .map_err(|e| format!("prepare packs: {e}"))?;
        let mut card_title = tx
            .prepare("INSERT INTO pack_card_titles(pack_dir, idx, title) VALUES(?1,?2,?3)")
            .map_err(|e| format!("prepare pack_card_titles: {e}"))?;
        for p in &model.packs {
            pack.execute((
                &p.pack_dir,
                &p.title,
                &p.date,
                p.units as i64,
                p.cards as i64,
                p.json_repaired as i64,
                &p.source_sha256,
            ))
            .map_err(|e| format!("pack {}: {e}", p.pack_dir))?;
            for (idx, t) in p.card_titles.iter().enumerate() {
                card_title
                    .execute((&p.pack_dir, idx as i64, t))
                    .map_err(|e| format!("card title {}: {e}", p.pack_dir))?;
            }
        }

        let mut claim = tx
            .prepare(
                "INSERT INTO claims(claim_id, claim_key, claim, theme, status, strength,
                 run_id, run_date, lane) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            )
            .map_err(|e| format!("prepare claims: {e}"))?;
        let mut claim_src = tx
            .prepare("INSERT INTO claim_sources(claim_id, sha256) VALUES(?1,?2)")
            .map_err(|e| format!("prepare claim_sources: {e}"))?;
        for c in &model.claims {
            claim
                .execute((
                    &c.claim_id,
                    &c.claim_key,
                    &c.claim,
                    &c.theme,
                    enum_str(&c.status),
                    &c.strength,
                    &c.run_id,
                    &c.run_date,
                    &c.lane,
                ))
                .map_err(|e| format!("claim {}: {e}", c.claim_id))?;
            for sha in &c.sources {
                claim_src
                    .execute((&c.claim_id, sha))
                    .map_err(|e| format!("claim source {}: {e}", c.claim_id))?;
            }
        }

        let mut run = tx
            .prepare(
                "INSERT INTO runs(run_id, date, report_file, succeeded, failed, skipped,
                 blocked, ingested, pinboard_new, lifecycle_warnings)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
            )
            .map_err(|e| format!("prepare runs: {e}"))?;
        for r in &model.runs {
            run.execute((
                &r.run_id,
                &r.date,
                &r.report_file,
                r.succeeded as i64,
                r.failed as i64,
                r.skipped as i64,
                r.blocked as i64,
                r.ingested as i64,
                r.pinboard_new as i64,
                r.lifecycle_warnings as i64,
            ))
            .map_err(|e| format!("run {}: {e}", r.run_id))?;
        }

        if let Some(ev) = evidence {
            let mut card = tx
                .prepare(
                    "INSERT INTO cards(id, pack_dir, source_sha256, source_title, title,
                     content, unit_type, cited_unit_ids)
                     VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
                )
                .map_err(|e| format!("prepare cards: {e}"))?;
            for c in &ev.cards {
                card.execute((
                    &c.id,
                    &c.pack_dir,
                    &c.source_sha256,
                    &c.source_title,
                    &c.title,
                    &c.content,
                    &c.unit_type,
                    serde_json::to_string(&c.cited_unit_ids).unwrap_or_else(|_| "[]".into()),
                ))
                .map_err(|e| format!("card {}: {e}", c.id))?;
            }
            let mut unit = tx
                .prepare(
                    "INSERT INTO units(id, pack_dir, source_sha256, source_title, unit_id,
                     text, quote, line, attribution, modality)
                     VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
                )
                .map_err(|e| format!("prepare units: {e}"))?;
            for u in &ev.units {
                unit.execute((
                    &u.id,
                    &u.pack_dir,
                    &u.source_sha256,
                    &u.source_title,
                    &u.unit_id,
                    &u.text,
                    &u.quote,
                    u.line.map(|l| l as i64),
                    &u.attribution,
                    &u.modality,
                ))
                .map_err(|e| format!("unit {}: {e}", u.id))?;
            }
        }
    }
    tx.commit().map_err(|e| format!("commit: {e}"))?;
    Ok(())
}

/// Parity report between a shadow database and the in-memory projections it
/// was built from. Counts must ALL match and every sampled row — INCLUDING
/// its child-table rows — must round-trip field-for-field; any mismatch is an
/// `Err` naming the first divergence, because a silently drifting shadow is
/// worse than none.
#[derive(Debug, PartialEq)]
pub struct ShadowParity {
    pub sources: usize,
    pub packs: usize,
    pub claims: usize,
    pub runs: usize,
    pub cards: usize,
    pub units: usize,
    pub sampled: usize,
}

/// Evenly-spaced sample indices — deterministic, no clock/randomness (cheap
/// to rerun in tests and on every build).
fn sample_indices(len: usize, want: usize) -> Vec<usize> {
    if len == 0 {
        return Vec::new();
    }
    let want = want.min(len);
    (0..want).map(|i| i * len / want).collect()
}

const SAMPLES_PER_SURFACE: usize = 20;

/// Compare two full-row JSON encodings, naming the surface and key on drift.
fn expect_row(surface: &str, key: &str, got: &Value, want: &Value) -> Result<(), String> {
    if got != want {
        return Err(format!(
            "{surface} {key} diverges:\n  sqlite: {got}\n  model:  {want}"
        ));
    }
    Ok(())
}

fn verify_at(
    path: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<ShadowParity, String> {
    let conn = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("opening {}: {e}", path.display()))?;

    let count = |table: &str| -> Result<usize, String> {
        conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| {
            r.get::<_, i64>(0)
        })
        .map(|n| n as usize)
        .map_err(|e| format!("counting {table}: {e}"))
    };
    let expect = |table: &str, got: usize, want: usize| -> Result<(), String> {
        if got != want {
            return Err(format!("{table}: sqlite has {got} rows, model has {want}"));
        }
        Ok(())
    };

    let parity = ShadowParity {
        sources: count("sources")?,
        packs: count("packs")?,
        claims: count("claims")?,
        runs: count("runs")?,
        cards: count("cards")?,
        units: count("units")?,
        sampled: 0,
    };
    expect("sources", parity.sources, model.sources.len())?;
    expect("packs", parity.packs, model.packs.len())?;
    expect("claims", parity.claims, model.claims.len())?;
    expect("runs", parity.runs, model.runs.len())?;
    let (want_cards, want_units) = evidence
        .map(|e| (e.cards.len(), e.units.len()))
        .unwrap_or((0, 0));
    expect("cards", parity.cards, want_cards)?;
    expect("units", parity.units, want_units)?;
    // Child tables count-check in full (cheap) so drift there cannot hide
    // outside the sampled parents.
    let want_tags: usize = model
        .sources
        .iter()
        .map(|s| s.tags.len() + s.tags_inferred.len() + s.tags_implied.len())
        .sum();
    expect("source_tags", count("source_tags")?, want_tags)?;
    let want_entities: usize = model.sources.iter().map(|s| s.entities.len()).sum();
    expect("source_entities", count("source_entities")?, want_entities)?;
    let want_card_titles: usize = model.packs.iter().map(|p| p.card_titles.len()).sum();
    expect("pack_card_titles", count("pack_card_titles")?, want_card_titles)?;
    let want_claim_sources: usize = model.claims.iter().map(|c| c.sources.len()).sum();
    expect("claim_sources", count("claim_sources")?, want_claim_sources)?;

    // meta scalars: built_at exists precisely so a stale projection cannot
    // render like a fresh one — the verifier must not skip the one surface
    // it writes but never reads.
    let mut got_meta: Vec<(String, String)> = conn
        .prepare("SELECT key, value FROM meta ORDER BY key")
        .and_then(|mut st| {
            st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(|e| format!("reading meta: {e}"))?;
    got_meta.sort();
    let mut want_meta = vec![
        ("schema_version".to_string(), SCHEMA_VERSION.to_string()),
        ("index_schema".to_string(), model.schema.clone()),
        ("date".to_string(), model.date.clone()),
        ("built_at".to_string(), model.built_at.clone().unwrap_or_default()),
        ("run_id".to_string(), model.run_id.clone().unwrap_or_default()),
    ];
    want_meta.sort();
    expect_row("meta", "scalars", &json!(got_meta), &json!(want_meta))?;

    let mut sampled = 0usize;

    // --- sources: every column + ordered (tag, kind) pairs + entities ---
    for i in sample_indices(model.sources.len(), SAMPLES_PER_SURFACE) {
        let s = &model.sources[i];
        let got = conn
            .query_row(
                "SELECT status, title, author, url, origin, rel_path, date, content_date,
                 captured_on, processed_on, last_run_id, pack_dir, fail_count, last_reason
                 FROM sources WHERE sha256 = ?1",
                [&s.sha256],
                |r| {
                    Ok(json!({
                        "status": r.get::<_, String>(0)?,
                        "title": r.get::<_, Option<String>>(1)?,
                        "author": r.get::<_, Option<String>>(2)?,
                        "url": r.get::<_, Option<String>>(3)?,
                        "origin": r.get::<_, Option<String>>(4)?,
                        "rel_path": r.get::<_, Option<String>>(5)?,
                        "date": r.get::<_, Option<String>>(6)?,
                        "content_date": r.get::<_, Option<String>>(7)?,
                        "captured_on": r.get::<_, Option<String>>(8)?,
                        "processed_on": r.get::<_, Option<String>>(9)?,
                        "last_run_id": r.get::<_, Option<String>>(10)?,
                        "pack_dir": r.get::<_, Option<String>>(11)?,
                        "fail_count": r.get::<_, i64>(12)?,
                        "last_reason": r.get::<_, Option<String>>(13)?,
                    }))
                },
            )
            .map_err(|e| format!("sampling source {}: {e}", s.sha256))?;
        let want = json!({
            "status": enum_str(&s.status),
            "title": s.title, "author": s.author, "url": s.url, "origin": s.origin,
            "rel_path": s.rel_path, "date": s.date, "content_date": s.content_date,
            "captured_on": s.captured_on, "processed_on": s.processed_on,
            "last_run_id": s.last_run_id, "pack_dir": s.pack_dir,
            "fail_count": s.fail_count, "last_reason": s.last_reason,
        });
        expect_row("source", &s.sha256, &got, &want)?;

        let got_tags: Vec<(String, String)> = conn
            .prepare("SELECT tag, kind FROM source_tags WHERE sha256 = ?1 ORDER BY rowid")
            .and_then(|mut st| {
                st.query_map([&s.sha256], |r| Ok((r.get(0)?, r.get(1)?)))?
                    .collect::<Result<Vec<_>, _>>()
            })
            .map_err(|e| format!("sampling tags {}: {e}", s.sha256))?;
        let want_tags: Vec<(String, String)> = s
            .tags
            .iter()
            .map(|t| (t.clone(), "tag".to_string()))
            .chain(s.tags_inferred.iter().map(|t| (t.clone(), "inferred".to_string())))
            .chain(s.tags_implied.iter().map(|t| (t.clone(), "implied".to_string())))
            .collect();
        expect_row("source_tags", &s.sha256, &json!(got_tags), &json!(want_tags))?;

        let got_entities: Vec<String> = conn
            .prepare("SELECT entity FROM source_entities WHERE sha256 = ?1 ORDER BY rowid")
            .and_then(|mut st| {
                st.query_map([&s.sha256], |r| r.get(0))?
                    .collect::<Result<Vec<_>, _>>()
            })
            .map_err(|e| format!("sampling entities {}: {e}", s.sha256))?;
        expect_row("source_entities", &s.sha256, &json!(got_entities), &json!(s.entities))?;
        sampled += 1;
    }

    // --- packs: every column + ordered card titles ---
    for i in sample_indices(model.packs.len(), SAMPLES_PER_SURFACE) {
        let p = &model.packs[i];
        let got = conn
            .query_row(
                "SELECT title, date, units, cards, json_repaired, source_sha256
                 FROM packs WHERE pack_dir = ?1",
                [&p.pack_dir],
                |r| {
                    Ok(json!({
                        "title": r.get::<_, String>(0)?,
                        "date": r.get::<_, Option<String>>(1)?,
                        "units": r.get::<_, i64>(2)?,
                        "cards": r.get::<_, i64>(3)?,
                        "json_repaired": r.get::<_, i64>(4)? != 0,
                        "source_sha256": r.get::<_, Option<String>>(5)?,
                    }))
                },
            )
            .map_err(|e| format!("sampling pack {}: {e}", p.pack_dir))?;
        let want = json!({
            "title": p.title, "date": p.date, "units": p.units, "cards": p.cards,
            "json_repaired": p.json_repaired, "source_sha256": p.source_sha256,
        });
        expect_row("pack", &p.pack_dir, &got, &want)?;

        let got_titles: Vec<String> = conn
            .prepare("SELECT title FROM pack_card_titles WHERE pack_dir = ?1 ORDER BY idx")
            .and_then(|mut st| {
                st.query_map([&p.pack_dir], |r| r.get(0))?
                    .collect::<Result<Vec<_>, _>>()
            })
            .map_err(|e| format!("sampling card titles {}: {e}", p.pack_dir))?;
        expect_row("pack_card_titles", &p.pack_dir, &json!(got_titles), &json!(p.card_titles))?;
        sampled += 1;
    }

    // --- runs: every column. Keyed by report_file, NOT run_id — same-day
    // reruns share a run_id (`daily-<date>`), so run_id is ambiguous on real
    // vaults; the report file is one-per-run.
    for i in sample_indices(model.runs.len(), SAMPLES_PER_SURFACE) {
        let r0 = &model.runs[i];
        let got = conn
            .query_row(
                "SELECT run_id, date, succeeded, failed, skipped, blocked, ingested,
                 pinboard_new, lifecycle_warnings FROM runs WHERE report_file = ?1",
                [&r0.report_file],
                |r| {
                    Ok(json!({
                        "run_id": r.get::<_, String>(0)?,
                        "date": r.get::<_, String>(1)?,
                        "succeeded": r.get::<_, i64>(2)?,
                        "failed": r.get::<_, i64>(3)?,
                        "skipped": r.get::<_, i64>(4)?,
                        "blocked": r.get::<_, i64>(5)?,
                        "ingested": r.get::<_, i64>(6)?,
                        "pinboard_new": r.get::<_, i64>(7)?,
                        "lifecycle_warnings": r.get::<_, i64>(8)?,
                    }))
                },
            )
            .map_err(|e| format!("sampling run {}: {e}", r0.report_file))?;
        let want = json!({
            "run_id": r0.run_id, "date": r0.date, "succeeded": r0.succeeded,
            "failed": r0.failed, "skipped": r0.skipped, "blocked": r0.blocked,
            "ingested": r0.ingested, "pinboard_new": r0.pinboard_new,
            "lifecycle_warnings": r0.lifecycle_warnings,
        });
        expect_row("run", &r0.report_file, &got, &want)?;
        sampled += 1;
    }

    // --- claims: every column + ordered source list ---
    for i in sample_indices(model.claims.len(), SAMPLES_PER_SURFACE) {
        let c = &model.claims[i];
        let n: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM claims WHERE claim_id = ?1 AND claim = ?2
                 AND status = ?3 AND coalesce(claim_key,'') = coalesce(?4,'')
                 AND coalesce(theme,'') = coalesce(?5,'')
                 AND coalesce(strength,'') = coalesce(?6,'')
                 AND coalesce(run_id,'') = coalesce(?7,'')
                 AND coalesce(run_date,'') = coalesce(?8,'')
                 AND coalesce(lane,'') = coalesce(?9,'')",
                (
                    &c.claim_id,
                    &c.claim,
                    enum_str(&c.status),
                    &c.claim_key,
                    &c.theme,
                    &c.strength,
                    &c.run_id,
                    &c.run_date,
                    &c.lane,
                ),
                |r| r.get(0),
            )
            .map_err(|e| format!("sampling claim {}: {e}", c.claim_id))?;
        if n == 0 {
            return Err(format!("claim {} missing or diverges", c.claim_id));
        }
        let got_sources: Vec<String> = conn
            .prepare("SELECT sha256 FROM claim_sources WHERE claim_id = ?1 ORDER BY rowid")
            .and_then(|mut st| {
                st.query_map([&c.claim_id], |r| r.get(0))?
                    .collect::<Result<Vec<_>, _>>()
            })
            .map_err(|e| format!("sampling claim sources {}: {e}", c.claim_id))?;
        // claim_id is NOT unique on real vaults (a claim can appear in both
        // the durable and caveated lanes) — compare the AGGREGATE source list
        // across every model row sharing this id, in model order (insertion
        // order preserves it).
        let want_sources: Vec<&String> = model
            .claims
            .iter()
            .filter(|other| other.claim_id == c.claim_id)
            .flat_map(|other| other.sources.iter())
            .collect();
        expect_row("claim_sources", &c.claim_id, &json!(got_sources), &json!(want_sources))?;
        sampled += 1;
    }

    if let Some(ev) = evidence {
        // --- cards: every column ---
        for i in sample_indices(ev.cards.len(), SAMPLES_PER_SURFACE) {
            let c = &ev.cards[i];
            let got = conn
                .query_row(
                    "SELECT pack_dir, source_sha256, source_title, title, content, unit_type,
                     cited_unit_ids FROM cards WHERE id = ?1",
                    [&c.id],
                    |r| {
                        Ok(json!({
                            "pack_dir": r.get::<_, String>(0)?,
                            "source_sha256": r.get::<_, Option<String>>(1)?,
                            "source_title": r.get::<_, String>(2)?,
                            "title": r.get::<_, String>(3)?,
                            "content": r.get::<_, String>(4)?,
                            "unit_type": r.get::<_, Option<String>>(5)?,
                            "cited_unit_ids": r.get::<_, String>(6)?,
                        }))
                    },
                )
                .map_err(|e| format!("sampling card {}: {e}", c.id))?;
            let want = json!({
                "pack_dir": c.pack_dir, "source_sha256": c.source_sha256,
                "source_title": c.source_title, "title": c.title, "content": c.content,
                "unit_type": c.unit_type,
                "cited_unit_ids": serde_json::to_string(&c.cited_unit_ids).unwrap_or_else(|_| "[]".into()),
            });
            expect_row("card", &c.id, &got, &want)?;
            sampled += 1;
        }
        // --- units: every column ---
        for i in sample_indices(ev.units.len(), SAMPLES_PER_SURFACE) {
            let u = &ev.units[i];
            let got = conn
                .query_row(
                    "SELECT pack_dir, source_sha256, source_title, unit_id, text, quote, line,
                     attribution, modality FROM units WHERE id = ?1",
                    [&u.id],
                    |r| {
                        Ok(json!({
                            "pack_dir": r.get::<_, String>(0)?,
                            "source_sha256": r.get::<_, Option<String>>(1)?,
                            "source_title": r.get::<_, String>(2)?,
                            "unit_id": r.get::<_, String>(3)?,
                            "text": r.get::<_, String>(4)?,
                            "quote": r.get::<_, String>(5)?,
                            "line": r.get::<_, Option<i64>>(6)?,
                            "attribution": r.get::<_, String>(7)?,
                            "modality": r.get::<_, String>(8)?,
                        }))
                    },
                )
                .map_err(|e| format!("sampling unit {}: {e}", u.id))?;
            let want = json!({
                "pack_dir": u.pack_dir, "source_sha256": u.source_sha256,
                "source_title": u.source_title, "unit_id": u.unit_id, "text": u.text,
                "quote": u.quote, "line": u.line, "attribution": u.attribution,
                "modality": u.modality,
            });
            expect_row("unit", &u.id, &got, &want)?;
            sampled += 1;
        }
    }

    Ok(ShadowParity { sampled, ..parity })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::evidence::{CardEvidenceRow, UnitEvidenceRow};
    use crate::model::{ClaimRow, ClaimStatus, OpsState, PackRow, SourceRow, SourceStatus, Totals};

    /// In-process tests pin the db path explicitly (no env: parallel tests
    /// share the process environment); the CLI e2e covers env resolution.
    fn shadow_at(
        dir: &Path,
        model: &IndexModel,
        evidence: Option<&EvidenceModel>,
    ) -> Result<(PathBuf, ShadowParity), String> {
        let tmp = dir.join("candidate.sqlite");
        build_into(&tmp, model, evidence)?;
        let parity = verify_at(&tmp, model, evidence)?;
        Ok((tmp, parity))
    }

    fn model() -> IndexModel {
        IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-08-05".into(),
            built_at: Some("2026-08-05T10:00:00Z".into()),
            run_id: Some("daily-2026-08-05".into()),
            totals: Totals::default(),
            sources: vec![SourceRow {
                sha256: "sha-a".into(),
                status: SourceStatus::Processed,
                title: Some("A 标题 <\"quoted\">".into()),
                author: Some("Ada".into()),
                url: Some("https://e.x/a?q=1&z=2".into()),
                origin: Some("pinboard".into()),
                rel_path: Some("50-Inbox/03-Processed/a.md".into()),
                date: Some("2026-08-01".into()),
                content_date: Some("2026-07-30".into()),
                captured_on: Some("2026-07-31".into()),
                processed_on: Some("2026-08-02".into()),
                last_run_id: Some("daily-2026-08-02".into()),
                pack_dir: Some("40-Resources/Reader/a".into()),
                fail_count: 0,
                last_reason: None,
                tags: vec!["ai".into()],
                tags_inferred: vec!["agents".into(), "记忆".into()],
                tags_implied: vec!["ml".into()],
                entities: vec!["Anthropic".into()],
            }],
            packs: vec![PackRow {
                pack_dir: "40-Resources/Reader/a".into(),
                title: "A pack".into(),
                date: Some("2026-08-02".into()),
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec!["Card one".into()],
                source_sha256: Some("sha-a".into()),
            }],
            claims: vec![ClaimRow {
                claim_id: "m1-01".into(),
                claim_key: Some("ck-abc".into()),
                claim: "记忆是持久状态 with \"quotes\"".into(),
                theme: Some("agent-memory".into()),
                status: ClaimStatus::Durable,
                sources: vec!["sha-a".into()],
                strength: Some("well_supported".into()),
                run_id: None,
                run_date: None,
                lane: None,
            }],
            runs: vec![crate::model::RunRow {
                run_id: "daily-2026-08-02".into(),
                date: "2026-08-02".into(),
                report_file: ".ovp/reports/daily-2026-08-02.json".into(),
                succeeded: 3,
                failed: 1,
                skipped: 0,
                blocked: 0,
                ingested: 2,
                pinboard_new: 1,
                lifecycle_warnings: 0,
            }],
            ops: OpsState::default(),
        }
    }

    fn evidence() -> EvidenceModel {
        EvidenceModel {
            schema: "ovp.index.evidence/v1".into(),
            date: "2026-08-05".into(),
            cards: vec![CardEvidenceRow {
                id: "card:40-Resources/Reader/a:0".into(),
                pack_dir: "40-Resources/Reader/a".into(),
                source_sha256: Some("sha-a".into()),
                source_title: "A pack".into(),
                title: "Card one".into(),
                content: "内容 body".into(),
                unit_type: Some("fact".into()),
                cited_unit_ids: vec!["u-1".into()],
            }],
            units: vec![UnitEvidenceRow {
                id: "unit:40-Resources/Reader/a:u-1".into(),
                pack_dir: "40-Resources/Reader/a".into(),
                source_sha256: Some("sha-a".into()),
                source_title: "A pack".into(),
                unit_id: "u-1".into(),
                text: "unit text 中文".into(),
                quote: "\"quoted\" 引文".into(),
                line: Some(12),
                attribution: "author".into(),
                modality: "asserted".into(),
            }],
            warnings: vec![],
        }
    }

    #[test]
    fn shadow_round_trips_and_verifies_all_surfaces() {
        let tmp = tempfile::tempdir().unwrap();
        let m = model();
        let ev = evidence();
        let (_, parity) = shadow_at(tmp.path(), &m, Some(&ev)).unwrap();
        assert_eq!(
            parity,
            ShadowParity {
                sources: 1,
                packs: 1,
                claims: 1,
                runs: 1,
                cards: 1,
                units: 1,
                sampled: 6,
            }
        );
    }

    #[test]
    fn verify_catches_divergence_in_unsampled_looking_fields() {
        let tmp = tempfile::tempdir().unwrap();
        let m = model();
        let ev = evidence();
        let db = tmp.path().join("candidate.sqlite");
        build_into(&db, &m, Some(&ev)).unwrap();

        // Count drift.
        let mut newer = m.clone();
        let mut extra = newer.sources[0].clone();
        extra.sha256 = "sha-b".into();
        newer.sources.push(extra);
        let err = verify_at(&db, &newer, Some(&ev)).unwrap_err();
        assert!(err.contains("sources"), "unexpected error: {err}");

        // Field-level drift in fields the OLD sampler never looked at:
        // author, a tag KIND, an entity, a run column, a card citation.
        let mut edited = m.clone();
        edited.sources[0].author = Some("Mallory".into());
        assert!(verify_at(&db, &edited, Some(&ev)).is_err(), "author drift");

        let mut edited = m.clone();
        edited.sources[0].tags_implied = vec!["other".into()];
        assert!(verify_at(&db, &edited, Some(&ev)).is_err(), "tag-kind drift");

        let mut edited = m.clone();
        edited.sources[0].entities = vec!["Someone".into()];
        assert!(verify_at(&db, &edited, Some(&ev)).is_err(), "entity drift");

        let mut edited = m.clone();
        edited.runs[0].ingested = 99;
        assert!(verify_at(&db, &edited, Some(&ev)).is_err(), "run drift");

        let mut ev_edited = ev.clone();
        ev_edited.cards[0].cited_unit_ids = vec!["u-2".into()];
        assert!(verify_at(&db, &m, Some(&ev_edited)).is_err(), "citation drift");

        let mut edited = m.clone();
        edited.packs[0].card_titles = vec!["Other".into()];
        assert!(verify_at(&db, &edited, Some(&ev)).is_err(), "card-title drift");
    }

    #[test]
    fn write_shadow_keeps_last_good_when_candidate_fails_verify() {
        // Simulate the promotion contract at the verify layer: a candidate
        // that fails parity must never replace the previous generation.
        // (write_shadow wires cache-path resolution + this exact sequence;
        // the e2e exercises it end-to-end through the binary.)
        let tmp = tempfile::tempdir().unwrap();
        let m = model();
        let ev = evidence();
        let (good, _) = shadow_at(tmp.path(), &m, Some(&ev)).unwrap();

        let mut drifted = m.clone();
        drifted.sources[0].title = Some("tampered".into());
        // Candidate built from drifted model verifies fine against itself…
        let candidate = tmp.path().join("candidate2.sqlite");
        build_into(&candidate, &drifted, Some(&ev)).unwrap();
        // …but never against the authoritative model — promotion must stop.
        assert!(verify_at(&candidate, &m, Some(&ev)).is_err());
        // Last-good is still readable and still passes.
        assert!(verify_at(&good, &m, Some(&ev)).is_ok());
    }

    #[test]
    fn sample_indices_are_bounded_and_deterministic() {
        assert!(sample_indices(0, 20).is_empty());
        assert_eq!(sample_indices(3, 20), vec![0, 1, 2]);
        let s = sample_indices(1000, 20);
        assert_eq!(s.len(), 20);
        assert!(s.windows(2).all(|w| w[0] < w[1]));
        assert!(*s.last().unwrap() < 1000);
    }
}
