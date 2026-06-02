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
}
