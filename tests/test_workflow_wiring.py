from __future__ import annotations

import json

import pytest


def test_root_and_ops_dispatch_to_distinct_renderers(temp_vault, monkeypatch, fetch_ui):
    import ovp_pipeline.commands.ui_server as ui_server

    calls = []
    monkeypatch.setattr(
        ui_server,
        "_build_runtime_home_payload_from_query",
        lambda vault_dir, query: {"screen": "runtime/home", "requested_pack": ""},
    )
    monkeypatch.setattr(
        ui_server,
        "_render_library_home",
        lambda payload: calls.append("library") or "<html>library</html>",
    )
    monkeypatch.setattr(
        ui_server,
        "_render_dashboard",
        lambda payload: calls.append("dashboard") or "<html>dashboard</html>",
    )

    assert fetch_ui(temp_vault, "/")[0] == 200
    assert fetch_ui(temp_vault, "/ops")[0] == 200
    assert calls == ["library", "dashboard"]


def test_candidate_review_route_uses_truth_api_governance_seam(
    temp_vault, monkeypatch, post_ui
):
    import ovp_pipeline.commands.ui_server as ui_server

    calls = []

    def fake_review_candidate_concept(*args, **kwargs):
        calls.append(kwargs)
        return {
            "action": "promote",
            "mutation": {"action": "promote"},
            "knowledge_index_rebuilt": False,
            "next_path": "/candidates",
        }

    monkeypatch.setattr(ui_server, "review_candidate_concept", fake_review_candidate_concept)

    status, payload = post_ui(
        temp_vault,
        "/api/candidates/review",
        "slug=alpha-candidate&action=promote",
    )

    assert status == 200
    assert json.loads(payload)["action"] == "promote"
    assert calls == [
        {
            "slug": "alpha-candidate",
            "action": "promote",
            "target_slug": None,
            "note": "",
            "pack_name": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body", "patched_name"),
    [
        (
            "/api/evolution/review",
            "evolution_id=evo-1&status=accepted&pack=media-editorial",
            "review_evolution_candidate",
        ),
        (
            "/api/candidates/review",
            "slug=alpha&action=promote&pack=media-editorial",
            "review_candidate_concept",
        ),
        (
            "/api/summaries/rebuild",
            "object_id=alpha&pack=media-editorial",
            "rebuild_compiled_summaries",
        ),
    ],
)
def test_research_mutation_routes_guard_non_research_pack_before_work(
    temp_vault,
    monkeypatch,
    post_ui,
    path,
    body,
    patched_name,
):
    import ovp_pipeline.commands.ui_server as ui_server

    monkeypatch.setattr(
        ui_server,
        patched_name,
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("mutation should not run")
        ),
    )

    status, payload = post_ui(temp_vault, path, body)

    parsed = json.loads(payload)
    assert status == 409
    assert parsed["status"] == "unsupported_pack"


def test_action_enqueue_route_uses_truth_api_action_queue_seam(
    temp_vault, monkeypatch, post_ui
):
    import ovp_pipeline.commands.ui_server as ui_server

    calls = []

    def fake_enqueue(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "queued", "next_path": "/signals"}

    monkeypatch.setattr(ui_server, "enqueue_signal_action", fake_enqueue)

    status, payload = post_ui(
        temp_vault,
        "/api/actions/enqueue",
        "signal_id=sig-1&action_kind=deep_dive_workflow",
    )

    assert status == 200
    assert json.loads(payload)["status"] == "queued"
    assert calls == [{"signal_id": "sig-1"}]
