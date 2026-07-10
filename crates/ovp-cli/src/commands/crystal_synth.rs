//! `crystal-synth` — M32 turnkey Crystal synthesis (the pilot reproduced as one
//! command). End-to-end, offline by default:
//!
//!   reader packs → units catalog + packs dir (deterministic)
//!     → keyword theme clusters (deterministic)
//!     → deterministic full-coverage sub-batches
//!     → per-batch cross-source synthesis (`crystal_synth/v1`, cassette-replayable)
//!     → grounded filter (drop ungrounded claims via the SAME `lint_candidate`)
//!     → conservative citation-set dedup
//!     → chunked claim-strength verdicts (`crystal_strength/v1`, cassette-replayable)
//!     → durable write (delegates to `crystal_write::write_durable`)
//!     → optional index/console refresh.
//!
//! Every model call goes through `build_client(ClientKind, cache_dir)`; every
//! gate + the durable write reuse the frozen `ovp_domain::crystal` functions, so
//! this command cannot drift from `crystal-lint`/`crystal-write`. It NEVER
//! touches demoted substrate (referents / concept_registry / canonical / moc /
//! knowledge_index / evergreen).

use std::path::PathBuf;

use ovp_domain::crystal::synth::{
    SynthError, build_grounding_index as synth_build_index, cluster_batches, collect_catalog,
    count_durable_provenance, crystal_synth_batch_request, dedup_exact_citation_sets,
    filter_grounded, parse_strength_verdicts, parse_synth_claims, strength_request, write_packs,
};
use ovp_domain::crystal::themes::{ThemesFile, clusters_date_ordered, clusters_from_themes};
use ovp_domain::crystal::{
    ClaimStrengthVerdict, CrystalCandidate, CrystalClaim, CrystalHeader, strength_coverage,
};
use ovp_domain::model_reply::{JsonDefect, json_repair_request, parse_reply_value};
use ovp_domain::vault_layout::VaultLayout;
use ovp_llm::ModelClient;

use crate::CliError;
use crate::commands::client::{ClientKind, build_client};
use crate::commands::crystal_write::{WriteInputs, write_durable};
use crate::commands::{console_cmd, index_cmd};

pub(crate) const MAX_STRENGTH_CLAIMS_PER_CALL: usize = 20;

pub struct CrystalSynthArgs {
    pub reader_dir: Option<PathBuf>,
    pub vault_root: Option<PathBuf>,
    pub work_dir: PathBuf,
    pub store: Option<PathBuf>,
    /// Semantic themes projection for batch grouping. Default:
    /// `<vault-root>/.ovp/crystal/themes.json` when `--vault-root` is set.
    /// Missing file → deterministic date-ordered fallback batches.
    pub themes_file: Option<PathBuf>,
    pub client_kind: ClientKind,
    pub cache_dir: Option<PathBuf>,
    pub max_cases_per_cluster: usize,
    pub max_units_per_case: usize,
    pub run_id: Option<String>,
    pub title: Option<String>,
    pub scope: Option<String>,
    pub not_claiming: Option<String>,
    pub refresh: bool,
    pub date: Option<String>,
    /// Strict CI gate: when true, a run that produced any caveated/review claim
    /// fails loud. Default false — caveated claims route to review.json and the
    /// run succeeds (they are never durable regardless).
    pub strict: bool,
    /// When true, a cluster larger than `max_cases_per_cluster` fails loud
    /// BEFORE any model call (instead of silently excluding the overflow cases
    /// from synthesis). Default false — overflow is warned + recorded in
    /// warnings.json and the run proceeds on the capped slice.
    pub strict_cluster_cap: bool,
}

/// Resolved paths after applying `--vault-root` precedence.
struct Resolved {
    reader_dir: PathBuf,
    store: PathBuf,
    cache_dir: PathBuf,
}

fn resolve_paths(args: &CrystalSynthArgs) -> Resolved {
    let layout = VaultLayout::new();
    let reader_dir = args
        .reader_dir
        .clone()
        .unwrap_or_else(|| match &args.vault_root {
            Some(v) => v.join(layout.reader_root()),
            None => args.work_dir.join("packs-src"),
        });
    let store = args
        .store
        .clone()
        .unwrap_or_else(|| match &args.vault_root {
            Some(v) => v.join(layout.crystal_store_dir()),
            None => args.work_dir.join("store"),
        });
    let cache_dir = args
        .cache_dir
        .clone()
        .unwrap_or_else(|| match &args.vault_root {
            Some(v) => v.join(".ovp/cassettes/crystal"),
            None => args.work_dir.join("cassettes"),
        });
    Resolved {
        reader_dir,
        store,
        cache_dir,
    }
}

fn synth_err(e: SynthError) -> CliError {
    CliError::Io(format!("crystal-synth: {e}"))
}

/// A record of a JSON salvage, surfaced in the run summary.
pub(crate) struct RepairLog {
    pub(crate) stage: String,
    pub(crate) method: String,
}

/// Call the model for `request`, parse via `parse` with tolerant recovery + ONE
/// bounded JSON-repair follow-up (same contract as the reader trunk). Returns the
/// parsed value plus an optional repair note. Fails loud (never silent) when both
/// the parser-local recovery and the repair call cannot yield valid JSON.
pub(crate) fn call_and_parse<T>(
    client: &mut dyn ModelClient,
    request: &ovp_llm::ModelRequest,
    stage: &str,
    parse: impl Fn(&str) -> Result<T, String>,
) -> Result<(T, Option<RepairLog>), CliError> {
    let reply = client
        .call(request)
        .map_err(|e| CliError::Io(format!("crystal-synth: {stage} call failed: {e}")))?;
    match parse_reply_value(&reply.text) {
        Ok((_v, note)) => {
            let parsed = match parse(&reply.text) {
                Ok(p) => p,
                Err(d) => {
                    // Valid JSON the stage parser rejects (e.g. no `claims`
                    // array) pins a rerun just like unparseable JSON — forget
                    // it too so the retry re-asks the model.
                    client.invalidate(request);
                    return Err(CliError::Io(format!("crystal-synth: {stage} parse: {d}")));
                }
            };
            let log = note.map(|_| RepairLog {
                stage: stage.to_string(),
                method: "parser-local: unescaped-backslash".to_string(),
            });
            Ok((parsed, log))
        }
        Err(defect) => {
            // One bounded model repair, re-parsed through the SAME parser.
            let repaired = client
                .call(&json_repair_request(&reply.text))
                .ok()
                .map(|r| r.text);
            match repaired.as_deref().map(&parse) {
                Some(Ok(parsed)) => Ok((
                    parsed,
                    Some(RepairLog {
                        stage: stage.to_string(),
                        method: format!("model-repair (input defect: {defect})"),
                    }),
                )),
                _ => {
                    // Forget the unrecoverable exchange under a recording
                    // cache: a rerun must re-ask the model, not replay the
                    // same unparseable reply forever. No-op for replay/fakes.
                    client.invalidate(request);
                    client.invalidate(&json_repair_request(&reply.text));
                    Err(CliError::Io(format!(
                        "crystal-synth: {stage} JSON unrecoverable: {}",
                        match defect {
                            JsonDefect::Unrecoverable(d) => d,
                            other => other.to_string(),
                        }
                    )))
                }
            }
        }
    }
}

pub fn run(args: CrystalSynthArgs) -> Result<(), CliError> {
    // Validate --refresh prerequisites BEFORE any model calls or store writes,
    // so an invalid flag combination can never partially mutate the ledger.
    if args.refresh && (args.vault_root.is_none() || args.date.is_none()) {
        return Err(CliError::Io(
            "crystal-synth: --refresh requires --vault-root and --date <YYYY-MM-DD>".into(),
        ));
    }
    if args.max_cases_per_cluster == 0 {
        return Err(CliError::Gate(
            "crystal-synth: --max-cases-per-cluster must be greater than 0".into(),
        ));
    }
    if args.max_units_per_case == 0 {
        return Err(CliError::Gate(
            "crystal-synth: --max-units-per-case must be greater than 0".into(),
        ));
    }
    let paths = resolve_paths(&args);
    std::fs::create_dir_all(&args.work_dir).map_err(|e| {
        CliError::Io(format!(
            "creating work dir {}: {e}",
            args.work_dir.display()
        ))
    })?;

    // (a) Collect the units catalog + canonical packs dir. ------------------
    let catalog = collect_catalog(&paths.reader_dir).map_err(synth_err)?;
    let packs_dir = args.work_dir.join("packs");
    write_packs(&packs_dir, &paths.reader_dir, &catalog).map_err(synth_err)?;
    write_json(&args.work_dir.join("units-catalog.json"), &catalog)?;

    // (b) Deterministic synthesis clusters: semantic communities from the
    // themes.json projection when available, else date-ordered cap-size
    // batches (fresh vault / embed feature off / no model). The hardcoded
    // keyword taxonomy is retired. A corrupt themes.json fails loud — synth
    // feeds the durable ledger and must not proceed on a broken projection.
    let themes_path = args.themes_file.clone().or_else(|| {
        args.vault_root
            .as_ref()
            .map(|v| v.join(".ovp/crystal/themes.json"))
    });
    let themes = match &themes_path {
        Some(p) => ThemesFile::load(p).map_err(|e| CliError::Io(format!("crystal-synth: {e}")))?,
        None => None,
    };
    let clusters = match &themes {
        Some(t) => clusters_from_themes(&catalog, t),
        None => {
            eprintln!(
                "crystal-synth: no semantic themes available — run `ovp2 crystal-themes` \
                 (falling back to deterministic date-ordered batches)"
            );
            clusters_date_ordered(&catalog, args.max_cases_per_cluster)
        }
    };

    // Surface degraded inputs BEFORE any model call: (1) cases whose title fell
    // back to the directory name (no reader.md heading / run-status source) —
    // embedding text + theme labels degrade; (2) clusters larger than the cap.
    // Stage 3a no longer excludes the overflow; it records deterministic
    // sub-batches so full coverage is auditably preserved.
    let fallback_title_cases: Vec<String> = catalog
        .cases
        .iter()
        .filter(|(id, c)| c.title.as_str() == id.as_str())
        .map(|(id, _)| id.clone())
        .collect();
    if !fallback_title_cases.is_empty() {
        eprintln!(
            "crystal-synth: WARNING: {} case(s) have no readable title (fell back to the \
             directory name; theming degrades): {}",
            fallback_title_cases.len(),
            fallback_title_cases.join(", ")
        );
    }
    let mut cluster_batching: Vec<serde_json::Value> = Vec::new();
    for cluster in &clusters {
        if cluster.cases.len() > args.max_cases_per_cluster {
            let batches = cluster.cases.len().div_ceil(args.max_cases_per_cluster);
            eprintln!(
                "crystal-synth: WARNING: cluster `{}` has {} case(s) but the cap is {} — \
                 Stage 3a will synthesize all cases across {} deterministic batch(es)",
                cluster.key,
                cluster.cases.len(),
                args.max_cases_per_cluster,
                batches
            );
            cluster_batching.push(serde_json::json!({
                "cluster": cluster.key,
                "cases": cluster.cases.len(),
                "cap": args.max_cases_per_cluster,
                "batches": batches,
                "mode": "split_all_cases",
            }));
        }
    }
    let batches = cluster_batches(&clusters, args.max_cases_per_cluster);
    // Always written (empty on a clean run), so a rerun in the same work dir
    // can never leave a stale warning report behind.
    write_json(
        &args.work_dir.join("warnings.json"),
        &serde_json::json!({
            "fallback_title_cases": fallback_title_cases,
            "cluster_cap_overflow": [],
            "cluster_batching": cluster_batching,
        }),
    )?;
    // Stage 3a interprets the strict cap as a per-request invariant, not a
    // cluster-size gate: every synthesized batch must stay within the cap.
    if args.strict_cluster_cap
        && batches
            .iter()
            .any(|b| b.cases.len() > args.max_cases_per_cluster)
    {
        return Err(CliError::Gate(
            "crystal-synth: internal error: synthesized batch exceeds strict cluster cap".into(),
        ));
    }
    write_json(&args.work_dir.join("synth-batches.json"), &batches)?;

    // (c) Per-batch cross-source synthesis (model, cassette-replayable). -----
    let mut base = build_client(args.client_kind, &paths.cache_dir)?;
    let mut repairs: Vec<RepairLog> = Vec::new();
    let mut all_claims: Vec<CrystalClaim> = Vec::new();
    for batch in &batches {
        let req = crystal_synth_batch_request(&catalog, batch, args.max_units_per_case);
        let (claims, log): (Vec<CrystalClaim>, Option<RepairLog>) =
            call_and_parse(base.as_mut(), &req, "synth", |t| {
                parse_synth_claims(t, &batch.claim_prefix())
            })?;
        if let Some(l) = log {
            repairs.push(l);
        }
        all_claims.extend(claims);
    }
    let candidate = CrystalCandidate { items: all_claims };
    write_json(&args.work_dir.join("candidate.json"), &candidate)?;
    let n_synthesized = candidate.items.len();

    // (d) Grounded filter — drop ungrounded/defective claims (same linter). --
    let index = synth_build_index(&packs_dir).map_err(synth_err)?;
    let (grounded, dropped) = filter_grounded(&candidate, &index);
    write_json(
        &args.work_dir.join("candidate.grounded.pre-dedup.json"),
        &grounded,
    )?;
    let n_dropped = dropped.len();
    let (grounded, deduped) = dedup_exact_citation_sets(&grounded);
    write_json(&args.work_dir.join("candidate.grounded.json"), &grounded)?;
    write_json(&args.work_dir.join("deduped-claims.json"), &deduped)?;

    if grounded.items.is_empty() {
        return Err(CliError::Gate(format!(
            "crystal-synth: no grounded claims survived (synthesized={n_synthesized}, \
             dropped_ungrounded={n_dropped}). Nothing to write."
        )));
    }

    // (e) Chunked claim-strength verdicts (model, cassette-replayable). ------
    let mut verdicts: Vec<ClaimStrengthVerdict> = Vec::new();
    for (idx, chunk) in grounded
        .items
        .chunks(MAX_STRENGTH_CLAIMS_PER_CALL)
        .enumerate()
    {
        let req = strength_request(
            &CrystalCandidate {
                items: chunk.to_vec(),
            },
            &catalog,
        );
        let stage = format!("strength-b{:03}", idx + 1);
        let (chunk_verdicts, log): (Vec<ClaimStrengthVerdict>, Option<RepairLog>) =
            call_and_parse(base.as_mut(), &req, &stage, parse_strength_verdicts)?;
        if let Some(l) = log {
            repairs.push(l);
        }
        verdicts.extend(chunk_verdicts);
    }
    write_json(&args.work_dir.join("strength.json"), &verdicts)?;

    // Fail loud on incomplete coverage — a durable write requires 1:1 verdicts.
    let claim_ids: Vec<String> = grounded.items.iter().map(|c| c.id.clone()).collect();
    let coverage = strength_coverage(&claim_ids, &verdicts);
    if !coverage.complete() {
        return Err(CliError::Gate(format!(
            "crystal-synth: strength verdicts incomplete — missing={:?} duplicate={:?} unknown={:?}. \
             A durable write requires exactly one verdict per grounded claim.",
            coverage.missing, coverage.duplicate, coverage.unknown
        )));
    }

    let durable_provenance = count_durable_provenance(&grounded, &index);

    // (f) Durable write — delegate to the shared crystal-write core. --------
    let header = CrystalHeader {
        title: args.title.clone().unwrap_or_else(|| "Crystal".into()),
        scope: args.scope.clone().unwrap_or_default(),
        not_claiming: args.not_claiming.clone().unwrap_or_default(),
    };
    let outcome = write_durable(WriteInputs {
        candidate: grounded,
        verdicts,
        index,
        store: paths.store.clone(),
        run_id: args.run_id.clone(),
        header,
        processed_review_ids: std::collections::BTreeSet::new(),
    })?;

    // --- Summary (mirrors crystal-write). ---
    println!("crystal-synth: run_id={}", outcome.run_id);
    println!(
        "  collected: {} case(s) → {} cluster(s), {} synth batch(es)",
        catalog.cases.len(),
        clusters.len(),
        batches.len()
    );
    println!(
        "  synthesized {n_synthesized} claim(s); dropped_ungrounded={n_dropped}; deduped={}; durable-provenance={durable_provenance}",
        deduped.len()
    );
    println!(
        "  durable: {} considered, {} newly appended ({} already active)",
        outcome.considered,
        outcome.appended,
        outcome.considered - outcome.appended
    );
    println!(
        "  store: {} active durable claim(s) total",
        outcome.active_total
    );
    println!(
        "  review (NOT durable): {} caveated/reject claim(s)",
        outcome.review
    );
    for r in &repairs {
        println!("  json-repair[{}]: {}", r.stage, r.method);
    }
    println!("  ledger: {}", outcome.ledger_path.display());
    println!("  view:   {}", outcome.crystal_md_path.display());

    // Default: caveated claims route to review.json and the run succeeds.
    // `--strict` turns any caveated/review claim into a loud failure (CI gate).
    if args.strict && outcome.review > 0 {
        return Err(CliError::Gate(format!(
            "crystal-synth: {} caveated/reject claim(s) with --strict. \
             They are in review.json but the run is marked failed.",
            outcome.review
        )));
    }

    // (g) Optional index/console refresh. -----------------------------------
    if args.refresh {
        let vault_root = args
            .vault_root
            .clone()
            .ok_or_else(|| CliError::Io("crystal-synth: --refresh requires --vault-root".into()))?;
        let date = args.date.clone().ok_or_else(|| {
            CliError::Io("crystal-synth: --refresh requires --date <YYYY-MM-DD>".into())
        })?;
        index_cmd::run_index(index_cmd::IndexArgs {
            vault_root: vault_root.clone(),
            date: date.clone(),
        })?;
        console_cmd::run(console_cmd::ConsoleArgs { vault_root, date })?;
    }

    Ok(())
}

fn write_json<T: serde::Serialize>(path: &std::path::Path, v: &T) -> Result<(), CliError> {
    let s = serde_json::to_string_pretty(v)
        .map_err(|e| CliError::Io(format!("serializing {}: {e}", path.display())))?;
    std::fs::write(path, format!("{s}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", path.display())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::synth::{
        cluster_batches, crystal_synth_batch_request, crystal_synth_request,
    };
    use ovp_domain::source_doc::SourceDoc;
    use ovp_domain::units::{Unit, validate};
    use ovp_llm::request_key;

    /// Write a reader pack fixture (units.accepted.json + reader.md).
    fn write_pack(dir: &std::path::Path, case_id: &str, title: &str, body: &str, quotes: &[&str]) {
        let case_dir = dir.join(case_id);
        std::fs::create_dir_all(&case_dir).unwrap();
        let raw: Vec<_> = quotes
            .iter()
            .enumerate()
            .map(|(i, q)| {
                serde_json::json!({
                    "kind": "assertion", "text": format!("t{i}"), "evidence_ref": "p001",
                    "evidence_quote": q, "attribution": "author", "modality": "asserted", "arguments": []
                })
            })
            .collect();
        let ex = validate(
            &raw,
            &SourceDoc::article("T", "https://e/x", None, None, vec![], body),
        );
        let units: Vec<Unit> = ex.accepted().cloned().collect();
        std::fs::write(
            case_dir.join("units.accepted.json"),
            serde_json::to_string_pretty(&units).unwrap(),
        )
        .unwrap();
        std::fs::write(case_dir.join("reader.md"), format!("# {title}\n\nbody\n")).unwrap();
    }

    /// Write a replay cassette for a request under its namespace.
    fn write_cassette(cache_dir: &std::path::Path, req: &ovp_llm::ModelRequest, reply_text: &str) {
        let ns = req.cache_namespace.as_deref().unwrap();
        let dir = cache_dir.join(ns);
        std::fs::create_dir_all(&dir).unwrap();
        let key = request_key(req);
        let reply = ovp_llm::ModelReply {
            model: "canned".into(),
            text: reply_text.to_string(),
            stop_reason: ovp_llm::StopReason::EndTurn,
            usage: ovp_llm::Usage {
                input_tokens: 1,
                output_tokens: 1,
            },
        };
        std::fs::write(
            dir.join(format!("{key}.json")),
            serde_json::to_string_pretty(&reply).unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn e2e_replay_produces_durable_and_is_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        // Two cases in the "memory" bucket, both citing verbatim → cross-source.
        // Titles avoid "agent" so both land in the memory bucket (single cluster).
        write_pack(
            &reader,
            "m18-01",
            "Working memory systems",
            "Memory is scarce working memory in systems. It must be curated.",
            &[
                "Memory is scarce working memory in systems.",
                "It must be curated.",
            ],
        );
        write_pack(
            &reader,
            "m18-02",
            "Context and retrieval",
            "Context windows are a scarce budget for retrieval.",
            &["Context windows are a scarce budget for retrieval."],
        );

        let work = tmp.path().join("work");
        let cache = work.join("cassettes");
        std::fs::create_dir_all(&cache).unwrap();

        // Rebuild the catalog + clusters exactly as run() will, to key
        // cassettes. No themes.json here → date-ordered fallback batches.
        let catalog = collect_catalog(&reader).unwrap();
        let clusters = clusters_date_ordered(&catalog, 16);
        assert_eq!(clusters.len(), 1);
        assert_eq!(clusters[0].key, "batch-001");
        let u01 = catalog.cases["m18-01"].units[0].unit_id.clone();
        let u02 = catalog.cases["m18-02"].units[0].unit_id.clone();

        // Synthesis cassette: one cross-source claim citing both cases verbatim.
        let synth_req = crystal_synth_request(&catalog, &clusters[0], 16, 22);
        let synth_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Memory and context are treated as a scarce budget across agent systems.","theme":"memory","citations":[
                {{"case_id":"m18-01","unit_id":"{u01}","quote":"scarce working memory in systems"}},
                {{"case_id":"m18-02","unit_id":"{u02}","quote":"scarce budget for retrieval"}}
            ]}}]}}"#
        );
        write_cassette(&cache, &synth_req, &synth_reply);

        // The grounded candidate is deterministic → build the strength request.
        let candidate = CrystalCandidate {
            items: parse_synth_claims(&synth_reply, "batch-001").unwrap(),
        };
        let idx = synth_build_index(&{
            let p = work.join("packs");
            write_packs(&p, &reader, &catalog).unwrap();
            p
        })
        .unwrap();
        let (grounded, _) = filter_grounded(&candidate, &idx);
        assert_eq!(grounded.items.len(), 1, "cross-source claim is grounded");
        let strength_req = strength_request(&grounded, &catalog);
        let strength_reply = format!(
            r#"[{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"both quotes state a scarce budget"}}]"#,
            grounded.items[0].id
        );
        write_cassette(&cache, &strength_req, &strength_reply);

        let store = work.join("store");
        let mk_args = || CrystalSynthArgs {
            reader_dir: Some(reader.clone()),
            vault_root: None,
            work_dir: work.clone(),
            store: Some(store.clone()),
            themes_file: None,
            client_kind: ClientKind::Replay,
            cache_dir: Some(cache.clone()),
            max_cases_per_cluster: 16,
            max_units_per_case: 22,
            run_id: None,
            title: Some("Test Crystal".into()),
            scope: None,
            not_claiming: None,
            refresh: false,
            date: None,
            strict: false,
            strict_cluster_cap: false,
        };

        // First run: writes candidate.json + a durable ledger line + crystal.md.
        run(mk_args()).expect("first run ok");
        assert!(work.join("candidate.json").exists());
        assert!(work.join("strength.json").exists());
        assert!(store.join("crystal.md").exists());
        let ledger = std::fs::read_to_string(store.join("ledger.jsonl")).unwrap();
        let lines1 = ledger.lines().filter(|l| !l.trim().is_empty()).count();
        assert_eq!(lines1, 1, "exactly one durable claim written");

        // Second run: idempotent — no new ledger lines.
        run(mk_args()).expect("second run ok");
        let ledger2 = std::fs::read_to_string(store.join("ledger.jsonl")).unwrap();
        let lines2 = ledger2.lines().filter(|l| !l.trim().is_empty()).count();
        assert_eq!(
            lines2, 1,
            "re-run appends nothing (idempotent by claim_key)"
        );
    }

    #[test]
    fn e2e_split_batches_cover_all_cases() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        write_pack(
            &reader,
            "m18-01",
            "Working memory systems one",
            "Memory case one says working memory is scarce.",
            &["working memory is scarce"],
        );
        write_pack(
            &reader,
            "m18-02",
            "Working memory systems two",
            "Memory case two says context must be curated.",
            &["context must be curated"],
        );
        write_pack(
            &reader,
            "m18-03",
            "Working memory systems three",
            "Memory case three says retrieval should stay bounded.",
            &["retrieval should stay bounded"],
        );
        write_pack(
            &reader,
            "m18-04",
            "Working memory systems four",
            "Memory case four says memory pressure creates review work.",
            &["memory pressure creates review work"],
        );

        let work = tmp.path().join("work");
        let cache = work.join("cassettes");
        std::fs::create_dir_all(&cache).unwrap();
        let catalog = collect_catalog(&reader).unwrap();
        // Date-ordered fallback pre-chunks at the cap, so each cluster is one
        // batch — four cases at cap=2 still means two synth calls.
        let clusters = clusters_date_ordered(&catalog, 2);
        assert_eq!(clusters.len(), 2);
        let batches = cluster_batches(&clusters, 2);
        assert_eq!(
            batches.len(),
            2,
            "cap=2 should split four cases into two synth calls"
        );

        let mut all_claims = Vec::new();
        for batch in &batches {
            let ids = &batch.cases;
            let u0 = catalog.cases[&ids[0]].units[0].unit_id.clone();
            let u1 = catalog.cases[&ids[1]].units[0].unit_id.clone();
            let synth_req = crystal_synth_batch_request(&catalog, batch, 22);
            let synth_reply = format!(
                r#"{{"claims":[{{"id":"1","claim":"Batch {} ties two memory cases into one bounded-memory claim.","theme":"memory","citations":[
                    {{"case_id":"{}","unit_id":"{}","quote":"{}"}},
                    {{"case_id":"{}","unit_id":"{}","quote":"{}"}}
                ]}}]}}"#,
                batch.batch_index + 1,
                ids[0],
                u0,
                catalog.cases[&ids[0]].units[0].quote,
                ids[1],
                u1,
                catalog.cases[&ids[1]].units[0].quote
            );
            write_cassette(&cache, &synth_req, &synth_reply);
            all_claims.extend(parse_synth_claims(&synth_reply, &batch.claim_prefix()).unwrap());
        }

        let p = work.join("packs");
        write_packs(&p, &reader, &catalog).unwrap();
        let idx = synth_build_index(&p).unwrap();
        let (grounded, _) = filter_grounded(&CrystalCandidate { items: all_claims }, &idx);
        assert_eq!(grounded.items.len(), 2);
        let strength_req = strength_request(&grounded, &catalog);
        let strength_reply = format!(
            r#"[{}]"#,
            grounded
                .items
                .iter()
                .map(|c| format!(
                    r#"{{"claim_id":"{}","strength":"supported","evidence_sufficient":true,"rationale":"two cited cases support the bounded-memory claim"}}"#,
                    c.id
                ))
                .collect::<Vec<_>>()
                .join(",")
        );
        write_cassette(&cache, &strength_req, &strength_reply);

        let store = work.join("store");
        run(CrystalSynthArgs {
            reader_dir: Some(reader.clone()),
            vault_root: None,
            work_dir: work.clone(),
            store: Some(store.clone()),
            themes_file: None,
            client_kind: ClientKind::Replay,
            cache_dir: Some(cache.clone()),
            max_cases_per_cluster: 2,
            max_units_per_case: 22,
            run_id: None,
            title: Some("Split Test".into()),
            scope: None,
            not_claiming: None,
            refresh: false,
            date: None,
            strict: false,
            strict_cluster_cap: true,
        })
        .expect("split batches run");

        let ledger = std::fs::read_to_string(store.join("ledger.jsonl")).unwrap();
        assert_eq!(ledger.lines().filter(|l| !l.trim().is_empty()).count(), 2);
        let synth_batches = std::fs::read_to_string(work.join("synth-batches.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&synth_batches).unwrap();
        assert_eq!(v.as_array().unwrap().len(), 2);
    }

    #[test]
    fn incomplete_strength_fails_loud() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        write_pack(
            &reader,
            "m18-01",
            "Working memory systems",
            "Memory is scarce working memory in systems.",
            &["Memory is scarce working memory in systems."],
        );
        write_pack(
            &reader,
            "m18-02",
            "Context retrieval",
            "Context windows are a scarce budget for retrieval.",
            &["Context windows are a scarce budget for retrieval."],
        );

        let work = tmp.path().join("work");
        let cache = work.join("cassettes");
        std::fs::create_dir_all(&cache).unwrap();
        let catalog = collect_catalog(&reader).unwrap();
        let clusters = clusters_date_ordered(&catalog, 16);
        assert_eq!(clusters.len(), 1, "both cases in one fallback batch");
        let u01 = catalog.cases["m18-01"].units[0].unit_id.clone();
        let u02 = catalog.cases["m18-02"].units[0].unit_id.clone();
        let synth_req = crystal_synth_request(&catalog, &clusters[0], 16, 22);
        let synth_reply = format!(
            r#"{{"claims":[{{"id":"1","claim":"Both treat resources as scarce.","theme":"memory","citations":[
                {{"case_id":"m18-01","unit_id":"{u01}","quote":"scarce working memory"}},
                {{"case_id":"m18-02","unit_id":"{u02}","quote":"scarce budget for retrieval"}}
            ]}}]}}"#
        );
        write_cassette(&cache, &synth_req, &synth_reply);
        // Grounded candidate → strength request, but return an EMPTY verdict set.
        let candidate = CrystalCandidate {
            items: parse_synth_claims(&synth_reply, "batch-001").unwrap(),
        };
        let p = work.join("packs");
        write_packs(&p, &reader, &catalog).unwrap();
        let idx = synth_build_index(&p).unwrap();
        let (grounded, _) = filter_grounded(&candidate, &idx);
        write_cassette(&cache, &strength_request(&grounded, &catalog), "[]");

        let err = run(CrystalSynthArgs {
            reader_dir: Some(reader.clone()),
            vault_root: None,
            work_dir: work.clone(),
            store: Some(work.join("store")),
            themes_file: None,
            client_kind: ClientKind::Replay,
            cache_dir: Some(cache.clone()),
            max_cases_per_cluster: 16,
            max_units_per_case: 22,
            run_id: None,
            title: None,
            scope: None,
            not_claiming: None,
            refresh: false,
            date: None,
            strict: false,
            strict_cluster_cap: false,
        })
        .unwrap_err();
        assert!(
            matches!(err, CliError::Gate(_)),
            "incomplete strength must fail loud, got {err:?}"
        );
    }

    /// Minimal args for the warning-path tests: replay client, no cassettes.
    fn bare_args(reader: &std::path::Path, work: &std::path::Path) -> CrystalSynthArgs {
        CrystalSynthArgs {
            reader_dir: Some(reader.to_path_buf()),
            vault_root: None,
            work_dir: work.to_path_buf(),
            store: Some(work.join("store")),
            themes_file: None,
            client_kind: ClientKind::Replay,
            cache_dir: Some(work.join("cassettes")),
            max_cases_per_cluster: 16,
            max_units_per_case: 22,
            run_id: None,
            title: None,
            scope: None,
            not_claiming: None,
            refresh: false,
            date: None,
            strict: false,
            strict_cluster_cap: false,
        }
    }

    #[test]
    fn themes_json_groups_batches_and_overflow_is_recorded() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        // Two cases in the SAME semantic community with cap=1 → two
        // full-coverage batches, not one synthesized case plus one exclusion.
        write_pack(
            &reader,
            "m18-01",
            "Working memory systems",
            "Memory is scarce working memory in systems.",
            &["Memory is scarce working memory in systems."],
        );
        write_pack(
            &reader,
            "m18-02",
            "Context and retrieval",
            "Context windows are a scarce budget for retrieval.",
            &["Context windows are a scarce budget for retrieval."],
        );
        let work = tmp.path().join("work");
        std::fs::create_dir_all(&work).unwrap();
        let themes_path = work.join("themes.json");
        std::fs::write(
            &themes_path,
            serde_json::json!({
                "schema": "ovp.themes/v1",
                "model": "test-model",
                "params": {"k": 10, "cosine_threshold": 0.5, "resolution": 1.5,
                            "seed": 42, "text_prefix": "", "head_chars": 1500},
                "generated_from": "deadbeef",
                "packs": {"m18-01": 0, "m18-02": 0},
                "communities": [{"id": 0, "label": "Memory & context budgets",
                                  "label_zh": "记忆与上下文预算",
                                  "keywords": ["memory"], "size": 2}]
            })
            .to_string(),
        )
        .unwrap();
        let mut args = bare_args(&reader, &work);
        args.themes_file = Some(themes_path);
        args.max_cases_per_cluster = 1;
        args.strict_cluster_cap = true;
        // No cassettes exist, so the run errors at the first synth call. The
        // point is that Stage 3a no longer gates merely because the community
        // exceeds the per-request cap; it records deterministic batches first.
        let err = run(args).unwrap_err();
        assert!(
            matches!(err, CliError::Io(_)),
            "expected replay miss, got {err:?}"
        );
        // The split is recorded for auditability before the model call.
        let w = std::fs::read_to_string(work.join("warnings.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&w).unwrap();
        assert_eq!(v["cluster_cap_overflow"].as_array().unwrap().len(), 0);
        let batching = v["cluster_batching"].as_array().unwrap();
        assert_eq!(batching.len(), 1);
        assert_eq!(batching[0]["cluster"], "t000", "semantic community key");
        assert_eq!(batching[0]["batches"], serde_json::json!(2));
        let b = std::fs::read_to_string(work.join("synth-batches.json")).unwrap();
        let batches: serde_json::Value = serde_json::from_str(&b).unwrap();
        assert_eq!(batches.as_array().unwrap().len(), 2);
        assert_eq!(batches[0]["theme"], "Memory & context budgets");

        // A corrupt themes.json fails loud BEFORE any model call.
        let mut args = bare_args(&reader, &work);
        let corrupt = work.join("themes-corrupt.json");
        std::fs::write(&corrupt, "not json").unwrap();
        args.themes_file = Some(corrupt);
        let err = run(args).unwrap_err();
        assert!(matches!(err, CliError::Io(_)), "corrupt projection: {err:?}");
    }

    #[test]
    fn no_themes_falls_back_to_date_ordered_batches_with_note() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        write_pack(
            &reader,
            "m18-01",
            "Working memory systems",
            "Memory is scarce working memory in systems.",
            &["Memory is scarce working memory in systems."],
        );
        write_pack(
            &reader,
            "m18-02",
            "Context and retrieval",
            "Context windows are a scarce budget for retrieval.",
            &["Context windows are a scarce budget for retrieval."],
        );
        let work = tmp.path().join("work");
        let mut args = bare_args(&reader, &work);
        args.max_cases_per_cluster = 1;
        // Fallback pre-chunks at the cap: two date-ordered single-case
        // clusters, no overflow batching, everything still covered.
        let err = run(args).unwrap_err();
        assert!(matches!(err, CliError::Io(_)), "replay miss: {err:?}");
        let b = std::fs::read_to_string(work.join("synth-batches.json")).unwrap();
        let batches: serde_json::Value = serde_json::from_str(&b).unwrap();
        assert_eq!(batches.as_array().unwrap().len(), 2);
        assert_eq!(batches[0]["key"], "batch-001");
        assert_eq!(batches[1]["key"], "batch-002");
        let w = std::fs::read_to_string(work.join("warnings.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&w).unwrap();
        assert_eq!(v["cluster_batching"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn title_fallback_is_recorded_in_warnings() {
        let tmp = tempfile::tempdir().unwrap();
        let reader = tmp.path().join("reader");
        write_pack(
            &reader,
            "0951c213",
            "ignored",
            "A body sentence here.",
            &["A body sentence here."],
        );
        // Strip reader.md so the title falls back to the directory name — the
        // exact degradation that collapsed the M32 live repro's first attempt.
        std::fs::remove_file(reader.join("0951c213").join("reader.md")).unwrap();
        let work = tmp.path().join("work");
        // No cassettes → the run errors at the synth call, but warnings.json
        // must already be on disk by then.
        let err = run(bare_args(&reader, &work)).unwrap_err();
        assert!(
            matches!(err, CliError::Io(_)),
            "expected cassette miss, got {err:?}"
        );
        let w = std::fs::read_to_string(work.join("warnings.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&w).unwrap();
        assert_eq!(v["fallback_title_cases"], serde_json::json!(["0951c213"]));
    }
}
