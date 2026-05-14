"""Tests for the M23.1 /digests calendar grid + past-date regenerate."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ovp_pipeline.commands._digests_list_page import (
    build_calendar_cells,
    render_digests_list_body,
)
from ovp_pipeline.commands.digest_handler import (
    _as_of_for_target_date,
    _enqueue_daily,
    _parse_target_date_from_slug,
)
from ovp_pipeline.digest_config import DigestConfig


def _make_vault(tmp_path: Path) -> Path:
    """Vault skeleton with the directories the calendar reads."""
    (tmp_path / "40-Resources" / "Generated" / "digests").mkdir(parents=True)
    (tmp_path / "50-Inbox" / "02-Tasks").mkdir(parents=True)
    (tmp_path / "60-Logs").mkdir(parents=True)
    return tmp_path


def _make_audit_db(vault: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(vault / "60-Logs" / "knowledge.db")
    conn.executescript("""
        CREATE TABLE audit_events (
            source_log TEXT, event_type TEXT, slug TEXT, session_id TEXT,
            timestamp TEXT, payload_json TEXT
        );
    """)
    conn.commit()
    return conn


def _seed_intake(conn: sqlite3.Connection, day: str, count: int) -> None:
    for i in range(count):
        ts = f"{day}T{i:02d}:30:00+00:00"
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            ("pipeline.jsonl", "article_processed", f"s-{i}", "x", ts, "{}"),
        )
    conn.commit()


# ── Slug parsing ────────────────────────────────────────────────


# Slug parser is a shape-only regex; ``_as_of_for_target_date`` is
# what catches calendar-invalid dates like 2026-13-01.  Split the
# two responsibilities into two clearly-named tests so the
# parametrize stops mixing happy-path and shape-only-bad cases
# (gemini code-review nit on the prior single-table form).
@pytest.mark.parametrize(
    "slug,expected",
    [
        ("daily", ""),
        ("daily-foo", ""),
        ("", ""),
        ("daily-2026-05-11", "2026-05-11"),
        ("daily-2026-12-31", "2026-12-31"),
    ],
)
def test_parse_target_date_extracts_or_returns_empty(slug, expected):
    """Happy path: well-formed suffix → date string; missing or
    malformed suffix → empty string."""
    assert _parse_target_date_from_slug(slug) == expected


def test_parse_target_date_passes_shape_but_invalid_calendar_dates():
    """The regex only checks shape (``\\d{4}-\\d{2}-\\d{2}``); a bad
    calendar date like 2026-13-01 still matches the shape, and
    downstream ``_as_of_for_target_date`` is where it gets caught.
    Documented here so the contract is clear."""
    assert _parse_target_date_from_slug("daily-2026-13-01") == "2026-13-01"
    # The validation gate that catches it:
    assert _as_of_for_target_date(DigestConfig(tz="UTC"), "2026-13-01") is None


def test_as_of_for_target_date_invalid_returns_none():
    cfg = DigestConfig(tz="UTC")
    assert _as_of_for_target_date(cfg, "2026-13-01") is None
    assert _as_of_for_target_date(cfg, "") is None


def test_as_of_for_target_date_returns_end_of_day_in_tz():
    cfg = DigestConfig(tz="UTC")
    as_of = _as_of_for_target_date(cfg, "2026-05-11")
    assert as_of is not None
    assert as_of.year == 2026
    assert as_of.month == 5
    assert as_of.day == 11
    assert as_of.hour == 23


# ── _enqueue_daily ───────────────────────────────────────────────


def test_enqueue_daily_default_filename(tmp_path: Path):
    vault = _make_vault(tmp_path)
    p = _enqueue_daily(vault)
    assert p.name == "DIGEST-daily.md"


def test_enqueue_daily_with_target_date(tmp_path: Path):
    vault = _make_vault(tmp_path)
    p = _enqueue_daily(vault, target_date="2026-05-11")
    assert p.name == "DIGEST-daily-2026-05-11.md"


def test_enqueue_daily_is_idempotent(tmp_path: Path):
    vault = _make_vault(tmp_path)
    p1 = _enqueue_daily(vault, target_date="2026-05-11")
    p1.write_text("modified", encoding="utf-8")
    p2 = _enqueue_daily(vault, target_date="2026-05-11")
    assert p1 == p2
    assert p2.read_text() == "modified"  # didn't overwrite


# ── Calendar cells ───────────────────────────────────────────────


def test_calendar_window_default_size(tmp_path: Path):
    vault = _make_vault(tmp_path)
    cells = build_calendar_cells(vault, today=date(2026, 5, 13))
    assert len(cells) == 30
    # Newest day last → chronological reading order.
    assert cells[-1].date == "2026-05-13"
    assert cells[0].date == "2026-04-14"


def test_calendar_marks_existing_digests(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "40-Resources/Generated/digests" / "2026-05-12-digest-daily.md").write_text(
        "stub", encoding="utf-8"
    )
    cells = build_calendar_cells(vault, today=date(2026, 5, 13))
    has_digest = {c.date: c.has_digest for c in cells}
    assert has_digest["2026-05-12"] is True
    assert has_digest["2026-05-11"] is False
    assert has_digest["2026-05-13"] is False


def test_calendar_counts_intake_per_day(tmp_path: Path):
    vault = _make_vault(tmp_path)
    conn = _make_audit_db(vault)
    _seed_intake(conn, "2026-05-12", 5)
    _seed_intake(conn, "2026-05-10", 2)
    conn.close()
    cells = build_calendar_cells(vault, today=date(2026, 5, 13))
    counts = {c.date: c.intake_count for c in cells}
    assert counts["2026-05-12"] == 5
    assert counts["2026-05-10"] == 2
    assert counts["2026-05-11"] == 0


def test_calendar_ignores_non_intake_event_types(tmp_path: Path):
    vault = _make_vault(tmp_path)
    conn = _make_audit_db(vault)
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "pipeline.jsonl",
            "task_dispatched",
            "x",
            "s",
            "2026-05-12T12:00:00+00:00",
            "{}",
        ),
    )
    conn.commit()
    conn.close()
    cells = build_calendar_cells(vault, today=date(2026, 5, 13))
    counts = {c.date: c.intake_count for c in cells}
    assert counts["2026-05-12"] == 0


def test_calendar_handles_missing_db(tmp_path: Path):
    vault = _make_vault(tmp_path)
    cells = build_calendar_cells(vault, today=date(2026, 5, 13))
    assert all(c.intake_count == 0 for c in cells)


# ── Body rendering ──────────────────────────────────────────────


def test_render_body_includes_calendar_section(tmp_path: Path):
    vault = _make_vault(tmp_path)
    html = render_digests_list_body(vault)
    assert "Last 30 days at a glance" in html
    assert "cal-grid" in html


def test_render_body_marks_digest_cells_with_tick(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "40-Resources/Generated/digests" / "2026-05-12-digest-daily.md").write_text(
        "stub", encoding="utf-8"
    )
    html = render_digests_list_body(vault)
    assert "cal-cell-has-digest" in html
    assert "✓" in html


def test_render_body_marks_intake_cells_with_count(tmp_path: Path):
    vault = _make_vault(tmp_path)
    conn = _make_audit_db(vault)
    # Seed today (calendar's "newest day") so the test is
    # deterministic against the page's date.today() rendering.
    today_iso = date.today().isoformat()
    _seed_intake(conn, today_iso, 7)
    conn.close()
    html = render_digests_list_body(vault)
    assert "cal-cell-has-intake" in html
    assert "7</span>" in html


def test_render_body_empty_vault_keeps_calendar(tmp_path: Path):
    """Even with no digest files, the calendar grid + window
    label should render — the operator can still click days to
    inspect /ops/today."""
    vault = _make_vault(tmp_path)
    html = render_digests_list_body(vault)
    assert "Last 30 days at a glance" in html
    assert "No digest files yet" in html
