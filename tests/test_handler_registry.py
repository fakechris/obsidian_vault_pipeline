from __future__ import annotations

import inspect

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


def test_focused_action_handlers_accept_positional_vault_and_action():
    from openclaw_pipeline.focused_actions import (
        run_deep_dive_workflow_action,
        run_object_extraction_workflow_action,
    )

    inspect.signature(run_deep_dive_workflow_action).bind("vault", {})
    inspect.signature(run_object_extraction_workflow_action).bind("vault", {})


def test_execute_profile_stage_handler_preserves_caller_owned_empty_results(monkeypatch):
    import openclaw_pipeline.handler_registry as registry_source
    from openclaw_pipeline.packs.base import StageHandlerSpec

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        registry_source,
        "resolve_stage_handler",
        lambda **kwargs: StageHandlerSpec(
            name="articles",
            pack="research-tech",
            handler_kind="profile_stage",
            runtime_adapter="pipeline_step",
            entrypoint="tests.fake:handler",
            stage="articles",
        ),
    )
    monkeypatch.setattr(
        registry_source,
        "load_entrypoint",
        lambda entrypoint: (
            lambda **kwargs: captured.setdefault("results", kwargs["results"]) or {"success": True}
        ),
    )

    owned_results: dict[str, object] = {}
    registry_source.execute_profile_stage_handler(object(), "articles", results=owned_results)

    assert captured["results"] is owned_results


def test_execute_autopilot_stage_handler_preserves_caller_owned_empty_result(monkeypatch):
    import openclaw_pipeline.handler_registry as registry_source
    from openclaw_pipeline.packs.base import StageHandlerSpec

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        registry_source,
        "resolve_stage_handler",
        lambda **kwargs: StageHandlerSpec(
            name="quality",
            pack="research-tech",
            handler_kind="profile_stage",
            runtime_adapter="autopilot_stage",
            entrypoint="tests.fake:handler",
            stage="quality",
        ),
    )
    monkeypatch.setattr(
        registry_source,
        "load_entrypoint",
        lambda entrypoint: (
            lambda **kwargs: captured.setdefault("result", kwargs["result"]) or {"quality": 4.0}
        ),
    )

    owned_result: dict[str, object] = {}
    registry_source.execute_autopilot_stage_handler(object(), "quality", result=owned_result)

    assert captured["result"] is owned_result
