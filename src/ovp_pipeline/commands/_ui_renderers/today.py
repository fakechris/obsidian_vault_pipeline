# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from ._layer4 import *




def _render_timeline_page(payload: dict) -> str:
    """Daily digest of audit events.

    Sister to ``/ops/pulse`` (live tail) and ``/ops/events``
    (object-keyed dossier).  Pulse shows what's happening now;
    Events lets you drill down per object; Timeline answers the
    operator's day-to-day "what got created / went wrong today
    or yesterday" question without making them grep
    ``60-Logs/pipeline.jsonl`` themselves.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    window = int(payload.get("window_days") or 14)
    days = payload.get("days") or []

    timeline_help = _render_page_help(
        "Timeline",
        what=(
            "Day-by-day rollup of <code>audit_events</code> for the last"
            " ~14 days.  Sister to <strong>/ops/today</strong> (single"
            " day) and <strong>/ops/pulse</strong> (live tail)."
        ),
        can=(
            "Click any date heading or its <strong>See all N →</strong>"
            " link to drop into <strong>/ops/events</strong> filtered"
            " to that day.  Pills show top event types per day."
        ),
        effect=(
            "Read-only.  Following a drill-down link opens the events"
            " dossier with the date filter applied."
        ),
    )

    if not payload.get("available", True):
        body = (
            timeline_help + "<section class='card'>"
            "<h2>Timeline unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "<p>Run <code>ovp-knowledge-index</code> to populate "
            "<code>audit_events</code>.</p>"
            "</section>"
        )
        return _layout("Timeline", body, requested_pack=requested_pack)

    if not days:
        body = (
            timeline_help + "<section class='card'>"
            f"<h2>No events in the last {window} days</h2>"
            "<p class='muted'>The pipeline hasn't run in this window — "
            "check <code>60-Logs/pipeline.jsonl</code> for last activity.</p>"
            "</section>"
        )
        return _layout("Timeline", body, requested_pack=requested_pack)

    sections: list[str] = [_TIMELINE_DAY_CARD_STYLE]
    sections.append(timeline_help)
    sections.append(
        f"<p class='muted'>Showing the last {window} days of "
        f"<code>audit_events</code>.  {len(days)} day(s) with activity.</p>"
    )
    for day in days:
        date = escape(str(day.get("date", "")))
        total = int(day.get("total", 0))
        by_type = day.get("by_type") or {}
        samples = day.get("samples") or []
        errors = day.get("errors") or []

        # Sort by-type counts: highlighted ones first (in their canonical
        # order), then everything else by frequency.
        ordered_pills: list[tuple[str, int, bool, bool]] = []
        seen: set[str] = set()
        for t in payload.get("highlighted_types") or []:
            if t in by_type:
                ordered_pills.append((t, by_type[t], True, "error" in t or "broken" in t))
                seen.add(t)
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            if t in seen:
                continue
            ordered_pills.append((t, n, False, False))

        pills_html = "".join(
            "<span class='pill {cls}'>{type}: <strong>{n}</strong></span>".format(
                cls=("error" if is_error else ("highlight" if is_highlight else "")),
                type=escape(t),
                n=n,
            )
            for t, n, is_highlight, is_error in ordered_pills
        )

        samples_html = ""
        if samples:
            items = "".join(
                "<li><a href='{href}'>{title}</a> <span class='muted'>"
                "<code>{slug}</code></span></li>".format(
                    href=escape(str(s.get("note_href", ""))),
                    title=escape(str(s.get("title", "(untitled)"))),
                    slug=escape(str(s.get("slug", ""))),
                )
                for s in samples
            )
            samples_html = (
                "<div style='margin:.4rem 0'>"
                f"<h3 style='font-size:.95rem;margin:.4rem 0 .2rem 0'>"
                f"{_TIMELINE_NEW_EVERGREENS_LABEL} "
                f"(sample {len(samples)} of {by_type.get('evergreen_auto_promoted', 0)})</h3>"
                f"<ul class='list-tight' style='margin-left:1.2rem'>{items}</ul></div>"
            )

        errors_html = ""
        if errors:
            items = "".join(
                "<li>[{type}] <strong>{subject}</strong></li>".format(
                    type=escape(str(e.get("event_type", ""))),
                    subject=escape(str(e.get("subject", ""))[:_TIMELINE_ERROR_SUBJECT_MAX_CHARS]),
                )
                for e in errors
            )
            errors_html = (
                f"<div class='samples errors'><h3>{_TIMELINE_ERROR_SAMPLE_HEADING} "
                f"(sample {len(errors)})</h3><ul>{items}</ul></div>"
            )

        # Drill-down: every day card carries an explicit "open the
        # events dossier scoped to this date" link so the operator
        # can move from the histogram pill straight into the row-
        # level audit list.
        date_str = str(day.get("date", ""))
        drill_path = "/ops/events?date=" + quote(date_str, safe="") + "&limit=200"
        if requested_pack:
            drill_path += "&pack=" + quote(requested_pack, safe="")
        drill_html = (
            "<div class='tiny' style='margin-top:.5rem'>"
            f"<a href='{escape(drill_path)}'>"
            f"See all {total} events for {date} →</a></div>"
            if total
            else ""
        )

        sections.append(
            "<section class='card'>"
            f"<h2 style='margin:0 0 .3rem 0;font-size:1.1rem'>"
            f"<a href='{escape(drill_path)}'>{date}</a></h2>"
            f"<div class='muted tiny mono' style='margin-bottom:.7rem'>{total} events</div>"
            "<div class='grid' style='grid-template-columns:repeat(auto-fit,minmax(220px,1fr));"
            f"gap:.4rem;margin-bottom:.7rem'>{pills_html}</div>"
            f"{samples_html}"
            f"{errors_html}"
            f"{drill_html}"
            "</section>"
        )

    body = "".join(sections)
    return _layout("Timeline", body, requested_pack=requested_pack)



def _render_staleness_banner(staleness: dict) -> str:
    """BL-103a top-line freshness verdict.  Answers "can I trust
    these numbers?" BEFORE the operator reads any count — a zero (or
    any number) is meaningless if the audit sync or the lifecycle
    projection is behind.  ``unknown`` never implies freshness."""
    if not staleness:
        return ""
    summary = str(staleness.get("summary") or "unknown")
    detail = str(staleness.get("detail") or "")
    if summary == "current":
        icon, word, style = (
            "✓",
            "Projections current",
            "border-left:3px solid var(--ok,#3a7);"
            "background:var(--ok-bg,#f0f7f2)",
        )
    elif summary == "unknown":
        icon, word, style = (
            "?",
            "Run status unknown",
            "border-left:3px solid var(--border-strong,#999);"
            "background:var(--card-bg,#f6f6f6)",
        )
    else:  # audit_sync_stale | projection_stale
        icon, word, style = (
            "⚠",
            "Stale — refresh before trusting today's numbers",
            "border-left:3px solid var(--warn,#c70);"
            "background:var(--warn-bg,#fdf4e8)",
        )
    return (
        f"<section class='card' style='margin:0 0 .6rem;{style}'>"
        f"<strong>{icon} {escape(word)}</strong>"
        f"<div class='muted tiny' style='margin-top:2px'>"
        f"{escape(detail)}</div>"
        "</section>"
    )



def _render_intake_cohort_zone(cohort: dict, date: str) -> str:
    """BL-105 "Flow by intake day" zone.  Distinct from Activity:
    Activity counts what the system DID on the event day; this
    follows the sources whose intake STARTED on this day to where
    they are now.  The copy says so — BL-100 forbids making the
    operator infer which timestamp a number uses."""
    if not cohort or not cohort.get("available"):
        reason = escape(str((cohort or {}).get("reason") or ""))
        body = (
            f"<p class='muted tiny'>{reason}</p>" if reason else
            "<p class='muted tiny'>No intake-cohort data.</p>"
        )
        return (
            "<section class='card' style='margin:.6rem 0 0'>"
            "<div class='muted tiny'>Flow by intake day</div>"
            f"{body}</section>"
        )
    size = int(cohort.get("cohort_size") or 0)
    dist = cohort.get("distribution") or {}
    untracked = int(cohort.get("untracked") or 0)
    stalled = int(cohort.get("stalled") or 0)
    stall_days = int(cohort.get("stall_days") or 7)
    oldest = int(cohort.get("oldest_age_days") or 0)

    if size == 0:
        return (
            "<section class='card' style='margin:.6rem 0 0'>"
            "<div class='muted tiny'>Flow by intake day — "
            f"{escape(date)}</div>"
            "<p class='muted tiny' style='margin-top:4px'>No sources"
            " first entered intake on this day.  (This is the"
            " intake-time axis, not the event-time Activity above.)"
            "</p></section>"
        )

    parts = []
    for st in ("Received", "Extracted", "Accepted", "Synthesized", "NeedsAction"):
        n = int(dist.get(st) or 0)
        if n:
            parts.append(f"{n} {escape(st)}")
    if untracked:
        parts.append(f"{untracked} Untracked")
    distribution_line = " · ".join(parts) if parts else "no current state"

    stalled_html = (
        f"<span class='warn'> · {stalled} stalled "
        f"(&gt;{stall_days}d in Received/Extracted)</span>"
        if stalled
        else ""
    )

    sample_rows = ""
    for s in cohort.get("samples") or []:
        sample_rows += (
            "<div class='tiny' style='margin-top:2px'>"
            f"<a href='{escape(str(s.get('href') or '#'))}'>"
            f"{escape(str(s.get('slug') or ''))}</a> "
            f"<span class='muted'>→ {escape(str(s.get('state') or ''))}"
            f" · {int(s.get('age_days') or 0)}d old</span></div>"
        )

    return (
        "<section class='card' style='margin:.6rem 0 0'>"
        f"<div class='muted tiny'>Flow by intake day — {escape(date)}"
        " <span style='opacity:.7'>(where the sources first saved"
        " this day are now — NOT the event-time Activity above)</span>"
        "</div>"
        f"<div style='margin-top:4px'><strong>{size}</strong> "
        f"source{'s' if size != 1 else ''} first entered intake on "
        f"{escape(date)}</div>"
        f"<div class='muted tiny' style='margin-top:2px'>"
        f"now: {distribution_line}{stalled_html} · oldest {oldest}d"
        "</div>"
        f"{sample_rows}"
        "</section>"
    )



def _render_workflow_progress_zone(wp: dict, date: str) -> str:
    """BL-104 "Workflow Progress" zone — items that ENTERED a state
    on this day (transition-time axis).  Explicitly NOT the
    event-row Activity and NOT the right-now Current backlog."""
    if not wp or not wp.get("available"):
        reason = escape(str((wp or {}).get("reason") or ""))
        return (
            "<section class='card' style='margin:.6rem 0 0'>"
            "<div class='muted tiny'>Workflow progress</div>"
            + (f"<p class='muted tiny'>{reason}</p>" if reason else "")
            + "</section>"
        )
    moved = wp.get("moved") or {}
    total = int(wp.get("total") or 0)
    if total == 0:
        return (
            "<section class='card' style='margin:.6rem 0 0'>"
            "<div class='muted tiny'>Workflow progress — "
            f"{escape(date)}</div>"
            "<p class='muted tiny' style='margin-top:4px'>No items"
            " changed lifecycle state on this day.  (Transition-time"
            " axis — not the event-row Activity, not the right-now"
            " backlog.)</p></section>"
        )
    parts = []
    for st in ("Received", "Extracted", "Accepted", "Synthesized", "NeedsAction"):
        n = int(moved.get(st) or 0)
        if n:
            parts.append(f"{n} → {escape(st)}")
    line = " · ".join(parts) if parts else "no transitions"
    return (
        "<section class='card' style='margin:.6rem 0 0'>"
        f"<div class='muted tiny'>Workflow progress — {escape(date)}"
        " <span style='opacity:.7'>(items that ENTERED a state this"
        " day — not event rows, not the right-now backlog)</span>"
        "</div>"
        f"<div style='margin-top:4px'><strong>{total}</strong> "
        f"item{'s' if total != 1 else ''} moved forward</div>"
        f"<div class='muted tiny' style='margin-top:2px'>{line}</div>"
        "</section>"
    )



def _render_today_digest_page(payload: dict) -> str:
    """M25.7: ``/ops/today`` split into two clearly-separated zones.

    M25.6 dogfood found that the M25.3 hybrid card conflated two
    things with different date-sensitivity inside ONE card, so
    changing ``?date=`` looked like nothing happened (the big
    primary numbers dominate the visual and never move with the
    date; only small secondary lines do).  M25.7 splits the page:

    * **Zone A — Current backlog.**  Five lifecycle-state cards
      with the primary count from ``ops_state``.  A snapshot of
      *right now*.  Explicitly labeled "not affected by the date
      below".  Primary CTA → ``/ops/items?state=…`` (no date).

    * **Zone B — Activity on <date>.**  The date pivot lives here,
      visually attached to the only content it governs.  Per-state
      evidence counts from ``audit_events`` for the selected date.
      Secondary CTA → ``/ops/events/audit?…&date=…``.

    The data contract (``build_today_digest_payload``) is
    unchanged; this is purely presentational so card-N ===
    drilldown-N still holds on both axes.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    date = str(payload.get("date") or "")
    cards = payload.get("cards") or []

    if not payload.get("available", True):
        body = (
            "<section class='card'>"
            "<h2>Today digest unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "<p>Run <code>ovp-knowledge-index</code> to populate "
            "<code>audit_events</code> then <code>ovp-ops-state "
            "--rebuild</code> to materialise the lifecycle "
            "projection.</p>"
            "</section>"
        )
        return _layout(f"Today — {date}", body, requested_pack=requested_pack)

    sections: list[str] = [_TODAY_DIGEST_STYLE]
    sections.append(
        _render_page_help(
            "Today",
            what=(
                "Two zones.  <strong>Current backlog</strong> is a"
                " snapshot of how many items sit in each lifecycle"
                " state right now (from <code>ops_state</code>) — it"
                " does NOT change when you change the date."
                "  <strong>Activity on &lt;date&gt;</strong> is the"
                " evidence recorded on the selected day (from"
                " <code>audit_events</code>) — this is what the date"
                " selector governs."
            ),
            can=(
                "In Current backlog, click <strong>Open N items"
                " →</strong> to drill into all current items in a"
                " state.  In Activity, use the date pivot and click"
                " <strong>View N evidence events →</strong> to drop"
                " into the raw audit ledger for that day."
            ),
            effect=(
                "Read-only.  Drilldowns navigate to /ops/items"
                " (lifecycle) and /ops/events/audit (forensic)."
            ),
        )
    )

    # BL-103a: top-line freshness verdict — the operator must know
    # whether the numbers below are current before reading them.
    sections.append(
        _render_staleness_banner(payload.get("staleness") or {})
    )

    lifecycle = payload.get("lifecycle_summary") or {}
    projection_unavailable = (
        not lifecycle.get("available") and lifecycle.get("reason")
    )

    # M25.7 fix: the date pivot lives in the Activity zone.  If
    # Activity renders BELOW Current backlog, clicking prev/next
    # reloads the page at the top and the operator has to scroll
    # a long way to find the date controls again.  So we build
    # each zone into its own list and emit Activity FIRST (date
    # controls near the top, no scroll-hunting), Current backlog
    # second.
    backlog_sections: list[str] = []
    activity_sections: list[str] = []

    # ── ZONE A — Current backlog (date-independent) ────────────────
    backlog_sections.append(
        "<section style='margin-top:1.8rem;border-top:2px solid "
        "var(--border);padding-top:1rem'>"
        "<h2 style='margin-bottom:.2rem'>Current backlog</h2>"
        "<p class='muted small' style='margin:0 0 .6rem'>"
        "Snapshot of how many items are in each lifecycle state "
        "<strong>right now</strong>.  Source: "
        "<code>ops_state</code>.  "
        "<span style='color:var(--accent,#c2410c)'>Not affected by "
        "the date selector above.</span></p>"
    )
    if projection_unavailable:
        backlog_sections.append(
            "<div class='card' style='border-color:#c2410c;"
            "background:#fef3e8;padding:0.75rem 1rem;margin:0.5rem 0'>"
            "<strong>Lifecycle projection unavailable.</strong> "
            f"<p class='muted small' style='margin:0.3rem 0 0'>"
            f"{escape(str(lifecycle['reason']))}.  Backlog numbers "
            "below will be 0 until this lands.</p>"
            "</div>"
        )
    backlog_sections.append("<div class='grid stats'>")
    for card in cards:
        card_id = str(card.get("id") or "")
        label = str(card.get("label") or card_id)
        explainer = str(card.get("explainer") or "")
        primary_count = int(card.get("primary_count") or 0)
        primary_href = str(card.get("primary_href") or "")
        samples = card.get("samples") or []

        warn_cls = (
            " warn" if card_id == "NeedsAction" and primary_count > 0
            else ""
        )
        empty_style = (
            "color:var(--border-strong)" if primary_count == 0 else ""
        )

        sample_html = ""
        if samples:
            li_rows: list[str] = []
            for s in samples:
                item_id = str(s.get("item_id", ""))
                kind = str(s.get("item_kind", ""))
                href = str(s.get("path", "") or "")
                short_id = escape(item_id[:_TODAY_SAMPLE_SUBJECT_MAX_CHARS])
                kind_label = escape(kind[:_TODAY_SAMPLE_EVENT_TYPE_MAX_CHARS])
                subject_html = (
                    f"<a href='{escape(href)}' "
                    f"title='{escape(item_id)}'>{short_id}</a>"
                    if href
                    else f"<span title='{escape(item_id)}'>{short_id}</span>"
                )
                li_rows.append(
                    "<li style='overflow:hidden;text-overflow:ellipsis;"
                    "white-space:nowrap'>"
                    f"<span class='muted'>{kind_label}</span> "
                    f"<strong>{subject_html}</strong>"
                    "</li>"
                )
            sample_html = (
                "<div style='margin-top:.6rem;padding-top:.6rem;"
                "border-top:1px solid var(--border);max-width:100%'>"
                "<ul class='list-tight tiny' "
                "style='list-style:none;padding-left:0;margin:0'>"
                + "".join(li_rows)
                + "</ul></div>"
            )

        primary_cta = ""
        if primary_count > 0 and primary_href:
            primary_cta = (
                "<div class='tiny' style='margin-top:.5rem'>"
                f"<a href='{escape(primary_href)}'>"
                f"Open {primary_count} item"
                f"{'s' if primary_count != 1 else ''} →</a>"
                "</div>"
            )

        # Honest-zero only when there is genuinely nothing in this
        # state right now (and the projection IS available — if it
        # isn't, the banner above already explained the 0).
        zero_html = ""
        if primary_count == 0 and not projection_unavailable:
            zero_html = honest_zero_html(short=True)

        explainer_html = (
            f"<div class='muted tiny' style='margin-top:4px'>"
            f"{escape(explainer)}</div>"
            if explainer
            else ""
        )

        backlog_sections.append(
            "<div class='card' style='margin:0;overflow:hidden'>"
            f"<div class='muted tiny'>{escape(label)}</div>"
            f"<div class='metric-num{warn_cls}' "
            f"style='margin-top:4px;{empty_style}'>{primary_count}</div>"
            f"<div class='muted tiny'>current item"
            f"{'s' if primary_count != 1 else ''}</div>"
            f"{explainer_html}"
            f"{zero_html}"
            f"{sample_html}"
            f"{primary_cta}"
            "</div>"
        )
    backlog_sections.append("</div></section>")

    # ── ZONE B — Activity on <date> (date-driven) ──────────────────
    prev_path = str(payload.get("prev_date_path") or "")
    next_path = str(payload.get("next_date_path") or "")
    prev_date = str(payload.get("prev_date") or "")
    next_date = str(payload.get("next_date") or "")
    pivot_parts: list[str] = []
    if prev_path:
        pivot_parts.append(
            f"<a href='{escape(prev_path)}'>← {escape(prev_date)}</a>"
        )
    pivot_parts.append(
        f"<strong>{escape(date)}</strong>"
    )
    if next_path:
        pivot_parts.append(
            f"<a href='{escape(next_path)}'>{escape(next_date)} →</a>"
        )

    activity_sections.append(
        "<section style='margin-top:1rem'>"
        f"<h2 style='margin-bottom:.2rem'>Activity on "
        f"<span style='color:var(--accent,#c2410c)'>{escape(date)}"
        "</span></h2>"
        "<p class='muted small' style='margin:0 0 .5rem'>"
        "Evidence events recorded on this operator-local day "
        "(the server process timezone).  Source: "
        "<code>audit_events</code>.  "
        "<strong>This is the zone the date selector controls</strong>"
        " — change the day to see other days' flow.  Current "
        "backlog is below and does not move with the date.  "
        "<a href='/ops/timeline'>Timeline</a> has the multi-day view."
        "</p>"
        f"<p class='muted' style='margin:0 0 .6rem'>"
        f"{' · '.join(pivot_parts)}</p>"
    )

    # Regenerate-digest acts on the SELECTED date — belongs in this
    # zone, not globally.
    activity_sections.append(
        _render_digest_regenerate_button(requested_pack, date=date)
    )

    activity_sections.append("<div class='grid stats' style='margin-top:.6rem'>")
    for card in cards:
        card_id = str(card.get("id") or "")
        label = str(card.get("label") or card_id)
        event_count = int(card.get("event_count") or 0)
        event_label = str(card.get("event_label") or "")
        event_href = str(card.get("event_href") or "")

        warn_cls = (
            " warn" if card_id == "NeedsAction" and event_count > 0
            else ""
        )
        empty_style = (
            "color:var(--border-strong)" if event_count == 0 else ""
        )

        secondary_cta = ""
        if event_count > 0 and event_href:
            secondary_cta = (
                "<div class='tiny' style='margin-top:.4rem'>"
                f"<a href='{escape(event_href)}'>"
                f"View {event_count} item"
                f"{'s' if event_count != 1 else ''} in raw evidence →</a>"
                "</div>"
            )

        # Per-state label e.g. "5 arrived today" / "3 extracted
        # today".  When 0, the honest-zero footer carries the
        # ambiguity instead.
        label_html = (
            f"<div class='muted tiny' style='margin-top:4px'>"
            f"{escape(event_label)}</div>"
            if event_label and event_count > 0
            else ""
        )
        if event_count == 0:
            zr = str(card.get("zero_reason") or "")
            zd = str(card.get("zero_detail") or "")
            if zr:
                tone = (
                    "color:var(--ok,#3a7)"
                    if zr == "healthy"
                    else "color:var(--warn,#c70)"
                    if zr in ("audit_sync_stale", "projection_stale", "failed")
                    else "color:var(--muted,#888)"
                )
                zero_html = (
                    "<div class='tiny' style='margin-top:4px;"
                    f"{tone}'><strong>{escape(zr)}</strong>"
                    f"<div class='muted' style='margin-top:1px'>"
                    f"{escape(zd)}</div></div>"
                )
            else:
                zero_html = honest_zero_html(short=True)
        else:
            zero_html = ""

        activity_sections.append(
            "<div class='card' style='margin:0;overflow:hidden'>"
            f"<div class='muted tiny'>{escape(label)}</div>"
            f"<div class='metric-num{warn_cls}' "
            f"style='margin-top:4px;{empty_style}'>{event_count}</div>"
            f"<div class='muted tiny'>item"
            f"{'s' if event_count != 1 else ''} on {escape(date)}</div>"
            f"{label_html}"
            f"{zero_html}"
            f"{secondary_cta}"
            "</div>"
        )
    activity_sections.append("</div></section>")

    # BL-105: New Intake (intake-time axis) sits with the other
    # date-driven content, clearly separated from Activity.
    activity_sections.append(
        _render_intake_cohort_zone(
            payload.get("intake_cohort") or {}, date
        )
    )
    # BL-104: Workflow Progress (transition-time axis) joins the
    # date-driven group, clearly separated from Activity + backlog.
    activity_sections.append(
        _render_workflow_progress_zone(
            payload.get("workflow_progress") or {}, date
        )
    )

    # M25.7 fix: Activity FIRST (date pivot near top — no
    # scroll-hunting after prev/next), Current backlog SECOND.
    body = "".join(sections + activity_sections + backlog_sections)
    return _layout(f"Today — {date}", body, requested_pack=requested_pack)



# M25.4: /ops/events/audit renderer.  Raw-audit-evidence view —
# flat table of audit_events rows.  Card secondary CTAs target
# this page so that ``View today's N evidence events →`` lands on
# exactly N rows (card-N === page-N).  The pre-existing
# /ops/events page renders timeline projections, which is a
# different ledger; both pages carry reciprocal banners explaining
# their respective roles.
def _render_events_audit_page(payload: dict) -> str:
    """Render the ``/ops/events/audit`` raw-audit-evidence table."""
    requested_pack = str(payload.get("requested_pack") or "")
    event_types = payload.get("event_types") or []
    date_key = str(payload.get("date") or "")
    total = int(payload.get("total") or 0)
    rows = payload.get("rows") or []

    title = "Audit evidence"

    if not payload.get("available"):
        reason = escape(str(payload.get("reason") or "unavailable"))
        body = (
            f"<h1>{escape(title)}</h1>"
            "<div class='card' style='border-color:#9ca3af;"
            "background:#f5f5f4;padding:0.75rem 1rem;margin:0.5rem 0'>"
            f"<p class='muted small' style='margin:0'>{reason}</p>"
            "</div>"
        )
        return _layout(title, body, requested_pack=requested_pack)

    # Banner explaining the role + cross-link to /ops/events.
    timeline_href = (
        f"/ops/events?date={quote(date_key, safe='')}"
        if date_key
        else "/ops/events"
    )
    role_banner = (
        "<div class='card' style='border-color:#9ca3af;"
        "background:#f5f5f4;padding:0.75rem 1rem;margin-bottom:0.6rem'>"
        "<strong>Raw audit evidence.</strong> "
        "<p class='muted small' style='margin:0.3rem 0 0'>"
        "These are the exact <code>audit_events</code> rows the "
        "Maintainer card counted.  For timeline projections "
        "(events grouped by date and object) use "
        f"<a href='{escape(timeline_href)}'>/ops/events</a>."
        "</p></div>"
    )

    # Filter summary.
    filter_chips: list[str] = []
    if event_types:
        et_preview = ", ".join(event_types[:5])
        if len(event_types) > 5:
            et_preview += f" (+{len(event_types) - 5} more)"
        filter_chips.append(
            f"<span class='pill'>event_types: {escape(et_preview)}</span>"
        )
    if date_key:
        filter_chips.append(
            f"<span class='pill'>date: {escape(date_key)}</span>"
        )
    filter_html = ""
    if filter_chips:
        filter_html = (
            "<div style='display:flex;gap:0.4rem;flex-wrap:wrap;"
            "margin-bottom:0.6rem'>"
            + "".join(filter_chips)
            + "</div>"
        )

    head = [
        f"<h1>{escape(title)}</h1>",
        role_banner,
        filter_html,
        f"<p class='muted small'>"
        f"{total} matching row{'s' if total != 1 else ''} "
        f"(showing {len(rows)})"
        f"</p>",
    ]

    if not rows:
        head.append(honest_zero_html(short=True))
        return _layout(
            title, "".join(head), requested_pack=requested_pack
        )

    thead = (
        "<thead><tr>"
        "<th>Timestamp</th><th>Event type</th>"
        "<th>Slug</th><th>Payload</th>"
        "</tr></thead>"
    )
    body_rows: list[str] = []
    for r in rows:
        ts = escape(str(r.get("timestamp") or ""))
        event_type = escape(str(r.get("event_type") or ""))
        slug = escape(str(r.get("slug") or ""))
        snippet = escape(str(r.get("payload_snippet") or ""))
        full = escape(str(r.get("payload_full") or ""))
        slug_cell = (
            slug if slug else "<span class='muted'>—</span>"
        )
        body_rows.append(
            "<tr>"
            f"<td class='mono small'>{ts}</td>"
            f"<td><code>{event_type}</code></td>"
            f"<td>{slug_cell}</td>"
            f"<td class='mono small' title='{full}' "
            "style='max-width:50ch;overflow:hidden;"
            f"text-overflow:ellipsis'>{snippet}</td>"
            "</tr>"
        )
    table_html = (
        "<table class='table' style='margin-top:0.6rem'>"
        + thead
        + "<tbody>" + "".join(body_rows) + "</tbody>"
        + "</table>"
    )

    body = "".join(head) + table_html
    return _layout(title, body, requested_pack=requested_pack)



def _render_runs_index_page(payload: dict) -> str:
    """List of recent transactions with status + click-through.

    Each row is one ``transaction_started`` event; the ``status``
    cell reflects whether a matching ``transaction_completed`` row
    was found (otherwise ``running`` for fresh, ``stale`` for >6h
    without a completion event).
    """
    requested_pack = str(payload.get("requested_pack") or "")
    runs = payload.get("runs") or []

    runs_help = _render_page_help(
        "Runs",
        what=(
            "Index of pipeline transactions (one row per"
            " <code>transaction_started</code> audit event) grouped"
            " by calendar day with status, workflow, and event count."
            "  ‘Idle’ markers surface days the pipeline did not run."
        ),
        can=(
            "Click any <code>txn_id</code> to inspect that run's"
            " event timeline.  Use the window pivot (Last 10 / 30 /"
            " 100) to widen the lens when triaging."
        ),
        effect=(
            "Read-only.  Per-run drill-down is also read-only — it"
            " just reads from <code>audit_events</code>."
        ),
    )

    if not payload.get("available", True):
        body = (
            runs_help + "<section class='card'>"
            "<h2>Runs index unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "</section>"
        )
        return _layout("Runs", body, requested_pack=requested_pack)

    if not runs:
        body = (
            runs_help + "<section class='card'>"
            "<h2>No transactions found</h2>"
            "<p class='muted'>No <code>transaction_started</code> events "
            "in <code>audit_events</code>.</p>"
            "</section>"
        )
        return _layout("Runs", body, requested_pack=requested_pack)

    def _row_html(r: dict) -> str:
        return (
            "<tr>"
            "<td><code>{ts}</code></td>"
            "<td>{type}</td>"
            "<td class='status-{status}'>{status}</td>"
            "<td>{events}</td>"
            "<td><a href='{href}'>{txn_id}</a></td>"
            "</tr>".format(
                ts=escape(str(r.get("started_at", "")[:_TS_DISPLAY_LEN])),
                type=escape(str(r.get("workflow_type", ""))),
                status=escape(str(r.get("status", ""))),
                events=int(r.get("event_count") or 0),
                href=escape(str(r.get("detail_href", ""))),
                txn_id=escape(str(r.get("txn_id", ""))[:_RUN_TXN_ID_DISPLAY_MAX_CHARS]),
            )
        )

    day_groups = payload.get("day_groups") or []
    limit_value = int(payload.get("limit", len(runs)) or len(runs))
    window_days = payload.get("window_days")
    if window_days is None:
        window_text = ""
    elif window_days == 0:
        window_text = " (oldest from today)"
    else:
        window_text = f" (oldest from {window_days} day{'s' if window_days != 1 else ''} ago)"

    if day_groups:
        sections: list[str] = []
        for group in day_groups:
            date = str(group.get("date") or "")
            count = int(group.get("count") or 0)
            if group.get("idle"):
                sections.append(f"<h3 class='muted'>{escape(date)} — Idle (no scheduled run)</h3>")
                continue
            day_runs = group.get("runs") or []
            sections.append(
                f"<h3>{escape(date)} — {count} run{'s' if count != 1 else ''}</h3>"
                "<table class='data-table'>"
                "<thead><tr><th>Started</th><th>Workflow</th><th>Status</th>"
                "<th>Events</th><th>Run</th></tr></thead>"
                f"<tbody>{''.join(_row_html(r) for r in day_runs)}</tbody>"
                "</table>"
            )
        runs_html = "".join(sections)
    else:
        runs_html = (
            "<table class='data-table'>"
            "<thead><tr><th>Started</th><th>Workflow</th><th>Status</th>"
            "<th>Events</th><th>Run</th></tr></thead>"
            f"<tbody>{''.join(_row_html(r) for r in runs)}</tbody>"
            "</table>"
        )

    # Window-size pivot links.  ``limit=`` exposes how the cap is
    # applied; the operator can widen the window when triaging
    # whether a regression is recent or longstanding.
    def _runs_href(new_limit: int) -> str:
        params: list[tuple[str, str]] = []
        if requested_pack:
            params.append(("pack", requested_pack))
        if new_limit and new_limit != 30:
            params.append(("limit", str(new_limit)))
        if not params:
            return "/ops/runs"
        return "/ops/runs?" + "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params
        )

    pivot_options = (
        (10, "Last 10"),
        (30, "Last 30"),
        (100, "Last 100"),
    )
    pivot_links = " · ".join(
        (
            f"<strong>{escape(label)}</strong>"
            if value == limit_value
            else f"<a href='{escape(_runs_href(value))}'>{escape(label)}</a>"
        )
        for value, label in pivot_options
    )

    body = (
        f"{_RUNS_INDEX_STYLE}"
        + runs_help
        + f"<p class='muted'>Showing last {limit_value} run(s){window_text}. "
        f"Click a <code>txn_id</code> to see the full event timeline.</p>"
        + f"<p class='muted'>Window: {pivot_links}</p>"
        + f"{runs_html}"
    )
    return _layout("Runs", body, requested_pack=requested_pack)



def _render_run_detail_page(payload: dict) -> str:
    """Per-transaction event timeline.

    Renders every event tagged with this run's ``txn_id`` (or
    sharing the bracketing ``session_id``) in chronological order
    so the operator can scan the full sequence of stages, successes
    and failures of one run on a single page.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    txn_id = str(payload.get("txn_id") or "")
    workflow_type = str(payload.get("workflow_type") or "(unknown)")
    started_at = str(payload.get("started_at") or "")
    completed_at = str(payload.get("completed_at") or "(still running)")
    events = payload.get("events") or []

    if not payload.get("available", True):
        body = (
            "<section class='card'>"
            f"<h2>Run {escape(txn_id)} unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "</section>"
        )
        return _layout(
            f"Run {txn_id[:_RUN_TXN_ID_DISPLAY_MAX_CHARS]}",
            body,
            requested_pack=requested_pack,
        )

    header = (
        f"{_RUN_DETAIL_STYLE}"
        "<div class='card'>"
        "<dl>"
        f"<dt>Run id</th><td><code>{escape(txn_id)}</code></dd>"
        f"<dt>Workflow</th><td>{escape(workflow_type)}</dd>"
        f"<dt>Started</th><td><code>{escape(started_at)}</code></dd>"
        f"<dt>Completed</th><td><code>{escape(completed_at)}</code></dd>"
        f"<dt>Events</th><td>{len(events)}</dd>"
        "</dl></div>"
    )

    rows = []
    for ev in events:
        et = str(ev.get("event_type", ""))
        css_classes: list[str] = []
        if et == "transaction_started":
            css_classes.append("bracket")
        elif et == "transaction_completed":
            css_classes.append("bracket bracket-completed")
        elif any(et.startswith(p) for p in _ERROR_EVENT_TYPE_PREFIXES):
            css_classes.append("error")
        cls = (" class='" + " ".join(css_classes) + "'") if css_classes else ""
        rows.append(
            f"<tr{cls}>"
            f"<td class='ts'>{escape(str(ev.get('timestamp',''))[:_TS_DISPLAY_LEN])}</td>"
            f"<td class='type'>{escape(et)}</td>"
            f"<td class='subject'>{escape(str(ev.get('subject',''))[:_RUN_DETAIL_SUBJECT_MAX_CHARS])}</td>"
            "</tr>"
        )

    body = (
        f"{header}"
        "<table class='data-table'>"
        "<thead><tr><th>Time</th><th>Event</th><th>Subject</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )
    return _layout(
        f"Run {txn_id[:_RUN_TXN_ID_DISPLAY_MAX_CHARS]}",
        body,
        requested_pack=requested_pack,
    )


__all__ = [
    '_render_timeline_page',
    '_render_staleness_banner',
    '_render_intake_cohort_zone',
    '_render_workflow_progress_zone',
    '_render_today_digest_page',
    '_render_events_audit_page',
    '_render_runs_index_page',
    '_render_run_detail_page'
]
