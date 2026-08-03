//! Source-work artifacts: refined bilingual translation + deep summary.
//!
//! Durable vault archive (not ephemeral `.ovp` scratch):
//!
//! ```text
//! 40-Resources/Source-Work/<sha8>_<slug>/
//!   meta.json
//!   original.md
//!   zh.md
//!   summary.md
//! ```
//!
//! Translation uses the product LLM (providers.toml / ask factory) with a
//! refined 信达雅 system prompt + session glossary pre-pass (industry
//! refined-translator pattern: analyze terms → consistent render). Never free
//! MT engines.

use std::fs;
use std::path::{Path, PathBuf};

use ovp_llm::{ModelClient, ModelMessage, ModelRequest};
use serde::{Deserialize, Serialize};

/// Vault-relative root for source-work archives.
pub const SOURCE_WORK_ROOT: &str = "40-Resources/Source-Work";

/// Cap on body chars for a single summarize request (deep note need not
/// cover every appendix of a monorepo dump).
pub const MAX_WORK_BODY_CHARS: usize = 48_000;

/// Translate may walk further than summarize: long GitHub/deepwiki pages
/// still get multi-chunk refined translation. Soft ceiling on total chars
/// (≈ max_chunks × chunk size) so a pathological dump cannot burn unbounded
/// tokens. Beyond this the tail is truncated with an explicit note.
pub const MAX_TRANSLATE_BODY_CHARS: usize = 160_000;

/// Chunk size for long documents (refined translation).
const CHUNK_CHARS: usize = 12_000;

/// Hard cap on chunk count for one translate job.
const MAX_TRANSLATE_CHUNKS: usize = 16;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SourceWorkMeta {
    pub schema: String,
    pub source_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// Vault-relative directory of this archive.
    pub work_rel: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub translated_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summarized_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default)]
    pub source_lang: String,
}

impl SourceWorkMeta {
    pub fn new(sha: &str, title: Option<&str>, url: Option<&str>, work_rel: &str) -> Self {
        Self {
            schema: "ovp.source-work/v1".into(),
            source_sha256: sha.into(),
            title: title.map(str::to_string),
            url: url.map(str::to_string),
            work_rel: work_rel.into(),
            translated_at: None,
            summarized_at: None,
            model: None,
            source_lang: "en".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceWorkStatus {
    pub work_rel: String,
    pub has_original: bool,
    pub has_zh: bool,
    pub has_summary: bool,
    pub primarily_english: bool,
    pub meta: Option<SourceWorkMeta>,
}

/// Count CJK (incl. JP kana/ext) vs ASCII Latin letters in `text`.
fn count_script_letters(text: &str) -> (usize, usize) {
    let mut cjk = 0usize;
    let mut latin = 0usize;
    for ch in text.chars() {
        let c = ch as u32;
        if (0x4E00..=0x9FFF).contains(&c)
            || (0x3400..=0x4DBF).contains(&c)
            || (0x3040..=0x30FF).contains(&c)
        {
            cjk += 1;
        } else if ch.is_ascii_alphabetic() {
            latin += 1;
        }
    }
    (cjk, latin)
}

/// True when Latin letter share among CJK+Latin is high (mirror of UI heuristic).
pub fn is_primarily_english(text: &str) -> bool {
    let body = strip_frontmatter(text);
    if body.chars().count() < 80 {
        return false;
    }
    let (cjk, latin) = count_script_letters(body);
    let letters = cjk + latin;
    if letters < 40 {
        return false;
    }
    (latin as f64) / (letters as f64) >= 0.85
}

/// Drop fenced + inline code so language detection scores prose, not source.
///
/// Monorepo / deepwiki dumps are mostly paths and fences; those stay Latin
/// even after a correct translation. Without stripping them, a good zh body
/// can still look "English" by letter share.
pub fn strip_code_for_lang_detect(text: &str) -> String {
    // Fenced blocks first (line-oriented).
    let mut lines = text.split_inclusive('\n').peekable();
    let mut rebuilt = String::with_capacity(text.len());
    while let Some(line) = lines.next() {
        if line.trim_start().starts_with("```") {
            while let Some(l2) = lines.next() {
                if l2.trim_start().starts_with("```") {
                    break;
                }
            }
            rebuilt.push(' ');
            continue;
        }
        rebuilt.push_str(line);
    }
    // Inline `code` — char-safe (CJK must not be byte-sliced).
    let mut out = String::with_capacity(rebuilt.len());
    let mut chars = rebuilt.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '`' {
            while let Some(c2) = chars.next() {
                if c2 == '`' {
                    break;
                }
            }
            out.push(' ');
        } else {
            out.push(ch);
        }
    }
    out
}

/// Accept EN→zh output only when it is no longer primarily English.
///
/// **Root-cause class this gates (2026-08 vault audit):** models (and
/// poisoned `source_work/v2` cassettes) sometimes return English copy-through
/// or near-untranslated prose. Pre-gate we only checked that the *source*
/// looked English, then wrote any non-empty sanitize result as `zh.md` and
/// stamped `translated_at` — silent false "done".
///
/// Rule: language share is measured on **prose** (code fences + inline code
/// stripped). Latin letter share among CJK+Latin must be **below** the
/// `is_primarily_english` bar (85%). Pure-English bodies without fences fail
/// the same way; code-heavy monorepos with real Chinese prose pass.
pub fn is_acceptable_zh_translation(zh: &str) -> bool {
    let body = strip_frontmatter(zh);
    if body.chars().count() < 40 {
        // Tiny stubs: require at least one CJK ideograph.
        return count_script_letters(body).0 > 0;
    }
    let prose = strip_code_for_lang_detect(body);
    let (cjk_p, latin_p) = count_script_letters(&prose);
    let letters_p = cjk_p + latin_p;
    if letters_p >= 40 {
        return (latin_p as f64) / (letters_p as f64) < 0.85;
    }
    // Almost no prose left after stripping code — demand some CJK in the
    // full body, otherwise treat as untranslated English dump.
    let (cjk, _latin) = count_script_letters(body);
    cjk > 0
}

/// True when zh prose is essentially still the English original (copy-through).
pub fn is_near_untranslated_copy(zh: &str, original: &str) -> bool {
    fn norm(s: &str) -> String {
        strip_code_for_lang_detect(strip_frontmatter(s))
            .chars()
            .filter(|c| !c.is_whitespace())
            .take(400)
            .collect::<String>()
            .to_ascii_lowercase()
    }
    let a = norm(zh);
    let b = norm(original);
    if a.len() < 120 || b.len() < 120 {
        return false;
    }
    let n = a.len().min(b.len()).min(280);
    a[..n] == b[..n]
}

fn strip_frontmatter(text: &str) -> &str {
    let t = text.trim_start();
    if let Some(rest) = t.strip_prefix("---\n") {
        if let Some(idx) = rest.find("\n---") {
            return rest[idx + 4..].trim_start();
        }
    }
    text
}

fn slugify(title: &str, max: usize) -> String {
    let mut out = String::new();
    for ch in title.chars() {
        if out.chars().count() >= max {
            break;
        }
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if (ch == ' ' || ch == '-' || ch == '_') && !out.ends_with('-') {
            out.push('-');
        } else if ch > '\u{7f}' && !ch.is_whitespace() {
            // Keep a few CJK chars for readability in vault paths.
            out.push(ch);
        }
    }
    let s = out.trim_matches('-').to_string();
    if s.is_empty() {
        "source".into()
    } else {
        s
    }
}

/// Deterministic work directory relative path for a source.
pub fn work_rel_for(sha: &str, title: Option<&str>) -> String {
    let sha8 = sha.chars().take(8).collect::<String>();
    let slug = slugify(title.unwrap_or("source"), 40);
    format!("{SOURCE_WORK_ROOT}/{sha8}_{slug}")
}

pub fn work_abs(vault_root: &Path, work_rel: &str) -> PathBuf {
    vault_root.join(work_rel)
}

pub fn load_status(
    vault_root: &Path,
    sha: &str,
    title: Option<&str>,
    body: &str,
) -> SourceWorkStatus {
    let work_rel = work_rel_for(sha, title);
    let dir = work_abs(vault_root, &work_rel);
    let meta = read_meta(&dir);
    SourceWorkStatus {
        work_rel,
        has_original: dir.join("original.md").is_file(),
        has_zh: dir.join("zh.md").is_file(),
        has_summary: dir.join("summary.md").is_file(),
        primarily_english: is_primarily_english(body),
        meta,
    }
}

fn read_meta(dir: &Path) -> Option<SourceWorkMeta> {
    let p = dir.join("meta.json");
    let raw = fs::read_to_string(p).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_meta(dir: &Path, meta: &SourceWorkMeta) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|e| format!("mkdir work dir: {e}"))?;
    let raw = serde_json::to_string_pretty(meta).map_err(|e| e.to_string())?;
    fs::write(dir.join("meta.json"), raw).map_err(|e| format!("write meta: {e}"))
}

/// Serialize meta.json updates so parallel translate + summarize never
/// clobber each other's stamps (read-modify-write under one process lock).
static WORK_META_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn patch_meta(
    dir: &Path,
    sha: &str,
    title: Option<&str>,
    url: Option<&str>,
    work_rel: &str,
    patch: impl FnOnce(&mut SourceWorkMeta),
) -> Result<(), String> {
    let _guard = WORK_META_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut meta =
        read_meta(dir).unwrap_or_else(|| SourceWorkMeta::new(sha, title, url, work_rel));
    patch(&mut meta);
    write_meta(dir, &meta)
}

fn ensure_original(dir: &Path, body: &str) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|e| format!("mkdir: {e}"))?;
    let p = dir.join("original.md");
    if !p.is_file() {
        fs::write(&p, body).map_err(|e| format!("write original: {e}"))?;
    }
    Ok(())
}

fn clip_chars(s: &str, max: usize) -> String {
    let n = s.chars().count();
    if n <= max {
        return s.to_string();
    }
    s.chars().take(max).collect::<String>() + "\n\n…[truncated for length]"
}

fn chunk_body(body: &str) -> Vec<String> {
    let body = strip_frontmatter(body);
    let chars: Vec<char> = body.chars().collect();
    if chars.len() <= CHUNK_CHARS {
        return vec![body.to_string()];
    }
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let end = (i + CHUNK_CHARS).min(chars.len());
        // Prefer break at paragraph.
        let mut cut = end;
        if end < chars.len() {
            if let Some(rel) = chars[i..end].iter().rposition(|&c| c == '\n') {
                if rel > CHUNK_CHARS / 2 {
                    cut = i + rel + 1;
                }
            }
        }
        out.push(chars[i..cut].iter().collect());
        i = cut;
    }
    out
}

/// System prompt for EN→zh refined translation.
///
/// Quality bar is closer to industry refined translators (analysis → glossary
/// → translate → consistency) than free MT. Glossary is built in a prior call
/// for long docs and injected into each chunk.
const TRANSLATE_SYSTEM: &str = r#"You are a professional EN→zh-CN translator for technical and financial long-form (literary-technical register).

Rewrite into natural Simplified Chinese that a skilled native editor would publish — not word-for-word MT. Facts, numbers, and logic must match the source exactly.

## 信 · 达 · 雅
1. Faithful: meaning, argument structure, headings, tables, lists, links.
2. Fluent: idiomatic Chinese word order; break long English sentences; no Chinglish.
3. Elegant: tight rhythm; ban AI filler (总之/值得注意的是/在当今/首先…其次…最后 as boilerplate).

## Terminology (most quality is won or lost here)
**KEEP in English / original form (do NOT invent Chinese):**
- Tickers & product codes: NVDA, TSLA, 7709.HK, NVDL, TSLL, MUU, SK Hynix, etc.
- Exchanges & indices when conventionally Latin in CN media: KOSPI, NASDAQ, NYSE, S&P 500
- Acronyms widely used as-is in CN finance/tech: ETF, AUM, ADR, TRS, NAV, ASP, LTA, AP, FSS, HBM, DRAM, NAND, FOFs, PM, ROI
- Metrics notation: -6.07σ, 2x, Level 1, Q2
- Brand / product proper names when CN press keeps Latin (Goldman Sachs may be 高盛 — use the form already dominant in CN finance press)

**Standard industry Chinese (do not invent calques):**
- rebalancing → 再平衡; circuit breaker → 熔断; collateral → 保证金
- total return swap → 收益互换（Total Return Swap, TRS）on first use, then TRS/收益互换
- delta hedging → Delta 对冲; notional → 名义本金/名义敞口; volatility decay → 波动率衰减（Volatility Decay）
- authorized participant → 授权参与者（AP）

**First occurrence of specialized terms:** 「中文（English）」or keep English with brief Chinese gloss when the English form is the market standard.

**Consistency:** If a session glossary is provided below the user message, OBEY it for the whole chunk. Never switch mid-article.

## Hard rules
- Do NOT invent citations, numbers, or facts.
- Output ONLY the translated markdown body (no preface like「以下是翻译」).
- Preserve fenced code / inline `code` (except translating prose comments).
- Keep wikilinks `[[…]]` and URLs intact.
- Keep markdown tables aligned; do not drop columns.
"#;

/// One-shot term extraction before multi-chunk translate (baoyu-style glossary).
const GLOSSARY_SYSTEM: &str = r#"You extract a translation glossary for EN→zh-CN of a technical/finance article.

Output ONLY a bullet list (max 40 lines), one term per line, no prose:
- EnglishTerm → ChineseOrKEEP | note

Rules:
- Tickers, product codes, exchange codes, Greek metrics: → KEEP
- Established finance/tech jargon: standard CN press form + English in parens when helpful
- Prefer KEEP when a forced Chinese would sound amateur or non-standard
- No headings, no intro, no closing
"#;

const SUMMARY_SYSTEM: &str = r#"You are a senior research analyst writing a deep reading note for a personal knowledge vault.

Write in Simplified Chinese (keep critical English terms as 中文（English） on first use).

Structure the markdown EXACTLY as:

## 一句话
(one sentence thesis)

## 核心论点
- 3–7 bullets of the load-bearing claims

## 方法 / 机制
How it works (architecture, experiment, process) — concrete, not vague

## 证据与数据
What evidence the source offers (or admits is missing)

## 局限与风险
Caveats, failure modes, open questions

## 可行动作
2–5 things the reader could try or verify next
(use this exact heading — not 「可行动动作」)

## 术语表
| 术语 | 含义 |

Rules: no marketing fluff; quote key phrases sparingly with “…”; do not invent numbers.
"#;

fn now_rfc3339() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Good enough for archive stamps; full chrono available elsewhere.
    format!("{secs}")
}

/// Build the source_work LLM request (shared key material for call + invalidate).
fn source_work_request(
    model: &str,
    system: &str,
    user: &str,
    max_tokens: u32,
) -> ModelRequest {
    ModelRequest {
        model: model.to_string(),
        system: Some(system.to_string()),
        messages: vec![ModelMessage::User {
            content: user.to_string(),
        }],
        max_tokens,
        temperature: Some(0.2),
        tools: None,
        // v2: stronger terminology policy + optional session glossary.
        // Quality gate invalidates bad keys in-place; do not bump namespace
        // solely for English-output failures (would thrash good cassettes).
        cache_namespace: Some("source_work/v2".into()),
    }
}

fn llm_text(
    client: &mut dyn ModelClient,
    model: &str,
    system: &str,
    user: &str,
    max_tokens: u32,
) -> Result<String, String> {
    let req = source_work_request(model, system, user, max_tokens);
    let reply = client.call(&req).map_err(|e| e.to_string())?;
    let text = reply.text.trim().to_string();
    if text.is_empty() {
        client.invalidate(&req);
        return Err("model returned empty text".into());
    }
    Ok(text)
}

/// Call model and return both text and the request (for cassette invalidate).
fn llm_text_tracked(
    client: &mut dyn ModelClient,
    model: &str,
    system: &str,
    user: &str,
    max_tokens: u32,
) -> Result<(ModelRequest, String), String> {
    let req = source_work_request(model, system, user, max_tokens);
    let reply = client.call(&req).map_err(|e| e.to_string())?;
    let text = reply.text.trim().to_string();
    if text.is_empty() {
        client.invalidate(&req);
        return Err("model returned empty text".into());
    }
    Ok((req, text))
}

/// Translate one chunk: sanitize + optional quality gate with one live retry
/// after cassette invalidate (poisoned English Record hits).
fn translate_chunk_with_gate(
    client: &mut dyn ModelClient,
    model: &str,
    user: &str,
    source_chunk: &str,
    gate: bool,
) -> Result<(ModelRequest, String), String> {
    let (req, raw) = llm_text_tracked(client, model, TRANSLATE_SYSTEM, user, 8192)?;
    let cleaned = sanitize_translate_output(&raw);
    if !gate {
        return Ok((req, cleaned));
    }
    let bad = !is_acceptable_zh_translation(&cleaned)
        || is_near_untranslated_copy(&cleaned, source_chunk);
    if !bad {
        return Ok((req, cleaned));
    }
    // Drop poisoned cassette and re-ask once (Record mode re-fills).
    client.invalidate(&req);
    let (req2, raw2) = llm_text_tracked(client, model, TRANSLATE_SYSTEM, user, 8192)?;
    let cleaned2 = sanitize_translate_output(&raw2);
    if !is_acceptable_zh_translation(&cleaned2)
        || is_near_untranslated_copy(&cleaned2, source_chunk)
    {
        client.invalidate(&req2);
        return Err(
            "translate output still primarily English after live retry (quality gate); \
             will not mark zh done"
                .into(),
        );
    }
    Ok((req2, cleaned2))
}

/// Translate body → zh.md (skip if already present unless `force`).
pub fn translate_source(
    vault_root: &Path,
    sha: &str,
    title: Option<&str>,
    url: Option<&str>,
    body: &str,
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
) -> Result<SourceWorkStatus, String> {
    if !is_primarily_english(body) {
        return Err("source does not look primarily English; translate declined".into());
    }
    let work_rel = work_rel_for(sha, title);
    let dir = work_abs(vault_root, &work_rel);
    ensure_original(&dir, body)?;
    let zh_path = dir.join("zh.md");
    if zh_path.is_file() && !force {
        return Ok(load_status(vault_root, sha, title, body));
    }

    // Long docs: clip to translate ceiling (not the shorter summarize cap),
    // then chunk. Deepwiki monorepo dumps were previously truncated at 48k
    // and looked "done" while missing most sections.
    let body = clip_chars(body, MAX_TRANSLATE_BODY_CHARS);
    let mut chunks = chunk_body(&body);
    let truncated_chunks = chunks.len() > MAX_TRANSLATE_CHUNKS;
    if truncated_chunks {
        chunks.truncate(MAX_TRANSLATE_CHUNKS);
    }
    // Pre-pass glossary (industry refined pattern): extract terms once from a
    // head sample so multi-chunk (and long single-chunk) stays terminologically
    // consistent — product codes stay Latin, finance jargon uses market forms.
    let glossary = build_session_glossary(client, model, &body, chunks.len())?;
    let mut parts = Vec::with_capacity(chunks.len());
    let mut chunk_reqs: Vec<ModelRequest> = Vec::with_capacity(chunks.len());
    for (i, chunk) in chunks.iter().enumerate() {
        let user = format_translate_user(chunk, i, chunks.len(), glossary.as_deref());
        let (req, cleaned) =
            translate_chunk_with_gate(client, model, &user, chunk, chunks.len() == 1)?;
        chunk_reqs.push(req);
        parts.push(cleaned);
    }
    let mut zh = sanitize_translate_output(&parts.join("\n\n"));
    if truncated_chunks {
        zh.push_str(
            "\n\n---\n\n> 〔截断〕原文超过翻译长度上限，尾部未译。可对源做拆分或提高 `MAX_TRANSLATE_BODY_CHARS` 后 `--force` 重跑。\n",
        );
    }
    // Final join gate (covers multi-chunk dilution / copy-through).
    if !is_acceptable_zh_translation(&zh) || is_near_untranslated_copy(&zh, &body) {
        for req in &chunk_reqs {
            client.invalidate(req);
        }
        // One live re-join path is expensive; invalidate so a requeue is not
        // pinned to the bad cassette set, and refuse to stamp done.
        return Err(
            "translate output still primarily English (quality gate); \
             cassettes invalidated — will not mark zh done"
                .into(),
        );
    }
    fs::write(&zh_path, &zh).map_err(|e| format!("write zh.md: {e}"))?;
    if let Some(g) = glossary.as_ref() {
        // Durable for operators to audit / reuse (not shown in the portal tab).
        let _ = fs::write(dir.join("glossary.md"), g);
    }

    patch_meta(&dir, sha, title, url, &work_rel, |meta| {
        meta.translated_at = Some(now_rfc3339());
        meta.model = Some(model.to_string());
        meta.source_lang = "en".into();
    })?;
    Ok(load_status(vault_root, sha, title, &body))
}

/// Strip model echoes of the session-glossary prompt and collapse empty
/// fenced code blocks left when diagrams/source were dropped.
///
/// Observed failures (2026-08 vault):
/// - mid-body `## Session glossary (OBEY — …)` plus empty fences
/// - CoT JSON envelope `{understanding, plan, reasoning, response}` (or
///   fenced ```json) with the real translation only under `response`
///
/// Authority is the EN original; zh is rebuildable, so aggressive sanitize
/// is safer than leaving prompt / chain-of-thought text in the vault.
pub fn sanitize_translate_output(text: &str) -> String {
    let mut out = text.replace("\r\n", "\n");
    out = unwrap_cot_json_envelope(&out);
    out = strip_session_glossary_blocks(&out);
    for prefix in [
        "以下是翻译：",
        "以下是翻译:",
        "以下为翻译：",
        "以下为翻译:",
        "翻译如下：",
        "翻译如下:",
        "Here is the translation:",
        "Here's the translation:",
    ] {
        if let Some(rest) = out.trim_start().strip_prefix(prefix) {
            out = rest.trim_start().to_string();
        }
    }
    out = strip_empty_fences(&out);
    out = squeeze_blank_lines(&out);
    out.trim().to_string() + "\n"
}

/// If the model returned a CoT / tool-style JSON wrapper, keep only `response`
/// (or `translation` / `zh` / `text`). Otherwise return input unchanged.
fn unwrap_cot_json_envelope(text: &str) -> String {
    let trimmed = text.trim();
    // Fenced ```json … ```
    let candidate = if let Some(rest) = trimmed.strip_prefix("```") {
        let rest = rest
            .strip_prefix("json")
            .or_else(|| rest.strip_prefix("JSON"))
            .unwrap_or(rest)
            .trim_start_matches('\n');
        if let Some(end) = rest.rfind("```") {
            rest[..end].trim()
        } else {
            trimmed
        }
    } else {
        trimmed
    };
    if !(candidate.starts_with('{') && candidate.contains("\"response\""))
        && !(candidate.starts_with('{') && candidate.contains("\"translation\""))
    {
        // Also handle: prose + trailing/leading json fence with response key.
        if let Some(extracted) = extract_response_from_embedded_json(text) {
            return extracted;
        }
        return text.to_string();
    }
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(candidate) {
        for key in ["response", "translation", "zh", "text", "content"] {
            if let Some(s) = v.get(key).and_then(|x| x.as_str()) {
                if s.chars().count() > 40 {
                    return s.to_string();
                }
            }
        }
    }
    if let Some(extracted) = extract_response_from_embedded_json(text) {
        return extracted;
    }
    text.to_string()
}

/// Scan for a fenced or raw JSON object that has a string `response` field
/// large enough to be the translation body (hyperagents-style leak).
fn extract_response_from_embedded_json(text: &str) -> Option<String> {
    // Prefer fenced json blocks.
    let mut search = text;
    while let Some(start) = search.find("```") {
        let after = &search[start + 3..];
        let after = after
            .strip_prefix("json")
            .or_else(|| after.strip_prefix("JSON"))
            .unwrap_or(after);
        let after = after.trim_start_matches('\n');
        let end = after.find("```")?;
        let block = after[..end].trim();
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(block) {
            for key in ["response", "translation", "zh"] {
                if let Some(s) = v.get(key).and_then(|x| x.as_str()) {
                    if s.chars().count() > 40 {
                        // Keep material before the fence + extracted response.
                        let prefix = text[..text.len() - search.len() + start].trim_end();
                        if prefix.is_empty() {
                            return Some(s.to_string());
                        }
                        return Some(format!("{prefix}\n\n{s}"));
                    }
                }
            }
        }
        search = &after[end + 3..];
    }
    None
}

fn squeeze_blank_lines(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut blank_run = 0usize;
    for line in text.split_inclusive('\n') {
        if line.trim().is_empty() {
            blank_run += 1;
            if blank_run <= 2 {
                out.push('\n');
            }
        } else {
            blank_run = 0;
            out.push_str(line);
        }
    }
    out
}

fn strip_session_glossary_blocks(text: &str) -> String {
    let lower = text.to_ascii_lowercase();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    let marker = "## session glossary";
    while i < text.len() {
        let search_from = i;
        let rel = lower[search_from..].find(marker).map(|p| search_from + p);
        let Some(start) = rel else {
            out.push_str(&text[i..]);
            break;
        };
        // Require line start.
        if start > 0 && text.as_bytes()[start - 1] != b'\n' {
            // False hit inside a line — skip past this occurrence and continue.
            out.push_str(&text[i..start + marker.len()]);
            i = start + marker.len();
            continue;
        }
        out.push_str(&text[i..start]);
        let after = &text[start..];
        let mut consumed = after.find('\n').map(|n| n + 1).unwrap_or(after.len());
        let mut end_rel = consumed;
        for line in after[consumed..].split_inclusive('\n') {
            let t = line.trim();
            if t.starts_with("## ") && !t.to_ascii_lowercase().contains("glossary") {
                break;
            }
            let keep = t.is_empty()
                || t.starts_with('-')
                || t.starts_with('*')
                || t.starts_with('|')
                || t.contains('→')
                || t.contains("->")
                || t.to_ascii_lowercase().contains("obey")
                || t.to_ascii_lowercase().contains("keep");
            if !keep && !t.is_empty() {
                break;
            }
            consumed += line.len();
            end_rel = consumed;
        }
        i = start + end_rel;
    }
    out
}

fn strip_empty_fences(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut lines = text.split_inclusive('\n').peekable();
    while let Some(line) = lines.next() {
        if line.trim().starts_with("```") {
            let mut body = String::new();
            let mut closed = false;
            let mut close_line = String::new();
            while let Some(l2) = lines.next() {
                if l2.trim().starts_with("```") {
                    closed = true;
                    close_line = l2.to_string();
                    break;
                }
                body.push_str(l2);
            }
            if closed && body.trim().is_empty() {
                continue;
            }
            out.push_str(line);
            out.push_str(&body);
            if closed {
                out.push_str(&close_line);
            }
        } else {
            out.push_str(line);
        }
    }
    out
}

fn format_translate_user(
    chunk: &str,
    index: usize,
    total: usize,
    glossary: Option<&str>,
) -> String {
    let mut out = String::new();
    if let Some(g) = glossary.map(str::trim).filter(|s| !s.is_empty()) {
        out.push_str("## Session glossary (OBEY — do not re-decide these terms)\n");
        out.push_str(g);
        out.push_str("\n\n");
    }
    if total == 1 {
        out.push_str("Translate the following markdown to Simplified Chinese.\n\n");
    } else {
        out.push_str(&format!(
            "Translate chunk {}/{} of one continuous article to Simplified Chinese. \
             Match glossary + prior-chunk terminology; do not re-introduce alternate names.\n\n",
            index + 1,
            total
        ));
    }
    out.push_str(chunk);
    out
}

/// Extract a short glossary for the session. Best-effort: failure returns None
/// so translate still proceeds (never blocks the archive on glossary errors).
fn build_session_glossary(
    client: &mut dyn ModelClient,
    model: &str,
    body: &str,
    chunk_count: usize,
) -> Result<Option<String>, String> {
    // Always run for multi-chunk; for single long bodies (>4k chars) also run.
    let chars = body.chars().count();
    if chunk_count <= 1 && chars < 4_000 {
        return Ok(None);
    }
    let sample: String = body.chars().take(8_000).collect();
    let user = format!(
        "Article title/domain sample for glossary extraction:\n\n{sample}"
    );
    match llm_text(client, model, GLOSSARY_SYSTEM, &user, 1500) {
        Ok(g) if !g.trim().is_empty() => Ok(Some(g.trim().to_string())),
        Ok(_) => Ok(None),
        Err(e) => {
            // Soft-fail: translation quality degrades slightly but still completes.
            let _ = e;
            Ok(None)
        }
    }
}

/// Deep summary → summary.md.
pub fn summarize_source(
    vault_root: &Path,
    sha: &str,
    title: Option<&str>,
    url: Option<&str>,
    body: &str,
    client: &mut dyn ModelClient,
    model: &str,
    force: bool,
) -> Result<SourceWorkStatus, String> {
    let work_rel = work_rel_for(sha, title);
    let dir = work_abs(vault_root, &work_rel);
    ensure_original(&dir, body)?;
    let sum_path = dir.join("summary.md");
    if sum_path.is_file() && !force {
        return Ok(load_status(vault_root, sha, title, body));
    }
    let body = clip_chars(body, MAX_WORK_BODY_CHARS);
    let title_line = title.unwrap_or("(untitled)");
    let user = format!(
        "Source title: {title_line}\nURL: {}\n\n---\n\n{body}",
        url.unwrap_or("(none)")
    );
    let summary = llm_text(client, model, SUMMARY_SYSTEM, &user, 4096)?;
    fs::write(&sum_path, &summary).map_err(|e| format!("write summary.md: {e}"))?;

    patch_meta(&dir, sha, title, url, &work_rel, |meta| {
        meta.summarized_at = Some(now_rfc3339());
        meta.model = Some(model.to_string());
    })?;
    Ok(load_status(vault_root, sha, title, &body))
}

/// Read an artifact file if present.
pub fn read_work_file(vault_root: &Path, work_rel: &str, name: &str) -> Option<String> {
    if !matches!(name, "original.md" | "zh.md" | "summary.md" | "meta.json") {
        return None;
    }
    // Guard: work_rel must stay under SOURCE_WORK_ROOT and be plain-relative.
    if work_rel.contains("..") || !work_rel.starts_with(SOURCE_WORK_ROOT) {
        return None;
    }
    fs::read_to_string(work_abs(vault_root, work_rel).join(name)).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn english_detection() {
        let en = "The harness is all you need for reliable evaluation of agent systems in production. \
                  Teams that invest in harnesses ship faster with fewer regressions over months.";
        assert!(is_primarily_english(en));
        let zh = "这是一篇关于大模型评估与生产落地的深度笔记。我们讨论了评测集与可靠性，以及团队如何在业务中迭代系统。";
        assert!(!is_primarily_english(zh));
    }

    #[test]
    fn work_rel_stable() {
        let a = work_rel_for("abcdef0123456789", Some("Hello World"));
        assert!(a.starts_with("40-Resources/Source-Work/abcdef01_"));
        assert!(a.contains("hello-world") || a.contains("Hello"));
    }

    #[test]
    fn status_empty_vault() {
        let tmp = tempfile::tempdir().unwrap();
        let en = "The harness is all you need for reliable evaluation of agent systems in production environments today and tomorrow.";
        let st = load_status(tmp.path(), "aaaa1111bbbb", Some("T"), en);
        assert!(!st.has_zh);
        assert!(st.primarily_english);
    }

    #[test]
    fn sanitize_strips_session_glossary_leak() {
        let dirty = r#"# 标题

正文第一段。

## Session glossary (OBEY — do not re-decide these terms)
- open-slide → KEEP
- monorepo → 单体仓库

## 下一章

继续正文。
"#;
        let clean = sanitize_translate_output(dirty);
        assert!(!clean.to_ascii_lowercase().contains("session glossary"));
        assert!(!clean.contains("OBEY"));
        assert!(clean.contains("正文第一段"));
        assert!(clean.contains("下一章"));
        assert!(clean.contains("继续正文"));
    }

    #[test]
    fn sanitize_strips_empty_fences_and_preface() {
        let dirty = "以下是翻译：\n\n前言\n\n```\n\n```\n\n后记\n";
        let clean = sanitize_translate_output(dirty);
        assert!(!clean.starts_with("以下是翻译"));
        assert!(!clean.contains("```"));
        assert!(clean.contains("前言"));
        assert!(clean.contains("后记"));
    }

    #[test]
    fn quality_gate_rejects_english_copy_through() {
        let en = "The harness is all you need for reliable evaluation of agent \
                  systems in production. Teams that invest in harnesses ship \
                  faster with fewer regressions over months of iteration.";
        assert!(!is_acceptable_zh_translation(en));
        assert!(is_near_untranslated_copy(en, en));
    }

    #[test]
    fn quality_gate_accepts_chinese_prose() {
        let zh = "生产环境里，评估 harness 是可靠智能体系统的关键。\
                  愿意投入 harness 的团队通常迭代更快，回归更少。\
                  本文讨论指标、闸门与回放夹具的工程实践。";
        assert!(is_acceptable_zh_translation(zh));
    }

    #[test]
    fn quality_gate_ignores_code_fences_when_prose_is_chinese() {
        // Code stays Latin; prose is Chinese — must still accept.
        let mut body = String::from(
            "本文说明 monorepo 的 TypeScript 配置如何分层。\
             严格模式默认开启，详见下文。\n\n",
        );
        for _ in 0..30 {
            body.push_str("```ts\nconst x = require('fs');\nexport const y = 1;\n```\n\n");
        }
        assert!(
            is_acceptable_zh_translation(&body),
            "code-heavy but Chinese prose must pass"
        );
    }

    #[test]
    fn quality_gate_rejects_english_prose_even_with_chinese_title() {
        let mixed = "# 标题翻译\n\nThe monorepo uses a hierarchical tsconfig structure. \
                     Strict mode is enabled by default. The agents documentation \
                     covers code style, testing, and release procedures in detail \
                     for every package under the apps and packages directories.";
        assert!(!is_acceptable_zh_translation(mixed));
    }

    #[test]
    fn sanitize_unwraps_cot_json_response_envelope() {
        // Build via serde so we do not fight raw-string / markdown-heading rules.
        let body = "## 软件工程已死\n\nHyperagent 框架体现了苦涩的教训。本文继续讨论自我改进与元智能体边界。";
        let envelope = serde_json::json!({
            "understanding": "chunk 2/2 following the session glossary",
            "plan": "translate",
            "response": body,
        });
        let dirty = format!("```json\n{}\n```\n", envelope);
        let clean = sanitize_translate_output(&dirty);
        assert!(clean.contains("软件工程已死"), "{clean}");
        assert!(!clean.contains("understanding"), "{clean}");
        assert!(!clean.contains("session glossary"), "{clean}");
    }
}
