//! `crystal-synth` domain stages — the pure + model-facing halves of the
//! turnkey Crystal synthesis command. Everything here is offline-testable: the
//! deterministic stages (catalog collection, keyword clustering, grounded
//! filtering) take plain data, and the two model stages take `&mut dyn
//! ModelClient` so they replay from cassettes with zero network.
//!
//! Reuse-first: the grounded filter and every gate delegate to the existing
//! `crate::crystal` functions (`lint_candidate` / `score_candidate`), so the
//! Crystal layer can never drift from the reader trunk's truth invariants. This
//! module NEVER touches demoted substrate (referents / concept_registry /
//! canonical / moc / knowledge_index / evergreen).

use std::collections::BTreeMap;
use std::path::Path;

use ovp_llm::{ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};

use crate::crystal::{
    Citation, CrystalCandidate, CrystalClaim, GroundingIndex, ProvenanceClass, lint_candidate,
    score_candidate,
};
use crate::units::{Unit, UnitStatus};

const SYNTH_TEMPLATE: &str = include_str!("../../prompts/crystal_synth.md");
const STRENGTH_TEMPLATE: &str = include_str!("../../prompts/crystal_strength.md");
/// Cassette namespace + version marker for the synthesis stage.
pub const CRYSTAL_SYNTH_PROMPT_ID: &str = "crystal_synth/v1";
/// Cassette namespace + version marker for the claim-strength stage.
pub const CRYSTAL_STRENGTH_PROMPT_ID: &str = "crystal_strength/v1";
const DEFAULT_MODEL: &str = "claude-sonnet-4-6";
/// Synthesis reads many units and writes several claims — generous headroom.
const SYNTH_MAX_TOKENS: u32 = 8192;
/// Strength batches all grounded claims into one call.
const STRENGTH_MAX_TOKENS: u32 = 8192;

// ---- Deterministic catalog + clustering ----

/// One accepted unit as fed to the synthesis prompt (verbatim quote + line).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogUnit {
    pub unit_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub line: Option<usize>,
    pub quote: String,
    /// Unit attribution (author / quoted_person / system_interpretation) and
    /// modality (asserted / suggested / uncertain / contested / negated), so the
    /// strength judge can catch `opinion_as_fact` / modality mismatch. Without
    /// these the judge is asked to check modality but never given it.
    #[serde(default)]
    pub attribution: String,
    #[serde(default)]
    pub modality: String,
}

/// Serialize a units enum (Attribution/Modality) to its snake_case string.
fn enum_str<T: Serialize>(v: &T) -> String {
    serde_json::to_value(v)
        .ok()
        .and_then(|x| x.as_str().map(str::to_string))
        .unwrap_or_default()
}

/// One source case: its display title and its accepted units.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogCase {
    pub title: String,
    pub units: Vec<CatalogUnit>,
}

/// The whole reader-pack catalog: `case_id -> { title, units }`. BTreeMap for
/// deterministic iteration (stable clustering + stable cassette keys).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitsCatalog {
    pub cases: BTreeMap<String, CatalogCase>,
}

/// A keyword theme cluster over cases (bucket key + human theme + case ids).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Cluster {
    pub key: String,
    pub theme: String,
    pub cases: Vec<String>,
}

/// One bounded synthesis batch inside a theme cluster. Stage 3a keeps the
/// existing cluster semantics but stops truncating large clusters: every case is
/// assigned to exactly one deterministic batch, and each batch independently
/// reuses the same `crystal_synth/v1` prompt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClusterBatch {
    pub key: String,
    pub theme: String,
    pub batch_index: usize,
    pub batch_count: usize,
    pub cases: Vec<String>,
}

impl ClusterBatch {
    /// Claim-id namespace for this batch. A one-batch cluster keeps the legacy
    /// key so existing replay fixtures remain stable; multi-batch clusters add
    /// a deterministic suffix to avoid claim-id collisions across batches.
    pub fn claim_prefix(&self) -> String {
        if self.batch_count <= 1 {
            self.key.clone()
        } else {
            format!("{}-b{:03}", self.key, self.batch_index + 1)
        }
    }
}

/// Errors from the deterministic collection stage.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SynthError {
    /// A filesystem read failed (path + detail).
    Io(String),
    /// A `units.accepted.json` did not parse (path + detail).
    Parse(String),
    /// No reader pack under the reader dir had an accepted-units file.
    Empty(String),
}

impl std::fmt::Display for SynthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SynthError::Io(s) => write!(f, "io: {s}"),
            SynthError::Parse(s) => write!(f, "parse: {s}"),
            SynthError::Empty(s) => write!(f, "no reader packs with accepted units: {s}"),
        }
    }
}

/// The first `# ` heading of a `reader.md`, if present. Used as the case title
/// (mirrors the index's `build_packs` title resolution). Returns the trimmed
/// heading text without the leading `# `.
fn first_heading(reader_md: &str) -> Option<String> {
    for line in reader_md.lines() {
        let t = line.trim_start();
        if let Some(rest) = t.strip_prefix("# ") {
            let h = rest.trim();
            if !h.is_empty() {
                return Some(h.to_string());
            }
        }
    }
    None
}

/// Resolve a case title: `reader.md`'s first heading, else `run-status.json`'s
/// `source`, else the directory name. Best-effort — never fails the run.
/// Public so `crystal-themes` titles packs identically to the synth catalog.
pub fn resolve_title(case_dir: &Path, case_id: &str) -> String {
    if let Ok(md) = std::fs::read_to_string(case_dir.join("reader.md"))
        && let Some(h) = first_heading(&md) {
            return h;
        }
    if let Ok(rs) = std::fs::read_to_string(case_dir.join("run-status.json"))
        && let Ok(v) = serde_json::from_str::<serde_json::Value>(&rs)
            && let Some(s) = v.get("source").and_then(|s| s.as_str())
                && !s.trim().is_empty() {
                    return s.trim().to_string();
                }
    case_id.to_string()
}

/// Read every reader-pack subdir under `reader_dir`, collect accepted+quoted
/// units into a catalog keyed by directory name (== case_id, matching the
/// grounding index built from the packs dir). Deterministic; pure fs read.
///
/// A unit is kept iff `status == Accepted` and its `quote` is non-empty (the
/// reader already source-verified these spans, so the citation linter's
/// verbatim check is guaranteed to be able to hit them).
pub fn collect_catalog(reader_dir: &Path) -> Result<UnitsCatalog, SynthError> {
    let entries = std::fs::read_dir(reader_dir)
        .map_err(|e| SynthError::Io(format!("reading reader dir {}: {e}", reader_dir.display())))?;
    let mut catalog = UnitsCatalog::default();
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let units_path = path.join("units.accepted.json");
        if !units_path.exists() {
            continue;
        }
        let text = std::fs::read_to_string(&units_path)
            .map_err(|e| SynthError::Io(format!("reading {}: {e}", units_path.display())))?;
        let units: Vec<Unit> = serde_json::from_str(&text)
            .map_err(|e| SynthError::Parse(format!("{}: {e}", units_path.display())))?;
        let case_id = entry.file_name().to_string_lossy().to_string();
        let title = resolve_title(&path, &case_id);
        let kept: Vec<CatalogUnit> = units
            .iter()
            .filter(|u| u.status == UnitStatus::Accepted && !u.evidence.quote.trim().is_empty())
            .map(|u| CatalogUnit {
                unit_id: u.id.clone(),
                line: u.evidence.location.as_ref().map(|l| l.line),
                quote: u.evidence.quote.clone(),
                attribution: enum_str(&u.attribution),
                modality: enum_str(&u.modality),
            })
            .collect();
        if kept.is_empty() {
            continue;
        }
        catalog
            .cases
            .insert(case_id, CatalogCase { title, units: kept });
    }
    if catalog.cases.is_empty() {
        return Err(SynthError::Empty(reader_dir.display().to_string()));
    }
    Ok(catalog)
}

/// Copy each case's accepted units through to `<packs_dir>/<case_id>/units.accepted.json`
/// so the downstream linter + `crystal-write` read a single canonical packs dir
/// keyed identically to the catalog. Writes only the kept (accepted+quoted)
/// units, re-serialized as a `Vec<Unit>` — the exact shape `build_index` reads.
///
/// Takes the already-loaded units so it never re-reads or drifts from the
/// catalog filter. Deterministic.
pub fn write_packs(
    packs_dir: &Path,
    reader_dir: &Path,
    catalog: &UnitsCatalog,
) -> Result<(), SynthError> {
    // Clear any prior contents so a rerun with a narrower --reader-dir (or after
    // deleting reader packs) cannot leave stale cases the grounding index would
    // still scan and let out-of-scope citations satisfy.
    if packs_dir.exists() {
        std::fs::remove_dir_all(packs_dir).map_err(|e| {
            SynthError::Io(format!("clearing packs dir {}: {e}", packs_dir.display()))
        })?;
    }
    for case_id in catalog.cases.keys() {
        // Re-load the source units for this case and re-apply the same filter,
        // so the packs dir carries full `Unit` records (the linter needs the
        // evidence/location, not just the catalog projection).
        let src = reader_dir.join(case_id).join("units.accepted.json");
        let text = std::fs::read_to_string(&src)
            .map_err(|e| SynthError::Io(format!("reading {}: {e}", src.display())))?;
        let units: Vec<Unit> = serde_json::from_str(&text)
            .map_err(|e| SynthError::Parse(format!("{}: {e}", src.display())))?;
        let kept: Vec<Unit> = units
            .into_iter()
            .filter(|u| u.status == UnitStatus::Accepted && !u.evidence.quote.trim().is_empty())
            .collect();
        let out_dir = packs_dir.join(case_id);
        std::fs::create_dir_all(&out_dir)
            .map_err(|e| SynthError::Io(format!("creating {}: {e}", out_dir.display())))?;
        let s = serde_json::to_string_pretty(&kept)
            .map_err(|e| SynthError::Parse(format!("serializing units for {case_id}: {e}")))?;
        std::fs::write(out_dir.join("units.accepted.json"), format!("{s}\n"))
            .map_err(|e| SynthError::Io(format!("writing packs for {case_id}: {e}")))?;
    }
    Ok(())
}

/// Build the grounding index (packs -> accepted units) directly from a catalog's
/// source, by reading the same `<packs_dir>/<case>/units.accepted.json`. Reused
/// by the CLI so the linter sees exactly what `write_packs` produced.
pub fn build_grounding_index(packs_dir: &Path) -> Result<GroundingIndex, SynthError> {
    let entries = std::fs::read_dir(packs_dir)
        .map_err(|e| SynthError::Io(format!("reading packs dir {}: {e}", packs_dir.display())))?;
    let mut index = GroundingIndex::new();
    for entry in entries.flatten() {
        if !entry.path().is_dir() {
            continue;
        }
        let units_path = entry.path().join("units.accepted.json");
        if !units_path.exists() {
            continue;
        }
        let text = std::fs::read_to_string(&units_path)
            .map_err(|e| SynthError::Io(format!("reading {}: {e}", units_path.display())))?;
        let units: Vec<Unit> = serde_json::from_str(&text)
            .map_err(|e| SynthError::Parse(format!("{}: {e}", units_path.display())))?;
        index.insert(entry.file_name().to_string_lossy().to_string(), units);
    }
    if index.is_empty() {
        return Err(SynthError::Empty(packs_dir.display().to_string()));
    }
    Ok(index)
}

// NOTE (M-semantic-themes): the pilot's hardcoded 8-bucket English keyword
// taxonomy (`bucket_for` / `cluster_by_keyword`) is RETIRED. Synthesis
// grouping now comes from the semantic themes projection
// (`crate::crystal::themes::clusters_from_themes`) when `themes.json` exists,
// else deterministic date-ordered batches
// (`crate::crystal::themes::clusters_date_ordered`).

/// Split clusters into deterministic bounded batches. Returns an empty vec only
/// when `clusters` is empty; callers must reject `max_cases_per_batch == 0`.
pub fn cluster_batches(clusters: &[Cluster], max_cases_per_batch: usize) -> Vec<ClusterBatch> {
    let mut out = Vec::new();
    if max_cases_per_batch == 0 {
        return out;
    }
    for cluster in clusters {
        let batch_count = cluster.cases.len().div_ceil(max_cases_per_batch);
        for (batch_index, chunk) in cluster.cases.chunks(max_cases_per_batch).enumerate() {
            out.push(ClusterBatch {
                key: cluster.key.clone(),
                theme: cluster.theme.clone(),
                batch_index,
                batch_count,
                cases: chunk.to_vec(),
            });
        }
    }
    out
}

// ---- Synthesis model stage ----

/// A cluster sliced to caps and paired with its case data — the model input.
#[derive(Debug, Clone, Serialize)]
struct ClusterSlice<'a> {
    theme: &'a str,
    cases: BTreeMap<&'a str, SlicedCase<'a>>,
}

#[derive(Debug, Clone, Serialize)]
struct SlicedCase<'a> {
    title: &'a str,
    units: Vec<&'a CatalogUnit>,
}

/// Build the sliced case JSON for one cluster (caps applied), sorted by case_id.
fn slice_cases<'a>(
    catalog: &'a UnitsCatalog,
    theme: &'a str,
    cases_iter: impl IntoIterator<Item = &'a String>,
    max_units: usize,
) -> ClusterSlice<'a> {
    let mut cases: BTreeMap<&str, SlicedCase> = BTreeMap::new();
    for case_id in cases_iter {
        if let Some(case) = catalog.cases.get(case_id) {
            let units: Vec<&CatalogUnit> = case.units.iter().take(max_units).collect();
            cases.insert(
                case_id.as_str(),
                SlicedCase {
                    title: &case.title,
                    units,
                },
            );
        }
    }
    ClusterSlice { theme, cases }
}

/// Build the synthesis `ModelRequest` for one cluster (namespace = synth/v1).
pub fn crystal_synth_request(
    catalog: &UnitsCatalog,
    cluster: &Cluster,
    max_cases: usize,
    max_units: usize,
) -> ModelRequest {
    synth_request_for_cases(
        catalog,
        &cluster.theme,
        cluster.cases.iter().take(max_cases),
        max_units,
    )
}

/// Build the synthesis `ModelRequest` for one Stage 3a batch. If a cluster has
/// only one batch and the same caps are used, this request is byte-identical to
/// the legacy cluster request, preserving existing cassette keys.
pub fn crystal_synth_batch_request(
    catalog: &UnitsCatalog,
    batch: &ClusterBatch,
    max_units: usize,
) -> ModelRequest {
    synth_request_for_cases(catalog, &batch.theme, batch.cases.iter(), max_units)
}

fn synth_request_for_cases<'a>(
    catalog: &'a UnitsCatalog,
    theme: &'a str,
    cases_iter: impl IntoIterator<Item = &'a String>,
    max_units: usize,
) -> ModelRequest {
    let marker = "## Cases";
    let (system, _) = SYNTH_TEMPLATE
        .split_once(marker)
        .unwrap_or((SYNTH_TEMPLATE, ""));
    let slice = slice_cases(catalog, theme, cases_iter, max_units);
    let user = format!(
        "{marker}\n\nTheme: {theme}\n\n{cases}\n",
        theme = theme,
        cases = serde_json::to_string_pretty(&slice.cases).unwrap_or_else(|_| "{}".to_string()),
    );
    ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: SYNTH_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(CRYSTAL_SYNTH_PROMPT_ID.to_string()),
    }
}

/// The model's per-claim shape before it is namespaced into a `CrystalClaim`.
#[derive(Debug, Clone, Deserialize)]
struct RawSynthClaim {
    // NOTE: the model's `id` is intentionally NOT captured — synthesized claim
    // ids are a unique per-cluster ordinal (see parse_synth_claims). serde
    // ignores the unknown `id` key by default.
    #[serde(default)]
    claim: String,
    #[serde(default)]
    theme: String,
    #[serde(default)]
    citations: Vec<RawCitation>,
    #[serde(default)]
    caveat: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct RawCitation {
    #[serde(default)]
    case_id: String,
    #[serde(default)]
    unit_id: String,
    #[serde(default)]
    quote: String,
}

/// Parse a synthesis reply's `{ "claims": [...] }` envelope into namespaced
/// `CrystalClaim`s. Claim ids are prefixed with the cluster key (`<key>-<id>`)
/// to avoid collisions across clusters; malformed claims (empty text or no
/// citations) are dropped, not fatal. Returns `Err(detail)` if no claims array.
pub fn parse_synth_claims(
    reply_text: &str,
    cluster_key: &str,
) -> Result<Vec<CrystalClaim>, String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    let arr = value
        .get("claims")
        .and_then(|c| c.as_array())
        .ok_or("missing `claims` array")?;
    let mut out = Vec::with_capacity(arr.len());
    for item in arr.iter() {
        let Ok(rc) = serde_json::from_value::<RawSynthClaim>(item.clone()) else {
            continue;
        };
        if rc.claim.trim().is_empty() || rc.citations.is_empty() {
            continue;
        }
        // Derive the id from the KEPT-claim ordinal, not the model's `id`: the
        // model can repeat an id within a cluster, and downstream gates key on
        // claim_id (strength_coverage / final_of / verdict_of) — a collision
        // would apply one claim's verdict/score to another. Sequential per
        // cluster is unique and deterministic.
        let id = format!("{cluster_key}-{}", out.len() + 1);
        let citations: Vec<Citation> = rc
            .citations
            .into_iter()
            .filter(|c| {
                !c.case_id.trim().is_empty()
                    && !c.unit_id.trim().is_empty()
                    && !c.quote.trim().is_empty()
            })
            .map(|c| Citation {
                case_id: c.case_id.trim().to_string(),
                unit_id: c.unit_id.trim().to_string(),
                quote: c.quote,
                claimed_line: None,
            })
            .collect();
        if citations.is_empty() {
            continue;
        }
        out.push(CrystalClaim {
            id,
            claim: rc.claim.trim().to_string(),
            theme: if rc.theme.trim().is_empty() {
                cluster_key.to_string()
            } else {
                rc.theme.trim().to_string()
            },
            citations,
            caveat: rc.caveat.filter(|s| !s.trim().is_empty()),
        });
    }
    Ok(out)
}

// ---- Grounded filter (delegates to the existing gate) ----

/// Drop any claim that is not fully grounded per the SAME `lint_candidate` the
/// reader trunk uses (a citation with a Quote/Case/Unit defect, or zero
/// citations). The survivors are guaranteed defect-free, so a downstream
/// `crystal-write` gate is satisfied by construction. Returns the pruned
/// candidate plus the ids that were dropped (for auditability). Deterministic.
pub fn filter_grounded(
    candidate: &CrystalCandidate,
    index: &GroundingIndex,
) -> (CrystalCandidate, Vec<String>) {
    let report = lint_candidate(candidate, index);
    let grounded_ids: std::collections::BTreeSet<&str> = report
        .claims
        .iter()
        .filter(|c| c.fully_grounded)
        .map(|c| c.claim_id.as_str())
        .collect();
    let mut kept = Vec::new();
    let mut dropped = Vec::new();
    for item in &candidate.items {
        if grounded_ids.contains(item.id.as_str()) {
            kept.push(item.clone());
        } else {
            dropped.push(item.id.clone());
        }
    }
    (CrystalCandidate { items: kept }, dropped)
}

/// One deterministic duplicate removed before the strength judge.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DedupedClaim {
    pub kept_claim_id: String,
    pub dropped_claim_id: String,
    pub reason: String,
}

fn citation_signature(citations: &[Citation]) -> String {
    let mut parts: Vec<String> = citations
        .iter()
        .map(|c| {
            format!(
                "{}\u{1f}{}\u{1f}{}",
                c.case_id.trim(),
                c.unit_id.trim(),
                c.quote.trim()
            )
        })
        .collect();
    parts.sort();
    parts.dedup();
    parts.join("\u{1e}")
}

/// Conservative Stage 3a reduce: if two grounded claims cite the exact same
/// evidence set, keep the first deterministic emission and drop later repeats.
/// This avoids spending strength-judge tokens on obvious duplicates without
/// claiming semantic equivalence between merely similar claims.
pub fn dedup_exact_citation_sets(
    candidate: &CrystalCandidate,
) -> (CrystalCandidate, Vec<DedupedClaim>) {
    let mut seen: BTreeMap<String, String> = BTreeMap::new();
    let mut kept = Vec::new();
    let mut dropped = Vec::new();
    for item in &candidate.items {
        let sig = citation_signature(&item.citations);
        if let Some(kept_claim_id) = seen.get(&sig) {
            dropped.push(DedupedClaim {
                kept_claim_id: kept_claim_id.clone(),
                dropped_claim_id: item.id.clone(),
                reason: "exact_citation_set".to_string(),
            });
        } else {
            seen.insert(sig, item.id.clone());
            kept.push(item.clone());
        }
    }
    (CrystalCandidate { items: kept }, dropped)
}

// ---- Strength model stage ----

/// Build the claim-strength `ModelRequest` for a grounded candidate (namespace =
/// strength/v1). Batches every claim + its cited quotes into one call.
pub fn strength_request(candidate: &CrystalCandidate, catalog: &UnitsCatalog) -> ModelRequest {
    // (case_id, unit_id) -> (attribution, modality), so each cited quote carries
    // the modality the judge needs to catch opinion_as_fact / modality mismatch.
    let mut meta: std::collections::HashMap<(&str, &str), (&str, &str)> =
        std::collections::HashMap::new();
    for (case_id, case) in &catalog.cases {
        for u in &case.units {
            meta.insert(
                (case_id.as_str(), u.unit_id.as_str()),
                (u.attribution.as_str(), u.modality.as_str()),
            );
        }
    }
    let marker = "## Claims and their cited evidence";
    let (system, _) = STRENGTH_TEMPLATE
        .split_once(marker)
        .unwrap_or((STRENGTH_TEMPLATE, ""));
    let mut user = format!("{marker}\n\n");
    for item in &candidate.items {
        user.push_str(&format!("### claim_id: {}\n", item.id));
        user.push_str(&format!("claim: {}\n", item.claim));
        if let Some(cav) = &item.caveat {
            user.push_str(&format!("caveat: {cav}\n"));
        }
        user.push_str("cited quotes (with the cited unit's attribution/modality):\n");
        for c in &item.citations {
            let (attr, modal) = meta
                .get(&(c.case_id.as_str(), c.unit_id.as_str()))
                .copied()
                .unwrap_or(("", ""));
            user.push_str(&format!(
                "- ({} · {}) [attribution={} modality={}] \"{}\"\n",
                c.case_id,
                c.unit_id,
                if attr.is_empty() { "unknown" } else { attr },
                if modal.is_empty() { "unknown" } else { modal },
                c.quote
            ));
        }
        user.push('\n');
    }
    ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system.trim_end().to_string()),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: STRENGTH_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(CRYSTAL_STRENGTH_PROMPT_ID.to_string()),
    }
}

/// Parse a strength reply into `ClaimStrengthVerdict`s. Accepts either a bare
/// `[ ... ]` array or a `{ "verdicts": [...] }` envelope. Returns `Err(detail)`
/// if neither shape is found.
pub fn parse_strength_verdicts(
    reply_text: &str,
) -> Result<Vec<crate::crystal::ClaimStrengthVerdict>, String> {
    let (value, _note) =
        crate::model_reply::parse_reply_value(reply_text).map_err(|d| d.to_string())?;
    let arr = if value.is_array() {
        value.as_array().cloned().unwrap_or_default()
    } else if let Some(a) = value.get("verdicts").and_then(|v| v.as_array()) {
        a.clone()
    } else {
        return Err("expected a verdict array or `{verdicts:[...]}`".to_string());
    };
    let mut out = Vec::with_capacity(arr.len());
    for item in &arr {
        let v: crate::crystal::ClaimStrengthVerdict = serde_json::from_value(item.clone())
            .map_err(|e| format!("bad strength verdict: {e}"))?;
        out.push(v);
    }
    Ok(out)
}

/// Convenience: count durable-provenance claims in a scored candidate (used only
/// for the CLI summary; the real routing is `final_routing` in the write path).
pub fn count_durable_provenance(candidate: &CrystalCandidate, index: &GroundingIndex) -> usize {
    let report = lint_candidate(candidate, index);
    score_candidate(&report)
        .iter()
        .filter(|s| s.class == ProvenanceClass::Durable)
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;

    /// Build accepted Units for a case from a body + quotes (mirrors crystal.rs).
    fn accepted_units(body: &str, quotes: &[&str]) -> Vec<Unit> {
        let raw: Vec<_> = quotes
            .iter()
            .enumerate()
            .map(|(i, q)| {
                serde_json::json!({
                    "kind": "assertion", "text": format!("t{i}"), "evidence_ref": "p001",
                    "evidence_quote": q, "attribution": "author", "modality": "asserted", "arguments": []
                })
            })
            .collect();
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![], body),
        );
        ex.accepted().cloned().collect()
    }

    fn write_pack(dir: &Path, case_id: &str, title: &str, body: &str, quotes: &[&str]) {
        let case_dir = dir.join(case_id);
        std::fs::create_dir_all(&case_dir).unwrap();
        let units = accepted_units(body, quotes);
        std::fs::write(
            case_dir.join("units.accepted.json"),
            serde_json::to_string_pretty(&units).unwrap(),
        )
        .unwrap();
        std::fs::write(case_dir.join("reader.md"), format!("# {title}\n\nbody\n")).unwrap();
    }

    #[test]
    fn collect_catalog_keys_by_dirname_and_filters_unquoted() {
        let tmp = tempfile::tempdir().unwrap();
        write_pack(
            tmp.path(),
            "m18-01",
            "Agent memory systems",
            "A chunk is a structurally neutral container.",
            &["A chunk is a structurally neutral container."],
        );
        // A dir with NO units file is skipped.
        std::fs::create_dir_all(tmp.path().join("empty-case")).unwrap();
        let cat = collect_catalog(tmp.path()).unwrap();
        assert_eq!(cat.cases.len(), 1);
        let case = &cat.cases["m18-01"];
        assert_eq!(
            case.title, "Agent memory systems",
            "title from reader.md heading"
        );
        assert_eq!(case.units.len(), 1);
        assert!(!case.units[0].quote.is_empty());
    }

    #[test]
    fn collect_catalog_empty_dir_errors() {
        let tmp = tempfile::tempdir().unwrap();
        let err = collect_catalog(tmp.path()).unwrap_err();
        assert!(matches!(err, SynthError::Empty(_)));
    }

    #[test]
    fn collect_catalog_missing_dir_is_io_error() {
        let err = collect_catalog(Path::new("/nonexistent/reader/dir")).unwrap_err();
        assert!(matches!(err, SynthError::Io(_)));
    }

    #[test]
    fn cluster_batches_cover_every_case_without_truncation() {
        let cluster = Cluster {
            key: "memory".into(),
            theme: "Memory & context".into(),
            cases: vec![
                "c1".into(),
                "c2".into(),
                "c3".into(),
                "c4".into(),
                "c5".into(),
            ],
        };
        let batches = cluster_batches(&[cluster], 2);
        assert_eq!(batches.len(), 3);
        assert_eq!(batches[0].cases, vec!["c1", "c2"]);
        assert_eq!(batches[1].cases, vec!["c3", "c4"]);
        assert_eq!(batches[2].cases, vec!["c5"]);
        assert_eq!(batches[0].claim_prefix(), "memory-b001");
        assert_eq!(batches[2].claim_prefix(), "memory-b003");
    }

    #[test]
    fn write_packs_and_index_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        write_pack(
            tmp.path(),
            "m18-01",
            "Agents",
            "A chunk is a structurally neutral container.",
            &["A chunk is a structurally neutral container."],
        );
        let cat = collect_catalog(tmp.path()).unwrap();
        let packs = tmp.path().join("packs");
        write_packs(&packs, tmp.path(), &cat).unwrap();
        let index = build_grounding_index(&packs).unwrap();
        assert!(index.contains_key("m18-01"));
        assert_eq!(index["m18-01"].len(), 1);
    }

    #[test]
    fn parse_synth_claims_namespaces_and_drops_uncited() {
        let reply = r#"{"claims":[
            {"id":"1","claim":"cross-source finding","theme":"memory","citations":[{"case_id":"m18-01","unit_id":"u-0","quote":"neutral container"}]},
            {"id":"2","claim":"no citations here","citations":[]},
            {"claim":"","citations":[{"case_id":"x","unit_id":"y","quote":"z"}]}
        ]}"#;
        let claims = parse_synth_claims(reply, "memory").unwrap();
        assert_eq!(claims.len(), 1, "uncited + empty-text dropped");
        assert_eq!(claims[0].id, "memory-1", "namespaced by cluster key");
        assert_eq!(claims[0].citations.len(), 1);
    }

    #[test]
    fn filter_grounded_drops_defective_claim() {
        let body = "A chunk is a structurally neutral container.";
        let units = accepted_units(body, &[body]);
        let uid = units[0].id.clone();
        let mut index = GroundingIndex::new();
        index.insert("m18-01".to_string(), units);
        let good = CrystalClaim {
            id: "memory-1".into(),
            claim: "grounded".into(),
            theme: "memory".into(),
            citations: vec![Citation {
                case_id: "m18-01".into(),
                unit_id: uid,
                quote: "structurally neutral".into(),
                claimed_line: None,
            }],
            caveat: None,
        };
        let bad = CrystalClaim {
            id: "memory-2".into(),
            claim: "ungrounded".into(),
            theme: "memory".into(),
            citations: vec![Citation {
                case_id: "m18-01".into(),
                unit_id: "u-nope".into(),
                quote: "nope".into(),
                claimed_line: None,
            }],
            caveat: None,
        };
        let cand = CrystalCandidate {
            items: vec![good, bad],
        };
        let (kept, dropped) = filter_grounded(&cand, &index);
        assert_eq!(kept.items.len(), 1);
        assert_eq!(kept.items[0].id, "memory-1");
        assert_eq!(dropped, vec!["memory-2"]);
        // The survivor is defect-free by construction.
        let report = lint_candidate(&kept, &index);
        assert_eq!(report.n_with_defects, 0);
    }

    #[test]
    fn dedup_exact_citation_sets_keeps_first_claim_only() {
        let cite = Citation {
            case_id: "m18-01".into(),
            unit_id: "u-1".into(),
            quote: "same quote".into(),
            claimed_line: None,
        };
        let first = CrystalClaim {
            id: "memory-b001-1".into(),
            claim: "first wording".into(),
            theme: "memory".into(),
            citations: vec![cite.clone()],
            caveat: None,
        };
        let duplicate = CrystalClaim {
            id: "memory-b002-1".into(),
            claim: "second wording".into(),
            theme: "memory".into(),
            citations: vec![cite],
            caveat: None,
        };
        let distinct = CrystalClaim {
            id: "memory-b002-2".into(),
            claim: "different evidence".into(),
            theme: "memory".into(),
            citations: vec![Citation {
                case_id: "m18-02".into(),
                unit_id: "u-2".into(),
                quote: "other quote".into(),
                claimed_line: None,
            }],
            caveat: None,
        };
        let (kept, dropped) = dedup_exact_citation_sets(&CrystalCandidate {
            items: vec![first, duplicate, distinct],
        });
        assert_eq!(
            kept.items.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(),
            vec!["memory-b001-1", "memory-b002-2"]
        );
        assert_eq!(dropped.len(), 1);
        assert_eq!(dropped[0].kept_claim_id, "memory-b001-1");
        assert_eq!(dropped[0].dropped_claim_id, "memory-b002-1");
    }

    #[test]
    fn parse_strength_verdicts_array_or_envelope() {
        let arr = r#"[{"claim_id":"memory-1","strength":"supported","evidence_sufficient":true,"rationale":"ok"}]"#;
        let v = parse_strength_verdicts(arr).unwrap();
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].claim_id, "memory-1");
        let env = r#"{"verdicts":[{"claim_id":"a","strength":"overreach","evidence_sufficient":false,"rationale":"r"}]}"#;
        let v2 = parse_strength_verdicts(env).unwrap();
        assert_eq!(v2.len(), 1);
    }

    #[test]
    fn synth_request_carries_namespace_and_cases() {
        let mut cat = UnitsCatalog::default();
        cat.cases.insert(
            "m18-01".into(),
            CatalogCase {
                title: "Agents".into(),
                units: vec![CatalogUnit {
                    unit_id: "u-0".into(),
                    line: Some(3),
                    quote: "a quote".into(),
                    attribution: "author".into(),
                    modality: "asserted".into(),
                }],
            },
        );
        let clusters = crate::crystal::themes::clusters_date_ordered(&cat, 16);
        let req = crystal_synth_request(&cat, &clusters[0], 16, 22);
        assert_eq!(req.cache_namespace.as_deref(), Some("crystal_synth/v1"));
        let ModelMessage::User { content } = &req.messages[0] else {
            panic!()
        };
        assert!(content.contains("m18-01"));
        assert!(content.contains("a quote"));
        assert!(req.system.unwrap().contains("crystal_synth/v1"));
    }
}
