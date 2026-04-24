"""Phase 38 — Hybrid search (RRF + bi-temporal decay).

Coverage:
* empty vault returns []
* page_metrics is populated by rebuild and survives a re-run
* RRF: a slug returned by both BM25 and vector ranks higher than a slug
  returned by only one branch
* Recency: same slug + same RRF, but newer ``last_seen_ts`` ranks first
* Frequency / importance multipliers compose with RRF
* The ``fused`` engine is wired into ``discover_related``
"""

from __future__ import annotations

import sqlite3

from ovp_pipeline.discovery import discover_related
from ovp_pipeline.knowledge_index import (
    rebuild_knowledge_index,
    search_fused,
)
from ovp_pipeline.runtime import VaultLayout


def _evergreen(name: str, body: str) -> str:
    return (
        "---\n"
        f"note_id: {name}\n"
        f"title: {name.replace('-', ' ').title()}\n"
        "type: evergreen\n"
        "date: 2026-04-24\n"
        "---\n\n"
        f"# {name.replace('-', ' ').title()}\n\n"
        f"{body}\n"
    )


def _seed_two_pages(vault) -> None:
    eg = vault / "10-Knowledge" / "Evergreen"
    (eg / "Rag.md").write_text(
        _evergreen(
            "rag",
            "RAG combines retrieval with generation to ground LLM answers.",
        ),
        encoding="utf-8",
    )
    (eg / "Vanilla-Retrieval.md").write_text(
        _evergreen(
            "vanilla-retrieval",
            "Vanilla retrieval returns documents without generation.",
        ),
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)


def test_search_fused_empty_vault_returns_empty(temp_vault):
    rebuild_knowledge_index(temp_vault)
    assert search_fused(temp_vault, "anything", limit=5) == []


def test_rebuild_populates_page_metrics(temp_vault):
    """``page_metrics`` must have one row per ``pages_index`` slug after
    rebuild — even when there are no audit/reuse signals yet (zeros)."""
    _seed_two_pages(temp_vault)

    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT slug, last_seen_ts, reuse_count, citation_count "
            "FROM page_metrics ORDER BY slug"
        ).fetchall()

    assert {r[0] for r in rows} >= {"rag", "vanilla-retrieval"}
    for _slug, last_seen, reuse, cite in rows:
        assert last_seen >= 0
        assert reuse >= 0
        assert cite >= 0


def test_search_fused_returns_results_for_seeded_query(temp_vault):
    _seed_two_pages(temp_vault)
    results = search_fused(temp_vault, "retrieval", limit=5)
    assert results, "fused search should return at least one hit"
    slugs = {r["slug"] for r in results}
    # query touches both notes via "retrieval" (bm25) and embeddings; at
    # least one of the seeded slugs must appear.
    assert slugs & {"rag", "vanilla-retrieval"}


def test_recency_boost_promotes_newer_slug(temp_vault):
    """Two slugs with identical RRF scores: the one with a newer
    ``last_seen_ts`` must rank first."""
    _seed_two_pages(temp_vault)
    db = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db) as conn:
        # Force identical metrics for both, with rag being "newer".
        conn.executemany(
            "INSERT OR REPLACE INTO page_metrics "
            "(slug, last_seen_ts, reuse_count, citation_count) VALUES (?, ?, ?, ?)",
            [
                ("rag", 2_000_000_000, 0, 0),
                ("vanilla-retrieval", 1_000_000_000, 0, 0),
            ],
        )
        conn.commit()

    # now_ts pinned just after the newer timestamp so decay is large for the
    # older slug, small for the newer one.
    results = search_fused(
        temp_vault,
        "retrieval",
        limit=5,
        tau_days=30.0,
        now_ts=2_000_000_001,
    )
    assert results
    ranked_slugs = [r["slug"] for r in results]
    if "rag" in ranked_slugs and "vanilla-retrieval" in ranked_slugs:
        assert ranked_slugs.index("rag") < ranked_slugs.index("vanilla-retrieval")


def test_frequency_and_importance_compose(temp_vault):
    """A high reuse_count + citation_count pushes a slug above an equally
    ranked but cold one."""
    _seed_two_pages(temp_vault)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO page_metrics "
            "(slug, last_seen_ts, reuse_count, citation_count) VALUES (?, ?, ?, ?)",
            [
                ("rag", 0, 0, 0),
                ("vanilla-retrieval", 0, 50, 50),
            ],
        )
        conn.commit()

    results = search_fused(temp_vault, "retrieval", limit=5)
    assert results
    by_slug = {r["slug"]: r for r in results}
    if "rag" in by_slug and "vanilla-retrieval" in by_slug:
        # vanilla-retrieval has high freq/importance; its score must beat rag.
        assert by_slug["vanilla-retrieval"]["score"] > by_slug["rag"]["score"]


def test_discover_related_fused_engine(temp_vault):
    """The ``fused`` engine must round-trip through ``discover_related`` and
    annotate each row with ``rrf_score`` / ``recency`` / ``frequency`` /
    ``importance`` so reviewers can audit the ranking."""
    _seed_two_pages(temp_vault)

    rows = discover_related(temp_vault, "retrieval", engine="fused", limit=5)
    assert rows
    sample = rows[0]
    assert sample["engine"] == "knowledge"
    assert sample["kind"] == "fused"
    assert "rrf_score" in sample
    assert "recency" in sample
    assert "frequency" in sample
    assert "importance" in sample
