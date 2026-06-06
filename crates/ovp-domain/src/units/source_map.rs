//! M14a.1 — deterministic paragraph segmentation + id injection.
//!
//! Shared by the prompt (which annotates the body the model sees with `[pNNN]`
//! markers) and the validator (which resolves a unit's `evidence_ref` and scopes
//! quote matching to the referenced paragraph). ONE algorithm so the ids line up
//! on both sides — that is the whole point of evidence-transport hardening: the
//! model points at a paragraph instead of free-copying a long, easily-corrupted
//! quote string.

/// A source paragraph with its byte range in the ORIGINAL body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paragraph {
    pub id: String,
    pub text: String,
    pub byte_start: usize,
    pub byte_end: usize,
}

/// Segment `body` into paragraphs — maximal runs of non-blank lines — ranged in
/// the original body, ids `p001`, `p002`, … in document order. Deterministic.
pub fn paragraphs(body: &str) -> Vec<Paragraph> {
    let mut ranges: Vec<(usize, usize)> = Vec::new();
    let mut start: Option<usize> = None;
    let mut end = 0usize;
    let mut pos = 0usize;
    for line in body.split_inclusive('\n') {
        let line_start = pos;
        pos += line.len();
        if line.trim().is_empty() {
            if let Some(s) = start.take() {
                ranges.push((s, end));
            }
        } else {
            if start.is_none() {
                start = Some(line_start);
            }
            end = line_start + line.trim_end_matches(['\n', '\r']).len();
        }
    }
    if let Some(s) = start.take() {
        ranges.push((s, end));
    }
    ranges
        .into_iter()
        .enumerate()
        .map(|(i, (s, e))| Paragraph {
            id: format!("p{:03}", i + 1),
            text: body[s..e].to_string(),
            byte_start: s,
            byte_end: e,
        })
        .collect()
}

/// Look up a paragraph by id.
pub fn find_paragraph<'a>(paras: &'a [Paragraph], id: &str) -> Option<&'a Paragraph> {
    paras.iter().find(|p| p.id == id)
}

/// Insert `[pNNN] ` before each paragraph, preserving all other spacing. This is
/// the body the model is shown; the markers are NOT part of the paragraph text
/// the validator matches against.
pub fn annotate(body: &str) -> String {
    let paras = paragraphs(body);
    let mut out = String::with_capacity(body.len() + paras.len() * 8);
    let mut cursor = 0usize;
    for p in &paras {
        out.push_str(&body[cursor..p.byte_start]);
        out.push('[');
        out.push_str(&p.id);
        out.push_str("] ");
        out.push_str(&body[p.byte_start..p.byte_end]);
        cursor = p.byte_end;
    }
    out.push_str(&body[cursor..]);
    out
}

// ---- M14a.2: rendered source view + finer span ids ----

/// A finest-grain source span the model anchors evidence to. `text` is the
/// RENDERED plain text (the model sees exactly this); `src_*` map back to the
/// original raw-markdown byte range for the review pack.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RenderedSpan {
    pub id: String,       // e.g. "p017.s002"
    pub para_id: String,  // e.g. "p017"
    pub text: String,     // rendered plain text
    pub src_start: usize,
    pub src_end: usize,
}

/// Render the body into the flat list of finest spans the model is shown and the
/// validator checks against — the SAME text on both sides (the core M14a.2 fix).
/// Paragraphs (blank-line blocks) are sub-split into sentences / list items.
pub fn rendered_view(body: &str) -> Vec<RenderedSpan> {
    let mut spans = Vec::new();
    for (pi, p) in paragraphs(body).iter().enumerate() {
        let para_id = format!("p{:03}", pi + 1);
        let mut si = 0usize;
        for (s, e) in split_units(&p.text) {
            let raw_sub = &p.text[s..e];
            let text = render_plain(raw_sub);
            if text.is_empty() {
                continue;
            }
            si += 1;
            spans.push(RenderedSpan {
                id: format!("{para_id}.s{si:03}"),
                para_id: para_id.clone(),
                text,
                src_start: p.byte_start + s,
                src_end: p.byte_start + e,
            });
        }
    }
    spans
}

/// The model-facing body: one `[id] rendered text` line per span.
pub fn annotate_rendered(spans: &[RenderedSpan]) -> String {
    let mut out = String::new();
    for sp in spans {
        out.push('[');
        out.push_str(&sp.id);
        out.push_str("] ");
        out.push_str(&sp.text);
        out.push('\n');
    }
    out
}

/// Split one paragraph's RAW text into byte ranges at sentence / clause / line
/// boundaries (deterministic). CJK enders `。！？；`, a newline, or an ASCII
/// `.!?` followed by whitespace/end. The ender stays with the preceding unit.
fn split_units(raw: &str) -> Vec<(usize, usize)> {
    let chars: Vec<(usize, char)> = raw.char_indices().collect();
    let mut out = Vec::new();
    let mut start = 0usize;
    for k in 0..chars.len() {
        let (idx, c) = chars[k];
        let end = idx + c.len_utf8();
        let cjk = matches!(c, '。' | '！' | '？' | '；' | '\n');
        let en = matches!(c, '.' | '!' | '?')
            && chars.get(k + 1).is_none_or(|(_, n)| n.is_whitespace());
        if cjk || en {
            out.push((start, end));
            start = end;
        }
    }
    if start < raw.len() {
        out.push((start, raw.len()));
    }
    out
}

/// Render one raw fragment to readable plain text: drop a leading heading/list
/// marker, extract markdown link text, strip emphasis markers, collapse
/// whitespace. Keeps case + punctuation + unicode (the matcher folds those).
pub(crate) fn render_plain(raw: &str) -> String {
    let stripped = strip_leading_marker(raw.trim());
    let linked = strip_markdown_links(stripped);
    let chars: Vec<char> = linked.chars().collect();
    let mut out = String::with_capacity(linked.len());
    let mut prev_ws = false;
    for (i, &c) in chars.iter().enumerate() {
        // Drop Markdown emphasis/code markers — EXCEPT an underscore BETWEEN two
        // alphanumerics, which belongs to a code identifier (`message_agent`,
        // `tool_call`), not emphasis. (M20: previously stripped → `messageagent`,
        // the M17-documented underscore bug.)
        if matches!(c, '*' | '`' | '~') {
            continue;
        }
        if c == '_' {
            let prev_alnum = i > 0 && chars[i - 1].is_alphanumeric();
            let next_alnum = chars.get(i + 1).is_some_and(|n| n.is_alphanumeric());
            if !(prev_alnum && next_alnum) {
                continue; // emphasis underscore (word boundary) → drop
            }
            // intra-identifier underscore → keep (falls through to push)
        }
        if c.is_whitespace() {
            if !prev_ws && !out.is_empty() {
                out.push(' ');
                prev_ws = true;
            }
            continue;
        }
        out.push(c);
        prev_ws = false;
    }
    out.trim().to_string()
}

fn strip_leading_marker(s: &str) -> &str {
    let t = s.trim_start().trim_start_matches('#').trim_start_matches('>').trim_start();
    for m in ["- ", "* ", "+ "] {
        if let Some(r) = t.strip_prefix(m) {
            return r.trim_start();
        }
    }
    // ordered list "12. "
    let digits: String = t.chars().take_while(|c| c.is_ascii_digit()).collect();
    if !digits.is_empty() {
        if let Some(r) = t[digits.len()..].strip_prefix(". ") {
            return r.trim_start();
        }
    }
    t
}

/// Replace `[text](url)` / `![alt](url)` with the visible `text`/`alt` — EXCEPT a
/// **citation link** whose anchor merely names its own source ([`is_citation_link`]),
/// which is dropped entirely so it does not pollute prose. (M20: `LongMemEval
/// [Medium](https://medium.com/…)` was rendering as "LongMemEval Medium".)
pub(crate) fn strip_markdown_links(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < s.len() {
        if !s.is_char_boundary(i) {
            i += 1;
            continue;
        }
        if bytes[i] == b'[' {
            if let Some(close) = s[i + 1..].find(']') {
                let text_end = i + 1 + close;
                let after = text_end + 1;
                if bytes.get(after) == Some(&b'(') {
                    if let Some(paren) = s[after..].find(')') {
                        let anchor = &s[i + 1..text_end];
                        let url = &s[after + 1..after + paren];
                        if !is_citation_link(anchor, url) {
                            out.push_str(anchor); // content link → keep visible text
                        }
                        i = after + paren + 1;
                        continue;
                    }
                }
            }
        }
        let ch = s[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

/// True when a markdown link's anchor merely names its own source/host — a
/// citation like `[Medium](https://medium.com/…)`, `[arXiv](https://arxiv.org/…)`,
/// `[Emergent Mind](https://emergentmind.com/…)` — rather than content
/// (`[Claude Code](https://docs.anthropic.com/…)`). Deterministic: the anchor,
/// normalized to lowercase alphanumerics (a trailing ` +N` reference-count suffix
/// removed), EXACTLY equals one of the URL host's domain labels (minus `www` and
/// common TLDs). Exact match keeps it conservative — a content anchor that merely
/// contains a domain word is preserved.
fn is_citation_link(anchor: &str, url: &str) -> bool {
    let norm = |s: &str| -> String {
        s.chars().filter(|c| c.is_alphanumeric()).flat_map(|c| c.to_lowercase()).collect()
    };
    // Strip a trailing " +N" reference-count suffix (e.g. "GitHub +2").
    let a = match anchor.trim().rsplit_once(" +") {
        Some((head, tail)) if !tail.is_empty() && tail.chars().all(|c| c.is_ascii_digit()) => head,
        _ => anchor.trim(),
    };
    let anchor_norm = norm(a);
    if anchor_norm.is_empty() {
        return false;
    }
    const TLDS: &[&str] = &[
        "www", "com", "org", "net", "io", "ai", "co", "gov", "edu", "pub", "dev", "app", "info",
    ];
    let host = url
        .trim()
        .trim_start_matches("https://")
        .trim_start_matches("http://");
    let host = host.split(['/', '?', '#']).next().unwrap_or("");
    host.split('.').any(|label| {
        let l = norm(label);
        !l.is_empty() && !TLDS.contains(&l.as_str()) && l == anchor_norm
    })
}

/// Fold a char to ASCII: fullwidth ASCII (CJK) → halfwidth, plus smart
/// quotes/dashes and CJK punctuation → ASCII. Used by the matcher so a model
/// copying ASCII still matches a source with fullwidth/smart punctuation.
pub(crate) fn fold_char(c: char) -> char {
    match c {
        '\u{FF01}'..='\u{FF5E}' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c),
        '\u{3000}' => ' ',
        '\u{2018}' | '\u{2019}' => '\'',
        '\u{201C}' | '\u{201D}' | '\u{300C}' | '\u{300D}' => '"',
        '\u{2013}' | '\u{2014}' => '-',
        '\u{3001}' => ',',
        '\u{3002}' => '.',
        _ => c,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_on_blank_lines_with_sequential_ids() {
        let body = "First para line one.\nstill first.\n\nSecond para.\n\n\nThird.";
        let paras = paragraphs(body);
        assert_eq!(paras.len(), 3);
        assert_eq!(paras[0].id, "p001");
        assert_eq!(paras[1].id, "p002");
        assert_eq!(paras[2].id, "p003");
        assert_eq!(paras[0].text, "First para line one.\nstill first.");
        assert_eq!(paras[1].text, "Second para.");
        assert_eq!(paras[2].text, "Third.");
        // byte ranges point back into the original body.
        assert_eq!(&body[paras[1].byte_start..paras[1].byte_end], "Second para.");
    }

    #[test]
    fn annotate_inserts_markers_and_preserves_text() {
        let body = "Alpha.\n\nBeta.";
        let a = annotate(body);
        assert!(a.contains("[p001] Alpha."));
        assert!(a.contains("[p002] Beta."));
        // The clean paragraph text (no marker) is what validation matches.
        let paras = paragraphs(body);
        assert!(!paras[0].text.contains("[p001]"));
    }

    #[test]
    fn single_block_is_one_paragraph() {
        let paras = paragraphs("no blank lines here at all");
        assert_eq!(paras.len(), 1);
        assert_eq!(paras[0].id, "p001");
    }

    #[test]
    fn find_paragraph_by_id() {
        let paras = paragraphs("a\n\nb\n\nc");
        assert_eq!(find_paragraph(&paras, "p002").unwrap().text, "b");
        assert!(find_paragraph(&paras, "p099").is_none());
    }

    #[test]
    fn cjk_byte_ranges_are_valid() {
        // UTF-8 multi-byte paragraphs must produce valid byte boundaries.
        let body = "首先对大模型的两次调用之间没有记忆。\n\n这是第二段。";
        let paras = paragraphs(body);
        assert_eq!(paras.len(), 2);
        assert_eq!(&body[paras[0].byte_start..paras[0].byte_end], paras[0].text);
        assert_eq!(&body[paras[1].byte_start..paras[1].byte_end], "这是第二段。");
    }

    #[test]
    fn rendered_view_splits_english_sentences() {
        let body = "# H\n\nA chunk is neutral. It knows nothing. The fix is upstream.";
        let v = rendered_view(body);
        // heading p001.s001 + three sentence spans under p002.
        let p2: Vec<_> = v.iter().filter(|s| s.para_id == "p002").collect();
        assert_eq!(p2.len(), 3);
        assert_eq!(p2[0].text, "A chunk is neutral.");
        assert_eq!(p2[1].text, "It knows nothing.");
        assert_eq!(p2[0].id, "p002.s001");
    }

    #[test]
    fn rendered_view_splits_cjk_semicolon_lists() {
        let body = "情景记忆：昨天发生了啥；语义记忆：你叫什么；程序性记忆：怎么完成";
        let v = rendered_view(body);
        assert_eq!(v.len(), 3, "one span per ；-separated item");
        assert!(v[0].text.starts_with("情景记忆"));
        assert!(v[1].text.starts_with("语义记忆"));
    }

    #[test]
    fn rendered_view_renders_markdown_and_maps_to_source() {
        let body = "Use [vitest-evals](https://x/y) and **bold** here.";
        let v = rendered_view(body);
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].text, "Use vitest-evals and bold here.");
        // src range still points into the ORIGINAL raw markdown.
        assert!(body[v[0].src_start..v[0].src_end].contains("vitest-evals]("));
    }

    #[test]
    fn render_plain_strips_list_marker_and_link() {
        assert_eq!(render_plain("- **First** point [see](u)"), "First point see");
        assert_eq!(render_plain("1. Ordered item"), "Ordered item");
    }

    #[test]
    fn render_plain_drops_citation_links_keeps_content_links() {
        // M20 (m18-02): citation links whose anchor names the source are dropped.
        assert_eq!(
            render_plain("91.4% on LongMemEval [Medium](https://yogeshyadav.medium.com/x) with Gemini-3 Pro"),
            "91.4% on LongMemEval with Gemini-3 Pro"
        );
        assert_eq!(render_plain("see [arXiv](https://arxiv.org/abs/1)"), "see");
        assert_eq!(render_plain("[Emergent Mind](https://www.emergentmind.com/t) Cognee rules"), "Cognee rules");
        // content links keep their visible text.
        assert_eq!(
            render_plain("Use [Claude Code](https://docs.anthropic.com/en/docs/claude-code) now"),
            "Use Claude Code now"
        );
        assert_eq!(render_plain("Use [vitest-evals](https://x/y) here"), "Use vitest-evals here");
    }

    #[test]
    fn is_citation_link_classification() {
        assert!(is_citation_link("Medium", "https://medium.com/x"));
        assert!(is_citation_link("Medium", "https://yogeshyadav.medium.com/x"));
        assert!(is_citation_link("arXiv", "https://arxiv.org/abs/1"));
        assert!(is_citation_link("Emergent Mind", "https://www.emergentmind.com/t"));
        assert!(is_citation_link("GitHub +2", "https://github.com/x")); // trailing +N
        assert!(!is_citation_link("Claude Code", "https://docs.anthropic.com/x"));
        assert!(!is_citation_link("vitest-evals", "https://x/y"));
        assert!(!is_citation_link("the docs", "https://github.com/x")); // anchor ≠ a label
    }

    #[test]
    fn render_plain_preserves_code_identifier_underscores() {
        // M20 (M17 bug): intra-word underscores belong to identifiers, not emphasis.
        assert_eq!(render_plain("Call message_agent and list_teammates."), "Call message_agent and list_teammates.");
        assert_eq!(render_plain("Use `tool_call` here"), "Use tool_call here");
        assert_eq!(render_plain("the shared_content dir"), "the shared_content dir");
        // emphasis underscores (word boundary) are still stripped.
        assert_eq!(render_plain("this is _emphasis_ text"), "this is emphasis text");
        assert_eq!(render_plain("**bold** and _ital_ done"), "bold and ital done");
    }

    #[test]
    fn fold_char_folds_fullwidth_and_smart() {
        assert_eq!(fold_char('：'), ':');
        assert_eq!(fold_char('，'), ',');
        assert_eq!(fold_char('\u{2019}'), '\'');
        assert_eq!(fold_char('a'), 'a');
    }
}
