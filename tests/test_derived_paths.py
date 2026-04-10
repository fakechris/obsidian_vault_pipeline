from __future__ import annotations

from pathlib import Path

from openclaw_pipeline.derived.paths import compiled_view_path, extraction_run_path, review_queue_path
from openclaw_pipeline.runtime import VaultLayout


def test_extraction_run_path_is_stable_for_same_inputs(tmp_path):
    layout = VaultLayout.from_vault(tmp_path / "vault")
    source = Path("50-Inbox/01-Raw/example.md")

    first = extraction_run_path(layout, pack_name="default-knowledge", profile_name="tech/doc_structure", source_path=source)
    second = extraction_run_path(layout, pack_name="default-knowledge", profile_name="tech/doc_structure", source_path=source)

    assert first == second
    assert first.parent == layout.extraction_runs_dir / "default-knowledge" / "tech__doc_structure"
    assert first.suffix == ".json"


def test_review_queue_path_stays_under_review_queue_directory(tmp_path):
    layout = VaultLayout.from_vault(tmp_path / "vault")

    path = review_queue_path(layout, queue_name="frontmatter", subject="missing-title")

    assert path.parent == layout.review_queue_dir / "frontmatter"
    assert path.name == "missing-title.json"


def test_compiled_view_path_stays_under_compiled_view_directory(tmp_path):
    layout = VaultLayout.from_vault(tmp_path / "vault")

    path = compiled_view_path(layout, pack_name="default-knowledge", view_name="overview/domain")

    assert path.parent == layout.compiled_views_dir / "default-knowledge"
    assert path.name == "overview__domain.md"
