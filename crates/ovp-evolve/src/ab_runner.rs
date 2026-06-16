//! Paired A/B runner that compares control (cassette replay) vs candidate
//! (live LLM + record) on the same fixture set.
//!
//! Phase 2 implementation: for now exposes the types and comparison entry
//! point. Actual pipeline invocation will integrate with `ovp-run` once
//! the crate is wired as a dependency.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::candidate::CandidateSpec;
use crate::scorecard::{self, ArmMetrics, Scorecard};

/// Configuration for a paired A/B run.
#[derive(Debug, Clone)]
pub struct AbConfig {
    pub candidate_spec_path: PathBuf,
    pub fixture_dir: PathBuf,
    pub output_dir: PathBuf,
}

/// Result of running both arms.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AbResult {
    pub candidate_id: String,
    pub control_metrics: ArmMetrics,
    pub candidate_metrics: ArmMetrics,
    pub scorecard: Scorecard,
}

/// Compare pre-collected control and candidate metrics to produce a scorecard.
///
/// In the full implementation, this function will orchestrate pipeline runs.
/// For now it accepts pre-computed metrics (e.g., from manual runs or test
/// harnesses).
pub fn compare(
    spec: &CandidateSpec,
    control: ArmMetrics,
    candidate: ArmMetrics,
) -> AbResult {
    let scorecard = scorecard::generate(
        &spec.id,
        &control,
        &candidate,
        &spec.guardrails,
    );
    AbResult {
        candidate_id: spec.id.clone(),
        control_metrics: control,
        candidate_metrics: candidate,
        scorecard,
    }
}

/// Placeholder: discover fixture sources from a directory.
pub fn discover_fixtures(fixture_dir: &Path) -> Vec<PathBuf> {
    if !fixture_dir.is_dir() {
        return Vec::new();
    }
    let mut sources = Vec::new();
    if let Ok(entries) = std::fs::read_dir(fixture_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                sources.push(path);
            }
        }
    }
    sources.sort();
    sources
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::candidate::CandidateSpec;
    use crate::types::Decision;

    fn test_spec() -> CandidateSpec {
        serde_json::from_str(r#"{
            "kind": "ovp.evolution.candidate/v1",
            "id": "ab-test-001",
            "base_version": "v5",
            "target_version": "v6",
            "surface": "prompt",
            "component": "prompt.unit_extract",
            "hypothesis": "test hypothesis",
            "rollback": "revert"
        }"#).unwrap()
    }

    #[test]
    fn compare_produces_scorecard() {
        let spec = test_spec();
        let control = ArmMetrics {
            sources_processed: 3,
            accepted_units_mean: 4.0,
            quote_found_rate_mean: 0.95,
            cards_kept_mean: 3.0,
            ..Default::default()
        };
        let candidate = ArmMetrics {
            sources_processed: 3,
            accepted_units_mean: 6.0,
            quote_found_rate_mean: 0.96,
            cards_kept_mean: 4.0,
            ..Default::default()
        };
        let result = compare(&spec, control, candidate);
        assert_eq!(result.scorecard.decision, Decision::Accept);
        assert!(result.scorecard.primary_metrics.units_delta_pct > 40.0);
    }
}
