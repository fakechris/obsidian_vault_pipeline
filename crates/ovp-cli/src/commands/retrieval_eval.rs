//! `retrieval-eval` — the R0 baseline harness (docs/design/retrieval-eval.md):
//! run each qrel question verbatim against the ask vault-tool surface
//! (search_sources / search_evidence / search_claims through the REAL
//! dispatch path, fts lane included when the shadow serves) and report
//! bucketed candidate recall. Read-only over the projections; the report is
//! a run artifact, never ledger state.
//!
//! This measures the TOOL layer under a fixed policy (query = question), not
//! the agent loop — reformulation quality is an agent metric, tracked
//! separately in transcripts. Buckets are never averaged into one composite
//! score: a change that helps the semantic cohort while regressing exact-ID
//! lookups must stay visible.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

use ovp_memory::agent::{ToolExecutor, ToolOutcome};
use ovp_memory::vault_tools::VaultTools;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::CliError;

pub struct RetrievalEvalArgs {
    pub vault_root: PathBuf,
    /// A qrel JSON file or a directory of `q-*.json` files.
    pub qrels: PathBuf,
    /// Cutoffs, e.g. [10, 20]. Tool fetch limit is max(k).
    pub ks: Vec<usize>,
    /// Only score records with confidence == "gold".
    pub gold_only: bool,
    /// Write the JSON report here (stdout when absent).
    pub out: Option<PathBuf>,
    /// How the question becomes a tool query. `verbatim` = the question as
    /// typed (the baseline policy); `terms` = deterministic distinctive-term
    /// extraction (request-noise stripped) — the fast-planner EXPERIMENT arm,
    /// eval-only, not production behavior.
    pub query_mode: QueryMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueryMode {
    Verbatim,
    Terms,
}

/// One qrel record — accepts both the promoted (`qrel/v1`) and draft
/// (`qrel_draft/v1`) kinds so a baseline can run before promotion; the
/// report carries per-confidence counts so a draft-heavy run cannot read
/// as a gold verdict.
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
    #[serde(default)]
    confidence: Option<String>,
}

#[derive(Debug, Deserialize)]
struct Relevant {
    surface: String,
    id: String,
    #[serde(default)]
    grade: Option<u8>,
}

#[derive(Debug, Serialize)]
struct QuestionReport {
    id: String,
    /// The query actually sent to the tools (differs from the question in
    /// `terms` mode — keep it visible so a bad extraction is debuggable).
    effective_query: String,
    class: String,
    language: String,
    confidence: String,
    no_answer: bool,
    gold_sources: usize,
    gold_claims: usize,
    lanes: BTreeMap<String, String>,
    /// tool → k → recall over gold SOURCE ids (absent when no source gold).
    source_recall: BTreeMap<String, BTreeMap<String, f64>>,
    /// k → recall over gold claim ids/keys via search_claims.
    claim_recall: BTreeMap<String, f64>,
    /// For no_answer records: hits any tool returned (should be noise-only).
    hits_returned: usize,
    tool_errors: Vec<String>,
}

const TOOL_BUDGET: Duration = Duration::from_secs(60);

pub fn run(args: RetrievalEvalArgs) -> Result<(), CliError> {
    let ks = if args.ks.is_empty() {
        vec![10, 20]
    } else {
        args.ks.clone()
    };
    let limit = ks.iter().copied().max().unwrap_or(20).clamp(1, 50);
    let qrels = load_qrels(&args.qrels)?;
    let qrels: Vec<Qrel> = qrels
        .into_iter()
        .filter(|q| !args.gold_only || q.confidence.as_deref() == Some("gold"))
        .collect();
    if qrels.is_empty() {
        return Err(CliError::Io(format!(
            "no qrel records under {} (gold_only={})",
            args.qrels.display(),
            args.gold_only
        )));
    }

    let mut tools = VaultTools::new(&args.vault_root);
    let mut rows = Vec::new();
    for q in &qrels {
        rows.push(score_question(&mut tools, q, &ks, limit, args.query_mode));
    }

    let report = assemble_report(&qrels, rows, &ks, args.query_mode);
    let text = serde_json::to_string_pretty(&report)
        .map_err(|e| CliError::Io(format!("serializing report: {e}")))?;
    match &args.out {
        Some(path) => {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)
                    .map_err(|e| CliError::Io(format!("creating {}: {e}", parent.display())))?;
            }
            std::fs::write(path, &text)
                .map_err(|e| CliError::Io(format!("writing {}: {e}", path.display())))?;
            println!("wrote {}", path.display());
        }
        None => println!("{text}"),
    }
    Ok(())
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
            if name.starts_with("q-") && name.ends_with(".json") {
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
        if !q.schema.starts_with("ovp.retrieval_eval.qrel") {
            return Err(CliError::Io(format!(
                "{}: unexpected schema {}",
                f.display(),
                q.schema
            )));
        }
        out.push(q);
    }
    Ok(out)
}

/// Run one tool and return (ordered candidate source ids, ordered claim
/// ids+keys, lane, error). A failed tool scores 0 recall but is REPORTED —
/// a crash must not read as "no relevant documents".
fn run_tool(
    tools: &mut VaultTools,
    name: &str,
    query: &str,
    limit: usize,
) -> (Vec<String>, Vec<String>, Option<String>, Option<String>) {
    let input = json!({"query": query, "limit": limit});
    match tools.execute(name, &input, TOOL_BUDGET) {
        ToolOutcome::Ok(raw) => {
            let v: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
            let lane = v["lane"].as_str().map(str::to_string);
            let mut sources = Vec::new();
            let mut claims = Vec::new();
            if let Some(hits) = v["hits"].as_array() {
                for hit in hits {
                    if let Some(sid) = hit["source_id"].as_str() {
                        if !sources.contains(&sid.to_string()) {
                            sources.push(sid.to_string());
                        }
                    }
                    for key in ["claim_key", "claim_id"] {
                        if let Some(cid) = hit[key].as_str() {
                            if !claims.contains(&cid.to_string()) {
                                claims.push(cid.to_string());
                            }
                        }
                    }
                    if let Some(sids) = hit["source_ids"].as_array() {
                        for sid in sids.iter().filter_map(Value::as_str) {
                            if !sources.contains(&sid.to_string()) {
                                sources.push(sid.to_string());
                            }
                        }
                    }
                }
            }
            (sources, claims, lane, None)
        }
        ToolOutcome::InvalidArgs(e) | ToolOutcome::Failed(e) => {
            (Vec::new(), Vec::new(), None, Some(format!("{name}: {e}")))
        }
    }
}

/// Deterministic distinctive-term extraction — the `terms` experiment arm.
/// A question as typed carries request scaffolding ("帮我找一篇…的文章",
/// "give me two … with citations") whose CJK bigrams flood BM25 and bury
/// the content terms (measured: verbatim R@20 ≈ 0 where a clean query
/// scores 0.4). Strategy: latin words pass a stopword list; CJK runs have
/// request-phrase substrings blanked (longest first), then surviving
/// fragments of >= 2 chars count as terms. Cap 8 — matching MAX_SEARCH_TERMS
/// headroom, and an honest planner should be selective anyway.
fn extract_terms(question: &str) -> String {
    const LATIN_STOP: &[&str] = &[
        "vault", "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was",
        "what", "which", "that", "this", "with", "about", "please", "find", "give", "show", "me",
        "my", "our", "how", "do", "does", "did", "you", "your", "there", "any",
    ];
    // Longest first: 有哪些 must strip before 哪些, 帮我找一篇 before 帮我.
    const ZH_NOISE: &[&str] = &[
        "帮我找一篇",
        "帮我找",
        "帮我",
        "请问",
        "找一篇",
        "一篇",
        "那篇",
        "这篇",
        "文章",
        "有哪些",
        "是哪些",
        "哪些",
        "是什么",
        "什么",
        "给两条",
        "给出",
        "给我",
        "带引用",
        "引用",
        "结论",
        "关于",
        "里面",
        "我记得",
        "我看过",
        "有没有",
        "看过",
        "记得",
        "总结一下",
        "总结",
        "介绍一下",
        "介绍",
        "讲了",
        "说了",
        "内容",
        "笔记",
        "分享了",
        "还",
        "了",
        "的",
        "里",
        "吗",
        "呢",
        "和",
        "与",
        "在",
        "个",
    ];
    let mut terms: Vec<String> = Vec::new();
    let mut push = |t: &str| {
        let t = t.trim();
        if !t.is_empty() && terms.len() < 8 && !terms.iter().any(|x| x == t) {
            terms.push(t.to_string());
        }
    };
    // Split the question into latin/CJK segments first so noise stripping
    // never touches latin words (滤掉 "durable" 里不会出现中文噪声,反之亦然).
    let mut latin = String::new();
    let mut cjk = String::new();
    let mut flush_latin = |buf: &mut String, push: &mut dyn FnMut(&str)| {
        for word in buf.split_whitespace() {
            let w = word.to_lowercase();
            let w = w.trim_matches(|c: char| !c.is_alphanumeric());
            if w.len() >= 2 && !LATIN_STOP.contains(&w) {
                push(w);
            }
        }
        buf.clear();
    };
    let mut flush_cjk = |buf: &mut String, push: &mut dyn FnMut(&str)| {
        let mut s = buf.clone();
        for noise in ZH_NOISE {
            s = s.replace(noise, " ");
        }
        for frag in s.split_whitespace() {
            if frag.chars().count() >= 2 {
                push(frag);
            }
        }
        buf.clear();
    };
    for ch in question.chars() {
        let is_cjk = (ch as u32) >= 0x2E80;
        if is_cjk {
            flush_latin(&mut latin, &mut push);
            cjk.push(ch);
        } else if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' || ch == '.' {
            flush_cjk(&mut cjk, &mut push);
            latin.push(ch);
        } else {
            // Separator: flush both sides so punctuation splits runs.
            flush_latin(&mut latin, &mut push);
            flush_cjk(&mut cjk, &mut push);
            latin.push(' ');
        }
    }
    flush_latin(&mut latin, &mut push);
    flush_cjk(&mut cjk, &mut push);
    if terms.is_empty() {
        // A question that is ALL scaffolding degrades to verbatim rather
        // than an empty query (which every tool rejects).
        return question.to_string();
    }
    terms.join(" ")
}

/// Interleave ranked lists round-robin with dedup: rank-0 of every list,
/// then rank-1 of every list, and so on.
fn round_robin_union(lists: &[Vec<String>]) -> Vec<String> {
    let mut out = Vec::new();
    let max_len = lists.iter().map(Vec::len).max().unwrap_or(0);
    for i in 0..max_len {
        for list in lists {
            if let Some(id) = list.get(i) {
                if !out.contains(id) {
                    out.push(id.clone());
                }
            }
        }
    }
    out
}

fn recall_at(golds: &[String], ranked: &[String], k: usize) -> f64 {
    if golds.is_empty() {
        return 0.0;
    }
    let top: Vec<&String> = ranked.iter().take(k).collect();
    let hit = golds
        .iter()
        .filter(|g| top.iter().any(|r| *r == *g))
        .count();
    hit as f64 / golds.len() as f64
}

fn score_question(
    tools: &mut VaultTools,
    q: &Qrel,
    ks: &[usize],
    limit: usize,
    mode: QueryMode,
) -> QuestionReport {
    let effective_query = match mode {
        QueryMode::Verbatim => q.question.clone(),
        QueryMode::Terms => extract_terms(&q.question),
    };
    let gold_sources: Vec<String> = q
        .relevant
        .iter()
        .filter(|r| r.surface == "source")
        .map(|r| r.id.clone())
        .collect();
    let gold_claims: Vec<String> = q
        .relevant
        .iter()
        .filter(|r| r.surface == "claim")
        .map(|r| r.id.clone())
        .collect();

    let mut lanes = BTreeMap::new();
    let mut source_recall: BTreeMap<String, BTreeMap<String, f64>> = BTreeMap::new();
    let mut claim_recall = BTreeMap::new();
    let mut tool_errors = Vec::new();
    let mut per_tool_sources: Vec<Vec<String>> = Vec::new();
    let mut hits_returned = 0usize;

    for tool in ["search_sources", "search_evidence", "search_claims"] {
        let (sources, claims, lane, err) = run_tool(tools, tool, &effective_query, limit);
        hits_returned += sources.len().max(claims.len());
        if let Some(lane) = lane {
            lanes.insert(tool.to_string(), lane);
        }
        if let Some(err) = err {
            tool_errors.push(err);
        }
        per_tool_sources.push(sources.clone());
        if !gold_sources.is_empty() {
            let per_k: BTreeMap<String, f64> = ks
                .iter()
                .map(|k| (format!("@{k}"), recall_at(&gold_sources, &sources, *k)))
                .collect();
            source_recall.insert(tool.to_string(), per_k);
        }
        if tool == "search_claims" && !gold_claims.is_empty() {
            claim_recall = ks
                .iter()
                .map(|k| (format!("@{k}"), recall_at(&gold_claims, &claims, *k)))
                .collect();
        }
    }
    if !gold_sources.is_empty() {
        // Round-robin interleave, NOT concatenation: one noisy tool must not
        // fill the union's top-k and make the union score BELOW its best
        // member (rank i from every tool precedes rank i+1 from any).
        let union_sources = round_robin_union(&per_tool_sources);
        let per_k: BTreeMap<String, f64> = ks
            .iter()
            .map(|k| {
                (
                    format!("@{k}"),
                    recall_at(&gold_sources, &union_sources, *k),
                )
            })
            .collect();
        source_recall.insert("union".to_string(), per_k);
    }

    QuestionReport {
        id: q.id.clone(),
        effective_query,
        class: q.class.clone().unwrap_or_else(|| "unclassified".into()),
        language: q.language.clone().unwrap_or_else(|| "unknown".into()),
        confidence: q.confidence.clone().unwrap_or_else(|| "unknown".into()),
        no_answer: q.no_answer,
        gold_sources: gold_sources.len(),
        gold_claims: gold_claims.len(),
        lanes,
        source_recall,
        claim_recall,
        hits_returned,
        tool_errors,
    }
}

fn assemble_report(
    qrels: &[Qrel],
    rows: Vec<QuestionReport>,
    ks: &[usize],
    mode: QueryMode,
) -> Value {
    // Buckets: mean union source-recall per class and per language, scored
    // ONLY over records that carry source gold — a bucket with none reports
    // n=0 rather than a fabricated 0.0 average.
    let mut buckets: BTreeMap<String, Vec<&QuestionReport>> = BTreeMap::new();
    for row in &rows {
        buckets
            .entry(format!("class:{}", row.class))
            .or_default()
            .push(row);
        buckets
            .entry(format!("language:{}", row.language))
            .or_default()
            .push(row);
    }
    let bucket_stats: BTreeMap<String, Value> = buckets
        .iter()
        .map(|(name, members)| {
            let scored: Vec<&&QuestionReport> = members
                .iter()
                .filter(|r| r.gold_sources > 0 && !r.no_answer)
                .collect();
            let mut means = BTreeMap::new();
            for k in ks {
                let key = format!("@{k}");
                let vals: Vec<f64> = scored
                    .iter()
                    .filter_map(|r| r.source_recall.get("union").and_then(|m| m.get(&key)))
                    .copied()
                    .collect();
                let mean = if vals.is_empty() {
                    Value::Null
                } else {
                    json!(vals.iter().sum::<f64>() / vals.len() as f64)
                };
                means.insert(format!("union_source_recall{key}"), mean);
            }
            (
                name.clone(),
                json!({"n": members.len(), "n_scored": scored.len(), "means": means}),
            )
        })
        .collect();

    let mut confidence_counts: BTreeMap<String, usize> = BTreeMap::new();
    for q in qrels {
        *confidence_counts
            .entry(q.confidence.clone().unwrap_or_else(|| "unknown".into()))
            .or_default() += 1;
    }
    let negatives_with_hits = rows
        .iter()
        .filter(|r| r.no_answer && r.hits_returned > 0)
        .count();

    json!({
        "schema": "ovp.retrieval_eval.report/v1",
        "query_mode": match mode {
            QueryMode::Verbatim => "verbatim",
            QueryMode::Terms => "terms",
        },
        "ks": ks,
        "questions": rows.len(),
        "confidence_counts": confidence_counts,
        "negatives_with_hits": negatives_with_hits,
        "buckets": bucket_stats,
        "per_question": rows,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source_row(sha: &str, title: &str) -> ovp_index::SourceRow {
        ovp_index::SourceRow {
            sha256: sha.into(),
            status: ovp_index::SourceStatus::Processed,
            title: Some(title.into()),
            author: None,
            url: None,
            origin: None,
            rel_path: None,
            date: None,
            content_date: None,
            captured_on: None,
            processed_on: None,
            last_run_id: None,
            pack_dir: None,
            fail_count: 0,
            last_reason: None,
            tags: vec![],
            tags_inferred: vec![],
            tags_implied: vec![],
            entities: vec![],
        }
    }

    fn write_vault(dir: &std::path::Path, sources: Vec<ovp_index::SourceRow>) {
        let model = ovp_index::IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-08-13".into(),
            built_at: Some("2026-08-13T00:00:00Z".into()),
            run_id: Some("eval-test".into()),
            totals: ovp_index::Totals::default(),
            sources,
            packs: vec![],
            claims: vec![],
            runs: vec![],
            ops: ovp_index::OpsState::default(),
        };
        ovp_index::write_index(dir, &model).expect("index");
        // search_claims folds the crystal ledger and treats a MISSING file
        // as a reportable error (honest coverage); the fixture wants the
        // empty-but-present state instead.
        let store = dir.join(".ovp/crystal");
        std::fs::create_dir_all(&store).expect("crystal dir");
        std::fs::write(store.join("ledger.jsonl"), "").expect("ledger");
    }

    fn qrel_json(dir: &std::path::Path, name: &str, body: Value) -> PathBuf {
        let p = dir.join(name);
        std::fs::write(&p, serde_json::to_string(&body).unwrap()).unwrap();
        p
    }

    #[test]
    fn recall_at_scores_prefix_membership() {
        let golds = vec!["a".to_string(), "b".to_string()];
        let ranked = vec!["x".to_string(), "a".to_string(), "b".to_string()];
        assert_eq!(recall_at(&golds, &ranked, 1), 0.0);
        assert_eq!(recall_at(&golds, &ranked, 2), 0.5);
        assert_eq!(recall_at(&golds, &ranked, 3), 1.0);
    }

    /// Request scaffolding strips away; content terms survive with their
    /// scripts separated; an all-scaffolding question degrades to verbatim.
    #[test]
    fn extract_terms_strips_request_noise() {
        assert_eq!(
            extract_terms(
                "帮我找一篇文章:一个女生毕业求职,诀窍是手写 Transformer,还分享了她的笔记"
            ),
            "女生毕业求职 诀窍是手写 transformer"
        );
        assert_eq!(
            extract_terms("vault 里关于 context engineering 的 durable 结论,给两条带引用"),
            "context engineering durable"
        );
        // All scaffolding → verbatim fallback, never an empty query.
        assert_eq!(extract_terms("帮我找一篇文章"), "帮我找一篇文章");
    }

    /// A noisy first tool must not bury a later tool's rank-0 hit: the
    /// union interleaves by rank, so the union@k can never read below a
    /// member tool's @1 hit at k >= member count.
    #[test]
    fn union_interleaves_round_robin_with_dedup() {
        let noisy: Vec<String> = (0..5).map(|i| format!("noise{i}")).collect();
        let good = vec!["gold".to_string(), "noise0".to_string()];
        let union = round_robin_union(&[noisy, good]);
        assert_eq!(union[0], "noise0");
        assert_eq!(union[1], "gold", "rank-0 of the second tool comes second");
        assert_eq!(union.iter().filter(|x| *x == "noise0").count(), 1);
    }

    #[test]
    fn baseline_scores_a_title_hit_and_reports_buckets() {
        let temp = tempfile::tempdir().expect("tempdir");
        write_vault(
            temp.path(),
            vec![
                source_row("aaaa1111", "Retrieval evaluation notes"),
                source_row("bbbb2222", "Unrelated cooking article"),
            ],
        );
        let qdir = temp.path().join("qrels");
        std::fs::create_dir_all(&qdir).unwrap();
        qrel_json(
            &qdir,
            "q-001-hit.json",
            json!({
                "schema": "ovp.retrieval_eval.qrel_draft/v1",
                "id": "q-001",
                "question": "retrieval",
                "language": "en",
                "class": "exact",
                "relevant": [{"surface": "source", "id": "aaaa1111", "grade": 3}],
                "no_answer": false,
                "confidence": "needs_review"
            }),
        );
        qrel_json(
            &qdir,
            "q-002-negative.json",
            json!({
                "schema": "ovp.retrieval_eval.qrel_draft/v1",
                "id": "q-002",
                "question": "quantum basket weaving",
                "language": "en",
                "class": "negative",
                "relevant": [],
                "no_answer": true,
                "confidence": "needs_review"
            }),
        );

        let qrels = load_qrels(&qdir).expect("qrels");
        assert_eq!(qrels.len(), 2);
        let mut tools = VaultTools::new(temp.path());
        let ks = vec![10];
        let rows: Vec<QuestionReport> = qrels
            .iter()
            .map(|q| score_question(&mut tools, q, &ks, 10, QueryMode::Verbatim))
            .collect();

        let hit = &rows[0];
        assert_eq!(hit.gold_sources, 1);
        assert_eq!(hit.source_recall["union"]["@10"], 1.0);
        assert_eq!(hit.source_recall["search_sources"]["@10"], 1.0);
        assert!(hit.tool_errors.is_empty(), "errors: {:?}", hit.tool_errors);

        let neg = &rows[1];
        assert!(neg.no_answer);
        assert_eq!(neg.hits_returned, 0);

        let report = assemble_report(&qrels, rows, &ks, QueryMode::Verbatim);
        assert_eq!(report["query_mode"], "verbatim");
        assert_eq!(report["questions"], 2);
        assert_eq!(report["negatives_with_hits"], 0);
        assert_eq!(
            report["buckets"]["class:exact"]["means"]["union_source_recall@10"],
            1.0
        );
        // The negative bucket carries n but no fabricated average.
        assert_eq!(report["buckets"]["class:negative"]["n_scored"], 0);
        assert_eq!(
            report["buckets"]["class:negative"]["means"]["union_source_recall@10"],
            Value::Null
        );
    }
}
