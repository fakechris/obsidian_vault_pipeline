# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from ._layer4 import *




def _topic_entry_card(entry: dict, *, compact: bool = False) -> str:
    """Shared topic-card markup used by Knowledge Library home and
    Featured Topics.  Same card shape (rank · title · score pill ·
    kind pill · teaser) so the two pages read as one component
    family.  ``compact=True`` drops the 6-metric breakdown chips —
    home uses compact, /topics uses full.

    ``entry`` keys consumed:
    rank · label · note_href · score · teaser · crystal_kind ·
    size_norm/credibility_norm/source_diversity_norm/
    contradiction_norm/reuse_recency_norm/evergreen_recency_norm
    (only the breakdown keys are needed when compact=False).
    """
    kind = str(entry.get("crystal_kind", ""))
    kind_label = (
        "topic"
        if kind == "community"
        else ("open question" if kind == "contradiction" else (kind or "topic"))
    )
    kind_pill_class = "pill warn" if kind == "contradiction" else "pill"
    label = escape(str(entry.get("label", "(untitled)")))
    score = float(entry.get("score", 0.0))
    teaser = str(entry.get("teaser") or "")
    note_href = str(entry.get("note_href") or "")
    rank = int(entry.get("rank", 0) or 0)
    link_html = f"<a href='{escape(note_href)}'>{label}</a>" if note_href else label
    teaser_html = (
        f"<p>{escape(teaser)}</p>"
        if teaser
        else "<p class='muted'><em>(no teaser available)</em></p>"
    )
    breakdown_html = ""
    if not compact:
        breakdown_chips = "".join(
            f"<span class='muted tiny mono' style='margin-right:0.6rem'>{escape(label_text)} "
            f"<strong style='color:var(--text-soft)'>{value:.2f}</strong></span>"
            for label_text, value in [
                ("size", float(entry.get("size_norm", 0) or 0)),
                ("credibility", float(entry.get("credibility_norm", 0) or 0)),
                ("source-div", float(entry.get("source_diversity_norm", 0) or 0)),
                ("contradict", float(entry.get("contradiction_norm", 0) or 0)),
                ("reuse-rec", float(entry.get("reuse_recency_norm", 0) or 0)),
                ("evergreen-rec", float(entry.get("evergreen_recency_norm", 0) or 0)),
            ]
        )
        breakdown_html = (
            "<div style='margin-top:.6rem;padding-top:.5rem;"
            "border-top:1px dashed var(--border);"
            "display:flex;flex-wrap:wrap;font-size:.78rem;color:var(--muted)'>"
            f"{breakdown_chips}</div>"
        )
    # Rank lives in a small muted-mono line above the title rather
    # than in a reserved left-column inside the card-head.  Pre-fix
    # the 1.6rem rank column shifted the title's left edge ~25px in
    # from the .card-body's left padding — so the title visually
    # didn't line up with the teaser below it.  Post-fix everything
    # in the card shares the same left edge.
    rank_html = f"<div class='muted tiny mono'>#{rank}</div>" if rank else ""
    score_pill = f"<span class='pill'>score {score:.3f}</span>" if score else ""
    kind_pill = f"<span class='{kind_pill_class}'>{escape(kind_label)}</span>" if kind else ""
    return (
        "<section class='card flush'>"
        "<div class='card-head'>"
        "<div style='flex:1;min-width:0'>"
        f"{rank_html}"
        f"<h3 style='margin:0;font-size:1.05rem'>{link_html}</h3>"
        "</div>"
        f"{score_pill}"
        f"{kind_pill}"
        "</div>"
        "<div class='card-body'>"
        f"{teaser_html}"
        f"{breakdown_html}"
        "</div>"
        "</section>"
    )



def _render_topic_page(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    research_shell_enabled = bool(
        payload.get("research_shell_enabled", _shell_supports_research_nav(requested_pack))
    )
    next_path = _shell_href(
        f"/topic?id={quote(str(payload['center']['object_id']), safe='')}", requested_pack
    )
    assembly_contract_card = _render_assembly_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    neighbors = (
        "".join(
            f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", ""), requested_pack=requested_pack))}">{escape(item["title"])}</a></li>'
            for item in payload["neighbors"]
        )
        or "<li>None</li>"
    )
    mocs = (
        "".join(
            f'<li><a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a></li>'
            for item in payload["provenance"]["mocs"]
        )
        or "<li>None</li>"
    )
    evolution = payload.get(
        "evolution",
        {
            "candidate_items": [],
            "accepted_links": [],
            "accepted_count": 0,
            "candidate_count": 0,
            "link_types": [],
        },
    )
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    summary_form = (
        "<form method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
        + "".join(
            f"<input type='hidden' name='object_id' value='{escape(object_id)}' />"
            for object_id in payload["scoped_stale_summary_ids"]
        )
        + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
        + "<button type='submit'>Rebuild Scoped Summaries</button>"
        + "</form>"
        if payload["scoped_stale_summary_ids"]
        else "<p class='muted'>No stale summaries in this topic scope.</p>"
    )
    contradiction_entry = (
        "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
        + f"<a href='{escape(payload['links']['contradictions_path'])}'>Review scoped contradictions</a>"
        + "</div>"
        if payload["scoped_open_contradiction_ids"]
        else "<p class='muted'>No open contradictions in this topic scope.</p>"
    )
    hero_links = [
        f"<a href='{escape(payload['links']['center_object_path'])}'>Open center object</a>",
    ]
    if research_shell_enabled:
        hero_links.extend(
            [
                f"<a href='{escape(payload['links']['events_path'])}'>Related events</a>",
                f"<a href='{escape(payload['links']['contradictions_path'])}'>Contradictions</a>",
                f"<a href='{escape(payload['links']['summaries_path'])}'>Stale summaries</a>",
                f"<a href='{escape(payload['links']['atlas_path'])}'>Atlas / MOC</a>",
            ]
        )
    right_sections = []
    if research_shell_enabled:
        right_sections.extend(
            [
                f"<section class='card'><h2>Atlas / MOC</h2><ul class='list-tight'>{mocs}</ul></section>",
                "<section class='card'><h2>Evolution</h2>"
                f"<p class='muted'>{evolution['accepted_count']} accepted links and {evolution['candidate_count']} candidate links in scope."
                + (
                    f" Link types: {escape(', '.join(evolution['link_types']))}."
                    if evolution["link_types"]
                    else ""
                )
                + "</p>"
                + f"<h3>Accepted Links</h3>{_render_evolution_links(evolution['accepted_links'], empty_text='No accepted evolution links yet.')}"
                + f"<h3>Candidate Links</h3>{_render_evolution_candidates(evolution['candidate_items'], compact=True, reviewable=True, requested_pack=requested_pack, next_path=next_path)}"
                + "</section>",
                _render_review_context_card(payload["review_context"]),
                _render_review_history(payload["review_history"]),
                "<section class='card'><h2>Quick Maintenance</h2>"
                f"{contradiction_entry}"
                f"{summary_form}"
                "</section>",
            ]
        )
    else:
        right_sections.append(_render_research_scope_notice(requested_pack))
    right_sections.append(
        _render_production_summary_card(
            payload["production_summary"], requested_pack=requested_pack
        )
    )
    # Codex P2: bind the center's vault-relative path (not the
    # object_id) so the BL-083 binder loads the underlying note.
    # ``links.center_object_path`` is a route URL (``/object?id=...``),
    # NOT a vault path — CodeRabbit Major.  Use ``provenance.evergreen_path``
    # (the actual markdown file) instead.
    center_path = str(payload.get("provenance", {}).get("evergreen_path") or "")
    ask_button = _render_ask_about_this_button(
        "object",
        center_path,
        title=str(payload["center"]["title"]),
        requested_pack=requested_pack,
    )
    return _layout(
        f"Topic: {payload['center']['title']}",
        (
            f"<h1 style='margin:4px 0 6px'>Topic: {escape(payload['center']['title'])}</h1>"
            f"<p class='muted'>{payload['neighbor_count']} neighbors, {payload['edge_count']} edges."
            + (f" Pack scope: {escape(requested_pack)}." if requested_pack else "")
            + "</p>"
            + f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{''.join(hero_links)}{ask_button}</div>"
            + _render_compiled_sections(lead_sections)
            + operator_rail_card
            + assembly_contract_card
            + (f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "")
            + "<section class='grid two-col'>"
            f"{_render_compiled_sections(remaining_sections)}"
            f"<section class='card'><h2>Center Summary</h2><p>{escape(payload['center_summary'])}</p></section>"
            f"<section class='card'><h2>Neighbors</h2><ul class='list-tight'>{neighbors}</ul></section>"
            f"{''.join(right_sections)}"
            "</section>"
        ),
        requested_pack=requested_pack,
    )



def _render_atlas_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    limit_note = (
        f" Showing the most recent {payload['limit']} atlas pages in this browser window."
        if payload.get("is_limited")
        else ""
    )

    def render_member_link(member: dict[str, object]) -> str:
        href = _object_href(
            str(member["object_id"]),
            str(member.get("object_path", "")),
            requested_pack=requested_pack,
        )
        return f'<a href="{escape(href)}">{escape(str(member["title"]))}</a>'

    items = (
        "".join(
            "<li>"
            f'<a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
            + f" <span class='pill'>{item['member_count']} objects</span>"
            + f" <span class='pill'>{len(item['source_notes'])} source notes</span>"
            + f" <span class='muted'>{_render_limited_inline_links(item['members'], render_member_link)}</span>"
            + (
                f"<div class='muted'>Preview: {escape(', '.join(item['preview_titles']))}</div>"
                if item["preview_titles"]
                else ""
            )
            + f"<div class='muted'>Source Notes: {_render_named_note_links(item['source_notes'], requested_pack=requested_pack)}</div>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li>None</li>"
    )
    topics_href = _shell_href("/topics", requested_pack)
    return _layout(
        "Atlas / MOC Browser",
        "".join(
            [
                "<h1>Atlas / MOC Browser</h1>",
                f"<p class='muted'>Looking for the reading entry instead? <a href='{escape(topics_href)}'>View Featured Topics →</a></p>",
                "<form method='get' action='/atlas'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter MOCs or objects' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} atlas/moc pages linked to indexed objects.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                "<section class='card'><h2>Contribution Summary</h2><p class='muted'>Each Atlas page now shows the source notes and deep dives that feed the objects it organizes.</p></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_curated_atlas_page(payload: dict) -> str:
    """BL-051: rendered as **Featured Topics**.  The function name
    keeps ``curated_atlas`` for internal consistency with
    ``build_curated_atlas_payload`` and the ``crystal_scores``
    Projection it reads — but every user-facing string says "Topic"."""
    requested_pack = payload.get("requested_pack", "")
    pack = payload.get("pack", "")
    top_n = int(payload.get("top_n") or 0)
    total_chains = int(payload.get("total_chains") or 0)
    entries = payload.get("entries") or []
    generated_at = payload.get("generated_at", "")
    api_href = _shell_href("/api/topics", requested_pack)

    if not entries and total_chains == 0:
        empty_note = (
            "<section class='card'><p>No topics synthesized yet. Run "
            "<code>ovp-synthesize-community-crystals</code> to build the "
            "topic corpus, then <code>ovp-knowledge-index</code> (or "
            "<code>ovp-rescore-crystals</code>) to score it.</p></section>"
        )
        body_html = empty_note
    elif not entries:
        body_html = (
            "<section class='card'><p>The corpus has "
            f"{total_chains} topics but the top-{top_n} window came back empty. "
            "Re-run <code>ovp-rescore-crystals</code> to refresh the Projection.</p></section>"
        )
    else:
        # Each topic entry rendered via the shared _topic_entry_card
        # helper.  Featured Topics uses the full density (with the
        # 6-metric breakdown chips); the home page uses the same
        # helper with compact=True.
        body_html = "".join(_topic_entry_card(entry, compact=False) for entry in entries)

    header_lines = [
        "<h1>Featured Topics</h1>",
        f"<p class='muted'>Top {len(entries)} of {total_chains} synthesized topics in pack "
        f"<code>{escape(pack)}</code>, ranked by <code>crystal_scores</code>. "
        f"Generated {_ts(generated_at)}.</p>",
        f"<p class='muted'><a href='{escape(api_href)}'>JSON</a></p>",
        "<form method='get' action='/topics' style='display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:.5rem 0 1rem'>",
        (
            f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
            if requested_pack
            else ""
        ),
        f"<label>top_n <input type='number' name='top_n' value='{top_n}' min='1' max='{int(payload.get('max_top_n', 100))}' style='width:5rem' /></label> ",
        "<button type='submit'>Refresh</button>",
        "</form>",
    ]
    return _layout(
        "Featured Topics",
        "".join(header_lines) + body_html,
        requested_pack=requested_pack,
    )



def _render_clusters_page(payload: dict, *, action_path: str = "/ops/clusters") -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    total_count = int(payload.get("total_count", payload.get("count", 0)) or 0)
    show_all = bool(payload.get("show_all"))
    limit_value = int(payload.get("limit", 0) or 0)
    default_limit = int(payload.get("default_limit", limit_value) or limit_value)
    offset_value = int(payload.get("offset", 0) or 0)
    rendered_count = int(payload.get("count", 0) or 0)

    def _cluster_href(
        *,
        limit_: int | None = None,
        show_all_: bool | None = None,
        offset_: int | None = None,
    ) -> str:
        params: list[tuple[str, str]] = []
        if requested_pack:
            params.append(("pack", requested_pack))
        if query:
            params.append(("q", query))
        if show_all_ is not None:
            if show_all_:
                params.append(("show_all", "1"))
        elif show_all:
            params.append(("show_all", "1"))
        eff_limit = limit_ if limit_ is not None else default_limit
        if eff_limit and eff_limit != 15 and not (show_all_ or show_all):
            params.append(("limit", str(eff_limit)))
        # Offset only makes sense in the paginated mode; show_all
        # always reads from cluster #0 so we drop it.
        eff_offset = offset_ if offset_ is not None else offset_value
        if eff_offset and not (show_all_ or show_all):
            params.append(("offset", str(eff_offset)))
        if not params:
            return action_path
        return (
            action_path
            + "?"
            + "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params)
        )

    if show_all:
        limit_note = f" Showing all {total_count} clusters."
    elif total_count > 0 and rendered_count > 0:
        # Use 1-indexed range - "Showing 51-100 of 730" reads more
        # naturally than "Showing 50-100 of 730".
        first = offset_value + 1
        last = offset_value + rendered_count
        limit_note = (
            f" Showing {first}-{last} of {total_count}"
            " by priority (member count + open contradictions"
            " + stale summaries)."
        )
    else:
        limit_note = ""

    # Per-page chip rail + Show-all escape hatch — unchanged from
    # PR #179 except that switching per-page resets offset to 0,
    # which the href builder already handles.
    if total_count > 0 and not show_all:
        page_size_links = " ".join(
            (
                f"<strong>{value}</strong>"
                if value == default_limit
                else f"<a href='{escape(_cluster_href(limit_=value, show_all_=False, offset_=0))}'>{value}</a>"
            )
            for value in (15, 50, 200)
        )
        toggle_links = (
            f" · <a href='{escape(_cluster_href(show_all_=True))}'>Show all {total_count}</a>"
            if total_count > default_limit
            else ""
        )
        # Prev/Next pager — clamped at boundaries so first / last page
        # show disabled labels rather than dead links.
        has_prev = offset_value > 0
        has_next = (offset_value + rendered_count) < total_count
        prev_offset = max(0, offset_value - default_limit)
        next_offset = offset_value + default_limit
        if has_prev:
            prev_link = f"<a href='{escape(_cluster_href(offset_=prev_offset))}'>← Prev</a>"
        else:
            prev_link = "<span class='muted'>← Prev</span>"
        if has_next:
            next_link = f"<a href='{escape(_cluster_href(offset_=next_offset))}'>Next →</a>"
        else:
            next_link = "<span class='muted'>Next →</span>"
        cluster_pager = (
            f"<p class='muted'>Per page: {page_size_links}{toggle_links}</p>"
            f"<p class='muted'>{prev_link} · {next_link}</p>"
        )
    elif show_all:
        cluster_pager = (
            "<p class='muted'>"
            f"<a href='{escape(_cluster_href(show_all_=False, limit_=15, offset_=0))}'>← Back to top 15</a>"
            "</p>"
        )
    else:
        cluster_pager = ""
    kind_counts = (
        "".join(
            f"<span class='pill'>{escape(cluster_kind)}: {count}</span>"
            for cluster_kind, count in payload["cluster_kind_counts"].items()
        )
        or "<span class='muted'>None</span>"
    )

    def render_member_link(member: dict[str, object]) -> str:
        return f'<a href="{escape(str(member["path"]))}">{escape(str(member["title"]))}</a>'

    items = (
        "".join(
            "<li>"
            f'<a href="{escape(item["detail_path"])}">{escape(item.get("display_title") or item["label"])}</a>'
            + f" <span class='pill'>{escape(item['cluster_kind'])}</span>"
            + f" <span class='pill'>{escape(item['priority_band'])}</span>"
            + f" <span class='pill'>{item['member_count']} objects</span>"
            + f" <span class='muted'>{_render_limited_inline_links(item['member_links'], render_member_link)}</span>"
            + f"<div class='muted'>Canonical cluster: {escape(item['label'])}</div>"
            + f"<div class='muted'>Center: <a href='{escape(item['center_object_path'])}'>{escape(item['center_title'])}</a></div>"
            + f"<div class='muted'>Priority: {escape(item['priority_reason'])}</div>"
            + (
                f"<div class='muted'>Relation patterns: {escape(item['relation_pattern_preview'])}</div>"
                if item.get("relation_pattern_preview")
                else ""
            )
            + (
                f"<div class='muted'>Related clusters: {item['related_cluster_count']} · {escape(item['related_cluster_preview'])}</div>"
                if item.get("related_cluster_count")
                else ""
            )
            + (
                f"<div class='muted'>Neighborhood: {escape(item['neighborhood_band'])} · {escape(item['neighborhood_bridge_kind'])} · {escape(item['neighborhood_reason'])}</div>"
                if item.get("neighborhood_score")
                else ""
            )
            + (
                f"<div class='muted'>Next read: <a href='{escape(item['next_read_path'])}'>{escape(item['next_read_title'])}</a> · {escape(item['next_read_reason'])}</div>"
                if item.get("next_read_title")
                else ""
            )
            + (
                f"<div class='muted'>Top route: {escape(item['top_reading_route_kind'])} · {escape(item['top_reading_route_title'])} · {escape(item['top_reading_route_reason'])}</div>"
                if item.get("top_reading_route_kind")
                else ""
            )
            + (
                f"<div class='muted'>Reading intents: {item['reading_intent_count']} · {escape(item['reading_intent_preview'])}</div>"
                if item.get("reading_intent_count")
                else ""
            )
            + (
                f"<div class='muted'>{escape(item['top_summary_bullet'])}</div>"
                if item.get("top_summary_bullet")
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No graph clusters found.</li>"
    )
    model_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["model_notes"])
    return _layout(
        "Graph Clusters",
        "".join(
            [
                "<h1>Graph Clusters</h1>",
                _render_page_help(
                    "Graph clusters",
                    what=(
                        "Connected components of pack-scoped graph relations"
                        " (research-tech: louvain communities; other packs:"
                        " plain transitive closure).  Clusters surface the"
                        " neighborhoods a single object lives in."
                    ),
                    can=(
                        "Use sort + per-page (15/50/200) to slice the list."
                        "  <strong>Show all</strong> drops the limit so you"
                        " can audit every cluster.  Click a row to drill"
                        " into the cluster detail page."
                    ),
                    effect=(
                        "Read-only.  Cluster scoring rebuilds when you"
                        " regenerate the graph index, not on click."
                    ),
                ),
                f"<form method='get' action='{escape(action_path)}'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter clusters or members' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>Showing {payload['count']} of {total_count} graph clusters. Largest cluster has {payload['largest_cluster_size']} objects.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                cluster_pager,
                f"<section class='card'><h2>Cluster Kinds</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{kind_counts}</div></section>",
                f"<section class='card'><h2>Model Notes</h2><ul class='list-tight'>{model_notes}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_graph_atlas_page(payload: dict, *, action_path: str = "/map") -> str:
    """Full-bleed dark 3D Atlas at ``/map`` (and ``/graph``).

    Replaces the prior 2D D3 ``_render_graph_map_page`` (which itself
    superseded the static SVG view in ``bb7c961``).  Renders the
    OVP design system kit chrome from
    ``docs/design-system/ui_kits/ovp/graph.html`` and feeds it the
    real vault's atlas projection from ``payload["atlas"]``
    (``communities``, ``nodes``, ``links``).  Three.js + 3d-force-graph
    load from unpkg; ``/static/atlas-graph.js`` does the assembly.

    The Atlas is the design system's one approved dark surface
    (BL-051 caveat in docs/design-system/SKILL.md §5).  The page
    therefore defaults ``data-theme="dark"`` regardless of
    ``localStorage['ovp-theme']`` for first paint, but the in-page
    LIGHT/DARK toggle still writes through to localStorage so the
    rest of the shell follows the operator's last choice.
    """
    requested_pack = payload.get("requested_pack", "")
    query = payload.get("query", "")
    atlas = payload.get("atlas") or {"communities": [], "nodes": [], "links": []}
    summary = payload.get("map_summary") or {}
    node_count = int(summary.get("node_count") or 0)
    edge_count = int(summary.get("edge_count") or 0)
    cluster_count = int(summary.get("cluster_count") or 0)

    # XSS-safe inline JSON: escape characters that could break out of
    # the surrounding ``<script>`` element or kill JSON parsing in
    # certain HTML contexts.
    atlas_json = (
        json.dumps(atlas, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )

    library_href = escape(_shell_href("/", requested_pack))
    ops_href = escape(_shell_href("/ops", requested_pack))
    clusters_href = escape(_shell_href("/ops/clusters", requested_pack))
    search_q = escape(query)
    is_empty = node_count == 0
    empty_visibility = "flex" if is_empty else "none"

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Atlas — Knowledge Graph</title>
    <link rel="icon" type="image/svg+xml" href="/static/monogram.svg" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" />
    <link rel="stylesheet" href="/static/ovp-tokens.css" />
    <link rel="stylesheet" href="/static/atlas-graph.css" />
    <style>
      body {{ font-family: var(--ovp-font-sans); margin: 0;
              background: var(--bg); color: var(--text); }}
    </style>
    <script>
      // Atlas defaults to dark.  If localStorage already has a value,
      // honour it — but read at first paint so there is no flash.
      (function () {{
        try {{
          var saved = localStorage.getItem('ovp-theme');
          if (saved === 'light' || saved === 'dark') {{
            document.documentElement.dataset.theme = saved;
          }}
        }} catch (e) {{}}
      }})();
    </script>
    <script id="ovp-atlas-data" type="application/json">{atlas_json}</script>
    <script>
      try {{
        window.OVP_GRAPH = JSON.parse(
          document.getElementById('ovp-atlas-data').textContent
        );
      }} catch (e) {{ console.error('atlas payload parse failed', e); }}
      window.OVP_ATLAS_ROUTES = {{
        library: "{library_href}",
        ops: "{ops_href}",
        clusters: "{clusters_href}",
        action: "{escape(action_path)}",
      }};
    </script>
  </head>
  <body>
    <div class="graph-page" id="graph-page">
      <div id="graph-canvas"></div>

      <header class="topbar">
        <a class="brand" href="{library_href}">
          <img src="/static/monogram.svg" width="20" height="20" alt="" />
          <span>obsidian vault pipeline<span class="dot">.</span></span>
          <span class="crumb">/ atlas · cluster graph</span>
        </a>
        <div class="search-wrap">
          <span class="muted mono tiny">⌕</span>
          <input id="search" type="text" value="{search_q}"
                 placeholder="Find a node, topic, or community…" autocomplete="off" />
          <span class="kbd">/</span>
          <div class="search-results" id="search-results"></div>
        </div>
        <span class="spacer"></span>
        <a class="nav-link" href="{library_href}">← Library</a>
        <a class="nav-link" href="{ops_href}">Maintenance</a>
        <div class="theme-toggle">
          <button data-theme-set="light" id="th-light">LIGHT</button>
          <button data-theme-set="dark"  id="th-dark" class="active">DARK</button>
        </div>
      </header>

      <aside class="panel left-panel">
        <h4>Communities</h4>
        <div class="desc">Click to isolate · ⇧-click to multi-select</div>
        <div id="legend"></div>
        <div class="filter-section">
          <h4>Node type</h4>
          <label class="filter-row"><input type="checkbox" data-filter="evergreen"     checked />Evergreen<span class="badge" id="cnt-evergreen">0</span></label>
          <label class="filter-row"><input type="checkbox" data-filter="deepdive"      checked />Deep dive<span class="badge" id="cnt-deepdive">0</span></label>
          <label class="filter-row"><input type="checkbox" data-filter="topic"         checked />Topic<span class="badge" id="cnt-topic">0</span></label>
          <label class="filter-row"><input type="checkbox" data-filter="open-question" checked />Open question<span class="badge" id="cnt-open-question">0</span></label>
        </div>
        <div class="filter-section">
          <h4>Source</h4>
          <label class="filter-row"><input type="checkbox" data-filter-src="manual"   checked />Manual</label>
          <label class="filter-row"><input type="checkbox" data-filter-src="pinboard" checked />Pinboard</label>
          <label class="filter-row"><input type="checkbox" data-filter-src="clipper"  checked />Clipper</label>
          <label class="filter-row"><input type="checkbox" data-filter-src="github"   checked />GitHub</label>
        </div>
        <div class="filter-section">
          <h4>Quality threshold</h4>
          <input type="range" id="quality-slider" min="0" max="5" step="0.1" value="0" style="width:100%;accent-color:var(--accent)" />
          <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:var(--muted);font-family:var(--ovp-font-mono);margin-top:4px">
            <span>≥ <span id="quality-val">0.0</span></span>
            <span>5.0</span>
          </div>
        </div>
      </aside>

      <div class="hud">
        <div class="chip"><strong id="hud-nodes">{node_count}</strong>nodes</div>
        <div class="chip"><strong id="hud-links">{edge_count}</strong>links</div>
        <div class="chip"><strong id="hud-comms">{cluster_count}</strong>communities</div>
        <div class="chip" id="hud-mode"><strong>EXPAND</strong>double-click</div>
      </div>

      <div class="timeline-bar"></div>

      <aside class="panel right-panel" id="detail">
        <div class="detail-empty">
          <div class="glyph">⌖</div>
          <div>Click a node to inspect.</div>
          <div style="margin-top:6px;font-size:0.78rem">Or hover to highlight neighbors.</div>
        </div>
      </aside>

      <div class="panel tweaks" id="tweaks">
        <h4>Tweaks</h4>
        <div class="tweaks-row">
          <span class="label">View</span>
          <div class="seg" id="seg-dims">
            <button data-dims="3" class="active">3D</button>
            <button data-dims="2">2D</button>
          </div>
        </div>
        <div class="tweaks-row">
          <span class="label">Disclosure</span>
          <div class="seg" id="seg-mode">
            <button data-mode="dblclick" class="active">Dbl-click</button>
            <button data-mode="hover">Hover</button>
            <button data-mode="zoom">Zoom</button>
          </div>
        </div>
        <div class="tweaks-row">
          <span class="label">Communities</span>
          <div class="seg" id="seg-super">
            <button data-super="off" class="active">Expanded</button>
            <button data-super="on">Collapsed</button>
          </div>
        </div>
        <div class="tweaks-row">
          <span class="label">Show hulls</span>
          <div class="seg" id="seg-hulls">
            <button data-hulls="on" class="active">On</button>
            <button data-hulls="off">Off</button>
          </div>
        </div>
        <div class="tweaks-row">
          <span class="label">Link strength</span>
          <input type="range" id="link-strength" min="-300" max="-30" step="10" value="-150" />
          <span class="val" id="link-strength-val">-150</span>
        </div>
        <div class="tweaks-row">
          <span class="label">Node base size</span>
          <input type="range" id="node-size" min="2" max="12" step="0.5" value="6" />
          <span class="val" id="node-size-val">6</span>
        </div>
        <div class="tweaks-row">
          <span class="label">Spin</span>
          <div class="seg" id="seg-spin">
            <button data-spin="off" class="active">Off</button>
            <button data-spin="slow">Slow</button>
          </div>
        </div>
      </div>

      <div class="hover-label" id="hover-label">
        <div class="ttl"></div>
        <div class="sub"></div>
      </div>

      <div class="atlas-empty" id="atlas-empty" style="display: {empty_visibility}">
        <div class="glyph">∅</div>
        <h2>No nodes in scope</h2>
        <p>Try widening the search query, removing the per-cluster cap with
           <code>?show_all=1</code>, or seeding the graph via
           <code>ovp-knowledge-index</code>.
           The cluster browser at <a href="{clusters_href}">/ops/clusters</a>
           lists every neighborhood currently in the projection.</p>
      </div>
    </div>

    <script src="https://unpkg.com/three@0.155.0/build/three.min.js"></script>
    <script src="https://unpkg.com/3d-force-graph@1.73.4/dist/3d-force-graph.min.js"></script>
    <script src="/static/atlas-graph.js"></script>
  </body>
</html>
"""



def _render_cluster_detail_page(payload: dict) -> str:
    cluster = payload["cluster"]
    requested_pack = payload.get("requested_pack", "")
    edge_kind_counts = (
        "".join(
            f"<span class='pill'>{escape(edge_kind)}: {count}</span>"
            for edge_kind, count in payload["edge_kind_counts"].items()
        )
        or "<span class='muted'>None</span>"
    )
    object_kind_counts = (
        "".join(
            f"<span class='pill'>{escape(object_kind)}: {count}</span>"
            for object_kind, count in payload["object_kind_counts"].items()
        )
        or "<span class='muted'>None</span>"
    )
    summary_bullets = (
        "".join(f"<li>{escape(item)}</li>" for item in payload["summary_bullets"])
        or "<li class='muted'>No cluster summary available.</li>"
    )
    members = (
        "".join(
            f'<li><a href="{escape(member["path"])}">{escape(member["title"])}</a></li>'
            for member in cluster["member_links"]
        )
        or "<li class='muted'>No members.</li>"
    )
    edges = (
        "".join(
            "<li>"
            f'<a href="{escape(edge["source_path"])}">{escape(edge["source_title"])}</a>'
            f" <span class='pill'>{escape(edge['edge_kind'])}</span> "
            f'<a href="{escape(edge["target_path"])}">{escape(edge["target_title"])}</a>'
            + (
                f" <span class='muted'>source: {escape(edge['evidence_source_slug'])}</span>"
                if edge["evidence_source_slug"]
                else ""
            )
            + "</li>"
            for edge in payload["edges"]
        )
        or "<li class='muted'>No internal edges for this cluster.</li>"
    )
    top_source_notes = (
        "".join(
            f"<li>{escape(item['title'])} <span class='pill'>{item['object_count']} objects</span></li>"
            for item in payload["top_source_notes"]
        )
        or "<li class='muted'>No source-note coverage.</li>"
    )
    top_mocs = (
        "".join(
            f"<li>{escape(item['title'])} <span class='pill'>{item['object_count']} objects</span></li>"
            for item in payload["top_mocs"]
        )
        or "<li class='muted'>No atlas coverage.</li>"
    )
    open_contradictions = (
        "".join(
            f"<li><a href=\"{escape(item['path'])}\">{escape(item['subject_key'])}</a> <span class='pill'>{len(item['object_ids'])} objects</span></li>"
            for item in payload["open_contradictions"]
        )
        or "<li class='muted'>No open contradictions in this cluster.</li>"
    )
    stale_summaries = (
        "".join(
            f"<li><a href=\"{escape(item['object_path'])}\">{escape(item['title'])}</a> <span class='pill'>{', '.join(escape(code) for code in item['reason_codes'])}</span></li>"
            for item in payload["stale_summaries"]
        )
        or "<li class='muted'>No stale summaries in this cluster.</li>"
    )
    related_clusters = (
        "".join(
            "<li>"
            f'<a href="{escape(item["detail_path"])}">{escape(item["display_title"])}</a> '
            f"<span class='pill'>{item['member_count']} objects</span> "
            f"<span class='pill'>{escape(item['bridge_kind'])}</span> "
            f"<span class='pill'>{escape(item['reason'])}</span>"
            + (
                f"<div class='muted'>Shared source notes: {escape(', '.join(item['shared_source_titles']))}</div>"
                if item["shared_source_titles"]
                else ""
            )
            + (
                f"<div class='muted'>Shared atlas pages: {escape(', '.join(item['shared_moc_titles']))}</div>"
                if item["shared_moc_titles"]
                else ""
            )
            + "</li>"
            for item in payload["related_clusters"]
        )
        or "<li class='muted'>No related clusters surfaced for this scope.</li>"
    )
    related_cluster_groups = (
        "".join(
            f"<li>{escape(item['display_name'])} <span class='pill'>{item['count']}</span>"
            + (
                f"<div class='muted'>{escape(', '.join(item['cluster_titles'][:3]))}</div>"
                if item["cluster_titles"]
                else ""
            )
            + "</li>"
            for item in payload["related_cluster_groups"]
        )
        or "<li class='muted'>No neighborhood groups surfaced for this cluster.</li>"
    )
    reading_routes = (
        "".join(
            "<li>"
            f"<span class='pill'>#{item['route_rank']}</span> "
            f"{escape(item['display_name'])}: "
            f'<a href="{escape(item["detail_path"])}">{escape(item["display_title"])}</a> '
            f"<span class='pill'>{escape(item['bridge_kind'])}</span> "
            f"<span class='pill'>{escape(item['bridge_band'])}</span>"
            f"<div class='muted'>Score: {item['route_score']} · {escape(item['route_reason'])}</div>"
            f"<div class='muted'>Bridge evidence: {escape(item['reason'])}</div>"
            "</li>"
            for item in payload["reading_routes"]
        )
        or "<li class='muted'>No reading routes derived for this cluster.</li>"
    )
    next_read_cluster = payload.get("next_read_cluster")
    next_read_route = (
        (
            "<p>"
            f'<a href="{escape(next_read_cluster["detail_path"])}">{escape(next_read_cluster["display_title"])}</a> '
            f"<span class='pill'>{escape(next_read_cluster['bridge_kind'])}</span> "
            f"<span class='pill'>{escape(next_read_cluster['bridge_band'])}</span>"
            "</p>"
            f"<p class='muted'>{escape(next_read_cluster['reason'])}</p>"
            + (
                f"<p class='muted'>Shared source notes: {escape(', '.join(next_read_cluster['shared_source_titles']))}</p>"
                if next_read_cluster["shared_source_titles"]
                else ""
            )
            + (
                f"<p class='muted'>Shared atlas pages: {escape(', '.join(next_read_cluster['shared_moc_titles']))}</p>"
                if next_read_cluster["shared_moc_titles"]
                else ""
            )
        )
        if next_read_cluster
        else "<p class='muted'>No next reading route surfaced for this cluster.</p>"
    )
    relation_patterns = (
        "".join(
            f"<li>{escape(item['display_name'])} <span class='pill'>{item['count']}</span></li>"
            for item in payload["relation_pattern_items"]
        )
        or "<li class='muted'>No relation patterns in this cluster.</li>"
    )
    review_context = payload["review_context"]
    model_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["model_notes"])
    # Single force-graph stack across the app: every visual graph
    # (atlas, cluster, future neighborhood views) goes through
    # ``/map`` with ``?community=<cluster_id>`` so atlas-graph.js
    # pre-isolates this cluster on first paint.  This retires the
    # standalone D3 SVG mount that used to live here under
    # ``render_cluster_force_graph`` — same tech, same visual identity
    # as /map, no duplicate codepath.
    cluster_id = str(cluster.get("cluster_id") or "")
    atlas_href = _shell_href(
        f"/map?community={quote(cluster_id, safe='')}",
        requested_pack,
    )
    force_graph_section = (
        "<section class='card'><h2>Force-Directed View</h2>"
        "<p class='muted'>Cluster member graph renders in the dark "
        "Atlas, scoped to this community.  Same tech and visual "
        "identity as the global Knowledge Map — drag to pan, scroll "
        "to zoom, double-click any node to return to the full atlas.</p>"
        f"<p><a class='btn' href='{escape(atlas_href)}'>Open this cluster in the Atlas →</a></p>"
        "</section>"
    )
    return _layout(
        "Graph Cluster",
        (
            "<h1>Graph Cluster</h1>"
            f"<p><a href='{escape(payload['browser_path'])}'>Back to clusters</a></p>"
            f"<section class='card'><h2>{escape(payload.get('display_title') or cluster['label'])}</h2>"
            f"<p class='muted'>Pack: {escape(cluster['pack'])} · Kind: {escape(cluster['cluster_kind'])} · Score: {cluster['score']:.1f}</p>"
            f"<p class='muted'>Canonical cluster id: {escape(cluster['cluster_id'])}</p>"
            f"<p>Center: <a href='{escape(cluster['center_object_path'])}'>{escape(cluster['center_title'])}</a></p>"
            f"<p class='muted'>{cluster['member_count']} member objects.</p>"
            "</section>"
            + force_graph_section
            + f"<section class='card'><h2>Cluster Synthesis</h2><ul class='list-tight'>{summary_bullets}</ul></section>"
            f"<section class='card'><h2>Structural Label</h2><p><strong>{escape(payload['structural_label']['title'])}</strong></p><p class='muted'>{escape(payload['structural_label']['reason'])}</p></section>"
            f"<section class='card'><h2>Relation Patterns</h2><ul class='list-tight'>{relation_patterns}</ul></section>"
            f"<section class='card'><h2>Review Pressure</h2><h3>Open Contradictions</h3><ul class='list-tight'>{open_contradictions}</ul><h3>Stale Summaries</h3><ul class='list-tight'>{stale_summaries}</ul></section>"
            f"<section class='card'><h2>Reading Routes</h2><ul class='list-tight'>{reading_routes}</ul></section>"
            f"<section class='card'><h2>Next Reading Route</h2>{next_read_route}</section>"
            f"<section class='card'><h2>Neighborhood Groups</h2><ul class='list-tight'>{related_cluster_groups}</ul></section>"
            f"<section class='card'><h2>Related Clusters</h2><ul class='list-tight'>{related_clusters}</ul></section>"
            f"<section class='card'><h2>Edge Kinds</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{edge_kind_counts}</div></section>"
            f"<section class='card'><h2>Object Kinds</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{object_kind_counts}</div></section>"
            f"<section class='card'><h2>Coverage</h2><p class='muted'>"
            f"{review_context['source_note_count']} source notes · "
            f"{review_context['moc_count']} atlas pages · "
            f"{review_context['open_contradiction_count']} open contradictions · "
            f"{review_context['stale_summary_count']} stale summaries"
            "</p></section>"
            f"<section class='card'><h2>Top Source Notes</h2><ul class='list-tight'>{top_source_notes}</ul></section>"
            f"<section class='card'><h2>Top Atlas Pages</h2><ul class='list-tight'>{top_mocs}</ul></section>"
            f"<section class='card'><h2>Members</h2><ul class='list-tight'>{members}</ul></section>"
            f"<section class='card'><h2>Internal Edges</h2><ul class='list-tight'>{edges}</ul></section>"
            f"<section class='card'><h2>Model Notes</h2><ul class='list-tight'>{model_notes}</ul></section>"
        ),
        requested_pack=requested_pack,
    )


__all__ = [
    '_topic_entry_card',
    '_render_topic_page',
    '_render_atlas_page',
    '_render_curated_atlas_page',
    '_render_clusters_page',
    '_render_graph_atlas_page',
    '_render_cluster_detail_page'
]
