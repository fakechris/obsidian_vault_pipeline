use super::*;
use serde_json::json;
fn plan() -> RetrievalPlan {
    RetrievalPlan {
        runner: "retrieval".into(),
        fixture_dir: ".".into(),
        control_query_mode: "verbatim".into(),
        candidate_query_mode: "terms".into(),
        k: 1,
        expected_questions: 1,
        min_mean_recall_delta: 0.0,
        timeout_seconds: 60,
    }
}
fn qrels() -> Vec<Qrel> {
    vec![Qrel {
        schema: "ovp.retrieval_eval.qrel/v1".into(),
        id: "q1".into(),
        question: "retrieval".into(),
        class: "exact".into(),
        language: "en".into(),
        confidence: "gold".into(),
        no_answer: false,
        relevant: vec![Relevant {
            surface: "source".into(),
            id: "a".into(),
        }],
    }]
}
fn report(mode: &str, ranks: &[&str]) -> Report {
    serde_json::from_value(json!({"schema": "ovp.retrieval_eval.report/v1",
    "query_mode": mode, "questions": 1, "per_question": [{
        "id": "q1", "tool_errors": [], "source_ranks": {
            "search_sources": ranks, "search_evidence": [], "search_claims": []
        }, "source_recall": {"union": {"@1": 999.0}}
    }]}))
    .unwrap()
}
fn known() -> BTreeSet<String> {
    ["a".into(), "b".into()].into()
}
#[test]
fn derives_recall_from_ordered_outputs_not_reported_metrics() {
    let c = compare(
        &plan(),
        &qrels(),
        &known(),
        report("verbatim", &["b"]),
        report("terms", &["a"]),
    )
    .unwrap();
    assert_eq!(c.mean_recall_delta, 1.0);
    assert_eq!(c.decision, Decision::Accept);
    let c = compare(
        &plan(),
        &qrels(),
        &known(),
        report("verbatim", &["a"]),
        report("terms", &["b"]),
    )
    .unwrap();
    assert_eq!(c.decision, Decision::Reject);
    assert_eq!(c.regressions, ["q1"]);
}
#[test]
fn identical_outputs_require_review_instead_of_claiming_improvement() {
    let c = compare(
        &plan(),
        &qrels(),
        &known(),
        report("verbatim", &["a"]),
        report("terms", &["a"]),
    )
    .unwrap();
    assert_eq!(c.decision, Decision::NeedsHumanReview);
}
#[test]
fn incomplete_failed_and_unknown_source_outputs_are_invalid() {
    let mut bad = report("terms", &["a"]);
    bad.per_question.clear();
    assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
    let mut bad = report("terms", &["a"]);
    bad.per_question[0].tool_errors.push("timeout".into());
    assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
    let mut bad = report("terms", &["a"]);
    bad.per_question[0].source_ranks.remove("search_claims");
    assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
    assert!(
        compare(
            &plan(),
            &qrels(),
            &known(),
            report("verbatim", &["a"]),
            report("terms", &["foreign"])
        )
        .is_err()
    );
}
#[test]
fn missing_observation_fields_do_not_default_to_zero() {
    assert!(
        serde_json::from_value::<Report>(json!({"schema":"ovp.retrieval_eval.report/v1",
        "query_mode":"terms", "questions":1, "per_question":[{"id":"q1"}]}))
        .is_err()
    );
}
#[test]
fn rejects_duplicate_ids_and_policy_mismatch() {
    let mut bad = report("terms", &["a"]);
    bad.per_question[0].id = "other".into();
    assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
    assert!(
        compare(
            &plan(),
            &qrels(),
            &known(),
            report("verbatim", &["a"]),
            report("verbatim", &["a"])
        )
        .is_err()
    );
}
#[test]
fn stronger_preregistered_target_rejects_no_improvement() {
    let mut p = plan();
    p.min_mean_recall_delta = 0.2;
    assert_eq!(
        compare(
            &p,
            &qrels(),
            &known(),
            report("verbatim", &["a"]),
            report("terms", &["a"])
        )
        .unwrap()
        .decision,
        Decision::Reject
    );
    p.k = 0;
    assert!(p.validate().is_err());
    p.k = 1;
    p.min_mean_recall_delta = f64::NAN;
    assert!(p.validate().is_err());
}
#[test]
fn negative_question_regression_is_not_hidden_by_positive_gain() {
    let mut qs = qrels();
    qs.push(Qrel {
        schema: "ovp.retrieval_eval.qrel/v1".into(),
        id: "negative".into(),
        question: "missing".into(),
        class: "negative".into(),
        language: "zh".into(),
        confidence: "gold".into(),
        no_answer: true,
        relevant: vec![],
    });
    let mut a = report("verbatim", &["b"]);
    let mut b = report("terms", &["a"]);
    a.questions = 2;
    b.questions = 2;
    let mut ar = report("verbatim", &[]).per_question.remove(0);
    ar.id = "negative".into();
    let mut br = report("terms", &["b"]).per_question.remove(0);
    br.id = "negative".into();
    a.per_question.push(ar);
    b.per_question.push(br);
    let c = compare(&plan(), &qs, &known(), a, b).unwrap();
    assert_eq!(c.mean_recall_delta, 1.0);
    assert_eq!(c.decision, Decision::Reject);
}
