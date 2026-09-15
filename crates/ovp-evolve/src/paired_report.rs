use crate::{paired::RetrievalPlan, paired_io as io, types::Decision};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

#[derive(Debug, Deserialize)]
pub(super) struct Qrel {
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
pub(super) struct Report {
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

pub(super) fn load_qrels(dir: &Path) -> Result<Vec<Qrel>, String> {
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
            if let Some(id) = list.get(i)
                && !union.contains(&id.as_str())
            {
                union.push(id.as_str());
            }
        }
    }
    union
}

pub(super) fn compare(
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

#[cfg(test)]
#[path = "paired_tests.rs"]
mod tests;
