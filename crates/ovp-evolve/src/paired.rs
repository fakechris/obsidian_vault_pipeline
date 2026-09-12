//! Executed, offline retrieval experiments. Unlike the legacy scorecard, this
//! path derives metrics from ordered tool outputs and frozen qrels. It makes
//! no claim about generated answers, semantic support, or LLM cost.
//! Candidate: evolve-ab-runtime-v1 (INV-498).
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::paired_io as io;
use crate::{
    candidate::CandidateSpec,
    ledger::LedgerEntry,
    registry::ComponentRegistry,
    types::{ChangeSurface, Decision},
};

pub const RUN_SCHEMA: &str = "ovp.evolution.paired_retrieval/v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetrievalPlan {
    pub runner: String,
    pub fixture_dir: PathBuf,
    pub control_query_mode: String,
    pub candidate_query_mode: String,
    pub k: usize,
    pub expected_questions: usize,
    pub min_mean_recall_delta: f64,
    pub timeout_seconds: u64,
}

impl RetrievalPlan {
    pub fn validate(&self) -> Result<(), String> {
        if self.runner != "retrieval" {
            return Err("only the offline retrieval runner is supported".into());
        }
        for mode in [&self.control_query_mode, &self.candidate_query_mode] {
            if !["verbatim", "terms"].contains(&mode.as_str()) {
                return Err(format!("unsupported retrieval query mode: {mode}"));
            }
        }
        if !(1..=50).contains(&self.k)
            || !(1..=3600).contains(&self.timeout_seconds)
            || !self.min_mean_recall_delta.is_finite()
            || !(0.0..=1.0).contains(&self.min_mean_recall_delta)
        {
            return Err("invalid cutoff, timeout or pre-registered recall delta".into());
        }
        Ok(())
    }
}

pub struct RunConfig {
    pub candidate: PathBuf,
    pub registry: PathBuf,
    /// A new, durable directory under .run or .ovp. Never overwritten.
    pub output: PathBuf,
    /// The current ovp2 executable; both arms use this identical binary.
    pub executable: PathBuf,
}

#[derive(Debug, Deserialize)]
struct Qrel {
    schema: String,
    id: String,
    question: String,
    class: String,
    language: String,
    confidence: String,
    no_answer: bool,
    relevant: Vec<Relevant>,
}
#[derive(Debug, Deserialize)]
struct Relevant {
    surface: String,
    id: String,
}

#[derive(Debug, Deserialize)]
struct Report {
    schema: String,
    query_mode: String,
    questions: usize,
    per_question: Vec<Row>,
}
#[derive(Debug, Deserialize)]
struct Row {
    id: String,
    tool_errors: Vec<String>,
    source_ranks: BTreeMap<String, Vec<String>>,
}

#[derive(Debug, Serialize)]
pub struct QuestionComparison {
    pub id: String,
    pub class: String,
    pub language: String,
    pub control_recall: Option<f64>,
    pub candidate_recall: Option<f64>,
    pub control_negative_hits: Option<usize>,
    pub candidate_negative_hits: Option<usize>,
}

#[derive(Debug, Serialize)]
pub struct Comparison {
    pub decision: Decision,
    pub mean_recall_delta: f64,
    pub questions: Vec<QuestionComparison>,
    pub regressions: Vec<String>,
    pub target_met: bool,
    /// The runner cannot create or accept claims; zero is structural, not a
    /// measurement of the production claim gate.
    pub accepted_without_quote: u32,
    pub admission_gate_scope: &'static str,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

fn load_qrels(dir: &Path) -> Result<Vec<Qrel>, String> {
    let mut files: Vec<_> = std::fs::read_dir(dir)
        .map_err(|e| e.to_string())?
        .map(|r| r.map(|e| e.path()).map_err(|e| e.to_string()))
        .collect::<Result<_, _>>()?;
    files.sort();
    let mut out = Vec::new();
    let mut ids = BTreeSet::new();
    for path in files {
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        let q: Qrel = io::read_json(&path)?;
        if q.schema != "ovp.retrieval_eval.qrel/v1"
            || q.confidence != "gold"
            || q.id.is_empty()
            || q.question.trim().is_empty()
            || !ids.insert(q.id.clone())
            || q.relevant
                .iter()
                .any(|r| r.surface != "source" || r.id.is_empty())
            || (q.no_answer && !q.relevant.is_empty())
            || (!q.no_answer && q.relevant.is_empty())
        {
            return Err(format!(
                "unsupported or invalid gold qrel: {}",
                path.display()
            ));
        }
        let unique: BTreeSet<_> = q.relevant.iter().map(|r| &r.id).collect();
        if unique.len() != q.relevant.len() {
            return Err("duplicate gold source".into());
        }
        out.push(q);
    }
    if out.is_empty() || !out.iter().any(|q| !q.no_answer) {
        return Err(
            "paired retrieval requires gold source questions; claim-only qrels are unsupported"
                .into(),
        );
    }
    Ok(out)
}

fn rows(
    report: Report,
    mode: &str,
    qrels: &[Qrel],
    known: &BTreeSet<String>,
) -> Result<BTreeMap<String, Row>, String> {
    if report.schema != "ovp.retrieval_eval.report/v1"
        || report.query_mode != mode
        || report.questions != qrels.len()
        || report.per_question.len() != qrels.len()
    {
        return Err("incomplete report or mismatched runner policy".into());
    }
    let expected: BTreeSet<_> = qrels.iter().map(|q| q.id.as_str()).collect();
    let mut out = BTreeMap::new();
    for row in report.per_question {
        if !row.tool_errors.is_empty() {
            return Err(format!("{}: tool failure: {:?}", row.id, row.tool_errors));
        }
        if !expected.contains(row.id.as_str()) || out.contains_key(&row.id) {
            return Err("duplicate or unexpected question in executed report".into());
        }
        for tool in ["search_sources", "search_evidence", "search_claims"] {
            let ranks = row
                .source_ranks
                .get(tool)
                .ok_or_else(|| format!("{}: missing {tool} output", row.id))?;
            if ranks.iter().any(|id| !known.contains(id)) {
                return Err(format!(
                    "{}: returned source does not belong to frozen fixture",
                    row.id
                ));
            }
        }
        out.insert(row.id.clone(), row);
    }
    Ok(out)
}

fn ranked_union(row: &Row) -> Vec<&str> {
    let lists: Vec<_> = ["search_sources", "search_evidence", "search_claims"]
        .iter()
        .map(|name| &row.source_ranks[*name])
        .collect();
    let mut union = Vec::new();
    for i in 0..lists.iter().map(|v| v.len()).max().unwrap_or(0) {
        for list in &lists {
            if let Some(id) = list.get(i) {
                if !union.contains(&id.as_str()) {
                    union.push(id.as_str());
                }
            }
        }
    }
    union
}

fn compare(
    plan: &RetrievalPlan,
    qrels: &[Qrel],
    known: &BTreeSet<String>,
    control: Report,
    candidate: Report,
) -> Result<Comparison, String> {
    let control = rows(control, &plan.control_query_mode, qrels, known)?;
    let candidate = rows(candidate, &plan.candidate_query_mode, qrels, known)?;
    let mut comparisons = Vec::new();
    let mut regressions = Vec::new();
    let mut delta = 0.0;
    let mut positive = 0;
    for q in qrels {
        let a = ranked_union(&control[&q.id]);
        let b = ranked_union(&candidate[&q.id]);
        let (ar, br, ah, bh) = if q.no_answer {
            if b.len() > a.len() {
                regressions.push(q.id.clone());
            }
            (None, None, Some(a.len()), Some(b.len()))
        } else {
            if q.relevant.iter().any(|r| !known.contains(&r.id)) {
                return Err(format!("{}: gold source absent from fixture", q.id));
            }
            let recall = |ranked: &[&str]| {
                q.relevant
                    .iter()
                    .filter(|r| ranked.iter().take(plan.k).any(|id| *id == r.id))
                    .count() as f64
                    / q.relevant.len() as f64
            };
            let (ar, br) = (recall(&a), recall(&b));
            if br < ar {
                regressions.push(q.id.clone());
            }
            delta += br - ar;
            positive += 1;
            (Some(ar), Some(br), None, None)
        };
        comparisons.push(QuestionComparison {
            id: q.id.clone(),
            class: q.class.clone(),
            language: q.language.clone(),
            control_recall: ar,
            candidate_recall: br,
            control_negative_hits: ah,
            candidate_negative_hits: bh,
        });
    }
    let delta = delta / positive as f64;
    let target_met = delta >= plan.min_mean_recall_delta;
    let decision = if !regressions.is_empty() || !target_met {
        Decision::Reject
    } else if delta > 0.0 {
        Decision::Accept
    } else {
        Decision::NeedsHumanReview
    };
    Ok(Comparison {
        decision,
        mean_recall_delta: delta,
        questions: comparisons,
        regressions,
        target_met,
        accepted_without_quote: 0,
        admission_gate_scope: "not_exercised_read_only_retrieval",
        input_tokens: 0,
        output_tokens: 0,
    })
}

/// Writes a run manifest even when an executed arm fails. Invalid input is
/// rejected before any execution. Existing output/evidence is never reused.
pub fn run(config: RunConfig) -> Result<Decision, String> {
    let spec_bytes = std::fs::read(&config.candidate).map_err(|e| e.to_string())?;
    let spec: CandidateSpec = serde_json::from_slice(&spec_bytes).map_err(|e| e.to_string())?;
    let registry = ComponentRegistry::load(&config.registry).map_err(|e| e.to_string())?;
    spec.validate(&registry).map_err(|e| e.to_string())?;
    if spec.surface != ChangeSurface::Runtime
        || registry.get(&spec.component).map(|c| c.surface) != Some(ChangeSurface::Runtime)
        || spec.ablation_required
    {
        return Err("this runner supports exactly one runtime surface".into());
    }
    if !["runtime.evolve_ab", "runtime.ask_vault_tools"].contains(&spec.component.as_str())
        || spec.guardrails.quote_found_rate_floor.is_some()
        || spec.guardrails.max_token_regression.is_some()
        || spec
            .guardrails
            .accepted_without_quote
            .is_some_and(|v| v != 0)
    {
        return Err(
            "unsupported component or non-retrieval guardrail; this runner cannot verify it".into(),
        );
    }
    let plan = spec
        .eval_plan
        .paired_run
        .as_ref()
        .ok_or("candidate is missing eval_plan.paired_run")?;
    plan.validate()?;
    let input = plan.fixture_dir.canonicalize().map_err(|e| e.to_string())?;
    let fixture_hashes = io::snapshot(&input)?;
    let qrels = load_qrels(&input.join("qrels"))?;
    if plan.expected_questions != qrels.len() {
        return Err("expected_questions must equal the frozen question count".into());
    }
    let index: Value = io::read_json(&input.join("vault/.ovp/index/index.json"))?;
    let known: BTreeSet<String> = index
        .get("sources")
        .and_then(Value::as_array)
        .ok_or("fixture has no source array")?
        .iter()
        .map(|s| {
            s.get("sha256")
                .and_then(Value::as_str)
                .map(str::to_string)
                .ok_or("source sha256 missing".to_string())
        })
        .collect::<Result<_, _>>()?;
    if spec.eval_plan.paired_sources != Some(known.len()) {
        return Err("paired_sources must equal the frozen source count".into());
    }
    if known.is_empty() {
        return Err("fixture source set is empty".into());
    }
    let output = io::new_output(&config.output)?;
    if output.starts_with(&input) {
        return Err("output must be outside the frozen fixture".into());
    }
    let executable = config
        .executable
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let mut manifest = json!({
        "schema": RUN_SCHEMA, "status": "running", "candidate_id": spec.id,
        "candidate_sha256": io::hash(&spec_bytes), "candidate_spec": spec,
        "registry_sha256": io::hash(&std::fs::read(&config.registry).map_err(|e| e.to_string())?),
        "fixture_files": fixture_hashes, "executable": executable, "executable_sha256": io::hash_file(&executable)?,
        "code": io::code_identity()?, "plan": plan,
        "model": null, "prompt": null, "cost_scope": "offline retrieval; no model calls",
        "arms": [], "comparison": null, "error": null
    });
    io::write_json(&output.join("manifest.json"), &manifest)?;
    let result = (|| {
        let mut reports = Vec::new();
        for (arm, mode) in [
            ("control", &plan.control_query_mode),
            ("candidate", &plan.candidate_query_mode),
        ] {
            let dest = output.join(arm);
            std::fs::create_dir(&dest).map_err(|e| e.to_string())?;
            let frozen = dest.join("fixture");
            io::copy_snapshot(&input, &frozen, &fixture_hashes)?;
            let execution = io::execute(
                &executable,
                &dest,
                &frozen,
                mode,
                plan.k,
                plan.timeout_seconds,
            );
            manifest["arms"]
                .as_array_mut()
                .unwrap()
                .push(match &execution {
                    Ok(v) => v.clone(),
                    Err(e) => json!({"arm": arm, "status": "failed", "error": e}),
                });
            io::write_json(&output.join("manifest.json"), &manifest)?;
            execution?;
            reports.push(io::read_json::<Report>(&dest.join("report.json"))?);
            if io::snapshot(&frozen)? != fixture_hashes {
                return Err(format!(
                    "{arm}: frozen fixture was mutated during evaluation"
                ));
            }
        }
        if io::snapshot(&input)? != fixture_hashes
            || io::hash_file(&executable)? != manifest["executable_sha256"]
        {
            return Err("input fixture or executable changed during paired run".into());
        }
        let candidate = reports.pop().unwrap();
        let control = reports.pop().unwrap();
        compare(plan, &qrels, &known, control, candidate)
    })();
    match result {
        Ok(comparison) => {
            manifest["status"] = json!("completed");
            manifest["comparison"] =
                serde_json::to_value(&comparison).map_err(|e| e.to_string())?;
            io::write_json(&output.join("manifest.json"), &manifest)?;
            let mut entry = LedgerEntry::new(&spec.id, &spec.component, comparison.decision);
            entry.git_sha = manifest["code"]["git_sha"].as_str().map(str::to_string);
            entry.version_from = Some(spec.base_version.clone());
            entry.version_to = Some(spec.target_version.clone());
            entry.rollback = Some(spec.rollback.clone());
            entry.scorecard_summary = json!({"manifest": output.join("manifest.json"),
                "manifest_sha256": io::hash_file(&output.join("manifest.json"))?, "comparison": comparison,
                "candidate_sha256": manifest["candidate_sha256"], "executable_sha256": manifest["executable_sha256"]});
            // Isolated operator evidence vault. This is a recorded evaluation
            // decision, not permission to promote a production prompt/model.
            crate::ledger::append_entry(&output.join(".ovp/evolution-ledger.jsonl"), &entry)
                .map_err(|e| e.to_string())?;
            Ok(comparison.decision)
        }
        Err(error) => {
            manifest["status"] = json!("invalid");
            manifest["error"] = json!(error);
            io::write_json(&output.join("manifest.json"), &manifest)?;
            Err(error)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plan() -> RetrievalPlan {
        RetrievalPlan {
            runner: "retrieval".into(),
            fixture_dir: ".".into(),
            control_query_mode: "verbatim".into(),
            candidate_query_mode: "terms".into(),
            k: 1,
            expected_questions: 1,
            min_mean_recall_delta: 0.0,
            timeout_seconds: 60,
        }
    }
    fn qrels() -> Vec<Qrel> {
        vec![Qrel {
            schema: "ovp.retrieval_eval.qrel/v1".into(),
            id: "q1".into(),
            question: "retrieval".into(),
            class: "exact".into(),
            language: "en".into(),
            confidence: "gold".into(),
            no_answer: false,
            relevant: vec![Relevant {
                surface: "source".into(),
                id: "a".into(),
            }],
        }]
    }
    fn report(mode: &str, ranks: &[&str]) -> Report {
        serde_json::from_value(json!({"schema": "ovp.retrieval_eval.report/v1",
        "query_mode": mode, "questions": 1, "per_question": [{
            "id": "q1", "tool_errors": [], "source_ranks": {
                "search_sources": ranks, "search_evidence": [], "search_claims": []
            }, "source_recall": {"union": {"@1": 999.0}}
        }]}))
        .unwrap()
    }
    fn known() -> BTreeSet<String> {
        ["a".into(), "b".into()].into()
    }
    #[test]
    fn derives_recall_from_ordered_outputs_not_reported_metrics() {
        let c = compare(
            &plan(),
            &qrels(),
            &known(),
            report("verbatim", &["b"]),
            report("terms", &["a"]),
        )
        .unwrap();
        assert_eq!(c.mean_recall_delta, 1.0);
        assert_eq!(c.decision, Decision::Accept);
        let c = compare(
            &plan(),
            &qrels(),
            &known(),
            report("verbatim", &["a"]),
            report("terms", &["b"]),
        )
        .unwrap();
        assert_eq!(c.decision, Decision::Reject);
        assert_eq!(c.regressions, ["q1"]);
    }
    #[test]
    fn identical_outputs_require_review_instead_of_claiming_improvement() {
        let c = compare(
            &plan(),
            &qrels(),
            &known(),
            report("verbatim", &["a"]),
            report("terms", &["a"]),
        )
        .unwrap();
        assert_eq!(c.decision, Decision::NeedsHumanReview);
    }
    #[test]
    fn incomplete_failed_and_unknown_source_outputs_are_invalid() {
        let mut bad = report("terms", &["a"]);
        bad.per_question.clear();
        assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
        let mut bad = report("terms", &["a"]);
        bad.per_question[0].tool_errors.push("timeout".into());
        assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
        let mut bad = report("terms", &["a"]);
        bad.per_question[0].source_ranks.remove("search_claims");
        assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
        assert!(
            compare(
                &plan(),
                &qrels(),
                &known(),
                report("verbatim", &["a"]),
                report("terms", &["foreign"])
            )
            .is_err()
        );
    }
    #[test]
    fn missing_observation_fields_do_not_default_to_zero() {
        assert!(
            serde_json::from_value::<Report>(json!({"schema":"ovp.retrieval_eval.report/v1",
            "query_mode":"terms", "questions":1, "per_question":[{"id":"q1"}]}))
            .is_err()
        );
    }
    #[test]
    fn rejects_duplicate_ids_and_policy_mismatch() {
        let mut bad = report("terms", &["a"]);
        bad.per_question[0].id = "other".into();
        assert!(compare(&plan(), &qrels(), &known(), report("verbatim", &["a"]), bad).is_err());
        assert!(
            compare(
                &plan(),
                &qrels(),
                &known(),
                report("verbatim", &["a"]),
                report("verbatim", &["a"])
            )
            .is_err()
        );
    }
    #[test]
    fn stronger_preregistered_target_rejects_no_improvement() {
        let mut p = plan();
        p.min_mean_recall_delta = 0.2;
        assert_eq!(
            compare(
                &p,
                &qrels(),
                &known(),
                report("verbatim", &["a"]),
                report("terms", &["a"])
            )
            .unwrap()
            .decision,
            Decision::Reject
        );
        p.k = 0;
        assert!(p.validate().is_err());
        p.k = 1;
        p.min_mean_recall_delta = f64::NAN;
        assert!(p.validate().is_err());
    }
    #[test]
    fn negative_question_regression_is_not_hidden_by_positive_gain() {
        let mut qs = qrels();
        qs.push(Qrel {
            schema: "ovp.retrieval_eval.qrel/v1".into(),
            id: "negative".into(),
            question: "missing".into(),
            class: "negative".into(),
            language: "zh".into(),
            confidence: "gold".into(),
            no_answer: true,
            relevant: vec![],
        });
        let mut a = report("verbatim", &["b"]);
        let mut b = report("terms", &["a"]);
        a.questions = 2;
        b.questions = 2;
        let mut ar = report("verbatim", &[]).per_question.remove(0);
        ar.id = "negative".into();
        let mut br = report("terms", &["b"]).per_question.remove(0);
        br.id = "negative".into();
        a.per_question.push(ar);
        b.per_question.push(br);
        let c = compare(&plan(), &qs, &known(), a, b).unwrap();
        assert_eq!(c.mean_recall_delta, 1.0);
        assert_eq!(c.decision, Decision::Reject);
    }
}
