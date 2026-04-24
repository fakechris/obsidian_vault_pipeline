"""Phase 38 — Crystal materializer.

Coverage:
* materializing a snapshot writes a frontmatter+body file under 40-Resources/Crystals
* the file is idempotent (same snapshot → same crystal_id, no rewrite)
* a content-changing snapshot produces a *new* crystal_id
* EVOLVES relations from graph_edges are surfaced in frontmatter and body
* the CLI runs end-to-end on an empty vault without crashing
"""

from __future__ import annotations

import sqlite3
from datetime import date

from ovp_pipeline.commands.build_crystals import main as build_crystals_main
from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.materializers.crystal import (
    CRYSTAL_DIR,
    materialize_crystal,
)
from ovp_pipeline.runtime import VaultLayout


def _seed_evolves_edge(vault_dir, source: str, target: str, subtype: str) -> None:
    """Insert a fake EVOLVES edge directly into graph_edges so the materializer
    can pick it up without running the full promotion pipeline."""
    rebuild_knowledge_index(vault_dir)  # creates the schema
    db_path = VaultLayout.from_vault(vault_dir).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO graph_edges "
            "(pack, edge_id, source_object_id, target_object_id, edge_kind, weight, evidence_source_slug) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "research-tech",
                f"e_{source}_{target}",
                source,
                target,
                f"evolves:{subtype}",
                1.0,
                source,
            ),
        )
        conn.commit()


def _basic_snapshot() -> dict:
    return {
        "generated_at": "2026-04-24T00:00:00Z",
        "active_topics": [
            {"object_id": "rag", "title": "RAG"},
            {"object_id": "ai-agent", "title": "AI Agent"},
        ],
        "changed_objects": [
            {"object_id": "rag", "title": "RAG"},
        ],
        "insights": [
            {
                "kind": "evolution_evolves",
                "title": "RAG replaces vanilla retrieval",
                "object_ids": ["rag", "vanilla-retrieval"],
            },
        ],
        "priority_items": [
            {"signal_id": "s1", "signal_type": "stale_summary", "title": "RAG summary stale"},
        ],
        "unresolved_issues": [
            {"signal_id": "s1", "signal_type": "stale_summary", "title": "RAG summary stale"},
        ],
    }


def test_materialize_writes_frontmatter_and_sections(temp_vault):
    snapshot = _basic_snapshot()
    record = materialize_crystal(snapshot, temp_vault, when=date(2026, 4, 24))

    assert record.created is True
    assert record.path.exists()
    assert record.path.parent == temp_vault.joinpath(*CRYSTAL_DIR)

    content = record.path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert f"crystal_id: {record.crystal_id}" in content
    assert "type: crystal" in content
    assert "assembly_recipe: operator_briefing" in content
    assert "date: 2026-04-24" in content
    # source_object_ids include changed_objects + active_topics + insight ids
    assert "rag" in content and "ai-agent" in content and "vanilla-retrieval" in content
    # Body sections
    assert "## Priority Items" in content
    assert "## Active Topics" in content
    assert "## Insights" in content
    assert "## EVOLVES Relations" in content
    assert "## Source Objects" in content


def test_materialize_is_idempotent_for_same_snapshot(temp_vault):
    """Running twice with the same snapshot produces the same crystal_id and
    leaves the file unchanged on the second run — `created=False`."""
    snapshot = _basic_snapshot()
    first = materialize_crystal(snapshot, temp_vault, when=date(2026, 4, 24))
    second = materialize_crystal(snapshot, temp_vault, when=date(2026, 4, 24))

    assert first.crystal_id == second.crystal_id
    assert first.path == second.path
    assert second.created is False


def test_materialize_changes_id_when_content_changes(temp_vault):
    """A different priority item changes the salient hash → new crystal_id,
    new file. Prior Crystal stays put as a historical record."""
    base = _basic_snapshot()
    first = materialize_crystal(base, temp_vault, when=date(2026, 4, 24))

    mutated = _basic_snapshot()
    mutated["priority_items"].append(
        {"signal_id": "s2", "signal_type": "contradiction_open", "title": "Contradiction X"}
    )
    second = materialize_crystal(mutated, temp_vault, when=date(2026, 4, 24))

    assert first.crystal_id != second.crystal_id
    assert first.path.exists() and second.path.exists()
    assert first.path != second.path


def test_materialize_surfaces_evolves_relations(temp_vault):
    """EVOLVES edges in graph_edges that touch any source object id show up
    in both frontmatter and the rendered EVOLVES section."""
    _seed_evolves_edge(temp_vault, "rag", "vanilla-retrieval", "replaces")

    snapshot = _basic_snapshot()
    record = materialize_crystal(snapshot, temp_vault, when=date(2026, 4, 24))

    assert len(record.evolves_relations) == 1
    rel = record.evolves_relations[0]
    assert rel == {"source": "rag", "target": "vanilla-retrieval", "subtype": "replaces"}

    content = record.path.read_text(encoding="utf-8")
    assert "evolves_relations:" in content
    assert "[[rag]] **replaces** [[vanilla-retrieval]]" in content


def test_cli_runs_end_to_end_on_empty_vault(temp_vault, monkeypatch, capsys):
    """The CLI must not crash on an empty vault with no signals — it should
    write an empty Crystal documenting the (empty) state."""
    monkeypatch.setattr(
        "sys.argv", ["ovp-build-crystals", "--vault-dir", str(temp_vault), "--json"]
    )
    rc = build_crystals_main()
    assert rc == 0

    out = capsys.readouterr().out
    assert "crystal_id" in out
    assert "operator_briefing" in (
        temp_vault / "40-Resources" / "Crystals"
    ).iterdir().__next__().read_text(encoding="utf-8")
