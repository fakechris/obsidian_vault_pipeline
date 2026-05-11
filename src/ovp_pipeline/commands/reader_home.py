"""Reader-shell home renderer.

Extracted from ``_ui_renderers.py`` (BL-051) so the renderer file
stays under its 5000-line cap and the Reader home has a clear file
home of its own.

Imports back from ``_ui_renderers`` for layout primitives that
multiple shells share (``_layout``, ``_shell_href``, ``escape``).
"""

from __future__ import annotations

from html import escape

from ._ui_renderers import _layout, _shell_href, _topic_entry_card


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

    def _recent_card(item: dict) -> str:
        # Recent topics share the same compact card shape as Top
        # Topics so the home page reads as one component family.
        return _topic_entry_card(
            {
                "label": item.get("label") or "(untitled)",
                "teaser": item.get("teaser") or "",
                "note_href": item.get("note_href") or "",
                "crystal_kind": item.get("crystal_kind") or "community",
            },
            compact=True,
        )

    top_topics_html = (
        "".join(
            _topic_entry_card(
                {
                    "rank": item.get("rank") or 0,
                    "label": item.get("label") or "(untitled)",
                    "teaser": item.get("teaser") or "",
                    "note_href": item.get("note_href") or "",
                    "score": float(item.get("score") or 0.0),
                    "crystal_kind": item.get("crystal_kind") or "community",
                },
                compact=True,
            )
            for item in top_topics
        )
        or "<p class='muted'>No topics synthesized yet. Run "
           "<code>ovp-synthesize-community-crystals</code> then "
           "<code>ovp-knowledge-index</code> to populate.</p>"
    )
    recent_html = (
        "".join(_recent_card(item) for item in recent_crystals)
        or f"<p class='muted'>No topics synthesized in the last {recent_days} days.</p>"
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

    # M20 / BL-077: surface the latest daily digest as a banner card
    # above the Top Topics list.  payload["digest"] is populated when
    # 40-Resources/Generated/digests/ contains at least one file.
    digest_card = ""
    digest_info = payload.get("digest") or {}
    if digest_info.get("href"):
        digest_date = escape(str(digest_info.get("date") or ""))
        digest_href = escape(str(digest_info["href"]))
        digest_teaser = escape(str(digest_info.get("teaser") or "").strip())
        teaser_html = (
            f"<p class='muted' style='margin:0.35rem 0 0.6rem'>{digest_teaser}</p>"
            if digest_teaser
            else ""
        )
        digest_card = (
            "<section class='card'>"
            "<h2 style='margin-top:0'>Today's digest"
            f"<span class='muted tiny mono' style='margin-left:0.6rem'>{digest_date}</span>"
            "</h2>"
            f"{teaser_html}"
            f"<p><a href='{digest_href}'>Open digest →</a></p>"
            "</section>"
        )

    body = "".join([
        "<h1>Knowledge Library</h1>",
        "<p class='muted' style='margin-top:-2px'>Discover, read, and follow the ideas in this vault.</p>",
        f"<form method='get' action='{escape(search_href)}'>",
        pack_input,
        "<input type='search' name='q' placeholder='Search by title, topic, source…' autofocus />",
        "<button type='submit'>Search</button>",
        "</form>",
        digest_card,
        # Top Topics + Recent Topics share the same card-shell shape
        # used by /topics so the home and Featured Topics pages read
        # as one component family — same rank/title/score-pill/teaser
        # rhythm, just compact (no breakdown chips).
        "<h2>Top Topics</h2>",
        "<p class='muted'>The highest-scoring synthesized topics in your "
        f"vault — pack <code>{escape(pack)}</code>.</p>",
        top_topics_html,
        see_all_html,
        map_card,
        f"<h2>Recent Topics (last {recent_days} days)</h2>",
        recent_html,
    ])
    return _layout(
        "Knowledge Library",
        body,
        requested_pack=requested_pack,
    )
