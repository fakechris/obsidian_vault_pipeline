//! `scheduler` — the job registry engine behind `ovp2 schedule`.
//!
//! Decouples *what jobs exist and when* (the registry) from *how the OS wakes
//! us* (a single OS entry running `ovp2 scheduler tick` every ~10 min).
//!
//! - `.ovp/schedule.json`   — the registry: the SINGLE source of truth for the
//!   jobs and their cadences. Portable; a desktop app reads/writes the same
//!   file. Hand-editable (cadences are human strings like `"weekly Sun 10:00"`).
//! - `.ovp/schedule-state.json` — per-job last-run bookkeeping (never
//!   hand-edited; rewritten atomically after each tick).
//!
//! `tick` reads both, runs every job that is enabled AND due, and records the
//! outcome. Each job runs as a subprocess (`/bin/sh -c 'set -a; . <env>; set
//! +a; exec <ovp2> <argv…>'`) so credentials stay out of the registry and the
//! child's own RunLock prevents two runs overlapping. `run-now` forces one job
//! regardless of cadence.

use std::path::{Path, PathBuf};

use chrono::{Datelike, Duration, NaiveDateTime, Weekday};
use serde::{Deserialize, Serialize};

use super::schedule::sh_quote;
use crate::CliError;

/// Registry file, relative to the vault root.
pub const REGISTRY_REL: &str = ".ovp/schedule.json";
/// Per-job state file, relative to the vault root.
pub const STATE_REL: &str = ".ovp/schedule-state.json";

// ---------------------------------------------------------------------------
// Cadence — when a job runs, in the operator's LOCAL wall-clock time.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cadence {
    Daily { hour: u8, minute: u8 },
    Weekly { weekday: Weekday, hour: u8, minute: u8 },
}

fn parse_hm(s: &str) -> Result<(u8, u8), String> {
    let bad = || format!("invalid time '{s}': expected HH:MM (24h), e.g. 09:00");
    let (h, m) = s.split_once(':').ok_or_else(bad)?;
    if h.is_empty() || h.len() > 2 || m.len() != 2 {
        return Err(bad());
    }
    let hour: u8 = h.parse().map_err(|_| bad())?;
    let minute: u8 = m.parse().map_err(|_| bad())?;
    if hour > 23 || minute > 59 {
        return Err(bad());
    }
    Ok((hour, minute))
}

fn parse_weekday(s: &str) -> Result<Weekday, String> {
    match s.to_ascii_lowercase().as_str() {
        "sun" | "sunday" => Ok(Weekday::Sun),
        "mon" | "monday" => Ok(Weekday::Mon),
        "tue" | "tues" | "tuesday" => Ok(Weekday::Tue),
        "wed" | "weds" | "wednesday" => Ok(Weekday::Wed),
        "thu" | "thur" | "thurs" | "thursday" => Ok(Weekday::Thu),
        "fri" | "friday" => Ok(Weekday::Fri),
        "sat" | "saturday" => Ok(Weekday::Sat),
        _ => Err(format!(
            "invalid weekday '{s}': expected Sun|Mon|Tue|Wed|Thu|Fri|Sat"
        )),
    }
}

fn weekday_abbr(wd: Weekday) -> &'static str {
    match wd {
        Weekday::Sun => "Sun",
        Weekday::Mon => "Mon",
        Weekday::Tue => "Tue",
        Weekday::Wed => "Wed",
        Weekday::Thu => "Thu",
        Weekday::Fri => "Fri",
        Weekday::Sat => "Sat",
    }
}

impl Cadence {
    /// Parse `"daily HH:MM"` or `"weekly <DOW> HH:MM"` (case-insensitive DOW).
    pub fn parse(s: &str) -> Result<Cadence, String> {
        let parts: Vec<&str> = s.split_whitespace().collect();
        match parts.as_slice() {
            ["daily", hm] => {
                let (hour, minute) = parse_hm(hm)?;
                Ok(Cadence::Daily { hour, minute })
            }
            ["weekly", dow, hm] => {
                let weekday = parse_weekday(dow)?;
                let (hour, minute) = parse_hm(hm)?;
                Ok(Cadence::Weekly {
                    weekday,
                    hour,
                    minute,
                })
            }
            _ => Err(format!(
                "invalid cadence '{s}': expected 'daily HH:MM' or 'weekly <DOW> HH:MM'"
            )),
        }
    }

    pub fn to_display(self) -> String {
        match self {
            Cadence::Daily { hour, minute } => format!("daily {hour:02}:{minute:02}"),
            Cadence::Weekly {
                weekday,
                hour,
                minute,
            } => format!("weekly {} {hour:02}:{minute:02}", weekday_abbr(weekday)),
        }
    }

    /// The most recent scheduled instant at or before `now` (local wall-clock).
    /// Daily looks back at most 24h; weekly at most 7 days.
    pub fn most_recent_occurrence(self, now: NaiveDateTime) -> NaiveDateTime {
        match self {
            Cadence::Daily { hour, minute } => {
                let today_at = now
                    .date()
                    .and_hms_opt(hour as u32, minute as u32, 0)
                    .expect("cadence time validated on parse");
                if today_at <= now {
                    today_at
                } else {
                    today_at - Duration::days(1)
                }
            }
            Cadence::Weekly {
                weekday,
                hour,
                minute,
            } => {
                let now_wd = now.weekday().num_days_from_sunday() as i64;
                let job_wd = weekday.num_days_from_sunday() as i64;
                let days_back = (now_wd - job_wd).rem_euclid(7);
                let cand = (now.date() - Duration::days(days_back))
                    .and_hms_opt(hour as u32, minute as u32, 0)
                    .expect("cadence time validated on parse");
                if cand <= now {
                    cand
                } else {
                    cand - Duration::days(7)
                }
            }
        }
    }

    /// Next scheduled instant strictly after `now` (for status "next due").
    pub fn next_occurrence(self, now: NaiveDateTime) -> NaiveDateTime {
        let prev = self.most_recent_occurrence(now);
        let step = match self {
            Cadence::Daily { .. } => Duration::days(1),
            Cadence::Weekly { .. } => Duration::days(7),
        };
        prev + step
    }
}

/// A job is due if it has never run, or its last run predates the most recent
/// scheduled occurrence. Pure — `now`/`last_run` are local wall-clock.
pub fn is_due(cadence: Cadence, last_run: Option<NaiveDateTime>, now: NaiveDateTime) -> bool {
    let occ = cadence.most_recent_occurrence(now);
    match last_run {
        Some(lr) => lr < occ,
        None => now >= occ,
    }
}

// ---------------------------------------------------------------------------
// Registry — the on-disk job list (`schedule.json`).
// ---------------------------------------------------------------------------

/// One job as stored on disk. `cadence` is the human string; `argv` is the
/// `ovp2` subcommand + flags to run (the binary and env file are supplied by
/// the tick, not stored per-job).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct JobConfig {
    pub id: String,
    pub cadence: String,
    pub argv: Vec<String>,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub description: String,
    /// Append `--date "$(date +%F)"` to the shell command (local date). daily
    /// and crystallize both need today's date; the shell resolves it so it is
    /// correct in every timezone (unlike the UTC internal default).
    #[serde(default)]
    pub stamp_date: bool,
}

fn default_true() -> bool {
    true
}

impl JobConfig {
    pub fn parsed_cadence(&self) -> Result<Cadence, String> {
        Cadence::parse(&self.cadence)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Registry {
    #[serde(default = "registry_version")]
    pub version: u32,
    /// Env file each job sources before running, as configured at install
    /// (`schedule install --env-file`). May contain `{vault}`. `None` (or a
    /// pre-env-field registry) → the dispatcher falls back to the default
    /// `{vault}/.ovp/daily.env`. Honors a custom env file (codex P1).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub env_file: Option<String>,
    pub jobs: Vec<JobConfig>,
}

fn registry_version() -> u32 {
    1
}

impl Registry {
    pub fn get(&self, id: &str) -> Option<&JobConfig> {
        self.jobs.iter().find(|j| j.id == id)
    }

    pub fn get_mut(&mut self, id: &str) -> Option<&mut JobConfig> {
        self.jobs.iter_mut().find(|j| j.id == id)
    }

    /// Validate every cadence up front so a hand-edited typo fails loud at load
    /// rather than silently skipping a job at tick time.
    pub fn validate(&self) -> Result<(), String> {
        let mut seen = std::collections::BTreeSet::new();
        for job in &self.jobs {
            if !seen.insert(job.id.as_str()) {
                return Err(format!("duplicate job id '{}'", job.id));
            }
            job.parsed_cadence()
                .map_err(|e| format!("job '{}': {e}", job.id))?;
        }
        Ok(())
    }
}

/// The `{vault}` placeholder in a job's argv, substituted with the tick's
/// current vault root at dispatch. Keeps the registry portable: the absolute
/// vault path is never baked in, so a moved/copied vault dispatches correctly
/// (codex P1) and vault-local scratch is always resolved under the live vault.
pub const VAULT_PLACEHOLDER: &str = "{vault}";

/// The built-in default registry seeded on install: a daily reader run and a
/// weekly crystallize. `daily_time` is `(hour, minute)`. The vault root is NOT
/// embedded — argv carries `{vault}`, resolved at dispatch.
pub fn default_registry(
    client: &str,
    daily_time: (u8, u8),
    enrich: bool,
    max_sources: Option<usize>,
) -> Registry {
    let mut daily_argv = vec![
        "daily".to_string(),
        "--vault-root".to_string(),
        VAULT_PLACEHOLDER.to_string(),
        "--client".to_string(),
        client.to_string(),
    ];
    if let Some(n) = max_sources {
        daily_argv.push("--max-sources".to_string());
        daily_argv.push(n.to_string());
    }
    if enrich {
        daily_argv.push("--web-fetch-live".to_string());
        daily_argv.push("--github-live".to_string());
    }
    let crystallize_argv = vec![
        "crystal-synth".to_string(),
        "--vault-root".to_string(),
        VAULT_PLACEHOLDER.to_string(),
        "--client".to_string(),
        client.to_string(),
        "--refresh".to_string(),
        // Absolute, vault-local scratch — otherwise crystal-synth defaults to
        // cwd-relative `.run/crystal-synth`, which launchd (cwd=/) can't create
        // and which would write live scratch outside the vault (codex P1).
        "--work-dir".to_string(),
        format!("{VAULT_PLACEHOLDER}/.ovp/work/crystal-synth"),
    ];
    Registry {
        version: 1,
        env_file: Some(format!("{VAULT_PLACEHOLDER}/.ovp/daily.env")),
        jobs: vec![
            JobConfig {
                id: "daily".to_string(),
                cadence: format!("daily {:02}:{:02}", daily_time.0, daily_time.1),
                argv: daily_argv,
                enabled: true,
                description: "Ingest captures + build reader packs".to_string(),
                stamp_date: true,
            },
            JobConfig {
                id: "crystallize".to_string(),
                cadence: "weekly Sun 10:00".to_string(),
                argv: crystallize_argv,
                enabled: true,
                description: "Cross-source synthesis into durable crystal claims".to_string(),
                stamp_date: true,
            },
        ],
    }
}

// ---------------------------------------------------------------------------
// State — per-job last-run bookkeeping (`schedule-state.json`).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct JobRun {
    /// Local wall-clock the job last ran, `YYYY-MM-DDTHH:MM:SS`.
    pub last_run: String,
    /// `ok` or `error`.
    pub last_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct State {
    #[serde(default)]
    pub runs: std::collections::BTreeMap<String, JobRun>,
}

impl State {
    pub fn last_run_of(&self, id: &str) -> Option<NaiveDateTime> {
        self.runs
            .get(id)
            .and_then(|r| NaiveDateTime::parse_from_str(&r.last_run, "%Y-%m-%dT%H:%M:%S").ok())
    }
}

// ---------------------------------------------------------------------------
// Load / save (atomic temp+rename for state).
// ---------------------------------------------------------------------------

pub fn registry_path(vault_root: &Path) -> PathBuf {
    vault_root.join(REGISTRY_REL)
}

pub fn state_path(vault_root: &Path) -> PathBuf {
    vault_root.join(STATE_REL)
}

pub fn load_registry(vault_root: &Path) -> Result<Option<Registry>, CliError> {
    let path = registry_path(vault_root);
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| CliError::Io(format!("read {}: {e}", path.display())))?;
    let reg: Registry = serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parse {}: {e}", path.display())))?;
    reg.validate().map_err(CliError::Io)?;
    Ok(Some(reg))
}

pub fn save_registry(vault_root: &Path, reg: &Registry) -> Result<(), CliError> {
    let path = registry_path(vault_root);
    write_json_atomic(&path, reg)
}

pub fn load_state(vault_root: &Path) -> Result<State, CliError> {
    let path = state_path(vault_root);
    if !path.exists() {
        return Ok(State::default());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| CliError::Io(format!("read {}: {e}", path.display())))?;
    serde_json::from_str(&text)
        .map_err(|e| CliError::Io(format!("parse {}: {e}", path.display())))
}

pub fn save_state(vault_root: &Path, state: &State) -> Result<(), CliError> {
    write_json_atomic(&state_path(vault_root), state)
}

fn write_json_atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), CliError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| CliError::Io(format!("mkdir {}: {e}", parent.display())))?;
    }
    let body = serde_json::to_string_pretty(value)
        .map_err(|e| CliError::Io(format!("serialize {}: {e}", path.display())))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, body).map_err(|e| CliError::Io(format!("write {}: {e}", tmp.display())))?;
    std::fs::rename(&tmp, path)
        .map_err(|e| CliError::Io(format!("rename {} -> {}: {e}", tmp.display(), path.display())))
}

// ---------------------------------------------------------------------------
// Job execution (behind a trait so tests never spawn processes).
// ---------------------------------------------------------------------------

/// Resolve `{vault}` in a stored string against the live vault root.
fn resolve_vault(s: &str, vault_root: &Path) -> String {
    s.replace(VAULT_PLACEHOLDER, &vault_root.display().to_string())
}

/// The shell command a job runs: source the env file (credentials stay out of
/// the registry), then exec the pinned binary with the job's argv. `{vault}` in
/// the argv and env file is resolved against `vault_root` at dispatch, so the
/// registry stays portable and vault-local paths always point at the live vault.
pub fn job_shell_command(
    ovp2_path: &Path,
    env_file: Option<&Path>,
    vault_root: &Path,
    job: &JobConfig,
) -> String {
    let mut cmd = String::new();
    if let Some(env) = env_file {
        let env = resolve_vault(&env.display().to_string(), vault_root);
        cmd.push_str(&format!("set -a; . {}; set +a; ", sh_quote(&env)));
    }
    cmd.push_str("exec ");
    cmd.push_str(&sh_quote(&ovp2_path.display().to_string()));
    for arg in &job.argv {
        cmd.push(' ');
        cmd.push_str(&sh_quote(&resolve_vault(arg, vault_root)));
    }
    if job.stamp_date {
        // Local date via the shell — correct in every timezone.
        cmd.push_str(" --date \"$(date +%F)\"");
    }
    cmd
}

pub trait JobRunner {
    /// Run one job; return whether it exited successfully.
    fn run(&self, job: &JobConfig) -> bool;
}

/// Real runner: `/bin/sh -c <job_shell_command>`, output inherited to the
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

#[derive(Debug, Default, PartialEq, Eq)]
pub struct TickReport {
    /// (job id, ok) for jobs that ran this tick.
    pub ran: Vec<(String, bool)>,
    /// job ids skipped because not due.
    pub skipped_not_due: Vec<String>,
    /// job ids skipped because disabled.
    pub skipped_disabled: Vec<String>,
}

/// Pure-ish tick: decide due jobs from `now`, run them via `runner`, and return
/// the updated state + a report. `now` is local wall-clock. State mutation is
/// returned (not persisted) so tests need no filesystem.
pub fn tick_with(
    reg: &Registry,
    state: &State,
    now: NaiveDateTime,
    runner: &dyn JobRunner,
) -> (State, TickReport) {
    let mut new_state = state.clone();
    let mut report = TickReport::default();
    let stamp = now.format("%Y-%m-%dT%H:%M:%S").to_string();
    for job in &reg.jobs {
        if !job.enabled {
            report.skipped_disabled.push(job.id.clone());
            continue;
        }
        // A cadence that fails to parse was rejected at load; unwrap is safe,
        // but stay defensive and skip rather than panic in the dispatcher.
        let Ok(cadence) = job.parsed_cadence() else {
            report.skipped_not_due.push(job.id.clone());
            continue;
        };
        if !is_due(cadence, new_state.last_run_of(&job.id), now) {
            report.skipped_not_due.push(job.id.clone());
            continue;
        }
        let ok = runner.run(job);
        new_state.runs.insert(
            job.id.clone(),
            JobRun {
                last_run: stamp.clone(),
                last_status: if ok { "ok".into() } else { "error".into() },
            },
        );
        report.ran.push((job.id.clone(), ok));
    }
    (new_state, report)
}

/// Force one job to run regardless of cadence, updating its state entry.
pub fn run_now_with(
    reg: &Registry,
    state: &State,
    id: &str,
    now: NaiveDateTime,
    runner: &dyn JobRunner,
) -> Result<(State, bool), String> {
    let job = reg
        .get(id)
        .ok_or_else(|| format!("no job '{id}' in the registry"))?;
    let ok = runner.run(job);
    let mut new_state = state.clone();
    new_state.runs.insert(
        job.id.clone(),
        JobRun {
            last_run: now.format("%Y-%m-%dT%H:%M:%S").to_string(),
            last_status: if ok { "ok".into() } else { "error".into() },
        },
    );
    Ok((new_state, ok))
}

// ---------------------------------------------------------------------------
// CLI entry points
// ---------------------------------------------------------------------------

/// The env file the scheduled jobs source. Prefer the registry's configured
/// path (honors `schedule install --env-file <custom>`, codex P1); fall back to
/// the default `{vault}/.ovp/daily.env` for a pre-env-field registry. `{vault}`
/// is resolved and the file is only sourced when it exists.
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

/// Serialize scheduler dispatch (tick and run-now) against each other so two
/// invocations can't spawn the same job concurrently — `crystal-synth` takes no
/// `RunLock` of its own, so an overlapping tick + run-now would double the
/// model calls and race the crystal store (codex P1). Distinct from the
/// pipeline's `.ovp/run.lock`, so the `daily` child it spawns can still lock.
fn acquire_dispatch_lock(vault_root: &Path) -> Result<ovp_intake::RunLock, CliError> {
    ovp_intake::RunLock::acquire_named(vault_root, "scheduler.lock").map_err(CliError::Io)
}

pub fn local_now() -> NaiveDateTime {
    chrono::Local::now().naive_local()
}

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

/// The OS unit calls this every ~10 min: run every enabled+due job. Unlike the
/// interactive commands, a MISSING registry is an ERROR here: otherwise the OS
/// unit would exit 0 forever while nothing runs, and launchd/systemd would
/// report a healthy scheduler that is silently dead (codex P1).
pub fn run_tick(vault_root: &Path) -> Result<(), CliError> {
    let Some(reg) = load_registry(vault_root)? else {
        return Err(CliError::Io(format!(
            "scheduler tick: no registry at {} — run `ovp2 schedule install`",
            registry_path(vault_root).display()
        )));
    };
    // Held across the whole dispatch so a concurrent tick/run-now can't
    // double-spawn a job (dropped at end of scope).
    let _lock = acquire_dispatch_lock(vault_root)?;
    let state = load_state(vault_root)?;
    let runner = shell_runner(vault_root, &reg)?;
    let now = local_now();
    let (new_state, report) = tick_with(&reg, &state, now, &runner);
    let stamp = now.format("%Y-%m-%dT%H:%M:%S");
    if report.ran.is_empty() {
        println!("scheduler tick {stamp}: nothing due ({} job(s))", reg.jobs.len());
        return Ok(());
    }
    save_state(vault_root, &new_state)?;
    let mut failed = Vec::new();
    for (id, ok) in &report.ran {
        println!("scheduler tick {stamp}: ran '{id}' -> {}", if *ok { "ok" } else { "ERROR" });
        if !*ok {
            failed.push(id.clone());
        }
    }
    // Exit non-zero when a child failed, so launchd/systemd (and `status`) can
    // see the failure instead of a green tick that hid a broken run (codex P1).
    if !failed.is_empty() {
        return Err(CliError::Io(format!(
            "scheduler tick: job(s) failed: {}",
            failed.join(", ")
        )));
    }
    Ok(())
}

/// Render the per-job registry + state (last run / next due, or "due now" for
/// an overdue job). Shared by `schedule list` and `schedule status` so both
/// honor the same per-job contract. `indent` prefixes every line.
pub fn print_jobs(reg: &Registry, state: &State, now: NaiveDateTime, indent: &str) {
    println!("{indent}jobs ({}):", reg.jobs.len());
    for job in &reg.jobs {
        let flag = if job.enabled { "on " } else { "OFF" };
        // Show the normalized cadence when it parses (so a hand-typed
        // "weekly sunday 9:00" reads back as "weekly Sun 09:00"); fall back to
        // the raw string so a typo is still visible rather than hidden.
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
            // tick, so say "due now" rather than a misleading future time
            // (codex P2).
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
    let Some(reg) = require_registry(vault_root)? else {
        return Ok(());
    };
    let _lock = acquire_dispatch_lock(vault_root)?;
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
    let Some(mut reg) = require_registry(vault_root)? else {
        return Ok(());
    };
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
    use std::cell::RefCell;

    fn dt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S").unwrap()
    }

    // -- cadence parse / format round-trip ----------------------------------

    #[test]
    fn cadence_round_trips() {
        for s in ["daily 09:00", "daily 00:00", "weekly Sun 10:00", "weekly Fri 23:59"] {
            let c = Cadence::parse(s).unwrap();
            assert_eq!(c.to_display(), s, "round-trip {s}");
        }
    }

    #[test]
    fn cadence_parse_accepts_long_weekday_names_case_insensitive() {
        assert_eq!(
            Cadence::parse("weekly SUNDAY 10:00").unwrap(),
            Cadence::Weekly {
                weekday: Weekday::Sun,
                hour: 10,
                minute: 0
            }
        );
        assert!(
            Cadence::parse("weekly wednesday 6:5").is_err(),
            "minute must be 2 digits"
        );
    }

    #[test]
    fn cadence_parse_rejects_garbage() {
        for bad in [
            "", "daily", "daily 24:00", "weekly 10:00", "weekly Xxx 10:00", "monthly 09:00",
            "daily 9:0",
        ] {
            assert!(Cadence::parse(bad).is_err(), "{bad} should be rejected");
        }
    }

    // -- most_recent_occurrence / is_due ------------------------------------

    #[test]
    fn daily_due_after_the_time_passes() {
        let c = Cadence::parse("daily 09:00").unwrap();
        // 2026-07-12 is a Sunday.
        let now = dt("2026-07-12T09:30:00");
        // never run -> due once 09:00 has passed today
        assert!(is_due(c, None, now));
        // ran at 09:05 today -> not due again until tomorrow 09:00
        assert!(!is_due(c, Some(dt("2026-07-12T09:05:00")), now));
        // ran yesterday -> due
        assert!(is_due(c, Some(dt("2026-07-11T09:05:00")), now));
    }

    #[test]
    fn daily_not_due_before_the_time() {
        let c = Cadence::parse("daily 09:00").unwrap();
        let now = dt("2026-07-12T08:00:00");
        // most-recent occurrence is yesterday 09:00; ran yesterday 09:05 -> not due
        assert!(!is_due(c, Some(dt("2026-07-11T09:05:00")), now));
        // ran two days ago -> due (missed yesterday)
        assert!(is_due(c, Some(dt("2026-07-10T09:05:00")), now));
    }

    #[test]
    fn weekly_fires_on_its_weekday_and_not_between() {
        // Sunday 10:00; 2026-07-12 is Sunday.
        let c = Cadence::parse("weekly Sun 10:00").unwrap();
        let sun_11 = dt("2026-07-12T11:00:00");
        assert!(is_due(c, None, sun_11), "past 10:00 Sunday -> due");
        assert!(
            !is_due(c, Some(dt("2026-07-12T10:01:00")), sun_11),
            "already ran this Sunday"
        );
        // Wednesday after: still counts against last Sunday's occurrence.
        let wed = dt("2026-07-15T09:00:00");
        assert!(
            !is_due(c, Some(dt("2026-07-12T10:01:00")), wed),
            "ran Sunday -> not due midweek"
        );
        assert!(
            is_due(c, Some(dt("2026-07-05T10:01:00")), wed),
            "last run was the previous Sunday -> due"
        );
    }

    #[test]
    fn weekly_before_time_on_its_day_looks_back_a_week() {
        let c = Cadence::parse("weekly Sun 10:00").unwrap();
        // Sunday 08:00 — before 10:00, so most-recent occurrence is last Sunday.
        let sun_early = dt("2026-07-12T08:00:00");
        assert!(
            !is_due(c, Some(dt("2026-07-05T10:01:00")), sun_early),
            "ran last Sunday, this Sunday's slot not yet reached"
        );
        assert_eq!(
            c.most_recent_occurrence(sun_early),
            dt("2026-07-05T10:00:00")
        );
    }

    #[test]
    fn next_occurrence_steps_forward() {
        let daily = Cadence::parse("daily 09:00").unwrap();
        assert_eq!(
            daily.next_occurrence(dt("2026-07-12T09:30:00")),
            dt("2026-07-13T09:00:00")
        );
        let weekly = Cadence::parse("weekly Sun 10:00").unwrap();
        assert_eq!(
            weekly.next_occurrence(dt("2026-07-12T11:00:00")),
            dt("2026-07-19T10:00:00")
        );
    }

    // -- registry (de)serialization -----------------------------------------

    #[test]
    fn default_registry_has_daily_and_weekly_crystallize() {
        let reg = default_registry("live", (9, 0), true, Some(40));
        reg.validate().unwrap();
        // Vault path is templated, never embedded (portable — codex P1).
        let daily = reg.get("daily").unwrap();
        assert_eq!(daily.cadence, "daily 09:00");
        assert!(daily.argv.contains(&VAULT_PLACEHOLDER.to_string()));
        assert!(daily.argv.contains(&"--web-fetch-live".to_string()));
        assert!(daily.argv.contains(&"--max-sources".to_string()));
        assert!(daily.argv.contains(&"40".to_string()));
        let cry = reg.get("crystallize").unwrap();
        assert_eq!(cry.cadence, "weekly Sun 10:00");
        assert!(cry.argv.contains(&"--refresh".to_string()));
        // Vault-local scratch, templated (codex P1).
        assert!(cry
            .argv
            .contains(&format!("{VAULT_PLACEHOLDER}/.ovp/work/crystal-synth")));
        assert_eq!(
            reg.env_file.as_deref(),
            Some(format!("{VAULT_PLACEHOLDER}/.ovp/daily.env").as_str())
        );
    }

    #[test]
    fn registry_json_round_trips_and_defaults_enabled() {
        let json = r#"{
            "version": 1,
            "jobs": [
                {"id":"daily","cadence":"daily 09:00","argv":["daily"],"stamp_date":true}
            ]
        }"#;
        let reg: Registry = serde_json::from_str(json).unwrap();
        reg.validate().unwrap();
        assert!(reg.get("daily").unwrap().enabled, "enabled defaults true");
        let back = serde_json::to_string(&reg).unwrap();
        let reg2: Registry = serde_json::from_str(&back).unwrap();
        assert_eq!(reg, reg2);
    }

    #[test]
    fn registry_validate_rejects_dupes_and_bad_cadence() {
        let dupe = Registry {
            version: 1,
            env_file: None,
            jobs: vec![
                JobConfig {
                    id: "a".into(),
                    cadence: "daily 09:00".into(),
                    argv: vec![],
                    enabled: true,
                    description: String::new(),
                    stamp_date: false,
                },
                JobConfig {
                    id: "a".into(),
                    cadence: "daily 10:00".into(),
                    argv: vec![],
                    enabled: true,
                    description: String::new(),
                    stamp_date: false,
                },
            ],
        };
        assert!(dupe.validate().unwrap_err().contains("duplicate"));
        let bad = Registry {
            version: 1,
            env_file: None,
            jobs: vec![JobConfig {
                id: "a".into(),
                cadence: "hourly".into(),
                argv: vec![],
                enabled: true,
                description: String::new(),
                stamp_date: false,
            }],
        };
        assert!(bad.validate().is_err());
    }

    // -- shell command builder ----------------------------------------------

    #[test]
    fn shell_command_resolves_vault_sources_env_and_stamps_date() {
        // argv + env carry `{vault}`; both resolve to the live vault root.
        let job = JobConfig {
            id: "daily".into(),
            cadence: "daily 09:00".into(),
            argv: vec![
                "daily".into(),
                "--vault-root".into(),
                VAULT_PLACEHOLDER.into(),
            ],
            enabled: true,
            description: String::new(),
            stamp_date: true,
        };
        let cmd = job_shell_command(
            Path::new("/opt/homebrew/bin/ovp2"),
            Some(Path::new("{vault}/.ovp/daily.env")),
            Path::new("/Users/op/ovp-vault"),
            &job,
        );
        assert_eq!(
            cmd,
            "set -a; . '/Users/op/ovp-vault/.ovp/daily.env'; set +a; \
             exec '/opt/homebrew/bin/ovp2' 'daily' '--vault-root' '/Users/op/ovp-vault' \
             --date \"$(date +%F)\""
        );
    }

    #[test]
    fn shell_command_without_env_or_date() {
        let job = JobConfig {
            id: "x".into(),
            cadence: "daily 09:00".into(),
            argv: vec!["doctor".into()],
            enabled: true,
            description: String::new(),
            stamp_date: false,
        };
        let cmd = job_shell_command(Path::new("/bin/ovp2"), None, Path::new("/v"), &job);
        assert_eq!(cmd, "exec '/bin/ovp2' 'doctor'");
    }

    // -- tick dispatch ------------------------------------------------------

    #[derive(Default)]
    struct FakeRunner {
        ran: RefCell<Vec<String>>,
        fail: Vec<String>,
    }
    impl JobRunner for FakeRunner {
        fn run(&self, job: &JobConfig) -> bool {
            self.ran.borrow_mut().push(job.id.clone());
            !self.fail.contains(&job.id)
        }
    }

    fn two_job_registry() -> Registry {
        default_registry("live", (9, 0), false, None)
    }

    #[test]
    fn tick_runs_only_due_enabled_jobs_and_records_state() {
        let mut reg = two_job_registry();
        reg.get_mut("crystallize").unwrap().enabled = false;
        let state = State::default();
        // Sunday 09:30 — daily is due (never run), crystallize disabled.
        let now = dt("2026-07-12T09:30:00");
        let runner = FakeRunner::default();
        let (new_state, report) = tick_with(&reg, &state, now, &runner);
        assert_eq!(*runner.ran.borrow(), vec!["daily".to_string()]);
        assert_eq!(report.ran, vec![("daily".to_string(), true)]);
        assert_eq!(report.skipped_disabled, vec!["crystallize".to_string()]);
        assert_eq!(
            new_state.runs.get("daily").unwrap().last_status,
            "ok".to_string()
        );
        // A second tick at the same time is a no-op (already ran).
        let (_, report2) = tick_with(&reg, &new_state, now, &runner);
        assert!(report2.ran.is_empty());
        assert_eq!(report2.skipped_not_due, vec!["daily".to_string()]);
    }

    #[test]
    fn tick_records_error_status_on_failure() {
        let reg = two_job_registry();
        // Sunday 10:30 — both due (daily since 09:00, crystallize since 10:00).
        let now = dt("2026-07-12T10:30:00");
        let runner = FakeRunner {
            fail: vec!["crystallize".into()],
            ..Default::default()
        };
        let (new_state, report) = tick_with(&reg, &State::default(), now, &runner);
        assert_eq!(report.ran.len(), 2);
        assert_eq!(new_state.runs.get("daily").unwrap().last_status, "ok");
        assert_eq!(
            new_state.runs.get("crystallize").unwrap().last_status,
            "error"
        );
    }

    #[test]
    fn run_now_ignores_cadence() {
        let reg = two_job_registry();
        // 08:00 — crystallize NOT due, but run-now forces it.
        let now = dt("2026-07-12T08:00:00");
        let runner = FakeRunner::default();
        let (new_state, ok) =
            run_now_with(&reg, &State::default(), "crystallize", now, &runner).unwrap();
        assert!(ok);
        assert_eq!(*runner.ran.borrow(), vec!["crystallize".to_string()]);
        assert!(new_state.runs.contains_key("crystallize"));
        assert!(run_now_with(&reg, &State::default(), "nope", now, &runner).is_err());
    }

    #[test]
    fn resolved_env_file_honors_registry_and_gates_on_existence() {
        let dir = tempfile::tempdir().unwrap();
        let vault = dir.path();
        let mut reg = two_job_registry();
        // Custom, templated env path under the vault.
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
    fn state_parses_last_run_timestamp() {
        let mut state = State::default();
        state.runs.insert(
            "daily".into(),
            JobRun {
                last_run: "2026-07-12T09:05:00".into(),
                last_status: "ok".into(),
            },
        );
        assert_eq!(state.last_run_of("daily"), Some(dt("2026-07-12T09:05:00")));
        assert_eq!(state.last_run_of("missing"), None);
    }
}
