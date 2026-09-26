use ovp_llm::decision::{runtime::*, *};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionArmPlan {
    pub profile: String,
    pub execution: ExecutionMode,
    pub question_namespace: String,
    /// Explicit interpretation, scoped to this profile/model/question version.
    /// No default 0.5 threshold, and no threshold transfer across suppliers.
    #[serde(default)]
    pub boolean_thresholds: BTreeMap<QuestionId, f64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionBudget {
    pub max_requests_per_arm: usize,
    pub max_wall_ms_per_arm: u64,
    pub max_observed_input_tokens_per_arm: u64,
    pub max_observed_output_tokens_per_arm: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionPlan {
    pub runner: String,
    pub experiment_id: String,
    pub fixture_dir: PathBuf,
    pub capability: String,
    pub profiles: BTreeMap<String, DecisionProfile>,
    pub control: DecisionArmPlan,
    pub candidate: DecisionArmPlan,
    pub expected_cases: usize,
    pub expected_questions: usize,
    /// gold supports development; holdout is exclusively an acceptance check.
    pub split: String,
    pub purpose: String,
    pub min_accuracy_delta: f64,
    pub max_bucket_accuracy_regression: f64,
    pub min_bucket_size: usize,
    pub budget: DecisionBudget,
}
impl DecisionPlan {
    pub fn settings(&self, arm: &DecisionArmPlan) -> DecisionSettings {
        DecisionSettings {
            profiles: self.profiles.clone(),
            capabilities: BTreeMap::from([(
                self.capability.clone(),
                CapabilityConfig {
                    mode: DecisionMode::Enabled,
                    execution: arm.execution,
                    profile: Some(arm.profile.clone()),
                    question_namespace: Some(arm.question_namespace.clone()),
                    experiment: None,
                },
            )]),
        }
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.runner != "decision"
            || self.experiment_id.trim().is_empty()
            || self.expected_cases == 0
            || self.expected_cases > 10000
            || self.expected_questions < self.expected_cases
            || self.min_bucket_size == 0
            || !self.min_accuracy_delta.is_finite()
            || !(-1.0..=1.0).contains(&self.min_accuracy_delta)
            || !self.max_bucket_accuracy_regression.is_finite()
            || !(0.0..=1.0).contains(&self.max_bucket_accuracy_regression)
            || !matches!(
                (self.split.as_str(), self.purpose.as_str()),
                ("gold", "development") | ("holdout", "acceptance")
            )
            || self.budget.max_requests_per_arm < self.expected_cases
            || self.budget.max_requests_per_arm > 10000
            || self.budget.max_wall_ms_per_arm == 0
            || self.budget.max_wall_ms_per_arm > 3600000
            || self.budget.max_observed_input_tokens_per_arm == 0
            || self.budget.max_observed_output_tokens_per_arm == 0
        {
            return Err("invalid decision plan, split/purpose or budget".into());
        }
        for arm in [&self.control, &self.candidate] {
            self.settings(arm)
                .validate_structure()
                .map_err(|e| e.to_string())?;
            if arm
                .boolean_thresholds
                .values()
                .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
            {
                return Err("invalid preregistered threshold".into());
            }
        }
        Ok(())
    }
}
