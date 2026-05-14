"""Reader ``/digests`` list view (M22 / BL-093 + M23.1 calendar).

Daily digest history.  The home page surfaces only the latest
digest; this page lists *all* digests under
``40-Resources/Generated/digests/`` and adds a 30-day calendar
grid showing per-day digest existence + intake activity.

Calendar interactions:
* ``✓ <date>``       → digest exists; click opens ``/note?path=…``
* ``<date> · N ev``  → no digest, N intake events that day; click
                       goes to ``/ops/today?date=<date>`` which has
                       a "Regenerate digest for this day" button
* ``<date>  —``      → no audit events either (genuinely quiet);
                       click goes to ``/ops/today?date=<date>`` so
                       the operator can verify

The flat list below the grid stays unchanged for operators who
prefer to scan filenames.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote


DIGESTS_DIR = "40-Resources/Generated/digests"
KNOWLEDGE_DB_REL = "60-Logs/knowledge.db"
CALENDAR_WINDOW_DAYS = 30

# M24.0 stop-gap: pull intake event_types from the single canonical
# registry (``event_evidence_registry``) so the calendar, the
# ``/ops/today`` Intake card, and the M23 digest's Layer 0 all
# classify the same way.  Before this, three independent lists
# drifted and same-day counts disagreed across surfaces.
from ..event_evidence_registry import event_types_for_category

_INTAKE_EVENT_TYPES = event_types_for_category("intake")


@dataclass(frozen=True)
class DigestRow:
    """One digest entry rendered in the index.

    ``date`` is the ``YYYY-MM-DD`` prefix recovered from the file
    name; ``href`` is the same ``/note?path=…`` URL the home
    page uses; ``teaser`` is the first non-frontmatter
    paragraph (truncated).  ``filename`` is exposed so the
    template can render the underlying file id as a hover-only
    affordance for operators who like that level of detail.
    """

    date: str
    href: str
    teaser: str
    filename: str


def list_digests(vault_dir: Path | str) -> list[DigestRow]:
    """Return every digest file under ``DIGESTS_DIR``, newest first.

    Linear directory scan — there are at most a few hundred
    files for daily output across a year, so the cost is
    negligible.  Frontmatter is stripped from the teaser using
    the same logic as :func:`_build_latest_digest_info` so the
    two views agree.
    """
    folder = Path(vault_dir) / DIGESTS_DIR
    if not folder.is_dir():
        return []
    rows: list[DigestRow] = []
    for path in sorted(folder.glob("*.md"), reverse=True):
        date = path.name[:10]
        teaser = _extract_teaser(path)
        rel = str(path.relative_to(vault_dir))
        href = f"/note?path={quote(rel, safe='')}"
        rows.append(
            DigestRow(date=date, href=href, teaser=teaser, filename=path.name)
        )
    return rows


def _extract_teaser(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = body.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            close_idx = next(
                i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"
            )
            lines = lines[close_idx + 1 :]
        except StopIteration:
            lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) > 220:
            return stripped[:217].rstrip() + "…"
        return stripped
    return ""


@dataclass(frozen=True)
class CalendarCell:
    """One day in the calendar grid.

    Two boolean states drive the cell's class + click target:

    * ``has_digest``    — a file ``YYYY-MM-DD-digest-daily.md`` exists
    * ``intake_count``  — count of intake-event audit rows for the day
                          in operator-local tz (today the calendar
                          treats UTC and local as the same — the
                          ``audit_events.timestamp`` strings start
                          with ``YYYY-MM-DD`` regardless of suffix so
                          a ``LIKE`` filter works without parsing).
    """

    date: str  # YYYY-MM-DD
    has_digest: bool
    intake_count: int
    digest_href: str  # /note?path=... when has_digest else ""
    explore_href: str  # /ops/today?date=...


def build_calendar_cells(
    vault_dir: Path | str,
    *,
    today: date_cls | None = None,
    window_days: int = CALENDAR_WINDOW_DAYS,
) -> list[CalendarCell]:
    """Build the ``window_days``-day calendar window ending at ``today``.

    Newest day last (so the natural left-to-right, top-to-bottom
    grid reads chronologically).  Intake counts come from
    ``audit_events``; missing knowledge.db → all zeros.
    """
    if today is None:
        today = date_cls.today()
    start = today - timedelta(days=window_days - 1)

    digest_dates: dict[str, str] = {}
    folder = Path(vault_dir) / DIGESTS_DIR
    if folder.is_dir():
        for path in folder.glob("*-digest-daily.md"):
            d = path.name[:10]
            try:
                date_cls.fromisoformat(d)
            except ValueError:
                continue
            try:
                rel = str(path.relative_to(vault_dir))
            except ValueError:
                rel = str(path)
            digest_dates[d] = f"/note?path={quote(rel, safe='')}"

    intake_counts: dict[str, int] = {}
    db_path = Path(vault_dir) / KNOWLEDGE_DB_REL
    # CodeRabbit: guard against ``IN ()`` — if the registry ever
    # ships an empty intake category (e.g. operator override yields
    # zero types), the SQL would be invalid.  Skip the query and
    # leave intake_counts empty.
    if db_path.is_file() and _INTAKE_EVENT_TYPES:
        try:
            with sqlite3.connect(db_path) as conn:
                placeholders = ",".join("?" * len(_INTAKE_EVENT_TYPES))
                rows = conn.execute(
                    f"""
                    SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS n
                      FROM audit_events
                     WHERE event_type IN ({placeholders})
                       AND timestamp >= ?
                       AND timestamp <  ?
                     GROUP BY day
                    """,
                    (
                        *_INTAKE_EVENT_TYPES,
                        start.isoformat(),
                        (today + timedelta(days=1)).isoformat(),
                    ),
                ).fetchall()
                intake_counts = {row[0]: int(row[1]) for row in rows}
        except sqlite3.OperationalError:
            intake_counts = {}

    cells: list[CalendarCell] = []
    for offset in range(window_days):
        d = (start + timedelta(days=offset)).isoformat()
        cells.append(
            CalendarCell(
                date=d,
                has_digest=d in digest_dates,
                intake_count=int(intake_counts.get(d, 0)),
                digest_href=digest_dates.get(d, ""),
                explore_href=f"/ops/today?date={d}",
            )
        )
    return cells


def _render_calendar_grid(cells: list[CalendarCell]) -> str:
    """Render the calendar grid + a small legend.

    7-column grid (one column per weekday).  Newest day in the
    bottom-right.  Each cell gets a CSS class encoding its state
    so themes can recolour without touching this renderer.
    """
    if not cells:
        return ""

    # Pad the prefix so the first row aligns Monday → Sunday.  Use
    # ISO weekday() (Mon=0..Sun=6).
    first = date_cls.fromisoformat(cells[0].date)
    leading_blanks = first.weekday()

    def _cell_html(cell: CalendarCell) -> str:
        date_short = cell.date[5:]  # MM-DD for readability
        if cell.has_digest:
            cls = "cal-cell cal-cell-has-digest"
            inner = (
                f"<a href='{escape(cell.digest_href)}'>"
                f"<span class='cal-tick'>✓</span> "
                f"<span class='cal-date'>{escape(date_short)}</span>"
                "</a>"
                f"<a href='{escape(cell.explore_href)}' "
                f"class='cal-explore' title='Inspect this day in /ops/today'>↗</a>"
            )
        elif cell.intake_count > 0:
            cls = "cal-cell cal-cell-has-intake"
            inner = (
                f"<a href='{escape(cell.explore_href)}'>"
                f"<span class='cal-date'>{escape(date_short)}</span>"
                f"<span class='cal-count'>{cell.intake_count}</span>"
                "</a>"
            )
        else:
            cls = "cal-cell cal-cell-empty"
            inner = (
                f"<a href='{escape(cell.explore_href)}'>"
                f"<span class='cal-date'>{escape(date_short)}</span>"
                "<span class='cal-count muted'>—</span>"
                "</a>"
            )
        return f"<div class='{cls}'>{inner}</div>"

    blank_html = "<div class='cal-cell cal-cell-blank'></div>" * leading_blanks
    cells_html = "".join(_cell_html(c) for c in cells)

    legend = (
        "<p class='muted small' style='margin-top:0.4rem'>"
        "✓ digest exists · "
        "N = intake events (click to inspect day) · "
        "— = quiet day"
        "</p>"
    )
    # Calendar styles live in /static/ovp-digests-calendar.css (gemini
    # code review — embed-CSS was hurting cache + maintainability).
    # Loaded by the page shell via _layout's stylesheet block.
    return (
        "<section style='margin:1rem 0'>"
        f"<div class='cal-grid'>{blank_html}{cells_html}</div>"
        f"{legend}"
        "</section>"
    )


def render_digests_list_body(vault_dir: Path | str) -> str:
    """Render the body HTML for ``/digests``."""
    rows = list_digests(vault_dir)
    cells = build_calendar_cells(vault_dir)
    calendar_html = _render_calendar_grid(cells)

    header = (
        "<h1>Daily digests</h1>"
        "<p class='muted'>Last 30 days at a glance — click any day "
        "to open its digest or inspect the audit-event activity.</p>"
        + calendar_html
    )

    if not rows:
        empty = (
            "<p class='muted'>No digest files yet.  Generated digests land in "
            f"<code>{escape(DIGESTS_DIR)}</code> as the pipeline produces "
            "them — typically one per UTC day.</p>"
        )
        return header + empty

    items: list[str] = []
    for row in rows:
        teaser_html = (
            f"<p class='muted small'>{escape(row.teaser)}</p>" if row.teaser else ""
        )
        items.append(
            "<li class='card' style='margin-bottom:0.75rem'>"
            f"<a href='{escape(row.href)}'>"
            f"<strong>{escape(row.date)}</strong>"
            "</a>"
            f"<span class='muted small mono' style='margin-left:0.6rem'>"
            f"{escape(row.filename)}"
            "</span>"
            f"{teaser_html}"
            "</li>"
        )
    list_html = "<ul style='list-style:none;padding:0'>" + "".join(items) + "</ul>"
    return (
        header
        + "<h2 style='margin-top:1.5rem'>All digests</h2>"
        + f"<p class='muted small'>{len(rows)} digest"
        + f"{'s' if len(rows) != 1 else ''} — newest first.</p>"
        + list_html
    )


def neighbour_links(vault_dir: Path | str, current_relpath: str) -> tuple[str, str]:
    """Return ``(prev_href, next_href)`` for the digest at
    ``current_relpath`` (vault-relative).

    Used by the /note route to inject "← previous day · next
    day →" pivots when the operator opens a file under
    ``DIGESTS_DIR``.  Either value is empty when the current
    file has no neighbour on that side.
    """
    rows = list_digests(vault_dir)
    if not rows:
        return "", ""
    # rows are newest-first; "prev" in operator-speak means
    # *older*, i.e. further down the list.
    # CodeRabbit: ``endswith`` on the encoded path can collide on
    # path suffixes (``foo-bar.md`` ends with ``bar.md``); build the
    # exact href the row would have and compare for equality.
    target_href = f"/note?path={quote(current_relpath, safe='')}"
    idx = next(
        (i for i, r in enumerate(rows) if r.href == target_href),
        -1,
    )
    if idx < 0:
        return "", ""
    older = rows[idx + 1].href if idx + 1 < len(rows) else ""
    newer = rows[idx - 1].href if idx > 0 else ""
    return older, newer
