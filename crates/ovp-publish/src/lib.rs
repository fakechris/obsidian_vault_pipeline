//! `ovp-publish` — snapshot the public-safe read-only API surface to a static
//! `site/api/*.json` tree, so the same `console-ui` SPA (built in static mode)
//! can serve durable knowledge + the visualizations off any static host with no
//! server.
//!
//! The snapshot is *just another projection* of the ledgers — deterministic,
//! rebuilt whole each publish, content-hash-stable so git computes a minimal
//! diff. It reuses the SAME `ovp-api-projection` builders the live server uses
//! (no drift) over a `PublicView` (durable claims + processed sources only).

use std::path::{Path, PathBuf};

use ovp_api_projection::{PublicView, bodies, graph, readers};
use ovp_domain::VaultLayout;
use ovp_index::{
    IndexModel, Query, build_evidence, build_index_at, now_rfc3339, read_index, write_evidence,
    write_index,
};
use sha2::{Digest, Sha256};

/// Node cap for the published global graph — matches the live embedded view's
/// `fetchGlobalGraph(400)` so a published site doesn't ship the full overview.
const PUBLIC_GRAPH_LIMIT: usize = 400;

pub struct PublishArgs {
    pub vault_root: PathBuf,
    /// Output directory; the API tree lands under `<out>/api/`.
    pub out_dir: PathBuf,
    /// Day string for a rebuild (ignored when `no_rebuild`).
    pub date: String,
    /// Read the existing `index.json` instead of rebuilding it.
    pub no_rebuild: bool,
    /// RFC3339 instant to stamp the publish with (caller supplies — the script
    /// runtime forbids clock reads deep in libraries). `None` → omit.
    pub published_at: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct PublishReport {
    pub file_count: usize,
    /// sha256 over the CONTENT (sources+packs+claims+themes+terrain), excluding
    /// volatile stamps — the change-detection key.
    pub content_hash: String,
    pub index_run_id: Option<String>,
    pub index_built_at: Option<String>,
    pub sources: usize,
    pub claims: usize,
}

/// Build the static API snapshot under `<out>/api/`. Returns a report the CLI
/// uses for the publish ledger + change detection.
pub fn publish(args: &PublishArgs) -> Result<PublishReport, String> {
    let layout = VaultLayout::new();

    // 1. Fresh, self-consistent model (or read the existing one). A rebuild also
    // refreshes the vault's evidence.json side-car (parity with `console`); the
    // published pages themselves are lite and carry no evidence layer.
    let model: IndexModel = if args.no_rebuild {
        read_index(&args.vault_root)?
    } else {
        let built_at = now_rfc3339();
        let run_id = format!("publish-{built_at}");
        let m = build_index_at(&args.vault_root, &args.date, Some(&run_id), &built_at)?;
        write_index(&args.vault_root, &m)?;
        let e = build_evidence(&args.vault_root, &args.date, &m)?;
        write_evidence(&args.vault_root, &e)?;
        m
    };

    // 2. Redact to public-safe. 3. Durable/active records (durable-only by
    // construction — caveated live in review.json and never in the ledger).
    let view = PublicView::from_model(&model);
    let public = view.model();
    // Strict read: a corrupt ledger must FAIL the publish, never silently
    // deploy a site with every claim removed.
    let records = readers::load_active_records_strict(&args.vault_root, &layout)?;
    let terrain_src = args
        .vault_root
        .join(layout.crystal_store_dir())
        .join("terrain.json");

    let files = write_api_tree(
        &args.out_dir.join("api"),
        public,
        &records,
        &terrain_src,
        args.published_at.as_deref(),
    )?;

    Ok(PublishReport {
        file_count: files,
        content_hash: content_hash(public, &records),
        index_run_id: model.run_id.clone(),
        index_built_at: model.built_at.clone(),
        sources: public.sources.len(),
        claims: records.len(),
    })
}

/// Write the full public API tree under `api_dir` from an ALREADY-redacted
/// model + its durable records. Split out from `publish` so it's testable
/// without a full vault rebuild. Returns the file count. Everything is
/// public-lite: source pages carry no evidence layer, claim pages carry only
/// the short verbatim quote (no full unit text), terrain is filtered to public
/// sources.
fn write_api_tree(
    api: &Path,
    public: &IndexModel,
    records: &[ovp_domain::crystal::DurableRecord],
    terrain_src: &Path,
    published_at: Option<&str>,
) -> Result<usize, String> {
    fs_reset_dir(api)?;
    let mut files = 0usize;
    let public_shas: std::collections::HashSet<&str> =
        public.sources.iter().map(|s| s.sha256.as_str()).collect();

    // Top-level projections.
    write_json(&api.join("model.json"), &serde_json::to_value(public).unwrap_or_default())?;
    write_json(&api.join("themes.json"), &bodies::themes_body(records))?;
    write_json(&api.join("flow.json"), &bodies::flow_body(public))?;
    write_json(&api.join("settings.json"), &bodies::settings_public_body(Some(public)))?;
    let empty = Query { kind: None, status: None, date: None, term: None };
    write_json(&api.join("search-index.json"), &bodies::find_body(public, &empty))?;
    files += 5;

    // Graph: the global overview + one keyed file of per-theme subgraphs (keyed
    // by label so the SPA looks up client-side, no slug-matching). Neighborhood
    // subgraphs are omitted in static mode.
    // Match the embedded live view's cap (`fetchGlobalGraph(400)`) so the
    // published graph isn't the full 2000-node overview.
    let params = graph::GraphParams {
        mode: graph::GraphMode::Overview,
        limit: PUBLIC_GRAPH_LIMIT,
        theme: None,
        focus: None,
        hops: graph::MAX_HOPS,
    };
    if let Ok(g) = graph::build_graph(records, Some(public), &params) {
        write_json(&api.join("graph").join("global.json"), &serde_json::to_value(&g).unwrap_or_default())?;
        files += 1;
    }
    let mut themes_map = serde_json::Map::new();
    for (theme, _) in graph::theme_counts(records) {
        if let Ok(g) = graph::theme_subgraph(records, Some(public), &theme) {
            themes_map.insert(theme, serde_json::to_value(&g).unwrap_or_default());
        }
    }
    write_json(&api.join("graph").join("themes.json"), &serde_json::Value::Object(themes_map))?;
    files += 1;

    // Per-claim pages (durable records). `include_unit_text=false` → only the
    // short verbatim quote ships, not the fuller grounded-unit sentence. Alias
    // under BOTH claim_key and claim_id, since the model/graph link by claim_id
    // while `claim_body` keys the file name on claim_key.
    for r in records {
        if let Some(v) = bodies::claim_body(records, Some(public), Path::new(""), &r.claim_key, false) {
            write_json(&api.join("claim").join(format!("{}.json", r.claim_key)), &v)?;
            files += 1;
            if r.claim_id != r.claim_key {
                write_json(&api.join("claim").join(format!("{}.json", r.claim_id)), &v)?;
                files += 1;
            }
        }
    }

    // Per-source LITE pages: no evidence layer, no markdown body — link out to
    // the original via `source.url`.
    for s in &public.sources {
        if let Some(v) = bodies::source_body(public, None, &s.sha256, None) {
            write_json(&api.join("source").join(format!("{}.json", s.sha256)), &v)?;
            files += 1;
        }
    }

    // Terrain viz — filter points to PUBLIC sources (crystal-terrain builds a
    // point per reader pack, including orphan/non-processed ones whose title +
    // case id would otherwise leak). Copy through filtered if built.
    if let Ok(body) = std::fs::read_to_string(terrain_src)
        && let Some(filtered) = filter_terrain(&body, &public_shas)
    {
        write_json(&api.join("terrain.json"), &filtered)?;
        files += 1;
    }

    // Volatile stamps isolated in meta.json so content files stay stable.
    let meta = serde_json::json!({
        "built_at": public.built_at,
        "run_id": public.run_id,
        "published_at": published_at,
        "static": true,
    });
    write_json(&api.join("meta.json"), &meta)?;
    files += 1;

    Ok(files)
}

/// sha256 over the durable content only (sources, packs, claims, records, and
/// the terrain file if present) — NOT `built_at`/`run_id`. Two publishes with
/// the same knowledge produce the same hash, so the CLI can skip a no-op push.
fn content_hash(public: &IndexModel, records: &[ovp_domain::crystal::DurableRecord]) -> String {
    let mut h = Sha256::new();
    let payload = serde_json::json!({
        "sources": public.sources,
        "packs": public.packs,
        "claims": public.claims,
        "records": records,
    });
    h.update(serde_json::to_string(&payload).unwrap_or_default().as_bytes());
    format!("{:x}", h.finalize())
}

/// Keep only terrain points whose source sha is public; drop orphan/
/// non-processed points that would leak private titles + case ids. Returns
/// `None` when the file isn't the expected shape (skip writing it).
fn filter_terrain(
    body: &str,
    public_shas: &std::collections::HashSet<&str>,
) -> Option<serde_json::Value> {
    let mut v: serde_json::Value = serde_json::from_str(body).ok()?;
    let points = v.get_mut("points")?.as_array_mut()?;
    points.retain(|p| {
        p.get("sha")
            .and_then(|s| s.as_str())
            .is_some_and(|s| public_shas.contains(s))
    });
    let n = points.len();
    if let Some(obj) = v.as_object_mut() {
        obj.insert("point_count".into(), serde_json::json!(n));
    }
    Some(v)
}

fn write_json(path: &Path, value: &serde_json::Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string(value).map_err(|e| format!("serialize {}: {e}", path.display()))?;
    std::fs::write(path, body).map_err(|e| format!("write {}: {e}", path.display()))
}

/// Clear (or create) the api output dir so stale per-item files from a previous
/// publish (a retracted claim, a removed source) never linger.
fn fs_reset_dir(dir: &Path) -> Result<(), String> {
    if dir.exists() {
        std::fs::remove_dir_all(dir).map_err(|e| format!("clean {}: {e}", dir.display()))?;
    }
    std::fs::create_dir_all(dir).map_err(|e| format!("mkdir {}: {e}", dir.display()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        CrystalStatus, DurableCitation, DurableRecord, FinalClass, ProvenanceClass, StrengthClass,
    };
    use ovp_index::{ClaimRow, ClaimStatus, PackRow, SourceRow, SourceStatus, Totals};

    fn record() -> DurableRecord {
        DurableRecord {
            claim_key: "ck-abc123".into(),
            claim_id: "id-1".into(),
            claim: "A durable claim.".into(),
            theme: "Theme A".into(),
            source_cases: vec!["case1".into()],
            citations: vec![DurableCitation {
                case_id: "case1".into(),
                unit_id: "u-001".into(),
                quote: "q".into(),
                resolved_line: None,
            }],
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "ok".into(),
            final_class: FinalClass::Durable,
            run_id: "r1".into(),
            status: CrystalStatus::Active,
        }
    }

    fn src(sha: &str, status: SourceStatus) -> SourceRow {
        SourceRow {
            sha256: sha.into(),
            status,
            title: Some(format!("Src {sha}")),
            url: Some(format!("https://ex.com/{sha}")),
            rel_path: Some("50-Inbox/01-Raw/x.md".into()),
            date: Some("2026-07-01".into()),
            last_run_id: Some("r1".into()),
            pack_dir: Some("40-Resources/Reader/case1".into()),
            fail_count: 0,
            last_reason: None,
        }
    }

    fn model() -> IndexModel {
        IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-07-01".into(),
            built_at: Some("2026-07-01T00:00:00Z".into()),
            run_id: Some("r1".into()),
            totals: Totals::default(),
            sources: vec![src("aaa", SourceStatus::Processed), src("bbb", SourceStatus::Blocked)],
            packs: vec![PackRow {
                pack_dir: "40-Resources/Reader/case1".into(),
                title: "Src aaa".into(),
                date: Some("2026-07-01".into()),
                units: 3,
                cards: 2,
                json_repaired: false,
                card_titles: vec![],
                source_sha256: Some("aaa".into()),
            }],
            claims: vec![
                ClaimRow {
                    claim_id: "id-1".into(),
                    claim: "A durable claim.".into(),
                    theme: Some("Theme A".into()),
                    status: ClaimStatus::Durable,
                    sources: vec!["case1".into()],
                    strength: Some("supported".into()),
                    run_id: Some("r1".into()),
                    lane: None,
                },
                ClaimRow {
                    claim_id: "id-2".into(),
                    claim: "caveated".into(),
                    theme: Some("Theme A".into()),
                    status: ClaimStatus::Caveated,
                    sources: vec!["case1".into()],
                    strength: None,
                    run_id: None,
                    lane: Some("review".into()),
                },
            ],
            runs: vec![],
            ops: Default::default(),
        }
    }

    fn read(p: &Path) -> serde_json::Value {
        serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
    }

    #[test]
    fn write_api_tree_emits_public_files_only() {
        let tmp = tempfile::tempdir().unwrap();
        let api = tmp.path().join("api");
        let pv = PublicView::from_model(&model());
        let recs = vec![record()];
        let n = write_api_tree(
            &api,
            pv.model(),
            &recs,
            &tmp.path().join("no-terrain.json"),
            Some("2026-07-01T12:00:00Z"),
        )
        .unwrap();
        assert!(n >= 8, "expected the full tree, got {n} files");

        // model.json: processed source only, durable claim only.
        let m = read(&api.join("model.json"));
        assert_eq!(m["sources"].as_array().unwrap().len(), 1);
        assert_eq!(m["claims"].as_array().unwrap().len(), 1);

        // Source-lite page for the processed source, NO markdown body; the
        // blocked source has no page.
        let s = read(&api.join("source/aaa.json"));
        assert!(s["doc"]["markdown"].is_null(), "lite page must omit markdown");
        assert!(!api.join("source/bbb.json").exists(), "blocked source must not publish");

        // Per-claim page exists under BOTH claim_key and claim_id; ships the
        // short quote but not the fuller unit text.
        assert!(api.join("claim/ck-abc123.json").exists());
        assert!(api.join("claim/id-1.json").exists(), "claim_id alias missing");
        let c = read(&api.join("claim/id-1.json"));
        assert_eq!(c["citations"][0]["quote"], "q");
        assert_eq!(c["citations"][0]["unit_text"], "", "unit_text must be redacted");

        // Source-lite page carries no evidence layer.
        assert!(s["memory"]["cards"].as_array().unwrap().is_empty());
        assert!(s["memory"]["units"].as_array().unwrap().is_empty());
        // pack_dir reduced to the case_id basename (no vault folder path).
        assert_eq!(s["source"]["pack_dir"], "case1");

        // meta carries published_at; terrain absent (source file missing).
        let meta = read(&api.join("meta.json"));
        assert_eq!(meta["published_at"], "2026-07-01T12:00:00Z");
        assert!(!api.join("terrain.json").exists());

        // settings scrubbed.
        assert!(read(&api.join("settings.json")).get("vault_root").is_none());
    }

    #[test]
    fn content_hash_ignores_volatile_stamps() {
        let mut a = model();
        let h1 = content_hash(PublicView::from_model(&a).model(), &[record()]);
        // Only the stamps change → identical content hash (no-op publish skips).
        a.built_at = Some("2099-01-01T00:00:00Z".into());
        a.run_id = Some("rZ".into());
        let h2 = content_hash(PublicView::from_model(&a).model(), &[record()]);
        assert_eq!(h1, h2);
    }
}
