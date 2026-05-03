"""Lightweight LLM client wrapper for entity extraction and similar tasks.

Provides a single factory ``get_litellm_client(vault_dir)`` returning an
object with ``.call(system_prompt, user_prompt, max_tokens) -> str``.
This is the contract ``EntityExtractor`` and similar consumers expect.

Backed by ``LiteLLMClient`` from ``auto_evergreen_extractor``, which
already handles retries, the proxy policy, and api_key/api_base
resolution from the vault's ``.env`` file plus shell environment
fallbacks (``AUTO_VAULT_API_KEY`` → ``MINIMAX_API_KEY`` → ...).

Returns ``None`` when no API key is available so callers can gracefully
fall back to alias-only mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class _CallableLLMClient:
    """Adapt LiteLLMClient.generate(...) → .call(...) for entity_extractor."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def call(self, system_prompt: str, user_prompt: str, max_tokens: int = 3000) -> str:
        # ``LiteLLMClient.generate`` in auto_evergreen_extractor returns
        # plain ``str``; the auto_article_processor variant returns
        # ``tuple[str, dict]``.  Handle either shape.
        out = self._inner.generate(system_prompt, user_prompt, max_tokens=max_tokens)
        if isinstance(out, tuple):
            text = out[0]
        else:
            text = out
        return text or ""


def get_litellm_client(vault_dir: Path | None = None) -> _CallableLLMClient | None:
    """Construct an LLM client for the given vault, or return ``None`` if
    no API key is configured.

    The vault's ``.env`` is loaded first so values like
    ``AUTO_VAULT_API_BASE`` / ``AUTO_VAULT_MODEL`` set there take effect
    when the shell environment doesn't override them.
    """
    try:
        from .auto_evergreen_extractor import LiteLLMClient, load_env_file
        from .llm_defaults import resolve_api_key
    except ImportError:
        return None

    if vault_dir is not None:
        try:
            load_env_file(Path(vault_dir))
        except Exception:
            # Don't fail client creation just because .env was missing.
            pass

    if not resolve_api_key():
        return None

    try:
        inner = LiteLLMClient()
    except Exception:
        return None

    return _CallableLLMClient(inner)
