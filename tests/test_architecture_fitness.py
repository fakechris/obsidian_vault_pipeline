from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", ["/", "/search?q=alpha", "/objects"])
def test_reader_routes_do_not_expose_operator_jargon(
    temp_vault,
    fetch_ui,
    seed_hot_path_vault,
    path,
):
    seed_hot_path_vault(temp_vault)

    status, body, _content_type = fetch_ui(temp_vault, path)

    assert status == 200
    for banned in ["Workflow Map", "Compile gate", "Projection lifecycle", "source of truth"]:
        assert banned not in body


def test_readme_and_milestone_avoid_source_of_truth_language(repo_root):
    docs = [
        repo_root / "README.md",
        repo_root / "README.zh-CN.md",
        repo_root / "MILESTONE.md",
        repo_root / "MILESTONE.zh-CN.md",
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "source of truth" not in text.lower()
