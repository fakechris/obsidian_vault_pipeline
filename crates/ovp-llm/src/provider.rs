//! Provider selection and key resolution — pure, always compiled (no HTTP),
//! so readiness checks in builds without the live feature agree with what
//! the live builder would actually use.

/// Stable machine-readable error when no API key is available. Servers map
/// this to HTTP 503 `llm_not_configured` so the portal can show setup guidance
/// instead of a generic 502.
pub const LLM_NOT_CONFIGURED: &str = "llm_not_configured";

/// Which wire protocol the live client speaks (`OVP_LLM_PROVIDER`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum LlmProvider {
    /// Anthropic Messages — also every Anthropic-compatible endpoint
    /// (`ANTHROPIC_BASE_URL`). The default when the variable is unset.
    #[default]
    Anthropic,
    /// OpenAI Responses API (`OPENAI_API_KEY`, optional `OPENAI_BASE_URL`).
    OpenAi,
}

impl LlmProvider {
    /// The provider-specific API-key variable.
    pub fn api_key_var(self) -> &'static str {
        match self {
            Self::Anthropic => "ANTHROPIC_API_KEY",
            Self::OpenAi => "OPENAI_API_KEY",
        }
    }

    /// Parse `OVP_LLM_PROVIDER` (absent/blank → Anthropic; unknown → Err).
    pub fn from_lookup(lookup: &impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        match lookup("OVP_LLM_PROVIDER")
            .map(|v| v.trim().to_ascii_lowercase())
            .filter(|v| !v.is_empty())
            .as_deref()
        {
            None | Some("anthropic") => Ok(Self::Anthropic),
            Some("openai") => Ok(Self::OpenAi),
            Some(other) => Err(format!(
                "OVP_LLM_PROVIDER must be `anthropic` or `openai`, got `{other}`"
            )),
        }
    }
}

/// Resolve a non-empty API key from a lookup: `OPENAI_API_KEY` when
/// `OVP_LLM_PROVIDER=openai`, else `ANTHROPIC_API_KEY`. Missing / blank (or
/// an unparseable provider, which `LiveClientConfig` reports precisely) →
/// [`LLM_NOT_CONFIGURED`].
pub fn resolve_api_key(lookup: impl Fn(&str) -> Option<String>) -> Result<String, String> {
    let var = LlmProvider::from_lookup(&lookup)
        .map(LlmProvider::api_key_var)
        .map_err(|_| LLM_NOT_CONFIGURED.to_string())?;
    match lookup(var).map(|v| v.trim().to_string()).filter(|v| !v.is_empty()) {
        Some(k) => Ok(k),
        None => Err(LLM_NOT_CONFIGURED.into()),
    }
}
