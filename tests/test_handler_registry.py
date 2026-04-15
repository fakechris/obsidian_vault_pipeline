from __future__ import annotations

from openclaw_pipeline.handler_registry import resolve_focused_action_handler, resolve_stage_handler


def test_compatibility_pack_falls_back_to_base_stage_handler():
    spec = resolve_stage_handler(
        pack_name="default-knowledge",
        stage="articles",
        runtime_adapter="pipeline_step",
    )

    assert spec.pack == "research-tech"
    assert spec.stage == "articles"
    assert spec.runtime_adapter == "pipeline_step"


def test_compatibility_pack_falls_back_to_base_focused_action_handler():
    spec = resolve_focused_action_handler(
        pack_name="default-knowledge",
        action_kind="deep_dive_workflow",
    )

    assert spec.pack == "research-tech"
    assert spec.action_kind == "deep_dive_workflow"
    assert spec.safe_to_run is True
