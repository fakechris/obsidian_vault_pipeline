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

/// Resolve `{vault}` in a stored string against the live vault root.
pub fn resolve_vault(s: &str, vault_root: &Path) -> String {
    s.replace(VAULT_PLACEHOLDER, &vault_root.display().to_string())
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
        // and which would write live scratch outside the vault.
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
    /// `ok`, `error`, or `seeded` (install placeholder so a fresh job runs at
    /// its next occurrence, not immediately).
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
    pub fn record(&mut self, id: &str, now: NaiveDateTime, status: &str) {
        self.runs.insert(
            id.to_string(),
            JobRun {
                last_run: now.format("%Y-%m-%dT%H:%M:%S").to_string(),
                last_status: status.to_string(),
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

pub fn load_registry(vault_root: &Path) -> Result<Option<Registry>, String> {
    let path = registry_path(vault_root);
    if !path.exists() {
        return Ok(None);
    }
    let text =
        std::fs::read_to_string(&path).map_err(|e| format!("read {}: {e}", path.display()))?;
    let reg: Registry =
        serde_json::from_str(&text).map_err(|e| format!("parse {}: {e}", path.display()))?;
    reg.validate()?;
    Ok(Some(reg))
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
}

pub fn plan_tick(reg: &Registry, state: &State, now: NaiveDateTime) -> TickPlan {
    let mut plan = TickPlan::default();
    for job in &reg.jobs {
        if !job.enabled {
            plan.skipped_disabled.push(job.id.clone());
            continue;
        }
        // A cadence that fails to parse was rejected at load; stay defensive and
        // skip rather than panic in the dispatcher.
        let Ok(cadence) = job.parsed_cadence() else {
            plan.skipped_not_due.push(job.id.clone());
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
        let ok = runner.run(job);
        new_state.record(id, now, if ok { "ok" } else { "error" });
        report.ran.push((id.clone(), ok));
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
    new_state.record(&job.id, now, if ok { "ok" } else { "error" });
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
    fn default_registry_has_daily_and_weekly_crystallize() {
        let reg = default_registry("live", (9, 0), true, Some(40));
        reg.validate().unwrap();
        let daily = reg.get("daily").unwrap();
        assert_eq!(daily.cadence, "daily 09:00");
        assert!(daily.argv.contains(&VAULT_PLACEHOLDER.to_string()));
        assert!(daily.argv.contains(&"--web-fetch-live".to_string()));
        assert!(daily.argv.contains(&"--max-sources".to_string()));
        assert!(daily.argv.contains(&"40".to_string()));
        let cry = reg.get("crystallize").unwrap();
        assert_eq!(cry.cadence, "weekly Sun 10:00");
        assert!(cry.argv.contains(&"--refresh".to_string()));
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
        assert_eq!(load_registry(v).unwrap().unwrap(), reg);
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
        fn run(&self, job: &JobConfig) -> bool {
            self.ran.borrow_mut().push(job.id.clone());
            !self.fail.contains(&job.id)
        }
    }

    fn two_job_registry() -> Registry {
        default_registry("live", (9, 0), false, None)
    }

    #[test]
    fn plan_tick_partitions_due_disabled_and_not_due() {
        let mut reg = two_job_registry();
        reg.get_mut("crystallize").unwrap().enabled = false;
        let now = dt("2026-07-12T09:30:00"); // Sunday, daily due, crystallize off
        let plan = plan_tick(&reg, &State::default(), now);
        assert_eq!(plan.due, vec!["daily".to_string()]);
        assert_eq!(plan.skipped_disabled, vec!["crystallize".to_string()]);
        assert!(plan.skipped_not_due.is_empty());
        // Once daily has run, the next plan has nothing due.
        let mut state = State::default();
        state.record("daily", now, "ok");
        assert!(plan_tick(&reg, &state, now).due.is_empty());
    }

    #[test]
    fn tick_runs_due_jobs_and_records_state() {
        let reg = two_job_registry();
        let now = dt("2026-07-12T10:30:00"); // both due
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
        let now = dt("2026-07-12T08:00:00"); // crystallize NOT due
        let runner = FakeRunner::default();
        let (new_state, ok) =
            run_now_with(&reg, &State::default(), "crystallize", now, &runner).unwrap();
        assert!(ok);
        assert_eq!(*runner.ran.borrow(), vec!["crystallize".to_string()]);
        assert!(new_state.runs.contains_key("crystallize"));
        assert!(run_now_with(&reg, &State::default(), "nope", now, &runner).is_err());
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
