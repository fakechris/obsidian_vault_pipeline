use serde::{Deserialize, Serialize};

use crate::canonical_slug::{CanonicalSlug, SlugError};

/// A proposal to mint a NEW evergreen concept — a `concept_candidate`
/// that `ConceptResolver` did not promote (no canonical page exists yet).
/// `EvergreenConceptWriter` emits one per new candidate; `EvergreenSink`
/// turns it into the actual write surface (`VaultCreate` for the stub
/// page + `CanonicalUpsert` to register canonical identity).
///
/// The stub page's content is derived purely from `slug`/`title` so that
/// re-minting the same concept is an idempotent VaultCreate. Provenance
/// (which doc first surfaced it) rides in the `CanonicalUpsert` payload,
/// where the canonical store can merge it across documents later.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvergreenConcept {
    /// Canonical slug (the candidate string, e.g. `agent-native-pm`).
    pub slug: String,
    /// Human title derived from the slug.
    pub title: String,
    /// Source URL of the document that proposed this concept.
    pub provenance_source_url: String,
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
        })
    }

    /// Build from a known-valid slug (tests, fixtures, seeding). Panics on
    /// an invalid slug — production minting must use [`Self::try_from_candidate`]
    /// so invalid candidates are dropped, not aborted.
    pub fn from_candidate(slug: impl Into<String>, provenance_source_url: impl Into<String>) -> Self {
        let slug = slug.into();
        Self::try_from_candidate(&slug, provenance_source_url)
            .unwrap_or_else(|e| panic!("invalid evergreen slug `{slug}`: {e}"))
    }
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
}
