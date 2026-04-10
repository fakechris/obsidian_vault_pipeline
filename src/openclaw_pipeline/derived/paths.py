from __future__ import annotations

from hashlib import sha1
from pathlib import Path

from ..runtime import VaultLayout


def _normalize_name(value: str) -> str:
    normalized = value.strip().replace("/", "__").replace("\\", "__")
    return normalized.replace(" ", "-")


def extraction_run_path(
    layout: VaultLayout,
    *,
    pack_name: str,
    profile_name: str,
    source_path: Path,
) -> Path:
    profile_dir = layout.extraction_runs_dir / pack_name / _normalize_name(profile_name)
    source_key = sha1(source_path.as_posix().encode("utf-8")).hexdigest()[:12]
    return profile_dir / f"{source_key}.json"


def review_queue_path(
    layout: VaultLayout,
    *,
    queue_name: str,
    subject: str,
) -> Path:
    return layout.review_queue_dir / _normalize_name(queue_name) / f"{_normalize_name(subject)}.json"


def compiled_view_path(
    layout: VaultLayout,
    *,
    pack_name: str,
    view_name: str,
) -> Path:
    return layout.compiled_views_dir / pack_name / f"{_normalize_name(view_name)}.md"
