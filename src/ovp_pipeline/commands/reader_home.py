"""Reader-shell home renderer.

Extracted from ``_ui_renderers.py`` (BL-051) so the renderer file
stays under its 5000-line cap and the Reader home has a clear file
home of its own.

Imports back from ``_ui_renderers`` for layout primitives that
multiple shells share (``_layout``, ``_shell_href``, ``escape``).
"""

from __future__ import annotations

from html import escape

from ._ui_renderers import _layout, _shell_href


def _render_reader_home(payload: dict) -> str:
    """BL-050 / BL-051 Reader-shell home.  No DB stat counts, no
    Workbench card, no typed-object list.  Lead with search + Top
    Topics + an inline "See all" link to Featured Topics + Recent
    Topics."""
    requested_pack = payload.get("requested_pack", "")
    pack = payload.get("pack", "")
    search_href = str(payload.get("search_href") or _shell_href("/search", requested_pack))
    map_href = str(payload.get("map_href") or _shell_href("/map", requested_pack))
    map_supported = bool(payload.get("map_supported"))
    top_topics = payload.get("top_topics") or []
    curated = payload.get("curated_atlas") or {}
    atlas_href = str(curated.get("atlas_href") or _shell_href("/topics", requested_pack))
    total_chains = int(curated.get("total_chains") or 0)
    atlas_available = bool(curated.get("available", total_chains > 0))
    # Show ``effective_top_n`` (= min(default, total)) so the body
    # copy never headlines more chains than actually shipped.
    atlas_top_n = int(curated.get("effective_top_n") or curated.get("top_n") or 0)
    recent_crystals = payload.get("recent_crystals") or []
    recent_days = int(payload.get("recent_days") or 7)

    def _topic_li(item: dict) -> str:
        href = escape(str(item.get("note_href") or ""))
        label = escape(str(item.get("label") or "(untitled)"))
        teaser = escape(str(item.get("teaser") or ""))
        teaser_html = f"<div class='muted'>{teaser}</div>" if teaser else ""
        rank = int(item.get("rank") or 0)
        return (
            "<li>"
            f"<strong>{rank}.</strong> <a href='{href}'>{label}</a>"
            f"{teaser_html}</li>"
        )

    def _recent_li(item: dict) -> str:
        href = escape(str(item.get("note_href") or ""))
        label = escape(str(item.get("label") or "(untitled)"))
        teaser = escape(str(item.get("teaser") or ""))
        teaser_html = f"<div class='muted'>{teaser}</div>" if teaser else ""
        return f"<li><a href='{href}'>{label}</a>{teaser_html}</li>"

    top_topics_html = (
        "".join(_topic_li(item) for item in top_topics)
        or "<li class='muted'>No topics synthesized yet. Run "
           "<code>ovp-synthesize-community-crystals</code> then "
           "<code>ovp-knowledge-index</code> to populate.</li>"
    )
    recent_html = (
        "".join(_recent_li(item) for item in recent_crystals)
        or f"<li class='muted'>No topics synthesized in the last {recent_days} days.</li>"
    )
    see_all_html = (
        f"<p class='muted'><a href='{escape(atlas_href)}'>See all "
        f"{atlas_top_n} featured topics →</a></p>"
        if atlas_available
        else ""
    )
    map_card = (
        "<section class='card'><h2>Knowledge Map</h2>"
        "<p class='muted'>See how ideas connect — entities, concepts, "
        "references woven into one graph.</p>"
        f"<p><a href='{escape(map_href)}'>Open the Map →</a></p></section>"
        if map_supported
        else ""
    )
    pack_input = (
        f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
        if requested_pack
        else ""
    )

    body = "".join([
        "<h1>Knowledge Library</h1>",
        "<p class='muted' style='margin-top:-2px'>Discover, read, and follow the ideas in this vault.</p>",
        f"<form method='get' action='{escape(search_href)}' class='form-inline'>",
        pack_input,
        "<input type='search' name='q' placeholder='Search by title, topic, source…' autofocus />",
        "<button type='submit'>Search</button>",
        "</form>",
        # Top Topics card includes the "See all N featured topics →"
        # link.  Pre-BL-051 this was a separate Curated Atlas card —
        # folded in because it was the same ranked list at a higher N.
        "<section class='card'>",
        "<h2>Top Topics</h2>",
        "<p class='muted'>The highest-scoring synthesized topics in your "
        f"vault — pack <code>{escape(pack)}</code>.</p>",
        f"<ul class='list-tight'>{top_topics_html}</ul>",
        see_all_html,
        "</section>",
        map_card,
        "<section class='card'>",
        f"<h2>Recent Topics (last {recent_days} days)</h2>",
        f"<ul class='list-tight'>{recent_html}</ul>",
        "</section>",
    ])
    return _layout(
        "Knowledge Library",
        body,
        requested_pack=requested_pack,
    )
