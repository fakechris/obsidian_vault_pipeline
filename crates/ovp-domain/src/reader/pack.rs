//! Render the human-usable **reader pack** for one processed source: a collapsible
//! self-contained HTML workbench + a flat Markdown view + machine artifacts. The
//! provenance chain (card → cited Unit → verbatim quote + source line) is collapsed
//! VISUALLY (HTML `<details>`) but is never absent from the artifacts. Deterministic
//! (no timestamps); a re-render is byte-identical.
//!
//! Decoupled from `SourceExtraction` so it renders equally from a live run or from
//! committed `units.accepted.json` + `cards.json` artifacts (the `--render-only` path).

use std::fs;
use std::io;
use std::path::Path;

use serde::Serialize;
use serde_json::json;

use crate::units::{RepairLog, Unit};

use super::{Card, CardReport};

/// Grounding/audit numbers shown in the reader pack's run-status, sourced from the
/// validator's report (full run) or assumed-clean for already-accepted artifacts.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct GroundingStatus {
    pub accepted_without_quote: usize,
    pub needs_review: usize,
    pub quote_not_found: usize,
    pub parse_error: Option<String>,
}

/// Small summary the CLI prints after writing.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ReaderPack {
    pub case_id: String,
    pub n_cards: usize,
    pub n_accepted_units: usize,
    pub accepted_without_quote: usize,
    pub cards_dropped_uncited: usize,
    pub repair_trims: usize,
    pub repair_adds: usize,
    pub needs_review: usize,
    pub quote_not_found: usize,
    pub parse_error: Option<String>,
}

/// Write the reader pack files into `out_dir`.
pub fn write_reader_pack(
    out_dir: &Path,
    source_title: &str,
    accepted: &[Unit],
    cards: &[Card],
    card_report: &CardReport,
    repair_log: Option<&RepairLog>,
    grounding: &GroundingStatus,
) -> io::Result<ReaderPack> {
    fs::create_dir_all(out_dir)?;
    let (trims, adds) = repair_log.map(|l| (l.trims, l.adds_proposed)).unwrap_or((0, 0));

    let summary = ReaderPack {
        case_id: source_title.to_string(),
        n_cards: cards.len(),
        n_accepted_units: accepted.len(),
        accepted_without_quote: grounding.accepted_without_quote,
        cards_dropped_uncited: card_report.cards_dropped_uncited,
        repair_trims: trims,
        repair_adds: adds,
        needs_review: grounding.needs_review,
        quote_not_found: grounding.quote_not_found,
        parse_error: grounding.parse_error.clone().or_else(|| card_report.parse_error.clone()),
    };

    fs::write(out_dir.join("reader.html"), render_html(source_title, accepted, cards, &summary))?;
    fs::write(out_dir.join("reader.md"), render_md(source_title, accepted, cards, &summary))?;
    fs::write(out_dir.join("source-support.md"), render_support(accepted, cards))?;
    write_json(out_dir.join("cards.json"), &cards)?;
    write_json(out_dir.join("run-status.json"), &json!({
        "source": source_title,
        "accepted_units": accepted.len(),
        "cards": cards.len(),
        "cards_dropped_uncited": card_report.cards_dropped_uncited,
        "repair_trims": trims,
        "repair_adds": adds,
        "accepted_without_quote": grounding.accepted_without_quote,
        "needs_review": grounding.needs_review,
        "quote_not_found": grounding.quote_not_found,
        "parse_error": summary.parse_error,
        "card_prompt": super::CARD_PROMPT_ID,
    }))?;
    Ok(summary)
}

fn write_json<T: Serialize>(path: std::path::PathBuf, v: &T) -> io::Result<()> {
    let s = serde_json::to_string_pretty(v).map_err(io::Error::other)?;
    fs::write(path, format!("{s}\n"))
}

fn unit_by_id<'a>(units: &'a [Unit], id: &str) -> Option<&'a Unit> {
    units.iter().find(|u| u.id == id)
}

/// Best-effort, render-only sentence→unit alignment: the cited unit whose quote
/// shares the most content words with the sentence. Labelled "derived" — a reading
/// aid, not a model claim (the model's real linkage is card→unit).
fn align_sentence<'a>(sentence: &str, cited: &[&'a Unit]) -> Option<&'a Unit> {
    let words = |s: &str| -> Vec<String> {
        s.to_lowercase().split(|c: char| !c.is_alphanumeric())
            .filter(|w| w.len() >= 4).map(str::to_string).collect()
    };
    let sw = words(sentence);
    let mut best: Option<(&Unit, usize)> = None;
    for u in cited {
        let qw = words(&u.evidence.quote);
        let overlap = sw.iter().filter(|w| qw.contains(w)).count();
        if overlap > 0 && best.map(|(_, b)| overlap > b).unwrap_or(true) {
            best = Some((u, overlap));
        }
    }
    best.map(|(u, _)| u)
}

fn split_sentences(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let cs: Vec<(usize, char)> = s.char_indices().collect();
    for (k, (i, ch)) in cs.iter().enumerate() {
        if matches!(ch, '.' | '!' | '?' | '。' | '！' | '？') {
            let after = i + ch.len_utf8();
            let next_ws = cs.get(k + 1).map(|(_, n)| n.is_whitespace()).unwrap_or(true);
            if next_ws {
                let seg = s[start..after].trim();
                if !seg.is_empty() {
                    out.push(seg.to_string());
                }
                start = after;
            }
        }
    }
    if start < s.len() {
        let seg = s[start..].trim();
        if !seg.is_empty() {
            out.push(seg.to_string());
        }
    }
    out
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

fn line_of(u: &Unit) -> String {
    u.evidence.location.as_ref().map(|l| format!("line {}", l.line)).unwrap_or_else(|| "—".into())
}

const CSS: &str = "body{font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}\
h1{font-size:1.6rem}h2{font-size:1.1rem;margin-top:1.6rem;border-bottom:1px solid #eee;padding-bottom:.2rem}\
.meta{color:#666;font-size:.85rem;margin-bottom:1.5rem}\
.card{border:1px solid #e3e3e3;border-radius:10px;padding:.9rem 1.1rem;margin:.9rem 0;background:#fff}\
.card h3{margin:.1rem 0 .3rem;font-size:1.06rem}\
.takeaway{font-weight:600;color:#111;margin:.2rem 0}\
.body{color:#333;margin:.3rem 0}\
.kind{display:inline-block;font-size:.7rem;color:#777;border:1px solid #ddd;border-radius:6px;padding:0 .4rem;margin-left:.4rem;vertical-align:middle}\
details{margin-top:.5rem;font-size:.88rem}summary{cursor:pointer;color:#556;user-select:none}\
.ev{color:#444;margin:.3rem 0 .3rem 1rem}.q{color:#0a5;font-style:italic}.uid{color:#999;font-family:monospace;font-size:.8rem}\
.ok{color:#0a7}.warn{color:#c60}.bad{color:#c00;font-weight:700}";

fn render_html(title: &str, accepted: &[Unit], cards: &[Card], s: &ReaderPack) -> String {
    let mut h = String::new();
    h.push_str(&format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Reader — {}</title><style>{}</style></head><body>\n",
        esc(title), CSS));
    h.push_str(&format!("<h1>{}</h1>\n", esc(title)));
    let inv = if s.accepted_without_quote == 0 {
        "<span class=ok>grounding ✓ (accepted_without_quote=0)</span>"
    } else {
        "<span class=bad>GROUNDING VIOLATED</span>"
    };
    h.push_str(&format!(
        "<div class=meta>{} cards · {} grounded units · critic trims {} / adds {} · {} · evidence collapsed by default — click to expand</div>\n",
        s.n_cards, s.n_accepted_units, s.repair_trims, s.repair_adds, inv));

    for c in cards {
        let (takeaway, body) = c.takeaway_and_body();
        let kind = c.unit_type.as_deref().map(|k| format!("<span class=kind>{}</span>", esc(k))).unwrap_or_default();
        h.push_str("<div class=card>\n");
        h.push_str(&format!("<h3>{}{}</h3>\n", esc(&c.title), kind));
        if !takeaway.is_empty() {
            h.push_str(&format!("<p class=takeaway>{}</p>\n", esc(&takeaway)));
        }
        if !body.is_empty() {
            h.push_str(&format!("<p class=body>{}</p>\n", esc(&body)));
        }
        let cited: Vec<&Unit> = c.cited_unit_ids.iter().filter_map(|id| unit_by_id(accepted, id)).collect();
        h.push_str(&format!("<details><summary>Evidence — {} source quote(s)</summary>\n", cited.len()));
        for u in &cited {
            h.push_str(&format!(
                "<div class=ev><span class=q>“{}”</span> <span class=uid>[{} · {}]</span></div>\n",
                esc(&u.evidence.quote), esc(&u.id), line_of(u)));
        }
        h.push_str("</details>\n</div>\n");
    }

    h.push_str("<h2>Source support (card → claim → unit → quote)</h2>\n");
    h.push_str("<details><summary>show per-sentence support map (derived alignment)</summary>\n");
    for c in cards {
        let cited: Vec<&Unit> = c.cited_unit_ids.iter().filter_map(|id| unit_by_id(accepted, id)).collect();
        h.push_str(&format!("<p class=takeaway>{}</p>\n", esc(&c.title)));
        for sent in split_sentences(&c.content) {
            let u = align_sentence(&sent, &cited);
            let tag = u.map(|u| format!("{} · {}", u.id, line_of(u))).unwrap_or_else(|| "(card-level)".into());
            h.push_str(&format!("<div class=ev>{} <span class=uid>⇐ {}</span></div>\n", esc(&sent), esc(&tag)));
        }
    }
    h.push_str("</details>\n");

    h.push_str("<h2>Run status</h2>\n<details><summary>extraction + repair audit</summary><div class=ev>");
    if let Some(e) = &s.parse_error {
        h.push_str(&format!("<div class=bad>PARSE ERROR: {}</div>", esc(e)));
    }
    h.push_str(&format!("parse: ok · accepted units: {} · cards: {} (dropped uncited: {})<br>",
        s.n_accepted_units, s.n_cards, s.cards_dropped_uncited));
    h.push_str(&format!("critic repair: trims {} · adds {}<br>", s.repair_trims, s.repair_adds));
    h.push_str(&format!("grounding: accepted_without_quote {} · needs_review {} · quote_not_found {}",
        s.accepted_without_quote, s.needs_review, s.quote_not_found));
    h.push_str("</div></details>\n");
    h.push_str("</body></html>\n");
    h
}

fn render_md(title: &str, accepted: &[Unit], cards: &[Card], s: &ReaderPack) -> String {
    let mut m = format!("# {title}\n\n");
    m.push_str(&format!("> {} cards · {} grounded units · critic trims {} / adds {} · accepted_without_quote={} {}\n\n",
        s.n_cards, s.n_accepted_units, s.repair_trims, s.repair_adds, s.accepted_without_quote,
        if s.accepted_without_quote == 0 { "(grounding ✓)" } else { "(**VIOLATED**)" }));
    for (i, c) in cards.iter().enumerate() {
        let (takeaway, body) = c.takeaway_and_body();
        m.push_str(&format!("## {}. {}{}\n\n", i + 1, c.title,
            c.unit_type.as_deref().map(|k| format!("  _{k}_")).unwrap_or_default()));
        if !takeaway.is_empty() { m.push_str(&format!("**{takeaway}**\n\n")); }
        if !body.is_empty() { m.push_str(&format!("{body}\n\n")); }
        let cited: Vec<&Unit> = c.cited_unit_ids.iter().filter_map(|id| unit_by_id(accepted, id)).collect();
        m.push_str(&format!("<details><summary>Evidence — {} quote(s)</summary>\n\n", cited.len()));
        for u in &cited {
            m.push_str(&format!("- “{}” `[{} · {}]`\n", u.evidence.quote, u.id, line_of(u)));
        }
        m.push_str("\n</details>\n\n");
    }
    m.push_str("---\nRun: parse ok · ");
    m.push_str(&format!("cards dropped uncited {} · needs_review {} · quote_not_found {} · card_prompt {}\n",
        s.cards_dropped_uncited, s.needs_review, s.quote_not_found, super::CARD_PROMPT_ID));
    m
}

fn render_support(accepted: &[Unit], cards: &[Card]) -> String {
    let mut m = String::from("# Source support — card → unit → quote/span\n\n");
    for c in cards {
        m.push_str(&format!("## {}\n\n", c.title));
        for id in &c.cited_unit_ids {
            if let Some(u) = unit_by_id(accepted, id) {
                m.push_str(&format!("- `{}` ({}): “{}”\n", u.id, line_of(u), u.evidence.quote));
            }
        }
        m.push('\n');
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use crate::units::validate;

    fn accepted_units() -> Vec<Unit> {
        let body = "IdeaBlocks replace prose chunks. It knows nothing about ownership.";
        let raw = vec![serde_json::json!({"kind":"assertion","text":"IdeaBlocks replace prose chunks.",
            "evidence_ref":"p001.s001","evidence_quote":"IdeaBlocks replace prose chunks.",
            "attribution":"author","modality":"asserted","arguments":[]})];
        validate(&raw, &SourceDoc::article("RAG", "https://e/x", None, None, vec![], body))
            .accepted().cloned().collect()
    }

    fn cards(acc: &[Unit]) -> Vec<Card> {
        vec![Card { title: "IdeaBlocks can replace prose chunks".into(),
            content: "IdeaBlocks replace prose chunks. They improve retrieval.".into(),
            unit_type: Some("definition".into()), cited_unit_ids: vec![acc[0].id.clone()] }]
    }

    #[test]
    fn writes_pack_with_collapsible_evidence_and_provenance() {
        let acc = accepted_units();
        let cs = cards(&acc);
        let rep = CardReport { cards_returned: 1, cards_kept: 1, cards_dropped_uncited: 0, parse_error: None };
        let dir = tempfile::tempdir().unwrap();
        let s = write_reader_pack(dir.path(), "RAG done right", &acc, &cs, &rep, None, &GroundingStatus::default()).unwrap();
        assert_eq!(s.n_cards, 1);
        assert_eq!(s.accepted_without_quote, 0);
        for f in ["reader.html", "reader.md", "source-support.md", "cards.json", "run-status.json"] {
            assert!(dir.path().join(f).exists(), "missing {f}");
        }
        let html = std::fs::read_to_string(dir.path().join("reader.html")).unwrap();
        assert!(html.contains("<details>"), "evidence must be collapsible");
        assert!(html.contains("IdeaBlocks replace prose chunks."), "quote present in artifact");
        assert!(html.contains("grounding ✓"));
        let sup = std::fs::read_to_string(dir.path().join("source-support.md")).unwrap();
        assert!(sup.contains(&acc[0].id));
    }

    #[test]
    fn pack_is_deterministic() {
        let acc = accepted_units();
        let cs = cards(&acc);
        let rep = CardReport::default();
        let d1 = tempfile::tempdir().unwrap();
        let d2 = tempfile::tempdir().unwrap();
        write_reader_pack(d1.path(), "T", &acc, &cs, &rep, None, &GroundingStatus::default()).unwrap();
        write_reader_pack(d2.path(), "T", &acc, &cs, &rep, None, &GroundingStatus::default()).unwrap();
        for f in ["reader.html", "reader.md", "cards.json"] {
            assert_eq!(std::fs::read_to_string(d1.path().join(f)).unwrap(),
                       std::fs::read_to_string(d2.path().join(f)).unwrap(), "{f} not deterministic");
        }
    }
}
