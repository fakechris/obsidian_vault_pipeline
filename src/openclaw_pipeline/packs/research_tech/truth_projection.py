from __future__ import annotations

from pathlib import Path

from ...truth_store import TruthStoreProjection, build_truth_store_projection


def build_truth_projection(
    *,
    vault_dir: Path,
    page_rows: list[tuple[str, str, str, str, str, str, str]],
    link_rows: list[tuple[str, str, str, str, int]],
    spec: object | None = None,
) -> TruthStoreProjection:
    _ = vault_dir, spec
    return build_truth_store_projection(page_rows, link_rows)
