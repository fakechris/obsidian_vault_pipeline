//! Live Anthropic-compatible client construction (feature = `anthropic`).
//!
//! Shared by the CLI (`--client live`), `ovp2 serve` / MCP ask factories, and
//! the desktop in-process portal. Config is resolved via an injected lookup so
//! callers can read process env, `.ovp/providers.toml`, or a merge of both —
//! without mutating the process environment (important for multi-threaded
//! hosts such as Tauri).

use std::path::Path;
use std::time::Duration;

use crate::anthropic::AnthropicBlockingClient;
use crate::cache::{CacheMode, CachedModelClient};
use crate::client::{BudgetEscalatingModelClient, ModelClient, RetryingModelClient};

/// Stable machine-readable error when no API key is available. Servers map
/// this to HTTP 503 `llm_not_configured` so the portal can show setup guidance
/// instead of a generic 502.
pub const LLM_NOT_CONFIGURED: &str = "llm_not_configured";

/// Validated config for an Anthropic-**compatible** live provider (e.g. MiniMax).
/// All fields are optional — absent leaves the real-Anthropic defaults baked
/// into the client. A var that is *present but invalid* is a hard error (never
/// silently ignored), so a typo'd budget fails loud.
///
/// Env / providers.toml keys (documented in `docs/live-capture.md`):
/// - `ANTHROPIC_BASE_URL` — the FULL Messages endpoint (e.g.
///   `https://api.minimaxi.com/anthropic/v1/messages`), NOT a root URL.
/// - `OVP_LLM_MODEL` — the provider's model name.
/// - `OVP_LLM_MAX_TOKENS` — positive integer; reasoning/thinking models need
///   headroom beyond the domain default to emit text after thinking blocks.
/// - `OVP_LLM_NO_PROXY` — boolean; bypass ambient `HTTP(S)_PROXY` for the live
///   HTTP client ONLY.
/// - `OVP_LLM_TIMEOUT_SECS` — non-negative integer request timeout.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LiveClientConfig {
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub max_tokens: Option<u32>,
    pub no_proxy: bool,
    /// Request timeout in seconds. `None` means "use the OVP default (180s)".
    pub timeout_secs: Option<u64>,
}

impl LiveClientConfig {
    /// Parse + validate from the process environment.
    pub fn from_env() -> Result<Self, String> {
        Self::from_lookup(|k| std::env::var(k).ok())
    }

    /// Testable core: `lookup(name)` yields a var's raw value (or `None`).
    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let nonempty =
            |k: &str| lookup(k).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());

        let max_tokens = match nonempty("OVP_LLM_MAX_TOKENS") {
            None => None,
            Some(raw) => {
                let n: u32 = raw.parse().map_err(|_| {
                    format!("OVP_LLM_MAX_TOKENS must be a positive integer, got `{raw}`")
                })?;
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
                let n: u64 = raw.parse().map_err(|_| {
                    format!("OVP_LLM_TIMEOUT_SECS must be a non-negative integer, got `{raw}`")
                })?;
                Some(n)
            }
        };
        Ok(Self {
            base_url,
            model: nonempty("OVP_LLM_MODEL"),
            max_tokens,
            no_proxy,
            timeout_secs,
        })
    }
}

/// Validate that `ANTHROPIC_BASE_URL` is the FULL Messages endpoint (path ends
/// in `/messages`), not a root/base URL — so a misconfig fails loud at startup
/// rather than on the first live call.
fn validate_messages_endpoint(url: &str) -> Result<(), String> {
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err(format!(
            "ANTHROPIC_BASE_URL must be an http(s) URL, got `{url}`"
        ));
    }
    let after_scheme = url.split_once("://").map(|x| x.1).unwrap_or("");
    let path = match after_scheme.find('/') {
        Some(i) => &after_scheme[i..],
        None => "",
    };
    let path = path
        .split(['?', '#'])
        .next()
        .unwrap_or(path)
        .trim_end_matches('/');
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
const LIVE_MAX_RETRIES: u32 = 2;
const LIVE_RETRY_BACKOFF: Duration = Duration::from_millis(400);
/// M20: on `BudgetExhausted`, retry once at this multiple of the effective
/// budget, capped.
const LIVE_BUDGET_ESCALATION_FACTOR: u32 = 2;
const LIVE_BUDGET_ESCALATION_CAP: u32 = 96_000;
const LIVE_BUDGET_BASE_DEFAULT: u32 = 16_000;

/// Build a recording live client: Anthropic-compatible HTTP + retry + budget
/// escalation + cassette cache under `cache_dir`.
///
/// `api_key` must be non-empty (callers that resolve from env / providers.toml
/// should map a missing key to [`LLM_NOT_CONFIGURED`]).
/// `cache_namespace` is the fallback cassette namespace (ask / article interpret
/// pass their own request-level namespace when present).
pub fn build_recording_live_client(
    api_key: &str,
    cfg: &LiveClientConfig,
    cache_dir: &Path,
    cache_namespace: &str,
) -> Result<Box<dyn ModelClient>, String> {
    if api_key.trim().is_empty() {
        return Err(LLM_NOT_CONFIGURED.into());
    }
    let mut live = AnthropicBlockingClient::new(api_key);
    if let Some(url) = cfg.base_url.as_ref() {
        live = live.with_base_url(url.clone());
    }
    if let Some(model) = cfg.model.as_ref() {
        live = live.with_model_override(model.clone());
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
    let retrying = RetryingModelClient::new(live, LIVE_MAX_RETRIES, LIVE_RETRY_BACKOFF);
    let escalated = cfg
        .max_tokens
        .unwrap_or(LIVE_BUDGET_BASE_DEFAULT)
        .saturating_mul(LIVE_BUDGET_ESCALATION_FACTOR)
        .min(LIVE_BUDGET_ESCALATION_CAP);
    let escalating = BudgetEscalatingModelClient::new(retrying, escalated);
    let cached = CachedModelClient::new(escalating, cache_dir, cache_namespace, CacheMode::Record)
        .map_err(|e| format!("opening cache dir `{}`: {e}", cache_dir.display()))?;
    Ok(Box::new(cached))
}

/// [`build_recording_live_client`] with a HARD wall-clock posture for hosts
/// that cannot tolerate long blocking calls (the synchronous MCP server):
/// the per-request timeout is clamped to `timeout_cap_secs` (user config may
/// lower it, never raise it) and transient-failure retries are capped at
/// `max_retries` (0 = fail fast into the agent loop's honest ModelError).
/// Everything else — budget escalation, cassette recording — is identical.
pub fn build_recording_live_client_bounded(
    api_key: &str,
    cfg: &LiveClientConfig,
    cache_dir: &Path,
    cache_namespace: &str,
    timeout_cap_secs: u64,
    max_retries: u32,
) -> Result<Box<dyn ModelClient>, String> {
    let mut bounded = cfg.clone();
    bounded.timeout_secs = Some(clamp_timeout(cfg.timeout_secs, timeout_cap_secs));
    if api_key.trim().is_empty() {
        return Err(LLM_NOT_CONFIGURED.into());
    }
    let mut live = AnthropicBlockingClient::new(api_key);
    if let Some(url) = bounded.base_url.as_ref() {
        live = live.with_base_url(url.clone());
    }
    if let Some(model) = bounded.model.as_ref() {
        live = live.with_model_override(model.clone());
    }
    if let Some(mt) = bounded.max_tokens {
        live = live.with_max_tokens(mt);
    }
    if bounded.no_proxy {
        live = live.with_no_proxy();
    }
    if let Some(secs) = bounded.timeout_secs {
        live = live.with_timeout(secs);
    }
    let retrying = RetryingModelClient::new(live, max_retries, LIVE_RETRY_BACKOFF);
    // NO budget escalation here: it silently issues a SECOND provider
    // request on BudgetExhausted, doubling the blocking window the whole
    // builder exists to bound. A truncated reply surfaces as the agent
    // loop's honest model_error instead.
    let cached = CachedModelClient::new(retrying, cache_dir, cache_namespace, CacheMode::Record)
        .map_err(|e| format!("opening cache dir `{}`: {e}", cache_dir.display()))?;
    Ok(Box::new(cached))
}

/// A user-configured timeout may SHORTEN the bound, never exceed or disable
/// it: `0` means "no timeout" to the underlying HTTP client, which would
/// defeat the hard wall-clock guarantee — it falls back to the cap.
pub fn clamp_timeout(configured: Option<u64>, cap_secs: u64) -> u64 {
    configured
        .filter(|&t| t > 0)
        .unwrap_or(cap_secs)
        .min(cap_secs)
        .max(1)
}

/// Resolve a non-empty API key from a lookup. Missing / blank → [`LLM_NOT_CONFIGURED`].
pub fn resolve_api_key(lookup: impl Fn(&str) -> Option<String>) -> Result<String, String> {
    match lookup("ANTHROPIC_API_KEY")
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
    {
        Some(k) => Ok(k),
        None => Err(LLM_NOT_CONFIGURED.into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn cfg(pairs: &[(&str, &str)]) -> Result<LiveClientConfig, String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        LiveClientConfig::from_lookup(|k| map.get(k).cloned())
    }

    #[test]
    fn empty_env_is_all_defaults() {
        assert_eq!(cfg(&[]).unwrap(), LiveClientConfig::default());
    }

    #[test]
    fn parses_full_minimax_config() {
        let c = cfg(&[
            (
                "ANTHROPIC_BASE_URL",
                "https://api.minimaxi.com/anthropic/v1/messages",
            ),
            ("OVP_LLM_MODEL", "MiniMax-M2"),
            ("OVP_LLM_MAX_TOKENS", "24000"),
            ("OVP_LLM_NO_PROXY", "1"),
            ("OVP_LLM_TIMEOUT_SECS", "300"),
        ])
        .unwrap();
        assert_eq!(
            c.base_url.as_deref(),
            Some("https://api.minimaxi.com/anthropic/v1/messages")
        );
        assert_eq!(c.model.as_deref(), Some("MiniMax-M2"));
        assert_eq!(c.max_tokens, Some(24000));
        assert!(c.no_proxy);
        assert_eq!(c.timeout_secs, Some(300));
    }

    #[test]
    fn clamp_timeout_never_disables_and_never_exceeds() {
        assert_eq!(clamp_timeout(None, 45), 45, "no config → cap");
        assert_eq!(clamp_timeout(Some(10), 45), 10, "shorter is honored");
        assert_eq!(clamp_timeout(Some(300), 45), 45, "longer is clamped");
        assert_eq!(clamp_timeout(Some(0), 45), 45, "0 = no-timeout → cap");
    }

    #[test]
    fn timeout_secs_omitted_means_use_default() {
        let c = cfg(&[]).unwrap();
        assert_eq!(
            c.timeout_secs, None,
            "absent OVP_LLM_TIMEOUT_SECS falls back to OVP default"
        );
    }

    #[test]
    fn timeout_secs_zero_is_an_explicit_choice() {
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
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")]).is_err());
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.anthropic.com")]).is_err());
        assert!(
            cfg(&[("ANTHROPIC_BASE_URL", "api.anthropic.com/v1/messages")]).is_err(),
            "missing scheme"
        );
        assert!(cfg(&[("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/messages")]).is_ok());
        assert!(
            cfg(&[("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic/v1/messages/")])
                .is_ok()
        );
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
        assert!(!cfg(&[("OVP_LLM_NO_PROXY", "false")]).unwrap().no_proxy);
    }

    #[test]
    fn invalid_timeout_secs_fails_loud() {
        assert!(cfg(&[("OVP_LLM_TIMEOUT_SECS", "long")]).is_err());
        assert!(cfg(&[("OVP_LLM_TIMEOUT_SECS", "-5")]).is_err());
    }

    #[test]
    fn resolve_api_key_maps_missing_to_stable_code() {
        assert_eq!(
            resolve_api_key(|_| None).unwrap_err(),
            LLM_NOT_CONFIGURED
        );
        assert_eq!(
            resolve_api_key(|k| {
                if k == "ANTHROPIC_API_KEY" {
                    Some("  ".into())
                } else {
                    None
                }
            })
            .unwrap_err(),
            LLM_NOT_CONFIGURED
        );
        assert_eq!(
            resolve_api_key(|k| {
                if k == "ANTHROPIC_API_KEY" {
                    Some(" sk-test ".into())
                } else {
                    None
                }
            })
            .unwrap(),
            "sk-test"
        );
    }
}
