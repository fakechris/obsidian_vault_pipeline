use ovp_evolve::decision_three_arm::*;
use std::collections::BTreeMap;
fn fixture() -> (ThreeArmPlan, Vec<CaseResult>) {
    let a = ArmSpec {
        provider: "incumbent".into(),
        model: "pinned-v1".into(),
        prompt_format: PromptFormat::GenerativeJudge,
        prompt_version: "judge/v1".into(),
        question_version: "strength/v1".into(),
    };
    let b = ArmSpec {
        prompt_format: PromptFormat::TypedNoRationale,
        prompt_version: "typed/v1".into(),
        ..a.clone()
    };
    let c = ArmSpec {
        provider: "candidate".into(),
        model: "candidate-v1".into(),
        ..b.clone()
    };
    let case = FrozenCase {
        case_id: "synthetic-1".into(),
        input_digest: "a".repeat(64),
        source_revision: "frozen-v1".into(),
        split: Split::Holdout,
        label: Label::Silver {
            value: "supported".into(),
            provenance: "blind-family-1+family-2/run-1".into(),
        },
    };
    let plan = ThreeArmPlan {
        experiment_id: "synthetic".into(),
        arms: BTreeMap::from([(Arm::A, a), (Arm::B, b), (Arm::C, c)]),
        cases: vec![case.clone()],
    };
    let results = plan
        .arms
        .iter()
        .map(|(arm, spec)| CaseResult {
            arm: *arm,
            case_id: case.case_id.clone(),
            input_digest: case.input_digest.clone(),
            source_revision: case.source_revision.clone(),
            split: case.split.clone(),
            spec: spec.clone(),
            outcome: Outcome::Answer {
                value: "supported".into(),
            },
            input_tokens: Some(12),
            output_tokens: Some(2),
            latency_ms: Some(70),
        })
        .collect();
    (plan, results)
}
#[test]
fn valid_report_is_silver_agreement_with_actual_usage() {
    let (p, r) = fixture();
    let report = validate_and_report(&p, &r);
    assert!(report.valid);
    let a = &report.arms[&Arm::A];
    assert_eq!(a.silver_agreement, Some(1.0));
    assert_eq!(a.observed_input_tokens, 12);
    assert_eq!(a.latency_p95_ms, Some(70));
    assert!(!serde_json::to_string(&report).unwrap().contains("accuracy"));
}
#[test]
fn missing_duplicate_and_failed_cases_remain_in_denominator() {
    for mode in 0..4 {
        let (p, mut r) = fixture();
        match mode {
            0 => {
                r.remove(0);
            }
            1 => r.push(r[0].clone()),
            2 => r[0].outcome = Outcome::ReplayMiss,
            _ => {
                r[0].outcome = Outcome::Failure {
                    reason: "timeout".into(),
                }
            }
        }
        let report = validate_and_report(&p, &r);
        assert!(!report.valid);
        let a = &report.arms[&Arm::A];
        assert_eq!(a.expected_cases, 1);
        assert_eq!(a.failures, 1);
        assert_eq!(a.silver_agreement, Some(0.0));
    }
}
#[test]
fn every_binding_is_checked() {
    for mode in 0..7 {
        let (p, mut r) = fixture();
        match mode {
            0 => r[0].input_digest = "b".repeat(64),
            1 => r[0].source_revision = "other".into(),
            2 => r[0].split = Split::Development,
            3 => r[0].spec.model = "other".into(),
            4 => r[0].spec.provider = "other".into(),
            5 => r[0].spec.prompt_version = "other".into(),
            _ => r[0].spec.question_version = "other".into(),
        }
        let report = validate_and_report(&p, &r);
        assert!(!report.valid);
        assert_eq!(report.arms[&Arm::A].failures, 1);
    }
}
#[test]
fn preregistration_rejects_confounded_models_formats_and_versions() {
    for mode in 0..7 {
        let (mut p, r) = fixture();
        match mode {
            0 => p.arms.get_mut(&Arm::B).unwrap().model = "different".into(),
            1 => p.arms.get_mut(&Arm::B).unwrap().provider = "different".into(),
            2 => p.arms.get_mut(&Arm::A).unwrap().prompt_format = PromptFormat::TypedNoRationale,
            3 => p.arms.get_mut(&Arm::C).unwrap().question_version = "different".into(),
            4 => p.arms.get_mut(&Arm::C).unwrap().prompt_version = "different".into(),
            5 => {
                p.arms.remove(&Arm::C);
            }
            _ => p.cases.push(p.cases[0].clone()),
        }
        assert!(!validate_and_report(&p, &r).valid);
    }
}
#[test]
fn abstention_is_not_failure_and_unknown_label_is_not_negative() {
    let (mut p, mut r) = fixture();
    p.cases[0].label = Label::Unknown {
        provenance: "models-disagree".into(),
    };
    r[0].outcome = Outcome::Abstain;
    r[0].input_tokens = None;
    r[0].latency_ms = None;
    let report = validate_and_report(&p, &r);
    assert!(report.valid);
    let a = &report.arms[&Arm::A];
    assert_eq!(a.abstentions, 1);
    assert_eq!(a.failures, 0);
    assert_eq!(a.silver_agreement, None);
    assert_eq!(a.input_usage_missing, 1);
    assert_eq!(a.latency_p50_ms, None);
}
#[test]
fn rejects_unknown_case_and_missing_provenance() {
    let (mut p, mut r) = fixture();
    r[0].case_id = "unknown".into();
    assert!(!validate_and_report(&p, &r).valid);
    p.cases[0].label = Label::Silver {
        value: "supported".into(),
        provenance: "".into(),
    };
    assert!(!validate_and_report(&p, &[]).valid);
}

#[test]
fn development_and_holdout_cannot_be_pooled() {
    let (mut p, r) = fixture();
    let mut other = p.cases[0].clone();
    other.case_id = "synthetic-2".into();
    other.split = Split::Development;
    p.cases.push(other);
    let report = validate_and_report(&p, &r);
    assert!(report
        .issues
        .iter()
        .any(|s| s.contains("separate frozen plans")));
}
