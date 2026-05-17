"""PR2a — knowledge-index embedding chunk size cap.

A page body with no ``## `` headings used to become a single chunk of
the entire body (observed ~903k chars) and be fed whole into the
embedding backend.  ``_chunk_page_body`` now hard-caps every chunk.
"""

from __future__ import annotations

from ovp_pipeline.knowledge_index import (
    _MAX_CHUNK_CHARS,
    _chunk_page_body,
    _split_to_cap,
)


def test_empty_body_yields_no_chunks():
    assert _chunk_page_body("", "Title") == []
    assert _chunk_page_body("   \n  \n", "Title") == []


def test_short_unsectioned_body_single_chunk_with_fallback_title():
    chunks = _chunk_page_body("just a short body", "Fallback")
    assert chunks == [("Fallback", "just a short body")]


def test_unsectioned_long_body_is_split_and_each_chunk_capped():
    body = "x" * (_MAX_CHUNK_CHARS * 5 + 123)
    chunks = _chunk_page_body(body, "BigDoc")
    assert len(chunks) > 1
    for title, text in chunks:
        assert title == "BigDoc"
        assert len(text) <= _MAX_CHUNK_CHARS


def test_section_titles_preserved_and_long_section_split():
    long_section = "para. " * 2000  # well over the cap
    body = f"## Intro\nshort intro text\n\n## Deep\n{long_section}"
    chunks = _chunk_page_body(body, "FB")
    titles = {t for t, _ in chunks}
    assert "Intro" in titles
    assert "Deep" in titles
    # every Deep chunk respects the cap
    deep = [c for t, c in chunks if t == "Deep"]
    assert len(deep) > 1
    assert all(len(c) <= _MAX_CHUNK_CHARS for c in deep)
    # the short Intro stays a single chunk
    intro = [c for t, c in chunks if t == "Intro"]
    assert intro == ["short intro text"]


def test_split_to_cap_overlaps_adjacent_pieces():
    text = "abcdefghij" * 100  # 1000 chars
    pieces = _split_to_cap(text, max_chars=300, overlap=50)
    assert all(len(p) <= 300 for p in pieces)
    assert len(pieces) > 1
    # reconstruct with the known step; overlap means tail of piece i
    # equals head of piece i+1.
    step = 300 - 50
    for i in range(len(pieces) - 1):
        assert text[(i + 1) * step : (i + 1) * step + 50] == pieces[i + 1][:50]


def test_split_to_cap_blank_input_no_pieces():
    assert _split_to_cap("", 100, 10) == []
    assert _split_to_cap("   ", 100, 10) == []


def test_split_to_cap_zero_overlap_is_contiguous():
    text = "0123456789" * 30  # 300 chars
    pieces = _split_to_cap(text, max_chars=100, overlap=0)
    assert pieces == [text[0:100], text[100:200], text[200:300]]
