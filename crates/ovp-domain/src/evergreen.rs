use serde::{Deserialize, Serialize};

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
    /// Build from a candidate slug + the proposing document's URL.
    /// The title is a readable rendering of the slug.
    pub fn from_candidate(slug: impl Into<String>, provenance_source_url: impl Into<String>) -> Self {
        let slug = slug.into();
        let title = title_from_slug(&slug);
        Self { slug, title, provenance_source_url: provenance_source_url.into() }
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
}
