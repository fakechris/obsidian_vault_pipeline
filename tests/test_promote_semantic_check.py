"""Tests for P2 — semantic similarity guard in promote_candidate."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from ovp_pipeline.concept_registry import ConceptRegistry
from ovp_pipeline.promote_candidates import (
    merge_candidate,
    promote_candidate,
)


def _setup_vault(vault: Path) -> None:
    """Create minimal vault layout required by promote_candidate."""
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True, exist_ok=True)
    (vault / "10-Knowledge" / "Atlas").mkdir(parents=True, exist_ok=True)
    (vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md").write_text(
        "---\ntitle: Atlas Index\n---\n", encoding="utf-8"
    )
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)


def _register_candidate(vault: Path, slug: str, title: str = "") -> None:
    registry = ConceptRegistry(vault).load()
    registry.upsert_candidate(
        slug=slug,
        title=title or slug.replace("-", " "),
        definition="test def",
        area="general",
        aliases=[slug],
    )
    registry.save()


def _register_active(vault: Path, slug: str, title: str = "") -> None:
    registry = ConceptRegistry(vault).load()
    registry.upsert_candidate(
        slug=slug,
        title=title or slug.replace("-", " "),
        definition="test def",
        area="general",
        aliases=[slug],
    )
    registry.promote_to_active(slug)
    registry.save()
    eg = vault / "10-Knowledge" / "Evergreen" / f"{slug}.md"
    eg.write_text(
        dedent(f"""\
        ---
        title: "{title or slug.replace('-', ' ')}"
        type: evergreen
        ---

        Body of {slug}.
        """),
        encoding="utf-8",
    )


def test_promote_merges_when_similar_active_exists(tmp_path: Path):
    _setup_vault(tmp_path)
    _register_active(
        tmp_path,
        "retrieval-augmented-generation",
        "Retrieval Augmented Generation",
    )
    _register_candidate(
        tmp_path,
        "retrieval-augmented-generations",
        "Retrieval Augmented Generations",
    )

    mutation = promote_candidate(
        tmp_path, "retrieval-augmented-generations", dry_run=False
    )

    assert mutation.action == "merge"
    assert mutation.target_slug == "retrieval-augmented-generation"


def test_promote_creates_new_when_no_similar(tmp_path: Path):
    _setup_vault(tmp_path)
    _register_active(tmp_path, "quantum-computing", "Quantum Computing")
    _register_candidate(tmp_path, "banana-bread-recipe", "Banana Bread Recipe")

    mutation = promote_candidate(tmp_path, "banana-bread-recipe", dry_run=False)

    assert mutation.action == "promote"
    assert mutation.slug == "banana-bread-recipe"


def test_promote_guard_does_not_match_inactive(tmp_path: Path):
    """If the similar slug is not active, do NOT merge — just promote normally."""
    _setup_vault(tmp_path)
    _register_candidate(tmp_path, "mcp-client")
    _register_candidate(tmp_path, "mcp-clients")
    (tmp_path / "10-Knowledge" / "Evergreen" / "mcp-clients.md").write_text(
        '---\ntitle: "MCP Clients"\ntype: evergreen\n---\nbody\n', encoding="utf-8"
    )

    mutation = promote_candidate(tmp_path, "mcp-client", dry_run=False)

    assert mutation.action == "promote"
