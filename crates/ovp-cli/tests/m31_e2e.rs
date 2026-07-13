//! M31 end-to-end dogfood over the REAL `ovp2` binary on a fixture vault:
//! pinboard fixture → intake → daily reader (replay over pre-seeded cassettes)
//! → lifecycle moves → run report → index → console → crystal-write into the
//! vault-local store → find. Then: idempotent rerun, and the failure → retry →
//! blocked path.
//!
//! No network: cassettes are pre-seeded by running the real request builders
//! through a Record-mode `CachedModelClient` wrapping a canned client.

use std::path::{Path, PathBuf};
use std::process::Command;

use ovp_domain::reader::card_model_request;
use ovp_domain::units::{
    critic_model_request, extract_units, read_source_from_path, unit_model_request, Unit,
};
use ovp_domain::SourceDoc;
use ovp_index::{read_evidence, read_index};
use ovp_llm::{
    CacheMode, CachedModelClient, CallError, ModelClient, ModelReply, ModelRequest, StopReason,
    Usage,
};
use ovp_memory::ask::{ask_with_evidence, AskArgs};

const DATE: &str = "2026-06-09";

const CLIP_BODY: &str = "A chunk is a structurally neutral container. It knows nothing about \
ownership, provenance, or trust. Grounded units keep verbatim quotes so every claim in the \
reader pack stays auditable end to end, which is the entire point of the truth layer.";

const PIN_BODY: &str = "Benchmark maxxing is for augmenting experts. Real product value comes \
from grounded daily workflows: capture, normalize, read with provenance, and review in a \
console that always links back to verbatim evidence rather than free-floating summaries.";

const CLIP_QUOTE: &str = "A chunk is a structurally neutral container.";
const PIN_QUOTE: &str = "Benchmark maxxing is for augmenting experts.";

struct Canned(String);
impl ModelClient for Canned {
    fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
        Ok(ModelReply {
            model: "canned".into(),
            text: self.0.clone(),
            stop_reason: StopReason::EndTurn,
            usage: Usage { input_tokens: 1, output_tokens: 1 },
        })
    }
}

fn units_reply(quote: &str, text: &str) -> String {
    format!(
        r#"{{"units":[{{"kind":"assertion","text":"{text}","evidence_ref":"p001","evidence_quote":"{quote}","attribution":"author","modality":"asserted","arguments":[]}}]}}"#
    )
}

/// Record cassettes for one source: base units, critic (no-op), cards citing
/// the accepted unit. Uses the EXACT request builders the binary uses, so the
/// replay run hits every cassette.
fn seed_cassettes(cache_dir: &Path, source: &SourceDoc, quote: &str, text: &str) {
    let base_reply = units_reply(quote, text);
    let mut rec =
        CachedModelClient::new(Canned(base_reply.clone()), cache_dir, "seed", CacheMode::Record)
            .unwrap();
    rec.call(&unit_model_request(source)).unwrap();

    let ex = extract_units(&base_reply, source);
    let mut rec = CachedModelClient::new(
        Canned(r#"{"faithfulness_defects":[],"coverage_gaps":[]}"#.into()),
        cache_dir,
        "seed",
        CacheMode::Record,
    )
    .unwrap();
    rec.call(&critic_model_request(source, &ex.units)).unwrap();

    let accepted: Vec<Unit> = ex.accepted().cloned().collect();
    let cards_reply = format!(
        r#"{{"cards":[{{"title":"Card for {t}","content":"{text}","cited_unit_ids":["{id}"]}}]}}"#,
        t = source.title,
        id = accepted[0].id,
    );
    let mut rec =
        CachedModelClient::new(Canned(cards_reply), cache_dir, "seed", CacheMode::Record).unwrap();
    rec.call(&card_model_request(&accepted)).unwrap();
}

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_ovp2"))
}

fn run_ok(cmd: &mut Command) -> String {
    run_ok_full(cmd).0
}

fn run_ok_full(cmd: &mut Command) -> (String, String) {
    let out = cmd.output().expect("binary runs");
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
    assert!(out.status.success(), "expected success.\nstdout:\n{stdout}\nstderr:\n{stderr}");
    (stdout, stderr)
}

fn run_fail(cmd: &mut Command) -> (String, String) {
    let out = cmd.output().expect("binary runs");
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
    assert!(!out.status.success(), "expected non-zero exit.\nstdout:\n{stdout}");
    (stdout, stderr)
}

fn clip_note(title: &str, url: &str, body: &str) -> String {
    format!("---\ntitle: \"{title}\"\nsource: \"{url}\"\npublished: 2026-06-01\ncreated: 2026-06-08\ntags:\n  - \"clippings\"\n---\n{body}\n")
}

fn md_files(dir: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    if dir.is_dir() {
        let mut stack = vec![dir.to_path_buf()];
        while let Some(d) = stack.pop() {
            for entry in std::fs::read_dir(&d).unwrap().flatten() {
                let p = entry.path();
                if entry.file_name().to_string_lossy().starts_with('.') {
                    continue;
                }
                if p.is_dir() {
                    stack.push(p);
                } else if p.extension().is_some_and(|e| e == "md") {
                    found.push(p);
                }
            }
        }
    }
    found.sort();
    found
}

/// First-sync flood guard through the REAL binary: an unfiltered pinboard
/// capture of >500 NEW bookmarks aborts `daily` and `pinboard-sync` before
/// any write; `--pinboard-since`/`--pinboard-max` (daily) and
/// `--since`/`--max`/`--yes-all` (pinboard-sync) are plumbed through; dry
/// runs report instead of aborting.
#[test]
fn daily_and_pinboard_sync_inherit_first_sync_flood_guard() {
    let tmp = tempfile::tempdir().unwrap();
    let vault = tmp.path().join("vault");
    // A real vault always has the raw inbox; the plan phase requires it.
    std::fs::create_dir_all(vault.join("50-Inbox/01-Raw")).unwrap();

    // 503 bare bookmarks (all in 2020) — a classic old-account first sync.
    let export = tmp.path().join("pinboard-flood.json");
    let posts: Vec<serde_json::Value> = (0..503)
        .map(|i| {
            serde_json::json!({
                "href": format!("https://e.x/p{i}"),
                "description": format!("Post {i}"),
                "extended": "",
                "time": format!("2020-01-01T{:02}:{:02}:00Z", i / 60, i % 60),
                "tags": ""
            })
        })
        .collect();
    std::fs::write(&export, serde_json::to_string(&posts).unwrap()).unwrap();

    // Unfiltered daily: the pinboard phase fails LOUDLY, nothing is written.
    let (_stdout, stderr) = run_fail(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-guard",
        "--pinboard-fixture", export.to_str().unwrap(),
    ]));
    assert!(stderr.contains("pinboard-sync guard"), "{stderr}");
    assert!(stderr.contains("503 NEW bookmarks"), "{stderr}");
    assert!(!vault.join("50-Inbox/02-Pinboard").exists(), "abort before any write");
    assert!(!vault.join(".ovp/pinboard-sync.jsonl").exists());

    // Dry run is exempt: reports the would-be abort, writes nothing.
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-guard-dry",
        "--pinboard-fixture", export.to_str().unwrap(),
        "--dry-run",
    ]));
    assert!(stdout.contains("a REAL run would ABORT at the pinboard phase"), "{stdout}");
    assert!(!vault.join("50-Inbox/02-Pinboard").exists());

    // --pinboard-max passthrough: only the 2 newest are materialized.
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-guard-max",
        "--pinboard-fixture", export.to_str().unwrap(),
        "--pinboard-max", "2",
    ]));
    assert!(stdout.contains("pinboard: 503 fetched, 2 new note(s)"), "{stdout}");
    assert_eq!(md_files(&vault.join("50-Inbox/02-Pinboard")).len(), 2);

    // --pinboard-since passthrough: everything predates the cutoff → 0 new.
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-guard-since",
        "--pinboard-fixture", export.to_str().unwrap(),
        "--pinboard-since", "2021-01-01",
    ]));
    assert!(stdout.contains("pinboard: 503 fetched, 0 new note(s)"), "{stdout}");
    assert_eq!(md_files(&vault.join("50-Inbox/02-Pinboard")).len(), 2, "nothing added");

    // pinboard-sync: same guard, same message.
    let (_stdout, stderr) = run_fail(bin().args([
        "pinboard-sync",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "pin-guard",
        "--fixture", export.to_str().unwrap(),
    ]));
    assert!(stderr.contains("pinboard-sync guard"), "{stderr}");
    assert!(stderr.contains("501 NEW bookmarks"), "2 already known: {stderr}");

    // pinboard-sync --max 1 narrows; --yes-all deliberately takes the rest.
    let stdout = run_ok(bin().args([
        "pinboard-sync",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "pin-max",
        "--fixture", export.to_str().unwrap(),
        "--max", "1",
    ]));
    assert!(stdout.contains("done: 503 fetched, 1 new, 2 known"), "{stdout}");
    let stdout = run_ok(bin().args([
        "pinboard-sync",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "pin-yes-all",
        "--fixture", export.to_str().unwrap(),
        "--yes-all",
    ]));
    assert!(stdout.contains("done: 503 fetched, 500 new, 3 known"), "{stdout}");
    assert_eq!(md_files(&vault.join("50-Inbox/02-Pinboard")).len(), 503);
}

#[test]
fn full_daily_workflow_capture_to_console_with_crystal_and_retry() {
    let tmp = tempfile::tempdir().unwrap();
    let vault = tmp.path().join("vault");
    let cache_dir = tmp.path().join("cassettes");
    std::fs::create_dir_all(vault.join("Clippings")).unwrap();

    // --- Fixtures: one rich clipping + a pinboard export (rich + bare). ---
    let clip_path = vault.join("Clippings/The Chunk Problem.md");
    std::fs::write(&clip_path, clip_note("The Chunk Problem", "https://e.x/chunk", CLIP_BODY)).unwrap();

    let export = tmp.path().join("pinboard-export.json");
    std::fs::write(&export, format!(r#"[
      {{"href":"https://e.x/benchmaxx","description":"Benchmark Maxxing","extended":"{PIN_BODY}","time":"2026-06-02T08:00:00Z","tags":"ai eval"}},
      {{"href":"https://e.x/bare","description":"Bare Bookmark","extended":"just a link","time":"2026-06-03T09:00:00Z","tags":""}}
    ]"#)).unwrap();

    // --- Seed cassettes from the EXACT SourceDocs the binary will read. ---
    // The clipping is moved (bytes unchanged) by intake, so parse it directly.
    let clip_doc = read_source_from_path(&clip_path).unwrap();
    seed_cassettes(&cache_dir, &clip_doc, CLIP_QUOTE, "A chunk is structurally neutral.");
    // The pinboard note is materialized by sync; render it in a scratch vault
    // via the same library path the binary uses, then parse THAT file.
    {
        let scratch = tmp.path().join("scratch-vault");
        std::fs::create_dir_all(&scratch).unwrap();
        let cfg = ovp_intake::IntakeConfig::new(scratch.clone(), DATE.into(), "seed".into());
        let mut fetch = ovp_intake::FixturePinboardFetch::new(&export);
        let out =
            ovp_intake::sync_pinboard(&cfg, &mut fetch, false, &Default::default()).unwrap();
        let rich = out.new_notes.iter().find(|r| r.url.contains("benchmaxx")).unwrap();
        let doc = read_source_from_path(&scratch.join(&rich.to)).unwrap();
        seed_cassettes(&cache_dir, &doc, PIN_QUOTE, "Benchmark maxxing augments experts.");
    }

    // === Run 1: the full daily loop. ===
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-e2e",
        "--pinboard-fixture", export.to_str().unwrap(),
        "--cache-dir", cache_dir.to_str().unwrap(),
    ]));
    assert!(stdout.contains("pinboard: 2 fetched, 2 new"), "{stdout}");
    assert!(stdout.contains("intake:"), "{stdout}");
    assert!(stdout.contains("done: 2 processed, 0 failed"), "{stdout}");

    // Product state: 2 packs; raw inbox drained; processed dir has both; the
    // bare bookmark stays in 02-Pinboard flagged needs-content.
    let packs = std::fs::read_dir(vault.join("40-Resources/Reader")).unwrap().count();
    assert_eq!(packs, 2);
    assert!(md_files(&vault.join("50-Inbox/01-Raw")).is_empty(), "raw queue drained");
    assert_eq!(md_files(&vault.join("50-Inbox/03-Processed")).len(), 2);
    assert_eq!(md_files(&vault.join("50-Inbox/02-Pinboard")).len(), 1, "bare bookmark left");
    for state in [
        ".ovp/daily-runs.jsonl", ".ovp/intake.jsonl", ".ovp/pinboard-sync.jsonl",
        ".ovp/reports/daily-e2e.json", ".ovp/index/index.json", ".ovp/index/evidence.json",
        ".ovp/console/index.html", "60-Logs/pipeline.jsonl",
    ] {
        assert!(vault.join(state).exists(), "missing {state}");
    }
    let console = std::fs::read_to_string(vault.join(".ovp/console/index.html")).unwrap();
    assert!(console.contains("The Chunk Problem"), "console shows sources");
    assert!(console.contains("Benchmark Maxxing"));
    assert!(console.contains("待补内容"), "needs-content surfaced bilingually");

    // P1 provenance: the daily path stamps the wall-clock instant AND passes
    // its own run id through — a stale projection can no longer render like a
    // fresh one, and the number's producer is always named.
    let idx = read_index(&vault).unwrap();
    assert_eq!(idx.run_id.as_deref(), Some("daily-e2e"), "daily run id kept");
    let built = idx.built_at.expect("daily path stamps built_at");
    assert!(built.starts_with(|c: char| c.is_ascii_digit()) && built.contains('T'), "RFC3339: {built}");

    // === Run 2: idempotence. Same inputs → nothing new. ===
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-e2e-2",
        "--pinboard-fixture", export.to_str().unwrap(),
        "--cache-dir", cache_dir.to_str().unwrap(),
    ]));
    assert!(stdout.contains("pinboard: 2 fetched, 0 new"), "{stdout}");
    assert!(stdout.contains("plan: 0 new source(s)"), "{stdout}");
    assert!(stdout.contains("done: 0 processed, 0 failed"), "{stdout}");
    let daily_ledger = std::fs::read_to_string(vault.join(".ovp/daily-runs.jsonl")).unwrap();
    assert_eq!(daily_ledger.lines().count(), 2, "no new attempts on rerun");
    assert_eq!(std::fs::read_dir(vault.join("40-Resources/Reader")).unwrap().count(), 2);

    // === Crystal: author a 2-source candidate over the packs, write to the
    // vault-local store through the REAL gate, and see it in console + find. ===
    let reader_root = vault.join("40-Resources/Reader");
    let mut cases: Vec<(String, String, String)> = Vec::new(); // (case_id, unit_id, quote)
    for entry in std::fs::read_dir(&reader_root).unwrap().flatten() {
        let case_id = entry.file_name().to_string_lossy().into_owned();
        let units: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(entry.path().join("units.accepted.json")).unwrap(),
        )
        .unwrap();
        let u = &units.as_array().unwrap()[0];
        cases.push((
            case_id,
            u["id"].as_str().unwrap().to_string(),
            u["evidence"]["quote"].as_str().unwrap().to_string(),
        ));
    }
    assert_eq!(cases.len(), 2);
    let candidate = serde_json::json!({
        "items": [{
            "id": "e2e-1",
            "claim": "Grounded reading and capture hygiene are both required for a daily knowledge workflow.",
            "theme": "daily-workflow",
            "citations": cases.iter().map(|(case, unit, quote)| serde_json::json!({
                "case_id": case, "unit_id": unit, "quote": quote
            })).collect::<Vec<_>>()
        }]
    });
    let strength = serde_json::json!([{
        "claim_id": "e2e-1", "strength": "supported",
        "evidence_sufficient": true, "rationale": "e2e fixture"
    }]);
    let candidate_path = tmp.path().join("candidate.json");
    let strength_path = tmp.path().join("strength.json");
    std::fs::write(&candidate_path, candidate.to_string()).unwrap();
    std::fs::write(&strength_path, strength.to_string()).unwrap();

    run_ok(bin().args([
        "crystal-write",
        "--candidate", candidate_path.to_str().unwrap(),
        "--packs-dir", reader_root.to_str().unwrap(),
        "--strength", strength_path.to_str().unwrap(),
        "--store", vault.join(".ovp/crystal").to_str().unwrap(),
        "--run-id", "crystal-e2e",
    ]));
    assert!(vault.join(".ovp/crystal/ledger.jsonl").exists());

    let stdout = run_ok(bin().args([
        "console", "--vault-root", vault.to_str().unwrap(), "--date", DATE,
    ]));
    assert!(stdout.contains("durable=1"), "{stdout}");
    let console = std::fs::read_to_string(vault.join(".ovp/console/index.html")).unwrap();
    assert!(console.contains("e2e-1"), "durable claim on console");
    assert!(console.contains("持久化"), "bilingual durable pill");

    // P1 provenance on the standalone console path: it rebuilds the index with
    // a `console-<built_at>` producer marker (never a silently-None run_id) and
    // stamps the wall-clock instant.
    let console_idx = read_index(&vault).unwrap();
    let console_built = console_idx.built_at.clone().expect("console path stamps built_at");
    assert_eq!(
        console_idx.run_id.as_deref(),
        Some(format!("console-{console_built}").as_str()),
        "console path synthesizes a console-<built_at> marker",
    );

    let stdout = run_ok(bin().args([
        "find", "--vault-root", vault.to_str().unwrap(),
        "--kind", "claims", "--status", "durable",
    ]));
    assert!(stdout.contains("e2e-1"), "{stdout}");
    let stdout = run_ok(bin().args([
        "find", "--vault-root", vault.to_str().unwrap(), "chunk",
    ]));
    assert!(stdout.contains("The Chunk Problem"), "{stdout}");
    let stdout = run_ok(bin().args([
        "find", "--vault-root", vault.to_str().unwrap(),
        "--kind", "cards", "structurally neutral",
    ]));
    assert!(stdout.contains("[card") && stdout.contains("Card for The Chunk Problem"), "{stdout}");
    let stdout = run_ok(bin().args([
        "find", "--vault-root", vault.to_str().unwrap(),
        "--kind", "units", "structurally neutral",
    ]));
    assert!(stdout.contains("[unit") && stdout.contains("A chunk is structurally neutral"), "{stdout}");

    let ask_question = "What does OVP say about structurally neutral chunks?";
    let model = read_index(&vault).unwrap();
    let evidence = read_evidence(&vault).unwrap();
    let cited_unit_id = evidence
        .units
        .iter()
        .find(|unit| unit.quote == CLIP_QUOTE)
        .map(|unit| unit.id.clone())
        .expect("clip unit evidence");
    let mut rec = CachedModelClient::new(
        Canned(format!("OVP ask uses evidence cards and units [unit:{cited_unit_id}].")),
        &cache_dir,
        "seed",
        CacheMode::Record,
    )
    .unwrap();
    ask_with_evidence(
        &model,
        &evidence,
        &mut rec,
        &AskArgs { question: ask_question.into(), ..Default::default() },
        &vault,
    )
    .unwrap();
    let (stdout, stderr) = run_ok_full(bin().args([
        "ask", "--vault-root", vault.to_str().unwrap(),
        "--cache-dir", cache_dir.to_str().unwrap(),
        ask_question,
    ]));
    assert!(stdout.contains("OVP ask uses evidence cards and units"), "{stdout}");
    assert!(stderr.contains("verified citations: 1/1"), "{stderr}");

    let strict_question = "Give a strict answer about chunks.";
    let mut rec = CachedModelClient::new(
        Canned("This answer has no citation.".into()),
        &cache_dir,
        "seed",
        CacheMode::Record,
    )
    .unwrap();
    ask_with_evidence(
        &model,
        &evidence,
        &mut rec,
        &AskArgs { question: strict_question.into(), ..Default::default() },
        &vault,
    )
    .unwrap();
    let (_stdout, stderr) = run_fail(bin().args([
        "ask", "--vault-root", vault.to_str().unwrap(),
        "--cache-dir", cache_dir.to_str().unwrap(),
        "--strict-ask",
        strict_question,
    ]));
    assert!(stderr.contains("strict ask citation verification failed"), "{stderr}");

    // === Failure → retry → blocked: a source with NO cassettes. ===
    std::fs::write(
        vault.join("Clippings/No Cassette.md"),
        clip_note("No Cassette", "https://e.x/nocassette",
            &"Entirely different content that has no recorded model reply at all. ".repeat(5)),
    )
    .unwrap();
    for attempt in 0..3 {
        let (stdout, stderr) = run_fail(bin().args([
            "daily",
            "--vault-root", vault.to_str().unwrap(),
            "--date", DATE,
            "--run-id", &format!("daily-e2e-fail-{attempt}"),
            "--cache-dir", cache_dir.to_str().unwrap(),
        ]));
        assert!(stdout.contains("FAIL"), "attempt {attempt}: {stdout}");
        assert!(stderr.contains("gate"), "attempt {attempt}: {stderr}");
    }
    // Fourth run: source is blocked → skipped → exit 0; console shows it.
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-e2e-after-block",
        "--cache-dir", cache_dir.to_str().unwrap(),
    ]));
    assert!(stdout.contains("blocked (3 failures)"), "{stdout}");
    assert!(stdout.contains("done: 0 processed, 0 failed"), "{stdout}");
    let console = std::fs::read_to_string(vault.join(".ovp/console/index.html")).unwrap();
    assert!(console.contains("失败暂停"), "blocked pill on console");
    assert!(console.contains("--retry-blocked"), "operator action hint on console");

    // The blocked source is still findable with its failure context.
    let stdout = run_ok(bin().args([
        "find", "--vault-root", vault.to_str().unwrap(),
        "--kind", "sources", "--status", "blocked",
    ]));
    assert!(stdout.contains("No Cassette") && stdout.contains("fails=3"), "{stdout}");
}

/// Periodic mid-run projection refresh (the stale-during-run fix). Two clippings
/// with `--refresh-every 1` must rebuild the read-model projection MID-run
/// (before the final end-of-run rebuild), so the portal's counts/facets go fresh
/// during a long run instead of showing the pre-run projection for its whole
/// duration. `--refresh-every 0` preserves the old behavior: no mid-run rebuild.
#[test]
fn daily_periodic_refresh_rebuilds_projection_mid_run() {
    let tmp = tempfile::tempdir().unwrap();
    let cache_dir = tmp.path().join("cassettes");

    // Two distinct clippings so a run processes >1 source; seed cassettes for
    // each from the exact SourceDoc the binary will read.
    let seed_two = |vault: &Path| {
        std::fs::create_dir_all(vault.join("Clippings")).unwrap();
        let a = vault.join("Clippings/Alpha.md");
        let b = vault.join("Clippings/Beta.md");
        std::fs::write(&a, clip_note("Alpha", "https://e.x/alpha", CLIP_BODY)).unwrap();
        std::fs::write(&b, clip_note("Beta", "https://e.x/beta", PIN_BODY)).unwrap();
        seed_cassettes(&cache_dir, &read_source_from_path(&a).unwrap(), CLIP_QUOTE, "Alpha unit.");
        seed_cassettes(&cache_dir, &read_source_from_path(&b).unwrap(), PIN_QUOTE, "Beta unit.");
    };

    // --- refresh-every 1: a rebuild fires after EACH of the two sources. ---
    let vault = tmp.path().join("vault-every1");
    seed_two(&vault);
    let stdout = run_ok(bin().args([
        "daily",
        "--vault-root", vault.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-refresh-1",
        "--refresh-every", "1",
        "--cache-dir", cache_dir.to_str().unwrap(),
    ]));
    // The mid-run rebuild log line proves the projection was refreshed DURING
    // the run, not just at the end. With --refresh-every 1 the FIRST source
    // rebuilds; the second source is a candidate too but finishes within the
    // debounce window (two trivial cassette sources complete in well under 20s),
    // so the debounce SKIPS it — exactly the anti-thrash behavior. So: at least
    // one mid-run rebuild AND the debounce line for the suppressed candidate.
    let mid_run_rebuilds = stdout.matches("refresh: portal projection updated").count();
    assert!(
        mid_run_rebuilds >= 1,
        "expected >=1 mid-run projection rebuild with --refresh-every 1, got {mid_run_rebuilds}\n{stdout}"
    );
    assert!(
        stdout.contains("refresh: skipped (debounced"),
        "the second fast source must be debounced, not thrash a rebuild\n{stdout}"
    );
    // The end-of-run authoritative rebuild still wrote the projection.
    assert!(vault.join(".ovp/index/index.json").exists(), "final index written");
    assert!(vault.join(".ovp/evidence.json").exists() || vault.join(".ovp/index/evidence.json").exists(),
        "evidence written");
    assert!(vault.join(".ovp/console/index.html").exists(), "console written");

    // --- refresh-every 0: OLD behavior — no mid-run rebuild, only the end. ---
    let vault0 = tmp.path().join("vault-every0");
    seed_two(&vault0);
    let stdout0 = run_ok(bin().args([
        "daily",
        "--vault-root", vault0.to_str().unwrap(),
        "--date", DATE,
        "--run-id", "daily-refresh-0",
        "--refresh-every", "0",
        "--cache-dir", cache_dir.to_str().unwrap(),
    ]));
    assert!(
        !stdout0.contains("refresh: portal projection updated"),
        "N=0 must NOT rebuild mid-run (old behavior)\n{stdout0}"
    );
    // …but the end-of-run projection is still built.
    assert!(vault0.join(".ovp/index/index.json").exists(), "final index still written at N=0");
}
