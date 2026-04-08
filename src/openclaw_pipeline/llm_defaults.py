from __future__ import annotations

from urllib.parse import urlparse


DEFAULT_MINIMAX_API_BASE = "https://api.minimaxi.com/anthropic"
DEFAULT_MINIMAX_MODEL = "anthropic/MiniMax-M2.7-highspeed"


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


def _uses_anthropic_minimax_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    parsed = urlparse(api_base)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return host == "api.minimaxi.com" and path == "/anthropic"
