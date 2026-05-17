# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from ._layer4 import *




# Kept as a thin alias so existing renderers that import the symbol
# don't break during the migration; they always use the active-shell
# variant.  Removing the alias is a follow-up.
def _shell_nav_items(
    requested_pack: str = "", *, reader_mode: bool = True
) -> list[tuple[str, str]]:
    if reader_mode:
        return _reader_nav_items(requested_pack)
    return _ops_nav_items(requested_pack)



# M25.2: /ops/items renderer.  Lifecycle-state drilldown that the
# M25 hybrid card primary CTAs target.  Read-only in v1 — no inline
# actions yet; that's a separate scoping pass per the M25 plan.
def _render_items_list_page(payload: dict) -> str:
    """Render the ``/ops/items?state=…`` table.

    Single-table view, paginated.  Carries an honest-zero footer
    when the projection isn't built or the bucket is empty.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    state = str(payload.get("state") or "")
    total = int(payload.get("total") or 0)
    rows = payload.get("rows") or []
    offset = int(payload.get("offset") or 0)
    limit = int(payload.get("limit") or 0)

    title = f"Items · {state or 'unknown state'}"

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

    # State-explanation banner so the operator knows what set of
    # items this page lists.  Mirrors the per-state vocabulary the
    # M25 cards expose.
    state_explainer = {
        "Received": (
            "Items where evidence shows intake but no extraction yet."
        ),
        "Extracted": (
            "Items where the interpreter / absorber ran, producing"
            " candidates waiting for promotion."
        ),
        "Accepted": (
            "Items that have a canonical artifact in the vault."
        ),
        "Synthesized": (
            "Items in clusters with a fresh community crystal."
        ),
        "NeedsAction": (
            "Items blocked or waiting on operator action — failures,"
            " open contradictions, or stale review queues."
        ),
    }.get(state, "")

    head = [
        f"<h1>{escape(title)}</h1>",
    ]
    if state_explainer:
        head.append(
            f"<p class='muted'>{escape(state_explainer)}</p>"
        )
    head.append(
        f"<p class='muted small'>"
        f"{total} item{'s' if total != 1 else ''} in this state · "
        f"showing {len(rows)} starting at offset {offset}"
        f"</p>"
    )

    if not rows:
        head.append(honest_zero_html(short=True))
        return _layout(
            title, "".join(head), requested_pack=requested_pack
        )

    # Table.
    thead = (
        "<thead><tr>"
        "<th>Item</th><th>Kind</th><th>Sub-state</th>"
        "<th>Last evidence</th><th>Recent evidence types</th>"
        "</tr></thead>"
    )

    body_rows: list[str] = []
    for item in rows:
        item_id = str(item.get("item_id") or "")
        kind = str(item.get("item_kind") or "")
        sub = str(item.get("sub_state") or "")
        last_ts = str(item.get("last_evidence_at") or "")
        evt_types = item.get("evidence_types") or []
        href = str(item.get("primary_href") or "")
        na_reason = str(item.get("needs_action_reason") or "")

        if href:
            id_cell = (
                f"<a href='{escape(href)}'>{escape(item_id)}</a>"
            )
        else:
            id_cell = escape(item_id)
        if na_reason:
            id_cell += (
                f"<div class='muted tiny'>"
                f"Needs action: {escape(na_reason)}</div>"
            )

        sub_cell = (
            f"<span class='pill muted'>{escape(sub)}</span>"
            if sub
            else "<span class='muted'>—</span>"
        )
        evt_cell = (
            "<span class='muted'>—</span>" if not evt_types else
            " ".join(
                f"<span class='pill'>{escape(str(e))}</span>"
                for e in evt_types
            )
        )
        body_rows.append(
            "<tr>"
            f"<td>{id_cell}</td>"
            f"<td><span class='muted small'>{escape(kind)}</span></td>"
            f"<td>{sub_cell}</td>"
            f"<td><span class='muted small mono'>{escape(last_ts) or '—'}</span></td>"
            f"<td>{evt_cell}</td>"
            "</tr>"
        )

    table_html = (
        "<table class='table' style='margin-top:0.6rem'>"
        + thead
        + "<tbody>" + "".join(body_rows) + "</tbody>"
        + "</table>"
    )

    # Pagination footer.
    pack_qs = (
        f"&pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )

    def _page_link(offset_value: int, label: str) -> str:
        return (
            f"<a href='/ops/items?state={quote(state, safe='')}"
            f"&offset={offset_value}&limit={limit}{pack_qs}'>"
            f"{escape(label)}</a>"
        )

    prev_offset = payload.get("prev_offset")
    next_offset = payload.get("next_offset")
    pager_parts: list[str] = []
    if prev_offset is not None:
        pager_parts.append(_page_link(int(prev_offset), "← Previous"))
    if next_offset is not None:
        pager_parts.append(_page_link(int(next_offset), "Next →"))
    pager_html = (
        f"<p style='margin-top:0.8rem'>{' · '.join(pager_parts)}</p>"
        if pager_parts
        else ""
    )

    body = "".join(head) + table_html + pager_html
    return _layout(title, body, requested_pack=requested_pack)


__all__ = [
    '_shell_nav_items',
    '_render_items_list_page'
]
