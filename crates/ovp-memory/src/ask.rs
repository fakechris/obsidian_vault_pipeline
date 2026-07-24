//! `ask` — vault assistant over OVP product state.
//!
//! Pipeline: **intent route** (find source / grounded Q&A / explore / meta) →
//! retrieval surface matched to the job → LLM (or a fixed meta reply) →
//! optional citation verify → chat transcript.
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

use crate::intent::{
    classify_intent, content_query_for_find, meta_capability_answer, AskIntent,
};
use crate::verify::{VerificationReport, verify_answer};

/// One completed Q/A turn from the same conversation (not including the
/// question currently being asked). Used for multi-turn continuity so
/// follow-ups like "what about that claim?" resolve against prior dialogue.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AskHistoryTurn {
    pub question: String,
    pub answer: String,
}

pub struct AskArgs {
    pub question: String,
    pub max_context_hits: usize,
    pub evidence_quotas: EvidenceQuotas,
    pub max_tokens: u32,
    pub model_name: String,
    pub save_chat: bool,
    pub verify_citations: bool,
    /// Prior turns in this conversation (oldest first). Empty = new session.
    pub history: Vec<AskHistoryTurn>,
    /// Stem of an existing `.ovp/chats/<chat>.md` to append to. `None` creates
    /// a new file. Invalid / missing names fall back to creating a new chat
    /// (never path-traverse).
    pub chat: Option<String>,
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
            history: Vec::new(),
            chat: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceKind {
    Unit,
    Card,
    Claim,
    /// A library source (sha256 id) — used by find-source routing.
    Source,
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
    /// Which job path ran for this turn (surfaced to clients for transparency).
    pub intent: AskIntent,
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
    let intent = classify_intent(&args.question, &args.history);

    // Meta: answer about Ask itself — no retrieval, no LLM (deterministic).
    if intent == AskIntent::MetaCapability {
        let answer = meta_capability_answer(&args.question);
        let chat_file = if args.save_chat {
            Some(save_or_append_chat(
                vault_root,
                args.chat.as_deref(),
                &args.question,
                &answer,
                &[],
                None,
                0,
            )?)
        } else {
            None
        };
        return Ok(AskResult {
            answer,
            context_hits: 0,
            evidence: Vec::new(),
            verification: None,
            chat_file,
            intent,
        });
    }

    let (evidence_items, system, user_prefix, temperature, verify) = match intent {
        AskIntent::FindSource => {
            let q = content_query_for_find(&args.question);
            let search_q = if q.is_empty() {
                args.question.as_str()
            } else {
                q.as_str()
            };
            // Merge history clues into the search string so follow-ups
            // ("不记得了") still retrieve against the original hunt.
            let mut hunt = search_q.to_string();
            for turn in args.history.iter().rev().take(2) {
                if looks_like_find_question(&turn.question) {
                    let prior = content_query_for_find(&turn.question);
                    if !prior.is_empty() {
                        hunt = format!("{hunt} {prior}");
                    }
                }
            }
            let items = assemble_find_hits(model, evidence, &hunt, args.max_context_hits.max(24));
            (
                items,
                FIND_SOURCE_SYSTEM.to_string(),
                "Vault candidates (sources / packs / excerpts)",
                0.3,
                true, // verify source:/unit:/card: keys that appear
            )
        }
        AskIntent::Explore => {
            let mut quotas = explore_quotas(args.evidence_quotas);
            if args.max_context_hits == 0 {
                quotas.units = 0;
                quotas.cards = 0;
                quotas.claims = 0;
            }
            let mut items = assemble_evidence(model, evidence, &args.question, quotas);
            if args.max_context_hits > 0 && items.len() > args.max_context_hits {
                items.truncate(args.max_context_hits);
            }
            (
                items,
                EXPLORE_SYSTEM.to_string(),
                "Context from the vault (broad recall)",
                0.55,
                false,
            )
        }
        AskIntent::GroundedQa | AskIntent::MetaCapability => {
            let mut quotas = args.evidence_quotas;
            if args.max_context_hits == 0 {
                quotas.units = 0;
                quotas.cards = 0;
                quotas.claims = 0;
            }
            let mut items = assemble_evidence(model, evidence, &args.question, quotas);
            if args.max_context_hits > 0 && items.len() > args.max_context_hits {
                items.truncate(args.max_context_hits);
            }
            (
                items,
                GROUNDED_QA_SYSTEM.to_string(),
                "Context from OVP evidence index",
                0.4,
                args.verify_citations,
            )
        }
    };

    let context_hits = evidence_items.len();
    let context = render_evidence_context(&evidence_items);

    let mut messages: Vec<ModelMessage> = Vec::with_capacity(args.history.len() * 2 + 1);
    for turn in &args.history {
        messages.push(ModelMessage::User {
            content: turn.question.clone(),
        });
        messages.push(ModelMessage::Assistant {
            content: turn.answer.clone(),
        });
    }
    messages.push(ModelMessage::User {
        content: format!(
            "{user_prefix} ({context_hits} hits):\n\n{context}\n\n---\n\nUser request: {}",
            args.question
        ),
    });

    let request = ModelRequest {
        model: args.model_name.clone(),
        system: Some(system),
        messages,
        max_tokens: args.max_tokens,
        temperature: Some(temperature),
        // v4: intent-routed assistant (find / qa / explore / meta).
        cache_namespace: Some("ask/v4".into()),
    };

    let reply = client.call(&request).map_err(|e| format!("ask LLM: {e}"))?;
    let verification = if verify {
        Some(verify_answer(&reply.text, &evidence_items))
    } else {
        None
    };

    let chat_file = if args.save_chat {
        Some(save_or_append_chat(
            vault_root,
            args.chat.as_deref(),
            &args.question,
            &reply.text,
            &evidence_items,
            verification.as_ref(),
            context_hits,
        )?)
    } else {
        None
    };

    Ok(AskResult {
        answer: reply.text,
        context_hits,
        evidence: evidence_items,
        verification,
        chat_file,
        intent,
    })
}

const GROUNDED_QA_SYSTEM: &str = "You are a knowledge assistant for the user's OVP vault. \
        Answer using ONLY the provided claim/card/unit evidence context. \
        Cite evidence with the FULL bracketed key exactly as shown \
        (e.g. [claim:ck-1a2b3c4d], [card:…], [unit:…]) — never shorten or drop the kind prefix. \
        Follow-up questions may refer to earlier turns — use that dialogue for reference, \
        but still ground factual claims in the CURRENT evidence context. \
        If evidence is insufficient, say what is missing. Do not invent citations.";

const FIND_SOURCE_SYSTEM: &str = "You help the user LOCATE material in their vault \
        (articles, notes, web clippings, reader packs). \
        You are NOT writing an academic evidence report. \
        Given candidate sources/packs/excerpts: \
        (1) pick the best matches and explain briefly why they fit the request; \
        (2) cite matches with FULL keys as shown — [source:SHA256] for library sources, \
        and [unit:…]/[card:…] when an excerpt supports the match; \
        (3) if nothing fits, say so clearly and ask for better clues (title words, person name, \
        date, URL fragment) — do NOT invent articles or titles not present in the candidates; \
        (4) prefer concrete titles, paths, and openable sources over abstract claims. \
        Use prior conversation turns as the hunt target when the latest message is a short follow-up.";

const EXPLORE_SYSTEM: &str = "You are a conversational guide for the user's OVP vault. \
        Use the provided context when helpful, but a warm, exploratory tone is OK. \
        When you lean on a specific claim/card/unit, cite the FULL bracketed key. \
        Uncertainty is fine — say what you're unsure about. Do not invent citations. \
        Do not force an academic 'evidence insufficient' template when the user is just chatting.";

fn looks_like_find_question(q: &str) -> bool {
    crate::intent::classify_intent(q, &[]) == AskIntent::FindSource
}

fn explore_quotas(base: EvidenceQuotas) -> EvidenceQuotas {
    EvidenceQuotas {
        units: base.units.max(12),
        cards: base.cards.max(6),
        claims: base.claims.max(6),
        max_chars: base.max_chars.max(32_000),
    }
}

/// Source/pack-first retrieval for find-source jobs, with unit/card excerpts
/// as supporting content (not claim-first academic ranking).
pub fn assemble_find_hits(
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
    question: &str,
    max_hits: usize,
) -> Vec<EvidenceItem> {
    let mut rows: Vec<ScoredEvidence> = Vec::new();

    for s in &model.sources {
        let title = s.title.as_deref().unwrap_or("(untitled)");
        let url = s.url.as_deref().unwrap_or("");
        let path = s.rel_path.as_deref().unwrap_or("");
        let tags = s.tags.join(" ");
        let score = lexical_score(question, &[title, url, path, &tags, &s.sha256]);
        if score <= 0.0 {
            continue;
        }
        // Prefer title/path hits over pure token noise.
        let boost = if !title.is_empty() && score >= 10.0 {
            5.0
        } else {
            0.0
        };
        rows.push(ScoredEvidence {
            score: score + boost,
            tier: 0,
            item: EvidenceItem {
                id: s.sha256.clone(),
                kind: EvidenceKind::Source,
                title: title.to_string(),
                body: format!(
                    "URL: {}\nPath: {}\nStatus: {:?}\nDate: {}",
                    url,
                    path,
                    s.status,
                    s.date.as_deref().unwrap_or("-")
                ),
                quote: None,
                path: s.rel_path.clone(),
            },
        });
    }

    for p in &model.packs {
        let cards_joined = p.card_titles.join(" | ");
        let score = lexical_score(question, &[&p.title, &p.pack_dir, &cards_joined]);
        if score <= 0.0 {
            continue;
        }
        let sha = p.source_sha256.clone().unwrap_or_default();
        // Surface pack as a source-shaped hit when we have a sha, else card path.
        if !sha.is_empty() {
            rows.push(ScoredEvidence {
                score: score + 2.0,
                tier: 1,
                item: EvidenceItem {
                    id: sha,
                    kind: EvidenceKind::Source,
                    title: p.title.clone(),
                    body: format!(
                        "Reader pack: {}\nCards: {}\nCard titles: {}",
                        p.pack_dir, p.cards, cards_joined
                    ),
                    quote: None,
                    path: Some(format!("{}/reader.md", p.pack_dir)),
                },
            });
        } else {
            rows.push(ScoredEvidence {
                score,
                tier: 1,
                item: EvidenceItem {
                    id: p.pack_dir.clone(),
                    kind: EvidenceKind::Card,
                    title: p.title.clone(),
                    body: format!("Pack (no source sha): {cards_joined}"),
                    quote: None,
                    path: Some(format!("{}/reader.md", p.pack_dir)),
                },
            });
        }
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
            rows.push(ScoredEvidence {
                score,
                tier: 2,
                item: EvidenceItem {
                    id: card.id.clone(),
                    kind: EvidenceKind::Card,
                    title: format!("{} — {}", card.source_title, card.title),
                    body: format!("Content: {}", clip_chars(&card.content, 1_200)),
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
            rows.push(ScoredEvidence {
                score,
                tier: 3,
                item: EvidenceItem {
                    id: unit.id.clone(),
                    kind: EvidenceKind::Unit,
                    title: unit.source_title.clone(),
                    body: format!("Text: {}", clip_chars(&unit.text, 800)),
                    quote: if unit.quote.is_empty() {
                        None
                    } else {
                        Some(unit.quote.clone())
                    },
                    path: Some(format!("{}/reader.md", unit.pack_dir)),
                },
            });
        }
    }

    rows.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.tier.cmp(&b.tier))
            .then_with(|| a.item.id.cmp(&b.item.id))
    });

    // Dedup sources by id (pack + source row may both contribute).
    let mut seen = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for row in rows {
        let key = format!("{:?}:{}", row.item.kind, row.item.id);
        if !seen.insert(key) {
            continue;
        }
        out.push(row.item);
        if out.len() >= max_hits {
            break;
        }
    }
    out
}

/// Safe chat stem: single path component, same rules as GET /api/chats/:name.
pub fn valid_chat_stem(name: &str) -> bool {
    let name = name.strip_suffix(".md").unwrap_or(name);
    !name.is_empty()
        && !name.contains("..")
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
}

/// Create a new chat file, or append a turn to an existing one when `chat`
/// names a valid existing stem. Always returns the path that was written.
fn save_or_append_chat(
    vault_root: &Path,
    chat: Option<&str>,
    question: &str,
    answer: &str,
    evidence: &[EvidenceItem],
    verification: Option<&VerificationReport>,
    context_hits: usize,
) -> Result<PathBuf, String> {
    let chats_dir = vault_root.join(".ovp").join("chats");
    std::fs::create_dir_all(&chats_dir).map_err(|e| format!("create chats dir: {e}"))?;

    let turn_block = format_chat_turn(question, answer, evidence, verification, context_hits);

    if let Some(stem) = chat.map(str::trim).filter(|s| !s.is_empty()) {
        if valid_chat_stem(stem) {
            let path = chats_dir.join(format!("{stem}.md"));
            if path.is_file() {
                append_chat_turn(&path, &turn_block)?;
                return Ok(path);
            }
        }
        // Invalid name or missing file → fall through to a new session file.
    }

    let ts = chrono_like_timestamp();
    let chat_content = format!("# Ask — {ts}\n\n{turn_block}");
    write_unique_chat(&chats_dir, &ts, &chat_content)
}

fn format_chat_turn(
    question: &str,
    answer: &str,
    evidence: &[EvidenceItem],
    verification: Option<&VerificationReport>,
    context_hits: usize,
) -> String {
    format!(
        "**Q:** {question}\n\n**A:** {answer}\n\n---\n\n## Evidence\n\n{}\n\n## Verification\n\n{}\n\nContext hits: {context_hits}\n",
        render_evidence_markdown(evidence),
        render_verification_markdown(verification)
    )
}

fn append_chat_turn(path: &Path, turn_block: &str) -> Result<(), String> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .append(true)
        .open(path)
        .map_err(|e| format!("open chat {}: {e}", path.display()))?;
    // Separate turns with a blank line so the transcript stays readable.
    write!(file, "\n---\n\n{turn_block}")
        .map_err(|e| format!("append chat {}: {e}", path.display()))
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
        EvidenceKind::Source => "source",
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
        assert_eq!(request.cache_namespace.as_deref(), Some("ask/v4"));
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
    fn continuing_a_chat_appends_to_the_same_file_and_passes_history() {
        let vault = std::env::temp_dir().join(format!(
            "ovp-memory-chat-continue-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&vault);
        std::fs::create_dir_all(&vault).unwrap();

        let captured = Arc::new(Mutex::new(None));
        let mut client = CapturingClient {
            request: captured.clone(),
            reply_text: "first answer".into(),
        };
        let first = ask_with_evidence(
            &model(),
            &evidence(),
            &mut client,
            &AskArgs {
                question: "What is memory?".into(),
                save_chat: true,
                ..Default::default()
            },
            &vault,
        )
        .unwrap();
        let stem = first
            .chat_file
            .as_ref()
            .and_then(|p| p.file_stem())
            .and_then(|s| s.to_str())
            .unwrap()
            .to_string();

        client.reply_text = "follow-up answer".into();
        let second = ask_with_evidence(
            &model(),
            &evidence(),
            &mut client,
            &AskArgs {
                question: "What about that claim?".into(),
                save_chat: true,
                chat: Some(stem.clone()),
                history: vec![crate::ask::AskHistoryTurn {
                    question: "What is memory?".into(),
                    answer: "first answer".into(),
                }],
                ..Default::default()
            },
            &vault,
        )
        .unwrap();
        assert_eq!(
            second
                .chat_file
                .as_ref()
                .and_then(|p| p.file_stem())
                .and_then(|s| s.to_str()),
            Some(stem.as_str()),
            "follow-up must append to the same chat stem"
        );
        let md = std::fs::read_to_string(first.chat_file.as_ref().unwrap()).unwrap();
        assert!(md.contains("What is memory?"));
        assert!(md.contains("first answer"));
        assert!(md.contains("What about that claim?"));
        assert!(md.contains("follow-up answer"));

        // LLM request carries prior dialogue + current evidence-grounded user turn.
        let req = captured.lock().unwrap().clone().expect("request captured");
        assert!(req.messages.len() >= 3, "history user+assistant + current user");
        match &req.messages[0] {
            ovp_llm::ModelMessage::User { content } => assert_eq!(content, "What is memory?"),
            other => panic!("expected prior user turn, got {other:?}"),
        }
        match &req.messages[1] {
            ovp_llm::ModelMessage::Assistant { content } => {
                assert_eq!(content, "first answer")
            }
            other => panic!("expected prior assistant turn, got {other:?}"),
        }

        let _ = std::fs::remove_dir_all(&vault);
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
