//! Rebuildable bilingual projections over English authorities.
//!
//! - Crystal ledger / claim text stays English (authority).
//! - `.ovp/crystal/claims_zh.json` — claim_key → Simplified Chinese claim.
//! - `.ovp/crystal/cards_zh.json` — card id → title_zh + content_zh.
//! - `.ovp/crystal/theme_pages_zh.json` — community_id → sections_zh.
//! - `.ovp/crystal/glossary.json` — shared EN→zh term table (session + vault).
//!
//! Translation quality: same 信达雅 bar as source_work (key terms KEEP English;
//! session glossary injected). Never free MT engines.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use ovp_llm::{ModelClient, ModelMessage, ModelRequest, StopReason};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Cassette cache root for every bilingual-tail client (daily / crystal-synth /
/// crystal-theme-pages / source-work) — one place to change the replay store.
pub const CACHE_REL: &str = ".ovp/cassettes/bilingual";

/// Shared vault glossary (merge of operator edits + LLM extractions).
pub const GLOSSARY_REL: &str = ".ovp/crystal/glossary.json";
pub const CLAIMS_ZH_REL: &str = ".ovp/crystal/claims_zh.json";
pub const CARDS_ZH_REL: &str = ".ovp/crystal/cards_zh.json";
pub const THEME_PAGES_ZH_REL: &str = ".ovp/crystal/theme_pages_zh.json";

pub const GLOSSARY_SCHEMA: &str = "ovp.glossary/v1";
pub const CLAIMS_ZH_SCHEMA: &str = "ovp.claims_zh/v1";
pub const CARDS_ZH_SCHEMA: &str = "ovp.cards_zh/v1";
pub const THEME_PAGES_ZH_SCHEMA: &str = "ovp.theme_pages_zh/v1";

const TRANSLATE_SHORT_SYSTEM: &str = r#"You are a professional EN→zh-CN translator for technical knowledge claims and memory cards.

Rewrite into natural Simplified Chinese a skilled native editor would publish.

## Rules
1. Faithful meaning — do not invent facts, numbers, or citations.
2. Idiomatic Chinese; no Chinglish; no AI filler.
3. KEEP in English: tickers, product codes, widely-used acronyms (ETF, LLM, API, ROI, HBM…), metrics (-6.07σ, 2x).
4. First use of specialized terms: 中文（English） when helpful; then consistent form.
5. If a glossary is provided, OBEY it.
6. Output ONLY the translated text — no preface, no quotes around the whole answer.
"#;

// ---- Glossary ----

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GlossaryTerm {
    pub en: String,
    pub zh: String,
    /// When true, prefer Latin form (or 中文（EN）) over pure Chinese.
    #[serde(default)]
    pub keep_en: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GlossaryFile {
    pub schema: String,
    #[serde(default)]
    pub terms: Vec<GlossaryTerm>,
}

impl Default for GlossaryFile {
    fn default() -> Self {
        Self {
            schema: GLOSSARY_SCHEMA.into(),
            terms: Vec::new(),
        }
    }
}

impl GlossaryFile {
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        let path = vault_root.join(GLOSSARY_REL);
        if !path.is_file() {
            return Ok(Self::default());
        }
        let raw = fs::read_to_string(&path).map_err(|e| format!("read glossary: {e}"))?;
        let file: Self = serde_json::from_str(&raw).map_err(|e| format!("parse glossary: {e}"))?;
        if file.schema != GLOSSARY_SCHEMA {
            return Err(format!(
                "unsupported glossary schema `{}` (expected `{GLOSSARY_SCHEMA}`)",
                file.schema
            ));
        }
        Ok(file)
    }

    pub fn save(&self, vault_root: &Path) -> Result<(), String> {
        save_json(vault_root, GLOSSARY_REL, self)
    }

    /// Render as bullet list for prompt injection.
    pub fn as_prompt_block(&self) -> String {
        if self.terms.is_empty() {
            return String::new();
        }
        let mut lines = Vec::new();
        for t in &self.terms {
            let keep = if t.keep_en { " | KEEP" } else { "" };
            let note = t
                .note
                .as_deref()
                .map(|n| format!(" — {n}"))
                .unwrap_or_default();
            lines.push(format!("- {} → {}{keep}{note}", t.en, t.zh));
        }
        lines.join("\n")
    }

    /// Merge terms by lowercase English key (incoming wins on conflict).
    pub fn merge_terms(&mut self, incoming: impl IntoIterator<Item = GlossaryTerm>) {
        let mut map: BTreeMap<String, GlossaryTerm> = self
            .terms
            .drain(..)
            .map(|t| (t.en.to_ascii_lowercase(), t))
            .collect();
        for t in incoming {
            map.insert(t.en.to_ascii_lowercase(), t);
        }
        self.terms = map.into_values().collect();
        self.terms.sort_by(|a, b| a.en.to_ascii_lowercase().cmp(&b.en.to_ascii_lowercase()));
    }
}

// ---- Claims zh ----

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimZhEntry {
    pub claim_zh: String,
    /// sha256 prefix of English claim text — staleness marker.
    pub en_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub translated_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimsZhFile {
    pub schema: String,
    #[serde(default)]
    pub entries: BTreeMap<String, ClaimZhEntry>,
}

impl Default for ClaimsZhFile {
    fn default() -> Self {
        Self {
            schema: CLAIMS_ZH_SCHEMA.into(),
            entries: BTreeMap::new(),
        }
    }
}

impl ClaimsZhFile {
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        load_map_file(vault_root, CLAIMS_ZH_REL, CLAIMS_ZH_SCHEMA)
    }

    pub fn save(&self, vault_root: &Path) -> Result<(), String> {
        save_json(vault_root, CLAIMS_ZH_REL, self)
    }

    pub fn get_fresh(&self, claim_key: &str, en_claim: &str) -> Option<&str> {
        let e = self.entries.get(claim_key)?;
        if e.en_hash == text_hash(en_claim) {
            Some(e.claim_zh.as_str())
        } else {
            None
        }
    }
}

// ---- Cards zh ----

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CardZhEntry {
    pub title_zh: String,
    pub content_zh: String,
    pub en_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub translated_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CardsZhFile {
    pub schema: String,
    #[serde(default)]
    pub entries: BTreeMap<String, CardZhEntry>,
}

impl Default for CardsZhFile {
    fn default() -> Self {
        Self {
            schema: CARDS_ZH_SCHEMA.into(),
            entries: BTreeMap::new(),
        }
    }
}

impl CardsZhFile {
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        load_map_file(vault_root, CARDS_ZH_REL, CARDS_ZH_SCHEMA)
    }

    pub fn save(&self, vault_root: &Path) -> Result<(), String> {
        save_json(vault_root, CARDS_ZH_REL, self)
    }

    pub fn get_fresh(&self, card_id: &str, title: &str, content: &str) -> Option<&CardZhEntry> {
        let e = self.entries.get(card_id)?;
        if e.en_hash == text_hash(&format!("{title}\n{content}")) {
            Some(e)
        } else {
            None
        }
    }
}

// ---- Theme pages zh ----

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemePageSectionZh {
    pub heading: String,
    pub body: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemePageZhEntry {
    pub community_id: i64,
    pub sections: Vec<ThemePageSectionZh>,
    /// Staleness: hash of English section bodies joined.
    pub en_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub translated_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThemePagesZhFile {
    pub schema: String,
    #[serde(default)]
    pub pages: BTreeMap<String, ThemePageZhEntry>,
}

impl Default for ThemePagesZhFile {
    fn default() -> Self {
        Self {
            schema: THEME_PAGES_ZH_SCHEMA.into(),
            pages: BTreeMap::new(),
        }
    }
}

impl ThemePagesZhFile {
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        let path = vault_root.join(THEME_PAGES_ZH_REL);
        if !path.is_file() {
            return Ok(Self::default());
        }
        let raw = fs::read_to_string(&path).map_err(|e| format!("read: {e}"))?;
        let file: Self = serde_json::from_str(&raw).map_err(|e| format!("parse: {e}"))?;
        if file.schema != THEME_PAGES_ZH_SCHEMA {
            return Err(format!("unsupported schema `{}`", file.schema));
        }
        Ok(file)
    }

    pub fn save(&self, vault_root: &Path) -> Result<(), String> {
        save_json(vault_root, THEME_PAGES_ZH_REL, self)
    }

    pub fn get_fresh(&self, community_id: i64, en_hash: &str) -> Option<&ThemePageZhEntry> {
        let key = community_id.to_string();
        let e = self.pages.get(&key)?;
        if e.en_hash == en_hash {
            Some(e)
        } else {
            None
        }
    }
}

// ---- Translate primitives ----

fn text_hash(s: &str) -> String {
    let mut h = Sha256::new();
    h.update(s.trim().as_bytes());
    format!("{:x}", h.finalize())[..16].to_string()
}

fn now_stamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs().to_string())
        .unwrap_or_else(|_| "0".into())
}

fn llm_text(
    client: &mut dyn ModelClient,
    model: &str,
    system: &str,
    user: &str,
    max_tokens: u32,
) -> Result<String, String> {
    let req = ModelRequest {
        model: model.to_string(),
        system: Some(system.to_string()),
        messages: vec![ModelMessage::User {
            content: user.to_string(),
        }],
        max_tokens,
        temperature: Some(0.2),
        tools: None,
        cache_namespace: Some("bilingual/v1".into()),
    };
    let reply = client.call(&req).map_err(|e| e.to_string())?;
    // A truncated/refused reply must never be persisted: stored with a
    // matching en_hash it would be treated as fresh forever. Error instead so
    // the item is collected and retried next run.
    if !reply.is_final_success() {
        return Err(format!(
            "model stopped early (stop_reason={}) — not storing truncated translation",
            stop_reason_label(&reply.stop_reason, reply.raw_stop_reason.as_deref())
        ));
    }
    let text = reply.text.trim().to_string();
    if text.is_empty() {
        return Err("model returned empty text".into());
    }
    Ok(text)
}

/// snake_case stop reason for error messages, with the verbatim provider
/// string appended when the reason was not recognized.
fn stop_reason_label(reason: &StopReason, raw: Option<&str>) -> String {
    let name = match reason {
        StopReason::EndTurn => "end_turn",
        StopReason::MaxTokens => "max_tokens",
        StopReason::StopSequence => "stop_sequence",
        StopReason::ToolUse => "tool_use",
        StopReason::Refusal => "refusal",
        StopReason::Unknown => "unknown",
    };
    match (reason, raw) {
        (StopReason::Unknown, Some(raw)) => format!("{name} (raw: {raw})"),
        _ => name.to_string(),
    }
}

fn user_with_glossary(glossary: &GlossaryFile, kind: &str, body: &str) -> String {
    let mut out = String::new();
    let g = glossary.as_prompt_block();
    if !g.is_empty() {
        out.push_str("## Session glossary (OBEY)\n");
        out.push_str(&g);
        out.push_str("\n\n");
    }
    out.push_str(&format!("Translate this {kind} to Simplified Chinese:\n\n{body}"));
    out
}

/// Translate one claim into the claims_zh projection (skip if fresh).
pub fn translate_claim(
    vault_root: &Path,
    claim_key: &str,
    claim_en: &str,
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
) -> Result<String, String> {
    let mut file = ClaimsZhFile::load(vault_root)?;
    if !force
        && let Some(zh) = file.get_fresh(claim_key, claim_en)
    {
        return Ok(zh.to_string());
    }
    let glossary = GlossaryFile::load(vault_root).unwrap_or_default();
    let user = user_with_glossary(&glossary, "knowledge claim", claim_en);
    let zh = llm_text(client, model, TRANSLATE_SHORT_SYSTEM, &user, 1024)?;
    file.entries.insert(
        claim_key.to_string(),
        ClaimZhEntry {
            claim_zh: zh.clone(),
            en_hash: text_hash(claim_en),
            model: Some(model.to_string()),
            translated_at: Some(now_stamp()),
        },
    );
    file.save(vault_root)?;
    Ok(zh)
}

/// Consecutive per-item failures tolerated before a batch helper aborts.
/// During a provider outage every stale item would otherwise go through the
/// retrying client's backoff — an unattended daily tail hammering the API.
const MAX_CONSECUTIVE_ERRORS: usize = 3;

/// Substring marking the single corrupt-projection error produced by
/// `load_snapshot!` — the manual CLIs match on it to fail loud (nonzero)
/// where the automatic tails only warn. (codex P2)
pub const CORRUPT_PROJECTION_MARKER: &str = "projection corrupt";

/// Load a batch snapshot, distinguishing a MISSING projection (default, the
/// steady state before the first run) from a CORRUPT one (exists but won't
/// parse / schema-mismatched). Corrupt must fail once, loud and clear —
/// defaulting to empty would turn every per-item `load()?` into N permanent
/// errors that never heal.
macro_rules! load_snapshot {
    ($ty:ty, $vault_root:expr, $rel:expr) => {
        match <$ty>::load($vault_root) {
            Ok(f) => f,
            Err(e) if $vault_root.join($rel).is_file() => {
                return (
                    0,
                    0,
                    vec![format!(
                        "{}: {} ({e}) — delete the file to rebuild",
                        $rel, CORRUPT_PROJECTION_MARKER
                    )],
                );
            }
            // Vanished between the existence check and the read — treat as missing.
            Err(_) => <$ty>::default(),
        }
    };
}

/// Batch-translate missing claims. Returns (done, skipped, errors).
pub fn translate_claims_batch(
    vault_root: &Path,
    claims: &[(String, String)], // (claim_key, claim_en)
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
    max: usize,
) -> (usize, usize, Vec<String>) {
    let mut done = 0usize;
    let mut skipped = 0usize;
    let mut errors = Vec::new();
    let mut consecutive_errors = 0usize;
    let existing = load_snapshot!(ClaimsZhFile, vault_root, CLAIMS_ZH_REL);
    for (key, en) in claims {
        if max > 0 && done >= max {
            break;
        }
        if !force && existing.get_fresh(key, en).is_some() {
            // A fresh skip makes no provider call, so it must NOT reset the
            // outage breaker — interleaved cache hits would otherwise let an
            // outage retry every stale item (codex P2).
            skipped += 1;
            continue;
        }
        match translate_claim(vault_root, key, en, client, model, force) {
            Ok(_) => {
                done += 1;
                consecutive_errors = 0;
            }
            Err(e) => {
                errors.push(format!("{key}: {e}"));
                consecutive_errors += 1;
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS {
                    errors.push(format!(
                        "aborting batch after {MAX_CONSECUTIVE_ERRORS} consecutive failures (provider outage?)"
                    ));
                    break;
                }
            }
        }
    }
    (done, skipped, errors)
}

/// Translate one memory card.
pub fn translate_card(
    vault_root: &Path,
    card_id: &str,
    title: &str,
    content: &str,
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
) -> Result<CardZhEntry, String> {
    let mut file = CardsZhFile::load(vault_root)?;
    if !force
        && let Some(e) = file.get_fresh(card_id, title, content)
    {
        return Ok(e.clone());
    }
    let glossary = GlossaryFile::load(vault_root).unwrap_or_default();
    let blob = format!("# {title}\n\n{content}");
    let user = user_with_glossary(&glossary, "memory card (keep markdown)", &blob);
    let zh = llm_text(client, model, TRANSLATE_SHORT_SYSTEM, &user, 2048)?;
    // Split first heading line as title when possible.
    let (title_zh, content_zh) = split_card_zh(&zh, title);
    let entry = CardZhEntry {
        title_zh,
        content_zh,
        en_hash: text_hash(&format!("{title}\n{content}")),
        model: Some(model.to_string()),
        translated_at: Some(now_stamp()),
    };
    file.entries.insert(card_id.to_string(), entry.clone());
    file.save(vault_root)?;
    Ok(entry)
}

/// Top up the cards_zh projection for `cards` ((card_id, title, content)),
/// translating only stale/missing entries through `get_fresh`. Shared by the
/// manual `source-work memory-zh` CLI and the daily bilingual tail — an
/// unchanged authority (`force = false`) costs zero LLM calls.
/// Returns (done, skipped, errors).
pub fn topup_cards_zh(
    vault_root: &Path,
    cards: &[(String, String, String)],
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
    max: usize,
) -> (usize, usize, Vec<String>) {
    let mut done = 0usize;
    let mut skipped = 0usize;
    let mut errors = Vec::new();
    let mut consecutive_errors = 0usize;
    let existing = load_snapshot!(CardsZhFile, vault_root, CARDS_ZH_REL);
    for (id, title, content) in cards {
        if max > 0 && done >= max {
            break;
        }
        if !force && existing.get_fresh(id, title, content).is_some() {
            // Fresh skip = no provider call — must not reset the outage
            // breaker (see translate_claims_batch).
            skipped += 1;
            continue;
        }
        match translate_card(vault_root, id, title, content, client, model, force) {
            Ok(_) => {
                done += 1;
                consecutive_errors = 0;
            }
            Err(e) => {
                errors.push(format!("{id}: {e}"));
                consecutive_errors += 1;
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS {
                    errors.push(format!(
                        "aborting batch after {MAX_CONSECUTIVE_ERRORS} consecutive failures (provider outage?)"
                    ));
                    break;
                }
            }
        }
    }
    (done, skipped, errors)
}

fn split_card_zh(zh: &str, fallback_title: &str) -> (String, String) {
    let t = zh.trim();
    if let Some(rest) = t.strip_prefix("# ") {
        if let Some((first, body)) = rest.split_once('\n') {
            return (first.trim().to_string(), body.trim().to_string());
        }
        return (rest.trim().to_string(), String::new());
    }
    // No heading — use first line as title if short.
    if let Some((first, body)) = t.split_once('\n')
        && first.chars().count() <= 80
    {
        return (first.trim().to_string(), body.trim().to_string());
    }
    (fallback_title.to_string(), t.to_string())
}

/// Hash English theme page sections for staleness.
pub fn theme_page_en_hash(sections: &[(String, String)]) -> String {
    let joined = sections
        .iter()
        .map(|(h, b)| format!("{h}\n{b}"))
        .collect::<Vec<_>>()
        .join("\n---\n");
    text_hash(&joined)
}

/// Translate theme page sections (preserves [claim:…] markers).
pub fn translate_theme_page(
    vault_root: &Path,
    community_id: i64,
    sections: &[(String, String)], // (heading, body) EN
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
) -> Result<ThemePageZhEntry, String> {
    let en_hash = theme_page_en_hash(sections);
    let mut file = ThemePagesZhFile::load(vault_root)?;
    let key = community_id.to_string();
    if !force
        && let Some(e) = file.get_fresh(community_id, &en_hash)
    {
        return Ok(e.clone());
    }
    let glossary = GlossaryFile::load(vault_root).unwrap_or_default();
    let mut out_sections = Vec::new();
    for (heading, body) in sections {
        let blob = format!("## {heading}\n\n{body}");
        let mut user = user_with_glossary(
            &glossary,
            "theme page section (KEEP [claim:…] citation tokens EXACTLY)",
            &blob,
        );
        user.push_str("\n\nDo not translate or alter [claim:…] tokens.");
        let zh = llm_text(client, model, TRANSLATE_SHORT_SYSTEM, &user, 3000)?;
        let (h_zh, b_zh) = split_section_zh(&zh, heading);
        out_sections.push(ThemePageSectionZh {
            heading: h_zh,
            body: b_zh,
        });
    }
    let entry = ThemePageZhEntry {
        community_id,
        sections: out_sections,
        en_hash,
        model: Some(model.to_string()),
        translated_at: Some(now_stamp()),
    };
    file.pages.insert(key, entry.clone());
    file.save(vault_root)?;
    Ok(entry)
}

/// Top up the theme_pages_zh projection for `pages`
/// ((community_id, [(heading, body)])), translating only stale/missing
/// entries through `get_fresh`. Shared by the manual `source-work memory-zh`
/// CLI and the crystal-theme-pages bilingual tail — an unchanged authority
/// (`force = false`) costs zero LLM calls. Returns (done, skipped, errors).
pub fn topup_theme_pages_zh(
    vault_root: &Path,
    pages: &[(i64, Vec<(String, String)>)],
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
    max: usize,
) -> (usize, usize, Vec<String>) {
    let mut done = 0usize;
    let mut skipped = 0usize;
    let mut errors = Vec::new();
    let mut consecutive_errors = 0usize;
    let existing = load_snapshot!(ThemePagesZhFile, vault_root, THEME_PAGES_ZH_REL);
    for (community_id, sections) in pages {
        if max > 0 && done >= max {
            break;
        }
        let en_hash = theme_page_en_hash(sections);
        if !force && existing.get_fresh(*community_id, &en_hash).is_some() {
            // Fresh skip = no provider call — must not reset the outage
            // breaker (see translate_claims_batch).
            skipped += 1;
            continue;
        }
        match translate_theme_page(vault_root, *community_id, sections, client, model, force) {
            Ok(_) => {
                done += 1;
                consecutive_errors = 0;
            }
            Err(e) => {
                errors.push(format!("community {community_id}: {e}"));
                consecutive_errors += 1;
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS {
                    errors.push(format!(
                        "aborting batch after {MAX_CONSECUTIVE_ERRORS} consecutive failures (provider outage?)"
                    ));
                    break;
                }
            }
        }
    }
    (done, skipped, errors)
}

fn split_section_zh(zh: &str, fallback_heading: &str) -> (String, String) {
    let t = zh.trim();
    for prefix in ["## ", "# "] {
        if let Some(rest) = t.strip_prefix(prefix) {
            if let Some((first, body)) = rest.split_once('\n') {
                return (first.trim().to_string(), body.trim().to_string());
            }
            return (rest.trim().to_string(), String::new());
        }
    }
    (fallback_heading.to_string(), t.to_string())
}

// ---- IO helpers ----

fn load_map_file<T: for<'de> Deserialize<'de> + Default + SchemaCheck>(
    vault_root: &Path,
    rel: &str,
    schema: &str,
) -> Result<T, String> {
    let path = vault_root.join(rel);
    if !path.is_file() {
        return Ok(T::default());
    }
    let raw = fs::read_to_string(&path).map_err(|e| format!("read {rel}: {e}"))?;
    let file: T = serde_json::from_str(&raw).map_err(|e| format!("parse {rel}: {e}"))?;
    if file.schema_str() != schema {
        return Err(format!(
            "{rel}: unsupported schema `{}` (expected `{schema}`)",
            file.schema_str()
        ));
    }
    Ok(file)
}

trait SchemaCheck {
    fn schema_str(&self) -> &str;
}

impl SchemaCheck for ClaimsZhFile {
    fn schema_str(&self) -> &str {
        &self.schema
    }
}
impl SchemaCheck for CardsZhFile {
    fn schema_str(&self) -> &str {
        &self.schema
    }
}

fn save_json<T: Serialize>(vault_root: &Path, rel: &str, value: &T) -> Result<(), String> {
    let path = vault_root.join(rel);
    if let Some(p) = path.parent() {
        fs::create_dir_all(p).map_err(|e| format!("mkdir: {e}"))?;
    }
    let raw = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    // Atomic publish: tmp + rename in the same directory, so concurrent
    // readers (the portal serve worker answering HTTP requests) never see a
    // torn projection mid-write.
    let mut tmp_os = path.clone().into_os_string();
    tmp_os.push(".tmp");
    let tmp = std::path::PathBuf::from(tmp_os);
    if let Err(e) = fs::write(&tmp, raw) {
        let _ = fs::remove_file(&tmp);
        return Err(format!("write {rel}: {e}"));
    }
    fs::rename(&tmp, &path).map_err(|e| format!("rename {rel}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn glossary_round_trip() {
        let tmp = tempfile::tempdir().unwrap();
        let mut g = GlossaryFile::default();
        g.terms.push(GlossaryTerm {
            en: "total return swap".into(),
            zh: "收益互换".into(),
            keep_en: true,
            note: Some("TRS".into()),
        });
        g.save(tmp.path()).unwrap();
        let loaded = GlossaryFile::load(tmp.path()).unwrap();
        assert_eq!(loaded.terms.len(), 1);
        assert!(loaded.as_prompt_block().contains("KEEP"));
    }

    #[test]
    fn claim_zh_staleness() {
        let mut f = ClaimsZhFile::default();
        f.entries.insert(
            "ck-abc".into(),
            ClaimZhEntry {
                claim_zh: "记忆是预算".into(),
                en_hash: text_hash("Memory is a budget"),
                model: None,
                translated_at: None,
            },
        );
        assert!(f.get_fresh("ck-abc", "Memory is a budget").is_some());
        assert!(f.get_fresh("ck-abc", "Memory is scarce").is_none());
    }

    #[test]
    fn split_card_heading() {
        let (t, b) = split_card_zh("# 标题\n\n正文段落", "Fallback");
        assert_eq!(t, "标题");
        assert!(b.contains("正文"));
    }

    // ---- topup helpers (shared by manual CLIs + bilingual tails) ----

    use ovp_llm::{CallError, ModelReply, StopReason, Usage};

    /// Scripted client: counts calls and replies with a fixed zh blob.
    struct CountingClient {
        calls: usize,
        reply: String,
    }

    impl CountingClient {
        fn new(reply: &str) -> Self {
            Self {
                calls: 0,
                reply: reply.to_string(),
            }
        }
    }

    impl ModelClient for CountingClient {
        fn call(&mut self, _request: &ModelRequest) -> Result<ModelReply, CallError> {
            self.calls += 1;
            Ok(ModelReply {
                model: "counting".into(),
                text: self.reply.clone(),
                stop_reason: StopReason::EndTurn,
                usage: Usage {
                    input_tokens: 1,
                    output_tokens: 1,
                },
                blocks: None,
                raw_stop_reason: None,
            })
        }
    }

    /// Client whose every call fails (transport-style outage).
    #[derive(Default)]
    struct AlwaysFailsClient {
        calls: usize,
    }

    impl ModelClient for AlwaysFailsClient {
        fn call(&mut self, _request: &ModelRequest) -> Result<ModelReply, CallError> {
            self.calls += 1;
            Err(CallError::Transport {
                detail: "simulated outage".into(),
            })
        }
    }

    /// Client whose replies are truncated at the token cap.
    struct MaxTokensClient;

    impl ModelClient for MaxTokensClient {
        fn call(&mut self, _request: &ModelRequest) -> Result<ModelReply, CallError> {
            Ok(ModelReply {
                model: "truncated".into(),
                text: "partial translation".into(),
                stop_reason: StopReason::MaxTokens,
                usage: Usage {
                    input_tokens: 1,
                    output_tokens: 1,
                },
                blocks: None,
                raw_stop_reason: None,
            })
        }
    }

    fn card(id: &str, title: &str, content: &str) -> (String, String, String) {
        (id.to_string(), title.to_string(), content.to_string())
    }

    #[test]
    fn topup_cards_zh_writes_delta_then_second_run_is_zero_call_noop() {
        let tmp = tempfile::tempdir().unwrap();
        let cards = vec![
            card("c1", "Memory budget", "Memory is a budget."),
            card("c2", "Context", "Context compounds."),
        ];
        let mut client = CountingClient::new("# 标题\n\n正文");
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (2, 0));
        assert!(errors.is_empty());
        assert_eq!(client.calls, 2);
        // Projection file written, keyed by card id.
        let file = CardsZhFile::load(tmp.path()).unwrap();
        assert_eq!(file.entries.len(), 2);
        assert!(file.entries.contains_key("c1"));

        // Unchanged authority: a fresh client must NEVER be called.
        let mut client2 = CountingClient::new("# 标题\n\n正文");
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards, &mut client2, "m", false, 0);
        assert_eq!((done, skipped), (0, 2));
        assert!(errors.is_empty());
        assert_eq!(client2.calls, 0, "unchanged authority = 0 LLM calls");

        // One changed card → exactly one call.
        let cards2 = vec![
            card("c1", "Memory budget", "Memory is a SCARCE budget."),
            card("c2", "Context", "Context compounds."),
        ];
        let mut client3 = CountingClient::new("# 新标题\n\n新正文");
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards2, &mut client3, "m", false, 0);
        assert_eq!((done, skipped), (1, 1));
        assert!(errors.is_empty());
        assert_eq!(client3.calls, 1);
        let file = CardsZhFile::load(tmp.path()).unwrap();
        assert_eq!(file.entries["c1"].title_zh, "新标题");
    }

    #[test]
    fn topup_cards_zh_errors_are_collected_not_thrown() {
        let tmp = tempfile::tempdir().unwrap();
        let cards = vec![card("c1", "T", "B")];
        let mut client = AlwaysFailsClient::default();
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("c1"), "{}", errors[0]);
    }

    /// Interleaved fresh hits make no provider call, so they must not reset
    /// the outage breaker — otherwise a half-populated projection would retry
    /// every stale card through the backoff during an outage (codex P2).
    #[test]
    fn topup_cards_zh_breaker_ignores_fresh_skips() {
        let tmp = tempfile::tempdir().unwrap();
        // Populate c1/c3/c5 as fresh entries.
        let fresh = vec![
            card("c1", "T", "B"),
            card("c3", "T", "B"),
            card("c5", "T", "B"),
        ];
        let mut seed = CountingClient::new("# 标题\n\n正文");
        topup_cards_zh(tmp.path(), &fresh, &mut seed, "m", false, 0);
        // Interleave fresh (skip) with stale (failing) cards.
        let mixed = vec![
            card("c1", "T", "B"),
            card("c2", "T", "B"),
            card("c3", "T", "B"),
            card("c4", "T", "B"),
            card("c5", "T", "B"),
            card("c6", "T", "B"),
        ];
        let mut client = AlwaysFailsClient::default();
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &mixed, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 3));
        assert_eq!(client.calls, 3, "breaker trips on 3 failed CALLS despite interleaved fresh skips");
        assert!(errors.iter().any(|e| e.contains("provider outage")));
    }

    /// Circuit breaker: 3 consecutive failures abort the batch instead of
    /// attempting every stale item through the retrying client's backoff
    /// (provider outage ⇒ a daily tail must not hammer the API).
    #[test]
    fn topup_cards_zh_aborts_after_three_consecutive_failures() {
        let tmp = tempfile::tempdir().unwrap();
        let cards: Vec<_> = (0..5).map(|i| card(&format!("c{i}"), "T", "B")).collect();
        let mut client = AlwaysFailsClient::default();
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(client.calls, 3, "abort after 3 consecutive failures");
        assert_eq!(errors.len(), 4, "3 per-item errors + 1 summary");
        assert!(errors[3].contains("provider outage"), "{}", errors[3]);
    }

    /// A MaxTokens reply is an error, never persisted — stored with a matching
    /// en_hash, a truncated translation would be treated as fresh forever.
    #[test]
    fn max_tokens_reply_errors_and_persists_nothing() {
        let tmp = tempfile::tempdir().unwrap();
        let cards = vec![card("c1", "T", "B")];
        let mut client = MaxTokensClient;
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("stop_reason=max_tokens"), "{}", errors[0]);
        assert!(
            !tmp.path().join(CARDS_ZH_REL).exists(),
            "truncated translations must never be written"
        );
    }

    /// Write unparseable bytes at a projection path; returns the bytes written
    /// so the caller can assert the corrupt file survives the batch untouched.
    fn write_corrupt(vault: &std::path::Path, rel: &str) -> String {
        let path = vault.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let bytes = "{corrupt not json";
        std::fs::write(&path, bytes).unwrap();
        bytes.to_string()
    }

    #[test]
    fn topup_cards_zh_corrupt_projection_fails_once_and_preserves_file() {
        let tmp = tempfile::tempdir().unwrap();
        let corrupt = write_corrupt(tmp.path(), CARDS_ZH_REL);
        let cards = vec![card("c1", "T", "B")];
        let mut client = CountingClient::new("# 标题\n\n正文");
        let (done, skipped, errors) =
            topup_cards_zh(tmp.path(), &cards, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(errors.len(), 1, "one clear error, not N per-item errors");
        assert!(errors[0].contains(CARDS_ZH_REL), "{}", errors[0]);
        assert!(errors[0].contains("corrupt"), "{}", errors[0]);
        assert_eq!(client.calls, 0, "no item attempted against a corrupt projection");
        assert_eq!(
            std::fs::read_to_string(tmp.path().join(CARDS_ZH_REL)).unwrap(),
            corrupt,
            "the corrupt file must NOT be overwritten"
        );
    }

    #[test]
    fn topup_theme_pages_zh_corrupt_projection_fails_once_and_preserves_file() {
        let tmp = tempfile::tempdir().unwrap();
        let corrupt = write_corrupt(tmp.path(), THEME_PAGES_ZH_REL);
        let pages = vec![(7i64, vec![("Memory".to_string(), "Persists.".to_string())])];
        let mut client = CountingClient::new("## 记忆\n\n持久化。");
        let (done, skipped, errors) =
            topup_theme_pages_zh(tmp.path(), &pages, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(errors.len(), 1, "one clear error, not N per-item errors");
        assert!(errors[0].contains(THEME_PAGES_ZH_REL), "{}", errors[0]);
        assert!(errors[0].contains("corrupt"), "{}", errors[0]);
        assert_eq!(client.calls, 0, "no item attempted against a corrupt projection");
        assert_eq!(
            std::fs::read_to_string(tmp.path().join(THEME_PAGES_ZH_REL)).unwrap(),
            corrupt,
            "the corrupt file must NOT be overwritten"
        );
    }

    // ---- translate_claims_batch ----

    fn claim(key: &str, text: &str) -> (String, String) {
        (key.to_string(), text.to_string())
    }

    #[test]
    fn translate_claims_batch_writes_delta_then_second_run_is_zero_call_noop() {
        let tmp = tempfile::tempdir().unwrap();
        let claims = vec![
            claim("ck-1", "Memory is a budget."),
            claim("ck-2", "Context compounds."),
        ];
        let mut client = CountingClient::new("译文");
        let (done, skipped, errors) =
            translate_claims_batch(tmp.path(), &claims, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (2, 0));
        assert!(errors.is_empty());
        assert_eq!(client.calls, 2);
        let file = ClaimsZhFile::load(tmp.path()).unwrap();
        assert_eq!(file.entries.len(), 2);
        assert!(file.entries.contains_key("ck-1"));

        // Unchanged authority: a fresh client must NEVER be called.
        let mut client2 = CountingClient::new("译文");
        let (done, skipped, errors) =
            translate_claims_batch(tmp.path(), &claims, &mut client2, "m", false, 0);
        assert_eq!((done, skipped), (0, 2));
        assert!(errors.is_empty());
        assert_eq!(client2.calls, 0, "unchanged authority = 0 LLM calls");
    }

    #[test]
    fn translate_claims_batch_errors_are_collected_not_thrown() {
        let tmp = tempfile::tempdir().unwrap();
        let claims = vec![claim("ck-1", "Claim one.")];
        let mut client = AlwaysFailsClient::default();
        let (done, skipped, errors) =
            translate_claims_batch(tmp.path(), &claims, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("ck-1"), "{}", errors[0]);
    }

    #[test]
    fn translate_claims_batch_corrupt_projection_fails_once_and_preserves_file() {
        let tmp = tempfile::tempdir().unwrap();
        let corrupt = write_corrupt(tmp.path(), CLAIMS_ZH_REL);
        let claims = vec![claim("ck-1", "Claim one.")];
        let mut client = CountingClient::new("译文");
        let (done, skipped, errors) =
            translate_claims_batch(tmp.path(), &claims, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (0, 0));
        assert_eq!(errors.len(), 1, "one clear error, not N per-item errors");
        assert!(errors[0].contains(CLAIMS_ZH_REL), "{}", errors[0]);
        assert!(errors[0].contains("corrupt"), "{}", errors[0]);
        assert_eq!(client.calls, 0, "no item attempted against a corrupt projection");
        assert_eq!(
            std::fs::read_to_string(tmp.path().join(CLAIMS_ZH_REL)).unwrap(),
            corrupt,
            "the corrupt file must NOT be overwritten"
        );
    }

    #[test]
    fn topup_theme_pages_zh_writes_delta_then_second_run_is_zero_call_noop() {
        let tmp = tempfile::tempdir().unwrap();
        let pages = vec![(
            7i64,
            vec![(
                "Memory".to_string(),
                "Persists [claim:ck-a].".to_string(),
            )],
        )];
        let mut client = CountingClient::new("## 记忆\n\n持久化 [claim:ck-a]。");
        let (done, skipped, errors) =
            topup_theme_pages_zh(tmp.path(), &pages, &mut client, "m", false, 0);
        assert_eq!((done, skipped), (1, 0));
        assert!(errors.is_empty());
        assert_eq!(client.calls, 1, "one section = one call");
        let file = ThemePagesZhFile::load(tmp.path()).unwrap();
        let entry = &file.pages["7"];
        assert_eq!(entry.sections[0].heading, "记忆");
        assert!(
            entry.sections[0].body.contains("[claim:ck-a]"),
            "citation tokens preserved"
        );

        // Unchanged authority: zero calls.
        let mut client2 = CountingClient::new("## 记忆\n\n持久化 [claim:ck-a]。");
        let (done, skipped, errors) =
            topup_theme_pages_zh(tmp.path(), &pages, &mut client2, "m", false, 0);
        assert_eq!((done, skipped), (0, 1));
        assert!(errors.is_empty());
        assert_eq!(client2.calls, 0, "unchanged authority = 0 LLM calls");

        // Changed body → retranslated.
        let pages2 = vec![(
            7i64,
            vec![(
                "Memory".to_string(),
                "Persists and compounds [claim:ck-a].".to_string(),
            )],
        )];
        let mut client3 = CountingClient::new("## 记忆\n\n持久化并复利 [claim:ck-a]。");
        let (done, skipped, errors) =
            topup_theme_pages_zh(tmp.path(), &pages2, &mut client3, "m", false, 0);
        assert_eq!((done, skipped), (1, 0));
        assert!(errors.is_empty());
        assert_eq!(client3.calls, 1);
    }
}
