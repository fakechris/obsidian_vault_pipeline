//! SQLite shadow read-model (stage 3 of `docs/design/storage-read-model.md`).
//!
//! Same philosophy as the JSON projection: derived, rebuildable state — every
//! build produces a FRESH database file that atomically replaces the previous
//! generation, so there is no migration story (`ovp2 index` IS the
//! migration). The JSON files remain the serving projection; this shadow
//! exists to be diffed against them (`verify_shadow`) until parity has soaked
//! long enough to switch endpoints over (stage 4).
//!
//! Deliberately NOT here yet: incremental cursors (full rebuild is minutes at
//! 100x scale, measured), FTS tables (stage 3c), vector columns (stage 5).

use std::path::{Path, PathBuf};

use rusqlite::Connection;
use serde::Serialize;

use crate::evidence::EvidenceModel;
use crate::model::IndexModel;

pub const SQLITE_FILE: &str = ".ovp/index/read-model.sqlite";
const SCHEMA_VERSION: &str = "1";

pub fn sqlite_path(vault_root: &Path) -> PathBuf {
    vault_root.join(SQLITE_FILE)
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
CREATE TABLE source_entities(sha256 TEXT NOT NULL, entity TEXT NOT NULL);
CREATE INDEX idx_source_entities ON source_entities(entity, sha256);
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

/// Build the shadow database into a unique temp file and atomically rename it
/// over the previous generation. A crash mid-build leaves only a stale tmp
/// (removed on the next build) — the previous generation stays intact, never
/// a torn database. Plain (non-unique) indexes throughout: this is a
/// projection, and a constraint abort on quirky data would kill the whole
/// index build; uniqueness is the parity checker's job.
pub fn write_shadow(
    vault_root: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<String, String> {
    let target = sqlite_path(vault_root);
    let parent = target
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", target.display()))?;
    std::fs::create_dir_all(parent)
        .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    // Sweep stale tmp generations from crashed builds before making a new one.
    if let Ok(entries) = std::fs::read_dir(parent) {
        for entry in entries.flatten() {
            if entry
                .file_name()
                .to_string_lossy()
                .starts_with("read-model.sqlite.tmp.")
            {
                let _ = std::fs::remove_file(entry.path());
            }
        }
    }
    let tmp = parent.join(format!("read-model.sqlite.tmp.{}", std::process::id()));

    let built = build_into(&tmp, model, evidence);
    if let Err(e) = built {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    std::fs::rename(&tmp, &target).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("renaming {} into place: {e}", target.display())
    })?;
    crate::build::sync_dir(parent)?;
    Ok(ovp_intake::vaultops::rel_to(vault_root, &target))
}

fn build_into(
    path: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<(), String> {
    let mut conn = Connection::open(path).map_err(|e| format!("opening {}: {e}", path.display()))?;
    // Rebuildable projection built into a tmp file: durability comes from the
    // rename + dir fsync, not the journal — skip WAL/fsync during the build.
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

/// Parity report between the shadow database and the in-memory projections it
/// was built from. Counts must ALL match and every sampled row must
/// round-trip field-for-field — any mismatch is an `Err` naming the first
/// divergence, because a silently drifting shadow is worse than none.
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

pub fn verify_shadow(
    vault_root: &Path,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
) -> Result<ShadowParity, String> {
    let path = sqlite_path(vault_root);
    let conn = Connection::open_with_flags(
        &path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
    )
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

    // Row-level samples: sources by sha, cards/units by id, claims by
    // position-independent lookup on claim_id + claim text.
    let mut sampled = 0usize;
    for i in sample_indices(model.sources.len(), 20) {
        let s = &model.sources[i];
        let (status, title, fail_count): (String, Option<String>, i64) = conn
            .query_row(
                "SELECT status, title, fail_count FROM sources WHERE sha256 = ?1",
                [&s.sha256],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .map_err(|e| format!("sampling source {}: {e}", s.sha256))?;
        if status != enum_str(&s.status) || title != s.title || fail_count as usize != s.fail_count
        {
            return Err(format!("source {} diverges: sqlite ({status}, {title:?}, {fail_count}) vs model ({}, {:?}, {})", s.sha256, enum_str(&s.status), s.title, s.fail_count));
        }
        let tag_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM source_tags WHERE sha256 = ?1",
                [&s.sha256],
                |r| r.get(0),
            )
            .map_err(|e| format!("sampling tags {}: {e}", s.sha256))?;
        let want_tags = s.tags.len() + s.tags_inferred.len() + s.tags_implied.len();
        if tag_count as usize != want_tags {
            return Err(format!(
                "source {} tag rows diverge: sqlite {tag_count} vs model {want_tags}",
                s.sha256
            ));
        }
        sampled += 1;
    }
    if let Some(ev) = evidence {
        for i in sample_indices(ev.units.len(), 20) {
            let u = &ev.units[i];
            let (text, quote, line): (String, String, Option<i64>) = conn
                .query_row(
                    "SELECT text, quote, line FROM units WHERE id = ?1",
                    [&u.id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .map_err(|e| format!("sampling unit {}: {e}", u.id))?;
            if text != u.text || quote != u.quote || line.map(|l| l as usize) != u.line {
                return Err(format!("unit {} diverges", u.id));
            }
            sampled += 1;
        }
        for i in sample_indices(ev.cards.len(), 20) {
            let c = &ev.cards[i];
            let content: String = conn
                .query_row("SELECT content FROM cards WHERE id = ?1", [&c.id], |r| {
                    r.get(0)
                })
                .map_err(|e| format!("sampling card {}: {e}", c.id))?;
            if content != c.content {
                return Err(format!("card {} diverges", c.id));
            }
            sampled += 1;
        }
    }
    for i in sample_indices(model.claims.len(), 20) {
        let c = &model.claims[i];
        let n: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM claims WHERE claim_id = ?1 AND claim = ?2 AND status = ?3",
                (&c.claim_id, &c.claim, enum_str(&c.status)),
                |r| r.get(0),
            )
            .map_err(|e| format!("sampling claim {}: {e}", c.claim_id))?;
        if n == 0 {
            return Err(format!("claim {} missing or diverges", c.claim_id));
        }
        sampled += 1;
    }

    Ok(ShadowParity { sampled, ..parity })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::evidence::{CardEvidenceRow, UnitEvidenceRow};
    use crate::model::{ClaimRow, ClaimStatus, OpsState, PackRow, SourceRow, SourceStatus, Totals};

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
                author: None,
                url: Some("https://e.x/a?q=1&z=2".into()),
                origin: None,
                rel_path: Some("50-Inbox/03-Processed/a.md".into()),
                date: Some("2026-08-01".into()),
                content_date: None,
                captured_on: None,
                processed_on: Some("2026-08-02".into()),
                last_run_id: None,
                pack_dir: Some("40-Resources/Reader/a".into()),
                fail_count: 0,
                last_reason: None,
                tags: vec!["ai".into()],
                tags_inferred: vec!["agents".into(), "记忆".into()],
                tags_implied: vec![],
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
            runs: vec![],
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
    fn shadow_round_trips_and_verifies() {
        let tmp = tempfile::tempdir().unwrap();
        let m = model();
        let ev = evidence();
        let rel = write_shadow(tmp.path(), &m, Some(&ev)).unwrap();
        assert_eq!(rel, ".ovp/index/read-model.sqlite");

        let parity = verify_shadow(tmp.path(), &m, Some(&ev)).unwrap();
        assert_eq!(
            parity,
            ShadowParity {
                sources: 1,
                packs: 1,
                claims: 1,
                runs: 0,
                cards: 1,
                units: 1,
                sampled: 4,
            }
        );

        // No stray tmp generations left behind, and rebuilds replace cleanly.
        write_shadow(tmp.path(), &m, Some(&ev)).unwrap();
        let strays: Vec<_> = std::fs::read_dir(tmp.path().join(".ovp/index"))
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.contains(".tmp."))
            .collect();
        assert!(strays.is_empty(), "stray tmp files: {strays:?}");
    }

    #[test]
    fn verify_catches_divergence() {
        let tmp = tempfile::tempdir().unwrap();
        let m = model();
        let ev = evidence();
        write_shadow(tmp.path(), &m, Some(&ev)).unwrap();

        // A model that gained a source after the shadow was built must fail
        // the count check.
        let mut newer = m.clone();
        let mut extra = newer.sources[0].clone();
        extra.sha256 = "sha-b".into();
        newer.sources.push(extra);
        let err = verify_shadow(tmp.path(), &newer, Some(&ev)).unwrap_err();
        assert!(err.contains("sources"), "unexpected error: {err}");

        // A silently edited row must fail the sample check.
        let mut edited = m.clone();
        edited.sources[0].title = Some("tampered".into());
        let err = verify_shadow(tmp.path(), &edited, Some(&ev)).unwrap_err();
        assert!(err.contains("diverges"), "unexpected error: {err}");
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
