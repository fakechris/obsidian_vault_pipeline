"""PR3 — embedding reuse via page_embeddings.chunk_hash.

A rebuild used to re-run the embedding backend for every chunk every
time.  Now an unchanged chunk (same embed text + same model) reuses
the prior embedding from the still-on-disk DB; only changed chunks
hit the backend.
"""

from __future__ import annotations

import sqlite3

from ovp_pipeline import knowledge_index
from ovp_pipeline.knowledge_index import (
    _migrate_8_to_9_embedding_hash,
    rebuild_knowledge_index,
)
from ovp_pipeline.runtime import VaultLayout


def _evergreen(vault, slug: str, body: str) -> None:
    p = vault / "10-Knowledge" / "Evergreen" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nnote_id: {slug}\ntitle: {slug}\ntype: evergreen\n"
        f"date: 2026-05-17\n---\n\n# {slug}\n\n## Body\n{body}\n",
        encoding="utf-8",
    )


def _count_embed(monkeypatch) -> list[int]:
    calls = [0]
    real = knowledge_index._embed_text

    def counting(text: str) -> bytes:
        calls[0] += 1
        return real(text)

    monkeypatch.setattr(knowledge_index, "_embed_text", counting)
    return calls


def test_second_rebuild_reuses_all_unchanged_embeddings(temp_vault, monkeypatch):
    _evergreen(temp_vault, "alpha", "stable content one")
    _evergreen(temp_vault, "beta", "stable content two")

    first = rebuild_knowledge_index(temp_vault)
    assert first["embedding_chunks_indexed"] > 0
    assert first["embedding_chunks_reused"] == 0  # no prior DB

    calls = _count_embed(monkeypatch)
    second = rebuild_knowledge_index(temp_vault)

    assert calls[0] == 0  # every chunk reused, backend never called
    assert second["embedding_chunks_reused"] == second["embedding_chunks_indexed"]
    assert second["embedding_chunks_indexed"] == first["embedding_chunks_indexed"]


def test_only_changed_chunks_are_recomputed(temp_vault, monkeypatch):
    _evergreen(temp_vault, "alpha", "stable content one")
    _evergreen(temp_vault, "beta", "stable content two")
    first = rebuild_knowledge_index(temp_vault)

    _evergreen(temp_vault, "beta", "CHANGED content two now different")

    calls = _count_embed(monkeypatch)
    second = rebuild_knowledge_index(temp_vault)

    # alpha reused, beta recomputed — backend called, but not for all.
    assert calls[0] >= 1
    assert second["embedding_chunks_reused"] >= 1
    assert second["embedding_chunks_reused"] < second["embedding_chunks_indexed"]
    assert first["embedding_chunks_indexed"] == second["embedding_chunks_indexed"]


def test_model_change_invalidates_reuse(temp_vault, monkeypatch):
    _evergreen(temp_vault, "alpha", "stable content one")
    rebuild_knowledge_index(temp_vault)

    monkeypatch.setattr(
        knowledge_index, "get_model_name", lambda: "different-model-v2"
    )
    calls = _count_embed(monkeypatch)
    second = rebuild_knowledge_index(temp_vault)

    assert second["embedding_chunks_reused"] == 0
    assert calls[0] == second["embedding_chunks_indexed"]


def test_migrate_8_to_9_adds_column_and_index(tmp_path):
    db = tmp_path / "knowledge.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE page_embeddings (
              slug TEXT NOT NULL,
              chunk_index INTEGER NOT NULL,
              section_title TEXT NOT NULL,
              chunk_text TEXT NOT NULL,
              embedding_blob BLOB NOT NULL,
              embedding_model TEXT NOT NULL,
              PRIMARY KEY (slug, chunk_index)
            );
            """
        )
        _migrate_8_to_9_embedding_hash(conn, tmp_path)
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(page_embeddings)")}
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='page_embeddings'"
            )
        }
        # idempotent: a second run must not raise
        _migrate_8_to_9_embedding_hash(conn, tmp_path)

    assert "chunk_hash" in cols
    assert "idx_page_embeddings_hash" in idx


def test_migrate_8_to_9_noop_when_table_absent(tmp_path):
    db = tmp_path / "knowledge.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE pages_index (slug TEXT PRIMARY KEY)")
        # must not raise even though page_embeddings does not exist
        _migrate_8_to_9_embedding_hash(conn, tmp_path)


def test_reuse_survives_into_db_rows(temp_vault):
    _evergreen(temp_vault, "alpha", "persisted content")
    rebuild_knowledge_index(temp_vault)
    rebuild_knowledge_index(temp_vault)

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT chunk_hash FROM page_embeddings WHERE slug='alpha'"
        ).fetchall()

    assert rows
    assert all(len(h[0]) == 64 for h in rows)  # sha256 hex
