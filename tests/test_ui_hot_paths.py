from __future__ import annotations

import pytest


def _forbid_rebuilds(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("hot GET route must not rebuild knowledge.db")

    import ovp_pipeline.knowledge_index as knowledge_index
    import ovp_pipeline.truth_api as truth_api

    monkeypatch.setattr(knowledge_index, "rebuild_knowledge_index", fail)
    monkeypatch.setattr(truth_api, "rebuild_knowledge_index", fail)


@pytest.mark.parametrize(
    ("path", "expected_text"),
    [
        ("/", "Knowledge Library"),
        ("/ops", "OVP Truth UI"),
        ("/objects", "Alpha"),
        ("/api/objects", '"object_id": "alpha"'),
        ("/search?q=alpha", "Alpha"),
        ("/api/search?q=alpha", '"query": "alpha"'),
    ],
)
def test_ui_hot_get_routes_do_not_rebuild_knowledge_db(
    temp_vault,
    monkeypatch,
    fetch_ui,
    seed_hot_path_vault,
    path,
    expected_text,
):
    seed_hot_path_vault(temp_vault)
    _forbid_rebuilds(monkeypatch)

    status, body, _content_type = fetch_ui(temp_vault, path)

    assert status == 200
    assert expected_text in body
