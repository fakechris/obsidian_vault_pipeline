//! Shared render-normalization for grounding (M14a units + M14b referents).
//!
//! `render_norm` / `contains_ci` are the SAME deterministic transform the M14a
//! validator uses to accept a quote — link-text extraction, fullwidth-CJK +
//! smart-quote folding, emphasis/markdown-punctuation strip, whitespace drop,
//! ASCII case-fold. M14b referent grounding MUST reuse this exact function (not a
//! re-implementation) so a referent surface is judged "present in a unit" under
//! the identical rules that judged the unit's quote "present in the source". A
//! test asserts byte-identical behavior across both call sites.

use super::source_map::{fold_char, strip_markdown_links};

/// Whitespace- and markdown-insensitive, fullwidth/smart-fold, lowercase form.
pub(crate) fn render_norm(s: &str) -> String {
    let linked = strip_markdown_links(s);
    let mut out = String::with_capacity(linked.len());
    for c in linked.chars() {
        let c = fold_char(c);
        if c.is_whitespace() || matches!(c, '*' | '_' | '`' | '#' | '>' | '~' | '[' | ']' | '(' | ')') {
            continue;
        }
        out.push(c.to_ascii_lowercase());
    }
    out
}

/// True if `needle` is a substring of `haystack` after render-normalization.
pub(crate) fn contains_ci(haystack: &str, needle: &str) -> bool {
    let h = render_norm(haystack);
    let n = render_norm(needle);
    !n.is_empty() && h.contains(&n)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folds_markdown_smart_and_fullwidth() {
        assert!(contains_ci("Use [vitest-evals](https://x) and **Blockify**.", "blockify"));
        assert!(contains_ci("语义记忆：你叫什么", "语义记忆"));
        assert!(!contains_ci("a chunk", "ownership"));
    }
}
