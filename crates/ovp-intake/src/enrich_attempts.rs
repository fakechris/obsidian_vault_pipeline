//! The enrich-attempt ledger (`.ovp/enrich-attempts.jsonl`) and the terminal
//! `content_unavailable` decision.
//!
//! The intake ledger is one record per content hash, so it cannot count how
//! many times enrichment already tried a needs-content capture. This ledger
//! is append-only, one line per attempt; [`fold_attempts`] reduces it to the
//! latest state per hash, and [`enrich_verdict`] turns that state into
//! "still pending" or "close it" with no I/O, so the rule is unit-testable.
//!
//! Before this existed every unreachable URL was fetched on every run,
//! forever — the only exit was the operator noticing and tagging `ovp/skip`.

use std::collections::HashMap;
use std::path::Path;

use ovp_domain::VaultLayout;
use serde::{Deserialize, Serialize};

use crate::ledger::{INTAKE_SCHEMA, IntakeAction, IntakeRecord, append_intake_record};
use crate::vaultops::{append_jsonl, hex_sha256, read_jsonl};

/// Schema tag stamped on every attempt record.
pub const ENRICH_ATTEMPT_SCHEMA: &str = "ovp.enrich-attempt/v1";

/// Failed enrichment attempts after which a needs-content capture is closed
/// as `content_unavailable`. Same budget as the reader's
/// `MAX_FAILURES_BEFORE_BLOCKED`: three independent runs is enough to tell
/// "the page is gone" from "the network hiccuped".
pub const MAX_ENRICH_ATTEMPTS: usize = 3;

/// Wall-clock budget after the FIRST failed attempt (72h). A capture that has
/// been failing for three days is closed even if fewer than
/// [`MAX_ENRICH_ATTEMPTS`] runs happened (a paused scheduler must not keep a
/// dead URL pending for weeks).
pub const MAX_ENRICH_PENDING_SECS: u64 = 72 * 60 * 60;

/// One enrichment attempt on one capture file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EnrichAttemptRecord {
    pub schema: String,
    pub run_id: String,
    /// sha256 of the capture file bytes at attempt time (the intake identity).
    pub sha256: String,
    /// Vault-relative path of the capture file.
    pub from: String,
    pub url: String,
    /// Unix seconds.
    pub attempted_at: u64,
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Folded attempt state for one capture hash.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnrichAttemptState {
    pub sha256: String,
    pub from: String,
    pub url: String,
    /// Number of FAILED attempts.
    pub failures: usize,
    /// Unix seconds of the first failed attempt.
    pub first_failure: u64,
    /// Unix seconds of the latest attempt (failed or not).
    pub last_attempt: u64,
    pub last_error: Option<String>,
}

/// What to do with a capture after its latest failed attempt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EnrichVerdict {
    /// Keep re-offering it to enrichment on later runs.
    Pending,
    /// Close it: append a `content_unavailable` intake record with this reason.
    Unavailable(String),
}

pub fn read_enrich_attempts(path: &Path) -> Result<Vec<EnrichAttemptRecord>, String> {
    read_jsonl(path)
}

pub fn append_enrich_attempt(path: &Path, rec: &EnrichAttemptRecord) -> Result<(), String> {
    append_jsonl(path, rec)
}

/// Reduce the append-only attempt log to the latest state per hash. A
/// successful attempt resets the failure streak (the file changes hash on
/// success anyway, so this mostly matters for a same-hash retry that worked).
pub fn fold_attempts(records: &[EnrichAttemptRecord]) -> HashMap<String, EnrichAttemptState> {
    let mut out: HashMap<String, EnrichAttemptState> = HashMap::new();
    for r in records {
        let st = out
            .entry(r.sha256.clone())
            .or_insert_with(|| EnrichAttemptState {
                sha256: r.sha256.clone(),
                from: r.from.clone(),
                url: r.url.clone(),
                failures: 0,
                first_failure: 0,
                last_attempt: 0,
                last_error: None,
            });
        st.from = r.from.clone();
        st.url = r.url.clone();
        st.last_attempt = st.last_attempt.max(r.attempted_at);
        if r.ok {
            st.failures = 0;
            st.first_failure = 0;
            st.last_error = None;
        } else {
            if st.failures == 0 {
                st.first_failure = r.attempted_at;
            }
            st.failures += 1;
            st.last_error = r.error.clone();
        }
    }
    out
}

/// The terminal decision, pure: `now` is unix seconds. Closes on either the
/// attempt budget or the wall-clock budget, whichever trips first.
///
/// KNOWN GAP: a fetch that keeps succeeding with content still under the
/// reader's size gate resets the streak every run, so the capture stays
/// `NeedsContent` forever. That is a different loop from the one this closes
/// (unreachable content) and narrowing it means deciding whether "enriched but
/// still too thin" is the pipeline's failure or the page's — left alone rather
/// than guessed at, because closing it wrongly strands a capture the operator
/// could still rescue by hand.
pub fn enrich_verdict(state: &EnrichAttemptState, now: u64) -> EnrichVerdict {
    if state.failures == 0 {
        return EnrichVerdict::Pending;
    }
    let elapsed = now.saturating_sub(state.first_failure);
    let why = if state.failures >= MAX_ENRICH_ATTEMPTS {
        Some(format!("{} failed enrich attempts", state.failures))
    } else if elapsed >= MAX_ENRICH_PENDING_SECS {
        Some(format!(
            "{} failed enrich attempt(s) over {}h",
            state.failures,
            elapsed / 3600
        ))
    } else {
        None
    };
    match why {
        Some(w) => {
            let err = state.last_error.as_deref().unwrap_or("fetch failed");
            EnrichVerdict::Unavailable(format!("{w}; last error: {err}"))
        }
        None => EnrichVerdict::Pending,
    }
}

/// One enrichment outcome to record, as the daily loop sees it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnrichOutcome {
    /// Vault-relative capture path.
    pub from: String,
    pub url: String,
    pub ok: bool,
    pub error: Option<String>,
}

/// Newly closed captures from one [`record_enrich_outcomes`] call.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct ClosedCaptures {
    pub unavailable: Vec<IntakeRecord>,
}

/// Record this run's enrichment outcomes and close the ones that exhausted
/// their budget. Reads the file at `from` to key the attempt by the SAME
/// sha256 the intake ledger uses (a failed fetch leaves the bytes untouched,
/// so this is the hash the sweep flagged). A file that vanished since the
/// sweep is skipped: nothing to close.
///
/// Returns the `content_unavailable` records appended this call.
pub fn record_enrich_outcomes(
    vault_root: &Path,
    run_id: &str,
    date: &str,
    now: u64,
    outcomes: &[EnrichOutcome],
) -> Result<ClosedCaptures, String> {
    let layout = VaultLayout::new();
    let attempts_path = vault_root.join(layout.enrich_attempts_ledger());
    let ledger_path = vault_root.join(layout.intake_ledger());

    let mut closed = ClosedCaptures::default();
    if outcomes.is_empty() {
        return Ok(closed);
    }
    let mut history = read_enrich_attempts(&attempts_path)?;
    for o in outcomes {
        let path = vault_root.join(&o.from);
        let Ok(bytes) = std::fs::read(&path) else {
            continue;
        };
        let sha256 = hex_sha256(&bytes);
        let rec = EnrichAttemptRecord {
            schema: ENRICH_ATTEMPT_SCHEMA.into(),
            run_id: run_id.into(),
            sha256: sha256.clone(),
            from: o.from.clone(),
            url: o.url.clone(),
            attempted_at: now,
            ok: o.ok,
            error: o.error.clone(),
        };
        append_enrich_attempt(&attempts_path, &rec)?;
        history.push(rec);
        if o.ok {
            continue;
        }
        let folded = fold_attempts(&history);
        let Some(state) = folded.get(&sha256) else {
            continue;
        };
        if let EnrichVerdict::Unavailable(reason) = enrich_verdict(state, now) {
            let closed_rec = IntakeRecord {
                schema: INTAKE_SCHEMA.into(),
                run_id: run_id.into(),
                date: date.into(),
                action: IntakeAction::ContentUnavailable,
                from: o.from.clone(),
                to: None,
                url: Some(o.url.clone()),
                sha256,
                dup_of: None,
                title: None,
                note: Some(reason),
            };
            append_intake_record(&ledger_path, &closed_rec)?;
            closed.unavailable.push(closed_rec);
        }
    }
    Ok(closed)
}

/// Unix seconds now; `0` if the clock is before the epoch (never, in practice).
pub fn now_unix_secs() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn attempt(sha: &str, at: u64, ok: bool, err: Option<&str>) -> EnrichAttemptRecord {
        EnrichAttemptRecord {
            schema: ENRICH_ATTEMPT_SCHEMA.into(),
            run_id: "daily-test".into(),
            sha256: sha.into(),
            from: "50-Inbox/02-Pinboard/x.md".into(),
            url: "https://example.com/x".into(),
            attempted_at: at,
            ok,
            error: err.map(String::from),
        }
    }

    const H: u64 = 3600;

    #[test]
    fn below_both_budgets_stays_pending() {
        let folded = fold_attempts(&[
            attempt("h", 1_000, false, Some("404")),
            attempt("h", 1_000 + 4 * H, false, Some("404")),
        ]);
        let st = &folded["h"];
        assert_eq!(st.failures, 2);
        assert_eq!(st.first_failure, 1_000);
        assert_eq!(st.last_error.as_deref(), Some("404"));
        assert_eq!(enrich_verdict(st, 1_000 + 8 * H), EnrichVerdict::Pending);
    }

    #[test]
    fn third_failure_closes_on_count() {
        let folded = fold_attempts(&[
            attempt("h", 1_000, false, Some("timeout")),
            attempt("h", 1_000 + 4 * H, false, Some("timeout")),
            attempt("h", 1_000 + 8 * H, false, Some("404 Not Found")),
        ]);
        match enrich_verdict(&folded["h"], 1_000 + 8 * H) {
            EnrichVerdict::Unavailable(reason) => {
                assert!(reason.contains("3 failed enrich attempts"), "{reason}");
                assert!(reason.contains("404 Not Found"), "{reason}");
            }
            other => panic!("expected Unavailable, got {other:?}"),
        }
    }

    #[test]
    fn one_failure_older_than_72h_closes_on_time() {
        let folded = fold_attempts(&[attempt("h", 1_000, false, Some("dns"))]);
        assert_eq!(
            enrich_verdict(&folded["h"], 1_000 + 71 * H),
            EnrichVerdict::Pending
        );
        match enrich_verdict(&folded["h"], 1_000 + 72 * H) {
            EnrichVerdict::Unavailable(reason) => assert!(reason.contains("72h"), "{reason}"),
            other => panic!("expected Unavailable, got {other:?}"),
        }
    }

    #[test]
    fn success_resets_the_streak_and_is_never_closed() {
        let folded = fold_attempts(&[
            attempt("h", 1_000, false, Some("timeout")),
            attempt("h", 1_000 + H, false, Some("timeout")),
            attempt("h", 1_000 + 2 * H, true, None),
        ]);
        let st = &folded["h"];
        assert_eq!(st.failures, 0);
        assert_eq!(st.last_error, None);
        assert_eq!(enrich_verdict(st, 1_000 + 400 * H), EnrichVerdict::Pending);
    }

    #[test]
    fn hashes_fold_independently() {
        let folded = fold_attempts(&[
            attempt("a", 1, false, Some("x")),
            attempt("b", 2, false, Some("y")),
            attempt("a", 3, false, Some("x")),
        ]);
        assert_eq!(folded["a"].failures, 2);
        assert_eq!(folded["b"].failures, 1);
    }

    #[test]
    fn record_outcomes_closes_on_the_third_run_with_the_intake_hash() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let rel = "50-Inbox/02-Pinboard/2026-06-01-x.md";
        let abs = root.join(rel);
        std::fs::create_dir_all(abs.parent().unwrap()).unwrap();
        let body = "---\ntitle: x\nsource: \"https://example.com/x\"\n---\nshort\n";
        std::fs::write(&abs, body).unwrap();
        let sha = hex_sha256(body.as_bytes());

        let fail = EnrichOutcome {
            from: rel.into(),
            url: "https://example.com/x".into(),
            ok: false,
            error: Some("404".into()),
        };
        let layout = VaultLayout::new();
        let ledger = root.join(layout.intake_ledger());

        let one = std::slice::from_ref(&fail);
        let c1 = record_enrich_outcomes(root, "r1", "2026-06-01", 1_000, one).unwrap();
        assert!(c1.unavailable.is_empty());
        let c2 = record_enrich_outcomes(root, "r2", "2026-06-02", 1_000 + 4 * H, one).unwrap();
        assert!(c2.unavailable.is_empty());
        assert!(
            !ledger.exists(),
            "nothing closed yet → no intake record written"
        );

        let c3 = record_enrich_outcomes(root, "r3", "2026-06-03", 1_000 + 8 * H, &[fail]).unwrap();
        assert_eq!(c3.unavailable.len(), 1);
        let rec = &c3.unavailable[0];
        assert_eq!(rec.action, IntakeAction::ContentUnavailable);
        assert_eq!(
            rec.sha256, sha,
            "closed under the same hash the sweep flagged"
        );
        assert_eq!(rec.from, rel);
        assert!(rec.note.as_deref().unwrap().contains("404"));

        let records = crate::ledger::read_intake_ledger(&ledger).unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(
            crate::ledger::flagged_hashes(&records).get(&sha),
            Some(&IntakeAction::ContentUnavailable)
        );
        let attempts = read_enrich_attempts(&root.join(layout.enrich_attempts_ledger())).unwrap();
        assert_eq!(attempts.len(), 3);
    }

    #[test]
    fn record_outcomes_skips_a_vanished_file_and_ignores_successes() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let ok = EnrichOutcome {
            from: "50-Inbox/02-Pinboard/gone.md".into(),
            url: "https://example.com/gone".into(),
            ok: true,
            error: None,
        };
        let closed = record_enrich_outcomes(root, "r", "2026-06-01", 1, &[ok]).unwrap();
        assert!(closed.unavailable.is_empty());
        let layout = VaultLayout::new();
        assert!(!root.join(layout.enrich_attempts_ledger()).exists());
    }
}
