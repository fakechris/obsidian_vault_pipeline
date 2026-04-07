from __future__ import annotations

from pathlib import Path

from openclaw_pipeline.graph.frontmatter import FrontmatterParser
from openclaw_pipeline.graph.link_parser import LinkParser


def test_frontmatter_parser_scans_relative_vault_path(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    workspace.mkdir()
    evergreen_dir.mkdir(parents=True)
    (evergreen_dir / "Example.md").write_text(
        "---\n"
        'title: "Example"\n'
        "type: evergreen\n"
        "date: 2026-04-07\n"
        "---\n\n"
        "# Example\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(workspace)

    parser = FrontmatterParser(Path("..") / "vault")
    results = parser.parse_directory(Path("..") / "vault" / "10-Knowledge" / "Evergreen", recursive=True)

    assert len(results) == 1
    assert results[0].note_id == "example"


def test_link_parser_scans_relative_vault_path(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    evergreen_dir = vault / "10-Knowledge" / "Evergreen"
    workspace.mkdir()
    evergreen_dir.mkdir(parents=True)
    (evergreen_dir / "Source.md").write_text(
        "---\n"
        'title: "Source"\n'
        "type: evergreen\n"
        "date: 2026-04-07\n"
        "---\n\n"
        "# Source\n\n"
        "[[Target Concept]]\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(workspace)

    parser = LinkParser(Path("..") / "vault")
    results = parser.parse_directory(Path("..") / "vault" / "10-Knowledge" / "Evergreen", recursive=True)

    assert len(results) == 1
    assert results[0].source == "source"
    assert results[0].target == "target-concept"
