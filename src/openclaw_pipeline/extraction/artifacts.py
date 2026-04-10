from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..derived.paths import extraction_run_path, normalize_derived_name
from ..runtime import VaultLayout
from .results import ExtractionRunResult


def write_run_result(layout: VaultLayout, result: ExtractionRunResult) -> Path:
    path = extraction_run_path(
        layout,
        pack_name=result.pack_name,
        profile_name=result.profile_name,
        source_path=Path(result.source_path),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_run_results(
    layout: VaultLayout,
    *,
    pack_name: str,
    profile_name: str | None = None,
) -> list[ExtractionRunResult]:
    return list(iter_run_results(layout, pack_name=pack_name, profile_name=profile_name))


def iter_run_results(
    layout: VaultLayout,
    *,
    pack_name: str,
    profile_name: str | None = None,
) -> Iterator[ExtractionRunResult]:
    base_dir = layout.extraction_runs_dir / pack_name
    if profile_name:
        base_dir = base_dir / normalize_derived_name(profile_name)
    if not base_dir.exists():
        return

    for artifact in sorted(base_dir.rglob("*.json")):
        yield ExtractionRunResult.from_dict(json.loads(artifact.read_text(encoding="utf-8")))
