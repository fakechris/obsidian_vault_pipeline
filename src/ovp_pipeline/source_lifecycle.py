from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    from .runtime import VaultLayout
except ImportError:  # pragma: no cover - script mode fallback
    from runtime import VaultLayout  # type: ignore


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def unique_child(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = directory / f"{stem}-{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def clipping_raw_name(
    source: Path,
    sanitize_filename: Callable[[str], str],
    *,
    when: datetime | None = None,
) -> str:
    clean_name = sanitize_filename(source.stem) + ".md"
    timestamp = (when or datetime.now()).strftime("%Y-%m-%d")
    return f"{timestamp}_{clean_name}"


def archive_pinboard_source(layout: VaultLayout, source: Path) -> Path:
    month_dir = layout.pinboard_archive_dir / datetime.now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_child(month_dir, source.name)
    return source.rename(destination)


def maybe_archive_pinboard_process_single(
    layout: VaultLayout,
    source: Path | None,
    result: dict,
    *,
    dry_run: bool = False,
) -> Path | None:
    if dry_run or not source or result.get("status") != "completed":
        return None
    if not source.exists() or not is_under(source, layout.pinboard_dir):
        return None
    archived = archive_pinboard_source(layout, source)
    result["source_path"] = str(archived)
    return archived
