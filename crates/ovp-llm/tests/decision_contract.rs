//! Intentionally synthetic frozen contracts; no live vault/provider captures.
use std::collections::BTreeMap;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

use ovp_llm::decision::*;
use serde_json::{Value, json};

fn profile() -> DecisionProfile {
    DecisionProfile {
        id: "test-account".into(),
        provider: "typesafe".into(),
        endpoint: "https://api.typesafe.ai/v1/systemone".into(),
        model: "jev-1.13.0".into(),
        credential_ref: "OVP_TEST_DECISION_KEY".into(),
    }
}
fn request() -> DecisionRequest {
    DecisionRequest {
        namespace: "contract/v1".into(),
        state: json!({"quote":"possibly useful", "claim":"always useful"}),
        evidence: vec![EvidenceRef {
            source_id: "source-a".into(),
            revision: "sha-a".into(),
            start_line: 2,
            end_line: 4,
        }],
        questions: BTreeMap::from([
            (
                "supported".into(),
                DecisionQuestion {
                    instructions: "Does quote support claim?".into(),
                    kind: QuestionKind::Boolean {
                        yes: "supported".into(),
                        no: "not supported".into(),
                    },
                },
            ),
            (
                "relation".into(),
                DecisionQuestion {
                    instructions: "Which relation holds?".into(),
                    kind: QuestionKind::Choice {
                        options: BTreeMap::from([
                            ("supports".into(), "fully supports".into()),
                            ("overreach".into(), "stronger than evidence".into()),
                        ]),
                    },
                },
            ),
            (
                "quality".into(),
                DecisionQuestion {
                    instructions: "How useful is the evidence?".into(),
                    kind: QuestionKind::Score {
                        levels: vec![
                            ScoreLevel {
                                id: "none".into(),
                                description: "No usable evidence".into(),
                            },
                            ScoreLevel {
                                id: "direct".into(),
                                description: "Direct evidence".into(),
                            },
                        ],
                    },
                },
            ),
        ]),
    }
}
fn wire() -> Value {
    json!({"model":"jev-1.13.0", "usage":{"input_tokens":120,"output_tokens":10}, "answers":{
        "supported":{"type":"noul","noul":0.15},
        "relation":{"type":"choice","choice":"overreach","probabilities":{"supports":0.1,"overreach":0.9},"confidence":0.8},
        "quality":{"type":"score","score":0.7,"legend":{"0":"No usable evidence","1":"Direct evidence"},"probabilities":{"0":0.3,"1":0.7},"confidence":0.4}
    }})
}
fn decode(
    p: &DecisionProfile,
    q: &DecisionRequest,
    v: &Value,
) -> Result<DecisionReply, DecisionError> {
    typesafe::decode_reply(p, q, &serde_json::to_vec(v).unwrap(), 42, 1)
}

#[test]
fn all_primitives_preserve_meaning_and_ids() {
    let p = profile();
    let q = request();
    let body = typesafe::encode_request(&p, &q).unwrap();
    assert_eq!(body["state"], q.state);
    assert_eq!(body["questions"]["supported"]["type"], "noul");
    assert_eq!(
        body["questions"]["quality"]["criteria"],
        json!(["No usable evidence", "Direct evidence"])
    );
    let r = decode(&p, &q, &wire()).unwrap();
    assert_eq!(
        r.answers[&QuestionId::from("supported")],
        DecisionAnswer::Boolean {
            value: None,
            probability_true: Some(0.15)
        }
    );
    match &r.answers[&QuestionId::from("quality")] {
        DecisionAnswer::Score {
            position,
            probabilities,
            ..
        } => {
            assert_eq!(*position, 0.7);
            assert_eq!(
                probabilities.as_ref().unwrap()[&OptionId::from("direct")],
                0.7
            );
        }
        _ => panic!("expected score"),
    }
    assert_eq!(r.receipt.provider.model, "jev-1.13.0");
    assert_eq!(r.receipt.calibration, Calibration::ProviderClaimed);
}

#[test]
fn malformed_provider_responses_are_not_partial_successes() {
    let mut invalid = vec![];
    let mut v = wire();
    v["answers"].as_object_mut().unwrap().remove("supported");
    invalid.push(v);
    let mut v = wire();
    v["answers"]["extra"] = json!({"type":"noul","noul":0.5});
    invalid.push(v);
    for probability in [-0.01, 1.01] {
        let mut v = wire();
        v["answers"]["supported"]["noul"] = json!(probability);
        invalid.push(v);
    }
    let mut v = wire();
    v["answers"]["supported"] =
        json!({"type":"choice","choice":"overreach","probabilities":{},"confidence":0.5});
    invalid.push(v);
    let mut v = wire();
    v["answers"]["relation"]["choice"] = json!("fabricated-id");
    invalid.push(v);
    let mut v = wire();
    v["answers"]["relation"]["choice"] = json!("supports");
    invalid.push(v);
    let mut v = wire();
    v["answers"]["relation"]["probabilities"] = json!({"overreach":0.9});
    invalid.push(v);
    let mut v = wire();
    v["answers"]["relation"]["probabilities"]["supports"] = json!(0.3);
    invalid.push(v);
    let mut v = wire();
    v["answers"]["relation"]["confidence"] = json!(2);
    invalid.push(v);
    let mut v = wire();
    v["answers"]["quality"]["score"] = json!(0.4);
    invalid.push(v);
    let mut v = wire();
    v["answers"]["quality"]["probabilities"] = json!({"0":0.3,"2":0.7});
    invalid.push(v);
    let mut v = wire();
    v["answers"]["quality"]["legend"]["1"] = json!("wrong rubric");
    invalid.push(v);
    let mut v = wire();
    v["model"] = json!("jev-1.14.0");
    invalid.push(v);
    let mut v = wire();
    v.as_object_mut().unwrap().remove("usage");
    invalid.push(v);
    for v in invalid {
        assert!(decode(&profile(), &request(), &v).is_err());
    }
}

#[test]
fn duplicate_keys_nonfinite_numbers_and_private_echoes_are_rejected() {
    let body = serde_json::to_string(&wire()).unwrap();
    for raw in [
        body.replace("\"noul\":0.15", "\"noul\":0.15,\"noul\":0.99"),
        body.replace(
            "\"supported\":",
            "\"supported\":{\"type\":\"noul\",\"noul\":0.99},\"supported\":",
        ),
        body.replace("0.15", "NaN"),
        "{secret-private-source-body".into(),
    ] {
        let error =
            typesafe::decode_reply(&profile(), &request(), raw.as_bytes(), 0, 1).unwrap_err();
        assert!(!error.to_string().contains("secret-private"));
    }
    let mut r = decode(&profile(), &request(), &wire()).unwrap();
    r.answers.insert(
        "supported".into(),
        DecisionAnswer::Boolean {
            value: None,
            probability_true: Some(f64::NAN),
        },
    );
    assert!(r.validate(&profile(), &request()).is_err());
}

#[test]
fn request_validation_and_capability_negotiation_precede_calls() {
    let mut p = profile();
    p.model = "jev-latest".into();
    assert!(typesafe::encode_request(&p, &request()).is_err());
    let mut q = request();
    q.evidence[0].revision.clear();
    assert!(typesafe::encode_request(&profile(), &q).is_err());
    let mut q = request();
    q.questions
        .get_mut(&QuestionId::from("quality"))
        .unwrap()
        .kind = QuestionKind::Score { levels: vec![] };
    assert!(typesafe::encode_request(&profile(), &q).is_err());
    assert!(decode(&profile(), &q, &wire()).is_err());
    let mut caps = typesafe::CAPABILITIES;
    caps.batch = false;
    assert_eq!(
        request().validate(caps),
        Err(DecisionError::Unsupported("batch"))
    );
    caps.batch = true;
    caps.score = false;
    assert_eq!(
        request().validate(caps),
        Err(DecisionError::Unsupported("score"))
    );
}

#[test]
fn keys_isolate_provider_model_question_and_source_versions_not_key_rotation() {
    let p = profile();
    let q = request();
    let key = decision_key(&p, &q).unwrap();
    for field in ["id", "provider", "model", "endpoint"] {
        let mut changed = p.clone();
        match field {
            "id" => changed.id.push('2'),
            "provider" => changed.provider.push('2'),
            "model" => changed.model.push('2'),
            _ => changed.endpoint.push('2'),
        }
        assert_ne!(key, decision_key(&changed, &q).unwrap());
    }
    let mut p2 = p.clone();
    p2.credential_ref = "ROTATED_KEY".into();
    assert_eq!(key, decision_key(&p2, &q).unwrap());
    let mut q2 = q.clone();
    q2.namespace = "contract/v2".into();
    assert_ne!(key, decision_key(&p, &q2).unwrap());
    q2 = q.clone();
    q2.evidence[0].revision = "sha-b".into();
    assert_ne!(key, decision_key(&p, &q2).unwrap());
    q2 = q.clone();
    q2.state = json!({"claim":"always useful","quote":"possibly useful"});
    assert_eq!(key, decision_key(&p, &q2).unwrap());
}

struct Counting {
    profile: DecisionProfile,
    reply: DecisionReply,
    calls: Arc<AtomicUsize>,
}
impl DecisionClient for Counting {
    fn profile(&self) -> &DecisionProfile {
        &self.profile
    }
    fn capabilities(&self) -> DecisionCapabilities {
        typesafe::CAPABILITIES
    }
    fn decide(&mut self, _: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(self.reply.clone())
    }
}

#[test]
fn recording_replays_without_client_credentials_or_new_billable_attempts() {
    let dir = tempfile::tempdir().unwrap();
    let p = profile();
    let q = request();
    let calls = Arc::new(AtomicUsize::new(0));
    let inner = Counting {
        profile: p.clone(),
        reply: decode(&p, &q, &wire()).unwrap(),
        calls: calls.clone(),
    };
    let mut recorder = CachedDecisionClient::record(Box::new(inner), dir.path()).unwrap();
    let first = recorder.decide(&q).unwrap();
    let hit = recorder.decide(&q).unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(hit.receipt.origin, DecisionOrigin::Cache);
    assert_eq!(hit.receipt.network_attempts, 0);
    assert_eq!(hit.receipt.evaluation_usage, first.receipt.evaluation_usage);
    drop(recorder);
    let mut replay =
        CachedDecisionClient::replay(p.clone(), typesafe::CAPABILITIES, dir.path()).unwrap();
    let replayed = replay.decide(&q).unwrap();
    assert_eq!(first.answers, replayed.answers);
    assert_eq!(replayed.receipt.origin, DecisionOrigin::Replay);
    let mut changed = q.clone();
    changed.evidence[0].revision = "new".into();
    assert!(matches!(
        replay.decide(&changed),
        Err(DecisionError::CacheMiss { .. })
    ));
    let path = dir
        .path()
        .join(format!("{}.json", decision_key(&p, &q).unwrap()));
    let mut cassette: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    cassette["reply"]["receipt"]["provider"]["model"] = json!("other-model");
    std::fs::write(&path, serde_json::to_vec(&cassette).unwrap()).unwrap();
    assert_eq!(replay.decide(&q), Err(DecisionError::CorruptCassette));
    let inner = Counting {
        profile: p.clone(),
        reply: first,
        calls: calls.clone(),
    };
    let mut recorder = CachedDecisionClient::record(Box::new(inner), dir.path()).unwrap();
    assert_eq!(recorder.decide(&q), Err(DecisionError::CorruptCassette));
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

/// A different protocol might only return a typed label, with no probabilities.
/// The same consumer can use it without inventing calibrated confidence.
struct LabelOnly {
    profile: DecisionProfile,
}
impl DecisionClient for LabelOnly {
    fn profile(&self) -> &DecisionProfile {
        &self.profile
    }
    fn capabilities(&self) -> DecisionCapabilities {
        DecisionCapabilities {
            boolean: true,
            choice: false,
            score: false,
            batch: false,
            probabilities: false,
        }
    }
    fn decide(&mut self, q: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
        q.validate(self.capabilities())?;
        Ok(DecisionReply {
            answers: BTreeMap::from([(
                "supported".into(),
                DecisionAnswer::Boolean {
                    value: Some(false),
                    probability_true: None,
                },
            )]),
            receipt: DecisionReceipt {
                provider: self.profile.identity(),
                request_key: decision_key(&self.profile, q)?,
                question_namespace: q.namespace.clone(),
                evidence: q.evidence.clone(),
                calibration: Calibration::Unknown,
                confidence_semantics: None,
                evaluation_usage: None,
                evaluation_ms: 0,
                origin: DecisionOrigin::Fixture,
                network_attempts: 0,
            },
        })
    }
}
#[test]
fn consumer_accepts_another_provider_and_explicit_abstention() {
    fn consume(c: &mut dyn DecisionClient, q: &DecisionRequest) -> DecisionReply {
        let r = c.decide(q).unwrap();
        r.validate(c.profile(), q).unwrap();
        r
    }
    let mut q = request();
    q.questions.retain(|id, _| id.0 == "supported");
    let mut p = profile();
    p.provider = "label-only".into();
    p.model = "rules-v1".into();
    p.endpoint = "fixture://labels".into();
    let mut other = LabelOnly { profile: p.clone() };
    let mut r = consume(&mut other, &q);
    assert_eq!(r.receipt.calibration, Calibration::Unknown);
    r.answers.insert(
        "supported".into(),
        DecisionAnswer::Abstain {
            reason: AbstainReason::InsufficientEvidence,
        },
    );
    let mut fixture = FixtureDecisionClient::new(p, other.capabilities()).unwrap();
    fixture.insert(&q, r.clone()).unwrap();
    assert_eq!(consume(&mut fixture, &q).answers, r.answers);
}

#[test]
fn record_never_persists_invalid_inner_reply() {
    let dir = tempfile::tempdir().unwrap();
    let mut reply = decode(&profile(), &request(), &wire()).unwrap();
    reply.answers.clear();
    let inner = Counting {
        profile: profile(),
        reply,
        calls: Arc::new(AtomicUsize::new(0)),
    };
    let mut recorder = CachedDecisionClient::record(Box::new(inner), dir.path()).unwrap();
    assert!(recorder.decide(&request()).is_err());
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
}

#[test]
fn concurrent_recordings_only_return_the_durable_winner() {
    struct Racing {
        inner: Counting,
        barrier: Arc<std::sync::Barrier>,
    }
    impl DecisionClient for Racing {
        fn profile(&self) -> &DecisionProfile {
            self.inner.profile()
        }
        fn capabilities(&self) -> DecisionCapabilities {
            self.inner.capabilities()
        }
        fn decide(&mut self, q: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
            // Both cache misses must occur before either result is persisted.
            self.barrier.wait();
            self.inner.decide(q)
        }
    }
    let dir = tempfile::tempdir().unwrap();
    let barrier = Arc::new(std::sync::Barrier::new(2));
    let calls = Arc::new(AtomicUsize::new(0));
    let handles: Vec<_> = [0.1, 0.9]
        .into_iter()
        .map(|probability| {
            let mut response = wire();
            response["answers"]["supported"]["noul"] = json!(probability);
            let inner = Racing {
                inner: Counting {
                    profile: profile(),
                    reply: decode(&profile(), &request(), &response).unwrap(),
                    calls: calls.clone(),
                },
                barrier: barrier.clone(),
            };
            let mut recorder = CachedDecisionClient::record(Box::new(inner), dir.path()).unwrap();
            std::thread::spawn(move || recorder.decide(&request()))
        })
        .collect();
    let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
    assert_eq!(
        results
            .iter()
            .filter(|r| **r == Err(DecisionError::CacheConflict))
            .count(),
        1
    );
    let winner = results.into_iter().find_map(Result::ok).unwrap();
    let mut replay =
        CachedDecisionClient::replay(profile(), typesafe::CAPABILITIES, dir.path()).unwrap();
    assert_eq!(replay.decide(&request()).unwrap().answers, winner.answers);
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
}
