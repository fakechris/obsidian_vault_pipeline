from __future__ import annotations

import os
from urllib.parse import urlparse


DEFAULT_MINIMAX_API_BASE = "https://api.minimaxi.com/anthropic"
DEFAULT_MINIMAX_MODEL = "anthropic/MiniMax-M2.7-highspeed"
DEFAULT_LITELLM_TIMEOUT_SECONDS = 180

API_KEY_FALLBACKS = (
    "AUTO_VAULT_API_KEY",
    "SPEC_ORCH_LLM_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)

API_BASE_FALLBACKS = (
    "AUTO_VAULT_API_BASE",
    "SPEC_ORCH_LLM_API_BASE",
    "MINIMAX_ANTHROPIC_BASE_URL",
    "ANTHROPIC_BASE_URL",
)


def normalize_model_for_api_base(
    model: str | None,
    *,
    api_type: str = "anthropic",
    api_base: str | None = None,
    default_model: str = DEFAULT_MINIMAX_MODEL,
) -> str:
    resolved = (model or "").strip() or default_model
    if "/" not in resolved:
        resolved = f"{api_type}/{resolved}"

    if _uses_anthropic_minimax_base(api_base):
        provider, _, model_name = resolved.partition("/")
        if provider == "minimax" and model_name:
            return f"anthropic/{model_name}"

    return resolved


def resolve_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for env_name in API_KEY_FALLBACKS:
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def resolve_api_base(explicit: str | None = None, default: str = DEFAULT_MINIMAX_API_BASE) -> str | None:
    if explicit:
        return explicit
    for env_name in API_BASE_FALLBACKS:
        value = os.environ.get(env_name)
        if value:
            return value
    return default


def _uses_anthropic_minimax_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    parsed = urlparse(api_base)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return host == "api.minimaxi.com" and path == "/anthropic"
