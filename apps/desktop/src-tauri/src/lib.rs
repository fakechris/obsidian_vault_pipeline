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
    if let Ok(v) = std::env::var("OVP2_VAULT")
        && !v.trim().is_empty()
    {
        return AppConfig { vault: Some(v) };
    }
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
    let port = free_port()?;
    let config = ovp_server::ServeConfig {
        vault_root: vault,
        host: "127.0.0.1".to_string(),
        port,
        viz_dir: Some(viz_dir),
        ask_client: None,
        ask_timeout: None,
        max_concurrent_asks: None,
    };
    std::thread::spawn(move || {
        if let Err(e) = ovp_server::run_server(config) {
            eprintln!("ovp2-desktop: portal server exited: {e}");
        }
    });
    wait_until_up(port)?;
    Ok(format!("http://127.0.0.1:{port}/"))
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
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        let side = dir.join("ovp2");
        if side.exists() {
            return Some(side);
        }
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
    std::thread::spawn(move || loop {
        std::thread::sleep(SCHEDULER_INTERVAL);
        let status = Command::new(&bin)
            .arg("schedule")
            .arg("tick")
            .arg("--vault-root")
            .arg(&vault)
            .status();
        if let Err(e) = status {
            eprintln!("ovp2-desktop: scheduler tick failed to spawn: {e}");
        }
    });
}

fn valid_vault(vault: &str) -> bool {
    !vault.trim().is_empty() && Path::new(vault).is_dir()
}

// ---------------------------------------------------------------------------
// Tauri commands (the small surface the splash calls).
// ---------------------------------------------------------------------------

#[tauri::command]
fn boot(app: AppHandle, state: State<AppState>) -> BootState {
    let cfg = load_config(&app);
    match cfg.vault {
        Some(vault) if valid_vault(&vault) => {
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
    }
}

#[tauri::command]
fn set_vault_and_start(app: AppHandle, state: State<AppState>, vault: String) -> Result<String, String> {
    if !valid_vault(&vault) {
        return Err("that folder is not a readable directory".into());
    }
    save_config(&app, &AppConfig { vault: Some(vault.clone()) })?;
    let v = PathBuf::from(&vault);
    let url = ensure_server(&app, &state, &v)?;
    start_scheduler(&state, &v);
    Ok(url)
}

/// `open <path>` in the OS file manager (used by the portal's "reveal in Finder").
#[tauri::command]
fn open_path(app: AppHandle, path: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .open_path(path, None::<&str>)
        .map_err(|e| e.to_string())
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
        };
        let s = serde_json::to_string(&cfg).unwrap();
        let back: AppConfig = serde_json::from_str(&s).unwrap();
        assert_eq!(back.vault.as_deref(), Some("/Users/op/ovp-vault"));
        // A missing field tolerates older/empty config files.
        let empty: AppConfig = serde_json::from_str("{}").unwrap();
        assert!(empty.vault.is_none());
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
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![boot, set_vault_and_start, open_path])
        .run(tauri::generate_context!())
        .expect("error while running the OVP2 desktop app");
}
