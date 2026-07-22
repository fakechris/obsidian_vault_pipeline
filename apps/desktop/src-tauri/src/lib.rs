//! OVP2 desktop app (Tauri). Thin shell over the existing stack:
//!
//! - runs `ovp-server` IN-PROCESS on a loopback port and points the window at
//!   it, so the whole `console-ui` portal + `/api/*` work unchanged;
//! - runs the scheduler on an in-app timer that exec's the bundled `ovp2`
//!   sidecar's `schedule tick` — REPLACING launchd/systemd entirely;
//! - persists the chosen vault, with a first-run folder picker.

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
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn save_config(app: &AppHandle, cfg: &AppConfig) -> Result<(), String> {
    let path = config_file(app).ok_or("cannot resolve the app config dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(&path, body).map_err(|e| format!("write {}: {e}", path.display()))
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

fn wait_until_up(port: u16) -> Result<(), String> {
    for _ in 0..100 {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err("portal server did not come up in time".into())
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

/// Start `ovp-server` in a background thread and return its loopback URL. Ask is
/// left unconfigured for now (POST /api/ask answers 503) — read/query/portal all
/// work; live ask is wired in a later stage.
fn start_server(vault: PathBuf, viz_dir: PathBuf) -> Result<String, String> {
    // NOTE deliberately NO `providers::apply_providers_env` here: the Tauri
    // runtime is multithreaded by the time any command handler runs, so the
    // unsafe `set_var` contract cannot hold. The desktop process itself does
    // not read provider env today (in-process ask stays unconfigured), and
    // every scheduler job execs a fresh `ovp2` child whose own startup loads
    // `.ovp/providers.toml` safely.
    // Retry a few times: free_port has a tiny TOCTOU window (another process
    // could grab the port between the probe and run_server binding it), so a
    // lost race just picks a new port rather than failing the launch.
    let mut last_err = "failed to start the portal server".to_string();
    for _ in 0..3 {
        let port = match free_port() {
            Ok(p) => p,
            Err(e) => {
                last_err = e;
                continue;
            }
        };
        let config = ovp_server::ServeConfig {
            vault_root: vault.clone(),
            host: "127.0.0.1".to_string(),
            port,
            viz_dir: Some(viz_dir.clone()),
            ask_client: None,
            // Manual-run children must exec the bundled CLI sidecar — the
            // desktop's own current_exe is the GUI shell, not ovp2.
            ovp2_bin: resolve_ovp2_bin(),
            ask_timeout: None,
            max_concurrent_asks: None,
        };
        std::thread::spawn(move || {
            if let Err(e) = ovp_server::run_server(config) {
                eprintln!("ovp2-desktop: portal server exited: {e}");
            }
        });
        match wait_until_up(port) {
            Ok(()) => return Ok(format!("http://127.0.0.1:{port}/")),
            Err(e) => last_err = e,
        }
    }
    Err(last_err)
}

fn ensure_server(app: &AppHandle, state: &AppState, vault: &Path) -> Result<String, String> {
    let mut guard = state.server_url.lock().unwrap();
    if let Some(url) = guard.as_ref() {
        return Ok(url.clone());
    }
    let url = start_server(vault.to_path_buf(), resolve_viz_dir(app))?;
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
    // to the app executable.
    if let Some(side) = std::env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(|d| d.join("ovp2")))
        .filter(|p| p.exists())
    {
        return Some(side);
    }
    // Dev fallback: the workspace release/debug build.
    for rel in ["../../../target/release/ovp2", "../../../target/debug/ovp2"] {
        let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel);
        if p.exists() {
            return Some(p);
        }
    }
    None
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
        let init = Command::new(&bin)
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
            match Command::new(&bin)
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
                    },
                );
                refresh_vault_menu(&app);
            }
            let v = PathBuf::from(&vault);
            match ensure_server(&app, &state, &v) {
                Ok(url) => {
                    start_scheduler(&state, &v);
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
        },
    )?;
    refresh_vault_menu(&app);
    let v = PathBuf::from(&vault);
    let url = ensure_server(&app, &state, &v)?;
    start_scheduler(&state, &v);
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
        other => {
            if let Some(path) = other.strip_prefix("vault-open:") {
                switch_to_vault(app, path);
            }
        }
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
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            boot,
            set_vault_and_start,
            known_vaults
        ])
        .setup(|app| {
            rebuild_vault_menu(app.handle())?;
            Ok(())
        })
        .on_menu_event(|app, event| handle_menu_event(app, event.id().as_ref()))
        .run(tauri::generate_context!())
        .expect("error while running the OVP2 desktop app");
}
