use ovp_evolve::{
    candidate::CandidateSpec,
    decision_paired::{self, DecisionFixture},
    paired::RunConfig,
    types::Decision,
};
use ovp_llm::decision::{runtime::*, *};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap()
}
fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.path().is_dir() {
            copy_dir(&entry.path(), &target)
        } else {
            std::fs::copy(entry.path(), target).unwrap();
        }
    }
}
struct Setup {
    _dir: tempfile::TempDir,
    path: PathBuf,
    spec: CandidateSpec,
    fixture: PathBuf,
}
impl Setup {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".run");
        std::fs::create_dir(&path).unwrap();
        let fixture = path.join("input");
        copy_dir(&root().join("fixtures/decision-paired-v1"), &fixture);
        let mut spec = CandidateSpec::load(
            &root().join("evolution/candidates/decision-experiment-runtime-v1.json"),
        )
        .unwrap();
        spec.eval_plan.decision_run.as_mut().unwrap().fixture_dir = fixture.clone();
        Self {
            _dir: dir,
            path,
            spec,
            fixture,
        }
    }
    fn config(&self) -> RunConfig {
        let candidate = self.path.join("candidate.json");
        std::fs::write(&candidate, serde_json::to_vec(&self.spec).unwrap()).unwrap();
        RunConfig {
            candidate,
            registry: root().join("evolution/components.json"),
            output: self.path.join("result"),
            executable: std::env::current_exe().unwrap(),
        }
    }
    fn manifest(&self) -> Value {
        serde_json::from_slice(&std::fs::read(self.path.join("result/manifest.json")).unwrap())
            .unwrap()
    }
    fn cassette(&self, arm: &str) -> PathBuf {
        std::fs::read_dir(
            self.fixture
                .join("cassettes")
                .join(arm)
                .join("runtime_smoke"),
        )
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path()
    }
    fn mutate_cassettes(&self, arm: &str, mut f: impl FnMut(&mut Value)) {
        for entry in std::fs::read_dir(
            self.fixture
                .join("cassettes")
                .join(arm)
                .join("runtime_smoke"),
        )
        .unwrap()
        {
            let path = entry.unwrap().path();
            let mut v: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
            f(&mut v);
            std::fs::write(path, serde_json::to_vec(&v).unwrap()).unwrap();
        }
    }
}
#[test]
fn executes_both_frozen_arms_and_reports_per_question_without_auto_promotion() {
    let s = Setup::new();
    assert_eq!(
        decision_paired::run(s.config()).unwrap(),
        Decision::NeedsHumanReview
    );
    let m = s.manifest();
    assert_eq!(m["status"], "completed");
    assert_eq!(m["comparison"]["accuracy_delta"], 0.5);
    assert_eq!(m["comparison"]["questions"].as_array().unwrap().len(), 12);
    assert_eq!(m["arms"]["candidate"]["metrics"]["network_attempts"], 0);
    assert!(m["accepted_without_quote"].is_null());
    assert_eq!(m["admission_gate_scope"], "not_exercised");
    assert!(
        m["comparison"]["buckets"]
            .as_array()
            .unwrap()
            .iter()
            .all(|b| b["small_bucket"] == true)
    );
    let ledger =
        std::fs::read_to_string(s.path.join("result/.ovp/evolution-ledger.jsonl")).unwrap();
    assert!(ledger.contains("needs_human_review"));
    assert!(
        decision_paired::run(s.config()).is_err(),
        "must not overwrite an experiment"
    );
}
#[test]
fn replay_miss_and_incomplete_reply_are_invalid_not_baseline_success() {
    for missing in [true, false] {
        let s = Setup::new();
        if missing {
            std::fs::remove_file(s.cassette("candidate")).unwrap();
        } else {
            s.mutate_cassettes("candidate", |v| {
                v["reply"]["answers"]
                    .as_object_mut()
                    .unwrap()
                    .remove("relation");
            });
        }
        assert!(decision_paired::run(s.config()).is_err());
        let m = s.manifest();
        assert_eq!(m["status"], "invalid");
        assert_eq!(m["arms"]["candidate"]["valid"], false);
        assert_eq!(m["arms"]["candidate"]["metrics"]["fallback_cases"], 1);
        assert!(m["comparison"].is_null());
    }
}
#[test]
fn absent_threshold_invalidates_probability_interpretation() {
    let mut s = Setup::new();
    s.spec
        .eval_plan
        .decision_run
        .as_mut()
        .unwrap()
        .candidate
        .boolean_thresholds
        .clear();
    assert!(decision_paired::run(s.config()).is_err());
    assert_eq!(s.manifest()["status"], "invalid");
}
#[test]
fn replay_historical_tokens_are_not_charged_again() {
    let s = Setup::new();
    s.mutate_cassettes("candidate", |v| {
        v["reply"]["receipt"]["evaluation_usage"] = json!({"input_tokens":100,"output_tokens":10})
    });
    decision_paired::run(s.config()).unwrap();
    let m = s.manifest();
    let metrics = &m["arms"]["candidate"]["metrics"];
    assert_eq!(metrics["live_input_tokens"], 0);
    assert_eq!(metrics["historical_input_tokens"], 400);
    assert_eq!(metrics["historical_output_tokens"], 40);
}
#[test]
fn abstention_is_visible_and_full_denominator_prevents_inflated_accuracy() {
    let mut s = Setup::new();
    s.spec
        .eval_plan
        .decision_run
        .as_mut()
        .unwrap()
        .min_accuracy_delta = 0.5;
    s.mutate_cassettes("candidate", |v| {
        v["reply"]["answers"]["supported"] = json!({"type":"abstain","reason":"uncertain"})
    });
    assert_eq!(decision_paired::run(s.config()).unwrap(), Decision::Reject);
    let m = s.manifest();
    assert_eq!(m["arms"]["candidate"]["metrics"]["abstained"], 4);
    assert_eq!(m["arms"]["candidate"]["metrics"]["questions"], 12);
}
#[test]
fn wrong_predictions_are_measured_as_quality_regressions() {
    let s = Setup::new();
    s.mutate_cassettes("candidate", |v| {
        v["reply"]["answers"]["supported"]["probability_true"] = json!(0.1)
    });
    assert_eq!(decision_paired::run(s.config()).unwrap(), Decision::Reject);
    assert_eq!(s.manifest()["status"], "completed");
}
#[test]
fn source_tampering_coverage_and_holdout_tuning_are_rejected_before_execution() {
    let s = Setup::new();
    let mut config = s.config();
    config.output = s.fixture.join(".run/should-not-be-created/result");
    assert!(decision_paired::run(config).is_err());
    assert!(!s.fixture.join(".run").exists());
    let mut s = Setup::new();
    s.spec.eval_plan.decision_run.as_mut().unwrap().split = "holdout".into();
    assert!(decision_paired::run(s.config()).is_err());
    let s = Setup::new();
    let path = s.fixture.join("cases.json");
    let mut v: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    v["sources"]["source-0"]["text"] = json!("changed");
    std::fs::write(&path, serde_json::to_vec(&v).unwrap()).unwrap();
    assert!(decision_paired::run(s.config()).is_err());
    assert!(!s.path.join("result").exists());
    let mut s = Setup::new();
    s.spec
        .eval_plan
        .decision_run
        .as_mut()
        .unwrap()
        .expected_questions = 13;
    assert!(decision_paired::run(s.config()).is_err());
}
struct Supplier {
    builds: AtomicUsize,
    fixture: DecisionFixture,
    unknown: bool,
}
impl DecisionClientFactory for Supplier {
    fn supports(&self, p: &DecisionProfile, _: ExecutionMode) -> bool {
        p.provider == "substitute"
    }
    fn build(
        &self,
        p: &DecisionProfile,
        _: ExecutionMode,
        _: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError> {
        self.builds.fetch_add(1, Ordering::SeqCst);
        let mut client = FixtureDecisionClient::new(
            p.clone(),
            DecisionCapabilities {
                boolean: true,
                choice: true,
                score: true,
                batch: true,
                probabilities: false,
            },
        )?;
        for case in &self.fixture.cases {
            let answers = case
                .request
                .questions
                .iter()
                .map(|(id, q)| {
                    (
                        id.clone(),
                        match &q.kind {
                            QuestionKind::Boolean { .. } => DecisionAnswer::Boolean {
                                value: Some(true),
                                probability_true: None,
                            },
                            QuestionKind::Choice { .. } => DecisionAnswer::Choice {
                                selected: "supports".into(),
                                probabilities: None,
                                confidence: None,
                            },
                            QuestionKind::Score { .. } => DecisionAnswer::Score {
                                position: 1.0,
                                probabilities: None,
                                confidence: None,
                            },
                        },
                    )
                })
                .collect();
            let reply = DecisionReply {
                answers,
                receipt: DecisionReceipt {
                    provider: p.identity(),
                    request_key: decision_key(p, &case.request)?,
                    question_namespace: case.request.namespace.clone(),
                    evidence: case.request.evidence.clone(),
                    calibration: Calibration::Unknown,
                    confidence_semantics: None,
                    evaluation_usage: if self.unknown {
                        None
                    } else {
                        Some(DecisionUsage {
                            input_tokens: 2,
                            output_tokens: 1,
                        })
                    },
                    evaluation_ms: 0,
                    origin: DecisionOrigin::Live,
                    network_attempts: 1,
                },
            };
            client.insert(&case.request, reply)?;
        }
        // Wrapper represents a distinct live protocol, without real networking.
        struct Live {
            inner: FixtureDecisionClient,
        }
        impl DecisionClient for Live {
            fn profile(&self) -> &DecisionProfile {
                self.inner.profile()
            }
            fn capabilities(&self) -> DecisionCapabilities {
                self.inner.capabilities()
            }
            fn decide(&mut self, q: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
                let mut r = self.inner.decide(q)?;
                r.receipt.origin = DecisionOrigin::Live;
                r.receipt.network_attempts = 1;
                Ok(r)
            }
        }
        Ok(Box::new(Live { inner: client }))
    }
}
#[test]
fn replacement_provider_uses_same_runner_and_budget_stops_further_calls() {
    for unknown in [false, true] {
        let mut s = Setup::new();
        let plan = s.spec.eval_plan.decision_run.as_mut().unwrap();
        for p in plan.profiles.values_mut() {
            p.provider = "substitute".into();
        }
        plan.control.execution = ExecutionMode::Live;
        plan.candidate.execution = ExecutionMode::Live;
        plan.budget.max_observed_input_tokens_per_arm = 1;
        let factory = Supplier {
            builds: AtomicUsize::new(0),
            fixture: serde_json::from_slice(&std::fs::read(s.fixture.join("cases.json")).unwrap())
                .unwrap(),
            unknown,
        };
        assert!(decision_paired::run_with_factory(s.config(), &factory).is_err());
        assert_eq!(
            factory.builds.load(Ordering::SeqCst),
            2,
            "one call per arm then stop"
        );
        assert_eq!(s.manifest()["status"], "invalid");
    }
    let mut s = Setup::new();
    let plan = s.spec.eval_plan.decision_run.as_mut().unwrap();
    for p in plan.profiles.values_mut() {
        p.provider = "substitute".into();
    }
    plan.control.execution = ExecutionMode::Live;
    plan.candidate.execution = ExecutionMode::Live;
    plan.min_accuracy_delta = 0.0;
    let factory = Supplier {
        builds: AtomicUsize::new(0),
        fixture: serde_json::from_slice(&std::fs::read(s.fixture.join("cases.json")).unwrap())
            .unwrap(),
        unknown: false,
    };
    assert_eq!(
        decision_paired::run_with_factory(s.config(), &factory).unwrap(),
        Decision::NeedsHumanReview
    );
    assert_eq!(
        s.manifest()["arms"]["candidate"]["metrics"]["live_input_tokens"],
        8
    );
}
