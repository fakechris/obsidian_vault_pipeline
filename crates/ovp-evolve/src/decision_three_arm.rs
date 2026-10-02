//! Descriptive, fail-closed validation of preregistered three-arm experiments.
//! This module does not execute providers, infer prices, or accept candidates.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Arm {
    A,
    B,
    C,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Split {
    Development,
    Holdout,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PromptFormat {
    GenerativeJudge,
    TypedNoRationale,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArmSpec {
    pub provider: String,
    pub model: String,
    pub prompt_format: PromptFormat,
    pub prompt_version: String,
    pub question_version: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Label {
    Silver { value: String, provenance: String },
    Unknown { provenance: String },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenCase {
    pub case_id: String,
    /// SHA-256 of the exact shared source input, not the arm-specific prompt.
    pub input_digest: String,
    pub source_revision: String,
    pub split: Split,
    pub label: Label,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ThreeArmPlan {
    pub experiment_id: String,
    pub arms: BTreeMap<Arm, ArmSpec>,
    pub cases: Vec<FrozenCase>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Outcome {
    Answer { value: String },
    Abstain,
    Failure { reason: String },
    ReplayMiss,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaseResult {
    pub arm: Arm,
    pub case_id: String,
    pub input_digest: String,
    pub source_revision: String,
    pub split: Split,
    pub spec: ArmSpec,
    pub outcome: Outcome,
    /// Actual provider-reported usage only; None means unknown, not zero.
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub latency_ms: Option<u64>,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ArmReport {
    pub expected_cases: usize,
    pub failures: usize,
    pub abstentions: usize,
    pub answers: usize,
    pub silver_cases: usize,
    pub silver_matches: usize,
    /// Denominator includes all silver cases, including failures and abstentions.
    pub silver_agreement: Option<f64>,
    pub observed_input_tokens: u64,
    pub observed_output_tokens: u64,
    pub input_usage_missing: usize,
    pub output_usage_missing: usize,
    pub latency_missing: usize,
    pub latency_p50_ms: Option<u64>,
    pub latency_p95_ms: Option<u64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ThreeArmReport {
    pub valid: bool,
    pub issues: Vec<String>,
    pub arms: BTreeMap<Arm, ArmReport>,
}
fn nonempty(s: &str) -> bool {
    !s.trim().is_empty()
}
fn digest(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}
fn percentile(values: &[u64], percent: usize) -> Option<u64> {
    if values.is_empty() {
        None
    } else {
        Some(values[(values.len() * percent).div_ceil(100) - 1])
    }
}

/// Validate bindings and produce descriptive metrics even when the experiment is invalid.
/// Invalid reports must never be used as evidence for candidate promotion.
pub fn validate_and_report(plan: &ThreeArmPlan, results: &[CaseResult]) -> ThreeArmReport {
    let mut issues = Vec::new();
    if !nonempty(&plan.experiment_id) || plan.cases.is_empty() {
        issues.push("empty experiment or case set".into());
    }
    if plan.arms.len() != 3
        || [Arm::A, Arm::B, Arm::C]
            .iter()
            .any(|a| !plan.arms.contains_key(a))
    {
        issues.push("plan must specify exactly A, B and C".into());
    }
    for (arm, spec) in &plan.arms {
        if [
            &spec.provider,
            &spec.model,
            &spec.prompt_version,
            &spec.question_version,
        ]
        .iter()
        .any(|s| !nonempty(s))
        {
            issues.push(format!("{arm:?}: empty provider/model/version"));
        }
        let expected = if *arm == Arm::A {
            PromptFormat::GenerativeJudge
        } else {
            PromptFormat::TypedNoRationale
        };
        if spec.prompt_format != expected {
            issues.push(format!("{arm:?}: incorrect prompt format"));
        }
    }
    if let (Some(a), Some(b), Some(c)) = (
        plan.arms.get(&Arm::A),
        plan.arms.get(&Arm::B),
        plan.arms.get(&Arm::C),
    ) {
        if a.provider != b.provider || a.model != b.model {
            issues.push("A/B must use identical provider and model".into());
        }
        if b.question_version != c.question_version || b.prompt_version != c.prompt_version {
            issues.push("B/C must share question and prompt versions".into());
        }
    }
    if plan
        .cases
        .first()
        .is_some_and(|first| plan.cases.iter().any(|case| case.split != first.split))
    {
        issues.push("development and holdout require separate frozen plans".into());
    }
    let mut ids = BTreeSet::new();
    for case in &plan.cases {
        if !nonempty(&case.case_id) || !ids.insert(case.case_id.as_str()) {
            issues.push("empty or duplicate planned case".into());
        }
        if !digest(&case.input_digest) || !nonempty(&case.source_revision) {
            issues.push(format!("{}: invalid input binding", case.case_id));
        }
        let (value, provenance) = match &case.label {
            Label::Silver { value, provenance } => (Some(value), provenance),
            Label::Unknown { provenance } => (None, provenance),
        };
        if !nonempty(provenance) || value.is_some_and(|v| !nonempty(v)) {
            issues.push(format!("{}: invalid label provenance", case.case_id));
        }
    }
    let mut indexed: BTreeMap<(Arm, &str), Vec<&CaseResult>> = BTreeMap::new();
    for result in results {
        if !ids.contains(result.case_id.as_str()) {
            issues.push(format!("unknown result case {}", result.case_id));
        }
        indexed
            .entry((result.arm, &result.case_id))
            .or_default()
            .push(result);
    }
    let mut arms = BTreeMap::new();
    for arm in [Arm::A, Arm::B, Arm::C] {
        let mut report = ArmReport {
            expected_cases: plan.cases.len(),
            ..ArmReport::default()
        };
        let mut latencies = Vec::new();
        for case in &plan.cases {
            if matches!(case.label, Label::Silver { .. }) {
                report.silver_cases += 1;
            }
            let rows = indexed.get(&(arm, case.case_id.as_str()));
            if rows.is_none_or(|r| r.len() != 1) {
                issues.push(format!(
                    "{arm:?}/{}: missing or duplicate result",
                    case.case_id
                ));
                report.failures += 1;
                report.input_usage_missing += 1;
                report.output_usage_missing += 1;
                report.latency_missing += 1;
                continue;
            }
            let result = rows.expect("checked above")[0];
            for (usage, total, missing) in [
                (
                    result.input_tokens,
                    &mut report.observed_input_tokens,
                    &mut report.input_usage_missing,
                ),
                (
                    result.output_tokens,
                    &mut report.observed_output_tokens,
                    &mut report.output_usage_missing,
                ),
            ] {
                if let Some(n) = usage {
                    if let Some(sum) = total.checked_add(n) {
                        *total = sum;
                    } else {
                        issues.push("token sum overflow".into());
                    }
                } else {
                    *missing += 1;
                }
            }
            if let Some(ms) = result.latency_ms {
                latencies.push(ms);
            } else {
                report.latency_missing += 1;
            }
            if result.input_digest != case.input_digest
                || result.source_revision != case.source_revision
                || result.split != case.split
                || plan.arms.get(&arm) != Some(&result.spec)
            {
                issues.push(format!("{arm:?}/{}: result binding mismatch", case.case_id));
                report.failures += 1;
                continue;
            }
            match &result.outcome {
                Outcome::Answer { value } if nonempty(value) => {
                    report.answers += 1;
                    if matches!(&case.label, Label::Silver { value: label, .. } if label == value) {
                        report.silver_matches += 1;
                    }
                }
                Outcome::Abstain => report.abstentions += 1,
                _ => {
                    report.failures += 1;
                    issues.push(format!(
                        "{arm:?}/{}: failed execution or empty answer",
                        case.case_id
                    ));
                }
            }
        }
        latencies.sort_unstable();
        report.latency_p50_ms = percentile(&latencies, 50);
        report.latency_p95_ms = percentile(&latencies, 95);
        report.silver_agreement = (report.silver_cases > 0)
            .then(|| report.silver_matches as f64 / report.silver_cases as f64);
        arms.insert(arm, report);
    }
    ThreeArmReport {
        valid: issues.is_empty(),
        issues,
        arms,
    }
}
