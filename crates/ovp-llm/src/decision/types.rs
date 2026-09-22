use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

macro_rules! identifier {
    ($name:ident) => {
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(pub String);
        impl From<&str> for $name {
            fn from(value: &str) -> Self {
                Self(value.to_owned())
            }
        }
    };
}
identifier!(QuestionId);
identifier!(OptionId);

/// No credential values. `credential_ref` names a host-provided environment
/// variable; a different account should use a different profile id.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionProfile {
    pub id: String,
    pub provider: String,
    /// Full evaluation endpoint, not a base URL. Never embed credentials here.
    pub endpoint: String,
    /// A pinned version, not a moving alias. Adapters enforce vendor syntax.
    pub model: String,
    pub credential_ref: String,
}

impl DecisionProfile {
    pub fn validate(&self) -> Result<(), DecisionError> {
        if [
            &self.id,
            &self.provider,
            &self.endpoint,
            &self.model,
            &self.credential_ref,
        ]
        .iter()
        .any(|v| v.trim().is_empty() || v.trim() != v.as_str())
        {
            return Err(DecisionError::InvalidRequest(
                "empty or padded profile field",
            ));
        }
        if !self
            .credential_ref
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_')
        {
            return Err(DecisionError::InvalidRequest(
                "invalid credential reference",
            ));
        }
        Ok(())
    }

    /// Deliberately excludes the credential reference/value so rotating a secret
    /// does not change inference identity. Includes profile id for tenant isolation.
    pub fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            profile_id: self.id.clone(),
            provider: self.provider.clone(),
            endpoint: self.endpoint.clone(),
            model: self.model.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderIdentity {
    pub profile_id: String,
    pub provider: String,
    pub endpoint: String,
    pub model: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvidenceRef {
    pub source_id: String,
    pub revision: String,
    pub start_line: u32,
    pub end_line: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionRequest {
    /// Versioned domain question contract, e.g. `evidence_relevance/v1`.
    pub namespace: String,
    pub state: Value,
    pub evidence: Vec<EvidenceRef>,
    pub questions: BTreeMap<QuestionId, DecisionQuestion>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionQuestion {
    /// Full meaning belongs here: question ids are bookkeeping, not instructions.
    pub instructions: String,
    pub kind: QuestionKind,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum QuestionKind {
    Boolean { yes: String, no: String },
    Choice { options: BTreeMap<OptionId, String> },
    Score { levels: Vec<ScoreLevel> },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScoreLevel {
    pub id: OptionId,
    pub description: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionCapabilities {
    pub boolean: bool,
    pub choice: bool,
    pub score: bool,
    pub batch: bool,
    pub probabilities: bool,
}

impl DecisionRequest {
    pub fn validate(&self, caps: DecisionCapabilities) -> Result<(), DecisionError> {
        if self.namespace.trim().is_empty() || self.questions.is_empty() {
            return Err(DecisionError::InvalidRequest(
                "namespace and questions required",
            ));
        }
        if self.questions.len() > 1 && !caps.batch {
            return Err(DecisionError::Unsupported("batch"));
        }
        for e in &self.evidence {
            if e.source_id.is_empty()
                || e.revision.is_empty()
                || e.start_line == 0
                || e.end_line < e.start_line
            {
                return Err(DecisionError::InvalidRequest("invalid evidence reference"));
            }
        }
        for (id, q) in &self.questions {
            if id.0.trim().is_empty() || q.instructions.trim().is_empty() {
                return Err(DecisionError::InvalidRequest(
                    "question id and instructions required",
                ));
            }
            match &q.kind {
                QuestionKind::Boolean { .. } if !caps.boolean => {
                    return Err(DecisionError::Unsupported("boolean"));
                }
                QuestionKind::Choice { .. } if !caps.choice => {
                    return Err(DecisionError::Unsupported("choice"));
                }
                QuestionKind::Score { .. } if !caps.score => {
                    return Err(DecisionError::Unsupported("score"));
                }
                QuestionKind::Boolean { yes, no } => {
                    if yes.trim().is_empty() || no.trim().is_empty() {
                        return Err(DecisionError::InvalidRequest(
                            "boolean needs described outcomes",
                        ));
                    }
                }
                QuestionKind::Choice { options } => {
                    if options.len() < 2
                        || options.keys().any(|k| k.0.trim().is_empty())
                        || options.values().any(|v| v.trim().is_empty())
                    {
                        return Err(DecisionError::InvalidRequest(
                            "choice needs distinct nonempty options",
                        ));
                    }
                }
                QuestionKind::Score { levels } => {
                    let ids: BTreeSet<_> = levels.iter().map(|l| &l.id).collect();
                    if levels.len() < 2
                        || ids.len() != levels.len()
                        || levels
                            .iter()
                            .any(|l| l.id.0.trim().is_empty() || l.description.trim().is_empty())
                    {
                        return Err(DecisionError::InvalidRequest(
                            "score needs distinct described levels",
                        ));
                    }
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum DecisionAnswer {
    /// Probability and discrete label are independent provider capabilities.
    /// TypeSafe supplies probability only; the caller chooses its threshold.
    Boolean {
        value: Option<bool>,
        probability_true: Option<f64>,
    },
    Choice {
        selected: OptionId,
        probabilities: Option<BTreeMap<OptionId, f64>>,
        confidence: Option<f64>,
    },
    /// Expected zero-based rubric position, not an exact measured quantity.
    Score {
        position: f64,
        probabilities: Option<BTreeMap<OptionId, f64>>,
        confidence: Option<f64>,
    },
    Abstain {
        reason: AbstainReason,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AbstainReason {
    InsufficientEvidence,
    Uncertain,
    ProviderAbstained,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Calibration {
    Unknown,
    ProviderClaimed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionOrigin {
    Live,
    Fixture,
    Cache,
    Replay,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionReply {
    pub answers: BTreeMap<QuestionId, DecisionAnswer>,
    pub receipt: DecisionReceipt,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionReceipt {
    pub provider: ProviderIdentity,
    pub request_key: String,
    pub question_namespace: String,
    pub evidence: Vec<EvidenceRef>,
    pub calibration: Calibration,
    /// Vendor-specific label, not a portable threshold. None when absent.
    pub confidence_semantics: Option<String>,
    /// Usage of the original evaluation. Not fresh billable usage on replay.
    pub evaluation_usage: Option<DecisionUsage>,
    pub evaluation_ms: u64,
    pub origin: DecisionOrigin,
    /// Current invocation only. Cache/replay/fixtures always report zero.
    pub network_attempts: u32,
}

pub(crate) fn probability(p: f64) -> bool {
    p.is_finite() && (0.0..=1.0).contains(&p)
}

fn distribution(ps: &BTreeMap<OptionId, f64>, ids: &[OptionId]) -> bool {
    ps.len() == ids.len()
        && ids
            .iter()
            .all(|id| ps.get(id).is_some_and(|p| probability(*p)))
        && (ps.values().sum::<f64>() - 1.0).abs() <= 1e-5
}

impl DecisionReply {
    pub fn validate(
        &self,
        profile: &DecisionProfile,
        request: &DecisionRequest,
    ) -> Result<(), DecisionError> {
        profile.validate()?;
        request.validate(DecisionCapabilities {
            boolean: true,
            choice: true,
            score: true,
            batch: true,
            probabilities: true,
        })?;
        if self.receipt.provider != profile.identity()
            || self.receipt.request_key != super::decision_key(profile, request)?
            || self.receipt.question_namespace != request.namespace
            || self.receipt.evidence != request.evidence
        {
            return Err(DecisionError::InvalidReply("receipt identity mismatch"));
        }
        if self.answers.len() != request.questions.len()
            || self.answers.keys().ne(request.questions.keys())
        {
            return Err(DecisionError::InvalidReply("answer coverage mismatch"));
        }
        for (id, q) in &request.questions {
            let valid = match (&q.kind, &self.answers[id]) {
                (_, DecisionAnswer::Abstain { .. }) => true,
                (
                    QuestionKind::Boolean { .. },
                    DecisionAnswer::Boolean {
                        value,
                        probability_true,
                    },
                ) => {
                    (value.is_some() || probability_true.is_some())
                        && probability_true.is_none_or(probability)
                }
                (
                    QuestionKind::Choice { options },
                    DecisionAnswer::Choice {
                        selected,
                        probabilities,
                        confidence,
                    },
                ) => {
                    options.contains_key(selected)
                        && confidence.is_none_or(probability)
                        && probabilities.as_ref().is_none_or(|ps| {
                            distribution(ps, &options.keys().cloned().collect::<Vec<_>>())
                                && ps.values().all(|p| *p <= ps[selected] + 1e-6)
                        })
                }
                (
                    QuestionKind::Score { levels },
                    DecisionAnswer::Score {
                        position,
                        probabilities,
                        confidence,
                    },
                ) => {
                    position.is_finite()
                        && (0.0..=(levels.len() - 1) as f64).contains(position)
                        && confidence.is_none_or(probability)
                        && probabilities.as_ref().is_none_or(|ps| {
                            distribution(
                                ps,
                                &levels.iter().map(|l| l.id.clone()).collect::<Vec<_>>(),
                            ) && (levels
                                .iter()
                                .enumerate()
                                .map(|(i, l)| i as f64 * ps[&l.id])
                                .sum::<f64>()
                                - position)
                                .abs()
                                <= 1e-4
                        })
                }
                _ => false,
            };
            if !valid {
                return Err(DecisionError::InvalidReply(
                    "answer violates question contract",
                ));
            }
        }
        Ok(())
    }
}

/// Diagnostics deliberately omit response bodies, URLs and free-form provider
/// error text: those may echo private evidence or credentials.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecisionError {
    InvalidRequest(&'static str),
    InvalidReply(&'static str),
    Unsupported(&'static str),
    MissingCredential,
    Http(u16),
    Transport,
    Timeout,
    CacheMiss { key: String },
    CacheIo,
    CacheConflict,
    CorruptCassette,
}
impl std::fmt::Display for DecisionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "decision: {self:?}")
    }
}
impl std::error::Error for DecisionError {}
