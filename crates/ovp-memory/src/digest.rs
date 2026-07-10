//! Daily digest: summarize today's product activity into a markdown file.
//!
//! Output: `.ovp/digests/<YYYY-MM-DD>.md` — new packs, crystal status changes,
//! attention items. Optionally synthesized via LLM (token budget constrained).
//! This is an **ephemeral reuse surface** — not durable truth.

use std::path::{Path, PathBuf};

use ovp_index::model::{IndexModel, SourceStatus};
use ovp_llm::{ModelClient, ModelMessage, ModelRequest};

pub struct DigestArgs {
    pub date: String,
    pub max_tokens: usize,
}

pub struct DigestData {
    pub date: String,
    pub new_packs: Vec<PackSummary>,
    pub claims_durable: usize,
    pub claims_caveated: usize,
    pub sources_blocked: usize,
    pub sources_failed: usize,
    pub sources_queued: usize,
    pub run_summary: Option<RunSummary>,
}

pub struct PackSummary {
    pub title: String,
    pub cards: usize,
    pub units: usize,
}

pub struct RunSummary {
    pub run_id: String,
    pub succeeded: usize,
    pub failed: usize,
    pub skipped: usize,
    pub ingested: usize,
}

pub fn collect_digest_data(model: &IndexModel, date: &str) -> DigestData {
    let new_packs: Vec<PackSummary> = model
        .packs
        .iter()
        .filter(|p| p.date.as_deref() == Some(date))
        .map(|p| PackSummary { title: p.title.clone(), cards: p.cards, units: p.units })
        .collect();

    let run_summary = model
        .runs
        .iter()
        .rev()
        .find(|r| r.date == date)
        .map(|r| RunSummary {
            run_id: r.run_id.clone(),
            succeeded: r.succeeded,
            failed: r.failed,
            skipped: r.skipped,
            ingested: r.ingested,
        });

    let sources_blocked =
        model.sources.iter().filter(|s| s.status == SourceStatus::Blocked).count();
    let sources_failed = model.sources.iter().filter(|s| s.status == SourceStatus::Failed).count();
    let sources_queued = model.sources.iter().filter(|s| s.status == SourceStatus::Queued).count();

    DigestData {
        date: date.to_string(),
        new_packs,
        claims_durable: model.totals.claims_durable,
        claims_caveated: model.totals.claims_caveated,
        sources_blocked,
        sources_failed,
        sources_queued,
        run_summary,
    }
}

pub fn render_plain_digest(data: &DigestData) -> String {
    let mut out = String::new();
    out.push_str(&format!("# Daily Digest — {}\n\n", data.date));

    out.push_str("## Run\n\n");
    match &data.run_summary {
        Some(r) => {
            out.push_str(&format!(
                "- **{}**: succeeded={} failed={} skipped={} ingested={}\n\n",
                r.run_id, r.succeeded, r.failed, r.skipped, r.ingested
            ));
        }
        None => out.push_str("- No runs recorded for today.\n\n"),
    }

    out.push_str("## New Reader Packs\n\n");
    if data.new_packs.is_empty() {
        out.push_str("- None today.\n\n");
    } else {
        for p in &data.new_packs {
            out.push_str(&format!("- **{}** ({} cards, {} units)\n", p.title, p.cards, p.units));
        }
        out.push('\n');
    }

    out.push_str("## Crystal Status\n\n");
    out.push_str(&format!("- Durable claims: {}\n", data.claims_durable));
    out.push_str(&format!("- Caveated (review): {}\n\n", data.claims_caveated));

    out.push_str("## Attention\n\n");
    if data.sources_blocked > 0 || data.sources_failed > 0 {
        if data.sources_blocked > 0 {
            out.push_str(&format!("- {} source(s) **blocked** (needs operator review)\n", data.sources_blocked));
        }
        if data.sources_failed > 0 {
            out.push_str(&format!("- {} source(s) failed (will retry)\n", data.sources_failed));
        }
    } else {
        out.push_str("- No attention items.\n");
    }
    if data.sources_queued > 0 {
        out.push_str(&format!("- {} source(s) queued for processing\n", data.sources_queued));
    }

    out
}

pub fn render_llm_digest(
    data: &DigestData,
    client: &mut dyn ModelClient,
    max_tokens: u32,
    model_name: &str,
) -> Result<String, String> {
    let plain = render_plain_digest(data);

    let prompt = format!(
        "You are an operations assistant for OVP (Obsidian Vault Pipeline). \
         Summarize the following daily activity log into a concise, actionable digest. \
         Keep it brief (under 500 words). Highlight anything that needs attention. \
         Output markdown.\n\n---\n\n{plain}"
    );

    let request = ModelRequest {
        model: model_name.to_string(),
        system: Some("You are a concise technical operations summarizer.".into()),
        messages: vec![ModelMessage::User { content: prompt }],
        max_tokens,
        temperature: Some(0.3),
        cache_namespace: Some("digest/v1".into()),
    };

    let reply = client.call(&request).map_err(|e| format!("LLM digest: {e}"))?;

    let mut out = String::new();
    out.push_str(&format!("# Daily Digest — {} (LLM synthesized)\n\n", data.date));
    out.push_str(&reply.text);
    out.push_str("\n\n---\n\n<details><summary>Raw data</summary>\n\n");
    out.push_str(&render_plain_digest(data));
    out.push_str("\n</details>\n");
    Ok(out)
}

pub fn digest_path(vault_root: &Path, date: &str) -> PathBuf {
    vault_root.join(".ovp").join("digests").join(format!("{date}.md"))
}

pub fn write_digest(vault_root: &Path, date: &str, content: &str) -> Result<PathBuf, String> {
    let path = digest_path(vault_root, date);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("create digests dir: {e}"))?;
    }
    std::fs::write(&path, content).map_err(|e| format!("write digest: {e}"))?;
    Ok(path)
}
