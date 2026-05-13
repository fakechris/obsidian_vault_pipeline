"""Tests for M23 / BL-097 — digest instrumentation + /ops/digest-health.

Covers:
* ``_wrap_digest_links`` rewrites maintainer markdown links through
  ``/digest/click`` while leaving non-maintainer URLs alone.
* ``_digest_action_shape`` returns the closed vocabulary the
  /ops/digest-health metrics rely on.
* ``build_digest_health_payload`` reads ``audit_events`` and computes
  skip rate + intake-reflection rate + click-through breakdown.
* ``_render_digest_health_page`` renders all three panels and
  handles the "no data yet" stubs honestly.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ovp_pipeline.commands._ui_renderers import _render_digest_health_page
from ovp_pipeline.commands.digest_handler import (
    _digest_action_shape,
    _wrap_digest_links,
)
from ovp_pipeline.ui.view_models import build_digest_health_payload


# ── _wrap_digest_links ─────────────────────────────────────────────


def test_wrap_rewrites_ops_queue_link():
    md = "Click [Resolve →](/ops/queue/contradictions?status=open) for more."
    out = _wrap_digest_links(md, day="2026-05-13")
    assert "/digest/click?to=" in out
    assert "action=resolve_contradiction" in out
    assert "day=2026-05-13" in out
    # Label text preserved.
    assert "[Resolve →]" in out
    # Original URL is URL-encoded inside `to=`.
    assert "%2Fops%2Fqueue%2Fcontradictions%3Fstatus%3Dopen" in out


def test_wrap_rewrites_cluster_link_with_run_synthesis_action():
    md = "[Open cluster →](/ops/cluster?id=cluster::memory-systems)"
    out = _wrap_digest_links(md, day="2026-05-13")
    assert "action=run_synthesis" in out


def test_wrap_rewrites_note_path_link_with_read_source_action():
    md = "[Source](/note?path=10-Knowledge/Evergreen/test.md)"
    out = _wrap_digest_links(md, day="2026-05-13")
    assert "action=read_source" in out


def test_wrap_preserves_external_urls():
    """https/external links pass through unchanged — only OVP
    internal maintainer + note links get wrapped."""
    md = "Reference: [paper](https://example.com/paper.pdf)"
    out = _wrap_digest_links(md, day="2026-05-13")
    assert out == md


def test_wrap_preserves_anchor_links():
    """In-page anchors pass through — they're not navigation."""
    md = "See [section below](#summary) for context."
    out = _wrap_digest_links(md, day="2026-05-13")
    assert out == md


def test_wrap_is_idempotent():
    """Already-wrapped links survive a second pass unchanged."""
    md = "[Resolve →](/ops/queue/contradictions?status=open)"
    first = _wrap_digest_links(md, day="2026-05-13")
    second = _wrap_digest_links(first, day="2026-05-13")
    assert first == second


def test_wrap_skips_non_maintainer_ops_paths():
    """``/ops/`` paths NOT in the wrap allowlist (e.g.
    ``/ops/runs``) pass through.  Keeps the vocabulary closed."""
    md = "[Runs](/ops/runs)"
    out = _wrap_digest_links(md, day="2026-05-13")
    assert out == md


# ── _digest_action_shape ───────────────────────────────────────────


@pytest.mark.parametrize("url,expected", [
    ("/ops/queue/contradictions?status=open", "resolve_contradiction"),
    ("/ops/contradictions", "resolve_contradiction"),
    ("/ops/cluster?id=foo", "run_synthesis"),
    ("/note?path=10-Knowledge/Evergreen/x.md", "read_source"),
    ("/ops/today", "review_today"),
    ("/something-else", "other"),
])
def test_action_shape_vocabulary(url, expected):
    assert _digest_action_shape(url) == expected


# ── Health payload ─────────────────────────────────────────────────


def _make_audit_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE audit_events (
            source_log TEXT, event_type TEXT, slug TEXT, session_id TEXT,
            timestamp TEXT, payload_json TEXT
        );
    """)
    conn.commit()
    conn.close()
    return tmp_path


def _seed_audit(
    vault: Path, event_type: str, *, payload: dict | None = None, day: str | None = None
) -> None:
    db_path = vault / "60-Logs" / "knowledge.db"
    ts = (day or datetime.now(timezone.utc).strftime("%Y-%m-%d")) + "T12:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", event_type, "", "s", ts, json.dumps(payload or {})),
    )
    conn.commit()
    conn.close()


def test_health_unavailable_when_no_db(tmp_path: Path):
    payload = build_digest_health_payload(tmp_path)
    assert payload["available"] is False


def test_health_skip_rate_high_when_gate_works(tmp_path: Path):
    vault = _make_audit_db(tmp_path)
    # 1 generated, 4 skipped → skip rate = 80%.
    _seed_audit(vault, "digest_generated", payload={"layer0_events": 5, "window_end": "2026-05-13"})
    for _ in range(4):
        _seed_audit(vault, "digest_skipped_no_change", payload={"input_hash": "h"})
    payload = build_digest_health_payload(vault)
    assert payload["generated_count"] == 1
    assert payload["skipped_count"] == 4
    assert payload["total_attempts"] == 5
    assert payload["skip_rate"] == pytest.approx(0.8)


def test_health_intake_reflection_rate_computed_per_active_day(tmp_path: Path):
    """Active day = ≥ 3 article_processed events.  Reflected =
    digest_generated for that day had layer0_events > 0."""
    vault = _make_audit_db(tmp_path)
    day = "2026-05-13"
    # 3 article_processed on this day → active.
    for i in range(3):
        _seed_audit(vault, "article_processed", day=day)
    # Digest_generated for this day with layer0_events > 0 → reflected.
    _seed_audit(vault, "digest_generated", payload={
        "layer0_events": 3, "window_end": day,
    })
    payload = build_digest_health_payload(vault)
    assert payload["intake_reflection_rate"] == pytest.approx(1.0)


def test_health_intake_reflection_misses_when_layer0_zero(tmp_path: Path):
    vault = _make_audit_db(tmp_path)
    day = "2026-05-13"
    for i in range(3):
        _seed_audit(vault, "article_processed", day=day)
    # Digest generated but layer0 came back 0 → reflected=False.
    _seed_audit(vault, "digest_generated", payload={
        "layer0_events": 0, "window_end": day,
    })
    payload = build_digest_health_payload(vault)
    assert payload["intake_reflection_rate"] == pytest.approx(0.0)


def test_health_inactive_days_excluded(tmp_path: Path):
    """A day with < 3 articles doesn't count toward the rate either way."""
    vault = _make_audit_db(tmp_path)
    day = "2026-05-13"
    _seed_audit(vault, "article_processed", day=day)  # only 1
    _seed_audit(vault, "digest_generated", payload={
        "layer0_events": 0, "window_end": day,
    })
    payload = build_digest_health_payload(vault)
    # No active days → rate is None (honest), not zero.
    assert payload["intake_reflection_rate"] is None


def test_health_click_breakdown_groups_by_action(tmp_path: Path):
    vault = _make_audit_db(tmp_path)
    _seed_audit(vault, "digest_clicked_through", payload={"action": "resolve_contradiction"})
    _seed_audit(vault, "digest_clicked_through", payload={"action": "resolve_contradiction"})
    _seed_audit(vault, "digest_clicked_through", payload={"action": "run_synthesis"})
    payload = build_digest_health_payload(vault)
    assert payload["click_breakdown"] == {
        "resolve_contradiction": 2, "run_synthesis": 1,
    }


# ── Renderer ───────────────────────────────────────────────────────


def test_render_health_page_renders_three_panels(tmp_path: Path):
    payload = build_digest_health_payload(_make_audit_db(tmp_path))
    html = _render_digest_health_page(payload)
    assert "Idempotency gate" in html
    assert "Intake reflection rate" in html
    assert "Click-through breakdown" in html


def test_render_health_page_handles_unavailable():
    html = _render_digest_health_page({"available": False, "reason": "knowledge_index has not been built yet"})
    assert "Digest health unavailable" in html
    assert "ovp-knowledge-index" in html


def test_render_health_page_shows_no_clicks_message_when_empty(tmp_path: Path):
    payload = build_digest_health_payload(_make_audit_db(tmp_path))
    html = _render_digest_health_page(payload)
    assert "No clicks recorded yet" in html


def test_render_health_page_renders_click_breakdown_table(tmp_path: Path):
    vault = _make_audit_db(tmp_path)
    _seed_audit(vault, "digest_clicked_through", payload={"action": "resolve_contradiction"})
    payload = build_digest_health_payload(vault)
    html = _render_digest_health_page(payload)
    assert "resolve_contradiction" in html
