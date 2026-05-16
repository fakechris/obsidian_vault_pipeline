"""Shared audit-timestamp parsing and operator-local day bucketing.

Audit rows carry mixed timestamp formats with DIFFERENT timezone
semantics:

* ``event_emitter.emit`` writes UTC ISO-8601
  (``2026-05-14T12:30:00Z`` / ``+00:00``).
* the older ``PipelineLogger`` path writes
  ``datetime.now().isoformat()`` — a NAIVE *local* wall time.

A lexicographic SQL compare across the ``T``/space separator
boundary, or a ``date(timestamp)`` bucket that treats a naive-local
string and a UTC-``Z`` string as the same clock, silently
misclassifies rows.  Near-midnight events land on the wrong day and
the two producers disagree on the day boundary.

This module is the single source of truth so ``refresh_ops`` (the
canonical-evidence window) and the ``/ops/today`` daily cards bucket
identically.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

__all__ = ["parse_audit_ts", "local_day"]

_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)
_OFFSET_RE = re.compile(r"[+-]\d{2}:?\d{2}$")


def parse_audit_ts(raw: str) -> datetime | None:
    """Parse an audit timestamp to an aware datetime, or None.

    Explicit ``Z`` / numeric offset → UTC (``event_emitter`` always
    emits UTC).  A naive value → the machine's local wall time
    (``PipelineLogger``); ``astimezone()`` on a naive datetime
    attaches the local tz, so a freshly-emitted row is not
    re-clocked by the operator's UTC offset.
    """
    s = (raw or "").strip()
    if not s:
        return None
    s = s.replace("T", " ", 1)
    had_tz = False
    if s.endswith("Z"):
        s = s[:-1]
        had_tz = True
    m = _OFFSET_RE.search(s)
    if m:
        s = s[: m.start()]
        had_tz = True
    s = s.strip()
    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if had_tz:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    return None


def local_day(raw: str) -> str | None:
    """Operator-local calendar day (``YYYY-MM-DD``) for an audit
    timestamp, or None if unparseable.

    All rows are normalized to the operator's local timezone before
    the date is taken, so a UTC-``Z`` row and a naive-local row that
    happened at the same wall-clock instant bucket to the same day.
    """
    dt = parse_audit_ts(raw)
    if dt is None:
        return None
    return dt.astimezone().date().isoformat()
