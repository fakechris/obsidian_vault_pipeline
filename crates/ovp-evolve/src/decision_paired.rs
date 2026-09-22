//! Execute typed decision comparisons on identical frozen inputs. Predictions
//! are scored against labels; fallback is never counted as a candidate success.
use crate::{
    candidate::CandidateSpec,
    decision_plan::*,
    ledger::LedgerEntry,
    paired::RunConfig,
    paired_io as io,
    registry::ComponentRegistry,
    types::{ChangeSurface, Decision},
};
use ovp_llm::decision::{runtime::*, *};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Instant;

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenSource {
    pub revision: String,
    pub text: String,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum GoldAnswer {
    Boolean { value: bool },
    Choice { selected: OptionId },
    Score { position: f64, tolerance: f64 },
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionCase {
    pub id: String,
    pub bucket: String,
    pub request: DecisionRequest,
    pub gold: BTreeMap<QuestionId, GoldAnswer>,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionFixture {
    pub schema: String,
    pub split: String,
    pub provenance: String,
    pub sources: BTreeMap<String, FrozenSource>,
    pub cases: Vec<DecisionCase>,
}
impl DecisionFixture {
    pub fn validate(&self, plan: &DecisionPlan) -> Result<(), String> {
        if self.schema != "ovp.decision.fixture/v1"
            || self.split != plan.split
            || self.provenance.trim().is_empty()
            || self.cases.len() != plan.expected_cases
            || self
                .cases
                .iter()
                .map(|c| c.request.questions.len())
                .sum::<usize>()
                != plan.expected_questions
        {
            return Err("fixture identity, split or coverage mismatch".into());
        }
        for source in self.sources.values() {
            if source.revision != io::hash(source.text.as_bytes()) {
                return Err("source revision is not its content digest".into());
            }
        }
        let mut ids = BTreeSet::new();
        for case in &self.cases {
            if case.id.trim().is_empty()
                || case.bucket.trim().is_empty()
                || !ids.insert(&case.id)
                || case.gold.keys().ne(case.request.questions.keys())
                || case.request.evidence.is_empty()
            {
                return Err("invalid case or incomplete gold labels/evidence".into());
            }
            case.request
                .validate(DecisionCapabilities {
                    boolean: true,
                    choice: true,
                    score: true,
                    batch: true,
                    probabilities: true,
                })
                .map_err(|e| e.to_string())?;
            for evidence in &case.request.evidence {
                let source = self
                    .sources
                    .get(&evidence.source_id)
                    .ok_or("evidence source missing")?;
                if source.revision != evidence.revision
                    || evidence.end_line as usize > source.text.lines().count()
                {
                    return Err("evidence revision or line mismatch".into());
                }
            }
            for (id, question) in &case.request.questions {
                let valid = match (&question.kind, &case.gold[id]) {
                    (QuestionKind::Boolean { .. }, GoldAnswer::Boolean { .. }) => true,
                    (QuestionKind::Choice { options }, GoldAnswer::Choice { selected }) => {
                        options.contains_key(selected)
                    }
                    (
                        QuestionKind::Score { levels },
                        GoldAnswer::Score {
                            position,
                            tolerance,
                        },
                    ) => {
                        position.is_finite()
                            && (0.0..=(levels.len() - 1) as f64).contains(position)
                            && tolerance.is_finite()
                            && *tolerance >= 0.0
                            && *tolerance <= (levels.len() - 1) as f64
                    }
                    _ => false,
                };
                if !valid {
                    return Err("gold answer violates question contract".into());
                }
            }
            for arm in [&plan.control, &plan.candidate] {
                if arm.question_namespace != case.request.namespace {
                    return Err("question namespace must match both arms".into());
                }
                if arm.boolean_thresholds.keys().any(|id| {
                    !case
                        .request
                        .questions
                        .get(id)
                        .is_some_and(|q| matches!(q.kind, QuestionKind::Boolean { .. }))
                }) {
                    return Err("threshold references an absent/nonboolean question".into());
                }
            }
        }
        Ok(())
    }
}
#[derive(Debug, Serialize)]
pub struct QuestionResult {
    pub id: QuestionId,
    pub correct: bool,
    pub abstained: bool,
}
#[derive(Debug, Serialize)]
pub struct CaseResult {
    pub id: String,
    pub bucket: String,
    pub observation: DecisionObservation,
    pub questions: Vec<QuestionResult>,
}
#[derive(Debug, Default, Serialize)]
pub struct ArmMetrics {
    pub questions: usize,
    pub correct: usize,
    pub abstained: usize,
    pub fallback_cases: usize,
    pub errors: usize,
    pub usage_unknown_cases: usize,
    pub live_input_tokens: u64,
    pub live_output_tokens: u64,
    pub historical_input_tokens: u64,
    pub historical_output_tokens: u64,
    pub historical_usage_unknown_cases: usize,
    pub network_attempts: u64,
    pub p50_ms: u64,
    pub p95_ms: u64,
}
#[derive(Debug, Serialize)]
pub struct ArmResult {
    pub valid: bool,
    pub rows: Vec<CaseResult>,
    pub metrics: ArmMetrics,
    pub error: Option<String>,
}
fn score(
    answer: &DecisionAnswer,
    gold: &GoldAnswer,
    threshold: Option<f64>,
) -> Result<(bool, bool), String> {
    match (answer, gold) {
        (DecisionAnswer::Abstain { .. }, _) => Ok((false, true)),
        (
            DecisionAnswer::Boolean {
                value,
                probability_true,
            },
            GoldAnswer::Boolean { value: expected },
        ) => {
            let value = match value {
                Some(v) => *v,
                None => {
                    probability_true.ok_or("missing boolean probability")?
                        >= threshold
                            .ok_or("explicit profile/model/question boolean threshold required")?
                }
            };
            Ok((value == *expected, false))
        }
        (DecisionAnswer::Choice { selected, .. }, GoldAnswer::Choice { selected: expected }) => {
            Ok((selected == expected, false))
        }
        (
            DecisionAnswer::Score { position, .. },
            GoldAnswer::Score {
                position: expected,
                tolerance,
            },
        ) => Ok(((position - expected).abs() <= *tolerance, false)),
        _ => Err("answer/gold type mismatch".into()),
    }
}
fn add_usage(metrics: &mut ArmMetrics, observation: &DecisionObservation) -> Result<(), String> {
    metrics.usage_unknown_cases += usize::from(observation.usage_unknown);
    if let Some(reply) = &observation.candidate {
        metrics.network_attempts = metrics
            .network_attempts
            .checked_add(reply.receipt.network_attempts as u64)
            .ok_or("attempt overflow")?;
        if let Some(usage) = &reply.receipt.evaluation_usage {
            let (input, output) = if reply.receipt.origin == DecisionOrigin::Live {
                (
                    &mut metrics.live_input_tokens,
                    &mut metrics.live_output_tokens,
                )
            } else {
                (
                    &mut metrics.historical_input_tokens,
                    &mut metrics.historical_output_tokens,
                )
            };
            *input = input
                .checked_add(usage.input_tokens)
                .ok_or("usage overflow")?;
            *output = output
                .checked_add(usage.output_tokens)
                .ok_or("usage overflow")?;
        } else if reply.receipt.origin != DecisionOrigin::Live {
            metrics.historical_usage_unknown_cases += 1;
        }
    }
    Ok(())
}
fn execute_arm(
    plan: &DecisionPlan,
    arm: &DecisionArmPlan,
    fixture: &DecisionFixture,
    cache: &Path,
    factory: &dyn DecisionClientFactory,
) -> ArmResult {
    let start = Instant::now();
    let mut result = ArmResult {
        valid: true,
        rows: vec![],
        metrics: ArmMetrics::default(),
        error: None,
    };
    let settings = plan.settings(arm);
    let run = (|| -> Result<(), String> {
        for case in &fixture.cases {
            if start.elapsed().as_millis() >= plan.budget.max_wall_ms_per_arm as u128 {
                return Err("arm wall-time budget exceeded".into());
            }
            let observation = evaluate(
                &settings,
                factory,
                &plan.capability,
                &case.request,
                &case.id,
                cache,
            )
            .map_err(|e| e.to_string())?;
            add_usage(&mut result.metrics, &observation)?;
            let mut row = CaseResult {
                id: case.id.clone(),
                bucket: case.bucket.clone(),
                observation,
                questions: vec![],
            };
            let score_result = (|| -> Result<(), String> {
                if row.observation.status == ObservationStatus::Fallback {
                    result.metrics.fallback_cases += 1;
                    return Err(
                        "candidate evaluation failed; baseline fallback is not a paired result"
                            .into(),
                    );
                }
                let reply = row
                    .observation
                    .candidate
                    .as_ref()
                    .ok_or("arm did not execute")?;
                for (id, gold) in &case.gold {
                    let (correct, abstained) = score(
                        &reply.answers[id],
                        gold,
                        arm.boolean_thresholds.get(id).copied(),
                    )?;
                    result.metrics.questions += 1;
                    result.metrics.correct += usize::from(correct);
                    result.metrics.abstained += usize::from(abstained);
                    row.questions.push(QuestionResult {
                        id: id.clone(),
                        correct,
                        abstained,
                    });
                }
                Ok(())
            })();
            result.rows.push(row);
            score_result?;
            if result.metrics.live_input_tokens > plan.budget.max_observed_input_tokens_per_arm
                || result.metrics.live_output_tokens
                    > plan.budget.max_observed_output_tokens_per_arm
            {
                return Err("observed token budget exceeded; no further calls made".into());
            }
            if result.metrics.usage_unknown_cases > 0 {
                return Err("unknown live usage prevents budget verification".into());
            }
        }
        if start.elapsed().as_millis() > plan.budget.max_wall_ms_per_arm as u128 {
            return Err("arm wall-time budget exceeded".into());
        }
        if result.metrics.questions != plan.expected_questions {
            return Err("incomplete question coverage".into());
        }
        Ok(())
    })();
    if let Err(error) = run {
        result.valid = false;
        result.metrics.errors += 1;
        result.error = Some(error);
    }
    let mut durations: Vec<_> = result
        .rows
        .iter()
        .map(|r| r.observation.elapsed_ms)
        .collect();
    durations.sort_unstable();
    if !durations.is_empty() {
        result.metrics.p50_ms = durations[(durations.len() * 50).div_ceil(100) - 1];
        result.metrics.p95_ms = durations[(durations.len() * 95).div_ceil(100) - 1];
    }
    result
}

/// Injectable factory keeps new suppliers independent of the runner and CLI.
fn check_output_location(output: &Path, input: &Path) -> Result<(), String> {
    let absolute = if output.is_absolute() {
        output.to_owned()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(output)
    };
    let mut ancestor = absolute.as_path();
    let mut suffix = Vec::new();
    while !ancestor.exists() {
        suffix.push(ancestor.file_name().ok_or("invalid output path")?);
        ancestor = ancestor.parent().ok_or("invalid output ancestor")?;
    }
    let mut resolved = ancestor.canonicalize().map_err(|e| e.to_string())?;
    for part in suffix.into_iter().rev() {
        resolved.push(part);
    }
    if resolved.starts_with(input) {
        return Err("output must be outside fixture".into());
    }
    Ok(())
}

pub fn run_with_factory(
    config: RunConfig,
    factory: &dyn DecisionClientFactory,
) -> Result<Decision, String> {
    let spec_bytes = std::fs::read(&config.candidate).map_err(|e| e.to_string())?;
    let spec: CandidateSpec = serde_json::from_slice(&spec_bytes).map_err(|e| e.to_string())?;
    let registry_bytes = std::fs::read(&config.registry).map_err(|e| e.to_string())?;
    let registry: ComponentRegistry =
        serde_json::from_slice(&registry_bytes).map_err(|e| e.to_string())?;
    registry.validate().map_err(|e| e.to_string())?;
    spec.validate(&registry).map_err(|e| e.to_string())?;
    let plan = spec
        .eval_plan
        .decision_run
        .as_ref()
        .ok_or("missing decision plan")?;
    if spec.surface != ChangeSurface::Runtime
        || spec.component != "runtime.decision_client"
        || spec.ablation_required
        || spec.guardrails.quote_found_rate_floor.is_some()
        || spec.guardrails.max_token_regression.is_some()
        || spec
            .guardrails
            .accepted_without_quote
            .is_some_and(|v| v != 0)
    {
        return Err("decision runner supports runtime decision wiring only; admission/model/prompt experiments require their own gate".into());
    }
    for arm in [&plan.control, &plan.candidate] {
        plan.settings(arm)
            .validate(factory)
            .map_err(|e| e.to_string())?;
    }
    let input = plan.fixture_dir.canonicalize().map_err(|e| e.to_string())?;
    let files = io::snapshot(&input)?;
    let fixture: DecisionFixture = io::read_json(&input.join("cases.json"))?;
    fixture.validate(plan)?;
    check_output_location(&config.output, &input)?;
    let output = io::new_output(&config.output)?;
    if output.starts_with(&input) {
        return Err("output must be outside fixture".into());
    }
    let executable = config
        .executable
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let mut manifest = json!({"schema":"ovp.evolution.paired_decision/v1","status":"running","candidate_spec":spec,"candidate_sha256":io::hash(&spec_bytes),"registry_sha256":io::hash(&registry_bytes),"fixture_files":files,"code":io::code_identity()?,"executable_sha256":io::hash_file(&executable)?,"plan":plan,"accepted_without_quote":null,"admission_gate_scope":"not_exercised","arms":{},"comparison":null,"error":null});
    io::write_json(&output.join("manifest.json"), &manifest)?;
    let execution = (|| -> Result<Decision, String> {
        let mut arms = Vec::new();
        for (name, arm) in [("control", &plan.control), ("candidate", &plan.candidate)] {
            let dest = output.join(name);
            std::fs::create_dir(&dest).map_err(|e| e.to_string())?;
            let frozen = dest.join("fixture");
            io::copy_snapshot(&input, &frozen, &files)?;
            let cache = dest.join("cache");
            let captures = frozen.join("cassettes").join(name);
            if captures.exists() {
                io::copy_snapshot(&captures, &cache, &io::snapshot(&captures)?)?;
            }
            let frozen_fixture: DecisionFixture = io::read_json(&frozen.join("cases.json"))?;
            let result = execute_arm(plan, arm, &frozen_fixture, &cache, factory);
            io::write_json(
                &dest.join("results.json"),
                &serde_json::to_value(&result).map_err(|e| e.to_string())?,
            )?;
            manifest["arms"][name] = json!({"valid":result.valid,"results_sha256":io::hash_file(&dest.join("results.json"))?,"metrics":result.metrics,"error":result.error});
            io::write_json(&output.join("manifest.json"), &manifest)?;
            if io::snapshot(&frozen)? != files {
                return Err("arm modified frozen inputs".into());
            }
            arms.push(result);
        }
        if io::snapshot(&input)? != files {
            return Err("original fixture changed during evaluation".into());
        }
        if arms.iter().any(|a| !a.valid) {
            return Err(
                "invalid paired run; inspect per-arm errors (no candidate acceptance)".into(),
            );
        }
        let delta = (arms[1].metrics.correct as f64 - arms[0].metrics.correct as f64)
            / plan.expected_questions as f64;
        let mut buckets: BTreeMap<String, (usize, usize, usize)> = BTreeMap::new();
        let mut questions = Vec::new();
        for (a, b) in arms[0].rows.iter().zip(&arms[1].rows) {
            for (aq, bq) in a.questions.iter().zip(&b.questions) {
                let counts = buckets.entry(a.bucket.clone()).or_default();
                counts.0 += 1;
                counts.1 += usize::from(aq.correct);
                counts.2 += usize::from(bq.correct);
                questions.push(json!({"case":a.id,"question":aq.id,"bucket":a.bucket,"control":aq,"candidate":bq}));
            }
        }
        let buckets: Vec<Value> = buckets.into_iter().map(|(name,(n,a,b))| json!({"bucket":name,"questions":n,"accuracy_delta":(b as f64-a as f64)/n as f64,"small_bucket":n<plan.min_bucket_size})).collect();
        let guardrails_hold = buckets
            .iter()
            .all(|b| b["accuracy_delta"].as_f64().unwrap() >= -plan.max_bucket_accuracy_regression);
        let target_met = delta >= plan.min_accuracy_delta;
        // Runtime wiring smoke tests are review evidence, never model promotion.
        let decision = if guardrails_hold && target_met {
            Decision::NeedsHumanReview
        } else {
            Decision::Reject
        };
        manifest["comparison"] = json!({"decision":decision,"accuracy_delta":delta,"target_met":target_met,"guardrails_hold":guardrails_hold,"buckets":buckets,"questions":questions,"cost_scope":"observed tokens only; historical replay usage separated; no currency cost inferred"});
        Ok(decision)
    })();
    let decision = match &execution {
        Ok(d) => {
            manifest["status"] = json!("completed");
            *d
        }
        Err(e) => {
            manifest["status"] = json!("invalid");
            manifest["error"] = json!(e);
            Decision::NeedsHumanReview
        }
    };
    io::write_json(&output.join("manifest.json"), &manifest)?;
    let mut entry = LedgerEntry::new(&spec.id, &spec.component, decision);
    entry.git_sha = manifest["code"]["git_sha"].as_str().map(str::to_string);
    entry.version_from = Some(spec.base_version.clone());
    entry.version_to = Some(spec.target_version.clone());
    entry.rollback = Some(spec.rollback.clone());
    entry.scorecard_summary = json!({"status":manifest["status"],"comparison":manifest["comparison"],"error":manifest["error"],"accepted_without_quote":null,"admission_gate_scope":"not_exercised"});
    crate::ledger::append_entry(&output.join(".ovp/evolution-ledger.jsonl"), &entry)
        .map_err(|e| e.to_string())?;
    execution
}
pub fn run(config: RunConfig) -> Result<Decision, String> {
    run_with_factory(config, &ovp_app::decisions::BuiltinDecisionFactory)
}
