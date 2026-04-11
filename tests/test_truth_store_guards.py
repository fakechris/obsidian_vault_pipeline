from __future__ import annotations

from openclaw_pipeline.truth_store import _detect_contradictions


def test_detect_contradictions_ignores_not_only_phrases():
    claims = [
        ("one::a", "one", "page_summary", "Agent harness supports local-first execution for operators.", 1.0),
        ("two::b", "two", "page_summary", "Agent harness not only supports local-first execution for operators but also improves safety.", 1.0),
    ]

    contradictions = _detect_contradictions(claims)

    assert contradictions == []
