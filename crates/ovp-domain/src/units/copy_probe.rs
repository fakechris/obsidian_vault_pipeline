//! M14a.4 Step 1 — copy-only probe.
//!
//! Tests whether the model can VERBATIM-copy a contiguous substring from a given
//! rendered span — no extraction, no writing. Decides model-incapacity vs
//! task-coupling: if the model can't even copy here, strict quote-copy can't be
//! the sole accepted gate; if it can copy but unit extraction still fails, the
//! coupling (extract + copy at once) is the problem. Diagnostic only — not part
//! of the unit pipeline.

use ovp_llm::{CallError, ModelClient, ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};

use crate::source_doc::SourceDoc;

use super::source_map::{rendered_view, RenderedSpan};
use super::validator::deterministic_contains;

const COPY_PROBE_TEMPLATE: &str = include_str!("../../prompts/copy_probe.md");
pub const COPY_PROBE_ID: &str = "copy_probe/v1";
const DEFAULT_MODEL: &str = "claude-sonnet-4-6";

#[derive(Debug, Clone, Serialize)]
pub struct CopyOutcome {
    pub span_id: String,
    pub quote: String,
    pub verbatim: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct CopyProbeReport {
    pub requested: usize,
    pub returned: usize,
    pub verbatim_ok: usize,
    pub outcomes: Vec<CopyOutcome>,
}

impl CopyProbeReport {
    /// Verbatim copies / spans the model returned (0.0 if none returned).
    pub fn copy_rate(&self) -> f64 {
        if self.returned == 0 {
            0.0
        } else {
            self.verbatim_ok as f64 / self.returned as f64
        }
    }
}

#[derive(Deserialize)]
struct CopyReply {
    #[serde(default)]
    copies: Vec<CopyItem>,
}
#[derive(Deserialize)]
struct CopyItem {
    span_id: String,
    #[serde(default)]
    quote: String,
}

/// Sample up to `max_n` spans spread evenly across the document (deterministic).
pub fn sample_spans(spans: &[RenderedSpan], max_n: usize) -> Vec<RenderedSpan> {
    if spans.len() <= max_n || max_n == 0 {
        return spans.to_vec();
    }
    let step = spans.len() as f64 / max_n as f64;
    (0..max_n).map(|i| spans[(i as f64 * step) as usize].clone()).collect()
}

fn build_prompt(sampled: &[RenderedSpan]) -> (String, String) {
    let marker = "## Spans";
    let (system, _) = COPY_PROBE_TEMPLATE.split_once(marker).unwrap_or((COPY_PROBE_TEMPLATE, ""));
    let spans_block: String =
        sampled.iter().map(|s| format!("[{}] {}\n", s.id, s.text)).collect();
    (system.trim_end().to_string(), format!("{marker}\n\n{spans_block}"))
}

fn check(reply_text: &str, sampled: &[RenderedSpan]) -> CopyProbeReport {
    let raw = strip_fence(reply_text);
    let reply: CopyReply = serde_json::from_str(raw).unwrap_or(CopyReply { copies: vec![] });
    let mut outcomes = Vec::new();
    let mut verbatim_ok = 0;
    for item in &reply.copies {
        let span = sampled.iter().find(|s| s.id == item.span_id);
        let verbatim = span.is_some_and(|s| deterministic_contains(&s.text, &item.quote));
        if verbatim {
            verbatim_ok += 1;
        }
        outcomes.push(CopyOutcome {
            span_id: item.span_id.clone(),
            quote: item.quote.clone(),
            verbatim,
        });
    }
    CopyProbeReport { requested: sampled.len(), returned: reply.copies.len(), verbatim_ok, outcomes }
}

/// Run the copy-only probe live/replay on `source`. Returns the report + the raw
/// model reply (for the operator to inspect).
pub fn run_copy_probe(
    source: &SourceDoc,
    client: &mut dyn ModelClient,
    max_spans: usize,
) -> Result<(CopyProbeReport, String), CallError> {
    let sampled = sample_spans(&rendered_view(&source.body_markdown), max_spans);
    let (system, user) = build_prompt(&sampled);
    let request = ModelRequest {
        model: DEFAULT_MODEL.to_string(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user }],
        max_tokens: 8192,
        temperature: None,
        cache_namespace: Some(COPY_PROBE_ID.to_string()),
    };
    let reply = client.call(&request)?;
    Ok((check(&reply.text, &sampled), reply.text))
}

fn strip_fence(t: &str) -> &str {
    let t = t.trim();
    if let Some(r) = t.strip_prefix("```json") {
        return r.trim_start_matches('\n').trim_end_matches("```").trim();
    }
    if let Some(r) = t.strip_prefix("```") {
        return r.trim_start_matches('\n').trim_end_matches("```").trim();
    }
    t
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spans() -> Vec<RenderedSpan> {
        rendered_view("# H\n\nAlpha sentence one. Beta sentence two.\n\n第三段：情景记忆；语义记忆。")
    }

    #[test]
    fn sample_is_deterministic_and_bounded() {
        let s = spans();
        let a = sample_spans(&s, 2);
        let b = sample_spans(&s, 2);
        assert_eq!(a.len(), 2);
        assert_eq!(a, b);
    }

    #[test]
    fn check_marks_verbatim_vs_edited() {
        let s = spans();
        // one exact copy, one edited (not a substring).
        let first = &s[1];
        let reply = format!(
            r#"{{"copies":[{{"span_id":"{}","quote":"{}"}},{{"span_id":"{}","quote":"totally invented text"}}]}}"#,
            first.id, "Alpha sentence one.", first.id
        );
        let r = check(&reply, &s);
        assert_eq!(r.returned, 2);
        assert_eq!(r.verbatim_ok, 1);
    }

    #[test]
    fn cjk_punctuation_copy_is_verbatim() {
        let s = spans();
        let cjk = s.iter().find(|x| x.text.contains("情景记忆")).unwrap();
        // copy the first ；-clause verbatim, keeping CJK punctuation.
        let frag = cjk.text.split('；').next().unwrap();
        let reply = format!(r#"{{"copies":[{{"span_id":"{}","quote":"{}"}}]}}"#, cjk.id, frag);
        let r = check(&reply, &s);
        assert_eq!(r.verbatim_ok, 1);
    }
}
