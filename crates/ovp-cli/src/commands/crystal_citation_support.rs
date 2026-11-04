//! Citation-support shadow (INV-1422): after a durable write, ask whether each
//! cited quote of each newly written claim backs it, and record the answers in
//! `<store>/citation-support.jsonl`. Off unless `.ovp/decisions.json` turns
//! the `citation_support` capability to shadow. It never touches the ledger,
//! the review queue or any grade; a failure is recorded as a failure.
use super::crystal_strength_shadow::vault_settings;
use ovp_app::decisions::BuiltinDecisionFactory;
use ovp_domain::citation_support::{self, CitationJudgment};
use ovp_domain::crystal::DurableRecord;
use ovp_domain::crystal::synth::UnitsCatalog;
use ovp_llm::decision::runtime::{DecisionClientFactory, DecisionMode, DecisionSettings, evaluate};
use serde_json::{Value, json};
use std::path::Path;

const CAPABILITY: &str = "citation_support";
pub const FILE: &str = "citation-support.jsonl";
const SCHEMA: &str = "ovp.crystal.citation_support/v1";

pub fn observe(
    vault_root: Option<&Path>,
    store: &Path,
    records: &[DurableRecord],
    catalog: &UnitsCatalog,
) {
    let Some(vault) = vault_root else {
        return;
    };
    if records.is_empty() {
        return;
    }
    let Some(settings) = vault_settings(vault, "citation support") else {
        return;
    };
    observe_with(
        &settings,
        &BuiltinDecisionFactory,
        vault,
        store,
        records,
        catalog,
    );
}

fn observe_with(
    settings: &DecisionSettings,
    factory: &dyn DecisionClientFactory,
    vault: &Path,
    store: &Path,
    records: &[DurableRecord],
    catalog: &UnitsCatalog,
) {
    let Some(config) = settings.capabilities.get(CAPABILITY) else {
        return;
    };
    if config.mode == DecisionMode::Off {
        return;
    }
    // Shadow only: an enabled mode would let the answers matter, which needs
    // its own decision and thresholds first.
    let refusal = if config.mode != DecisionMode::Shadow {
        Some("enabled mode is not supported; use shadow")
    } else if config.experiment.is_some() {
        Some("experiments are not supported; every claim is asked")
    } else if config.question_namespace.as_deref() != Some(citation_support::NAMESPACE) {
        Some("question namespace mismatch")
    } else {
        None
    };
    let mut rows = Vec::new();
    for record in records {
        let outcome = match refusal {
            Some(reason) => Err(reason.to_string()),
            None => ask(settings, factory, vault, record, catalog),
        };
        rows.extend(rows_for(record, outcome));
    }
    persist(store, &rows);
}

/// What one claim's answers were, with where they came from.
struct Answered {
    judged: Vec<CitationJudgment>,
    model: String,
    origin: Value,
    request_key: String,
}

fn ask(
    settings: &DecisionSettings,
    factory: &dyn DecisionClientFactory,
    vault: &Path,
    record: &DurableRecord,
    catalog: &UnitsCatalog,
) -> Result<Answered, String> {
    let request = citation_support::request(record, catalog)?;
    let observation = evaluate(
        settings,
        factory,
        CAPABILITY,
        &request,
        &format!("crystal-citation-support:{}", record.claim_key),
        &vault.join(".ovp/cassettes/decisions"),
    )
    .map_err(|e| e.to_string())?;
    if observation.applied.is_some() {
        return Err("shadow attempted to apply a decision".into());
    }
    let reply = observation.candidate.as_ref().ok_or_else(|| {
        observation
            .error
            .clone()
            .unwrap_or_else(|| format!("no answer (status {:?})", observation.status))
    })?;
    let judged = citation_support::judgments(record, reply).map_err(|e| e.to_string())?;
    Ok(Answered {
        judged,
        model: reply.receipt.provider.model.clone(),
        origin: json!(reply.receipt.origin),
        request_key: reply.receipt.request_key.clone(),
    })
}

/// One row per citation, judged or failed — never a judgment made up for a
/// citation that was not answered.
fn rows_for(record: &DurableRecord, outcome: Result<Answered, String>) -> Vec<Value> {
    record
        .citations
        .iter()
        .enumerate()
        .map(|(i, c)| {
            let mut row = json!({
                "schema": SCHEMA,
                "claim_key": record.claim_key,
                "run_id": record.run_id,
                "citation": i,
                "case_id": c.case_id,
                "unit_id": c.unit_id,
                "question": citation_support::NAMESPACE,
            });
            match &outcome {
                Ok(a) => {
                    let j = &a.judged[i];
                    row["status"] = json!("judged");
                    row["support"] = json!(j.support);
                    row["confidence"] = json!(j.confidence);
                    row["probabilities"] = json!(j.probabilities);
                    row["flagged"] = json!(j.flagged());
                    row["model"] = json!(a.model);
                    row["origin"] = a.origin.clone();
                    row["request_key"] = json!(a.request_key);
                }
                Err(e) => {
                    row["status"] = json!("failed");
                    row["error"] = json!(e);
                }
            }
            row
        })
        .collect()
}

fn persist(store: &Path, rows: &[Value]) {
    let path = store.join(FILE);
    let result = (|| -> std::io::Result<()> {
        std::fs::create_dir_all(store)?;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?;
        for row in rows {
            ovp_domain::jsonl::append_line(&mut file, &path, &row.to_string())?;
        }
        Ok(())
    })();
    if let Err(e) = result {
        eprintln!("citation support: {} unavailable: {e}", path.display());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::synth::{CatalogCase, CatalogUnit};
    use ovp_domain::crystal::{
        CrystalStatus, DurableCitation, FinalClass, ProvenanceClass, StrengthClass,
    };
    use ovp_llm::decision::runtime::{CapabilityConfig, ExecutionMode};
    use ovp_llm::decision::*;
    use std::collections::BTreeMap;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// Answers q0 "supports" and every other citation "says_nothing" at 0.9;
    /// or fails every call when `fail` is set.
    struct Factory {
        calls: AtomicUsize,
        fail: bool,
    }
    struct Client(DecisionProfile, bool);
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
                probabilities: true,
            }
        }
        fn decide(&mut self, request: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
            if self.1 {
                return Err(DecisionError::MissingCredential);
            }
            let answers = request
                .questions
                .keys()
                .map(|id| {
                    let selected = if id.0 == "q0" {
                        "supports"
                    } else {
                        "says_nothing"
                    };
                    let a = DecisionAnswer::Choice {
                        selected: selected.into(),
                        probabilities: None,
                        confidence: Some(0.9),
                    };
                    (id.clone(), a)
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
            self.calls.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(Client(p.clone(), self.fail)))
        }
    }

    fn settings(mode: DecisionMode) -> DecisionSettings {
        DecisionSettings {
            profiles: BTreeMap::from([(
                "p".into(),
                DecisionProfile {
                    id: "p".into(),
                    provider: "fixture".into(),
                    endpoint: "fixture://citation".into(),
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
                    question_namespace: Some(citation_support::NAMESPACE.into()),
                    experiment: None,
                },
            )]),
        }
    }

    fn record(key: &str, quotes: &[&str]) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: key.into(),
            claim: "Agents need memory".into(),
            theme: "t".into(),
            theme_id: None,
            source_cases: vec!["c".into()],
            citations: quotes
                .iter()
                .map(|q| DurableCitation {
                    case_id: "c".into(),
                    unit_id: "u".into(),
                    quote: (*q).into(),
                    resolved_line: Some(1),
                })
                .collect(),
            provenance_score: 1.0,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "r".into(),
            final_class: FinalClass::Durable,
            run_id: "run-1".into(),
            status: CrystalStatus::Active,
        }
    }

    fn catalog() -> UnitsCatalog {
        UnitsCatalog {
            cases: BTreeMap::from([(
                "c".into(),
                CatalogCase {
                    title: "source".into(),
                    units: vec![CatalogUnit {
                        unit_id: "u".into(),
                        line: Some(1),
                        quote: "Agents forget. Memory is needed.".into(),
                        attribution: "author".into(),
                        modality: "asserted".into(),
                    }],
                },
            )]),
        }
    }

    fn rows(store: &Path) -> Vec<Value> {
        match std::fs::read_to_string(store.join(FILE)) {
            Ok(text) => text
                .lines()
                .map(|l| serde_json::from_str(l).unwrap())
                .collect(),
            Err(_) => Vec::new(),
        }
    }

    fn run(s: &DecisionSettings, f: &Factory, dir: &Path, records: &[DurableRecord]) -> Vec<Value> {
        let store = dir.join("store");
        let _ = std::fs::remove_file(store.join(FILE));
        observe_with(s, f, dir, &store, records, &catalog());
        rows(&store)
    }

    #[test]
    fn off_makes_no_call_and_writes_nothing() {
        let tmp = tempfile::tempdir().unwrap();
        let f = Factory {
            calls: AtomicUsize::new(0),
            fail: false,
        };
        let r = [record("ck-1", &["Agents forget"])];
        assert!(run(&settings(DecisionMode::Off), &f, tmp.path(), &r).is_empty());
        let mut none = settings(DecisionMode::Off);
        none.capabilities.clear();
        assert!(run(&none, &f, tmp.path(), &r).is_empty());
        assert_eq!(f.calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn shadow_records_one_judged_row_per_citation() {
        let tmp = tempfile::tempdir().unwrap();
        let f = Factory {
            calls: AtomicUsize::new(0),
            fail: false,
        };
        let r = [
            record("ck-1", &["Agents forget", "Memory is needed"]),
            record("ck-2", &["Agents forget"]),
        ];
        let got = run(&settings(DecisionMode::Shadow), &f, tmp.path(), &r);
        assert_eq!(got.len(), 3);
        assert_eq!(f.calls.load(Ordering::SeqCst), 2, "one request per claim");
        assert_eq!(got[0]["claim_key"], "ck-1");
        assert_eq!(got[0]["status"], "judged");
        assert_eq!(got[0]["support"], "supports");
        assert_eq!(got[0]["flagged"], false);
        assert_eq!(
            (got[1]["citation"].clone(), got[1]["support"].clone()),
            (json!(1), json!("says_nothing"))
        );
        assert_eq!(got[1]["flagged"], true);
        assert_eq!(got[1]["model"], "synthetic-v1");
        assert_eq!(got[1]["question"], citation_support::NAMESPACE);
        assert!(
            got[1]["request_key"]
                .as_str()
                .is_some_and(|k| !k.is_empty())
        );
    }

    #[test]
    fn failures_are_recorded_as_failures() {
        let tmp = tempfile::tempdir().unwrap();
        let failing = Factory {
            calls: AtomicUsize::new(0),
            fail: true,
        };
        let r = [record("ck-1", &["Agents forget", "Memory is needed"])];
        let got = run(&settings(DecisionMode::Shadow), &failing, tmp.path(), &r);
        assert_eq!(got.len(), 2);
        assert!(
            got.iter()
                .all(|row| row["status"] == "failed" && row.get("support").is_none())
        );
        let expected = DecisionError::MissingCredential.to_string();
        assert!(
            got[0]["error"].as_str().unwrap().contains(&expected),
            "{}",
            got[0]["error"]
        );

        // A citation the gate would not ground: the claim is not asked at all.
        let ok = Factory {
            calls: AtomicUsize::new(0),
            fail: false,
        };
        let got = run(
            &settings(DecisionMode::Shadow),
            &ok,
            tmp.path(),
            &[record("ck-2", &["invented"])],
        );
        assert_eq!(got[0]["status"], "failed");
        assert_eq!(ok.calls.load(Ordering::SeqCst), 0);
    }

    /// A recorded answer replays to the same judgments, with no credential
    /// and no network: the built-in factory in replay mode reads the cassette.
    #[test]
    fn a_recorded_answer_replays_to_the_same_judgments() {
        let tmp = tempfile::tempdir().unwrap();
        let profile = DecisionProfile {
            id: "typesafe-primary".into(),
            provider: "typesafe".into(),
            endpoint: "https://api.typesafe.ai/v1/systemone".into(),
            model: "jev-1.13.0".into(),
            credential_ref: "TYPESAFE_TOKEN".into(),
        };
        let mut s = settings(DecisionMode::Shadow);
        s.profiles = BTreeMap::from([(profile.id.clone(), profile.clone())]);
        s.capabilities.get_mut(CAPABILITY).unwrap().profile = Some(profile.id.clone());
        let r = [record("ck-1", &["Agents forget", "Memory is needed"])];
        let request = citation_support::request(&r[0], &catalog()).unwrap();
        let cassettes = tmp.path().join(".ovp/cassettes/decisions").join(CAPABILITY);
        CachedDecisionClient::record(Box::new(Client(profile, false)), &cassettes)
            .unwrap()
            .decide(&request)
            .unwrap();

        let store = tmp.path().join("store");
        let factory = BuiltinDecisionFactory;
        observe_with(&s, &factory, tmp.path(), &store, &r, &catalog());
        observe_with(&s, &factory, tmp.path(), &store, &r, &catalog());
        let got = rows(&store);
        assert_eq!(got.len(), 4);
        let answers = |rows: &[Value]| {
            rows.iter()
                .map(|r| (r["status"].clone(), r["support"].clone()))
                .collect::<Vec<_>>()
        };
        assert_eq!(
            answers(&got[..2]),
            answers(&got[2..]),
            "same input, same judgment"
        );
        assert_eq!(
            answers(&got[..2]),
            [
                (json!("judged"), json!("supports")),
                (json!("judged"), json!("says_nothing"))
            ]
        );
        assert_eq!(got[0]["origin"], got[2]["origin"]);
        assert_eq!(got[0]["model"], "jev-1.13.0");
    }

    #[test]
    fn enabled_mode_and_experiments_are_refused_without_a_call() {
        let tmp = tempfile::tempdir().unwrap();
        let f = Factory {
            calls: AtomicUsize::new(0),
            fail: false,
        };
        let r = [record("ck-1", &["Agents forget"])];
        let got = run(&settings(DecisionMode::Enabled), &f, tmp.path(), &r);
        assert_eq!(got[0]["status"], "failed");
        assert!(got[0]["error"].as_str().unwrap().contains("shadow"));
        let mut exp = settings(DecisionMode::Shadow);
        exp.capabilities.get_mut(CAPABILITY).unwrap().experiment =
            Some(ovp_llm::decision::runtime::Experiment {
                id: "e".into(),
                seed: "s".into(),
                candidate_basis_points: 5000,
            });
        assert_eq!(run(&exp, &f, tmp.path(), &r)[0]["status"], "failed");
        assert_eq!(f.calls.load(Ordering::SeqCst), 0);
    }
}
