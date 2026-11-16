//! `crystal-citation-review` — send durable claims back to review when the
//! citations that still support them come from fewer than two sources
//! (INV-1423).
//!
//! The admission gate grounds a citation by checking that its quote occurs in
//! an accepted unit; it cannot tell whether that quote SUPPORTS the claim. A
//! per-citation support verdict (supports / contradicts / says_nothing, e.g. a
//! typed-decision audit) can. This command takes such verdicts and acts only
//! on durable claims that fall below the cross-source minimum once the
//! citations judged not to support them, at high confidence, are set aside:
//!
//! - default: print the plan and its id, write nothing;
//! - `--apply <plan-id>`: rebuild the plan and run it only if it is the plan
//!   that was reviewed (same id: same claims, same records, same verdicts);
//!   otherwise refuse with nothing written. Then append a `Retract` per claim
//!   (reason `citation_support_review:<plan-id>`) and queue the claim in the
//!   review lane with the verdicts in its rationale. Nothing is deleted;
//! - `--rollback <run-id>`: re-activate what that run retracted and unqueue it
//!   (each apply gets its own run id, so re-applying after a rollback is a
//!   separate run).
//!
//! Whether a passage supports a claim is a judgment, not a decidable check, so
//! the verdicts only send claims to a person; they never decide durability.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use ovp_domain::crystal::{
    Citation, CrystalStatus, DURABLE_MIN_SOURCES, DurableRecord, FinalClass, ReviewEntry,
    ReviewLane, StoreEvent, StoreOp, fold_ledger,
};

use crate::CliError;
use crate::commands::crystal_regate::{append_events, edit_review};
use crate::commands::crystal_write::read_ledger;

/// Verdicts below this confidence are left for a person to read, not acted on.
pub const MIN_CONFIDENCE: f64 = 0.8;

pub struct CrystalCitationReviewArgs {
    pub vault_root: PathBuf,
    pub verdicts: Option<PathBuf>,
    pub apply: Option<String>,
    pub rollback: Option<String>,
    pub limit: usize,
}

/// The three documented answers. Anything else fails the parse: a typo must
/// never read as "unsupported" and retract a claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Choice {
    Supports,
    Contradicts,
    SaysNothing,
}

impl Choice {
    fn as_str(self) -> &'static str {
        match self {
            Choice::Supports => "supports",
            Choice::Contradicts => "contradicts",
            Choice::SaysNothing => "says_nothing",
        }
    }
}

/// One per-citation support verdict (one JSON object per line).
#[derive(Debug, Clone, Deserialize)]
struct Verdict {
    claim_key: String,
    citation_index: usize,
    case_id: String,
    unit_id: String,
    quote: String,
    choice: Choice,
    confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Unsupported {
    pub citation_index: usize,
    pub case_id: String,
    pub choice: String,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReviewPlanEntry {
    pub claim_key: String,
    /// The durable record as it was — what a rollback writes back.
    pub record: DurableRecord,
    pub unsupported: Vec<Unsupported>,
    /// Distinct sources among the citations NOT judged unsupported.
    pub remaining_sources: usize,
    pub review: ReviewEntry,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReviewPlan {
    /// Hash of what the plan would do; what `--apply` must be given.
    pub plan_id: String,
    /// Set by apply: identifies ONE applied run for rollback. A re-apply of
    /// the same plan (after a rollback) gets its own run id. Not hashed.
    #[serde(default)]
    pub run_id: String,
    pub verdicts_sha256: String,
    pub entries: Vec<ReviewPlanEntry>,
    /// Claims whose verdicts no longer match the ledger's citations.
    #[serde(default)]
    pub skipped_stale: Vec<String>,
}

fn store_dir(vault: &Path) -> PathBuf {
    vault.join(".ovp/crystal")
}

fn plan_path(vault: &Path, plan_id: &str) -> PathBuf {
    store_dir(vault)
        .join("citation-review")
        .join(format!("{plan_id}.json"))
}

fn hex(bytes: impl AsRef<[u8]>) -> String {
    ovp_intake::hex_sha256(bytes.as_ref())
}

fn read_verdicts(path: &Path) -> Result<(Vec<Verdict>, String), CliError> {
    let raw = std::fs::read(path)
        .map_err(|e| CliError::Io(format!("reading verdicts {}: {e}", path.display())))?;
    let text = String::from_utf8(raw.clone())
        .map_err(|e| CliError::Io(format!("verdicts {} are not UTF-8: {e}", path.display())))?;
    let mut out = Vec::new();
    for (n, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let v: Verdict = serde_json::from_str(line).map_err(|e| {
            CliError::Io(format!("verdicts {} line {}: {e}", path.display(), n + 1))
        })?;
        // A probability: anything else is malformed input, not "very sure".
        if !(0.0..=1.0).contains(&v.confidence) {
            return Err(CliError::Io(format!(
                "verdicts {} line {}: confidence {} is not in [0, 1]",
                path.display(),
                n + 1,
                v.confidence
            )));
        }
        out.push(v);
    }
    Ok((out, hex(&raw)))
}

/// The plan for the vault as it is now. Its id hashes everything that decides
/// what an apply would do — the verdicts file, and each selected claim's
/// current record — so a changed input yields a different id.
pub fn build_plan(vault: &Path, verdicts_path: &Path) -> Result<ReviewPlan, CliError> {
    let (verdicts, verdicts_sha256) = read_verdicts(verdicts_path)?;
    let mut flagged: BTreeMap<&str, Vec<&Verdict>> = BTreeMap::new();
    for v in &verdicts {
        if v.choice != Choice::Supports && v.confidence >= MIN_CONFIDENCE {
            flagged.entry(v.claim_key.as_str()).or_default().push(v);
        }
    }
    let events = read_ledger(&store_dir(vault).join("ledger.jsonl"))?;
    let active: BTreeMap<String, DurableRecord> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active && r.final_class == FinalClass::Durable)
        .map(|r| (r.claim_key.clone(), r))
        .collect();
    let identities = ovp_domain::crystal::source_identity::load_source_identities(vault)
        .map_err(CliError::Io)?;
    let identity = |case: &str| {
        identities
            .get(case)
            .cloned()
            .unwrap_or_else(|| format!("case:{case}"))
    };

    let mut entries = Vec::new();
    let mut skipped_stale = Vec::new();
    for (key, flags) in flagged {
        // Retracted or superseded since the verdicts were made: nothing to do.
        let Some(rec) = active.get(key) else {
            continue;
        };
        // A verdict judged a citation as it was; if the ledger's citation at
        // that position is no longer the same one, the verdict says nothing
        // about the claim as it is now.
        let current = flags.iter().all(|f| {
            rec.citations.get(f.citation_index).is_some_and(|c| {
                c.case_id == f.case_id && c.unit_id == f.unit_id && c.quote == f.quote
            })
        });
        if !current {
            skipped_stale.push(key.to_string());
            continue;
        }
        let bad: BTreeSet<usize> = flags.iter().map(|f| f.citation_index).collect();
        let remaining: BTreeSet<String> = rec
            .citations
            .iter()
            .enumerate()
            .filter(|(i, _)| !bad.contains(i))
            .map(|(_, c)| identity(&c.case_id))
            .collect();
        if remaining.len() >= DURABLE_MIN_SOURCES {
            continue;
        }
        let unsupported: Vec<Unsupported> = flags
            .iter()
            .map(|f| Unsupported {
                citation_index: f.citation_index,
                case_id: f.case_id.clone(),
                choice: f.choice.as_str().to_string(),
                confidence: f.confidence,
            })
            .collect();
        let judged: Vec<String> = unsupported
            .iter()
            .map(|u| {
                format!(
                    "citation {} ({}) judged {} at {:.2}",
                    u.citation_index, u.case_id, u.choice, u.confidence
                )
            })
            .collect();
        let review = ReviewEntry {
            // claim_ids (`c-001`) repeat across runs; the key never does.
            claim_id: rec.claim_key.clone(),
            claim: rec.claim.clone(),
            theme: rec.theme.clone(),
            final_class: FinalClass::Caveated,
            strength: rec.strength,
            evidence_sufficient: false,
            rationale: format!(
                "{} | citation support review: {}; {} supporting source(s) remain",
                rec.strength_rationale,
                judged.join("; "),
                remaining.len()
            ),
            citations: rec
                .citations
                .iter()
                .map(|c| Citation {
                    case_id: c.case_id.clone(),
                    unit_id: c.unit_id.clone(),
                    quote: c.quote.clone(),
                    claimed_line: None,
                })
                .collect(),
            // Support is disputed — text↔evidence repair, not a parked insight.
            lane: ReviewLane::Review,
            defer: None,
        };
        entries.push(ReviewPlanEntry {
            claim_key: key.to_string(),
            record: rec.clone(),
            unsupported,
            remaining_sources: remaining.len(),
            review,
        });
    }
    let fingerprint = serde_json::to_string(&(&verdicts_sha256, &entries))
        .map_err(|e| CliError::Io(format!("serializing plan: {e}")))?;
    Ok(ReviewPlan {
        plan_id: format!("cr-{}", &hex(fingerprint)[..12]),
        run_id: String::new(),
        verdicts_sha256,
        entries,
        skipped_stale,
    })
}

/// Run the plan with id `plan_id` — and only that plan. Returns the plan.
pub fn apply(vault: &Path, verdicts_path: &Path, plan_id: &str) -> Result<ReviewPlan, CliError> {
    let mut plan = build_plan(vault, verdicts_path)?;
    if plan.plan_id != plan_id {
        return Err(CliError::Io(format!(
            "CONFLICT: the plan is now {} (reviewed: {plan_id}) — the ledger or the verdicts \
             changed since the dry-run; nothing was written. Re-run the dry-run and review it.",
            plan.plan_id
        )));
    }
    if plan.entries.is_empty() {
        return Ok(plan);
    }
    // Queueing replaces any entry with the same claim_id. One already keyed by
    // a selected claim (another command's, or a person's) must not be lost.
    let queued = queued_entries(&store_dir(vault).join("review.json"))?;
    let taken: Vec<&str> = plan
        .entries
        .iter()
        .map(|e| e.claim_key.as_str())
        .filter(|k| queued.contains_key(*k))
        .collect();
    if !taken.is_empty() {
        return Err(CliError::Io(format!(
            "CONFLICT: review.json already has entries for {} — resolve them first; nothing was written",
            taken.join(", ")
        )));
    }
    // apply → rollback → apply on the same inputs is the same plan; it must
    // not share the first run's file or retraction reason, or rolling back one
    // would undo the other.
    plan.run_id = plan.plan_id.clone();
    let mut n = 1;
    while plan_path(vault, &plan.run_id).exists() {
        n += 1;
        plan.run_id = format!("{}-{n}", plan.plan_id);
    }
    // The plan goes to disk BEFORE anything moves, so the run can always be
    // rolled back.
    let path = plan_path(vault, &plan.run_id);
    std::fs::create_dir_all(path.parent().expect("plan path has a parent"))
        .map_err(|e| CliError::Io(format!("creating {}: {e}", path.display())))?;
    let text = serde_json::to_string_pretty(&plan)
        .map_err(|e| CliError::Io(format!("serializing plan: {e}")))?;
    ovp_domain::tags::write_atomic(&path, &(text + "\n")).map_err(CliError::Io)?;

    // Queue first, ledger second (as in crystal-regate-sources): if the append
    // fails, the claims are still active, so a retry plans them again and
    // re-adding their queue entries is idempotent.
    let keys: BTreeSet<&str> = plan.entries.iter().map(|e| e.claim_key.as_str()).collect();
    let add: Vec<ReviewEntry> = plan.entries.iter().map(|e| e.review.clone()).collect();
    edit_review(&store_dir(vault).join("review.json"), &keys, &add)?;
    let reason = format!("citation_support_review:{}", plan.run_id);
    let events: Vec<StoreEvent> = plan
        .entries
        .iter()
        .map(|e| StoreEvent {
            op: StoreOp::Retract,
            record: e.record.clone(),
            supersedes: None,
            reason: Some(reason.clone()),
        })
        .collect();
    append_events(&store_dir(vault).join("ledger.jsonl"), &events)?;
    Ok(plan)
}

/// review.json entries by claim_id, as raw JSON. A missing file is an empty
/// queue; an unreadable one is an error (guessing "empty" would misjudge
/// ownership).
fn queued_entries(path: &Path) -> Result<BTreeMap<String, serde_json::Value>, CliError> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(e) => return Err(CliError::Io(format!("reading {}: {e}", path.display()))),
    };
    let body: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing {}: {e}", path.display())))?;
    Ok(body
        .get("review")
        .and_then(|r| r.as_array())
        .into_iter()
        .flatten()
        .filter_map(|e| Some((e.get("claim_id")?.as_str()?.to_string(), e.clone())))
        .collect())
}

/// Undo one applied run: re-activate what it retracted, unqueue what it
/// queued. A claim that has moved on since (re-written, superseded, retracted
/// by something else) is left alone, so a second rollback is a no-op. Returns
/// how many claims were re-activated.
pub fn rollback(vault: &Path, run_id: &str) -> Result<usize, CliError> {
    let path = plan_path(vault, run_id);
    let text = std::fs::read_to_string(&path)
        .map_err(|e| CliError::Io(format!("reading plan {}: {e}", path.display())))?;
    let plan: ReviewPlan = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing {}: {e}", path.display())))?;
    let ledger = store_dir(vault).join("ledger.jsonl");
    let ours = format!("citation_support_review:{run_id}");
    let events_now = read_ledger(&ledger)?;
    let folded: BTreeMap<String, CrystalStatus> = fold_ledger(&events_now)
        .into_iter()
        .map(|r| (r.claim_key, r.status))
        .collect();
    let mut last: BTreeMap<String, StoreEvent> = BTreeMap::new();
    for ev in events_now {
        last.insert(ev.record.claim_key.clone(), ev);
    }
    // A person acting on a queued claim (review-session rewrite, reject,
    // defer …) changes or removes its queue entry without touching the old
    // claim's ledger key, so the ledger alone cannot tell. The queue can: the
    // claim is still this run's only while its entry is exactly what we queued.
    let queue_path = store_dir(vault).join("review.json");
    let queued = queued_entries(&queue_path)?;
    let undo = format!("citation_support_review_rollback:{run_id}");
    let still_queued = |e: &ReviewPlanEntry| {
        serde_json::to_value(&e.review).is_ok_and(|q| queued.get(&e.claim_key) == Some(&q))
    };
    let last_reason_is = |e: &ReviewPlanEntry, op: StoreOp, reason: &str| {
        last.get(&e.claim_key)
            .is_some_and(|ev| ev.op == op && ev.reason.as_deref() == Some(reason))
    };
    // Re-activate: still retracted by this run, and nobody has acted on it.
    let owned: Vec<&ReviewPlanEntry> = plan
        .entries
        .iter()
        .filter(|e| {
            folded.get(&e.claim_key) == Some(&CrystalStatus::Retracted)
                && last_reason_is(e, StoreOp::Retract, &ours)
                && still_queued(e)
        })
        .collect();
    // Unqueue: the claims re-activated now, plus any a previous attempt
    // re-activated before it was interrupted (ledger first, queue second).
    let unqueue: BTreeSet<&str> = plan
        .entries
        .iter()
        .filter(|e| still_queued(e) && last_reason_is(e, StoreOp::Write, &undo))
        .chain(owned.iter().copied())
        .map(|e| e.claim_key.as_str())
        .collect();
    let events: Vec<StoreEvent> = owned
        .iter()
        .map(|e| StoreEvent {
            op: StoreOp::Write,
            record: DurableRecord {
                status: CrystalStatus::Active,
                ..e.record.clone()
            },
            supersedes: None,
            reason: Some(undo.clone()),
        })
        .collect();
    // Ledger first: if the queue edit then fails, a retry sees this run's
    // rollback event and finishes the unqueue instead of stranding the claim.
    if !events.is_empty() {
        append_events(&ledger, &events)?;
    }
    if !unqueue.is_empty() {
        edit_review(&queue_path, &unqueue, &[])?;
    }
    Ok(events.len())
}

pub fn run(args: CrystalCitationReviewArgs) -> Result<(), CliError> {
    let vault = &args.vault_root;
    if let Some(run_id) = &args.rollback {
        let _lock = ovp_intake::RunLock::acquire(vault).map_err(CliError::Io)?;
        let n = rollback(vault, run_id)?;
        println!("crystal-citation-review: rolled back {run_id} — {n} claim(s) re-activated");
        println!(
            "  run `ovp2 index --vault-root <vault>` (or wait for daily) to refresh the portal"
        );
        return Ok(());
    }
    let verdicts = args.verdicts.as_deref().ok_or_else(|| {
        CliError::Io("--verdicts <file> is required unless --rollback is given".into())
    })?;
    let _lock = match args.apply {
        Some(_) => Some(ovp_intake::RunLock::acquire(vault).map_err(CliError::Io)?),
        None => None,
    };
    let plan = match &args.apply {
        Some(id) => apply(vault, verdicts, id)?,
        None => build_plan(vault, verdicts)?,
    };
    if plan.entries.is_empty() {
        println!(
            "crystal-citation-review: no durable claim falls below {DURABLE_MIN_SOURCES} supporting \
             sources once citations judged unsupported (confidence >= {MIN_CONFIDENCE}) are set aside"
        );
    } else {
        println!(
            "crystal-citation-review: {} durable claim(s) {} the review lane (plan {})",
            plan.entries.len(),
            if args.apply.is_some() {
                "sent to"
            } else {
                "would go to"
            },
            plan.plan_id
        );
        for e in plan.entries.iter().take(args.limit) {
            let text: String = e.record.claim.chars().take(90).collect();
            println!("  {} — {text}", e.claim_key);
            for u in &e.unsupported {
                let quote: String = e.record.citations[u.citation_index]
                    .quote
                    .chars()
                    .take(80)
                    .collect();
                println!(
                    "      [{}] {} {:.2}: \"{quote}\"",
                    u.citation_index, u.choice, u.confidence
                );
            }
            println!("      {} supporting source(s) remain", e.remaining_sources);
        }
        if plan.entries.len() > args.limit {
            println!("  … {} more", plan.entries.len() - args.limit);
        }
    }
    if !plan.skipped_stale.is_empty() {
        println!(
            "  left alone: {} claim(s) whose verdicts no longer match their citations",
            plan.skipped_stale.len()
        );
    }
    if args.apply.is_some() {
        if !plan.entries.is_empty() {
            println!(
                "  APPLIED run {}: {} retract event(s) appended, queued in review.json; plan saved to {}",
                plan.run_id,
                plan.entries.len(),
                plan_path(vault, &plan.run_id).display()
            );
            println!(
                "  undo: ovp2 crystal-citation-review --vault-root <vault> --rollback {}",
                plan.run_id
            );
            println!(
                "  run `ovp2 index --vault-root <vault>` (or wait for daily) to refresh the portal"
            );
        }
    } else if !plan.entries.is_empty() {
        println!(
            "  dry-run: nothing written. To run exactly this plan: --apply {}",
            plan.plan_id
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::crystal_write::read_review_queue;
    use ovp_domain::SourceDoc;
    use ovp_domain::crystal::{DurableCitation, ProvenanceClass, StrengthClass};
    use ovp_domain::units::{Unit, validate};

    fn pack(vault: &Path, case_id: &str, body: &str) -> (String, String) {
        let raw = vec![serde_json::json!({
            "kind": "assertion", "text": "t", "evidence_ref": "p001",
            "evidence_quote": body, "attribution": "author", "modality": "asserted", "arguments": []
        })];
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![], body),
        );
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        let dir = vault.join("40-Resources/Reader").join(case_id);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("units.accepted.json"),
            serde_json::to_string(&units).unwrap(),
        )
        .unwrap();
        (units[0].id.clone(), units[0].evidence.quote.clone())
    }

    fn record(key: &str, cites: &[(&str, &(String, String))]) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: "c-001".into(),
            claim: format!("claim {key}"),
            theme: "t".into(),
            theme_id: None,
            source_cases: cites.iter().map(|c| c.0.to_string()).collect(),
            citations: cites
                .iter()
                .map(|(case, (unit, quote))| DurableCitation {
                    case_id: (*case).into(),
                    unit_id: unit.clone(),
                    quote: quote.clone(),
                    resolved_line: Some(1),
                })
                .collect(),
            provenance_score: 0.9,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "fixture".into(),
            final_class: FinalClass::Durable,
            run_id: "run-fixture".into(),
            status: CrystalStatus::Active,
        }
    }

    const A: &str = "2026-06-14_A-aaaa0000";
    const B: &str = "2026-06-15_B-bbbb0000";
    const C: &str = "2026-06-16_C-cccc0000";

    /// Three independent sources. `ck-weak` cites A+B, `ck-strong` A+B+C.
    /// Returns the vault and the verdicts file.
    fn vault() -> (tempfile::TempDir, PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let v = tmp.path();
        let a = pack(v, A, "Alpha is one thing.");
        let b = pack(v, B, "Beta is another thing.");
        let c = pack(v, C, "Gamma is a third thing.");
        std::fs::create_dir_all(v.join(".ovp/index")).unwrap();
        std::fs::write(
            v.join(".ovp/index/index.json"),
            serde_json::json!({
                "sources": [{"sha256": "sa"}, {"sha256": "sb"}, {"sha256": "sc"}],
                "packs": [
                    {"pack_dir": format!("40-Resources/Reader/{A}"), "source_sha256": "sa"},
                    {"pack_dir": format!("40-Resources/Reader/{B}"), "source_sha256": "sb"},
                    {"pack_dir": format!("40-Resources/Reader/{C}"), "source_sha256": "sc"}
                ]
            })
            .to_string(),
        )
        .unwrap();
        let weak = record("ck-weak", &[(A, &a), (B, &b)]);
        let strong = record("ck-strong", &[(A, &a), (B, &b), (C, &c)]);
        let store = v.join(".ovp/crystal");
        std::fs::create_dir_all(&store).unwrap();
        let lines: Vec<String> = [weak, strong]
            .into_iter()
            .map(|r| {
                serde_json::to_string(&StoreEvent {
                    op: StoreOp::Write,
                    record: r,
                    supersedes: None,
                    reason: None,
                })
                .unwrap()
            })
            .collect();
        std::fs::write(store.join("ledger.jsonl"), lines.join("\n") + "\n").unwrap();
        std::fs::write(
            store.join("review.json"),
            serde_json::json!({"review": [{
                "claim_id": "c-001", "claim": "unrelated", "theme": "t",
                "final_class": "caveated", "strength": "supported",
                "evidence_sufficient": false, "rationale": "r"
            }]})
            .to_string(),
        )
        .unwrap();
        let verdict =
            |key: &str, i: usize, case: &str, cite: &(String, String), choice: &str, conf: f64| {
                serde_json::json!({"claim_key": key, "citation_index": i, "case_id": case,
                "unit_id": cite.0, "quote": cite.1, "choice": choice, "confidence": conf})
                .to_string()
            };
        let verdicts = v.join("verdicts.jsonl");
        std::fs::write(
            &verdicts,
            [
                verdict("ck-weak", 0, A, &a, "supports", 0.99),
                verdict("ck-weak", 1, B, &b, "says_nothing", 0.97),
                verdict("ck-strong", 2, C, &c, "contradicts", 0.9),
                // Low confidence: read by a person, not acted on.
                verdict("ck-strong", 1, B, &b, "says_nothing", 0.4),
            ]
            .join("\n")
                + "\n",
        )
        .unwrap();
        (tmp, verdicts)
    }

    fn snapshot(v: &Path) -> (Vec<u8>, Vec<u8>) {
        (
            std::fs::read(v.join(".ovp/crystal/ledger.jsonl")).unwrap(),
            std::fs::read(v.join(".ovp/crystal/review.json")).unwrap(),
        )
    }

    fn active_keys(v: &Path) -> Vec<String> {
        fold_ledger(&read_ledger(&v.join(".ovp/crystal/ledger.jsonl")).unwrap())
            .into_iter()
            .filter(|r| r.status == CrystalStatus::Active)
            .map(|r| r.claim_key)
            .collect()
    }

    #[test]
    fn dry_run_selects_only_claims_left_below_two_sources_and_writes_nothing() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let before = snapshot(v);
        let plan = build_plan(v, &verdicts).unwrap();
        assert_eq!(plan.entries.len(), 1, "{plan:?}");
        let e = &plan.entries[0];
        assert_eq!(e.claim_key, "ck-weak");
        assert_eq!(e.remaining_sources, 1);
        assert_eq!(e.unsupported.len(), 1);
        assert_eq!(e.unsupported[0].citation_index, 1);
        assert_eq!(e.review.lane, ReviewLane::Review);
        assert_eq!(e.review.final_class, FinalClass::Caveated);
        assert!(
            e.review.rationale.contains("says_nothing"),
            "{}",
            e.review.rationale
        );
        assert!(plan.plan_id.starts_with("cr-"));
        // Same inputs, same id.
        assert_eq!(build_plan(v, &verdicts).unwrap().plan_id, plan.plan_id);
        assert_eq!(snapshot(v), before);
        assert!(!v.join(".ovp/crystal/citation-review").exists());
    }

    #[test]
    fn a_verdict_that_no_longer_matches_the_ledger_is_skipped_as_stale() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let text = std::fs::read_to_string(&verdicts)
            .unwrap()
            .replace("Beta is another thing.", "Beta was edited.");
        std::fs::write(&verdicts, text).unwrap();
        let plan = build_plan(v, &verdicts).unwrap();
        assert!(plan.entries.is_empty(), "{plan:?}");
        assert_eq!(plan.skipped_stale, vec!["ck-weak".to_string()]);
    }

    /// INV-1241's lesson: apply runs the plan that was reviewed, or nothing.
    #[test]
    fn apply_refuses_any_plan_but_the_reviewed_one() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let before = snapshot(v);
        assert!(apply(v, &verdicts, "cr-000000000000").is_err());
        assert_eq!(snapshot(v), before);
        // The ledger moves after the dry-run: the reviewed id is now stale.
        let plan = build_plan(v, &verdicts).unwrap();
        let mut ledger = std::fs::read_to_string(v.join(".ovp/crystal/ledger.jsonl")).unwrap();
        let mut moved = fold_ledger(&read_ledger(&v.join(".ovp/crystal/ledger.jsonl")).unwrap())
            .into_iter()
            .find(|r| r.claim_key == "ck-weak")
            .unwrap();
        moved.claim = "claim ck-weak, revised".into();
        ledger.push_str(
            &(serde_json::to_string(&StoreEvent {
                op: StoreOp::Write,
                record: moved,
                supersedes: None,
                reason: None,
            })
            .unwrap()
                + "\n"),
        );
        std::fs::write(v.join(".ovp/crystal/ledger.jsonl"), &ledger).unwrap();
        let moved_state = snapshot(v);
        assert!(apply(v, &verdicts, &plan.plan_id).is_err());
        assert_eq!(snapshot(v), moved_state);
    }

    /// A misspelled or unknown choice is an error, never "unsupported".
    #[test]
    fn unknown_verdict_choice_fails_loud() {
        let (tmp, verdicts) = vault();
        let text = std::fs::read_to_string(&verdicts)
            .unwrap()
            .replace("\"supports\"", "\"suports\"");
        std::fs::write(&verdicts, text).unwrap();
        assert!(build_plan(tmp.path(), &verdicts).is_err());
    }

    /// A queue entry already keyed by a selected claim (e.g. queued by
    /// crystal-regate-sources) would be replaced by the apply — refuse instead.
    #[test]
    fn apply_refuses_when_the_queue_already_holds_a_selected_claim() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let path = v.join(".ovp/crystal/review.json");
        let mut q: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        q["review"].as_array_mut().unwrap().push(serde_json::json!({
            "claim_id": "ck-weak", "claim": "queued earlier", "theme": "t",
            "final_class": "caveated", "strength": "supported",
            "evidence_sufficient": true, "rationale": "someone else's"
        }));
        std::fs::write(&path, q.to_string()).unwrap();
        let before = snapshot(v);
        let plan = build_plan(v, &verdicts).unwrap();
        let err = apply(v, &verdicts, &plan.plan_id).unwrap_err();
        assert!(format!("{err:?}").contains("ck-weak"), "{err:?}");
        assert_eq!(snapshot(v), before);
    }

    #[test]
    fn confidence_outside_zero_to_one_fails_loud() {
        let (tmp, verdicts) = vault();
        let text = std::fs::read_to_string(&verdicts)
            .unwrap()
            .replace("0.97", "97");
        std::fs::write(&verdicts, text).unwrap();
        assert!(build_plan(tmp.path(), &verdicts).is_err());
    }

    /// A rollback interrupted after re-activating (ledger) but before
    /// unqueueing must finish on retry, not strand the queue entry.
    #[test]
    fn interrupted_rollback_finishes_on_retry() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let plan = build_plan(v, &verdicts).unwrap();
        let run = apply(v, &verdicts, &plan.plan_id).unwrap();
        // Simulate the crash: only the ledger half of a rollback happened.
        let rec = run.entries[0].record.clone();
        append_events(
            &v.join(".ovp/crystal/ledger.jsonl"),
            &[StoreEvent {
                op: StoreOp::Write,
                record: DurableRecord {
                    status: CrystalStatus::Active,
                    ..rec
                },
                supersedes: None,
                reason: Some(format!("citation_support_review_rollback:{}", run.run_id)),
            }],
        )
        .unwrap();
        assert_eq!(
            rollback(v, &run.run_id).unwrap(),
            0,
            "nothing left to re-activate"
        );
        let queue = read_review_queue(&v.join(".ovp/crystal/review.json")).unwrap();
        assert!(
            !queue.iter().any(|e| e.claim_id == "ck-weak"),
            "entry finished unqueueing"
        );
        assert!(active_keys(v).contains(&"ck-weak".to_string()));
    }

    /// apply → rollback → apply on the same inputs: each apply is its own run,
    /// so undoing the first again must not touch the second.
    #[test]
    fn each_apply_is_its_own_run_for_rollback() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let plan = build_plan(v, &verdicts).unwrap();
        let first = apply(v, &verdicts, &plan.plan_id).unwrap();
        assert_eq!(rollback(v, &first.run_id).unwrap(), 1);
        let second = apply(v, &verdicts, &plan.plan_id).unwrap();
        assert_ne!(second.run_id, first.run_id);
        assert_eq!(
            rollback(v, &first.run_id).unwrap(),
            0,
            "first run is already undone"
        );
        assert_eq!(active_keys(v), vec!["ck-strong".to_string()]);
        assert_eq!(rollback(v, &second.run_id).unwrap(), 1);
    }

    /// Once a person has acted on the queued entry (rewritten, rejected,
    /// deferred — the queue entry changes or goes), rollback must leave that
    /// claim alone rather than resurrect it over their decision.
    #[test]
    fn rollback_leaves_claims_a_person_already_reviewed() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let plan = build_plan(v, &verdicts).unwrap();
        let run = apply(v, &verdicts, &plan.plan_id).unwrap();
        let path = v.join(".ovp/crystal/review.json");
        let mut q: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        for e in q["review"].as_array_mut().unwrap() {
            if e["claim_id"] == "ck-weak" {
                e["claim"] = "narrowed by a person".into();
            }
        }
        std::fs::write(&path, q.to_string()).unwrap();
        let before = std::fs::read(&path).unwrap();
        assert_eq!(rollback(v, &run.run_id).unwrap(), 0);
        assert_eq!(active_keys(v), vec!["ck-strong".to_string()]);
        assert_eq!(
            std::fs::read(&path).unwrap(),
            before,
            "their entry is untouched"
        );
    }

    #[test]
    fn apply_appends_only_queues_for_review_and_rollback_restores() {
        let (tmp, verdicts) = vault();
        let v = tmp.path();
        let (ledger_before, review_before) = snapshot(v);
        let fold_before = fold_ledger(&read_ledger(&v.join(".ovp/crystal/ledger.jsonl")).unwrap());
        let plan = build_plan(v, &verdicts).unwrap();
        let applied = apply(v, &verdicts, &plan.plan_id).unwrap();
        assert_eq!(applied.plan_id, plan.plan_id);
        let ledger_after = std::fs::read(v.join(".ovp/crystal/ledger.jsonl")).unwrap();
        assert!(
            ledger_after.starts_with(&ledger_before),
            "ledger must only grow"
        );
        assert_eq!(active_keys(v), vec!["ck-strong".to_string()]);
        let queue = read_review_queue(&v.join(".ovp/crystal/review.json")).unwrap();
        assert!(
            queue
                .iter()
                .any(|e| e.claim_id == "ck-weak" && e.lane == ReviewLane::Review)
        );
        assert!(
            queue.iter().any(|e| e.claim == "unrelated"),
            "unrelated entry survives"
        );
        // A second apply of the same id finds a different plan (claim retracted).
        assert!(apply(v, &verdicts, &plan.plan_id).is_err());

        assert_eq!(rollback(v, &applied.run_id).unwrap(), 1);
        let fold_after = fold_ledger(&read_ledger(&v.join(".ovp/crystal/ledger.jsonl")).unwrap());
        assert_eq!(fold_after, fold_before);
        // Rollback rewrites review.json (pretty-printed), so compare parsed.
        let q: serde_json::Value =
            serde_json::from_slice(&std::fs::read(v.join(".ovp/crystal/review.json")).unwrap())
                .unwrap();
        let q0: serde_json::Value = serde_json::from_slice(&review_before).unwrap();
        assert_eq!(q, q0);
        // A second rollback is a no-op.
        assert_eq!(rollback(v, &applied.run_id).unwrap(), 0);
    }
}
