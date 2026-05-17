# BL-112: leaf-extracted from unified_pipeline_enhanced.py — verbatim move, no logic change.
import json
from pathlib import Path
from typing import Any




def _extract_json_suffix(text: str) -> dict[str, Any] | None:
    """Extract a JSON object payload from stdout that may contain log prefixes."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            payload, end = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        if idx + end == len(raw) and isinstance(payload, dict):
            return payload
    return None



def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0



def _get_version() -> str:
    """Read the installed package version, falling back to repository metadata."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("obsidian-vault-pipeline")
    except (ImportError, PackageNotFoundError):
        pass

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            tomllib = None

    if tomllib is not None:
        for parent in Path(__file__).resolve().parents:
            pyproject = parent / "pyproject.toml"
            if not pyproject.exists():
                continue
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError, UnicodeError):
                continue
            project_version = data.get("project", {}).get("version")
            if project_version:
                return str(project_version)
    return "0.3.2"


__all__ = [
    '_extract_json_suffix',
    '_safe_int',
    '_get_version'
]
