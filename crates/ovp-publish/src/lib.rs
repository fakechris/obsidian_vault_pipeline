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
    EvidenceModel, IndexModel, Query, build_evidence, build_index_at, now_rfc3339, read_evidence,
    read_index, write_evidence, write_index,
};
use sha2::{Digest, Sha256};

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

    // 1. Fresh, self-consistent model + evidence (or read the existing ones).
    let (model, evidence): (IndexModel, Option<EvidenceModel>) = if args.no_rebuild {
        let m = read_index(&args.vault_root)?;
        let e = read_evidence(&args.vault_root).ok();
        (m, e)
    } else {
        let built_at = now_rfc3339();
        let run_id = format!("publish-{built_at}");
        let m = build_index_at(&args.vault_root, &args.date, Some(&run_id), &built_at)?;
        write_index(&args.vault_root, &m)?;
        let e = build_evidence(&args.vault_root, &args.date, &m)?;
        write_evidence(&args.vault_root, &e)?;
        (m, Some(e))
    };

    // 2. Redact to public-safe. 3. Durable/active records (durable-only by
    // construction — caveated live in review.json and never in the ledger).
    let view = PublicView::from_model(&model);
    let public = view.model();
    let records = readers::load_active_records(&args.vault_root, &layout);
    let reader_root = args.vault_root.join(layout.reader_root());

    let api = args.out_dir.join("api");
    fs_reset_dir(&api)?;
    let mut files = 0usize;

    // 4a. Top-level projections.
    write_json(&api.join("model.json"), &serde_json::to_value(public).unwrap_or_default())?;
    write_json(&api.join("themes.json"), &bodies::themes_body(&records))?;
    write_json(&api.join("flow.json"), &bodies::flow_body(public))?;
    write_json(&api.join("settings.json"), &bodies::settings_public_body(Some(public)))?;
    let empty = Query { kind: None, status: None, date: None, term: None };
    write_json(&api.join("search-index.json"), &bodies::find_body(public, &empty))?;
    files += 5;

    // 4b. Graph: the global overview + one keyed file of per-theme subgraphs
    // (a handful of themes; keyed by label so the SPA looks up client-side, no
    // slug-matching). Neighborhood subgraphs are omitted in static mode.
    let params = graph::GraphParams {
        mode: graph::GraphMode::Overview,
        limit: graph::DEFAULT_OVERVIEW_LIMIT,
        theme: None,
        focus: None,
        hops: graph::MAX_HOPS,
    };
    if let Ok(g) = graph::build_graph(&records, Some(public), &params) {
        write_json(&api.join("graph").join("global.json"), &serde_json::to_value(&g).unwrap_or_default())?;
        files += 1;
    }
    let mut themes_map = serde_json::Map::new();
    for (theme, _) in graph::theme_counts(&records) {
        if let Ok(g) = graph::theme_subgraph(&records, Some(public), &theme) {
            themes_map.insert(theme, serde_json::to_value(&g).unwrap_or_default());
        }
    }
    write_json(&api.join("graph").join("themes.json"), &serde_json::Value::Object(themes_map))?;
    files += 1;

    // 4c. Per-claim pages (durable records).
    for r in &records {
        if let Some(v) = bodies::claim_body(&records, Some(public), &reader_root, &r.claim_key) {
            write_json(&api.join("claim").join(format!("{}.json", r.claim_key)), &v)?;
            files += 1;
        }
    }

    // 4d. Per-source LITE pages (no markdown body — link out to the original).
    for s in &public.sources {
        if let Some(v) = bodies::source_body(public, evidence.as_ref(), &s.sha256, None) {
            write_json(&api.join("source").join(format!("{}.json", s.sha256)), &v)?;
            files += 1;
        }
    }

    // 4e. Terrain viz — already public-safe (points carry source sha + title +
    // theme, no third-party body). Copy through if built.
    let terrain = args
        .vault_root
        .join(layout.crystal_store_dir())
        .join("terrain.json");
    if let Ok(body) = std::fs::read_to_string(&terrain) {
        std::fs::write(api.join("terrain.json"), &body)
            .map_err(|e| format!("write terrain.json: {e}"))?;
        files += 1;
    }

    // 4f. Volatile stamps isolated in meta.json so content files stay stable.
    let meta = serde_json::json!({
        "built_at": public.built_at,
        "run_id": public.run_id,
        "published_at": args.published_at,
        "static": true,
    });
    write_json(&api.join("meta.json"), &meta)?;
    files += 1;

    let content_hash = content_hash(public, &records);
    Ok(PublishReport {
        file_count: files,
        content_hash,
        index_run_id: model.run_id.clone(),
        index_built_at: model.built_at.clone(),
        sources: public.sources.len(),
        claims: records.len(),
    })
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
