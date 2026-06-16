use serde::{Deserialize, Serialize};

use crate::candidate::CandidateGuardrails;
use crate::types::Decision;

/// Metrics from a single arm (control or candidate) of an A/B run.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ArmMetrics {
    pub sources_processed: usize,
    pub accepted_units_mean: f64,
    pub quote_found_rate_mean: f64,
    pub cards_kept_mean: f64,
    pub cards_dropped_uncited_mean: f64,
    pub accepted_without_quote_total: u32,
    pub parse_errors: u32,
    pub total_input_tokens: u64,
    pub total_output_tokens: u64,
}

/// Result of comparing control and candidate arms.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Scorecard {
    pub candidate_id: String,
    pub sources_compared: usize,
    pub hard_gates: HardGates,
    pub primary_metrics: PrimaryMetrics,
    pub guardrail_results: GuardrailResults,
    pub decision: Decision,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HardGates {
    pub accepted_without_quote_passed: bool,
    pub no_parse_errors_passed: bool,
    pub all_passed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrimaryMetrics {
    pub units_delta_pct: f64,
    pub quote_rate_delta_pct: f64,
    pub cards_delta_pct: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GuardrailResults {
    pub quote_floor_passed: bool,
    pub token_regression_passed: bool,
    pub all_passed: bool,
}

/// Generate a scorecard by comparing control and candidate metrics.
pub fn generate(
    candidate_id: &str,
    control: &ArmMetrics,
    candidate: &ArmMetrics,
    guardrails: &CandidateGuardrails,
) -> Scorecard {
    let hard_gates = check_hard_gates(candidate);
    let primary_metrics = compute_primary_deltas(control, candidate);
    let guardrail_results = check_guardrails(control, candidate, guardrails);

    let decision = decide(&hard_gates, &primary_metrics, &guardrail_results);

    Scorecard {
        candidate_id: candidate_id.into(),
        sources_compared: control.sources_processed.min(candidate.sources_processed),
        hard_gates,
        primary_metrics,
        guardrail_results,
        decision,
        reasoning: None,
    }
}

fn check_hard_gates(candidate: &ArmMetrics) -> HardGates {
    let awq = candidate.accepted_without_quote_total == 0;
    let pe = candidate.parse_errors == 0;
    HardGates {
        accepted_without_quote_passed: awq,
        no_parse_errors_passed: pe,
        all_passed: awq && pe,
    }
}

fn compute_primary_deltas(control: &ArmMetrics, candidate: &ArmMetrics) -> PrimaryMetrics {
    let units_delta = if control.accepted_units_mean > 0.0 {
        (candidate.accepted_units_mean - control.accepted_units_mean) / control.accepted_units_mean * 100.0
    } else {
        0.0
    };
    let quote_delta = if control.quote_found_rate_mean > 0.0 {
        (candidate.quote_found_rate_mean - control.quote_found_rate_mean) / control.quote_found_rate_mean * 100.0
    } else {
        0.0
    };
    let cards_delta = if control.cards_kept_mean > 0.0 {
        (candidate.cards_kept_mean - control.cards_kept_mean) / control.cards_kept_mean * 100.0
    } else {
        0.0
    };
    PrimaryMetrics {
        units_delta_pct: units_delta,
        quote_rate_delta_pct: quote_delta,
        cards_delta_pct: cards_delta,
    }
}

fn check_guardrails(
    control: &ArmMetrics,
    candidate: &ArmMetrics,
    guardrails: &CandidateGuardrails,
) -> GuardrailResults {
    let quote_floor = guardrails.quote_found_rate_floor.unwrap_or(0.90);
    let quote_ok = candidate.quote_found_rate_mean >= quote_floor;

    let max_token = guardrails.max_token_regression.unwrap_or(1.3);
    let control_tokens = control.total_input_tokens + control.total_output_tokens;
    let candidate_tokens = candidate.total_input_tokens + candidate.total_output_tokens;
    let token_ratio = if control_tokens > 0 {
        candidate_tokens as f64 / control_tokens as f64
    } else {
        1.0
    };
    let token_ok = token_ratio <= max_token;

    GuardrailResults {
        quote_floor_passed: quote_ok,
        token_regression_passed: token_ok,
        all_passed: quote_ok && token_ok,
    }
}

fn decide(
    hard: &HardGates,
    primary: &PrimaryMetrics,
    guardrails: &GuardrailResults,
) -> Decision {
    if !hard.all_passed {
        return Decision::Reject;
    }
    if !guardrails.all_passed {
        return Decision::Reject;
    }
    if primary.units_delta_pct > 0.0 || primary.cards_delta_pct > 0.0 {
        Decision::Accept
    } else if primary.quote_rate_delta_pct < -5.0 {
        Decision::Reject
    } else {
        Decision::NeedsHumanReview
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn baseline() -> ArmMetrics {
        ArmMetrics {
            sources_processed: 5,
            accepted_units_mean: 4.0,
            quote_found_rate_mean: 0.97,
            cards_kept_mean: 3.0,
            cards_dropped_uncited_mean: 0.5,
            accepted_without_quote_total: 0,
            parse_errors: 0,
            total_input_tokens: 10000,
            total_output_tokens: 5000,
        }
    }

    #[test]
    fn improved_candidate_accepted() {
        let control = baseline();
        let mut candidate = baseline();
        candidate.accepted_units_mean = 5.5;
        candidate.cards_kept_mean = 4.0;

        let sc = generate("test-001", &control, &candidate, &CandidateGuardrails::default());
        assert_eq!(sc.decision, Decision::Accept);
        assert!(sc.primary_metrics.units_delta_pct > 30.0);
    }

    #[test]
    fn hard_gate_failure_rejects() {
        let control = baseline();
        let mut candidate = baseline();
        candidate.accepted_without_quote_total = 2;

        let sc = generate("test-002", &control, &candidate, &CandidateGuardrails::default());
        assert_eq!(sc.decision, Decision::Reject);
        assert!(!sc.hard_gates.all_passed);
    }

    #[test]
    fn token_regression_rejects() {
        let control = baseline();
        let mut candidate = baseline();
        candidate.accepted_units_mean = 5.0;
        candidate.total_input_tokens = 20000;
        candidate.total_output_tokens = 15000;

        let guardrails = CandidateGuardrails {
            max_token_regression: Some(1.3),
            ..Default::default()
        };
        let sc = generate("test-003", &control, &candidate, &guardrails);
        assert_eq!(sc.decision, Decision::Reject);
        assert!(!sc.guardrail_results.token_regression_passed);
    }

    #[test]
    fn no_improvement_needs_review() {
        let control = baseline();
        let candidate = baseline();
        let sc = generate("test-004", &control, &candidate, &CandidateGuardrails::default());
        assert_eq!(sc.decision, Decision::NeedsHumanReview);
    }
}
