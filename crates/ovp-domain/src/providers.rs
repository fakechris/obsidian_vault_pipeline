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
//! ```

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProvidersFile {
    #[serde(default)]
    env: BTreeMap<String, toml::Value>,
}

/// What `apply_providers_env` did, for the caller's (stderr) log line.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ProvidersApplied {
    /// Variables set from the file (they were absent from the environment).
    pub applied: Vec<String>,
    /// Variables skipped because the environment already had them.
    pub already_set: Vec<String>,
}

/// Read `<vault>/.ovp/providers.toml`'s `[env]` table as strings (the same
/// scalar coercion `apply_providers_env` uses). Missing file → empty map;
/// malformed → Err.
pub fn read_providers_file(
    vault_root: &Path,
) -> Result<std::collections::BTreeMap<String, String>, String> {
    let path = vault_root.join(".ovp/providers.toml");
    let raw = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Default::default()),
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    let file: ProvidersFile =
        toml::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))?;
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

/// Rewrite `<vault>/.ovp/providers.toml` from a validated map (UPPER_SNAKE
/// names enforced; values written as TOML strings), with the standard header
/// and 0600 permissions (it holds credentials).
pub fn write_providers_file(
    vault_root: &Path,
    entries: &std::collections::BTreeMap<String, String>,
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
    let mut body = String::from(
        "# Provider configuration — read by ovp2/desktop at startup.\n\
         # Values are DEFAULTS: variables already set in the environment win.\n[env]\n",
    );
    for (k, v) in entries {
        body.push_str(&format!("{k} = {}\n", toml_escape(v)));
    }
    std::fs::write(&path, body).map_err(|e| format!("writing {}: {e}", path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("chmod {}: {e}", path.display()))?;
    }
    Ok(())
}

/// A TOML basic-string literal for `v` (serde_toml handles the escaping).
fn toml_escape(v: &str) -> String {
    toml::Value::String(v.to_string()).to_string()
}

/// Load `<vault>/.ovp/providers.toml` and export every `[env]` entry that is
/// not already set in the process environment. Missing file → no-op;
/// unparseable file, unknown top-level keys, lowercase names, or non-scalar
/// values → `Err` (a typo'd secrets file must fail loud, not silently run
/// unconfigured).
///
/// # Safety contract
/// Mutates the PROCESS environment (`std::env::set_var`) — call once at
/// startup before any threads exist. Both binaries do (CLI `main`, desktop
/// boot).
pub fn apply_providers_env(vault_root: &Path) -> Result<ProvidersApplied, String> {
    let path = vault_root.join(".ovp/providers.toml");
    let raw = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(ProvidersApplied::default());
        }
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    let file: ProvidersFile =
        toml::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))?;

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
    Ok(out)
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
        // Unknown top-level table — probably a misplaced section.
        std::fs::write(&p, "[llm]\nkey = \"x\"\n").unwrap();
        assert!(apply_providers_env(tmp.path()).is_err());
    }
}
