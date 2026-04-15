from __future__ import annotations

from pathlib import Path
from typing import Any

from .runtime import VaultLayout, resolve_vault_dir


def run_deep_dive_workflow_action(
    *,
    vault_dir: Path | str,
    action: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    from .auto_article_processor import AutoArticleProcessor, PipelineLogger, TransactionManager

    resolved_vault = resolve_vault_dir(vault_dir)
    note_paths = [path for path in action.get("note_paths", []) if path]
    if not note_paths:
        raise ValueError("deep_dive_workflow action missing note_paths")
    source_path = resolved_vault / note_paths[0]
    if not source_path.exists():
        raise FileNotFoundError(f"source note not found: {note_paths[0]}")
    layout = VaultLayout.from_vault(resolved_vault)
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    processor = AutoArticleProcessor(resolved_vault, logger, txn)
    processor.init_llm()
    result = processor.process_single_file(source_path, dry_run=False)
    if result.get("status") != "completed":
        raise RuntimeError(str(result.get("error") or "deep_dive_workflow_failed"))
    return result


def run_object_extraction_workflow_action(
    *,
    vault_dir: Path | str,
    action: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    from .auto_evergreen_extractor import run_absorb_workflow

    resolved_vault = resolve_vault_dir(vault_dir)
    note_paths = [path for path in action.get("note_paths", []) if path]
    if not note_paths:
        raise ValueError("object_extraction_workflow action missing note_paths")
    deep_dive_path = resolved_vault / note_paths[0]
    if not deep_dive_path.exists():
        raise FileNotFoundError(f"deep dive not found: {note_paths[0]}")
    return run_absorb_workflow(
        resolved_vault,
        file_path=deep_dive_path,
        dry_run=False,
        auto_promote=True,
        promote_threshold=1,
    )
