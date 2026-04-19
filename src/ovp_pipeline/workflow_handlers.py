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
    **_: Any,
) -> dict[str, Any]:
    return pipeline.step_quality(batch_size=batch_size, dry_run=dry_run)


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
    )


def run_pipeline_registry_sync(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_registry_sync(dry_run)


def run_pipeline_moc(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_moc(dry_run)


def run_pipeline_refine(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_refine(dry_run)


def run_pipeline_knowledge_index(*, pipeline: Any, dry_run: bool = False, **_: Any) -> dict[str, Any]:
    return pipeline.step_knowledge_index(dry_run)


def run_autopilot_interpretation(*, daemon: Any, task: Any, **_: Any) -> dict[str, Any]:
    return daemon._run_interpretation(task)


def run_autopilot_quality(*, daemon: Any, task: Any, **_: Any) -> dict[str, Any]:
    quality, dimensions = daemon._run_quality_stage(task)
    return {"quality": quality, "quality_dimensions": dimensions}


def run_autopilot_absorb(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_absorb()
    return {"stage": "absorb"}


def run_autopilot_moc(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_moc_update()
    return {"stage": "moc"}


def run_autopilot_refine(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_refine()
    return {"stage": "refine"}


def run_autopilot_knowledge_index(*, daemon: Any, **_: Any) -> dict[str, Any]:
    daemon._run_knowledge_index_refresh()
    return {"stage": "knowledge_index"}
