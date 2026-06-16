use serde::{Deserialize, Serialize};

/// The surface a change targets. Exactly one per candidate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangeSurface {
    Prompt,
    Parser,
    Runtime,
    Gate,
    Model,
}

/// Quality dimension that a component can affect.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum QualityBucket {
    ExtractionFidelity,
    Coverage,
    CardQuality,
    CrystalProvenance,
    OperationalReliability,
    CostEfficiency,
}

/// Final decision on a candidate after scorecard evaluation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Accept,
    Reject,
    NeedsAblation,
    NeedsHumanReview,
}

/// Suspected failure surface in a root-cause card.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SuspectedSurface {
    Prompt,
    Parser,
    Runtime,
    Gate,
    Model,
    Unknown,
}

impl std::fmt::Display for ChangeSurface {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Prompt => write!(f, "prompt"),
            Self::Parser => write!(f, "parser"),
            Self::Runtime => write!(f, "runtime"),
            Self::Gate => write!(f, "gate"),
            Self::Model => write!(f, "model"),
        }
    }
}

impl std::fmt::Display for Decision {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Accept => write!(f, "accept"),
            Self::Reject => write!(f, "reject"),
            Self::NeedsAblation => write!(f, "needs_ablation"),
            Self::NeedsHumanReview => write!(f, "needs_human_review"),
        }
    }
}
