use serde::{Deserialize, Serialize};

use crate::canonical_slug::{CanonicalSlug, SlugError};
use crate::interpreted::{ExtractedConcept, InterpretedDoc};

/// A proposal to mint a NEW evergreen concept — a `concept_candidate`
/// that `ConceptResolver` did not promote (no canonical page exists yet).
/// `EvergreenConceptWriter` emits one per new candidate; `EvergreenSink`
/// turns it into the actual write surface (`VaultCreate` for the page +
/// `CanonicalUpsert` to register canonical identity).
///
/// **M12a — rich minting.** A concept minted from an interpreted article
/// carries grounding pulled deterministically from that article: a one-line
/// `definition`, up to five `source_claims`, the `source_title`, and `related`
/// slugs. `EvergreenSink` renders these into a grounded note body, so a
/// freshly minted note is a usable knowledge unit, not a bare stub. The body
/// is a pure function of these fields, so re-minting the *same* concept is an
/// idempotent `VaultCreate` (same content → same hash).
///
/// **M12b — same-slug reconcile.** The grounded body is per-document, so two
/// *distinct* articles surfacing the same slug render *different* bodies. A raw
/// `VaultCreate` to an existing path with a different hash fails loud (the
/// applier never overwrites), but `RunCycle` reconciles before applying: an
/// already-present note is enriched via a merge `VaultUpdate` (see
/// [`crate::reconcile_evergreen_write`]), so a repeat slug enriches rather than
/// failing the run. Still future: semantic dedup of near-duplicate claims,
/// concept-specific definitions, and mint/enrich/reject policy lanes.
///
/// The thin constructors ([`Self::try_from_candidate`] / [`Self::from_candidate`])
/// leave the rich fields empty; `EvergreenSink` then falls back to the legacy
/// provenance-free stub body. Production minting goes through [`Self::try_mint`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvergreenConcept {
    /// Canonical slug (the candidate string, e.g. `agent-native-pm`).
    pub slug: String,
    /// Human title derived from the slug.
    pub title: String,
    /// Source URL of the document that proposed this concept.
    pub provenance_source_url: String,
    /// One-sentence atomic definition (the article's `one_liner` dimension).
    /// Empty for a thin/stub mint.
    pub definition: String,
    /// 2-5 source-backed claims selected deterministically from the article.
    /// Empty for a thin/stub mint.
    pub source_claims: Vec<String>,
    /// Title of the document that surfaced this concept (for the Source link).
    pub source_title: String,
    /// Related concept slugs to wikilink (the article's `linked_concepts`).
    pub related: Vec<String>,
}

impl EvergreenConcept {
    /// Build from a raw candidate slug + the proposing document's URL,
    /// validating the slug through [`CanonicalSlug`]. Returns `Err` if the
    /// candidate is not a safe single-segment slug — the caller
    /// (`EvergreenConceptWriter`) drops invalid candidates with an
    /// observable reason rather than minting a divergent concept. The
    /// stored slug is the normalized form (surrounding whitespace trimmed).
    pub fn try_from_candidate(
        raw_slug: &str,
        provenance_source_url: impl Into<String>,
    ) -> Result<Self, SlugError> {
        let slug = CanonicalSlug::parse(raw_slug)?;
        let title = title_from_slug(slug.as_str());
        Ok(Self {
            slug: slug.into_string(),
            title,
            provenance_source_url: provenance_source_url.into(),
            definition: String::new(),
            source_claims: Vec::new(),
            source_title: String::new(),
            related: Vec::new(),
        })
    }

    /// Build from a known-valid slug (tests, fixtures, seeding). Panics on
    /// an invalid slug — production minting must use [`Self::try_from_candidate`]
    /// or [`Self::try_mint`] so invalid candidates are dropped, not aborted.
    pub fn from_candidate(slug: impl Into<String>, provenance_source_url: impl Into<String>) -> Self {
        let slug = slug.into();
        Self::try_from_candidate(&slug, provenance_source_url)
            .unwrap_or_else(|e| panic!("invalid evergreen slug `{slug}`: {e}"))
    }

    /// Mint a *rich* concept from a candidate slug + the interpreted article
    /// that surfaced it. Validates the slug (same discipline as
    /// [`Self::try_from_candidate`]) and pulls grounding from the article:
    /// the `one_liner` becomes the `definition`, [`select_source_claims`]
    /// picks the claims, `linked_concepts` (minus self) become `related`, and
    /// provenance is the article's `source_url`. Pure: same `(slug, doc)` →
    /// same concept, so the rendered note body is deterministic.
    pub fn try_mint(raw_slug: &str, interp: &InterpretedDoc) -> Result<Self, SlugError> {
        let slug = CanonicalSlug::parse(raw_slug)?.into_string();
        let title = title_from_slug(&slug);
        Ok(Self {
            definition: interp.dimensions.one_liner.trim().to_string(),
            source_claims: select_source_claims(interp, &slug),
            source_title: interp.title.clone(),
            related: select_related(interp, &slug),
            provenance_source_url: interp.source_url.clone(),
            title,
            slug,
        })
    }

    /// Mint from a v2 [`ExtractedConcept`] — using the concept's OWN
    /// `definition`, `claims`, and `related` (M13.2), NOT the article
    /// `one_liner` or token-matched article claims. The slug is assumed valid:
    /// the `ConceptResolver` gate validated + normalized it (and dropped
    /// invalid/low-evidence concepts) before this point, so the only path to a
    /// minted note is through a gated concept.
    pub fn from_extracted(
        c: &ExtractedConcept,
        source_title: impl Into<String>,
        source_url: impl Into<String>,
    ) -> Self {
        let title = if c.title.trim().is_empty() {
            title_from_slug(&c.slug)
        } else {
            c.title.trim().to_string()
        };
        Self {
            slug: c.slug.clone(),
            title,
            provenance_source_url: source_url.into(),
            definition: c.definition.trim().to_string(),
            source_claims: c
                .claims
                .iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
            source_title: source_title.into(),
            related: c
                .related
                .iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
        }
    }
}

/// Deterministically select up to five source-backed claims for a minted
/// concept. Deliberately *explainable, not scored*: build a priority-ordered
/// pool from the article's `details`, then `actions`, then the what/why/how
/// explanation; keep claims that mention a slug token first (in pool order),
/// and if fewer than a small floor matched, top up from the front of the pool
/// so a grounded note never degrades to a bare definition when the article has
/// material. Pure and order-stable. Empty pool → empty claims (the note still
/// carries its definition).
fn select_source_claims(interp: &InterpretedDoc, slug: &str) -> Vec<String> {
    const MAX_CLAIMS: usize = 5;
    let tokens = slug_tokens(slug);
    let pool = claim_pool(interp);

    let mut selected: Vec<String> = Vec::new();
    // Pass 1: claims that mention a slug token, in pool (priority) order.
    for c in &pool {
        if mentions_any_token(c, &tokens) && !selected.iter().any(|s| s == *c) {
            selected.push((*c).to_string());
            if selected.len() == MAX_CLAIMS {
                return selected;
            }
        }
    }
    // Pass 2: top up from the front of the pool to a small floor (≤3) so a
    // note with material is never reduced to a lone definition.
    let floor = pool.len().min(3);
    if selected.len() < floor {
        for c in &pool {
            if !selected.iter().any(|s| s == *c) {
                selected.push((*c).to_string());
                if selected.len() >= floor {
                    break;
                }
            }
        }
    }
    selected.truncate(MAX_CLAIMS);
    selected
}

/// Related concept slugs for a minted concept: the article's `linked_concepts`,
/// trimmed, de-duplicated, with the concept's own slug removed (no self-link),
/// capped at eight. Pure and order-stable.
fn select_related(interp: &InterpretedDoc, slug: &str) -> Vec<String> {
    const MAX_RELATED: usize = 8;
    let mut out: Vec<String> = Vec::new();
    for r in &interp.dimensions.linked_concepts {
        let r = r.trim();
        if r.is_empty() || r == slug {
            continue;
        }
        if !out.iter().any(|x| x == r) {
            out.push(r.to_string());
            if out.len() == MAX_RELATED {
                break;
            }
        }
    }
    out
}

/// Priority-ordered claim pool: details, then actions, then what/why/how.
/// Trims each entry, drops empties, and drops exact duplicates (so a claim
/// repeated across dimensions is not double-counted and the selection floor
/// reflects *distinct* claims). Borrows from `interp`.
fn claim_pool(interp: &InterpretedDoc) -> Vec<&str> {
    let d = &interp.dimensions;
    let mut pool: Vec<&str> = Vec::new();
    for s in &d.details {
        let t = s.trim();
        if !t.is_empty() && !pool.contains(&t) {
            pool.push(t);
        }
    }
    for s in &d.actions {
        let t = s.trim();
        if !t.is_empty() && !pool.contains(&t) {
            pool.push(t);
        }
    }
    for s in [&d.explanation.what, &d.explanation.why, &d.explanation.how] {
        let t = s.trim();
        if !t.is_empty() && !pool.contains(&t) {
            pool.push(t);
        }
    }
    pool
}

/// Lowercased slug tokens: `agent-native-pm` → `["agent", "native", "pm"]`.
fn slug_tokens(slug: &str) -> Vec<String> {
    slug.split(['-', '_']).filter(|w| !w.is_empty()).map(|w| w.to_lowercase()).collect()
}

/// True if `text` contains any slug token as a *whole word* (alphanumeric
/// tokenization, matching the retriever's), so a short token like `ai` does
/// not spuriously match inside `claim`.
fn mentions_any_token(text: &str, tokens: &[String]) -> bool {
    let words = tokenize(text);
    tokens.iter().any(|t| words.iter().any(|w| w == t))
}

/// Split a string into lowercased alphanumeric tokens (mirrors the retriever).
fn tokenize(s: &str) -> Vec<String> {
    s.split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_lowercase())
        .collect()
}

/// Render a slug into a title: split on `-`/`_`, capitalize ASCII words,
/// leave non-ASCII (e.g. Chinese) words untouched. `agent-native-pm` →
/// `Agent Native Pm`; `对话即工作` → `对话即工作`.
fn title_from_slug(slug: &str) -> String {
    slug.split(['-', '_'])
        .filter(|w| !w.is_empty())
        .map(capitalize_ascii_word)
        .collect::<Vec<_>>()
        .join(" ")
}

fn capitalize_ascii_word(w: &str) -> String {
    let mut chars = w.chars();
    match chars.next() {
        Some(first) if first.is_ascii_alphabetic() => {
            first.to_ascii_uppercase().to_string() + chars.as_str()
        }
        Some(first) => first.to_string() + chars.as_str(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn title_from_ascii_slug() {
        let c = EvergreenConcept::from_candidate("agent-native-pm", "https://x/y");
        assert_eq!(c.slug, "agent-native-pm");
        assert_eq!(c.title, "Agent Native Pm");
        assert_eq!(c.provenance_source_url, "https://x/y");
    }

    #[test]
    fn title_preserves_non_ascii() {
        let c = EvergreenConcept::from_candidate("对话即工作", "u");
        assert_eq!(c.title, "对话即工作");
    }

    #[test]
    fn title_mixed_underscore_and_dash() {
        let c = EvergreenConcept::from_candidate("rag_vs-graphrag", "u");
        assert_eq!(c.title, "Rag Vs Graphrag");
    }

    #[test]
    fn try_from_candidate_rejects_invalid_slug() {
        assert_eq!(
            EvergreenConcept::try_from_candidate("a/b", "u"),
            Err(SlugError::PathSeparator)
        );
        assert_eq!(EvergreenConcept::try_from_candidate("  ", "u"), Err(SlugError::Empty));
    }

    #[test]
    fn try_from_candidate_normalizes_whitespace() {
        let c = EvergreenConcept::try_from_candidate("  rag\n", "u").unwrap();
        assert_eq!(c.slug, "rag");
        assert_eq!(c.title, "Rag");
    }

    // ---- M12a: rich minting ----

    use crate::interpreted::{Dimensions, Explanation, InterpretationSchema};

    fn interp(
        one_liner: &str,
        details: Vec<&str>,
        actions: Vec<&str>,
        linked: Vec<&str>,
    ) -> InterpretedDoc {
        InterpretedDoc {
            title: "Source Article".into(),
            source_url: "https://example.com/post".into(),
            author: None,
            date: "2026-05-31".into(),
            doc_type: "article".into(),
            area: "ai".into(),
            tags: vec![],
            canonical_concepts: vec![],
            concept_candidates: vec![],
            dimensions: Dimensions {
                one_liner: one_liner.into(),
                explanation: Explanation { what: "".into(), why: "".into(), how: "".into() },
                details: details.into_iter().map(String::from).collect(),
                structure: None,
                actions: actions.into_iter().map(String::from).collect(),
                linked_concepts: linked.into_iter().map(String::from).collect(),
            },
            schema: InterpretationSchema::ArticleV1,
            concepts: Vec::new(),
        }
    }

    #[test]
    fn mint_pulls_definition_source_title_and_provenance() {
        let d = interp("RAG augments generation with retrieval.", vec![], vec![], vec![]);
        let c = EvergreenConcept::try_mint("rag", &d).unwrap();
        assert_eq!(c.slug, "rag");
        assert_eq!(c.title, "Rag");
        assert_eq!(c.definition, "RAG augments generation with retrieval.");
        assert_eq!(c.source_title, "Source Article");
        assert_eq!(c.provenance_source_url, "https://example.com/post");
    }

    #[test]
    fn mint_selects_token_matched_claim_first() {
        let d = interp(
            "x",
            vec![
                "Unrelated point about pipelines.",
                "RAG retrieves documents before generation.",
                "Another general note.",
            ],
            vec![],
            vec![],
        );
        let c = EvergreenConcept::try_mint("rag", &d).unwrap();
        // The token-matched claim is selected first, then the floor tops up.
        assert_eq!(c.source_claims[0], "RAG retrieves documents before generation.");
        assert_eq!(c.source_claims.len(), 3, "floor = min(3, pool)");
    }

    #[test]
    fn mint_tops_up_from_front_when_no_token_match() {
        let d = interp(
            "x",
            vec!["First detail.", "Second detail.", "Third detail.", "Fourth detail."],
            vec![],
            vec![],
        );
        let c = EvergreenConcept::try_mint("kubernetes", &d).unwrap();
        // No claim mentions the slug → take the first three details (floor).
        assert_eq!(c.source_claims, vec!["First detail.", "Second detail.", "Third detail."]);
    }

    #[test]
    fn mint_claims_capped_at_five() {
        let d = interp(
            "x",
            vec!["rag a", "rag b", "rag c", "rag d", "rag e", "rag f", "rag g"],
            vec![],
            vec![],
        );
        let c = EvergreenConcept::try_mint("rag", &d).unwrap();
        assert_eq!(c.source_claims.len(), 5, "capped at 5");
        assert_eq!(c.source_claims[0], "rag a");
    }

    #[test]
    fn mint_claim_pool_falls_back_to_actions_and_explanation() {
        let mut d = interp("x", vec![], vec!["Action one mentions rag."], vec![]);
        d.dimensions.explanation =
            Explanation { what: "What note about rag.".into(), why: "".into(), how: "".into() };
        let c = EvergreenConcept::try_mint("rag", &d).unwrap();
        // Pool order is details → actions → what/why/how; both mention "rag".
        assert_eq!(c.source_claims[0], "Action one mentions rag.");
        assert!(c.source_claims.iter().any(|s| s == "What note about rag."));
    }

    #[test]
    fn mint_related_drops_self_dedups_and_caps() {
        let d = interp("x", vec![], vec![], vec!["rag", "ai-agent", "ai-agent", "vector-db"]);
        let c = EvergreenConcept::try_mint("rag", &d).unwrap();
        assert_eq!(c.related, vec!["ai-agent", "vector-db"], "self removed, dup removed");
    }

    #[test]
    fn mint_rejects_invalid_slug() {
        let d = interp("x", vec![], vec![], vec![]);
        assert!(matches!(
            EvergreenConcept::try_mint("a/b", &d),
            Err(SlugError::PathSeparator)
        ));
    }

    #[test]
    fn thin_constructor_leaves_rich_fields_empty() {
        let c = EvergreenConcept::from_candidate("rag", "u");
        assert!(c.definition.is_empty());
        assert!(c.source_claims.is_empty());
        assert!(c.source_title.is_empty());
        assert!(c.related.is_empty());
    }
}
