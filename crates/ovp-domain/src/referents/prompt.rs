//! Build the referent-classifier prompt + wire request. The model is shown ONLY
//! the accepted units (id, kind/subtype, text, quote, arguments) and a
//! deterministic seed-surface list — never the source article, title, or
//! metadata — so it cannot pull referents from outside the units.

use ovp_llm::{ModelMessage, ModelRequest};

use crate::units::Unit;

const REFERENT_TEMPLATE: &str = include_str!("../../prompts/referent_classify.md");
/// Cassette namespace for the classifier call.
pub const REFERENT_PROMPT_ID: &str = "referent_classify/v1";
const DEFAULT_MODEL: &str = "claude-sonnet-4-6";
/// The live client raises this via `OVP_LLM_MAX_TOKENS` for thinking headroom.
const DEFAULT_MAX_TOKENS: u32 = 8192;

/// Render one accepted unit as an audit line for the classifier.
fn unit_line(u: &Unit) -> String {
    let subtype = u.subtype.as_deref().unwrap_or("-");
    let args: Vec<String> = u
        .arguments
        .iter()
        .map(|a| format!("{}[{}{}]", a.surface, a.role, if a.locatable { ",loc" } else { "" }))
        .collect();
    format!(
        "{} | {:?}/{} | text=\"{}\" | quote=\"{}\" | args: {}",
        u.id,
        u.kind,
        subtype,
        u.text,
        u.evidence.quote,
        if args.is_empty() { "—".into() } else { args.join("; ") }
    )
}

/// Split the asset on the units marker and append the accepted-unit checklist +
/// the deterministic seed surfaces.
pub fn build_referent_prompt(units: &[Unit], seeds: &[String]) -> (String, String) {
    let marker = "## Accepted units";
    let (system, _) = REFERENT_TEMPLATE.split_once(marker).unwrap_or((REFERENT_TEMPLATE, ""));
    let mut user = format!("{marker} (id | kind/subtype | text | quote | args)\n\n");
    for u in units {
        user.push_str(&unit_line(u));
        user.push('\n');
    }
    user.push_str("\n## Deterministic seed surfaces (from unit arguments — classify or discard, and ADD object surfaces found in unit text/quote that these miss)\n\n");
    user.push_str(&seeds.join("\n"));
    user.push('\n');
    (system.trim_end().to_string(), user)
}

/// Build the provider-neutral request under the `referent_classify/v1` namespace.
pub fn referent_model_request(units: &[Unit], seeds: &[String]) -> ModelRequest {
    let (system, user) = build_referent_prompt(units, seeds);
    ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: DEFAULT_MAX_TOKENS,
        temperature: None,
        cache_namespace: Some(REFERENT_PROMPT_ID.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;

    fn units() -> Vec<Unit> {
        let raw = vec![serde_json::json!({
            "kind":"assertion","text":"IdeaBlocks replace prose chunks.",
            "evidence_ref":"p001.s001","evidence_quote":"IdeaBlocks replace prose chunks.",
            "attribution":"author","modality":"asserted","arguments":[{"surface":"IdeaBlocks","role":"subject"}]
        })];
        validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![], "IdeaBlocks replace prose chunks.")).units
    }

    #[test]
    fn prompt_has_rubric_units_and_seeds_but_not_the_article() {
        let u = units();
        let (system, user) = build_referent_prompt(&u, &["IdeaBlocks".into()]);
        assert!(system.contains("NOT canonicalization") || system.to_lowercase().contains("not canonical"));
        assert!(system.contains("concept is NEVER the default") || system.contains("NEVER the default"));
        assert!(user.contains(&u[0].id));
        assert!(user.contains("seed surfaces"));
        // The model sees units, not the raw article body / title beyond the unit text.
        assert!(user.contains("IdeaBlocks replace prose chunks."));
    }

    #[test]
    fn request_uses_referent_namespace() {
        let u = units();
        let req = referent_model_request(&u, &[]);
        assert_eq!(req.cache_namespace.as_deref(), Some("referent_classify/v1"));
    }
}
