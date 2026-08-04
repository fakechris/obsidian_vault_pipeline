//! `usage` — read-only LLM usage report over the metering ledger
//! (`runtime.llm_usage_metering`, candidate `llm_usage_metering-v1`).
//!
//! Reads the monthly shards `<vault>/.ovp/usage/llm-usage-YYYY-MM.jsonl`
//! written by `AnthropicBlockingClient::with_usage_ledger` and prints a
//! day × lane rollup, a soft daily-budget line (from `.ovp/providers.toml`
//! `[budget]`), and a source-work queue drain estimate. Never writes or
//! deletes anything — shard rotation happens write-side.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ovp_llm::UsageRow;

use crate::CliError;

pub struct UsageArgs {
    pub vault_root: PathBuf,
    pub days: usize,
}

/// Metering ledger dir relative to the vault root (wired call sites pass
/// `<vault>/USAGE_LEDGER_REL` to `with_usage_ledger`).
pub(crate) const USAGE_LEDGER_REL: &str = ".ovp/usage";

/// Cold-start per-call token upper bounds (the prompts' max_tokens
/// ceilings) used when the ledger has no history for a lane yet. These
/// overestimate typical spend 3–5x and are always labeled as such.
pub(crate) fn cold_start_upper_bound(kind: &str) -> Option<u64> {
    Some(match kind {
        "translate" => 8192,
        "summarize" => 4096,
        "card" => 2048,
        "claim" => 1024,
        _ => return None,
    })
}

/// Load every ledger row from the monthly shards (sorted by filename =
/// chronological). Returns (rows, malformed_lines_skipped, io_warnings).
/// Malformed lines are skipped and counted; a MISSING ledger dir is just an
/// empty ledger — but a ledger/shard that EXISTS and can't be read is an
/// io_warning (permission, transient IO): the caller must surface it,
/// because silently treating it as "no usage" would print a zero budget
/// total while spend is unknown (codex adversarial).
pub(crate) fn load_usage_rows(ledger_dir: &Path) -> (Vec<UsageRow>, usize, Vec<String>) {
    let mut paths: Vec<PathBuf> = Vec::new();
    let mut io_warnings = Vec::new();
    match std::fs::read_dir(ledger_dir) {
        Ok(entries) => {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let Some(name) = name.to_str() else { continue };
                if name.starts_with("llm-usage-") && name.ends_with(".jsonl") {
                    paths.push(entry.path());
                }
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => io_warnings.push(format!(
            "usage ledger {} unreadable ({e}) — report incomplete",
            ledger_dir.display()
        )),
    }
    paths.sort();
    let mut rows = Vec::new();
    let mut skipped = 0;
    for path in paths {
        match std::fs::read_to_string(&path) {
            Ok(raw) => {
                for line in raw.lines() {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<UsageRow>(line) {
                        Ok(row) => rows.push(row),
                        Err(_) => skipped += 1,
                    }
                }
            }
            Err(e) => io_warnings.push(format!(
                "usage shard {} unreadable ({e}) — report incomplete",
                path.display()
            )),
        }
    }
    (rows, skipped, io_warnings)
}

/// Local civil day (YYYY-MM-DD) for a row timestamp — matches the operator's
/// "today" in `ovp2 daily` / digests.
pub(crate) fn day_key(ts: u64) -> String {
    chrono::DateTime::from_timestamp(ts as i64, 0)
        .map(|dt| dt.with_timezone(&chrono::Local).format("%Y-%m-%d").to_string())
        .unwrap_or_else(|| "unknown".into())
}

/// Per-(day, lane) rollup cell.
#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct LaneDayStats {
    pub calls: usize,
    pub failures: usize,
    /// input+output over rows that carry token counts.
    pub tokens: u64,
    /// Rows that carried token counts (the avg/call denominator — transport
    /// failures have null tokens and must not drag the average down).
    pub token_rows: usize,
}

/// Group rows at/after `cutoff_day` (YYYY-MM-DD, string comparison) into
/// day × lane cells. Cross-month shards land in the same map.
pub(crate) fn aggregate_by_day_lane(
    rows: &[UsageRow],
    cutoff_day: &str,
) -> BTreeMap<(String, String), LaneDayStats> {
    let mut out: BTreeMap<(String, String), LaneDayStats> = BTreeMap::new();
    for r in rows {
        let day = day_key(r.ts);
        if day.as_str() < cutoff_day {
            continue;
        }
        let st = out.entry((day, r.lane.clone())).or_default();
        st.calls += 1;
        if !r.ok {
            st.failures += 1;
        }
        if let (Some(i), Some(o)) = (r.input_tokens, r.output_tokens) {
            st.tokens += u64::from(i) + u64::from(o);
            st.token_rows += 1;
        }
    }
    out
}

/// Average total tokens per call for one lane across ALL loaded history
/// (the estimate basis). `None` when the lane has no token-carrying rows.
pub(crate) fn lane_avg_tokens(rows: &[UsageRow], lane: &str) -> Option<u64> {
    let mut sum = 0u64;
    let mut n = 0u64;
    for r in rows.iter().filter(|r| r.lane == lane) {
        if let (Some(i), Some(o)) = (r.input_tokens, r.output_tokens) {
            sum += u64::from(i) + u64::from(o);
            n += 1;
        }
    }
    if n == 0 {
        None
    } else {
        Some(sum.div_ceil(n))
    }
}

/// (percent, verdict) for the soft budget line. Thresholds per the approved
/// candidate: warn at >=80%, over at >=100%. An explicit zero budget means
/// "spend nothing": any recorded usage is OVER (codex adversarial — 0% / ok
/// would silence exactly the alarm a zero budget exists to raise).
pub(crate) fn budget_verdict(tokens_today: u64, budget: usize) -> (u64, &'static str) {
    if budget == 0 {
        return if tokens_today > 0 {
            (100, "OVER BUDGET")
        } else {
            (0, "ok")
        };
    }
    let pct = tokens_today * 100 / budget as u64;
    let verdict = if pct >= 100 {
        "OVER BUDGET"
    } else if pct >= 80 {
        "WARN"
    } else {
        "ok"
    };
    (pct, verdict)
}

/// Estimate for source-work tasks (backfill dry-run / queue drain).
pub(crate) struct TokenEstimate {
    pub calls: usize,
    pub tokens: u64,
    /// `true` = no ledger history, so the numbers are the labeled cold-start
    /// upper bounds, not measured averages.
    pub cold_start: bool,
}

/// Estimate `translate` + `summarize` source-work tasks: the ledger's
/// source-work lane average when history exists, else the cold-start
/// upper-bound constants.
pub(crate) fn estimate_source_work(
    vault_root: &Path,
    translate: usize,
    summarize: usize,
) -> TokenEstimate {
    let (rows, _, _) = load_usage_rows(&vault_root.join(USAGE_LEDGER_REL));
    let calls = translate + summarize;
    match lane_avg_tokens(&rows, "source-work") {
        Some(avg) => TokenEstimate {
            calls,
            tokens: avg * calls as u64,
            cold_start: false,
        },
        None => TokenEstimate {
            calls,
            tokens: translate as u64 * cold_start_upper_bound("translate").unwrap_or(0)
                + summarize as u64 * cold_start_upper_bound("summarize").unwrap_or(0),
            cold_start: true,
        },
    }
}

/// Count `wanted && queued` source-work tasks per kind from
/// `.ovp/source-work-queue.json` (missing/corrupt file → zeros).
fn queued_source_work_counts(vault_root: &Path) -> (usize, usize) {
    use ovp_memory::source_work_queue::{QueueFile, TaskStatus};
    let path = vault_root.join(".ovp/source-work-queue.json");
    let Ok(raw) = std::fs::read_to_string(&path) else {
        return (0, 0);
    };
    let Ok(file) = serde_json::from_str::<QueueFile>(&raw) else {
        return (0, 0);
    };
    let mut translate = 0;
    let mut summarize = 0;
    for item in &file.items {
        if item.translate.wanted && item.translate.status == TaskStatus::Queued {
            translate += 1;
        }
        if item.summarize.wanted && item.summarize.status == TaskStatus::Queued {
            summarize += 1;
        }
    }
    (translate, summarize)
}

pub fn run(args: UsageArgs) -> Result<(), CliError> {
    let ledger_dir = args.vault_root.join(USAGE_LEDGER_REL);
    let (rows, skipped, io_warnings) = load_usage_rows(&ledger_dir);
    let now = chrono::Local::now();
    let today = now.format("%Y-%m-%d").to_string();
    let cutoff = (now - chrono::Duration::days(args.days.saturating_sub(1) as i64))
        .format("%Y-%m-%d")
        .to_string();

    sayln!("usage ledger: {}", ledger_dir.display());
    for w in &io_warnings {
        sayln!("  warn: {w}");
    }
    if skipped > 0 {
        sayln!("  ({skipped} malformed ledger line(s) skipped)");
    }
    let agg = aggregate_by_day_lane(&rows, &cutoff);
    if agg.is_empty() {
        sayln!("  no metered calls in the last {} day(s)", args.days);
    } else {
        sayln!("day         lane              calls  fails    tokens  avg/call");
        for ((day, lane), st) in &agg {
            let avg = if st.token_rows > 0 {
                (st.tokens / st.token_rows as u64).to_string()
            } else {
                "-".into()
            };
            sayln!(
                "{day}  {lane:<16}  {:>5}  {:>5}  {:>8}  {avg:>8}",
                st.calls, st.failures, st.tokens
            );
        }
    }

    // Soft daily budget (visibility only — nothing is ever blocked on it).
    // Rows without token counts (transport failures — the provider may still
    // have generated and charged a response) make the true total UNKNOWN;
    // the printed figure is a lower bound and says so (codex adversarial).
    let tokens_today: u64 = rows
        .iter()
        .filter(|r| day_key(r.ts) == today)
        .filter_map(|r| Some(u64::from(r.input_tokens?) + u64::from(r.output_tokens?)))
        .sum();
    let unknown_today = rows
        .iter()
        .filter(|r| day_key(r.ts) == today)
        .filter(|r| r.input_tokens.is_none() || r.output_tokens.is_none())
        .count();
    let budget = ovp_domain::providers::read_budget(&args.vault_root).map_err(CliError::Io)?;
    match budget.daily_token_budget {
        Some(b) => {
            let (pct, verdict) = budget_verdict(tokens_today, b);
            sayln!("budget: today: ≥{tokens_today} / {b} ({pct}%) [{verdict}]");
            if unknown_today > 0 {
                sayln!(
                    "  (+ {unknown_today} call(s) with unknown usage — total is a LOWER BOUND)"
                );
            }
        }
        None => {
            sayln!(
                "budget: not set (add `[budget] daily_token_budget = <n>` to .ovp/providers.toml)"
            );
        }
    }

    // Source-work queue drain estimate alongside today's spend.
    let (qt, qs) = queued_source_work_counts(&args.vault_root);
    if qt + qs > 0 {
        let est = estimate_source_work(&args.vault_root, qt, qs);
        let basis = if est.cold_start {
            "cold-start output-token ceilings — excludes input tokens & retries, typically 3-5x over"
        } else {
            "source-work lane average"
        };
        sayln!(
            "queue drain: {} queued task(s) (translate={qt} summarize={qs}) ≈ {} call(s), ~{} tokens ({basis})",
            qt + qs,
            est.calls,
            est.tokens
        );
    } else {
        sayln!("queue drain: source-work queue empty");
    }

    sayln!(
        "note: metering covers live calls from wired entry points only (daily, source-work \
         claims-zh/memory-zh, crystal-synth, crystal-theme-pages, tags-bootstrap, ask, and the \
         server ask/worker). Unwired commands (copy-probe, extract-units, read-source, \
         run-cycle, auto-run, compare-run, review-run, interpret-article, crystal-themes, \
         crystal-review-session, digest) are NOT metered."
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(ts: u64, lane: &str, ok: bool, tokens: Option<(u32, u32)>) -> UsageRow {
        UsageRow {
            ts,
            lane: lane.into(),
            ns: Some(format!("{lane}/v1")),
            model: "m".into(),
            ok,
            input_tokens: tokens.map(|t| t.0),
            output_tokens: tokens.map(|t| t.1),
            ms: 100,
            err_kind: if ok { None } else { Some("provider".into()) },
        }
    }

    fn write_shard(dir: &Path, name: &str, rows: &[UsageRow]) {
        std::fs::create_dir_all(dir).unwrap();
        let body: String = rows
            .iter()
            .map(|r| serde_json::to_string(r).unwrap() + "\n")
            .collect();
        std::fs::write(dir.join(name), body).unwrap();
    }

    #[test]
    fn aggregates_day_by_lane_across_month_shards() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        // Two timestamps exactly 86400s apart are ALWAYS on different local
        // days regardless of tz; rows within +1h of each stay together.
        let ts1 = 1_786_752_000;
        let ts2 = ts1 + 86_400;
        write_shard(
            &dir,
            "llm-usage-2026-07.jsonl",
            &[row(ts1, "source-work", true, Some((100, 50)))],
        );
        write_shard(
            &dir,
            "llm-usage-2026-08.jsonl",
            &[
                row(ts2, "source-work", false, Some((200, 60))),
                row(ts2 + 60, "source-work", false, None),
                row(ts2 + 120, "bilingual", true, Some((10, 5))),
            ],
        );
        let (rows, skipped, io_warnings) = load_usage_rows(&dir);
        assert_eq!(skipped, 0);
        assert!(io_warnings.is_empty());
        assert_eq!(rows.len(), 4, "rows across month shards all load");

        let cutoff = day_key(ts1); // include both rows' day
        let agg = aggregate_by_day_lane(&rows, &cutoff);
        let day1 = day_key(ts1);
        let day2 = day_key(ts2);
        let sw1 = &agg[&(day1.clone(), "source-work".to_string())];
        assert_eq!(sw1, &LaneDayStats { calls: 1, failures: 0, tokens: 150, token_rows: 1 });
        let sw2 = &agg[&(day2.clone(), "source-work".to_string())];
        assert_eq!(sw2.calls, 2);
        assert_eq!(sw2.failures, 2);
        assert_eq!(sw2.tokens, 260, "failure-with-body tokens count");
        assert_eq!(sw2.token_rows, 1, "transport row excluded from the avg basis");
        assert_eq!(agg[&(day2, "bilingual".to_string())].tokens, 15);

        // A cutoff AFTER the first day drops only that day's rows.
        let agg = aggregate_by_day_lane(&rows, &day_key(ts1 + 86_400));
        assert!(!agg.contains_key(&(day1, "source-work".to_string())));
    }

    #[test]
    fn malformed_lines_are_skipped_not_fatal() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        std::fs::create_dir_all(&dir).unwrap();
        let good = serde_json::to_string(&row(1_786_752_000, "ask", true, Some((1, 1)))).unwrap();
        std::fs::write(dir.join("llm-usage-2026-08.jsonl"), format!("{good}\n{{garbage\n")).unwrap();
        let (rows, skipped, io_warnings) = load_usage_rows(&dir);
        assert_eq!(rows.len(), 1);
        assert_eq!(skipped, 1);
        assert!(io_warnings.is_empty());
    }

    /// A ledger dir that exists but is unreadable must NOT look like an
    /// empty ledger (codex: IO failure masquerading as zero usage).
    #[test]
    fn unreadable_ledger_is_a_warning_not_empty() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        std::fs::create_dir_all(&dir).unwrap();
        let shard = dir.join("llm-usage-2026-08.jsonl");
        std::fs::write(&shard, "{}\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&shard, std::fs::Permissions::from_mode(0o000)).unwrap();
            let (_, _, io_warnings) = load_usage_rows(&dir);
            assert_eq!(io_warnings.len(), 1, "{io_warnings:?}");
            assert!(io_warnings[0].contains("unreadable"));
            std::fs::set_permissions(&shard, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
    }

    #[test]
    fn budget_thresholds_warn_at_80_and_over_at_100() {
        assert_eq!(budget_verdict(799, 1000), (79, "ok"));
        assert_eq!(budget_verdict(800, 1000), (80, "WARN"));
        assert_eq!(budget_verdict(999, 1000), (99, "WARN"));
        assert_eq!(budget_verdict(1000, 1000), (100, "OVER BUDGET"));
        assert_eq!(budget_verdict(0, 0), (0, "ok"));
        // An explicit zero budget means "spend nothing" — any usage is over.
        assert_eq!(budget_verdict(1, 0), (100, "OVER BUDGET"));
    }

    #[test]
    fn cold_start_estimate_uses_labeled_upper_bounds() {
        let tmp = tempfile::tempdir().unwrap();
        let est = estimate_source_work(tmp.path(), 3, 2);
        assert!(est.cold_start);
        assert_eq!(est.calls, 5);
        assert_eq!(est.tokens, 3 * 8192 + 2 * 4096);
        assert_eq!(cold_start_upper_bound("card"), Some(2048));
        assert_eq!(cold_start_upper_bound("claim"), Some(1024));
    }

    #[test]
    fn history_estimate_uses_lane_average() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join(USAGE_LEDGER_REL);
        write_shard(
            &dir,
            "llm-usage-2026-08.jsonl",
            &[
                row(1_786_752_000, "source-work", true, Some((300, 100))),
                row(1_786_752_000, "source-work", false, Some((500, 100))),
                row(1_786_752_000, "bilingual", true, Some((9_999, 1))),
            ],
        );
        let est = estimate_source_work(tmp.path(), 2, 1);
        assert!(!est.cold_start);
        // lane avg = (400 + 600) / 2 = 500, bilingual rows excluded.
        assert_eq!(est.tokens, 500 * 3);
    }
}
