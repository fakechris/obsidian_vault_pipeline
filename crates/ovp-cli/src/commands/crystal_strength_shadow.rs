//! Optional typed-decision observer. It cannot produce admission verdicts.
use ovp_app::decisions::{BuiltinDecisionFactory, load_settings};
use ovp_domain::crystal::synth::UnitsCatalog;
use ovp_domain::crystal::{ClaimStrengthVerdict, CrystalCandidate};
use ovp_domain::decision_strength::{self, ShadowJudgment};
use ovp_llm::decision::runtime::{DecisionClientFactory, DecisionMode, DecisionSettings, evaluate};
use serde_json::{Value, json};
use std::path::Path;

const CAPABILITY: &str = "strength_check";

pub fn observe(
    vault_root: Option<&Path>,
    work_dir: &Path,
    candidate: &CrystalCandidate,
    catalog: &UnitsCatalog,
    baseline: &[ClaimStrengthVerdict],
) {
    let Some(vault) = vault_root else {
        return;
    };
    let path = vault.join(".ovp/decisions.json");
    let settings = match std::fs::metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return,
        Err(e) => {
            eprintln!("strength shadow: cannot stat settings: {e}");
            return;
        }
        Ok(meta) if meta.len() > 1024 * 1024 => {
            eprintln!("strength shadow: settings too large");
            return;
        }
        Ok(_) => match load_settings(&path) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("strength shadow: settings unavailable: {e}");
                return;
            }
        },
    };
    observe_with(
        &settings,
        &BuiltinDecisionFactory,
        vault,
        work_dir,
        candidate,
        catalog,
        baseline,
    );
}

fn observe_with(
    settings: &DecisionSettings,
    factory: &dyn DecisionClientFactory,
    vault: &Path,
    work_dir: &Path,
    candidate: &CrystalCandidate,
    catalog: &UnitsCatalog,
    baseline: &[ClaimStrengthVerdict],
) {
    let Some(config) = settings.capabilities.get(CAPABILITY) else {
        return;
    };
    if config.mode == DecisionMode::Off {
        return;
    }
    let mut trace = json!({"schema":"ovp.decision.strength_shadow/v1","configuration":config,"status":"shadow","claims":[]});
    if config.mode != DecisionMode::Shadow {
        trace["status"] = json!("unsupported_enabled_mode");
        persist(work_dir, &trace);
        return;
    }
    // One batch can cite multiple articles. An online arm assignment here
    // could split the same source across control/candidate; require a future
    // source-grouped runner instead of making a misleading A/B claim.
    if config.experiment.is_some() {
        trace["status"] = json!("experiment_requires_source_partition");
        persist(work_dir, &trace);
        return;
    }
    let mut records = Vec::new();
    for (batch, claims) in candidate
        .items
        .chunks(decision_strength::MAX_CLAIMS)
        .enumerate()
    {
        let sub = CrystalCandidate {
            items: claims.to_vec(),
        };
        let baseline_batch = claims
            .iter()
            .map(|c| baseline.iter().find(|v| v.claim_id == c.id))
            .collect::<Vec<_>>();
        let mut record = json!({"batch":batch,"claim_ids":claims.iter().map(|c|&c.id).collect::<Vec<_>>(),"baseline":baseline_batch,"status":"fallback"});
        let result = (|| {
            if config.question_namespace.as_deref() != Some(decision_strength::NAMESPACE) {
                return Err("strength namespace mismatch".to_string());
            }
            let request = decision_strength::request(&sub, catalog).map_err(|e| e.to_string())?;
            record["request"] = json!(request);
            let observation = evaluate(
                settings,
                factory,
                CAPABILITY,
                &request,
                &format!(
                    "crystal-strength:{}",
                    claims
                        .iter()
                        .map(|c| c.id.as_str())
                        .collect::<Vec<_>>()
                        .join("|")
                ),
                &vault.join(".ovp/cassettes/decisions"),
            )
            .map_err(|e| e.to_string())?;
            record["observation"] = json!(observation);
            record["status"] = json!(observation.status);
            if observation.applied.is_some() {
                return Err("shadow attempted to apply verdict".into());
            }
            if let Some(reply) = observation.candidate.as_ref() {
                let judged: Vec<ShadowJudgment> =
                    decision_strength::judgments(&sub, reply).map_err(|e| e.to_string())?;
                let comparisons: Vec<Value> = judged.iter().map(|j| {
                    let base = baseline.iter().find(|v| v.claim_id == j.claim_id);
                    json!({"claim_id":j.claim_id,"existing_strength":base.map(|b|b.strength),"existing_sufficient":base.map(|b|b.evidence_sufficient),"candidate_strength":j.strength,"candidate_sufficient":j.evidence_sufficient,"disagrees":base.is_some_and(|b|b.strength!=j.strength || b.evidence_sufficient!=j.evidence_sufficient),"requires_review":true})
                }).collect();
                record["comparisons"] = json!(comparisons);
            }
            Ok::<(), String>(())
        })();
        if let Err(e) = result {
            record["status"] = json!("fallback");
            record["error"] = json!(e);
        }
        records.push(record);
    }
    trace["claims"] = json!(records);
    persist(work_dir, &trace);
}
fn persist(work_dir: &Path, trace: &Value) {
    use std::io::Write;
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let path = work_dir.join(format!(
        "decision-strength-shadow-{stamp}-{}-{}.json",
        std::process::id(),
        SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    ));
    let result = (|| -> std::io::Result<()> {
        std::fs::create_dir_all(work_dir)?;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&path)?;
        file.write_all(&serde_json::to_vec_pretty(trace)?)?;
        file.sync_all()
    })();
    if let Err(e) = result {
        eprintln!("strength shadow: trace unavailable: {e}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::synth::{CatalogCase, CatalogUnit};
    use ovp_domain::crystal::{Citation, CrystalClaim, StrengthClass};
    use ovp_llm::decision::runtime::{CapabilityConfig, ExecutionMode};
    use ovp_llm::decision::*;
    use std::collections::BTreeMap;
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Factory(AtomicUsize);
    struct Client(DecisionProfile);
    impl DecisionClient for Client {
        fn profile(&self) -> &DecisionProfile {
            &self.0
        }
        fn capabilities(&self) -> DecisionCapabilities {
            DecisionCapabilities {
                boolean: true,
                choice: true,
                score: false,
                batch: true,
                probabilities: false,
            }
        }
        fn decide(&mut self, request: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
            let answers = request
                .questions
                .keys()
                .map(|id| {
                    (
                        id.clone(),
                        if id.0.ends_with("_class") {
                            DecisionAnswer::Choice {
                                selected: "overreach".into(),
                                probabilities: None,
                                confidence: None,
                            }
                        } else {
                            DecisionAnswer::Boolean {
                                value: Some(false),
                                probability_true: None,
                            }
                        },
                    )
                })
                .collect();
            Ok(DecisionReply {
                answers,
                receipt: DecisionReceipt {
                    provider: self.0.identity(),
                    request_key: decision_key(&self.0, request)?,
                    question_namespace: request.namespace.clone(),
                    evidence: request.evidence.clone(),
                    calibration: Calibration::Unknown,
                    confidence_semantics: None,
                    evaluation_usage: None,
                    evaluation_ms: 1,
                    origin: DecisionOrigin::Fixture,
                    network_attempts: 0,
                },
            })
        }
    }
    impl DecisionClientFactory for Factory {
        fn supports(&self, _: &DecisionProfile, _: ExecutionMode) -> bool {
            true
        }
        fn build(
            &self,
            p: &DecisionProfile,
            _: ExecutionMode,
            _: &Path,
        ) -> Result<Box<dyn DecisionClient>, DecisionError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(Client(p.clone())))
        }
    }
    fn inputs(
        mode: DecisionMode,
    ) -> (
        DecisionSettings,
        CrystalCandidate,
        UnitsCatalog,
        Vec<ClaimStrengthVerdict>,
    ) {
        let settings = DecisionSettings {
            profiles: BTreeMap::from([(
                "p".into(),
                DecisionProfile {
                    id: "p".into(),
                    provider: "fixture".into(),
                    endpoint: "fixture://strength".into(),
                    model: "synthetic-v1".into(),
                    credential_ref: "UNUSED".into(),
                },
            )]),
            capabilities: BTreeMap::from([(
                CAPABILITY.into(),
                CapabilityConfig {
                    mode,
                    execution: ExecutionMode::Replay,
                    profile: Some("p".into()),
                    question_namespace: Some(decision_strength::NAMESPACE.into()),
                    experiment: None,
                },
            )]),
        };
        let candidate = CrystalCandidate {
            items: vec![CrystalClaim {
                id: "real-id".into(),
                claim: "All studies prove it".into(),
                theme: "x".into(),
                citations: vec![Citation {
                    case_id: "c".into(),
                    unit_id: "u".into(),
                    quote: "Some studies suggest it".into(),
                    claimed_line: None,
                }],
                caveat: None,
            }],
        };
        let catalog = UnitsCatalog {
            cases: BTreeMap::from([(
                "c".into(),
                CatalogCase {
                    title: "source".into(),
                    units: vec![CatalogUnit {
                        unit_id: "u".into(),
                        line: Some(1),
                        quote: "Some studies suggest it".into(),
                        attribution: "author".into(),
                        modality: "suggested".into(),
                    }],
                },
            )]),
        };
        let baseline = vec![ClaimStrengthVerdict {
            claim_id: "real-id".into(),
            strength: StrengthClass::Supported,
            evidence_sufficient: true,
            rationale: "existing judge reason".into(),
        }];
        (settings, candidate, catalog, baseline)
    }
    fn last_trace(dir: &Path) -> Option<Value> {
        let mut files = std::fs::read_dir(dir)
            .ok()?
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("decision-strength-shadow-")
            })
            .collect::<Vec<_>>();
        files.sort();
        files
            .last()
            .map(|p| serde_json::from_slice(&std::fs::read(p).unwrap()).unwrap())
    }
    #[test]
    fn off_shadow_and_invalid_evidence_never_change_existing_verdict() {
        let tmp = tempfile::tempdir().unwrap();
        let f = Factory(AtomicUsize::new(0));
        let (mut s, mut c, catalog, baseline) = inputs(DecisionMode::Off);
        observe_with(&s, &f, tmp.path(), tmp.path(), &c, &catalog, &baseline);
        assert!(last_trace(tmp.path()).is_none());
        assert_eq!(f.0.load(Ordering::SeqCst), 0);
        s.capabilities.get_mut(CAPABILITY).unwrap().mode = DecisionMode::Shadow;
        observe_with(&s, &f, tmp.path(), tmp.path(), &c, &catalog, &baseline);
        let trace = last_trace(tmp.path()).unwrap();
        assert_eq!(trace["claims"][0]["status"], "shadow");
        assert_eq!(trace["claims"][0]["comparisons"][0]["disagrees"], true);
        assert_eq!(trace["claims"][0]["comparisons"][0]["claim_id"], "real-id");
        assert_eq!(
            trace["claims"][0]["baseline"][0]["rationale"],
            "existing judge reason"
        );
        assert!(
            trace["claims"][0]["comparisons"][0]
                .get("rationale")
                .is_none()
        );
        assert_eq!(baseline[0].strength, StrengthClass::Supported);
        c.items[0].citations[0].quote = "invented".into();
        observe_with(&s, &f, tmp.path(), tmp.path(), &c, &catalog, &baseline);
        let failed = last_trace(tmp.path()).unwrap();
        assert_eq!(failed["claims"][0]["status"], "fallback");
        assert_eq!(f.0.load(Ordering::SeqCst), 1);
        s.capabilities.get_mut(CAPABILITY).unwrap().experiment =
            Some(ovp_llm::decision::runtime::Experiment {
                id: "e".into(),
                seed: "s".into(),
                candidate_basis_points: 5000,
            });
        observe_with(&s, &f, tmp.path(), tmp.path(), &c, &catalog, &baseline);
        let grouped = last_trace(tmp.path()).unwrap();
        assert_eq!(grouped["status"], "experiment_requires_source_partition");
        assert_eq!(f.0.load(Ordering::SeqCst), 1);
        let config = s.capabilities.get_mut(CAPABILITY).unwrap();
        config.experiment = None;
        config.mode = DecisionMode::Enabled;
        observe_with(&s, &f, tmp.path(), tmp.path(), &c, &catalog, &baseline);
        assert_eq!(
            last_trace(tmp.path()).unwrap()["status"],
            "unsupported_enabled_mode"
        );
        assert_eq!(f.0.load(Ordering::SeqCst), 1);
    }
}
