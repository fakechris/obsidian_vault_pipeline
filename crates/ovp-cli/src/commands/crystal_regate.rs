//! `crystal-regate-sources` — send durable claims that only reached the
//! two-source minimum on duplicate packs of ONE source back through the gate
//! (INV-929).
//!
//! INV-915 fixed the gate going forward and made `crystal-recheck` list the
//! claims written before the fix (`duplicate_identity`). This acts on that
//! list and nothing else:
//!
//! - default: print the plan, write nothing;
//! - `--apply`: append a `Retract` event per claim (reason
//!   `source_identity_regate:<run>`) and queue the claim in `review.json` in
//!   the lane the CURRENT gate routes it to — a single-source, supported
//!   claim lands in `source_insight`. Nothing is deleted: the claim stays
//!   readable as caveated, and the ledger only grows;
//! - `--rollback <run>`: re-activate every claim the run retracted (a `Write`
//!   of the same record) and take its queue entries back out.
//!
//! The plan is saved before anything is appended, so a run can always be
//! rolled back. Finding an independent second source for a claim is NOT
//! attempted: whether a passage supports a claim is a judgment, not a
//! decidable check, and belongs to review.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use ovp_domain::crystal::{
    Citation, ClaimStrengthVerdict, CrystalStatus, DurableRecord, FinalClass, ReviewEntry,
    StoreEvent, StoreOp, default_run_id, fold_ledger, review_lane,
};

use crate::CliError;
use crate::commands::crystal_write::read_ledger;

pub struct CrystalRegateArgs {
    pub vault_root: PathBuf,
    pub apply: bool,
    pub rollback: Option<String>,
    pub limit: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RegatePlan {
    pub run_id: String,
    pub as_of: String,
    pub entries: Vec<RegateEntry>,
    /// Listed by recheck but ALSO carrying citations that no longer ground.
    /// The current gate would reject those, not caveat them; they are a
    /// staleness problem for `crystal-recheck`, so this command leaves them.
    #[serde(default)]
    pub skipped_stale: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RegateEntry {
    pub claim_key: String,
    /// The case_id groups recheck found to be one source.
    pub merged_cases: Vec<Vec<String>>,
    /// The durable record as it was — what a rollback writes back.
    pub record: DurableRecord,
    /// Where the current gate puts the claim.
    pub review: ReviewEntry,
}

fn store_dir(vault: &Path) -> PathBuf {
    vault.join(ovp_domain::VaultLayout::new().crystal_store_dir())
}

fn plan_path(vault: &Path, run_id: &str) -> PathBuf {
    store_dir(vault)
        .join("regate")
        .join(format!("{run_id}.json"))
}

/// The plan for the vault as it is now: every durable claim recheck lists
/// under `duplicate_identity`, routed by the current gate.
pub fn build_plan(vault: &Path, today: (i32, u32, u32)) -> Result<RegatePlan, CliError> {
    let report = ovp_domain::crystal::recheck::recheck_vault(vault, None, None, today)
        .map_err(CliError::Io)?;
    let events = read_ledger(&store_dir(vault).join("ledger.jsonl"))?;
    let active: BTreeMap<String, DurableRecord> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active && r.final_class == FinalClass::Durable)
        .map(|r| (r.claim_key.clone(), r))
        .collect();

    let stale: BTreeSet<&str> = report.stale.iter().map(|c| c.claim_id.as_str()).collect();
    let mut entries = Vec::new();
    let mut skipped_stale = Vec::new();
    // recheck rebuilds its candidate with claim_key as the claim id.
    for dup in &report.duplicate_identity {
        // Fully grounded + below the source minimum is exactly what the gate
        // routes to caveated; a stale citation would make it a reject instead.
        if stale.contains(dup.claim_id.as_str()) {
            skipped_stale.push(dup.claim_id.clone());
            continue;
        }
        let Some(rec) = active.get(&dup.claim_id) else {
            return Err(CliError::Io(format!(
                "recheck listed {} but the ledger has no active durable record for it",
                dup.claim_id
            )));
        };
        // A durable record passed final_routing, which requires a supported
        // verdict with sufficient evidence; only the source count changed.
        let verdict = ClaimStrengthVerdict {
            claim_id: rec.claim_key.clone(),
            strength: rec.strength,
            evidence_sufficient: true,
            rationale: rec.strength_rationale.clone(),
        };
        let review = ReviewEntry {
            // claim_ids (`c-001`) repeat across runs; the key never does, so
            // the queue merge cannot overwrite an unrelated entry.
            claim_id: rec.claim_key.clone(),
            claim: rec.claim.clone(),
            theme: rec.theme.clone(),
            final_class: FinalClass::Caveated,
            strength: rec.strength,
            evidence_sufficient: true,
            rationale: rec.strength_rationale.clone(),
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
            lane: review_lane(dup.distinct_sources, Some(&verdict)),
            defer: None,
        };
        entries.push(RegateEntry {
            claim_key: rec.claim_key.clone(),
            merged_cases: dup.merged_cases.clone(),
            record: rec.clone(),
            review,
        });
    }
    let keys: Vec<String> = entries.iter().map(|e| e.claim_key.clone()).collect();
    Ok(RegatePlan {
        run_id: default_run_id(&keys).replacen("run-", "regate-", 1),
        as_of: format!("{:04}-{:02}-{:02}", today.0, today.1, today.2),
        entries,
        skipped_stale,
    })
}

fn append_events(ledger: &Path, events: &[StoreEvent]) -> Result<(), CliError> {
    let mut lines = String::new();
    for ev in events {
        let line = serde_json::to_string(ev)
            .map_err(|e| CliError::Io(format!("serializing ledger event: {e}")))?;
        lines.push_str(&line);
        lines.push('\n');
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(ledger)
        .map_err(|e| CliError::Io(format!("opening ledger {}: {e}", ledger.display())))?;
    f.write_all(lines.as_bytes())
        .map_err(|e| CliError::Io(format!("appending ledger: {e}")))
}

/// Edit review.json as raw JSON: drop entries whose `claim_id` is in `drop`,
/// then append `add`. Entries this command does not own are never
/// re-serialized (no defaults filled in) and every other key (e.g. the
/// `collapsed` audit record) is kept, so apply-then-rollback restores the
/// queue exactly.
fn edit_review(path: &Path, drop: &BTreeSet<&str>, add: &[ReviewEntry]) -> Result<(), CliError> {
    if !path.exists() && add.is_empty() {
        return Ok(());
    }
    let mut body: serde_json::Value = if path.exists() {
        let text = std::fs::read_to_string(path)
            .map_err(|e| CliError::Io(format!("reading {}: {e}", path.display())))?;
        serde_json::from_str(&text)
            .map_err(|e| CliError::Io(format!("parsing {}: {e}", path.display())))?
    } else {
        serde_json::json!({})
    };
    let mut entries: Vec<serde_json::Value> = body
        .get("review")
        .and_then(|r| r.as_array())
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|e| {
            !e.get("claim_id")
                .and_then(|c| c.as_str())
                .is_some_and(|c| drop.contains(c))
        })
        .collect();
    for e in add {
        entries.push(
            serde_json::to_value(e)
                .map_err(|e| CliError::Io(format!("serializing review entry: {e}")))?,
        );
    }
    body["review"] = serde_json::Value::Array(entries);
    let text = serde_json::to_string_pretty(&body)
        .map_err(|e| CliError::Io(format!("serializing review queue: {e}")))?;
    // Temp sibling + rename: a crash mid-write leaves the old queue or the
    // whole new one, never a truncated file that loses unrelated entries.
    ovp_domain::tags::write_atomic(path, &(text + "\n")).map_err(CliError::Io)
}

/// Retract the planned claims and queue them. Returns the saved plan.
pub fn apply(vault: &Path, today: (i32, u32, u32)) -> Result<RegatePlan, CliError> {
    let mut plan = build_plan(vault, today)?;
    if plan.entries.is_empty() {
        return Ok(plan);
    }
    // apply → rollback → apply on the same claims hashes to the same id; a
    // second run must not overwrite the first's plan or share its retraction
    // reason, or rolling back one would undo the other.
    let base = plan.run_id.clone();
    let mut n = 1;
    while plan_path(vault, &plan.run_id).exists() {
        n += 1;
        plan.run_id = format!("{base}-{n}");
    }
    // The plan goes to disk BEFORE the ledger moves, so whatever happens next
    // the run can be rolled back.
    let path = plan_path(vault, &plan.run_id);
    std::fs::create_dir_all(path.parent().expect("plan path has a parent"))
        .map_err(|e| CliError::Io(format!("creating {}: {e}", path.display())))?;
    let text = serde_json::to_string_pretty(&plan)
        .map_err(|e| CliError::Io(format!("serializing plan: {e}")))?;
    ovp_domain::tags::write_atomic(&path, &(text + "\n")).map_err(CliError::Io)?;

    let reason = format!("source_identity_regate:{}", plan.run_id);
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

    // Append, leaving the existing queue as it is (order included), so a
    // rollback that filters these keys back out restores it.
    let keys: BTreeSet<&str> = plan.entries.iter().map(|e| e.claim_key.as_str()).collect();
    let add: Vec<ReviewEntry> = plan.entries.iter().map(|e| e.review.clone()).collect();
    edit_review(&store_dir(vault).join("review.json"), &keys, &add)?;
    Ok(plan)
}

/// Undo one applied run: re-activate what it retracted, unqueue what it
/// queued. Claims no longer retracted are left alone, so a second rollback is
/// a no-op. Returns how many claims were re-activated.
pub fn rollback(vault: &Path, run_id: &str) -> Result<usize, CliError> {
    let path = plan_path(vault, run_id);
    let text = std::fs::read_to_string(&path)
        .map_err(|e| CliError::Io(format!("reading regate plan {}: {e}", path.display())))?;
    let plan: RegatePlan = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing {}: {e}", path.display())))?;

    // Only undo what THIS run did: the claim must still be retracted in the
    // fold (a later Supersede flips it without touching its own key) AND its
    // latest event must be this run's Retract. Anything that moved on —
    // re-written, superseded, retracted by something else — is left alone,
    // queue entry included.
    let ledger = store_dir(vault).join("ledger.jsonl");
    let ours = format!("source_identity_regate:{run_id}");
    let events_now = read_ledger(&ledger)?;
    let folded: BTreeMap<String, CrystalStatus> = fold_ledger(&events_now)
        .into_iter()
        .map(|r| (r.claim_key, r.status))
        .collect();
    let mut last: BTreeMap<String, StoreEvent> = BTreeMap::new();
    for ev in events_now {
        last.insert(ev.record.claim_key.clone(), ev);
    }
    let owned: Vec<&RegateEntry> = plan
        .entries
        .iter()
        .filter(|e| {
            folded.get(&e.claim_key) == Some(&CrystalStatus::Retracted)
                && last.get(&e.claim_key).is_some_and(|ev| {
                    ev.op == StoreOp::Retract && ev.reason.as_deref() == Some(ours.as_str())
                })
        })
        .collect();

    // Queue first, ledger second: if the ledger append fails or the process
    // dies in between, the claims are still this run's retractions, so a
    // retry redoes both steps (unqueueing is idempotent).
    let keys: BTreeSet<&str> = owned.iter().map(|e| e.claim_key.as_str()).collect();
    if !keys.is_empty() {
        edit_review(&store_dir(vault).join("review.json"), &keys, &[])?;
    }
    let events: Vec<StoreEvent> = owned
        .iter()
        .map(|e| StoreEvent {
            op: StoreOp::Write,
            record: DurableRecord {
                status: CrystalStatus::Active,
                ..e.record.clone()
            },
            supersedes: None,
            reason: Some(format!("source_identity_regate_rollback:{run_id}")),
        })
        .collect();
    if !events.is_empty() {
        append_events(&ledger, &events)?;
    }
    Ok(events.len())
}

pub fn run(args: CrystalRegateArgs) -> Result<(), CliError> {
    let today = ovp_doctor::today_civil();
    let vault = &args.vault_root;
    if let Some(run_id) = &args.rollback {
        let _lock = ovp_intake::RunLock::acquire(vault).map_err(CliError::Io)?;
        let n = rollback(vault, run_id)?;
        println!("crystal-regate-sources: rolled back {run_id} — {n} claim(s) re-activated");
        println!(
            "  run `ovp2 index --vault-root <vault>` (or wait for daily) to refresh the portal"
        );
        return Ok(());
    }
    let _lock = if args.apply {
        Some(ovp_intake::RunLock::acquire(vault).map_err(CliError::Io)?)
    } else {
        None
    };
    let plan = if args.apply {
        apply(vault, today)?
    } else {
        build_plan(vault, today)?
    };
    if plan.entries.is_empty() {
        println!("crystal-regate-sources: no durable claim rests on duplicate packs of one source");
        return Ok(());
    }
    let mut by_lane: BTreeMap<String, usize> = BTreeMap::new();
    for e in &plan.entries {
        let lane = serde_json::to_value(e.review.lane)
            .ok()
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or_default();
        *by_lane.entry(lane).or_insert(0) += 1;
    }
    let lanes: Vec<String> = by_lane.iter().map(|(k, v)| format!("{k}={v}")).collect();
    println!(
        "crystal-regate-sources: {} durable claim(s) rest on one source — {} {}",
        plan.entries.len(),
        if args.apply {
            "routed to"
        } else {
            "would route to"
        },
        lanes.join(" ")
    );
    for e in plan.entries.iter().take(args.limit) {
        let text: String = e.record.claim.chars().take(90).collect();
        println!("  {} — {text}", e.claim_key);
        for g in &e.merged_cases {
            println!("      same source: {}", g.join(" = "));
        }
    }
    if plan.entries.len() > args.limit {
        println!("  … {} more", plan.entries.len() - args.limit);
    }
    if !plan.skipped_stale.is_empty() {
        println!(
            "  left alone: {} claim(s) that also have citations that no longer ground — see crystal-recheck",
            plan.skipped_stale.len()
        );
    }
    if args.apply {
        println!(
            "  APPLIED {}: {} retract event(s) appended, queued in review.json; plan saved to {}",
            plan.run_id,
            plan.entries.len(),
            plan_path(vault, &plan.run_id).display()
        );
        println!(
            "  undo: ovp2 crystal-regate-sources --vault-root <vault> --rollback {}",
            plan.run_id
        );
        println!(
            "  run `ovp2 index --vault-root <vault>` (or wait for daily) to refresh the portal; \
             crystal.md refreshes on the next crystallize"
        );
    } else {
        println!("  dry-run: nothing written. Pass --apply to retract and queue them.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::crystal_write::read_review_queue;
    use ovp_domain::SourceDoc;
    use ovp_domain::crystal::{DurableCitation, ProvenanceClass, ReviewLane, StrengthClass};
    use ovp_domain::units::{Unit, validate};

    const TODAY: (i32, u32, u32) = (2026, 10, 1);

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

    fn record(key: &str, cites: Vec<(&str, String, String)>) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: "c-001".into(),
            claim: format!("claim {key}"),
            theme: "t".into(),
            theme_id: None,
            source_cases: cites.iter().map(|c| c.0.to_string()).collect(),
            citations: cites
                .into_iter()
                .map(|(case, unit, quote)| DurableCitation {
                    case_id: case.into(),
                    unit_id: unit,
                    quote,
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

    /// A vault with one durable claim on two packs of ONE source (`ck-dup`)
    /// and one on two real sources (`ck-ok`), plus an unrelated review entry
    /// that shares the `c-001` claim_id and must survive.
    fn vault() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        let v = tmp.path();
        let a_old = pack(v, "aaaa0000-2026-01-01_A_", "Alpha is one thing.");
        let a_new = pack(v, "2026-06-15_A-aaaa0000", "Alpha is still one thing.");
        let b = pack(v, "2026-06-16_B-bbbb0000", "Beta is another thing.");
        std::fs::create_dir_all(v.join(".ovp/index")).unwrap();
        std::fs::write(
            v.join(".ovp/index/index.json"),
            serde_json::json!({
                "sources": [{"sha256": "sa"}, {"sha256": "sb"}],
                "packs": [
                    {"pack_dir": "40-Resources/Reader/aaaa0000-2026-01-01_A_", "source_sha256": "sa"},
                    {"pack_dir": "40-Resources/Reader/2026-06-15_A-aaaa0000", "source_sha256": "sa"},
                    {"pack_dir": "40-Resources/Reader/2026-06-16_B-bbbb0000", "source_sha256": "sb"}
                ]
            })
            .to_string(),
        )
        .unwrap();
        let dup = record(
            "ck-dup",
            vec![
                ("aaaa0000-2026-01-01_A_", a_old.0, a_old.1),
                ("2026-06-15_A-aaaa0000", a_new.0.clone(), a_new.1.clone()),
            ],
        );
        let ok = record(
            "ck-ok",
            vec![
                ("2026-06-15_A-aaaa0000", a_new.0, a_new.1),
                ("2026-06-16_B-bbbb0000", b.0, b.1),
            ],
        );
        let store = v.join(".ovp/crystal");
        std::fs::create_dir_all(&store).unwrap();
        let lines: Vec<String> = [dup, ok]
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
            serde_json::json!({
                "review": [{
                    "claim_id": "c-001", "claim": "unrelated", "theme": "t",
                    "final_class": "caveated", "strength": "supported",
                    "evidence_sufficient": false, "rationale": "r"
                }],
                "collapsed": [{"kept": "x", "dropped": "y", "reason": "z"}]
            })
            .to_string(),
        )
        .unwrap();
        tmp
    }

    fn active_keys(v: &Path) -> Vec<String> {
        fold_ledger(&read_ledger(&v.join(".ovp/crystal/ledger.jsonl")).unwrap())
            .into_iter()
            .filter(|r| r.status == CrystalStatus::Active)
            .map(|r| r.claim_key)
            .collect()
    }

    #[test]
    fn dry_run_plans_only_the_duplicate_and_writes_nothing() {
        let tmp = vault();
        let v = tmp.path();
        let before_ledger = std::fs::read(v.join(".ovp/crystal/ledger.jsonl")).unwrap();
        let before_review = std::fs::read(v.join(".ovp/crystal/review.json")).unwrap();
        let plan = build_plan(v, TODAY).unwrap();
        assert_eq!(plan.entries.len(), 1);
        assert_eq!(plan.entries[0].claim_key, "ck-dup");
        assert_eq!(plan.entries[0].review.lane, ReviewLane::SourceInsight);
        assert_eq!(plan.entries[0].review.final_class, FinalClass::Caveated);
        assert_eq!(
            std::fs::read(v.join(".ovp/crystal/ledger.jsonl")).unwrap(),
            before_ledger
        );
        assert_eq!(
            std::fs::read(v.join(".ovp/crystal/review.json")).unwrap(),
            before_review
        );
        assert!(!v.join(".ovp/crystal/regate").exists());
    }

    #[test]
    fn apply_appends_only_and_rollback_restores_the_fold() {
        let tmp = vault();
        let v = tmp.path();
        let ledger_path = v.join(".ovp/crystal/ledger.jsonl");
        let before = std::fs::read_to_string(&ledger_path).unwrap();
        let active_before = active_keys(v);
        let review_before: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(v.join(".ovp/crystal/review.json")).unwrap(),
        )
        .unwrap();

        let plan = apply(v, TODAY).unwrap();
        let after = std::fs::read_to_string(&ledger_path).unwrap();
        assert!(
            after.starts_with(&before),
            "history bytes unchanged — append only"
        );
        assert_eq!(after.lines().count(), before.lines().count() + 1);
        assert_eq!(
            active_keys(v),
            vec!["ck-ok".to_string()],
            "only ck-dup retracted"
        );
        assert!(
            v.join(format!(".ovp/crystal/regate/{}.json", plan.run_id))
                .exists()
        );

        let review = read_review_queue(&v.join(".ovp/crystal/review.json")).unwrap();
        let ids: Vec<&str> = review.iter().map(|r| r.claim_id.as_str()).collect();
        assert!(ids.contains(&"ck-dup"), "queued under its key: {ids:?}");
        assert!(
            ids.contains(&"c-001"),
            "unrelated entry with a colliding claim_id kept"
        );
        let raw: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(v.join(".ovp/crystal/review.json")).unwrap(),
        )
        .unwrap();
        assert!(
            raw.get("collapsed").is_some(),
            "other review.json keys preserved"
        );

        // Recheck no longer lists it (retracted claims are not rechecked).
        let r = ovp_domain::crystal::recheck::recheck_vault(v, None, None, TODAY).unwrap();
        assert!(r.duplicate_identity.is_empty());
        // A second apply finds nothing to do.
        assert!(build_plan(v, TODAY).unwrap().entries.is_empty());

        assert_eq!(rollback(v, &plan.run_id).unwrap(), 1);
        assert_eq!(active_keys(v), active_before, "fold back to where it was");
        let restored: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(v.join(".ovp/crystal/review.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(restored, review_before, "queue content and order restored");
        let review = read_review_queue(&v.join(".ovp/crystal/review.json")).unwrap();
        assert!(review.iter().all(|r| r.claim_id != "ck-dup"));
        assert!(review.iter().any(|r| r.claim_id == "c-001"));
        // Rolling back twice is a no-op.
        assert_eq!(rollback(v, &plan.run_id).unwrap(), 0);
    }

    #[test]
    fn rollback_never_undoes_a_later_retraction() {
        let tmp = vault();
        let v = tmp.path();
        let plan = apply(v, TODAY).unwrap();
        // Something else retracts the same claim afterwards.
        let later = StoreEvent {
            op: StoreOp::Retract,
            record: plan.entries[0].record.clone(),
            supersedes: None,
            reason: Some("operator: wrong".into()),
        };
        append_events(&v.join(".ovp/crystal/ledger.jsonl"), &[later]).unwrap();
        assert_eq!(rollback(v, &plan.run_id).unwrap(), 0, "not ours any more");
        assert!(!active_keys(v).contains(&"ck-dup".to_string()));
        let review = read_review_queue(&v.join(".ovp/crystal/review.json")).unwrap();
        assert!(
            review.iter().any(|r| r.claim_id == "ck-dup"),
            "queue entry left alone"
        );
    }

    #[test]
    fn rollback_never_resurrects_a_superseded_claim() {
        let tmp = vault();
        let v = tmp.path();
        let plan = apply(v, TODAY).unwrap();
        let mut replacement = plan.entries[0].record.clone();
        replacement.claim_key = "ck-new".into();
        let sup = StoreEvent {
            op: StoreOp::Supersede,
            record: replacement,
            supersedes: Some("ck-dup".into()),
            reason: None,
        };
        append_events(&v.join(".ovp/crystal/ledger.jsonl"), &[sup]).unwrap();
        assert_eq!(rollback(v, &plan.run_id).unwrap(), 0);
        assert!(!active_keys(v).contains(&"ck-dup".to_string()));
    }

    #[test]
    fn a_rollback_whose_ledger_append_failed_can_be_retried() {
        let tmp = vault();
        let v = tmp.path();
        let plan = apply(v, TODAY).unwrap();
        // Simulate dying between the two steps: queue already cleaned, no
        // Write appended. The claims are still this run's retractions.
        let keys: BTreeSet<&str> = ["ck-dup"].into_iter().collect();
        edit_review(&v.join(".ovp/crystal/review.json"), &keys, &[]).unwrap();
        assert_eq!(
            rollback(v, &plan.run_id).unwrap(),
            1,
            "retry finishes the job"
        );
        assert!(active_keys(v).contains(&"ck-dup".to_string()));
    }

    #[test]
    fn a_second_apply_of_the_same_claims_is_a_separate_run() {
        let tmp = vault();
        let v = tmp.path();
        let first = apply(v, TODAY).unwrap();
        assert_eq!(rollback(v, &first.run_id).unwrap(), 1);
        let second = apply(v, TODAY).unwrap();
        assert_ne!(first.run_id, second.run_id);
        assert!(
            v.join(format!(".ovp/crystal/regate/{}.json", first.run_id))
                .exists()
        );
        // Rolling the FIRST run back again must not undo the second.
        assert_eq!(rollback(v, &first.run_id).unwrap(), 0);
        assert!(!active_keys(v).contains(&"ck-dup".to_string()));
        assert_eq!(rollback(v, &second.run_id).unwrap(), 1);
    }

    #[test]
    fn a_claim_with_stale_citations_is_left_to_recheck() {
        let tmp = vault();
        let v = tmp.path();
        // ck-dup keeps both grounded duplicate packs (so recheck still lists
        // it) and gains a third citation whose unit is gone. The gate would
        // reject it now, so regate must not caveat it.
        let ledger = v.join(".ovp/crystal/ledger.jsonl");
        let mut events = read_ledger(&ledger).unwrap();
        for ev in events.iter_mut().filter(|e| e.record.claim_key == "ck-dup") {
            ev.record.citations.push(DurableCitation {
                case_id: "2026-06-16_B-bbbb0000".into(),
                unit_id: "u-gone".into(),
                quote: "Beta".into(),
                resolved_line: None,
            });
        }
        std::fs::remove_file(&ledger).unwrap();
        append_events(&ledger, &events).unwrap();
        let report = ovp_domain::crystal::recheck::recheck_vault(v, None, None, TODAY).unwrap();
        assert_eq!(
            report.duplicate_identity.len(),
            1,
            "still listed by recheck"
        );
        let plan = build_plan(v, TODAY).unwrap();
        assert!(plan.entries.is_empty(), "{plan:?}");
        assert_eq!(plan.skipped_stale, vec!["ck-dup".to_string()]);
    }
}
