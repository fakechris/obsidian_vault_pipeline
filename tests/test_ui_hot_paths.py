from __future__ import annotations

from pathlib import Path

import pytest

RAW_SOURCE_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def _forbid_rebuilds(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("hot GET route must not rebuild knowledge.db")

    import ovp_pipeline.knowledge_index as knowledge_index
    import ovp_pipeline.truth_api as truth_api

    monkeypatch.setattr(knowledge_index, "rebuild_knowledge_index", fail)
    monkeypatch.setattr(truth_api, "rebuild_knowledge_index", fail)


def _forbid_raw_source_access(monkeypatch) -> None:
    original_glob = Path.glob
    original_iterdir = Path.iterdir
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    original_rglob = Path.rglob

    def assert_not_raw_source(path: Path) -> None:
        if "Raw" in path.parts or path.suffix.lower() in RAW_SOURCE_SUFFIXES:
            raise AssertionError(f"hot GET route must not touch raw source path: {path}")

    def guarded_glob(self, pattern):
        assert_not_raw_source(self)
        return original_glob(self, pattern)

    def guarded_iterdir(self):
        assert_not_raw_source(self)
        return original_iterdir(self)

    def guarded_read_bytes(self):
        assert_not_raw_source(self)
        return original_read_bytes(self)

    def guarded_read_text(self, *args, **kwargs):
        assert_not_raw_source(self)
        return original_read_text(self, *args, **kwargs)

    def guarded_rglob(self, pattern):
        assert_not_raw_source(self)
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "glob", guarded_glob)
    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "rglob", guarded_rglob)


@pytest.mark.parametrize(
    ("path", "expected_text"),
    [
        ("/", "Knowledge Library"),
        ("/ops", "OVP Truth UI"),
        ("/ops/objects", "Alpha"),
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
    _forbid_raw_source_access(monkeypatch)

    status, body, _content_type = fetch_ui(temp_vault, path)

    assert status == 200
    assert expected_text in body
