"""LLM profile loader (M21a / BL-081).

Reads ``.ovp/llm_profiles.yaml`` and exposes typed access to the
four built-in abstract profiles (Fast / Balanced / Deep, plus any
custom profiles the operator adds) and per-use-case defaults.

Why a separate config layer
===========================

Before M21 every LLM call site read ``AUTO_VAULT_API_KEY`` /
``AUTO_VAULT_API_BASE`` / ``AUTO_VAULT_MODEL`` directly.  That
worked when there was only one model — the absorb extractor.
M21 introduces *cost / quality tiers*: chat uses Balanced, the
background extractor uses Fast, deep synthesis uses Deep.  The
Reader UI surfaces the abstract names; raw provider/model strings
stay out of chrome.

Design rules
============

1. **Graceful degradation.**  Missing or unreadable
   ``.ovp/llm_profiles.yaml`` falls back to a single synthetic
   ``balanced`` profile sourced from the existing ``AUTO_VAULT_*``
   env vars (via :mod:`ovp_pipeline.llm_defaults`).  Legacy vaults
   see no behavioural change.
2. **No side effects.**  This module never writes.  All caching
   is keyed to mtime so weekly edits propagate without restarts.
3. **Frozen dataclasses.**  :class:`ProfileConfig` is immutable —
   callers can't accidentally mutate the cached singleton.
4. **Documentation is separate.**  ``00-Polaris/MODELS.md`` is
   hand-authored operator notes — never parsed by this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping

import yaml

from ovp_pipeline.llm_defaults import (
    DEFAULT_MINIMAX_API_BASE,
    DEFAULT_MINIMAX_MODEL,
    normalize_model_for_api_base,
    resolve_api_base,
    resolve_api_key,
)
from ovp_pipeline.runtime import resolve_vault_dir

logger = logging.getLogger(__name__)

CONFIG_REL: Final[str] = ".ovp/llm_profiles.yaml"

# Cap on YAML file size — the config is human-edited and tiny in
# practice (a few hundred bytes).  Defends against a runaway edit.
MAX_BYTES: Final[int] = 32 * 1024

DEFAULT_USE_CASES: Final[tuple[str, ...]] = (
    "chat",
    "extraction",
    "digest",
    "router",
)

# Per-field defaults — referenced both by :class:`ProfileConfig` and
# the yaml parser so the two stay in sync (CodeRabbit M2).
_DEFAULT_MAX_TOKENS: Final[int] = 4000
_DEFAULT_TEMPERATURE: Final[float] = 0.7
_DEFAULT_API_TYPE: Final[str] = "anthropic"

# Built-in defaults — used when ``.ovp/llm_profiles.yaml`` is
# missing.  Three abstract tiers; only the Balanced tier is
# materialised from env vars so legacy vaults keep working.  Fast
# and Deep need explicit yaml entries to point at real providers.
_FALLBACK_PROFILE_NAME: Final[str] = "balanced"
_FALLBACK_USE_CASE_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        "chat": "balanced",
        "extraction": "balanced",
        "digest": "balanced",
        "router": "balanced",
    }
)

# Per-pack-per-day input+output token cap.  Conservative default
# the operator can raise in yaml after watching real usage.
_FALLBACK_DAILY_TOKEN_CAP: Final[int] = 200_000
_FALLBACK_INPUT_CAP: Final[int] = 16_000
_FALLBACK_OUTPUT_CAP: Final[int] = 4_000


@dataclass(frozen=True)
class ProfileConfig:
    """One row of ``.ovp/llm_profiles.yaml`` ``profiles:`` map.

    ``cost_per_1k_in`` / ``cost_per_1k_out`` are informational
    only — the BL-084 cost guardrail counts *tokens*, not dollars.
    They're carried here so the ``/chats`` list view (BL-088) can
    show a friendly dollar estimate.
    """

    name: str
    provider: str
    model: str
    max_tokens: int = _DEFAULT_MAX_TOKENS
    temperature: float = _DEFAULT_TEMPERATURE
    api_base: str | None = None
    api_key: str | None = None
    api_type: str = _DEFAULT_API_TYPE
    cost_per_1k_in: float = 0.0
    cost_per_1k_out: float = 0.0

    @property
    def litellm_model(self) -> str:
        """Return the provider-prefixed model string LiteLLM expects.

        ``profiles.balanced.provider = anthropic`` + ``model =
        claude-sonnet-4-6`` collapses to ``anthropic/claude-sonnet-4-6``.
        Already-prefixed models pass through unchanged.
        """
        if "/" in self.model:
            return self.model
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class ProfileLimits:
    """``limits:`` block from yaml.  Defaults match the M21 plan."""

    chat_input_tokens_per_request: int = _FALLBACK_INPUT_CAP
    chat_output_tokens_per_request: int = _FALLBACK_OUTPUT_CAP
    chat_daily_tokens_per_pack: int = _FALLBACK_DAILY_TOKEN_CAP


@dataclass(frozen=True)
class ProfileBook:
    """Parsed contents of ``.ovp/llm_profiles.yaml``.

    Treat this as immutable; replace via :func:`load_profiles`
    rather than mutating fields.
    """

    profiles: Mapping[str, ProfileConfig]
    default_for: Mapping[str, str]
    limits: ProfileLimits
    source: str = "fallback"  # "yaml" | "fallback"
    extras: Mapping[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------
# Public API
# ---------------------------------------------------------------


def load_profiles(vault_dir: Path | str | None = None) -> ProfileBook:
    """Return the :class:`ProfileBook` for ``vault_dir``.

    Falls back to a synthetic single-profile book derived from the
    ``AUTO_VAULT_*`` env vars when ``.ovp/llm_profiles.yaml`` is
    missing or unreadable.  Cached by mtime — editing the yaml
    invalidates the cache automatically on the next call.
    """
    config_path = _config_path(vault_dir)
    raw = _read_capped(config_path)
    if not raw:
        return _fallback_book()

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        logger.warning(
            "llm_profiles: failed to parse %s: %s — using fallback",
            config_path,
            exc,
        )
        return _fallback_book()

    if not isinstance(data, Mapping):
        logger.warning(
            "llm_profiles: top-level YAML must be a mapping; " "got %s — using fallback",
            type(data).__name__,
        )
        return _fallback_book()

    profiles = _parse_profiles(data.get("profiles") or {})
    if not profiles:
        logger.warning(
            "llm_profiles: %s has no usable ``profiles:`` map — " "using fallback",
            config_path,
        )
        return _fallback_book()

    default_for = _parse_default_for(
        data.get("default_for") or {},
        profiles,
    )
    limits = _parse_limits(data.get("limits") or {})

    return ProfileBook(
        profiles=MappingProxyType(dict(profiles)),
        default_for=MappingProxyType(dict(default_for)),
        limits=limits,
        source="yaml",
    )


def resolve_profile(
    name: str,
    *,
    vault_dir: Path | str | None = None,
) -> ProfileConfig:
    """Return the named profile.

    Raises :class:`KeyError` when ``name`` isn't defined.  Use
    :func:`profile_for_use_case` if you want a default fallback.
    """
    book = load_profiles(vault_dir)
    try:
        return book.profiles[name]
    except KeyError as exc:
        raise KeyError(
            f"llm_profiles: profile {name!r} not defined; " f"available: {sorted(book.profiles)}"
        ) from exc


def profile_for_use_case(
    use_case: str,
    *,
    vault_dir: Path | str | None = None,
) -> ProfileConfig:
    """Return the profile assigned to ``use_case`` in ``default_for:``.

    ``use_case`` is one of ``chat / extraction / digest / router``
    (the canonical set in :data:`DEFAULT_USE_CASES`).  When the
    yaml doesn't assign a profile for the use case, falls back to
    ``balanced``.
    """
    book = load_profiles(vault_dir)
    profile_name = book.default_for.get(use_case)
    if profile_name is None or profile_name not in book.profiles:
        return book.profiles[_pick_fallback_name(book.profiles)]
    return book.profiles[profile_name]


def clear_cache() -> None:
    """Drop the mtime cache.  Used by tests."""
    _CACHE.clear()


# ---------------------------------------------------------------
# Internals
# ---------------------------------------------------------------


_CACHE: dict[Path, tuple[float, str]] = {}


def _config_path(vault_dir: Path | str | None) -> Path:
    """Resolve to an absolute path to the yaml config.

    When ``vault_dir`` is ``None``, defer to :func:`resolve_vault_dir`
    so CLI calls launched outside the vault still find the operator's
    ``.ovp/llm_profiles.yaml`` (codex review P2) — the same resolver
    every other ``ovp-*`` command uses.  Calling ``.resolve()`` keeps
    the ``_CACHE`` key stable across ``os.chdir`` (CodeRabbit P1).
    """
    base = resolve_vault_dir(vault_dir)
    return (base / CONFIG_REL).resolve()


def _read_capped(path: Path) -> str:
    """Return ``path`` content or ``""`` when missing/unreadable."""
    try:
        st = path.stat()
    except OSError:
        return ""

    if st.st_size > MAX_BYTES:
        # Check size *before* slurping the file (CodeRabbit M3).
        logger.warning(
            "llm_profiles: %s exceeded %d bytes; using fallback",
            path,
            MAX_BYTES,
        )
        return ""

    mtime = st.st_mtime
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        logger.debug("llm_profiles: failed to read %s: %s", path, exc)
        return ""

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            "llm_profiles: %s is not valid UTF-8; using fallback",
            path,
        )
        return ""

    _CACHE[path] = (mtime, text)
    return text


def _parse_profiles(
    raw: object,
) -> Mapping[str, ProfileConfig]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, ProfileConfig] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(body, Mapping):
            logger.warning(
                "llm_profiles: profile %r is not a mapping; skipped",
                name,
            )
            continue
        provider = str(body.get("provider") or "").strip()
        model = str(body.get("model") or "").strip()
        if not provider or not model:
            logger.warning(
                "llm_profiles: profile %r missing provider/model; skipped",
                name,
            )
            continue
        api_type = _coerce_optional_str(body.get("api_type")) or provider
        result[name] = ProfileConfig(
            name=name,
            provider=provider,
            model=model,
            max_tokens=_coerce_positive_int(
                body.get("max_tokens"),
                _DEFAULT_MAX_TOKENS,
            ),
            temperature=_coerce_float(
                body.get("temperature"),
                _DEFAULT_TEMPERATURE,
            ),
            api_base=_coerce_optional_str(body.get("api_base")),
            api_key=_coerce_optional_str(body.get("api_key")),
            api_type=api_type,
            cost_per_1k_in=_coerce_float(
                body.get("cost_per_1k_in"),
                0.0,
            ),
            cost_per_1k_out=_coerce_float(
                body.get("cost_per_1k_out"),
                0.0,
            ),
        )
    return result


def _parse_default_for(
    raw: object,
    profiles: Mapping[str, ProfileConfig],
) -> Mapping[str, str]:
    if not isinstance(raw, Mapping):
        return dict(_FALLBACK_USE_CASE_MAP)
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if not isinstance(value, str):
            continue
        if value in profiles:
            result[key] = value
        else:
            logger.warning(
                "llm_profiles: default_for.%s references undefined " "profile %r; ignored",
                key,
                value,
            )
    # Ensure every canonical use case has an entry — fall back to
    # the first available profile if the yaml didn't list it.
    if profiles:
        first_profile = _pick_fallback_name(profiles)
        for use_case in DEFAULT_USE_CASES:
            result.setdefault(use_case, first_profile)
    return result


def _pick_fallback_name(profiles: Mapping[str, ProfileConfig]) -> str:
    """Pick a deterministic fallback profile name (CodeRabbit M6).

    Prefer ``balanced`` when present; otherwise the first profile
    sorted by name so two callers in the same vault agree.  Caller
    must guarantee ``profiles`` is non-empty.
    """
    if _FALLBACK_PROFILE_NAME in profiles:
        return _FALLBACK_PROFILE_NAME
    return sorted(profiles)[0]


def _parse_limits(raw: object) -> ProfileLimits:
    if not isinstance(raw, Mapping):
        return ProfileLimits()
    return ProfileLimits(
        chat_input_tokens_per_request=_coerce_positive_int(
            raw.get("chat_input_tokens_per_request"),
            _FALLBACK_INPUT_CAP,
        ),
        chat_output_tokens_per_request=_coerce_positive_int(
            raw.get("chat_output_tokens_per_request"),
            _FALLBACK_OUTPUT_CAP,
        ),
        chat_daily_tokens_per_pack=_coerce_positive_int(
            raw.get("chat_daily_tokens_per_pack"),
            _FALLBACK_DAILY_TOKEN_CAP,
        ),
    )


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _coerce_positive_int(value: object, default: int) -> int:
    """Like :func:`_coerce_int`, but reject ``<= 0`` (CodeRabbit P1).

    ``max_tokens: 0`` and ``chat_daily_tokens_per_pack: -1`` would
    silently disable caps if accepted verbatim; falling back to the
    default is safer than honoring nonsensical values.
    """
    coerced = _coerce_int(value, default)
    if coerced <= 0:
        return default
    return coerced


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _coerce_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fallback_book() -> ProfileBook:
    """Build a one-profile book from ``AUTO_VAULT_*`` env vars.

    Legacy vaults without ``.ovp/llm_profiles.yaml`` still get a
    usable ``balanced`` profile so :func:`profile_for_use_case`
    never raises.  Model defaults to ``DEFAULT_MINIMAX_MODEL`` and
    is routed through :func:`normalize_model_for_api_base` so the
    ``minimax/<m>`` legacy shape (with the Anthropic-compatible
    MiniMax endpoint as base) collapses to ``anthropic/<m>`` —
    matching what existing ``LiteLLMClient`` consumers expect
    (codex review P2).
    """
    api_key = resolve_api_key()
    api_base = resolve_api_base(default=DEFAULT_MINIMAX_API_BASE)
    raw_model = (os.environ.get("AUTO_VAULT_MODEL") or "").strip()
    # Always run through the same normalizer LiteLLMClient uses so
    # the fallback profile produces a model string downstream code
    # actually accepts.
    normalised = normalize_model_for_api_base(
        raw_model or None,
        api_type=_DEFAULT_API_TYPE,
        api_base=api_base,
        default_model=DEFAULT_MINIMAX_MODEL,
    )
    provider, _, bare_model = normalised.partition("/")
    if not bare_model:
        bare_model, provider = provider, _DEFAULT_API_TYPE

    balanced = ProfileConfig(
        name=_FALLBACK_PROFILE_NAME,
        provider=provider,
        model=bare_model,
        max_tokens=_DEFAULT_MAX_TOKENS,
        temperature=_DEFAULT_TEMPERATURE,
        api_base=api_base,
        api_key=api_key,
        api_type=provider or _DEFAULT_API_TYPE,
    )
    return ProfileBook(
        profiles=MappingProxyType({_FALLBACK_PROFILE_NAME: balanced}),
        default_for=MappingProxyType(dict(_FALLBACK_USE_CASE_MAP)),
        limits=ProfileLimits(),
        source="fallback",
    )
