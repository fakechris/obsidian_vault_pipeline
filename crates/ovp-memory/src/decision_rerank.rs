//! Bounded reranking shared by the tool-agent and legacy CLI Ask paths.
//! Original candidates are never dropped. Off/shadow/failure preserve ordering.
use crate::ask::{EvidenceItem, EvidenceKind};
use ovp_domain::decision_relevance::{self as domain, RelevanceCandidate};
use ovp_index::{EvidenceModel, IndexModel};
use ovp_llm::decision::{runtime::*, *};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};
use std::time::{Duration, Instant};

pub const CAPABILITY: &str = "evidence_relevance";
type Ranking = (Vec<usize>, Vec<Value>);
pub struct DecisionReranker {
    root: PathBuf,
    settings: DecisionSettings,
    factory: Arc<dyn DecisionClientFactory + Send + Sync>,
    unit_key: String,
}
impl std::fmt::Debug for DecisionReranker {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DecisionReranker")
            .field("settings", &self.settings)
            .finish_non_exhaustive()
    }
}
struct RankInput<'a> {
    model: &'a IndexModel,
    query: &'a str,
    ids: &'a [Option<String>],
    titles: &'a [String],
    route: &'a str,
    remaining: Duration,
}
impl DecisionReranker {
    pub fn new(
        root: impl Into<PathBuf>,
        settings: DecisionSettings,
        factory: Arc<dyn DecisionClientFactory + Send + Sync>,
        unit_key: String,
    ) -> Result<Self, DecisionError> {
        settings.validate(factory.as_ref())?;
        if let Some(c) = settings.capabilities.get(CAPABILITY)
            && c.mode != DecisionMode::Off
            && c.question_namespace.as_deref() != Some(domain::NAMESPACE)
        {
            return Err(DecisionError::InvalidRequest(
                "relevance question namespace mismatch",
            ));
        }
        Ok(Self {
            root: root.into(),
            settings,
            factory,
            unit_key,
        })
    }
    fn active(&self) -> bool {
        self.settings
            .capabilities
            .get(CAPABILITY)
            .is_some_and(|c| c.mode != DecisionMode::Off)
    }
    /// A complete trace must be durable before enabled results can be applied.
    fn trace(&self, record: &Value) -> Result<(), String> {
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let dir = self.root.join(".ovp/decision-traces");
        std::fs::create_dir_all(&dir).map_err(|_| "cannot create decision trace directory")?;
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| "clock before epoch")?
            .as_nanos();
        let path = dir.join(format!(
            "relevance-{stamp}-{}-{}.json",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(path)
            .map_err(|_| "cannot open decision trace")?;
        let bytes =
            serde_json::to_vec_pretty(record).map_err(|_| "cannot encode decision trace")?;
        file.write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| "cannot persist decision trace".into())
    }
    fn rank(
        &self,
        input: RankInput<'_>,
        accept: impl FnOnce(&[usize], &[Value]) -> bool,
    ) -> Option<Ranking> {
        let RankInput {
            model,
            query,
            ids,
            titles,
            route,
            remaining,
        } = input;
        if !self.active() || ids.is_empty() {
            return None;
        }
        let start = Instant::now();
        let config = &self.settings.capabilities[CAPABILITY];
        let mut record = json!({"schema":"ovp.decision.relevance_trace/v1","route":route,"configuration":config,"query_sha256":digest(query),"baseline":ids,"candidate_order":null,"status":"fallback","reason":null});
        let result = (|| -> Result<Option<Ranking>, String> {
            // Assignment precedes source reads; control does not assemble provider state.
            if let Some(exp) = &config.experiment {
                let (arm, bucket) = exp
                    .assign(CAPABILITY, &self.unit_key)
                    .map_err(|e| e.to_string())?;
                record["arm"] = json!(arm);
                record["bucket"] = json!(bucket);
                if arm == ExperimentArm::Control {
                    record["status"] = json!("control");
                    return Ok(None);
                }
            }
            if remaining < Duration::from_secs(1) {
                return Err("remaining turn budget below rerank budget".into());
            }
            let count = ids.len().min(domain::MAX_CANDIDATES);
            let mut candidates = Vec::new();
            for i in 0..count {
                if start.elapsed() > Duration::from_millis(450) {
                    return Err("excerpt preparation budget exceeded".into());
                }
                let source = ids[i]
                    .as_deref()
                    .ok_or("candidate has no verified source identity")?;
                let text = crate::vault_tools::read_source_text(&self.root, model, source)
                    .map_err(|_| "source excerpt unavailable")?;
                let (quote, start_line, end_line) =
                    excerpt(&text, query).ok_or("empty source excerpt")?;
                candidates.push(RelevanceCandidate {
                    id: format!("hit_{i:04}"),
                    title: titles[i].chars().take(240).collect(),
                    quote,
                    evidence: EvidenceRef {
                        source_id: source.into(),
                        revision: digest(&text),
                        start_line,
                        end_line,
                    },
                });
            }
            let request = domain::request(query, &candidates).map_err(|e| e.to_string())?;
            record["request"] = json!(request);
            let observation = evaluate(
                &self.settings,
                self.factory.as_ref(),
                CAPABILITY,
                &request,
                &self.unit_key,
                &self.root.join(".ovp/cassettes/decisions"),
            )
            .map_err(|e| e.to_string())?;
            record["observation"] = json!(observation);
            record["status"] = json!(observation.status);
            let Some(reply) = observation.candidate.as_ref() else {
                return Ok(None);
            };
            if observation.status == ObservationStatus::Abstained {
                return Ok(None);
            }
            let judgments = domain::judgments(&candidates, reply).map_err(|e| e.to_string())?;
            let mut order = domain::order(&judgments);
            order.extend(count..ids.len());
            record["candidate_order"] = json!(order);
            record["judgments"] = json!(judgments);
            if observation.applied.is_none() {
                return Ok(None);
            }
            if start.elapsed() > Duration::from_secs(1) {
                return Err("rerank exceeded one-second budget; baseline retained".into());
            }
            let annotations: Vec<Value>=candidates.iter().zip(&judgments).map(|(c,j)|json!({"relevance":j.relevance,"relation":j.relation,"source_view":"annotation-redacted/v1","evidence":c.evidence,"quote":c.quote})).collect();
            if !accept(&order, &annotations) {
                return Err("reranked result exceeds delivery cap".into());
            }
            Ok(Some((order, annotations)))
        })();
        record["elapsed_ms"] = json!(start.elapsed().as_millis());
        if let Err(e) = &result {
            record["status"] = json!("fallback");
            record["reason"] = json!(e);
        }
        if self.trace(&record).is_err() {
            eprintln!("decision relevance: trace unavailable; baseline retained");
            return None;
        }
        result.ok().flatten()
    }
    pub fn rerank_tool(
        &self,
        model: &IndexModel,
        query: &str,
        value: &mut Value,
        route: &str,
        remaining: Duration,
        result_cap: usize,
    ) {
        let Some(hits) = value.get("hits").and_then(Value::as_array) else {
            return;
        };
        let ids: Vec<_> = hits
            .iter()
            .map(|h| {
                h.get("source_id")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .collect();
        let titles: Vec<_> = hits
            .iter()
            .map(|h| {
                h.get("title")
                    .or_else(|| h.get("pack_title"))
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string()
            })
            .collect();
        let reordered = |order: &[usize], annotations: &[Value]| {
            let mut candidate = value.clone();
            candidate["hits"] = json!(
                order
                    .iter()
                    .map(|&i| {
                        let mut hit = hits[i].clone();
                        if let Some(annotation) = annotations.get(i) {
                            hit["semantic_evidence"] = annotation.clone();
                        }
                        hit
                    })
                    .collect::<Vec<_>>()
            );
            candidate
        };
        let Some((order, annotations)) = self.rank(
            RankInput {
                model,
                query,
                ids: &ids,
                titles: &titles,
                route,
                remaining,
            },
            |order, annotations| {
                serde_json::to_vec(&reordered(order, annotations))
                    .is_ok_and(|bytes| bytes.len() <= result_cap)
            },
        ) else {
            return;
        };
        *value = reordered(&order, &annotations);
    }
    pub fn rerank_ask(
        &self,
        model: &IndexModel,
        evidence: Option<&EvidenceModel>,
        query: &str,
        items: &mut Vec<EvidenceItem>,
    ) {
        let ids = items
            .iter()
            .map(|item| match item.kind {
                EvidenceKind::Source => Some(
                    item.id
                        .strip_prefix("source:")
                        .unwrap_or(&item.id)
                        .to_string(),
                ),
                EvidenceKind::Unit => evidence
                    .and_then(|e| e.units.iter().find(|u| u.id == item.id))
                    .and_then(|u| u.source_sha256.clone()),
                EvidenceKind::Card => evidence
                    .and_then(|e| e.cards.iter().find(|c| c.id == item.id))
                    .and_then(|c| c.source_sha256.clone()),
                EvidenceKind::Claim => None,
            })
            .collect::<Vec<_>>();
        let titles = items.iter().map(|i| i.title.clone()).collect::<Vec<_>>();
        if let Some((order, annotations)) = self.rank(
            RankInput {
                model,
                query,
                ids: &ids,
                titles: &titles,
                route: "cli_ask",
                remaining: Duration::from_secs(1),
            },
            |_, _| true,
        ) {
            *items = order.into_iter().map(|i| {
                let mut item = items[i].clone();
                if let Some(annotation) = annotations.get(i) {
                    item.body.push_str("\nSemantic assessment (experimental judgment, not an admission verdict): ");
                    item.body.push_str(&annotation.to_string());
                }
                item
            }).collect();
        }
    }
}
fn digest(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}
fn excerpt(text: &str, query: &str) -> Option<(String, u32, u32)> {
    let lines: Vec<_> = text.lines().collect();
    if lines.is_empty() {
        return None;
    }
    // Frontmatter is metadata, not quotable evidence. Annotation was already redacted.
    let body_start = if lines.first().is_some_and(|l| l.trim() == "---") {
        lines
            .iter()
            .skip(1)
            .position(|l| l.trim() == "---")
            .map(|i| i + 2)
            .unwrap_or(lines.len())
    } else {
        0
    };
    let terms = crate::vault_tools::tokenize_search_terms(query);
    let anchor = (body_start..lines.len()).max_by_key(|i| {
        let line = lines[*i].to_lowercase();
        (
            terms
                .iter()
                .filter(|term| line.contains(term.as_str()))
                .count(),
            std::cmp::Reverse(*i),
        )
    })?;
    let start = anchor.saturating_sub(2).max(body_start);
    let end = (anchor + 4).min(lines.len());
    let quote: String = lines[start..end].join("\n").chars().take(1200).collect();
    if quote.trim().is_empty() {
        return None;
    }
    let used = quote.lines().count();
    Some((quote, (start + 1) as u32, (start + used) as u32))
}
