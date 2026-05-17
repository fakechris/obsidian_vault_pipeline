# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *




def _render_events_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    assembly_contract_card = _render_assembly_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    # M24.0 stop-gap: when a ``See all N →`` link from ``/ops/today``
    # lands here with a non-empty ``event_types`` filter, warn the
    # operator about the data-source mismatch.  ``/ops/today`` counts
    # raw ``audit_events`` rows; this page is a *timeline projection*
    # over dated notes / headings / contradictions.  The two never
    # align row-for-row.  Without this banner the operator clicks
    # "See all 27 →" and sees 0 rows and thinks something is broken
    # — actually they're looking at a different ledger.  M25's
    # ``/ops/items`` will unify the two surfaces.
    event_types_filter = payload.get("event_types_filter") or []
    cross_surface_warning = ""
    if event_types_filter and payload.get("event_count", 0) == 0:
        # CodeRabbit: pack-scope the backlink so the operator
        # doesn't drop their ``?pack=`` context when clicking back
        # to /ops/today.
        today_href = _shell_href("/ops/today", requested_pack)
        filter_chip = escape(", ".join(event_types_filter[:3])) + (
            "…" if len(event_types_filter) > 3 else ""
        )
        cross_surface_warning = (
            "<div class='card' style='border-color:#c2410c;"
            "background:#fef3e8;padding:0.75rem 1rem;margin-top:0.5rem'>"
            "<strong>Heads up — this page can't show those rows.</strong>"
            "<p class='muted small' style='margin:0.3rem 0 0'>"
            "<code>/ops/today</code> counts raw <code>audit_events</code> "
            "(intake / absorb / synthesis / governance / failures), but "
            "this <em>Event Dossier</em> is a timeline projection over "
            "dated notes &amp; contradictions.  The two ledgers track "
            f"different things, so <code>{filter_chip}</code> won't "
            "match a timeline row.  M24 / M25 will unify these two "
            f"surfaces; for now use the per-row links on "
            f"<a href='{escape(today_href)}'>/ops/today</a> to drill "
            "into the actual source files."
            "</p></div>"
        )
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    limit_note = (
        f" Showing the most recent {payload['limit']} timeline rows in this dossier window."
        if payload.get("is_limited")
        else ""
    )
    type_breakdown = "".join(
        f"<span class='pill'>{escape(kind.replace('_', ' '))}: {count}</span>"
        for kind, count in payload["event_type_counts"].items()
    )
    timeline_contract = payload["timeline_contract"]
    timeline_contract_items = (
        f"<li>Timeline kind: {escape(timeline_contract['timeline_kind'])}</li>"
        + f"<li>Grouping kind: {escape(str(timeline_contract.get('grouping_kind') or ''))}</li>"
        + "".join(
            f"<li>Row type {escape(str(row_type))}: {count}</li>"
            for row_type, count in timeline_contract["row_type_counts"].items()
        )
        + "".join(
            f"<li>Anchor kind {escape(str(anchor_kind))}: {count}</li>"
            for anchor_kind, count in timeline_contract.get("anchor_kind_counts", {}).items()
        )
        + "".join(
            f"<li>Semantic role {escape(str(role))}: {count}</li>"
            for role, count in timeline_contract["semantic_roles"].items()
        )
        + f"<li>Semantics: {escape(str(timeline_contract.get('event_vs_note_explanation') or ''))}</li>"
    )
    model_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["model_notes"])
    date_nav = "".join(
        f"<a href='#date-{escape(section['date'])}'>{escape(section['date'])}</a>"
        for section in payload["cluster_sections"]
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    events = (
        "".join(
            f'<section id="date-{escape(section["date"])}" class="card"><h2>{escape(section["date"])}</h2><ul class="list-tight">'
            + "".join(
                (
                    "<li>"
                    + f'<a href="{escape(item["object_path"])}">{escape(item["title"])}</a>'
                    + f" <span class='pill'>{item['row_count']} timeline rows</span>"
                    + (
                        f" <span class='muted'>({escape(', '.join(item['event_labels']))})</span>"
                        if item["event_labels"]
                        else ""
                    )
                    + (
                        f"<div class='muted'>Anchors: {escape(', '.join(item['timeline_anchor_labels']))}</div>"
                        if item["timeline_anchor_labels"]
                        else ""
                    )
                    + (
                        f"<div class='muted'>Evergreen: <a href=\"{escape(_note_href(item['provenance']['evergreen_path'], requested_pack))}\">{escape(item['provenance']['evergreen_path'])}</a></div>"
                        if item["provenance"]["evergreen_path"]
                        else "<div class='muted'>Evergreen: <span class='muted'>None</span></div>"
                    )
                    + f"<div class='muted'>Source Notes: {_render_named_note_links(item['provenance']['source_notes'], requested_pack=requested_pack)}</div>"
                    + f"<div class='muted'>Atlas / MOC: {_render_named_note_links(item['provenance']['mocs'], requested_pack=requested_pack)}</div>"
                    + "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                    + f"<a href='{escape(item['review_links']['topic_path'])}'>Topic</a>"
                    + f"<a href='{escape(item['review_links']['contradictions_path'])}'>Contradictions</a>"
                    + f"<a href='{escape(item['review_links']['summaries_path'])}'>Stale summaries</a>"
                    + "</div>"
                    + "</li>"
                )
                for item in section["clusters"]
            )
            + "</ul></section>"
            for section in payload["cluster_sections"]
        )
        or "<li>None</li>"
    )
    summary_form = (
        "<form method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
        + "".join(
            f"<input type='hidden' name='object_id' value='{escape(object_id)}' />"
            for object_id in payload["scoped_stale_summary_ids"]
        )
        + "<button type='submit'>Rebuild Visible Summaries</button>"
        + "</form>"
        if payload["scoped_stale_summary_ids"]
        else "<p class='muted'>No stale summaries in the visible event scope.</p>"
    )
    contradiction_query_path = _shell_href(
        f"/ops/contradictions?q={quote(query, safe='')}", requested_pack
    )
    contradiction_browser_path = _shell_href("/ops/contradictions", requested_pack)
    contradiction_entry = (
        f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'><a href='{escape(contradiction_query_path)}'>Review visible contradictions</a></div>"
        if payload["scoped_open_contradiction_ids"] and query
        else (
            f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'><a href='{escape(contradiction_browser_path)}'>Review visible contradictions</a></div>"
            if payload["scoped_open_contradiction_ids"]
            else "<p class='muted'>No open contradictions in the visible event scope.</p>"
        )
    )
    # M25.4: reciprocal role banner naming this page as the timeline-
    # projection view (NOT raw audit_events).  Pairs with the
    # /ops/events/audit banner.  Pre-M25.4 the operator landed here
    # from a card's secondary CTA and saw fewer rows than the card
    # promised — different ledgers.
    role_banner = (
        "<div class='card' style='border-color:#9ca3af;"
        "background:#f5f5f4;padding:0.75rem 1rem;margin:0.5rem 0'>"
        "<strong>Timeline projection view.</strong> "
        "<p class='muted small' style='margin:0.3rem 0 0'>"
        "These rows are NOT raw <code>audit_events</code> — they "
        "are derived events grouped by date and object.  For raw "
        "audit evidence (the rows the Maintainer cards count) use "
        "<a href='/ops/events/audit'>/ops/events/audit</a>."
        "</p></div>"
    )

    return _layout(
        "Event Dossier",
        "".join(
            [
                "<h1>Event Dossier</h1>",
                role_banner,
                cross_surface_warning,
                _render_page_help(
                    "Event dossier",
                    what=(
                        "Audit-event projection grouped by object and date."
                        "  Each row is one timeline event (page projection,"
                        " heading projection, contradiction outcome, etc.)"
                        " linked back to its truth object."
                    ),
                    can=(
                        "Filter by free text, date range (single ?date= or"
                        " ?from_date= + ?to_date=), or limit (25/50/100/200)."
                        "  Drill into any object to see its full provenance."
                    ),
                    effect=(
                        "Read-only — the page just queries the audit ledger."
                        "  The Quick Maintenance card at the bottom does"
                        " expose actions (resolve / queue summary rebuild)"
                        " that mutate the truth store; the help block on"
                        " those buttons explains the consequences."
                    ),
                ),
                "<p class='muted'>A timeline-oriented view over dated truth objects, not a separate event object model.</p>",
                "<form method='get' action='/ops/events'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter events' /> ",
                "<label class='muted' style='margin-left:.5rem'>From "
                f"<input type='date' name='from_date' value='{escape(payload.get('from_date',''))}' />"
                "</label> ",
                "<label class='muted'>To "
                f"<input type='date' name='to_date' value='{escape(payload.get('to_date',''))}' />"
                "</label> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['cluster_count']} event clusters from {payload['event_count']} timeline rows across {len(payload['dates'])} dates.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                _format_event_date_filter_summary(
                    str(payload.get("from_date", "") or ""),
                    str(payload.get("to_date", "") or ""),
                ),
                f"{escape(limit_note)}</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                assembly_contract_card,
                (f"<nav class='subnav'>{section_nav}</nav>" if section_nav else ""),
                _render_compiled_sections(remaining_sections),
                f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{type_breakdown}</div>",
                f"{_render_production_summary_card(payload['production_summary'], requested_pack=requested_pack)}",
                f"{_render_review_context_card(payload['review_context'])}",
                f"{_render_review_history(payload['review_history'])}",
                "<section class='card'><h2>Quick Maintenance</h2>",
                f"{contradiction_entry}",
                f"{summary_form}",
                "</section>",
                "<section class='card'><h2>Event Clusters</h2><p class='muted'>Rows for the same object and date are grouped into a single cluster so the dossier reads as an object timeline instead of raw timeline rows.</p></section>",
                f"<section class='card'><h2>Timeline Contract</h2><ul class='list-tight'>{timeline_contract_items}</ul></section>",
                f"<section class='card'><h2>Model Notes</h2><ul class='list-tight'>{model_notes}</ul></section>",
                f"<nav class='subnav'>{date_nav}</nav>",
                f"{events}",
            ]
        ),
        requested_pack=requested_pack,
    )


__all__ = [
    '_render_events_page'
]
