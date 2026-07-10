//! Shared `ModelClient` construction for the CLI commands (`interpret-article`,
//! `run-cycle`). Replay is offline + HTTP-free; live is the capture path behind
//! the `anthropic` feature.

use std::path::Path;

use ovp_domain::ARTICLE_PROMPT_ID;
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};

use crate::CliError;

/// Selects which `ModelClient` impl the CLI wires into `LLMInvoker`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientKind {
    /// `CachedModelClient(NeverCallsClient, ReplayOnly)` — looks up canned
    /// replies from `--cache-dir`; never hits the network. The default.
    Replay,
    /// `CachedModelClient(AnthropicBlockingClient, Record)` — calls the live API
    /// and captures each reply into `--cache-dir`. Requires `--features
    /// anthropic` and `ANTHROPIC_API_KEY`; errors with guidance otherwise.
    Live,
}

/// Build the `ModelClient` for the requested mode. The per-request
/// `cache_namespace` set by each prompt builder selects the right cassette dir,
/// so this single client serves both article and paper prompts.
pub fn build_client(kind: ClientKind, cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    match kind {
        ClientKind::Replay => {
            let cached =
                CachedModelClient::new(NeverCallsClient, cache_dir, ARTICLE_PROMPT_ID, CacheMode::ReplayOnly)
                    .map_err(|e| {
                        CliError::Io(format!("opening cache dir `{}`: {e}", cache_dir.display()))
                    })?;
            Ok(Box::new(cached))
        }
        ClientKind::Live => build_live_client(cache_dir),
    }
}

/// Validated config for an Anthropic-**compatible** live provider (e.g. MiniMax),
/// read from the environment. All fields are optional — absent leaves the real-
/// Anthropic defaults baked into the client. A var that is *present but invalid*
/// is a hard error (never silently ignored), so a typo'd budget fails loud.
///
/// Env vars (documented in `docs/live-capture.md`):
/// - `ANTHROPIC_BASE_URL` — the FULL Messages endpoint (e.g.
///   `https://api.minimaxi.com/anthropic/v1/messages`), NOT a root URL.
/// - `OVP_LLM_MODEL` — the provider's model name (`claude-*` won't work on a
///   non-Anthropic provider).
/// - `OVP_LLM_MAX_TOKENS` — positive integer; reasoning/thinking models need
///   headroom beyond the domain default to emit text after their thinking blocks.
/// - `OVP_LLM_NO_PROXY` — boolean; bypass the ambient `HTTP(S)_PROXY` for the
///   live HTTP client ONLY (does not mutate the process environment).
#[cfg(feature = "anthropic")]
#[derive(Debug, Default, PartialEq, Eq)]
pub struct LiveClientConfig {
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub max_tokens: Option<u32>,
    pub no_proxy: bool,
    /// Request timeout in seconds. `None` means "use the OVP default
    /// (180s)"; reasoning/thinking models can spend 30-90s on a single
    /// response, and a too-short timeout surfaces as a useless
    /// `CallError::Transport` with no underlying chain.
    pub timeout_secs: Option<u64>,
}

#[cfg(feature = "anthropic")]
impl LiveClientConfig {
    /// Parse + validate from the process environment.
    pub fn from_env() -> Result<Self, String> {
        Self::from_lookup(|k| std::env::var(k).ok())
    }

    /// Testable core: `lookup(name)` yields a var's raw value (or `None`).
    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let nonempty = |k: &str| lookup(k).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());

        let max_tokens = match nonempty("OVP_LLM_MAX_TOKENS") {
            None => None,
            Some(raw) => {
                let n: u32 = raw
                    .parse()
                    .map_err(|_| format!("OVP_LLM_MAX_TOKENS must be a positive integer, got `{raw}`"))?;
                if n == 0 {
                    return Err("OVP_LLM_MAX_TOKENS must be greater than 0".to_string());
                }
                Some(n)
            }
        };
        let no_proxy = match nonempty("OVP_LLM_NO_PROXY") {
            None => false,
            Some(raw) => match raw.to_ascii_lowercase().as_str() {
                "1" | "true" | "yes" | "on" => true,
                "0" | "false" | "no" | "off" => false,
                other => {
                    return Err(format!(
                        "OVP_LLM_NO_PROXY must be a boolean (1/0/true/false), got `{other}`"
                    ))
                }
            },
        };
        let base_url = match nonempty("ANTHROPIC_BASE_URL") {
            None => None,
            Some(u) => {
                validate_messages_endpoint(&u)?;
                Some(u)
            }
        };
        let timeout_secs = match nonempty("OVP_LLM_TIMEOUT_SECS") {
            None => None,
            Some(raw) => {
                let n: u64 = raw
                    .parse()
                    .map_err(|_| format!("OVP_LLM_TIMEOUT_SECS must be a non-negative integer, got `{raw}`"))?;
                Some(n)
            }
        };
        Ok(Self { base_url, model: nonempty("OVP_LLM_MODEL"), max_tokens, no_proxy, timeout_secs })
    }
}

/// Validate that `ANTHROPIC_BASE_URL` is the FULL Messages endpoint (its path
/// ends in `/messages`), not a root/base URL — so a misconfig fails loud at
/// startup rather than on the first live call. Lightweight (no `url` dep): the
/// client POSTs directly to this URL, so the contract is just "http(s) + a
/// `/messages` path".
#[cfg(feature = "anthropic")]
fn validate_messages_endpoint(url: &str) -> Result<(), String> {
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err(format!("ANTHROPIC_BASE_URL must be an http(s) URL, got `{url}`"));
    }
    let after_scheme = url.split_once("://").map(|x| x.1).unwrap_or("");
    // The path starts at the first '/' after the host; strip any query/fragment
    // and a trailing slash before checking the final segment.
    let path = match after_scheme.find('/') {
        Some(i) => &after_scheme[i..],
        None => "",
    };
    let path = path.split(['?', '#']).next().unwrap_or(path).trim_end_matches('/');
    if !path.ends_with("/messages") {
        return Err(format!(
            "ANTHROPIC_BASE_URL must be the full Messages endpoint ending in `/messages` \
             (e.g. https://api.anthropic.com/v1/messages or \
             https://api.minimaxi.com/anthropic/v1/messages), got `{url}`"
        ));
    }
    Ok(())
}

/// Bounded retry budget for transient live failures (transport / 429 / 5xx).
#[cfg(feature = "anthropic")]
const LIVE_MAX_RETRIES: u32 = 2;
#[cfg(feature = "anthropic")]
const LIVE_RETRY_BACKOFF: std::time::Duration = std::time::Duration::from_millis(400);
/// M20: on `BudgetExhausted` (a thinking model that used its whole budget and
/// emitted no text), retry ONCE at this multiple of the effective budget,
/// capped. Base used when no `OVP_LLM_MAX_TOKENS` override is set.
#[cfg(feature = "anthropic")]
const LIVE_BUDGET_ESCALATION_FACTOR: u32 = 2;
#[cfg(feature = "anthropic")]
const LIVE_BUDGET_ESCALATION_CAP: u32 = 96_000;
#[cfg(feature = "anthropic")]
const LIVE_BUDGET_BASE_DEFAULT: u32 = 16_000;

#[cfg(feature = "anthropic")]
fn build_live_client(cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    use ovp_llm::{AnthropicBlockingClient, BudgetEscalatingModelClient, RetryingModelClient};

    let cfg = LiveClientConfig::from_env()
        .map_err(|e| CliError::Io(format!("live provider config: {e}")))?;
    let mut live = AnthropicBlockingClient::from_env()
        .map_err(|e| CliError::Io(format!("anthropic client: {e}")))?;
    if let Some(url) = cfg.base_url {
        live = live.with_base_url(url);
    }
    if let Some(model) = cfg.model {
        live = live.with_model_override(model);
    }
    if let Some(mt) = cfg.max_tokens {
        live = live.with_max_tokens(mt);
    }
    if cfg.no_proxy {
        live = live.with_no_proxy();
    }
    if let Some(secs) = cfg.timeout_secs {
        live = live.with_timeout(secs);
    }
    // Bounded retry on transient transport/429/5xx faults, INSIDE the cache so
    // a cache hit never retries and only a finally-successful live call records.
    let retrying = RetryingModelClient::new(live, LIVE_MAX_RETRIES, LIVE_RETRY_BACKOFF);
    // M20: one higher-budget retry on thinking-budget exhaustion, OUTSIDE retry
    // (BudgetExhausted is non-transient) and INSIDE the cache (records once).
    let escalated = cfg
        .max_tokens
        .unwrap_or(LIVE_BUDGET_BASE_DEFAULT)
        .saturating_mul(LIVE_BUDGET_ESCALATION_FACTOR)
        .min(LIVE_BUDGET_ESCALATION_CAP);
    let escalating = BudgetEscalatingModelClient::new(retrying, escalated);
    let cached = CachedModelClient::new(escalating, cache_dir, ARTICLE_PROMPT_ID, CacheMode::Record)
        .map_err(|e| CliError::Io(format!("opening cache dir `{}`: {e}", cache_dir.display())))?;
    Ok(Box::new(cached))
}

#[cfg(not(feature = "anthropic"))]
fn build_live_client(_cache_dir: &Path) -> Result<Box<dyn ModelClient>, CliError> {
    Err(CliError::Io(
        "--client live requires building with `--features anthropic` and a set \
         ANTHROPIC_API_KEY; the default build is replay-only. Rebuild: \
         `cargo run -p ovp-cli --features anthropic -- ... --client live`"
            .into(),
    ))
}

#[cfg(all(test, feature = "anthropic"))]
mod live_config_tests {
    use super::LiveClientConfig;
    use std::collections::HashMap;

    fn cfg(pairs: &[(&str, &str)]) -> Result<LiveClientConfig, String> {
        let map: HashMap<String, String> =
            pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        LiveClientConfig::from_lookup(|k| map.get(k).cloned())
    }

    #[test]
    fn empty_env_is_all_defaults() {
        assert_eq!(cfg(&[]).unwrap(), LiveClientConfig::default());
    }

    #[test]
    fn parses_full_minimax_config() {
        let c = cfg(&[
            ("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic/v1/messages"),
            ("OVP_LLM_MODEL", "MiniMax-M2"),
            ("OVP_LLM_MAX_TOKENS", "24000"),
            ("OVP_LLM_NO_PROXY", "1"),
            ("OVP_LLM_TIMEOUT_SECS", "300"),
        ])
        .unwrap();
        assert_eq!(c.base_url.as_deref(), Some("https://api.minimaxi.com/anthropic/v1/messages"));
        assert_eq!(c.model.as_deref(), Some("MiniMax-M2"));
        assert_eq!(c.max_tokens, Some(24000));
        assert!(c.no_proxy);
        assert_eq!(c.timeout_secs, Some(300));
    }

    #[test]
    fn timeout_secs_omitted_means_use_default() {
        let c = cfg(&[]).unwrap();
        assert_eq!(c.timeout_secs, None, "absent OVP_LLM_TIMEOUT_SECS falls back to OVP default");
    }

    #[test]
    fn timeout_secs_zero_is_an_explicit_choice() {
        // 0 disables the timeout — local dev against a mock. Must parse,
        // distinct from "absent".
        let c = cfg(&[("OVP_LLM_TIMEOUT_SECS", "0")]).unwrap();
        assert_eq!(c.timeout_secs, Some(0));
    }

    #[test]
    fn blank_values_are_treated_as_unset() {
        let c = cfg(&[("ANTHROPIC_BASE_URL", "   "), ("OVP_LLM_MODEL", "")]).unwrap();
        assert_eq!(c.base_url, None);
        assert_eq!(c.model, None);
    }

    #[test]
    fn base_url_must_be_a_full_messages_endpoint() {
        // Root / base URLs are rejected at startup (the doc promise).
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")]).is_err());
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.anthropic.com")]).is_err());
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "api.anthropic.com/v1/messages")]).is_err(), "missing scheme");
        // Full Messages endpoints (with/without trailing slash) are accepted.
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/messages")]).is_ok());
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic/v1/messages/")]).is_ok());
    }

    #[test]
    fn invalid_max_tokens_fails_loud() {
        assert!(cfg(&[("OVP_LLM_MAX_TOKENS", "lots")]).is_err());
        assert!(cfg(&[("OVP_LLM_MAX_TOKENS", "0")]).is_err());
        assert!(cfg(&[("OVP_LLM_MAX_TOKENS", "-5")]).is_err());
    }

    #[test]
    fn invalid_no_proxy_fails_loud() {
        assert!(cfg(&[("OVP_LLM_NO_PROXY", "maybe")]).is_err());
        // recognized falsey value is fine
        assert!(!cfg(&[("OVP_LLM_NO_PROXY", "false")]).unwrap().no_proxy);
    }

    #[test]
    fn invalid_timeout_secs_fails_loud() {
        assert!(cfg(&[("OVP_LLM_TIMEOUT_SECS", "long")]).is_err());
        // negative integers are not a thing for u64; the parse error catches them
        assert!(cfg(&[("OVP_LLM_TIMEOUT_SECS", "-5")]).is_err());
    }
}
