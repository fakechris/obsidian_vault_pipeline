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

/// Cap on body chars sent to a single translate/summarize request.
pub const MAX_WORK_BODY_CHARS: usize = 48_000;

/// Chunk size for long documents (refined translation).
const CHUNK_CHARS: usize = 12_000;

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

/// True when Latin letter share among CJK+Latin is high (mirror of UI heuristic).
pub fn is_primarily_english(text: &str) -> bool {
    let body = strip_frontmatter(text);
    if body.chars().count() < 80 {
        return false;
    }
    let mut cjk = 0usize;
    let mut latin = 0usize;
    for ch in body.chars() {
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
    let letters = cjk + latin;
    if letters < 40 {
        return false;
    }
    (latin as f64) / (letters as f64) >= 0.85
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
        // v2: stronger terminology policy + optional session glossary.
        cache_namespace: Some("source_work/v2".into()),
    };
    let reply = client.call(&req).map_err(|e| e.to_string())?;
    let text = reply.text.trim().to_string();
    if text.is_empty() {
        return Err("model returned empty text".into());
    }
    Ok(text)
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

    let body = clip_chars(body, MAX_WORK_BODY_CHARS);
    let chunks = chunk_body(&body);
    // Pre-pass glossary (industry refined pattern): extract terms once from a
    // head sample so multi-chunk (and long single-chunk) stays terminologically
    // consistent — product codes stay Latin, finance jargon uses market forms.
    let glossary = build_session_glossary(client, model, &body, chunks.len())?;
    let mut parts = Vec::with_capacity(chunks.len());
    for (i, chunk) in chunks.iter().enumerate() {
        let user = format_translate_user(chunk, i, chunks.len(), glossary.as_deref());
        parts.push(llm_text(client, model, TRANSLATE_SYSTEM, &user, 8192)?);
    }
    let zh = parts.join("\n\n");
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
}
