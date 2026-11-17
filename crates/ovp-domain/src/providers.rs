//! `.ovp/providers.toml` — the vault's provider configuration file.
//!
//! Replaces shell-sourcing `daily.env` (which launchd cannot reliably do —
//! the EPERM incident 2026-07) with a config file the APP reads itself. One
//! flat `[env]` table of UPPERCASE variables; values act as DEFAULTS — a
//! variable already present in the process environment always wins, so
//! one-off `ANTHROPIC_API_KEY=… ovp2 …` overrides keep working and nothing
//! in the existing env-reading code paths (LLM client, Pinboard, GitHub,
//! embedding cache) had to change.
//!
//! ```toml
//! [env]
//! ANTHROPIC_API_KEY = "sk-…"
//! ANTHROPIC_BASE_URL = "https://…/v1/messages"
//! OVP_LLM_MODEL = "…"
//! OVP_LLM_NO_PROXY = "1"
//! GITHUB_TOKEN = "ghp_…"
//! PINBOARD_TOKEN = "user:…"
//!
//! [budget]
//! daily_token_budget = 200000   # optional; soft, surfaced by `ovp2 usage`
//!
//! [llm_profiles.openai]          # optional saved LLM setups (portal switcher)
//! label = "OpenAI gpt-6-luna"
//! OVP_LLM_PROVIDER = "openai"
//! OPENAI_API_KEY = "sk-…"
//! OVP_LLM_MODEL = "gpt-6-luna"
//! ```
//!
//! `[llm_profiles.*]` are inert storage: only `[env]` is ever exported.
//! Activating a profile copies its [`LLM_PROFILE_KEYS`] into `[env]`
//! (clearing the others), so every reader keeps reading `[env]` alone, and
//! the active profile is DERIVED (the one matching `[env]`), never stored —
//! it cannot drift.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

// NOTE: no top-level `deny_unknown_fields` — a vault's providers.toml is read
// by MULTIPLE binaries of different versions (desktop sidecar, scheduler-
// spawned ovp2, other worktrees), so an unknown section (e.g. `[budget]`
// written before an older binary reads the file) must be TOLERATED, not
// fail loud. Format/type errors still fail loud.
#[derive(Debug, Default, Deserialize)]
struct ProvidersFile {
    #[serde(default)]
    env: BTreeMap<String, toml::Value>,
    #[serde(default)]
    budget: BudgetSection,
    #[serde(default)]
    llm_profiles: BTreeMap<String, BTreeMap<String, toml::Value>>,
}

/// The `[env]` variables one LLM setup consists of. Activating a profile
/// replaces exactly these (unrelated tokens — GitHub, Pinboard … — stay).
pub const LLM_PROFILE_KEYS: &[&str] = &[
    "OVP_LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OVP_LLM_MODEL",
    "OVP_LLM_MAX_TOKENS",
    "OVP_LLM_NO_PROXY",
    "OVP_LLM_TIMEOUT_SECS",
];

/// One saved LLM setup (`[llm_profiles.<id>]`).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LlmProfile {
    pub label: String,
    /// Only [`LLM_PROFILE_KEYS`], non-empty values.
    pub env: BTreeMap<String, String>,
}

/// A profile id usable as a bare TOML key: `[a-z0-9_-]`, 1–40 chars.
pub fn valid_profile_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 40
        && id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

fn scalar_text(value: &toml::Value) -> Option<String> {
    match value {
        toml::Value::String(s) => Some(s.clone()),
        toml::Value::Integer(i) => Some(i.to_string()),
        toml::Value::Boolean(b) => Some(if *b { "1".into() } else { "0".into() }),
        _ => None,
    }
}

/// Read the saved `[llm_profiles.*]`. Missing file → empty; malformed file
/// → Err. Unknown keys inside a profile are ignored (forward compat).
pub fn read_llm_profiles(vault_root: &Path) -> Result<BTreeMap<String, LlmProfile>, String> {
    let Some(file) = parse_providers_file(vault_root)? else {
        return Ok(BTreeMap::new());
    };
    let mut out = BTreeMap::new();
    for (id, table) in file.llm_profiles {
        let label = table
            .get("label")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .unwrap_or_else(|| id.clone());
        let env = table
            .iter()
            .filter(|(k, _)| LLM_PROFILE_KEYS.contains(&k.as_str()))
            .filter_map(|(k, v)| scalar_text(v).map(|t| (k.clone(), t)))
            .filter(|(_, t)| !t.trim().is_empty())
            .collect();
        out.insert(id, LlmProfile { label, env });
    }
    Ok(out)
}

/// The LLM part of an `[env]` map, NORMALIZED — the comparison key for
/// "active". Values that mean the same as absent collapse to absent
/// (`OVP_LLM_PROVIDER=anthropic`, a false `OVP_LLM_NO_PROXY`) and booleans
/// to `1`, so a setup saved from the live config (which the form writes in
/// canonical form) is still recognised as the live one.
pub fn llm_env(env: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    env.iter()
        .filter(|(k, _)| LLM_PROFILE_KEYS.contains(&k.as_str()))
        .filter_map(|(k, v)| {
            let t = v.trim();
            let canon = match k.as_str() {
                "OVP_LLM_PROVIDER" => {
                    let p = t.to_ascii_lowercase();
                    (p != "anthropic").then_some(p)
                }
                "OVP_LLM_NO_PROXY" => match t.to_ascii_lowercase().as_str() {
                    "1" | "true" | "yes" | "on" => Some("1".to_string()),
                    "0" | "false" | "no" | "off" => None,
                    _ => Some(t.to_string()),
                },
                _ => Some(t.to_string()),
            };
            canon.filter(|c| !c.is_empty()).map(|c| (k.clone(), c))
        })
        .collect()
}

/// The profile whose LLM variables equal `[env]`'s, if any (first by id).
pub fn active_llm_profile<'a>(
    env: &BTreeMap<String, String>,
    profiles: &'a BTreeMap<String, LlmProfile>,
) -> Option<&'a str> {
    let current = llm_env(env);
    profiles
        .iter()
        .find(|(_, p)| llm_env(&p.env) == current)
        .map(|(id, _)| id.as_str())
}

/// Replace the saved profiles (the `[env]` table and every other section are
/// kept as they are).
pub fn write_llm_profiles(
    vault_root: &Path,
    profiles: &BTreeMap<String, LlmProfile>,
) -> Result<(), String> {
    for id in profiles.keys() {
        if !valid_profile_id(id) {
            return Err(format!("`{id}` is not a valid profile id (a-z, 0-9, _ or -)"));
        }
    }
    let env = read_providers_file(vault_root)?;
    write_file(vault_root, &env, Some(profiles))
}

/// Make `id` the live LLM setup: its variables replace every
/// [`LLM_PROFILE_KEYS`] entry in `[env]`; other `[env]` entries stay.
pub fn activate_llm_profile(vault_root: &Path, id: &str) -> Result<(), String> {
    let profiles = read_llm_profiles(vault_root)?;
    let profile = profiles.get(id).ok_or_else(|| format!("no LLM profile `{id}`"))?;
    let mut env = read_providers_file(vault_root)?;
    env.retain(|k, _| !LLM_PROFILE_KEYS.contains(&k.as_str()));
    env.extend(profile.env.clone());
    write_providers_file(vault_root, &env)
}

/// `[budget]` — soft per-day token budget, surfaced by `ovp2 usage` as a
/// `today: X / Y (Z%)` line with 80%/100% warnings. Visibility only — by
/// operator decision nothing is ever blocked on it.
#[derive(Debug, Default, PartialEq, Eq, Deserialize)]
pub struct BudgetSection {
    #[serde(default)]
    pub daily_token_budget: Option<usize>,
}

/// What `apply_providers_env` did, for the caller's (stderr) log line.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ProvidersApplied {
    /// Variables set from the file (they were absent from the environment).
    pub applied: Vec<String>,
    /// Variables skipped because the environment already had them.
    pub already_set: Vec<String>,
}

/// Read + parse `<vault>/.ovp/providers.toml`. Missing file → `Ok(None)`;
/// malformed TOML / type errors → `Err` (fail loud). Unknown top-level
/// sections are tolerated (forward compat — see the struct note).
fn parse_providers_file(vault_root: &Path) -> Result<Option<ProvidersFile>, String> {
    let path = vault_root.join(".ovp/providers.toml");
    let raw = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    // NOT `{e}`: toml's Display quotes the offending source line, which on a
    // credential line is the secret — and this error reaches stderr (scheduler
    // logs), the portal and doctor. Location + reason only.
    let file: ProvidersFile = toml::from_str(&raw).map_err(|e| {
        let at = e
            .span()
            .map(|s| {
                let before = &raw[..s.start.min(raw.len())];
                let line = before.matches('\n').count() + 1;
                let col = before.rsplit('\n').next().map_or(0, |l| l.chars().count()) + 1;
                format!(" at line {line}, column {col}")
            })
            .unwrap_or_default();
        format!("parsing {}{at}: {}", path.display(), e.message())
    })?;
    Ok(Some(file))
}

/// Read `<vault>/.ovp/providers.toml`'s `[env]` table as strings (the same
/// scalar coercion `apply_providers_env` uses). Missing file → empty map;
/// malformed → Err.
pub fn read_providers_file(
    vault_root: &Path,
) -> Result<std::collections::BTreeMap<String, String>, String> {
    let path = vault_root.join(".ovp/providers.toml");
    let Some(file) = parse_providers_file(vault_root)? else {
        return Ok(Default::default());
    };
    let mut out = std::collections::BTreeMap::new();
    for (name, value) in &file.env {
        let text = match value {
            toml::Value::String(s) => s.clone(),
            toml::Value::Integer(i) => i.to_string(),
            toml::Value::Boolean(b) => {
                if *b {
                    "1".into()
                } else {
                    "0".into()
                }
            }
            other => {
                return Err(format!(
                    "{}: `{name}` must be a string/integer/boolean, got {}",
                    path.display(),
                    other.type_str()
                ));
            }
        };
        out.insert(name.clone(), text);
    }
    Ok(out)
}

/// Read `<vault>/.ovp/providers.toml`'s `[budget]` section (same parse as
/// [`read_providers_file`]). Missing file / missing section → default
/// (budget unset); malformed TOML or a wrong-typed `daily_token_budget`
/// fails loud, exactly like `[env]`.
pub fn read_budget(vault_root: &Path) -> Result<BudgetSection, String> {
    let Some(file) = parse_providers_file(vault_root)? else {
        return Ok(BudgetSection::default());
    };
    Ok(file.budget)
}

/// Parse the LEGACY `<vault>/.ovp/daily.env` (KEY=VALUE lines, optional
/// `export `, optional quotes) — the migration source when providers.toml
/// is first created. Once providers.toml exists the scheduler stops
/// sourcing daily.env, so its credentials MUST be carried over or scheduled
/// runs silently lose them.
pub fn read_legacy_daily_env(vault_root: &Path) -> std::collections::BTreeMap<String, String> {
    read_env_file(&vault_root.join(".ovp/daily.env"))
}

/// Parse any env file (same rules as [`read_legacy_daily_env`]); missing or
/// unreadable → empty.
pub fn read_env_file(path: &Path) -> std::collections::BTreeMap<String, String> {
    std::fs::read_to_string(path)
        .map(|raw| parse_env_lines(&raw))
        .unwrap_or_default()
}

/// Names an env file defines that the scheduled jobs will NOT get (INV-1411).
///
/// While `.ovp/providers.toml` exists the scheduler stops sourcing the env
/// file (see `ovp_scheduler::job_shell_command`), so any variable defined only
/// there silently disappears from every scheduled run — the symptom is a
/// provider error (e.g. `MissingCredential`) far from the cause. Without
/// providers.toml the env file IS sourced, so nothing is lost. Names only:
/// callers print this, and it must never carry a credential value.
pub fn shadowed_env_names(vault_root: &Path, env_file: &Path) -> Result<Vec<String>, String> {
    if !vault_root.join(".ovp/providers.toml").is_file() {
        return Ok(Vec::new());
    }
    let Ok(raw) = std::fs::read_to_string(env_file) else {
        return Ok(Vec::new());
    };
    let providers = read_providers_file(vault_root)?;
    Ok(parse_env_lines(&raw)
        .into_keys()
        // A blank providers value still shadows the env file, and is rejected
        // at call time — so the env-file value is just as lost.
        .filter(|name| providers.get(name).is_none_or(|v| v.trim().is_empty()))
        .collect())
}

/// KEY=VALUE lines, optional `export `, optional quotes; comments, blanks and
/// non-UPPER_SNAKE names are skipped.
fn parse_env_lines(raw: &str) -> std::collections::BTreeMap<String, String> {
    let mut out = std::collections::BTreeMap::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line);
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let k = k.trim();
        if k.is_empty()
            || !k
                .chars()
                .all(|c| c.is_ascii_uppercase() || c == '_' || c.is_ascii_digit())
        {
            continue;
        }
        let v = v.trim().trim_matches('"').trim_matches('\'');
        out.insert(k.to_string(), v.to_string());
    }
    out
}

/// Rewrite `<vault>/.ovp/providers.toml` from a validated map (UPPER_SNAKE
/// names enforced; values written as TOML strings), with the standard header
/// and 0600 permissions (it holds credentials). Non-`[env]` sections
/// (`[budget]`, future sections) are PRESERVED verbatim — rebuilding only
/// `[env]` from the server settings endpoint must not silently delete the
/// operator's budget config (codex adversarial).
pub fn write_providers_file(
    vault_root: &Path,
    entries: &std::collections::BTreeMap<String, String>,
) -> Result<(), String> {
    write_file(vault_root, entries, None)
}

/// Write `[env]` = `entries`, keep every other section verbatim — except
/// that with `profiles = Some(..)` the `[llm_profiles.*]` sections are
/// regenerated from it.
fn write_file(
    vault_root: &Path,
    entries: &std::collections::BTreeMap<String, String>,
    profiles: Option<&BTreeMap<String, LlmProfile>>,
) -> Result<(), String> {
    for name in entries.keys() {
        if name.is_empty()
            || !name
                .chars()
                .all(|c| c.is_ascii_uppercase() || c == '_' || c.is_ascii_digit())
        {
            return Err(format!(
                "`{name}` is not an UPPER_SNAKE_CASE environment variable name"
            ));
        }
    }
    let dir = vault_root.join(".ovp");
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir {}: {e}", dir.display()))?;
    let path = dir.join("providers.toml");
    // Preserve every non-[env] section block from the existing file. Section
    // blocks start at a `[name]` header line; preamble comments before the
    // first header are regenerated below, so they are intentionally dropped.
    let mut kept_sections = String::new();
    if let Ok(raw) = std::fs::read_to_string(&path) {
        let mut keep = false;
        for line in raw.lines() {
            let t = line.trim();
            if t.starts_with('[') {
                // Header spelling-insensitive (`[ env ]`, `[llm_profiles . x]`,
                // quoted keys): a section kept under one spelling and
                // re-rendered under another would duplicate the table.
                let header: String = t
                    .split('#')
                    .next()
                    .unwrap_or(t)
                    .chars()
                    .filter(|c| !c.is_whitespace() && *c != '"' && *c != '\'')
                    .collect();
                keep = header != "[env]"
                    && !(profiles.is_some() && header.starts_with("[llm_profiles."));
            }
            if keep {
                kept_sections.push_str(line);
                kept_sections.push('\n');
            }
        }
    }
    let mut body = String::from(
        "# Provider configuration — read by ovp2/desktop at startup.\n\
         # Values are DEFAULTS: variables already set in the environment win.\n[env]\n",
    );
    for (k, v) in entries {
        body.push_str(&format!("{k} = {}\n", toml_escape(v)));
    }
    if !kept_sections.trim().is_empty() {
        body.push('\n');
        body.push_str(kept_sections.trim_end());
        body.push('\n');
    }
    for (id, p) in profiles.into_iter().flatten() {
        body.push_str(&format!("\n[llm_profiles.{id}]\nlabel = {}\n", toml_escape(&p.label)));
        for (k, v) in &p.env {
            if LLM_PROFILE_KEYS.contains(&k.as_str()) {
                body.push_str(&format!("{k} = {}\n", toml_escape(v)));
            }
        }
    }
    // Create with 0600 FROM THE START — a write-then-chmod leaves a window
    // where the credentials are readable under the default umask (review
    // security finding).
    #[cfg(unix)]
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        use std::os::unix::fs::PermissionsExt;
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&path)
            .map_err(|e| format!("opening {}: {e}", path.display()))?;
        // An EXISTING file keeps its old mode — enforce 0600 on it too.
        f.set_permissions(std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("chmod {}: {e}", path.display()))?;
        f.write_all(body.as_bytes())
            .map_err(|e| format!("writing {}: {e}", path.display()))?;
    }
    #[cfg(not(unix))]
    std::fs::write(&path, body).map_err(|e| format!("writing {}: {e}", path.display()))?;
    Ok(())
}

/// A TOML basic-string literal for `v` (serde_toml handles the escaping).
fn toml_escape(v: &str) -> String {
    toml::Value::String(v.to_string()).to_string()
}

/// Load `<vault>/.ovp/providers.toml` and export every `[env]` entry that is
/// not already set in the process environment. Missing file → no-op;
/// unparseable file, lowercase names, or non-scalar values → `Err` (a typo'd
/// secrets file must fail loud, not silently run unconfigured). Unknown
/// top-level SECTIONS are tolerated (forward compat with newer binaries —
/// e.g. a `[budget]` section written for `ovp2 usage`).
///
/// # Safety contract
/// Mutates the PROCESS environment (`std::env::set_var`) — call once at
/// startup before any threads exist. Both binaries do (CLI `main`, desktop
/// boot).
pub fn apply_providers_env(vault_root: &Path) -> Result<ProvidersApplied, String> {
    let path = vault_root.join(".ovp/providers.toml");
    let Some(file) = parse_providers_file(vault_root)? else {
        return Ok(ProvidersApplied::default());
    };

    // Two phases: validate + convert the ENTIRE table first, mutate second.
    // A partial apply followed by an error would leave the process with an
    // earlier entry exported; a corrected retry (long-lived desktop process)
    // would then see it as "already set" and keep the stale value until
    // restart (codex P2).
    let mut pending: Vec<(String, String)> = Vec::with_capacity(file.env.len());
    for (name, value) in &file.env {
        if name.is_empty()
            || !name
                .chars()
                .all(|c| c.is_ascii_uppercase() || c == '_' || c.is_ascii_digit())
        {
            return Err(format!(
                "{}: `{name}` is not an UPPER_SNAKE_CASE environment variable name",
                path.display()
            ));
        }
        let text = match value {
            toml::Value::String(s) => s.clone(),
            toml::Value::Integer(i) => i.to_string(),
            toml::Value::Boolean(b) => {
                if *b {
                    "1".into()
                } else {
                    "0".into()
                }
            }
            other => {
                return Err(format!(
                    "{}: `{name}` must be a string/integer/boolean, got {}",
                    path.display(),
                    other.type_str()
                ));
            }
        };
        pending.push((name.clone(), text));
    }

    let mut out = ProvidersApplied::default();
    for (name, text) in pending {
        if std::env::var_os(&name).is_some() {
            out.already_set.push(name);
            continue;
        }
        // SAFETY: single-threaded startup path (see the function contract);
        // set_var is only unsound with concurrent env readers on some
        // platforms, and both call sites run before any thread spawns.
        unsafe { std::env::set_var(&name, &text) };
        out.applied.push(name);
    }
    // Remember which variables came FROM THE FILE: a long-lived process (the
    // `ovp2 serve` portal) must re-read those from the file after a portal
    // edit or LLM switch, instead of letting its startup copy win — that
    // copy would otherwise mix a stale model with a freshly switched
    // provider. Genuine env overrides (set before start) are not listed and
    // keep winning.
    let mut seeded: Vec<String> = out.applied.clone();
    if let Ok(prev) = std::env::var(SEEDED_VAR) {
        seeded.extend(prev.split(',').filter(|s| !s.is_empty()).map(String::from));
    }
    seeded.sort_unstable();
    seeded.dedup();
    // SAFETY: same single-threaded startup contract as above.
    unsafe { std::env::set_var(SEEDED_VAR, seeded.join(",")) };
    Ok(out)
}

/// Process-env marker listing the variables [`apply_providers_env`] copied
/// from providers.toml (comma-separated).
pub const SEEDED_VAR: &str = "OVP_PROVIDERS_SEEDED";

/// Whether `name` in this process's env is only a startup copy of
/// providers.toml (so the file, not the env, is authoritative for it).
pub fn seeded_from_file(name: &str) -> bool {
    std::env::var(SEEDED_VAR)
        .map(|v| v.split(',').any(|n| n == name))
        .unwrap_or(false)
}

/// The value a long-lived process should use for `name`: a genuine env
/// override wins; a startup copy of the file does NOT (the file is re-read,
/// so edits and LLM switches apply without a restart). Blank → None.
pub fn layered_value(
    name: &str,
    file: &BTreeMap<String, String>,
    env: impl Fn(&str) -> Option<String>,
    seeded: impl Fn(&str) -> bool,
) -> Option<String> {
    let nonblank = |v: String| {
        let t = v.trim().to_string();
        (!t.is_empty()).then_some(t)
    };
    if !seeded(name)
        && let Some(v) = env(name).and_then(nonblank)
    {
        return Some(v);
    }
    file.get(name).cloned().and_then(nonblank)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_is_a_noop_and_env_wins_over_file() {
        let tmp = tempfile::tempdir().unwrap();
        assert_eq!(
            apply_providers_env(tmp.path()).unwrap(),
            ProvidersApplied::default()
        );

        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        std::fs::write(
            tmp.path().join(".ovp/providers.toml"),
            "[env]\nOVP_TEST_PROVIDERS_A = \"from-file\"\nOVP_TEST_PROVIDERS_B = 42\nOVP_TEST_PROVIDERS_C = true\n",
        )
        .unwrap();
        // Pre-set one var: the file must NOT override it.
        unsafe { std::env::set_var("OVP_TEST_PROVIDERS_A", "from-env") };
        let applied = apply_providers_env(tmp.path()).unwrap();
        assert_eq!(applied.already_set, vec!["OVP_TEST_PROVIDERS_A"]);
        assert_eq!(
            applied.applied,
            vec!["OVP_TEST_PROVIDERS_B", "OVP_TEST_PROVIDERS_C"]
        );
        assert_eq!(std::env::var("OVP_TEST_PROVIDERS_A").unwrap(), "from-env");
        assert_eq!(std::env::var("OVP_TEST_PROVIDERS_B").unwrap(), "42");
        assert_eq!(std::env::var("OVP_TEST_PROVIDERS_C").unwrap(), "1");
        for k in [
            "OVP_TEST_PROVIDERS_A",
            "OVP_TEST_PROVIDERS_B",
            "OVP_TEST_PROVIDERS_C",
        ] {
            unsafe { std::env::remove_var(k) };
        }
    }

    #[test]
    fn corrupt_lowercase_and_nonscalar_fail_loud() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join(".ovp/providers.toml");
        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        std::fs::write(&p, "not toml [").unwrap();
        assert!(apply_providers_env(tmp.path()).is_err());
        std::fs::write(&p, "[env]\nlowercase = \"x\"\n").unwrap();
        assert!(apply_providers_env(tmp.path()).is_err());
        std::fs::write(&p, "[env]\nOVP_X = [1, 2]\n").unwrap();
        assert!(apply_providers_env(tmp.path()).is_err());
    }

    /// Forward compat: a providers.toml carrying sections a binary doesn't
    /// know (e.g. `[budget]` written for a newer ovp2, or any future section)
    /// must NOT break older readers of the same vault — unknown sections are
    /// ignored, while format/type errors (above) stay fail loud.
    #[test]
    fn unknown_sections_are_tolerated() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join(".ovp/providers.toml");
        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        std::fs::write(&p, "[llm]\nkey = \"x\"\n[future_thing]\nn = 1\n").unwrap();
        assert!(apply_providers_env(tmp.path()).is_ok());
        assert!(read_providers_file(tmp.path()).is_ok());
        assert_eq!(read_budget(tmp.path()).unwrap(), BudgetSection::default());
    }

    #[test]
    fn budget_section_parses_via_both_loaders() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join(".ovp/providers.toml");
        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        std::fs::write(
            &p,
            "[env]\nOVP_TEST_BUDGET_ENV = \"x\"\n\n[budget]\ndaily_token_budget = 200000\n",
        )
        .unwrap();
        assert_eq!(
            read_budget(tmp.path()).unwrap().daily_token_budget,
            Some(200000)
        );
        // The `[env]` loader sees the same file fine with `[budget]` present.
        assert_eq!(
            read_providers_file(tmp.path()).unwrap().get("OVP_TEST_BUDGET_ENV"),
            Some(&"x".to_string())
        );
        // Missing file → unset, not an error.
        let empty = tempfile::tempdir().unwrap();
        assert_eq!(read_budget(empty.path()).unwrap().daily_token_budget, None);
    }

    #[test]
    fn malformed_or_mistyped_budget_fails_loud() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join(".ovp/providers.toml");
        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        std::fs::write(&p, "[budget]\ndaily_token_budget = \"lots\"\n").unwrap();
        assert!(read_budget(tmp.path()).is_err());
        assert!(apply_providers_env(tmp.path()).is_err());
        std::fs::write(&p, "[budget]\ndaily_token_budget = -5\n").unwrap();
        assert!(read_budget(tmp.path()).is_err());
    }

    /// The server settings endpoint rewrites `[env]` through
    /// `write_providers_file` — `[budget]` (and any future section) must
    /// survive that rewrite verbatim (codex adversarial: silent deletion).
    #[test]
    fn write_providers_file_preserves_non_env_sections() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join(".ovp/providers.toml");
        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        std::fs::write(
            &p,
            "[env]\nOLD_KEY = \"old\"\n\n[budget]\ndaily_token_budget = 200000\n",
        )
        .unwrap();
        let entries = std::collections::BTreeMap::from([("NEW_KEY".to_string(), "v".to_string())]);
        write_providers_file(tmp.path(), &entries).unwrap();
        let raw = std::fs::read_to_string(&p).unwrap();
        assert!(raw.contains("NEW_KEY"), "{raw}");
        assert!(!raw.contains("OLD_KEY"), "[env] was replaced: {raw}");
        assert!(raw.contains("[budget]"), "[budget] preserved: {raw}");
        assert!(raw.contains("daily_token_budget = 200000"), "{raw}");
        // And it still parses through both loaders.
        assert_eq!(
            read_budget(tmp.path()).unwrap().daily_token_budget,
            Some(200000)
        );
        assert_eq!(
            read_providers_file(tmp.path()).unwrap().get("NEW_KEY"),
            Some(&"v".to_string())
        );
    }

    fn vault_with(providers: Option<&str>, env: &str) -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".ovp")).unwrap();
        if let Some(p) = providers {
            std::fs::write(tmp.path().join(".ovp/providers.toml"), p).unwrap();
        }
        std::fs::write(tmp.path().join(".ovp/daily.env"), env).unwrap();
        tmp
    }

    /// INV-1411: with providers.toml present the scheduler stops sourcing the
    /// env file, so every name only the env file defines is lost — by NAME,
    /// never by value.
    #[test]
    fn shadowed_env_names_lists_only_names_missing_from_providers() {
        let env = "# comment\nexport PINBOARD_TOKEN=\"secret-pin\"\nTYPESAFE_TOKEN='secret-ts'\n\nlower=ignored\n";
        let tmp = vault_with(Some("[env]\nPINBOARD_TOKEN = \"p\"\n"), env);
        let env_file = tmp.path().join(".ovp/daily.env");
        let names = shadowed_env_names(tmp.path(), &env_file).unwrap();
        assert_eq!(names, vec!["TYPESAFE_TOKEN".to_string()]);
        assert!(!format!("{names:?}").contains("secret"));
    }

    #[test]
    fn nothing_is_shadowed_without_providers_or_when_both_agree() {
        let tmp = vault_with(None, "TYPESAFE_TOKEN=x\n");
        let env_file = tmp.path().join(".ovp/daily.env");
        assert!(shadowed_env_names(tmp.path(), &env_file).unwrap().is_empty());
        std::fs::write(tmp.path().join(".ovp/providers.toml"), "[env]\nTYPESAFE_TOKEN = \"y\"\n").unwrap();
        assert!(shadowed_env_names(tmp.path(), &env_file).unwrap().is_empty());
        // A missing env file shadows nothing.
        assert!(shadowed_env_names(tmp.path(), &tmp.path().join("nope.env")).unwrap().is_empty());
        // A blank placeholder in providers.toml still hides the real value.
        std::fs::write(tmp.path().join(".ovp/providers.toml"), "[env]\nTYPESAFE_TOKEN = \" \"\n").unwrap();
        assert_eq!(shadowed_env_names(tmp.path(), &env_file).unwrap(), vec!["TYPESAFE_TOKEN".to_string()]);
    }

    #[test]
    fn shadow_check_fails_loud_on_malformed_providers() {
        let tmp = vault_with(Some("[env\n"), "A=1\n");
        let env_file = tmp.path().join(".ovp/daily.env");
        assert!(shadowed_env_names(tmp.path(), &env_file).is_err());
    }

    /// The toml error's Display quotes the offending source line — on a
    /// credential line that is the secret, and these errors reach stderr
    /// (scheduler logs), the portal and doctor. Location + reason only.
    #[test]
    fn parse_errors_never_quote_the_file() {
        let tmp = vault_with(Some("[env]\nOK = \"fine\"\nTYPESAFE_TOKEN = \"sk-leak\n"), "");
        for err in [
            read_providers_file(tmp.path()).unwrap_err(),
            apply_providers_env(tmp.path()).unwrap_err(),
            read_budget(tmp.path()).unwrap_err(),
        ] {
            assert!(!err.contains("sk-leak"), "{err}");
            assert!(err.contains("line 3"), "{err}");
        }
    }

    fn env_of(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    #[test]
    fn layered_value_rereads_startup_copies_but_honours_real_overrides() {
        let file = env_of(&[("OVP_LLM_MODEL", "gpt-6-luna"), ("OVP_LLM_PROVIDER", "openai")]);
        // The process env still holds the STARTUP copy of the old setup.
        let env = |k: &str| match k {
            "OVP_LLM_MODEL" => Some("MiniMax-M3".to_string()),
            "ANTHROPIC_API_KEY" => Some("sk-old".to_string()),
            "OPENAI_BASE_URL" => Some("https://gw/v1/responses".to_string()),
            _ => None,
        };
        let seeded = |k: &str| k == "OVP_LLM_MODEL" || k == "ANTHROPIC_API_KEY";
        assert_eq!(layered_value("OVP_LLM_MODEL", &file, env, seeded).as_deref(), Some("gpt-6-luna"));
        assert_eq!(layered_value("OVP_LLM_PROVIDER", &file, env, seeded).as_deref(), Some("openai"));
        // Removed from the file after the switch → gone, not the stale copy.
        assert_eq!(layered_value("ANTHROPIC_API_KEY", &file, env, seeded), None);
        // Set in the env BEFORE start (not seeded) → a real override, wins.
        assert_eq!(
            layered_value("OPENAI_BASE_URL", &file, env, seeded).as_deref(),
            Some("https://gw/v1/responses")
        );
    }

    #[test]
    fn llm_profiles_round_trip_activate_and_survive_env_saves() {
        let dir = tempfile::tempdir().unwrap();
        let vault = dir.path();
        // An existing file: MiniMax live in [env] plus unrelated tokens and a budget.
        std::fs::create_dir_all(vault.join(".ovp")).unwrap();
        std::fs::write(
            vault.join(".ovp/providers.toml"),
            "[env]\nANTHROPIC_API_KEY = \"sk-mm\"\n\
             ANTHROPIC_BASE_URL = \"https://api.minimaxi.com/anthropic/v1/messages\"\n\
             OVP_LLM_MODEL = \"MiniMax-M3\"\nOVP_LLM_NO_PROXY = \"1\"\n\
             GITHUB_TOKEN = \"ghp\"\n\n[budget]\ndaily_token_budget = 5000\n",
        )
        .unwrap();
        let minimax = LlmProfile {
            label: "MiniMax M3".into(),
            env: env_of(&[
                ("ANTHROPIC_API_KEY", "sk-mm"),
                ("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic/v1/messages"),
                ("OVP_LLM_MODEL", "MiniMax-M3"),
                ("OVP_LLM_NO_PROXY", "1"),
            ]),
        };
        let openai = LlmProfile {
            label: "OpenAI \"luna\"".into(),
            env: env_of(&[
                ("OVP_LLM_PROVIDER", "openai"),
                ("OPENAI_API_KEY", "sk-oai"),
                ("OVP_LLM_MODEL", "gpt-6-luna"),
            ]),
        };
        let profiles = BTreeMap::from([("minimax".to_string(), minimax), ("openai".to_string(), openai)]);
        write_llm_profiles(vault, &profiles).unwrap();
        assert_eq!(read_llm_profiles(vault).unwrap(), profiles, "round trip, quotes escaped");
        let env = read_providers_file(vault).unwrap();
        assert_eq!(active_llm_profile(&env, &profiles), Some("minimax"), "derived, not stored");

        activate_llm_profile(vault, "openai").unwrap();
        let env = read_providers_file(vault).unwrap();
        assert_eq!(env.get("OVP_LLM_PROVIDER").map(String::as_str), Some("openai"));
        assert_eq!(env.get("OVP_LLM_MODEL").map(String::as_str), Some("gpt-6-luna"));
        assert!(!env.contains_key("ANTHROPIC_BASE_URL"), "the old setup's LLM vars are cleared");
        assert!(!env.contains_key("OVP_LLM_NO_PROXY"));
        assert_eq!(env.get("GITHUB_TOKEN").map(String::as_str), Some("ghp"), "unrelated tokens stay");
        assert_eq!(active_llm_profile(&env, &profiles), Some("openai"));
        assert_eq!(read_budget(vault).unwrap().daily_token_budget, Some(5000), "[budget] kept");

        // An ordinary [env] save (portal token edit) keeps the profiles.
        let mut env = env;
        env.insert("PINBOARD_TOKEN".into(), "u:1".into());
        write_providers_file(vault, &env).unwrap();
        assert_eq!(read_llm_profiles(vault).unwrap(), profiles);

        // Equivalent spellings still match: explicit default provider and a
        // false NO_PROXY in [env] vs the canonical (absent) form in a profile.
        let mut spelled = env.clone();
        spelled.insert("OVP_LLM_NO_PROXY".into(), "0".into());
        let plain = BTreeMap::from([(
            "p".to_string(),
            LlmProfile { label: "p".into(), env: llm_env(&env) },
        )]);
        assert_eq!(active_llm_profile(&spelled, &plain), Some("p"));
        let mut ant = env_of(&[("OVP_LLM_PROVIDER", "Anthropic"), ("ANTHROPIC_API_KEY", "k")]);
        let ant_profile = BTreeMap::from([(
            "a".to_string(),
            LlmProfile { label: "a".into(), env: env_of(&[("ANTHROPIC_API_KEY", "k")]) },
        )]);
        assert_eq!(active_llm_profile(&ant, &ant_profile), Some("a"));
        ant.insert("OVP_LLM_NO_PROXY".into(), "true".into());
        assert_eq!(active_llm_profile(&ant, &ant_profile), None, "a real difference still counts");

        // Hand-editing [env] away from every profile → no active profile.
        env.insert("OVP_LLM_MODEL".into(), "gpt-6-sol".into());
        assert_eq!(active_llm_profile(&env, &profiles), None);

        // A hand-written header with spaces/quotes is replaced, not duplicated.
        let raw = std::fs::read_to_string(vault.join(".ovp/providers.toml")).unwrap();
        std::fs::write(
            vault.join(".ovp/providers.toml"),
            raw.replace("[llm_profiles.minimax]", "[ llm_profiles . \"minimax\" ]"),
        )
        .unwrap();
        write_llm_profiles(vault, &profiles).unwrap();
        assert_eq!(read_llm_profiles(vault).unwrap(), profiles, "still parses: no duplicate table");

        assert!(activate_llm_profile(vault, "nope").is_err());
        let bad = BTreeMap::from([("Bad Id".to_string(), LlmProfile::default())]);
        assert!(write_llm_profiles(vault, &bad).is_err());
    }
}
