//! End-to-end for the L6 RAG read path: seed a real canonical store + vault +
//! knowledge index (offline, tempdirs only), load through `KnowledgeView`, build
//! the corpus, and run the full retrieve → rank → context + eval pipeline.
//! Asserts read-only: a corpus build + retrieve writes nothing to either root.

use std::collections::BTreeMap;
use std::path::Path;

use ovp_core::{
    ApplyMode, CanonicalKey, CanonicalUpsertOp, ContentHash, OpId, PlanApplier, RecordId, RunId,
    WriteOp, WritePlan,
};
use ovp_domain::{CanonicalConcept, KnowledgeIndex};
use ovp_rag::{ContextBuilder, Eval, EvalCase, MatchField, RagCorpus, Ranker, Retriever};
use ovp_stores::CanonicalFsStoreApplier;
use sha2::{Digest, Sha256};

fn sha(b: &[u8]) -> String {
    let h = Sha256::digest(b);
    let mut s = String::new();
    use std::fmt::Write;
    for x in h.iter() {
        write!(s, "{x:02x}").unwrap();
    }
    s
}

fn concept(slug: &str, title: &str) -> CanonicalConcept {
    CanonicalConcept {
        slug: slug.into(),
        title: title.into(),
        evergreen_path: format!("10-Knowledge/Evergreen/{slug}.md"),
        provenance_source_url: "https://example.com/x".into(),
    }
}

fn seed_canonical(root: &Path, concepts: &[CanonicalConcept]) {
    let mut store = CanonicalFsStoreApplier::new(root);
    let mut plan = WritePlan::new(RunId::new("seed"));
    for c in concepts {
        let payload = c.to_payload();
        plan.push(WriteOp::CanonicalUpsert(CanonicalUpsertOp {
            op_id: OpId::new(format!("op-{}", c.slug)),
            key: CanonicalKey::new(c.slug.clone()),
            before_hash: None,
            after_hash: ContentHash::new(sha(payload.as_bytes())),
            payload,
            reason: "seed".into(),
            originating_record: RecordId::new("r"),
        }));
    }
    store.apply(&plan, ApplyMode::Apply);
}

fn write_note(vault: &Path, concept: &CanonicalConcept, body: &str) {
    let path = vault.join(&concept.evergreen_path);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, body).unwrap();
}

fn write_index(vault: &Path, concepts: &[CanonicalConcept], backlinks: BTreeMap<String, Vec<String>>) {
    let index = KnowledgeIndex::build(concepts, &backlinks);
    let path = vault.join("60-Logs/knowledge-index.json");
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, index.to_json()).unwrap();
}

/// A recursive `path → bytes` snapshot, for proving nothing was written.
fn snapshot(dir: &Path) -> BTreeMap<String, Vec<u8>> {
    let mut out = BTreeMap::new();
    snap_inner(dir, dir, &mut out);
    out
}

fn snap_inner(root: &Path, dir: &Path, out: &mut BTreeMap<String, Vec<u8>>) {
    for entry in std::fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            snap_inner(root, &path, out);
        } else {
            let rel = path.strip_prefix(root).unwrap().to_string_lossy().to_string();
            out.insert(rel, std::fs::read(&path).unwrap());
        }
    }
}

fn seeded() -> (tempfile::TempDir, tempfile::TempDir, Vec<CanonicalConcept>) {
    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let concepts = vec![
        concept("ai-agent", "AI Agent"),
        concept("rag", "Retrieval Augmented Generation"),
        concept("transformer", "Transformer"),
    ];
    seed_canonical(canon.path(), &concepts);
    write_note(vault.path(), &concepts[0], "An AI agent perceives and acts toward a goal.");
    write_note(vault.path(), &concepts[1], "RAG augments generation with retrieval. retrieval retrieval.");
    // transformer has NO note on disk → body should be None.
    let mut bl = BTreeMap::new();
    bl.insert("ai-agent".to_string(), vec!["20-Areas/AI-Research/agents.md".to_string()]);
    write_index(vault.path(), &concepts, bl);
    (vault, canon, concepts)
}

#[test]
fn corpus_carries_concepts_bodies_and_backlinks() {
    let (vault, canon, _) = seeded();
    let corpus = RagCorpus::load(vault.path(), canon.path()).unwrap();

    assert_eq!(corpus.len(), 3);
    let agent = corpus.get("ai-agent").unwrap();
    assert_eq!(agent.title, "AI Agent");
    assert!(agent.body.as_deref().unwrap().contains("perceives"));
    assert_eq!(agent.backlinks, vec!["20-Areas/AI-Research/agents.md".to_string()]);

    // A concept whose note file is absent loads with body None (not an error).
    assert!(corpus.get("transformer").unwrap().body.is_none());
}

#[test]
fn retrieve_rank_context_end_to_end() {
    let (vault, canon, _) = seeded();
    let corpus = RagCorpus::load(vault.path(), canon.path()).unwrap();

    let scored = Retriever::new().score(&corpus, "retrieval augmented generation");
    let ranked = Ranker::new().rank(scored);
    assert_eq!(ranked.first().unwrap().slug, "rag", "rag should rank first");

    let ctx = ContextBuilder::new().build(&corpus, &ranked, "retrieval augmented generation");
    let top = &ctx.selected[0];
    assert_eq!(top.slug, "rag");
    assert!(top.snippet.as_deref().unwrap().contains("augments"));
    // The explanation distinguishes a title-token match from a body hit.
    let fields: Vec<MatchField> = top.reasons.iter().map(|r| r.field).collect();
    assert!(fields.contains(&MatchField::Title));
    assert!(fields.contains(&MatchField::Body));
}

#[test]
fn eval_recall_over_fixtures() {
    let (vault, canon, _) = seeded();
    let corpus = RagCorpus::load(vault.path(), canon.path()).unwrap();
    let cases = [
        EvalCase::new("ai agent", &["ai-agent"]),
        EvalCase::new("retrieval augmented generation", &["rag"]),
        EvalCase::new("transformer", &["transformer"]),
    ];
    let report = Eval::run(&corpus, &Retriever::new(), &Ranker::new(), &cases, 3);
    assert!(report.passed(1.0), "expected perfect recall, got {}", report.mean_recall);
}

#[test]
fn corpus_build_and_retrieve_write_nothing() {
    let (vault, canon, _) = seeded();
    let vault_before = snapshot(vault.path());
    let canon_before = snapshot(canon.path());

    let corpus = RagCorpus::load(vault.path(), canon.path()).unwrap();
    let scored = Retriever::new().score(&corpus, "agent retrieval transformer");
    let ranked = Ranker::new().rank(scored);
    let _ = ContextBuilder::new().build(&corpus, &ranked, "agent retrieval transformer");

    assert_eq!(snapshot(vault.path()), vault_before, "RAG must not write to the vault");
    assert_eq!(snapshot(canon.path()), canon_before, "RAG must not write to the canonical store");
}

#[test]
fn corrupt_read_model_is_loud() {
    let (vault, canon, _) = seeded();
    // Corrupt the canonical store out-of-band.
    std::fs::write(canon.path().join("broken.json"), "not json").unwrap();
    let err = RagCorpus::load(vault.path(), canon.path()).unwrap_err();
    assert!(matches!(err, ovp_rag::RagError::Load(_)), "got {err:?}");
}
