# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *




def _append_query_param(path: str, key: str, value: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{quote(key, safe='')}={quote(value, safe='')}"



def _asset_href(path: str) -> str:
    return f"/asset?path={quote(path, safe='')}"



def _build_objects_query_string(
    *,
    query: str = "",
    object_kind: str = "",
    requested_pack: str = "",
) -> str:
    """Shared query-string builder for ``/ops/objects`` URLs.

    Centralises the q + kind + pack ordering so the type-facet chip
    rail and the active-filter clear-link can't drift apart on URL
    shape (e.g. param order, encoding) — both call sites used to
    duplicate this logic with subtly different rules.
    """
    params: list[str] = []
    if query:
        params.append(f"q={quote(query, safe='')}")
    if object_kind:
        params.append(f"kind={quote(object_kind, safe='')}")
    if requested_pack:
        params.append(f"pack={quote(requested_pack, safe='')}")
    return ("?" + "&".join(params)) if params else ""



def _build_open_questions_payload(vault_dir: Path) -> dict:
    """Phase 36 — read ``60-Logs/open-questions.jsonl`` for the UI panel.

    Stays read-only; never mutates the log. Returns the most recent 100
    entries reverse-chronologically so the panel shows fresh items first.
    Uses a bounded deque so the file is streamed line-by-line and only the
    tail is retained in memory regardless of log size.
    """
    import json as _json
    from collections import deque

    log = vault_dir / "60-Logs" / "open-questions.jsonl"
    if not log.exists():
        return {"questions": []}
    tail: deque[dict] = deque(maxlen=100)
    with log.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                tail.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
    return {"questions": list(reversed(tail))}



def _build_writing_prompts_payload(vault_dir: Path) -> dict:
    """Phase 36 — read ``00-Polaris/Writing-Prompts.md`` body for the UI panel.

    The file is append-only (router invariant), so we just stream its current
    contents. Returns plain markdown — the page renderer wraps it.
    """
    target = vault_dir / "00-Polaris" / "Writing-Prompts.md"
    if not target.exists():
        return {"body": ""}
    return {"body": target.read_text(encoding="utf-8")}



def _current_request_path() -> str:
    return getattr(_request_ctx, "path", "")



def _format_event_date_filter_summary(from_date: str, to_date: str) -> str:
    """Render the ``Date filter: ...`` segment for ``/ops/events``.

    The pre-fix string had a bug when only ``to_date`` was set: it
    rendered ``Date filter:  → YYYY-MM-DD.`` (empty ``from_date``
    on the left of the arrow).  Branch on which sides are present
    and format each case explicitly.
    """
    if from_date and to_date and from_date != to_date:
        return f" Date filter: {escape(from_date)} → {escape(to_date)}."
    if from_date:
        return f" Date filter: {escape(from_date)}."
    if to_date:
        return f" Date filter: ≤ {escape(to_date)}."
    return ""



def _fragment_from_page(page_html: str) -> str:
    """Extract the body content from a ``_layout``-wrapped page.

    Phase 37 reuses every existing page renderer for the Workbench panes by
    un-wrapping the chrome instead of refactoring each renderer.

    Strategy: find the literal ``shell-body`` opener, then walk forward
    through the body counting balanced ``<div ...>`` / ``</div>`` to locate
    the matching close. This is whitespace-insensitive and tolerates inner
    ``<div>`` tags inside the body content.

    A small bridge script is appended so anchor clicks on ``/object?id=...``
    bubble up to the Workbench parent as ``select_object`` messages instead
    of navigating the iframe in isolation.

    Returns the raw body HTML. Falls back to the full page if the opening
    marker is not found (defensive — never raise).
    """
    open_idx = page_html.find(_SHELL_BODY_OPEN)
    if open_idx == -1:
        return page_html
    cursor = open_idx + len(_SHELL_BODY_OPEN)
    depth = 1
    n = len(page_html)
    while cursor < n and depth > 0:
        next_open = page_html.find("<div", cursor)
        next_close = page_html.find("</div>", cursor)
        if next_close == -1:
            return page_html
        if next_open != -1 and next_open < next_close:
            # Skip past the opening tag's `>` to avoid matching attributes.
            tag_end = page_html.find(">", next_open)
            if tag_end == -1:
                return page_html
            depth += 1
            cursor = tag_end + 1
        else:
            depth -= 1
            if depth == 0:
                body = page_html[open_idx + len(_SHELL_BODY_OPEN) : next_close].strip("\n")
                return body + _FRAGMENT_BRIDGE_SCRIPT
            cursor = next_close + len("</div>")
    return page_html



def _infer_github_repo_base(frontmatter: dict[str, object], markdown: str) -> str | None:
    candidates: list[str] = []
    for value in frontmatter.values():
        if isinstance(value, str):
            candidates.append(value)
    candidates.append(markdown)
    for candidate in candidates:
        match = _GITHUB_REPO_RE.search(candidate)
        if not match:
            continue
        owner, repo = match.groups()
        return f"https://github.com/{owner}/{repo.removesuffix('.git')}"
    return None



def _is_ops_path(path: str) -> bool:
    """Return True iff ``path`` belongs to the Maintainer shell."""
    return path == "/ops" or path.startswith("/ops/")



def _is_search_href(href: str) -> bool:
    return href.startswith("/search?q=")



def _parse_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
    fenced_match = _FENCED_FRONTMATTER_RE.match(markdown)
    if fenced_match:
        raw_frontmatter = fenced_match.group(1)
        body = markdown[fenced_match.end() :]
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except yaml.YAMLError:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}, body
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return {}, markdown
    raw_frontmatter = markdown[4:end]
    body = markdown[end + 5 :]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}, body



def _read_vault_asset(vault_dir: Path, relative_path: str) -> tuple[bytes, str]:
    candidate = (vault_dir / relative_path).resolve()
    try:
        candidate.relative_to(vault_dir.resolve())
    except ValueError as exc:
        raise ValueError("invalid asset path") from exc
    if not candidate.is_file():
        raise ValueError(f"asset not found: {relative_path}")
    return (
        candidate.read_bytes(),
        mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
    )



def _render_action_worker_card(runtime: dict[str, object] | None) -> str:
    if not isinstance(runtime, dict):
        return ""
    worker = runtime.get("action_worker") if isinstance(runtime.get("action_worker"), dict) else {}
    if not worker:
        return ""
    current_action = (
        worker.get("current_action") if isinstance(worker.get("current_action"), dict) else {}
    )
    facts = [
        f"<li>State: {escape(str(worker.get('state') or 'stopped'))}</li>",
    ]
    mode = str(worker.get("mode") or "").strip()
    if mode:
        facts.append(f"<li>Mode: {escape(mode)}</li>")
    if worker.get("safe_only"):
        facts.append("<li>Execution policy: safe-only</li>")
    pid = worker.get("pid")
    if pid:
        facts.append(f"<li>PID {escape(str(pid))}</li>")
    elapsed = str(worker.get("elapsed_summary") or "").strip()
    if elapsed:
        facts.append(f"<li>Running for: {escape(elapsed)}</li>")
    heartbeat_age = str(worker.get("heartbeat_age_summary") or "").strip()
    if heartbeat_age:
        facts.append(f"<li>Heartbeat age: {escape(heartbeat_age)}</li>")
    if current_action:
        facts.append(
            f"<li>Current action: {escape(str(current_action.get('action_id') or ''))}</li>"
        )
        facts.append(
            f"<li>Action kind: {escape(str(current_action.get('action_kind') or ''))}</li>"
        )
        signal_id = str(current_action.get("source_signal_id") or "").strip()
        if signal_id:
            facts.append(f"<li>Source signal: {escape(signal_id)}</li>")
        target_ref = str(current_action.get("target_ref") or "").strip()
        if target_ref:
            facts.append(f"<li>Target: {escape(target_ref)}</li>")
    if not bool(worker.get("active")):
        facts.append("<li>Active: no</li>")
    return (
        "<section class='card'><h2>Action Worker</h2>"
        "<p class='muted'>Focused background action execution state.</p>"
        f"<ul class='list-tight'>{''.join(facts)}</ul>"
        "</section>"
    )



def _render_assembly_contract_card(payload: dict) -> str:
    contract = payload.get("assembly_contract")
    if not isinstance(contract, dict) or not contract:
        return ""
    recipe_name = str(contract.get("recipe_name") or "")
    provider_name = str(contract.get("provider_name") or "")
    provider_pack = str(contract.get("provider_pack") or "")
    status = str(contract.get("status") or "")
    recipe_kind = str(contract.get("recipe_kind") or "")
    source_contract_kind = str(contract.get("source_contract_kind") or "")
    source_contract_name = str(contract.get("source_contract_name") or "")
    source_provider_pack = str(contract.get("source_provider_pack") or "")
    source_provider_name = str(contract.get("source_provider_name") or "")
    publish_target = str(contract.get("publish_target") or "")
    output_mode = str(contract.get("output_mode") or "")
    description = str(contract.get("description") or "")
    if status == "declared":
        detail = (
            f"This access artifact resolves as {escape(recipe_name)} "
            f"declared by {escape(provider_name)} in {escape(provider_pack)}."
        )
    elif status == "inherited":
        detail = (
            f"This access artifact resolves as {escape(recipe_name)} "
            f"inherited from {escape(provider_name)} in {escape(provider_pack)}."
        )
    else:
        detail = f"This access artifact has no provider for {escape(recipe_name)} in the current pack scope."
    facts = "".join(
        item
        for item in (
            f"<li>Recipe kind: {escape(recipe_kind)}</li>" if recipe_kind else "",
            (
                f"<li>Source contract: {escape(source_contract_kind)} · {escape(source_contract_name)}</li>"
                if source_contract_kind or source_contract_name
                else ""
            ),
            (
                f"<li>Source provider: {escape(source_provider_pack)} · {escape(source_provider_name)}</li>"
                if source_provider_pack or source_provider_name
                else ""
            ),
            (
                f"<li>Output: {escape(output_mode)} → {escape(publish_target)}</li>"
                if output_mode or publish_target
                else ""
            ),
        )
    )
    description_html = f"<p class='muted'>{escape(description)}</p>" if description else ""
    facts_html = f"<ul class='list-tight'>{facts}</ul>" if facts else ""
    return (
        f"<section class='card'><h2>Assembly Contract</h2><p class='muted'>{detail}</p>"
        f"{description_html}{facts_html}</section>"
    )



def _render_candidates_pagination(payload: dict) -> str:
    count = int(payload.get("count") or 0)
    limit = int(payload.get("limit") or DEFAULT_CANDIDATE_BROWSER_LIMIT)
    offset = int(payload.get("offset") or 0)
    if limit <= 0 or count <= limit:
        return ""

    query = str(payload.get("query") or "")
    requested_pack = str(payload.get("requested_pack") or "")

    def href(next_offset: int) -> str:
        parts = []
        if query:
            parts.append(f"q={quote(query, safe='')}")
        parts.append(f"limit={limit}")
        parts.append(f"offset={max(0, next_offset)}")
        if requested_pack:
            parts.append(f"pack={quote(requested_pack, safe='')}")
        return "/ops/queue/concepts?" + "&".join(parts)

    links = []
    if offset > 0:
        links.append(f'<a href="{escape(href(max(0, offset - limit)))}">Previous</a>')
    if offset + limit < count:
        links.append(f'<a href="{escape(href(offset + limit))}">Next</a>')
    if not links:
        return ""
    current_start = offset + 1 if count else 0
    current_end = min(count, offset + limit)
    return (
        "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
        f"<span class='muted'>Showing {current_start}-{current_end} of {count}</span>"
        + "".join(links)
        + "</div>"
    )



def _render_chat_drawer_shell() -> str:
    """Inject the closed-state anchored inquiry drawer (M22).

    Imported lazily to keep the renderer entry surface flat and
    avoid a circular import — ``_chat_drawer`` only needs
    ``html.escape`` and has no upstream Reader dependencies.
    """
    from ovp_pipeline.commands._chat_drawer import render_drawer_shell

    return render_drawer_shell()



def _render_compiled_sections(sections: list[dict[str, object]]) -> str:
    if not sections:
        return ""
    rendered_sections: list[str] = []
    for section in sections:
        label = str(section.get("label") or section.get("id") or "")
        anchor = str(section.get("anchor") or str(section.get("id") or "").replace("_", "-"))
        summary = str(section.get("summary") or "")
        items = section.get("items") or []
        item_html = (
            "".join(
                "<li>"
                + (
                    f'<a href="{escape(str(item.get("path") or ""))}">{escape(str(item.get("label") or ""))}</a>'
                    if str(item.get("path") or "")
                    else escape(str(item.get("label") or ""))
                )
                + (
                    f"<div class='muted'>{escape(str(item.get('detail') or ''))}</div>"
                    if str(item.get("detail") or "")
                    else ""
                )
                + "</li>"
                for item in items
                if isinstance(item, dict)
            )
            or "<li class='muted'>No items surfaced.</li>"
        )
        summary_html = f"<p class='muted'>{escape(summary)}</p>" if summary else ""
        rendered_sections.append(
            f"<section id='{escape(anchor)}' class='card'>"
            f"<h2>{escape(label)}</h2>"
            f"{summary_html}"
            f"<ul class='list-tight'>{item_html}</ul>"
            "</section>"
        )
    return "".join(rendered_sections)



def _render_digest_neighbour_nav(vault_dir: Path, relative_path: str) -> str:
    """Prev/next pivot for digest notes (M22 BL-093).

    Returns an empty string for any note outside
    ``40-Resources/Generated/digests/`` — the helper is cheap to
    call on every thin-note render and short-circuits there.

    CodeRabbit: a Windows-flavored relative path with backslashes
    would otherwise miss the prefix check; normalize once so the
    helper works regardless of caller-side separators.
    """
    normalized = relative_path.replace("\\", "/")
    if not normalized.startswith("40-Resources/Generated/digests/"):
        return ""
    from ovp_pipeline.commands._digests_list_page import neighbour_links

    older, newer = neighbour_links(vault_dir, normalized)
    parts: list[str] = []
    if older:
        parts.append(f"<a href='{escape(older)}'>← previous day</a>")
    # Always include the index link so a digest reader is never
    # one click further from history than the home banner.
    parts.append("<a href='/digests'>All digests</a>")
    if newer:
        parts.append(f"<a href='{escape(newer)}'>next day →</a>")
    # Inline span so the caller can lay this strip out on the same
    # row as the "Ask about this" button without forcing a separate
    # paragraph break.  Caller is responsible for vertical spacing.
    return "<span class='muted small'>" + " · ".join(parts) + "</span>"



def _render_digest_preamble(frontmatter: dict[str, object]) -> str:
    generated = escape(str(frontmatter.get("generated_at") or ""))
    pack = escape(str(frontmatter.get("pack") or ""))
    return (
        "<section class='card'>"
        "<h2 style='margin-top:0'>About this digest</h2>"
        "<p>An automated daily synthesis written by the OVP "
        "<code>DIGEST</code> handler.  It reads the top-scoring "
        "contradictions, recently-synthesised community crystals, "
        "and open questions from <code>knowledge.db</code>, then "
        "asks the LLM to write a ~200-word brief in your voice "
        "(from <code>00-Polaris/USER.md</code>).</p>"
        "<table class='kv'>"
        f"<tr><th>Generated</th><td>{generated}</td></tr>"
        f"<tr><th>Pack</th><td><code>{pack}</code></td></tr>"
        "<tr><th>Pipeline</th><td>"
        "<code>ovp-digest --enqueue-daily</code> → "
        "<code>50-Inbox/02-Tasks/DIGEST-daily.md</code> → "
        "<code>ovp-task --process-pending</code></td></tr>"
        "<tr><th>Schedule</th><td>"
        "Runs daily at 06:00 via <code>~/Library/LaunchAgents/com.ovp.digest.plist</code>"
        "</td></tr>"
        "</table>"
        "<p class='muted tiny'>Wikilinks at the bottom under "
        "<strong>Sources</strong> jump straight to the underlying "
        "crystals and evergreens.</p>"
        "</section>"
    )



def _render_digest_regenerate_button(
    requested_pack: str, *, date: str = ""
) -> str:
    """Render the "Regenerate digest now" button on ``/ops/today``.

    POSTs to ``/ops/digest/regenerate`` which enqueues + dispatches
    a ``DIGEST-daily`` task synchronously.  With the M23 input-hash
    gate (BL-095), unchanged-data clicks return the prior body
    without an LLM call — operators get a cheap "fresh" button.

    ``date`` (YYYY-MM-DD) is passed through as a hidden field for
    past-date regenerate (M23.1).  Empty → today's UTC date.
    """
    pack_input = (
        f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
        if requested_pack
        else ""
    )
    date_input = (
        f"<input type='hidden' name='date' value='{escape(date)}' />"
        if date
        else ""
    )
    label = (
        f"Regenerate digest for {escape(date)}"
        if date
        else "Regenerate today's digest"
    )
    return (
        "<form method='post' action='/ops/digest/regenerate' "
        "style='display:inline-block;margin:0.6rem 0 0.2rem 0'>"
        f"{pack_input}"
        f"{date_input}"
        f"<button type='submit' class='btn'>{label}</button>"
        " <span class='muted small'>"
        "Cheap — skips the LLM call when nothing changed since last run."
        "</span>"
        "</form>"
    )



def _render_evolution_link_type_select(selected: str) -> str:
    return (
        "<select name='link_type'>"
        + "".join(
            f"<option value='{escape(option)}' {'selected' if option == selected else ''}>{escape(option)}</option>"
            for option in _EVOLUTION_LINK_TYPES
        )
        + "</select>"
    )



def _render_explore_fragment(object_id: str) -> str:
    """Phase 38 Stage C — agent-decisions SSE consumer.

    Tails ``60-Logs/agent-decisions.jsonl`` (written by graph_ops calls
    invoked through MCP) and renders one frame per decision. Mirrors the
    Pulse fragment so the look-and-feel is consistent across SSE panes.
    """
    object_qs = quote(object_id, safe="")
    # Reuses the .live-feed kit-style extension; .tall removes the
    # max-height cap so it fills the explore right pane.
    return (
        "<section class='live-feed tall'>"
        "<ul id='agent-feed'><li class='empty' style='color:var(--muted);font-style:italic;padding:.4rem'>Waiting for agent decisions…</li></ul>"
        "<script>(function(){"
        "var feed=document.getElementById('agent-feed');"
        "var empty=feed.querySelector('.empty');"
        f"var src=new EventSource('/explore/stream?object_id={object_qs}');"
        "function render(ev){"
        "if(empty){empty.remove();empty=null;}"
        "try{var obj=JSON.parse(ev.data);"
        "var li=document.createElement('li');"
        "var ts=document.createElement('span');ts.className='ts';ts.textContent=obj.ts||'';"
        "var tool=document.createElement('span');tool.className='tool';"
        "tool.textContent=obj.tool||obj.event_type||'';"
        "var body=document.createElement('span');"
        "body.textContent=JSON.stringify(obj.arguments||obj.payload||{}).slice(0,140);"
        "li.appendChild(ts);li.appendChild(tool);li.appendChild(body);"
        "feed.appendChild(li);"
        "while(feed.children.length>200){feed.removeChild(feed.firstChild);}"
        "feed.scrollTop=feed.scrollHeight;"
        "}catch(e){/* swallow */}}"
        "src.onmessage=render;"
        "src.addEventListener('agent_decision',render);"
        "src.onerror=function(){src.close();};"
        "})();</script>"
        "</section>"
    )



def _render_fragment_shell(title: str, fragment: str) -> str:
    """Minimal token-driven shell for fragment-only standalone pages.

    Used by ``/reuse``, ``/open-questions``, ``/writing-prompts`` —
    pages that render a single section into a centered card without
    the full ``_layout()`` nav chrome.  Loads the same three
    stylesheets as ``_layout()`` so light/dark + IBM Plex apply.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en" data-theme="light">\n'
        "<head>\n"
        "<meta charset='utf-8' />\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1' />\n"
        f"<title>{escape(title)}</title>\n"
        '<link rel="icon" type="image/svg+xml" href="/static/monogram.svg" />\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com" />\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&"
        'display=swap" />\n'
        '<link rel="stylesheet" href="/static/ovp-tokens.css" />\n'
        '<link rel="stylesheet" href="/static/ovp-ui.css" />\n'
        '<link rel="stylesheet" href="/static/ovp-pages.css" />\n'
        "<style>main.page { max-width: 880px; padding-top: 2rem; }</style>\n"
        "<script>(function(){try{var s=localStorage.getItem('ovp-theme');"
        "if(s==='light'||s==='dark')document.documentElement.dataset.theme=s;}"
        "catch(e){}})();</script>\n"
        "</head>\n<body>\n"
        '<main class="page">\n<div class="shell"><div class="shell-body">\n'
        f'<h1 style="margin-top:0">{escape(title)}</h1>\n'
        f"{fragment}\n"
        "</div></div>\n</main>\n</body>\n</html>"
    )



def _render_frontmatter(frontmatter: dict[str, object]) -> str:
    def render_value(value: object) -> str:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return f'<a href="{escape(value)}" target="_blank" rel="noopener noreferrer">{escape(value)}</a>'
        if isinstance(value, (list, dict)):
            return escape(json.dumps(value, ensure_ascii=False))
        return escape(str(value))

    if not frontmatter:
        return ""
    rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{render_value(value)}</td></tr>"
        for key, value in frontmatter.items()
    )
    return (
        f"<section class='card'><h2>Frontmatter</h2><table><tbody>{rows}</tbody></table></section>"
    )



def _render_frontmatter_details(frontmatter_html: str) -> str:
    """Wrap rendered frontmatter HTML in a collapsed ``<details>``
    disclosure, or return ``""`` when the file has no frontmatter
    so the page doesn't show an empty expandable block (rev-bot
    208 round-2 #4)."""
    if not frontmatter_html or not frontmatter_html.strip():
        return ""
    return (
        "<details class='page-help'>"
        "<summary>Frontmatter</summary>"
        f"{frontmatter_html}"
        "</details>"
    )



def _render_generated_preamble(relative_path: str, frontmatter: dict[str, object]) -> str:
    return (
        "<section class='card'>"
        "<h2 style='margin-top:0'>About this generated artifact</h2>"
        "<p>Produced by a QUEUE task handler "
        "(<code>RESEARCH</code> / <code>SYNTHESIZE</code> / "
        "<code>CONTRADICT</code>) from a "
        "<code>50-Inbox/02-Tasks/</code> file.</p>"
        "<p class='muted tiny'>To regenerate, drop a new "
        "<code>&lt;PREFIX&gt;-&lt;slug&gt;.md</code> into "
        "<code>50-Inbox/02-Tasks/</code> and run "
        "<code>ovp-task --process-pending</code>.</p>"
        "</section>"
    )



def _render_governance_contract_card(payload: dict) -> str:
    contract = payload.get("governance_contract")
    if not isinstance(contract, dict) or not contract:
        return ""
    provider_name = str(contract.get("provider_name") or "")
    provider_pack = str(contract.get("provider_pack") or "")
    status = str(contract.get("status") or "")
    description = str(contract.get("description") or "")
    review_queue_names = [str(item) for item in contract.get("review_queue_names", []) if str(item)]
    signal_rule_names = [str(item) for item in contract.get("signal_rule_names", []) if str(item)]
    resolver_rule_names = [
        str(item) for item in contract.get("resolver_rule_names", []) if str(item)
    ]
    if status == "declared":
        detail = f"This governance contract is declared by {escape(provider_name)} in {escape(provider_pack)}."
    elif status == "inherited":
        detail = f"This governance contract is inherited from {escape(provider_name)} in {escape(provider_pack)}."
    else:
        detail = "This runtime surface has no governance contract in the current pack scope."
    facts = "".join(
        item
        for item in (
            (
                f"<li>Review queues: {int(contract.get('review_queue_count') or 0)}"
                + (f" · {escape(', '.join(review_queue_names[:4]))}" if review_queue_names else "")
                + "</li>"
            ),
            (
                f"<li>Signal rules: {int(contract.get('signal_rule_count') or 0)}"
                + (f" · {escape(', '.join(signal_rule_names[:4]))}" if signal_rule_names else "")
                + "</li>"
            ),
            (
                f"<li>Resolver rules: {int(contract.get('resolver_rule_count') or 0)}"
                + (
                    f" · {escape(', '.join(resolver_rule_names[:4]))}"
                    if resolver_rule_names
                    else ""
                )
                + "</li>"
            ),
        )
    )
    description_html = f"<p class='muted'>{escape(description)}</p>" if description else ""
    facts_html = f"<ul class='list-tight'>{facts}</ul>" if facts else ""
    return (
        f"<section class='card'><h2>Governance Contract</h2><p class='muted'>{detail}</p>"
        f"{description_html}{facts_html}</section>"
    )



def _render_kind_profile_card(payload: dict) -> str:
    profile = payload.get("kind_profile") or {}
    prompts = profile.get("reading_prompts") or []
    prompt_html = (
        "".join(
            "<li>"
            f"<strong>{escape(str(item.get('label') or 'Prompt'))}</strong>"
            f"<p class='muted'>{escape(str(item.get('detail') or ''))}</p>"
            "</li>"
            for item in prompts
            if isinstance(item, dict)
        )
        or "<li class='muted'>Start with the summary, then verify against sources.</li>"
    )
    return (
        "<section class='card'><h2>"
        f"{escape(str(profile.get('title') or 'Object Brief'))}"
        "</h2>"
        f"<p>{escape(str(profile.get('primary_question') or 'What should I understand here?'))}</p>"
        f"<ul class='list-tight'>{prompt_html}</ul>"
        "</section>"
    )



def _render_limited_inline_links(
    items,
    render_link,
    *,
    limit: int = _INLINE_MEMBER_LINK_LIMIT,
) -> str:
    visible = items[:limit]
    hidden_count = max(0, len(items) - len(visible))
    links = ", ".join(render_link(item) for item in visible)
    if hidden_count:
        links += f" <span class='muted'>+{hidden_count} more</span>"
    return links



def _render_lineage_card(
    lineage: dict | None,
    *,
    requested_pack: str = "",
) -> str:
    """Render the BL-058 raw-source ↔ evergreens ↔ crystals chain.

    Returns ``""`` when ``lineage`` is ``None`` (note isn't an
    evergreen or 03-Processed source) so the surrounding template
    can interpolate it without a conditional.

    Visual model — single vertical card with three or four arrows
    depending on direction:

      Raw source  →  N evergreens  →  M clusters  →  K crystals

    The arrow blocks each link to a real surface so the operator
    can drill down without copy-pasting paths.
    """
    if not lineage:
        return ""

    raw = lineage.get("raw_source")
    evergreens = lineage.get("evergreens") or []
    clusters = lineage.get("clusters") or []
    crystals = lineage.get("crystals") or []
    kind = str(lineage.get("kind") or "")

    # Top "current node" indicator — different copy depending on
    # whether the user is looking at the source or one of the
    # downstream evergreens.
    if kind == "raw_source":
        header = "<strong>You are here:</strong> raw source intake"
    else:
        header = "<strong>You are here:</strong> evergreen"

    blocks: list[str] = [
        "<section class='card'><h2>Lineage</h2>",
        _LINEAGE_CARD_STYLE,
        "<div style='display:flex;flex-direction:column;gap:.4rem'>",
        "<div style='padding:.5rem .7rem;border-left:3px solid var(--accent);"
        f"background:var(--accent-soft);border-radius:0 4px 4px 0'>{header}</div>",
    ]

    # ── Raw source row ─────────────────────────────────────────
    if raw:
        path = escape(str(raw.get("path") or ""))
        slug = escape(str(raw.get("slug") or ""))
        href = str(raw.get("note_href") or "")
        link = f"<a href='{escape(href)}'>{slug}</a>" if href else slug
        archived_note = (
            "" if path else " <span class='muted'>(archived — only stem available)</span>"
        )
        blocks.append(
            "<div class='muted tiny' style='text-align:center;padding:.1rem 0'>"
            "↑ derived from</div>"
        )
        blocks.append(
            "<div style='padding:.5rem .7rem;border-left:3px solid var(--border-strong);background:var(--surface-2);border-radius:0 4px 4px 0'>"
            "<h3>Raw source</h3>"
            f"<div>{link}{archived_note}</div>"
            f"<div class='muted'>{path}</div>"
            "</div>"
        )

    # ── Evergreens row ─────────────────────────────────────────
    if evergreens:
        items = "".join(
            "<li><a href='{href}'>{title}</a> "
            "<span class='muted'><code>{slug}</code></span></li>".format(
                href=escape(str(eg.get("note_href", ""))),
                title=escape(str(eg.get("title", "(untitled)"))),
                slug=escape(str(eg.get("slug", ""))),
            )
            for eg in evergreens
        )
        blocks.append(
            "<div class='muted tiny' style='text-align:center;padding:.1rem 0'>"
            f"↓ produced {len(evergreens)} evergreen(s)"
            "</div>"
            "<div style='padding:.5rem .7rem;border-left:3px solid var(--border-strong);background:var(--surface-2);border-radius:0 4px 4px 0'>"
            "<h3>Evergreens</h3>"
            f"<ul>{items}</ul>"
            "</div>"
        )

    # ── Clusters row ───────────────────────────────────────────
    if clusters:
        items = "".join(
            "<li><a href='{href}'>{label}</a> "
            "<span class='muted'>{n} members</span></li>".format(
                href=escape(str(cl.get("crystal_note_href", "") or cl.get("cluster_href", ""))),
                label=escape(str(cl.get("label", "(untitled)"))),
                n=int(cl.get("member_count", 0)),
            )
            for cl in clusters
        )
        blocks.append(
            "<div class='muted tiny' style='text-align:center;padding:.1rem 0'>"
            f"↓ grouped into {len(clusters)} cluster(s)"
            "</div>"
            "<div style='padding:.5rem .7rem;border-left:3px solid var(--border-strong);background:var(--surface-2);border-radius:0 4px 4px 0'>"
            "<h3>Clusters (Louvain communities)</h3>"
            f"<ul>{items}</ul>"
            "</div>"
        )

    # ── Crystals row ───────────────────────────────────────────
    if crystals:
        items = "".join(
            "<li><a href='{href}'>{label}</a> "
            "<span class='muted'>[{kind}]</span></li>".format(
                href=escape(str(cr.get("note_href", ""))),
                label=escape(str(cr.get("label", "(untitled)"))),
                kind=escape(str(cr.get("kind", ""))),
            )
            for cr in crystals
        )
        blocks.append(
            "<div class='muted tiny' style='text-align:center;padding:.1rem 0'>"
            f"↓ synthesized into {len(crystals)} crystal(s)"
            "</div>"
            "<div style='padding:.5rem .7rem;border-left:3px solid var(--border-strong);background:var(--surface-2);border-radius:0 4px 4px 0'>"
            "<h3>Crystals</h3>"
            f"<ul>{items}</ul>"
            "</div>"
        )

    # If only the "you are here" row exists (no upstream / downstream
    # links) tell the user that explicitly so the empty card doesn't
    # look broken.
    has_chain = bool(raw or evergreens or clusters or crystals)
    if not has_chain:
        blocks.append(
            "<div class='lineage-row muted'>"
            "<em>No lineage links found yet — re-run "
            "<code>ovp-knowledge-index</code> after absorb / synthesis.</em>"
            "</div>"
        )

    blocks.append("</div></section>")
    return "".join(blocks)



def _render_open_questions_fragment(payload: dict) -> str:
    rows = payload.get("questions") or []
    if not rows:
        return "<section class='open-questions'><p class='muted'><em>No open questions yet.</em></p></section>"
    items = "".join(
        f"<li><strong>{escape(str(row.get('question') or ''))}</strong>"
        f" <small>{escape(str(row.get('ts') or ''))}</small></li>"
        for row in rows
    )
    return f"<section class='open-questions'><ul>{items}</ul></section>"



def _render_page_help(
    title: str,
    *,
    what: str,
    can: str,
    effect: str,
) -> str:
    """Three-line maintainer help banner used on every /ops/* surface.

    Each ops page answers three questions: what is this surface, what
    can the operator do here, and what changes when they click a
    button.  Until BL-053 Phase 2 the operator had to learn that by
    trial and error; this helper renders a collapsed ``<details>``
    block so the answers are one click away on every page without
    eating screen space when not needed.
    """
    return (
        "<aside class='page-help'><details>"
        f"<summary>{escape(title)} — what is this?</summary>"
        "<dl>"
        f"<dt>What this is</th><td>{what}</dd>"
        f"<dt>What you can do</th><td>{can}</dd>"
        f"<dt>What happens when you click</th><td>{effect}</dd>"
        "</dl></details></aside>"
    )



def _render_pulse_fragment() -> str:
    """Phase 37 — self-contained Pulse SSE consumer.

    The fragment opens an ``EventSource`` against ``/pulse/stream`` and
    appends frames into a tight scrolling list. Designed for the Workbench
    bottom pane; works equally well as a standalone iframe.
    """
    # ``.live-feed`` is the kit-style extension primitive defined in
    # /static/ovp-pages.css for SSE event tails.
    return (
        "<section class='live-feed'>"
        "<ul id='pulse-feed'><li class='empty' style='color:var(--muted);font-style:italic;padding:.4rem'>Waiting for events…</li></ul>"
        "<script>(function(){"
        "var feed=document.getElementById('pulse-feed');"
        "var empty=feed.querySelector('.empty');"
        "var src=new EventSource('/ops/pulse/stream');"
        "function render(ev){"
        "if(empty){empty.remove();empty=null;}"
        "try{var obj=JSON.parse(ev.data);"
        "var li=document.createElement('li');"
        "var ts=document.createElement('span');ts.className='ts';ts.textContent=obj.ts||'';"
        "var et=document.createElement('span');et.className='et';et.textContent=obj.event_type||'';"
        "var pk=document.createElement('span');pk.className='pk';pk.textContent=obj.pack||'';"
        "var body=document.createElement('span');"
        "var keys=Object.keys(obj).filter(function(k){"
        "return k!=='ts'&&k!=='event_type'&&k!=='pack'&&k!=='event_id'&&k!=='session_id';});"
        "body.textContent=keys.slice(0,3).map(function(k){"
        "return k+'='+JSON.stringify(obj[k]).slice(0,80);}).join(' ');"
        "li.appendChild(ts);li.appendChild(et);li.appendChild(pk);li.appendChild(body);"
        "feed.appendChild(li);"
        "while(feed.children.length>200){feed.removeChild(feed.firstChild);}"
        "feed.scrollTop=feed.scrollHeight;"
        "}catch(e){/* swallow */}}"
        # Server emits named SSE frames (`event: <event_type>`) — onmessage
        # only fires for default-named frames, so subscribe to every event_type
        # in our closed vocabulary plus a generic 'message' fallback.
        "var TYPES=['trusted_reuse_event','promotion','relation_promoted',"
        "'evidence_reverified','evidence_verified','zone_violation','feedback_yield'];"
        "TYPES.forEach(function(t){src.addEventListener(t,render);});"
        "src.onmessage=render;"
        "src.onerror=function(){src.close();};"
        "})();</script>"
        "</section>"
    )



def _render_research_scope_notice(requested_pack: str = "") -> str:
    pack_label = f" for pack '{requested_pack}'" if requested_pack else ""
    return (
        "<section class='card'><h2>Research Review</h2>"
        f"<p class='muted'>Research-specific review surfaces stay hidden{escape(pack_label)}. "
        "This page still shows shared object/topic context, but contradiction, summary, evolution, and related research affordances only appear when the current pack declares those semantics.</p>"
        "</section>"
    )



def _render_reuse_report_fragment(payload: dict) -> str:
    """Self-contained HTML fragment summarising reuse events (Phase 32).

    Plain table markup, no ``<html>`` wrapper — designed so the Phase 37
    Workbench can iframe or fetch this directly without re-parsing UI chrome.
    """
    pack = escape(str(payload.get("pack") or ""))
    weekly_rows = payload.get("weekly") or []
    never_reused = payload.get("never_reused_after_30_days") or []
    window_days = int(payload.get("never_reused_window_days") or 30)

    if weekly_rows:
        weekly_html = (
            "<table class='reuse-weekly'>"
            "<thead><tr><th>ISO Week</th><th>Pack</th><th>Surface</th>"
            "<th>Events</th><th>Trusted</th></tr></thead><tbody>"
            + "".join(
                f"<tr><td>{escape(str(row['iso_week']))}</td>"
                f"<td>{escape(str(row['pack']))}</td>"
                f"<td>{escape(str(row['surface']))}</td>"
                f"<td>{int(row['events'])}</td>"
                f"<td>{int(row['trusted_events'])}</td></tr>"
                for row in weekly_rows
            )
            + "</tbody></table>"
        )
    else:
        weekly_html = "<p class='muted'><em>No reuse events recorded yet.</em></p>"

    if never_reused:
        never_html = (
            f"<h3>Never reused after {window_days} days</h3>"
            "<ul class='reuse-never'>"
            + "".join(
                f"<li><code>{escape(str(item['object_id']))}</code> "
                f"— {escape(str(item.get('title') or ''))}</li>"
                for item in never_reused
            )
            + "</ul>"
        )
    else:
        never_html = ""

    return (
        f"<section class='reuse-report' data-pack='{pack}'>"
        f"<h2>Trusted reuse — pack <code>{pack}</code></h2>"
        f"{weekly_html}{never_html}"
        f"</section>"
    )



def _render_review_context_card(
    context: dict[str, object], *, title: str = "Review Context"
) -> str:
    latest_event_date = str(context.get("latest_event_date") or "")
    latest_event_html = (
        escape(latest_event_date) if latest_event_date else "<span class='muted'>None</span>"
    )
    stale_summary_ids = (
        ", ".join(str(item) for item in context.get("stale_summary_object_ids", [])) or "None"
    )
    contradiction_object_ids = (
        ", ".join(str(item) for item in context.get("contradiction_object_ids", [])) or "None"
    )
    return (
        "<section class='card'>"
        f"<h2>{escape(title)}</h2>"
        "<table class='kv'>"
        f"<tr><th>Objects in scope</th><td>{int(context.get('object_count', 0))}</td></tr>"
        f"<tr><th>Source notes</th><td>{int(context.get('source_note_count', 0))}</td></tr>"
        f"<tr><th>Atlas / MOC pages</th><td>{int(context.get('moc_count', 0))}</td></tr>"
        f"<tr><th>Open contradictions</th><td>{int(context.get('open_contradiction_count', 0))}</td></tr>"
        f"<tr><th>Total contradictions</th><td>{int(context.get('contradiction_count', 0))}</td></tr>"
        f"<tr><th>Stale summaries</th><td>{int(context.get('stale_summary_count', 0))}</td></tr>"
        f"<tr><th>Latest event date</th><td>{latest_event_html}</td></tr>"
        f"<tr><th>Contradiction objects</th><td>{escape(contradiction_object_ids)}</td></tr>"
        f"<tr><th>Stale summary objects</th><td>{escape(stale_summary_ids)}</td></tr>"
        "</table>"
        "</section>"
    )



def _render_runtime_state_card(runtime_state: dict[str, object] | None) -> str:
    if not isinstance(runtime_state, dict):
        return ""
    metrics = runtime_state.get("metrics") if isinstance(runtime_state.get("metrics"), dict) else {}
    attention = (
        runtime_state.get("attention") if isinstance(runtime_state.get("attention"), list) else []
    )
    status = str(runtime_state.get("status") or "unknown")
    facts = [
        f"<li>Open repair markers: {escape(str(metrics.get('open_projection_repair_markers', 0)))}</li>",
        f"<li>Expired repair leases: {escape(str(metrics.get('expired_projection_repair_leases', 0)))}</li>",
        f"<li>Queued actions: {escape(str(metrics.get('queued_actions', 0)))}</li>",
        f"<li>Running actions: {escape(str(metrics.get('running_actions', 0)))}</li>",
        f"<li>Stale running actions: {escape(str(metrics.get('stale_running_actions', 0)))}</li>",
        f"<li>Failed actions: {escape(str(metrics.get('failed_actions', 0)))}</li>",
        f"<li>Pipeline events: {escape(str(metrics.get('pipeline_events', 0)))}</li>",
        f"<li>Reuse surfaces: {escape(str(metrics.get('reuse_surfaces', 0)))}</li>",
    ]
    attention_items: list[str] = []
    for item in attention[:5]:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "info")
        message = str(item.get("message") or "")
        attention_items.append(
            f"<li><span class='pill'>{escape(severity)}</span> {escape(message)}</li>"
        )
    attention_html = (
        f"<ul class='list-tight'>{''.join(attention_items)}</ul>"
        if attention_items
        else "<p class='muted'>No runtime-state attention items surfaced.</p>"
    )
    return (
        "<section class='card'><h2>System Health</h2>"
        f"<p class='muted'>Runtime state: {escape(status)}. Derived from repair markers, "
        "workflow actions, pipeline events, and trusted reuse events.</p>"
        f"<ul class='list-tight'>{''.join(facts)}</ul>"
        f"{attention_html}"
        "</section>"
    )



def _render_signal_context_contract(item: dict) -> str:
    payload = item.get("payload") or {}
    brain_lookup = payload.get("brain_first_lookup") or {}
    backlink_expectation = payload.get("backlink_expectation") or {}
    parts: list[str] = []
    if brain_lookup:
        count = int(brain_lookup.get("existing_object_count") or 0)
        parts.append(
            "<div class='muted'>Brain-first lookup: "
            f"{escape(str(brain_lookup.get('decision') or 'inspect'))} · "
            f"{escape(str(brain_lookup.get('status') or 'unknown'))} · "
            f"{count} existing objects"
            "</div>"
        )
    if backlink_expectation:
        source_count = len(backlink_expectation.get("source_note_paths") or [])
        parts.append(
            "<div class='muted'>Backlinks: "
            f"{escape(str(backlink_expectation.get('status') or 'unknown'))} · "
            f"{source_count} source notes"
            "</div>"
        )
    return "".join(parts)



def _render_surface_contract_card(payload: dict) -> str:
    contract = payload.get("surface_contract")
    if not isinstance(contract, dict) or not contract:
        return ""
    provider_name = str(contract.get("provider_name") or "")
    provider_pack = str(contract.get("provider_pack") or "")
    status = str(contract.get("status") or "")
    surface_kind = str(contract.get("surface_kind") or "")
    if status == "declared":
        detail = (
            f"This shared shell surface resolves as {escape(surface_kind)} "
            f"declared by {escape(provider_name)} in {escape(provider_pack)}."
        )
    elif status == "inherited":
        detail = (
            f"This shared shell surface resolves as {escape(surface_kind)} "
            f"inherited from {escape(provider_name)} in {escape(provider_pack)}."
        )
    else:
        detail = (
            f"This shared shell surface has no provider for {escape(surface_kind)} "
            f"in the current pack scope."
        )
    title = (
        f"{surface_kind.replace('_', ' ').title()} Surface Contract"
        if surface_kind
        else "Surface Contract"
    )
    error_text = str(payload.get("surface_error") or "").strip()
    extra = f"<p class='muted'>{escape(error_text)}</p>" if error_text else ""
    return f"<section class='card'><h2>{escape(title)}</h2><p class='muted'>{detail}</p>{extra}</section>"



def _render_user_profile_preamble() -> str:
    return (
        "<section class='card'>"
        "<h2 style='margin-top:0'>About this profile</h2>"
        "<p>Your operator profile.  Read by "
        "<code>context_loader.load_llm_context</code> and prepended "
        "as a system-prompt prefix to every LLM call site that "
        "needs user-aware behaviour (extractor, crystal synthesizers, "
        "task handlers, digest).</p>"
        "<p class='muted tiny'>Edit freely.  Changes take effect on "
        "the next LLM call — no restart needed.</p>"
        "</section>"
    )



def _render_workbench_page(*, object_id: str, requested_pack: str) -> str:
    """Phase 37 — 4-pane reviewer surface composed from existing fragments.

    Layout (CSS grid):

        ┌───────────────┬──────────────────────────┬───────────────┐
        │ Candidates    │ Object body (top)        │ Actions       │
        │ (left)        │ Briefing  (bottom)       │ (right)       │
        ├───────────────┴──────────────────────────┴───────────────┤
        │ Pulse (full-width)                                       │
        └──────────────────────────────────────────────────────────┘

    Selecting an object_id is a query-string param so back/forward navigation
    just works. Iframes post a ``select_object`` message to the parent on
    candidate clicks; the parent updates child ``src`` attributes and
    rewrites ``location.search`` via ``history.replaceState``.
    """
    # Fragment URLs. Candidate / Briefing / Actions are pack-aware but do not
    # care about the object id; Object pane needs the id and is hidden when
    # none is selected (the iframe falls back to the Objects index).
    cand_src = "/ops/candidates/fragment" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    actions_src = "/ops/actions/fragment" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    briefing_src = "/ops/briefing/fragment" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    object_src = (
        f"/object/fragment?id={quote(object_id, safe='')}"
        + (f"&pack={quote(requested_pack, safe='')}" if requested_pack else "")
        if object_id
        else "/ops/objects" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    )
    pulse_src = "/ops/pulse/fragment"
    # ``</`` would close the surrounding <script> block early — escape it the
    # same way graph/visualize.py does for inline JSON-in-HTML. Precomputed
    # because Python 3.10 disallows backslashes inside f-string expressions.
    pack_json = json.dumps(requested_pack).replace("</", "<\\/")

    return (
        "<!doctype html>\n<html lang='en' data-theme='light'><head><meta charset='utf-8' />"
        "<meta name='viewport' content='width=device-width, initial-scale=1' />"
        "<title>Workbench</title>"
        "<link rel='icon' type='image/svg+xml' href='/static/monogram.svg' />"
        "<link rel='preconnect' href='https://fonts.googleapis.com' />"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin />"
        "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
        "family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&"
        "display=swap' />"
        "<link rel='stylesheet' href='/static/ovp-tokens.css' />"
        "<link rel='stylesheet' href='/static/ovp-ui.css' />"
        "<link rel='stylesheet' href='/static/ovp-pages.css' />"
        "<style>*{box-sizing:border-box}</style>"
        "<script>(function(){try{var s=localStorage.getItem('ovp-theme');"
        "if(s==='light'||s==='dark')document.documentElement.dataset.theme=s;}"
        "catch(e){}})();</script>"
        "</head><body class='fullbleed-shell'>"
        "<header>"
        "<h1>Workbench</h1>"
        f"<span class='meta'>object: <code id='wb-object'>{escape(object_id) or '∅'}</code></span>"
        f"<span class='meta'>pack: <code>{escape(requested_pack) or '∅'}</code></span>"
        "<a href='/' style='margin-left:auto'>← Shell</a>"
        "</header>"
        "<div class='fullbleed-grid workbench'>"
        f"<section class='pane cand'><iframe id='pane-cand' src='{escape(cand_src)}'></iframe></section>"
        f"<section class='pane obj'><iframe id='pane-obj' src='{escape(object_src)}'></iframe></section>"
        f"<section class='pane brief'><iframe id='pane-brief' src='{escape(briefing_src)}'></iframe></section>"
        f"<section class='pane act'><iframe id='pane-act' src='{escape(actions_src)}'></iframe></section>"
        f"<section class='pane pulse'><iframe id='pane-pulse' src='{escape(pulse_src)}'></iframe></section>"
        "</div>"
        "<script>(function(){"
        f"var pack={pack_json};"
        "function selectObject(id){"
        "var packQs=pack?'&pack='+encodeURIComponent(pack):'';"
        "var packQsLead=pack?'?pack='+encodeURIComponent(pack):'';"
        "document.getElementById('pane-obj').src=id"
        "?'/object/fragment?id='+encodeURIComponent(id)+packQs"
        ":'/ops/objects'+packQsLead;"
        "document.getElementById('wb-object').textContent=id||'∅';"
        "var url=new URL(window.location.href);"
        "if(id){url.searchParams.set('object_id',id);}else{url.searchParams.delete('object_id');}"
        "history.replaceState({},'',url.toString());"
        "}"
        "window.addEventListener('message',function(ev){"
        "var d=ev.data;if(!d||typeof d!=='object')return;"
        "if(d.type==='select_object'&&typeof d.id==='string'){selectObject(d.id);}"
        "});"
        "})();</script>"
        "</body></html>"
    )



def _render_workflow_groups(groups: list[dict[str, object]]) -> str:
    if not groups:
        return ""
    return "".join(
        "<section class='card'>"
        f"<h2>{escape(str(group.get('title') or ''))}</h2>"
        f"<p class='muted'>{escape(str(group.get('summary') or ''))}</p>"
        "<ul class='list-tight'>"
        + "".join(
            "<li>"
            + (
                f'<a href="{escape(str(item.get("path") or ""))}">{escape(str(item.get("label") or ""))}</a>'
                if item.get("path")
                else escape(str(item.get("label") or ""))
            )
            + (
                f"<div class='muted'>{escape(str(item.get('detail') or ''))}</div>"
                if item.get("detail")
                else ""
            )
            + "</li>"
            for item in (group.get("items") or [])
        )
        + "</ul>"
        "</section>"
        for group in groups
    )



def _render_writing_prompts_fragment(payload: dict) -> str:
    body = str(payload.get("body") or "").strip()
    if not body:
        return "<section class='writing-prompts'><p class='muted'><em>No writing prompts captured yet.</em></p></section>"
    return f"<section class='writing-prompts'><pre>{escape(body)}</pre></section>"



def _resolve_effective_type(frontmatter: dict[str, object]) -> str:
    """Pick the surviving "thin shell" type from a note's
    frontmatter.  Returns the canonical type string when it matches
    one of ``_THIN_NOTE_TYPES`` (or it can be inferred structurally
    from a ``live:`` block), otherwise the raw ``type:`` value.

    Precedence (highest first):

    1. ``type:`` itself — matches a thin type as-is.
    2. ``original_note_type:`` — protects against a stale
       ``note_type_normalize`` run that rewrote the type to
       ``article`` before the M19/M20 canonical-set fix (PR #207).
    3. Presence of a ``live:`` block — structural marker for the
       Live Concept primitive.

    Single source of truth for ``_is_thin_note`` *and*
    ``_render_thin_note_preamble``; see rev-bot PR #208 comment
    208.2.
    """
    type_value = str(frontmatter.get("type") or "").strip().lower()
    if type_value in _THIN_NOTE_TYPES:
        return type_value
    original_type = str(frontmatter.get("original_note_type") or "").strip().lower()
    if original_type in _THIN_NOTE_TYPES:
        return original_type
    if isinstance(frontmatter.get("live"), dict):
        return "live-concept"
    return type_value



def _safe_redirect_path(location: str, *, fallback: str = "/") -> str:
    """Validate redirect target is a safe relative path (no open redirect)."""
    if any(ord(ch) < 0x20 or ch == "\\" for ch in location):
        return fallback
    stripped = location.strip()
    if not stripped:
        return fallback
    parsed = urlparse(stripped)
    if parsed.scheme or parsed.netloc:
        return fallback
    if stripped.startswith("//"):
        return fallback
    if not stripped.startswith("/"):
        return fallback
    return stripped



def _shell_href(path: str, requested_pack: str = "") -> str:
    if not requested_pack:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}pack={quote(requested_pack, safe='')}"



def _shell_supports_research_nav(requested_pack: str = "") -> bool:
    try:
        return any(
            pack.name == PRIMARY_PACK_NAME for pack in iter_compatible_packs(requested_pack or None)
        )
    except ValueError:
        return False



def _smart_markdown_link(label: str, href: str) -> str:
    safe_label = label.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe_label}]({href})"



def _split_lead_compiled_sections(
    sections: list[dict[str, object]] | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    normalized = [section for section in (sections or []) if isinstance(section, dict)]
    if not normalized:
        return [], []
    return [normalized[0]], normalized[1:]



def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return markdown
    return markdown[end + 5 :]



def _ts(text) -> str:
    """Render a timestamp/date in the kit's mono-tiny-muted style.

    Centralises the visual treatment of every ISO date / unix
    timestamp / "generated at" line across the UI so they read as
    one design language instead of as raw text.  Pass ``None`` /
    ``""`` and you get the em-dash placeholder.

    Humanises ISO 8601 timestamps (``2026-05-10T01:08:56+00:00``)
    to ``YYYY-MM-DD HH:MM:SS`` — drops the ``T`` separator, the
    timezone suffix, and any sub-second precision.  Non-ISO input
    falls through unchanged.
    """
    if text is None or text == "":
        return "<span class='muted tiny'>—</span>"
    raw = str(text)
    rendered = raw
    if len(raw) >= 19 and raw[4] == "-" and raw[7] == "-" and raw[10] in ("T", " "):
        try:
            from datetime import datetime as _dt

            dt = _dt.fromisoformat(raw.replace("Z", "+00:00"))
            rendered = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            rendered = raw
    return f"<span class='muted tiny mono'>{escape(rendered)}</span>"



def _unsupported_route_payload(route_path: str, requested_pack: str = "") -> dict[str, str]:
    normalized_pack = requested_pack.strip()
    return {
        "status": "unsupported_pack",
        "route": route_path,
        "requested_pack": normalized_pack,
        "error": (
            f"Route '{route_path}' is not available in the shared shell for pack '{normalized_pack}'."
            if normalized_pack
            else f"Route '{route_path}' is not available in the shared shell."
        ),
    }



# BL-050: shell selection.  Reader shell renders at ``/`` and any
# top-level reader-tree path; Maintainer shell renders under ``/ops``.
# The HTTP handler stores the current request path before dispatch so
# ``_layout()`` can pick the right shell + nav without each renderer
# threading a ``shell=`` parameter.
def set_request_path(path: str) -> None:
    """Set per-request URL path (called by the HTTP handler)."""
    _request_ctx.path = path


__all__ = [
    '_append_query_param',
    '_asset_href',
    '_build_objects_query_string',
    '_build_open_questions_payload',
    '_build_writing_prompts_payload',
    '_current_request_path',
    '_format_event_date_filter_summary',
    '_fragment_from_page',
    '_infer_github_repo_base',
    '_is_ops_path',
    '_is_search_href',
    '_parse_frontmatter',
    '_read_vault_asset',
    '_render_action_worker_card',
    '_render_assembly_contract_card',
    '_render_candidates_pagination',
    '_render_chat_drawer_shell',
    '_render_compiled_sections',
    '_render_digest_neighbour_nav',
    '_render_digest_preamble',
    '_render_digest_regenerate_button',
    '_render_evolution_link_type_select',
    '_render_explore_fragment',
    '_render_fragment_shell',
    '_render_frontmatter',
    '_render_frontmatter_details',
    '_render_generated_preamble',
    '_render_governance_contract_card',
    '_render_kind_profile_card',
    '_render_limited_inline_links',
    '_render_lineage_card',
    '_render_open_questions_fragment',
    '_render_page_help',
    '_render_pulse_fragment',
    '_render_research_scope_notice',
    '_render_reuse_report_fragment',
    '_render_review_context_card',
    '_render_runtime_state_card',
    '_render_signal_context_contract',
    '_render_surface_contract_card',
    '_render_user_profile_preamble',
    '_render_workbench_page',
    '_render_workflow_groups',
    '_render_writing_prompts_fragment',
    '_resolve_effective_type',
    '_safe_redirect_path',
    '_shell_href',
    '_shell_supports_research_nav',
    '_smart_markdown_link',
    '_split_lead_compiled_sections',
    '_strip_frontmatter',
    '_ts',
    '_unsupported_route_payload',
    'set_request_path'
]
