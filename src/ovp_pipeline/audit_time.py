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
from datetime import datetime, timedelta, timezone

# A trailing timezone designator: ``Z`` is handled separately; this
# matches a numeric offset with OR without the colon (``+08:00`` /
# ``-0700``) anchored at end of string.  Python 3.10's
# ``datetime.fromisoformat`` rejects the colonless form, and the
# strptime fallback has no ``%z``; peeling the designator to a
# tzinfo BEFORE parsing the offset-free body makes every emitted
# form parse correctly on 3.10/3.11 alike instead of being silently
# dropped (gemini review: ``2026-05-14Z`` previously mis-parsed as
# local time, and offset rows that fromisoformat rejected returned
# ``None``).
_TRAILING_OFFSET_RE = re.compile(r"([+-])(\d{2}):?(\d{2})$")

__all__ = ["parse_audit_ts", "local_day"]

_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)
def parse_audit_ts(raw: str) -> datetime | None:
    """Parse an audit timestamp to an aware datetime, or None.

    Explicit ``Z`` / numeric offset → the represented instant in
    UTC.  A naive value → the machine's local wall time
    (``PipelineLogger``); ``astimezone()`` on a naive datetime
    attaches the local tz, so a freshly-emitted row is not
    re-clocked by the operator's UTC offset.
    """
    s = (raw or "").strip()
    if not s:
        return None

    # Peel any explicit trailing tz designator into ``tz`` and parse
    # an offset-free ``body``.  Doing this BEFORE fromisoformat means
    # the colonless offset, the ``Z``, and a date-only ``Z`` all
    # parse uniformly on 3.10 and 3.11 — and the strptime fallback
    # (which has no ``%z``) still works because ``body`` carries no
    # offset.
    tz: timezone | None = None
    body = s
    if s.endswith("Z"):
        tz = timezone.utc
        body = s[:-1]
    else:
        m = _TRAILING_OFFSET_RE.search(s)
        if m:
            delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
            tz = timezone(delta if m.group(1) == "+" else -delta)
            body = s[: m.start()]

    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(body)
    except ValueError:
        normalized = body.replace("T", " ", 1).strip()
        for fmt in _FORMATS:
            try:
                dt = datetime.strptime(normalized, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        return None
    if tz is not None:
        # Explicit designator → the represented instant, in UTC.
        return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    # Naive value (``PipelineLogger``) → the machine's local wall
    # time; ``astimezone()`` attaches the local tz without
    # re-clocking by the operator's UTC offset.
    return dt.astimezone()


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
