# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from ._layer4 import *




def _build_runtime_home_payload_from_query(vault_dir: Path, query: dict[str, list[str]]) -> dict:
    pack_name = query.get("pack", [""])[0] or None
    return build_runtime_home_payload(vault_dir, pack_name=pack_name)



def _anchor_title_for_note(relative_path: str, markdown: str) -> str:
    """Return the friendly title for a note anchor.

    Prefers the H1 line from the markdown body, then the YAML
    ``title:`` field *from the frontmatter block only* (CodeRabbit
    M — searching the whole body would pick up an example code
    fence's ``title:`` line), then the path basename.
    """
    # ``_parse_frontmatter`` already handles malformed YAML
    # (returns ``{}, markdown``), so no try/except needed
    # (CodeRabbit Minor — narrow broad exceptions).
    frontmatter, body = _parse_frontmatter(markdown)
    for line in body.splitlines():
        if line.startswith("# "):
            text = line[2:].strip()
            if text:
                return text
            break
        if line.startswith("## ") or line.startswith("### "):
            break
    if isinstance(frontmatter, dict):
        candidate = str(frontmatter.get("title") or "").strip()
        if candidate:
            return candidate
    stem = relative_path.rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return stem



def _read_vault_note(vault_dir: Path, relative_path: str) -> tuple[Path, str]:
    candidate = (vault_dir / relative_path).resolve()
    try:
        candidate.relative_to(vault_dir.resolve())
    except ValueError as exc:
        raise ValueError("invalid note path") from exc
    if not candidate.is_file():
        raise ValueError(f"note not found: {relative_path}")
    return candidate, candidate.read_text(encoding="utf-8")



def _render_markdown_note(
    vault_dir: Path, markdown: str, *, requested_pack: str = ""
) -> tuple[str, str]:
    frontmatter, body = _parse_frontmatter(markdown)
    github_repo_base = _infer_github_repo_base(frontmatter, body)
    rendered_body = _convert_box_table_fences(body, github_repo_base=github_repo_base)
    rendered_body = _rewrite_local_image_links(vault_dir, rendered_body)
    rendered_body = _replace_wikilinks_with_markdown_links(
        vault_dir, rendered_body, requested_pack=requested_pack
    )
    rendered_body = _linkify_related_knowledge_section(
        vault_dir, rendered_body, requested_pack=requested_pack
    )
    rendered_body = _linkify_keywords(rendered_body, requested_pack=requested_pack).strip()
    if not rendered_body:
        html_body = "<p class='muted'>Empty note.</p>"
    else:
        html_body = _MARKDOWN_RENDERER.render(rendered_body)
    return _render_frontmatter(frontmatter), html_body



def _is_thin_note(relative_path: str, markdown: str) -> bool:
    """Decide whether ``/note?path=<relative_path>`` should render
    the thin shell (header + body) instead of the full evergreen
    scaffold.

    Detection signals (any one is sufficient):

    * file path under ``_THIN_NOTE_PATH_PREFIXES``
    * ``_resolve_effective_type`` lands on a thin type

    Files outside both keep the existing full-scaffold behaviour
    so evergreens, deep-dives, and atlas pages render unchanged.
    """
    normalised = (relative_path or "").replace("\\", "/")
    if any(normalised.startswith(prefix) for prefix in _THIN_NOTE_PATH_PREFIXES):
        return True
    try:
        frontmatter, _ = _parse_frontmatter(markdown)
    except Exception:
        return False
    return _resolve_effective_type(frontmatter) in _THIN_NOTE_TYPES



def _render_thin_note_preamble(
    relative_path: str, markdown: str, *, requested_pack: str = ""
) -> str:
    """Type-aware "what is this page" card.

    Goal: someone who lands on a digest or Live Concept page knows
    immediately what the file is, where it came from, and what to
    do next.  The user complaint that prompted this was "I don't
    see any links and I'm not sure what made this appear".

    Returns ``""`` when no preamble applies, so the thin shell can
    concatenate unconditionally.
    """
    try:
        frontmatter, _ = _parse_frontmatter(markdown)
    except Exception:
        frontmatter = {}

    type_value = _resolve_effective_type(frontmatter)

    if type_value == "digest":
        return _render_digest_preamble(frontmatter)
    if type_value == "live-concept":
        return _render_live_concept_preamble(
            relative_path,
            frontmatter,
            requested_pack=requested_pack,
        )
    if type_value == "user-profile":
        return _render_user_profile_preamble()
    if relative_path.startswith("40-Resources/Generated/"):
        return _render_generated_preamble(relative_path, frontmatter)
    return ""



def _render_note_page(
    vault_dir: Path, relative_path: str, markdown: str, payload: dict | None = None
) -> str:
    requested_pack = payload.get("requested_pack", "") if payload else ""
    frontmatter_html, note_html = _render_markdown_note(
        vault_dir, markdown, requested_pack=requested_pack
    )

    # Thin shell for digest / live-concept / user-profile / anything
    # under 40-Resources/Generated/.  Header → preamble → body →
    # frontmatter (collapsed).  No source-chain / production-chain /
    # inbound-capture cards because those concepts don't apply to
    # agent-produced surfaces.  The preamble card explains *where*
    # the file came from and *what to do with it*, which the user
    # complained was missing on digest + Live Concept pages.
    if _is_thin_note(relative_path, markdown):
        preamble_html = _render_thin_note_preamble(
            relative_path,
            markdown,
            requested_pack=requested_pack,
        )
        ask_button = _render_ask_about_this_button(
            "note",
            relative_path,
            title=_anchor_title_for_note(relative_path, markdown),
            requested_pack=requested_pack,
        )
        # M22 BL-093: prev/next pivot when this is a daily digest
        # so operators can step through history without bouncing
        # back to the /digests list every time.
        # User feedback (2026-05-13): the "Ask about this" button
        # and the digest prev/next strip were rendering on separate
        # lines.  Lay them out as a single flex row so the page
        # header stays compact.
        digest_nav_html = _render_digest_neighbour_nav(vault_dir, relative_path)
        actions_row = (
            "<div class='entry-actions' "
            "style='display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap'>"
            f"{ask_button}"
            f"{digest_nav_html}"
            "</div>"
        )
        return _layout(
            f"Markdown Note: {relative_path}",
            (
                "<h1>Markdown Note</h1>"
                f"<p class='muted'>{escape(relative_path)}</p>"
                + actions_row
                + preamble_html
                + f"<section class='card'>{note_html}</section>"
                + _render_frontmatter_details(frontmatter_html)
            ),
            requested_pack=requested_pack,
        )

    source_note = None
    production_chain = None
    compiled_sections: list[dict[str, object]] = []
    section_nav_items: list[dict[str, str]] = []
    if payload:
        source_note = payload.get("provenance", {}).get("original_source_note")
        production_chain = payload.get("production_chain")
        compiled_sections = list(payload.get("compiled_sections") or [])
        section_nav_items = list(payload.get("section_nav") or [])
    lead_sections, remaining_sections = _split_lead_compiled_sections(compiled_sections)
    provenance_html = ""
    if source_note:
        provenance_html = (
            "<section class='card'>"
            "<h2>Provenance</h2>"
            "<table class='kv'>"
            "<tr><th>Original Source Note</th><td>"
            f'<a href="{escape(_note_href(source_note["path"], requested_pack))}">{escape(source_note["title"])}</a>'
            f"<div class='muted'>{escape(source_note['path'])}</div>"
            "</td></tr>"
            "</table>"
            "</section>"
        )
    production_chain_html = ""
    if production_chain:
        missing_stages = (
            ", ".join(
                str(item).replace("_", " ") for item in production_chain.get("missing_stages", [])
            )
            or "None"
        )
        production_chain_html = (
            "<section class='card'>"
            "<h2>Production Chain</h2>"
            "<table class='kv'>"
            f"<tr><th>Current Note</th><td>{escape(production_chain['note']['title'])}<div class='muted'>{escape(production_chain['note']['path'])}</div></td></tr>"
            f"<tr><th>Chain Status</th><td>{escape(str(production_chain.get('chain_status') or ''))}</td></tr>"
            f"<tr><th>Missing Stages</th><td>{escape(missing_stages)}</td></tr>"
            f"<tr><th>Chain Summary</th><td>{escape(str(production_chain.get('chain_summary') or ''))}</td></tr>"
            f"<tr><th>Source Notes</th><td>{_render_named_note_links(production_chain['source_notes'], requested_pack=requested_pack)}</td></tr>"
            f"<tr><th>Derived Objects</th><td>{_render_object_links(production_chain['objects'], requested_pack=requested_pack)}</td></tr>"
            f"<tr><th>Atlas / MOC Reach</th><td>{_render_named_note_links(production_chain['atlas_pages'], requested_pack=requested_pack)}</td></tr>"
            "</table>"
            "</section>"
        )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in section_nav_items
    )
    lineage_html = _render_lineage_card(
        payload.get("lineage") if payload else None,
        requested_pack=requested_pack,
    )
    operator_rail_card = _render_operator_rail(payload or {})
    # Body-first ordering: the actual note content (``note_html``)
    # leads the page so readers don't scroll past empty scaffold to
    # find what they came for.  Scaffolding cards (lineage,
    # provenance, production-chain, compiled-section probes) sit
    # after the body.  Frontmatter is collapsed into a <details>
    # below the body — it's metadata, not lede.
    ask_button = _render_ask_about_this_button(
        "note",
        relative_path,
        title=_anchor_title_for_note(relative_path, markdown),
        requested_pack=requested_pack,
    )
    return _layout(
        f"Markdown Note: {relative_path}",
        (
            "<h1>Markdown Note</h1>"
            f"<p class='muted'>{escape(relative_path)}</p>"
            + f"<div class='entry-actions'>{ask_button}</div>"
            + (f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "")
            + f"<section class='card'>{note_html}</section>"
            + _render_compiled_sections(lead_sections)
            + operator_rail_card
            + f"{lineage_html}"
            + f"{provenance_html}"
            + f"{production_chain_html}"
            + f"{_render_compiled_sections(remaining_sections)}"
            + _render_frontmatter_details(frontmatter_html)
        ),
        requested_pack=requested_pack,
    )



def _render_search_page(payload: dict) -> str:
    query = payload["query"]
    requested_pack = payload.get("requested_pack", "")
    page = int(payload.get("page", 1))
    page_size = int(payload.get("page_size", len(payload["objects"]) or 1))
    object_total = int(payload.get("object_total", payload["object_count"]))
    note_total = int(payload.get("note_total", payload["note_count"]))

    def _pager(total: int, label: str) -> str:
        last_page = max(1, (total + page_size - 1) // page_size)
        if last_page <= 1:
            return ""
        from urllib.parse import urlencode

        def _link(target_page: int, text: str, disabled: bool) -> str:
            if disabled:
                return f"<span class='muted'>{text}</span>"
            params = {"q": query, "page": target_page}
            if requested_pack:
                params["pack"] = requested_pack
            href = "/search?" + urlencode(params)
            return f"<a href='{escape(href)}'>{text}</a>"

        prev_link = _link(max(1, page - 1), "« Prev", page <= 1)
        next_link = _link(min(last_page, page + 1), "Next »", page >= last_page)
        # Objects and notes share the same `page` cursor but may have different
        # totals, so clamp the displayed value to avoid "page 3 of 2" when the
        # smaller result set runs out.
        displayed_page = min(page, last_page)
        return (
            f"<p class='muted'>{label}: page {displayed_page} of {last_page} "
            f"&middot; {prev_link} &middot; {next_link}</p>"
        )

    def _render_group_card(
        group: dict, *, fallback_label: str, empty_text: str, item_html: str
    ) -> str:
        rendered_items = item_html or f'<li class="muted">{escape(empty_text)}</li>'
        return (
            "<section class='card'>"
            f"<h2>{escape(str(group.get('label') or fallback_label))}</h2>"
            f"<p class='muted'>{int(group.get('result_count') or 0)} result(s)</p>"
            f"<ul class='list-tight'>{rendered_items}</ul>"
            "</section>"
        )

    def _render_reader_group(group: dict) -> str:
        items = group.get("items") or []
        item_html = "".join(
            "<li>"
            f'<a href="{escape(item.get("object_path") or _object_href(item["object_id"], requested_pack=requested_pack))}">{escape(str(item["title"]))}</a>'
            f"<p>{escape(str(item.get('summary') or 'No compiled summary yet.'))}</p>"
            f"<p class='muted'>{escape(str(item.get('reason') or 'Matched object text.'))} "
            f"Evidence: {int(item.get('evidence_count') or 0)}</p>"
            "</li>"
            for item in items
        )
        return _render_group_card(
            group,
            fallback_label="Objects",
            empty_text="No object hits.",
            item_html=item_html,
        )

    def _render_source_group(group: dict) -> str:
        items = group.get("items") or []
        item_html = "".join(
            "<li>"
            f'<a href="{escape(item.get("note_path") or _note_href(item["path"], requested_pack))}">{escape(str(item["title"]))}</a> '
            f'<span class="pill">{escape(str(item["note_type"]))}</span>'
            f"<p class='muted'>{escape(str(item.get('reason') or 'Matched note title or body.'))}</p>"
            "</li>"
            for item in items
        )
        return _render_group_card(
            group,
            fallback_label="Notes",
            empty_text="No note hits.",
            item_html=item_html,
        )

    reader_groups = payload.get("reader_groups") or []
    source_groups = payload.get("source_groups") or []
    reader_group_html = (
        "".join(_render_reader_group(group) for group in reader_groups)
        or "<section class='card'><h2>Objects</h2><p class='muted'>No object hits.</p></section>"
    )
    source_group_html = (
        "".join(_render_source_group(group) for group in source_groups)
        or "<section class='card'><h2>Notes</h2><p class='muted'>No note hits.</p></section>"
    )
    showing = (
        f"Showing {payload['object_count']} of {object_total} object hits, "
        f"{payload['note_count']} of {note_total} note hits."
    )
    return _layout(
        f"Search: {query}",
        "".join(
            [
                "<h1>Reader Search</h1>",
                "<form method='get' action='/search' style='display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:.5rem 0 1rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' /> "
                    if requested_pack
                    else ""
                ),
                f"<input type='search' name='q' value='{escape(query)}' placeholder='Search vault' />",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{escape(str(payload.get('reader_summary') or showing))}</p>",
                "<p class='muted'>Objects are grouped by kind. Notes are grouped by source type.</p>",
                "<section class='grid two-col'>",
                f"<div style='display:grid;gap:1rem'>{reader_group_html}{_pager(object_total, 'Objects')}</div>",
                f"<div style='display:grid;gap:1rem'>{source_group_html}{_pager(note_total, 'Notes')}</div>",
                "</section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _render_library_home(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    object_items = (
        "".join(
            f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", ""), requested_pack=requested_pack))}">{escape(item["title"])}</a></li>'
            for item in payload.get("objects", {}).get("items", [])
        )
        or "<li class='muted'>No library items yet.</li>"
    )
    object_count = int(payload.get("objects", {}).get("count") or 0)
    map_path = _shell_href("/map", requested_pack)
    workbench_path = _shell_href("/ops", requested_pack)
    search_path = _shell_href("/search", requested_pack)
    map_card = (
        "<div class='card' style='margin:0'>"
        "<div class='muted tiny'>Knowledge Map</div>"
        f"<div style='margin-top:8px'><a href='{escape(map_path)}'>See how ideas connect →</a></div>"
        "</div>"
        if _shell_supports_research_nav(requested_pack)
        else ""
    )
    # ``/`` is part of the Reader shell after BL-050 — never show the
    # Workbench card here.  Maintainer entry is the cross-shell link
    # rendered by ``_layout``.
    workbench_card = ""
    body = "".join(
        [
            "<h1>Knowledge Library</h1>",
            "<p class='muted'>Browse the people, concepts, sources, and ideas in this vault.</p>",
            "<section class='grid stats'>",
            f"<div class='card'><div class='muted tiny'>Library Items</div>"
            f"<div class='metric-num'>{object_count}</div></div>",
            f"<div class='card'><div class='muted tiny'>Search Library</div>"
            f"<div style='margin-top:8px'><a href='{escape(search_path)}'>Search by title, topic, or source →</a></div></div>",
            map_card,
            "</section>",
            "<section class='grid two-col'>",
            "<section class='card'><h2>Recent Knowledge</h2>",
            f"<ul class='list-tight'>{object_items}</ul>",
            "</section>",
            workbench_card,
            "</section>",
        ]
    )
    return _layout("Knowledge Library", body, requested_pack=requested_pack)



def _render_objects_index(payload: dict) -> str:
    query = payload.get("query", "")
    active_kind = str(payload.get("object_kind") or "")
    requested_pack = payload.get("requested_pack", "")
    sort = payload.get("sort", "alpha") or "alpha"
    limit = int(payload.get("limit", 100) or 100)
    offset = int(payload.get("offset", 0) or 0)
    total_count = int(payload.get("total_count", 0) or 0)
    object_kind = active_kind  # alias kept for the shared href builder
    kind_stats = payload.get("kind_stats") or []

    def _href(
        *,
        sort_: str | None = None,
        offset_: int | None = None,
        limit_: int | None = None,
        object_kind_: str | None = None,
    ) -> str:
        params: list[tuple[str, str]] = []
        if requested_pack:
            params.append(("pack", requested_pack))
        if query:
            params.append(("q", query))
        eff_kind = object_kind_ if object_kind_ is not None else object_kind
        if eff_kind:
            params.append(("object_kind", eff_kind))
        eff_sort = sort_ if sort_ is not None else sort
        if eff_sort and eff_sort != "alpha":
            params.append(("sort", eff_sort))
        eff_limit = limit_ if limit_ is not None else limit
        if eff_limit != 100:
            params.append(("limit", str(eff_limit)))
        eff_offset = offset_ if offset_ is not None else offset
        if eff_offset:
            params.append(("offset", str(eff_offset)))
        if not params:
            return "/ops/objects"
        return "/ops/objects?" + "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params
        )

    def _item_html(item: dict) -> str:
        suffix = ""
        if sort == "most_linked":
            count = int(item.get("backlink_count", 0) or 0)
            suffix = f" <span class='muted'>· {count} backlinks</span>"
        return (
            "<li>"
            f'<a href="{escape(_object_href(item["object_id"], item.get("object_path", ""), requested_pack=requested_pack))}">'
            f'{escape(item["title"])}</a> '
            f'<span class="muted">({escape(item["object_id"])})</span>'
            f"{suffix}</li>"
        )

    items = "".join(_item_html(item) for item in payload["items"]) or (
        "<li class='muted'>No objects match the current filter.</li>"
    )

    sort_options = (
        ("alpha", "A → Z"),
        ("most_linked", "Most-linked"),
    )
    sort_links = " · ".join(
        (
            f"<strong>{escape(label)}</strong>"
            if value == sort
            else f"<a href='{escape(_href(sort_=value, offset_=0))}'>{escape(label)}</a>"
        )
        for value, label in sort_options
    )

    page_size_links = " ".join(
        (
            f"<strong>{value}</strong>"
            if value == limit
            else f"<a href='{escape(_href(limit_=value, offset_=0))}'>{value}</a>"
        )
        for value in _OBJECTS_INDEX_PAGE_SIZES
    )

    showing_start = offset + 1 if total_count else 0
    showing_end = min(offset + limit, total_count)
    showing = (
        f"Showing {showing_start}–{showing_end} of {total_count}" if total_count else "No matches"
    )

    prev_offset = max(0, offset - limit)
    has_prev = offset > 0
    has_next = offset + limit < total_count
    pager_parts: list[str] = []
    if has_prev:
        pager_parts.append(f"<a href='{escape(_href(offset_=prev_offset))}'>← Prev</a>")
    else:
        pager_parts.append("<span class='muted'>← Prev</span>")
    if has_next:
        pager_parts.append(f"<a href='{escape(_href(offset_=offset + limit))}'>Next →</a>")
    else:
        pager_parts.append("<span class='muted'>Next →</span>")
    pager = " · ".join(pager_parts)

    facet_html = _render_type_facet(
        kind_stats,
        active_kind=active_kind,
        query=query,
        requested_pack=requested_pack,
        base_path="/ops/objects",
    )

    # Active filter banner — gives the reader a clear "you're seeing
    # only X" signal + a one-click escape hatch.  Reuses the shared
    # query-string builder so the clear-link can't drift from the
    # chip-rail URLs.
    filter_banner = ""
    if active_kind:
        from ovp_pipeline.object_kinds import display_label

        clear_href = "/ops/objects" + _build_objects_query_string(
            query=query, requested_pack=requested_pack
        )
        filter_banner = (
            f"<p class='muted'>Filtered to type "
            f"<strong>{escape(display_label(active_kind))}</strong>"
            f" · <a href='{escape(clear_href)}'>clear filter</a></p>"
        )

    body = (
        "<h1>Objects</h1>"
        + _render_page_help(
            "Objects",
            what=(
                "Every canonical Evergreen object in the pack-scoped truth"
                " store.  These are the rows downstream consumers"
                " (briefings, deep dives, atlas, signals) read from."
            ),
            can=(
                "Filter by text or object kind (chip rail below), sort by"
                " alpha or most-linked, paginate 10/50/100/200 at a time."
                "  Click any row to open the full object page."
            ),
            effect=(
                "Read-only browser.  Mutations live on the per-object"
                " page (and on /ops/queue/contradictions for"
                " contradiction resolution)."
            ),
        )
        + "<form method='get' action='/ops/objects'>"
        + (
            f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
            if requested_pack
            else ""
        )
        + (
            f"<input type='hidden' name='sort' value='{escape(sort)}' />"
            if sort and sort != "alpha"
            else ""
        )
        + (
            f"<input type='hidden' name='object_kind' value='{escape(object_kind)}' />"
            if object_kind
            else ""
        )
        + f"<input type='text' name='q' value='{escape(query)}' placeholder='Search objects' /> "
        + "<button type='submit'>Search</button>"
        + "</form>"
        + facet_html
        + filter_banner
        + "<p class='muted'>"
        + escape(showing)
        + (f" · pack scope: {escape(requested_pack)}" if requested_pack else "")
        + "</p>"
        + f"<p class='muted'>Sort: {sort_links} · Per page: {page_size_links}</p>"
        + f"<p class='muted'>{pager}</p>"
        + f"<section class='card'><ul class='list-tight'>{items}</ul></section>"
        + f"<p class='muted'>{pager}</p>"
    )
    return _layout("Objects", body, requested_pack=requested_pack)



def _render_object_page(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    research_shell_enabled = bool(
        payload.get("research_shell_enabled", _shell_supports_research_nav(requested_pack))
    )
    next_path = _shell_href(
        f"/object?id={quote(str(payload['object']['object_id']), safe='')}", requested_pack
    )
    assembly_contract_card = _render_assembly_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    evergreen_path = payload["provenance"]["evergreen_path"]
    evergreen_html = (
        f'<a href="{escape(_note_href(evergreen_path, requested_pack))}">{escape(evergreen_path)}</a>'
        if evergreen_path
        else "<span class='muted'>None</span>"
    )
    canonical_path = payload["context"]["canonical_path"]
    canonical_path_html = (
        f'<a href="{escape(_note_href(canonical_path, requested_pack))}">{escape(canonical_path)}</a>'
        if canonical_path
        else "<span class='muted'>None</span>"
    )
    claims = (
        "".join(f"<li>{escape(item['claim_text'])}</li>" for item in payload["claims"])
        or "<li>None</li>"
    )
    relations = (
        "".join(
            f'<li><a href="{escape(_object_href(item["target_object_id"], item.get("target_path", ""), requested_pack=requested_pack))}">{escape(item.get("target_title", item["target_object_id"]))}</a>'
            f' <span class="muted">({escape(item["relation_type"])})</span></li>'
            for item in payload["relations"]
        )
        or "<li>None</li>"
    )
    contradictions = (
        "".join(
            f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
            for item in payload["contradictions"]
        )
        or "<li>None</li>"
    )
    stale_summary_signals = (
        "".join(
            f"<li>{escape(reason)}</li>"
            for item in payload["stale_summary_details"]
            for reason in item["reason_texts"]
        )
        or "<li class='muted'>No stale summary signals for this object.</li>"
    )
    summary_text = payload["summary"]["summary_text"] if payload["summary"] else ""
    reader_profile = payload.get("reader_profile") or {}
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
    section_nav_items = [
        item
        for item in payload["section_nav"]
        if research_shell_enabled or item["href"] != "#contradictions"
    ]
    section_nav = "".join(
        f'<a href="{escape(item["href"])}">{escape(item["label"])}</a>'
        for item in section_nav_items
    )
    contradiction_form = (
        "<form method='post' action='/ops/contradictions/resolve' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
        + "".join(
            f"<input type='hidden' name='contradiction_id' value='{escape(contradiction_id)}' />"
            for contradiction_id in payload["open_contradiction_ids"]
        )
        + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
        + "<select name='status'>"
        + "<option value='resolved_keep_positive'>resolved_keep_positive</option>"
        + "<option value='resolved_keep_negative'>resolved_keep_negative</option>"
        + "<option value='dismissed'>dismissed</option>"
        + "<option value='needs_human'>needs_human</option>"
        + "</select>"
        + "<input type='text' name='note' placeholder='Resolution note' />"
        + "<label><input type='checkbox' name='rebuild_summaries' value='1' /> rebuild summaries</label>"
        + "<button type='submit'>Resolve Open Contradictions</button>"
        + "</form>"
        if payload["open_contradiction_ids"]
        else "<p class='muted'>No open contradictions on this object.</p>"
    )
    summary_form = (
        "<form method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
        + f"<input type='hidden' name='object_id' value='{escape(payload['object']['object_id'])}' />"
        + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
        + "<button type='submit'>Rebuild This Summary</button>"
        + "</form>"
        if payload["stale_summary_details"]
        else "<p class='muted'>No stale summary action needed for this object.</p>"
    )
    hero_links = [
        f"<a href='{escape(payload['links']['topic_path'])}'>Explore topic</a>",
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

    def _obj_tile(label, value, *, warn=False):
        warn_cls = " warn" if warn else ""
        return (
            "<div class='card' style='margin:0'>"
            f"<div class='muted tiny'>{label}</div>"
            f"<div class='metric-num{warn_cls}' style='margin-top:4px'>{value}</div>"
            "</div>"
        )

    stats_cards = [
        _obj_tile("Claims", payload["claim_count"]),
        _obj_tile("Relations", payload["relation_count"]),
    ]
    if research_shell_enabled:
        stats_cards.append(
            _obj_tile(
                "Contradictions",
                payload["contradiction_count"],
                warn=int(payload["contradiction_count"]) > 0,
            )
        )
    right_sections = []
    if research_shell_enabled:
        right_sections.extend(
            [
                _render_review_context_card(payload["review_context"]),
                _render_review_history(payload["review_history"]),
                "<section class='card'><h2>Quick Maintenance</h2>"
                f"{contradiction_form}"
                f"{summary_form}"
                "</section>",
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
            ]
        )
    else:
        right_sections.append(_render_research_scope_notice(requested_pack))
    right_sections.extend(
        [
            _render_kind_profile_card(payload),
            _render_source_chain_card(payload, requested_pack=requested_pack),
            _render_source_backlink_rail(payload, requested_pack=requested_pack),
            "<section class='card'><h2>Context</h2><table class='kv'>"
            f"<tr><th>Object Kind</th><td>{escape(payload['context']['object_kind'])}</td></tr>"
            f"<tr><th>Source Slug</th><td>{escape(payload['context']['source_slug'])}</td></tr>"
            f"<tr><th>Canonical Path</th><td>{canonical_path_html}</td></tr>"
            "</table></section>",
            "<section class='card'><h2>Production Chain</h2><table class='kv'>"
            f"<tr><th>Chain Status</th><td>{escape(str(payload['production_chain'].get('chain_status') or ''))}</td></tr>"
            f"<tr><th>Missing Stages</th><td>{escape(', '.join(str(item).replace('_', ' ') for item in payload['production_chain'].get('missing_stages', [])) or 'None')}</td></tr>"
            f"<tr><th>Chain Summary</th><td>{escape(str(payload['production_chain'].get('chain_summary') or ''))}</td></tr>"
            f"<tr><th>Source Notes</th><td>{_render_named_note_links(payload['production_chain']['source_notes'], requested_pack=requested_pack)}</td></tr>"
            f"<tr><th>Evergreen Note</th><td>{evergreen_html}</td></tr>"
            f"<tr><th>Atlas / MOC Reach</th><td>{_render_named_note_links(payload['production_chain']['atlas_pages'], requested_pack=requested_pack)}</td></tr>"
            "</table></section>",
            f"<section id='relations' class='card'><h2>Relations</h2><ul class='list-tight'>{relations}</ul></section>",
        ]
    )
    if research_shell_enabled:
        right_sections.extend(
            [
                f"<section id='contradictions' class='card'><h2>Contradictions</h2><ul class='list-tight'>{contradictions}</ul></section>",
                f"<section class='card'><h2>Stale Summary Signals</h2><ul class='list-tight'>{stale_summary_signals}</ul></section>",
            ]
        )
    # Codex P2: the BL-083 context binder reads ``anchor.path`` as a
    # vault-relative file path.  Prefer the object's evergreen /
    # canonical markdown so the binder loads real anchor body;
    # fall back to standalone (no anchor) when neither path exists
    # rather than emitting a bare object_id that the binder can't
    # resolve.
    anchor_path_for_object = evergreen_path or payload["context"].get("canonical_path") or ""
    if anchor_path_for_object:
        ask_button = _render_ask_about_this_button(
            "object",
            str(anchor_path_for_object),
            title=str(reader_profile.get("headline") or payload["object"]["title"]),
            requested_pack=requested_pack,
        )
    else:
        ask_button = _render_ask_about_this_button(
            "standalone",
            "",
            requested_pack=requested_pack,
        )
    return _layout(
        f"Object: {payload['object']['title']}",
        (
            f"<div style='display:flex;gap:6px;flex-wrap:wrap;margin:0 0 4px'><span class=\"pill\">{escape(str(reader_profile.get('kind_label') or payload['context']['object_kind']))}</span></div>"
            f"<h1 style='margin:4px 0 6px'>{escape(str(reader_profile.get('headline') or payload['object']['title']))}</h1>"
            f"<p style='max-width:60ch'>{escape(str(reader_profile.get('dek') or summary_text or 'No compiled summary yet.'))}</p>"
            f"<p class='muted'>{escape(str(reader_profile.get('supporting_line') or payload['object']['object_id']))}"
            + (f" Pack scope: {escape(requested_pack)}." if requested_pack else "")
            + "</p>"
            + f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{''.join(hero_links)}{ask_button}</div>"
            + _render_compiled_sections(lead_sections)
            + operator_rail_card
            + assembly_contract_card
            + f"<nav class='subnav'>{section_nav}</nav>"
            + f"<section class='grid stats'>{''.join(stats_cards)}</section>"
            "<section class='grid two-col'>"
            "<div style='display:grid;gap:1rem'>"
            f"<section id='summary' class='card'><h2>Compiled Summary</h2><p>{escape(summary_text)}</p></section>"
            f"{_render_compiled_sections(remaining_sections)}"
            f"<section id='claims' class='card'><h2>Claims</h2><ul class='list-tight'>{claims}</ul></section>"
            "</div>"
            "<div style='display:grid;gap:1rem'>"
            f"{''.join(right_sections)}"
            "</div>"
            "</section>"
        ),
        requested_pack=requested_pack,
    )



def _render_briefing_page(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    next_path = "/ops/briefing" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    surface_contract_card = _render_surface_contract_card(payload)
    assembly_contract_card = _render_assembly_contract_card(payload)
    governance_contract_card = _render_governance_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    first_useful_sign = payload.get("first_useful_sign")
    first_useful_sign_html = (
        "<li>"
        + f"<span class='pill'>{escape(str(first_useful_sign['kind']))}</span> "
        + f'<a href="{escape(str(first_useful_sign["path"]))}">{escape(str(first_useful_sign["title"]))}</a>'
        + f"<div class='muted'>{escape(str(first_useful_sign['detail']))}</div>"
        + (
            f"<div class='muted'>Sources: {escape(', '.join(first_useful_sign.get('source_paths', [])))}</div>"
            if first_useful_sign.get("source_paths")
            else ""
        )
        + "</li>"
        if first_useful_sign
        else "<li class='muted'>No useful sign surfaced yet.</li>"
    )
    insights = (
        "".join(
            "<li>"
            + f"<span class='pill'>{escape(str(item['link_type']))}</span> "
            + f'<a href="{escape(str(item["path"]))}">{escape(str(item["title"]))}</a>'
            + f"<div class='muted'>{escape(str(item['detail']))}</div>"
            + (
                f"<div class='muted'>Sources: {escape(', '.join(item.get('source_paths', [])))}</div>"
                if item.get("source_paths")
                else ""
            )
            + "</li>"
            for item in payload["insights"]
        )
        or "<li class='muted'>No evolution insights surfaced.</li>"
    )
    priority_items = (
        "".join(
            "<li>"
            + f"<span class='pill'>{escape(str(item['kind']))}</span> "
            + f'<a href="{escape(str(item["path"]))}">{escape(str(item["title"]))}</a>'
            + f"<div class='muted'>{escape(str(item['detail']))}</div>"
            + (
                "<div class='muted'>Recommended Action: "
                + f'<a href="{escape(str(item["recommended_action"]["path"]))}">{escape(str(item["recommended_action"]["label"]))}</a>'
                + (
                    f" <span class='pill'>{escape(str(item['recommended_action']['queue_status']))}</span>"
                    if item["recommended_action"].get("queue_status")
                    else (
                        " <span class='pill'>executable</span>"
                        if item["recommended_action"].get("executable")
                        else " <span class='pill'>manual</span>"
                    )
                )
                + (
                    f"<div class='muted'>Resolver: {escape(str(item['recommended_action']['resolution_kind']))}</div>"
                    if item["recommended_action"].get("resolution_kind")
                    else ""
                )
                + (
                    f"<div class='muted'>Dispatch: {escape(str(item['recommended_action']['dispatch_mode']))}</div>"
                    if item["recommended_action"].get("dispatch_mode")
                    else ""
                )
                + (
                    f"<div class='muted'>Rule: {escape(str(item['recommended_action']['resolver_rule_name']))}</div>"
                    if item["recommended_action"].get("resolver_rule_name")
                    else ""
                )
                + (
                    f"<div class='muted'>Governance contract: {escape(str(item['recommended_action']['governance_provider_name']))} · {escape(str(item['recommended_action']['governance_provider_pack']))}</div>"
                    if item["recommended_action"].get("governance_provider_name")
                    or item["recommended_action"].get("governance_provider_pack")
                    else ""
                )
                + (
                    "<div class='muted'>Governance: safe</div>"
                    if item["recommended_action"].get("safe_to_run")
                    else ""
                )
                + "</div>"
                if item.get("recommended_action")
                else ""
            )
            + (
                "<form method='post' action='/ops/actions/enqueue' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                + f"<input type='hidden' name='signal_id' value='{escape(str(item['signal_id']))}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Queue action</button>"
                + "</form>"
                if item.get("signal_id")
                and item.get("recommended_action")
                and not item["recommended_action"].get("queue_status")
                else ""
            )
            + "</li>"
            for item in payload["priority_items"]
        )
        or "<li class='muted'>No priority items surfaced.</li>"
    )
    recent_signals = (
        "".join(
            f'<li><span class="pill">{escape(item["signal_type"])}</span> '
            f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div></li>"
            for item in payload["recent_signals"]
        )
        or "<li class='muted'>No recent signals.</li>"
    )
    unresolved = (
        "".join(
            f'<li><span class="pill">{escape(item["signal_type"])}</span> '
            f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a></li>'
            for item in payload["unresolved_issues"]
        )
        or "<li class='muted'>No unresolved issues.</li>"
    )
    changed_objects = (
        "".join(
            f'<li><a href="{escape(item["path"])}">{escape(item["title"])}</a></li>'
            for item in payload["changed_objects"]
        )
        or "<li class='muted'>No recent changed objects.</li>"
    )
    active_topics = (
        "".join(
            f'<li><a href="{escape(item["path"])}">{escape(item["title"])}</a> '
            f"<span class='muted'>({item['signal_count']} signals)</span></li>"
            for item in payload["active_topics"]
        )
        or "<li class='muted'>No active topics surfaced.</li>"
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    queue_summary = payload.get("queue_summary")
    if not isinstance(queue_summary, dict):
        queue_summary = {}
    loop_summary = payload.get("loop_summary")
    if not isinstance(loop_summary, dict):
        loop_summary = {}
    first_useful_sign_check = payload.get("first_useful_sign_check")
    if not isinstance(first_useful_sign_check, dict):
        first_useful_sign_check = {}
    background_policy = payload.get("background_policy")
    if not isinstance(background_policy, dict):
        background_policy = {}
    failure_bucket_values = queue_summary.get("failure_buckets")
    if not isinstance(failure_bucket_values, dict):
        failure_bucket_values = {}
    signal_type_decisions = background_policy.get("signal_type_decisions")
    if not isinstance(signal_type_decisions, dict):
        signal_type_decisions = {}
    auto_queue_enabled_signal_types = background_policy.get("auto_queue_enabled_signal_types")
    if not isinstance(auto_queue_enabled_signal_types, list):
        auto_queue_enabled_signal_types = []
    review_only_signal_types = background_policy.get("review_only_signal_types")
    if not isinstance(review_only_signal_types, list):
        review_only_signal_types = []

    def _safe_count(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    loop_blocked_count = _safe_count(loop_summary.get("failed_count")) + _safe_count(
        loop_summary.get("stalled_count")
    )
    skipped_signal_count = _safe_count(background_policy.get("skipped_signal_count"))
    failure_buckets = (
        "".join(
            f"<li><span class='pill'>{escape(str(bucket))}</span> " f"{_safe_count(count)}</li>"
            for bucket, count in failure_bucket_values.items()
        )
        or "<li class='muted'>No failed actions.</li>"
    )
    policy_decisions = (
        "".join(
            "<li>"
            f"<span class='pill'>{escape(str(signal_type))}</span> "
            f"{escape(str(decision.get('decision') or ''))}"
            f"<div class='muted'>Active: {_safe_count(decision.get('active_signal_count'))} · "
            f"Queued: {_safe_count(decision.get('queued_action_count'))} · "
            f"Skipped: {_safe_count(decision.get('skipped_count'))}</div>"
            "</li>"
            for signal_type, decision in signal_type_decisions.items()
            if isinstance(decision, dict)
        )
        or "<li class='muted'>No governed signal policy decisions are active.</li>"
    )
    return _layout(
        "Working Memory Snapshot",
        "".join(
            [
                "<h1>Orientation Brief</h1>",
                f"<p class='muted'>Generated at {_ts(payload['generated_at'])}. "
                f"{_safe_count(payload.get('recent_signal_count'))} recent signals, "
                f"{_safe_count(payload.get('unresolved_issue_count'))} unresolved issues.",
                (
                    " "
                    + f"Loop: {_safe_count(loop_summary.get('productive_count'))} productive, "
                    + f"{_safe_count(loop_summary.get('waiting_count'))} waiting, "
                    + f"{loop_blocked_count} blocked."
                ),
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                surface_contract_card,
                assembly_contract_card,
                governance_contract_card,
                f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "",
                _render_compiled_sections(remaining_sections),
                f"<section class='card'><h2>First Useful Sign</h2><ul class='list-tight'>{first_useful_sign_html}</ul></section>",
                "<section class='card'><h2>Value Proof</h2>"
                f"<p class='muted'>{escape(str(first_useful_sign_check.get('reason') or 'No value proof yet.'))}</p>"
                "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                f"<span class='pill'>Status: {escape(str(first_useful_sign_check.get('status') or 'empty'))}</span>"
                f"<span class='pill'>Evidence: {escape(str(first_useful_sign_check.get('evidence_count') or 0))}</span>"
                f"<span class='pill'>Actionability: {escape(str(first_useful_sign_check.get('actionability') or 'review'))}</span>"
                "</div></section>",
                "<section class='card'><h2>Background Policy</h2>"
                "<p class='muted'>Auto-queue enabled: "
                + escape(
                    ", ".join(
                        str(item)
                        for item in auto_queue_enabled_signal_types
                        if str(item or "").strip()
                    )
                    or "none"
                )
                + ". Review-only: "
                + escape(
                    ", ".join(
                        str(item) for item in review_only_signal_types if str(item or "").strip()
                    )
                    or "none"
                )
                + ".</p>"
                f"<p class='muted'>Skipped: {skipped_signal_count}</p>"
                f"<ul class='list-tight'>{policy_decisions}</ul></section>",
                f"<section class='card'><h2>Insights</h2><ul class='list-tight'>{insights}</ul></section>",
                f"<section class='card'><h2>Priority Items</h2><ul class='list-tight'>{priority_items}</ul></section>",
                "<section class='card'><h2>Execution Surface</h2>",
                f"<p class='muted'>{_safe_count(queue_summary.get('queued_count'))} queued, ",
                f"{_safe_count(queue_summary.get('safe_queued_count'))} safe to auto-run, ",
                f"{_safe_count(queue_summary.get('running_count'))} running, ",
                f"{_safe_count(queue_summary.get('failed_count'))} failed.</p>",
                "<form method='post' action='/ops/actions/run-batch' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                "<input type='hidden' name='limit' value='5' />",
                "<input type='hidden' name='safe_only' value='1' />",
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run 5 safe queued actions</button>",
                "</form>",
                f"<ul class='list-tight'>{failure_buckets}</ul></section>",
                f"<section class='card'><h2>Recent Signals</h2><ul class='list-tight'>{recent_signals}</ul></section>",
                f"<section class='card'><h2>Unresolved Issues</h2><ul class='list-tight'>{unresolved}</ul></section>",
                f"<section class='card'><h2>Changed Objects</h2><ul class='list-tight'>{changed_objects}</ul></section>",
                f"<section class='card'><h2>Active Topics</h2><ul class='list-tight'>{active_topics}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )



def _event_matches_object(event: dict, object_id: str) -> bool:
    """Decide whether an agent-decision event belongs to ``object_id``.

    Decisions land in the log with the queried id either at the top level
    (``object_id``) or nested under ``arguments.object_id`` for graph_ops
    tools. Both shapes count as a match; everything else is filtered out so
    the timeline pane only shows decisions about the focused object.
    """
    if str(event.get("object_id") or "") == object_id:
        return True
    args = event.get("arguments") or {}
    if isinstance(args, dict) and str(args.get("object_id") or "") == object_id:
        return True
    return False


__all__ = [
    '_build_runtime_home_payload_from_query',
    '_anchor_title_for_note',
    '_read_vault_note',
    '_render_markdown_note',
    '_is_thin_note',
    '_render_thin_note_preamble',
    '_render_note_page',
    '_render_search_page',
    '_render_library_home',
    '_render_objects_index',
    '_render_object_page',
    '_render_briefing_page',
    '_event_matches_object'
]
