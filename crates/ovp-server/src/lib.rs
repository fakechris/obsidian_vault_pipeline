//! `ovp-server` — synchronous localhost HTTP server for the OVP2 portal
//! and API.
//!
//! Serves the portal SPA at the site root (deployed `.ovp/console/app/` or
//! the `--viz-dir` overlay; see `resolve_static` for the precedence rule),
//! legacy generated console pages by exact filename, and JSON API endpoints
//! (`/api/find`, `/api/search`, `/api/graph`, `/api/claim/:id`,
//! `/api/source/:sha`, `/api/flow`, `/api/settings`, `POST /api/ask`,
//! `/api/chats`). Uses `tiny_http` to avoid any async runtime dependency.

mod graph;

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock, mpsc};
use std::time::{Duration, SystemTime};

use ovp_domain::VaultLayout;
use ovp_domain::crystal::themes::{ThemesFile, UNCLASSIFIED_THEME};
use ovp_domain::crystal::{CrystalStatus, DurableRecord, StoreEvent, fold_ledger};
use ovp_domain::units::Unit;
use ovp_index::{
    EvidenceModel, IndexModel, LastRunModel, Query, QueryKind, evidence_path, read_evidence,
    read_index, read_last_run_model, run_query,
};
use ovp_intake::read_jsonl;
use ovp_llm::ModelClient;
use ovp_memory::ask::{AskArgs, AskResult, EvidenceItem, EvidenceKind, ask_with_optional_evidence};
use ovp_memory::verify::{citation_key, citations_in_order};
use tiny_http::{Header, Method, Response, Server};

/// Cap for source markdown shipped in the /api/source payload — beyond this
/// the response truncates with an explicit flag instead of shipping megabytes
/// of JSON (same limit the v1 server-rendered page used).
pub const MAX_SOURCE_DOC_BYTES: usize = 200 * 1024;

/// Cap for POST request bodies (`/api/ask` questions are short; anything
/// bigger is a mistake, not a question).
pub const MAX_POST_BODY_BYTES: usize = 64 * 1024;

/// Default LLM transport timeout. Restates
/// `ovp_llm::anthropic` `DEFAULT_TIMEOUT_SECS` (feature-gated behind
/// `anthropic` there, so not importable from this transport-free crate);
/// `OVP_LLM_TIMEOUT_SECS` supersedes both, exactly like the client itself.
const DEFAULT_TRANSPORT_TIMEOUT_SECS: u64 = 180;

/// Margin the /api/ask wall-clock guard adds ON TOP of the transport
/// timeout — retrieval, citation shaping and the chat write around the LLM
/// call. A guard shorter than the transport timeout would 504 the portal
/// while the billable call keeps running.
const ASK_GUARD_MARGIN_SECS: u64 = 30;

/// Guard when the operator DISABLED the transport timeout
/// (`OVP_LLM_TIMEOUT_SECS=0`): the guard is then the only bound on how
/// long a request handle stays open.
const ASK_GUARD_NO_TRANSPORT_TIMEOUT_SECS: u64 = 600;

/// Cap on concurrently running ask pipelines. Every in-flight ask is a
/// paid LLM call holding a worker thread; a stuck provider holds its slot
/// until the transport gives up. Saturation answers 429 immediately — no
/// queue. Override via [`ServeConfig::max_concurrent_asks`].
pub const DEFAULT_MAX_CONCURRENT_ASKS: usize = 2;

/// The /api/ask wall-clock guard, derived from the SAME `OVP_LLM_TIMEOUT_SECS`
/// the live client reads, plus [`ASK_GUARD_MARGIN_SECS`]. NOTE: the guard
/// firing does NOT cancel the provider call — the worker finishes (and
/// saves the chat) in the background; the 504 body says so.
pub fn ask_guard_from_env() -> Duration {
    ask_guard(|k| std::env::var(k).ok())
}

/// Testable core of [`ask_guard_from_env`] (same lookup-injection pattern
/// as the CLI's `LiveClientConfig::from_lookup`). Unparseable values fall
/// back to the default rather than failing — the CLIENT validates the var
/// fail-loud at startup; the guard just needs a sane bound.
fn ask_guard(lookup: impl Fn(&str) -> Option<String>) -> Duration {
    let transport = lookup("OVP_LLM_TIMEOUT_SECS").and_then(|v| v.trim().parse::<u64>().ok());
    let secs = match transport {
        Some(0) => return Duration::from_secs(ASK_GUARD_NO_TRANSPORT_TIMEOUT_SECS),
        Some(n) => n,
        None => DEFAULT_TRANSPORT_TIMEOUT_SECS,
    };
    Duration::from_secs(secs + ASK_GUARD_MARGIN_SECS)
}

/// Builds the LLM client for `POST /api/ask` on demand. Injected by the CLI,
/// which owns the feature-gated live transport and the key check — the
/// server itself stays transport-free. `None` = ask answers 503.
pub type AskClientFactory =
    Arc<dyn Fn() -> Result<Box<dyn ModelClient>, String> + Send + Sync>;

pub struct ServeConfig {
    pub vault_root: PathBuf,
    pub host: String,
    pub port: u16,
    /// Fallback directory for the portal SPA build (`console-ui/dist`).
    /// When the vault's deployed `.ovp/console/app/` misses, files are
    /// served from here — so a dev checkout can serve ANY vault without
    /// copying the build in.
    pub viz_dir: Option<PathBuf>,
    /// LLM client factory for `POST /api/ask` — `None` when no live LLM is
    /// configured (missing key / feature); ask then answers 503.
    pub ask_client: Option<AskClientFactory>,
    /// Override the /api/ask wall-clock guard (tests). `None` derives it
    /// from the transport timeout env — see [`ask_guard_from_env`].
    pub ask_timeout: Option<Duration>,
    /// Override the in-flight ask cap (tests). `None` =
    /// [`DEFAULT_MAX_CONCURRENT_ASKS`].
    pub max_concurrent_asks: Option<usize>,
}

/// Counting semaphore for in-flight asks — no queue, `try_acquire` only.
/// The slot travels INTO the pipeline thread and releases on drop, so an
/// ask that outlived its 504 guard still counts against the cap until the
/// provider call actually returns.
struct AskSlots {
    active: std::sync::atomic::AtomicUsize,
    /// The cap — surfaced read-only via `/api/settings`.
    max: usize,
}

impl AskSlots {
    fn new(max: usize) -> Arc<Self> {
        Arc::new(Self {
            active: std::sync::atomic::AtomicUsize::new(0),
            max,
        })
    }

    fn try_acquire(self: &Arc<Self>) -> Option<AskSlot> {
        use std::sync::atomic::Ordering;
        let mut current = self.active.load(Ordering::Relaxed);
        loop {
            if current >= self.max {
                return None;
            }
            match self.active.compare_exchange(
                current,
                current + 1,
                Ordering::AcqRel,
                Ordering::Relaxed,
            ) {
                Ok(_) => return Some(AskSlot(Arc::clone(self))),
                Err(now) => current = now,
            }
        }
    }
}

/// RAII slot — dropping it (pipeline done, success or panic-unwind) frees
/// the concurrency unit.
struct AskSlot(Arc<AskSlots>);

impl Drop for AskSlot {
    fn drop(&mut self) {
        self.0
            .active
            .fetch_sub(1, std::sync::atomic::Ordering::AcqRel);
    }
}

/// mtime-keyed disk cache. `stamp` records the source file's modified-time
/// AT LOAD TIME so an accessor can compare it against the file's current
/// mtime and reload only when the file actually changed. A cache built from
/// an ABSENT file stores `stamp: None` — so it never re-reads a
/// legitimately-missing sidecar every request, yet reloads the moment one
/// appears. `data` is `None` when the file was absent or failed to parse.
///
/// `Cached::default()` (`loaded=false`) is the "never attempted" state; once
/// an accessor runs it is always `loaded=true`, distinguishing "no data
/// because absent" from "not yet looked".
struct Cached<T> {
    loaded: bool,
    stamp: Option<SystemTime>,
    data: Option<T>,
}

impl<T> Default for Cached<T> {
    fn default() -> Self {
        Self {
            loaded: false,
            stamp: None,
            data: None,
        }
    }
}

/// The source file's modified-time, or `None` when the file is absent /
/// unstattable. A `stat` per request is negligible next to a reload.
fn mtime_of(path: &Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// Count `.md` files under `dir` recursively, skipping dotfiles/dot-dirs.
/// Mirrors `ovp_index::build`'s `walk` (the same rule that feeds the
/// projection's raw-inbox scan) so the two see the SAME set of files. This is
/// the RAW walk — the gross 01-Raw backlog; [`AppState::live_queued_count`]
/// then subtracts the non-queued rows the projection knows about to match
/// `SourceStatus::Queued`. An absent dir → 0 (a fresh vault has no backlog).
/// I/O errors degrade to the partial count already walked — the server keeps
/// serving; the number is a gauge, not a gate.
fn count_markdown_files(dir: &Path) -> usize {
    fn walk(dir: &Path, count: &mut usize) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            if entry.file_name().to_string_lossy().starts_with('.') {
                continue;
            }
            let path = entry.path();
            if path.is_dir() {
                walk(&path, count);
            } else if path.extension().is_some_and(|e| e == "md") {
                *count += 1;
            }
        }
    }
    let mut count = 0;
    walk(dir, &mut count);
    count
}

/// Count projection sources that occupy a file under 01-Raw but are NOT queued
/// — the stable subtrahend that corrects the raw walk to the projection's
/// `SourceStatus::Queued` semantics (see [`AppState::live_queued_count`]).
///
/// Non-queued raw-inbox classifications, straight from `build_sources`:
///
/// - Blocked / Failed: a failed attempt keeps the source in 01-Raw with
///   `rel_path = source_path` (under 01-Raw).
/// - NeedsContent / Unparseable: intake parked them in place, still in 01-Raw
///   awaiting operator enrichment/repair.
/// - Duplicate: a parked duplicate COPY; the intake sweep moves it to
///   `03-Processed/duplicates/`, so its `rel_path` is normally NOT under 01-Raw
///   and it is not subtracted — the rel_path filter subtracts one only in the
///   rare case a duplicate copy still physically sits in 01-Raw.
///
/// Queued rows ARE the files being counted, and Processed rows already left
/// 01-Raw — neither is subtracted.
fn count_non_queued_in_raw(model: &IndexModel, raw_dir: &str) -> usize {
    let raw_prefix = format!("{raw_dir}/");
    model
        .sources
        .iter()
        // Any NON-queued source still physically in 01-Raw is subtracted,
        // not just blocked/failed/dup: `--no-lifecycle` (or a failed
        // lifecycle move) leaves a Processed file in 01-Raw too, and it must
        // not inflate the live queue (codex review P1). At rest this makes
        // queued_live == projection.queued in every lifecycle mode.
        //
        // KNOWN TRANSIENT (codex P2, accepted): a source that FAILS mid-run
        // stays in 01-Raw while the cached projection still calls it Queued
        // until the end-of-run rebuild, so queued_live can briefly overstate
        // by up to (failures this run) — typically 0-1. It self-corrects the
        // instant the index rebuilds; tracking it live would couple this to
        // per-run failure state for a sub-1-count, seconds-long discrepancy,
        // which isn't worth the complexity.
        .filter(|s| !matches!(s.status, ovp_index::SourceStatus::Queued))
        .filter(|s| {
            s.rel_path
                .as_deref()
                .is_some_and(|p| p.starts_with(&raw_prefix) || p == raw_dir)
        })
        .count()
}

struct AppState {
    vault_root: PathBuf,
    layout: VaultLayout,
    /// The index read-model, cached with the mtime of `index.json` it was
    /// loaded from. `current_model` auto-reloads when the file is newer, so
    /// the portal reflects the latest `daily`/`crystal-synth` run without a
    /// manual `/api/refresh` (the separate serve process no longer serves a
    /// frozen startup snapshot).
    model: RwLock<Cached<IndexModel>>,
    /// Card/unit bodies for the /api/source/:sha memory layer — same
    /// mtime-keyed auto-reload as the model, keyed on `evidence.json`.
    evidence: RwLock<Cached<EvidenceModel>>,
    /// The run-liveness heartbeat (`.ovp/last-run.json`), read LIVE (mtime
    /// auto-reload) — NOT the baked `index.json` snapshot. The heartbeat is a
    /// live sidecar: `daily` writes it before the index is rebuilt (and a crash
    /// can leave the index's baked copy saying "running" forever), so every
    /// server surface overlays THIS fresh value over `model.ops.last_run`.
    last_run: RwLock<Cached<LastRunModel>>,
    viz_dir: Option<PathBuf>,
    ask_client: Option<AskClientFactory>,
    /// The /api/ask wall-clock guard — [`ask_guard_from_env`] unless
    /// overridden via [`ServeConfig::ask_timeout`].
    ask_timeout: Duration,
    /// In-flight ask cap — see [`AskSlots`].
    ask_slots: Arc<AskSlots>,
    /// Serve-time LIVE queued backlog — the count of pending-source `.md` files
    /// under `50-Inbox/01-Raw/**` RIGHT NOW, TTL-cached (see [`LiveQueued`]).
    /// This is ground truth, not the `index.json` projection's `totals.queued`,
    /// which is only rebuilt at the END of a `daily` run: during a 1-2h run the
    /// projection is frozen even as sources drain out of 01-Raw. Overlaying this
    /// live number is the whole point — it may differ from the projection
    /// mid-run, and it is the authoritative-now figure.
    live_queued: RwLock<LiveQueued>,
}

/// TTL cache for the live 01-Raw backlog count. A recursive walk of ~200 files
/// is cheap, but under request load (portal poll + monitor tab) an unbounded
/// per-request walk is wasteful, so a recount happens at most once per
/// [`LIVE_QUEUED_TTL`]. Never negative-cached forever: the TTL is short enough
/// that the count tracks a draining run within seconds.
#[derive(Default)]
struct LiveQueued {
    count: usize,
    /// When `count` was computed; `None` = never (forces a first walk).
    computed_at: Option<std::time::Instant>,
}

/// How long a live queued count is served before a fresh 01-Raw walk. Short by
/// design: the whole feature exists so the number ticks down DURING a run.
const LIVE_QUEUED_TTL: Duration = Duration::from_secs(5);

impl AppState {
    fn index_path(&self) -> PathBuf {
        self.vault_root.join(self.layout.index_file())
    }

    fn evidence_path(&self) -> PathBuf {
        evidence_path(&self.vault_root)
    }

    fn last_run_path(&self) -> PathBuf {
        self.vault_root.join(self.layout.last_run_file())
    }

    /// The LIVE queued backlog, TTL-cached (see [`LiveQueued`]) — the number the
    /// portal shows as "Queued" so it ticks DOWN as a run drains 01-Raw, while
    /// the projection's `totals.queued` only refreshes at end-of-run.
    ///
    /// It must match the projection's `SourceStatus::Queued` semantics, NOT a
    /// naive raw-file walk: blocked / failed / needs-content / unparseable
    /// sources and parked duplicate COPIES all keep files in 01-Raw yet are NOT
    /// queued, so a bare walk over-counts and stays inflated after a run. The
    /// formula (verified against `ovp-index::build_sources`):
    ///
    /// ```text
    /// queued_live = (current .md under 01-Raw)
    ///             − (projection sources classified non-queued whose rel_path
    ///                is under 01-Raw)
    /// ```
    ///
    /// The subtrahend is STABLE during a run: a source classified blocked /
    /// failed / needs_content / unparseable / duplicate does not change class as
    /// OTHER (queued) sources get processed and leave 01-Raw. So the raw walk
    /// ticks down live as processed sources move to 03-Processed, while the
    /// stable subtrahend keeps the number equal to the projection's queued at
    /// rest. `queued` and `processed` rows are NOT subtracted (a queued row IS
    /// the file we're counting; a processed row's file already left 01-Raw).
    fn live_queued_count(&self, model: Option<&IndexModel>) -> usize {
        {
            let guard = self.live_queued.read().unwrap();
            if let Some(at) = guard.computed_at
                && at.elapsed() < LIVE_QUEUED_TTL
            {
                return guard.count;
            }
        }
        let mut guard = self.live_queued.write().unwrap();
        // Re-check under the write lock: a burst of requests recounts once.
        if let Some(at) = guard.computed_at
            && at.elapsed() < LIVE_QUEUED_TTL
        {
            return guard.count;
        }
        let raw_dir = self.vault_root.join(self.layout.inbox_raw_dir());
        let raw_files = count_markdown_files(&raw_dir);
        let non_queued_in_raw = model
            .map(|m| count_non_queued_in_raw(m, self.layout.inbox_raw_dir()))
            .unwrap_or(0);
        // Saturating: a stale projection could momentarily know MORE non-queued
        // raw rows than files currently on disk (e.g. a blocked file was just
        // fixed in place); never report a negative backlog.
        let count = raw_files.saturating_sub(non_queued_in_raw);
        *guard = LiveQueued {
            count,
            computed_at: Some(std::time::Instant::now()),
        };
        count
    }

    /// The LIVE run-liveness heartbeat, mtime-freshened against
    /// `.ovp/last-run.json`. This is the single source of truth for last-run
    /// status on every API surface — never the baked `index.json` copy, which
    /// can be a stale "running" snapshot from before a crash. A corrupt file
    /// degrades to `None` here (the server must keep serving); `doctor` is the
    /// fail-loud gate for corruption.
    fn current_last_run(&self) -> Option<LastRunModel> {
        let path = self.last_run_path();
        freshen(&self.last_run, &path, || {
            read_last_run_model(&self.vault_root).ok().flatten()
        })
    }

    /// The index model with its `ops.last_run` field OVERLAID by the live
    /// heartbeat sidecar — so `/api/model` (and the SPA banner reading it) never
    /// sees a stale baked "running". Returns None only when there is no index
    /// at all.
    fn model_with_live_last_run(&self) -> Option<IndexModel> {
        let mut model = self.current_model()?;
        model.ops.last_run = self.current_last_run();
        Some(model)
    }

    /// The cached index read-model, auto-freshened against `index.json`'s
    /// mtime. A `stat` per call is negligible; the parse only happens when
    /// the file actually changed (or was never loaded), so a separate
    /// `daily` process rebuilding the index is picked up on the next request
    /// — the portal is never stuck on the startup snapshot.
    fn current_model(&self) -> Option<IndexModel> {
        let path = self.index_path();
        freshen(&self.model, &path, || read_index(&self.vault_root).ok())
    }

    /// The cached evidence sidecar, same mtime-keyed auto-reload as the model
    /// (keyed on `evidence.json`). Legitimately absent on pre-M31 vaults; a
    /// missing file caches `None` without re-reading every request, yet
    /// reloads the instant one appears.
    fn current_evidence(&self) -> Option<EvidenceModel> {
        let path = self.evidence_path();
        freshen(&self.evidence, &path, || read_evidence(&self.vault_root).ok())
    }

    /// Force-reload both caches from disk regardless of mtime. `/api/refresh`
    /// keeps this for scripts; with mtime auto-reload it is now optional
    /// (every accessor already freshens on its own).
    fn refresh_model(&self) {
        force_reload(
            &self.model,
            &self.index_path(),
            read_index(&self.vault_root).ok(),
        );
        force_reload(
            &self.evidence,
            &self.evidence_path(),
            read_evidence(&self.vault_root).ok(),
        );
        force_reload(
            &self.last_run,
            &self.last_run_path(),
            read_last_run_model(&self.vault_root).ok().flatten(),
        );
    }

    fn console_dir(&self) -> PathBuf {
        self.vault_root.join(self.layout.console_dir())
    }
}

/// Serve a cached value, reloading only when the source file's mtime advanced
/// (or the cache was never loaded). Read-guard fast path first; on a miss,
/// take the write guard and RE-CHECK the mtime under it (double-checked
/// locking) so a burst of concurrent requests reloads once, not N times.
/// `load` re-reads+parses the file and returns `None` if absent/invalid.
fn freshen<T: Clone>(
    cache: &RwLock<Cached<T>>,
    path: &Path,
    load: impl FnOnce() -> Option<T>,
) -> Option<T> {
    let disk = mtime_of(path);
    {
        let guard = cache.read().unwrap();
        if guard.loaded && guard.stamp == disk {
            return guard.data.clone();
        }
    }
    let mut guard = cache.write().unwrap();
    // Re-check under the write lock: another thread may have just reloaded.
    // Re-stat too — the file could have changed again between the read
    // guard's stat and acquiring the write lock.
    let disk = mtime_of(path);
    if guard.loaded && guard.stamp == disk {
        return guard.data.clone();
    }
    let data = load();
    *guard = Cached {
        loaded: true,
        stamp: disk,
        data: data.clone(),
    };
    data
}

/// Force-store a freshly-read value with the file's current mtime, bypassing
/// the freshness check. Used by `/api/refresh`.
fn force_reload<T>(cache: &RwLock<Cached<T>>, path: &Path, data: Option<T>) {
    let stamp = mtime_of(path);
    *cache.write().unwrap() = Cached {
        loaded: true,
        stamp,
        data,
    };
}

pub fn run_server(config: ServeConfig) -> Result<(), String> {
    let bind = format!("{}:{}", config.host, config.port);
    let server = Server::http(&bind).map_err(|e| format!("failed to bind {bind}: {e}"))?;

    let state = Arc::new(AppState {
        vault_root: config.vault_root,
        layout: VaultLayout::new(),
        model: RwLock::new(Cached::default()),
        evidence: RwLock::new(Cached::default()),
        last_run: RwLock::new(Cached::default()),
        viz_dir: config.viz_dir,
        ask_client: config.ask_client,
        ask_timeout: config.ask_timeout.unwrap_or_else(ask_guard_from_env),
        ask_slots: AskSlots::new(
            config
                .max_concurrent_asks
                .unwrap_or(DEFAULT_MAX_CONCURRENT_ASKS),
        ),
        live_queued: RwLock::new(LiveQueued::default()),
    });

    // Pre-load model
    state.refresh_model();

    eprintln!("ovp-server listening on http://{bind}");
    eprintln!("  console: http://{bind}/");
    eprintln!("  API:     http://{bind}/api/find?term=...");
    eprintln!(
        "  reload:  automatic — the portal reflects the latest completed run \
         (index.json mtime); http://{bind}/api/refresh forces it (optional)"
    );
    match &state.viz_dir {
        Some(dir) => eprintln!("  portal:  overlay from {}", dir.display()),
        None => {
            if !state.console_dir().join("app").join("index.html").exists() {
                eprintln!(
                    "  portal:  NOT DEPLOYED in this vault — pass --viz-dir \
                     <repo>/console-ui/dist to serve the SPA build \
                     (legacy console pages still served)"
                );
            }
        }
    }

    serve_loop(&server, &state);

    Ok(())
}

/// The accept loop, extracted from `run_server` so tests can drive it on an
/// ephemeral port. `POST /api/ask` is the one long-running route (it makes
/// an LLM call): the WHOLE request moves to a detached worker thread
/// (`tiny_http::Request` is Send) which reads the body, runs the pipeline
/// (with its own wall-clock guard) and responds itself — the loop continues
/// immediately, so every other request stays snappy while an ask is in
/// flight.
fn serve_loop(server: &Server, state: &Arc<AppState>) {
    for mut request in server.incoming_requests() {
        let path = request.url().to_string();
        let method = request.method().clone();
        if method == Method::Post && path.split('?').next().unwrap_or(&path) == "/api/ask" {
            // Bounded admission BEFORE any body bytes are read: a client
            // that sends headers with a nonzero Content-Length and then
            // withholds the body would otherwise pin an unbounded number
            // of reader threads despite the ask cap (codex review P2).
            // Saturation answers 429 inline — nothing spawned, nothing read.
            let slot = match admit_ask_slot(state) {
                Ok(slot) => slot,
                Err(resp) => {
                    let _ = request.respond(resp);
                    continue;
                }
            };
            let state = Arc::clone(state);
            std::thread::spawn(move || {
                let headers = AskHeaders::of(&request);
                let body = read_post_body(&mut request);
                let resp = handle_ask(&state, &headers, &body, slot);
                let _ = request.respond(resp);
            });
            continue;
        }
        let body = if method == Method::Post {
            read_post_body(&mut request)
        } else {
            String::new()
        };
        let resp = dispatch(state, method, &path, &body);
        let _ = request.respond(resp);
    }
}

/// Read a POST body up to one byte past [`MAX_POST_BODY_BYTES`] — the
/// handler rejects oversize bodies with a 400 instead of parsing a silent
/// truncation. Read/encoding errors yield an unparseable body (→ 400 too).
fn read_post_body(request: &mut tiny_http::Request) -> String {
    use std::io::Read;
    let mut body = String::new();
    let limit = (MAX_POST_BODY_BYTES + 1) as u64;
    let _ = request.as_reader().take(limit).read_to_string(&mut body);
    body
}

/// Route one request. Extracted from the accept loop so routing is unit
/// testable. Matching runs on the path WITHOUT the query string (so exact
/// routes like `/api/model?x=1` still hit their handler); handlers get the
/// full url and parse their own params. `body` is only populated for POST.
/// `POST /api/ask` never reaches here — `serve_loop` hands it to a detached
/// worker before dispatch so the accept loop can't block on an LLM call.
/// Anything under `/api/` that matches no route is a JSON 404 — it must
/// never fall through to the SPA shell.
fn dispatch(
    state: &AppState,
    method: Method,
    url: &str,
    // POST bodies are consumed pre-dispatch today (ask is the only POST
    // route); the parameter stays so a future body-taking route slots in.
    _body: &str,
) -> Response<std::io::Cursor<Vec<u8>>> {
    let path = url.split('?').next().unwrap_or(url);
    match (method, path) {
        (Method::Get, "/api/refresh") => {
            state.refresh_model();
            json_response(200, r#"{"ok":true}"#)
        }
        (Method::Get, "/api/chats") => handle_chats_list(state),
        (Method::Get, p) if p.starts_with("/api/chats/") => handle_chat_detail(state, p),
        (Method::Get, p) if p.starts_with("/api/find") => handle_find(state, url),
        (Method::Get, p) if p.starts_with("/api/search") => handle_search(state, url),
        (Method::Get, "/api/model") => handle_model(state),
        (Method::Get, p) if p.starts_with("/api/graph") => handle_graph(state, url),
        (Method::Get, "/api/flow") => handle_flow(state),
        (Method::Get, "/api/settings") => handle_settings(state),
        (Method::Get, "/api/themes") => handle_themes(state),
        (Method::Get, p) if p.starts_with("/api/claim/") => handle_claim(state, url),
        (Method::Get, p) if p.starts_with("/api/source/") => handle_source_api(state, url),
        (Method::Get, p) if p == "/api" || p.starts_with("/api/") => {
            json_response(404, r#"{"error":"unknown api route"}"#)
        }
        (Method::Get, _) => serve_static(state, url),
        _ => text_response(405, "Method Not Allowed"),
    }
}

fn handle_find(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let params = parse_query_string(url);
    let query = Query {
        kind: params.get("kind").and_then(|k| match k.as_str() {
            "sources" => Some(QueryKind::Sources),
            "packs" => Some(QueryKind::Packs),
            "claims" => Some(QueryKind::Claims),
            "runs" => Some(QueryKind::Runs),
            _ => None,
        }),
        status: params.get("status").cloned(),
        date: params.get("date").cloned(),
        term: params.get("term").cloned(),
    };

    let hits = run_query(&model, &query);
    let body = serde_json::to_string(&hits).unwrap_or_else(|_| "[]".into());
    json_stamped(200, &body, Some(&model))
}

fn handle_search(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let params = parse_query_string(url);
    let term = params.get("q").or_else(|| params.get("term")).cloned();

    // Graph search mode: return a hit-flagged subgraph instead of text hits
    // (the ≤40-node tight-layout scenario in the console).
    if params.get("subgraph").map(String::as_str) == Some("1") {
        let Some(term) = term.filter(|t| !t.trim().is_empty()) else {
            return json_response(400, r#"{"error":"subgraph search requires q"}"#);
        };
        // One model read for the whole handler: the subgraph is joined against
        // the ledger records AND the index, so both halves must reflect the
        // same freshness — the stamp pairs them.
        let model = state.current_model();
        let records = load_active_records(state);
        let resp = graph::search_subgraph(&records, model.as_ref(), term.trim());
        let body = serde_json::to_string(&resp).unwrap_or_else(|_| "{}".into());
        return json_stamped(200, &body, model.as_ref());
    }

    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };
    let query = Query {
        kind: None,
        status: None,
        date: None,
        term,
    };
    let hits = run_query(&model, &query);
    let body = serde_json::to_string(&hits).unwrap_or_else(|_| "[]".into());
    json_stamped(200, &body, Some(&model))
}

fn handle_themes(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    // Read the model ONCE up front so the theme counts (from the live ledger)
    // ship with the projection stamp they were paired against — the client can
    // see both halves came from the same freshness.
    let model = state.current_model();
    let records = load_active_records(state);
    let themes: Vec<serde_json::Value> = graph::theme_counts(&records)
        .into_iter()
        .map(|(theme, count)| serde_json::json!({ "theme": theme, "count": count }))
        .collect();
    let body = serde_json::to_string(&themes).unwrap_or_else(|_| "[]".into());
    json_stamped(200, &body, model.as_ref())
}

fn handle_model(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    // Overlay the LIVE heartbeat over the baked `ops.last_run` so the SPA banner
    // never reads a stale "running" snapshot baked into index.json.
    let model = match state.model_with_live_last_run() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };
    // The IndexModel already carries `built_at`/`run_id`; splice in the
    // server-computed `age_seconds` (now - built_at) so the client need not
    // trust its own clock, and echo the same three as headers.
    let mut value = serde_json::to_value(&model).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(obj) = value.as_object_mut() {
        obj.insert(
            "age_seconds".into(),
            serde_json::json!(age_seconds(model.built_at.as_deref())),
        );
        // LIVE queued overlay: `totals.queued` stays the projection value (other
        // readers depend on it and it's the end-of-run provenance figure);
        // `queued_live` is the serve-time 01-Raw count the SPA renders as the
        // primary "Queued", so it ticks down during a run instead of freezing.
        // Also mirrored as `queued_at_build` for a symmetric label with settings.
        obj.insert(
            "queued_live".into(),
            serde_json::json!(state.live_queued_count(Some(&model))),
        );
        obj.insert(
            "queued_at_build".into(),
            serde_json::json!(model.totals.queued),
        );
    }
    let body = serde_json::to_string(&value).unwrap_or_else(|_| "{}".into());
    json_stamped(200, &body, Some(&model))
}

fn load_active_records(state: &AppState) -> Vec<DurableRecord> {
    let store = state.vault_root.join(state.layout.crystal_store_dir());
    let events: Vec<StoreEvent> = match read_jsonl(&store.join("ledger.jsonl")) {
        Ok(e) => e,
        Err(_) => return Vec::new(),
    };
    let mut records: Vec<DurableRecord> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect();
    // Semantic theme PROJECTION: mirror `ovp-index::build_claims` so every
    // server surface (/api/themes, graph scopes, claim pages) shows the same
    // display themes as the read model. The ledger stays untouched. A corrupt
    // themes.json degrades to passthrough here (the server must keep serving);
    // `ovp2 index` is where corruption fails loud.
    match ThemesFile::load(&store.join("themes.json")) {
        Ok(Some(themes)) => {
            for r in records.iter_mut() {
                r.theme = themes
                    .majority_label(&r.source_cases)
                    .unwrap_or_else(|| UNCLASSIFIED_THEME.to_string());
            }
        }
        Ok(None) => {}
        Err(e) => eprintln!("warning: ignoring themes.json ({e})"),
    }
    records
}

fn handle_graph(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let query = parse_query_string(url);

    // Read the model ONCE for the whole handler (torn-read fix): every scope
    // joins the live ledger records against this projection, so a single read
    // pairs both halves at one freshness and the stamp reflects that pairing.
    let model = state.current_model();

    // Portal v2 scoped-component API (design §4) — one KnowledgeGraph
    // component, three scopes:
    //   scope=neighborhood&source=<sha>  this source → citing claims →
    //                                    sibling sources (B2)
    //   scope=global[&limit=n]           the overview/density graph (claims
    //                                    + community metadata) — the
    //                                    knowledge-page graph view (B3)
    //   scope=theme&theme=<t>            the theme's claims + their sources
    //                                    — the theme-detail rail (B3)
    // Unknown scopes fail loud, never guess.
    if let Some(scope) = query.get("scope") {
        let result = match scope.as_str() {
            "neighborhood" => {
                let Some(sha) = query.get("source").filter(|s| !s.is_empty()) else {
                    return json_response(
                        400,
                        r#"{"error":"scope=neighborhood requires source=<sha256>"}"#,
                    );
                };
                let records = load_active_records(state);
                // Evidence sidecar feeds the memory-layer card nodes (B5).
                let evidence = state.current_evidence();
                graph::source_neighborhood(&records, model.as_ref(), evidence.as_ref(), sha)
            }
            "global" => {
                let limit = query
                    .get("limit")
                    .and_then(|v| v.parse::<usize>().ok())
                    .unwrap_or(graph::DEFAULT_OVERVIEW_LIMIT)
                    .max(1);
                let params = graph::GraphParams {
                    mode: graph::GraphMode::Overview,
                    limit,
                    theme: None,
                    focus: None,
                    hops: graph::MAX_HOPS,
                };
                let records = load_active_records(state);
                graph::build_graph(&records, model.as_ref(), &params)
            }
            "theme" => {
                let Some(theme) = query.get("theme").filter(|t| !t.is_empty()) else {
                    return json_response(400, r#"{"error":"scope=theme requires theme=<theme>"}"#);
                };
                let records = load_active_records(state);
                graph::theme_subgraph(&records, model.as_ref(), theme)
            }
            other => {
                let body = serde_json::json!({
                    "error": format!("unknown scope: {other} (neighborhood|global|theme)"),
                });
                return json_response(400, &body.to_string());
            }
        };
        return match result {
            Ok(resp) => {
                let body = serde_json::to_string(&resp).unwrap_or_else(|_| "{}".into());
                json_stamped(200, &body, model.as_ref())
            }
            Err(e) => {
                let body = serde_json::json!({ "error": e.message });
                json_response(e.status, &body.to_string())
            }
        };
    }

    let params = match graph::GraphParams::from_query(&query) {
        Ok(p) => p,
        Err(e) => {
            let body = serde_json::json!({ "error": e.message });
            return json_response(e.status, &body.to_string());
        }
    };

    let records = load_active_records(state);

    match graph::build_graph(&records, model.as_ref(), &params) {
        Ok(resp) => {
            let body = serde_json::to_string(&resp).unwrap_or_else(|_| "{}".into());
            json_stamped(200, &body, model.as_ref())
        }
        Err(e) => {
            let body = serde_json::json!({ "error": e.message });
            json_response(e.status, &body.to_string())
        }
    }
}

fn handle_claim(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let id = url.strip_prefix("/api/claim/").unwrap_or("");
    let id = url_decode(id);
    if id.is_empty() {
        return json_response(400, r#"{"error":"missing claim id"}"#);
    }

    // Read the model BEFORE the ledger records so a claim's citations resolve
    // their source metadata against the SAME freshness the claim came from
    // (torn-read fix): the auto-freshen keeps them paired, and the stamp on the
    // response lets the client see the pairing.
    let model = state.current_model();
    let records = load_active_records(state);
    let rec = records
        .iter()
        .find(|r| r.claim_key == id || r.claim_id == id);
    let rec = match rec {
        Some(r) => r,
        None => return json_response(404, r#"{"error":"claim not found"}"#),
    };

    let source_lookup: HashMap<String, &ovp_index::SourceRow> = model
        .as_ref()
        .map(|m| m.sources.iter().map(|s| (s.sha256.clone(), s)).collect())
        .unwrap_or_default();
    let pack_lookup: HashMap<String, &ovp_index::PackRow> = model
        .as_ref()
        .map(|m| {
            m.packs
                .iter()
                .filter_map(|p| {
                    let case = graph::last_path_segment(&p.pack_dir)?;
                    Some((case.to_string(), p))
                })
                .collect()
        })
        .unwrap_or_default();

    let reader_root = state.vault_root.join(state.layout.reader_root());
    let mut citations = Vec::new();

    for cit in &rec.citations {
        let units_path = reader_root.join(&cit.case_id).join("units.accepted.json");
        let unit_text = std::fs::read_to_string(&units_path)
            .ok()
            .and_then(|raw| serde_json::from_str::<Vec<Unit>>(&raw).ok())
            .and_then(|units| {
                units
                    .into_iter()
                    .find(|u| u.id == cit.unit_id)
                    .map(|u| u.text)
            })
            .unwrap_or_default();

        let (source_title, source_url, source_sha) =
            if let Some(pack) = pack_lookup.get(cit.case_id.as_str()) {
                let sha = pack.source_sha256.as_deref().unwrap_or("").to_string();
                let src = source_lookup.get(&sha);
                (
                    src.and_then(|s| s.title.clone())
                        .unwrap_or_else(|| pack.title.clone()),
                    src.and_then(|s| s.url.clone()).unwrap_or_default(),
                    sha,
                )
            } else {
                (cit.case_id.clone(), String::new(), String::new())
            };

        citations.push(serde_json::json!({
            "unit_id": cit.unit_id,
            "unit_text": unit_text,
            "quote": cit.quote,
            "resolved_line": cit.resolved_line,
            "case_id": cit.case_id,
            "source_title": source_title,
            "source_url": source_url,
            "source_sha256": source_sha,
        }));
    }

    let body = serde_json::json!({
        "claim_id": rec.claim_key,
        "claim": rec.claim,
        "theme": rec.theme,
        "strength": format!("{:?}", rec.strength).to_lowercase(),
        "citations": citations,
    });
    json_stamped(200, &body.to_string(), model.as_ref())
}

/// GET /api/source/<sha256> — JSON for the portal's three-layer source
/// detail page (B2): full SourceRow meta, the memory layer (cards + grounded
/// units from the evidence sidecar), crystal claims citing this source, and
/// the raw source markdown (size-capped, traversal-safe). The markdown is
/// DATA in a JSON string — the client renders it safely; nothing here emits
/// HTML.
fn handle_source_api(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let raw = url.split('?').next().unwrap_or(url);
    let sha = url_decode(
        raw.strip_prefix("/api/source/")
            .unwrap_or("")
            .trim_end_matches('/'),
    );
    if sha.is_empty() {
        return json_response(400, r#"{"error":"missing source sha"}"#);
    }

    let Some(source) = model.sources.iter().find(|s| s.sha256 == sha) else {
        let body = serde_json::json!({ "error": format!("source not found: {sha}") });
        return json_response(404, &body.to_string());
    };

    // Memory layer: evidence rows keyed by the source sha or its pack dir.
    let evidence = state.current_evidence();
    let evidence_available = evidence.is_some();
    let pack_dir = source.pack_dir.as_deref();
    let belongs = |row_sha: Option<&str>, row_pack: &str| {
        row_sha == Some(sha.as_str()) || pack_dir == Some(row_pack)
    };
    let cards: Vec<serde_json::Value> = evidence
        .as_ref()
        .map(|ev| {
            ev.cards
                .iter()
                .filter(|c| belongs(c.source_sha256.as_deref(), &c.pack_dir))
                .map(|c| serde_json::json!({ "title": c.title, "content": c.content }))
                .collect()
        })
        .unwrap_or_default();
    let units: Vec<serde_json::Value> = evidence
        .as_ref()
        .map(|ev| {
            ev.units
                .iter()
                .filter(|u| belongs(u.source_sha256.as_deref(), &u.pack_dir))
                .map(|u| {
                    serde_json::json!({
                        "unit_id": u.unit_id,
                        "text": u.text,
                        "quote": u.quote,
                        "line": u.line,
                        "attribution": u.attribution,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    // Crystal layer: ClaimRow.sources holds case ids (last pack_dir segment).
    let case_id = pack_dir.and_then(graph::last_path_segment);
    let mut citing: Vec<&ovp_index::ClaimRow> = match case_id {
        Some(case) => model
            .claims
            .iter()
            .filter(|c| c.sources.iter().any(|s| s == case))
            .collect(),
        None => Vec::new(),
    };
    citing.sort_by_key(|c| {
        (
            match c.status {
                ovp_index::ClaimStatus::Durable => 0u8,
                ovp_index::ClaimStatus::Caveated => 1,
                _ => 2,
            },
            c.claim_id.clone(),
        )
    });

    let (markdown, truncated, doc_error) = read_source_doc(state, source.rel_path.as_deref());

    let body = serde_json::json!({
        "source": source,
        "memory": {
            "evidence_available": evidence_available,
            "cards": cards,
            "units": units,
        },
        "citing_claims": citing,
        "doc": {
            "markdown": markdown,
            "truncated": truncated,
            "error": doc_error,
        },
    });
    json_response(200, &body.to_string())
}

/// Read the source markdown from the vault, capped at MAX_SOURCE_DOC_BYTES.
/// All failure modes become an explicit error string — the endpoint always
/// answers.
fn read_source_doc(
    state: &AppState,
    rel_path: Option<&str>,
) -> (Option<String>, bool, Option<String>) {
    let Some(rel) = rel_path else {
        return (None, false, None);
    };
    // rel_path comes from our own index, but never trust it anyway: reject
    // parent components and absolute roots — including Windows prefixes
    // (`C:\…`, `\\srv\share`) that `is_absolute()` misses on Unix and that
    // would make `Path::join` discard the vault root entirely.
    if !is_plain_relative(rel) {
        return (None, false, Some("source path rejected".into()));
    }
    let recorded = state.vault_root.join(rel);
    let path = if recorded.is_file() {
        recorded
    } else if let Some(moved) = lifecycle_moved_path(state, rel) {
        moved
    } else {
        recorded
    };
    match std::fs::read_to_string(&path) {
        Ok(mut text) => {
            let truncated = text.len() > MAX_SOURCE_DOC_BYTES;
            if truncated {
                let mut cut = MAX_SOURCE_DOC_BYTES;
                while cut > 0 && !text.is_char_boundary(cut) {
                    cut -= 1;
                }
                text.truncate(cut);
            }
            (Some(text), truncated, None)
        }
        Err(e) => (None, false, Some(format!("{rel}: {e}"))),
    }
}

/// Lifecycle-move fallback: `SourceRow.rel_path` records the INTAKE location
/// (`50-Inbox/01-Raw/<month>/…`), but the daily lifecycle step moves
/// processed sources to `50-Inbox/03-Processed/<month>/…` keeping the same
/// trailing subpath. When the recorded path misses and sits under the raw
/// inbox dir, retry the processed dir — both directory names come from
/// `VaultLayout`, never hardcoded here. `rel` is already traversal-checked
/// by the caller. Returns the candidate only when it actually exists.
fn lifecycle_moved_path(state: &AppState, rel: &str) -> Option<PathBuf> {
    let raw_prefix = format!("{}/", state.layout.inbox_raw_dir());
    let rest = rel.strip_prefix(&raw_prefix)?;
    let (month, file) = rest.split_once('/')?;
    let candidate = state
        .vault_root
        .join(state.layout.processed_dir(month))
        .join(file);
    candidate.is_file().then_some(candidate)
}

fn handle_flow(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let t = &model.totals;
    let total_units: usize = model.packs.iter().map(|p| p.units).sum();
    let total_cards: usize = model.packs.iter().map(|p| p.cards).sum();

    let body = serde_json::json!({
        "stages": ["intake", "reader", "units", "cards", "crystal", "blocked", "needs_content"],
        "flows": [
            { "from": "intake", "to": "reader", "value": t.processed, "label": "processed" },
            { "from": "intake", "to": "blocked", "value": t.blocked, "label": "blocked" },
            { "from": "intake", "to": "needs_content", "value": t.needs_content, "label": "needs content" },
            { "from": "reader", "to": "units", "value": total_units, "label": "accepted units" },
            { "from": "units", "to": "cards", "value": total_cards, "label": "cards kept" },
            { "from": "cards", "to": "crystal", "value": t.claims_durable, "label": "durable claims" },
        ],
    });
    json_response(200, &body.to_string())
}

/// GET /api/settings — read-only server/vault configuration for the System
/// page (B5, v1). Everything here is display data: the vault path, the index
/// projection's schema/date/counts (null when no index is built yet), whether
/// ask has an LLM behind it, the ask guardrails, and the server version.
/// Nothing is writable over HTTP — settings changes happen at the CLI.
fn handle_settings(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = state.current_model();
    let body = serde_json::json!({
        "vault_root": state.vault_root.display().to_string(),
        "schema_version": model.as_ref().map(|m| m.schema.clone()),
        "index_date": model.as_ref().map(|m| m.date.clone()),
        // Provenance stamp (P1): the wall-clock build instant, its run id, and
        // the server-computed age. The System page shows "as of <built_at> ·
        // N min ago" so `index_date` (a day string) can no longer stand in for
        // freshness.
        "built_at": model.as_ref().and_then(|m| m.built_at.clone()),
        "run_id": model.as_ref().and_then(|m| m.run_id.clone()),
        "age_seconds": model.as_ref().and_then(|m| age_seconds(m.built_at.as_deref())),
        "counts": model.as_ref().map(|m| serde_json::json!({
            "sources": m.totals.sources,
            "packs": m.totals.packs,
            "claims": m.totals.claims_durable + m.totals.claims_caveated,
        })),
        // LIVE queued backlog (01-Raw walk, TTL-cached) — the authoritative-now
        // figure the portal shows as "Queued", ticking down as a run drains the
        // inbox. `queued_at_build` is the projection's frozen end-of-run value,
        // kept for provenance ("live 159 · projection 175 as of <date>"); the
        // two legitimately differ mid-run. `queued_at_build` is null pre-index.
        "queued_live": state.live_queued_count(model.as_ref()),
        "queued_at_build": model.as_ref().map(|m| m.totals.queued),
        "llm_configured": state.ask_client.is_some(),
        "ask_limits": {
            "timeout_secs": state.ask_timeout.as_secs(),
            "max_concurrent": state.ask_slots.max,
        },
        // Run-liveness heartbeat (OVP2 observability P0) so `schedule status`
        // and any client read the same last-run block uniformly. Read LIVE from
        // `.ovp/last-run.json` (NOT the baked index snapshot, which can be a
        // stale "running"). Null on a fresh vault. The client derives age from
        // started_at/ended_at + now — the server ships no `minutes_since`.
        "last_run": state.current_last_run(),
        "version": env!("CARGO_PKG_VERSION"),
    });
    json_stamped(200, &body.to_string(), model.as_ref())
}

/// The request headers `POST /api/ask` validates — the endpoint triggers
/// paid LLM calls, so it gets cross-site hardening (see `handle_ask`).
struct AskHeaders {
    content_type: Option<String>,
    origin: Option<String>,
}

impl AskHeaders {
    fn of(request: &tiny_http::Request) -> Self {
        let get = |name: &str| {
            request
                .headers()
                .iter()
                .find(|h| h.field.as_str().as_str().eq_ignore_ascii_case(name))
                .map(|h| h.value.as_str().to_string())
        };
        Self {
            content_type: get("content-type"),
            origin: get("origin"),
        }
    }
}

/// `Origin` values a locally-served page legitimately sends: http(s) on a
/// loopback host. Any PORT is accepted — the vite dev server proxies /api
/// from its own port, so pinning the serve port would break `npm run dev`.
/// The trust boundary is "pages served from this machine"; `null` and
/// foreign hosts are rejected.
fn is_loopback_origin(origin: &str) -> bool {
    let rest = origin
        .strip_prefix("http://")
        .or_else(|| origin.strip_prefix("https://"));
    let Some(rest) = rest else {
        return false;
    };
    let host_port = rest.split('/').next().unwrap_or(rest);
    let host = if let Some(v6) = host_port.strip_prefix('[') {
        v6.split(']').next().unwrap_or("")
    } else {
        host_port.split(':').next().unwrap_or(host_port)
    };
    host.eq_ignore_ascii_case("localhost") || host == "127.0.0.1" || host == "::1"
}

/// Bounded admission for `/api/ask` — the slot is acquired BEFORE any body
/// bytes are read (slow-loris posts must not pin reader threads), and a
/// saturated server answers 429 without spawning or reading anything.
fn admit_ask_slot(state: &AppState) -> Result<AskSlot, Response<std::io::Cursor<Vec<u8>>>> {
    state
        .ask_slots
        .try_acquire()
        .ok_or_else(|| json_response(429, r#"{"error":"ask busy","code":"ask_busy"}"#))
}

/// POST /api/ask `{"question": "..."}` — the portal Ask page (design §3.5).
/// Runs the `ovp-memory::ask` pipeline against the cached model/evidence
/// with the injected LLM client factory, saves the chat like `ovp2 ask
/// --save`, and answers `{answer, citations, verified, context_hits, chat}`.
/// The LLM call runs on a worker thread with a wall-clock guard; the whole
/// handler already sits on a detached thread (see `serve_loop`), so a slow
/// provider can never stall other requests.
///
/// Cross-site hardening (a webpage anywhere can POST at localhost): the
/// body must be declared `application/json` — a CORS-"simple" text/plain
/// POST is refused with 415, and a real JSON POST from a foreign origin
/// needs a CORS preflight this server never grants. Belt-and-braces, any
/// attached `Origin` must additionally be loopback (403 otherwise).
fn handle_ask(
    state: &AppState,
    headers: &AskHeaders,
    body: &str,
    slot: AskSlot,
) -> Response<std::io::Cursor<Vec<u8>>> {
    let is_json = headers
        .content_type
        .as_deref()
        .and_then(|v| v.split(';').next())
        .map(|v| v.trim().eq_ignore_ascii_case("application/json"))
        .unwrap_or(false);
    if !is_json {
        return json_response(415, r#"{"error":"content-type must be application/json"}"#);
    }
    if let Some(origin) = headers.origin.as_deref()
        && !is_loopback_origin(origin) {
            return json_response(403, r#"{"error":"cross-origin ask rejected"}"#);
        }
    if body.len() > MAX_POST_BODY_BYTES {
        return json_response(400, r#"{"error":"request body too large"}"#);
    }
    let parsed: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(e) => {
            let body = serde_json::json!({ "error": format!("invalid JSON body: {e}") });
            return json_response(400, &body.to_string());
        }
    };
    let question = parsed
        .get("question")
        .and_then(|q| q.as_str())
        .map(str::trim)
        .unwrap_or("");
    if question.is_empty() {
        return json_response(
            400,
            r#"{"error":"body must be {\"question\": \"<non-empty string>\"}"}"#,
        );
    }

    // Fail-loud config checks BEFORE spawning anything. The `code` field is
    // a stable machine-readable discriminator for the portal (the human
    // `error` text may change).
    let Some(factory) = state.ask_client.clone() else {
        return json_response(
            503,
            r#"{"error":"llm not configured","code":"llm_not_configured"}"#,
        );
    };
    let Some(model) = state.current_model() else {
        return json_response(
            503,
            r#"{"error":"index not available","code":"index_unavailable"}"#,
        );
    };

    // The slot was acquired at admission (before the body was even read —
    // see serve_loop) and moves INTO the pipeline thread: even after the
    // guard 504s below, the still-running provider call keeps its slot
    // until it returns. Validation failures above drop it immediately.

    let evidence = state.current_evidence();
    let vault_root = state.vault_root.clone();
    let question = question.to_string();

    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let _slot = slot; // held for the WHOLE pipeline, freed on drop
        let result = run_ask(&factory, &model, evidence.as_ref(), &question, &vault_root);
        let _ = tx.send(result);
    });

    match rx.recv_timeout(state.ask_timeout) {
        Ok(Ok(payload)) => json_response(200, &payload.to_string()),
        Ok(Err(e)) => {
            let body = serde_json::json!({ "error": e });
            json_response(502, &body.to_string())
        }
        Err(_) => {
            // Honest 504: recv_timeout does NOT cancel the provider call —
            // it finishes in the background (and still saves the chat).
            let body = serde_json::json!({
                "error": format!(
                    "no answer within {}s; the request was not cancelled and may \
                     still complete in the background",
                    state.ask_timeout.as_secs()
                ),
                "code": "ask_timeout",
            });
            json_response(504, &body.to_string())
        }
    }
}

/// The worker side of /api/ask: build the client, run the pipeline (chat
/// always saved — parity with `ovp2 ask --save`), shape the JSON payload.
fn run_ask(
    factory: &AskClientFactory,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
    question: &str,
    vault_root: &std::path::Path,
) -> Result<serde_json::Value, String> {
    let mut client = factory()?;
    let args = AskArgs {
        question: question.to_string(),
        save_chat: true,
        ..Default::default()
    };
    let result = ask_with_optional_evidence(model, evidence, client.as_mut(), &args, vault_root)?;
    Ok(ask_response_json(model, &result))
}

/// Shape the /api/ask response. `citations` lists the keys the answer
/// actually cites, in first-appearance order (the UI numbers its `[1][2]`
/// markers by this order); each entry resolves to the evidence item behind
/// it, with a portal deep link: claims → `/knowledge#<claim_id>`,
/// cards/units → `/library/<sha>` via the pack lookup. Legacy packs without
/// a source sha get NO link (never a 404 target); citations the verifier
/// could not back get `verified: false`.
fn ask_response_json(model: &IndexModel, result: &AskResult) -> serde_json::Value {
    let pack_sha: HashMap<&str, &str> = model
        .packs
        .iter()
        .filter_map(|p| Some((p.pack_dir.as_str(), p.source_sha256.as_deref()?)))
        .collect();
    let missing: HashSet<&str> = result
        .verification
        .as_ref()
        .map(|r| r.missing.iter().map(String::as_str).collect())
        .unwrap_or_default();

    let citations: Vec<serde_json::Value> = citations_in_order(&result.answer)
        .into_iter()
        .map(|key| {
            let item = result.evidence.iter().find(|e| citation_key(e) == key);
            let verified = item.is_some() && !missing.contains(key.as_str());
            match item {
                Some(item) => serde_json::json!({
                    "id": key,
                    "kind": kind_str(item.kind),
                    "title": item.title,
                    "snippet": citation_snippet(item),
                    "link_target": citation_link(item, &pack_sha),
                    "verified": verified,
                }),
                // Cited but never supplied as evidence — surfaced so the UI
                // can render the warn pill instead of dropping the marker.
                None => serde_json::json!({
                    "id": key,
                    "kind": key.split(':').next().unwrap_or(""),
                    "title": serde_json::Value::Null,
                    "snippet": serde_json::Value::Null,
                    "link_target": serde_json::Value::Null,
                    "verified": false,
                }),
            }
        })
        .collect();

    serde_json::json!({
        "answer": result.answer,
        "citations": citations,
        "verified": result.verification,
        "context_hits": result.context_hits,
        "chat": result
            .chat_file
            .as_deref()
            .and_then(|p| p.file_stem())
            .and_then(|s| s.to_str()),
    })
}

fn kind_str(kind: EvidenceKind) -> &'static str {
    match kind {
        EvidenceKind::Unit => "unit",
        EvidenceKind::Card => "card",
        EvidenceKind::Claim => "claim",
    }
}

/// Short display text for a citation panel entry: the exact quote for
/// units, the body payload (sans the `Content:`/`Claim:` field prefix) for
/// cards and claims. Char-safe clipped.
fn citation_snippet(item: &EvidenceItem) -> String {
    const MAX: usize = 240;
    let raw = match (&item.quote, item.kind) {
        (Some(quote), EvidenceKind::Unit) => quote.as_str(),
        _ => item
            .body
            .lines()
            .next()
            .map(|l| {
                l.strip_prefix("Claim: ")
                    .or_else(|| l.strip_prefix("Content: "))
                    .or_else(|| l.strip_prefix("Text: "))
                    .unwrap_or(l)
            })
            .unwrap_or(""),
    };
    if raw.chars().count() <= MAX {
        return raw.to_string();
    }
    let mut clipped: String = raw.chars().take(MAX - 1).collect();
    clipped.push('…');
    clipped
}

/// Portal deep link for a citation (or None — the sha-guard: legacy packs
/// without a source sha must not produce a dead `/library/...` link).
fn citation_link(item: &EvidenceItem, pack_sha: &HashMap<&str, &str>) -> Option<String> {
    match item.kind {
        EvidenceKind::Claim => Some(format!("/knowledge#{}", item.id)),
        EvidenceKind::Card | EvidenceKind::Unit => {
            let pack_dir = item.path.as_deref()?.strip_suffix("/reader.md")?;
            let sha = pack_sha.get(pack_dir)?;
            Some(format!("/library/{sha}"))
        }
    }
}

fn chats_dir(state: &AppState) -> PathBuf {
    // Same location `ovp-memory::ask` writes to (`.ovp/chats/<ts>.md`).
    state.vault_root.join(".ovp").join("chats")
}

/// GET /api/chats — saved ask transcripts, newest first:
/// `[{name, mtime}]` (mtime = unix seconds; the client formats the date).
/// A vault without any chats answers an empty list, not an error.
fn handle_chats_list(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let mut rows: Vec<(u64, String)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(chats_dir(state)) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("md") {
                continue;
            }
            let Some(name) = path.file_stem().and_then(|s| s.to_str()) else {
                continue;
            };
            let mtime = entry
                .metadata()
                .ok()
                .and_then(|m| m.modified().ok())
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or(0);
            rows.push((mtime, name.to_string()));
        }
    }
    rows.sort_by(|a, b| b.cmp(a));
    let list: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(mtime, name)| serde_json::json!({ "name": name, "mtime": mtime }))
        .collect();
    let body = serde_json::to_string(&list).unwrap_or_else(|_| "[]".into());
    json_response(200, &body)
}

/// GET /api/chats/:name — one saved transcript as raw markdown (the client
/// renders it with the same escape-first renderer as source bodies). Names
/// are single path components; anything else is rejected, never joined.
fn handle_chat_detail(state: &AppState, path: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let name = url_decode(path.strip_prefix("/api/chats/").unwrap_or(""));
    let name = name.strip_suffix(".md").unwrap_or(&name);
    let valid = !name.is_empty()
        && !name.contains("..")
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'));
    if !valid {
        return json_response(400, r#"{"error":"invalid chat name"}"#);
    }
    match std::fs::read_to_string(chats_dir(state).join(format!("{name}.md"))) {
        Ok(md) => {
            let header =
                Header::from_bytes("Content-Type", "text/markdown; charset=utf-8").unwrap();
            Response::from_data(md.into_bytes())
                .with_header(header)
                .with_status_code(200)
        }
        Err(_) => json_response(404, r#"{"error":"chat not found"}"#),
    }
}

/// Result of static-path resolution — kept separate from `Response` so the
/// routing precedence is testable on content, not just status codes.
enum Resolved {
    File {
        body: Vec<u8>,
        content_type: &'static str,
    },
    BadRequest,
    NotFound,
}

fn serve_static(state: &AppState, url_path: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    match resolve_static(state, url_path) {
        Resolved::File { body, content_type } => {
            // HTML (the SPA shell + legacy console pages) must NEVER be cached:
            // the browser has to re-fetch index.html on every load so it picks
            // up the new content-hashed asset URLs after a rebuild/deploy —
            // otherwise a stale cached shell keeps pointing at old JS and the
            // portal "won't update" until a manual hard reload. Content-hashed
            // assets (assets/index-<hash>.js|css, fonts) are immutable: their
            // URL changes when content changes, so cache them for a year.
            let cache_control = if content_type.starts_with("text/html") {
                "no-cache, must-revalidate"
            } else {
                "public, max-age=31536000, immutable"
            };
            Response::from_data(body)
                .with_header(Header::from_bytes("Content-Type", content_type).unwrap())
                .with_header(Header::from_bytes("Cache-Control", cache_control).unwrap())
                .with_status_code(200)
        }
        Resolved::BadRequest => text_response(400, "Bad Request"),
        Resolved::NotFound => text_response(404, "Not Found"),
    }
}

/// Static routing precedence (portal v2 B1) — the SPA owns the site root,
/// legacy generated pages stay reachable by exact filename:
///
/// 1. `/api/*` never reaches here (dispatched in `run_server` first).
/// 2. `/legacy-index.html` → the OLD generated console index
///    (`<vault>/.ovp/console/index.html`), kept reachable after the SPA
///    took over `/`.
/// 3. SPA app build, exact file: deployed `<vault>/.ovp/console/app/`
///    first, then the `--viz-dir` overlay. `/` maps to `index.html`, so
///    the portal is the root whenever an app build is present.
/// 4. Legacy console file under `<vault>/.ovp/console/` by exact filename
///    (`ops.html`, `audit.html`, `candidates.html`, pre-B1 `/viz/*`
///    assets, …). Without any app build this also serves the old console
///    index at `/` — backward compatible.
/// 5. Extensionless paths are SPA client routes (`/library`,
///    `/library/:sha`, `/search`, old `/viz/graph` deep links) → the SPA
///    `index.html`; the router takes over. Paths WITH an extension that
///    missed on disk are plain 404s.
fn resolve_static(state: &AppState, url_path: &str) -> Resolved {
    let console_dir = state.console_dir();

    // Deep links like /library?c=pinboard carry a query string; file
    // lookup (and client-route detection) must see the path only.
    let url_path = url_path.split('?').next().unwrap_or(url_path);
    let relative = if url_path == "/" || url_path.is_empty() {
        "index.html"
    } else {
        url_path.trim_start_matches('/')
    };

    // Prevent directory traversal / absolute-path escape. `Path::join`
    // DISCARDS the base when the RHS is absolute — including Windows
    // prefixes (`C:\evil`, `\\server\share`) that `is_absolute()` on Unix
    // and a plain `..` substring check both miss.
    if !is_plain_relative(relative) {
        return Resolved::BadRequest;
    }

    if relative == "legacy-index.html" {
        return match std::fs::read(console_dir.join("index.html")) {
            Ok(body) => Resolved::File {
                body,
                content_type: "text/html; charset=utf-8",
            },
            Err(_) => Resolved::NotFound,
        };
    }

    if let Some(body) = read_app_file(state, relative) {
        return Resolved::File {
            body,
            content_type: content_type_for(relative),
        };
    }

    let file_path = console_dir.join(relative);
    let file_path = if file_path.is_dir() {
        file_path.join("index.html")
    } else {
        file_path
    };
    if let Ok(body) = std::fs::read(&file_path) {
        let fname = file_path.to_string_lossy().to_string();
        return Resolved::File {
            body,
            content_type: content_type_for(&fname),
        };
    }

    if is_client_route(relative)
        && let Some(body) = read_app_file(state, "index.html") {
            return Resolved::File {
                body,
                content_type: "text/html; charset=utf-8",
            };
        }

    Resolved::NotFound
}

/// True only for a plain relative path: every `Path::components()` entry is
/// `Component::Normal` — no `ParentDir`, no `RootDir`, no Windows
/// `Component::Prefix` (`C:\`, `\\server\share`). Backslashes and drive
/// colons are ALSO rejected as raw bytes: on Unix `C:\evil` parses as one
/// Normal component, yet a Windows deployment would treat it as absolute
/// and `Path::join` would silently replace the base directory.
fn is_plain_relative(rel: &str) -> bool {
    if rel.is_empty() || rel.contains('\\') || rel.contains(':') {
        return false;
    }
    std::path::Path::new(rel)
        .components()
        .all(|c| matches!(c, std::path::Component::Normal(_)))
}

/// Read a root-relative asset from the SPA app build: the deployed
/// `<vault>/.ovp/console/app/` wins, then the `--viz-dir` overlay — so a
/// dev checkout can serve ANY vault without copying the build in.
/// resolve_static already rejects unsafe paths, but this is the function
/// that joins request input onto a directory, so it guards independently.
fn read_app_file(state: &AppState, rest: &str) -> Option<Vec<u8>> {
    if !is_plain_relative(rest) {
        return None;
    }
    let rel = std::path::Path::new(rest);
    let deployed = state.console_dir().join("app").join(rel);
    if let Ok(body) = std::fs::read(&deployed) {
        return Some(body);
    }
    let dir = state.viz_dir.as_ref()?;
    std::fs::read(dir.join(rel)).ok()
}

/// Extensionless path = SPA client route. Malformed paths (leading slash
/// remnants, empty segments) are not client routes — they must 404, never
/// get a 200 SPA shell.
fn is_client_route(relative: &str) -> bool {
    if relative.is_empty() || relative.starts_with('/') || relative.contains("//") {
        return false;
    }
    let last = relative.rsplit('/').next().unwrap_or(relative);
    !last.contains('.')
}

fn json_response(status: u16, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let data = body.as_bytes().to_vec();
    let header = Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap();
    Response::from_data(data)
        .with_header(header)
        .with_status_code(status)
}

/// Seconds since an RFC3339 `built_at` instant, per the server's wall clock.
/// `None` when the stamp is absent (pre-P1 index) or unparseable — the client
/// then shows "unknown age" rather than a fabricated 0. Clamped at 0 so a
/// slight clock skew never reports a negative age.
fn age_seconds(built_at: Option<&str>) -> Option<i64> {
    let built = chrono::DateTime::parse_from_rfc3339(built_at?).ok()?;
    Some((chrono::Utc::now().timestamp() - built.timestamp()).max(0))
}

/// `json_response` plus provenance HEADERS (`X-OVP-Built-At`, `X-OVP-Run-Id`,
/// `X-OVP-Age-Seconds`) — so array-bodied routes (`/api/find|search|graph|
/// claim|themes`) still echo which build answered. Absent fields are omitted;
/// the header set is the client's pairing signal that ledger reads and model
/// reads in one handler came from the same freshness.
fn json_stamped(
    status: u16,
    body: &str,
    model: Option<&IndexModel>,
) -> Response<std::io::Cursor<Vec<u8>>> {
    let mut resp = json_response(status, body);
    if let Some(m) = model {
        if let Some(built) = m.built_at.as_deref()
            && let Ok(h) = Header::from_bytes("X-OVP-Built-At", built.as_bytes())
        {
            resp.add_header(h);
        }
        if let Some(run) = m.run_id.as_deref()
            && let Ok(h) = Header::from_bytes("X-OVP-Run-Id", run.as_bytes())
        {
            resp.add_header(h);
        }
        if let Some(age) = age_seconds(m.built_at.as_deref())
            && let Ok(h) = Header::from_bytes("X-OVP-Age-Seconds", age.to_string().as_bytes())
        {
            resp.add_header(h);
        }
    }
    resp
}

fn text_response(status: u16, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let data = body.as_bytes().to_vec();
    let header = Header::from_bytes("Content-Type", "text/plain; charset=utf-8").unwrap();
    Response::from_data(data)
        .with_header(header)
        .with_status_code(status)
}

fn content_type_for(path: &str) -> &'static str {
    if path.ends_with(".html") {
        "text/html; charset=utf-8"
    } else if path.ends_with(".css") {
        "text/css; charset=utf-8"
    } else if path.ends_with(".js") {
        "application/javascript; charset=utf-8"
    } else if path.ends_with(".json") {
        "application/json; charset=utf-8"
    } else if path.ends_with(".svg") {
        "image/svg+xml"
    } else if path.ends_with(".woff2") {
        "font/woff2"
    } else if path.ends_with(".png") {
        "image/png"
    } else if path.ends_with(".txt") {
        "text/plain; charset=utf-8"
    } else {
        "application/octet-stream"
    }
}

fn parse_query_string(url: &str) -> std::collections::HashMap<String, String> {
    let mut map = std::collections::HashMap::new();
    if let Some(qs) = url.split('?').nth(1) {
        for pair in qs.split('&') {
            let mut kv = pair.splitn(2, '=');
            if let (Some(k), Some(v)) = (kv.next(), kv.next()) {
                let key = url_decode(k);
                let val = url_decode(v);
                map.insert(key, val);
            }
        }
    }
    map
}

fn url_decode(s: &str) -> String {
    // Decode into BYTES first, then re-validate as UTF-8: percent-escaped
    // multibyte sequences (e.g. Chinese theme labels — `%E6%99%BA…`) are one
    // character across SEVERAL escapes, so pushing each decoded byte as a
    // `char` would produce mojibake and break exact theme matching.
    let mut bytes = Vec::with_capacity(s.len());
    let mut iter = s.bytes();
    while let Some(b) = iter.next() {
        if b == b'%' {
            let hi = iter.next().unwrap_or(b'0');
            let lo = iter.next().unwrap_or(b'0');
            bytes.push(hex_val(hi) * 16 + hex_val(lo));
        } else if b == b'+' {
            bytes.push(b' ');
        } else {
            bytes.push(b);
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn hex_val(b: u8) -> u8 {
    match b {
        b'0'..=b'9' => b - b'0',
        b'a'..=b'f' => b - b'a' + 10,
        b'A'..=b'F' => b - b'A' + 10,
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("ovp-server-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn state(vault: PathBuf, viz_dir: Option<PathBuf>) -> AppState {
        AppState {
            vault_root: vault,
            layout: VaultLayout::new(),
            model: RwLock::new(Cached::default()),
            evidence: RwLock::new(Cached::default()),
            last_run: RwLock::new(Cached::default()),
            viz_dir,
            ask_client: None,
            // Fixed (not env-derived) so tests stay deterministic on
            // machines that export OVP_LLM_TIMEOUT_SECS.
            ask_timeout: Duration::from_secs(60),
            ask_slots: AskSlots::new(DEFAULT_MAX_CONCURRENT_ASKS),
            live_queued: RwLock::new(LiveQueued::default()),
        }
    }

    /// Read a response header value (test helper).
    fn header_value(
        resp: &Response<std::io::Cursor<Vec<u8>>>,
        name: &str,
    ) -> String {
        resp.headers()
            .iter()
            .find(|h| h.field.as_str().as_str().eq_ignore_ascii_case(name))
            .map(|h| h.value.as_str().to_string())
            .unwrap_or_default()
    }

        /// A ModelClient that replies with a fixed answer (or blocks) — the
    /// /api/ask tests never touch a real transport.
    struct ScriptedClient {
        text: String,
        delay: Duration,
    }

    impl ModelClient for ScriptedClient {
        fn call(
            &mut self,
            request: &ovp_llm::ModelRequest,
        ) -> Result<ovp_llm::ModelReply, ovp_llm::CallError> {
            std::thread::sleep(self.delay);
            Ok(ovp_llm::ModelReply {
                model: request.model.clone(),
                text: self.text.clone(),
                stop_reason: ovp_llm::StopReason::EndTurn,
                usage: ovp_llm::Usage {
                    input_tokens: 1,
                    output_tokens: 1,
                },
            })
        }
    }

    fn scripted_factory(text: &str, delay: Duration) -> AskClientFactory {
        let text = text.to_string();
        Arc::new(move || {
            Ok(Box::new(ScriptedClient {
                text: text.clone(),
                delay,
            }) as Box<dyn ModelClient>)
        })
    }

    /// The headers a well-behaved local client (the portal fetch) sends.
    fn json_headers() -> AskHeaders {
        AskHeaders {
            content_type: Some("application/json".into()),
            origin: None,
        }
    }

    /// Drive the full admission + handle path like serve_loop does.
    fn ask_with(
        st: &AppState,
        headers: &AskHeaders,
        body: &str,
    ) -> Response<std::io::Cursor<Vec<u8>>> {
        match admit_ask_slot(st) {
            Ok(slot) => handle_ask(st, headers, body, slot),
            Err(resp) => resp,
        }
    }

    /// Drive handle_ask like serve_loop's worker does, with good headers.
    fn ask(st: &AppState, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
        ask_with(st, &json_headers(), body)
    }

    /// Unwrap the resolved body for content assertions.
    fn body(r: Resolved) -> Vec<u8> {
        match r {
            Resolved::File { body, .. } => body,
            Resolved::BadRequest => panic!("expected file, got 400"),
            Resolved::NotFound => panic!("expected file, got 404"),
        }
    }

    fn is_not_found(r: Resolved) -> bool {
        matches!(r, Resolved::NotFound)
    }

    #[test]
    fn spa_owns_root_and_client_routes_legacy_by_exact_filename() {
        let root = temp_root("precedence");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console")).unwrap();
        std::fs::write(vault.join(".ovp/console/index.html"), "legacy-index").unwrap();
        std::fs::write(vault.join(".ovp/console/ops.html"), "legacy-ops").unwrap();
        let overlay = root.join("dist");
        std::fs::create_dir_all(overlay.join("assets")).unwrap();
        std::fs::write(overlay.join("index.html"), "spa").unwrap();
        std::fs::write(overlay.join("assets/app.js"), "js").unwrap();

        let st = state(vault.clone(), Some(overlay));

        // The SPA owns the portal root and /index.html…
        assert_eq!(body(resolve_static(&st, "/")), b"spa");
        assert_eq!(body(resolve_static(&st, "/index.html")), b"spa");
        // …and every client route (query strings stripped).
        assert_eq!(body(resolve_static(&st, "/library")), b"spa");
        assert_eq!(body(resolve_static(&st, "/library/84fbf6dc")), b"spa");
        assert_eq!(body(resolve_static(&st, "/search?lang=zh")), b"spa");
        // Pre-B1 deep links are client routes too (router redirects).
        assert_eq!(body(resolve_static(&st, "/viz/graph")), b"spa");
        // Hashed assets come from the overlay.
        assert_eq!(body(resolve_static(&st, "/assets/app.js")), b"js");
        // Legacy generated pages stay reachable by exact filename, and the
        // old console index moves to /legacy-index.html.
        assert_eq!(body(resolve_static(&st, "/ops.html")), b"legacy-ops");
        assert_eq!(
            body(resolve_static(&st, "/legacy-index.html")),
            b"legacy-index"
        );
        // A missed path WITH an extension is a plain 404, not the SPA shell.
        assert!(is_not_found(resolve_static(&st, "/nope.js")));
        assert!(is_not_found(resolve_static(&st, "/nope.html")));
        // Traversal / malformed paths never resolve.
        assert!(matches!(
            resolve_static(&st, "/../secret.txt"),
            Resolved::BadRequest
        ));
        // Windows-absolute forms would replace the join base entirely on a
        // Windows host (`is_absolute()` on Unix misses them) — rejected.
        assert!(matches!(
            resolve_static(&st, "/C:\\windows\\system32"),
            Resolved::BadRequest
        ));
        assert!(matches!(
            resolve_static(&st, "/\\\\srv\\share"),
            Resolved::BadRequest
        ));
        std::fs::write(root.join("secret.txt"), "nope").unwrap();
        let abs = format!("/viz/{}", root.join("secret.txt").display());
        assert!(is_not_found(resolve_static(&st, &abs)));
        assert!(is_not_found(resolve_static(&st, "/viz//etc/hosts")));

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn deployed_app_dir_wins_over_overlay() {
        let root = temp_root("app-dir");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console/app")).unwrap();
        std::fs::write(vault.join(".ovp/console/app/index.html"), "deployed").unwrap();
        let overlay = root.join("dist");
        std::fs::create_dir_all(&overlay).unwrap();
        std::fs::write(overlay.join("index.html"), "overlay").unwrap();

        let st = state(vault.clone(), Some(overlay));
        assert_eq!(body(resolve_static(&st, "/")), b"deployed");
        assert_eq!(body(resolve_static(&st, "/library")), b"deployed");

        // Deployed app also works with no overlay configured at all.
        let st = state(vault, None);
        assert_eq!(body(resolve_static(&st, "/")), b"deployed");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn without_app_build_legacy_console_stays_root() {
        let root = temp_root("no-app");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console")).unwrap();
        std::fs::write(vault.join(".ovp/console/index.html"), "legacy-index").unwrap();
        std::fs::write(vault.join(".ovp/console/ops.html"), "legacy-ops").unwrap();

        let st = state(vault, None);
        // Backward compatible: the old console remains the root…
        assert_eq!(body(resolve_static(&st, "/")), b"legacy-index");
        assert_eq!(body(resolve_static(&st, "/ops.html")), b"legacy-ops");
        assert_eq!(
            body(resolve_static(&st, "/legacy-index.html")),
            b"legacy-index"
        );
        // …and client routes have no SPA to fall back to.
        assert!(is_not_found(resolve_static(&st, "/library")));
        assert!(is_not_found(resolve_static(&st, "/viz/graph")));

        let _ = std::fs::remove_dir_all(&root);
    }

    fn body_json(resp: Response<std::io::Cursor<Vec<u8>>>) -> serde_json::Value {
        use std::io::Read;
        let mut out = Vec::new();
        resp.into_reader().read_to_end(&mut out).unwrap();
        serde_json::from_slice(&out).expect("response body must be valid JSON")
    }

    /// Vault with one processed source (hostile markdown body), its pack,
    /// evidence sidecar (one card + one grounded unit) and one claim citing
    /// the case — the /api/source three-layer fixture.
    fn portal_vault(name: &str, rel_path: &str, body: &str) -> PathBuf {
        use ovp_index::evidence::{CardEvidenceRow, UnitEvidenceRow};
        use ovp_index::{
            ClaimRow, ClaimStatus, EvidenceModel, OpsState, PackRow, SourceRow, SourceStatus,
            Totals,
        };
        let root = temp_root(name);
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join("50-Inbox/03-Processed")).unwrap();
        std::fs::write(vault.join("50-Inbox/03-Processed/good.md"), body).unwrap();

        let model = IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-07-09".into(),
            // Provenance stamp present so the P1 echo tests have a value; the
            // instant is fixed 1s in the past so `age_seconds` is a small,
            // stable, non-negative number.
            built_at: Some("2026-07-09T00:00:00Z".into()),
            run_id: Some("daily-2026-07-09".into()),
            totals: Totals {
                sources: 1,
                processed: 1,
                packs: 1,
                ..Default::default()
            },
            sources: vec![SourceRow {
                sha256: "aaaa1111".into(),
                status: SourceStatus::Processed,
                title: Some("Good Article".into()),
                url: Some("https://example.com/good".into()),
                rel_path: Some(rel_path.into()),
                date: Some("2026-07-09".into()),
                last_run_id: None,
                pack_dir: Some("40-Resources/Reader/good".into()),
                fail_count: 0,
                last_reason: None,
            }],
            packs: vec![PackRow {
                pack_dir: "40-Resources/Reader/good".into(),
                title: "Good Article".into(),
                date: Some("2026-07-09".into()),
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec!["Card One".into()],
                source_sha256: Some("aaaa1111".into()),
            }],
            claims: vec![ClaimRow {
                claim_id: "c01".into(),
                claim: "Filesystem works as memory.".into(),
                theme: Some("memory".into()),
                status: ClaimStatus::Durable,
                sources: vec!["good".into()],
                strength: Some("supported".into()),
                run_id: None,
                lane: None,
            }],
            runs: vec![],
            ops: OpsState::default(),
        };
        ovp_index::write_index(&vault, &model).unwrap();

        let evidence = EvidenceModel {
            schema: "ovp.index.evidence/v1".into(),
            date: "2026-07-09".into(),
            cards: vec![CardEvidenceRow {
                id: "card:40-Resources/Reader/good:0".into(),
                pack_dir: "40-Resources/Reader/good".into(),
                source_sha256: Some("aaaa1111".into()),
                source_title: "Good Article".into(),
                title: "Card One".into(),
                content: "Body of card one.".into(),
                unit_type: None,
                cited_unit_ids: vec!["u-001".into()],
            }],
            units: vec![UnitEvidenceRow {
                id: "unit:40-Resources/Reader/good:u-001".into(),
                pack_dir: "40-Resources/Reader/good".into(),
                source_sha256: Some("aaaa1111".into()),
                source_title: "Good Article".into(),
                unit_id: "u-001".into(),
                text: "The unit text.".into(),
                quote: "the exact quote".into(),
                line: Some(14),
                attribution: "author".into(),
                modality: "asserted".into(),
            }],
            warnings: vec![],
        };
        ovp_index::write_evidence(&vault, &evidence).unwrap();
        vault
    }

    #[test]
    fn source_api_returns_three_layers_as_json_data() {
        // Hostile markdown must pass through as DATA in the JSON payload —
        // never HTML-escaped (the client renders it safely), never live.
        let vault = portal_vault(
            "source-api",
            "50-Inbox/03-Processed/good.md",
            "# Heading\n\nbody with <script>alert(1)</script>\n",
        );
        let st = state(vault.clone(), None);

        let resp = handle_source_api(&st, "/api/source/aaaa1111");
        assert_eq!(resp.status_code(), 200);
        let ct = resp
            .headers()
            .iter()
            .find(|h| {
                h.field
                    .as_str()
                    .as_str()
                    .eq_ignore_ascii_case("content-type")
            })
            .map(|h| h.value.as_str().to_string());
        assert_eq!(ct.as_deref(), Some("application/json; charset=utf-8"));

        let v = body_json(resp);
        assert_eq!(v["source"]["sha256"], "aaaa1111");
        assert_eq!(v["source"]["title"], "Good Article");
        assert_eq!(v["memory"]["evidence_available"], true);
        assert_eq!(v["memory"]["cards"][0]["title"], "Card One");
        assert_eq!(v["memory"]["units"][0]["unit_id"], "u-001");
        assert_eq!(v["memory"]["units"][0]["line"], 14);
        assert_eq!(v["citing_claims"][0]["claim_id"], "c01");
        assert_eq!(v["citing_claims"][0]["status"], "durable");
        // The XSS payload survives as a JSON string, exactly as written.
        let md = v["doc"]["markdown"].as_str().unwrap();
        assert!(md.contains("<script>alert(1)</script>"));
        assert!(!md.contains("&lt;script&gt;"));
        assert_eq!(v["doc"]["truncated"], false);
        assert!(v["doc"]["error"].is_null());

        // Unknown sha → JSON 404, not HTML.
        let missing = handle_source_api(&st, "/api/source/deadbeef");
        assert_eq!(missing.status_code(), 404);
        let v = body_json(missing);
        assert!(v["error"].as_str().unwrap().contains("deadbeef"));

        // Missing sha segment → 400.
        assert_eq!(handle_source_api(&st, "/api/source/").status_code(), 400);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_follows_lifecycle_move_raw_to_processed() {
        // rel_path records the intake location, but the daily lifecycle step
        // moved the file to the processed dir (same month + filename). The
        // doc must still resolve — via VaultLayout, not the stale path.
        let vault = portal_vault(
            "source-moved",
            "50-Inbox/01-Raw/2026-06/good.md",
            "unused body\n",
        );
        assert!(!vault.join("50-Inbox/01-Raw/2026-06/good.md").exists());
        std::fs::create_dir_all(vault.join("50-Inbox/03-Processed/2026-06")).unwrap();
        std::fs::write(
            vault.join("50-Inbox/03-Processed/2026-06/good.md"),
            "# Moved\n\nlifecycle-moved body\n",
        )
        .unwrap();

        let st = state(vault.clone(), None);
        let v = body_json(handle_source_api(&st, "/api/source/aaaa1111"));
        let md = v["doc"]["markdown"].as_str().expect("markdown resolved");
        assert!(md.contains("lifecycle-moved body"));
        assert!(v["doc"]["error"].is_null());

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    /// Write a two-theme crystal ledger into the vault so the scoped
    /// /api/graph endpoints have real records to shape.
    fn write_ledger(vault: &std::path::Path) {
        use ovp_domain::crystal::{
            DurableCitation, FinalClass, ProvenanceClass, StoreOp, StrengthClass,
        };
        let rec = |key: &str, theme: &str, case: &str, unit: &str| DurableRecord {
            claim_key: key.into(),
            claim_id: format!("id-{key}"),
            claim: format!("claim text for {key}"),
            theme: theme.into(),
            source_cases: vec![case.into()],
            citations: vec![DurableCitation {
                case_id: case.into(),
                unit_id: unit.into(),
                quote: format!("quote {unit}"),
                resolved_line: None,
            }],
            provenance_score: 0.8,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "test".into(),
            final_class: FinalClass::Durable,
            run_id: "r1".into(),
            status: CrystalStatus::Active,
        };
        let events = [
            StoreEvent {
                op: StoreOp::Write,
                record: rec("a", "alpha", "good", "u-001"),
                supersedes: None,
                reason: None,
            },
            StoreEvent {
                op: StoreOp::Write,
                record: rec("b", "beta", "good", "u-002"),
                supersedes: None,
                reason: None,
            },
        ];
        let dir = vault.join(VaultLayout::new().crystal_store_dir());
        std::fs::create_dir_all(&dir).unwrap();
        let lines: Vec<String> = events
            .iter()
            .map(|e| serde_json::to_string(e).unwrap())
            .collect();
        std::fs::write(dir.join("ledger.jsonl"), lines.join("\n") + "\n").unwrap();
    }

    #[test]
    fn graph_scope_global_returns_overview_shape() {
        let vault = portal_vault("graph-global", "50-Inbox/03-Processed/good.md", "body\n");
        write_ledger(&vault);
        let st = state(vault.clone(), None);

        let resp = dispatch(&st, Method::Get, "/api/graph?scope=global", "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert_eq!(v["mode"], "overview");
        // Overview = claims only, with community metadata alongside.
        let nodes = v["nodes"].as_array().unwrap();
        assert_eq!(nodes.len(), 2);
        assert!(nodes.iter().all(|n| n["type"] == "claim"));
        assert!(v["communities"].is_array());
        assert_eq!(v["truncated"], false);
        assert!(v["total_nodes"].as_u64().unwrap() >= 2);

        // limit is honored and flags truncation.
        let v = body_json(dispatch(
            &st,
            Method::Get,
            "/api/graph?scope=global&limit=1",
            "",
        ));
        assert_eq!(v["nodes"].as_array().unwrap().len(), 1);
        assert_eq!(v["truncated"], true);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn graph_scope_theme_filters_and_unknown_theme_is_404() {
        let vault = portal_vault("graph-theme", "50-Inbox/03-Processed/good.md", "body\n");
        write_ledger(&vault);
        let st = state(vault.clone(), None);

        let resp = dispatch(&st, Method::Get, "/api/graph?scope=theme&theme=alpha", "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert_eq!(v["mode"], "theme");
        let ids: Vec<&str> = v["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["id"].as_str().unwrap())
            .collect();
        // Theme alpha's claim + its source (case `good` → sha aaaa1111 via
        // the pack lookup); theme beta's claim stays out.
        assert!(ids.contains(&"claim:a"));
        assert!(ids.contains(&"source:aaaa1111"));
        assert!(!ids.contains(&"claim:b"));

        // Unknown theme → 404; missing/unknown scope params → 400.
        let resp = dispatch(&st, Method::Get, "/api/graph?scope=theme&theme=nope", "");
        assert_eq!(resp.status_code(), 404);
        let resp = dispatch(&st, Method::Get, "/api/graph?scope=theme", "");
        assert_eq!(resp.status_code(), 400);
        let resp = dispatch(&st, Method::Get, "/api/graph?scope=galaxy", "");
        assert_eq!(resp.status_code(), 400);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn themes_json_projects_record_themes_and_utf8_query_params_decode() {
        let vault = portal_vault("themes-proj", "50-Inbox/03-Processed/good.md", "body\n");
        write_ledger(&vault);
        // Both ledger claims cite case `good`; the projection maps it to a
        // bilingual community label, overriding the baked alpha/beta themes.
        let store = vault.join(VaultLayout::new().crystal_store_dir());
        std::fs::write(
            store.join("themes.json"),
            serde_json::json!({
                "schema": "ovp.themes/v1",
                "model": "test-model",
                "params": {"k": 10, "cosine_threshold": 0.5, "resolution": 1.5,
                            "seed": 42, "text_prefix": "", "head_chars": 1500},
                "generated_from": "deadbeef",
                "packs": {"good": 0},
                "communities": [{"id": 0, "label": "智能体记忆 Agent memory",
                                  "label_zh": "智能体记忆", "keywords": ["memory"],
                                  "size": 1}]
            })
            .to_string(),
        )
        .unwrap();
        let st = state(vault.clone(), None);

        // /api/themes reflects the projection, not the ledger themes.
        let v = body_json(dispatch(&st, Method::Get, "/api/themes", ""));
        let themes: Vec<&str> = v
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["theme"].as_str().unwrap())
            .collect();
        assert_eq!(themes, vec!["智能体记忆 Agent memory"]);

        // A percent-encoded multibyte theme (what the portal's
        // encodeURIComponent produces) round-trips into the exact label.
        let url = "/api/graph?scope=theme&theme=%E6%99%BA%E8%83%BD%E4%BD%93%E8%AE%B0%E5%BF%86%20Agent%20memory";
        let resp = dispatch(&st, Method::Get, url, "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        let ids: Vec<&str> = v["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["id"].as_str().unwrap())
            .collect();
        assert!(ids.contains(&"claim:a"), "{ids:?}");
        assert!(ids.contains(&"claim:b"), "{ids:?}");

        // The retired ledger theme no longer resolves.
        let resp = dispatch(&st, Method::Get, "/api/graph?scope=theme&theme=alpha", "");
        assert_eq!(resp.status_code(), 404);

        // Corrupt themes.json degrades to ledger passthrough (server keeps
        // serving; the index build is where corruption fails loud).
        std::fs::write(store.join("themes.json"), "not json").unwrap();
        let v = body_json(dispatch(&st, Method::Get, "/api/themes", ""));
        let themes: Vec<&str> = v
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["theme"].as_str().unwrap())
            .collect();
        assert_eq!(themes, vec!["alpha", "beta"]);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn url_decode_handles_multibyte_utf8() {
        assert_eq!(url_decode("%E6%99%BA%E8%83%BD"), "智能");
        assert_eq!(url_decode("a+b%20c"), "a b c");
        assert_eq!(url_decode("plain"), "plain");
    }

    #[test]
    fn graph_neighborhood_includes_memory_cards_from_evidence() {
        // The portal_vault fixture ships one card for source aaaa1111 in the
        // evidence sidecar; the neighborhood must surface it (B5).
        let vault = portal_vault("graph-cards", "50-Inbox/03-Processed/good.md", "body\n");
        write_ledger(&vault);
        let st = state(vault.clone(), None);

        let resp = dispatch(
            &st,
            Method::Get,
            "/api/graph?scope=neighborhood&source=aaaa1111",
            "",
        );
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        let nodes = v["nodes"].as_array().unwrap();
        let card = nodes
            .iter()
            .find(|n| n["type"] == "card")
            .expect("card node from the evidence sidecar");
        assert_eq!(card["label"], "Card One");
        assert!(
            v["edges"].as_array().unwrap().iter().any(|e| {
                e["type"] == "has_memory"
                    && e["source"] == "source:aaaa1111"
                    && e["target"] == card["id"]
            }),
            "has_memory edge from the focus source to its card"
        );

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn settings_endpoint_reports_readonly_config_shape() {
        let vault = portal_vault("settings", "50-Inbox/03-Processed/good.md", "body\n");
        let mut st = state(vault.clone(), None);

        // No LLM configured, index present.
        let resp = dispatch(&st, Method::Get, "/api/settings", "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert_eq!(
            v["vault_root"].as_str().unwrap(),
            vault.display().to_string()
        );
        assert_eq!(v["schema_version"], "ovp.index/v2");
        assert_eq!(v["index_date"], "2026-07-09");
        // P1 provenance: the instant, its producer, and a non-negative age.
        assert_eq!(v["built_at"], "2026-07-09T00:00:00Z");
        assert_eq!(v["run_id"], "daily-2026-07-09");
        assert!(v["age_seconds"].as_i64().unwrap() >= 0, "age is present and non-negative");
        assert_eq!(v["counts"]["sources"], 1);
        assert_eq!(v["counts"]["packs"], 1);
        assert!(v["counts"]["claims"].is_u64());
        assert_eq!(v["llm_configured"], false);
        assert_eq!(v["ask_limits"]["timeout_secs"], st.ask_timeout.as_secs());
        assert_eq!(v["ask_limits"]["max_concurrent"], DEFAULT_MAX_CONCURRENT_ASKS);
        assert_eq!(v["version"], env!("CARGO_PKG_VERSION"));

        // With an ask client the flag flips.
        st.ask_client = Some(scripted_factory("answer", Duration::ZERO));
        let v = body_json(dispatch(&st, Method::Get, "/api/settings", ""));
        assert_eq!(v["llm_configured"], true);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn settings_endpoint_answers_without_an_index() {
        // A vault with no index projection still gets settings (nulls, not
        // a 503) — the System page must render the panel with guidance.
        let root = temp_root("settings-no-index");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault, None);

        let resp = dispatch(&st, Method::Get, "/api/settings", "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert!(v["schema_version"].is_null());
        assert!(v["index_date"].is_null());
        // No index → provenance is uniformly null (client shows "unknown age").
        assert!(v["built_at"].is_null());
        assert!(v["run_id"].is_null());
        assert!(v["age_seconds"].is_null());
        assert!(v["counts"].is_null());
        assert_eq!(v["llm_configured"], false);

        let _ = std::fs::remove_dir_all(&root);
    }

    /// Write `n` markdown source files under `01-Raw/<month>/` (plus a dotfile
    /// and a non-md file that must NOT be counted). Returns the raw dir.
    fn seed_raw_inbox(vault: &Path, n: usize) -> PathBuf {
        let raw = vault.join("50-Inbox/01-Raw/2026-07");
        std::fs::create_dir_all(&raw).unwrap();
        for i in 0..n {
            std::fs::write(raw.join(format!("src-{i}.md")), format!("body {i}\n")).unwrap();
        }
        // Noise that the queued walk must ignore (mirrors ovp-index::walk).
        std::fs::write(raw.join(".hidden.md"), "x").unwrap();
        std::fs::write(raw.join("notes.txt"), "x").unwrap();
        raw
    }

    #[test]
    fn live_queued_matches_raw_inbox_file_count() {
        let root = temp_root("live-queued-count");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        seed_raw_inbox(&vault, 3);
        let st = state(vault.clone(), None);

        // Direct accessor (no projection → nothing to subtract): 3 md files →
        // 3, ignoring the dotfile and .txt.
        assert_eq!(st.live_queued_count(None), 3);

        // Surfaced on /api/settings as the authoritative-now figure.
        let v = body_json(dispatch(&st, Method::Get, "/api/settings", ""));
        assert_eq!(v["queued_live"], 3);
        // No index built → the projection value is null, but live still answers.
        assert!(v["queued_at_build"].is_null());

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn live_queued_reflects_a_drain_without_a_projection_rebuild() {
        let root = temp_root("live-queued-drain");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let raw = seed_raw_inbox(&vault, 4);

        // Bake a STALE projection: totals.queued frozen at 4 (end of last run).
        let mut baked = index_dated("2026-07-12");
        baked.totals.queued = 4;
        ovp_index::write_index(&vault, &baked).unwrap();
        let st = state(vault.clone(), None);

        let v = body_json(dispatch(&st, Method::Get, "/api/settings", ""));
        assert_eq!(v["queued_live"], 4);
        assert_eq!(v["queued_at_build"], 4, "projection and live agree at rest");

        // A run drains two sources out of 01-Raw. The projection is NOT rebuilt.
        std::fs::remove_file(raw.join("src-0.md")).unwrap();
        std::fs::remove_file(raw.join("src-1.md")).unwrap();
        // Bust the TTL cache the way real elapsed time would.
        st.live_queued.write().unwrap().computed_at = None;

        let v = body_json(dispatch(&st, Method::Get, "/api/settings", ""));
        assert_eq!(v["queued_live"], 2, "live count ticked down as 01-Raw drained");
        assert_eq!(
            v["queued_at_build"], 4,
            "projection stays frozen — the whole point of the live overlay"
        );

        // /api/model carries the same live overlay while totals.queued stays 4.
        let m = body_json(dispatch(&st, Method::Get, "/api/model", ""));
        assert_eq!(m["queued_live"], 2);
        assert_eq!(m["totals"]["queued"], 4, "projection value untouched for other readers");
        assert_eq!(m["queued_at_build"], 4);

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn live_queued_is_zero_on_a_vault_with_no_raw_inbox() {
        let root = temp_root("live-queued-empty");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault, None);
        assert_eq!(st.live_queued_count(None), 0);
        let _ = std::fs::remove_dir_all(&root);
    }

    /// A raw-inbox SourceRow with the given status, rel_path under 01-Raw.
    fn raw_source(sha: &str, status: ovp_index::SourceStatus, file: &str) -> ovp_index::SourceRow {
        ovp_index::SourceRow {
            sha256: sha.into(),
            status,
            title: None,
            url: None,
            rel_path: Some(format!("50-Inbox/01-Raw/2026-07/{file}")),
            date: None,
            last_run_id: None,
            pack_dir: None,
            fail_count: 0,
            last_reason: None,
        }
    }

    /// The P2 fix: at a quiescent vault, `queued_live` MUST equal the
    /// projection's `totals.queued` — a naive raw-file walk over-counts because
    /// blocked / duplicate sources keep files in 01-Raw yet are NOT queued.
    /// Fixture: 3 queued + 1 blocked + 1 duplicate ALL physically in 01-Raw
    /// (5 files) → the projection's queued is 3, and `queued_live` must be 3,
    /// NOT 5.
    #[test]
    fn live_queued_equals_projection_queued_at_rest() {
        use ovp_index::{SourceStatus, Totals};
        let root = temp_root("live-queued-at-rest");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let raw = vault.join("50-Inbox/01-Raw/2026-07");
        std::fs::create_dir_all(&raw).unwrap();
        // 6 real files in 01-Raw: 3 queued, 1 blocked, 1 parked dup, and 1
        // Processed file that stayed (the --no-lifecycle / failed-move case).
        for f in ["q0.md", "q1.md", "q2.md", "blocked.md", "dup.md", "proc.md"] {
            std::fs::write(raw.join(f), "body\n").unwrap();
        }
        assert_eq!(count_markdown_files(&raw), 6, "6 files physically present");

        // Projection mirroring what build_sources would classify for these
        // files: 3 Queued, 1 Blocked, 1 Duplicate — all rel_path under 01-Raw.
        let sources = vec![
            raw_source("h0", SourceStatus::Queued, "q0.md"),
            raw_source("h1", SourceStatus::Queued, "q1.md"),
            raw_source("h2", SourceStatus::Queued, "q2.md"),
            raw_source("hb", SourceStatus::Blocked, "blocked.md"),
            raw_source("hd", SourceStatus::Duplicate, "dup.md"),
            // Processed but still in 01-Raw (no-lifecycle) — must NOT count.
            raw_source("hp", SourceStatus::Processed, "proc.md"),
        ];
        let queued = sources
            .iter()
            .filter(|s| s.status == SourceStatus::Queued)
            .count();
        let mut baked = index_dated("2026-07-12");
        baked.sources = sources;
        baked.totals = Totals { queued, ..Default::default() };
        ovp_index::write_index(&vault, &baked).unwrap();

        let st = state(vault.clone(), None);
        let live = st.live_queued_count(st.current_model().as_ref());
        assert_eq!(live, 3, "6 files − (blocked + dup + processed-in-raw) = 3 queued");
        assert_eq!(
            live,
            st.current_model().unwrap().totals.queued,
            "at rest, queued_live MUST equal the projection's totals.queued"
        );

        // And through the API surface.
        let v = body_json(dispatch(&st, Method::Get, "/api/settings", ""));
        assert_eq!(v["queued_live"], 3);
        assert_eq!(v["queued_at_build"], 3);

        let _ = std::fs::remove_dir_all(&root);
    }

    /// The live count ticks down as QUEUED sources drain, while the stable
    /// non-queued subtrahend (blocked/dup lingering in 01-Raw) keeps the number
    /// honest — it does not double-drop when a queued file leaves.
    #[test]
    fn live_queued_subtracts_non_queued_while_draining() {
        use ovp_index::{SourceStatus, Totals};
        let root = temp_root("live-queued-drain-nonqueued");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let raw = vault.join("50-Inbox/01-Raw/2026-07");
        std::fs::create_dir_all(&raw).unwrap();
        for f in ["q0.md", "q1.md", "blocked.md"] {
            std::fs::write(raw.join(f), "body\n").unwrap();
        }
        let mut baked = index_dated("2026-07-12");
        baked.sources = vec![
            raw_source("h0", SourceStatus::Queued, "q0.md"),
            raw_source("h1", SourceStatus::Queued, "q1.md"),
            raw_source("hb", SourceStatus::Blocked, "blocked.md"),
        ];
        baked.totals = Totals { queued: 2, ..Default::default() };
        ovp_index::write_index(&vault, &baked).unwrap();
        let st = state(vault.clone(), None);

        // At rest: 3 files − 1 blocked = 2 queued.
        assert_eq!(st.live_queued_count(st.current_model().as_ref()), 2);

        // A run processes one queued source: its file leaves 01-Raw. The
        // projection is NOT rebuilt (blocked row still known).
        std::fs::remove_file(raw.join("q0.md")).unwrap();
        st.live_queued.write().unwrap().computed_at = None;
        // 2 files (q1 + blocked) − 1 blocked = 1 queued. Not 2, not 0.
        assert_eq!(st.live_queued_count(st.current_model().as_ref()), 1);

        let _ = std::fs::remove_dir_all(&root);
    }

    /// Value of a response header, case-insensitive, or `None`.
    fn header_of(resp: &Response<std::io::Cursor<Vec<u8>>>, name: &str) -> Option<String> {
        resp.headers()
            .iter()
            .find(|h| h.field.as_str().as_str().eq_ignore_ascii_case(name))
            .map(|h| h.value.as_str().to_string())
    }

    #[test]
    fn model_endpoint_carries_built_at_run_id_and_age() {
        let vault = portal_vault("model-provenance", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None);

        let resp = dispatch(&st, Method::Get, "/api/model", "");
        assert_eq!(resp.status_code(), 200);
        // Provenance headers pair the body with the build that produced it.
        assert_eq!(header_of(&resp, "X-OVP-Built-At").as_deref(), Some("2026-07-09T00:00:00Z"));
        assert_eq!(header_of(&resp, "X-OVP-Run-Id").as_deref(), Some("daily-2026-07-09"));
        assert!(header_of(&resp, "X-OVP-Age-Seconds").is_some(), "age header present");

        let v = body_json(resp);
        assert_eq!(v["built_at"], "2026-07-09T00:00:00Z");
        assert_eq!(v["run_id"], "daily-2026-07-09");
        assert!(v["age_seconds"].as_i64().unwrap() >= 0, "server-computed age spliced in");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn claim_endpoint_resolves_a_fresh_sources_metadata_without_a_dangling_citation() {
        // Torn-read guard: a claim cites case `good`, whose pack links source
        // `aaaa1111`, which is present in the freshly-loaded index. handle_claim
        // reads the model ONCE (before the ledger) so the citation joins the
        // SAME freshness — the source title/url/sha resolve, never a dangling
        // citation. write_ledger's claim `a` cites case `good`, unit `u-001`.
        let vault = portal_vault("claim-torn-read", "50-Inbox/03-Processed/good.md", "body\n");
        write_ledger(&vault);
        let st = state(vault.clone(), None);

        let resp = dispatch(&st, Method::Get, "/api/claim/a", "");
        assert_eq!(resp.status_code(), 200);
        // Response is paired with the projection it resolved against.
        assert_eq!(header_of(&resp, "X-OVP-Built-At").as_deref(), Some("2026-07-09T00:00:00Z"));
        let v = body_json(resp);
        assert_eq!(v["claim_id"], "a");
        let cit = &v["citations"][0];
        assert_eq!(cit["case_id"], "good");
        // The fresh source's metadata resolved from the SAME-freshness index —
        // NOT a dangling citation (case `good` → sha aaaa1111 via the pack).
        assert_eq!(cit["source_sha256"], "aaaa1111", "source sha resolved from the index");
        assert_eq!(cit["source_title"], "Good Article", "source title resolved");
        assert_eq!(cit["source_url"], "https://example.com/good");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn html_is_no_cache_hashed_assets_are_immutable() {
        // Regression: serve sent no Cache-Control, so browsers cached the SPA
        // shell and kept loading stale JS after a rebuild ("portal won't
        // update"). index.html must revalidate; hashed assets stay immutable.
        let root = temp_root("cache-headers");
        let vault = root.join("vault");
        let app = vault.join(".ovp/console/app/assets");
        std::fs::create_dir_all(&app).unwrap();
        std::fs::write(vault.join(".ovp/console/app/index.html"), "<!doctype html>").unwrap();
        std::fs::write(app.join("index-abc123.js"), "console.log(1)").unwrap();
        let st = state(vault, None);

        let html = dispatch(&st, Method::Get, "/", "");
        let hv = header_value(&html, "Cache-Control");
        assert!(hv.contains("no-cache"), "index.html must not be cached, got {hv:?}");

        let js = dispatch(&st, Method::Get, "/assets/index-abc123.js", "");
        let jv = header_value(&js, "Cache-Control");
        assert!(jv.contains("immutable"), "hashed asset should be immutable, got {jv:?}");
    }

    #[test]
    fn unknown_api_routes_are_json_404_not_spa() {
        // With an SPA overlay present, a bad /api/* path used to fall through
        // to the extensionless-client-route rule and answer 200 + index.html.
        let root = temp_root("api-404");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join(".ovp/console")).unwrap();
        let overlay = root.join("dist");
        std::fs::create_dir_all(&overlay).unwrap();
        std::fs::write(overlay.join("index.html"), "spa").unwrap();
        let st = state(vault, Some(overlay));

        for path in ["/api/nonexistent", "/api", "/api/", "/api/nope?x=1"] {
            let resp = dispatch(&st, Method::Get, path, "");
            assert_eq!(resp.status_code(), 404, "path {path}");
            let v = body_json(resp);
            assert_eq!(v["error"], "unknown api route", "path {path}");
        }

        // Non-API client routes still reach the SPA shell…
        assert_eq!(dispatch(&st, Method::Get, "/library", "").status_code(), 200);
        // …exact API routes keep working even with a query string…
        let resp = dispatch(&st, Method::Get, "/api/refresh?x=1", "");
        assert_eq!(resp.status_code(), 200);
        // …and non-GET stays 405.
        assert_eq!(dispatch(&st, Method::Post, "/api/model", "").status_code(), 405);

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn source_api_rejects_traversal_paths() {
        let vault = portal_vault("source-traversal", "../secret.md", "body\n");
        // A secret OUTSIDE the vault that `..` would reach.
        std::fs::write(vault.parent().unwrap().join("secret.md"), "TOP SECRET").unwrap();
        let st = state(vault.clone(), None);

        let resp = handle_source_api(&st, "/api/source/aaaa1111");
        assert_eq!(resp.status_code(), 200); // meta still served
        let v = body_json(resp);
        assert!(v["doc"]["markdown"].is_null());
        assert_eq!(v["doc"]["error"], "source path rejected");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_rejects_windows_absolute_rel_path() {
        // `C:\evil` is NOT absolute per is_absolute() on Unix, but on a
        // Windows host Path::join would discard the vault root — rejected.
        let vault = portal_vault("source-win-abs", "C:\\evil", "body\n");
        let st = state(vault.clone(), None);

        let v = body_json(handle_source_api(&st, "/api/source/aaaa1111"));
        assert!(v["doc"]["markdown"].is_null());
        assert_eq!(v["doc"]["error"], "source path rejected");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_truncates_oversized_markdown() {
        let big = "x".repeat(MAX_SOURCE_DOC_BYTES + 100);
        let vault = portal_vault("source-big", "50-Inbox/03-Processed/good.md", &big);
        let st = state(vault.clone(), None);

        let v = body_json(handle_source_api(&st, "/api/source/aaaa1111"));
        assert_eq!(v["doc"]["truncated"], true);
        assert_eq!(
            v["doc"]["markdown"].as_str().unwrap().len(),
            MAX_SOURCE_DOC_BYTES
        );

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn source_api_without_evidence_reports_unavailable() {
        let vault = portal_vault(
            "source-no-evidence",
            "50-Inbox/03-Processed/good.md",
            "body\n",
        );
        std::fs::remove_file(vault.join(".ovp/index/evidence.json")).unwrap();
        let st = state(vault.clone(), None);

        let v = body_json(handle_source_api(&st, "/api/source/aaaa1111"));
        assert_eq!(v["memory"]["evidence_available"], false);
        assert!(v["memory"]["cards"].as_array().unwrap().is_empty());
        assert!(v["memory"]["units"].as_array().unwrap().is_empty());

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_without_llm_config_is_503_json() {
        let vault = portal_vault("ask-no-llm", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None); // ask_client: None

        let resp = ask(&st, r#"{"question":"anything"}"#);
        assert_eq!(resp.status_code(), 503);
        let v = body_json(resp);
        assert_eq!(v["error"], "llm not configured");
        assert_eq!(v["code"], "llm_not_configured");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_503s_disambiguate_missing_index_from_missing_llm() {
        // An LLM IS configured but the vault has no index — the portal
        // must be able to tell this apart from the missing-key case, so
        // the `code` field differs.
        let root = temp_root("ask-no-index");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let mut st = state(vault, None);
        st.ask_client = Some(scripted_factory("answer", Duration::ZERO));

        let resp = ask(&st, r#"{"question":"anything"}"#);
        assert_eq!(resp.status_code(), 503);
        let v = body_json(resp);
        assert_eq!(v["code"], "index_unavailable");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn ask_guard_derives_from_transport_timeout_env() {
        let with = |v: Option<&str>| {
            ask_guard(|k| {
                assert_eq!(k, "OVP_LLM_TIMEOUT_SECS");
                v.map(str::to_string)
            })
        };
        // Absent → provider default (180s) + margin.
        assert_eq!(
            with(None),
            Duration::from_secs(DEFAULT_TRANSPORT_TIMEOUT_SECS + ASK_GUARD_MARGIN_SECS)
        );
        // Runbook value 480 → 480 + margin: the guard must outlive the
        // billable call, never 504 while the transport is still waiting.
        assert_eq!(with(Some("480")), Duration::from_secs(480 + 30));
        // 0 = transport timeout disabled → the guard becomes the only
        // bound, at its own generous cap.
        assert_eq!(
            with(Some("0")),
            Duration::from_secs(ASK_GUARD_NO_TRANSPORT_TIMEOUT_SECS)
        );
        // Garbage falls back to the default (the CLIENT fails loud on it).
        assert_eq!(
            with(Some("lots")),
            Duration::from_secs(DEFAULT_TRANSPORT_TIMEOUT_SECS + ASK_GUARD_MARGIN_SECS)
        );
    }

    #[test]
    fn ask_concurrency_cap_answers_429_and_slot_survives_the_504() {
        use std::time::Instant;

        let vault = portal_vault("ask-429", "50-Inbox/03-Processed/good.md", "body\n");
        let mut st = state(vault.clone(), None);
        st.ask_client = Some(scripted_factory("answer", Duration::from_millis(400)));
        st.ask_timeout = Duration::from_millis(50); // guard fires mid-pipeline
        st.ask_slots = AskSlots::new(1);
        let st = Arc::new(st);

        // First ask: the guard 504s, but the pipeline is still running…
        let body = r#"{"question":"memory"}"#;
        assert_eq!(ask(&st, body).status_code(), 504);

        // …so its slot is STILL taken: the next ask is refused immediately
        // (429, no queue) — a timed-out billable call must keep counting
        // against the cap until the provider returns.
        let started = Instant::now();
        let resp = ask(&st, body);
        assert_eq!(resp.status_code(), 429);
        assert!(
            started.elapsed() < Duration::from_millis(200),
            "429 must be immediate, not queued"
        );
        let v = body_json(resp);
        assert_eq!(v["code"], "ask_busy");

        // Once the pipeline actually finishes, the slot frees: the next
        // ask gets a real attempt again (504 via the tiny guard — the
        // point is it is NOT a 429).
        std::thread::sleep(Duration::from_millis(600));
        assert_eq!(ask(&st, body).status_code(), 504);

        // Let that last pipeline drain before removing its vault.
        std::thread::sleep(Duration::from_millis(600));
        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_malformed_body_is_400() {
        let vault = portal_vault("ask-bad-body", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None);

        // Not JSON / wrong shape / blank question — all 400 with a JSON
        // error, and all checked BEFORE the llm-config 503.
        for body in ["not json", "{}", r#"{"question":"   "}"#, r#"{"question":7}"#] {
            let resp = ask(&st, body);
            assert_eq!(resp.status_code(), 400, "body {body}");
            let v = body_json(resp);
            assert!(v["error"].is_string(), "body {body}");
        }

        // Oversize bodies are rejected, not silently truncated.
        let huge = format!(
            r#"{{"question":"{}"}}"#,
            "x".repeat(MAX_POST_BODY_BYTES + 10)
        );
        assert_eq!(
            ask(&st, &huge).status_code(),
            400
        );

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_answers_with_ordered_citations_and_saves_chat() {
        let vault = portal_vault("ask-e2e", "50-Inbox/03-Processed/good.md", "body\n");
        let answer = "Filesystem is memory [claim:c01], grounded \
                      [unit:unit:40-Resources/Reader/good:u-001], and a ghost [card:nope].";
        let mut st = state(vault.clone(), None);
        st.ask_client = Some(scripted_factory(answer, Duration::ZERO));

        let body = r#"{"question":"filesystem memory card unit text"}"#;
        let resp = ask(&st, body);
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert_eq!(v["answer"], answer);
        assert!(v["context_hits"].as_u64().unwrap() >= 3);

        // Citations come back in first-appearance order with deep links.
        let cits = v["citations"].as_array().unwrap();
        assert_eq!(cits.len(), 3);
        assert_eq!(cits[0]["id"], "claim:c01");
        assert_eq!(cits[0]["kind"], "claim");
        assert_eq!(cits[0]["link_target"], "/knowledge#c01");
        assert_eq!(cits[0]["verified"], true);
        assert_eq!(cits[1]["kind"], "unit");
        // Unit resolves through the pack lookup to the source page…
        assert_eq!(cits[1]["link_target"], "/library/aaaa1111");
        assert_eq!(cits[1]["snippet"], "the exact quote");
        assert_eq!(cits[1]["verified"], true);
        // …and a citation that was never in evidence is kept, unverified,
        // with no link (never a dead target).
        assert_eq!(cits[2]["id"], "card:nope");
        assert_eq!(cits[2]["verified"], false);
        assert!(cits[2]["link_target"].is_null());

        assert_eq!(v["verified"]["cited"], 3);
        assert_eq!(v["verified"]["verified"], 2);
        assert_eq!(v["verified"]["missing"][0], "card:nope");

        // The chat was saved like `ovp2 ask --save` and is listed + readable.
        let chat = v["chat"].as_str().expect("chat name").to_string();
        assert!(vault.join(".ovp/chats").join(format!("{chat}.md")).is_file());

        let list = body_json(dispatch(&st, Method::Get, "/api/chats", ""));
        let rows = list.as_array().unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["name"], chat.as_str());
        assert!(rows[0]["mtime"].as_u64().unwrap() > 0);

        let detail = dispatch(&st, Method::Get, &format!("/api/chats/{chat}"), "");
        assert_eq!(detail.status_code(), 200);
        let ct = detail
            .headers()
            .iter()
            .find(|h| {
                h.field
                    .as_str()
                    .as_str()
                    .eq_ignore_ascii_case("content-type")
            })
            .map(|h| h.value.as_str().to_string());
        assert_eq!(ct.as_deref(), Some("text/markdown; charset=utf-8"));
        use std::io::Read;
        let mut md = String::new();
        detail.into_reader().read_to_string(&mut md).unwrap();
        assert!(md.contains("filesystem memory card unit text"));
        assert!(md.contains("[claim:c01]"));

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_timeout_guard_answers_504() {
        let vault = portal_vault("ask-timeout", "50-Inbox/03-Processed/good.md", "body\n");
        let mut st = state(vault.clone(), None);
        st.ask_client = Some(scripted_factory("late", Duration::from_millis(300)));
        st.ask_timeout = Duration::from_millis(50);

        let resp = ask(&st, r#"{"question":"memory"}"#);
        assert_eq!(resp.status_code(), 504);
        let v = body_json(resp);
        assert_eq!(v["code"], "ask_timeout");
        // Honest copy: the guard does NOT cancel the provider call.
        assert!(v["error"].as_str().unwrap().contains("not cancelled"));

        // Let the detached worker finish before tearing the vault down.
        std::thread::sleep(Duration::from_millis(400));
        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_rejects_non_json_content_type_with_415() {
        // A cross-origin CORS-"simple" POST (text/plain, form enctype, or
        // no content-type at all) must never reach the paid LLM pipeline.
        let vault = portal_vault("ask-415", "50-Inbox/03-Processed/good.md", "body\n");
        let mut st = state(vault.clone(), None);
        st.ask_client = Some(scripted_factory("answer", Duration::ZERO));

        let body = r#"{"question":"memory"}"#;
        for ct in [
            None,
            Some("text/plain"),
            Some("text/plain;charset=UTF-8"),
            Some("application/x-www-form-urlencoded"),
            Some("multipart/form-data"),
        ] {
            let headers = AskHeaders {
                content_type: ct.map(str::to_string),
                origin: None,
            };
            let resp = ask_with(&st, &headers, body);
            assert_eq!(resp.status_code(), 415, "content-type {ct:?}");
        }
        // …while a charset suffix on real JSON is fine (reaches the
        // pipeline and answers 200).
        let headers = AskHeaders {
            content_type: Some("application/json; charset=utf-8".into()),
            origin: None,
        };
        assert_eq!(ask_with(&st, &headers, body).status_code(), 200);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn ask_rejects_foreign_origins_and_allows_loopback() {
        let vault = portal_vault("ask-origin", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None); // no LLM: pass-through = 503

        let body = r#"{"question":"memory"}"#;
        let with_origin = |origin: &str| AskHeaders {
            content_type: Some("application/json".into()),
            origin: Some(origin.into()),
        };

        // Foreign / opaque origins are refused before ANY other work.
        for evil in [
            "https://evil.example",
            "http://localhost.evil.example",
            "http://127.0.0.1.evil.example",
            "null",
            "file://",
        ] {
            let resp = ask_with(&st, &with_origin(evil), body);
            assert_eq!(resp.status_code(), 403, "origin {evil}");
        }

        // Loopback origins (any port — the vite dev proxy) pass the gate:
        // with no LLM configured they fall through to the 503, not a 403.
        for ok in [
            "http://localhost:5173",
            "http://127.0.0.1:8794",
            "http://[::1]:3141",
            "https://localhost",
        ] {
            let resp = ask_with(&st, &with_origin(ok), body);
            assert_eq!(resp.status_code(), 503, "origin {ok}");
        }
        // Absent Origin (curl, same-origin GET-navigations) also passes.
        assert_eq!(ask(&st, body).status_code(), 503);

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    /// P1 regression: a slow ask must NOT block the accept loop. Drives the
    /// real serve_loop over TCP: fire an ask whose scripted client sleeps
    /// 1.2s (saturating the 1-slot cap), then a concurrent GET /api/model
    /// AND a second ask — the GET must answer well before the first ask
    /// completes, and the second ask must be refused (429) immediately
    /// instead of queueing behind the paid call.
    #[test]
    fn ask_does_not_block_the_accept_loop() {
        use std::io::{Read, Write};
        use std::net::TcpStream;
        use std::time::Instant;

        let vault = portal_vault("ask-nonblock", "50-Inbox/03-Processed/good.md", "body\n");
        let mut st = state(vault.clone(), None);
        const ASK_DELAY: Duration = Duration::from_millis(1200);
        st.ask_client = Some(scripted_factory("answer [claim:c01]", ASK_DELAY));
        st.ask_slots = AskSlots::new(1);
        let state = Arc::new(st);

        let server = Arc::new(Server::http("127.0.0.1:0").expect("bind ephemeral port"));
        let port = server
            .server_addr()
            .to_ip()
            .expect("ip listener")
            .port();
        {
            let server = Arc::clone(&server);
            let state = Arc::clone(&state);
            std::thread::spawn(move || serve_loop(&server, &state));
        }

        // Fire the slow ask; don't read its response yet.
        let ask_started = Instant::now();
        let body = r#"{"question":"memory"}"#;
        let mut ask_conn = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(
            ask_conn,
            "POST /api/ask HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n\
             Connection: close\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        ask_conn.flush().unwrap();

        // Give the loop a beat to hand the ask to its worker…
        std::thread::sleep(Duration::from_millis(150));

        // …then the concurrent GET must answer while the ask is in flight.
        let get_started = Instant::now();
        let mut get_conn = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(
            get_conn,
            "GET /api/model HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        )
        .unwrap();
        let mut get_resp = String::new();
        get_conn.read_to_string(&mut get_resp).unwrap();
        let get_elapsed = get_started.elapsed();
        assert!(get_resp.starts_with("HTTP/1.1 200"), "{get_resp}");
        assert!(
            get_elapsed < Duration::from_millis(800),
            "GET /api/model blocked behind the ask: {get_elapsed:?}"
        );
        assert!(
            ask_started.elapsed() < ASK_DELAY,
            "ask finished too early to prove anything"
        );

        // A second ask while the slot is taken: refused fast with 429 —
        // never queued behind the in-flight paid call.
        let busy_started = Instant::now();
        let mut busy_conn = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(
            busy_conn,
            "POST /api/ask HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n\
             Connection: close\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        let mut busy_resp = String::new();
        busy_conn.read_to_string(&mut busy_resp).unwrap();
        assert!(busy_resp.starts_with("HTTP/1.1 429"), "{busy_resp}");
        assert!(busy_resp.contains("ask_busy"), "{busy_resp}");
        assert!(
            busy_started.elapsed() < Duration::from_millis(500),
            "429 must be immediate while saturated"
        );

        // The first ask itself still completes fine after its delay.
        let mut ask_resp = String::new();
        ask_conn.read_to_string(&mut ask_resp).unwrap();
        assert!(ask_resp.starts_with("HTTP/1.1 200"), "{ask_resp}");
        assert!(ask_started.elapsed() >= ASK_DELAY);

        server.unblock(); // let the loop thread exit
        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn loopback_origin_matcher() {
        assert!(is_loopback_origin("http://localhost:5173"));
        assert!(is_loopback_origin("http://127.0.0.1:8794"));
        assert!(is_loopback_origin("https://localhost"));
        assert!(is_loopback_origin("http://[::1]:3141"));
        assert!(!is_loopback_origin("https://evil.example"));
        assert!(!is_loopback_origin("http://localhost.evil.example"));
        assert!(!is_loopback_origin("null"));
        assert!(!is_loopback_origin("file://"));
        assert!(!is_loopback_origin(""));
    }

    #[test]
    fn chats_list_is_empty_without_dir_and_detail_rejects_bad_names() {
        let vault = portal_vault("chats-empty", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None);

        // No .ovp/chats dir yet — empty list, not an error.
        let list = body_json(dispatch(&st, Method::Get, "/api/chats", ""));
        assert_eq!(list.as_array().unwrap().len(), 0);

        // Traversal-ish names are rejected outright; unknown names are 404.
        for bad in [
            "/api/chats/..%2f..%2fsecret",
            "/api/chats/../secret",
            "/api/chats/a%2Fb",
            "/api/chats/",
        ] {
            let resp = dispatch(&st, Method::Get, bad, "");
            assert_eq!(resp.status_code(), 400, "path {bad}");
        }
        assert_eq!(
            dispatch(&st, Method::Get, "/api/chats/1751812345", "").status_code(),
            404
        );

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn client_route_detection() {
        assert!(is_client_route("library"));
        assert!(is_client_route("library/84fbf6dc"));
        assert!(is_client_route("search"));
        assert!(is_client_route("viz/graph"));
        // Extensions (missed files) and malformed paths are not routes.
        assert!(!is_client_route("index.html"));
        assert!(!is_client_route("assets/app.js"));
        assert!(!is_client_route("library/file.md"));
        assert!(!is_client_route(""));
        assert!(!is_client_route("/etc/hosts"));
        assert!(!is_client_route("viz//etc"));
    }

    #[test]
    fn plain_relative_rejects_windows_prefixes_and_traversal() {
        assert!(is_plain_relative("index.html"));
        assert!(is_plain_relative("assets/app.js"));
        assert!(is_plain_relative("library/84fbf6dc"));
        // Traversal and Unix-absolute.
        assert!(!is_plain_relative(""));
        assert!(!is_plain_relative("../secret.txt"));
        assert!(!is_plain_relative("a/../../b"));
        assert!(!is_plain_relative("/etc/hosts"));
        // Windows prefix / rootdir forms — one Normal component on Unix,
        // absolute on Windows, so raw-byte checks must catch them.
        assert!(!is_plain_relative("C:\\windows\\system32"));
        assert!(!is_plain_relative("C:/windows/system32"));
        assert!(!is_plain_relative("\\\\srv\\share"));
        assert!(!is_plain_relative("\\evil"));
    }

    // ---- mtime-based cache auto-reload (fix/serve-mtime-reload) ----

    /// A minimal index whose `date` field labels the version, so tests can
    /// assert WHICH on-disk model an accessor returned.
    fn index_dated(date: &str) -> IndexModel {
        use ovp_index::{OpsState, Totals};
        IndexModel {
            schema: "ovp.index/v2".into(),
            date: date.into(),
            built_at: Some(format!("{date}T00:00:00Z")),
            run_id: None,
            totals: Totals::default(),
            sources: vec![],
            packs: vec![],
            claims: vec![],
            runs: vec![],
            ops: OpsState::default(),
        }
    }

    /// A minimal evidence sidecar whose `date` field labels the version.
    fn evidence_dated(date: &str) -> EvidenceModel {
        EvidenceModel {
            schema: "ovp.index.evidence/v1".into(),
            date: date.into(),
            cards: vec![],
            units: vec![],
            warnings: vec![],
        }
    }

    /// Pin a file's mtime explicitly (std 1.75+ `File::set_modified`) — mtime
    /// resolution is coarse, so tests advance/hold it deterministically
    /// rather than sleeping.
    fn set_mtime(path: &Path, t: SystemTime) {
        std::fs::OpenOptions::new()
            .write(true)
            .open(path)
            .unwrap()
            .set_modified(t)
            .unwrap();
    }

    #[test]
    fn current_model_reloads_after_index_mtime_advances() {
        let root = temp_root("model-mtime-reload");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);
        let index = st.index_path();

        // v1 on disk, stamped at t0.
        ovp_index::write_index(&vault, &index_dated("2026-01-01")).unwrap();
        let t0 = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000);
        set_mtime(&index, t0);
        assert_eq!(st.current_model().unwrap().date, "2026-01-01");

        // A separate `daily` process rewrites the index with a NEWER model
        // and a bumped mtime — the running server must pick it up.
        ovp_index::write_index(&vault, &index_dated("2026-02-02")).unwrap();
        set_mtime(&index, t0 + Duration::from_secs(60));
        assert_eq!(st.current_model().unwrap().date, "2026-02-02");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn current_model_does_not_reload_when_mtime_unchanged() {
        let root = temp_root("model-mtime-hold");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);
        let index = st.index_path();

        ovp_index::write_index(&vault, &index_dated("2026-01-01")).unwrap();
        let t0 = SystemTime::UNIX_EPOCH + Duration::from_secs(2_000_000);
        set_mtime(&index, t0);
        assert_eq!(st.current_model().unwrap().date, "2026-01-01");

        // Corrupt the bytes but KEEP the mtime equal: a freshness check that
        // re-stat's (not re-parses) still serves the cached good model. If it
        // re-read on every call this would panic/None on the garbage.
        std::fs::write(&index, b"{ not json").unwrap();
        set_mtime(&index, t0);
        assert_eq!(st.current_model().unwrap().date, "2026-01-01");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn current_evidence_reloads_after_evidence_mtime_advances() {
        let root = temp_root("evidence-mtime-reload");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);
        let ev_path = st.evidence_path();

        ovp_index::write_evidence(&vault, &evidence_dated("2026-01-01")).unwrap();
        let t0 = SystemTime::UNIX_EPOCH + Duration::from_secs(3_000_000);
        set_mtime(&ev_path, t0);
        assert_eq!(st.current_evidence().unwrap().date, "2026-01-01");

        ovp_index::write_evidence(&vault, &evidence_dated("2026-02-02")).unwrap();
        set_mtime(&ev_path, t0 + Duration::from_secs(60));
        assert_eq!(st.current_evidence().unwrap().date, "2026-02-02");

        // mtime held → cached (corrupt bytes, same mtime, still good).
        let t1 = t0 + Duration::from_secs(60);
        std::fs::write(&ev_path, b"{ not json").unwrap();
        set_mtime(&ev_path, t1);
        assert_eq!(st.current_evidence().unwrap().date, "2026-02-02");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn api_refresh_forces_reload_even_when_mtime_unchanged() {
        let root = temp_root("refresh-forces-reload");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);
        let index = st.index_path();

        ovp_index::write_index(&vault, &index_dated("2026-01-01")).unwrap();
        let t0 = SystemTime::UNIX_EPOCH + Duration::from_secs(4_000_000);
        set_mtime(&index, t0);
        assert_eq!(st.current_model().unwrap().date, "2026-01-01");

        // Rewrite the model but PIN the mtime unchanged — auto-reload would
        // (correctly) not fire, but /api/refresh must still force it in.
        ovp_index::write_index(&vault, &index_dated("2026-09-09")).unwrap();
        set_mtime(&index, t0);
        assert_eq!(
            dispatch(&st, Method::Get, "/api/refresh", "").status_code(),
            200
        );
        assert_eq!(st.current_model().unwrap().date, "2026-09-09");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn absent_index_stays_none_without_rereading_until_it_appears() {
        let root = temp_root("model-absent-then-appears");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);

        // No index yet → None (as before), and the miss is cached.
        assert!(st.current_model().is_none());
        assert!(st.current_model().is_none());

        // The first run writes the index — the next accessor picks it up
        // (absent-file stamp was None; a present file's stamp differs).
        ovp_index::write_index(&vault, &index_dated("2026-03-03")).unwrap();
        assert_eq!(st.current_model().unwrap().date, "2026-03-03");

        let _ = std::fs::remove_dir_all(&root);
    }

    /// P1 regression: the index bakes `ops.last_run` at build time, so a crash
    /// after the heartbeat starts but before the index rebuild leaves the baked
    /// copy saying "running" forever. The server MUST read the sidecar LIVE and
    /// report the finalized status — never the stale baked snapshot.
    #[test]
    fn server_prefers_live_heartbeat_over_stale_baked_last_run() {
        let root = temp_root("live-heartbeat-overlay");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);

        // Bake an index whose ops.last_run says "running" (the stale snapshot).
        let mut baked = index_dated("2026-07-12");
        baked.ops.last_run = Some(LastRunModel {
            run_id: "daily-2026-07-12".into(),
            started_at: "2026-07-12T09:00:00Z".into(),
            ended_at: None,
            status: "running".into(),
            processed: None,
            failed: None,
            blocked: None,
            capped: None,
            queued_after: None,
            processed_so_far: None,
            total_planned: None,
            current: None,
            error: None,
        });
        ovp_index::write_index(&vault, &baked).unwrap();

        // On disk the run actually FINISHED — write a finalized sidecar.
        let (g, _) = ovp_daily::HeartbeatGuard::start(&vault, "daily-2026-07-12");
        g.finalize_completed(ovp_daily::RunCounts {
            processed: 8,
            queued_after: 180,
            ..Default::default()
        });

        // /api/model overlays the live sidecar over the baked "running".
        let live = st.model_with_live_last_run().unwrap();
        let lr = live.ops.last_run.expect("live last_run present");
        assert_eq!(lr.status, "completed", "must reflect the finalized sidecar, not baked 'running'");
        assert_eq!(lr.processed, Some(8));

        // /api/settings reads the same live value.
        let resp = dispatch(&st, Method::Get, "/api/settings", "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert_eq!(v["last_run"]["status"], "completed");

        let _ = std::fs::remove_dir_all(&root);
    }

    /// A still-running index snapshot with NO sidecar (e.g. sidecar deleted)
    /// overlays to None — the server never re-serves the stale baked value.
    #[test]
    fn live_overlay_nulls_baked_last_run_when_sidecar_absent() {
        let root = temp_root("live-heartbeat-absent");
        let vault = root.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        let st = state(vault.clone(), None);

        let mut baked = index_dated("2026-07-12");
        baked.ops.last_run = Some(LastRunModel {
            run_id: "r".into(),
            started_at: "2026-07-12T09:00:00Z".into(),
            ended_at: None,
            status: "running".into(),
            processed: None,
            failed: None,
            blocked: None,
            capped: None,
            queued_after: None,
            processed_so_far: None,
            total_planned: None,
            current: None,
            error: None,
        });
        ovp_index::write_index(&vault, &baked).unwrap();

        let live = st.model_with_live_last_run().unwrap();
        assert!(live.ops.last_run.is_none(), "no sidecar → live overlay is None, not the stale baked snapshot");

        let _ = std::fs::remove_dir_all(&root);
    }
}
