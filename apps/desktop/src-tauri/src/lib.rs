//! OVP2 desktop app (Tauri). Thin shell over the existing stack:
//!
//! - runs `ovp-server` IN-PROCESS on a loopback port and points the window at
//!   it, so the whole `console-ui` portal + `/api/*` work unchanged;
//! - runs the scheduler on an in-app timer that exec's the bundled `ovp2`
//!   sidecar's `schedule tick` — REPLACING launchd/systemd entirely;
//! - persists the chosen vault, with a first-run folder picker.

use std::collections::BTreeMap;
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State};

/// How often the in-app scheduler dispatches due jobs (the tick just decides
/// what's due, so this only bounds granularity — same 10-min feel as the OS
/// unit, but here it's our timer, not launchd/systemd).
const SCHEDULER_INTERVAL: Duration = Duration::from_secs(600);

// ---------------------------------------------------------------------------
// Config — the chosen vault, persisted in the OS app-config dir.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct AppConfig {
    vault: Option<String>,
    /// Obsidian-style recent-vault list, most-recent-first (additive: older
    /// config files deserialize to empty). The current `vault` is always
    /// also the head of this list after a successful open.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    known_vaults: Vec<String>,
    /// Canonical portal port per vault path — the vault's stable webview
    /// origin. App-scoped rather than per-vault because the property being
    /// protected is CROSS-vault: only a registry that sees every claim can
    /// keep two vaults off the same port. BTreeMap for deterministic output.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    ports: BTreeMap<String, u16>,
}

/// Recent-list cap — enough for real multi-vault use, small enough for a menu.
const MAX_KNOWN_VAULTS: usize = 10;

/// The updated recent list after opening `vault`: moved/inserted at the head,
/// deduped, capped. Pure — unit tested.
fn remember_vault(known: &[String], vault: &str) -> Vec<String> {
    let mut out = vec![vault.to_string()];
    out.extend(known.iter().filter(|v| v.as_str() != vault).cloned());
    out.truncate(MAX_KNOWN_VAULTS);
    out
}

/// Menu label for a vault path: "<folder> — <parent dir>".
fn vault_label(path: &str) -> String {
    let p = Path::new(path);
    let name = p.file_name().map(|n| n.to_string_lossy().to_string());
    let parent = p.parent().map(|d| d.display().to_string());
    match (name, parent) {
        (Some(n), Some(d)) if !d.is_empty() => format!("{n} — {d}"),
        (Some(n), _) => n,
        _ => path.to_string(),
    }
}

fn config_file(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|d| d.join("config.json"))
}

fn load_config(app: &AppHandle) -> AppConfig {
    // Env override wins (dev / CI): OVP2_VAULT points the app at a vault
    // without going through onboarding.
    match std::env::var("OVP2_VAULT") {
        Ok(v) if !v.trim().is_empty() => {
            // Env override carries no recent list of its own — keep the
            // persisted one so the menu/splash still show it.
            let mut cfg = read_config_file(app);
            cfg.vault = Some(v);
            return cfg;
        }
        _ => {}
    }
    read_config_file(app)
}

/// The persisted config.json, ignoring the OVP2_VAULT env override.
fn read_config_file(app: &AppHandle) -> AppConfig {
    let Some(path) = config_file(app) else {
        return AppConfig::default();
    };
    read_config_at(&path)
}

/// Path-addressed read — a corrupt or missing file is an empty config, never
/// a hard failure (the app must still boot into onboarding).
fn read_config_at(path: &Path) -> AppConfig {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn save_config(app: &AppHandle, cfg: &AppConfig) -> Result<(), String> {
    let path = config_file(app).ok_or("cannot resolve the app config dir")?;
    write_config_at(&path, cfg)
}

/// Path-addressed write, sibling-temp + rename so it is atomic. A crash or a
/// full disk mid-write must not leave truncated JSON: this file now carries
/// the per-vault port map, and a corrupt read means every vault silently
/// changes origin (and loses its portal settings) on the next launch.
fn write_config_at(path: &Path, cfg: &AppConfig) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    // Per-process temp name: two instances sharing one `config.json.tmp` would
    // overwrite each other's half-written content and rename the wrong bytes
    // into place. Rename itself is atomic, so the loser just loses its update.
    let tmp = path.with_extension(format!("json.tmp.{}", std::process::id()));
    std::fs::write(&tmp, body).map_err(|e| format!("write {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("rename into {}: {e}", path.display())
    })
}

// ---------------------------------------------------------------------------
// App state — the running server URL + whether the scheduler is up.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct AppState {
    server_url: Mutex<Option<String>>,
    scheduler_started: Mutex<bool>,
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum BootState {
    Ready { url: String },
    NeedVault,
    Error { message: String },
}

// ---------------------------------------------------------------------------
// Portal server (in-process) + scheduler (sidecar tick).
// ---------------------------------------------------------------------------

/// A free loopback TCP port (bind :0, read it back, drop). Tiny TOCTOU window
/// on 127.0.0.1 is acceptable for a desktop app; `wait_until_up` catches a
/// lost race and the caller retries.
fn free_port() -> Result<u16, String> {
    let l = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("bind loopback: {e}"))?;
    let port = l.local_addr().map_err(|e| e.to_string())?.port();
    Ok(port)
}

// ---------------------------------------------------------------------------
// Sticky portal port.
//
// The webview's origin is `http://127.0.0.1:<port>`, and Chromium/WKWebView
// partition localStorage + IndexedDB BY ORIGIN. A fresh `free_port()` per
// launch therefore hands the portal a brand-new, empty storage bucket every
// time the app restarts — silently wiping theme, language, panel width and
// the persisted knowledge sort prefs. Nothing errors; the settings are just
// gone. So the port has to be stable across launches, per vault.
//
// Per vault, not per app: each vault is served on its own origin, so switching
// vaults must not let vault A's portal state leak into vault B's.
// ---------------------------------------------------------------------------

/// Low end of the candidate window, and its width.
///
/// Deliberately below every common ephemeral range, because a remembered port
/// drawn from one is the port most likely to be gone by the next launch: macOS
/// `bind :0` draws from 49152..65535, and Linux's default
/// `net.ipv4.ip_local_port_range` is 32768..60999 — so the window has to stop
/// at 32768, not 40000, to clear both.
const PORT_WINDOW_LO: u16 = 20000;
const PORT_WINDOW_LEN: u16 = 12768; // 20000..32768

/// Deterministic candidate derived from the vault path (FNV-1a, so it is
/// stable across Rust versions and reinstalls — unlike `DefaultHasher`).
///
/// This is a STARTING POINT, not an identity: 12768 slots cannot give distinct
/// vaults distinct ports, and a collision would put two vaults on one origin
/// and hand vault B vault A's portal settings. `allocate_port` probes away from
/// a collision; the config's port map is what actually enforces uniqueness.
fn stable_port_candidate(vault: &str) -> u16 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for b in vault.as_bytes() {
        hash ^= u64::from(*b);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    PORT_WINDOW_LO + u16::try_from(hash % u64::from(PORT_WINDOW_LEN)).unwrap_or(0)
}

/// True iff we can bind `port` on loopback right now. Same TOCTOU caveat as
/// `free_port`; a lost race just falls through to the caller's retry.
fn port_is_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// The port `vault` should serve on, and whether it is that vault's canonical
/// origin (`true`) or a transient stand-in because the canonical one was busy
/// (`false`). Only a canonical result may be written back to the config —
/// overwriting the canonical port with a stand-in would strand the settings
/// stored under the original origin even after the conflict clears.
///
/// Allocation walks forward from the hashed candidate, skipping ports another
/// vault has already claimed and ports that will not bind, so two vaults whose
/// hashes collide still land on different origins.
fn allocate_port(ports: &BTreeMap<String, u16>, vault: &str) -> (u16, bool) {
    // A claim outside the window cannot have been written by this code, so it
    // is corrupt (hand-edited config, or a port that predates the window).
    // Treat it as no claim and repair, rather than honouring it or clamping
    // the probe to the window edge — clamping silently moves the search away
    // from the vault's own candidate.
    let window = PORT_WINDOW_LO..(PORT_WINDOW_LO + PORT_WINDOW_LEN);
    let claimed = ports.get(vault).copied().filter(|p| window.contains(p));
    if let Some(port) = claimed.filter(|p| port_is_free(*p)) {
        return (port, true);
    }
    // Either no claim yet, or the canonical port is busy right now. Probe the
    // window either way — a stand-in MUST come from the same vault-owned
    // space, never `free_port()`. An ephemeral stand-in is a port the OS will
    // later hand to some other vault's launch, which is the cross-vault
    // localStorage bleed this whole registry exists to prevent.
    let others: std::collections::BTreeSet<u16> = ports
        .iter()
        .filter(|(k, _)| k.as_str() != vault)
        .map(|(_, v)| *v)
        .collect();
    let start = claimed.unwrap_or_else(|| stable_port_candidate(vault));
    for step in 0..PORT_WINDOW_LEN {
        let offset = (start - PORT_WINDOW_LO + step) % PORT_WINDOW_LEN;
        let port = PORT_WINDOW_LO + offset;
        if !others.contains(&port) && port_is_free(port) {
            // Only the vault's FIRST allocation is canonical. When a claim
            // already exists, anything other than that exact port is a
            // stand-in and must not overwrite it.
            return (port, claimed.is_none_or(|c| c == port));
        }
    }
    // Whole window unavailable — serve rather than refuse to launch, but never
    // record it.
    (free_port().unwrap_or(0), false)
}

fn wait_until_up(port: u16) -> Result<(), String> {
    // ~10s: cold start can include providers.toml + schedule init logs on
    // the same process, and a slow disk vault should still clear this.
    for _ in 0..200 {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err("portal server did not come up in time".into())
}

/// Probe that the accept loop is serving HTTP, not just that the port is open.
fn wait_until_http(port: u16) -> Result<(), String> {
    wait_until_up(port)?;
    let req = format!(
        "GET / HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    for _ in 0..40 {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            use std::io::{Read, Write};
            let _ = stream.set_read_timeout(Some(Duration::from_millis(300)));
            let _ = stream.set_write_timeout(Some(Duration::from_millis(300)));
            if stream.write_all(req.as_bytes()).is_ok() {
                let mut buf = [0u8; 32];
                if let Ok(n) = stream.read(&mut buf) {
                    let head = std::str::from_utf8(&buf[..n]).unwrap_or("");
                    if head.starts_with("HTTP/1.") && head.contains(" 200") {
                        return Ok(());
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err("portal server accepted TCP but did not answer HTTP 200".into())
}

/// Drive the main webview to the loopback portal. Prefer this over the
/// splash's `location.replace`: leaving the asset-protocol origin from JS is
/// flaky on some WKWebView builds and leaves the splash stuck on
/// "Opening portal…".
fn navigate_main_to_portal(app: &AppHandle, url: &str) -> Result<(), String> {
    let win = app
        .get_webview_window("main")
        .ok_or_else(|| "main window is missing".to_string())?;
    let parsed: tauri::Url = url
        .parse()
        .map_err(|e| format!("invalid portal url {url:?}: {e}"))?;
    win.navigate(parsed)
        .map_err(|e| format!("navigate to portal failed: {e}"))
}

/// The `console-ui/dist` SPA build the server falls back to: bundled resource in
/// the packaged app, the repo build in dev, or an explicit override.
fn resolve_viz_dir(app: &AppHandle) -> PathBuf {
    if let Ok(p) = std::env::var("OVP2_VIZ_DIR") {
        return PathBuf::from(p);
    }
    if let Ok(res) = app.path().resource_dir() {
        let p = res.join("console-ui/dist");
        if p.exists() {
            return p;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../console-ui/dist")
}

/// Start `ovp-server` in a background thread and return its loopback URL.
///
/// Ask uses [`ovp_server::providers_ask_client_factory`]: it re-reads
/// `.ovp/providers.toml` on every question (env still wins when set) and never
/// calls `set_var` — safe under Tauri's multi-threaded runtime. Scheduler
/// children still exec the bundled `ovp2` sidecar, which loads providers.toml
/// into their own env at process start.
fn start_server(
    vault: PathBuf,
    viz_dir: PathBuf,
    cfg_path: Option<PathBuf>,
) -> Result<String, String> {
    // Retry a few times: free_port has a tiny TOCTOU window (another process
    // could grab the port between the probe and run_server binding it), so a
    // lost race just picks a new port rather than failing the launch.
    let mut last_err = "failed to start the portal server".to_string();
    // Local diagnostic (desktop launches swallow stderr when opened via Finder).
    let diag = |msg: &str| {
        let line = format!(
            "{} {}\n",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0),
            msg
        );
        let _ = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(vault.join(".ovp/desktop-portal.log"))
            .and_then(|mut f| {
                use std::io::Write;
                f.write_all(line.as_bytes())
            });
        eprintln!("ovp2-desktop: {msg}");
    };
    diag(&format!(
        "start_server vault={} viz={}",
        vault.display(),
        viz_dir.display()
    ));
    let vault_key = vault.display().to_string();
    for attempt in 0..3 {
        // First attempt takes this vault's canonical port so the webview
        // origin — and therefore the portal's localStorage — survives a
        // restart. Later attempts mean that port lost a race; take any free
        // one rather than failing the launch over a settings nicety.
        let (port, canonical) = if attempt == 0 {
            match cfg_path.as_deref() {
                Some(p) => allocate_port(&read_config_at(p).ports, &vault_key),
                None => (free_port().unwrap_or(0), false),
            }
        } else {
            (free_port().unwrap_or(0), false)
        };
        if port == 0 {
            last_err = "could not obtain a loopback port".to_string();
            diag(&format!("port allocation failed: {last_err}"));
            continue;
        }
        diag(&format!("attempt {attempt} port {port} canonical={canonical}"));
        let ask_client = ovp_server::providers_ask_client_factory(vault.clone());
        let config = ovp_server::ServeConfig {
            vault_root: vault.clone(),
            host: "127.0.0.1".to_string(),
            port,
            viz_dir: Some(viz_dir.clone()),
            ask_client,
            // Manual-run children must exec the bundled CLI sidecar — the
            // desktop's own current_exe is the GUI shell, not ovp2.
            ovp2_bin: resolve_ovp2_bin(),
            ask_timeout: None,
            max_concurrent_asks: None,
            // Agent-path parity for the desktop lands with the A3d rollout.
            // Parity with the CLI default AND its rollback hatch (A3d):
            // the agent path serves Ask unless OVP_ASK_AGENT=0.
            ask_agent: std::env::var("OVP_ASK_AGENT").map(|v| v != "0").unwrap_or(true),
        };
        // `wait_until_http` only proves SOMEBODY answers 200 on that port. If a
        // second instance won the bind race, the loser would read the winner's
        // response as its own success and then record a claim for a port it
        // never owned. Have the server report its own bind failure instead.
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        std::thread::spawn(move || {
            if let Err(e) = ovp_server::run_server(config) {
                let _ = tx.send(e.to_string());
            }
        });
        // A lost bind fails immediately; a healthy server never sends.
        if let Ok(e) = rx.recv_timeout(Duration::from_millis(300)) {
            last_err = format!("portal server could not bind {port}: {e}");
            diag(&last_err);
            continue;
        }
        match wait_until_http(port) {
            Ok(()) => {
                if let Ok(e) = rx.try_recv() {
                    last_err = format!("portal server exited on {port}: {e}");
                    diag(&last_err);
                    continue;
                }
                let url = format!("http://127.0.0.1:{port}/");
                // Claim only a canonical port that actually served. A stand-in
                // must never overwrite the claim: the settings live under the
                // canonical origin, and once a transient conflict clears we
                // want to go back to it, not strand them.
                let claim = if canonical { cfg_path.as_deref() } else { None };
                if let Some(p) = claim {
                    let mut cfg = read_config_at(p);
                    // Re-read and re-check: another vault may have claimed this
                    // port since allocation. Losing the claim costs stickiness
                    // once; taking it would alias two vaults onto one origin.
                    let stolen = cfg
                        .ports
                        .iter()
                        .any(|(k, v)| k != &vault_key && *v == port);
                    if stolen {
                        diag(&format!("port {port} claimed by another vault; not recording"));
                    } else if cfg.ports.get(&vault_key) != Some(&port) {
                        cfg.ports.insert(vault_key.clone(), port);
                        // Surfacing this matters: an unrecorded canonical port
                        // is one another vault may later be handed.
                        if let Err(e) = write_config_at(p, &cfg) {
                            diag(&format!("could not record port {port}: {e}"));
                        }
                    }
                }
                diag(&format!("portal up at {url}"));
                return Ok(url);
            }
            Err(e) => {
                last_err = e;
                diag(&format!("wait_until_http failed: {last_err}"));
            }
        }
    }
    diag(&format!("start_server giving up: {last_err}"));
    Err(last_err)
}

fn ensure_server(app: &AppHandle, state: &AppState, vault: &Path) -> Result<String, String> {
    // Do not hold the mutex across start_server — wait_until_http can take
    // seconds and must never block other boot paths on the same lock.
    {
        let guard = state.server_url.lock().unwrap();
        if let Some(url) = guard.as_ref() {
            return Ok(url.clone());
        }
    }
    let url = start_server(vault.to_path_buf(), resolve_viz_dir(app), config_file(app))?;
    let mut guard = state.server_url.lock().unwrap();
    if let Some(existing) = guard.as_ref() {
        return Ok(existing.clone());
    }
    *guard = Some(url.clone());
    Ok(url)
}

/// Resolve the bundled `ovp2` CLI the scheduler exec's: override, then the
/// sidecar next to the app binary, then a dev build. `None` = no CLI found, so
/// the scheduler no-ops (the portal still runs).
fn resolve_ovp2_bin() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("OVP2_BIN") {
        let p = PathBuf::from(p);
        if p.exists() {
            return Some(p);
        }
    }
    // Bundled sidecar: Tauri strips the target-triple suffix and places it next
    // to the app executable. Windows keeps the `.exe` extension.
    if let Some(side) = std::env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(|d| d.join(OVP2_EXE)))
        .filter(|p| p.exists())
    {
        return Some(side);
    }
    // Dev fallback: the workspace release/debug build.
    for profile in ["release", "debug"] {
        let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../target")
            .join(profile)
            .join(OVP2_EXE);
        if p.exists() {
            return Some(p);
        }
    }
    None
}

/// The sidecar's file name. Getting this wrong on Windows is silent: the
/// scheduler just reports "no ovp2 binary found" and the portal keeps working,
/// so nothing looks broken until a day of ticks has gone missing.
#[cfg(windows)]
const OVP2_EXE: &str = "ovp2.exe";
#[cfg(not(windows))]
const OVP2_EXE: &str = "ovp2";

/// Spawn configuration shared by every scheduler child. On Windows a plain
/// `Command` on a console subsystem binary pops a console window — that would
/// be a black box flashing over the operator's screen every 10 minutes.
fn scheduler_command(bin: &Path) -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new(bin);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Start the in-app scheduler: every `SCHEDULER_INTERVAL`, exec the sidecar's
/// `schedule tick`, which runs the FULL tested dispatch (registry, plan_tick,
/// vault locks, incremental state save, fail-exit). This is what replaces
/// launchd/systemd — the desktop app IS the clock.
fn start_scheduler(state: &AppState, vault: &Path) {
    let mut started = state.scheduler_started.lock().unwrap();
    if *started {
        return;
    }
    let Some(bin) = resolve_ovp2_bin() else {
        eprintln!("ovp2-desktop: no ovp2 binary found — scheduler idle (portal still runs)");
        return;
    };
    *started = true;
    let vault = vault.to_path_buf();
    std::thread::spawn(move || {
        // Seed the registry + state WITHOUT an OS unit — a fresh vault has no
        // schedule.json and `tick` errors on a missing registry, so the timer
        // would otherwise never run anything (codex P1). Idempotent.
        let init = scheduler_command(&bin)
            .arg("schedule")
            .arg("init")
            .arg("--vault-root")
            .arg(&vault)
            .arg("--client")
            .arg("live")
            .status();
        // A failed init means the registry was never seeded and every tick
        // would then silently no-op, so surface a non-zero exit, not just a
        // spawn error (codex/bot review).
        match init {
            Ok(s) if !s.success() => {
                eprintln!(
                    "ovp2-desktop: schedule init exited non-zero ({s}) — scheduler may not run"
                )
            }
            Err(e) => eprintln!("ovp2-desktop: schedule init failed to spawn: {e}"),
            _ => {}
        }
        loop {
            std::thread::sleep(SCHEDULER_INTERVAL);
            match scheduler_command(&bin)
                .arg("schedule")
                .arg("tick")
                .arg("--vault-root")
                .arg(&vault)
                .status()
            {
                Ok(s) if !s.success() => {
                    eprintln!("ovp2-desktop: scheduler tick exited non-zero ({s}) — a job failed")
                }
                Err(e) => eprintln!("ovp2-desktop: scheduler tick failed to spawn: {e}"),
                _ => {}
            }
        }
    });
}

fn valid_vault(vault: &str) -> bool {
    !vault.trim().is_empty() && Path::new(vault).is_dir()
}

// ---------------------------------------------------------------------------
// Tauri commands (the small surface the splash calls).
// ---------------------------------------------------------------------------

// async: Tauri runs async commands OFF the main thread, so the 1-2s server
// start (and any slow vault IO) can never beachball the window. The sync
// form runs on the main thread and froze the UI for its whole duration.
#[tauri::command]
async fn boot(app: AppHandle, state: State<'_, AppState>) -> Result<BootState, String> {
    let cfg = load_config(&app);
    Ok(match cfg.vault {
        Some(vault) if valid_vault(&vault) => {
            // Keep the recents honest: the vault that just opened belongs at
            // the HEAD of the list (MRU invariant), whether it is brand new
            // (hand-seeded config) or merely not first. Only `known_vaults`
            // is touched — the persisted `vault` stays as the FILE has it,
            // so a temporary OVP2_VAULT override never turns into a
            // persistent selection (codex P2s).
            if cfg.known_vaults.first() != Some(&vault) {
                let cfg_file = read_config_file(&app);
                let _ = save_config(
                    &app,
                    &AppConfig {
                        vault: cfg_file.vault.clone(),
                        known_vaults: remember_vault(&cfg_file.known_vaults, &vault),
                        // `..cfg_file` rather than a fresh struct: rebuilding
                        // field-by-field silently drops everything else in the
                        // file — `ports` here, whatever comes next later.
                        ..cfg_file
                    },
                );
                refresh_vault_menu(&app);
            }
            let v = PathBuf::from(&vault);
            match ensure_server(&app, &state, &v) {
                Ok(url) => {
                    start_scheduler(&state, &v);
                    // Navigate from Rust so the splash cannot get stuck after
                    // Ready (JS location.replace is a best-effort fallback).
                    if let Err(e) = navigate_main_to_portal(&app, &url) {
                        eprintln!("ovp2-desktop: {e}");
                    }
                    BootState::Ready { url }
                }
                Err(message) => BootState::Error { message },
            }
        }
        _ => BootState::NeedVault,
    })
}

#[tauri::command]
async fn set_vault_and_start(
    app: AppHandle,
    state: State<'_, AppState>,
    vault: String,
) -> Result<String, String> {
    if !valid_vault(&vault) {
        return Err("that folder is not a readable directory".into());
    }
    let prev = load_config(&app);
    save_config(
        &app,
        &AppConfig {
            vault: Some(vault.clone()),
            known_vaults: remember_vault(&prev.known_vaults, &vault),
            ..prev
        },
    )?;
    refresh_vault_menu(&app);
    let v = PathBuf::from(&vault);
    let url = ensure_server(&app, &state, &v)?;
    start_scheduler(&state, &v);
    if let Err(e) = navigate_main_to_portal(&app, &url) {
        eprintln!("ovp2-desktop: {e}");
        // Fall through — splash JS still has location.replace as backup.
    }
    Ok(url)
}

/// The recent-vault list for the splash quick-pick (existing dirs only) —
/// one click straight into a vault, no NSOpenPanel involved.
#[tauri::command]
async fn known_vaults(app: AppHandle) -> Result<Vec<String>, String> {
    Ok(load_config(&app)
        .known_vaults
        .into_iter()
        .filter(|v| valid_vault(v))
        .collect())
}

/// Open an external URL in the system browser. A WKWebView can't pop a browser
/// itself — `target="_blank"` and external navigations are silently dropped —
/// so the portal's external links (a source's original article URL, citations)
/// call this instead. http/https ONLY: never hand an arbitrary scheme to the
/// shell, and `.arg(&url)` passes the url as one argument (no shell parsing).
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err(format!("refusing to open non-http(s) url: {url}"));
    }
    #[cfg(target_os = "macos")]
    let opener = "open";
    #[cfg(target_os = "linux")]
    let opener = "xdg-open";
    #[cfg(target_os = "windows")]
    let opener = "explorer";
    let mut child = std::process::Command::new(opener)
        .arg(&url)
        .spawn()
        .map_err(|e| format!("open failed: {e}"))?;
    // Reap the short-lived opener: Rust doesn't wait on Child drop, so without
    // this every link click would leave a zombie until the app exits.
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

/// macOS Notification Center / Linux notify-send — used when a background
/// source-work queue item finishes while the operator is in another app.
#[tauri::command]
fn desktop_notify(title: String, body: String) -> Result<(), String> {
    // Cap payload size so we never hand a multi-KB dump to the shell.
    let title = title.chars().take(80).collect::<String>();
    let body = body.chars().take(200).collect::<String>();
    #[cfg(target_os = "macos")]
    {
        // Escape for AppleScript string literals.
        let esc = |s: &str| s.replace('\\', "\\\\").replace('"', "\\\"");
        let script = format!(
            "display notification \"{}\" with title \"{}\"",
            esc(&body),
            esc(&title)
        );
        let mut child = Command::new("osascript")
            .arg("-e")
            .arg(&script)
            .spawn()
            .map_err(|e| format!("osascript: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        let mut child = Command::new("notify-send")
            .arg(&title)
            .arg(&body)
            .spawn()
            .map_err(|e| format!("notify-send: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        let _ = (title, body);
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Vault menu — Obsidian-style switching. Every action is config + restart:
// one mechanism, and the boot path (auto-open the persisted vault, else the
// splash picker) does the rest. Restart also rebinds the scheduler and the
// in-process server to the new vault with zero lifecycle juggling.
// ---------------------------------------------------------------------------

/// Persist `vault` as current (+ remember it) and relaunch. No-op when it is
/// already the current vault or the directory is gone.
fn switch_to_vault(app: &AppHandle, vault: &str) {
    if !valid_vault(vault) {
        eprintln!("ovp2-desktop: vault {vault} is not a readable directory — not switching");
        return;
    }
    // The PERSISTED config is the truth here — under an OVP2_VAULT override
    // load_config reports the env vault, which would both mis-skip a real
    // switch and derive the saved state from a temporary value (bot review).
    let prev = read_config_file(app);
    if prev.vault.as_deref() == Some(vault) {
        return;
    }
    let cfg = AppConfig {
        vault: Some(vault.to_string()),
        known_vaults: remember_vault(&prev.known_vaults, vault),
        ..prev
    };
    if let Err(e) = save_config(app, &cfg) {
        eprintln!("ovp2-desktop: could not save vault config: {e}");
        return;
    }
    app.restart();
}

fn handle_menu_event(app: &AppHandle, id: &str) {
    match id {
        "vault-close" => {
            // Back to the splash picker (recent list keeps the old vault one
            // click away).
            let mut cfg = load_config(app);
            cfg.vault = None;
            if let Err(e) = save_config(app, &cfg) {
                eprintln!("ovp2-desktop: could not save vault config: {e}");
                return;
            }
            app.restart();
        }
        "vault-choose" => {
            use tauri_plugin_dialog::DialogExt;
            let handle = app.clone();
            app.dialog().file().pick_folder(move |dir| {
                let Some(dir) = dir else { return };
                match dir.into_path() {
                    Ok(p) => switch_to_vault(&handle, &p.to_string_lossy()),
                    Err(e) => eprintln!("ovp2-desktop: folder pick failed: {e}"),
                }
            });
        }
        "page-open-browser" => {
            let Some(win) = app.get_webview_window("main") else {
                return;
            };
            match win.url() {
                // Only the portal's http(s) pages make sense in a browser —
                // the tauri:// splash does not.
                Ok(url) if url.scheme() == "http" || url.scheme() == "https" => {
                    #[cfg(target_os = "macos")]
                    let opener = "open";
                    #[cfg(target_os = "linux")]
                    let opener = "xdg-open";
                    #[cfg(target_os = "windows")]
                    let opener = "explorer";
                    if let Err(e) = std::process::Command::new(opener)
                        .arg(url.to_string())
                        .spawn()
                    {
                        eprintln!("ovp2-desktop: open in browser failed: {e}");
                    }
                }
                Ok(url) => eprintln!("ovp2-desktop: not a browser-openable page: {url}"),
                Err(e) => eprintln!("ovp2-desktop: cannot read the page url: {e}"),
            }
        }
        "history-back" => nav_history(app, -1),
        "history-forward" => nav_history(app, 1),
        other => {
            if let Some(path) = other.strip_prefix("vault-open:") {
                switch_to_vault(app, path);
            }
        }
    }
}

/// Step the main webview's history — native Back/Forward (⌘[ / ⌘]). `history.go`
/// works on the SPA and on legacy full-document pages alike, and is a no-op at
/// the ends.
fn nav_history(app: &AppHandle, delta: i32) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    if let Err(e) = win.eval(format!("window.history.go({delta})")) {
        eprintln!("ovp2-desktop: history nav failed: {e}");
    }
}

/// Build the "Vault" submenu appended to the standard menu bar: choose,
/// recent vaults (current one checked), close. Rebuilt at launch AND after
/// any config change (boot remembering a seeded vault, a splash pick), so
/// the menu always reflects the live recent list.
fn rebuild_vault_menu(handle: &AppHandle) -> tauri::Result<()> {
    use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
    let cfg = load_config(handle);

    let menu = Menu::default(handle)?;
    let vault_menu = Submenu::new(handle, "Vault", true)?;
    vault_menu.append(&MenuItem::with_id(
        handle,
        "vault-choose",
        "Choose Vault Folder…",
        true,
        None::<&str>,
    )?)?;
    if !cfg.known_vaults.is_empty() {
        vault_menu.append(&PredefinedMenuItem::separator(handle)?)?;
        for v in &cfg.known_vaults {
            let checked = cfg.vault.as_deref() == Some(v.as_str());
            vault_menu.append(&CheckMenuItem::with_id(
                handle,
                format!("vault-open:{v}"),
                vault_label(v),
                true,
                checked,
                None::<&str>,
            )?)?;
        }
    }
    vault_menu.append(&PredefinedMenuItem::separator(handle)?)?;
    vault_menu.append(&MenuItem::with_id(
        handle,
        "vault-close",
        "Close Vault",
        cfg.vault.is_some(),
        None::<&str>,
    )?)?;
    menu.append(&vault_menu)?;
    // Page menu: browser hand-off for the current portal page (⌘⇧O) — the
    // in-app webview and a real browser tab show the same loopback URL.
    let page_menu = Submenu::new(handle, "Page", true)?;
    page_menu.append(&MenuItem::with_id(
        handle,
        "page-open-browser",
        "Open Page in Browser",
        true,
        Some("CmdOrCtrl+Shift+O"),
    )?)?;
    menu.append(&page_menu)?;

    // History: persistent native chrome so Back/Forward work everywhere — the
    // in-webview toolbar unmounts on full-document navigations (legacy admin
    // pages), and this survives them. Standard ⌘[ / ⌘] shortcuts.
    let history_menu = Submenu::new(handle, "History", true)?;
    history_menu.append(&MenuItem::with_id(
        handle,
        "history-back",
        "Back",
        true,
        Some("CmdOrCtrl+["),
    )?)?;
    history_menu.append(&MenuItem::with_id(
        handle,
        "history-forward",
        "Forward",
        true,
        Some("CmdOrCtrl+]"),
    )?)?;
    menu.append(&history_menu)?;

    handle.set_menu(menu)?;
    Ok(())
}

/// Menu rebuild from any thread: hops to the main thread (menus are
/// main-thread state on macOS).
fn refresh_vault_menu(app: &AppHandle) {
    let h = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Err(e) = rebuild_vault_menu(&h) {
            eprintln!("ovp2-desktop: vault menu rebuild failed: {e}");
        }
    });
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            boot,
            set_vault_and_start,
            known_vaults,
            open_external,
            desktop_notify
        ])
        .setup(|app| {
            rebuild_vault_menu(app.handle())?;
            Ok(())
        })
        .on_menu_event(|app, event| handle_menu_event(app, event.id().as_ref()))
        .run(tauri::generate_context!())
        .expect("error while running the OVP2 desktop app");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_vault_requires_a_real_directory() {
        assert!(!valid_vault(""));
        assert!(!valid_vault("   "));
        assert!(!valid_vault("/no/such/path/ovp2-xyz"));
        let dir = std::env::temp_dir();
        assert!(valid_vault(&dir.to_string_lossy()));
    }

    #[test]
    fn app_config_json_round_trips() {
        let cfg = AppConfig {
            vault: Some("/Users/op/ovp-vault".into()),
            known_vaults: vec!["/Users/op/ovp-vault".into(), "/Users/op/other".into()],
            ..AppConfig::default()
        };
        let s = serde_json::to_string(&cfg).unwrap();
        let back: AppConfig = serde_json::from_str(&s).unwrap();
        assert_eq!(back.vault.as_deref(), Some("/Users/op/ovp-vault"));
        assert_eq!(back.known_vaults.len(), 2);
        // Missing fields tolerate older/empty config files (known_vaults is
        // additive).
        let empty: AppConfig = serde_json::from_str("{}").unwrap();
        assert!(empty.vault.is_none());
        assert!(empty.known_vaults.is_empty());
        let old: AppConfig = serde_json::from_str(r#"{"vault":"/v"}"#).unwrap();
        assert!(old.known_vaults.is_empty());
    }

    #[test]
    fn remember_vault_moves_to_head_dedupes_and_caps() {
        let known: Vec<String> = vec!["/a".into(), "/b".into(), "/c".into()];
        assert_eq!(remember_vault(&known, "/b"), vec!["/b", "/a", "/c"]);
        assert_eq!(
            remember_vault(&known, "/new"),
            vec!["/new", "/a", "/b", "/c"]
        );
        let many: Vec<String> = (0..MAX_KNOWN_VAULTS).map(|i| format!("/v{i}")).collect();
        let out = remember_vault(&many, "/fresh");
        assert_eq!(out.len(), MAX_KNOWN_VAULTS);
        assert_eq!(out[0], "/fresh");
        assert!(
            !out.contains(&format!("/v{}", MAX_KNOWN_VAULTS - 1)),
            "oldest dropped"
        );
    }

    #[test]
    fn vault_label_shows_folder_and_parent() {
        assert_eq!(
            vault_label("/Users/op/Documents/ovp-vault"),
            "ovp-vault — /Users/op/Documents"
        );
    }

    #[test]
    fn free_port_is_bindable() {
        let p = free_port().unwrap();
        assert!(p > 0);
    }

    #[test]
    fn stable_port_candidate_is_deterministic_and_below_every_ephemeral_range() {
        let a = "/Users/op/Documents/ovp-vault";
        assert_eq!(stable_port_candidate(a), stable_port_candidate(a));
        // Must clear BOTH macOS (49152..) and Linux (32768..) ephemeral
        // ranges: a candidate the OS also hands to other processes is the one
        // least likely to still be free at the next launch.
        for v in ["/a", "/b/c", a, ""] {
            let p = stable_port_candidate(v);
            assert!((PORT_WINDOW_LO..32768).contains(&p), "{v} -> {p}");
        }
    }

    #[test]
    fn hash_collisions_do_not_share_an_origin() {
        // These two paths hash to the SAME candidate (12768 slots cannot be
        // injective over arbitrary paths). Allocation must still give them
        // different ports, or vault B reads vault A's portal localStorage.
        let (a, b) = ("/Users/op/vault-69", "/Users/op/vault-450");
        assert_eq!(
            stable_port_candidate(a),
            stable_port_candidate(b),
            "fixture is only meaningful if these collide"
        );
        let mut ports = BTreeMap::new();
        let (pa, ok_a) = allocate_port(&ports, a);
        assert!(ok_a);
        ports.insert(a.to_string(), pa);
        let (pb, ok_b) = allocate_port(&ports, b);
        assert!(ok_b);
        assert_ne!(pa, pb, "collision must be probed away from");
    }

    #[test]
    fn a_claimed_port_is_reused_and_is_stable_across_launches() {
        let mut ports = BTreeMap::new();
        // A DISTINCT path per test: these bind real ports, and a shared vault
        // path means a shared candidate, which races under cargo's parallel
        // test threads.
        let v = "/Users/op/vault-reuse";
        let (first, ok) = allocate_port(&ports, v);
        assert!(ok);
        ports.insert(v.to_string(), first);
        // Second launch: same vault, same origin. The whole point.
        assert_eq!(allocate_port(&ports, v), (first, true));
    }

    #[test]
    fn a_busy_canonical_port_yields_an_in_window_stand_in_and_recovers() {
        let v = "/Users/op/vault-standin";
        let mut ports = BTreeMap::new();
        let (canonical_port, ok) = allocate_port(&ports, v);
        assert!(ok);
        ports.insert(v.to_string(), canonical_port);
        // Occupy the vault's OWN canonical port — the claim has to be inside
        // the window for this to model a real conflict rather than a corrupt
        // entry (which takes the repair path instead).
        let held = TcpListener::bind(("127.0.0.1", canonical_port)).expect("just probed free");

        let (port, is_canonical) = allocate_port(&ports, v);
        assert_ne!(port, canonical_port);
        assert!(
            !is_canonical,
            "a stand-in must be flagged so the caller never overwrites the claim"
        );
        assert!(
            (PORT_WINDOW_LO..PORT_WINDOW_LO + PORT_WINDOW_LEN).contains(&port),
            "stand-in {port} escaped the window; the OS would hand it to another vault"
        );

        // Conflict clears -> back to the canonical origin, settings intact.
        drop(held);
        assert_eq!(allocate_port(&ports, v), (canonical_port, true));
    }

    #[test]
    fn allocation_skips_a_port_another_vault_already_claimed() {
        // `a` is unclaimed, so probing starts at ITS hashed candidate — which
        // `b` already owns. That is the only setup that actually exercises the
        // `others.contains` branch: give `a` an out-of-window claim instead and
        // the probe starts somewhere else entirely and passes for free.
        let (a, b) = ("/Users/op/vault-69", "/Users/op/vault-450");
        let contested = stable_port_candidate(a);
        assert_eq!(contested, stable_port_candidate(b), "fixture must collide");
        let mut ports = BTreeMap::new();
        ports.insert(b.to_string(), contested);
        let (port, canonical) = allocate_port(&ports, a);
        assert_ne!(port, contested, "must not allocate B's origin to A");
        assert!(canonical);
    }

    #[test]
    fn an_out_of_window_claim_is_repaired() {
        let v = "/Users/op/vault-outofwindow";
        let mut ports = BTreeMap::new();
        // Nothing this code writes lands here; honouring it would pin the
        // vault to a port the OS reassigns freely.
        ports.insert(v.to_string(), 51234u16);
        let (port, canonical) = allocate_port(&ports, v);
        assert_eq!(port, stable_port_candidate(v), "probe starts at the candidate");
        assert!(canonical, "a corrupt claim must be repaired");
    }

    #[test]
    fn a_privileged_claimed_port_is_repaired_not_reused() {
        // 80 is not something this code could have written, so the entry is
        // corrupt. Treat it as "no claim": allocate a real port and mark it
        // canonical so the bad entry gets overwritten rather than retried
        // forever.
        let v = "/Users/op/vault-privileged";
        let mut ports = BTreeMap::new();
        ports.insert(v.to_string(), 80u16);
        let (port, canonical) = allocate_port(&ports, v);
        assert_ne!(port, 80);
        assert!((PORT_WINDOW_LO..PORT_WINDOW_LO + PORT_WINDOW_LEN).contains(&port));
        assert!(canonical, "a corrupt claim must be repaired");
    }

    #[test]
    fn config_write_is_atomic_and_round_trips_the_port_map() {
        // tempfile, not `temp_dir()` + manual cleanup: the repo forbids run
        // artifacts in /tmp, and a hand-rolled cleanup at the end of the test
        // never runs when an assertion panics — leaving the directory behind
        // exactly on the failures worth investigating.
        let dir = tempfile::tempdir().unwrap();
        let dir = dir.path();
        let path = dir.join("config.json");
        let mut cfg = AppConfig::default();
        cfg.ports.insert("/Users/op/vault-a".into(), 20001);
        write_config_at(&path, &cfg).unwrap();
        assert_eq!(read_config_at(&path).ports.get("/Users/op/vault-a"), Some(&20001));
        // No temp file left behind, and a corrupt file reads as empty rather
        // than panicking the boot path.
        assert!(!path
            .with_extension(format!("json.tmp.{}", std::process::id()))
            .exists());
        std::fs::write(&path, "not json").unwrap();
        assert!(read_config_at(&path).ports.is_empty());
    }

    #[test]
    fn older_config_without_ports_still_loads() {
        let cfg: AppConfig =
            serde_json::from_str(r#"{"vault":"/v","known_vaults":["/v"]}"#).unwrap();
        assert!(cfg.ports.is_empty());
        assert_eq!(cfg.vault.as_deref(), Some("/v"));
    }
}
