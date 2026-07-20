//! `ovp-publish` — snapshot the public-safe read-only API surface to a static
//! `site/api/*.json` tree, so the same `console-ui` SPA (built in static mode)
//! can serve durable knowledge + the visualizations off any static host with no
//! server.
//!
//! The snapshot is *just another projection* of the ledgers — deterministic,
//! rebuilt whole each publish, content-hash-stable so git computes a minimal
//! diff. It reuses the SAME `ovp-api-projection` builders the live server uses
//! (no drift) over a `PublicView` (durable claims + processed sources only).

pub mod run;

use std::path::{Path, PathBuf};

use ovp_api_projection::{PublicView, bodies, graph, readers};
use ovp_domain::VaultLayout;
use ovp_index::{
    IndexModel, Query, build_evidence, build_index_at, now_rfc3339, write_evidence, write_index,
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

    // 0. Hold the vault's shared run lock for the WHOLE snapshot so a concurrent
    // `daily`/`crystal` write can't make the freshly-built model and the
    // separately-folded ledger observe different revisions (which would leak a
    // retracted claim or drop a new one). Released on drop at function end.
    let _lock = ovp_intake::RunLock::acquire(&args.vault_root)
        .map_err(|e| format!("publish: vault is busy (another run holds the lock): {e}"))?;

    // 1. ALWAYS build a fresh model, so it and the ledger records folded below
    // reflect the SAME revision (a stale index.json + current ledger would
    // publish retracted claims in model.json while dropping them from claim
    // files). `--no-rebuild` only skips PERSISTING index.json/evidence.json back
    // to the vault (a read-only publish); the in-memory build is cheap.
    let built_at = now_rfc3339();
    let run_id = format!("publish-{built_at}");
    let model: IndexModel =
        build_index_at(&args.vault_root, &args.date, Some(&run_id), &built_at)?;
    if !args.no_rebuild {
        write_index(&args.vault_root, &model)?;
        let e = build_evidence(&args.vault_root, &args.date, &model)?;
        write_evidence(&args.vault_root, &e)?;
    }

    // 2. Redact to a public-safe OWNED model.
    let mut public = PublicView::from_model(&model).into_model();
    let public_cases: std::collections::HashSet<String> =
        public.packs.iter().map(|p| p.pack_dir.clone()).collect();

    // Themes are a majority label over a claim's cited packs. After dropping
    // private cases the original label may reflect a removed pack, so recompute
    // it from the surviving PUBLIC cases (both the model's claims and the
    // records fed to claim/graph/theme projections).
    let themes_file = ovp_domain::crystal::themes::ThemesFile::load(
        &args
            .vault_root
            .join(layout.crystal_store_dir())
            .join("themes.json"),
    )
    .ok()
    .flatten();
    if let Some(tf) = &themes_file {
        for c in public.claims.iter_mut() {
            c.theme = tf.majority_label(&c.sources).or_else(|| c.theme.clone());
        }
    }

    // 3. Durable/active records (durable-only by construction — caveated live in
    // review.json and never in the ledger). Strict read: a corrupt ledger must
    // FAIL the publish, never silently deploy a site with every claim removed.
    let records = readers::load_active_records_strict(&args.vault_root, &layout)?;
    // Scrub each record's citations to PUBLIC cases (matching the claim
    // filtering) and recompute the theme, so claim pages / graph / themes never
    // surface an orphan case id or a removed pack's label; drop records left
    // with no public citation.
    let records: Vec<ovp_domain::crystal::DurableRecord> = records
        .into_iter()
        .filter_map(|mut r| {
            r.citations.retain(|c| public_cases.contains(&c.case_id));
            r.source_cases.retain(|s| public_cases.contains(s));
            if r.citations.is_empty() {
                return None;
            }
            if let Some(tf) = &themes_file {
                r.theme = tf.majority_label(&r.source_cases).unwrap_or_else(|| {
                    ovp_domain::crystal::themes::UNCLASSIFIED_THEME.to_string()
                });
            }
            Some(r)
        })
        .collect();
    let public = &public;
    let terrain_src = args
        .vault_root
        .join(layout.crystal_store_dir())
        .join("terrain.json");

    // Grounded topic pages. Strict like the ledger: a corrupt projection must
    // FAIL the publish, not silently deploy a site without its wiki layer. A
    // genuinely missing file (never built) is fine — the body ships empty.
    let theme_pages = ovp_domain::crystal::theme_pages::ThemePagesFile::load(
        &args
            .vault_root
            .join(layout.crystal_store_dir())
            .join("theme_pages.json"),
    )
    .map_err(|e| format!("publish: {e}"))?;

    let files = write_api_tree(
        &args.out_dir.join("api"),
        public,
        &records,
        theme_pages.as_ref(),
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
    theme_pages: Option<&ovp_domain::crystal::theme_pages::ThemePagesFile>,
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
    // Topic pages join against the SCRUBBED records: a page citing any
    // non-public claim is dropped whole inside the body builder.
    write_json(
        &api.join("theme-pages.json"),
        &bodies::theme_pages_body(theme_pages, records),
    )?;
    write_json(&api.join("flow.json"), &bodies::flow_body(public))?;
    write_json(&api.join("settings.json"), &bodies::settings_public_body(Some(public)))?;
    let empty = Query { kind: None, status: None, date: None, term: None, tag: None , entity: None };
    write_json(&api.join("search-index.json"), &bodies::find_body(public, &empty))?;
    files += 6;

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
        persp: graph::Perspective::Claim,
    };
    if let Ok(g) = graph::build_graph(records, Some(public), &params) {
        write_json(&api.join("graph").join("global.json"), &serde_json::to_value(&g).unwrap_or_default())?;
        files += 1;
    }
    // Source perspective of the same overview (the portal's ?persp=source
    // toggle) — a second static file the SPA loads client-side.
    let source_params = graph::GraphParams {
        persp: graph::Perspective::Source,
        ..params.clone()
    };
    if let Ok(g) = graph::build_graph(records, Some(public), &source_params) {
        write_json(
            &api.join("graph").join("global-source.json"),
            &serde_json::to_value(&g).unwrap_or_default(),
        )?;
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

    // `claim_id` is cluster/run-local and can repeat across records, while
    // `claim_key` is the unique ledger identity — only alias by claim_id when
    // it's globally unique, else the alias would silently serve the wrong record.
    let mut id_counts: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    for r in records {
        *id_counts.entry(r.claim_id.as_str()).or_default() += 1;
    }

    // Per-claim pages (durable records). `include_unit_text=false` → only the
    // short verbatim quote ships, not the fuller grounded-unit sentence. Alias
    // under claim_key (unique) plus claim_id when unambiguous (the model/graph
    // link by claim_id while `claim_body` keys on claim_key). `safe_component`
    // neutralizes any `..`/`/` so a filename can't escape the output tree.
    for r in records {
        if let Some(v) = bodies::claim_body(records, Some(public), Path::new(""), &r.claim_key, false) {
            write_json(&api.join("claim").join(format!("{}.json", safe_component(&r.claim_key))), &v)?;
            files += 1;
            if r.claim_id != r.claim_key && id_counts.get(r.claim_id.as_str()) == Some(&1) {
                write_json(&api.join("claim").join(format!("{}.json", safe_component(&r.claim_id))), &v)?;
                files += 1;
            }
        }
    }

    // Tier-0 URL entities: the index + one page per entity. Public content
    // (URLs, unlike personal tags), so they ship. `safe_component` guards the
    // filename against `/` in ids like `github:owner/repo`.
    let entities = bodies::entities_body(public);
    write_json(&api.join("entities.json"), &entities)?;
    files += 1;
    if let Some(list) = entities.get("entities").and_then(|v| v.as_array()) {
        for e in list {
            if let Some(id) = e.get("id").and_then(|v| v.as_str())
                && let Some(v) = bodies::entity_body(public, id)
            {
                write_json(&api.join("entity").join(format!("{}.json", entity_filename(id))), &v)?;
                files += 1;
            }
        }
    }

    // Per-source LITE pages: no evidence layer, no markdown body — link out to
    // the original via `source.url`. The `lite` flag tells the SPA to show the
    // link-out presentation instead of "run `ovp2 index`" remediation.
    for s in &public.sources {
        if let Some(mut v) = bodies::source_body(public, None, &s.sha256, None) {
            if let Some(o) = v.as_object_mut() {
                o.insert("lite".into(), serde_json::json!(true));
            }
            write_json(&api.join("source").join(format!("{}.json", safe_component(&s.sha256))), &v)?;
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
/// non-processed points that would leak private titles + case ids. The `themes`
/// array (labels, counts, centroids) is REBUILT from the retained points so it
/// carries no full-corpus metadata and its counts/positions match what ships.
/// Returns `None` when the file isn't the expected shape (skip writing it).
fn filter_terrain(
    body: &str,
    public_shas: &std::collections::HashSet<&str>,
) -> Option<serde_json::Value> {
    use std::collections::BTreeMap;
    let mut v: serde_json::Value = serde_json::from_str(body).ok()?;

    // Preserve each theme id's display labels before we drop the private array.
    let labels: BTreeMap<i64, (serde_json::Value, serde_json::Value)> = v
        .get("themes")
        .and_then(|t| t.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|t| {
                    let id = t.get("id")?.as_i64()?;
                    Some((id, (t.get("label")?.clone(), t.get("label_zh")?.clone())))
                })
                .collect()
        })
        .unwrap_or_default();

    let points = v.get_mut("points")?.as_array_mut()?;
    points.retain(|p| {
        p.get("sha")
            .and_then(|s| s.as_str())
            .is_some_and(|s| public_shas.contains(s))
    });

    // Recompute per-theme count + centroid from the retained points only.
    let mut agg: BTreeMap<i64, (usize, f64, f64)> = BTreeMap::new();
    for p in points.iter() {
        let Some(id) = p.get("theme_id").and_then(|t| t.as_i64()) else {
            continue;
        };
        let x = p.get("x").and_then(|n| n.as_f64()).unwrap_or(0.0);
        let y = p.get("y").and_then(|n| n.as_f64()).unwrap_or(0.0);
        let e = agg.entry(id).or_insert((0, 0.0, 0.0));
        e.0 += 1;
        e.1 += x;
        e.2 += y;
    }
    let n = points.len();
    let themes: Vec<serde_json::Value> = agg
        .iter()
        .filter(|(id, _)| **id >= 0) // the noise bucket has no island label
        .map(|(id, (count, sx, sy))| {
            let (label, label_zh) = labels
                .get(id)
                .cloned()
                .unwrap_or((serde_json::json!(""), serde_json::json!("")));
            serde_json::json!({
                "id": id,
                "label": label,
                "label_zh": label_zh,
                "cx": sx / *count as f64,
                "cy": sy / *count as f64,
                "count": count,
            })
        })
        .collect();

    // Recompute bounds from the retained points so excluded (private) points no
    // longer stretch the map and compress the public ones into a corner.
    let (mut minx, mut miny, mut maxx, mut maxy) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
    for p in points.iter() {
        let x = p.get("x").and_then(|n| n.as_f64()).unwrap_or(0.0);
        let y = p.get("y").and_then(|n| n.as_f64()).unwrap_or(0.0);
        minx = minx.min(x);
        miny = miny.min(y);
        maxx = maxx.max(x);
        maxy = maxy.max(y);
    }
    let bounds = if n == 0 {
        [0.0, 0.0, 0.0, 0.0]
    } else {
        [minx, miny, maxx, maxy]
    };

    if let Some(obj) = v.as_object_mut() {
        obj.insert("point_count".into(), serde_json::json!(n));
        obj.insert("themes".into(), serde_json::Value::Array(themes));
        obj.insert("bounds".into(), serde_json::json!(bounds));
    }
    Some(v)
}

/// Collapse an id (claim key/id, sha) to ONE safe path component: keep
/// `[A-Za-z0-9._-]`, map everything else (incl. `/` and `..`) to `_`, so a
/// hostile or malformed id can never escape the output directory.
fn safe_component(id: &str) -> String {
    let s: String = id
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') { c } else { '_' })
        .collect();
    // A leading-dots-only name (`.`, `..`) would still be unsafe after mapping;
    // `..` maps to `..` (dots are kept) → force a prefix.
    if s.is_empty() || s.chars().all(|c| c == '.') {
        format!("_{s}")
    } else {
        s
    }
}

/// Base64url (no padding) — a REVERSIBLE, collision-free, filesystem-safe
/// filename for an entity id. `safe_component` maps both `:` and `/` to `_`,
/// so `doi:10.x/a:b` and `doi:10.x/a/b` would collide; entity ids are ascii
/// (URL-derived), and the SPA computes the same encoding to fetch the file.
fn entity_filename(id: &str) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let b = id.as_bytes();
    let mut out = String::with_capacity(b.len().div_ceil(3) * 4);
    for chunk in b.chunks(3) {
        let n = chunk.len();
        let triple = (u32::from(chunk[0]) << 16)
            | (chunk.get(1).map_or(0, |&c| u32::from(c)) << 8)
            | chunk.get(2).map_or(0, |&c| u32::from(c));
        out.push(T[((triple >> 18) & 63) as usize] as char);
        out.push(T[((triple >> 12) & 63) as usize] as char);
        if n > 1 {
            out.push(T[((triple >> 6) & 63) as usize] as char);
        }
        if n > 2 {
            out.push(T[(triple & 63) as usize] as char);
        }
    }
    out
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

    #[test]
    fn entity_filename_is_collision_free_and_matches_base64url() {
        // The two DOIs that collide under `safe_component` (`:`/`/` → `_`)
        // get distinct base64url filenames.
        assert_ne!(
            entity_filename("doi:10.x/a:b"),
            entity_filename("doi:10.x/a/b")
        );
        // Standard base64url(no-pad) — the exact string the SPA's
        // `btoa(id).replace(+→-,/→_).strip(=)` produces.
        assert_eq!(entity_filename("github:mem0ai/mem0"), "Z2l0aHViOm1lbTBhaS9tZW0w");
        assert_eq!(entity_filename("arxiv:1"), "YXJ4aXY6MQ");
    }

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
            tags: Vec::new(),
            tags_inferred: Vec::new(),
            entities: Vec::new(),
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
                    claim_key: Some("ck-abc123".into()),
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
                    claim_key: None,
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
        let pages = ovp_domain::crystal::theme_pages::ThemePagesFile {
            schema: ovp_domain::crystal::theme_pages::THEME_PAGES_SCHEMA.into(),
            pages: vec![
                ovp_domain::crystal::theme_pages::ThemePage {
                    community_id: 0,
                    label: "Theme A".into(),
                    label_zh: "主题A".into(),
                    claim_keys: vec!["ck-abc123".into()],
                    sections: vec![ovp_domain::crystal::theme_pages::PageSection {
                        heading: "H".into(),
                        body: "Grounded [claim:ck-abc123].".into(),
                    }],
                },
                // Cites a claim that does not survive redaction — the page
                // must be dropped whole from the published body.
                ovp_domain::crystal::theme_pages::ThemePage {
                    community_id: 1,
                    label: "Private".into(),
                    label_zh: "私有".into(),
                    claim_keys: vec!["ck-private".into()],
                    sections: vec![ovp_domain::crystal::theme_pages::PageSection {
                        heading: "P".into(),
                        body: "Hidden [claim:ck-private].".into(),
                    }],
                },
            ],
        };
        let n = write_api_tree(
            &api,
            pv.model(),
            &recs,
            Some(&pages),
            &tmp.path().join("no-terrain.json"),
            Some("2026-07-01T12:00:00Z"),
        )
        .unwrap();
        assert!(n >= 8, "expected the full tree, got {n} files");

        // theme-pages.json: the public page ships with its claim lookup, the
        // page citing a non-public claim is dropped whole.
        let tp = read(&api.join("theme-pages.json"));
        assert_eq!(tp["pages"].as_array().unwrap().len(), 1);
        assert_eq!(tp["pages"][0]["label"], "Theme A");
        assert_eq!(tp["claims"]["ck-abc123"]["claim_id"], "id-1");
        assert!(tp["claims"].get("ck-private").is_none());

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
    fn filter_terrain_drops_orphans_and_rebuilds_themes() {
        let body = serde_json::json!({
            "schema": "ovp.crystal.terrain/v1",
            "point_count": 3,
            "bounds": [0.0, 0.0, 100.0, 100.0],
            "themes": [
                { "id": 1, "label": "Public", "label_zh": "公开", "cx": 9.9, "cy": 9.9, "count": 99 },
                { "id": 5, "label": "PrivateOnly", "label_zh": "私有", "cx": 1.0, "cy": 1.0, "count": 7 }
            ],
            "points": [
                { "sha": "aaa", "theme_id": 1, "x": 10.0, "y": 20.0, "title": "A" },
                { "sha": "bbb", "theme_id": 1, "x": 30.0, "y": 40.0, "title": "B" },
                { "sha": "zzz", "theme_id": 5, "x": 1.0, "y": 1.0, "title": "orphan-private" }
            ]
        })
        .to_string();
        let public: std::collections::HashSet<&str> = ["aaa", "bbb"].into_iter().collect();
        let v = filter_terrain(&body, &public).unwrap();

        // Orphan point (private theme 5) dropped; count updated.
        assert_eq!(v["points"].as_array().unwrap().len(), 2);
        assert_eq!(v["point_count"], 2);
        // Themes rebuilt from retained points: only theme 1, centroid recomputed
        // ((10+30)/2, (20+40)/2) = (20, 30), NOT the full-corpus (9.9, 9.9)/99.
        let themes = v["themes"].as_array().unwrap();
        assert_eq!(themes.len(), 1);
        assert_eq!(themes[0]["id"], 1);
        assert_eq!(themes[0]["count"], 2);
        assert_eq!(themes[0]["cx"], 20.0);
        assert_eq!(themes[0]["cy"], 30.0);
        assert_eq!(themes[0]["label"], "Public");
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
