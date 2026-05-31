use ovp_core::{EventKind, FilterDecision, Record, StepId, Transform};

use crate::body::DomainBody;

/// Resolves a record's canonical source URL when the raw `source_url`
/// is a social-clip indirection (currently: Twitter/X). The body
/// usually contains a markdown link to the underlying article; that
/// link is hoisted to `source_url` and a `SourceResolution` event is
/// emitted for audit.
///
/// Pure: no I/O. The "should I resolve?" decision and the resolved
/// URL are both derivable from the SourceDoc fields. If the source
/// isn't a Twitter clip, the record forwards unchanged.
///
/// v1.1 covers Twitter only. Future indirections (LinkedIn share,
/// HackerNews submission, Pinboard) would each add their own
/// detector + heuristic; the SourceResolution event shape stays the
/// same.
pub struct SourceResolver {
    step: StepId,
}

impl SourceResolver {
    pub fn new(step: impl Into<String>) -> Self {
        Self { step: StepId::new(step.into()) }
    }
}

impl Transform<DomainBody> for SourceResolver {
    fn step_id(&self) -> &StepId { &self.step }

    fn process(&mut self, record: Record<DomainBody>) -> FilterDecision<DomainBody> {
        let mut source = match record.body {
            DomainBody::Source(s) => *s,
            other => {
                // No-op for non-Source variants. Forward unchanged.
                let next = Record {
                    id: record.id,
                    body: other,
                    meta: record.meta,
                    provenance: record.provenance,
                };
                return FilterDecision::Forward(vec![next]);
            }
        };

        let original_url = source.source_url.clone();
        if !is_twitter_url(&original_url) {
            // Not a known indirection — pass through.
            let next = Record {
                id: record.id,
                body: DomainBody::Source(Box::new(source)),
                meta: record.meta,
                provenance: record.provenance,
            };
            return FilterDecision::Forward(vec![next]);
        }

        let resolved = match find_first_external_link(&source.body_markdown) {
            Some(url) => url,
            None => {
                // Twitter clip with no embedded article link — forward
                // unchanged but mark provenance so the audit trail
                // shows we tried.
                let next = Record {
                    id: record.id,
                    body: DomainBody::Source(Box::new(source)),
                    meta: record.meta,
                    provenance: record.provenance,
                }
                .with_step(self.step.clone(), "twitter source with no underlying link");
                return FilterDecision::Forward(vec![next]);
            }
        };

        // Rewrite. Keep the original in the event payload for audit.
        source.source_url = resolved.clone();
        let event = EventKind::SourceResolution {
            record_id: record.id.clone(),
            step_id: self.step.clone(),
            original_url: original_url.clone(),
            resolved_url: resolved.clone(),
            reason: "source_resolver.twitter_to_article".to_string(),
        };
        let next = Record {
            id: record.id,
            body: DomainBody::Source(Box::new(source)),
            meta: record.meta,
            provenance: record.provenance,
        }
        .with_step(self.step.clone(), format!("resolved {original_url} → {resolved}"));

        FilterDecision::ForwardWithEvents { records: vec![next], events: vec![event] }
    }
}

fn is_twitter_url(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    // We match by host suffix to allow scheme + path variations.
    lower.contains("://x.com/")
        || lower.contains("://twitter.com/")
        || lower.contains("://www.twitter.com/")
        || lower.contains("://t.co/")
}

/// Find the first markdown link `[text](https://...)` whose href is
/// NOT a Twitter/X URL. Returns the href as a `String`. Image embeds
/// (`![alt](url)`) are skipped — those are attachments, not citations.
fn find_first_external_link(body: &str) -> Option<String> {
    let bytes = body.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        // Find the next `[` that isn't preceded by `!` (skip image embeds).
        if bytes[i] == b'[' && (i == 0 || bytes[i - 1] != b'!') {
            // Find matching `]`
            if let Some(close_text) = find_unescaped(bytes, i + 1, b']') {
                // Need `(` immediately after `]`
                if close_text + 1 < bytes.len() && bytes[close_text + 1] == b'(' {
                    if let Some(close_url) = find_unescaped(bytes, close_text + 2, b')') {
                        let url = &body[close_text + 2..close_url];
                        // Trim and validate.
                        let url = url.trim();
                        if (url.starts_with("http://") || url.starts_with("https://"))
                            && !is_twitter_url(url)
                        {
                            return Some(url.to_string());
                        }
                        i = close_url + 1;
                        continue;
                    }
                }
            }
        }
        i += 1;
    }
    None
}

/// Find the next occurrence of `target` in `bytes` starting at `from`,
/// ignoring backslash-escaped instances.
fn find_unescaped(bytes: &[u8], from: usize, target: u8) -> Option<usize> {
    let mut i = from;
    while i < bytes.len() {
        if bytes[i] == b'\\' && i + 1 < bytes.len() {
            i += 2;
            continue;
        }
        if bytes[i] == target {
            return Some(i);
        }
        i += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_doc::SourceDoc;
    use ovp_core::{RecordId, RecordMeta, RunId};

    fn source_record(source_url: &str, body: &str) -> Record<DomainBody> {
        let doc = SourceDoc::article("T", source_url, None, None, vec![], body);
        Record::new(
            RecordId::new("r-1"),
            DomainBody::Source(Box::new(doc)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn rewrites_twitter_to_underlying_article() {
        let mut r = SourceResolver::new("source_resolver");
        let body = "作者：Daniel Miessler 原文：[Most Companies](https://danielmiessler.com/blog/most-companies-arent-ready-for-ai)\n";
        let rec = source_record("https://x.com/dotey/status/2051057428075053120", body);

        match r.process(rec) {
            FilterDecision::ForwardWithEvents { records, events } => {
                assert_eq!(records.len(), 1);
                assert_eq!(events.len(), 1);
                let body = match &records[0].body {
                    DomainBody::Source(s) => s,
                    _ => unreachable!(),
                };
                assert_eq!(
                    body.source_url,
                    "https://danielmiessler.com/blog/most-companies-arent-ready-for-ai"
                );
                match &events[0] {
                    EventKind::SourceResolution { original_url, resolved_url, reason, .. } => {
                        assert_eq!(original_url, "https://x.com/dotey/status/2051057428075053120");
                        assert_eq!(
                            resolved_url,
                            "https://danielmiessler.com/blog/most-companies-arent-ready-for-ai"
                        );
                        assert_eq!(reason, "source_resolver.twitter_to_article");
                    }
                    other => panic!("expected SourceResolution, got {other:?}"),
                }
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn non_twitter_url_passes_through() {
        let mut r = SourceResolver::new("source_resolver");
        let rec = source_record("https://example.com/article", "body");
        match r.process(rec) {
            FilterDecision::Forward(records) => {
                assert_eq!(records.len(), 1);
                let body = match &records[0].body {
                    DomainBody::Source(s) => s,
                    _ => unreachable!(),
                };
                assert_eq!(body.source_url, "https://example.com/article");
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn twitter_with_no_link_in_body_forwards_unchanged() {
        let mut r = SourceResolver::new("source_resolver");
        let rec = source_record("https://x.com/user/status/1", "just text no links");
        match r.process(rec) {
            FilterDecision::Forward(records) => {
                let body = match &records[0].body {
                    DomainBody::Source(s) => s,
                    _ => unreachable!(),
                };
                assert_eq!(body.source_url, "https://x.com/user/status/1");
            }
            other => panic!("expected Forward, got {other:?}"),
        }
    }

    #[test]
    fn skips_image_embeds() {
        let mut r = SourceResolver::new("source_resolver");
        let body = "![Image](https://cdn.example/img.png)\n\n原文：[Article](https://example.com/post)";
        let rec = source_record("https://x.com/u/status/1", body);
        match r.process(rec) {
            FilterDecision::ForwardWithEvents { records, .. } => {
                let body = match &records[0].body {
                    DomainBody::Source(s) => s,
                    _ => unreachable!(),
                };
                assert_eq!(body.source_url, "https://example.com/post");
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn skips_twitter_links_inside_body() {
        let mut r = SourceResolver::new("source_resolver");
        let body = "[mentions](https://twitter.com/foo) but the real article is [here](https://example.com/post)";
        let rec = source_record("https://x.com/u/1", body);
        match r.process(rec) {
            FilterDecision::ForwardWithEvents { records, .. } => {
                let body = match &records[0].body {
                    DomainBody::Source(s) => s,
                    _ => unreachable!(),
                };
                assert_eq!(body.source_url, "https://example.com/post");
            }
            other => panic!("expected ForwardWithEvents, got {other:?}"),
        }
    }

    #[test]
    fn non_source_variant_passes_through() {
        use crate::interpreted::{Dimensions, Explanation, InterpretedDoc};
        let mut r = SourceResolver::new("source_resolver");
        let interp = InterpretedDoc {
            title: "T".into(),
            source_url: "https://x.com/x".into(),
            author: None,
            date: "2026-01-01".into(),
            doc_type: "article".into(),
            area: "ai".into(),
            tags: vec![],
            canonical_concepts: vec![],
            concept_candidates: vec![],
            dimensions: Dimensions {
                one_liner: "x".into(),
                explanation: Explanation { what: "".into(), why: "".into(), how: "".into() },
                details: vec![],
                structure: None,
                actions: vec![],
                linked_concepts: vec![],
            },
            concepts: Vec::new(),
        };
        let rec: Record<DomainBody> = Record::new(
            RecordId::new("r-1"),
            DomainBody::Interpreted(Box::new(interp)),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        match r.process(rec) {
            FilterDecision::Forward(_) => {}
            other => panic!("expected Forward, got {other:?}"),
        }
    }
}
