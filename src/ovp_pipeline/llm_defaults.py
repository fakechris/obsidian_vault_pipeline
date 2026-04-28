from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
import os
import threading
import time
from typing import Any, Callable
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

PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
LLM_PROXY_MODE_ENV = "OVP_LLM_PROXY_MODE"
LLM_PROXY_URL_ENV = "OVP_LLM_PROXY_URL"
LITELLM_PROXY_BYPASS_ENV = "LITELLM_PROXY_BYPASS"
_BYPASS_MODES = {"", "bypass", "none", "off", "disabled", "direct"}
_AMBIENT_MODES = {"ambient", "system", "shell"}
_CUSTOM_MODES = {"custom", "proxy"}
_LITELLM_PROXY_ENV_LOCK = threading.RLock()


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


def _proxy_mode(env: Mapping[str, str]) -> str:
    raw_mode = (env.get(LLM_PROXY_MODE_ENV) or "").strip().lower()
    if not raw_mode and (env.get(LLM_PROXY_URL_ENV) or "").strip():
        return "custom"
    return raw_mode


def env_for_litellm(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment with the configured LLM proxy policy applied.

    ``OVP_LLM_PROXY_MODE`` is the dedicated switch:

    - ``bypass``/``none``/unset: strip proxy variables and call LiteLLM directly.
    - ``ambient``: preserve the shell's proxy variables.
    - ``custom``: set every common proxy variable to ``OVP_LLM_PROXY_URL``.
    """
    source = os.environ if env is None else env
    cleaned = dict(source)
    mode = _proxy_mode(cleaned)

    if mode in _AMBIENT_MODES:
        cleaned.pop(LITELLM_PROXY_BYPASS_ENV, None)
        return cleaned

    if mode in _CUSTOM_MODES:
        proxy_url = (cleaned.get(LLM_PROXY_URL_ENV) or "").strip()
        if not proxy_url:
            raise ValueError(f"{LLM_PROXY_URL_ENV} is required when {LLM_PROXY_MODE_ENV}=custom")
        for key in PROXY_ENV_VARS:
            cleaned[key] = proxy_url
        cleaned.pop(LITELLM_PROXY_BYPASS_ENV, None)
        return cleaned

    if mode not in _BYPASS_MODES:
        raise ValueError(
            f"Unsupported {LLM_PROXY_MODE_ENV}={mode!r}; use bypass, ambient, or custom"
        )

    for key in PROXY_ENV_VARS:
        cleaned.pop(key, None)
    cleaned[LITELLM_PROXY_BYPASS_ENV] = "1"
    return cleaned


def env_without_litellm_proxy(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Backward-compatible alias for the default direct LiteLLM environment."""
    return env_for_litellm(env)


@contextmanager
def litellm_proxy_policy() -> Iterator[None]:
    """Temporarily apply the configured proxy policy around a LiteLLM call."""
    with _LITELLM_PROXY_ENV_LOCK:
        managed = (*PROXY_ENV_VARS, LITELLM_PROXY_BYPASS_ENV)
        previous = {key: os.environ.get(key) for key in managed}
        try:
            configured = env_for_litellm(os.environ)
            for key in managed:
                if key in configured:
                    os.environ[key] = configured[key]
                else:
                    os.environ.pop(key, None)
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def litellm_proxy_bypass() -> AbstractContextManager[None]:
    """Backward-compatible name for the configured LiteLLM proxy policy."""
    return litellm_proxy_policy()


def completion_with_litellm_policy(
    completion_fn: Callable[..., Any],
    kwargs: Mapping[str, Any],
    *,
    attempts: int = 3,
    retry_sleep_seconds: float = 1.5,
) -> Any:
    """Call LiteLLM with the configured proxy policy and transient retry."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with litellm_proxy_policy():
                return completion_fn(**dict(kwargs))
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(retry_sleep_seconds * (attempt + 1))
    raise last_error or RuntimeError("litellm completion failed")


def _uses_anthropic_minimax_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    parsed = urlparse(api_base)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return host == "api.minimaxi.com" and path == "/anthropic"
