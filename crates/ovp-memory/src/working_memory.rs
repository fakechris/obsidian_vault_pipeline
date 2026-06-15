//! Working memory: a budget-constrained context package rebuilt on each daily
//! run. Ephemeral — not durable truth, not in ledger, not driving projection.
//!
//! Contents: today's digest summary + recent packs (3 days) + active crystal
//! claims. Written to `.ovp/working-memory.md`.

use std::path::{Path, PathBuf};

use ovp_index::model::IndexModel;

pub struct WorkingMemoryArgs {
    pub date: String,
    pub max_tokens: usize,
    pub lookback_days: usize,
}

impl Default for WorkingMemoryArgs {
    fn default() -> Self {
        Self { date: String::new(), max_tokens: 4000, lookback_days: 3 }
    }
}

pub fn build_working_memory(model: &IndexModel, args: &WorkingMemoryArgs) -> String {
    let mut out = String::new();
    out.push_str(&format!("# Working Memory — {}\n\n", args.date));
    out.push_str("> Ephemeral context package. Rebuilt each daily run. NOT durable truth.\n\n");

    out.push_str("## Recent Reader Packs\n\n");
    let date_prefix = if args.date.len() >= 7 { &args.date[..7] } else { &args.date };
    let recent_packs: Vec<_> = model
        .packs
        .iter()
        .filter(|p| {
            p.date.as_deref().is_some_and(|d| {
                within_lookback(d, &args.date, args.lookback_days)
            })
        })
        .collect();

    if recent_packs.is_empty() {
        out.push_str(&format!("- No packs in the last {} days.\n\n", args.lookback_days));
    } else {
        for p in recent_packs.iter().take(20) {
            let date_str = p.date.as_deref().unwrap_or("?");
            out.push_str(&format!(
                "- [{}] **{}** ({} cards)\n",
                date_str, p.title, p.cards
            ));
            for card in p.card_titles.iter().take(5) {
                out.push_str(&format!("  - {card}\n"));
            }
        }
        out.push('\n');
    }

    out.push_str("## Active Crystal Claims\n\n");
    let durable_claims: Vec<_> = model
        .claims
        .iter()
        .filter(|c| c.status == ovp_index::model::ClaimStatus::Durable)
        .collect();

    if durable_claims.is_empty() {
        out.push_str("- No durable claims yet.\n\n");
    } else {
        for c in durable_claims.iter().take(30) {
            let theme = c.theme.as_deref().unwrap_or("general");
            out.push_str(&format!("- [{}] {} ({} sources)\n", theme, c.claim, c.sources.len()));
        }
        out.push('\n');
    }

    out.push_str("## Attention\n\n");
    let blocked: Vec<_> = model
        .sources
        .iter()
        .filter(|s| s.status == ovp_index::model::SourceStatus::Blocked)
        .collect();
    if blocked.is_empty() {
        out.push_str("- No blocked sources.\n");
    } else {
        for s in blocked.iter().take(10) {
            let title = s.title.as_deref().unwrap_or("(untitled)");
            let reason = s.last_reason.as_deref().unwrap_or("unknown");
            out.push_str(&format!("- BLOCKED: {title} — {reason}\n"));
        }
    }

    // Truncate to approximate token budget (1 token ≈ 4 chars)
    let char_budget = args.max_tokens * 4;
    if out.len() > char_budget {
        out.truncate(char_budget);
        out.push_str("\n\n…(truncated to token budget)\n");
    }

    let _ = date_prefix;
    out
}

pub fn working_memory_path(vault_root: &Path) -> PathBuf {
    vault_root.join(".ovp").join("working-memory.md")
}

pub fn write_working_memory(vault_root: &Path, content: &str) -> Result<PathBuf, String> {
    let path = working_memory_path(vault_root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("create .ovp dir: {e}"))?;
    }
    std::fs::write(&path, content).map_err(|e| format!("write working-memory: {e}"))?;
    Ok(path)
}

fn within_lookback(pack_date: &str, current_date: &str, days: usize) -> bool {
    if pack_date.len() < 10 || current_date.len() < 10 {
        return pack_date.starts_with(&current_date[..current_date.len().min(7)]);
    }
    let Some(cur) = parse_date(current_date) else { return false };
    let Some(pd) = parse_date(pack_date) else { return false };
    let diff = cur.saturating_sub(pd);
    diff <= days as u32
}

fn parse_date(s: &str) -> Option<u32> {
    if s.len() < 10 { return None; }
    let y: u32 = s[..4].parse().ok()?;
    let m: u32 = s[5..7].parse().ok()?;
    let d: u32 = s[8..10].parse().ok()?;
    Some(y * 10000 + m * 100 + d)
}
