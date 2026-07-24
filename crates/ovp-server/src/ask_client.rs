//! Providers-aware live client factory for `POST /api/ask`.
//!
//! Reads `.ovp/providers.toml` on **every** factory call (env still wins over
//! the file) so System → LLM Provider save takes effect without restarting the
//! portal. Does **not** call `std::env::set_var` — safe for multi-threaded hosts
//! (desktop Tauri, long-lived `ovp2 serve`).

use std::path::{Path, PathBuf};

use crate::AskClientFactory;

/// Stable error string when no API key is available — mapped to HTTP 503
/// `llm_not_configured` by the ask handler.
#[cfg(feature = "anthropic")]
pub use ovp_llm::LLM_NOT_CONFIGURED;

#[cfg(not(feature = "anthropic"))]
pub const LLM_NOT_CONFIGURED: &str = "llm_not_configured";

/// Whether a non-empty `ANTHROPIC_API_KEY` is available from the process
/// environment or `<vault>/.ovp/providers.toml`. Used by `/api/settings`
/// (`llm_configured`) so the System page reflects the product config surface.
pub fn api_key_configured(vault_root: &Path) -> bool {
    if std::env::var("ANTHROPIC_API_KEY")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false)
    {
        return true;
    }
    match ovp_domain::providers::read_providers_file(vault_root) {
        Ok(map) => map
            .get("ANTHROPIC_API_KEY")
            .map(|v| !v.trim().is_empty())
            .unwrap_or(false),
        Err(_) => false,
    }
}

/// Live-transport factory for ask: `Some` when built with `--features
/// anthropic`, else `None` (ask answers 503 feature-missing).
///
/// The factory re-reads providers.toml each call. Missing key →
/// [`LLM_NOT_CONFIGURED`]; invalid base URL / tokens → descriptive Err.
pub fn providers_ask_client_factory(vault_root: PathBuf) -> Option<AskClientFactory> {
    #[cfg(feature = "anthropic")]
    {
        use std::sync::Arc;
        Some(Arc::new(move || build_ask_client(&vault_root)))
    }
    #[cfg(not(feature = "anthropic"))]
    {
        let _ = vault_root;
        None
    }
}

/// Env-over-file lookup matching `apply_providers_env` semantics without
/// mutating the process environment.
#[cfg(feature = "anthropic")]
fn provider_lookup(
    file: &std::collections::BTreeMap<String, String>,
) -> impl Fn(&str) -> Option<String> + '_ {
    move |name: &str| {
        if let Ok(v) = std::env::var(name) {
            let t = v.trim();
            if !t.is_empty() {
                return Some(t.to_string());
            }
        }
        file.get(name)
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    }
}

#[cfg(feature = "anthropic")]
fn build_ask_client(vault_root: &Path) -> Result<Box<dyn ovp_llm::ModelClient>, String> {
    use ovp_domain::ARTICLE_PROMPT_ID;
    use ovp_llm::{build_recording_live_client, resolve_api_key, LiveClientConfig};

    let file = ovp_domain::providers::read_providers_file(vault_root)?;
    let lookup = provider_lookup(&file);
    let key = resolve_api_key(&lookup)?;
    let cfg = LiveClientConfig::from_lookup(&lookup)?;
    let cache_dir = vault_root.join(".ovp/cassettes/ask");
    build_recording_live_client(&key, &cfg, &cache_dir, ARTICLE_PROMPT_ID)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_vault(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "ovp-server-ask-client-{}-{name}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(".ovp")).unwrap();
        dir
    }

    #[test]
    fn api_key_configured_reads_providers_file() {
        let vault = temp_vault("key-file");
        assert!(!api_key_configured(&vault));
        std::fs::write(
            vault.join(".ovp/providers.toml"),
            "[env]\nANTHROPIC_API_KEY = \"sk-from-file\"\n",
        )
        .unwrap();
        assert!(api_key_configured(&vault));
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[test]
    fn factory_without_anthropic_is_none_or_missing_key() {
        let vault = temp_vault("no-key");
        let factory = providers_ask_client_factory(vault.clone());
        #[cfg(feature = "anthropic")]
        {
            let f = factory.expect("anthropic feature installs factory");
            let err = match f() {
                Ok(_) => panic!("expected llm_not_configured without a key"),
                Err(e) => e,
            };
            assert_eq!(err, LLM_NOT_CONFIGURED);
        }
        #[cfg(not(feature = "anthropic"))]
        {
            assert!(factory.is_none());
        }
        let _ = std::fs::remove_dir_all(&vault);
    }

    #[cfg(feature = "anthropic")]
    #[test]
    fn factory_reads_key_from_providers_without_set_var() {
        let vault = temp_vault("from-file");
        std::fs::write(
            vault.join(".ovp/providers.toml"),
            "[env]\nANTHROPIC_API_KEY = \"sk-test-key\"\nOVP_LLM_MODEL = \"test-model\"\n",
        )
        .unwrap();
        // Ensure we are NOT depending on process env for the key.
        let had_env = std::env::var_os("ANTHROPIC_API_KEY");
        // SAFETY: test-only, single-threaded test binary section.
        unsafe { std::env::remove_var("ANTHROPIC_API_KEY") };

        let factory = providers_ask_client_factory(vault.clone()).expect("factory");
        // Building the client must succeed (no network call until `.call()`).
        if let Err(e) = factory() {
            panic!("unexpected factory error: {e}");
        }

        if let Some(v) = had_env {
            unsafe { std::env::set_var("ANTHROPIC_API_KEY", v) };
        }
        let _ = std::fs::remove_dir_all(&vault);
    }
}
