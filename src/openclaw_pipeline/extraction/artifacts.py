from __future__ import annotations

import json
from pathlib import Path

from ..derived.paths import extraction_run_path
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
    base_dir = layout.extraction_runs_dir / pack_name
    if profile_name:
        base_dir = base_dir / profile_name.replace("/", "__").replace("\\", "__")
    if not base_dir.exists():
        return []

    results: list[ExtractionRunResult] = []
    for artifact in sorted(base_dir.rglob("*.json")):
        results.append(ExtractionRunResult.from_dict(json.loads(artifact.read_text(encoding="utf-8"))))
    return results
