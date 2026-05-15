"""Tests for the M25.4 ``/ops/events/audit`` raw-audit-evidence view.

The hard contract is **card N === audit-page N**: when a card on
``/ops/today`` shows ``View today's 5 evidence events →``,
clicking it must land on a page with exactly 5 rows.  Pre-M25.4
the card's secondary CTA pointed at ``/ops/events`` (timeline
projection), which is a different ledger — card and page disagreed.

These tests lock:

* Card-N === audit-page-N invariant for every state's secondary
  number that's > 0.
* Date filtering matches the card SQL: ``date(timestamp) = ?``.
* Empty / missing-DB paths surface explicit reasons.
* Renderer carries the role banner and the reciprocal link to
  ``/ops/events``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ovp_pipeline.ui.view_models import (
    EVENTS_AUDIT_DEFAULT_LIMIT,
    EVENTS_AUDIT_MAX_LIMIT,
    build_events_audit_payload,
    build_today_digest_payload,
)
from ovp_pipeline.commands._ui_renderers import (
    _render_events_audit_page,
    _render_events_page,
)


_AUDIT_EVENTS_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL,
    event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
"""


def _seed_audit(tmp_path: Path, rows: list[tuple]) -> Path:
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUDIT_EVENTS_SCHEMA)
    conn.executemany(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)", rows,
    )
    conn.commit()
    conn.close()
    return db_path


# ── Availability / error paths ────────────────────────────────────


def test_unavailable_when_db_missing(tmp_path):
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key="2026-05-13",
    )
    assert payload["available"] is False
    assert "knowledge_index" in payload["reason"]


def test_empty_event_types_returns_recent_rows_across_all_types(tmp_path):
    """M25.4 (codex review on PR #239): when the page is hit without
    an event_types filter (e.g. operator landed here from the
    timeline-projection banner), default to showing the N most
    recent audit_events rows across ALL event_types rather than
    a useless empty page."""
    today_iso = "2026-05-13T08:00:00"
    _seed_audit(tmp_path, [
        ("pipeline.jsonl", "article_intake_only", "src-a", "s", today_iso, "{}"),
        ("pipeline.jsonl", "absorb_parse_error", "src-b", "s", today_iso, "{}"),
    ])
    payload = build_events_audit_payload(
        tmp_path, event_types=(), date_key="",
    )
    assert payload["available"] is True
    # Both seeded rows surface, despite no event_types filter.
    assert payload["total"] == 2
    types_in_rows = {r["event_type"] for r in payload["rows"]}
    assert types_in_rows == {"article_intake_only", "absorb_parse_error"}


# ── Filtering ─────────────────────────────────────────────────────


def test_event_type_filter_isolates_rows(tmp_path):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    today_date = today[:10]
    _seed_audit(tmp_path, [
        ("pipeline.jsonl", "article_intake_only", "src-a", "s", today, "{}"),
        ("pipeline.jsonl", "article_intake_only", "src-b", "s", today, "{}"),
        ("pipeline.jsonl", "absorb_parse_error", "src-c", "s", today, "{}"),
    ])
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key=today_date,
    )
    assert payload["total"] == 2
    assert all(r["event_type"] == "article_intake_only" for r in payload["rows"])


def test_date_filter_excludes_other_days(tmp_path):
    today_iso = "2026-05-13T08:00:00"
    other_iso = "2026-05-12T08:00:00"
    _seed_audit(tmp_path, [
        ("pipeline.jsonl", "article_intake_only", "src-a", "s", today_iso, "{}"),
        ("pipeline.jsonl", "article_intake_only", "src-b", "s", other_iso, "{}"),
    ])
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key="2026-05-13",
    )
    assert payload["total"] == 1


# ── Card N === audit-page N contract ──────────────────────────────


def test_card_n_equals_audit_page_n(tmp_path):
    """The M25 hybrid card promises ``View today's N events →``
    lands on exactly N rows.  Seed a mix of intake + failure events
    and verify each card's secondary count matches its audit-page
    total."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    today_date = today[:10]
    rows = [
        ("pipeline.jsonl", "article_intake_only", f"src-{i}", "s",
         today, "{}")
        for i in range(4)
    ] + [
        ("pipeline.jsonl", "absorb_parse_error", f"src-f{i}", "s",
         today, "{}")
        for i in range(2)
    ]
    _seed_audit(tmp_path, rows)
    digest = build_today_digest_payload(tmp_path)

    for card in digest["cards"]:
        if card["event_count"] == 0 or not card["event_types"]:
            continue
        audit = build_events_audit_payload(
            tmp_path,
            event_types=tuple(card["event_types"]),
            date_key=today_date,
        )
        assert audit["total"] == card["event_count"], (
            f"Card N != audit page N for {card['id']}: "
            f"card={card['event_count']} audit={audit['total']}"
        )


# ── Pagination ────────────────────────────────────────────────────


def test_limit_clamped_to_max(tmp_path):
    _seed_audit(tmp_path, [])
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key="2026-05-13",
        limit=EVENTS_AUDIT_MAX_LIMIT * 10,
    )
    assert payload["limit"] == EVENTS_AUDIT_MAX_LIMIT


def test_card_secondary_href_carries_limit_above_event_count(tmp_path):
    """M25.4 (codex review on PR #239): when a card has more than
    the default 200 events, the secondary CTA URL must carry a
    limit big enough to surface all rows — otherwise the audit
    page silently truncates and the card-N === page-N contract
    breaks for high-volume days."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    # Seed 250 intake rows — over the default 200 limit.
    rows = [
        ("pipeline.jsonl", "article_intake_only", f"src-{i:03d}", "s",
         today, "{}")
        for i in range(250)
    ]
    _seed_audit(tmp_path, rows)
    digest = build_today_digest_payload(tmp_path)
    received = next(c for c in digest["cards"] if c["id"] == "Received")
    assert received["event_count"] == 250
    href = received["event_href"]
    # URL must carry limit >= 250 (or hit the audit MAX cap).
    import re
    match = re.search(r"limit=(\d+)", href)
    assert match is not None
    url_limit = int(match.group(1))
    assert url_limit >= 250, (
        f"Secondary CTA limit {url_limit} is below event_count 250 "
        "— high-volume days will silently truncate"
    )


def test_zero_limit_falls_back_to_default(tmp_path):
    _seed_audit(tmp_path, [])
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key="2026-05-13",
        limit=0,
    )
    assert payload["limit"] == EVENTS_AUDIT_DEFAULT_LIMIT


# ── Renderer ──────────────────────────────────────────────────────


def test_audit_renderer_carries_role_banner(tmp_path):
    """Page must explain its role + link to /ops/events for the
    timeline-projection view."""
    today_iso = "2026-05-13T08:00:00"
    _seed_audit(tmp_path, [
        ("pipeline.jsonl", "article_intake_only", "src-a", "s", today_iso, "{}"),
    ])
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key="2026-05-13",
    )
    html = _render_events_audit_page(payload)
    assert "Raw audit evidence" in html
    assert "/ops/events" in html  # link to timeline-projection page
    # Filter chip surfaces the event_type so the page's scope is
    # explicit.
    assert "article_intake_only" in html


def test_audit_renderer_shows_honest_zero_on_empty_match(tmp_path):
    """When the filter matches zero rows, the page still renders
    cleanly with the honest-zero footer rather than an empty
    table."""
    _seed_audit(tmp_path, [])
    payload = build_events_audit_payload(
        tmp_path,
        event_types=("article_intake_only",),
        date_key="2026-05-13",
    )
    html = _render_events_audit_page(payload)
    # Honest-zero phrase.
    assert "may mean" in html.lower() or "may mean" in html


def test_events_dossier_carries_reciprocal_banner(tmp_path):
    """``/ops/events`` (timeline-projection view) must carry the
    reciprocal banner explaining it is NOT raw audit_events and
    linking to /ops/events/audit.  Pairs with the audit page's
    banner; together they remove the M24.0 two-ledger confusion."""
    from ovp_pipeline.ui.view_models import build_event_dossier_payload

    # An empty audit_events table is enough — we're checking the
    # banner renders, not the dossier content.
    _seed_audit(tmp_path, [])
    payload = build_event_dossier_payload(tmp_path)
    html = _render_events_page(payload)
    assert "Timeline projection view" in html
    assert "/ops/events/audit" in html
