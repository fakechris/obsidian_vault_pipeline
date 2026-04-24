"""Phase 38 — EVOLVES typing on SemanticRelationCandidate.

Round-trips a `relation_type="evolves" + relation_subtype="replaces"`
candidate through extract → promote → relations + graph_edges, and confirms
extractor rejection on missing/unknown subtype.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ovp_pipeline.extraction.semantic_relations import (
    SemanticRelationCandidate,
    extract_relations,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index
from ovp_pipeline.packs.loader import load_pack
from ovp_pipeline.relation_promotion import promote_candidates
from ovp_pipeline.runtime import VaultLayout


class _StubProposer:
    def __init__(self, raw):
        self._raw = raw

    def propose(self, text, *, source_slug, vocabulary, known_object_ids):
        return list(self._raw)


def _seed_deep_dive(vault: Path) -> Path:
    body = (
        "# Memory\n\n"
        "## Background\n"
        "RAG replaces vanilla retrieval as the substrate for long-context recall, "
        "according to recent benchmarks.\n"
    )
    target = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "memory.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def test_evolves_default_subtype_vocabulary():
    """The default evolves vocab matches Nowledge Mem v0.6 — extending packs
    can override but the platform default must stay stable so heuristics can
    rely on the four canonical subtypes."""
    pack = load_pack("research-tech")
    assert pack.evolves_relation_types() == (
        "replaces",
        "enriches",
        "confirms",
        "challenges",
    )


def test_extractor_accepts_evolves_with_known_subtype(temp_vault):
    pack = load_pack("research-tech")
    deep_dive = _seed_deep_dive(temp_vault)
    raw = SemanticRelationCandidate(
        relation_type="evolves",
        relation_subtype="replaces",
        source_object_id="rag",
        target_object_id="vanilla-retrieval",
        source_slug="memory",
        evidence_quote="RAG replaces vanilla retrieval",
        confidence=0.9,
    )
    report = extract_relations(
        deep_dive,
        pack=pack,
        vault_dir=temp_vault,
        proposer=_StubProposer([raw]),
        known_object_ids=["rag", "vanilla-retrieval"],
        object_kinds={"rag": "concept", "vanilla-retrieval": "concept"},
    )
    assert len(report.candidates) == 1
    assert report.candidates[0].relation_subtype == "replaces"


def test_extractor_rejects_evolves_with_missing_subtype(temp_vault):
    pack = load_pack("research-tech")
    deep_dive = _seed_deep_dive(temp_vault)
    raw = SemanticRelationCandidate(
        relation_type="evolves",
        relation_subtype="",
        source_object_id="rag",
        target_object_id="vanilla-retrieval",
        source_slug="memory",
        evidence_quote="RAG replaces vanilla retrieval",
        confidence=0.9,
    )
    report = extract_relations(
        deep_dive,
        pack=pack,
        vault_dir=temp_vault,
        proposer=_StubProposer([raw]),
        known_object_ids=["rag", "vanilla-retrieval"],
        object_kinds={"rag": "concept", "vanilla-retrieval": "concept"},
    )
    assert report.candidates == []
    assert report.rejected[0][1] == "missing_relation_subtype"


def test_extractor_rejects_evolves_with_unknown_subtype(temp_vault):
    pack = load_pack("research-tech")
    deep_dive = _seed_deep_dive(temp_vault)
    raw = SemanticRelationCandidate(
        relation_type="evolves",
        relation_subtype="invents",  # not in default vocab
        source_object_id="rag",
        target_object_id="vanilla-retrieval",
        source_slug="memory",
        evidence_quote="RAG replaces vanilla retrieval",
        confidence=0.9,
    )
    report = extract_relations(
        deep_dive,
        pack=pack,
        vault_dir=temp_vault,
        proposer=_StubProposer([raw]),
        known_object_ids=["rag", "vanilla-retrieval"],
        object_kinds={"rag": "concept", "vanilla-retrieval": "concept"},
    )
    assert report.candidates == []
    assert report.rejected[0][1] == "unknown_relation_subtype"


def test_promote_evolves_writes_composite_relation_type(temp_vault):
    """End-to-end: promoted EVOLVES candidate stores `evolves:{subtype}` in
    both `relations.relation_type` and `graph_edges.edge_kind`. Composite
    encoding is the design choice — see `_effective_relation_type` — so no
    schema migration is needed for the v0.6 vocabulary roll-out."""
    pack = load_pack("research-tech")
    layout = VaultLayout.from_vault(temp_vault)
    rebuild_knowledge_index(temp_vault)

    cand = SemanticRelationCandidate(
        relation_type="evolves",
        relation_subtype="replaces",
        source_object_id="rag",
        target_object_id="vanilla-retrieval",
        source_slug="memory",
        evidence_quote="RAG replaces vanilla retrieval",
        confidence=0.9,
        locator="section#background@0",
        content_hash="hash-evolves",
        retrieval_context="…",
        pack=pack.name,
    )
    report = promote_candidates([cand], pack=pack, layout=layout)
    assert report.lane_counts() == {"auto": 1, "escalate": 0, "reject": 0}

    with sqlite3.connect(layout.knowledge_db) as conn:
        rel_rows = conn.execute(
            "SELECT relation_type FROM relations WHERE pack = ?",
            (pack.name,),
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT edge_kind FROM graph_edges WHERE pack = ?",
            (pack.name,),
        ).fetchall()
    assert ("evolves:replaces",) in rel_rows
    assert ("evolves:replaces",) in edge_rows


def test_promote_evolves_survives_rebuild(temp_vault):
    """Composite type must round-trip through rebuild_knowledge_index — the
    JSONL replay path is what restores promoted rows after the projection
    truncates the table."""
    pack = load_pack("research-tech")
    layout = VaultLayout.from_vault(temp_vault)
    rebuild_knowledge_index(temp_vault)

    cand = SemanticRelationCandidate(
        relation_type="evolves",
        relation_subtype="confirms",
        source_object_id="rag",
        target_object_id="vanilla-retrieval",
        source_slug="memory",
        evidence_quote="RAG replaces vanilla retrieval",
        confidence=0.9,
        locator="section#background@0",
        content_hash="hash-evolves",
        retrieval_context="…",
        pack=pack.name,
    )
    promote_candidates([cand], pack=pack, layout=layout)
    rebuild_knowledge_index(temp_vault)

    with sqlite3.connect(layout.knowledge_db) as conn:
        rel_rows = conn.execute(
            "SELECT relation_type FROM relations WHERE pack = ?",
            (pack.name,),
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT edge_kind FROM graph_edges WHERE pack = ?",
            (pack.name,),
        ).fetchall()
    assert ("evolves:confirms",) in rel_rows
    assert ("evolves:confirms",) in edge_rows
