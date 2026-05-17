"""PR2b — knowledge-index bounded-flush rebuild.

The rebuild no longer accumulates every page body + every embedding
blob in Python lists.  Rows stream to the DB in capped batches; only
the object-slug subset is retained for the truth-projection builder.
These lock: counts still match the DB across multiple flush batches,
long bodies still produce only capped chunks, and the truth
projection is still produced (the retention trap did not break it).
"""

from __future__ import annotations

import sqlite3

from ovp_pipeline import knowledge_index
from ovp_pipeline.knowledge_index import _MAX_CHUNK_CHARS, rebuild_knowledge_index
from ovp_pipeline.runtime import VaultLayout


def _evergreen(vault, slug: str, body: str) -> None:
    p = vault / "10-Knowledge" / "Evergreen" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nnote_id: {slug}\ntitle: {slug.replace('-', ' ').title()}\n"
        f"type: evergreen\ndate: 2026-05-17\n---\n\n# {slug}\n\n{body}\n",
        encoding="utf-8",
    )


def test_bounded_flush_multi_batch_counts_match_db(temp_vault, monkeypatch):
    """With batch sizes forced below the page/chunk count, the loop
    flushes several times — stats must still equal the DB row counts."""
    monkeypatch.setattr(knowledge_index, "_PAGE_FLUSH_BATCH", 2)
    monkeypatch.setattr(knowledge_index, "_EMBED_FLUSH_BATCH", 2)

    for i in range(7):
        _evergreen(
            temp_vault,
            f"concept-{i}",
            f"## Overview\nBody for concept {i}.\n\n## Detail\nMore on {i}.",
        )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db_path) as conn:
        pages = conn.execute("SELECT COUNT(*) FROM pages_index").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM page_fts").fetchone()[0]
        embeds = conn.execute("SELECT COUNT(*) FROM page_embeddings").fetchone()[0]
        timeline = conn.execute("SELECT COUNT(*) FROM timeline_events").fetchone()[0]

    assert result["pages_indexed"] == 7 == pages == fts
    assert result["embedding_chunks_indexed"] == embeds
    assert result["timeline_events_indexed"] == timeline
    assert embeds > 0  # streamed across multiple flush batches


def test_long_body_chunks_all_capped_in_db(temp_vault):
    """An un-sectioned long body must not produce an over-cap chunk
    row in page_embeddings."""
    _evergreen(temp_vault, "huge-note", "word " * (_MAX_CHUNK_CHARS))  # ~5x cap
    _evergreen(temp_vault, "small-note", "short body")

    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db_path) as conn:
        lengths = [
            len(r[0])
            for r in conn.execute(
                "SELECT chunk_text FROM page_embeddings WHERE slug = 'huge-note'"
            ).fetchall()
        ]

    assert lengths, "huge-note should have produced embedding chunks"
    assert len(lengths) > 1
    assert all(n <= _MAX_CHUNK_CHARS for n in lengths)


def test_truth_projection_still_produced_after_bounded_flush(temp_vault, monkeypatch):
    """Retaining only the object-slug subset must still feed the
    truth-projection builder — objects/claims/graph stay populated."""
    monkeypatch.setattr(knowledge_index, "_PAGE_FLUSH_BATCH", 1)

    for i in range(3):
        _evergreen(
            temp_vault,
            f"obj-{i}",
            f"## Definition\nObject {i} is a thing.\n\n"
            f"Related to [[obj-{(i + 1) % 3}]].",
        )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db_path) as conn:
        objects = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]

    assert result["objects_indexed"] == objects
    assert objects > 0


def test_high_event_density_page_flushes_timeline_independently(
    temp_vault, monkeypatch
):
    """A single page can emit many timeline events; timeline must
    flush on its own size, not only at the page-batch boundary, so
    timeline_batch cannot grow unbounded between page flushes."""
    monkeypatch.setattr(knowledge_index, "_TIMELINE_FLUSH_BATCH", 10)
    monkeypatch.setattr(knowledge_index, "_PAGE_FLUSH_BATCH", 1000)

    # 60 dated headings on ONE page => 60 heading_date events (+1
    # page_date), far above the forced timeline batch of 10, while
    # the page batch never fills — only the independent timeline
    # flush can keep the batch bounded.
    headings = "\n".join(f"## 2026-{m:02d}\nnote {m}" for m in range(1, 13)) * 5
    _evergreen(temp_vault, "changelog", headings)

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db_path) as conn:
        timeline = conn.execute(
            "SELECT COUNT(*) FROM timeline_events WHERE slug = 'changelog'"
        ).fetchone()[0]

    assert timeline > 10  # exceeded the forced batch
    assert result["timeline_events_indexed"] == timeline
