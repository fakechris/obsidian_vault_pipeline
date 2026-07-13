//! Thin CLI/OS adapter over the [`ovp_scheduler`] engine. The reusable policy
//! (cadence math, registry/state, due calculation, dispatch decisions) lives in
//! that crate so a future desktop app shares one engine; this module supplies
//! only the process runner, the local clock, the dispatch lock, terminal output,
//! and the `CliError` mapping (AGENTS.md keeps `ovp-cli` a thin adapter).

use std::path::{Path, PathBuf};

use chrono::NaiveDateTime;
use ovp_scheduler::{plan_tick, run_now_with, JobConfig, JobRunner};
// Re-export the engine items the sibling `schedule` module (installer) and the
// CLI dispatch reference, so call sites keep using `commands::scheduler::…`.
pub use ovp_scheduler::{
    default_registry, is_due, job_shell_command, registry_path, resolve_vault, Cadence, Registry,
    State, VAULT_PLACEHOLDER,
};

use crate::CliError;

// -- persistence, error-mapped into CliError --------------------------------

pub fn load_registry(vault_root: &Path) -> Result<Option<Registry>, CliError> {
    ovp_scheduler::load_registry(vault_root).map_err(CliError::Io)
}
pub fn save_registry(vault_root: &Path, reg: &Registry) -> Result<(), CliError> {
    ovp_scheduler::save_registry(vault_root, reg).map_err(CliError::Io)
}
pub fn load_state(vault_root: &Path) -> Result<State, CliError> {
    ovp_scheduler::load_state(vault_root).map_err(CliError::Io)
}
pub fn save_state(vault_root: &Path, state: &State) -> Result<(), CliError> {
    ovp_scheduler::save_state(vault_root, state).map_err(CliError::Io)
}

// -- the process runner (the one impl of the engine's JobRunner trait) ------

/// Runs a job as `/bin/sh -c <job_shell_command>`, output inherited to the
/// scheduler's stdout/stderr (which the OS unit redirects to the log).
pub struct ShellRunner {
    pub ovp2_path: PathBuf,
    pub env_file: Option<PathBuf>,
    pub vault_root: PathBuf,
}

impl JobRunner for ShellRunner {
    fn run(&self, job: &JobConfig) -> bool {
        let cmd =
            job_shell_command(&self.ovp2_path, self.env_file.as_deref(), &self.vault_root, job);
        match std::process::Command::new("/bin/sh")
            .arg("-c")
            .arg(&cmd)
            .status()
        {
            Ok(status) => status.success(),
            Err(e) => {
                eprintln!("scheduler: job '{}' failed to spawn: {e}", job.id);
                false
            }
        }
    }
}

pub fn local_now() -> NaiveDateTime {
    chrono::Local::now().naive_local()
}

/// Serialize scheduler dispatch (tick / run-now / registry edits) against each
/// other so two invocations can't spawn the same job concurrently or lose a
/// registry write. Distinct from the pipeline's `.ovp/run.lock`, so the `daily`
/// child a tick spawns can still take that lock without deadlocking.
pub fn acquire_dispatch_lock(vault_root: &Path) -> Result<ovp_intake::RunLock, CliError> {
    ovp_intake::RunLock::acquire_named(vault_root, "scheduler.lock").map_err(CliError::Io)
}

// -- env resolution + runner construction -----------------------------------

/// The env file the scheduled jobs source. Prefer the registry's configured
/// path (honors `schedule install --env-file <custom>`); fall back to the
/// default `{vault}/.ovp/daily.env` for a pre-env-field registry. `{vault}` is
/// resolved and the file is only sourced when it exists.
fn resolved_env_file(vault_root: &Path, reg: &Registry) -> Option<PathBuf> {
    let raw = reg
        .env_file
        .clone()
        .unwrap_or_else(|| format!("{VAULT_PLACEHOLDER}/.ovp/daily.env"));
    let path = PathBuf::from(resolve_vault(&raw, vault_root));
    path.exists().then_some(path)
}

fn shell_runner(vault_root: &Path, reg: &Registry) -> Result<ShellRunner, CliError> {
    let ovp2_path = std::env::current_exe()
        .map_err(|e| CliError::Io(format!("cannot resolve the ovp2 binary path: {e}")))?;
    Ok(ShellRunner {
        ovp2_path,
        env_file: resolved_env_file(vault_root, reg),
        vault_root: vault_root.to_path_buf(),
    })
}

fn missing_registry_err(vault_root: &Path) -> CliError {
    CliError::Io(format!(
        "no registry at {} — run `ovp2 schedule install --vault-root {}`",
        registry_path(vault_root).display(),
        vault_root.display()
    ))
}

/// Give every registry job a state entry it lacks, so a job with no recorded
/// run first fires at its NEXT occurrence, not immediately on the first tick (an
/// unseeded job has no last-run, so `is_due` treats every past occurrence as
/// missed and fires it — including the expensive weekly crystallize). This
/// covers a fresh install (no state file) AND a re-install that restored a
/// removed built-in into an existing state file (codex P2). Existing entries are
/// never clobbered. Loading also validates the existing state, so a reinstall
/// over a malformed state file fails loud rather than installing a timer whose
/// every tick would then error (codex P2).
pub fn seed_missing_state(vault_root: &Path) -> Result<(), CliError> {
    let Some(reg) = load_registry(vault_root)? else {
        return Ok(());
    };
    let mut state = load_state(vault_root)?; // validates an existing file
    let now = local_now();
    let mut changed = false;
    for job in &reg.jobs {
        if !state.runs.contains_key(&job.id) {
            state.record(&job.id, now, "seeded");
            changed = true;
        }
    }
    if changed {
        save_state(vault_root, &state)?;
    }
    Ok(())
}

/// Lenient registry load for interactive read-only commands (`list`): prints a
/// hint and returns `None` instead of erroring when nothing is installed.
fn require_registry(vault_root: &Path) -> Result<Option<Registry>, CliError> {
    match load_registry(vault_root)? {
        Some(reg) => Ok(Some(reg)),
        None => {
            println!(
                "scheduler: no registry at {} — run `ovp2 schedule install --vault-root {}`",
                registry_path(vault_root).display(),
                vault_root.display()
            );
            Ok(None)
        }
    }
}

// -- rendering ---------------------------------------------------------------

/// Render the per-job registry + state (last run / next due, or "due now" for an
/// overdue job). Shared by `schedule list` and `schedule status`. `indent`
/// prefixes every line.
pub fn print_jobs(reg: &Registry, state: &State, now: NaiveDateTime, indent: &str) {
    println!("{indent}jobs ({}):", reg.jobs.len());
    for job in &reg.jobs {
        let flag = if job.enabled { "on " } else { "OFF" };
        // Normalized cadence when it parses (so "weekly sunday 9:00" reads back
        // as "weekly Sun 09:00"); raw string otherwise so a typo stays visible.
        let cadence_display = job
            .parsed_cadence()
            .map(Cadence::to_display)
            .unwrap_or_else(|_| format!("{} (INVALID)", job.cadence));
        println!("{indent}  [{flag}] {:<12} {}", job.id, cadence_display);
        if !job.description.is_empty() {
            println!("{indent}      {}", job.description);
        }
        match state.runs.get(&job.id) {
            Some(run) => println!("{indent}      last: {} ({})", run.last_run, run.last_status),
            None => println!("{indent}      last: never"),
        }
        if let (true, Ok(cadence)) = (job.enabled, job.parsed_cadence()) {
            // An overdue job (asleep/disabled past its slot) runs on the NEXT
            // tick, so say "due now" rather than a misleading future time.
            if is_due(cadence, state.last_run_of(&job.id), now) {
                println!("{indent}      next: due now");
            } else {
                println!(
                    "{indent}      next: {}",
                    cadence.next_occurrence(now).format("%Y-%m-%dT%H:%M")
                );
            }
        }
    }
}

// -- CLI entry points --------------------------------------------------------

/// The OS unit calls this every ~10 min: run every enabled+due job. Unlike the
/// interactive commands, a MISSING registry is an ERROR here: otherwise the OS
/// unit would exit 0 forever while nothing runs.
pub fn run_tick(vault_root: &Path) -> Result<(), CliError> {
    // Lock BEFORE the registry/state snapshot so a concurrent enable/disable
    // can't slip an edit between the read and the launch. Held across the whole
    // dispatch so a concurrent tick/run-now can't double-spawn a job.
    let _lock = acquire_dispatch_lock(vault_root)?;
    let Some(reg) = load_registry(vault_root)? else {
        return Err(CliError::Io(format!(
            "scheduler tick: no registry at {} — run `ovp2 schedule install`",
            registry_path(vault_root).display()
        )));
    };
    let mut state = load_state(vault_root)?;
    let runner = shell_runner(vault_root, &reg)?;
    let now = local_now();
    let plan = plan_tick(&reg, &state, now);
    let stamp = now.format("%Y-%m-%dT%H:%M:%S");
    if plan.due.is_empty() {
        println!("scheduler tick {stamp}: nothing due ({} job(s))", reg.jobs.len());
        return Ok(());
    }
    let mut failed = Vec::new();
    for id in &plan.due {
        let job = reg.get(id).expect("plan ids come from the registry");
        let ok = runner.run(job);
        state.record(id, now, if ok { "ok" } else { "error" });
        // Persist after EACH job so an interrupted tick never reruns a completed
        // (possibly expensive/non-idempotent) job on the next tick.
        save_state(vault_root, &state)?;
        println!("scheduler tick {stamp}: ran '{id}' -> {}", if ok { "ok" } else { "ERROR" });
        if !ok {
            failed.push(id.clone());
        }
    }
    // Exit non-zero when a child failed, so launchd/systemd (and `status`) can
    // see the failure instead of a green tick that hid a broken run.
    if !failed.is_empty() {
        return Err(CliError::Io(format!(
            "scheduler tick: job(s) failed: {}",
            failed.join(", ")
        )));
    }
    Ok(())
}

/// `schedule list` — the registry as an operator sees it.
pub fn run_list(vault_root: &Path) -> Result<(), CliError> {
    let Some(reg) = require_registry(vault_root)? else {
        return Ok(());
    };
    let state = load_state(vault_root)?;
    print_jobs(&reg, &state, local_now(), "");
    Ok(())
}

/// `schedule run-now <id>` — force one job immediately.
pub fn run_run_now(vault_root: &Path, id: &str) -> Result<(), CliError> {
    // A mutating command must FAIL on a missing registry, not exit 0, so a
    // script/desktop client can't record false success.
    let _lock = acquire_dispatch_lock(vault_root)?;
    let reg = load_registry(vault_root)?.ok_or_else(|| missing_registry_err(vault_root))?;
    let state = load_state(vault_root)?;
    let runner = shell_runner(vault_root, &reg)?;
    let (new_state, ok) =
        run_now_with(&reg, &state, id, local_now(), &runner).map_err(CliError::Io)?;
    save_state(vault_root, &new_state)?;
    println!("schedule run-now '{id}' -> {}", if ok { "ok" } else { "ERROR" });
    if !ok {
        return Err(CliError::Io(format!("job '{id}' exited non-zero")));
    }
    Ok(())
}

/// `schedule enable|disable <id>` — flip a job's enabled flag in the registry.
pub fn run_set_enabled(vault_root: &Path, id: &str, enabled: bool) -> Result<(), CliError> {
    // Hold the dispatch lock across the read-modify-write so a concurrent tick
    // can't act on a stale snapshot and two enable/disable calls can't lose an
    // update. Missing registry is an error for this mutator.
    let _lock = acquire_dispatch_lock(vault_root)?;
    let mut reg = load_registry(vault_root)?.ok_or_else(|| missing_registry_err(vault_root))?;
    let job = reg
        .get_mut(id)
        .ok_or_else(|| CliError::Io(format!("no job '{id}' in the registry")))?;
    if job.enabled == enabled {
        println!("schedule: '{id}' already {}", if enabled { "enabled" } else { "disabled" });
        return Ok(());
    }
    job.enabled = enabled;
    save_registry(vault_root, &reg)?;
    println!("schedule: '{id}' {}", if enabled { "enabled" } else { "disabled" });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn two_job_registry() -> Registry {
        default_registry("live", (9, 0), false, None)
    }

    #[test]
    fn resolved_env_file_honors_registry_and_gates_on_existence() {
        let dir = tempfile::tempdir().unwrap();
        let vault = dir.path();
        let mut reg = two_job_registry();
        reg.env_file = Some(format!("{VAULT_PLACEHOLDER}/.ovp/creds.env"));
        // Not created yet -> not sourced.
        assert_eq!(resolved_env_file(vault, &reg), None);
        // Create it -> resolved to the absolute vault path.
        let creds = vault.join(".ovp/creds.env");
        std::fs::create_dir_all(creds.parent().unwrap()).unwrap();
        std::fs::write(&creds, "K=v\n").unwrap();
        assert_eq!(resolved_env_file(vault, &reg), Some(creds));
        // A pre-env-field registry falls back to the default daily.env.
        reg.env_file = None;
        let default_env = vault.join(".ovp/daily.env");
        std::fs::write(&default_env, "K=v\n").unwrap();
        assert_eq!(resolved_env_file(vault, &reg), Some(default_env));
    }

    #[test]
    fn seed_state_stamps_all_jobs_and_never_clobbers() {
        let dir = tempfile::tempdir().unwrap();
        let vault = dir.path();
        save_registry(vault, &two_job_registry()).unwrap();
        seed_missing_state(vault).unwrap();
        let state = load_state(vault).unwrap();
        assert!(state.runs.contains_key("daily"));
        assert!(state.runs.contains_key("crystallize"));
        let before = state.runs.get("daily").unwrap().last_run.clone();
        // Second call is a no-op (never clobbers recorded runs).
        seed_missing_state(vault).unwrap();
        assert_eq!(
            load_state(vault).unwrap().runs.get("daily").unwrap().last_run,
            before
        );
    }

    #[test]
    fn mutating_commands_error_on_missing_registry() {
        let dir = tempfile::tempdir().unwrap();
        let vault = dir.path();
        // No registry -> enable/disable/run-now must FAIL, not exit 0.
        assert!(run_set_enabled(vault, "daily", false).is_err());
        assert!(run_run_now(vault, "daily").is_err());
        // list stays lenient (prints a hint, returns Ok).
        assert!(run_list(vault).is_ok());
    }
}
