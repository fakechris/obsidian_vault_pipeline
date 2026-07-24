//! `ovp-server` — synchronous localhost HTTP server for the OVP2 portal
//! and API.
//!
//! Serves the portal SPA at the site root (deployed `.ovp/console/app/` or
//! the `--viz-dir` overlay; see `resolve_static` for the precedence rule),
//! legacy generated console pages by exact filename, and JSON API endpoints
//! (`/api/find`, `/api/search`, `/api/graph`, `/api/claim/:id`,
//! `/api/source/:sha`, `/api/flow`, `/api/settings`, `POST /api/ask`,
//! `/api/chats`). Uses `tiny_http` to avoid any async runtime dependency.

// Read-only `/api/*` body builders + graph assembly + fs readers live in the
// shared `ovp-api-projection` crate so the live server and the static publisher
// can never drift. `graph` is re-exported under its old path so the many
// `graph::…` call sites in this file are unchanged.
use ovp_api_projection::{bodies, graph, readers};

mod ask_client;
pub use ask_client::{
    api_key_configured, providers_ask_client_factory, LLM_NOT_CONFIGURED,
};

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock, mpsc};
use std::time::{Duration, SystemTime};

use ovp_domain::VaultLayout;
use ovp_domain::crystal::DurableRecord;
use ovp_index::{
    EvidenceModel, IndexModel, LastRunModel, Query, QueryKind, evidence_path, read_evidence,
    read_index, read_last_run_model,
};
use ovp_llm::ModelClient;
use ovp_memory::ask::{
    AskArgs, AskHistoryTurn, AskResult, EvidenceItem, EvidenceKind, ask_with_optional_evidence,
    valid_chat_stem,
};
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

/// Builds the LLM client for `POST /api/ask` on demand. Product hosts install
/// [`providers_ask_client_factory`] (re-reads `.ovp/providers.toml` each call;
/// no `set_var`). `None` = binary built without the `anthropic` feature — ask
/// answers 503. A present factory that cannot resolve a key returns
/// [`LLM_NOT_CONFIGURED`] (also mapped to 503).
pub type AskClientFactory = Arc<dyn Fn() -> Result<Box<dyn ModelClient>, String> + Send + Sync>;

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
    /// The `ovp2` CLI binary the manual-run endpoint spawns for
    /// `schedule run-now`. `None` → `std::env::current_exe()` (correct when
    /// the server IS `ovp2 serve`); the desktop app passes its bundled
    /// sidecar (its own current_exe is the GUI shell, not the CLI).
    pub ovp2_bin: Option<PathBuf>,
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
/// The set of `.md` file basenames physically present under `dir` (recursive,
/// dotfiles skipped). Basename, not full path, because the live-queued count
/// intersects this with projection sources whose `rel_path` may point at the
/// PRE-lifecycle intake location (rel_path is not rewritten when a source is
/// moved to 03-Processed) — the basename is the only stable identity across
/// the move.
fn markdown_basenames(dir: &Path) -> std::collections::HashSet<String> {
    fn walk(dir: &Path, out: &mut std::collections::HashSet<String>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') {
                continue;
            }
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else if path.extension().is_some_and(|e| e == "md") {
                out.insert(name);
            }
        }
    }
    let mut out = std::collections::HashSet::new();
    walk(dir, &mut out);
    out
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
    /// Serializes the tag mutation handlers end-to-end (decisions/vocabulary
    /// load-modify-save + note frontmatter replace + projection rebuild) —
    /// two concurrent curation writes must never interleave.
    tags_write_lock: std::sync::Mutex<()>,
    /// The one background publish job (`POST /api/publish` + status). One at
    /// a time — the vault RunLock inside the run enforces cross-process
    /// safety, this slot gives the portal an honest "already running".
    publish_job: Arc<std::sync::Mutex<PublishJob>>,
    /// The one MANUAL pipeline run (`POST /api/schedule/run`): double-click
    /// protection at the endpoint. The scheduler dispatch lock + the vault
    /// RunLock still guard cross-process races underneath.
    manual_run: Arc<std::sync::Mutex<ManualRun>>,
    /// See [`ServeConfig::ovp2_bin`].
    ovp2_bin: Option<PathBuf>,
    /// Serializes attention-ack read-modify-writes.
    acks_write_lock: std::sync::Mutex<()>,
}

/// State of the portal-triggered manual pipeline run.
#[derive(Default)]
struct ManualRun {
    /// The job id currently running, if any.
    running: Option<String>,
    /// Last finished run: `{ok, job, exit, finished_at, ...}`.
    last: Option<serde_json::Value>,
}

/// State of the portal-triggered publish job.
#[derive(Default)]
struct PublishJob {
    running: bool,
    /// Last finished run: the RunSummary JSON on success, `{error}` on
    /// failure, plus `finished_at`.
    last: Option<serde_json::Value>,
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
        // Physical .md files actually in 01-Raw right now (basenames).
        let present = markdown_basenames(&raw_dir);
        // Of those, drop the ones the projection classifies as NON-queued
        // (blocked/failed/needs-content/dup/processed). Matching by basename —
        // NOT rel_path prefix — because a processed source's rel_path still
        // points at 01-Raw after the lifecycle move to 03-Processed, so a
        // prefix filter over-counts departed files and can drive the result
        // negative. A file that physically LEFT 01-Raw simply isn't in
        // `present`, so the intersection excludes it automatically; a
        // non-queued file that STAYED (blocked/failed/needs-content, or any
        // status under --no-lifecycle) is in both sets and is subtracted.
        let non_queued_present = model
            .map(|m| {
                m.sources
                    .iter()
                    .filter(|src| src.status != ovp_index::SourceStatus::Queued)
                    .filter_map(|src| {
                        std::path::Path::new(src.rel_path.as_deref()?)
                            .file_name()
                            .map(|n| n.to_string_lossy().to_string())
                    })
                    .filter(|name| present.contains(name))
                    .count()
            })
            .unwrap_or(0);
        let count = present.len().saturating_sub(non_queued_present);
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
        freshen(&self.evidence, &path, || {
            read_evidence(&self.vault_root).ok()
        })
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
        tags_write_lock: std::sync::Mutex::new(()),
        publish_job: Arc::new(std::sync::Mutex::new(PublishJob::default())),
        manual_run: Arc::new(std::sync::Mutex::new(ManualRun::default())),
        ovp2_bin: config.ovp2_bin,
        acks_write_lock: std::sync::Mutex::new(()),
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
        // Tag mutation routes: same JSON/same-origin gate as ask BEFORE any
        // body is read, and the body read + handler run on a detached worker
        // — a client that stalls mid-body must never pin the accept loop
        // (the same slow-body failure mode /api/ask already avoids).
        if method == Method::Post {
            let p = path.split('?').next().unwrap_or(&path).to_string();
            if p == "/api/tags/decision"
                || p == "/api/publish"
                || p == "/api/schedule/run"
                || p == "/api/attention/ack"
                || p == "/api/providers"
                || (p.starts_with("/api/source/") && p.ends_with("/tags"))
            {
                let headers = AskHeaders::of(&request);
                if let Some(resp) = guard_json_same_origin(&headers) {
                    let _ = request.respond(resp);
                    continue;
                }
                let state = Arc::clone(state);
                std::thread::spawn(move || {
                    let body = read_post_body(&mut request);
                    let resp = dispatch(&state, Method::Post, &path, &body);
                    let _ = request.respond(resp);
                });
                continue;
            }
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
    body: &str,
) -> Response<std::io::Cursor<Vec<u8>>> {
    let path = url.split('?').next().unwrap_or(url);
    match (method, path) {
        (Method::Get, "/api/refresh") => {
            state.refresh_model();
            json_response(200, r#"{"ok":true}"#)
        }
        (Method::Get, "/api/tags") => handle_tags_api(state),
        (Method::Get, "/api/publish/status") => handle_publish_status(state),
        (Method::Post, "/api/publish") => handle_publish_start(state),
        (Method::Get, "/api/schedule/run/status") => handle_run_status(state),
        (Method::Post, "/api/schedule/run") => handle_run_start(state, body),
        (Method::Post, "/api/attention/ack") => handle_attention_ack(state, body),
        (Method::Get, "/api/providers") => handle_providers_get(state),
        (Method::Post, "/api/providers") => handle_providers_set(state, body),
        (Method::Post, "/api/tags/decision") => handle_tag_decision(state, body),
        (Method::Get, "/api/entities") => handle_entities_api(state),
        (Method::Get, p) if p.starts_with("/api/entity/") => handle_entity_api(state, url),
        (Method::Post, p) if p.starts_with("/api/source/") && p.ends_with("/tags") => {
            handle_source_tags_post(state, p, body)
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
        (Method::Get, "/api/theme-pages") => handle_theme_pages(state),
        (Method::Get, "/api/terrain") => handle_terrain(state),
        (Method::Get, p) if p.starts_with("/api/claim/") => handle_claim(state, url),
        (Method::Get, p) if p.starts_with("/api/source/") => handle_source_api(state, url),
        (Method::Get, p) if p == "/api" || p.starts_with("/api/") => {
            json_response(404, r#"{"error":"unknown api route"}"#)
        }
        (Method::Get, _) => serve_static(state, url),
        _ => text_response(405, "Method Not Allowed"),
    }
}

/// `GET /api/tags` — the curation surface's one read: the canonical
/// vocabulary with per-tag user/inferred counts, the banned list, and the
/// still-undecided merge proposals (`proposals.json` minus pairs the alias
/// table already resolves).
fn handle_tags_api(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };
    let vocabulary = ovp_domain::tags::TagVocabulary::load(&state.vault_root).unwrap_or_default();
    let mut counts: std::collections::BTreeMap<&str, (usize, usize)> =
        std::collections::BTreeMap::new();
    // Seed from the vocabulary so zero-count community/llm entries still
    // appear (browser completeness + Source Detail autocomplete).
    for (name, _) in vocabulary.iter() {
        counts.entry(name).or_default();
    }
    for s in &model.sources {
        for t in &s.tags {
            counts.entry(t.as_str()).or_default().0 += 1;
        }
        for t in &s.tags_inferred {
            counts.entry(t.as_str()).or_default().1 += 1;
        }
    }
    let origins: std::collections::BTreeMap<&str, &str> = vocabulary
        .iter()
        .map(|(name, origin)| {
            (
                name,
                match origin {
                    ovp_domain::tags::TagOrigin::User => "user",
                    ovp_domain::tags::TagOrigin::Community => "community",
                    ovp_domain::tags::TagOrigin::Llm => "llm",
                },
            )
        })
        .collect();
    let tags: Vec<serde_json::Value> = counts
        .iter()
        .map(|(tag, (user, inferred))| {
            serde_json::json!({
                "tag": tag,
                "user": user,
                "inferred": inferred,
                "origin": origins.get(tag),
            })
        })
        .collect();
    // Proposals still awaiting a decision: drop pairs the (operator +
    // decisions) alias table already merges AND pairs rejected in the UI —
    // proposals.json only refreshes on the next tags-suggest run, so both
    // decision kinds must retire cards here, immediately.
    let aliases = ovp_domain::tags::TagAliases::load(&state.vault_root).unwrap_or_default();
    let decisions = ovp_domain::tags::TagDecisions::load(&state.vault_root).unwrap_or_default();
    let proposals: Vec<serde_json::Value> = std::fs::read_to_string(
        state
            .vault_root
            .join(state.layout.tags_proposals_json_file()),
    )
    .ok()
    .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
    .and_then(|v| v.get("proposals").cloned())
    .and_then(|v| v.as_array().cloned())
    .unwrap_or_default()
    .into_iter()
    .filter(|p| {
        let alias = p.get("alias").and_then(|v| v.as_str()).unwrap_or("");
        let canonical = p.get("canonical").and_then(|v| v.as_str()).unwrap_or("");
        !alias.is_empty()
            && aliases.resolve(alias) == alias
            && aliases.resolve(canonical) == canonical
            && !decisions.is_ignored(alias, canonical)
    })
    .collect();
    let banned: Vec<&str> = vocabulary.banned().collect();
    let body = serde_json::json!({
        "tags": tags,
        "banned": banned,
        "proposals": proposals,
    })
    .to_string();
    json_stamped(200, &body, Some(&model))
}

/// `POST /api/tags/decision` `{action: "accept"|"reject", alias, canonical}`
/// — record a curation decision in the MACHINE-owned decisions.toml (the
/// operator's aliases.toml is never rewritten), then rebuild the projection
/// so the merge takes effect immediately.
fn handle_tag_decision(state: &AppState, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let parsed: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return json_response(400, r#"{"error":"invalid JSON body"}"#),
    };
    let action = parsed.get("action").and_then(|v| v.as_str()).unwrap_or("");
    let alias = parsed.get("alias").and_then(|v| v.as_str()).unwrap_or("");
    let canonical = parsed
        .get("canonical")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if alias.is_empty() || canonical.is_empty() {
        return json_response(400, r#"{"error":"alias and canonical are required"}"#);
    }
    let _write = state
        .tags_write_lock
        .lock()
        .unwrap_or_else(|p| p.into_inner());
    let mut decisions = match ovp_domain::tags::TagDecisions::load(&state.vault_root) {
        Ok(d) => d,
        Err(e) => return json_response(500, &format!(r#"{{"error":{}}}"#, json_str(&e))),
    };
    let result = match action {
        "accept" => decisions.accept(alias, canonical),
        "reject" => decisions.reject(alias, canonical),
        _ => return json_response(400, r#"{"error":"action must be accept or reject"}"#),
    };
    if let Err(e) = result {
        return json_response(400, &format!(r#"{{"error":{}}}"#, json_str(&e)));
    }
    if let Err(e) = decisions.save(&state.vault_root) {
        return json_response(500, &format!(r#"{{"error":{}}}"#, json_str(&e)));
    }
    // An accept can be silently un-absorbed when it conflicts with the
    // operator's aliases.toml (e.g. `alias` is already an operator canonical):
    // the merged table then still resolves `alias` to itself. Detect that,
    // roll the decision back, and report a conflict instead of a false success
    // that leaves the proposal card stuck.
    if action == "accept" {
        let na = ovp_domain::tags::normalize_tag(alias).unwrap_or_default();
        let merged = ovp_domain::tags::TagAliases::load(&state.vault_root).unwrap_or_default();
        if na.is_empty() || merged.resolve(&na) == na {
            let mut d = ovp_domain::tags::TagDecisions::load(&state.vault_root).unwrap_or_default();
            d.remove_alias(alias);
            let _ = d.save(&state.vault_root);
            return json_response(
                409,
                &format!(
                    r#"{{"error":{}}}"#,
                    json_str(&format!(
                        "cannot merge #{alias} → #{canonical}: it conflicts with an existing \
                         rule in aliases.toml — edit that file directly"
                    ))
                ),
            );
        }
    }
    match rebuild_index_now(state) {
        Ok(_) => json_response(200, r#"{"ok":true,"changed":true}"#),
        Err(e) => json_response(500, &format!(r#"{{"error":{}}}"#, json_str(&e))),
    }
}

/// `POST /api/source/:sha/tags` `{tags: ["..."]}` — the ONE sanctioned
/// product write to a source file: an explicit per-source user action
/// (accepting an inferred tag / adding a tag) inserts into that note's
/// frontmatter, identical in kind to an Obsidian edit. Everything else about
/// tags stays projection-only.
fn handle_source_tags_post(
    state: &AppState,
    path: &str,
    body: &str,
) -> Response<std::io::Cursor<Vec<u8>>> {
    let sha = path
        .trim_start_matches("/api/source/")
        .trim_end_matches("/tags")
        .trim_matches('/');
    let parsed: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return json_response(400, r#"{"error":"invalid JSON body"}"#),
    };
    let tags: Vec<String> = parsed
        .get("tags")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|t| t.as_str())
                .filter_map(ovp_domain::tags::normalize_tag)
                .collect()
        })
        .unwrap_or_default();
    if tags.is_empty() {
        return json_response(400, r#"{"error":"tags must be a non-empty string array"}"#);
    }
    let _write = state
        .tags_write_lock
        .lock()
        .unwrap_or_else(|p| p.into_inner());
    let Some(model) = state.current_model() else {
        return json_response(503, r#"{"error":"index not available"}"#);
    };
    let Some(rel) = model
        .sources
        .iter()
        .find(|s| s.sha256 == sha)
        .and_then(|s| s.rel_path.clone())
    else {
        return json_response(404, r#"{"error":"unknown source or no file path"}"#);
    };
    // Same traversal guard as the read path: a poisoned index must never
    // direct a WRITE outside the vault.
    if !ovp_api_projection::is_plain_relative(&rel) {
        return json_response(400, r#"{"error":"source path rejected"}"#);
    }
    // Same lifecycle-move fallback as the read path: a processed source's
    // recorded raw-inbox path may have moved to 03-Processed.
    let recorded = state.vault_root.join(&rel);
    let note_path = if recorded.is_file() {
        recorded
    } else if let Some(moved) =
        ovp_api_projection::readers::lifecycle_moved_path(&state.vault_root, &state.layout, &rel)
    {
        moved
    } else {
        // Recorded path gone and no lifecycle candidate: a stale row, not a
        // server fault — 404, never a 500 from the read below.
        return json_response(404, r#"{"error":"source file not found"}"#);
    };
    let text = match std::fs::read_to_string(&note_path) {
        Ok(t) => t,
        Err(e) => {
            return json_response(
                500,
                &format!(
                    r#"{{"error":{}}}"#,
                    json_str(&format!("reading {rel}: {e}"))
                ),
            );
        }
    };
    match ovp_domain::tags::add_tags_to_frontmatter(&text, &tags) {
        Ok(Some(updated)) => {
            // Atomic replace with a unique temp sibling: canonical USER data,
            // not rebuildable — a crash mid-write must never truncate the note.
            if let Err(e) = ovp_domain::tags::write_atomic(&note_path, &updated) {
                return json_response(
                    500,
                    &format!(
                        r#"{{"error":{}}}"#,
                        json_str(&format!("writing {rel}: {e}"))
                    ),
                );
            }
            // Editing a QUEUED note changes its content hash → the rebuilt
            // index keys it under a NEW sha. Report the row now living at
            // this rel_path so the client can re-route instead of 404ing.
            match rebuild_index_now(state) {
                Ok(rebuilt) => {
                    let new_sha = rebuilt
                        .sources
                        .iter()
                        .find(|s| s.rel_path.as_deref() == Some(rel.as_str()))
                        .map(|s| s.sha256.clone())
                        .unwrap_or_else(|| sha.to_string());
                    json_response(
                        200,
                        &format!(
                            r#"{{"ok":true,"changed":true,"sha":{}}}"#,
                            json_str(&new_sha)
                        ),
                    )
                }
                Err(e) => json_response(500, &format!(r#"{{"error":{}}}"#, json_str(&e))),
            }
        }
        Ok(None) => json_response(200, r#"{"ok":true,"changed":false}"#),
        Err(e) => json_response(400, &format!(r#"{{"error":{}}}"#, json_str(&e))),
    }
}

/// Rebuild the read model (the tag changes live in files the projection
/// derives from), persist it, refresh the served snapshot, and return it.
/// Synchronous — vault-scale rebuilds are ~2s and curation is a
/// one-operator surface.
fn rebuild_index_now(state: &AppState) -> Result<ovp_index::IndexModel, String> {
    let built_at = ovp_index::now_rfc3339();
    let date = built_at.get(..10).unwrap_or("1970-01-01").to_string();
    let model = ovp_index::build_index_at(&state.vault_root, &date, Some("tags-ui"), &built_at)?;
    ovp_index::write_index(&state.vault_root, &model)?;
    state.refresh_model();
    Ok(model)
}

/// A string as a JSON string literal (error payloads).
fn json_str(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "\"error\"".into())
}

fn handle_find(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    let params = parse_query_string(url);
    // Alias-resolve the tag param the same way `ovp2 find` does; a broken
    // alias table degrades to normalize-only (a read endpoint should answer,
    // not 500 — `ovp2 index` is where table breakage fails loud).
    let tag = params.get("tag").map(|raw| {
        let aliases = ovp_domain::tags::TagAliases::load(&state.vault_root).unwrap_or_default();
        aliases.resolve_raw(raw).unwrap_or_else(|| raw.clone())
    });
    let query = Query {
        kind: params.get("kind").and_then(|k| match k.as_str() {
            "sources" => Some(QueryKind::Sources),
            "packs" => Some(QueryKind::Packs),
            "claims" => Some(QueryKind::Claims),
            "runs" => Some(QueryKind::Runs),
            "tags" => Some(QueryKind::Tags),
            "entities" => Some(QueryKind::Entities),
            _ => None,
        }),
        status: params.get("status").cloned(),
        date: params.get("date").cloned(),
        term: params.get("term").cloned(),
        tag,
        entity: params.get("entity").cloned(),
    };

    let body = bodies::find_body(&model, &query).to_string();
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
        tag: None,
        entity: None,
    };
    let body = bodies::find_body(&model, &query).to_string();
    json_stamped(200, &body, Some(&model))
}

fn handle_themes(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    // Read the model ONCE up front so the theme counts (from the live ledger)
    // ship with the projection stamp they were paired against — the client can
    // see both halves came from the same freshness.
    let model = state.current_model();
    let records = load_active_records(state);
    let body = bodies::themes_body(&records).to_string();
    json_stamped(200, &body, model.as_ref())
}

/// `POST /api/publish` — kick off ONE background publish run using
/// `.ovp/publish.toml` (the portal/desktop button never passes flags; an
/// unconfigured vault gets a 400 naming the file). 202 on start, 409 while a
/// prior run is still going; progress/result via `GET /api/publish/status`.
/// Vault consistency is the RunLock's job inside the run — this slot only
/// keeps the portal honest about concurrency.
fn handle_publish_start(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let resolved = match ovp_publish::run::resolve_publish(
        &state.vault_root,
        &ovp_publish::run::RunOverrides::default(),
    ) {
        Ok(r) => r,
        Err(e) => {
            let body = serde_json::json!({ "error": e, "code": "publish_not_configured" });
            return json_response(400, &body.to_string());
        }
    };
    {
        // A poisoned lock (a prior panic while holding it) must not brick the
        // endpoint for the server's lifetime — recover the inner state.
        let mut job = state.publish_job.lock().unwrap_or_else(|e| e.into_inner());
        if job.running {
            return json_response(
                409,
                r#"{"error":"publish already running","code":"publish_running"}"#,
            );
        }
        job.running = true;
    }
    let vault_root = state.vault_root.clone();
    let job = Arc::clone(&state.publish_job);
    std::thread::spawn(move || {
        let date = ovp_index::now_rfc3339()[..10].to_string();
        // catch_unwind: a panic anywhere inside the publish must still clear
        // `running` and surface as a failed outcome — otherwise the slot
        // wedges at running=true until restart (review finding).
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            ovp_publish::run::run_publish(&vault_root, &date, &resolved, false, false)
        }))
        .unwrap_or_else(|panic| {
            let msg = panic
                .downcast_ref::<&str>()
                .map(|s| (*s).to_string())
                .or_else(|| panic.downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "publish panicked".to_string());
            Err(format!("publish panicked: {msg}"))
        });
        let outcome = match result {
            Ok(summary) => {
                let mut v = serde_json::to_value(&summary).unwrap_or_default();
                if let Some(obj) = v.as_object_mut() {
                    obj.insert("ok".into(), serde_json::Value::Bool(true));
                    obj.insert("finished_at".into(), ovp_index::now_rfc3339().into());
                }
                v
            }
            Err(e) => serde_json::json!({
                "ok": false,
                "error": e,
                "finished_at": ovp_index::now_rfc3339(),
            }),
        };
        let mut job = job.lock().unwrap_or_else(|e| e.into_inner());
        job.running = false;
        job.last = Some(outcome);
    });
    json_response(202, r#"{"started":true}"#)
}

/// `GET /api/publish/status` — `{running, configured, last}` for the portal's
/// publish card.
fn handle_publish_status(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let configured = ovp_publish::run::resolve_publish(
        &state.vault_root,
        &ovp_publish::run::RunOverrides::default(),
    )
    .is_ok();
    let job = state.publish_job.lock().unwrap_or_else(|e| e.into_inner());
    let body = serde_json::json!({
        "running": job.running,
        "configured": configured,
        "last": job.last,
    });
    json_response(200, &body.to_string())
}

// ---------------------------------------------------------------------------
// Manual pipeline run (`POST /api/schedule/run`) — the portal's "run today's
// job now". Triple protection: this slot rejects double-clicks (409), a LIVE
// heartbeat run rejects overlap with the automatic schedule (409), and the
// scheduler dispatch lock + vault RunLock in the child guard cross-process
// races underneath. The child is `ovp2 schedule run-now`, which records
// schedule-state so the automatic tick will NOT re-run the same occurrence.
// ---------------------------------------------------------------------------

/// Jobs the portal may trigger — the registry's built-ins.
const MANUAL_RUN_JOBS: &[&str] = &["daily", "crystallize"];

fn handle_run_start(state: &AppState, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let job = if body.trim().is_empty() {
        "daily".to_string()
    } else {
        match serde_json::from_str::<serde_json::Value>(body) {
            Ok(v) => v
                .get("job")
                .and_then(|j| j.as_str())
                .unwrap_or("daily")
                .to_string(),
            Err(_) => return json_response(400, r#"{"error":"body must be JSON"}"#),
        }
    };
    if !MANUAL_RUN_JOBS.contains(&job.as_str()) {
        let body = serde_json::json!({
            "error": format!("unknown job `{job}` — expected one of {MANUAL_RUN_JOBS:?}"),
            "code": "unknown_job",
        });
        return json_response(400, &body.to_string());
    }
    // Overlap guard 1: a run the SCHEDULER (or a previous click) already has
    // in flight, as seen by the live heartbeat.
    if let Some(lr) = state.current_last_run()
        && lr.status == "running"
    {
        return json_response(
            409,
            r#"{"error":"a pipeline run is already in progress","code":"run_in_progress"}"#,
        );
    }
    // Overlap guard 2: this endpoint's own slot (double-click protection).
    {
        let mut slot = state.manual_run.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(running) = &slot.running {
            let body = serde_json::json!({
                "error": format!("manual run `{running}` is already in progress"),
                "code": "manual_run_running",
            });
            return json_response(409, &body.to_string());
        }
        slot.running = Some(job.clone());
    }
    let bin = state
        .ovp2_bin
        .clone()
        .or_else(|| std::env::current_exe().ok());
    let Some(bin) = bin else {
        let mut slot = state.manual_run.lock().unwrap_or_else(|e| e.into_inner());
        slot.running = None;
        return json_response(500, r#"{"error":"cannot resolve the ovp2 binary"}"#);
    };
    let vault_root = state.vault_root.clone();
    let slot = Arc::clone(&state.manual_run);
    let job_id = job.clone();
    std::thread::spawn(move || {
        let mut cmd = std::process::Command::new(&bin);
        cmd.args([
            "schedule",
            "run-now",
            "--vault-root",
            &vault_root.display().to_string(),
            // `<ID>` is a POSITIONAL arg on `schedule run-now`, not a `--id`
            // flag — passing `--id` makes clap reject it. Keep this in sync with
            // the CLI definition (see the run_now_args parse test in ovp-cli).
            &job_id,
            // Atomic with the dispatch lock in the child: skip when the job
            // (auto tick or another trigger) already ran moments ago — closes
            // the check-then-spawn window (codex P2).
            "--unless-ran-within-secs",
            "120",
        ]);
        // The child must re-derive provider values from the FILES, not
        // inherit this server's startup env (env wins over providers.toml in
        // the child, so stale inherited values would shadow a fresh edit —
        // codex P1). Remove every provider-managed variable.
        let mut managed: std::collections::BTreeSet<String> =
            ovp_domain::providers::read_providers_file(&vault_root)
                .map(|m| m.into_keys().collect())
                .unwrap_or_default();
        managed.extend(ovp_domain::providers::read_legacy_daily_env(&vault_root).into_keys());
        for key in [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "OVP_LLM_MODEL",
            "OVP_LLM_MAX_TOKENS",
            "OVP_LLM_NO_PROXY",
            "OVP_LLM_TIMEOUT_SECS",
            "GITHUB_TOKEN",
            "PINBOARD_TOKEN",
            "PINBOARD_API_BASE",
        ] {
            managed.insert(key.to_string());
        }
        for key in &managed {
            cmd.env_remove(key);
        }
        // catch_unwind: a panic anywhere here must still clear the slot, or
        // the manual-run button wedges until restart (same contract as the
        // publish job).
        let out = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| cmd.output()))
            .unwrap_or_else(|_| Err(std::io::Error::other("manual-run thread panicked")));
        let outcome = match out {
            Ok(o) => {
                let stderr_tail: String = String::from_utf8_lossy(&o.stderr)
                    .lines()
                    .rev()
                    .take(5)
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                    .collect::<Vec<_>>()
                    .join("\n");
                serde_json::json!({
                    "ok": o.status.success(),
                    "job": job_id,
                    "finished_at": ovp_index::now_rfc3339(),
                    "error": if o.status.success() { serde_json::Value::Null } else { serde_json::json!(stderr_tail) },
                })
            }
            Err(e) => serde_json::json!({
                "ok": false,
                "job": job_id,
                "finished_at": ovp_index::now_rfc3339(),
                "error": format!("spawn {}: {e}", bin.display()),
            }),
        };
        let mut slot = slot.lock().unwrap_or_else(|e| e.into_inner());
        slot.running = None;
        slot.last = Some(outcome);
    });
    json_response(202, r#"{"started":true}"#)
}

/// `GET /api/schedule/run/status` — `{running, heartbeat_running, last,
/// jobs: {id: {last_run, last_status}}}` so the portal can disable the
/// button, ask for confirmation on a re-run, and show the last outcome.
fn handle_run_status(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let heartbeat_running = state
        .current_last_run()
        .is_some_and(|lr| lr.status == "running");
    let jobs: serde_json::Map<String, serde_json::Value> =
        ovp_scheduler::load_state(&state.vault_root)
            .map(|st| {
                st.runs
                    .iter()
                    .map(|(id, run)| {
                        (
                            id.clone(),
                            serde_json::json!({
                                "last_run": run.last_run,
                                "last_status": run.last_status,
                            }),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
    let slot = state.manual_run.lock().unwrap_or_else(|e| e.into_inner());
    let body = serde_json::json!({
        "running": slot.running,
        "heartbeat_running": heartbeat_running,
        "last": slot.last,
        "jobs": jobs,
    });
    json_response(200, &body.to_string())
}

// ---------------------------------------------------------------------------
// Attention acknowledgements — `.ovp/attention-acks.json`. Keyed by
// (sha, status): an acknowledged needs-content source stays hidden until its
// STATUS changes (e.g. it later blocks), which re-surfaces it.
// ---------------------------------------------------------------------------

const ATTENTION_ACKS_REL: &str = ".ovp/attention-acks.json";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
struct AttentionAck {
    sha: String,
    status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    acked_at: Option<String>,
}

#[derive(Debug, Default, serde::Serialize, serde::Deserialize)]
struct AttentionAcksFile {
    #[serde(default)]
    acks: Vec<AttentionAck>,
}

fn read_attention_acks(vault_root: &Path) -> AttentionAcksFile {
    let path = vault_root.join(ATTENTION_ACKS_REL);
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn handle_attention_ack(state: &AppState, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let v: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return json_response(400, r#"{"error":"body must be JSON"}"#),
    };
    let (Some(sha), Some(status)) = (
        v.get("sha")
            .and_then(|s| s.as_str())
            .filter(|s| !s.is_empty()),
        v.get("status")
            .and_then(|s| s.as_str())
            .filter(|s| !s.is_empty()),
    ) else {
        return json_response(400, r#"{"error":"`sha` and `status` are required"}"#);
    };
    // Only real sources are acknowledgeable — a typo'd sha must not append.
    let known = state
        .current_model()
        .is_some_and(|m| m.sources.iter().any(|s| s.sha256 == sha));
    if !known {
        return json_response(404, r#"{"error":"unknown source sha"}"#);
    }
    let _guard = state
        .acks_write_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let mut file = read_attention_acks(&state.vault_root);
    let ack = AttentionAck {
        sha: sha.to_string(),
        status: status.to_string(),
        acked_at: Some(ovp_index::now_rfc3339()),
    };
    file.acks
        .retain(|a| !(a.sha == ack.sha && a.status == ack.status));
    file.acks.push(ack);
    let path = state.vault_root.join(ATTENTION_ACKS_REL);
    let body_json = match serde_json::to_string_pretty(&file) {
        Ok(b) => b,
        Err(e) => {
            let b = serde_json::json!({ "error": format!("serialize acks: {e}") });
            return json_response(500, &b.to_string());
        }
    };
    if let Err(e) = std::fs::write(&path, format!("{body_json}\n")) {
        let b = serde_json::json!({ "error": format!("write {}: {e}", path.display()) });
        return json_response(500, &b.to_string());
    }
    json_response(200, r#"{"acked":true}"#)
}

// ---------------------------------------------------------------------------
// LLM provider configuration — a GUI over `.ovp/providers.toml`.
// ---------------------------------------------------------------------------

/// Values for these names are secrets: GET masks them to their last 4 chars,
/// and POST ignores round-tripped masked values.
fn provider_secret(name: &str) -> bool {
    name.contains("KEY") || name.contains("TOKEN") || name.contains("SECRET")
}

const MASK_PREFIX: &str = "\u{2022}\u{2022}\u{2022}\u{2022}";

fn handle_providers_get(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    match ovp_domain::providers::read_providers_file(&state.vault_root) {
        Ok(map) => {
            let masked: serde_json::Map<String, serde_json::Value> = map
                .into_iter()
                .map(|(k, v)| {
                    let shown = if provider_secret(&k) {
                        let tail: String = v
                            .chars()
                            .rev()
                            .take(4)
                            .collect::<Vec<_>>()
                            .into_iter()
                            .rev()
                            .collect();
                        format!("{MASK_PREFIX}{tail}")
                    } else {
                        v
                    };
                    (k, serde_json::json!(shown))
                })
                .collect();
            let body = serde_json::json!({ "env": masked });
            json_response(200, &body.to_string())
        }
        Err(e) => {
            let body = serde_json::json!({ "error": e });
            json_response(500, &body.to_string())
        }
    }
}

fn handle_providers_set(state: &AppState, body: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    let v: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return json_response(400, r#"{"error":"body must be JSON"}"#),
    };
    let Some(set) = v.get("set").and_then(|s| s.as_object()) else {
        return json_response(400, r#"{"error":"`set` object is required"}"#);
    };
    let unset: Vec<String> = v
        .get("unset")
        .and_then(|u| u.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let _guard = state
        .acks_write_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let mut map = match ovp_domain::providers::read_providers_file(&state.vault_root) {
        Ok(m) => m,
        Err(e) => {
            let body = serde_json::json!({ "error": e });
            return json_response(500, &body.to_string());
        }
    };
    for (k, val) in set {
        let Some(text) = val.as_str() else {
            let body = serde_json::json!({ "error": format!("`{k}` must be a string") });
            return json_response(400, &body.to_string());
        };
        // A masked value round-tripped from GET means "unchanged".
        if text.starts_with(MASK_PREFIX) {
            continue;
        }
        if text.trim().is_empty() {
            map.remove(k);
        } else {
            map.insert(k.clone(), text.to_string());
        }
    }
    for k in unset {
        map.remove(&k);
    }
    match ovp_domain::providers::write_providers_file(&state.vault_root, &map) {
        Ok(()) => json_response(
            200,
            // The RUNNING server seeded its env at startup and env wins over
            // the file — children (scheduler jobs) pick the change up
            // immediately, the in-process ask does after a restart.
            r#"{"saved":true,"restart_required":true}"#,
        ),
        Err(e) => {
            let body = serde_json::json!({ "error": e });
            json_response(500, &body.to_string())
        }
    }
}

/// `GET /api/theme-pages` — the grounded topic pages built by
/// `ovp2 crystal-theme-pages`, joined against the live active records for the
/// citation lookup. A missing/corrupt projection degrades to an empty body
/// (the portal simply hides the wiki panel); corruption is logged, and
/// `crystal-theme-pages` is where it fails loud.
fn handle_theme_pages(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = state.current_model();
    let records = load_active_records(state);
    let pages = match ovp_domain::crystal::theme_pages::ThemePagesFile::load(
        &state
            .vault_root
            .join(state.layout.crystal_store_dir())
            .join("theme_pages.json"),
    ) {
        Ok(pages) => pages,
        Err(e) => {
            eprintln!("handle_theme_pages: ignoring theme_pages.json ({e})");
            None
        }
    };
    let body = bodies::theme_pages_body(pages.as_ref(), &records).to_string();
    json_stamped(200, &body, model.as_ref())
}

/// `GET /api/terrain` — the knowledge-terrain projection built by
/// `ovp2 crystal-terrain` (`.ovp/crystal/terrain.json`), served raw. 404 with a
/// hint when it hasn't been built yet.
/// Cheap contract check for `terrain.json`: parseable, `schema` is an
/// `ovp.crystal.terrain*` string, and `points` is an array. Guards the client
/// from a truncated/wrong-shaped projection that would crash the Terrain view.
fn terrain_shape_ok(body: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .filter(|v| {
            v.get("schema")
                .and_then(|s| s.as_str())
                .is_some_and(|s| s.starts_with("ovp.crystal.terrain"))
                && v.get("points").is_some_and(|p| p.is_array())
        })
        .is_some()
}

fn handle_terrain(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let path = state.vault_root.join(".ovp/crystal/terrain.json");
    match std::fs::read_to_string(&path) {
        // Validate the v1 shape before claiming 200: a truncated or wrong-shaped
        // file (e.g. `{}`) would otherwise reach the client and crash the view on
        // `data.points`. Terrain is generated independently of the index — do NOT
        // stamp it with the index's built_at/run_id, or a lone `index build`
        // would advertise false freshness for a stale terrain body.
        Ok(body) if terrain_shape_ok(&body) => json_stamped(200, &body, None),
        Ok(_) => {
            eprintln!(
                "handle_terrain: corrupt/incompatible terrain.json at {}",
                path.display()
            );
            json_stamped(
                500,
                "{\"error\":\"terrain.json is corrupt — rebuild with `ovp2 crystal-terrain`\"}",
                None,
            )
        }
        // Only a genuinely-absent file is "not built yet". Permission errors,
        // invalid UTF-8, or a dir at the path are real faults — surface them as
        // 500 (and log) rather than telling the operator to rebuild.
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => json_stamped(
            404,
            "{\"error\":\"no terrain.json — run `ovp2 crystal-terrain --vault-root <v>`\"}",
            None,
        ),
        Err(e) => {
            eprintln!("handle_terrain: reading {}: {e}", path.display());
            json_stamped(500, "{\"error\":\"terrain read failed\"}", None)
        }
    }
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
        // Attention acknowledgements overlay: (sha,status) pairs the operator
        // dismissed. All attention surfaces (Today, System, the nav dot)
        // derive from this one model payload, so filtering stays consistent.
        let acks = read_attention_acks(&state.vault_root);
        obj.insert(
            "attention_acks".into(),
            serde_json::json!(
                acks.acks
                    .iter()
                    .map(|a| serde_json::json!({ "sha": a.sha, "status": a.status }))
                    .collect::<Vec<_>>()
            ),
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
    readers::load_active_records(&state.vault_root, &state.layout)
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
                let persp = match query.get("persp").map(String::as_str) {
                    Some("source") => graph::Perspective::Source,
                    _ => graph::Perspective::Claim,
                };
                let params = graph::GraphParams {
                    mode: graph::GraphMode::Overview,
                    limit,
                    theme: None,
                    focus: None,
                    hops: graph::MAX_HOPS,
                    persp,
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

/// `GET /api/entities` — the Tier-0 URL entity index (id/kind/url/count).
fn handle_entities_api(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let Some(model) = state.current_model() else {
        return json_response(503, r#"{"error":"index not available"}"#);
    };
    json_stamped(
        200,
        &bodies::entities_body(&model).to_string(),
        Some(&model),
    )
}

/// `GET /api/entity/:id` — one entity's mentioning sources + citing claims.
fn handle_entity_api(state: &AppState, url: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    // Strip any query string before the id so `?x=1` never lands in it.
    let path = url.split('?').next().unwrap_or(url);
    let id = url_decode(path.strip_prefix("/api/entity/").unwrap_or(""));
    if id.is_empty() {
        return json_response(400, r#"{"error":"missing entity id"}"#);
    }
    let Some(model) = state.current_model() else {
        return json_response(503, r#"{"error":"index not available"}"#);
    };
    match bodies::entity_body(&model, &id) {
        Some(v) => json_stamped(200, &v.to_string(), Some(&model)),
        None => json_response(404, r#"{"error":"entity not found"}"#),
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
    let reader_root = state.vault_root.join(state.layout.reader_root());
    match bodies::claim_body(&records, model.as_ref(), &reader_root, &id, true) {
        Some(v) => json_stamped(200, &v.to_string(), model.as_ref()),
        None => json_response(404, r#"{"error":"claim not found"}"#),
    }
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

    // Read the doc via the shared reader (needs the source's rel_path). The
    // live server ships the full markdown; the publisher passes `None` for a
    // lite page. Missing sha → None doc → 404 from the builder below.
    let doc = model
        .sources
        .iter()
        .find(|s| s.sha256 == sha)
        .map(|source| {
            let (markdown, truncated, error) = readers::read_source_doc(
                &state.vault_root,
                &state.layout,
                source.rel_path.as_deref(),
            );
            bodies::SourceDoc {
                markdown,
                truncated,
                error,
            }
        });

    let evidence = state.current_evidence();
    match bodies::source_body(&model, evidence.as_ref(), &sha, doc) {
        Some(v) => json_response(200, &v.to_string()),
        None => {
            let body = serde_json::json!({ "error": format!("source not found: {sha}") });
            json_response(404, &body.to_string())
        }
    }
}

fn handle_flow(state: &AppState) -> Response<std::io::Cursor<Vec<u8>>> {
    let model = match state.current_model() {
        Some(m) => m,
        None => return json_response(503, r#"{"error":"index not available"}"#),
    };

    json_response(200, &bodies::flow_body(&model).to_string())
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
        // Factory present (anthropic feature) AND a non-empty key in env or
        // providers.toml — matches when POST /api/ask will accept work.
        "llm_configured": state.ask_client.is_some()
            && api_key_configured(&state.vault_root),
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
/// The mutation-route guard: JSON content type + same-machine origin. A
/// browser will happily SEND a cross-site `text/plain` simple POST to
/// loopback even though it can't read the response — every state-changing
/// route (ask, tag decisions, source tag writes) must pass this first.
fn guard_json_same_origin(headers: &AskHeaders) -> Option<Response<std::io::Cursor<Vec<u8>>>> {
    let is_json = headers
        .content_type
        .as_deref()
        .and_then(|v| v.split(';').next())
        .map(|v| v.trim().eq_ignore_ascii_case("application/json"))
        .unwrap_or(false);
    if !is_json {
        return Some(json_response(
            415,
            r#"{"error":"content-type must be application/json"}"#,
        ));
    }
    if let Some(origin) = headers.origin.as_deref()
        && !is_loopback_origin(origin)
    {
        return Some(json_response(
            403,
            r#"{"error":"cross-origin write rejected"}"#,
        ));
    }
    None
}

fn handle_ask(
    state: &AppState,
    headers: &AskHeaders,
    body: &str,
    slot: AskSlot,
) -> Response<std::io::Cursor<Vec<u8>>> {
    if let Some(resp) = guard_json_same_origin(headers) {
        return resp;
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
    // Optional session stem: continue an existing `.ovp/chats/<chat>.md`
    // (append + multi-turn context). Invalid stems are ignored (new chat).
    let chat = parsed
        .get("chat")
        .and_then(|c| c.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty() && valid_chat_stem(s))
        .map(str::to_string);
    // Prior turns for LLM continuity (client-owned live thread). Cap so a
    // runaway body cannot blow the request.
    const MAX_HISTORY_TURNS: usize = 32;
    let history: Vec<AskHistoryTurn> = parsed
        .get("history")
        .and_then(|h| h.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let q = item.get("question")?.as_str()?.trim();
                    let a = item.get("answer")?.as_str()?.trim();
                    if q.is_empty() || a.is_empty() {
                        return None;
                    }
                    Some(AskHistoryTurn {
                        question: q.to_string(),
                        answer: a.to_string(),
                    })
                })
                .take(MAX_HISTORY_TURNS)
                .collect()
        })
        .unwrap_or_default();

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
        let result = run_ask(
            &factory,
            &model,
            evidence.as_ref(),
            &question,
            chat.as_deref(),
            &history,
            &vault_root,
        );
        let _ = tx.send(result);
    });

    match rx.recv_timeout(state.ask_timeout) {
        Ok(Ok(payload)) => json_response(200, &payload.to_string()),
        Ok(Err(e)) if e == LLM_NOT_CONFIGURED => {
            // Factory is installed (feature on) but no key in env / providers.toml.
            json_response(
                503,
                r#"{"error":"llm not configured","code":"llm_not_configured"}"#,
            )
        }
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
/// `chat` + `history` continue a multi-turn session (one history entry).
fn run_ask(
    factory: &AskClientFactory,
    model: &IndexModel,
    evidence: Option<&EvidenceModel>,
    question: &str,
    chat: Option<&str>,
    history: &[AskHistoryTurn],
    vault_root: &std::path::Path,
) -> Result<serde_json::Value, String> {
    let mut client = factory()?;
    let args = AskArgs {
        question: question.to_string(),
        save_chat: true,
        chat: chat.map(str::to_string),
        history: history.to_vec(),
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
                    "link_target": citation_link(item, &pack_sha, model),
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
        "intent": result.intent.as_str(),
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
        EvidenceKind::Source => "source",
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
fn citation_link(
    item: &EvidenceItem,
    pack_sha: &HashMap<&str, &str>,
    model: &IndexModel,
) -> Option<String> {
    match item.kind {
        EvidenceKind::Claim => {
            // Claim evidence ids are now the STABLE ck- ledger key, but the
            // portal's claim-card anchors are keyed by claim_id — resolve the
            // key back through the model so the link lands on the card
            // instead of an unknown anchor (codex P1). Older indexes (no
            // claim_key) pass the id through unchanged. When the RESOLVED
            // claim_id is shared by several rows the anchor is ambiguous and
            // could open the wrong claim — no link then (round-3 P1; the
            // citation stays visible and auditable, just unclickable — same
            // degradation the theme-page chips use).
            let anchor = model
                .claims
                .iter()
                .find(|c| c.claim_key.as_deref() == Some(item.id.as_str()))
                .map(|c| c.claim_id.as_str())
                .unwrap_or(item.id.as_str());
            let occurrences = model.claims.iter().filter(|c| c.claim_id == anchor).count();
            if occurrences > 1 {
                return None;
            }
            Some(format!("/knowledge#{anchor}"))
        }
        EvidenceKind::Source => {
            // id is the source sha256 for find-source hits.
            if item.id.trim().is_empty() {
                None
            } else {
                Some(format!("/library/{}", item.id))
            }
        }
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
        && let Some(body) = read_app_file(state, "index.html")
    {
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

    #[test]
    fn terrain_shape_ok_gates_corrupt_projections() {
        assert!(terrain_shape_ok(
            r#"{"schema":"ovp.crystal.terrain/v1","points":[]}"#
        ));
        assert!(!terrain_shape_ok("{}")); // wrong shape
        assert!(!terrain_shape_ok(r#"{"schema":"ovp.crystal.terrain/v1"}"#)); // no points
        assert!(!terrain_shape_ok(r#"{"points":[]}"#)); // no schema
        assert!(!terrain_shape_ok(r#"{"schema":"other","points":[]}"#)); // wrong schema
        assert!(!terrain_shape_ok(
            r#"{"schema":"ovp.crystal.terrain/v1","points":{}}"#
        )); // points not array
        assert!(!terrain_shape_ok("{truncated")); // unparseable
    }

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
            tags_write_lock: std::sync::Mutex::new(()),
            publish_job: Arc::new(std::sync::Mutex::new(PublishJob::default())),
            manual_run: Arc::new(std::sync::Mutex::new(ManualRun::default())),
            ovp2_bin: None,
            acks_write_lock: std::sync::Mutex::new(()),
        }
    }

    /// Read a response header value (test helper).
    fn header_value(resp: &Response<std::io::Cursor<Vec<u8>>>, name: &str) -> String {
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
                blocks: None,
                raw_stop_reason: None,
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
                tags: Vec::new(),
                tags_inferred: Vec::new(),
                entities: Vec::new(),
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
                claim_key: None,
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
            CrystalStatus, DurableCitation, FinalClass, ProvenanceClass, StoreEvent, StoreOp,
            StrengthClass,
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
    fn attention_ack_persists_and_overlays_the_model() {
        let vault = portal_vault("attn-ack", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None);

        // Unknown sha → 404 (typos must not append).
        let resp = dispatch(
            &st,
            Method::Post,
            "/api/attention/ack",
            r#"{"sha":"nope","status":"needs_content"}"#,
        );
        assert_eq!(resp.status_code(), 404);
        // Missing fields → 400.
        assert_eq!(
            dispatch(&st, Method::Post, "/api/attention/ack", "{}").status_code(),
            400
        );

        // A real source acks fine and shows up in the /api/model overlay.
        let sha = body_json(dispatch(&st, Method::Get, "/api/model", ""))["sources"][0]["sha256"]
            .as_str()
            .unwrap()
            .to_string();
        let resp = dispatch(
            &st,
            Method::Post,
            "/api/attention/ack",
            &format!(r#"{{"sha":"{sha}","status":"needs_content"}}"#),
        );
        assert_eq!(resp.status_code(), 200);
        let v = body_json(dispatch(&st, Method::Get, "/api/model", ""));
        let acks = v["attention_acks"].as_array().unwrap();
        assert_eq!(acks.len(), 1);
        assert_eq!(acks[0]["sha"], sha.as_str());
        assert_eq!(acks[0]["status"], "needs_content");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn providers_endpoints_mask_secrets_and_merge_saves() {
        let vault = portal_vault("providers-ep", "50-Inbox/03-Processed/good.md", "body\n");
        std::fs::write(
            vault.join(".ovp/providers.toml"),
            "[env]\nANTHROPIC_API_KEY = \"sk-secret-1234\"\nGITHUB_TOKEN = \"ghp-tok-5678\"\nOVP_LLM_MODEL = \"m-1\"\n",
        ).unwrap();
        let st = state(vault.clone(), None);

        let v = body_json(dispatch(&st, Method::Get, "/api/providers", ""));
        let key = v["env"]["ANTHROPIC_API_KEY"].as_str().unwrap();
        assert!(
            key.ends_with("1234") && key.starts_with('\u{2022}'),
            "masked: {key}"
        );
        assert_eq!(
            v["env"]["OVP_LLM_MODEL"], "m-1",
            "non-secret values are plain"
        );

        // Round-tripping the masked key keeps the stored secret; new values
        // write; empty removes; unrelated keys survive.
        let body = format!(
            r#"{{"set":{{"ANTHROPIC_API_KEY":{},"OVP_LLM_MODEL":"m-2","OVP_LLM_NO_PROXY":"1","ANTHROPIC_BASE_URL":""}}}}"#,
            serde_json::json!(key)
        );
        let resp = dispatch(&st, Method::Post, "/api/providers", &body);
        assert_eq!(resp.status_code(), 200);
        let saved = ovp_domain::providers::read_providers_file(&vault).unwrap();
        assert_eq!(
            saved.get("ANTHROPIC_API_KEY").map(String::as_str),
            Some("sk-secret-1234")
        );
        assert_eq!(saved.get("OVP_LLM_MODEL").map(String::as_str), Some("m-2"));
        assert_eq!(saved.get("OVP_LLM_NO_PROXY").map(String::as_str), Some("1"));
        assert_eq!(
            saved.get("GITHUB_TOKEN").map(String::as_str),
            Some("ghp-tok-5678")
        );
        assert!(
            !saved.contains_key("ANTHROPIC_BASE_URL"),
            "empty value removes the key"
        );

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn manual_run_rejects_overlap_and_unknown_jobs() {
        let vault = portal_vault("run-now-ep", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None);

        // Unknown job → 400.
        let resp = dispatch(&st, Method::Post, "/api/schedule/run", r#"{"job":"nuke"}"#);
        assert_eq!(resp.status_code(), 400);

        // A LIVE heartbeat run → 409 (the automatic schedule already runs).
        std::fs::write(
            vault.join(".ovp/last-run.json"),
            serde_json::json!({
                "schema": "ovp.last-run/v1",
                "run_id": "r-test",
                "status": "running",
                "started_at": ovp_index::now_rfc3339(),
                // Our own pid: alive and probe-able, so effective_status()
                // keeps the record `running` instead of downgrading it.
                "pid": std::process::id(),
            })
            .to_string(),
        )
        .unwrap();
        let resp = dispatch(&st, Method::Post, "/api/schedule/run", r#"{"job":"daily"}"#);
        assert_eq!(resp.status_code(), 409);
        let v = body_json(resp);
        assert_eq!(v["code"], "run_in_progress");

        // Endpoint slot busy → 409 even without a heartbeat.
        std::fs::remove_file(vault.join(".ovp/last-run.json")).unwrap();
        st.manual_run.lock().unwrap().running = Some("daily".into());
        let resp = dispatch(&st, Method::Post, "/api/schedule/run", r#"{"job":"daily"}"#);
        assert_eq!(resp.status_code(), 409);
        let v = body_json(resp);
        assert_eq!(v["code"], "manual_run_running");
        // Status reflects the slot.
        let v = body_json(dispatch(&st, Method::Get, "/api/schedule/run/status", ""));
        assert_eq!(v["running"], "daily");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn publish_endpoints_report_config_state_and_run_in_background() {
        let vault = portal_vault("publish-ep", "50-Inbox/03-Processed/good.md", "body\n");
        let st = state(vault.clone(), None);

        // Unconfigured: status says so, POST is a 400 naming the config file.
        let v = body_json(dispatch(&st, Method::Get, "/api/publish/status", ""));
        assert_eq!(v["configured"], false);
        assert_eq!(v["running"], false);
        let resp = dispatch(&st, Method::Post, "/api/publish", "");
        assert_eq!(resp.status_code(), 400);
        let v = body_json(resp);
        assert_eq!(v["code"], "publish_not_configured");

        // Configure via .ovp/publish.toml (relative out resolves against the
        // vault) and run one background publish to completion.
        std::fs::write(
            vault.join(".ovp/publish.toml"),
            "out = \"../publish-site\"\n",
        )
        .unwrap();
        // `..` in the configured out is rejected by the run guard — use an
        // absolute sibling path instead.
        let site = vault.parent().unwrap().join("publish-site");
        std::fs::write(
            vault.join(".ovp/publish.toml"),
            format!("out = \"{}\"\n", site.display()),
        )
        .unwrap();
        let resp = dispatch(&st, Method::Post, "/api/publish", "");
        assert_eq!(resp.status_code(), 202);
        let deadline = std::time::Instant::now() + Duration::from_secs(60);
        let last = loop {
            let v = body_json(dispatch(&st, Method::Get, "/api/publish/status", ""));
            if v["running"] == false && !v["last"].is_null() {
                break v["last"].clone();
            }
            assert!(
                std::time::Instant::now() < deadline,
                "publish never finished"
            );
            std::thread::sleep(Duration::from_millis(100));
        };
        assert_eq!(last["ok"], true, "{last}");
        assert!(site.join("api/model.json").is_file(), "site tree written");
        assert!(last["pushed"].is_null(), "no repo configured → no deploy");

        let _ = std::fs::remove_dir_all(vault.parent().unwrap());
    }

    #[test]
    fn theme_pages_endpoint_serves_pages_with_claim_lookup_and_degrades() {
        let vault = portal_vault("theme-pages-ep", "50-Inbox/03-Processed/good.md", "body\n");
        write_ledger(&vault);
        let store = vault.join(VaultLayout::new().crystal_store_dir());
        std::fs::write(
            store.join("theme_pages.json"),
            serde_json::json!({
                "schema": "ovp.theme_pages/v1",
                "pages": [
                    {"community_id": 0, "label": "Agent memory", "label_zh": "智能体记忆",
                     "claim_keys": ["a"],
                     "sections": [{"heading": "H", "body": "Grounded [claim:a]."}]},
                    // Cites an unknown key — dropped whole from the body.
                    {"community_id": 1, "label": "Ghost", "label_zh": "鬼",
                     "claim_keys": ["nope"],
                     "sections": [{"heading": "G", "body": "X [claim:nope]."}]}
                ]
            })
            .to_string(),
        )
        .unwrap();
        let st = state(vault.clone(), None);

        let v = body_json(dispatch(&st, Method::Get, "/api/theme-pages", ""));
        let pages = v["pages"].as_array().unwrap();
        assert_eq!(pages.len(), 1, "ghost-claim page must drop: {v}");
        assert_eq!(pages[0]["label"], "Agent memory");
        assert_eq!(pages[0]["sections"][0]["body"], "Grounded [claim:a].");
        assert_eq!(v["claims"]["a"]["claim_id"], "id-a");
        assert_eq!(v["claims"]["a"]["sources"][0], "good");

        // Missing projection → empty body, still 200 (portal hides the wiki).
        std::fs::remove_file(store.join("theme_pages.json")).unwrap();
        let v = body_json(dispatch(&st, Method::Get, "/api/theme-pages", ""));
        assert_eq!(v["pages"].as_array().unwrap().len(), 0);
        // Corrupt projection degrades the same way (logged, never a 500).
        std::fs::write(store.join("theme_pages.json"), "not json").unwrap();
        let resp = dispatch(&st, Method::Get, "/api/theme-pages", "");
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        assert_eq!(v["pages"].as_array().unwrap().len(), 0);

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
        assert!(
            v["age_seconds"].as_i64().unwrap() >= 0,
            "age is present and non-negative"
        );
        assert_eq!(v["counts"]["sources"], 1);
        assert_eq!(v["counts"]["packs"], 1);
        assert!(v["counts"]["claims"].is_u64());
        assert_eq!(v["llm_configured"], false);
        assert_eq!(v["ask_limits"]["timeout_secs"], st.ask_timeout.as_secs());
        assert_eq!(
            v["ask_limits"]["max_concurrent"],
            DEFAULT_MAX_CONCURRENT_ASKS
        );
        assert_eq!(v["version"], env!("CARGO_PKG_VERSION"));

        // Factory alone is not enough — product surface needs a key in
        // providers.toml (or env). Seed the file the System page writes.
        st.ask_client = Some(scripted_factory("answer", Duration::ZERO));
        let v = body_json(dispatch(&st, Method::Get, "/api/settings", ""));
        assert_eq!(
            v["llm_configured"], false,
            "factory without key still reports unconfigured"
        );
        std::fs::create_dir_all(vault.join(".ovp")).unwrap();
        std::fs::write(
            vault.join(".ovp/providers.toml"),
            "[env]\nANTHROPIC_API_KEY = \"sk-test\"\n",
        )
        .unwrap();
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
        assert_eq!(
            v["queued_live"], 2,
            "live count ticked down as 01-Raw drained"
        );
        assert_eq!(
            v["queued_at_build"], 4,
            "projection stays frozen — the whole point of the live overlay"
        );

        // /api/model carries the same live overlay while totals.queued stays 4.
        let m = body_json(dispatch(&st, Method::Get, "/api/model", ""));
        assert_eq!(m["queued_live"], 2);
        assert_eq!(
            m["totals"]["queued"], 4,
            "projection value untouched for other readers"
        );
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
            tags: Vec::new(),
            tags_inferred: Vec::new(),
            entities: Vec::new(),
        }
    }

    /// The P2 fix: at a quiescent vault, `queued_live` MUST equal the
    /// projection's `totals.queued` — a naive raw-file walk over-counts because
    /// blocked / duplicate sources keep files in 01-Raw yet are NOT queued.
    /// Fixture: 3 queued + 1 blocked + 1 duplicate ALL physically in 01-Raw
    /// (5 files) → the projection's queued is 3, and `queued_live` must be 3,
    /// NOT 5.
    #[test]
    fn processed_source_with_stale_raw_rel_path_is_not_double_subtracted() {
        // The real bug: a Processed source's rel_path still points at
        // 50-Inbox/01-Raw after the lifecycle move to 03-Processed, but its
        // FILE is gone from 01-Raw. A rel_path-prefix subtrahend counted it
        // and drove queued_live to 0 with 100+ real queued files present.
        // Basename-intersection must ignore it (not in 01-Raw physically).
        use ovp_index::{SourceStatus, Totals};
        let root = temp_root("live-queued-stale-relpath");
        let vault = root.join("vault");
        let raw = vault.join("50-Inbox/01-Raw/2026-07");
        std::fs::create_dir_all(&raw).unwrap();
        // 2 real queued files physically in 01-Raw. NO processed file here.
        for f in ["q0.md", "q1.md"] {
            std::fs::write(raw.join(f), "body\n").unwrap();
        }
        // Projection: 2 queued (present) + 3 Processed whose rel_path STILL
        // says 01-Raw but whose files already moved to 03-Processed (absent
        // from 01-Raw on disk).
        let mut sources = vec![
            raw_source("h0", SourceStatus::Queued, "q0.md"),
            raw_source("h1", SourceStatus::Queued, "q1.md"),
        ];
        for name in ["gone0.md", "gone1.md", "gone2.md"] {
            sources.push(raw_source("x", SourceStatus::Processed, name));
        }
        let mut baked = index_dated("2026-07-12");
        baked.sources = sources;
        baked.totals = Totals {
            queued: 2,
            ..Default::default()
        };
        ovp_index::write_index(&vault, &baked).unwrap();

        let st = state(vault, None);
        let live = st.live_queued_count(st.current_model().as_ref());
        assert_eq!(
            live, 2,
            "the 3 departed Processed rows must NOT be subtracted"
        );
    }

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
        assert_eq!(
            markdown_basenames(&raw).len(),
            6,
            "6 files physically present"
        );

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
        baked.totals = Totals {
            queued,
            ..Default::default()
        };
        ovp_index::write_index(&vault, &baked).unwrap();

        let st = state(vault.clone(), None);
        let live = st.live_queued_count(st.current_model().as_ref());
        assert_eq!(
            live, 3,
            "6 files − (blocked + dup + processed-in-raw) = 3 queued"
        );
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
        baked.totals = Totals {
            queued: 2,
            ..Default::default()
        };
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
        let vault = portal_vault(
            "model-provenance",
            "50-Inbox/03-Processed/good.md",
            "body\n",
        );
        let st = state(vault.clone(), None);

        let resp = dispatch(&st, Method::Get, "/api/model", "");
        assert_eq!(resp.status_code(), 200);
        // Provenance headers pair the body with the build that produced it.
        assert_eq!(
            header_of(&resp, "X-OVP-Built-At").as_deref(),
            Some("2026-07-09T00:00:00Z")
        );
        assert_eq!(
            header_of(&resp, "X-OVP-Run-Id").as_deref(),
            Some("daily-2026-07-09")
        );
        assert!(
            header_of(&resp, "X-OVP-Age-Seconds").is_some(),
            "age header present"
        );

        let v = body_json(resp);
        assert_eq!(v["built_at"], "2026-07-09T00:00:00Z");
        assert_eq!(v["run_id"], "daily-2026-07-09");
        assert!(
            v["age_seconds"].as_i64().unwrap() >= 0,
            "server-computed age spliced in"
        );

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
        assert_eq!(
            header_of(&resp, "X-OVP-Built-At").as_deref(),
            Some("2026-07-09T00:00:00Z")
        );
        let v = body_json(resp);
        assert_eq!(v["claim_id"], "a");
        let cit = &v["citations"][0];
        assert_eq!(cit["case_id"], "good");
        // The fresh source's metadata resolved from the SAME-freshness index —
        // NOT a dangling citation (case `good` → sha aaaa1111 via the pack).
        assert_eq!(
            cit["source_sha256"], "aaaa1111",
            "source sha resolved from the index"
        );
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
        assert!(
            hv.contains("no-cache"),
            "index.html must not be cached, got {hv:?}"
        );

        let js = dispatch(&st, Method::Get, "/assets/index-abc123.js", "");
        let jv = header_value(&js, "Cache-Control");
        assert!(
            jv.contains("immutable"),
            "hashed asset should be immutable, got {jv:?}"
        );
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
        assert_eq!(
            dispatch(&st, Method::Get, "/library", "").status_code(),
            200
        );
        // …exact API routes keep working even with a query string…
        let resp = dispatch(&st, Method::Get, "/api/refresh?x=1", "");
        assert_eq!(resp.status_code(), 200);
        // …and non-GET stays 405.
        assert_eq!(
            dispatch(&st, Method::Post, "/api/model", "").status_code(),
            405
        );

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
        for body in [
            "not json",
            "{}",
            r#"{"question":"   "}"#,
            r#"{"question":7}"#,
        ] {
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
        assert_eq!(ask(&st, &huge).status_code(), 400);

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
        assert!(
            vault
                .join(".ovp/chats")
                .join(format!("{chat}.md"))
                .is_file()
        );

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

        // Multi-turn: continue the same chat → still one history entry.
        st.ask_client = Some(scripted_factory("follow-up answer", Duration::ZERO));
        let cont = serde_json::json!({
            "question": "and the follow-up?",
            "chat": chat,
            "history": [{
                "question": "filesystem memory card unit text",
                "answer": answer,
            }],
        })
        .to_string();
        let resp2 = ask(&st, &cont);
        assert_eq!(resp2.status_code(), 200);
        let v2 = body_json(resp2);
        assert_eq!(v2["chat"], chat.as_str());
        assert_eq!(v2["answer"], "follow-up answer");
        let list2 = body_json(dispatch(&st, Method::Get, "/api/chats", ""));
        assert_eq!(
            list2.as_array().unwrap().len(),
            1,
            "continued turns stay one history row"
        );
        let mut md2 = String::new();
        dispatch(&st, Method::Get, &format!("/api/chats/{chat}"), "")
            .into_reader()
            .read_to_string(&mut md2)
            .unwrap();
        assert!(md2.contains("and the follow-up?"));
        assert!(md2.contains("follow-up answer"));

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
        let port = server.server_addr().to_ip().expect("ip listener").port();
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
            recent: vec![],
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
        assert_eq!(
            lr.status, "completed",
            "must reflect the finalized sidecar, not baked 'running'"
        );
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
            recent: vec![],
            error: None,
        });
        ovp_index::write_index(&vault, &baked).unwrap();

        let live = st.model_with_live_last_run().unwrap();
        assert!(
            live.ops.last_run.is_none(),
            "no sidecar → live overlay is None, not the stale baked snapshot"
        );

        let _ = std::fs::remove_dir_all(&root);
    }

    /// The T2 curation loop end-to-end at the dispatch level: GET /api/tags
    /// lists the vocabulary+proposals; POST /api/tags/decision records into
    /// the MACHINE-owned decisions.toml (operator aliases.toml untouched) and
    /// the rebuilt index reflects the merge; POST /api/source/:sha/tags is
    /// the sanctioned frontmatter write and retires inferred tags.
    #[test]
    fn tag_curation_endpoints_round_trip() {
        let root = temp_root("tag-curation");
        let vault = root.join("vault");
        std::fs::create_dir_all(vault.join("50-Inbox/01-Raw/2026-07")).unwrap();
        let note = vault.join("50-Inbox/01-Raw/2026-07/n.md");
        std::fs::write(
            &note,
            "---\ntitle: N\nsource: https://e.x/n\ntags:\n  - ai-agents\n---\nbody\n",
        )
        .unwrap();
        // A proposals file with one pending merge.
        std::fs::create_dir_all(vault.join(".ovp/tags")).unwrap();
        std::fs::write(
            vault.join(".ovp/tags/proposals.json"),
            r#"{"schema":"ovp.tags-proposals/v1","proposals":[{"alias":"ai-agents","alias_count":1,"canonical":"agent","canonical_count":9,"cosine":0.93}]}"#,
        )
        .unwrap();
        let model = ovp_index::build_index(&vault, "2026-07-16", None).unwrap();
        ovp_index::write_index(&vault, &model).unwrap();
        let st = state(vault.clone(), None);

        // GET: the pending proposal + the tag counts are visible.
        let v = body_json(dispatch(&st, Method::Get, "/api/tags", ""));
        assert_eq!(v["proposals"][0]["alias"], "ai-agents");
        assert_eq!(v["tags"][0]["tag"], "ai-agents");

        // POST accept: decisions.toml written, index rebuilt with the merge.
        let resp = dispatch(
            &st,
            Method::Post,
            "/api/tags/decision",
            r#"{"action":"accept","alias":"ai-agents","canonical":"agent"}"#,
        );
        assert_eq!(resp.status_code(), 200);
        assert!(vault.join(".ovp/tags/decisions.toml").exists());
        assert!(
            !vault.join(".ovp/tags/aliases.toml").exists(),
            "operator file untouched"
        );
        let v = body_json(dispatch(&st, Method::Get, "/api/tags", ""));
        assert_eq!(v["tags"][0]["tag"], "agent", "merge applied on rebuild");
        assert!(
            v["proposals"].as_array().unwrap().is_empty(),
            "decided card retired"
        );

        // POST reject: the card retires IMMEDIATELY (proposals.json is only
        // regenerated by tags-suggest, so /api/tags must filter decisions).
        std::fs::write(
            vault.join(".ovp/tags/proposals.json"),
            r#"{"schema":"ovp.tags-proposals/v1","proposals":[{"alias":"crypto","alias_count":4,"canonical":"benchmark","canonical_count":5,"cosine":0.79}]}"#,
        )
        .unwrap();
        let resp = dispatch(
            &st,
            Method::Post,
            "/api/tags/decision",
            r#"{"action":"reject","alias":"crypto","canonical":"benchmark"}"#,
        );
        assert_eq!(resp.status_code(), 200);
        let v = body_json(dispatch(&st, Method::Get, "/api/tags", ""));
        assert!(
            v["proposals"].as_array().unwrap().is_empty(),
            "rejected card retired"
        );

        // POST source tags: the one sanctioned frontmatter write. Editing a
        // QUEUED note changes its content hash — the response must carry the
        // sha the source now lives under.
        let old_sha = model.sources[0].sha256.clone();
        let resp = dispatch(
            &st,
            Method::Post,
            &format!("/api/source/{old_sha}/tags"),
            r#"{"tags":["Memory Systems"]}"#,
        );
        assert_eq!(resp.status_code(), 200);
        let v = body_json(resp);
        let new_sha = v["sha"].as_str().unwrap();
        assert_ne!(new_sha, old_sha, "queued edit re-keys the row");
        let text = std::fs::read_to_string(&note).unwrap();
        assert!(text.contains("- \"memory-systems\""), "{text}");
        let rebuilt = ovp_index::read_index(&vault).unwrap();
        assert!(rebuilt.sources.iter().any(|s| s.sha256 == new_sha));

        // Bad requests fail loud, not silently.
        assert_eq!(
            dispatch(&st, Method::Post, "/api/tags/decision", "{}").status_code(),
            400
        );
        assert_eq!(
            dispatch(
                &st,
                Method::Post,
                "/api/source/nope/tags",
                r#"{"tags":["x"]}"#
            )
            .status_code(),
            404
        );

        let _ = std::fs::remove_dir_all(&root);
    }
}
