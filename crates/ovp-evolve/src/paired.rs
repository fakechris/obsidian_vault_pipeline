//! Executed, offline retrieval experiments. Unlike the legacy scorecard, this
//! path derives metrics from ordered tool outputs and frozen qrels. It makes
//! no claim about generated answers, semantic support, or LLM cost.
//! Candidate: evolve-ab-runtime-v1 (INV-498).
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::paired_io as io;
use crate::paired_report::{Comparison, Qrel, Report, compare, load_qrels};
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
struct Prepared {
    spec: CandidateSpec,
    plan: RetrievalPlan,
    input: PathBuf,
    files: BTreeMap<PathBuf, String>,
    qrels: Vec<Qrel>,
    known: BTreeSet<String>,
    output: PathBuf,
    executable: PathBuf,
    manifest: Value,
}

fn load_spec(config: &RunConfig) -> Result<(CandidateSpec, Vec<u8>, Vec<u8>), String> {
    let bytes = std::fs::read(&config.candidate).map_err(|e| e.to_string())?;
    let spec: CandidateSpec = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let registry_bytes = std::fs::read(&config.registry).map_err(|e| e.to_string())?;
    let registry: ComponentRegistry =
        serde_json::from_slice(&registry_bytes).map_err(|e| e.to_string())?;
    registry.validate().map_err(|e| e.to_string())?;
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
    spec.eval_plan
        .paired_run
        .as_ref()
        .ok_or("candidate is missing eval_plan.paired_run")?
        .validate()?;
    Ok((spec, bytes, registry_bytes))
}

fn source_ids(input: &Path, count: Option<usize>) -> Result<BTreeSet<String>, String> {
    let index: Value = io::read_json(&input.join("vault/.ovp/index/index.json"))?;
    let sources = index
        .get("sources")
        .and_then(Value::as_array)
        .ok_or("fixture has no source array")?;
    let known: BTreeSet<String> = sources
        .iter()
        .map(|s| {
            s.get("sha256")
                .and_then(Value::as_str)
                .map(str::to_string)
                .ok_or("source sha256 missing".to_string())
        })
        .collect::<Result<_, _>>()?;
    if known.is_empty() || known.len() != sources.len() || count != Some(known.len()) {
        return Err("paired_sources must equal a nonempty, unique frozen source set".into());
    }
    Ok(known)
}

impl Prepared {
    fn new(config: RunConfig) -> Result<Self, String> {
        let (spec, spec_bytes, registry_bytes) = load_spec(&config)?;
        let plan = spec.eval_plan.paired_run.clone().unwrap();
        let input = plan.fixture_dir.canonicalize().map_err(|e| e.to_string())?;
        let files = io::snapshot(&input)?;
        let qrels = load_qrels(&input.join("qrels"))?;
        if plan.expected_questions != qrels.len() {
            return Err("expected_questions must equal the frozen question count".into());
        }
        let known = source_ids(&input, spec.eval_plan.paired_sources)?;
        let output = io::new_output(&config.output)?;
        if output.starts_with(&input) {
            return Err("output must be outside the frozen fixture".into());
        }
        let executable = config
            .executable
            .canonicalize()
            .map_err(|e| e.to_string())?;
        let manifest = json!({
            "schema": RUN_SCHEMA, "status": "running", "candidate_id": spec.id,
            "candidate_sha256": io::hash(&spec_bytes), "candidate_spec": spec,
            "registry_sha256": io::hash(&registry_bytes), "fixture_files": files,
            "executable": executable, "executable_sha256": io::hash_file(&executable)?,
            "code": io::code_identity()?, "plan": plan,
            "model": null, "prompt": null, "cost_scope": "offline retrieval; no model calls",
            "arms": [], "comparison": null, "error": null
        });
        io::write_json(&output.join("manifest.json"), &manifest)?;
        Ok(Self {
            spec,
            plan,
            input,
            files,
            qrels,
            known,
            output,
            executable,
            manifest,
        })
    }

    fn execute(&mut self) -> Result<Comparison, String> {
        let mut reports = Vec::new();
        for (arm, mode) in [
            ("control", &self.plan.control_query_mode),
            ("candidate", &self.plan.candidate_query_mode),
        ] {
            let dest = self.output.join(arm);
            std::fs::create_dir(&dest).map_err(|e| e.to_string())?;
            let frozen = dest.join("fixture");
            io::copy_snapshot(&self.input, &frozen, &self.files)?;
            let mut receipt = crate::paired_process::execute(
                &self.executable,
                &dest,
                &frozen,
                mode,
                self.plan.k,
                self.plan.timeout_seconds,
            )
            .unwrap_or_else(|e| json!({"status": "failed", "error": e}));
            receipt["arm"] = json!(arm);
            self.manifest["arms"]
                .as_array_mut()
                .unwrap()
                .push(receipt.clone());
            io::write_json(&self.output.join("manifest.json"), &self.manifest)?;
            if receipt["status"] != "completed" {
                return Err(format!("{arm}: {}", receipt["error"]));
            }
            reports.push(crate::paired_process::read_report::<Report>(
                &dest, &receipt,
            )?);
            if io::snapshot(&frozen)? != self.files {
                return Err(format!(
                    "{arm}: frozen fixture was mutated during evaluation"
                ));
            }
        }
        if io::snapshot(&self.input)? != self.files
            || io::hash_file(&self.executable)? != self.manifest["executable_sha256"]
        {
            return Err("input fixture or executable changed during paired run".into());
        }
        for receipt in self.manifest["arms"].as_array().unwrap() {
            let dest = self.output.join(receipt["arm"].as_str().unwrap());
            crate::paired_process::verify_evidence(&dest, receipt)?;
            if io::snapshot(&dest.join("fixture"))? != self.files {
                return Err("arm fixture changed before decision".into());
            }
        }
        let candidate = reports.pop().unwrap();
        let control = reports.pop().unwrap();
        compare(&self.plan, &self.qrels, &self.known, control, candidate)
    }

    fn finish(&mut self, result: Result<Comparison, String>) -> Result<Decision, String> {
        match result {
            Ok(comparison) => {
                self.manifest["status"] = json!("completed");
                self.manifest["comparison"] =
                    serde_json::to_value(&comparison).map_err(|e| e.to_string())?;
                let path = self.output.join("manifest.json");
                io::write_json(&path, &self.manifest)?;
                let spec = &self.spec;
                let mut entry = LedgerEntry::new(&spec.id, &spec.component, comparison.decision);
                entry.git_sha = self.manifest["code"]["git_sha"]
                    .as_str()
                    .map(str::to_string);
                entry.version_from = Some(spec.base_version.clone());
                entry.version_to = Some(spec.target_version.clone());
                entry.rollback = Some(spec.rollback.clone());
                entry.scorecard_summary = json!({"manifest": path,
                    "manifest_sha256": io::hash_file(&path)?, "comparison": comparison,
                    "candidate_sha256": self.manifest["candidate_sha256"],
                    "executable_sha256": self.manifest["executable_sha256"]});
                // An isolated evaluation record, not production promotion authority.
                crate::ledger::append_entry(
                    &self.output.join(".ovp/evolution-ledger.jsonl"),
                    &entry,
                )
                .map_err(|e| e.to_string())?;
                Ok(comparison.decision)
            }
            Err(error) => {
                self.manifest["status"] = json!("invalid");
                self.manifest["error"] = json!(error);
                io::write_json(&self.output.join("manifest.json"), &self.manifest)?;
                Err(error)
            }
        }
    }
}

/// Executes frozen offline arms and persists valid or invalid run evidence.
/// Invalid input is rejected before execution. Existing evidence is never reused.
pub fn run(config: RunConfig) -> Result<Decision, String> {
    let mut run = Prepared::new(config)?;
    let result = run.execute();
    run.finish(result)
}
