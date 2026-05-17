# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *




def _ask_about_this_href(
    anchor_kind: str,
    anchor_ref: str,
    *,
    title: str = "",
    requested_pack: str = "",
) -> str:
    """Compose the ``/chat?anchor=<kind>:<ref>&title=<title>`` URL
    for the "Ask about this" entry buttons (BL-087).

    Reader-side only — the route is wired by BL-086.  ``title``
    rides through as a hidden field on the composer so the new
    session's frontmatter records the artifact's friendly name.
    """
    params = [f"anchor={quote(f'{anchor_kind}:{anchor_ref}', safe='')}"]
    if title:
        params.append(f"title={quote(title, safe='')}")
    return _shell_href(f"/chat?{'&'.join(params)}", requested_pack)



def _convert_box_table_fences(markdown: str, *, github_repo_base: str | None) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            fence = [line]
            index += 1
            while index < len(lines):
                fence.append(lines[index])
                if lines[index].strip().startswith("```"):
                    index += 1
                    break
                index += 1
            body = fence[1:-1]
            if (
                body
                and any("│" in row for row in body)
                and any("┌" in row or "├" in row or "└" in row for row in body)
            ):
                rows: list[tuple[str, str]] = []
                for row in body:
                    if "│" not in row:
                        continue
                    parts = [part.strip() for part in row.strip().strip("│").split("│")]
                    if len(parts) != 2:
                        continue
                    left, right = parts
                    if not left or left == "参考链接":
                        continue
                    if right.startswith(("http://", "https://")):
                        right = _smart_markdown_link(right, right)
                    elif github_repo_base and right.endswith(".md") and not right.startswith("/"):
                        right = _smart_markdown_link(right, f"{github_repo_base}/blob/main/{right}")
                    rows.append((left, right))
                if rows:
                    output.append("| 名称 | 值 |")
                    output.append("| --- | --- |")
                    for left, right in rows:
                        output.append(f"| {left} | {right} |")
                    continue
            output.extend(fence)
            continue
        output.append(line)
        index += 1
    return "\n".join(output)



def _note_href(path: str, requested_pack: str = "") -> str:
    return _shell_href(f"/note?path={quote(path, safe='')}", requested_pack)



def _object_href(object_id: str, path: str = "", requested_pack: str = "") -> str:
    if path:
        return path
    return _shell_href(f"/object?id={quote(str(object_id), safe='')}", requested_pack)



def _ops_nav_items(requested_pack: str = "") -> list[tuple[str, str]]:
    """Maintainer shell nav, BL-053 IA: workbench mode, not toolbox.

    Pre-BL-053 the nav was a flat 9-item list — every surface
    individually addressable.  Operators had to remember which URL
    answered which question.  Post-BL-053 the nav is grouped by
    operator intent:

      * **Today** (`/ops/today`) — what happened in the current day,
        five cards across the pipeline's macro-stages.
      * **Runs** (`/ops/runs`) — by-transaction pivot; click any run
        to see its full event timeline.
      * **Timeline** (`/ops/timeline`) — multi-day digest, kept for
        the long-window view that complements ``/ops/today``.
      * **Pulse / Events** — live tail + object-keyed dossier.
      * **Queue group**: Concept Candidates / Relation Proposals /
        Actions / Contradictions / Review Queue — everything waiting
        for human attention.
      * **Catalog group**: Evergreens / Signals / Clusters / Deep-
        dives — browseable surfaces.

    BL-052 vocab fixes folded in:
      - Nav label ``Audit`` → ``Events`` (path unchanged).
      - Nav label ``Candidates`` → ``Concept Candidates``.
      - Nav label ``Workbench`` no longer in nav (it's reachable via
        the Queue group's Review Queue link when ready).
    """
    items: list[tuple[str, str]] = [
        # Workbench root + by-time pivots
        ("Overview", "/ops"),
        ("Today", "/ops/today"),
        ("Runs", "/ops/runs"),
        ("Timeline", "/ops/timeline"),
        # Live + audit
        ("Pulse", "/ops/pulse"),
        ("Events", "/ops/events"),
        # Browseables / queues
        ("Evergreens", "/ops/objects"),
        # BL-053 Phase 2: ``/ops/queue`` is the single landing page
        # for the four pending-review queues; the legacy four pages
        # live under ``/ops/queue/<sub>`` and the bare ``/ops/<sub>``
        # paths 301 to the queue routes for backwards compatibility.
        ("Queue", "/ops/queue"),
    ]
    if _shell_supports_research_nav(requested_pack):
        items.extend(
            [
                ("Clusters", "/ops/clusters"),
            ]
        )
        # ``Deep-dives`` was removed post-BL-029.  The legacy
        # 13-section LLM rewrite no longer produces deep-dive
        # markdown, so the index page is permanently empty.
        # ``/ops/deep-dives`` 301s to ``/ops/today`` for any
        # existing bookmarks.
    return items



def _reader_nav_items(requested_pack: str = "") -> list[tuple[str, str]]:
    """Reader shell nav.  Strictly reading-focused — no maintainer
    routes.  ``Map`` only when the pack supports research nav."""
    items: list[tuple[str, str]] = [
        ("Library", "/"),
        ("Search", "/search"),
        ("Topics", "/topics"),
        # M21 BL-088: ``/chats`` is the inquiry history surface.
        # Without a nav entry, operators have no discoverable path
        # back from a session they just started (codex review P2).
        ("Chats", "/chats"),
        # M22 BL-093: ``/digests`` lists every daily digest so an
        # operator can step into prior days rather than only seeing
        # the latest one through the home banner.
        ("Digests", "/digests"),
    ]
    if _shell_supports_research_nav(requested_pack):
        items.append(("Map", "/map"))
    return items



def _render_candidate_items(payload: dict) -> str:
    requested_pack = str(payload.get("requested_pack") or "")
    next_path = _shell_href("/ops/queue/concepts", requested_pack)
    rendered: list[str] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        title = str(item.get("title") or slug)
        candidate_note_path = str(item.get("candidate_note_path") or "")
        suggested_action = str(item.get("suggested_action") or "keep_as_candidate")
        similar_existing = (
            item.get("similar_existing") if isinstance(item.get("similar_existing"), list) else []
        )
        first_similar = similar_existing[0] if similar_existing else {}
        default_target = ""
        if isinstance(first_similar, dict):
            try:
                first_score = float(first_similar.get("score", 0.0))
            except (TypeError, ValueError):
                first_score = 0.0
            if first_score >= _CANDIDATE_MERGE_AUTOFILL_THRESHOLD:
                default_target = str(first_similar.get("slug") or "")
        similar_html = (
            "".join(
                "<li>"
                f"<a href='{escape(str(similar.get('path') or ''))}'>{escape(str(similar.get('title') or similar.get('slug') or ''))}</a> "
                f"<span class='pill'>{escape(str(similar['score']) if 'score' in similar else '')}</span>"
                "</li>"
                for similar in similar_existing[:5]
                if isinstance(similar, dict)
            )
            or "<li class='muted'>No strong active concept matches.</li>"
        )
        pack_hidden = (
            f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
            if requested_pack
            else ""
        )
        title_html = (
            f"<a href='{escape(candidate_note_path)}'>{escape(title)}</a>"
            if candidate_note_path
            else escape(title)
        )
        rendered.append(
            "<li>"
            f"<h3>{title_html} <span class='pill'>{escape(slug)}</span></h3>"
            f"<div class='muted'>Suggested: {escape(suggested_action)} · "
            f"sources {escape(str(item.get('source_count') or 0))} · "
            f"evidence {escape(str(item.get('evidence_count') or 0))}</div>"
            f"<p>{escape(str(item.get('definition') or ''))}</p>"
            "<div class='muted'>Similar active concepts</div>"
            f"<ul class='list-tight'>{similar_html}</ul>"
            "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
            "<form method='post' action='/ops/candidates/review' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
            f"{pack_hidden}"
            f"<input type='hidden' name='slug' value='{escape(slug)}' />"
            "<input type='hidden' name='action' value='promote' />"
            f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            "<button type='submit'>Promote</button>"
            "</form>"
            "<form method='post' action='/ops/candidates/review' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
            f"{pack_hidden}"
            f"<input type='hidden' name='slug' value='{escape(slug)}' />"
            "<input type='hidden' name='action' value='merge' />"
            f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            f"<input type='text' name='target_slug' value='{escape(default_target)}' placeholder='target slug' />"
            "<button type='submit'>Merge</button>"
            "</form>"
            "<form method='post' action='/ops/candidates/review' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
            f"{pack_hidden}"
            f"<input type='hidden' name='slug' value='{escape(slug)}' />"
            "<input type='hidden' name='action' value='reject' />"
            f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            "<button type='submit'>Reject</button>"
            "</form>"
            "</div>"
            "</li>"
        )
    if not rendered:
        return "<p class='muted'>No candidate concepts match the current filter.</p>"
    return f"<ul class='list-tight'>{''.join(rendered)}</ul>"



def _render_evolution_links(items: list[dict[str, object]], *, empty_text: str) -> str:
    if not items:
        return f"<p class='muted'>{escape(empty_text)}</p>"
    rows = []
    for item in items:
        rows.append(
            "<li>"
            f"<span class='pill'>{escape(str(item.get('link_type') or 'evolution'))}</span> "
            f"{escape(str(item.get('subject_kind') or 'subject'))}: {escape(str(item.get('subject_id') or ''))}"
            f"<div class='muted'>Earlier: {escape(str(item.get('earlier_ref') or ''))} | Later: {escape(str(item.get('later_ref') or ''))}</div>"
            + (
                f"<div class='muted'>Note: {escape(str(item.get('note') or ''))}</div>"
                if item.get("note")
                else ""
            )
            + (
                f"<div class='muted'>Reviewed at: {_ts(item.get('timestamp') or '')}</div>"
                if item.get("timestamp")
                else ""
            )
            + "</li>"
        )
    return "<ul class='list-tight'>" + "".join(rows) + "</ul>"



def _render_evolution_review_form(
    item: dict[str, object],
    *,
    requested_pack: str = "",
    next_path: str = "",
) -> str:
    link_type = str(item.get("link_type") or "")
    return "".join(
        [
            "<form method='post' action='/ops/evolution/review' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
            f"<input type='hidden' name='evolution_id' value='{escape(str(item['evolution_id']))}' />",
            (
                f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                if requested_pack
                else ""
            ),
            (
                f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                if next_path
                else ""
            ),
            _render_evolution_link_type_select(link_type),
            "<input type='text' name='note' placeholder='Review note' />",
            "<button type='submit' name='status' value='accepted'>Accept</button>",
            "<button type='submit' name='status' value='rejected'>Reject</button>",
            "</form>",
        ]
    )



def _render_explore_page(*, object_id: str) -> str:
    """Phase 38 Stage C — graph-native exploration surface.

    Layout (CSS grid):

        ┌──────────────────┬──────────────────┐
        │  Graph canvas    │  Agent timeline  │
        │  (iframe of      │  (SSE stream of  │
        │   /graph?id=...) │   graph_ops      │
        │                  │   tool calls)    │
        ├──────────────────┴──────────────────┤
        │  Synthesis pane (Crystal preview)   │
        └─────────────────────────────────────┘
    """
    canvas_src = f"/object/fragment?id={quote(object_id, safe='')}" if object_id else "/ops/objects"
    synth_src = f"/object/fragment?id={quote(object_id, safe='')}" if object_id else "/ops/objects"
    return (
        "<!doctype html>\n<html lang='en' data-theme='light'><head><meta charset='utf-8' />"
        "<meta name='viewport' content='width=device-width, initial-scale=1' />"
        "<title>Explore</title>"
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
        "<h1>Explore</h1>"
        f"<span class='meta'>object: <code id='ex-object'>{escape(object_id) or '∅'}</code></span>"
        "<a href='/' style='margin-left:auto'>← Shell</a>"
        "</header>"
        "<div class='fullbleed-grid explore'>"
        f"<section class='pane canvas'><iframe id='pane-canvas' src='{escape(canvas_src)}'></iframe></section>"
        f"<section class='pane timeline'>{_render_explore_fragment(object_id)}</section>"
        f"<section class='pane synth'><iframe id='pane-synth' src='{escape(synth_src)}'></iframe></section>"
        "</div>"
        "<script>(function(){"
        "function selectObject(id){"
        "document.getElementById('pane-canvas').src=id?'/object/fragment?id='+encodeURIComponent(id):'/ops/objects';"
        "document.getElementById('pane-synth').src=id?'/object/fragment?id='+encodeURIComponent(id):'/ops/objects';"
        "document.getElementById('ex-object').textContent=id||'∅';"
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



def _render_open_questions_page(payload: dict) -> str:
    return _render_fragment_shell("Open Questions", _render_open_questions_fragment(payload))



def _render_operator_rail(payload: dict) -> str:
    # Operator rail is a maintainer-only widget; suppress in Reader shell.
    if not _is_ops_path(_current_request_path()):
        return ""
    items = payload.get("operator_rail")
    if not isinstance(items, list) or not items:
        return ""
    rendered_items: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        path = str(item.get("path") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if not label:
            continue
        label_html = f'<a href="{escape(path)}">{escape(label)}</a>' if path else escape(label)
        detail_html = f"<div class='muted'>{escape(detail)}</div>" if detail else ""
        rendered_items.append(f"<li>{label_html}{detail_html}</li>")
    if not rendered_items:
        return ""
    return (
        "<section class='card'><h2>Next Actions</h2>"
        f"<ul class='list-tight'>{''.join(rendered_items)}</ul>"
        "</section>"
    )



def _render_reuse_report_page(payload: dict) -> str:
    return _render_fragment_shell("Reuse Report", _render_reuse_report_fragment(payload))



def _render_review_history(items: list[dict[str, object]], *, title: str = "Review History") -> str:
    if not items:
        return (
            "<section class='card'>"
            f"<h2>{escape(title)}</h2>"
            "<p class='muted'>No recent review actions recorded for this scope.</p>"
            "</section>"
        )
    rows = "".join(
        "<li>"
        f"<span class='pill'>{escape(str(item['event_type']))}</span> "
        f"{_ts(item['timestamp'])}"
        + (
            f"<div class='muted'>Status: {escape(str(item['status']))}</div>"
            if item.get("status")
            else ""
        )
        + (
            f"<div class='muted'>Note: {escape(str(item['note']))}</div>"
            if item.get("note")
            else ""
        )
        + (
            f"<div class='muted'>Objects: {escape(', '.join(str(v) for v in item['object_ids']))}</div>"
            if item.get("object_ids")
            else ""
        )
        + (
            f"<div class='muted'>Rebuilt: {escape(', '.join(str(v) for v in item['rebuilt_object_ids']))}</div>"
            if item.get("rebuilt_object_ids")
            else ""
        )
        + "</li>"
        for item in items
    )
    return (
        "<section class='card'>"
        f"<h2>{escape(title)}</h2>"
        f"<ul class='list-tight'>{rows}</ul>"
        "</section>"
    )



def _render_run_history_card(runtime: dict[str, object] | None) -> str:
    if not isinstance(runtime, dict):
        return ""
    history = runtime.get("run_history") if isinstance(runtime.get("run_history"), dict) else {}
    items = history.get("items") if isinstance(history.get("items"), list) else []
    if not items:
        return (
            "<section class='card'><h2>Recent Runs</h2>"
            "<p class='muted'>No persisted run history found in the transaction ledger.</p></section>"
        )
    rendered_items: list[str] = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "")
        status = str(item.get("status") or "")
        duration = str(item.get("duration_summary") or "duration unknown")
        scope = str(item.get("scope_summary") or "scope unknown")
        work = str(item.get("content_summary") or "No counted work recorded.")
        started_at = str(item.get("started_at") or "")
        finished_at = str(item.get("finished_at") or "running")
        step_summaries = (
            item.get("step_summaries") if isinstance(item.get("step_summaries"), list) else []
        )
        step_items: list[str] = []
        for step in step_summaries[:8]:
            if not isinstance(step, dict):
                continue
            labels = [str(step.get("status") or "").strip()]
            if step.get("cache_hit"):
                labels.append("cache hit")
            if step.get("skipped"):
                labels.append("skipped")
            blocked_reason = str(step.get("blocked_reason") or "").strip()
            if blocked_reason:
                labels.append(f"blocked: {blocked_reason}")
            labels_html = " · ".join(escape(label) for label in labels if label)
            step_items.append(
                f"<li>{escape(str(step.get('step_name') or ''))}"
                + (f" <span class='muted'>{labels_html}</span>" if labels_html else "")
                + "</li>"
            )
        steps_html = f"<ul class='list-tight'>{''.join(step_items)}</ul>" if step_items else ""
        rendered_items.append(
            "<li>"
            f"<strong>{escape(run_id)}</strong> "
            f"<span class='pill'>{escape(status)}</span>"
            f"<div class='muted'>Duration: {escape(duration)} · {_ts(started_at)} → {_ts(finished_at)}</div>"
            f"<div class='muted'>Scope: {escape(scope)}</div>"
            f"<div class='muted'>Work: {escape(work)}</div>"
            f"{steps_html}"
            "</li>"
        )
    total_count = history.get("total_count")
    total_suffix = (
        f"<p class='muted'>Showing {len(rendered_items)} of {escape(str(total_count))} persisted run(s).</p>"
        if total_count
        else ""
    )
    return (
        "<section class='card'><h2>Recent Runs</h2>"
        f"{total_suffix}"
        f"<ul class='list-tight'>{''.join(rendered_items)}</ul>"
        "</section>"
    )



def _render_runtime_card(runtime: dict[str, object] | None) -> str:
    if not isinstance(runtime, dict):
        return ""
    action_worker_card = _render_action_worker_card(runtime)
    active_run = runtime.get("active_run")
    try:
        stale_count = int(runtime.get("stale_count") or 0)
    except (TypeError, ValueError):
        stale_count = 0
    if not isinstance(active_run, dict):
        detail = "No active workflow is currently recorded in the canonical run ledger."
        stale_html = (
            f"<p class='muted'>{stale_count} stale run(s) remain in the ledger and need operator cleanup.</p>"
            if stale_count
            else ""
        )
        return (
            f"<section class='card'><h2>Current Workflow</h2><p class='muted'>{detail}</p>{stale_html}</section>"
            + action_worker_card
        )

    ledger = active_run.get("run_ledger") if isinstance(active_run.get("run_ledger"), dict) else {}
    current = ledger.get("current_step") if isinstance(ledger.get("current_step"), dict) else {}
    runtime_progress = (
        active_run.get("runtime_progress")
        if isinstance(active_run.get("runtime_progress"), dict)
        else {}
    )
    stage_progress = (
        runtime_progress.get("stage") if isinstance(runtime_progress.get("stage"), dict) else {}
    )
    work_progress = (
        runtime_progress.get("work") if isinstance(runtime_progress.get("work"), dict) else {}
    )
    performance = (
        runtime_progress.get("performance")
        if isinstance(runtime_progress.get("performance"), dict)
        else {}
    )
    run_state = str(ledger.get("run_state") or active_run.get("status") or "running")
    step_name = str(
        current.get("step_name")
        or ledger.get("current_step_name")
        or active_run.get("checkpoint")
        or ""
    )
    steps = active_run.get("steps") if isinstance(active_run.get("steps"), dict) else {}
    current_step_record = steps.get(step_name) if isinstance(steps.get(step_name), dict) else {}
    progress_summary = str(
        work_progress.get("summary")
        or current.get("progress_summary")
        or "Progress is currently indeterminate."
    )
    current_item = str(
        work_progress.get("current_item") or current.get("current_item") or ""
    ).strip()
    heartbeat_at = str(ledger.get("heartbeat_at") or active_run.get("last_updated") or "")
    facts = [
        f"<li>Run: {escape(str(active_run.get('id') or ''))}</li>",
        f"<li>State: {escape(run_state)}</li>",
    ]
    runtime_processes = (
        runtime.get("runtime_processes")
        if isinstance(runtime.get("runtime_processes"), dict)
        else {}
    )
    process_items = (
        runtime_processes.get("items") if isinstance(runtime_processes.get("items"), list) else []
    )
    if process_items:
        for process in process_items[:2]:
            if not isinstance(process, dict):
                continue
            process_kind = str(process.get("process_kind") or "unknown").replace("_", "-")
            elapsed = str(process.get("elapsed_summary") or process.get("elapsed_raw") or "unknown")
            args_summary = str(process.get("args_summary") or "").strip()
            process_detail = (
                f"PID {escape(str(process.get('pid') or ''))} · {escape(process_kind)} · "
                f"running {escape(elapsed)}"
            )
            if args_summary:
                process_detail += f" · {escape(args_summary)}"
            facts.append(f"<li>Process: {process_detail}</li>")
    else:
        facts.append("<li>Process: no matching pipeline worker detected</li>")
    stage_summary = str(stage_progress.get("summary") or "").strip()
    if stage_summary:
        facts.append(f"<li>Stage: {escape(stage_summary)}</li>")
    elif step_name:
        facts.append(f"<li>Step: {escape(step_name)}</li>")
    if current_step_record.get("cache_hit") or current.get("cache_hit"):
        facts.append("<li>Cache: hit</li>")
    if current_step_record.get("skipped") or current.get("skipped"):
        facts.append("<li>Skipped: yes</li>")
    blocked_reason = str(
        current_step_record.get("blocked_reason")
        or current.get("blocked_reason")
        or ledger.get("blocked_reason")
        or ""
    ).strip()
    if blocked_reason:
        facts.append(f"<li>Blocked reason: {escape(blocked_reason)}</li>")
    stage_fingerprint = str(
        current_step_record.get("stage_fingerprint") or current.get("stage_fingerprint") or ""
    ).strip()
    if stage_fingerprint:
        facts.append(f"<li>Fingerprint: {escape(stage_fingerprint)}</li>")
    work_done = work_progress.get("done")
    work_total = work_progress.get("total")
    work_percent = work_progress.get("percent")
    if work_done is not None and work_total is not None:
        if work_percent is not None:
            facts.append(
                f"<li>Files: {escape(str(work_done))}/{escape(str(work_total))} "
                f"({escape(str(work_percent))}%)</li>"
            )
        else:
            facts.append(f"<li>Files: {escape(str(work_done))}/{escape(str(work_total))}</li>")
    failed = work_progress.get("failed")
    if failed:
        facts.append(f"<li>Failed files: {escape(str(failed))}</li>")
    rate_summary = str(performance.get("rate_summary") or "").strip()
    if rate_summary:
        facts.append(f"<li>Speed: {escape(rate_summary)}</li>")
    eta_summary = str(performance.get("eta_summary") or "").strip()
    if eta_summary:
        facts.append(f"<li>ETA: {escape(eta_summary)}</li>")
    elapsed_summary = str(performance.get("elapsed_summary") or "").strip()
    if elapsed_summary:
        facts.append(f"<li>Stage elapsed: {escape(elapsed_summary)}</li>")
    if heartbeat_at:
        facts.append(f"<li>Heartbeat: {escape(heartbeat_at)}</li>")
    if stale_count:
        facts.append(f"<li>Stale runs: {stale_count}</li>")
    current_item_html = (
        f"<p class='muted'>Current item: {escape(current_item)}</p>" if current_item else ""
    )
    return (
        "<section class='card'><h2>Current Workflow</h2>"
        f"<p class='muted'>{escape(progress_summary)}</p>"
        f"{current_item_html}"
        f"<ul class='list-tight'>{''.join(facts)}</ul>"
        "</section>" + action_worker_card
    )



def _render_type_facet(
    kind_stats: list[dict],
    *,
    active_kind: str,
    query: str,
    requested_pack: str,
    base_path: str = "/ops/objects",
    top_n: int = _TYPE_FACET_DEFAULT_LIMIT,
) -> str:
    """Render the type-facet chip rail for ``/ops/objects`` and
    similar Reader-side surfaces.

    ``kind_stats`` is the ``list_object_kind_stats`` shape:
    ``[{"object_kind": str, "count": int, ...}, ...]``.  Top-N
    types by count are shown as clickable chips; an "All" chip
    clears the filter.  The active kind is highlighted.

    If ``active_kind`` is set but its row sits outside the top-N
    slice, we splice it in so the operator can still see (and
    click off) the active filter — otherwise the chip rail would
    look as if no filter were applied.
    """
    if not kind_stats:
        return ""
    from ovp_pipeline.object_kinds import display_label

    ranked = sorted(
        (s for s in kind_stats if s.get("object_kind")),
        key=lambda s: -int(s.get("count") or 0),
    )
    sorted_stats = ranked[:top_n]
    if active_kind and not any(str(s.get("object_kind")) == active_kind for s in sorted_stats):
        active_row = next(
            (s for s in ranked if str(s.get("object_kind")) == active_kind),
            None,
        )
        if active_row is not None:
            sorted_stats = [*sorted_stats, active_row]
    if not sorted_stats:
        return ""

    def _href(kind: str) -> str:
        return f"{base_path}{_build_objects_query_string(query=query, object_kind=kind, requested_pack=requested_pack)}"

    chips: list[str] = []
    chips.append(
        f"<a href='{escape(_href(''))}'"
        + (" class='active'" if not active_kind else "")
        + ">All</a>"
    )
    for stat in sorted_stats:
        kind = str(stat["object_kind"])
        count = int(stat.get("count") or 0)
        label = display_label(kind)
        cls_attr = " class='active'" if kind == active_kind else ""
        chips.append(
            f"<a href='{escape(_href(kind))}'{cls_attr}>"
            f"{escape(label)} <span class='muted tiny mono'>{count}</span>"
            "</a>"
        )
    return (
        "<div style='margin:.75rem 0 1rem'>"
        "<h3 style='font-size:.85rem;font-weight:500;color:var(--muted);"
        "margin:0 0 .35rem'>Filter by type</h3>"
        f"<div class='subnav'>{''.join(chips)}</div>"
        "</div>"
    )



def _render_writing_prompts_page(payload: dict) -> str:
    return _render_fragment_shell("Writing Prompts", _render_writing_prompts_fragment(payload))



def _rewrite_local_image_links(vault_dir: Path, markdown: str) -> str:
    def replace_match(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        raw_target = match.group(2).strip()
        if raw_target.startswith(("http://", "https://", "data:", "/asset?")):
            return match.group(0)
        candidate = (vault_dir / raw_target).resolve()
        try:
            relative_path = str(candidate.relative_to(vault_dir.resolve()))
        except ValueError:
            return match.group(0)
        if not candidate.is_file():
            return match.group(0)
        return f"![{alt_text}]({_asset_href(relative_path)})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_match, markdown)



def _search_href(query: str, requested_pack: str = "") -> str:
    return _shell_href(f"/search?q={quote(query, safe='')}", requested_pack)


__all__ = [
    '_ask_about_this_href',
    '_convert_box_table_fences',
    '_note_href',
    '_object_href',
    '_ops_nav_items',
    '_reader_nav_items',
    '_render_candidate_items',
    '_render_evolution_links',
    '_render_evolution_review_form',
    '_render_explore_page',
    '_render_open_questions_page',
    '_render_operator_rail',
    '_render_reuse_report_page',
    '_render_review_history',
    '_render_run_history_card',
    '_render_runtime_card',
    '_render_type_facet',
    '_render_writing_prompts_page',
    '_rewrite_local_image_links',
    '_search_href'
]
