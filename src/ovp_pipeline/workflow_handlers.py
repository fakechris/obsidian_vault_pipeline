from __future__ import annotations

from typing import Any


def run_pipeline_pinboard(
    *,
    pipeline: Any,
    pinboard_days: int | None = None,
    pinboard_start: str | None = None,
    pinboard_end: str | None = None,
    dry_run: bool = False,
    **_: Any,
) -> dict[str, Any]:
    return pipeline.step_pinboard(
        days=pinboard_days,
        start_date=pinboard_start,
        end_date=pinboard_end,
        dry_run=dry_run,
    )


def run_pipeline_pinboard_process(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_pinboard_process(dry_run)


def run_pipeline_clippings(
    *,
    pipeline: Any,
    batch_size: int | None = None,
    dry_run: bool = False,
    **_: Any,
) -> dict[str, Any]:
    return pipeline.step_clippings(batch_size, dry_run)


def run_pipeline_articles(
    *,
    pipeline: Any,
    batch_size: int | None = None,
    dry_run: bool = False,
    **_: Any,
) -> dict[str, Any]:
    return pipeline.step_articles(batch_size, dry_run)


def run_pipeline_quality(
    *,
    pipeline: Any,
    batch_size: int | None = None,
    dry_run: bool = False,
    results: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    target_files = None
    if getattr(pipeline, "run_mode", None) == "incremental":
        target_files = pipeline._incremental_quality_target_files(results or {})
    return pipeline.step_quality(batch_size=batch_size, dry_run=dry_run, target_files=target_files)


def run_pipeline_fix_links(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_fix_links(dry_run)


def run_pipeline_absorb(
    *,
    pipeline: Any,
    batch_size: int | None = None,
    dry_run: bool = False,
    results: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    quality_result = (results or {}).get("quality", {})
    quality_score = quality_result.get("quality_score", -1.0)
    qualified_files = quality_result.get("quality_qualified_files")
    return pipeline.step_absorb(
        7,
        dry_run,
        quality_score=quality_score,
        qualified_files=qualified_files,
        batch_size=batch_size,
        require_quality_artifact=True,
    )


def run_pipeline_entity_extract(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_entity_extract(dry_run=dry_run)


def run_pipeline_dedup(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_dedup(dry_run)


def run_pipeline_registry_sync(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_registry_sync(dry_run)


def run_pipeline_note_type_normalize(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_note_type_normalize(dry_run)


def run_pipeline_moc(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_moc(dry_run)


def run_pipeline_refine(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_refine(dry_run)


def run_pipeline_knowledge_index(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_knowledge_index(dry_run)


def run_pipeline_synthesize(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    """BL-117: budgeted re-synthesis of stale community crystals.

    Runs after ``knowledge_index`` (so the graph/cluster snapshot the
    matcher just produced is current) and before ``ops_state`` (so
    lifecycle classification sees the fresh crystals).  Skips
    entirely on ``dry_run`` — same contract as ``step_knowledge_index``.
    """
    return pipeline.step_synthesize(dry_run)


def run_pipeline_ops_state(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    """M24.1: rebuild the lifecycle projection over knowledge.db.

    Runs after ``knowledge_index`` in the standard DAG so the
    truth-projection tables it reads are fresh.
    """
    return pipeline.step_ops_state(dry_run)


def run_autopilot_interpretation(*, daemon: Any, task: Any, **_: Any) -> dict[str, Any]:
    return daemon._run_interpretation(task)


def run_autopilot_quality(*, daemon: Any, task: Any, **_: Any) -> dict[str, Any]:
    quality, dimensions = daemon._run_quality_stage(task)
    return {"quality": quality, "quality_dimensions": dimensions}


def run_autopilot_absorb(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_absorb()
    return {"stage": "absorb"}


def run_autopilot_dedup(*, daemon: Any, **_: Any) -> dict[str, Any]:
    """Autopilot dedup is fail-closed: the daemon runs absorb as a
    subprocess and carries no promoted-slug scope, so there is no
    incremental scope to dedup against.  We SKIP rather than fall back
    to an implicit full-vault O(N²) scan (9k+ Evergreen ⇒ ~44M pair
    comparisons every cycle).  Full-vault dedup is an explicit
    maintenance op (``ovp-concept-dedup propose``) only.
    """
    return {
        "stage": "dedup",
        "skipped": True,
        "reason": "no_promoted_scope",
    }


def run_autopilot_moc(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_moc_update()
    return {"stage": "moc"}


def run_autopilot_refine(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_refine()
    return {"stage": "refine"}


def run_autopilot_knowledge_index(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_knowledge_index_refresh()
    return {"stage": "knowledge_index"}


def run_autopilot_synthesize(*, daemon: Any, **_: Any) -> dict[str, Any]:
    """BL-117: autopilot ``synthesize`` stage — invokes the same
    budgeted re-synthesis the pipeline DAG uses.

    Imported lazily so an autopilot run that doesn't reach this stage
    doesn't pay the import cost.  Failures degrade gracefully — a
    crystal-resynth blip must not sink the autopilot cycle, the same
    way ``crystal_scores rebuild skipped`` is best-effort inside
    ``knowledge_index``.  ``ovp-resynth-stale-crystals`` itself
    handles the missing-DB / empty-stale-set cases with a 0-return.
    """
    from pathlib import Path
    from .commands.resynth_stale_crystals import resynth_stale_crystals

    try:
        summary = resynth_stale_crystals(
            vault_dir=Path(daemon.vault_dir),
            pack=getattr(daemon, "workflow_pack_name", "research-tech"),
        )
    except Exception as exc:  # noqa: BLE001
        return {"stage": "synthesize", "error": str(exc)}
    return {"stage": "synthesize", **summary}


def run_autopilot_ops_state(*, daemon: Any, **_: Any) -> dict[str, Any]:
    """M24.1: autopilot ``ops_state`` refresh — pairs with the
    pipeline_step handler.  Runs after each autopilot cycle's
    knowledge_index rebuild so the lifecycle projection stays
    in sync with the truth tables that knowledge_index wrote.
    """
    daemon._run_ops_state_refresh()
    return {"stage": "ops_state"}
