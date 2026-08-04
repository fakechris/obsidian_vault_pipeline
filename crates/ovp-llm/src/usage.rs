//! LLM usage metering ledger (`runtime.llm_usage_metering`, candidate
//! `llm_usage_metering-v1`).
//!
//! One real provider HTTP call = one append-only JSONL row in a per-vault
//! monthly shard `<ledger_dir>/llm-usage-YYYY-MM.jsonl`. Metering is a pure
//! SIDE CHANNEL: callers use [`record_usage_side_channel`], which logs a
//! write failure to stderr ONCE per process and never propagates it — the
//! LLM path behaves byte-identically with metering on or off.
//!
//! No chrono dep: the month/day math below mirrors `ovp2`'s `today_iso`.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use serde::{Deserialize, Serialize};

use crate::client::CallError;

/// One metered HTTP call. `input_tokens`/`output_tokens` are `None` when the
/// call produced no response body (transport failure) — never fabricated
/// zeros. `err_kind` is set iff `ok == false`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UsageRow {
    /// Unix epoch seconds at call end.
    pub ts: u64,
    /// Routing lane derived from the request's `cache_namespace`
    /// ([`lane_for_namespace`]).
    pub lane: String,
    /// The request's `cache_namespace` verbatim (e.g. `source_work/v2`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ns: Option<String>,
    pub model: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u32>,
    /// Wall-clock milliseconds of the HTTP exchange.
    pub ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub err_kind: Option<String>,
}

/// Map a request `cache_namespace` onto its reporting lane: the first path
/// segment, with `source_work/*` normalized to `source-work` (`bilingual/*`
/// is already lane-shaped). Absent namespace → `unknown`.
pub fn lane_for_namespace(ns: Option<&str>) -> String {
    let Some(ns) = ns else {
        return "unknown".into();
    };
    match ns.split('/').next().unwrap_or(ns) {
        "source_work" => "source-work".into(),
        first => first.to_string(),
    }
}

/// Stable snake_case label for a failed call's [`CallError`] variant.
pub fn err_kind_of(err: &CallError) -> &'static str {
    match err {
        CallError::CacheMiss { .. } => "cache_miss",
        CallError::Provider { .. } => "provider",
        CallError::Transport { .. } => "transport",
        CallError::Decode { .. } => "decode",
        CallError::Protocol { .. } => "protocol",
        CallError::BudgetExhausted { .. } => "budget_exhausted",
        CallError::Unexpected { .. } => "unexpected",
    }
}

/// Extract `/usage/input_tokens` + `/usage/output_tokens` from a raw
/// Anthropic response body. Works for Ok AND Err-with-a-body replies; a
/// missing body / missing usage yields `None` (never 0).
pub fn body_usage_tokens(json_body: &str) -> (Option<u32>, Option<u32>) {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(json_body) else {
        return (None, None);
    };
    let pick = |key: &str| {
        v.pointer(&format!("/usage/{key}"))
            .and_then(|n| n.as_u64())
            .map(|n| n as u32)
    };
    (pick("input_tokens"), pick("output_tokens"))
}

/// Shard file for a timestamp: `<ledger_dir>/llm-usage-YYYY-MM.jsonl`.
pub fn shard_path(ledger_dir: &Path, ts: u64) -> PathBuf {
    let (y, m, _) = ymd_from_unix(ts);
    ledger_dir.join(format!("llm-usage-{y:04}-{m:02}.jsonl"))
}

/// Append `row` to its monthly shard. On the FIRST write of a new month
/// (shard does not exist yet) shards older than 90 days are pruned —
/// write-side rotation, so the read-only report path never deletes and
/// cannot conflict with `--days N`. Pruning is best-effort: a prune failure
/// must not lose the row being written.
pub fn record_usage(ledger_dir: &Path, row: &UsageRow) -> std::io::Result<()> {
    std::fs::create_dir_all(ledger_dir)?;
    let shard = shard_path(ledger_dir, row.ts);
    if !shard.exists() {
        prune_old_shards(ledger_dir, row.ts);
    }
    let mut line = serde_json::to_string(row)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    line.push('\n');
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&shard)?;
    f.write_all(line.as_bytes())
}

static LEDGER_WARNED: AtomicBool = AtomicBool::new(false);

/// The metering entry point used inside `call()`: write the row, and on ANY
/// failure (disk full, unwritable dir, shard creation) log to stderr ONCE
/// per process and swallow — metering must never change a call's result.
pub fn record_usage_side_channel(ledger_dir: &Path, row: &UsageRow) {
    if let Err(e) = record_usage(ledger_dir, row) {
        // Log ONCE per process; later failures stay silent (a metering
        // problem must never spam the operator log of a multi-hour run).
        if !LEDGER_WARNED.swap(true, Ordering::Relaxed) {
            eprintln!("ovp-llm: usage ledger write failed (further failures stay silent): {e}");
        }
    }
}

/// Delete shards whose month is older than 90 days before `now_ts`
/// (month granularity: the shard covering part of the window is kept).
/// Best-effort — individual failures are ignored.
fn prune_old_shards(ledger_dir: &Path, now_ts: u64) {
    let cutoff = ymd_from_unix(now_ts.saturating_sub(90 * 86_400));
    let Ok(entries) = std::fs::read_dir(ledger_dir) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        let Some((y, m)) = parse_shard_name(name) else { continue };
        if (y, m) < (cutoff.0, cutoff.1) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Parse `llm-usage-YYYY-MM.jsonl` → `(year, month)`.
fn parse_shard_name(name: &str) -> Option<(i32, u32)> {
    let stem = name.strip_prefix("llm-usage-")?.strip_suffix(".jsonl")?;
    let (y, m) = stem.split_once('-')?;
    if y.len() != 4 || m.len() != 2 {
        return None;
    }
    Some((y.parse().ok()?, m.parse().ok()?))
}

/// Unix epoch seconds → (year, month, day-of-month), UTC. Same tiny
/// month-length-table approach as `ovp2`'s `today_iso` (no chrono dep here).
fn ymd_from_unix(ts: u64) -> (i32, u32, u32) {
    let mut days = (ts / 86_400) as i64;
    let mut year: i32 = 1970;
    loop {
        let dy = if is_leap(year) { 366 } else { 365 };
        if days < dy {
            break;
        }
        days -= dy;
        year += 1;
    }
    let months: [i64; 12] = [
        31,
        if is_leap(year) { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut month: u32 = 1;
    for m in months.iter() {
        if days < *m {
            return (year, month, (days + 1) as u32);
        }
        days -= *m;
        month += 1;
    }
    (year, 12, 31)
}

fn is_leap(y: i32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(ts: u64, ok: bool) -> UsageRow {
        UsageRow {
            ts,
            lane: "source-work".into(),
            ns: Some("source_work/v2".into()),
            model: "claude-sonnet-4-6".into(),
            ok,
            input_tokens: Some(100),
            output_tokens: Some(50),
            ms: 1234,
            err_kind: if ok { None } else { Some("provider".into()) },
        }
    }

    fn read_rows(shard: &Path) -> Vec<UsageRow> {
        std::fs::read_to_string(shard)
            .unwrap()
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    // 2026-08-15T00:00:00Z = 1786752000; 2026-07-15T12:00:00Z = 1784116800.
    const TS_AUG: u64 = 1_786_752_000;
    const TS_JUL: u64 = 1_784_116_800;

    #[test]
    fn ok_row_lands_in_the_monthly_shard() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        record_usage(&dir, &row(TS_AUG, true)).unwrap();
        let rows = read_rows(&dir.join("llm-usage-2026-08.jsonl"));
        assert_eq!(rows.len(), 1);
        assert!(rows[0].ok);
        assert_eq!(rows[0].input_tokens, Some(100));
        assert_eq!(rows[0].output_tokens, Some(50));
        assert_eq!(rows[0].err_kind, None);
    }

    #[test]
    fn body_failure_row_carries_real_tokens_and_err_kind() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        record_usage(&dir, &row(TS_AUG, false)).unwrap();
        let rows = read_rows(&dir.join("llm-usage-2026-08.jsonl"));
        assert_eq!(rows.len(), 1);
        assert!(!rows[0].ok);
        assert_eq!(rows[0].input_tokens, Some(100), "a failed call with a body keeps real tokens");
        assert_eq!(rows[0].err_kind.as_deref(), Some("provider"));
    }

    #[test]
    fn transport_row_has_null_tokens_never_zeros() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        let mut r = row(TS_AUG, false);
        r.input_tokens = None;
        r.output_tokens = None;
        r.err_kind = Some("transport".into());
        record_usage(&dir, &r).unwrap();
        let raw = std::fs::read_to_string(dir.join("llm-usage-2026-08.jsonl")).unwrap();
        assert!(!raw.contains("input_tokens"), "None fields are omitted, not 0: {raw}");
        let rows = read_rows(&dir.join("llm-usage-2026-08.jsonl"));
        assert_eq!(rows[0].input_tokens, None);
        assert_eq!(rows[0].output_tokens, None);
    }

    #[test]
    fn unwritable_ledger_path_never_errors_the_side_channel() {
        // A FILE where the ledger dir must be created: create_dir_all fails.
        let tmp = tempfile::tempdir().unwrap();
        let blocker = tmp.path().join("blocker");
        std::fs::write(&blocker, "x").unwrap();
        let dir = blocker.join("usage");
        assert!(record_usage(&dir, &row(TS_AUG, true)).is_err());
        // The side channel swallows it (and only logs once) — never panics,
        // never returns anything to propagate.
        record_usage_side_channel(&dir, &row(TS_AUG, true));
        record_usage_side_channel(&dir, &row(TS_AUG, true));
    }

    #[test]
    fn appends_across_months_into_separate_shards() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        record_usage(&dir, &row(TS_JUL, true)).unwrap();
        record_usage(&dir, &row(TS_AUG, true)).unwrap();
        record_usage(&dir, &row(TS_AUG, true)).unwrap();
        assert_eq!(read_rows(&dir.join("llm-usage-2026-07.jsonl")).len(), 1);
        assert_eq!(read_rows(&dir.join("llm-usage-2026-08.jsonl")).len(), 2);
    }

    #[test]
    fn first_write_of_a_new_month_prunes_shards_older_than_90_days() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        std::fs::create_dir_all(&dir).unwrap();
        // 2026-08-04 minus 90d = 2026-05-06 → 2026-04 and older must go,
        // 2026-05 (partially inside the window) and newer stay.
        for stale in ["llm-usage-2026-03.jsonl", "llm-usage-2026-04.jsonl"] {
            std::fs::write(dir.join(stale), "{}\n").unwrap();
        }
        for keep in ["llm-usage-2026-05.jsonl", "llm-usage-2026-07.jsonl"] {
            std::fs::write(dir.join(keep), "{}\n").unwrap();
        }
        std::fs::write(dir.join("not-a-shard.jsonl"), "{}\n").unwrap();
        record_usage(&dir, &row(TS_AUG, true)).unwrap();
        assert!(!dir.join("llm-usage-2026-03.jsonl").exists());
        assert!(!dir.join("llm-usage-2026-04.jsonl").exists());
        assert!(dir.join("llm-usage-2026-05.jsonl").exists());
        assert!(dir.join("llm-usage-2026-07.jsonl").exists());
        assert!(dir.join("not-a-shard.jsonl").exists(), "foreign files are never touched");
    }

    #[test]
    fn no_prune_when_appending_to_an_existing_shard() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("usage");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("llm-usage-2026-01.jsonl"), "{}\n").unwrap();
        record_usage(&dir, &row(TS_JUL, true)).unwrap();
        // Second row in the SAME month: the old shard stays (rotation fires
        // only on the first write of a new month).
        std::fs::write(dir.join("llm-usage-2026-02.jsonl"), "{}\n").unwrap();
        record_usage(&dir, &row(TS_JUL, true)).unwrap();
        assert!(!dir.join("llm-usage-2026-01.jsonl").exists());
        assert!(dir.join("llm-usage-2026-02.jsonl").exists());
    }

    #[test]
    fn lane_mapping() {
        assert_eq!(lane_for_namespace(Some("bilingual/v1")), "bilingual");
        assert_eq!(lane_for_namespace(Some("source_work/v2")), "source-work");
        assert_eq!(lane_for_namespace(Some("crystal_synth/v1")), "crystal_synth");
        assert_eq!(lane_for_namespace(Some("ask/v4")), "ask");
        assert_eq!(lane_for_namespace(None), "unknown");
    }

    #[test]
    fn body_usage_tokens_reads_ok_and_error_bodies() {
        let ok = r#"{"model":"m","content":[{"type":"text","text":"x"}],"usage":{"input_tokens":12,"output_tokens":34}}"#;
        assert_eq!(body_usage_tokens(ok), (Some(12), Some(34)));
        let err = r#"{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}"#;
        assert_eq!(body_usage_tokens(err), (None, None));
        assert_eq!(body_usage_tokens("<html>502</html>"), (None, None));
    }
}
