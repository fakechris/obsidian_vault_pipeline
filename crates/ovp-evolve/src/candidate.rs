use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::registry::ComponentRegistry;
use crate::types::ChangeSurface;

pub const CANDIDATE_KIND: &str = "ovp.evolution.candidate/v1";

/// A candidate spec proposing a change to one component.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CandidateSpec {
    pub kind: String,
    pub id: String,
    pub base_version: String,
    pub target_version: String,
    pub surface: ChangeSurface,
    pub component: String,
    pub hypothesis: String,
    #[serde(default)]
    pub predicted_delta: Value,
    #[serde(default)]
    pub guardrails: CandidateGuardrails,
    #[serde(default)]
    pub eval_plan: EvalPlan,
    pub rollback: String,
    #[serde(default)]
    pub ablation_required: bool,
}

/// Guardrails that must not be violated for the candidate to pass.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CandidateGuardrails {
    #[serde(default)]
    pub quote_found_rate_floor: Option<f64>,
    #[serde(default)]
    pub accepted_without_quote: Option<u32>,
    #[serde(default)]
    pub max_token_regression: Option<f64>,
}

/// Plan for evaluating this candidate.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EvalPlan {
    #[serde(default)]
    pub replay_set: Option<String>,
    #[serde(default)]
    pub paired_sources: Option<usize>,
    #[serde(default)]
    pub soak: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum CandidateError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
    #[error("validation: {0}")]
    Validation(String),
}

impl CandidateSpec {
    /// Load a candidate spec from a JSON file.
    pub fn load(path: &Path) -> Result<Self, CandidateError> {
        let data = std::fs::read_to_string(path)?;
        let spec: Self = serde_json::from_str(&data)?;
        Ok(spec)
    }

    /// Validate the candidate spec against the registry.
    pub fn validate(&self, registry: &ComponentRegistry) -> Result<(), CandidateError> {
        if self.kind != CANDIDATE_KIND {
            return Err(CandidateError::Validation(format!(
                "expected kind '{}', got '{}'",
                CANDIDATE_KIND, self.kind
            )));
        }

        if self.id.is_empty() {
            return Err(CandidateError::Validation("id must not be empty".into()));
        }

        if self.hypothesis.is_empty() {
            return Err(CandidateError::Validation(
                "hypothesis must not be empty".into(),
            ));
        }

        if self.rollback.is_empty() {
            return Err(CandidateError::Validation(
                "rollback plan must not be empty".into(),
            ));
        }

        let comp = registry.get(&self.component).ok_or_else(|| {
            CandidateError::Validation(format!(
                "unknown component '{}'; registered: {:?}",
                self.component,
                registry.ids()
            ))
        })?;

        if comp.surface != self.surface && !self.ablation_required {
            return Err(CandidateError::Validation(format!(
                "candidate surface '{}' differs from component surface '{}' \
                 without ablation_required=true",
                self.surface, comp.surface
            )));
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::ComponentRegistry;

    fn test_registry() -> ComponentRegistry {
        serde_json::from_str(
            r#"{
            "components": [
                {
                    "id": "prompt.unit_extract",
                    "surface": "prompt",
                    "current_version": "v5",
                    "file": "crates/ovp-domain/prompts/unit_extraction.md",
                    "quality_buckets": ["extraction_fidelity", "coverage"]
                }
            ]
        }"#,
        )
        .unwrap()
    }

    fn valid_spec() -> CandidateSpec {
        serde_json::from_str(
            r#"{
            "kind": "ovp.evolution.candidate/v1",
            "id": "unit-extract-v6-test",
            "base_version": "unit_extract/v5",
            "target_version": "unit_extract/v6",
            "surface": "prompt",
            "component": "prompt.unit_extract",
            "hypothesis": "improve coverage",
            "rollback": "revert to v5"
        }"#,
        )
        .unwrap()
    }

    #[test]
    fn valid_candidate_passes() {
        let reg = test_registry();
        let spec = valid_spec();
        spec.validate(&reg).unwrap();
    }

    #[test]
    fn unknown_component_rejected() {
        let reg = test_registry();
        let mut spec = valid_spec();
        spec.component = "nonexistent".into();
        assert!(spec.validate(&reg).is_err());
    }

    #[test]
    fn missing_hypothesis_rejected() {
        let reg = test_registry();
        let mut spec = valid_spec();
        spec.hypothesis = String::new();
        assert!(spec.validate(&reg).is_err());
    }

    #[test]
    fn missing_rollback_rejected() {
        let reg = test_registry();
        let mut spec = valid_spec();
        spec.rollback = String::new();
        assert!(spec.validate(&reg).is_err());
    }

    #[test]
    fn surface_mismatch_without_ablation_rejected() {
        let reg = test_registry();
        let mut spec = valid_spec();
        spec.surface = ChangeSurface::Runtime;
        assert!(spec.validate(&reg).is_err());
    }

    #[test]
    fn surface_mismatch_with_ablation_passes() {
        let reg = test_registry();
        let mut spec = valid_spec();
        spec.surface = ChangeSurface::Runtime;
        spec.ablation_required = true;
        spec.validate(&reg).unwrap();
    }
}
