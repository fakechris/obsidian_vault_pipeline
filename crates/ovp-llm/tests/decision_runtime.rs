use ovp_llm::decision::{runtime::*, *};
use serde_json::json;
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};

fn request() -> DecisionRequest {
    DecisionRequest {
        namespace: "test/v1".into(),
        state: json!({"quote":"synthetic"}),
        evidence: vec![],
        questions: BTreeMap::from([(
            "q".into(),
            DecisionQuestion {
                instructions: "Supported?".into(),
                kind: QuestionKind::Boolean {
                    yes: "yes".into(),
                    no: "no".into(),
                },
            },
        )]),
    }
}
fn settings(mode: DecisionMode) -> DecisionSettings {
    let profile = DecisionProfile {
        id: "p".into(),
        provider: "test".into(),
        endpoint: "fixture://test".into(),
        model: "test-v1".into(),
        credential_ref: "UNUSED".into(),
    };
    DecisionSettings {
        profiles: BTreeMap::from([("p".into(), profile)]),
        capabilities: BTreeMap::from([(
            "strength".into(),
            CapabilityConfig {
                mode,
                execution: ExecutionMode::Replay,
                profile: Some("p".into()),
                question_namespace: Some("test/v1".into()),
                experiment: None,
            },
        )]),
    }
}
struct Factory {
    builds: AtomicUsize,
    fail: bool,
    abstain: bool,
}
impl Factory {
    fn new() -> Self {
        Self {
            builds: AtomicUsize::new(0),
            fail: false,
            abstain: false,
        }
    }
}
impl DecisionClientFactory for Factory {
    fn supports(&self, p: &DecisionProfile, _: ExecutionMode) -> bool {
        p.provider == "test"
    }
    fn build(
        &self,
        p: &DecisionProfile,
        _: ExecutionMode,
        _: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError> {
        self.builds.fetch_add(1, Ordering::SeqCst);
        if self.fail {
            return Err(DecisionError::MissingCredential);
        }
        let q = request();
        let reply = DecisionReply {
            answers: BTreeMap::from([(
                "q".into(),
                if self.abstain {
                    DecisionAnswer::Abstain {
                        reason: AbstainReason::Uncertain,
                    }
                } else {
                    DecisionAnswer::Boolean {
                        value: Some(true),
                        probability_true: None,
                    }
                },
            )]),
            receipt: DecisionReceipt {
                provider: p.identity(),
                request_key: decision_key(p, &q)?,
                question_namespace: q.namespace.clone(),
                evidence: q.evidence.clone(),
                calibration: Calibration::Unknown,
                confidence_semantics: None,
                evaluation_usage: None,
                evaluation_ms: 0,
                origin: DecisionOrigin::Fixture,
                network_attempts: 0,
            },
        };
        let mut c = FixtureDecisionClient::new(
            p.clone(),
            DecisionCapabilities {
                boolean: true,
                choice: false,
                score: false,
                batch: false,
                probabilities: false,
            },
        )?;
        c.insert(&q, reply)?;
        Ok(Box::new(c))
    }
}
fn eval(s: &DecisionSettings, f: &Factory) -> Result<DecisionObservation, DecisionError> {
    evaluate(
        s,
        f,
        "strength",
        &request(),
        "session-123",
        Path::new("unused"),
    )
}
#[test]
fn off_and_control_never_construct_clients() {
    let f = Factory {
        fail: true,
        ..Factory::new()
    };
    let mut s = settings(DecisionMode::Off);
    assert_eq!(eval(&s, &f).unwrap().status, ObservationStatus::Off);
    s.capabilities.get_mut("strength").unwrap().mode = DecisionMode::Enabled;
    s.capabilities.get_mut("strength").unwrap().experiment = Some(Experiment {
        id: "exp".into(),
        seed: "seed".into(),
        candidate_basis_points: 0,
    });
    let o = eval(&s, &f).unwrap();
    assert_eq!(o.status, ObservationStatus::Control);
    assert!(o.applied.is_none());
    assert_eq!(f.builds.load(Ordering::SeqCst), 0);
}
#[test]
fn shadow_observes_enabled_applies_and_off_immediately_reverts() {
    let f = Factory::new();
    let mut s = settings(DecisionMode::Shadow);
    let o = eval(&s, &f).unwrap();
    assert!(o.candidate.is_some());
    assert!(o.applied.is_none());
    assert_eq!(o.status, ObservationStatus::Shadow);
    s.capabilities.get_mut("strength").unwrap().mode = DecisionMode::Enabled;
    assert!(eval(&s, &f).unwrap().applied.is_some());
    s.capabilities.get_mut("strength").unwrap().mode = DecisionMode::Off;
    assert!(eval(&s, &f).unwrap().applied.is_none());
    assert_eq!(f.builds.load(Ordering::SeqCst), 2);
}
#[test]
fn missing_credentials_and_abstention_retain_baseline() {
    let s = settings(DecisionMode::Enabled);
    let failed = eval(
        &s,
        &Factory {
            fail: true,
            ..Factory::new()
        },
    )
    .unwrap();
    assert_eq!(failed.status, ObservationStatus::Fallback);
    assert!(failed.applied.is_none());
    assert!(failed.candidate.is_none());
    assert!(failed.error.unwrap().contains("MissingCredential"));
    let o = eval(
        &s,
        &Factory {
            abstain: true,
            ..Factory::new()
        },
    )
    .unwrap();
    assert_eq!(o.status, ObservationStatus::Abstained);
    assert!(o.applied.is_none());
    assert!(o.candidate.is_some());
}
#[test]
fn strict_settings_and_independent_capabilities() {
    assert!(
        DecisionSettings::parse(br#"{"capabilities":{"x":{"mode":"off","mode":"enabled"}}}"#)
            .is_err()
    );
    let f = Factory::new();
    let mut s = settings(DecisionMode::Enabled);
    s.capabilities
        .insert("relevance".into(), CapabilityConfig::default());
    let o = evaluate(&s, &f, "relevance", &request(), "", Path::new("unused")).unwrap();
    assert_eq!(o.status, ObservationStatus::Off);
    assert!(eval(&s, &f).unwrap().applied.is_some());
    s.profiles.get_mut("p").unwrap().provider = "typo".into();
    assert!(eval(&s, &f).is_err());
    assert!(
        serde_json::from_value::<DecisionSettings>(json!({"capabilities":{"x":{"mode":"shdaow"}}}))
            .is_err()
    );
    assert!(serde_json::from_value::<DecisionSettings>(json!({"capabilites":{}})).is_err());
    s = settings(DecisionMode::Enabled);
    s.capabilities
        .get_mut("strength")
        .unwrap()
        .question_namespace = Some("wrong/v1".into());
    assert!(eval(&s, &f).is_err());
}
#[test]
fn bucketing_is_stable_and_changes_only_with_assignment_identity() {
    let exp = Experiment {
        id: "trial-v1".into(),
        seed: "seed".into(),
        candidate_basis_points: 5000,
    };
    let a = exp.assign("strength", "user-123").unwrap();
    assert_eq!(
        a,
        (ExperimentArm::Candidate, 4350),
        "frozen cross-platform assignment contract"
    );
    for _ in 0..100 {
        assert_eq!(a, exp.assign("strength", "user-123").unwrap());
    }
    let buckets: BTreeMap<_, _> = (0..100)
        .map(|i| (i, exp.assign("strength", &format!("user-{i}")).unwrap().1))
        .collect();
    assert!(buckets.values().any(|b| *b < 5000));
    assert!(buckets.values().any(|b| *b >= 5000));
    let other = Experiment {
        id: "trial-v2".into(),
        ..exp.clone()
    };
    assert_ne!(a.1, other.assign("strength", "user-123").unwrap().1);
    assert!(exp.assign("strength", "").is_err());
    assert!(
        Experiment {
            candidate_basis_points: 10001,
            ..exp
        }
        .validate()
        .is_err()
    );
}
