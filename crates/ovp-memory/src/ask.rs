//! `ask` — retrieval-augmented Q&A over OVP product state.
//!
//! Pipeline: lexical retrieval over durable claims + reader cards + accepted
//! units → context assembly with evidence ids → LLM → cited answer.
//!
//! Ephemeral reuse surface: answers are NOT durable truth, NOT in ledger.
//! Optionally persisted to `.ovp/chats/<timestamp>.md` for session continuity.

use std::path::{Path, PathBuf};

use ovp_index::evidence::EvidenceModel;
use ovp_index::model::IndexModel;
use ovp_index::query::claim_status_str;
use ovp_index::score::lexical_score;
use ovp_llm::{ModelClient, ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};

use crate::verify::{VerificationReport, verify_answer};

pub struct AskArgs {
    pub question: String,
    pub max_context_hits: usize,
    pub evidence_quotas: EvidenceQuotas,
    pub max_tokens: u32,
    pub model_name: String,
    pub save_chat: bool,
    pub verify_citations: bool,
}

impl Default for AskArgs {
    fn default() -> Self {
        Self {
            question: String::new(),
            max_context_hits: 20,
            evidence_quotas: EvidenceQuotas::default(),
            max_tokens: 2048,
            model_name: "claude-sonnet-4-20250514".into(),
            save_chat: false,
            verify_citations: true,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceKind {
    Unit,
    Card,
    Claim,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidenceItem {
    pub id: String,
    pub kind: EvidenceKind,
    pub title: String,
    pub body: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quote: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EvidenceQuotas {
    pub units: usize,
    pub cards: usize,
    pub claims: usize,
    pub max_chars: usize,
}

impl Default for EvidenceQuotas {
    fn default() -> Self {
        Self {
            units: 8,
            cards: 4,
            claims: 4,
            max_chars: 24_000,
        }
    }
}

pub struct AskResult {
    pub answer: String,
    pub context_hits: usize,
    pub evidence: Vec<EvidenceItem>,
    pub verification: Option<VerificationReport>,
    pub chat_file: Option<PathBuf>,
}

pub fn ask(
    model: &IndexModel,
    client: &mut dyn ModelClient,
    args: &AskArgs,
    vault_root: &Path,
) -> Result<AskResult, String> {
    ask_with_optional_evidence(model, None, client, args, vault_root)
}

pub fn ask_with_evidence(
    model: &IndexModel,
    evidence: &EvidenceModel,
    client: &mut dyn ModelClient,
    args: &AskArgs,
    vault_root: &Path,
) -> Result<AskResult, String> {
    ask_with_optional_evidence(model, Some(evidence), client, args, vault_root)
}

pub fn ask_with_optional_evidence(
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
    client: &mut dyn ModelClient,
    args: &AskArgs,
    vault_root: &Path,
) -> Result<AskResult, String> {
    let mut quotas = args.evidence_quotas;
    if args.max_context_hits == 0 {
        quotas.units = 0;
        quotas.cards = 0;
        quotas.claims = 0;
    }
    let mut evidence_items = assemble_evidence(model, evidence, &args.question, quotas);
    if args.max_context_hits > 0 && evidence_items.len() > args.max_context_hits {
        evidence_items.truncate(args.max_context_hits);
    }
    let context_hits = evidence_items.len();
    let context = render_evidence_context(&evidence_items);

    let system = "You are a knowledge assistant for OVP (Obsidian Vault Pipeline). \
        Answer questions using ONLY the provided claim/card/unit evidence context. \
        Cite evidence ids in square brackets. \
        If evidence is insufficient, say what is missing. Do not invent citations."
        .to_string();

    let user_msg = format!(
        "Context from OVP evidence index ({context_hits} hits):\n\n{context}\n\n---\n\nQuestion: {}",
        args.question
    );

    let request = ModelRequest {
        model: args.model_name.clone(),
        system: Some(system),
        messages: vec![ModelMessage::User { content: user_msg }],
        max_tokens: args.max_tokens,
        temperature: Some(0.4),
        cache_namespace: Some("ask/v2".into()),
    };

    let reply = client.call(&request).map_err(|e| format!("ask LLM: {e}"))?;
    let verification = if args.verify_citations {
        Some(verify_answer(&reply.text, &evidence_items))
    } else {
        None
    };

    let chat_file = if args.save_chat {
        let ts = chrono_like_timestamp();
        let chats_dir = vault_root.join(".ovp").join("chats");
        std::fs::create_dir_all(&chats_dir).map_err(|e| format!("create chats dir: {e}"))?;
        let chat_content = format!(
            "# Ask — {}\n\n**Q:** {}\n\n**A:** {}\n\n---\n\n## Evidence\n\n{}\n\n## Verification\n\n{}\n\nContext hits: {context_hits}\n",
            ts,
            args.question,
            reply.text,
            render_evidence_markdown(&evidence_items),
            render_verification_markdown(verification.as_ref())
        );
        Some(write_unique_chat(&chats_dir, &ts, &chat_content)?)
    } else {
        None
    };

    Ok(AskResult {
        answer: reply.text,
        context_hits,
        evidence: evidence_items,
        verification,
        chat_file,
    })
}

#[derive(Debug)]
struct ScoredEvidence {
    score: f64,
    tier: u8,
    item: EvidenceItem,
}

pub fn assemble_evidence(
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
    question: &str,
    quotas: EvidenceQuotas,
) -> Vec<EvidenceItem> {
    if quotas.max_chars == 0 || (quotas.units + quotas.cards + quotas.claims) == 0 {
        return Vec::new();
    }

    let mut claims = Vec::new();
    let mut cards = Vec::new();
    let mut units = Vec::new();

    for claim in &model.claims {
        let theme = claim.theme.as_deref().unwrap_or("");
        let score = lexical_score(question, &[&claim.claim_id, &claim.claim, theme]);
        if score <= 0.0 {
            continue;
        }
        let status = claim_status_str(claim.status);
        claims.push(ScoredEvidence {
            score,
            tier: 0,
            item: EvidenceItem {
                // The STABLE ledger key when the index carries it (claim_ids
                // can collide across runs; an answer's [claim:…] citation
                // must audit unambiguously), claim_id for older indexes.
                id: claim
                    .claim_key
                    .clone()
                    .unwrap_or_else(|| claim.claim_id.clone()),
                kind: EvidenceKind::Claim,
                title: format!("{status} claim{}", optional_theme_suffix(theme)),
                body: format!(
                    "Claim: {}\nSources: {}",
                    claim.claim,
                    claim.sources.join(",")
                ),
                quote: None,
                path: None,
            },
        });
    }

    if let Some(evidence) = evidence {
        for card in &evidence.cards {
            let score = lexical_score(
                question,
                &[&card.id, &card.source_title, &card.title, &card.content],
            );
            if score <= 0.0 {
                continue;
            }
            cards.push(ScoredEvidence {
                score,
                tier: 1,
                item: EvidenceItem {
                    id: card.id.clone(),
                    kind: EvidenceKind::Card,
                    title: format!("{} — {}", card.source_title, card.title),
                    body: format!(
                        "Content: {}\nCites: {}",
                        clip_chars(&card.content, 1_200),
                        card.cited_unit_ids.join(",")
                    ),
                    quote: None,
                    path: Some(format!("{}/reader.md", card.pack_dir)),
                },
            });
        }

        for unit in &evidence.units {
            let score = lexical_score(
                question,
                &[&unit.id, &unit.source_title, &unit.text, &unit.quote],
            );
            if score <= 0.0 {
                continue;
            }
            let line = unit.line.map(|n| format!(" line {n}")).unwrap_or_default();
            units.push(ScoredEvidence {
                score,
                tier: 2,
                item: EvidenceItem {
                    id: unit.id.clone(),
                    kind: EvidenceKind::Unit,
                    title: format!("{}{}", unit.source_title, line),
                    body: format!(
                        "Text: {}\nAttribution: {}\nModality: {}",
                        unit.text, unit.attribution, unit.modality
                    ),
                    quote: Some(unit.quote.clone()),
                    path: Some(format!("{}/reader.md", unit.pack_dir)),
                },
            });
        }
    }

    sort_scored(&mut claims);
    sort_scored(&mut cards);
    sort_scored(&mut units);

    claims.truncate(quotas.claims);
    cards.truncate(quotas.cards);
    units.truncate(quotas.units);

    let mut rows = Vec::new();
    rows.extend(claims);
    rows.extend(cards);
    rows.extend(units);
    rows.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.tier.cmp(&b.tier))
            .then_with(|| a.item.id.cmp(&b.item.id))
    });

    let mut out = Vec::new();
    let mut used_chars = 0usize;
    for row in rows {
        let rendered_len = render_evidence_block(&row.item).len();
        if used_chars > 0 && used_chars + rendered_len > quotas.max_chars {
            continue;
        }
        used_chars += rendered_len;
        out.push(row.item);
    }
    out
}

fn sort_scored(rows: &mut [ScoredEvidence]) {
    rows.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.tier.cmp(&b.tier))
            .then_with(|| a.item.id.cmp(&b.item.id))
    });
}

pub fn render_evidence_context(items: &[EvidenceItem]) -> String {
    let mut context = String::new();
    for item in items {
        context.push_str(&render_evidence_block(item));
        context.push('\n');
    }
    context
}

fn render_evidence_block(item: &EvidenceItem) -> String {
    let kind = evidence_kind_label(item.kind);
    let mut out = format!("[{kind}:{}] {}\n{}\n", item.id, item.title, item.body);
    if let Some(quote) = &item.quote {
        out.push_str(&format!("Quote: {quote}\n"));
    }
    if let Some(path) = &item.path {
        out.push_str(&format!("Path: {path}\n"));
    }
    out
}

fn render_evidence_markdown(items: &[EvidenceItem]) -> String {
    if items.is_empty() {
        return "(none)".into();
    }
    let mut out = String::new();
    for item in items {
        let kind = evidence_kind_label(item.kind);
        let path = item.path.as_deref().unwrap_or("");
        out.push_str(&format!(
            "- `[{kind}:{}]` {} {}\n",
            item.id, item.title, path
        ));
    }
    out
}

fn render_verification_markdown(report: Option<&VerificationReport>) -> String {
    match report {
        None => "not run".into(),
        Some(report) => format!(
            "verified citations: {}/{}\nmissing: {}\nwarnings: {}",
            report.verified,
            report.cited,
            empty_dash(&report.missing),
            empty_dash(&report.warnings)
        ),
    }
}

fn empty_dash(items: &[String]) -> String {
    if items.is_empty() {
        "-".into()
    } else {
        items.join(", ")
    }
}

fn evidence_kind_label(kind: EvidenceKind) -> &'static str {
    match kind {
        EvidenceKind::Unit => "unit",
        EvidenceKind::Card => "card",
        EvidenceKind::Claim => "claim",
    }
}

fn optional_theme_suffix(theme: &str) -> String {
    if theme.is_empty() {
        String::new()
    } else {
        format!(" theme={theme}")
    }
}

fn clip_chars(input: &str, max_chars: usize) -> String {
    if input.chars().count() <= max_chars {
        return input.to_string();
    }
    let mut clipped: String = input.chars().take(max_chars.saturating_sub(1)).collect();
    clipped.push_str("...");
    clipped
}

fn chrono_like_timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    format!("{}", now.as_secs())
}

/// Create `<ts>.md` in `dir`, falling back to `<ts>-2.md`, `<ts>-3.md`, …
/// when the second-resolution timestamp collides — two answers in the same
/// second must both survive, never silently overwrite. `create_new` makes
/// the existence check atomic (no check-then-write race).
fn write_unique_chat(dir: &Path, ts: &str, content: &str) -> Result<PathBuf, String> {
    use std::io::Write;
    for attempt in 1..=100u32 {
        let name = if attempt == 1 {
            format!("{ts}.md")
        } else {
            format!("{ts}-{attempt}.md")
        };
        let path = dir.join(name);
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(mut file) => {
                file.write_all(content.as_bytes())
                    .map_err(|e| format!("write chat: {e}"))?;
                return Ok(path);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(format!("write chat {}: {e}", path.display())),
        }
    }
    Err(format!(
        "write chat: no free filename for {ts} after 100 attempts"
    ))
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use ovp_index::evidence::{CardEvidenceRow, EVIDENCE_SCHEMA, EvidenceModel, UnitEvidenceRow};
    use ovp_index::model::{
        ClaimRow, ClaimStatus, INDEX_SCHEMA, IndexModel, OpsState, PackRow, Totals,
    };
    use ovp_llm::{CallError, ModelClient, ModelReply, ModelRequest, StopReason, Usage};

    use crate::ask::{AskArgs, EvidenceKind, EvidenceQuotas, ask_with_evidence, assemble_evidence};

    struct CapturingClient {
        request: Arc<Mutex<Option<ModelRequest>>>,
        reply_text: String,
    }

    impl ModelClient for CapturingClient {
        fn call(&mut self, request: &ModelRequest) -> Result<ModelReply, CallError> {
            *self.request.lock().unwrap() = Some(request.clone());
            Ok(ModelReply {
                model: request.model.clone(),
                text: self.reply_text.clone(),
                stop_reason: StopReason::EndTurn,
                usage: Usage {
                    input_tokens: 1,
                    output_tokens: 1,
                },
            })
        }
    }

    fn model() -> IndexModel {
        IndexModel {
            schema: INDEX_SCHEMA.into(),
            date: "2026-07-06".into(),
            built_at: None,
            run_id: None,
            totals: Totals::default(),
            sources: vec![],
            packs: vec![PackRow {
                pack_dir: "40-Resources/Reader/memory".into(),
                title: "Agent Memory Systems".into(),
                date: Some("2026-07-06".into()),
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec!["Memory as state".into()],
                source_sha256: Some("sha-a".into()),
            }],
            claims: vec![ClaimRow {
                claim_id: "claim-memory-1".into(),
                claim_key: None,
                claim: "Agent memory should be treated as persistent state.".into(),
                theme: Some("memory".into()),
                status: ClaimStatus::Durable,
                sources: vec!["40-Resources/Reader/memory".into()],
                strength: Some("supported".into()),
                run_id: Some("run-1".into()),
                lane: None,
            }],
            runs: vec![],
            ops: OpsState::default(),
        }
    }

    fn evidence() -> EvidenceModel {
        EvidenceModel {
            schema: EVIDENCE_SCHEMA.into(),
            date: "2026-07-06".into(),
            cards: vec![CardEvidenceRow {
                id: "card:40-Resources/Reader/memory:0".into(),
                pack_dir: "40-Resources/Reader/memory".into(),
                source_sha256: Some("sha-a".into()),
                source_title: "Agent Memory Systems".into(),
                title: "Memory as state".into(),
                content: "Agent memory is durable state, not transient prompt text.".into(),
                unit_type: Some("claim".into()),
                cited_unit_ids: vec!["u-001".into()],
            }],
            units: vec![UnitEvidenceRow {
                id: "unit:40-Resources/Reader/memory:u-001".into(),
                pack_dir: "40-Resources/Reader/memory".into(),
                source_sha256: Some("sha-a".into()),
                source_title: "Agent Memory Systems".into(),
                unit_id: "u-001".into(),
                text: "Agent memory is durable state.".into(),
                quote: "Agent memory is durable state, not transient prompt text.".into(),
                line: Some(42),
                attribution: "author".into(),
                modality: "asserted".into(),
            }],
            warnings: vec![],
        }
    }

    #[test]
    fn ask_prompt_includes_claim_card_and_unit_evidence_context() {
        let captured = Arc::new(Mutex::new(None));
        let mut client = CapturingClient {
            request: captured.clone(),
            reply_text: "answer".into(),
        };
        let args = AskArgs {
            question: "How should agent memory be treated?".into(),
            max_context_hits: 10,
            ..Default::default()
        };

        let result = ask_with_evidence(
            &model(),
            &evidence(),
            &mut client,
            &args,
            std::path::Path::new("."),
        )
        .unwrap();

        assert_eq!(result.context_hits, 3);
        assert_eq!(result.evidence.len(), 3);
        assert!(
            result
                .evidence
                .iter()
                .any(|item| item.kind == EvidenceKind::Claim && item.id == "claim-memory-1")
        );
        assert!(
            result
                .evidence
                .iter()
                .any(|item| item.kind == EvidenceKind::Card
                    && item.id == "card:40-Resources/Reader/memory:0")
        );
        assert!(
            result
                .evidence
                .iter()
                .any(|item| item.kind == EvidenceKind::Unit
                    && item.id == "unit:40-Resources/Reader/memory:u-001")
        );
        let request = captured.lock().unwrap().clone().unwrap();
        assert_eq!(request.cache_namespace.as_deref(), Some("ask/v2"));
        let user = match &request.messages[0] {
            ovp_llm::ModelMessage::User { content } => content,
            ovp_llm::ModelMessage::Assistant { .. } => panic!("expected user message"),
        };

        assert!(user.contains("[claim:claim-memory-1]"), "{user}");
        assert!(
            user.contains("[card:card:40-Resources/Reader/memory:0]"),
            "{user}"
        );
        assert!(
            user.contains("[unit:unit:40-Resources/Reader/memory:u-001]"),
            "{user}"
        );
        assert!(
            user.contains("Agent memory is durable state, not transient prompt text."),
            "{user}"
        );
        assert!(user.contains("line 42"), "{user}");
    }

    #[test]
    fn ask_result_verifies_answer_citations_against_supplied_evidence() {
        let captured = Arc::new(Mutex::new(None));
        let mut client = CapturingClient {
            request: captured,
            reply_text: "Memory persists [unit:unit:40-Resources/Reader/memory:u-001].".into(),
        };
        let args = AskArgs {
            question: "How should agent memory be treated?".into(),
            max_context_hits: 10,
            ..Default::default()
        };

        let result = ask_with_evidence(
            &model(),
            &evidence(),
            &mut client,
            &args,
            std::path::Path::new("."),
        )
        .unwrap();

        let report = result.verification.expect("verification report");
        assert_eq!(report.cited, 1);
        assert_eq!(report.verified, 1);
        assert!(report.missing.is_empty());
    }

    #[test]
    fn saving_two_chats_in_the_same_second_creates_two_files() {
        let vault =
            std::env::temp_dir().join(format!("ovp-memory-chat-collision-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&vault);
        std::fs::create_dir_all(&vault).unwrap();

        // Deterministic collision: same timestamp twice, straight through
        // the uniquifier.
        let dir = vault.join(".ovp/chats");
        std::fs::create_dir_all(&dir).unwrap();
        let first = crate::ask::write_unique_chat(&dir, "1751812345", "one").unwrap();
        let second = crate::ask::write_unique_chat(&dir, "1751812345", "two").unwrap();
        assert_ne!(first, second);
        assert_eq!(std::fs::read_to_string(&first).unwrap(), "one");
        assert_eq!(std::fs::read_to_string(&second).unwrap(), "two");
        assert!(second.to_string_lossy().ends_with("1751812345-2.md"));

        // And the full pipeline: two immediate saves both survive.
        let args = AskArgs {
            question: "How should agent memory be treated?".into(),
            save_chat: true,
            ..Default::default()
        };
        let mut client = CapturingClient {
            request: Arc::new(Mutex::new(None)),
            reply_text: "answer".into(),
        };
        let a = ask_with_evidence(&model(), &evidence(), &mut client, &args, &vault).unwrap();
        let b = ask_with_evidence(&model(), &evidence(), &mut client, &args, &vault).unwrap();
        let (a, b) = (a.chat_file.unwrap(), b.chat_file.unwrap());
        assert_ne!(a, b);
        assert!(a.is_file() && b.is_file());

        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn evidence_assembly_retrieves_cjk_card_and_unit_content() {
        let evidence = EvidenceModel {
            schema: EVIDENCE_SCHEMA.into(),
            date: "2026-07-06".into(),
            cards: vec![CardEvidenceRow {
                id: "card:40-Resources/Reader/cjk:0".into(),
                pack_dir: "40-Resources/Reader/cjk".into(),
                source_sha256: Some("sha-cjk".into()),
                source_title: "代理记忆系统".into(),
                title: "记忆作为状态".into(),
                content: "代理记忆系统把用户信息保存为长期状态。".into(),
                unit_type: Some("claim".into()),
                cited_unit_ids: vec!["u-cn-1".into()],
            }],
            units: vec![UnitEvidenceRow {
                id: "unit:40-Resources/Reader/cjk:u-cn-1".into(),
                pack_dir: "40-Resources/Reader/cjk".into(),
                source_sha256: Some("sha-cjk".into()),
                source_title: "代理记忆系统".into(),
                unit_id: "u-cn-1".into(),
                text: "代理记忆系统保存长期状态。".into(),
                quote: "代理记忆系统把用户信息保存为长期状态。".into(),
                line: Some(7),
                attribution: "author".into(),
                modality: "asserted".into(),
            }],
            warnings: vec![],
        };

        let items = assemble_evidence(
            &model(),
            Some(&evidence),
            "记忆",
            EvidenceQuotas {
                claims: 0,
                cards: 4,
                units: 4,
                max_chars: 24_000,
            },
        );

        assert!(
            items
                .iter()
                .any(|item| item.kind == EvidenceKind::Card && item.body.contains("长期状态"))
        );
        assert!(items.iter().any(|item| {
            item.kind == EvidenceKind::Unit
                && item
                    .quote
                    .as_deref()
                    .is_some_and(|q| q.contains("用户信息"))
        }));
    }
}
