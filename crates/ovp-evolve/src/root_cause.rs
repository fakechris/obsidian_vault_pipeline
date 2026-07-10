use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::types::{QualityBucket, SuspectedSurface};

pub const ROOT_CAUSE_KIND: &str = "ovp.evolution.root-cause/v1";

/// A structured diagnosis of a pipeline failure or quality degradation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RootCauseCard {
    pub kind: String,
    pub run_id: String,
    pub source: String,
    pub status: String,
    pub symptoms: Vec<String>,
    pub primary_bucket: QualityBucket,
    #[serde(default)]
    pub secondary_buckets: Vec<QualityBucket>,
    pub suspected_surface: SuspectedSurface,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub evidence: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommendation: Option<String>,
}

impl RootCauseCard {
    pub fn new(run_id: &str, source: &str) -> Self {
        Self {
            kind: ROOT_CAUSE_KIND.into(),
            run_id: run_id.into(),
            source: source.into(),
            status: "unknown".into(),
            symptoms: Vec::new(),
            primary_bucket: QualityBucket::OperationalReliability,
            secondary_buckets: Vec::new(),
            suspected_surface: SuspectedSurface::Unknown,
            confidence: 0.0,
            evidence: Value::Null,
            recommendation: None,
        }
    }
}

/// Validate a root-cause card for completeness.
pub fn validate_card(card: &RootCauseCard) -> Result<(), String> {
    if card.kind != ROOT_CAUSE_KIND {
        return Err(format!("expected kind '{}', got '{}'", ROOT_CAUSE_KIND, card.kind));
    }
    if card.run_id.is_empty() {
        return Err("run_id must not be empty".into());
    }
    if card.source.is_empty() {
        return Err("source must not be empty".into());
    }
    if card.symptoms.is_empty() {
        return Err("at least one symptom required".into());
    }
    if card.confidence < 0.0 || card.confidence > 1.0 {
        return Err("confidence must be in [0.0, 1.0]".into());
    }
    Ok(())
}

/// Infer the suspected surface from symptoms using deterministic rules.
pub fn infer_surface(symptoms: &[String]) -> (SuspectedSurface, QualityBucket, f64) {
    for s in symptoms {
        let lower = s.to_lowercase();

        if lower.contains("parse_error") || lower.contains("json_repair") || lower.contains("malformed json") {
            return (SuspectedSurface::Parser, QualityBucket::OperationalReliability, 0.85);
        }

        if lower.contains("timeout") || lower.contains("network") || lower.contains("connection") {
            return (SuspectedSurface::Runtime, QualityBucket::OperationalReliability, 0.90);
        }

        if lower.contains("content_moderation") || lower.contains("blocked") {
            return (SuspectedSurface::Runtime, QualityBucket::OperationalReliability, 0.95);
        }

        if lower.contains("quote_found_rate") || lower.contains("accepted_without_quote") {
            return (SuspectedSurface::Prompt, QualityBucket::ExtractionFidelity, 0.70);
        }

        if lower.contains("cards_dropped") || lower.contains("uncited") {
            return (SuspectedSurface::Prompt, QualityBucket::CardQuality, 0.65);
        }

        if lower.contains("crystal") || lower.contains("provenance") {
            return (SuspectedSurface::Gate, QualityBucket::CrystalProvenance, 0.60);
        }
    }

    (SuspectedSurface::Unknown, QualityBucket::OperationalReliability, 0.30)
}

/// Build a root-cause card from a failed run's symptoms.
pub fn diagnose(run_id: &str, source: &str, symptoms: Vec<String>) -> RootCauseCard {
    let (surface, bucket, confidence) = infer_surface(&symptoms);
    let mut card = RootCauseCard::new(run_id, source);
    card.status = "failed".into();
    card.symptoms = symptoms;
    card.primary_bucket = bucket;
    card.suspected_surface = surface;
    card.confidence = confidence;
    card
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn infer_timeout_surface() {
        let symptoms = vec!["timeout after 30s waiting for LLM response".into()];
        let (surface, bucket, conf) = infer_surface(&symptoms);
        assert_eq!(surface, SuspectedSurface::Runtime);
        assert_eq!(bucket, QualityBucket::OperationalReliability);
        assert!(conf >= 0.85);
    }

    #[test]
    fn infer_parse_error_surface() {
        let symptoms = vec!["parse_error: unexpected token at position 42".into()];
        let (surface, bucket, _) = infer_surface(&symptoms);
        assert_eq!(surface, SuspectedSurface::Parser);
        assert_eq!(bucket, QualityBucket::OperationalReliability);
    }

    #[test]
    fn infer_quote_issue_surface() {
        let symptoms = vec!["quote_found_rate dropped from 0.97 to 0.82".into()];
        let (surface, bucket, _) = infer_surface(&symptoms);
        assert_eq!(surface, SuspectedSurface::Prompt);
        assert_eq!(bucket, QualityBucket::ExtractionFidelity);
    }

    #[test]
    fn diagnose_builds_card() {
        let card = diagnose(
            "daily-2026-06-15",
            "test.md",
            vec!["timeout after 30s".into()],
        );
        assert_eq!(card.suspected_surface, SuspectedSurface::Runtime);
        assert_eq!(card.status, "failed");
    }

    #[test]
    fn validate_rejects_empty_symptoms() {
        let card = RootCauseCard::new("run-1", "src.md");
        assert!(validate_card(&card).is_err());
    }
}
