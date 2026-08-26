//! The reusable job-scheduling engine behind `ovp2 schedule`.
//!
//! This crate is pure policy + persistence, with NO CLI, process, or OS
//! dependencies, so the CLI and a future desktop app share ONE engine (AGENTS.md
//! keeps `ovp-cli` a thin adapter). It owns:
//!
//! - [`Cadence`] — when a job runs, in local wall-clock time, and the
//!   [`is_due`]/occurrence math.
//! - [`Registry`] / [`JobConfig`] — the job list persisted to
//!   `.ovp/schedule.json`, the single source of truth (portable; `{vault}` in a
//!   job's argv is resolved at dispatch, never baked in).
//! - [`State`] — per-job last-run bookkeeping in `.ovp/schedule-state.json`.
//! - [`plan_tick`] — the pure decision of which jobs are due, and the
//!   [`JobRunner`] trait the adapter implements to actually run them.
//!
//! The adapter (in `ovp-cli`) supplies the process runner, the local clock, the
//! dispatch lock, and the terminal output.

use std::path::{Path, PathBuf};

use chrono::{Datelike, Duration, NaiveDateTime, Weekday};
use serde::{Deserialize, Serialize};

/// Registry file, relative to the vault root.
pub const REGISTRY_REL: &str = ".ovp/schedule.json";
/// Per-job state file, relative to the vault root.
pub const STATE_REL: &str = ".ovp/schedule-state.json";
/// The `{vault}` placeholder in a job's argv / env path, substituted with the
/// tick's current vault root at dispatch. Keeps the registry portable: the
/// absolute vault path is never baked in, so a moved/copied vault dispatches
/// correctly and vault-local scratch always resolves under the live vault.
pub const VAULT_PLACEHOLDER: &str = "{vault}";

// ---------------------------------------------------------------------------
// Cadence — when a job runs, in the operator's LOCAL wall-clock time.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cadence {
    Daily {
        hour: u8,
        minute: u8,
    },
    Weekly {
        weekday: Weekday,
        hour: u8,
        minute: u8,
    },
    /// Every `hours` hours, ANCHORED AT LOCAL MIDNIGHT (`every 4h` → 00:00,
    /// 04:00, …, 20:00). Anchoring — rather than stepping from `last_run` —
    /// keeps occurrences drift-free: a late tick or a long run shifts nothing,
    /// so `is_due` stays a pure function of the wall clock.
    ///
    /// `hours` that do not divide 24 leave a SHORT final slot before midnight
    /// (`every 5h` → 00,05,10,15,20, then 00 four hours later). That is
    /// accepted, not prevented: the alternative is drift.
    EveryHours {
        hours: u8,
    },
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

/// Parse the `<N>h` of an `every <N>h` cadence. 1..=23 only: `24h` is `daily
/// 00:00` (use that — it says what it means), and 0 would divide by zero.
fn parse_every_hours(s: &str) -> Result<u8, String> {
    let bad = || {
        format!(
            "invalid interval '{s}': expected <N>h with N in 1..=23, e.g. 4h (24h = 'daily 00:00')"
        )
    };
    let n = s.strip_suffix('h').ok_or_else(bad)?;
    if n.is_empty() || n.len() > 2 {
        return Err(bad());
    }
    let hours: u8 = n.parse().map_err(|_| bad())?;
    if !(1..=23).contains(&hours) {
        return Err(bad());
    }
    Ok(hours)
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
    /// Parse `"daily HH:MM"`, `"weekly <DOW> HH:MM"` (case-insensitive DOW), or
    /// `"every <N>h"`.
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
            ["every", spec] => {
                let hours = parse_every_hours(spec)?;
                Ok(Cadence::EveryHours { hours })
            }
            _ => Err(format!(
                "invalid cadence '{s}': expected 'daily HH:MM', 'weekly <DOW> HH:MM', or 'every <N>h'"
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
            Cadence::EveryHours { hours } => format!("every {hours}h"),
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
            Cadence::EveryHours { hours } => {
                // Anchor at LOCAL midnight and floor to the slot: the last
                // occurrence is today's midnight + floor(elapsed_hours / N) * N.
                // Sub-hour components are dropped, so 13:59 with `every 4h`
                // resolves to 12:00, not 13:00.
                let midnight = now.date().and_hms_opt(0, 0, 0).expect("midnight exists");
                let slots = (now - midnight).num_hours() / hours as i64;
                midnight + Duration::hours(slots * hours as i64)
            }
        }
    }

    /// Next scheduled instant strictly after `now` (for status "next due").
    pub fn next_occurrence(self, now: NaiveDateTime) -> NaiveDateTime {
        let prev = self.most_recent_occurrence(now);
        match self {
            Cadence::Daily { .. } => prev + Duration::days(1),
            Cadence::Weekly { .. } => prev + Duration::days(7),
            Cadence::EveryHours { hours } => {
                // Midnight RE-ANCHORS the slot grid, so an interval that does
                // not divide 24 must not step past it: `every 5h` goes
                // 20:00 → 00:00 (the short final slot), never 01:00.
                let stepped = prev + Duration::hours(hours as i64);
                let next_midnight = (now.date() + Duration::days(1))
                    .and_hms_opt(0, 0, 0)
                    .expect("midnight exists");
                stepped.min(next_midnight)
            }
        }
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

/// Resolve a vault-relative stored path against the live vault root.
///
/// Registry paths use `/` so they remain portable. Build the result through
/// `PathBuf` instead of string replacement: Windows extended-length paths
/// (`\\?\C:\...`) reject the mixed `\\?\C:\.../.ovp/...` form that a raw
/// replacement would create.
pub fn resolve_vault(s: &str, vault_root: &Path) -> String {
    let Some(rest) = s.strip_prefix(VAULT_PLACEHOLDER) else {
        return s.replace(VAULT_PLACEHOLDER, &vault_root.display().to_string());
    };
    let mut resolved = vault_root.to_path_buf();
    for component in rest
        .trim_start_matches(['/', '\\'])
        .split(['/', '\\'])
        .filter(|component| !component.is_empty())
    {
        resolved.push(component);
    }
    resolved.display().to_string()
}

// ---------------------------------------------------------------------------
// Registry — the on-disk job list (`schedule.json`).
// ---------------------------------------------------------------------------

/// One job as stored on disk. `cadence` is the human string; `argv` is the
/// `ovp2` subcommand + flags to run (the binary is supplied by the adapter).
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
    /// Env file each job sources before running, as configured at install. May
    /// contain `{vault}`. `None` (or a pre-env-field registry) → the adapter
    /// falls back to the default `{vault}/.ovp/daily.env`.
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
    ///
    /// Whole-registry verdict, kept for callers that genuinely need "is every
    /// job usable". Loading uses [`Registry::partition`] instead — see there
    /// for why an all-or-nothing answer is the wrong shape at load time.
    pub fn validate(&self) -> Result<(), String> {
        let (_, rejected) = self.clone().partition();
        match rejected.first() {
            Some(r) => Err(format!("job '{}': {}", r.id, r.reason)),
            None => Ok(()),
        }
    }

    /// Split into the jobs that can run and the ones that cannot.
    ///
    /// A single unparseable cadence used to fail the whole load, which meant
    /// `schedule list` and `tick` exited non-zero and **every** job stopped —
    /// not just the broken one. Worse, the desktop app writes a tick's stderr
    /// to `eprintln` only, so from the GUI that looked like nothing happening
    /// at all. The usual way in is a `schedule.json` edited to a cadence syntax
    /// the installed binary does not know yet, which is a routine ordering
    /// mistake, not corruption.
    ///
    /// So quarantine per job: the healthy ones keep running and the broken ones
    /// are reported by id and reason. A duplicate id keeps the FIRST occurrence
    /// — that is what `get()` already resolves to, so quarantining the later
    /// one matches the behaviour the rest of the code already has.
    pub fn partition(mut self) -> (Registry, Vec<RejectedJob>) {
        let mut seen = std::collections::BTreeSet::new();
        let mut kept = Vec::new();
        let mut rejected = Vec::new();
        for job in std::mem::take(&mut self.jobs) {
            if !seen.insert(job.id.clone()) {
                rejected.push(RejectedJob {
                    id: job.id,
                    reason: "duplicate job id (the first one is used)".to_string(),
                });
                continue;
            }
            match job.parsed_cadence() {
                Ok(_) => kept.push(job),
                Err(e) => rejected.push(RejectedJob { id: job.id, reason: e }),
            }
        }
        // `..self` rather than restating fields: a field added later must be
        // carried through automatically, not silently reset to its default.
        (Registry { jobs: kept, ..self }, rejected)
    }
}

/// A job the registry could not use, and why. Reported, never silently dropped:
/// a job missing from `schedule list` with no explanation is indistinguishable
/// from one that was never configured.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RejectedJob {
    pub id: String,
    pub reason: String,
}

/// A loaded registry plus whatever could not be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedRegistry {
    /// Only the jobs that can actually run. Dispatch reads THIS.
    pub registry: Registry,
    pub rejected: Vec<RejectedJob>,
    /// The registry exactly as it sits on disk, quarantined jobs included.
    ///
    /// Edit-and-save paths (`schedule disable`, `set-cadence`, install
    /// migration) must go through this, never through `registry`: saving the
    /// filtered view would DELETE the operator's broken job definition as a
    /// side effect of an unrelated edit. Quarantine hides a job from the
    /// dispatcher; it must never erase it from the file.
    pub as_written: Registry,
}

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
        // Pinboard capture is ON by default in the scheduled daily loop:
        // `--pinboard-since auto` resumes from the last sync's bookmark-day
        // watermark. Both failure modes a fresh/unconfigured vault hits are
        // GRACEFUL skips in `daily` (warn + continue), never a failed run:
        // no PINBOARD_TOKEN → capture skipped; no watermark yet → skipped
        // with seeding guidance (`pinboard-sync --since <d>` once).
        "--pinboard-live".to_string(),
        "--pinboard-since".to_string(),
        "auto".to_string(),
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
        // and which would write live scratch outside the vault.
        "--work-dir".to_string(),
        format!("{VAULT_PLACEHOLDER}/.ovp/work/crystal-synth"),
    ];
    // Semantic-theme re-clustering: BEFORE crystallize's Sunday slot so the
    // weekly synthesis batches over fresh communities, and weekly at all so
    // the themes projection can never quietly go a month stale again
    // (2026-08-06 incident: 203 unclustered packs → 202 claims in
    // Unclassified). A build without the `embed` feature skips gracefully
    // (warn + exit 0), so lean sidecars never fail the tick.
    let themes_argv = vec![
        "crystal-themes".to_string(),
        "--vault-root".to_string(),
        VAULT_PLACEHOLDER.to_string(),
        "--client".to_string(),
        client.to_string(),
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
                id: "themes".to_string(),
                cadence: "weekly Sun 09:30".to_string(),
                argv: themes_argv,
                enabled: true,
                description: "Re-cluster semantic themes (embeddings + labels)".to_string(),
                stamp_date: false,
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
    /// `ok`, `error`, or `seeded` (install placeholder so a fresh job runs at
    /// its next occurrence, not immediately).
    pub last_status: String,
    /// Tail of the child's stderr from the last FAILED run, cleared on ok.
    /// The 2026-08-03 crystallize failure was undiagnosable because the tick
    /// discarded stderr — a failure must carry its own evidence.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    /// Trigger-level counters (never reset): a scheduler that is alive but
    /// failing every trigger must be distinguishable from one that never
    /// fires at all.
    #[serde(default)]
    pub runs_total: u64,
    #[serde(default)]
    pub failures_total: u64,
    /// Consecutive failures since the last ok — the portal escalation signal.
    #[serde(default)]
    pub consecutive_failures: u32,
}

/// One job execution's outcome as seen by the runner.
#[derive(Debug, Clone, Default)]
pub struct JobResult {
    pub ok: bool,
    /// Bounded stderr tail when the job failed (None on ok/spawn-fail-known).
    pub error_tail: Option<String>,
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

    /// Fail loud on a corrupt `last_run`: silently treating it as "never run"
    /// would make `plan_tick` re-dispatch a job that already ran (possibly an
    /// expensive/non-idempotent one), erasing authoritative history.
    pub fn validate(&self) -> Result<(), String> {
        for (id, run) in &self.runs {
            if NaiveDateTime::parse_from_str(&run.last_run, "%Y-%m-%dT%H:%M:%S").is_err() {
                return Err(format!(
                    "job '{id}': invalid last_run '{}' (expected YYYY-MM-DDTHH:MM:SS)",
                    run.last_run
                ));
            }
        }
        Ok(())
    }

    /// Record a job's terminal outcome at `now` (formatted local wall-clock).
    /// Counters carry over from the previous entry; `seeded` placeholders do
    /// not count as runs.
    pub fn record(&mut self, id: &str, now: NaiveDateTime, status: &str) {
        self.record_outcome(id, now, status, None);
    }

    pub fn record_outcome(
        &mut self,
        id: &str,
        now: NaiveDateTime,
        status: &str,
        error_tail: Option<String>,
    ) {
        let prev = self.runs.get(id).cloned().unwrap_or_default();
        let counted = status == "ok" || status == "error";
        let failed = status == "error";
        self.runs.insert(
            id.to_string(),
            JobRun {
                last_run: now.format("%Y-%m-%dT%H:%M:%S").to_string(),
                last_status: status.to_string(),
                last_error: if failed { error_tail } else { None },
                runs_total: prev.runs_total + u64::from(counted),
                failures_total: prev.failures_total + u64::from(failed),
                consecutive_failures: if failed {
                    prev.consecutive_failures + 1
                } else if status == "ok" {
                    0
                } else {
                    prev.consecutive_failures
                },
            },
        );
    }
}

// ---------------------------------------------------------------------------
// Load / save (atomic temp+rename for both files). String errors — the adapter
// maps them into its own error type.
// ---------------------------------------------------------------------------

pub fn registry_path(vault_root: &Path) -> PathBuf {
    vault_root.join(REGISTRY_REL)
}

pub fn state_path(vault_root: &Path) -> PathBuf {
    vault_root.join(STATE_REL)
}

/// Load the registry, quarantining individual unusable jobs.
///
/// Still `Err` when the FILE is the problem (missing read permission,
/// unparseable JSON) — that is genuinely all-or-nothing and there is no
/// partial registry to salvage. A bad cadence inside an otherwise valid file
/// is not: it takes down one job, and everything else must keep running.
pub fn load_registry(vault_root: &Path) -> Result<Option<LoadedRegistry>, String> {
    let path = registry_path(vault_root);
    if !path.exists() {
        return Ok(None);
    }
    let text =
        std::fs::read_to_string(&path).map_err(|e| format!("read {}: {e}", path.display()))?;
    let reg: Registry =
        serde_json::from_str(&text).map_err(|e| format!("parse {}: {e}", path.display()))?;
    let as_written = reg.clone();
    let (registry, rejected) = reg.partition();
    Ok(Some(LoadedRegistry {
        registry,
        rejected,
        as_written,
    }))
}

pub fn save_registry(vault_root: &Path, reg: &Registry) -> Result<(), String> {
    write_json_atomic(&registry_path(vault_root), reg)
}

pub fn load_state(vault_root: &Path) -> Result<State, String> {
    let path = state_path(vault_root);
    if !path.exists() {
        return Ok(State::default());
    }
    let text =
        std::fs::read_to_string(&path).map_err(|e| format!("read {}: {e}", path.display()))?;
    let state: State =
        serde_json::from_str(&text).map_err(|e| format!("parse {}: {e}", path.display()))?;
    state
        .validate()
        .map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(state)
}

pub fn save_state(vault_root: &Path, state: &State) -> Result<(), String> {
    write_json_atomic(&state_path(vault_root), state)
}

fn write_json_atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string_pretty(value)
        .map_err(|e| format!("serialize {}: {e}", path.display()))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, body).map_err(|e| format!("write {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path)
        .map_err(|e| format!("rename {} -> {}: {e}", tmp.display(), path.display()))
}

// ---------------------------------------------------------------------------
// Dispatch — shell-command construction, the runner trait, and the pure plan.
// ---------------------------------------------------------------------------

/// Single-quote a string for /bin/sh.
pub fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
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
    // `.ovp/providers.toml` supersedes shell-sourcing: the child ovp2 process
    // loads it itself at startup, so sourcing daily.env here would both
    // OVERRIDE the file's values (env wins inside the child) and reintroduce
    // the launchd EPERM failure mode the file exists to retire.
    let providers_active = vault_root.join(".ovp/providers.toml").is_file();
    if let Some(env) = env_file
        && !providers_active
    {
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

/// The same job as a program + argv, with no shell in between — what Windows
/// dispatch uses (`/bin/sh` does not exist there, and routing through cmd.exe
/// would re-introduce quoting bugs that `sh_quote` exists to avoid).
///
/// The one capability this drops is env-file sourcing: there is no portable
/// `. daily.env`. That is deliberate rather than missing — `.ovp/providers.toml`
/// already supersedes `daily.env` on every platform (the child process reads it
/// itself), so Windows simply never gets the legacy path. `run_with` surfaces
/// this as a warning when a Windows registry still names an env file.
///
/// `today` is the caller's local `YYYY-MM-DD`; the shell form computes it with
/// `$(date +%F)` at dispatch, and this is the same value.
pub fn job_direct_command(
    ovp2_path: &Path,
    vault_root: &Path,
    job: &JobConfig,
    today: &str,
) -> (PathBuf, Vec<String>) {
    let mut args: Vec<String> = job
        .argv
        .iter()
        .map(|a| resolve_vault(a, vault_root))
        .collect();
    if job.stamp_date {
        args.push("--date".into());
        args.push(today.to_string());
    }
    (ovp2_path.to_path_buf(), args)
}

pub trait JobRunner {
    /// Run one job; the result carries success and, on failure, a bounded
    /// stderr tail so the state file records WHY.
    fn run(&self, job: &JobConfig) -> JobResult;
}

/// The pure decision of which jobs a tick should run, given `now` and the
/// recorded state. No side effects — the adapter runs `due` in order, persisting
/// after each so an interrupted tick never reruns a completed job.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct TickPlan {
    /// Job ids to run, in registry order.
    pub due: Vec<String>,
    /// Job ids skipped because not due.
    pub skipped_not_due: Vec<String>,
    /// Job ids skipped because disabled.
    pub skipped_disabled: Vec<String>,
    /// Job ids skipped because their cadence does not parse — broken, not
    /// waiting. Kept apart from `skipped_not_due` so a job that will NEVER run
    /// cannot read as one that simply is not due yet.
    pub skipped_invalid: Vec<String>,
}

pub fn plan_tick(reg: &Registry, state: &State, now: NaiveDateTime) -> TickPlan {
    let mut plan = TickPlan::default();
    for job in &reg.jobs {
        if !job.enabled {
            plan.skipped_disabled.push(job.id.clone());
            continue;
        }
        // A cadence that fails to parse is quarantined at load, but stay
        // defensive here for a registry built in memory. Its own bucket, NOT
        // `skipped_not_due`: "not due" reads as "fine, just not time yet",
        // while the truth is the job is broken and will never run.
        let Ok(cadence) = job.parsed_cadence() else {
            plan.skipped_invalid.push(job.id.clone());
            continue;
        };
        if is_due(cadence, state.last_run_of(&job.id), now) {
            plan.due.push(job.id.clone());
        } else {
            plan.skipped_not_due.push(job.id.clone());
        }
    }
    plan
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct TickReport {
    /// (job id, ok) for jobs that ran this tick.
    pub ran: Vec<(String, bool)>,
    pub skipped_not_due: Vec<String>,
    pub skipped_disabled: Vec<String>,
}

/// In-memory tick (no persistence): run every due job via `runner` and return
/// the updated state + report. The CLI adapter uses [`plan_tick`] directly so it
/// can persist after each job; this convenience is for tests and embedders that
/// don't need incremental durability.
pub fn tick_with(
    reg: &Registry,
    state: &State,
    now: NaiveDateTime,
    runner: &dyn JobRunner,
) -> (State, TickReport) {
    let plan = plan_tick(reg, state, now);
    let mut new_state = state.clone();
    let mut report = TickReport {
        skipped_not_due: plan.skipped_not_due,
        skipped_disabled: plan.skipped_disabled,
        ..Default::default()
    };
    for id in &plan.due {
        let job = reg.get(id).expect("plan ids come from the registry");
        let result = runner.run(job);
        new_state.record_outcome(
            id,
            now,
            if result.ok { "ok" } else { "error" },
            result.error_tail.clone(),
        );
        report.ran.push((id.clone(), result.ok));
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
    let result = runner.run(job);
    let ok = result.ok;
    let mut new_state = state.clone();
    new_state.record_outcome(
        &job.id,
        now,
        if ok { "ok" } else { "error" },
        result.error_tail,
    );
    Ok((new_state, ok))
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
        for s in [
            "daily 09:00",
            "daily 00:00",
            "weekly Sun 10:00",
            "weekly Fri 23:59",
        ] {
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
            "",
            "daily",
            "daily 24:00",
            "weekly 10:00",
            "weekly Xxx 10:00",
            "monthly 09:00",
            "daily 9:0",
        ] {
            assert!(Cadence::parse(bad).is_err(), "{bad} should be rejected");
        }
    }

    // -- interval cadence (`every <N>h`) ------------------------------------

    #[test]
    fn interval_cadence_round_trips_and_rejects_garbage() {
        for s in ["every 1h", "every 4h", "every 23h"] {
            assert_eq!(Cadence::parse(s).unwrap().to_display(), s, "round-trip {s}");
        }
        for bad in [
            "every 0h",  // would divide by zero
            "every 24h", // that is `daily 00:00` — say what you mean
            "every 25h",
            "every 4",
            "every h",
            "every -4h",
            "every 4hh",
            "every 4h 00:00",
        ] {
            assert!(Cadence::parse(bad).is_err(), "{bad} should be rejected");
        }
    }

    #[test]
    fn interval_anchors_at_midnight_and_floors_to_the_slot() {
        let c = Cadence::parse("every 4h").unwrap();
        // Slots: 00 04 08 12 16 20. Sub-hour components are dropped.
        for (now, want) in [
            ("2026-07-12T00:00:00", "2026-07-12T00:00:00"),
            ("2026-07-12T03:59:59", "2026-07-12T00:00:00"),
            ("2026-07-12T04:00:00", "2026-07-12T04:00:00"),
            ("2026-07-12T13:59:00", "2026-07-12T12:00:00"),
            ("2026-07-12T23:59:59", "2026-07-12T20:00:00"),
        ] {
            assert_eq!(c.most_recent_occurrence(dt(now)), dt(want), "now={now}");
        }
    }

    #[test]
    fn interval_next_occurrence_never_steps_past_midnight() {
        // 24 % 5 != 0, so the final slot is SHORT: 20:00 → 00:00, not 01:00.
        // Stepping naively from `prev` would drift the whole next day's grid.
        let c = Cadence::parse("every 5h").unwrap();
        assert_eq!(
            c.next_occurrence(dt("2026-07-12T21:30:00")),
            dt("2026-07-13T00:00:00")
        );
        // A cadence that divides 24 is unaffected mid-day.
        let c4 = Cadence::parse("every 4h").unwrap();
        assert_eq!(
            c4.next_occurrence(dt("2026-07-12T13:00:00")),
            dt("2026-07-12T16:00:00")
        );
        assert_eq!(
            c4.next_occurrence(dt("2026-07-12T21:00:00")),
            dt("2026-07-13T00:00:00")
        );
    }

    #[test]
    fn interval_next_occurrence_is_strictly_after_now() {
        for spec in ["every 1h", "every 4h", "every 5h", "every 7h", "every 23h"] {
            let c = Cadence::parse(spec).unwrap();
            for h in 0..24 {
                for m in [0, 1, 30, 59] {
                    let now = dt(&format!("2026-07-12T{h:02}:{m:02}:00"));
                    let next = c.next_occurrence(now);
                    assert!(next > now, "{spec} at {now}: next {next} must be > now");
                    assert!(
                        c.most_recent_occurrence(now) <= now,
                        "{spec} at {now}: prev must be <= now"
                    );
                }
            }
        }
    }

    #[test]
    fn interval_is_due_once_per_slot() {
        let c = Cadence::parse("every 4h").unwrap();
        let now = dt("2026-07-12T13:00:00"); // slot 12:00
        assert!(is_due(c, None, now), "never run → due");
        assert!(
            !is_due(c, Some(dt("2026-07-12T12:05:00")), now),
            "already ran inside this slot → not due"
        );
        assert!(
            is_due(c, Some(dt("2026-07-12T11:59:00")), now),
            "ran in the PREVIOUS slot → due again"
        );
        // The is_due contract is status-blind (see `is_due` docs): a run that
        // FAILED inside the slot still consumes it. With `every 4h` the wait is
        // 4 hours, not the 24 a `daily` cadence would impose.
        assert!(!is_due(c, Some(dt("2026-07-12T12:00:00")), now));
    }

    // -- most_recent_occurrence / is_due ------------------------------------

    #[test]
    fn daily_due_after_the_time_passes() {
        let c = Cadence::parse("daily 09:00").unwrap();
        let now = dt("2026-07-12T09:30:00"); // Sunday
        assert!(is_due(c, None, now));
        assert!(!is_due(c, Some(dt("2026-07-12T09:05:00")), now));
        assert!(is_due(c, Some(dt("2026-07-11T09:05:00")), now));
    }

    #[test]
    fn daily_not_due_before_the_time() {
        let c = Cadence::parse("daily 09:00").unwrap();
        let now = dt("2026-07-12T08:00:00");
        assert!(!is_due(c, Some(dt("2026-07-11T09:05:00")), now));
        assert!(is_due(c, Some(dt("2026-07-10T09:05:00")), now));
    }

    #[test]
    fn weekly_fires_on_its_weekday_and_not_between() {
        let c = Cadence::parse("weekly Sun 10:00").unwrap();
        let sun_11 = dt("2026-07-12T11:00:00");
        assert!(is_due(c, None, sun_11));
        assert!(!is_due(c, Some(dt("2026-07-12T10:01:00")), sun_11));
        let wed = dt("2026-07-15T09:00:00");
        assert!(!is_due(c, Some(dt("2026-07-12T10:01:00")), wed));
        assert!(is_due(c, Some(dt("2026-07-05T10:01:00")), wed));
    }

    #[test]
    fn weekly_before_time_on_its_day_looks_back_a_week() {
        let c = Cadence::parse("weekly Sun 10:00").unwrap();
        let sun_early = dt("2026-07-12T08:00:00");
        assert!(!is_due(c, Some(dt("2026-07-05T10:01:00")), sun_early));
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

    // -- registry ------------------------------------------------------------

    #[test]
    fn default_registry_has_daily_weekly_crystallize_and_weekly_themes() {
        let reg = default_registry("live", (9, 0), true, Some(40));
        reg.validate().unwrap();
        let daily = reg.get("daily").unwrap();
        assert_eq!(daily.cadence, "daily 09:00");
        assert!(daily.argv.contains(&VAULT_PLACEHOLDER.to_string()));
        assert!(daily.argv.contains(&"--web-fetch-live".to_string()));
        // Pinboard capture is on by default in the scheduled loop, following
        // the sync watermark (graceful skip without a token / watermark).
        assert!(daily.argv.contains(&"--pinboard-live".to_string()));
        assert!(daily.argv.contains(&"auto".to_string()));
        assert!(daily.argv.contains(&"--max-sources".to_string()));
        assert!(daily.argv.contains(&"40".to_string()));
        let cry = reg.get("crystallize").unwrap();
        assert_eq!(cry.cadence, "weekly Sun 10:00");
        assert!(cry.argv.contains(&"--refresh".to_string()));
        assert!(
            cry.argv
                .contains(&format!("{VAULT_PLACEHOLDER}/.ovp/work/crystal-synth"))
        );
        assert_eq!(
            reg.env_file.as_deref(),
            Some(format!("{VAULT_PLACEHOLDER}/.ovp/daily.env").as_str())
        );
        // The themes job runs BEFORE crystallize's slot and skips gracefully
        // on embed-less builds — weekly by default so the projection can
        // never quietly go a month stale again.
        let themes = reg.get("themes").unwrap();
        assert_eq!(themes.cadence, "weekly Sun 09:30");
        assert!(themes.argv.contains(&"crystal-themes".to_string()));
        assert!(themes.argv.contains(&VAULT_PLACEHOLDER.to_string()));
        assert!(!themes.stamp_date);
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

    fn job(id: &str, cadence: &str) -> JobConfig {
        JobConfig {
            id: id.into(),
            cadence: cadence.into(),
            argv: vec![],
            enabled: true,
            description: String::new(),
            stamp_date: false,
        }
    }

    #[test]
    fn registry_validate_rejects_dupes_and_bad_cadence() {
        let dupe = Registry {
            version: 1,
            env_file: None,
            jobs: vec![job("a", "daily 09:00"), job("a", "daily 10:00")],
        };
        assert!(dupe.validate().unwrap_err().contains("duplicate"));
        let bad = Registry {
            version: 1,
            env_file: None,
            jobs: vec![job("a", "hourly")],
        };
        assert!(bad.validate().is_err());
    }

    // -- persistence round-trip ---------------------------------------------

    #[test]
    fn registry_and_state_persist_atomically() {
        let dir = tempfile::tempdir().unwrap();
        let v = dir.path();
        assert!(load_registry(v).unwrap().is_none());
        let reg = default_registry("live", (9, 0), false, None);
        save_registry(v, &reg).unwrap();
        assert_eq!(load_registry(v).unwrap().unwrap().as_written, reg);
        let mut state = State::default();
        state.record("daily", dt("2026-07-12T09:05:00"), "ok");
        save_state(v, &state).unwrap();
        assert_eq!(
            load_state(v).unwrap().last_run_of("daily"),
            Some(dt("2026-07-12T09:05:00"))
        );
    }

    // -- shell command builder ----------------------------------------------

    #[test]
    fn vault_paths_resolve_with_native_separators() {
        let root = Path::new(r"C:\vault root\100% notes");
        assert_eq!(
            PathBuf::from(resolve_vault("{vault}/.ovp/work/crystal-synth", root)),
            root.join(".ovp").join("work").join("crystal-synth")
        );
        assert_eq!(
            PathBuf::from(resolve_vault(r"{vault}\.ovp\daily.env", root)),
            root.join(".ovp").join("daily.env")
        );
        assert_eq!(resolve_vault("literal", root), "literal");
    }

    #[cfg(not(windows))]
    #[test]
    fn shell_command_resolves_vault_sources_env_and_stamps_date() {
        let j = JobConfig {
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
            &j,
        );
        assert_eq!(
            cmd,
            "set -a; . '/Users/op/ovp-vault/.ovp/daily.env'; set +a; \
             exec '/opt/homebrew/bin/ovp2' 'daily' '--vault-root' '/Users/op/ovp-vault' \
             --date \"$(date +%F)\""
        );
    }

    #[test]
    fn shell_command_skips_env_sourcing_when_providers_toml_exists() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        std::fs::create_dir_all(vault.join(".ovp")).unwrap();
        std::fs::write(vault.join(".ovp/providers.toml"), "[env]\n").unwrap();
        let j = JobConfig {
            id: "daily".into(),
            cadence: "daily 09:00".into(),
            argv: vec!["daily".into()],
            enabled: true,
            description: String::new(),
            stamp_date: false,
        };
        let cmd = job_shell_command(
            Path::new("/bin/ovp2"),
            Some(Path::new("{vault}/.ovp/daily.env")),
            vault,
            &j,
        );
        assert!(
            !cmd.contains("daily.env"),
            "providers.toml supersedes daily.env sourcing: {cmd}"
        );
        assert!(cmd.starts_with("exec "), "{cmd}");
    }

    #[test]
    fn shell_command_without_env_or_date() {
        let j = JobConfig {
            id: "x".into(),
            cadence: "daily 09:00".into(),
            argv: vec!["doctor".into()],
            enabled: true,
            description: String::new(),
            stamp_date: false,
        };
        let cmd = job_shell_command(Path::new("/bin/ovp2"), None, Path::new("/v"), &j);
        assert_eq!(cmd, "exec '/bin/ovp2' 'doctor'");
    }

    // -- plan / tick dispatch -----------------------------------------------

    #[derive(Default)]
    struct FakeRunner {
        ran: RefCell<Vec<String>>,
        fail: Vec<String>,
    }
    impl JobRunner for FakeRunner {
        fn run(&self, job: &JobConfig) -> JobResult {
            self.ran.borrow_mut().push(job.id.clone());
            let ok = !self.fail.contains(&job.id);
            JobResult {
                ok,
                error_tail: (!ok).then(|| format!("stderr tail for {}", job.id)),
            }
        }
    }

    #[test]
    fn record_outcome_tracks_counters_and_error_evidence() {
        let mut state = State::default();
        let t = dt("2026-08-06T10:00:00");
        // seeded placeholder: not a run.
        state.record("daily", t, "seeded");
        let run = state.runs.get("daily").unwrap();
        assert_eq!(
            (run.runs_total, run.failures_total, run.consecutive_failures),
            (0, 0, 0)
        );

        state.record_outcome("daily", t, "error", Some("boom: refresh failed".into()));
        state.record_outcome("daily", t, "error", Some("boom again".into()));
        let run = state.runs.get("daily").unwrap();
        assert_eq!(
            (run.runs_total, run.failures_total, run.consecutive_failures),
            (2, 2, 2)
        );
        assert_eq!(run.last_error.as_deref(), Some("boom again"));

        // ok clears the error evidence and the consecutive streak, keeps totals.
        state.record_outcome("daily", t, "ok", None);
        let run = state.runs.get("daily").unwrap();
        assert_eq!(
            (run.runs_total, run.failures_total, run.consecutive_failures),
            (3, 2, 0)
        );
        assert!(run.last_error.is_none());

        // Old state files (no counter fields) deserialize with defaults.
        let legacy: State = serde_json::from_str(
            r#"{"runs":{"daily":{"last_run":"2026-08-03T19:41:37","last_status":"error"}}}"#,
        )
        .unwrap();
        assert_eq!(legacy.runs.get("daily").unwrap().runs_total, 0);
    }

    /// The full default registry (daily + weekly crystallize + weekly themes).
    fn default_jobs_registry() -> Registry {
        default_registry("live", (9, 0), false, None)
    }

    #[test]
    fn plan_tick_partitions_due_disabled_and_not_due() {
        let mut reg = default_jobs_registry();
        reg.get_mut("crystallize").unwrap().enabled = false;
        let now = dt("2026-07-12T09:30:00"); // Sunday: daily + themes due, crystallize off
        let plan = plan_tick(&reg, &State::default(), now);
        assert_eq!(plan.due, vec!["daily".to_string(), "themes".to_string()]);
        assert_eq!(plan.skipped_disabled, vec!["crystallize".to_string()]);
        assert!(plan.skipped_not_due.is_empty());
        // Once both have run, the next plan has nothing due.
        let mut state = State::default();
        state.record("daily", now, "ok");
        state.record("themes", now, "ok");
        assert!(plan_tick(&reg, &state, now).due.is_empty());
    }

    #[test]
    fn tick_runs_due_jobs_and_records_state() {
        let reg = default_jobs_registry();
        let now = dt("2026-07-12T10:30:00"); // all three due (Sunday past 10:00)
        let runner = FakeRunner {
            fail: vec!["crystallize".into()],
            ..Default::default()
        };
        let (new_state, report) = tick_with(&reg, &State::default(), now, &runner);
        assert_eq!(report.ran.len(), 3);
        assert_eq!(new_state.runs.get("themes").unwrap().last_status, "ok");
        assert_eq!(new_state.runs.get("daily").unwrap().last_status, "ok");
        assert_eq!(
            new_state.runs.get("crystallize").unwrap().last_status,
            "error"
        );
    }

    #[test]
    fn run_now_ignores_cadence() {
        let reg = default_jobs_registry();
        let now = dt("2026-07-12T08:00:00"); // crystallize NOT due
        let runner = FakeRunner::default();
        let (new_state, ok) =
            run_now_with(&reg, &State::default(), "crystallize", now, &runner).unwrap();
        assert!(ok);
        assert_eq!(*runner.ran.borrow(), vec!["crystallize".to_string()]);
        assert!(new_state.runs.contains_key("crystallize"));
        assert!(run_now_with(&reg, &State::default(), "nope", now, &runner).is_err());
    }

    /// Write a registry file whose second job has a cadence this binary cannot
    /// parse — the exact shape of the failure CLAUDE.md documents (schedule.json
    /// edited to a syntax newer than the installed binary).
    fn registry_with_one_bad_cadence(v: &Path) {
        std::fs::create_dir_all(v.join(".ovp")).unwrap();
        let raw = r#"{
          "version": 1,
          "jobs": [
            {"id":"daily","cadence":"daily 09:00","argv":["daily"],"enabled":true,"description":"d"},
            {"id":"future","cadence":"every 3rd tuesday","argv":["x"],"enabled":true,"description":"f"},
            {"id":"themes","cadence":"weekly Sun 08:00","argv":["themes"],"enabled":true,"description":"t"}
          ]
        }"#;
        std::fs::write(v.join(REGISTRY_REL), raw).unwrap();
    }

    #[test]
    fn one_unparseable_cadence_does_not_stop_the_other_jobs() {
        // The whole point. This used to make load_registry return Err, which
        // made `schedule list` and `tick` exit non-zero — every job stopped,
        // not just the broken one, and from the GUI it looked like nothing
        // happening at all.
        let dir = tempfile::tempdir().unwrap();
        registry_with_one_bad_cadence(dir.path());

        let loaded = load_registry(dir.path()).unwrap().unwrap();
        let ids: Vec<&str> = loaded.registry.jobs.iter().map(|j| j.id.as_str()).collect();
        assert_eq!(ids, vec!["daily", "themes"], "healthy jobs survive");
        assert_eq!(loaded.rejected.len(), 1);
        assert_eq!(loaded.rejected[0].id, "future");
        assert!(
            loaded.rejected[0].reason.contains("invalid cadence"),
            "the reason must name the problem: {}",
            loaded.rejected[0].reason
        );
    }

    #[test]
    fn a_quarantined_job_is_kept_in_the_file_not_deleted() {
        // Quarantine hides a job from the dispatcher. If an unrelated edit
        // (`schedule disable`) then saved the FILTERED view, the operator's
        // broken definition would be erased instead of fixed — a worse
        // outcome than the bug this whole change exists to fix.
        let dir = tempfile::tempdir().unwrap();
        registry_with_one_bad_cadence(dir.path());

        let loaded = load_registry(dir.path()).unwrap().unwrap();
        let mut edited = loaded.as_written;
        assert_eq!(edited.jobs.len(), 3, "as_written keeps the broken job");
        edited.get_mut("daily").unwrap().enabled = false;
        save_registry(dir.path(), &edited).unwrap();

        let after = load_registry(dir.path()).unwrap().unwrap();
        assert_eq!(after.as_written.jobs.len(), 3, "still three on disk");
        assert_eq!(after.rejected.len(), 1, "still quarantined, still present");
    }

    #[test]
    fn a_broken_job_is_reported_as_invalid_not_as_not_due() {
        // "not due" reads as "fine, just not time yet". A job that will NEVER
        // run must not hide in that bucket.
        let mut reg = default_jobs_registry();
        reg.jobs.push(JobConfig {
            id: "broken".into(),
            cadence: "every 3rd tuesday".into(),
            argv: vec!["x".into()],
            enabled: true,
            description: "b".into(),
            ..reg.jobs[0].clone()
        });
        let plan = plan_tick(&reg, &State::default(), dt("2026-07-12T09:30:00"));
        assert_eq!(plan.skipped_invalid, vec!["broken".to_string()]);
        assert!(!plan.skipped_not_due.contains(&"broken".to_string()));
    }

    #[test]
    fn a_duplicate_id_quarantines_the_later_copy_and_keeps_the_first() {
        // `get()` already resolves to the first occurrence, so quarantining the
        // later one matches the behaviour the rest of the code already has.
        let dir = tempfile::tempdir().unwrap();
        let v = dir.path();
        std::fs::create_dir_all(v.join(".ovp")).unwrap();
        std::fs::write(
            v.join(REGISTRY_REL),
            r#"{"version":1,"jobs":[
              {"id":"daily","cadence":"daily 09:00","argv":["first"],"enabled":true,"description":"a"},
              {"id":"daily","cadence":"daily 10:00","argv":["second"],"enabled":true,"description":"b"}
            ]}"#,
        )
        .unwrap();
        let loaded = load_registry(v).unwrap().unwrap();
        assert_eq!(loaded.registry.jobs.len(), 1);
        assert_eq!(loaded.registry.jobs[0].argv, vec!["first".to_string()]);
        assert_eq!(loaded.rejected.len(), 1);
        assert!(loaded.rejected[0].reason.contains("duplicate"));
    }

    #[test]
    fn a_file_level_problem_is_still_fatal() {
        // Per-job quarantine must not swallow a genuinely unusable FILE —
        // there is no partial registry to salvage from unparseable JSON.
        let dir = tempfile::tempdir().unwrap();
        let v = dir.path();
        std::fs::create_dir_all(v.join(".ovp")).unwrap();
        std::fs::write(v.join(REGISTRY_REL), "{not json").unwrap();
        assert!(load_registry(v).is_err());
    }

    #[test]
    fn load_state_rejects_malformed_last_run() {
        let dir = tempfile::tempdir().unwrap();
        let v = dir.path();
        std::fs::create_dir_all(v.join(".ovp")).unwrap();
        // Valid JSON, invalid timestamp — must fail loud, not silently reset.
        std::fs::write(
            state_path(v),
            r#"{"runs":{"daily":{"last_run":"not-a-date","last_status":"ok"}}}"#,
        )
        .unwrap();
        let err = load_state(v).unwrap_err();
        assert!(err.contains("invalid last_run"), "got: {err}");
    }

    #[test]
    fn state_parses_last_run_timestamp() {
        let mut state = State::default();
        state.record("daily", dt("2026-07-12T09:05:00"), "ok");
        assert_eq!(state.last_run_of("daily"), Some(dt("2026-07-12T09:05:00")));
        assert_eq!(state.last_run_of("missing"), None);
    }
}
