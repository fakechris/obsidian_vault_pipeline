from __future__ import annotations

from pathlib import Path
from typing import Any

from ...focused_actions import (
    run_deep_dive_workflow_action as _run_deep_dive_workflow_action,
    run_object_extraction_workflow_action as _run_object_extraction_workflow_action,
)


def run_deep_dive_workflow_action(
    vault_dir: Path | str,
    action: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_deep_dive_workflow_action(vault_dir=vault_dir, action=action, **kwargs)


def run_object_extraction_workflow_action(
    vault_dir: Path | str,
    action: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_object_extraction_workflow_action(vault_dir=vault_dir, action=action, **kwargs)
