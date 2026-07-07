use std::collections::BTreeSet;
use std::path::PathBuf;

use ovp_domain::crystal::synth::{collect_catalog, parse_strength_verdicts, strength_request};
use ovp_domain::crystal::{
    apply_decisions, strength_coverage, ClaimStrengthVerdict, CrystalCandidate, CrystalClaim,
    CrystalHeader, ReviewAction, ReviewDecision, ReviewEntry,
};
use ovp_domain::vault_layout::VaultLayout;

use crate::commands::client::{build_client, ClientKind};
use crate::commands::crystal_synth::{call_and_parse, RepairLog, MAX_STRENGTH_CLAIMS_PER_CALL};
use crate::commands::crystal_write::{
    build_grounding_index, merge_review_queue, read_review_queue, write_durable,
    write_review_queue, WriteInputs,
};
use crate::commands::{console_cmd, index_cmd, project};
use crate::CliError;

pub struct CrystalReviewSessionPrepareArgs {
    pub vault_root: PathBuf,
    pub batch: usize,
    pub out: PathBuf,
}

pub fn run_prepare(args: CrystalReviewSessionPrepareArgs) -> Result<(), CliError> {
    let review_path = args.vault_root.join(".ovp/crystal/review.json");
    let mut review = read_review_queue(&review_path)?;
    // Human sessions review the Review lane only — source-scoped insights
    // (single-source + Supported) are parked, not human debt (M35).
    let parked = review
        .iter()
        .filter(|e| e.lane == ovp_domain::crystal::ReviewLane::SourceInsight)
        .count();
    review.retain(|e| e.lane == ovp_domain::crystal::ReviewLane::Review);
    if parked > 0 {
        println!("  ({parked} source-insight entr(ies) parked outside the human queue)");
    }
    review.sort_by(|a, b| {
        (a.theme.as_str(), a.claim_id.as_str()).cmp(&(b.theme.as_str(), b.claim_id.as_str()))
    });
    review.truncate(args.batch);

    std::fs::create_dir_all(&args.out)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", args.out.display())))?;
    std::fs::write(
        args.out.join("selected-claim-ids.txt"),
        selected_ids(&review),
    )
    .map_err(|e| CliError::Io(format!("writing selected ids: {e}")))?;
    std::fs::write(
        args.out.join("review-sheet.md"),
        render_review_sheet(&review),
    )
    .map_err(|e| CliError::Io(format!("writing review sheet: {e}")))?;
    std::fs::write(
        args.out.join("decisions.template.json"),
        render_decisions_template(&review)?,
    )
    .map_err(|e| CliError::Io(format!("writing decisions template: {e}")))?;

    println!(
        "crystal-review session prepare: {} claim(s) -> {}",
        review.len(),
        args.out.display()
    );
    println!("  review-sheet.md");
    println!("  decisions.template.json");
    println!("  selected-claim-ids.txt");
    Ok(())
}

fn selected_ids(review: &[ReviewEntry]) -> String {
    let mut out = String::new();
    for entry in review {
        out.push_str(&entry.claim_id);
        out.push('\n');
    }
    out
}

pub struct CrystalReviewSessionApplyArgs {
    pub vault_root: PathBuf,
    pub decisions: PathBuf,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub run_id: Option<String>,
    pub title: Option<String>,
    pub refresh: bool,
    pub date: Option<String>,
}

/// Apply a filled decisions file against the vault-local review queue:
/// decisions → revised claims → strength gate → durable write (the reviewed
/// queue entries retire) → optional project/index/console refresh. Human
/// decisions NEVER bypass the gate. Malformed decisions and revisions with
/// defective citations fail LOUD before anything is mutated (fix
/// decisions.json and re-run — silent routing would discard the reviewer's
/// citation work); revisions that pass grounding but fail the strength gate
/// route back into the review queue with their rationale.
pub fn run_apply(args: CrystalReviewSessionApplyArgs) -> Result<(), CliError> {
    if args.refresh && args.date.is_none() {
        return Err(CliError::Io(
            "crystal-review-session-apply: --refresh requires --date <YYYY-MM-DD>".into(),
        ));
    }
    let layout = VaultLayout::new();
    let store = args.vault_root.join(layout.crystal_store_dir());
    let review_path = store.join("review.json");
    let entries = read_review_queue(&review_path)?;
    if entries.is_empty() {
        return Err(CliError::Io(format!(
            "crystal-review-session-apply: empty review queue at {}",
            review_path.display()
        )));
    }
    let text = std::fs::read_to_string(&args.decisions)
        .map_err(|e| CliError::Io(format!("reading {}: {e}", args.decisions.display())))?;
    let decisions: Vec<ReviewDecision> = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parsing {}: {e}", args.decisions.display())))?;

    // Validate decision cardinality BEFORE anything mutates: a rewrite/split
    // left with empty revisions would otherwise retire the entry as a silent
    // reject (a template edited halfway is the common mistake).
    let mut malformed: Vec<String> = Vec::new();
    for d in &decisions {
        match d.action {
            ReviewAction::Rewrite if d.revisions.len() != 1 => malformed.push(format!(
                "{}: rewrite requires exactly 1 revision (got {})",
                d.claim_id,
                d.revisions.len()
            )),
            ReviewAction::Split if d.revisions.len() < 2 => malformed.push(format!(
                "{}: split requires >=2 revisions (got {})",
                d.claim_id,
                d.revisions.len()
            )),
            _ => {}
        }
    }
    if !malformed.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: malformed decisions — {} — nothing was changed; \
             fix the decisions file and re-run",
            malformed.join("; ")
        )));
    }

    // The queue entries ARE the original candidate for id-matching purposes
    // (rewrite/split revisions carry their own full claims + citations).
    let original = CrystalCandidate {
        items: entries
            .iter()
            .map(|e| CrystalClaim {
                id: e.claim_id.clone(),
                claim: e.claim.clone(),
                theme: e.theme.clone(),
                citations: Vec::new(),
                caveat: None,
            })
            .collect(),
    };
    let outcome = apply_decisions(&original, &decisions);
    if !outcome.unknown.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: decisions reference unknown claim ids {:?} — \
             refusing to proceed (stale decisions file?)",
            outcome.unknown
        )));
    }
    // Reviewed ids leave the queue: rewrite/split (replaced by revisions that
    // re-enter the gate) and reject. keep_caveated stays queued.
    let processed: BTreeSet<String> = outcome
        .log
        .iter()
        .filter(|(_, action, _)| !matches!(action, ReviewAction::KeepCaveated))
        .map(|(id, _, _)| id.clone())
        .collect();
    for (id, action, n) in &outcome.log {
        println!("  decision {id}: {action:?} -> {n} revision(s)");
    }

    let revised = outcome.revised;
    if revised.items.is_empty() {
        // reject/keep-only session: no model call, no ledger change — just
        // retire the processed entries. crystal.md's review section refreshes
        // on the next durable write.
        let merged = merge_review_queue(entries, &processed, Vec::new());
        write_review_queue(&review_path, &merged)?;
        println!(
            "crystal-review-session-apply: no revisions to re-gate; retired {} entr(ies), \
             {} remain in {}",
            processed.len(),
            merged.len(),
            review_path.display()
        );
        refresh_views(&args)?;
        return Ok(());
    }

    // Re-gate the revisions through the SAME machinery as crystal-synth:
    // grounding index + chunked strength verdicts + the shared durable write.
    let reader_root = args.vault_root.join(layout.reader_root());
    let index = build_grounding_index(&reader_root)?;
    let catalog = collect_catalog(&reader_root)
        .map_err(|e| CliError::Io(format!("crystal-review-session-apply: {e}")))?;

    // Pre-lint the revisions BEFORE any model spend or mutation: write_durable
    // fails loud on citation defects, so one typo'd citation would abort the
    // whole batch after the queue math. Failing here keeps decisions.json the
    // single thing to fix and never discards the reviewer's citation work.
    let lint = ovp_domain::crystal::lint_candidate(&revised, &index);
    let defective: Vec<String> = lint
        .claims
        .iter()
        .filter(|c| !c.fully_grounded)
        .map(|c| {
            let details: Vec<String> = c
                .citations
                .iter()
                .filter_map(|cc| cc.defect.as_ref().map(|d| format!("{}:{d:?}", cc.unit_id)))
                .collect();
            let detail = if details.is_empty() { "no citations".to_string() } else { details.join(", ") };
            format!("{} ({detail})", c.claim_id)
        })
        .collect();
    if !defective.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: {} revision(s) have citation defects — {} — \
             nothing was changed; fix the revisions' citations and re-run",
            defective.len(),
            defective.join("; ")
        )));
    }
    let cache_dir = args
        .cache_dir
        .clone()
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/crystal"));
    let mut client = build_client(args.client_kind, &cache_dir)?;
    let mut verdicts: Vec<ClaimStrengthVerdict> = Vec::new();
    let mut repairs: Vec<RepairLog> = Vec::new();
    for chunk in revised.items.chunks(MAX_STRENGTH_CLAIMS_PER_CALL) {
        let sub = CrystalCandidate { items: chunk.to_vec() };
        let req = strength_request(&sub, &catalog);
        let (chunk_verdicts, log): (Vec<ClaimStrengthVerdict>, Option<RepairLog>) =
            call_and_parse(client.as_mut(), &req, "strength", parse_strength_verdicts)?;
        verdicts.extend(chunk_verdicts);
        if let Some(l) = log {
            repairs.push(l);
        }
    }
    let ids: Vec<String> = revised.items.iter().map(|c| c.id.clone()).collect();
    let coverage = strength_coverage(&ids, &verdicts);
    if !coverage.complete() {
        return Err(CliError::Gate(format!(
            "crystal-review-session-apply: strength verdicts incomplete — missing={:?} \
             duplicate={:?} unknown={:?}",
            coverage.missing, coverage.duplicate, coverage.unknown
        )));
    }

    let header = CrystalHeader {
        title: args.title.clone().unwrap_or_else(|| "Crystal".into()),
        scope: String::new(),
        not_claiming: String::new(),
    };
    let n_revised = revised.items.len();
    let out = write_durable(WriteInputs {
        candidate: revised,
        verdicts,
        index,
        store,
        run_id: args.run_id.clone(),
        header,
        processed_review_ids: processed,
    })?;
    println!("crystal-review-session-apply: run_id={}", out.run_id);
    println!(
        "  re-gated {n_revised} revision(s): {} newly durable ({} already active), \
         {} routed back to review",
        out.appended,
        out.considered.saturating_sub(out.appended),
        out.review
    );
    for r in &repairs {
        println!("  json-repair[{}]: {}", r.stage, r.method);
    }
    println!("  store: {} active durable claim(s) total", out.active_total);
    println!("  ledger: {}", out.ledger_path.display());

    refresh_views(&args)?;
    Ok(())
}

/// Optional post-write refresh: Crystal Notes projection + index + console.
/// Runs for BOTH the durable-write path and the reject/keep-only path — a
/// queue-only session still changes what the console's review page must show.
fn refresh_views(args: &CrystalReviewSessionApplyArgs) -> Result<(), CliError> {
    if !args.refresh {
        return Ok(());
    }
    let date = args.date.clone().expect("checked at entry");
    project::run(project::ProjectArgs {
        vault_root: args.vault_root.clone(),
        lane: project::LaneFilter::Durable,
        verbose: false,
        write: true,
        rebuild: false,
    })?;
    index_cmd::run_index(index_cmd::IndexArgs {
        vault_root: args.vault_root.clone(),
        date: date.clone(),
    })?;
    console_cmd::run(console_cmd::ConsoleArgs { vault_root: args.vault_root.clone(), date })
}

fn render_review_sheet(review: &[ReviewEntry]) -> String {
    let mut out = String::from("# Crystal Review Session\n\n");
    if review.is_empty() {
        out.push_str("_No review entries selected._\n");
        return out;
    }
    for (idx, entry) in review.iter().enumerate() {
        out.push_str(&format!(
            "## {}. `{}` [{}] - {}\n\n{}\n\n_strength: {:?} | evidence_sufficient: {}_\n\n{}\n\n",
            idx + 1,
            entry.claim_id,
            entry.theme,
            final_class_label(entry),
            entry.claim.trim(),
            entry.strength,
            entry.evidence_sufficient,
            entry.rationale.trim()
        ));
        if entry.citations.is_empty() {
            out.push_str(
                "_No citations on this entry (pre-M35 queue) — regenerate the queue with a \
                 replay `crystal-synth` run to populate them._\n\n",
            );
        } else {
            out.push_str("Citations (copy into `revisions` for rewrite/split):\n\n");
            for c in &entry.citations {
                out.push_str(&format!(
                    "- `{}` · `{}` — \"{}\"\n",
                    c.case_id,
                    c.unit_id,
                    c.quote.trim()
                ));
            }
            out.push('\n');
        }
    }
    out
}

fn final_class_label(entry: &ReviewEntry) -> String {
    format!("{:?}", entry.final_class)
}

fn render_decisions_template(review: &[ReviewEntry]) -> Result<String, CliError> {
    let decisions: Vec<ReviewDecision> = review
        .iter()
        .map(|entry| ReviewDecision {
            claim_id: entry.claim_id.clone(),
            action: ReviewAction::KeepCaveated,
            revisions: Vec::new(),
            note: "TODO: rewrite | split | keep_caveated | reject".into(),
        })
        .collect();
    serde_json::to_string_pretty(&decisions)
        .map(|body| format!("{body}\n"))
        .map_err(|e| CliError::Io(format!("serializing decisions template: {e}")))
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::json;

    use crate::commands::crystal_review_session::{run_prepare, CrystalReviewSessionPrepareArgs};

    #[test]
    fn crystal_review_session_prepare_writes_deterministic_batch_files() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            serde_json::to_string_pretty(&json!({
                "review": [
                    {
                        "claim_id": "z",
                        "claim": "z claim",
                        "theme": "zeta",
                        "final_class": "caveated",
                        "strength": "supported",
                        "evidence_sufficient": true,
                        "rationale": "z rationale"
                    },
                    {
                        "claim_id": "a",
                        "claim": "a claim",
                        "theme": "alpha",
                        "final_class": "caveated",
                        "strength": "supported",
                        "evidence_sufficient": true,
                        "rationale": "a rationale"
                    }
                ]
            }))
            .unwrap(),
        )
        .unwrap();
        let out = tmp.path().join("session");

        run_prepare(CrystalReviewSessionPrepareArgs {
            vault_root: vault,
            batch: 1,
            out: out.clone(),
        })
        .unwrap();

        assert_eq!(
            fs::read_to_string(out.join("selected-claim-ids.txt")).unwrap(),
            "a\n"
        );
        let sheet = fs::read_to_string(out.join("review-sheet.md")).unwrap();
        assert!(sheet.contains("a claim"), "{sheet}");
        assert!(!sheet.contains("z claim"), "{sheet}");
        let template = fs::read_to_string(out.join("decisions.template.json")).unwrap();
        assert!(template.contains(r#""claim_id": "a""#), "{template}");
        assert!(
            template.contains(r#""action": "keep_caveated""#),
            "{template}"
        );
    }

    fn review_json(entries: serde_json::Value) -> String {
        serde_json::to_string_pretty(&json!({ "review": entries })).unwrap()
    }

    fn caveated_entry(id: &str, claim: &str, theme: &str) -> serde_json::Value {
        json!({
            "claim_id": id, "claim": claim, "theme": theme,
            "final_class": "caveated", "strength": "supported",
            "evidence_sufficient": true, "rationale": "needs review"
        })
    }

    #[test]
    fn apply_reject_and_keep_only_retires_entries_without_model_calls() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            review_json(json!([
                caveated_entry("c1", "too broad", "memory"),
                caveated_entry("c2", "still deciding", "agents"),
            ])),
        )
        .unwrap();
        let decisions = tmp.path().join("decisions.json");
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "reject", "revisions": [], "note": "dup" }
            ]))
            .unwrap(),
        )
        .unwrap();

        super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect("reject-only session applies");

        let queue = fs::read_to_string(store.join("review.json")).unwrap();
        assert!(!queue.contains("\"c1\""), "rejected entry retired: {queue}");
        assert!(queue.contains("\"c2\""), "undecided entry stays: {queue}");
        assert!(!store.join("ledger.jsonl").exists(), "no durable write happened");
    }

    #[test]
    fn apply_rejects_rewrite_with_empty_revisions_before_mutating_anything() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        let queue_before = review_json(json!([caveated_entry("c1", "too broad", "memory")]));
        fs::write(store.join("review.json"), &queue_before).unwrap();
        let decisions = tmp.path().join("decisions.json");
        // The common template mistake: action flipped to rewrite, revisions left empty.
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "rewrite", "revisions": [], "note": "oops" }
            ]))
            .unwrap(),
        )
        .unwrap();

        let err = super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect_err("empty rewrite must fail loud, not silently reject");
        assert!(matches!(err, crate::CliError::Gate(_)), "got {err:?}");
        assert_eq!(
            fs::read_to_string(store.join("review.json")).unwrap(),
            queue_before,
            "queue untouched on malformed decisions"
        );
    }

    #[test]
    fn apply_fails_loud_on_citation_defects_without_mutating_the_queue() {
        use ovp_domain::crystal::{Citation, CrystalClaim};
        use ovp_domain::source_doc::SourceDoc;
        use ovp_domain::units::{validate, Unit};

        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let reader = vault.join("40-Resources/Reader");
        let case_dir = reader.join("m18-01");
        fs::create_dir_all(&case_dir).unwrap();
        let raw = vec![json!({
            "kind": "assertion", "text": "t0", "evidence_ref": "p001",
            "evidence_quote": "Memory is scarce working memory in systems.",
            "attribution": "author", "modality": "asserted", "arguments": []
        })];
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![],
                "Memory is scarce working memory in systems."),
        );
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        fs::write(case_dir.join("units.accepted.json"), serde_json::to_string_pretty(&units).unwrap()).unwrap();
        fs::write(case_dir.join("reader.md"), "# T\n\nbody\n").unwrap();

        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        let queue_before = review_json(json!([caveated_entry("c1", "too broad", "memory")]));
        fs::write(store.join("review.json"), &queue_before).unwrap();

        // Revision cites a unit that does not exist → citation defect.
        let bad = CrystalClaim {
            id: String::new(),
            claim: "narrowed".into(),
            theme: "memory".into(),
            citations: vec![Citation {
                case_id: "m18-01".into(),
                unit_id: "u-nope".into(),
                quote: "nope".into(),
                claimed_line: None,
            }],
            caveat: None,
        };
        let decisions = tmp.path().join("decisions.json");
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "rewrite", "revisions": [bad], "note": "typo" }
            ]))
            .unwrap(),
        )
        .unwrap();

        let err = super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: None,
            refresh: false,
            date: None,
        })
        .expect_err("citation defect must fail loud before any model call or write");
        assert!(matches!(err, crate::CliError::Gate(_)), "got {err:?}");
        assert_eq!(
            fs::read_to_string(store.join("review.json")).unwrap(),
            queue_before,
            "queue untouched on defective revision"
        );
        assert!(!store.join("ledger.jsonl").exists(), "no durable write happened");
    }

    #[test]
    fn apply_rewrite_regates_to_durable_and_retires_the_old_entry() {
        use ovp_domain::crystal::synth::{collect_catalog, strength_request};
        use ovp_domain::crystal::{Citation, CrystalCandidate, CrystalClaim};
        use ovp_domain::source_doc::SourceDoc;
        use ovp_domain::units::{validate, Unit};

        fn write_pack(dir: &std::path::Path, case_id: &str, title: &str, body: &str, quotes: &[&str]) -> String {
            let case_dir = dir.join(case_id);
            fs::create_dir_all(&case_dir).unwrap();
            let raw: Vec<_> = quotes
                .iter()
                .enumerate()
                .map(|(i, q)| {
                    json!({
                        "kind": "assertion", "text": format!("t{i}"), "evidence_ref": "p001",
                        "evidence_quote": q, "attribution": "author", "modality": "asserted", "arguments": []
                    })
                })
                .collect();
            let ex = validate(&raw, &SourceDoc::article("T", "https://e/x", None, None, vec![], body));
            let units: Vec<Unit> = ex.accepted().cloned().collect();
            let uid = units[0].id.clone();
            fs::write(
                case_dir.join("units.accepted.json"),
                serde_json::to_string_pretty(&units).unwrap(),
            )
            .unwrap();
            fs::write(case_dir.join("reader.md"), format!("# {title}\n\nbody\n")).unwrap();
            uid
        }

        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let reader = vault.join("40-Resources/Reader");
        let u1 = write_pack(&reader, "m18-01", "Working memory systems",
            "Memory is scarce working memory in systems. It must be curated.",
            &["Memory is scarce working memory in systems."]);
        let u2 = write_pack(&reader, "m18-02", "Context and retrieval",
            "Context windows are a scarce budget for retrieval.",
            &["Context windows are a scarce budget for retrieval."]);

        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            review_json(json!([caveated_entry("c1", "memory is everything", "memory")])),
        )
        .unwrap();

        // The human rewrite: narrower claim, verbatim cross-source citations.
        let revised_claim = CrystalClaim {
            id: "c1r".into(), // apply_decisions derives this from an empty revision id
            claim: "Memory and context are treated as a scarce budget across systems.".into(),
            theme: "memory".into(),
            citations: vec![
                Citation { case_id: "m18-01".into(), unit_id: u1, quote: "scarce working memory in systems".into(), claimed_line: None },
                Citation { case_id: "m18-02".into(), unit_id: u2, quote: "scarce budget for retrieval".into(), claimed_line: None },
            ],
            caveat: None,
        };
        let decisions = tmp.path().join("decisions.json");
        let mut rev_json = serde_json::to_value(&revised_claim).unwrap();
        rev_json["id"] = json!(""); // template users leave the id empty
        fs::write(
            &decisions,
            serde_json::to_string_pretty(&json!([
                { "claim_id": "c1", "action": "rewrite", "revisions": [rev_json], "note": "narrowed" }
            ]))
            .unwrap(),
        )
        .unwrap();

        // Seed the strength cassette for the re-gate call (replay = zero network).
        let catalog = collect_catalog(&reader).unwrap();
        let req = strength_request(
            &CrystalCandidate { items: vec![revised_claim.clone()] },
            &catalog,
        );
        let cache = vault.join(".ovp/cassettes/crystal");
        let ns = req.cache_namespace.as_deref().unwrap();
        let dir = cache.join(ns);
        fs::create_dir_all(&dir).unwrap();
        let reply = ovp_llm::ModelReply {
            model: "canned".into(),
            text: r#"[{"claim_id":"c1r","strength":"supported","evidence_sufficient":true,"rationale":"both quotes state a scarce budget"}]"#.into(),
            stop_reason: ovp_llm::StopReason::EndTurn,
            usage: ovp_llm::Usage { input_tokens: 1, output_tokens: 1 },
        };
        fs::write(
            dir.join(format!("{}.json", ovp_llm::request_key(&req))),
            serde_json::to_string_pretty(&reply).unwrap(),
        )
        .unwrap();

        super::run_apply(super::CrystalReviewSessionApplyArgs {
            vault_root: vault.clone(),
            decisions,
            client_kind: crate::commands::client::ClientKind::Replay,
            cache_dir: None,
            run_id: None,
            title: Some("Test Crystal".into()),
            refresh: false,
            date: None,
        })
        .expect("rewrite session re-gates and writes");

        let ledger = fs::read_to_string(store.join("ledger.jsonl")).unwrap();
        assert_eq!(
            ledger.lines().filter(|l| !l.trim().is_empty()).count(),
            1,
            "one revised claim written durable"
        );
        assert!(ledger.contains("c1r"), "{ledger}");
        let queue = fs::read_to_string(store.join("review.json")).unwrap();
        assert!(!queue.contains("\"c1\""), "reviewed entry retired: {queue}");
    }
}
