//! Capability-scoped routing. Domain consumers retain their baseline when
//! `applied` is absent. Factories are lazy: off/control never resolve credentials.
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::*;

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionMode {
    #[default]
    Off,
    Shadow,
    Enabled,
}
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionMode {
    Live,
    Record,
    #[default]
    Replay,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExperimentArm {
    Control,
    Candidate,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Experiment {
    pub id: String,
    pub seed: String,
    pub candidate_basis_points: u16,
}
impl Experiment {
    pub fn validate(&self) -> Result<(), DecisionError> {
        if self.id.trim().is_empty()
            || self.seed.trim().is_empty()
            || self.candidate_basis_points > 10000
        {
            return Err(DecisionError::InvalidRequest("invalid experiment"));
        }
        Ok(())
    }
    /// Versioned, length-delimited hash: stable across processes and platforms.
    pub fn assign(
        &self,
        capability: &str,
        unit_key: &str,
    ) -> Result<(ExperimentArm, u16), DecisionError> {
        self.validate()?;
        if unit_key.trim().is_empty() {
            return Err(DecisionError::InvalidRequest(
                "experiment unit key required",
            ));
        }
        let mut hash = Sha256::new();
        for field in [
            "ovp.decision.assignment/v1",
            &self.id,
            &self.seed,
            capability,
            unit_key,
        ] {
            hash.update((field.len() as u64).to_be_bytes());
            hash.update(field.as_bytes());
        }
        let digest = hash.finalize();
        let value = u64::from_be_bytes(digest[..8].try_into().unwrap());
        let bucket = (value % 10000) as u16;
        Ok((
            if bucket < self.candidate_basis_points {
                ExperimentArm::Candidate
            } else {
                ExperimentArm::Control
            },
            bucket,
        ))
    }
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CapabilityConfig {
    #[serde(default)]
    pub mode: DecisionMode,
    #[serde(default)]
    pub execution: ExecutionMode,
    pub profile: Option<String>,
    pub question_namespace: Option<String>,
    pub experiment: Option<Experiment>,
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionSettings {
    #[serde(default)]
    pub profiles: BTreeMap<String, DecisionProfile>,
    #[serde(default)]
    pub capabilities: BTreeMap<String, CapabilityConfig>,
}
/// Host owns supplier registration, secret resolution and construction.
/// Adding a supplier does not change routing or the evaluation runner.
pub trait DecisionClientFactory {
    fn supports(&self, profile: &DecisionProfile, execution: ExecutionMode) -> bool;
    fn build(
        &self,
        profile: &DecisionProfile,
        execution: ExecutionMode,
        cache: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError>;
}
impl DecisionSettings {
    pub fn parse(bytes: &[u8]) -> Result<Self, DecisionError> {
        let value = super::typesafe::strict_json(bytes)
            .map_err(|_| DecisionError::InvalidRequest("invalid or duplicate decision settings"))?;
        serde_json::from_value(value)
            .map_err(|_| DecisionError::InvalidRequest("invalid decision settings"))
    }
    pub fn validate(&self, factory: &dyn DecisionClientFactory) -> Result<(), DecisionError> {
        self.validate_structure()?;
        for profile in self.profiles.values() {
            if !factory.supports(profile, ExecutionMode::Replay) {
                return Err(DecisionError::Unsupported("provider"));
            }
        }
        for config in self.capabilities.values() {
            if let Some(id) = &config.profile
                && !factory.supports(&self.profiles[id], config.execution)
            {
                return Err(DecisionError::Unsupported("provider execution mode"));
            }
        }
        Ok(())
    }
    pub fn validate_structure(&self) -> Result<(), DecisionError> {
        for (id, profile) in &self.profiles {
            profile.validate()?;
            if id != &profile.id {
                return Err(DecisionError::InvalidRequest(
                    "profile map identity mismatch",
                ));
            }
        }
        for (name, config) in &self.capabilities {
            if !safe_name(name) {
                return Err(DecisionError::InvalidRequest("invalid capability name"));
            }
            if let Some(experiment) = &config.experiment {
                experiment.validate()?;
            }
            if let Some(id) = &config.profile {
                self.profiles
                    .get(id)
                    .ok_or(DecisionError::InvalidRequest("unknown profile"))?;
            }
            if config.mode != DecisionMode::Off
                && (config.profile.is_none()
                    || config
                        .question_namespace
                        .as_ref()
                        .is_none_or(|v| v.trim().is_empty()))
            {
                return Err(DecisionError::InvalidRequest(
                    "active capability needs profile and namespace",
                ));
            }
        }
        Ok(())
    }
}
fn safe_name(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObservationStatus {
    Off,
    Control,
    Shadow,
    Enabled,
    Abstained,
    Fallback,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecisionObservation {
    pub capability: String,
    pub configuration: CapabilityConfig,
    pub provider: Option<ProviderIdentity>,
    pub arm: Option<ExperimentArm>,
    pub bucket: Option<u16>,
    /// Correlates assignment units without retaining their raw identifiers.
    pub unit_key_sha256: Option<String>,
    pub status: ObservationStatus,
    /// Only enabled, candidate-assigned, complete non-abstaining results appear here.
    pub applied: Option<DecisionReply>,
    pub candidate: Option<DecisionReply>,
    pub error: Option<String>,
    pub elapsed_ms: u64,
    /// Errors may occur after billable attempts. Absence of a receipt is not zero cost.
    pub usage_unknown: bool,
}
/// Evaluate one capability, with a private cache namespace per capability.
/// The caller writes observations to its own experiment trace and uses baseline
/// whenever `applied` is None. No actions or admission writes occur here.
pub fn evaluate(
    settings: &DecisionSettings,
    factory: &dyn DecisionClientFactory,
    capability: &str,
    request: &DecisionRequest,
    unit_key: &str,
    cache_root: &Path,
) -> Result<DecisionObservation, DecisionError> {
    settings.validate(factory)?;
    let config = settings
        .capabilities
        .get(capability)
        .cloned()
        .ok_or(DecisionError::InvalidRequest("unknown capability"))?;
    let profile = config
        .profile
        .as_ref()
        .and_then(|id| settings.profiles.get(id));
    let mut out = DecisionObservation {
        capability: capability.into(),
        configuration: config.clone(),
        provider: profile.map(DecisionProfile::identity),
        arm: None,
        bucket: None,
        unit_key_sha256: None,
        status: ObservationStatus::Off,
        applied: None,
        candidate: None,
        error: None,
        elapsed_ms: 0,
        usage_unknown: false,
    };
    if config.mode == DecisionMode::Off {
        return Ok(out);
    }
    if request.namespace != *config.question_namespace.as_ref().unwrap() {
        return Err(DecisionError::InvalidRequest("question namespace mismatch"));
    }
    let (arm, bucket) = match &config.experiment {
        Some(experiment) => {
            let (arm, bucket) = experiment.assign(capability, unit_key)?;
            (arm, Some(bucket))
        }
        None => (ExperimentArm::Candidate, None),
    };
    out.arm = Some(arm);
    out.bucket = bucket;
    if config.experiment.is_some() {
        out.unit_key_sha256 = Some(format!("{:x}", Sha256::digest(unit_key.as_bytes())));
    }
    if arm == ExperimentArm::Control {
        out.status = ObservationStatus::Control;
        return Ok(out);
    }
    let start = Instant::now();
    let profile = profile.unwrap();
    let result = (|| {
        let mut client = factory.build(
            profile,
            config.execution,
            &PathBuf::from(cache_root).join(capability),
        )?;
        if client.profile().identity() != profile.identity() {
            return Err(DecisionError::InvalidReply("factory profile mismatch"));
        }
        request.validate(client.capabilities())?;
        let reply = client.decide(request)?;
        reply.validate(profile, request)?;
        Ok(reply)
    })();
    out.elapsed_ms = start.elapsed().as_millis().min(u64::MAX as u128) as u64;
    match result {
        Ok(reply) => {
            let abstained = reply
                .answers
                .values()
                .any(|a| matches!(a, DecisionAnswer::Abstain { .. }));
            out.status = if abstained {
                ObservationStatus::Abstained
            } else if config.mode == DecisionMode::Shadow {
                ObservationStatus::Shadow
            } else {
                ObservationStatus::Enabled
            };
            if out.status == ObservationStatus::Enabled {
                out.applied = Some(reply.clone());
            }
            out.usage_unknown = reply.receipt.origin == DecisionOrigin::Live
                && (reply.receipt.evaluation_usage.is_none() || reply.receipt.network_attempts > 1);
            out.candidate = Some(reply);
        }
        Err(error) => {
            out.status = ObservationStatus::Fallback;
            out.error = Some(error.to_string());
            out.usage_unknown = config.execution != ExecutionMode::Replay;
        }
    }
    Ok(out)
}
