use ovp_core::{
    ContentHash, Event, EventKind, OpId, Record, RunId, Sink, SinkOutput, StepId, VaultCreateOp,
    WriteOp,
};
use sha2::{Digest, Sha256};

use crate::body::DomainBody;
use crate::paper_doc::PaperDoc;
use crate::vault_layout::VaultLayout;

/// Renders a `PaperDoc` (ten sections) into a `VaultCreate` op at
/// `20-Areas/AI-Research/Papers/<date>_<arxiv-id>_<title>_深度解读.md`
/// (via `VaultLayout`). Frontmatter carries paper-specific fields
/// (`arxiv_id`, `authors`).
///
/// Wrong-variant records produce an explanatory event but no op.
pub struct PaperVaultPlanSink {
    step: StepId,
    run_id: RunId,
    layout: VaultLayout,
}

impl PaperVaultPlanSink {
    pub fn new(step: impl Into<String>, run_id: RunId) -> Self {
        Self { step: StepId::new(step.into()), run_id, layout: VaultLayout::new() }
    }
}

impl Sink<DomainBody> for PaperVaultPlanSink {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn consume(&mut self, record: Record<DomainBody>) -> SinkOutput {
        let paper = match record.body {
            DomainBody::InterpretedPaper(d) => *d,
            other => {
                let extra = Event::new(
                    self.run_id.clone(),
                    ovp_core::EventTs::new(0),
                    EventKind::FilterDropped {
                        record_id: record.id.clone(),
                        step_id: self.step.clone(),
                        reason: ovp_core::DropReason::new(
                            "sink.paper_vault_plan.wrong_variant",
                            format!("expected InterpretedPaper, got {}", other.variant_name()),
                        ),
                    },
                );
                return SinkOutput { plan_ops: vec![], extra_events: vec![extra] };
            }
        };

        let body_md = render_paper(&paper);
        let path = self.layout.paper_note(&paper.date, &paper.arxiv_id, &paper.title);
        let after_hash = ContentHash::new(hex_sha256(body_md.as_bytes()));

        let op = WriteOp::VaultCreate(VaultCreateOp {
            op_id: OpId::new(format!("op-{}", record.id.as_str())),
            path,
            after_hash,
            body: body_md,
            reason: "paper interpretation".into(),
            originating_record: record.id.clone(),
        });
        SinkOutput { plan_ops: vec![op], extra_events: vec![] }
    }
}

fn render_paper(d: &PaperDoc) -> String {
    let mut s = String::new();
    s.push_str("---\n");
    s.push_str(&format!("title: {}\n", yaml_quote(&d.title)));
    s.push_str(&format!("source: {}\n", d.source_url));
    s.push_str(&format!("arxiv_id: {}\n", d.arxiv_id));
    s.push_str(&format!("date: {}\n", d.date));
    if let Some(src_date) = &d.source_date {
        s.push_str(&format!("source_date: {src_date}\n"));
    }
    s.push_str("type: paper\n");
    s.push_str(&yaml_list("authors", &d.authors, true));
    s.push_str(&yaml_list("categories", &d.categories, false));
    s.push_str(&yaml_list("tags", &d.tags, true));
    s.push_str("---\n\n");

    // Numbered ten-section body.
    for (i, (heading, body)) in d.sections.ordered().iter().enumerate() {
        s.push_str(&format!("## {}. {}\n\n", i + 1, heading));
        s.push_str(body.trim());
        s.push_str("\n\n");
    }
    s
}

fn yaml_list(name: &str, items: &[String], quote: bool) -> String {
    if items.is_empty() {
        return format!("{name}: []\n");
    }
    let mut s = format!("{name}:\n");
    for item in items {
        let v = if quote { yaml_quote(item) } else { item.clone() };
        s.push_str(&format!("  - {v}\n"));
    }
    s
}

fn yaml_quote(s: &str) -> String {
    let needs = s.is_empty()
        || s.starts_with([
            '-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%',
            '@', '`',
        ])
        || s.contains(": ")
        || s.contains(" #")
        || s.contains('\n');
    if needs {
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
    use crate::paper_doc::PaperSections;
    use ovp_core::{RecordId, RecordMeta};

    fn paper() -> PaperDoc {
        PaperDoc {
            title: "Deep GraphRAG".into(),
            source_url: "https://arxiv.org/abs/2601.11144".into(),
            arxiv_id: "2601.11144".into(),
            authors: vec!["Yuejie Li".into(), "Ke Yang".into()],
            categories: vec!["cs.IR".into()],
            date: "2026-05-29".into(),
            source_date: Some("2026-01-16".into()),
            tags: vec!["GraphRAG".into(), "RAG".into(), "RL".into()],
            sections: PaperSections {
                metadata: "m".into(),
                core_contribution: "c".into(),
                background: "b".into(),
                method: "meth".into(),
                experiments: "e".into(),
                key_insights: "k".into(),
                reproduction: "r".into(),
                limitations: "l".into(),
                related_work: "rw".into(),
                personal_notes: "p".into(),
            },
        }
    }

    fn record() -> Record<DomainBody> {
        Record::new(
            RecordId::new("r-paper"),
            DomainBody::InterpretedPaper(Box::new(paper())),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        )
    }

    #[test]
    fn produces_paper_vault_create() {
        let mut sink = PaperVaultPlanSink::new("paper_vault_plan", RunId::new("run"));
        let out = sink.consume(record());
        assert_eq!(out.plan_ops.len(), 1);
        let op = match &out.plan_ops[0] {
            WriteOp::VaultCreate(o) => o,
            other => panic!("expected VaultCreate, got {other:?}"),
        };
        assert_eq!(
            op.path.as_str(),
            "20-Areas/AI-Research/Papers/2026-05-29_2601.11144_Deep GraphRAG_深度解读.md"
        );
        assert!(op.body.contains("arxiv_id: 2601.11144"));
        assert!(op.body.contains("type: paper"));
        assert!(op.body.contains("- Yuejie Li"));
        // All ten numbered sections present.
        for (i, heading) in PaperSections::HEADINGS.iter().enumerate() {
            assert!(
                op.body.contains(&format!("## {}. {}", i + 1, heading)),
                "missing section {heading}"
            );
        }
    }

    #[test]
    fn wrong_variant_event_no_op() {
        use crate::source_doc::SourceDoc;
        let mut sink = PaperVaultPlanSink::new("paper_vault_plan", RunId::new("run"));
        let rec = Record::new(
            RecordId::new("r"),
            DomainBody::Source(Box::new(SourceDoc::article("t", "u", None, None, vec![], ""))),
            RecordMeta { run_id: RunId::new("run"), seq: 0 },
        );
        let out = sink.consume(rec);
        assert!(out.plan_ops.is_empty());
        assert_eq!(out.extra_events.len(), 1);
    }
}
