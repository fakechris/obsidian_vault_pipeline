//! Unit-style tests for the contract assertion engine. Parses the real
//! fixtures/article_clean/expected/contract.yaml and runs it against
//! hand-crafted InterpretedDoc + WritePlan inputs covering happy path
//! + failure modes.

use ovp_core::{
    ContentHash, OpId, RunId, VaultCreateOp, VaultPath, WriteOp, WritePlan,
};
use ovp_domain::testing::{assert_contract, load_contract};
use ovp_domain::*;

fn fixture_contract_path() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir)
        .ancestors()
        .nth(2)
        .unwrap()
        .join("fixtures/article_clean/expected/contract.yaml")
}

fn happy_interpreted() -> InterpretedDoc {
    InterpretedDoc {
        title: "A Guide to Agent-native Product Management".into(),
        source_url: "https://every.to/guides/ai-product-management-guide".into(),
        author: Some("Marcus Moretti".into()),
        date: "2026-05-04".into(),
        doc_type: "article".into(),
        area: "ai".into(),
        tags: vec!["AI产品管理".into(), "Agent".into(), "PM".into()],
        canonical_concepts: vec![],
        // 12 candidates — satisfies length_gte: 10 + length_in_range: [8, 20].
        concept_candidates: (1..=12).map(|i| format!("concept-{i}")).collect(),
        dimensions: Dimensions {
            one_liner: "x".into(),
            explanation: Explanation {
                what: "w".into(),
                why: "y".into(),
                how: "h".into(),
            },
            details: vec!["d1".into(), "d2".into(), "d3".into()],
            structure: None,
            actions: vec!["a1".into()],
            linked_concepts: vec![],
        },
        schema: InterpretationSchema::ArticleV1,
        concepts: Vec::new(),
    }
}

fn happy_plan(body: &str) -> WritePlan {
    let mut plan = WritePlan::new(RunId::new("test-run"));
    plan.push(WriteOp::VaultCreate(VaultCreateOp {
        op_id: OpId::new("op-1"),
        path: VaultPath::new("20-Areas/AI-Research/Topics/2026-05/x.md"),
        after_hash: ContentHash::new("h"),
        body: body.into(),
        reason: "test".into(),
        originating_record: ovp_core::RecordId::new("r-1"),
    }));
    plan
}

const HAPPY_BODY: &str = "---\ntitle: x\n---\n\n## 一句话定义\n\nx\n\n## 详细解释\n\nbody\n\n## 行动建议\n\n- a\n";

#[test]
fn happy_path_passes_all_must() {
    let contract = load_contract(&fixture_contract_path()).expect("load contract");
    let doc = happy_interpreted();
    let plan = happy_plan(HAPPY_BODY);

    let report = assert_contract(&contract, Some(&doc), &plan, &[]);

    assert!(
        report.must_clean(),
        "MUST clauses failed: {:?}",
        report.must_failed
    );
    assert!(
        !report.must_passed.is_empty(),
        "expected at least one must_passed"
    );
}

#[test]
fn wrong_title_fails_must() {
    let contract = load_contract(&fixture_contract_path()).unwrap();
    let mut doc = happy_interpreted();
    doc.title = "Different Title".into();
    let plan = happy_plan(HAPPY_BODY);

    let report = assert_contract(&contract, Some(&doc), &plan, &[]);

    assert!(!report.must_clean(), "expected at least one MUST failure");
    let titled_failure = report
        .must_failed
        .iter()
        .find(|f| f.clause.contains("title"));
    assert!(titled_failure.is_some(), "expected a `title` MUST failure");
}

#[test]
fn missing_body_section_fails_must() {
    let contract = load_contract(&fixture_contract_path()).unwrap();
    let doc = happy_interpreted();
    let plan = happy_plan("---\ntitle: x\n---\n\n# nothing structural\n");

    let report = assert_contract(&contract, Some(&doc), &plan, &[]);

    assert!(!report.must_clean());
    let body_failures: Vec<_> = report
        .must_failed
        .iter()
        .filter(|f| f.clause.contains("body_section"))
        .collect();
    assert_eq!(body_failures.len(), 3, "all 3 body_section MUSTs should fail");
}

#[test]
fn short_concept_candidates_fails_length_gte() {
    let contract = load_contract(&fixture_contract_path()).unwrap();
    let mut doc = happy_interpreted();
    doc.concept_candidates = vec!["only-one".into()];
    let plan = happy_plan(HAPPY_BODY);

    let report = assert_contract(&contract, Some(&doc), &plan, &[]);
    let length_fail = report
        .must_failed
        .iter()
        .find(|f| f.clause.contains("length_gte"));
    assert!(length_fail.is_some());
}

#[test]
fn no_interpreted_doc_fails_field_clauses() {
    let contract = load_contract(&fixture_contract_path()).unwrap();
    let plan = happy_plan(HAPPY_BODY);

    let report = assert_contract(&contract, None, &plan, &[]);

    assert!(
        !report.must_failed.is_empty(),
        "expected field clauses to fail when no InterpretedDoc is produced"
    );
}

// --- v1.1 ops: not_equals / matches_one_of / event_emitted / utf8_clean ----

fn mixed_lang_contract_path() -> std::path::PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir)
        .ancestors()
        .nth(2)
        .unwrap()
        .join("fixtures/article_mixed_lang/expected/contract.yaml")
}

#[test]
fn mixed_lang_contract_parses() {
    // No assertion run; just confirms the contract.yaml deserializes with
    // all v1.1 ops in place (not_equals, matches_one_of, event_emitted,
    // utf8_clean). Catches schema regressions before v1.1 work starts.
    let contract = load_contract(&mixed_lang_contract_path()).expect("mixed_lang contract loads");
    assert!(!contract.must.is_empty());
}

#[test]
fn not_equals_op() {
    // Hand-roll a tiny contract with not_equals.
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
must:
  - field: source
    op: not_equals
    value: "https://forbidden.example/"
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();

    let mut doc = happy_interpreted();
    doc.source_url = "https://ok.example/".into();
    let plan = happy_plan(HAPPY_BODY);
    let report = assert_contract(&contract, Some(&doc), &plan, &[]);
    assert!(report.must_clean());

    doc.source_url = "https://forbidden.example/".into();
    let report = assert_contract(&contract, Some(&doc), &plan, &[]);
    assert!(!report.must_clean());
}

#[test]
fn matches_one_of_op() {
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
must:
  - field: source
    op: matches_one_of
    values:
      - "https://a.example/"
      - "https://b.example/"
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let plan = happy_plan(HAPPY_BODY);

    let mut doc = happy_interpreted();
    doc.source_url = "https://a.example/".into();
    assert!(assert_contract(&contract, Some(&doc), &plan, &[]).must_clean());

    doc.source_url = "https://c.example/".into();
    assert!(!assert_contract(&contract, Some(&doc), &plan, &[]).must_clean());
}

#[test]
fn event_emitted_op() {
    use ovp_core::{Event, EventKind, EventTs, RunId};
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
must:
  - event_emitted:
      kind: run_started
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let plan = happy_plan(HAPPY_BODY);
    let doc = happy_interpreted();

    // Without the event: fails.
    let report = assert_contract(&contract, Some(&doc), &plan, &[]);
    assert!(!report.must_clean());

    // With the event: passes.
    let events = vec![Event::new(RunId::new("r"), EventTs::new(0), EventKind::RunStarted)];
    let report = assert_contract(&contract, Some(&doc), &plan, &events);
    assert!(report.must_clean(), "{:?}", report.must_failed);
}

#[test]
fn utf8_clean_op() {
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
must:
  - utf8_clean:
      paths: [title, tags]
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let plan = happy_plan(HAPPY_BODY);

    let doc = happy_interpreted();
    assert!(assert_contract(&contract, Some(&doc), &plan, &[]).must_clean());

    let mut empty_title = happy_interpreted();
    empty_title.title = String::new();
    assert!(!assert_contract(&contract, Some(&empty_title), &plan, &[]).must_clean());
}

// --- unknown-clause hardening (F2) -----------------------------------------

#[test]
fn unknown_op_at_must_fails_loudly() {
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
must:
  - field: title
    op: some_unimplemented_op
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let report = assert_contract(&contract, Some(&happy_interpreted()), &happy_plan(HAPPY_BODY), &[]);
    assert!(!report.must_clean(), "unknown MUST op must fail, not skip");
}

#[test]
fn unknown_op_at_should_fails_loudly() {
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
should:
  - field: title
    op: some_unimplemented_op
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let report = assert_contract(&contract, Some(&happy_interpreted()), &happy_plan(HAPPY_BODY), &[]);
    assert!(report.must_clean(), "no MUST clauses");
    assert!(!report.should_failed.is_empty(), "unknown SHOULD op must fail, not skip");
}

#[test]
fn unknown_clause_shape_at_must_fails_loudly() {
    // A github-style clause we haven't implemented (e.g. writeplan_constraint)
    // arriving as a MUST must fail, never silently pass.
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
must:
  - writeplan_constraint:
      forbidden_path_prefix: "20-Areas/"
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let report = assert_contract(&contract, Some(&happy_interpreted()), &happy_plan(HAPPY_BODY), &[]);
    assert!(!report.must_clean(), "unknown MUST clause shape must fail");
}

#[test]
fn unknown_clause_at_may_break_is_documentation_only() {
    let yaml = r#"
version: 1
terminal_state: interpretation_produced
may_break:
  - absorb_skipped: true
  - field: pipeline_run_id
"#;
    let contract: ovp_domain::testing::Contract = serde_yaml::from_str(yaml).unwrap();
    let report = assert_contract(&contract, Some(&happy_interpreted()), &happy_plan(HAPPY_BODY), &[]);
    // No hard failures: may-break unknowns are documentation-only.
    assert!(report.must_clean());
    assert!(report.should_failed.is_empty());
    assert!(report.may_break_failed.is_empty());
    // The unknown clause shows up as skipped (visible, not silently gone).
    assert!(!report.skipped.is_empty(), "may-break unknown should be recorded as skipped");
}
