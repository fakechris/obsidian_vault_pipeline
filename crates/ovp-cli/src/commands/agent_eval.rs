//! `agent-eval` — the END-TO-END R0: drive the REAL ask-agent loop (the
//! production config recipe: AGENT_POLICY system, production model default,
//! max_rounds 10) over qrel questions and measure ANSWER-level quality, not
//! tool-level recall — the objective the retrieval track ultimately serves:
//!
//! - citation validity: cited ids that resolve against the live index/ledger
//! - gold hit: whether any cited source (directly, or via a cited claim's
//!   receipts) is in the qrel's gold set
//! - abstain honesty on no_answer questions (zero-citation proxy, raw
//!   answers preserved for human reading)
//! - cost: rounds, tokens, wall time, stopped_reason
//!
//! Live LLM required (`--features anthropic`, providers.toml key). Sessions
//! land in `.ovp/ask-sessions` under an `agenteval-` prefix so future
//! transcript mining can filter them out.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::{Value, json};

use crate::CliError;

pub struct AgentEvalArgs {
    pub vault_root: PathBuf,
    /// Qrel file or directory (same format as retrieval-eval).
    pub qrels: PathBuf,
    /// Only run these qrel ids (empty = all).
    pub ids: Vec<String>,
    pub out: PathBuf,
    /// Cap on questions actually run (cost guard).
    pub max_questions: usize,
}

#[derive(Debug, Deserialize)]
struct Qrel {
    schema: String,
    id: String,
    question: String,
    #[serde(default)]
    language: Option<String>,
    #[serde(default)]
    class: Option<String>,
    #[serde(default)]
    relevant: Vec<Relevant>,
    #[serde(default)]
    no_answer: bool,
}

#[derive(Debug, Deserialize)]
struct Relevant {
    surface: String,
    id: String,
}

pub fn run(args: AgentEvalArgs) -> Result<(), CliError> {
    use ovp_memory::agent::{AgentConfig, run_agent_turn_with_progress};
    use ovp_memory::agent_transcript::SessionStore;
    use ovp_memory::vault_tools::VaultTools;
    use ovp_memory::verify::citations_in_order;

    let Some(factory) = ovp_server::providers_ask_client_factory(args.vault_root.clone()) else {
        return Err(CliError::Io(
            "agent-eval needs a live model: build with --features anthropic".into(),
        ));
    };

    let mut qrels = load_qrels(&args.qrels)?;
    if !args.ids.is_empty() {
        qrels.retain(|q| args.ids.contains(&q.id));
    }
    qrels.truncate(args.max_questions.max(1));
    if qrels.is_empty() {
        return Err(CliError::Io("no qrel records selected".into()));
    }

    // Resolve claim receipts once: a cited claim counts as a gold hit when
    // one of ITS sources is gold — the claim layer is a legitimate answer
    // route, not a citation-precision loophole.
    let model = ovp_index::read_index(&args.vault_root)
        .map_err(|e| CliError::Io(format!("index: {e}")))?;
    let generation = json!({
        "built_at": model.built_at,
        "run_id": model.run_id,
    });
    let claim_sources: BTreeMap<String, Vec<String>> = model
        .claims
        .iter()
        .filter_map(|c| {
            c.claim_key
                .clone()
                .map(|k| (k, resolved_claim_source_ids(&model, c)))
        })
        .collect();
    let known_sources: std::collections::BTreeSet<&str> =
        model.sources.iter().map(|s| s.sha256.as_str()).collect();

    let epoch_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let mut rows = Vec::new();
    for (i, q) in qrels.iter().enumerate() {
        eprintln!("[{}/{}] {} …", i + 1, qrels.len(), q.id);
        let session = format!("agenteval-{}-{}", q.id, epoch_ms);
        // SessionStore takes the SESSIONS DIR, not the vault root — passing
        // the root scattered transcripts into the vault top level once.
        let sessions_dir = args.vault_root.join(".ovp/ask-sessions");
        let mut store = SessionStore::open(&sessions_dir, &session)
            .map_err(|e| CliError::Io(format!("session store: {e}")))?;
        let mut client = factory().map_err(CliError::Io)?;
        let cfg = AgentConfig {
            // The production recipe (server handle_ask_agent): policy
            // prompt, default model name, answer headroom, deep rounds.
            model: ovp_memory::ask::AskArgs::default().model_name,
            system: ovp_memory::agent_policy::AGENT_POLICY.to_string(),
            max_tokens: 4096,
            max_rounds: 10,
            deadline: Duration::from_secs(180),
            ..AgentConfig::default()
        };
        let mut tools = VaultTools::new(&args.vault_root)
            .with_result_cap(cfg.max_result_bytes.saturating_sub(2 * 1024));
        let started = Instant::now();
        let outcome = run_agent_turn_with_progress(
            client.as_mut(),
            &mut tools,
            &mut store,
            &q.question,
            None,
            &cfg,
            None,
        );
        let wall_ms = started.elapsed().as_millis() as u64;
        let row = match outcome {
            Ok(o) => {
                let cited = citations_in_order(&o.answer);
                let gold: std::collections::BTreeSet<&str> = q
                    .relevant
                    .iter()
                    .filter(|r| r.surface == "source")
                    .map(|r| r.id.as_str())
                    .collect();
                let mut valid = 0usize;
                let mut gold_hit = false;
                // Source-attributed citation set for PRECISION: gold-hit is
                // boolean recall; precision says how much of the citation
                // volume actually points at gold (the 85-citation spam
                // problem the boolean cannot see).
                let mut attributed: std::collections::BTreeSet<String> =
                    std::collections::BTreeSet::new();
                for c in &cited {
                    if let Some(sid) = c.strip_prefix("source:") {
                        let full = known_sources
                            .iter()
                            .find(|k| k.starts_with(sid))
                            .copied();
                        if let Some(full) = full {
                            valid += 1;
                            attributed.insert(full.to_string());
                            if gold.iter().any(|g| *g == full) {
                                gold_hit = true;
                            }
                        }
                    } else if let Some(key) = c.strip_prefix("claim:") {
                        if let Some(sources) = claim_sources.get(key) {
                            valid += 1;
                            attributed.extend(sources.iter().cloned());
                            if sources.iter().any(|s| gold.contains(s.as_str())) {
                                gold_hit = true;
                            }
                        }
                    } else {
                        // unit:/card: citations resolve inside packs — count
                        // as valid-shaped; gold attribution via sources only.
                        valid += 1;
                    }
                }
                let citation_precision = if attributed.is_empty() {
                    Value::Null
                } else {
                    json!(
                        attributed.iter().filter(|s| gold.contains(s.as_str())).count() as f64
                            / attributed.len() as f64
                    )
                };
                let abstained = cited.is_empty();
                json!({
                    "id": q.id,
                    "class": q.class,
                    "language": q.language,
                    "no_answer": q.no_answer,
                    "session": session,
                    "stopped_reason": format!("{:?}", o.stopped_reason),
                    "rounds": o.rounds,
                    "input_tokens": o.input_tokens_total,
                    "output_tokens": o.output_tokens_total,
                    "wall_ms": wall_ms,
                    "citations": cited,
                    "citations_valid": valid,
                    "attributed_sources": attributed.len(),
                    "citation_precision": citation_precision,
                    "gold_source_cited": gold_hit,
                    "abstained": abstained,
                    "abstain_correct": q.no_answer == abstained,
                    "answer_head": o.answer.chars().take(400).collect::<String>(),
                })
            }
            Err(e) => json!({
                "id": q.id,
                "class": q.class,
                "session": session,
                "error": format!("{e:?}"),
                "wall_ms": wall_ms,
            }),
        };
        rows.push(row);
    }

    let ran: Vec<&Value> = rows.iter().filter(|r| r.get("error").is_none()).collect();
    let answerable: Vec<&&Value> = ran
        .iter()
        .filter(|r| r["no_answer"] == json!(false))
        .collect();
    let summary = json!({
        "questions_run": rows.len(),
        "errors": rows.len() - ran.len(),
        "gold_source_cited_rate": rate(&answerable, |r| r["gold_source_cited"] == json!(true)),
        "citation_validity": {
            "cited_total": ran.iter().map(|r| r["citations"].as_array().map_or(0, Vec::len)).sum::<usize>(),
            "valid_total": ran.iter().filter_map(|r| r["citations_valid"].as_u64()).sum::<u64>(),
        },
        "abstain_correct_rate": rate(&ran.iter().collect::<Vec<_>>(), |r| r["abstain_correct"] == json!(true)),
        "mean_citation_precision": mean(&ran, "citation_precision"),
        "mean_rounds": mean(&ran, "rounds"),
        "mean_output_tokens": mean(&ran, "output_tokens"),
        "mean_wall_ms": mean(&ran, "wall_ms"),
    });
    let report = json!({
        "schema": "ovp.retrieval_eval.agent_eval/v1",
        "generation": generation,
        "summary": summary,
        "per_question": rows,
    });
    std::fs::write(
        &args.out,
        serde_json::to_string_pretty(&report).map_err(|e| CliError::Io(format!("json: {e}")))?,
    )
    .map_err(|e| CliError::Io(format!("writing {}: {e}", args.out.display())))?;
    println!("wrote {}", args.out.display());
    Ok(())
}

fn resolved_claim_source_ids(model: &ovp_index::IndexModel, c: &ovp_index::ClaimRow) -> Vec<String> {
    // ClaimRow.sources are case ids; resolve to sha256 via the SAME packs
    // join search_claims uses, so gold attribution matches the tool layer.
    let mut resolved = ovp_memory::vault_tools::resolved_source_ids(model, &c.sources);
    // Some lanes already carry shas — keep raw entries that look like shas.
    resolved.extend(c.sources.iter().filter(|s| s.len() == 64).cloned());
    resolved
}

fn rate(rows: &[&&Value], pred: impl Fn(&Value) -> bool) -> Value {
    if rows.is_empty() {
        return Value::Null;
    }
    json!(rows.iter().filter(|r| pred(r)).count() as f64 / rows.len() as f64)
}

fn mean(rows: &[&Value], key: &str) -> Value {
    let vals: Vec<f64> = rows.iter().filter_map(|r| r[key].as_f64()).collect();
    if vals.is_empty() {
        Value::Null
    } else {
        json!(vals.iter().sum::<f64>() / vals.len() as f64)
    }
}

fn load_qrels(path: &PathBuf) -> Result<Vec<Qrel>, CliError> {
    let mut files = Vec::new();
    if path.is_dir() {
        for entry in std::fs::read_dir(path)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", path.display())))?
        {
            let p = entry
                .map_err(|e| CliError::Io(format!("reading {}: {e}", path.display())))?
                .path();
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if name.ends_with(".json") && !name.starts_with("SUMMARY") {
                files.push(p);
            }
        }
        files.sort();
    } else {
        files.push(path.clone());
    }
    let mut out = Vec::new();
    for f in files {
        let raw = std::fs::read_to_string(&f)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", f.display())))?;
        let q: Qrel = serde_json::from_str(&raw)
            .map_err(|e| CliError::Io(format!("parsing {}: {e}", f.display())))?;
        if q.schema.starts_with("ovp.retrieval_eval.qrel") {
            out.push(q);
        }
    }
    Ok(out)
}
