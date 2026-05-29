use ovp_core::{
    ContentHash, Event, EventKind, OpId, Record, RunId, Sink, SinkOutput, StepId, VaultCreateOp,
    WriteOp,
};
use sha2::{Digest, Sha256};

use crate::body::DomainBody;
use crate::interpreted::InterpretedDoc;
use crate::vault_layout::VaultLayout;

/// Renders an `InterpretedDoc` into a `VaultCreate` write op. The path
/// follows the legacy convention `20-Areas/<area>/Topics/<YYYY-MM>/
/// <YYYY-MM-DD>_<title>_深度解读.md`. The body is the frontmatter +
/// six-dimension markdown shape that consumers (Obsidian + downstream
/// MOC builders) already understand.
///
/// Wrong-variant records produce a SinkOutput with no plan ops but an
/// extra event for visibility — sinks can't drop records the way
/// transforms can.
pub struct ArticleVaultPlanSink {
    step: StepId,
    run_id: RunId,
    layout: VaultLayout,
}

impl ArticleVaultPlanSink {
    pub fn new(step: impl Into<String>, run_id: RunId) -> Self {
        Self { step: StepId::new(step.into()), run_id, layout: VaultLayout::new() }
    }
}

impl Sink<DomainBody> for ArticleVaultPlanSink {
    fn step_id(&self) -> &StepId { &self.step }

    fn consume(&mut self, record: Record<DomainBody>) -> SinkOutput {
        let interp = match record.body {
            DomainBody::Interpreted(d) => *d,
            other => {
                let extra = Event::new(
                    self.run_id.clone(),
                    ovp_core::EventTs::new(0), // re-stamped by the runner
                    EventKind::FilterDropped {
                        record_id: record.id.clone(),
                        step_id: self.step.clone(),
                        reason: ovp_core::DropReason::new(
                            "sink.article_vault_plan.wrong_variant",
                            format!("expected Interpreted, got {}", other.variant_name()),
                        ),
                    },
                );
                return SinkOutput { plan_ops: vec![], extra_events: vec![extra] };
            }
        };

        let body_md = render_article(&interp);
        let path = self.layout.area_topic_note(&interp.area, &interp.date, &interp.title);
        let after_hash = ContentHash::new(hex_sha256(body_md.as_bytes()));

        let op = WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new(format!("op-{}", record.id.as_str())),
            path,
            after_hash,
            body: body_md,
            reason: "article interpretation".into(),
            originating_record: record.id.clone(),
        });

        SinkOutput { plan_ops: vec![op], extra_events: vec![] }
    }
}

fn render_article(d: &InterpretedDoc) -> String {
    let mut s = String::new();
    s.push_str("---\n");
    s.push_str(&render_frontmatter(d));
    s.push_str("---\n\n");

    s.push_str("## 一句话定义\n\n");
    s.push_str(d.dimensions.one_liner.trim());
    s.push_str("\n\n");

    s.push_str("## 详细解释\n\n");
    s.push_str("### What\n\n");
    s.push_str(d.dimensions.explanation.what.trim());
    s.push_str("\n\n");
    s.push_str("### Why\n\n");
    s.push_str(d.dimensions.explanation.why.trim());
    s.push_str("\n\n");
    s.push_str("### How\n\n");
    s.push_str(d.dimensions.explanation.how.trim());
    s.push_str("\n\n");

    s.push_str("## 重要细节\n\n");
    for detail in &d.dimensions.details {
        s.push_str("- ");
        s.push_str(detail.trim());
        s.push('\n');
    }
    s.push('\n');

    if let Some(structure) = &d.dimensions.structure {
        s.push_str("## 结构\n\n");
        s.push_str(structure.trim());
        s.push_str("\n\n");
    }

    s.push_str("## 行动建议\n\n");
    for action in &d.dimensions.actions {
        s.push_str("- ");
        s.push_str(action.trim());
        s.push('\n');
    }
    s.push('\n');

    s.push_str("## 相关概念\n\n");
    for concept in &d.dimensions.linked_concepts {
        s.push_str(&format!("- [[{}]]\n", concept));
    }
    s
}

fn render_frontmatter(d: &InterpretedDoc) -> String {
    let mut s = String::new();
    s.push_str(&format!("title: {}\n", yaml_quote(&d.title)));
    s.push_str(&format!("source: {}\n", d.source_url));
    if let Some(author) = &d.author {
        s.push_str(&format!("author: {}\n", yaml_quote(author)));
    }
    s.push_str(&format!("date: {}\n", d.date));
    s.push_str(&format!("type: {}\n", d.doc_type));
    s.push_str(&format!("area: {}\n", d.area));
    s.push_str(&yaml_list_field("tags", &d.tags, true));
    s.push_str(&yaml_list_field("canonical_concepts", &d.canonical_concepts, false));
    s.push_str(&yaml_list_field("concept_candidates", &d.concept_candidates, false));
    s
}

/// Render a YAML list field. Empty lists emit `<name>: []\n` (valid
/// empty sequence) rather than `<name>:\n` (parses as null and breaks
/// round-trips through serde). When `quote_values` is true each entry
/// is run through `yaml_quote`.
fn yaml_list_field(name: &str, items: &[String], quote_values: bool) -> String {
    if items.is_empty() {
        return format!("{name}: []\n");
    }
    let mut s = format!("{name}:\n");
    for item in items {
        let v = if quote_values { yaml_quote(item) } else { item.to_string() };
        s.push_str(&format!("  - {v}\n"));
    }
    s
}

/// Minimal YAML string quoting: wraps in double quotes if the string
/// contains characters YAML's default plain scalar disallows.
fn yaml_quote(s: &str) -> String {
    let needs_quote = s.is_empty()
        || s.starts_with(['-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%', '@', '`'])
        || s.contains(": ")
        || s.contains(" #")
        || s.contains('\n');
    if needs_quote {
        format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        s.to_string()
    }
}

fn hex_sha256(bytes: &[u8]) -> String {
    let hash = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    use std::fmt::Write;
    for b in hash.iter() {
        write!(s, "{:02x}", b).expect("infallible");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::interpreted::{Dimensions, Explanation};
    use crate::source_doc::SourceDoc;
    use ovp_core::{RecordId, RecordMeta};

    fn interp() -> InterpretedDoc {
        InterpretedDoc {
            title: "Agent-native PM".into(),
            source_url: "https://example.com/agent-pm".into(),
            author: Some("Marcus Moretti".into()),
            date: "2026-05-27".into(),
            doc_type: "article".into(),
            area: "ai".into(),
            tags: vec!["AI".into(), "PM".into()],
            canonical_concepts: vec![],
            concept_candidates: vec!["agent-native-pm".into(), "compound-engineering".into()],
            dimensions: Dimensions {
                one_liner: "Agent-native PM treats the conversation as the work.".into(),
                explanation: Explanation {
                    what: "what".into(),
                    why: "why".into(),
                    how: "how".into(),
                },
                details: vec!["detail-1".into(), "detail-2".into(), "detail-3".into()],
                structure: None,
                actions: vec!["short-term: try it".into()],
                linked_concepts: vec!["agent-native-pm".into()],
            },
        }
    }

    fn record() -> Record<DomainBody> {
        Record::new(
            RecordId::new("r-agent"),
            DomainBody::Interpreted(Box::new(interp())),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn produces_vault_create_op() {
        let mut sink = ArticleVaultPlanSink::new("article_vault_plan", RunId::new("run"));
        let out = sink.consume(record());
        assert_eq!(out.plan_ops.len(), 1);
        assert!(out.extra_events.is_empty());

        let op = match &out.plan_ops[0] {
            WriteOp::VaultCreate(o) => o,
            other => panic!("expected VaultCreate, got {other:?}"),
        };
        assert_eq!(
            op.path.as_str(),
            "20-Areas/AI-Research/Topics/2026-05/2026-05-27_Agent-native PM_深度解读.md"
        );
        assert!(op.body.starts_with("---\n"));
        assert!(op.body.contains("title: Agent-native PM"));
        assert!(op.body.contains("source: https://example.com/agent-pm"));
        assert!(op.body.contains("area: ai"));
        assert!(op.body.contains("## 一句话定义"));
        assert!(op.body.contains("Agent-native PM treats the conversation as the work."));
        assert!(op.body.contains("## 详细解释"));
        assert!(op.body.contains("- [[agent-native-pm]]"));
        assert_eq!(op.originating_record.as_str(), "r-agent");
    }

    #[test]
    fn wrong_variant_produces_event_no_op() {
        let mut sink = ArticleVaultPlanSink::new("article_vault_plan", RunId::new("run"));
        let rec = Record::new(
            RecordId::new("r-x"),
            DomainBody::Source(Box::new(SourceDoc {
                title: "T".into(),
                source_url: "u".into(),
                author: None,
                published: None,
                tags: vec![],
                body_markdown: "".into(),
            })),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        let out = sink.consume(rec);
        assert!(out.plan_ops.is_empty());
        assert_eq!(out.extra_events.len(), 1);
        match &out.extra_events[0].kind {
            EventKind::FilterDropped { reason, .. } => {
                assert_eq!(reason.code.as_str(), "sink.article_vault_plan.wrong_variant");
            }
            other => panic!("expected FilterDropped, got {other:?}"),
        }
    }

    #[test]
    fn body_hash_is_64_hex() {
        let mut sink = ArticleVaultPlanSink::new("article_vault_plan", RunId::new("run"));
        let out = sink.consume(record());
        let op = match &out.plan_ops[0] {
            WriteOp::VaultCreate(o) => o,
            _ => unreachable!(),
        };
        assert_eq!(op.after_hash.as_str().len(), 64);
    }
}
