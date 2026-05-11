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

# BL-068: router-specific overrides.  When set, the BL-062 shadow
# router (and a future BL-062-PR4 "router-as-primary" path) targets
# a different model / endpoint than the main absorb extractor.
#
# Motivation: the absorb extractor wants the highest-quality model
# available (per-target extraction quality matters most), but the
# cheap Pass 1 router needs a different trade-off — large input
# context (to fit a 1000-evergreen index), low cost-per-call, and
# can tolerate lower output quality (the router only decides
# update-vs-create, not the actual content).
#
# Concretely: MiniMax M2.7-highspeed has a ~2K-token input cap that
# makes the router prompt impossible.  Pointing the router at e.g.
# DeepSeek-V4-Flash (1M context, OpenAI-compatible) via SenseNova
# unblocks the live-vault shadow run without changing the absorb
# extractor's model.
#
# Env vars (all four optional; setting just ``OVP_ROUTER_MODEL`` is
# fine if the main api_base/api_key already point at a compatible
# endpoint):
#
# * ``OVP_ROUTER_MODEL`` — model name (without provider prefix; the
#   prefix is inferred from ``OVP_ROUTER_API_TYPE``)
# * ``OVP_ROUTER_API_BASE`` — endpoint URL (e.g.
#   ``https://token.sensenova.cn/v1`` for SenseNova OpenAI-compat)
# * ``OVP_ROUTER_API_KEY`` — secret; falls back to the main key
# * ``OVP_ROUTER_API_TYPE`` — ``"openai"`` or ``"anthropic"``;
#   defaults to ``"anthropic"`` for backwards-compat
ROUTER_MODEL_ENV = "OVP_ROUTER_MODEL"
ROUTER_API_BASE_ENV = "OVP_ROUTER_API_BASE"
ROUTER_API_KEY_ENV = "OVP_ROUTER_API_KEY"
ROUTER_API_TYPE_ENV = "OVP_ROUTER_API_TYPE"

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


def resolve_router_llm_config() -> dict[str, str] | None:
    """Return router LLM config when **any** of the BL-068 env vars
    are set; otherwise ``None`` (caller falls back to the main
    extractor's LLM client).

    Soft-merge contract: each field independently overrides — set
    only ``OVP_ROUTER_MODEL`` to keep the same endpoint but switch
    models, or set ``OVP_ROUTER_API_BASE`` + ``OVP_ROUTER_API_KEY``
    to point at a different provider while inheriting the model
    name from the main config.  The api_type defaults to
    ``"anthropic"`` (matches the main extractor) unless explicitly
    set to ``"openai"`` for OpenAI-compatible providers like
    SenseNova.

    Security: when ``OVP_ROUTER_API_BASE`` overrides the endpoint,
    the api_key does **not** inherit from the main vault config —
    setting only ``OVP_ROUTER_API_BASE`` without
    ``OVP_ROUTER_API_KEY`` returns an empty key so the operator
    sees an auth failure on the new endpoint rather than
    accidentally leaking the main key to an unintended provider.
    When the base is *not* overridden (e.g. operator just wants a
    different model on the same provider), inheriting the main key
    is safe and convenient.

    The returned dict is shaped to pass straight into
    :class:`auto_evergreen_extractor.LiteLLMClient`'s kwargs.
    """
    model = (os.environ.get(ROUTER_MODEL_ENV) or "").strip()
    api_base = (os.environ.get(ROUTER_API_BASE_ENV) or "").strip()
    api_key = (os.environ.get(ROUTER_API_KEY_ENV) or "").strip()
    api_type = (os.environ.get(ROUTER_API_TYPE_ENV) or "").strip()

    if not any((model, api_base, api_key, api_type)):
        return None

    # Key resolution rules:
    # 1. Explicit OVP_ROUTER_API_KEY wins.
    # 2. Otherwise, only inherit the main key when the endpoint
    #    is unchanged (same api_base) — avoids leaking the main
    #    key to a different provider when the operator forgot the
    #    router key.
    resolved_key = api_key
    if not resolved_key and not api_base:
        resolved_key = resolve_api_key() or ""

    return {
        "model": model or DEFAULT_MINIMAX_MODEL,
        "api_base": api_base or resolve_api_base(),
        "api_key": resolved_key,
        "api_type": api_type or "anthropic",
    }


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
