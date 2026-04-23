from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import re
import subprocess
import sys
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import yaml
from markdown_it import MarkdownIt

from ..identity import canonicalize_note_id
from ..knowledge_index import (
    contradiction_object_ids,
    rebuild_compiled_summaries,
    resolve_contradictions,
)
from ..pack_resolution import iter_compatible_packs
from ..packs.loader import DEFAULT_PACK_NAME, PRIMARY_PACK_NAME
from ..pulse import initial_positions, tail_events
from ..runtime import VaultLayout, resolve_vault_dir
from .reuse_report import build_reuse_report_payload
from ..ui.view_models import (
    build_action_queue_payload,
    build_atlas_browser_payload,
    build_briefing_payload,
    build_candidate_browser_payload,
    build_cluster_browser_payload,
    build_cluster_detail_payload,
    build_contradiction_browser_payload,
    build_derivation_browser_payload,
    build_evolution_browser_payload,
    build_event_dossier_payload,
    build_note_page_payload,
    build_object_page_payload,
    build_objects_index_payload,
    build_production_browser_payload,
    build_runtime_home_payload,
    build_search_payload,
    build_signal_browser_payload,
    build_stale_summary_browser_payload,
    build_topic_overview_payload,
)
from ..truth_api import (
    dismiss_action_queue_item,
    enqueue_signal_action,
    ensure_signal_ledger_synced,
    get_runtime_status,
    list_review_actions,
    record_review_action,
    review_candidate_concept,
    retry_action_queue_item,
    review_evolution_candidate,
    run_action_queue,
    run_next_action_queue_item,
)

_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_GITHUB_REPO_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s#]+)")
_EVOLUTION_LINK_TYPES = ["challenges", "replaces", "enriches", "confirms"]
_CANDIDATE_MERGE_AUTOFILL_THRESHOLD = 0.7


def _shell_href(path: str, requested_pack: str = "") -> str:
    if not requested_pack:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}pack={quote(requested_pack, safe='')}"


def _append_query_param(path: str, key: str, value: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{quote(key, safe='')}={quote(value, safe='')}"


def _shell_supports_research_nav(requested_pack: str = "") -> bool:
    try:
        return any(
            pack.name == PRIMARY_PACK_NAME for pack in iter_compatible_packs(requested_pack or None)
        )
    except ValueError:
        return False


def _shell_nav_items(requested_pack: str = "") -> list[tuple[str, str]]:
    items = [
        ("Home", "/"),
        ("Objects", "/objects"),
        ("Search", "/search"),
        ("Signals", "/signals"),
        ("Briefing", "/briefing"),
        ("Actions", "/actions"),
        ("Production", "/production"),
        ("Workbench", "/workbench"),
    ]
    if _shell_supports_research_nav(requested_pack):
        items.extend(
            [
                ("Candidates", "/candidates"),
                ("Evolution", "/evolution"),
                ("Clusters", "/clusters"),
                ("Atlas", "/atlas"),
                ("Deep Dives", "/deep-dives"),
                ("Event Dossier", "/events"),
                ("Contradictions", "/contradictions"),
                ("Stale Summaries", "/summaries"),
            ]
        )
    return items


def _layout(
    title: str, body: str, *, requested_pack: str = "", auto_refresh_seconds: int | None = None
) -> str:
    nav_items = "".join(
        f'<a href="{escape(_shell_href(path, requested_pack))}">{escape(label)}</a>'
        for label, path in _shell_nav_items(requested_pack)
    )
    refresh_meta = (
        f'    <meta http-equiv="refresh" content="{int(auto_refresh_seconds)}" />\n'
        if auto_refresh_seconds and auto_refresh_seconds > 0
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
{refresh_meta}    <meta name="ovp-runtime-refresh" content="{int(auto_refresh_seconds or 0)}" />
    <title>{escape(title)}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f7f6f2;
        --surface: #fffdfa;
        --border: #e7e1d8;
        --text: #1f1a17;
        --muted: #71675d;
        --accent: #9f4f24;
        --accent-soft: #f4dfd2;
      }}
      * {{ box-sizing: border-box; }}
      body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; line-height: 1.5; background: var(--bg); color: var(--text); }}
      main {{ max-width: 1180px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; }}
      nav {{ margin-bottom: 1.5rem; display: flex; gap: 0.9rem; flex-wrap: wrap; }}
      nav a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
      nav a:hover {{ text-decoration: underline; }}
      h1, h2, h3 {{ margin-bottom: 0.5rem; line-height: 1.2; }}
      ul {{ padding-left: 1.2rem; }}
      pre {{ background: #f4f4f5; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
      img {{ max-width: 100%; height: auto; display: block; border-radius: 12px; }}
      input, select, button {{ font: inherit; }}
      input, select {{ padding: 0.55rem 0.7rem; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }}
      button {{ padding: 0.55rem 0.8rem; border-radius: 10px; border: 1px solid var(--accent); background: var(--accent); color: white; cursor: pointer; }}
      button:hover {{ opacity: 0.92; }}
      .muted {{ color: var(--muted); }}
      .hero {{ margin-bottom: 1.5rem; }}
      .shell {{ background: var(--surface); border: 1px solid var(--border); border-radius: 20px; box-shadow: 0 12px 36px rgba(31, 26, 23, 0.06); }}
      .shell-head {{ padding: 1.1rem 1.25rem 0; }}
      .shell-body {{ padding: 0 1.25rem 1.25rem; }}
      .card {{ border: 1px solid var(--border); background: var(--surface); border-radius: 16px; padding: 1rem; margin-bottom: 1rem; }}
      .warning {{ border-color: #d48a2f; background: #fff8ec; }}
      .grid {{ display: grid; gap: 1rem; }}
      .stats {{ grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }}
      .two-col {{ grid-template-columns: minmax(0, 2.1fr) minmax(280px, 1fr); align-items: start; }}
      .pill {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; background: var(--accent-soft); color: var(--accent); margin-right: 0.5rem; }}
      .link-row {{ display: flex; gap: 0.75rem; flex-wrap: wrap; margin-top: 0.9rem; }}
      .link-row a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
      .subnav {{ display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.9rem; margin-bottom: 1rem; }}
      .subnav a {{ color: var(--muted); text-decoration: none; padding: 0.35rem 0.6rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); }}
      .subnav a:hover {{ color: var(--accent); border-color: var(--accent-soft); }}
      .list-tight li {{ margin-bottom: 0.4rem; }}
      .section-stack {{ display: grid; gap: 1rem; }}
      .meta-list {{ display: grid; gap: 0.6rem; margin: 0; }}
      .meta-list dt {{ font-weight: 700; }}
      .meta-list dd {{ margin: 0; color: var(--muted); }}
      @media (max-width: 780px) {{ .two-col {{ grid-template-columns: 1fr; }} main {{ padding: 1rem 1rem 2rem; }} }}
    </style>
  </head>
  <body>
    <main>
      <div class="shell">
        <div class="shell-head">
          <nav>
            {nav_items}
          </nav>
        </div>
        <div class="shell-body">
          {body}
        </div>
      </div>
    </main>
  </body>
</html>
"""


def _note_href(path: str, requested_pack: str = "") -> str:
    return _shell_href(f"/note?path={quote(path, safe='')}", requested_pack)


def _asset_href(path: str) -> str:
    return f"/asset?path={quote(path, safe='')}"


def _search_href(query: str, requested_pack: str = "") -> str:
    return _shell_href(f"/search?q={quote(query, safe='')}", requested_pack)


def _object_href(object_id: str, path: str = "", requested_pack: str = "") -> str:
    if path:
        return path
    return _shell_href(f"/object?id={quote(str(object_id), safe='')}", requested_pack)


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
            f"<li>Source contract: {escape(source_contract_kind)} · {escape(source_contract_name)}</li>"
            if source_contract_kind or source_contract_name
            else "",
            f"<li>Source provider: {escape(source_provider_pack)} · {escape(source_provider_name)}</li>"
            if source_provider_pack or source_provider_name
            else "",
            f"<li>Output: {escape(output_mode)} → {escape(publish_target)}</li>"
            if output_mode or publish_target
            else "",
        )
    )
    description_html = f"<p class='muted'>{escape(description)}</p>" if description else ""
    facts_html = f"<ul class='list-tight'>{facts}</ul>" if facts else ""
    return (
        f"<section class='card'><h2>Assembly Contract</h2><p class='muted'>{detail}</p>"
        f"{description_html}{facts_html}</section>"
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
            f"<div class='muted'>Duration: {escape(duration)} · {escape(started_at)} → {escape(finished_at)}</div>"
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


def _render_operator_rail(payload: dict) -> str:
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


def _split_lead_compiled_sections(
    sections: list[dict[str, object]] | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    normalized = [section for section in (sections or []) if isinstance(section, dict)]
    if not normalized:
        return [], []
    return [normalized[0]], normalized[1:]


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


def _render_unsupported_route_page(route_path: str, requested_pack: str = "") -> str:
    payload = _unsupported_route_payload(route_path, requested_pack)
    return _layout(
        "Route Unavailable",
        "".join(
            [
                "<h1>Route Unavailable</h1>",
                f"<p class='muted'>{escape(payload['error'])}</p>",
                "<section class='card'><h2>Why</h2><p class='muted'>This route currently belongs to the research-specific observation shell. Shared shell routes remain available, but research-only routes stay hidden until the current pack declares equivalent semantics.</p></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_research_scope_notice(requested_pack: str = "") -> str:
    pack_label = f" for pack '{requested_pack}'" if requested_pack else ""
    return (
        "<section class='card'><h2>Research Review</h2>"
        f"<p class='muted'>Research-specific review surfaces stay hidden{escape(pack_label)}. "
        "This page still shows shared object/topic context, but contradiction, summary, evolution, and related research affordances only appear when the current pack declares those semantics.</p>"
        "</section>"
    )


def _read_vault_note(vault_dir: Path, relative_path: str) -> tuple[Path, str]:
    candidate = (vault_dir / relative_path).resolve()
    try:
        candidate.relative_to(vault_dir.resolve())
    except ValueError as exc:
        raise ValueError("invalid note path") from exc
    if not candidate.is_file():
        raise ValueError(f"note not found: {relative_path}")
    return candidate, candidate.read_text(encoding="utf-8")


def _read_vault_asset(vault_dir: Path, relative_path: str) -> tuple[bytes, str]:
    candidate = (vault_dir / relative_path).resolve()
    try:
        candidate.relative_to(vault_dir.resolve())
    except ValueError as exc:
        raise ValueError("invalid asset path") from exc
    if not candidate.is_file():
        raise ValueError(f"asset not found: {relative_path}")
    return candidate.read_bytes(), mimetypes.guess_type(candidate.name)[
        0
    ] or "application/octet-stream"


def _lookup_wikilink_target(
    vault_dir: Path, target: str, *, requested_pack: str = ""
) -> tuple[str, str] | None:
    db_path = VaultLayout.from_vault(vault_dir).knowledge_db
    if not db_path.exists():
        return None

    raw_target = target.split("|", 1)[0].split("#", 1)[0].strip()
    if not raw_target:
        return None

    exact_path = raw_target
    stem = Path(raw_target).stem
    normalized = canonicalize_note_id(raw_target)
    normalized_stem = canonicalize_note_id(stem)
    suffixes = [f"%/{stem.lower()}.md"]
    if raw_target.lower().endswith(".md"):
        suffixes.append(f"%/{raw_target.lower()}")

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT slug, title, note_type, path
            FROM pages_index
            WHERE lower(slug) = ?
               OR lower(title) = ?
               OR lower(path) = ?
               OR lower(path) LIKE ?
               OR lower(path) LIKE ?
            LIMIT 25
            """,
            (
                normalized,
                raw_target.lower(),
                exact_path.lower(),
                suffixes[0],
                suffixes[-1],
            ),
        ).fetchall()

    def rank(row: tuple[str, str, str, str]) -> tuple[int, str]:
        slug, title, _note_type, path = row
        path_lower = path.lower()
        title_lower = title.lower()
        if slug == normalized:
            return (0, path)
        if normalized_stem and slug == normalized_stem:
            return (1, path)
        if title_lower == raw_target.lower():
            return (2, path)
        if path_lower.endswith(f"/{raw_target.lower()}"):
            return (3, path)
        if path_lower.endswith(f"/{stem.lower()}.md"):
            return (4, path)
        return (10, path)

    if not rows:
        for candidate in vault_dir.rglob("*.md"):
            if candidate.stem.lower() != stem.lower():
                continue
            relative_path = str(candidate.resolve().relative_to(vault_dir.resolve()))
            if "10-Knowledge/Evergreen/" in relative_path:
                return (
                    _shell_href(
                        f"/object?id={quote(canonicalize_note_id(stem), safe='')}", requested_pack
                    ),
                    canonicalize_note_id(stem),
                )
            return (_note_href(relative_path, requested_pack), relative_path)
        return None

    slug, _title, note_type, path = sorted(rows, key=rank)[0]
    relative_path = path
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            relative_path = str(candidate.resolve().relative_to(vault_dir.resolve()))
        except ValueError:
            relative_path = path

    if note_type == "evergreen":
        return (_shell_href(f"/object?id={quote(slug, safe='')}", requested_pack), slug)
    return (_note_href(relative_path, requested_pack), relative_path)


def _is_search_href(href: str) -> bool:
    return href.startswith("/search?q=")


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return markdown
    return markdown[end + 5 :]


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


def _replace_wikilinks_with_markdown_links(
    vault_dir: Path, markdown: str, *, requested_pack: str = ""
) -> str:
    def replace_match(match: re.Match[str]) -> str:
        raw_inner = match.group(1)
        target_part, _, label_part = raw_inner.partition("|")
        label = label_part.strip() or target_part.split("#", 1)[0].strip()
        resolved = _lookup_wikilink_target(vault_dir, target_part, requested_pack=requested_pack)
        href = (
            resolved[0]
            if resolved
            else _search_href(target_part.split("#", 1)[0].strip() or label, requested_pack)
        )
        emoji = "🔍" if _is_search_href(href) else "🎯"
        safe_label = label.replace("[", "\\[").replace("]", "\\]")
        return f"[{emoji} {safe_label}]({href})"

    output_lines: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            output_lines.append(line)
            continue
        if in_fence:
            output_lines.append(line)
            continue
        output_lines.append(re.sub(r"\[\[([^\]]+)\]\]", replace_match, line))
    return "\n".join(output_lines)


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


def _smart_markdown_link(label: str, href: str) -> str:
    safe_label = label.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe_label}]({href})"


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


def _linkify_keywords(markdown: str, *, requested_pack: str = "") -> str:
    output: list[str] = []
    keyword_re = re.compile(r"^(\*\*关键词\*\*|关键词)\s*[：:]\s*(.+)$")
    for line in markdown.splitlines():
        match = keyword_re.match(line.strip())
        if not match:
            output.append(line)
            continue
        prefix, values = match.groups()
        rendered = []
        for raw in values.split(","):
            keyword = raw.strip()
            if not keyword:
                continue
            rendered.append(_smart_markdown_link(keyword, _search_href(keyword, requested_pack)))
        output.append(f"{prefix}：{'，'.join(rendered)}")
    return "\n".join(output)


def _linkify_related_knowledge_section(
    vault_dir: Path, markdown: str, *, requested_pack: str = ""
) -> str:
    output_lines: list[str] = []
    in_related = False

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_related = stripped.lstrip("#").strip() == "关联知识"
            output_lines.append(line)
            continue
        if in_related and re.match(r"^- [^\[][^—]+ — ", stripped):
            concept, sep, remainder = stripped[2:].partition(" — ")
            concept = concept.strip()
            resolved = _lookup_wikilink_target(vault_dir, concept, requested_pack=requested_pack)
            href = resolved[0] if resolved else _search_href(concept, requested_pack)
            emoji = "🔍" if _is_search_href(href) else "🎯"
            output_lines.append(f"- [{emoji} {concept}]({href}) — {remainder}")
            continue
        output_lines.append(line)

    return "\n".join(output_lines)


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


def _render_note_page(
    vault_dir: Path, relative_path: str, markdown: str, payload: dict | None = None
) -> str:
    requested_pack = payload.get("requested_pack", "") if payload else ""
    frontmatter_html, note_html = _render_markdown_note(
        vault_dir, markdown, requested_pack=requested_pack
    )
    source_note = None
    derived_notes: list[dict[str, str]] = []
    production_chain = None
    compiled_sections: list[dict[str, object]] = []
    section_nav_items: list[dict[str, str]] = []
    if payload:
        source_note = payload.get("provenance", {}).get("original_source_note")
        derived_notes = payload.get("provenance", {}).get("derived_deep_dives", [])
        production_chain = payload.get("production_chain")
        compiled_sections = list(payload.get("compiled_sections") or [])
        section_nav_items = list(payload.get("section_nav") or [])
    lead_sections, remaining_sections = _split_lead_compiled_sections(compiled_sections)
    provenance_html = ""
    if source_note:
        provenance_html = (
            "<section class='card'>"
            "<h2>Provenance</h2>"
            "<dl class='meta-list'>"
            "<div><dt>Original Source Note</dt><dd>"
            f'<a href="{escape(_note_href(source_note["path"], requested_pack))}">{escape(source_note["title"])}</a>'
            f"<div class='muted'>{escape(source_note['path'])}</div>"
            "</dd></div>"
            "</dl>"
            "</section>"
        )
    if derived_notes:
        derived_list = "".join(
            f'<li><a href="{escape(item.get("note_path") or _note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
            f"<div class='muted'>{escape(item['path'])}</div></li>"
            for item in derived_notes
        )
        provenance_html += (
            "<section class='card'>"
            "<h2>Derived Deep Dives</h2>"
            f"<ul class='list-tight'>{derived_list}</ul>"
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
            "<dl class='meta-list'>"
            f"<div><dt>Current Note</dt><dd>{escape(production_chain['note']['title'])}<div class='muted'>{escape(production_chain['note']['path'])}</div></dd></div>"
            f"<div><dt>Chain Status</dt><dd>{escape(str(production_chain.get('chain_status') or ''))}</dd></div>"
            f"<div><dt>Missing Stages</dt><dd>{escape(missing_stages)}</dd></div>"
            f"<div><dt>Chain Summary</dt><dd>{escape(str(production_chain.get('chain_summary') or ''))}</dd></div>"
            f"<div><dt>Source Notes</dt><dd>{_render_named_note_links(production_chain['source_notes'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Deep Dives</dt><dd>{_render_named_note_links(production_chain['deep_dives'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Derived Objects</dt><dd>{_render_object_links(production_chain['objects'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Atlas / MOC Reach</dt><dd>{_render_named_note_links(production_chain['atlas_pages'], requested_pack=requested_pack)}</dd></div>"
            "</dl>"
            "</section>"
        )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in section_nav_items
    )
    operator_rail_card = _render_operator_rail(payload or {})
    return _layout(
        f"Markdown Note: {relative_path}",
        (
            "<section class='hero'>"
            "<h1>Markdown Note</h1>"
            f"<p class='muted'>{escape(relative_path)}</p>"
            "</section>"
            + _render_compiled_sections(lead_sections)
            + operator_rail_card
            + (f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "")
            + f"{frontmatter_html}"
            + f"{provenance_html}"
            + f"{production_chain_html}"
            + f"{_render_compiled_sections(remaining_sections)}"
            + f"<section class='card'>{note_html}</section>"
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

    object_items = (
        "".join(
            f'<li><a href="{escape(item.get("object_path") or _object_href(item["object_id"], requested_pack=requested_pack))}">{escape(item["title"])}</a> '
            f'<span class="muted">({escape(item["object_id"])})</span></li>'
            for item in payload["objects"]
        )
        or "<li class='muted'>No object hits.</li>"
    )
    note_items = (
        "".join(
            f'<li><a href="{escape(item.get("note_path") or _note_href(item["path"], requested_pack))}">{escape(item["title"])}</a> '
            f'<span class="pill">{escape(item["note_type"])}</span></li>'
            for item in payload["notes"]
        )
        or "<li class='muted'>No note hits.</li>"
    )
    showing = (
        f"Showing {payload['object_count']} of {object_total} object hits, "
        f"{payload['note_count']} of {note_total} note hits."
    )
    return _layout(
        f"Search: {query}",
        "".join(
            [
                "<h1>Search</h1>",
                "<form method='get' action='/search'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' /> "
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Search vault' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{escape(showing)}</p>",
                "<section class='grid two-col'>",
                "<section class='card'>"
                f"<h2>Objects</h2>"
                f"<ul class='list-tight'>{object_items}</ul>"
                f"{_pager(object_total, 'Objects')}"
                "</section>",
                "<section class='card'>"
                f"<h2>Notes</h2>"
                f"<ul class='list-tight'>{note_items}</ul>"
                f"{_pager(note_total, 'Notes')}"
                "</section>",
                "</section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_named_note_links(items: list[dict[str, str]], *, requested_pack: str = "") -> str:
    if not items:
        return "<span class='muted'>None</span>"
    return ", ".join(
        f'<a href="{escape(item.get("note_path") or _note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
        for item in items
    )


def _render_object_links(items: list[dict[str, str]], *, requested_pack: str = "") -> str:
    if not items:
        return "<span class='muted'>None</span>"
    return ", ".join(
        f'<a href="{escape(_object_href(item["object_id"], item.get("object_path", ""), requested_pack=requested_pack))}">{escape(item["title"])}</a>'
        for item in items
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


def _render_evolution_review_form(
    item: dict[str, object],
    *,
    requested_pack: str = "",
    next_path: str = "",
) -> str:
    link_type = str(item.get("link_type") or "")
    return "".join(
        [
            "<form method='post' action='/evolution/review' class='link-row'>",
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
                f"<div class='muted'>Reviewed at: {escape(str(item.get('timestamp') or ''))}</div>"
                if item.get("timestamp")
                else ""
            )
            + "</li>"
        )
    return "<ul class='list-tight'>" + "".join(rows) + "</ul>"


def _render_evolution_candidates(
    items: list[dict[str, object]],
    *,
    compact: bool = False,
    reviewable: bool = False,
    requested_pack: str = "",
    next_path: str = "",
) -> str:
    if not items:
        return "<p class='muted'>No evolution candidates surfaced for this scope.</p>"
    rows = []
    for item in items[: 3 if compact else len(items)]:
        source_paths = (
            ", ".join(
                f'<a href="{escape(_note_href(path, requested_pack))}">{escape(path)}</a>'
                for path in item["source_paths"]
            )
            or "<span class='muted'>None</span>"
        )
        evidence = ", ".join(
            escape(str(entry.get("source_slug") or entry.get("path") or entry.get("title") or ""))
            for entry in item["evidence"][:2]
            if isinstance(entry, dict)
        )
        rows.append(
            "<li>"
            f"<span class='pill'>{escape(str(item['link_type']))}</span> "
            f"{escape(str(item['subject_kind']))}: {escape(str(item['subject_id']))}"
            f"<div class='muted'>Earlier: {escape(str(item['earlier_ref']))} | Later: {escape(str(item['later_ref']))}</div>"
            f"<div class='muted'>Reasons: {escape(', '.join(str(code) for code in item['reason_codes']))}</div>"
            f"<div class='muted'>Sources: {source_paths}</div>"
            + (f"<div class='muted'>Evidence: {evidence}</div>" if evidence else "")
            + (
                _render_evolution_review_form(
                    item,
                    requested_pack=requested_pack,
                    next_path=next_path,
                )
                if reviewable
                else ""
            )
            + "</li>"
        )
    return "<ul class='list-tight'>" + "".join(rows) + "</ul>"


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
        "<dl class='meta-list'>"
        f"<div><dt>Objects in scope</dt><dd>{int(context.get('object_count', 0))}</dd></div>"
        f"<div><dt>Source notes</dt><dd>{int(context.get('source_note_count', 0))}</dd></div>"
        f"<div><dt>Atlas / MOC pages</dt><dd>{int(context.get('moc_count', 0))}</dd></div>"
        f"<div><dt>Open contradictions</dt><dd>{int(context.get('open_contradiction_count', 0))}</dd></div>"
        f"<div><dt>Total contradictions</dt><dd>{int(context.get('contradiction_count', 0))}</dd></div>"
        f"<div><dt>Stale summaries</dt><dd>{int(context.get('stale_summary_count', 0))}</dd></div>"
        f"<div><dt>Latest event date</dt><dd>{latest_event_html}</dd></div>"
        f"<div><dt>Contradiction objects</dt><dd>{escape(contradiction_object_ids)}</dd></div>"
        f"<div><dt>Stale summary objects</dt><dd>{escape(stale_summary_ids)}</dd></div>"
        "</dl>"
        "</section>"
    )


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
        f"{escape(str(item['timestamp']))}"
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


def _render_production_summary_card(
    summary: dict[str, object],
    *,
    title: str = "Production Contribution",
    requested_pack: str = "",
) -> str:
    signal_items = (
        "".join(
            f"<li>{escape(str(signal['label']))}: {int(signal['count'])}</li>"
            for signal in summary["signals"]
        )
        or "<li class='muted'>No production-chain gaps surfaced for this scope.</li>"
    )
    count_items = "".join(
        f"<li>{escape(label)}: {int(summary['counts'][key])}</li>"
        for key, label in (
            ("source_notes", "Source notes"),
            ("deep_dives", "Deep dives"),
            ("atlas_pages", "Atlas / MOC pages"),
        )
    )
    return (
        "<section class='card'>"
        f"<h2>{escape(title)}</h2>"
        "<dl class='meta-list'>"
        f"<div><dt>Objects in scope</dt><dd>{int(summary['object_count'])}</dd></div>"
        f"<div><dt>Top Source Notes</dt><dd>{_render_named_note_links(summary['top_source_notes'], requested_pack=requested_pack)}</dd></div>"
        f"<div><dt>Top Deep Dives</dt><dd>{_render_named_note_links(summary['top_deep_dives'], requested_pack=requested_pack)}</dd></div>"
        f"<div><dt>Atlas / MOC Reach</dt><dd>{_render_named_note_links(summary['top_atlas_pages'], requested_pack=requested_pack)}</dd></div>"
        "</dl>"
        f"<ul class='list-tight'>{count_items}{signal_items}</ul>"
        "</section>"
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


def _render_dashboard(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    runtime_card = _render_runtime_card(payload.get("runtime"))
    run_history_card = _render_run_history_card(payload.get("runtime"))
    research_overview = payload.get("research_overview", {})
    research_overview_supported = research_overview.get("status") == "supported"
    orientation = payload.get("orientation", {})
    signals_surface_contract = _render_surface_contract_card(payload["signals"])
    production_surface_contract = _render_surface_contract_card(payload["production"])
    orientation_assembly_contract = (
        _render_assembly_contract_card(orientation) if isinstance(orientation, dict) else ""
    )
    orientation_governance_contract = (
        _render_governance_contract_card(orientation) if isinstance(orientation, dict) else ""
    )
    entry_sections_html = _render_compiled_sections(payload.get("entry_sections", []))
    workflow_groups_html = _render_workflow_groups(payload.get("workflow_groups", []))
    object_items = (
        "".join(
            f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", "")))}">{escape(item["title"])}</a></li>'
            for item in payload["objects"]["items"]
        )
        or "<li>None</li>"
    )
    contradiction_items = (
        "".join(
            f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
            for item in payload["contradictions"]["items"]
        )
        or "<li>None</li>"
    )
    event_items = (
        "".join(
            f"<li>{escape(item['event_date'])} - "
            f'<a href="{escape(item["object_path"])}">{escape(item["title"])}</a></li>'
            for item in payload["events"]["items"]
        )
        or "<li>None</li>"
    )
    stale_summary_items = (
        "".join(
            f'<li><a href="{escape(item["object_path"])}">{escape(item["title"])}</a> '
            f"<span class='muted'>({escape(item['summary_text'])})</span></li>"
            for item in payload["stale_summaries"]["items"]
        )
        or "<li>None</li>"
    )
    evolution_items = _render_evolution_candidates(
        payload["evolution"]["items"],
        compact=False,
        requested_pack=requested_pack,
        next_path=_shell_href("/evolution", requested_pack),
    )
    production_gap_items = (
        "".join(
            f'<li><span class="pill">{escape(item["stage_label"].replace("_", " "))}</span> '
            f'<a href="{escape(_note_href(item["note_path"], requested_pack))}">{escape(item["title"])}</a>'
            f"<div class='muted'>Missing: {escape(item['detail'])}</div></li>"
            for item in payload["production"]["weak_points"]
        )
        or "<li class='muted'>No production-chain weak points surfaced.</li>"
    )
    signal_items = (
        "".join(
            f'<li><span class="pill">{escape(item["signal_type"])}</span> '
            f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div></li>"
            for item in payload["signals"]["items"]
        )
        or "<li class='muted'>No active signals surfaced.</li>"
    )
    priority_items = (
        "".join(
            f'<li><span class="pill">{escape(item["kind"].replace("_", " "))}</span> '
            f'<a href="{escape(item["path"])}">{escape(item["label"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div></li>"
            for item in payload["priorities"]
        )
        or "<li class='muted'>No urgent maintenance items surfaced.</li>"
    )
    stats_cards = [
        f"<div class='card'><h2>Objects Indexed</h2><p>{payload['objects']['count']}</p></div>",
        f"<div class='card'><h2>Signal Count</h2><p>{payload['signals']['count']}</p></div>",
        "<div class='card'><h2>Weak Point Count</h2>"
        f"<p>{payload['production']['weak_point_count']}</p></div>",
    ]
    if research_overview_supported:
        stats_cards[1:1] = [
            "<div class='card'><h2>Contradictions Open</h2>"
            f"<p>{payload['contradictions']['open_count']}</p></div>",
            f"<div class='card'><h2>Event Count</h2><p>{payload['events']['count']}</p></div>",
            "<div class='card'><h2>Stale Summary Count</h2>"
            f"<p>{payload['stale_summaries']['count']}</p></div>",
            "<div class='card'><h2>Evolution Candidates</h2>"
            f"<p>{payload['evolution']['candidate_count']}</p></div>",
        ]
    research_overview_card = (
        ""
        if research_overview_supported
        else (
            "<section class='card'><h2>Research Overview</h2>"
            f"<p class='muted'>{escape(str(research_overview.get('reason') or ''))}</p>"
            "</section>"
        )
    )
    left_sections = [
        f"<section class='card'><h2>Needs Attention Now</h2><ul class='list-tight'>{priority_items}</ul></section>",
        f"<section class='card'><h2>Recent Objects</h2><ul class='list-tight'>{object_items}</ul></section>",
    ]
    if research_overview_supported:
        left_sections.extend(
            [
                f"<section class='card'><h2><a href='{escape(_shell_href('/evolution', requested_pack))}'>Evolution</a></h2>{evolution_items}</section>",
                f"<section class='card'><h2><a href='{escape(payload['events']['browser_path'])}'>Recent Events</a></h2><ul class='list-tight'>{event_items}</ul></section>",
                f"<section class='card'><h2><a href='{escape(payload['stale_summaries']['browser_path'])}'>Stale Summaries</a></h2><ul class='list-tight'>{stale_summary_items}</ul></section>",
            ]
        )
    else:
        left_sections.append(research_overview_card)
    right_sections = [
        signals_surface_contract,
        f"<section class='card'><h2><a href='{escape(payload['signals']['browser_path'])}'>Signals</a></h2><ul class='list-tight'>{signal_items}</ul></section>",
        production_surface_contract,
        f"<section class='card'><h2><a href='{escape(payload['production']['browser_path'])}'>Production Weak Points</a></h2><ul class='list-tight'>{production_gap_items}</ul></section>",
    ]
    if research_overview_supported:
        right_sections.append(
            f"<section class='card'><h2><a href='{escape(payload['contradictions']['browser_path'])}'>Contradiction Queue</a></h2><ul class='list-tight'>{contradiction_items}</ul></section>"
        )
    right_sections.append(
        _render_review_history(payload["recent_review_actions"], title="Recent Review Actions")
    )
    dashboard_body = "".join(
        [
            "<section class='hero'>",
            "<h1>OVP Truth UI</h1>",
            "<p class='muted'>Read-only browser over <code>knowledge.db</code>. JSON APIs remain available at <code>/api/*</code>, including <code>/api/objects</code>.",
            f"{' Pack scope: ' + escape(requested_pack) + '.' if requested_pack else ''}</p>",
            "</section>",
            runtime_card,
            run_history_card,
            "<section class='grid stats'>",
            "".join(stats_cards),
            "</section>",
            "<section class='section-stack'>",
            "<section class='card'><h2>Workflow Map</h2><p class='muted'>Start here if you do not yet know which route to open. Each group maps one common operator workflow onto the current shell.</p></section>",
            workflow_groups_html,
            "<section class='card'><h2>Where To Start</h2><p class='muted'>Use the workflow map above to choose a route, then inspect the attention queues and knowledge surfaces below.</p></section>",
            orientation_assembly_contract,
            orientation_governance_contract,
            entry_sections_html,
            "</section>",
            "<section class='grid two-col'>",
            "<div class='section-stack'>",
            "".join(left_sections),
            "</div>",
            "<div class='section-stack'>",
            "".join(right_sections),
            "</div>",
            "</section>",
        ]
    )
    return _layout(
        "OVP Truth UI",
        dashboard_body,
        requested_pack=requested_pack,
        auto_refresh_seconds=10,
    )


def _render_objects_index(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    items = "".join(
        f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", "")))}">{escape(item["title"])}</a> '
        f'<span class="muted">({escape(item["object_id"])})</span></li>'
        for item in payload["items"]
    )
    return _layout(
        "Objects",
        (
            "<h1>Objects</h1>"
            + "<form method='get' action='/objects'>"
            + (
                f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                if requested_pack
                else ""
            )
            + f"<input type='text' name='q' value='{escape(query)}' placeholder='Search objects' /> "
            + "<button type='submit'>Search</button>"
            + "</form>"
            + f"<p class='muted'>{payload['count']} objects in current page."
            + (f" Pack scope: {escape(requested_pack)}." if requested_pack else "")
            + "</p>"
            + f"<section class='card'><ul class='list-tight'>{items}</ul></section>"
        ),
        requested_pack=requested_pack,
    )


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
    source_notes = (
        "".join(
            f'<li><a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a> '
            f"<span class='muted'>({escape(item['note_type'])})</span></li>"
            for item in payload["provenance"]["source_notes"]
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
    summary_text = payload["summary"]["summary_text"] if payload["summary"] else ""
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
        "<form method='post' action='/contradictions/resolve' class='link-row'>"
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
        "<form method='post' action='/summaries/rebuild' class='link-row'>"
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
                f"<a href='{escape(payload['links']['deep_dives_path'])}'>Source deep dives</a>",
                f"<a href='{escape(payload['links']['atlas_path'])}'>Atlas / MOC</a>",
            ]
        )
    stats_cards = [
        f"<div class='card'><h2>Claims</h2><p>{payload['claim_count']}</p></div>",
        f"<div class='card'><h2>Relations</h2><p>{payload['relation_count']}</p></div>",
    ]
    if research_shell_enabled:
        stats_cards.append(
            f"<div class='card'><h2>Contradictions</h2><p>{payload['contradiction_count']}</p></div>"
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
            "<section class='card'><h2>Context</h2><dl class='meta-list'>"
            f"<div><dt>Object Kind</dt><dd>{escape(payload['context']['object_kind'])}</dd></div>"
            f"<div><dt>Source Slug</dt><dd>{escape(payload['context']['source_slug'])}</dd></div>"
            f"<div><dt>Canonical Path</dt><dd>{canonical_path_html}</dd></div>"
            "</dl></section>",
            "<section class='card'><h2>Provenance</h2><dl class='meta-list'>"
            f"<div><dt>Evergreen Markdown</dt><dd>{evergreen_html}</dd></div>"
            f"<div><dt>Source Notes</dt><dd><ul class='list-tight'>{source_notes}</ul></dd></div>"
            f"<div><dt>Atlas / MOC</dt><dd><ul class='list-tight'>{mocs}</ul></dd></div>"
            "</dl></section>",
            "<section class='card'><h2>Production Chain</h2><dl class='meta-list'>"
            f"<div><dt>Chain Status</dt><dd>{escape(str(payload['production_chain'].get('chain_status') or ''))}</dd></div>"
            f"<div><dt>Missing Stages</dt><dd>{escape(', '.join(str(item).replace('_', ' ') for item in payload['production_chain'].get('missing_stages', [])) or 'None')}</dd></div>"
            f"<div><dt>Chain Summary</dt><dd>{escape(str(payload['production_chain'].get('chain_summary') or ''))}</dd></div>"
            f"<div><dt>Source Notes</dt><dd>{_render_named_note_links(payload['production_chain']['source_notes'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Source Deep Dives</dt><dd>{_render_named_note_links(payload['production_chain']['deep_dives'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Evergreen Note</dt><dd>{evergreen_html}</dd></div>"
            f"<div><dt>Atlas / MOC Reach</dt><dd>{_render_named_note_links(payload['production_chain']['atlas_pages'], requested_pack=requested_pack)}</dd></div>"
            "</dl></section>",
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
    return _layout(
        f"Object: {payload['object']['title']}",
        (
            f"<section class='hero'><h1>Object: {escape(payload['object']['title'])}</h1>"
            f"<p class='muted'>{escape(payload['object']['object_id'])}"
            + (f" Pack scope: {escape(requested_pack)}." if requested_pack else "")
            + "</p>"
            + f"<div class='link-row'>{''.join(hero_links)}</div></section>"
            + _render_compiled_sections(lead_sections)
            + operator_rail_card
            + assembly_contract_card
            + f"<nav class='subnav'>{section_nav}</nav>"
            + f"<section class='grid stats'>{''.join(stats_cards)}</section>"
            "<section class='grid two-col'>"
            "<div class='section-stack'>"
            f"<section id='summary' class='card'><h2>Compiled Summary</h2><p>{escape(summary_text)}</p></section>"
            f"{_render_compiled_sections(remaining_sections)}"
            f"<section id='claims' class='card'><h2>Claims</h2><ul class='list-tight'>{claims}</ul></section>"
            "</div>"
            "<div class='section-stack'>"
            f"{''.join(right_sections)}"
            "</div>"
            "</section>"
        ),
        requested_pack=requested_pack,
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
        "<form method='post' action='/summaries/rebuild' class='link-row'>"
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
        "<div class='link-row'>"
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
                f"<a href='{escape(payload['links']['deep_dives_path'])}'>Source deep dives</a>",
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
    return _layout(
        f"Topic: {payload['center']['title']}",
        (
            f"<section class='hero'><h1>Topic: {escape(payload['center']['title'])}</h1>"
            f"<p class='muted'>{payload['neighbor_count']} neighbors, {payload['edge_count']} edges."
            + (f" Pack scope: {escape(requested_pack)}." if requested_pack else "")
            + "</p>"
            + f"<div class='link-row'>{''.join(hero_links)}</div></section>"
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


def _render_events_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    assembly_contract_card = _render_assembly_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
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
                    + "<div class='link-row'>"
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
        "<form method='post' action='/summaries/rebuild' class='link-row'>"
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
        f"/contradictions?q={quote(query, safe='')}", requested_pack
    )
    contradiction_browser_path = _shell_href("/contradictions", requested_pack)
    contradiction_entry = (
        f"<div class='link-row'><a href='{escape(contradiction_query_path)}'>Review visible contradictions</a></div>"
        if payload["scoped_open_contradiction_ids"] and query
        else (
            f"<div class='link-row'><a href='{escape(contradiction_browser_path)}'>Review visible contradictions</a></div>"
            if payload["scoped_open_contradiction_ids"]
            else "<p class='muted'>No open contradictions in the visible event scope.</p>"
        )
    )
    return _layout(
        "Event Dossier",
        "".join(
            [
                "<h1>Event Dossier</h1>",
                "<p class='muted'>A timeline-oriented view over dated truth objects, not a separate event object model.</p>",
                "<form method='get' action='/events'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter events' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['cluster_count']} event clusters from {payload['event_count']} timeline rows across {len(payload['dates'])} dates.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                assembly_contract_card,
                (f"<nav class='subnav'>{section_nav}</nav>" if section_nav else ""),
                _render_compiled_sections(remaining_sections),
                f"<div class='link-row'>{type_breakdown}</div>",
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


def _render_atlas_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    limit_note = (
        f" Showing the most recent {payload['limit']} atlas pages in this browser window."
        if payload.get("is_limited")
        else ""
    )
    items = (
        "".join(
            "<li>"
            f'<a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
            + f" <span class='pill'>{item['member_count']} objects</span>"
            + f" <span class='pill'>{len(item['deep_dives'])} deep dives</span>"
            + f" <span class='pill'>{len(item['source_notes'])} source notes</span>"
            + (
                " <span class='muted'>"
                + ", ".join(
                    f'<a href="{escape(_object_href(member["object_id"], member.get("object_path", ""), requested_pack=requested_pack))}">{escape(member["title"])}</a>'
                    for member in item["members"]
                )
                + "</span>"
            )
            + (
                f"<div class='muted'>Preview: {escape(', '.join(item['preview_titles']))}</div>"
                if item["preview_titles"]
                else ""
            )
            + f"<div class='muted'>Source Notes: {_render_named_note_links(item['source_notes'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Deep Dives: {_render_named_note_links(item['deep_dives'], requested_pack=requested_pack)}</div>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li>None</li>"
    )
    return _layout(
        "Atlas / MOC Browser",
        "".join(
            [
                "<h1>Atlas / MOC Browser</h1>",
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


def _render_derivations_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    limit_note = (
        f" Showing the most recent {payload['limit']} deep dives in this browser window."
        if payload.get("is_limited")
        else ""
    )
    items = (
        "".join(
            "<li>"
            f'<a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
            + f" <span class='pill'>{item['derived_object_count']} derived objects</span>"
            + f" <span class='pill'>{len(item['source_notes'])} source notes</span>"
            + f" <span class='pill'>{len(item['atlas_pages'])} atlas pages</span>"
            + (
                " <span class='muted'>"
                + ", ".join(
                    f'<a href="{escape(_object_href(member["object_id"], member.get("object_path", ""), requested_pack=requested_pack))}">{escape(member["title"])}</a>'
                    for member in item["derived_objects"]
                )
                + "</span>"
            )
            + (
                f"<div class='muted'>Preview: {escape(', '.join(item['preview_titles']))}</div>"
                if item["preview_titles"]
                else ""
            )
            + f"<div class='muted'>Source Notes: {_render_named_note_links(item['source_notes'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Atlas / MOC Reach: {_render_named_note_links(item['atlas_pages'], requested_pack=requested_pack)}</div>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li>None</li>"
    )
    return _layout(
        "Deep Dive Derivations",
        "".join(
            [
                "<h1>Deep Dive Derivations</h1>",
                "<form method='get' action='/deep-dives'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter deep dives or objects' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} deep dive notes linked to indexed objects.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                "<section class='card'><h2>Contribution Summary</h2><p class='muted'>Each deep dive now shows upstream source notes and downstream Atlas reach, not just derived objects.</p></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_production_browser_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    surface_contract_card = _render_surface_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    limit_note = (
        f" Showing the most recent {payload['limit']} production-chain entries in this browser window."
        if payload.get("is_limited")
        else ""
    )
    items = (
        "".join(
            "<li>"
            f'<a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
            + f" <span class='pill'>{escape(item['stage_label'].replace('_', ' '))}</span>"
            + f" <span class='pill'>{escape(str(item['traceability'].get('chain_status') or ''))}</span>"
            + f" <span class='pill'>{item['traceability']['counts']['deep_dives']} deep dives</span>"
            + f" <span class='pill'>{item['traceability']['counts']['objects']} objects</span>"
            + f" <span class='pill'>{item['traceability']['counts']['atlas_pages']} atlas pages</span>"
            + f"<div class='muted'>Chain status: {escape(str(item['traceability'].get('chain_status') or ''))}</div>"
            + f"<div class='muted'>Missing stages: {escape(', '.join(str(value).replace('_', ' ') for value in item['traceability'].get('missing_stages', [])) or 'None')}</div>"
            + f"<div class='muted'>Chain summary: {escape(str(item['traceability'].get('chain_summary') or ''))}</div>"
            + f"<div class='muted'>Deep Dives: {_render_named_note_links(item['traceability']['deep_dives'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Objects: {_render_object_links(item['traceability']['objects'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Atlas / MOC Reach: {_render_named_note_links(item['traceability']['atlas_pages'], requested_pack=requested_pack)}</div>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No production chains found.</li>"
    )
    weak_points = (
        "".join(
            "<li>"
            f'<span class="pill">{escape(item["stage_label"].replace("_", " "))}</span> '
            f'<a href="{escape(_note_href(item["note_path"], requested_pack))}">{escape(item["title"])}</a>'
            f"<div class='muted'>Missing: {escape(item['detail'])}</div>"
            "</li>"
            for item in payload["weak_points"]
        )
        or "<li class='muted'>No production-chain weak points surfaced.</li>"
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    return _layout(
        "Production Browser",
        "".join(
            [
                "<h1>Production Browser</h1>",
                "<form method='get' action='/production'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter source notes, deep dives, objects, or atlas' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} production-chain entries. {payload['counts']['source_notes']} source notes and {payload['counts']['deep_dives']} deep dives.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                surface_contract_card,
                f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "",
                _render_compiled_sections(remaining_sections),
                "<section class='card'><h2>Chain Model</h2><p class='muted'>This browser shows the current upstream/downstream chain from traceable notes into deep dives, evergreen objects, and Atlas placement.</p></section>",
                f"<section class='card'><h2>Weak Points</h2><ul class='list-tight'>{weak_points}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_clusters_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    limit_note = (
        f" Showing the first {payload['limit']} graph clusters in this browser window."
        if payload.get("is_limited")
        else ""
    )
    kind_counts = (
        "".join(
            f"<span class='pill'>{escape(cluster_kind)}: {count}</span>"
            for cluster_kind, count in payload["cluster_kind_counts"].items()
        )
        or "<span class='muted'>None</span>"
    )
    items = (
        "".join(
            "<li>"
            f'<a href="{escape(item["detail_path"])}">{escape(item.get("display_title") or item["label"])}</a>'
            + f" <span class='pill'>{escape(item['cluster_kind'])}</span>"
            + f" <span class='pill'>{escape(item['priority_band'])}</span>"
            + f" <span class='pill'>{item['member_count']} objects</span>"
            + (
                " <span class='muted'>"
                + ", ".join(
                    f'<a href="{escape(member["path"])}">{escape(member["title"])}</a>'
                    for member in item["member_links"]
                )
                + "</span>"
            )
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
                "<form method='get' action='/clusters'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter clusters or members' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} graph clusters. Largest cluster has {payload['largest_cluster_size']} objects.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                f"{escape(limit_note)}</p>",
                f"<section class='card'><h2>Cluster Kinds</h2><div class='link-row'>{kind_counts}</div></section>",
                f"<section class='card'><h2>Model Notes</h2><ul class='list-tight'>{model_notes}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


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
            f"<section class='card'><h2>Cluster Synthesis</h2><ul class='list-tight'>{summary_bullets}</ul></section>"
            f"<section class='card'><h2>Structural Label</h2><p><strong>{escape(payload['structural_label']['title'])}</strong></p><p class='muted'>{escape(payload['structural_label']['reason'])}</p></section>"
            f"<section class='card'><h2>Relation Patterns</h2><ul class='list-tight'>{relation_patterns}</ul></section>"
            f"<section class='card'><h2>Review Pressure</h2><h3>Open Contradictions</h3><ul class='list-tight'>{open_contradictions}</ul><h3>Stale Summaries</h3><ul class='list-tight'>{stale_summaries}</ul></section>"
            f"<section class='card'><h2>Reading Routes</h2><ul class='list-tight'>{reading_routes}</ul></section>"
            f"<section class='card'><h2>Next Reading Route</h2>{next_read_route}</section>"
            f"<section class='card'><h2>Neighborhood Groups</h2><ul class='list-tight'>{related_cluster_groups}</ul></section>"
            f"<section class='card'><h2>Related Clusters</h2><ul class='list-tight'>{related_clusters}</ul></section>"
            f"<section class='card'><h2>Edge Kinds</h2><div class='link-row'>{edge_kind_counts}</div></section>"
            f"<section class='card'><h2>Object Kinds</h2><div class='link-row'>{object_kind_counts}</div></section>"
            f"<section class='card'><h2>Coverage</h2><p class='muted'>"
            f"{review_context['source_note_count']} source/deep-dive notes · "
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


def _render_evolution_browser_page(payload: dict) -> str:
    query = payload.get("query", "")
    status = payload.get("status", "all")
    selected_link_type = payload.get("link_type", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = _shell_href("/evolution", requested_pack)
    type_counts = (
        "".join(
            f"<span class='pill'>{escape(link_type)}: {count}</span>"
            for link_type, count in payload["type_counts"].items()
        )
        or "<span class='muted'>None</span>"
    )
    return _layout(
        "Evolution Browser",
        "".join(
            [
                "<h1>Evolution Browser</h1>",
                "<form method='get' action='/evolution' class='link-row'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter evolution links' />",
                "<select name='status'>",
                "".join(
                    f"<option value='{escape(option)}' {'selected' if status == option else ''}>{escape(option)}</option>"
                    for option in ("all", "candidate", "accepted", "rejected")
                ),
                "</select>",
                "<select name='link_type'>",
                "<option value=''>all link types</option>",
                "".join(
                    f"<option value='{escape(option)}' {'selected' if selected_link_type == option else ''}>{escape(option)}</option>"
                    for option in _EVOLUTION_LINK_TYPES
                ),
                "</select>",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} evolution records in the current view.</p>",
                f"<section class='card'><h2>Link Types</h2><div class='link-row'>{type_counts}</div></section>",
                f"<section class='card'><h2>Accepted Links</h2>{_render_evolution_links(payload['accepted_links'], empty_text='No accepted evolution links yet.')}</section>",
                f"<section class='card'><h2>Rejected Links</h2>{_render_evolution_links(payload['rejected_links'], empty_text='No rejected evolution links yet.')}</section>",
                f"<section class='card'><h2>Candidate Links</h2>{_render_evolution_candidates(payload['candidate_items'], reviewable=True, requested_pack=requested_pack, next_path=next_path)}</section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_candidate_items(payload: dict) -> str:
    requested_pack = str(payload.get("requested_pack") or "")
    next_path = _shell_href("/candidates", requested_pack)
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
            "<div class='link-row'>"
            "<form method='post' action='/candidates/review' class='link-row'>"
            f"{pack_hidden}"
            f"<input type='hidden' name='slug' value='{escape(slug)}' />"
            "<input type='hidden' name='action' value='promote' />"
            f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            "<button type='submit'>Promote</button>"
            "</form>"
            "<form method='post' action='/candidates/review' class='link-row'>"
            f"{pack_hidden}"
            f"<input type='hidden' name='slug' value='{escape(slug)}' />"
            "<input type='hidden' name='action' value='merge' />"
            f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            f"<input type='text' name='target_slug' value='{escape(default_target)}' placeholder='target slug' />"
            "<button type='submit'>Merge</button>"
            "</form>"
            "<form method='post' action='/candidates/review' class='link-row'>"
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


def _render_candidates_page(payload: dict) -> str:
    query = str(payload.get("query") or "")
    requested_pack = str(payload.get("requested_pack") or "")
    candidate_warning = str(payload.get("candidate_warning") or "")
    operator_rail = _render_operator_rail(payload)
    status_counts = " ".join(
        f"<span class='pill'>{escape(str(status))}: {escape(str(count))}</span>"
        for status, count in (payload.get("status_counts") or {}).items()
    )
    warning_card = (
        f"<section class='card warning'><h2>Review Warning</h2><p>{escape(candidate_warning)}</p></section>"
        if candidate_warning
        else ""
    )
    return _layout(
        "Candidate Workbench",
        "".join(
            [
                "<h1>Candidate Workbench</h1>",
                "<p class='muted'>Review registry candidates before they become canonical Evergreen objects. "
                "Promote creates an active note, merge rewrites candidate links into an existing object, "
                "and reject removes the pending candidate artifact.</p>",
                "<form method='get' action='/candidates' class='link-row'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter candidates' />",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{escape(str(payload.get('count') or 0))} candidate(s) in view.</p>",
                f"<section class='card'><h2>Status</h2><div class='link-row'>{status_counts}</div></section>",
                operator_rail,
                warning_card,
                f"<section class='card'><h2>Review Queue</h2>{_render_candidate_items(payload)}</section>",
            ]
        ),
        requested_pack=requested_pack,
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


def _render_signals_page(payload: dict) -> str:
    query = payload.get("query", "")
    selected_type = payload.get("signal_type", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/signals" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    surface_contract_card = _render_surface_contract_card(payload)
    governance_contract_card = _render_governance_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    options = ["", *sorted(payload["signal_type_explanations"].keys())]
    option_html = "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected_type else ''}>"
        f"{escape(option or 'all signal types')}</option>"
        for option in options
    )
    items = (
        "".join(
            "<li>"
            f'<span class="pill">{escape(item["signal_type"])}</span> '
            f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
            f"<div class='muted'>{escape(item['detail'])}</div>"
            + (
                f"<div class='muted'>Impact: {escape(str(item['impact_summary']['impact_label']))}</div>"
                if item.get("impact_summary", {}).get("impact_label")
                else ""
            )
            + (
                f"<div class='muted'>{escape(str(item['impact_summary']['impact_detail']))}</div>"
                if item.get("impact_summary", {}).get("impact_detail")
                else ""
            )
            + (
                f"<div class='muted'>Inbound capture: {escape(str(item['capture_summary']['summary']))}</div>"
                if item.get("capture_summary", {}).get("summary")
                else ""
            )
            + _render_signal_context_contract(item)
            + (
                "<div class='muted'>Recommended Action: "
                + f'<a href="{escape(item["recommended_action"]["path"])}">{escape(item["recommended_action"]["label"])}</a>'
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
                "<form method='post' action='/actions/enqueue' class='link-row'>"
                + f"<input type='hidden' name='signal_id' value='{escape(item['signal_id'])}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Queue action</button>"
                + "</form>"
                if item.get("recommended_action")
                and not item["recommended_action"].get("queue_status")
                else ""
            )
            + (
                "<div class='muted'>Downstream: "
                + ", ".join(
                    f'<a href="{escape(effect["path"])}">{escape(effect["label"])}</a>'
                    for effect in item["downstream_effects"]
                )
                + "</div>"
                if item["downstream_effects"]
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No active signals found.</li>"
    )
    explanations = "".join(
        f"<li><span class='pill'>{escape(signal_type)}</span> {escape(text)}</li>"
        for signal_type, text in payload["signal_type_explanations"].items()
    )
    return _layout(
        "Active Signals",
        "".join(
            [
                "<h1>Active Signals</h1>",
                "<form method='get' action='/signals' class='link-row'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Search signals' />",
                f"<select name='type'>{option_html}</select>",
                "<button type='submit'>Filter</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} active signals.",
                (
                    " "
                    + f"{payload.get('impact_counts', {}).get('productive', 0)} productive, "
                    + f"{payload.get('impact_counts', {}).get('waiting', 0)} waiting, "
                    + f"{payload.get('impact_counts', {}).get('failed', 0) + payload.get('impact_counts', {}).get('stalled', 0)} failed/stalled."
                ),
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                operator_rail_card,
                surface_contract_card,
                governance_contract_card,
                f"<section class='card'><h2>Signal Types</h2><ul class='list-tight'>{explanations}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_briefing_page(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    next_path = "/briefing" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
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
                "<form method='post' action='/actions/enqueue' class='link-row'>"
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
            f"<li><span class='pill'>{escape(str(bucket))}</span> "
            f"{_safe_count(count)}</li>"
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
                f"<p class='muted'>Generated at {escape(str(payload['generated_at']))}. "
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
                "<div class='link-row'>"
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
                "<form method='post' action='/actions/run-batch' class='link-row'>",
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


def _render_actions_page(payload: dict) -> str:
    query = payload.get("query", "")
    selected_status = payload.get("status", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = _shell_href("/actions", requested_pack)
    governance_contract_card = _render_governance_contract_card(payload)
    options = ["", "queued", "running", "succeeded", "failed", "blocked", "dismissed", "obsolete"]
    option_html = "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected_status else ''}>"
        f"{escape(option or 'all statuses')}</option>"
        for option in options
    )
    items = (
        "".join(
            "<li>"
            f"<span class='pill'>{escape(str(item['status']))}</span> "
            f"<span class='pill'>{escape(str(item['action_kind']))}</span> "
            + (
                " <span class='pill'>safe</span>"
                if item.get("safe_to_run")
                else " <span class='pill'>manual</span>"
            )
            + " "
            + f"{escape(str(item['title']))}"
            + (
                f"<div class='muted'>Target: {escape(str(item['target_ref']))}</div>"
                if item.get("target_ref")
                else ""
            )
            + (
                f"<div class='muted'>Created at {escape(str(item['created_at']))}</div>"
                if item.get("created_at")
                else ""
            )
            + (
                f"<div class='muted'>Retry count: {int(item.get('retry_count') or 0)}</div>"
                if item.get("retry_count") is not None
                else ""
            )
            + (
                f"<div class='muted'>Failure bucket: {escape(str(item['failure_bucket']))}</div>"
                if item.get("failure_bucket")
                else ""
            )
            + (
                f"<div class='muted'>Impact: {escape(str(item['impact_summary']['impact_label']))}</div>"
                if item.get("impact_summary", {}).get("impact_label")
                else ""
            )
            + (
                f"<div class='muted'>{escape(str(item['impact_summary']['impact_detail']))}</div>"
                if item.get("impact_summary", {}).get("impact_detail")
                else ""
            )
            + (
                f"<div class='muted'>Processor: {escape(str(item['processor_mode']))}</div>"
                if item.get("processor_mode")
                else ""
            )
            + (
                f"<div class='muted'>Resolver: {escape(str(item['resolution_kind']))}</div>"
                if item.get("resolution_kind")
                else ""
            )
            + (
                f"<div class='muted'>Dispatch: {escape(str(item['dispatch_mode']))}</div>"
                if item.get("dispatch_mode")
                else ""
            )
            + (
                f"<div class='muted'>Rule: {escape(str(item['resolver_rule_name']))}</div>"
                if item.get("resolver_rule_name")
                else ""
            )
            + (
                f"<div class='muted'>Governance contract: {escape(str(item['governance_provider_name']))} · {escape(str(item['governance_provider_pack']))}</div>"
                if item.get("governance_provider_name") or item.get("governance_provider_pack")
                else ""
            )
            + (
                f"<div class='muted'>Handler contract: {escape(str(item['handler_provider_name']))} · {escape(str(item['handler_provider_pack']))}</div>"
                if item.get("handler_provider_name") or item.get("handler_provider_pack")
                else ""
            )
            + (
                f"<div class='muted'>Processor contract: {escape(str(item['processor_provider_name']))} · {escape(str(item['processor_provider_pack']))}</div>"
                if item.get("processor_provider_name") or item.get("processor_provider_pack")
                else ""
            )
            + (
                f"<div class='muted'>Source signal: {'active' if item.get('source_signal_active') else 'inactive'}</div>"
                if "source_signal_active" in item
                else ""
            )
            + (
                f"<div class='muted'>Precondition: {escape(str(item['precondition_status']))}</div>"
                if item.get("precondition_status")
                else ""
            )
            + (
                f"<div class='muted'>Blocked reason: {escape(str(item['blocked_reason']))}</div>"
                if item.get("blocked_reason")
                else ""
            )
            + (
                f"<div class='muted'>Obsolete reason: {escape(str(item['obsolete_reason']))}</div>"
                if item.get("obsolete_reason")
                else ""
            )
            + (
                f"<div class='muted'>Last result: {escape(str(item['last_result_summary']))}</div>"
                if item.get("last_result_summary")
                else ""
            )
            + (
                f"<div class='muted'>Inputs: {escape(', '.join(str(value) for value in item['processor_inputs']))}</div>"
                if item.get("processor_inputs")
                else ""
            )
            + (
                f"<div class='muted'>Outputs: {escape(', '.join(str(value) for value in item['processor_outputs']))}</div>"
                if item.get("processor_outputs")
                else ""
            )
            + (
                f"<div class='muted'>Quality hooks: {escape(', '.join(str(value) for value in item['processor_quality_hooks']))}</div>"
                if item.get("processor_quality_hooks")
                else ""
            )
            + (
                "<form method='post' action='/actions/retry' class='link-row'>"
                + f"<input type='hidden' name='action_id' value='{escape(str(item['action_id']))}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Retry</button>"
                + "</form>"
                if item.get("status") in {"failed", "blocked", "obsolete"}
                else ""
            )
            + (
                "<form method='post' action='/actions/dismiss' class='link-row'>"
                + f"<input type='hidden' name='action_id' value='{escape(str(item['action_id']))}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Dismiss</button>"
                + "</form>"
                if item.get("status") in {"queued", "failed", "blocked", "obsolete", "running"}
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No queued actions yet.</li>"
    )
    return _layout(
        "Action Queue",
        "".join(
            [
                "<h1>Action Queue</h1>",
                "<p class='muted'>Asynchronous queue consumption is opt-in. Run <code>python -m ovp_pipeline.commands.run_actions --vault-dir &lt;vault&gt; --loop</code> or start the UI with <code>--with-action-worker</code> to spawn a detached worker process.</p>",
                "<form method='post' action='/actions/run-next' class='link-row'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run next queued action</button>",
                "</form>",
                "<form method='post' action='/actions/run-batch' class='link-row'>",
                "<input type='hidden' name='limit' value='5' />",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run 5 queued actions</button>",
                "</form>",
                "<form method='post' action='/actions/run-batch' class='link-row'>",
                "<input type='hidden' name='limit' value='5' />",
                "<input type='hidden' name='safe_only' value='1' />",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run 5 safe queued actions</button>",
                "</form>",
                "<form method='get' action='/actions' class='link-row'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Search actions' />",
                f"<select name='status'>{option_html}</select>",
                "<button type='submit'>Filter</button>",
                "</form>",
                (
                    f"<p class='muted'>{payload['count']} actions in the current execution surface. "
                    f"{payload.get('impact_counts', {}).get('productive', 0)} productive, "
                    f"{payload.get('impact_counts', {}).get('waiting', 0)} waiting, "
                    f"{payload.get('impact_counts', {}).get('failed', 0) + payload.get('impact_counts', {}).get('stalled', 0)} failed/stalled. "
                    f"{payload.get('queued_safe_count', 0)} queued safe actions. {payload.get('failed_count', 0)} failed actions.</p>"
                ),
                governance_contract_card,
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_contradictions_page(payload: dict) -> str:
    status = payload.get("status", "")
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/contradictions" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
    assembly_contract_card = _render_assembly_contract_card(payload)
    operator_rail_card = _render_operator_rail(payload)
    lead_sections, remaining_sections = _split_lead_compiled_sections(
        payload.get("compiled_sections", [])
    )
    detection_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["detection_notes"])
    scope_summary = payload["scope_summary"]
    scope_summary_items = (
        f"<li>Items: {scope_summary['item_count']}</li>"
        f"<li>Objects in scope: {scope_summary['object_count']}</li>"
        f"<li>Source notes in scope: {scope_summary['source_note_count']}</li>"
    )
    detection_contract = payload["detection_contract"]
    detection_contract_items = (
        f"<li>Model: {escape(detection_contract['model'])}</li>"
        + f"<li>Confidence: {escape(detection_contract['confidence'])}</li>"
        + f"<li>Polarity semantics: {escape(str(detection_contract.get('polarity_semantics') or ''))}</li>"
        + f"<li>Evidence semantics: {escape(str(detection_contract.get('evidence_semantics') or ''))}</li>"
        + "".join(
            f"<li>Status bucket {escape(str(bucket))}: {count}</li>"
            for bucket, count in detection_contract["status_buckets"].items()
        )
        + "".join(
            f"<li>Status {escape(str(status_name))}: {escape(text)}</li>"
            for status_name, text in detection_contract["status_explanations"].items()
        )
    )
    section_nav = "".join(
        f'<a href="{escape(str(item["href"]))}">{escape(str(item["label"]))}</a>'
        for item in payload.get("section_nav", [])
    )
    items = (
        "".join(
            "<li>"
            + (
                f"<label><input type='checkbox' form='contradiction-batch-form' name='contradiction_id' value='{escape(item['contradiction_id'])}' /> batch</label> "
                if item["status"] == "open"
                else ""
            )
            + f"<span class='pill'>{escape(item['status'])}</span>{escape(item['subject_key'])}"
            + f" <span class='muted'>[{escape(item['detection_model'])} / {escape(item['detection_confidence'])} / {escape(item['status_bucket'])}]</span>"
            + f"<div class='muted'>Status Meaning: {escape(item['status_explanation'])}</div>"
            + (
                "<div class='muted'>Scope Summary: "
                + f"{item['scope_summary']['object_count']} objects, "
                + f"{item['scope_summary']['positive_claim_count']} positive claims, "
                + f"{item['scope_summary']['negative_claim_count']} negative claims, "
                + f"{item['scope_summary']['source_note_count']} source notes"
                + "</div>"
            )
            + (
                " <span class='muted'>"
                + ", ".join(
                    f'<a href="{escape(link["path"])}">{escape(item["object_titles"].get(link["object_id"], link["object_id"]))}</a>'
                    for link in item["object_links"]
                )
                + "</span>"
                if item["object_links"]
                else ""
            )
            + f"<div class='muted'>Source Notes: {_render_named_note_links(item['provenance']['source_notes'], requested_pack=requested_pack)}</div>"
            + f"<div class='muted'>Atlas / MOC: {_render_named_note_links(item['provenance']['mocs'], requested_pack=requested_pack)}</div>"
            + (
                "<details><summary>Ranked Evidence</summary><ol class='list-tight'>"
                + "".join(
                    f"<li>#{evidence['rank']} {escape(evidence['polarity'])}: {escape(evidence['quote_text'])} "
                    + f"<span class='muted'>({escape(evidence['object_title'])} / {escape(evidence['source_slug'])} / {escape(evidence['evidence_kind'])})</span></li>"
                    for evidence in item["ranked_evidence"]
                )
                + "</ol></details>"
                if item["ranked_evidence"]
                else ""
            )
            + (
                "<details><summary>Claim Evidence</summary><ul class='list-tight'>"
                + "".join(
                    "<li>Positive: "
                    + f"{escape(claim['claim_text'])} <span class='muted'>({escape(claim['object_title'])})</span>"
                    + (
                        "<ul class='list-tight'>"
                        + "".join(
                            f"<li>{escape(evidence['evidence_kind'])}: {escape(evidence['quote_text'])} <span class='muted'>({escape(evidence['source_slug'])})</span></li>"
                            for evidence in claim["evidence"]
                        )
                        + "</ul>"
                        if claim["evidence"]
                        else ""
                    )
                    + "</li>"
                    for claim in item["positive_claims"]
                )
                + "".join(
                    "<li>Negative: "
                    + f"{escape(claim['claim_text'])} <span class='muted'>({escape(claim['object_title'])})</span>"
                    + (
                        "<ul class='list-tight'>"
                        + "".join(
                            f"<li>{escape(evidence['evidence_kind'])}: {escape(evidence['quote_text'])} <span class='muted'>({escape(evidence['source_slug'])})</span></li>"
                            for evidence in claim["evidence"]
                        )
                        + "</ul>"
                        if claim["evidence"]
                        else ""
                    )
                    + "</li>"
                    for claim in item["negative_claims"]
                )
                + "</ul></details>"
            )
            + f"<div class='muted'>Tension Summary: {escape(str(item.get('tension_summary') or ''))}</div>"
            + (
                "<details><summary>Review History</summary><ul class='list-tight'>"
                + "".join(
                    f"<li>{escape(str(history['timestamp']))} <span class='pill'>{escape(str(history['event_type']))}</span>"
                    + (
                        f"<div class='muted'>Status: {escape(str(history['status']))}</div>"
                        if history.get("status")
                        else ""
                    )
                    + (
                        f"<div class='muted'>Note: {escape(str(history['note']))}</div>"
                        if history.get("note")
                        else ""
                    )
                    + "</li>"
                    for history in item["review_history"]
                )
                + "</ul></details>"
                if item["review_history"]
                else ""
            )
            + (
                f"<div class='muted'>Resolution Note: {escape(item['resolution_note'])}</div>"
                if item.get("resolution_note")
                else ""
            )
            + (
                f"<div class='muted'>Resolved At: {escape(item['resolved_at'])}</div>"
                if item.get("resolved_at")
                else ""
            )
            + (
                "<form method='post' action='/contradictions/resolve' class='link-row'>"
                f"<input type='hidden' name='contradiction_id' value='{escape(item['contradiction_id'])}' />"
                f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                "<select name='status'>"
                "<option value='resolved_keep_positive'>resolved_keep_positive</option>"
                "<option value='resolved_keep_negative'>resolved_keep_negative</option>"
                "<option value='dismissed'>dismissed</option>"
                "<option value='needs_human'>needs_human</option>"
                "</select>"
                "<input type='text' name='note' placeholder='Resolution note' />"
                "<label><input type='checkbox' name='rebuild_summaries' value='1' /> rebuild summaries</label>"
                "<button type='submit'>Resolve</button>"
                "</form>"
                if item["status"] == "open"
                else ""
            )
            + "</li>"
            for item in payload["items"]
        )
        or f"<li>{escape(payload['empty_state'])}</li>"
    )
    return _layout(
        "Contradictions",
        "".join(
            [
                "<h1>Contradictions</h1>",
                "<form method='get' action='/contradictions'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                "<select name='status'>",
                f"<option value=''{' selected' if not status else ''}>all</option>",
                f"<option value='open'{' selected' if status == 'open' else ''}>open</option>",
                f"<option value='resolved'{' selected' if status == 'resolved' else ''}>resolved</option>",
                "</select> ",
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter contradictions' /> ",
                "<button type='submit'>Filter</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} records, {payload['open_count']} open.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                assembly_contract_card,
                f"<nav class='subnav'>{section_nav}</nav>" if section_nav else "",
                _render_compiled_sections(remaining_sections),
                f"<section class='card'><h2>Detection Notes</h2><ul class='list-tight'>{detection_notes}</ul></section>",
                "<section class='card'>",
                "<h2>Batch Resolve</h2>",
                "<form id='contradiction-batch-form' method='post' action='/contradictions/resolve' class='link-row'>",
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<select name='status'>",
                "<option value='resolved_keep_positive'>resolved_keep_positive</option>",
                "<option value='resolved_keep_negative'>resolved_keep_negative</option>",
                "<option value='dismissed'>dismissed</option>",
                "<option value='needs_human'>needs_human</option>",
                "</select>",
                "<input type='text' name='note' placeholder='Resolution note for selected rows' />",
                "<label><input type='checkbox' name='rebuild_summaries' value='1' /> rebuild summaries</label>",
                "<button type='submit'>Resolve Selected</button>",
                "</form>",
                "</section>",
                f"<section class='card'><h2>Scope Summary</h2><ul class='list-tight'>{scope_summary_items}</ul></section>",
                f"<section class='card'><h2>Detection Contract</h2><ul class='list-tight'>{detection_contract_items}</ul></section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
    )


def _render_stale_summaries_page(payload: dict) -> str:
    query = payload.get("query", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/summaries" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    detection_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["detection_notes"])
    items = (
        "".join(
            "<li>"
            f"<label><input type='checkbox' form='summary-batch-form' name='object_id' value='{escape(item['object_id'])}' /> batch</label> "
            f'<a href="{escape(item["object_path"])}">{escape(item["title"])}</a> '
            f"<span class='muted'>({escape(item['object_id'])})</span>"
            f"<div class='muted'>Summary: {escape(item['summary_text'])}</div>"
            f"<div class='muted'>Outgoing relations: {item['outgoing_relation_count']}</div>"
            + (
                f"<div class='muted'>Latest event date: {escape(item['latest_event_date'])}</div>"
                if item["latest_event_date"]
                else ""
            )
            + "<ul class='list-tight'>"
            + "".join(f"<li>{escape(reason)}</li>" for reason in item["reason_texts"])
            + "</ul>"
            + (
                "<details><summary>Review History</summary><ul class='list-tight'>"
                + "".join(
                    f"<li>{escape(str(history['timestamp']))} <span class='pill'>{escape(str(history['event_type']))}</span>"
                    + (
                        f"<div class='muted'>Rebuilt: {escape(', '.join(str(v) for v in history['rebuilt_object_ids']))}</div>"
                        if history.get("rebuilt_object_ids")
                        else ""
                    )
                    + "</li>"
                    for history in item["review_history"]
                )
                + "</ul></details>"
                if item["review_history"]
                else ""
            )
            + "<form method='post' action='/summaries/rebuild' class='link-row'>"
            + f"<input type='hidden' name='object_id' value='{escape(item['object_id'])}' />"
            + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            + "<button type='submit'>Rebuild Summary</button>"
            + "</form>"
            + "</li>"
            for item in payload["items"]
        )
        or "<li class='muted'>No stale summaries detected.</li>"
    )
    return _layout(
        "Stale Summaries",
        "".join(
            [
                "<h1>Stale Summaries</h1>",
                "<form method='get' action='/summaries'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter stale summaries' /> ",
                "<button type='submit'>Filter</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} stale summary candidates.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                f"{_render_review_context_card(payload['review_context'])}",
                f"{_render_review_history(payload['review_history'])}",
                f"<section class='card'><h2>Detection Notes</h2><ul class='list-tight'>{detection_notes}</ul></section>",
                "<section class='card'>",
                "<h2>Batch Rebuild</h2>",
                "<form id='summary-batch-form' method='post' action='/summaries/rebuild' class='link-row'>",
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Rebuild Selected</button>",
                "</form>",
                "</section>",
                f"<section class='card'><ul class='list-tight'>{items}</ul></section>",
            ]
        ),
        requested_pack=requested_pack,
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
        weekly_html = "<p class='empty'>No reuse events recorded yet.</p>"

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


def _render_reuse_report_page(payload: dict) -> str:
    fragment = _render_reuse_report_fragment(payload)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Reuse Report</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}"
        "th{background:#f4f4f4}"
        "code{background:#f0f0f0;padding:0 .2rem;border-radius:3px}"
        ".empty{color:#888;font-style:italic}"
        "</style></head><body>"
        f"{fragment}"
        "</body></html>"
    )


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


def _render_open_questions_fragment(payload: dict) -> str:
    rows = payload.get("questions") or []
    if not rows:
        return "<section class='open-questions'><p class='empty'>No open questions yet.</p></section>"
    items = "".join(
        f"<li><strong>{escape(str(row.get('question') or ''))}</strong>"
        f" <small>{escape(str(row.get('ts') or ''))}</small></li>"
        for row in rows
    )
    return f"<section class='open-questions'><ul>{items}</ul></section>"


def _render_open_questions_page(payload: dict) -> str:
    fragment = _render_open_questions_fragment(payload)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Open Questions</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem}"
        ".empty{color:#888;font-style:italic}"
        "li{margin:.4rem 0}small{color:#888;margin-left:.5rem}"
        "</style></head><body>"
        f"{fragment}</body></html>"
    )


def _render_writing_prompts_fragment(payload: dict) -> str:
    body = str(payload.get("body") or "").strip()
    if not body:
        return "<section class='writing-prompts'><p class='empty'>No writing prompts captured yet.</p></section>"
    return f"<section class='writing-prompts'><pre>{escape(body)}</pre></section>"


def _render_writing_prompts_page(payload: dict) -> str:
    fragment = _render_writing_prompts_fragment(payload)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Writing Prompts</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap;background:#f7f7f7;padding:1rem;border-radius:4px}"
        ".empty{color:#888;font-style:italic}"
        "</style></head><body>"
        f"{fragment}</body></html>"
    )


_SHELL_BODY_OPEN = '<div class="shell-body">'


# Bridge script appended to every fragment so iframe clicks on `/object?id=...`
# anchors become `postMessage({type:'select_object', id})` calls — the
# Workbench parent listens for this and re-points the object pane without a
# full page reload.
_FRAGMENT_BRIDGE_SCRIPT = (
    "<script>(function(){"
    "if(window.parent===window)return;"
    "document.addEventListener('click',function(ev){"
    "var a=ev.target&&ev.target.closest&&ev.target.closest('a[href]');"
    "if(!a)return;"
    "var href=a.getAttribute('href')||'';"
    "var m=href.match(/\\/object(?:\\/fragment)?\\?(?:[^#]*&)?id=([^&#]+)/);"
    "if(!m)return;"
    "ev.preventDefault();"
    "window.parent.postMessage({type:'select_object',id:decodeURIComponent(m[1])},'*');"
    "},true);"
    "})();</script>"
)


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


def _render_pulse_fragment() -> str:
    """Phase 37 — self-contained Pulse SSE consumer.

    The fragment opens an ``EventSource`` against ``/pulse/stream`` and
    appends frames into a tight scrolling list. Designed for the Workbench
    bottom pane; works equally well as a standalone iframe.
    """
    return (
        "<section class='pulse'>"
        "<style>"
        ".pulse{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;}"
        ".pulse ul{list-style:none;margin:0;padding:.4rem;max-height:240px;overflow:auto;"
        "background:#0e1116;color:#d6deeb;border-radius:6px;}"
        ".pulse li{margin:.1rem 0;white-space:pre-wrap;}"
        ".pulse li .ts{color:#7c8593;margin-right:.4rem;}"
        ".pulse li .et{color:#82aaff;margin-right:.4rem;}"
        ".pulse li .pk{color:#c792ea;margin-right:.4rem;}"
        ".pulse .empty{color:#7c8593;padding:.4rem;}"
        "</style>"
        "<ul id='pulse-feed'><li class='empty'>Waiting for events…</li></ul>"
        "<script>(function(){"
        "var feed=document.getElementById('pulse-feed');"
        "var empty=feed.querySelector('.empty');"
        "var src=new EventSource('/pulse/stream');"
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


def _render_pulse_page() -> str:
    fragment = _render_pulse_fragment()
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Pulse</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:1080px;margin:1.5rem auto;padding:0 1rem}"
        "h1{margin-bottom:.4rem}p.muted{color:#71675d}"
        "</style></head><body>"
        "<h1>Pulse</h1>"
        "<p class='muted'>Live tail of <code>60-Logs/*.jsonl</code> (pipeline, reuse, "
        "evidence, open-questions). Polls once per second.</p>"
        f"{fragment}</body></html>"
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
    cand_src = "/candidates/fragment" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    actions_src = "/actions/fragment" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    briefing_src = "/briefing/fragment" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    object_src = (
        f"/object/fragment?id={quote(object_id, safe='')}"
        + (f"&pack={quote(requested_pack, safe='')}" if requested_pack else "")
        if object_id
        else "/objects" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    )
    pulse_src = "/pulse/fragment"

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1' />"
        "<title>Workbench</title>"
        "<style>"
        "*{box-sizing:border-box}"
        "body{margin:0;font-family:ui-sans-serif,system-ui,sans-serif;background:#f7f6f2;color:#1f1a17}"
        "header{padding:.6rem 1rem;border-bottom:1px solid #e7e1d8;display:flex;gap:1rem;align-items:center}"
        "header h1{font-size:1rem;margin:0;color:#9f4f24}"
        "header .meta{color:#71675d;font-size:.85rem}"
        ".grid{display:grid;height:calc(100vh - 56px);"
        "grid-template-columns:280px 1fr 280px;"
        "grid-template-rows:1fr 1fr 220px;"
        "gap:1px;background:#e7e1d8;}"
        ".pane{background:#fffdfa;overflow:auto}"
        ".pane iframe{width:100%;height:100%;border:0;display:block}"
        ".pane.cand{grid-row:1/3}"
        ".pane.obj{grid-row:1/2}"
        ".pane.brief{grid-row:2/3}"
        ".pane.act{grid-row:1/3}"
        ".pane.pulse{grid-column:1/4;grid-row:3/4}"
        "</style></head><body>"
        "<header>"
        "<h1>Workbench</h1>"
        f"<span class='meta'>object: <code id='wb-object'>{escape(object_id) or '∅'}</code></span>"
        f"<span class='meta'>pack: <code>{escape(requested_pack) or '∅'}</code></span>"
        "<a href='/' style='margin-left:auto;color:#9f4f24;text-decoration:none'>← Shell</a>"
        "</header>"
        "<div class='grid'>"
        f"<section class='pane cand'><iframe id='pane-cand' src='{escape(cand_src)}'></iframe></section>"
        f"<section class='pane obj'><iframe id='pane-obj' src='{escape(object_src)}'></iframe></section>"
        f"<section class='pane brief'><iframe id='pane-brief' src='{escape(briefing_src)}'></iframe></section>"
        f"<section class='pane act'><iframe id='pane-act' src='{escape(actions_src)}'></iframe></section>"
        f"<section class='pane pulse'><iframe id='pane-pulse' src='{escape(pulse_src)}'></iframe></section>"
        "</div>"
        "<script>(function(){"
        f"var pack={json.dumps(requested_pack)};"
        "function selectObject(id){"
        "var packQs=pack?'&pack='+encodeURIComponent(pack):'';"
        "var packQsLead=pack?'?pack='+encodeURIComponent(pack):'';"
        "document.getElementById('pane-obj').src=id"
        "?'/object/fragment?id='+encodeURIComponent(id)+packQs"
        ":'/objects'+packQsLead;"
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


def create_server(
    vault_dir: Path | str, *, host: str = "127.0.0.1", port: int = 8787
) -> ThreadingHTTPServer:
    resolved_vault = resolve_vault_dir(vault_dir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # pragma: no cover
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            try:
                if path == "/":
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_runtime_home_payload(resolved_vault, pack_name=pack_name)
                    self._write_html(_render_dashboard(payload))
                    return
                if path == "/api/objects":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_objects_index_payload(
                            resolved_vault,
                            limit=limit,
                            offset=offset,
                            query=q,
                            pack_name=pack_name,
                        )
                    )
                    return
                if path == "/api/runtime":
                    try:
                        self._write_json(get_runtime_status(resolved_vault))
                    except (OSError, sqlite3.Error) as exc:
                        self._write_json(
                            {
                                "active_count": 0,
                                "stale_count": 0,
                                "active_run": None,
                                "stale_runs": [],
                                "error": "runtime_status_unavailable",
                                "detail": str(exc),
                            },
                            status=503,
                        )
                    return
                if path == "/objects":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_objects_index_payload(
                        resolved_vault,
                        limit=limit,
                        offset=offset,
                        query=q,
                        pack_name=pack_name,
                    )
                    self._write_html(_render_objects_index(payload))
                    return
                if path == "/api/search":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    try:
                        page = max(1, int(query.get("page", ["1"])[0]))
                    except (TypeError, ValueError):
                        page = 1
                    self._write_json(
                        build_search_payload(
                            resolved_vault, query=q, pack_name=pack_name, page=page
                        )
                    )
                    return
                if path == "/search":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    try:
                        page = max(1, int(query.get("page", ["1"])[0]))
                    except (TypeError, ValueError):
                        page = 1
                    payload = build_search_payload(
                        resolved_vault, query=q, pack_name=pack_name, page=page
                    )
                    self._write_html(_render_search_page(payload))
                    return
                if path == "/api/briefing":
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(build_briefing_payload(resolved_vault, pack_name=pack_name))
                    return
                if path in {"/briefing", "/briefing/fragment"}:
                    pack_name = query.get("pack", [""])[0] or None
                    page = _render_briefing_page(
                        build_briefing_payload(resolved_vault, pack_name=pack_name)
                    )
                    if path == "/briefing/fragment":
                        self._write_html(_fragment_from_page(page))
                    else:
                        self._write_html(page)
                    return
                if path == "/api/signals":
                    q = query.get("q", [""])[0]
                    signal_type = query.get("type", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_signal_browser_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            signal_type=signal_type,
                            query=q,
                        )
                    )
                    return
                if path == "/signals":
                    q = query.get("q", [""])[0]
                    signal_type = query.get("type", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_signal_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        signal_type=signal_type,
                        query=q,
                    )
                    self._write_html(_render_signals_page(payload))
                    return
                if path == "/api/candidates":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/candidates", api=True
                    ):
                        return
                    self._write_json(
                        build_candidate_browser_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            query=q,
                        )
                    )
                    return
                if path in {"/candidates", "/candidates/fragment"}:
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    candidate_warning = query.get("candidate_warning", [""])[0]
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/candidates", api=False
                    ):
                        return
                    payload = build_candidate_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        query=q,
                    )
                    payload["candidate_warning"] = candidate_warning
                    page = _render_candidates_page(payload)
                    if path == "/candidates/fragment":
                        self._write_html(_fragment_from_page(page))
                    else:
                        self._write_html(page)
                    return
                if path == "/api/evolution":
                    q = query.get("q", [""])[0]
                    status = query.get("status", ["all"])[0] or "all"
                    link_type = query.get("link_type", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/evolution", api=True
                    ):
                        return
                    self._write_json(
                        build_evolution_browser_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            query=q,
                            status=status,
                            link_type=link_type,
                        )
                    )
                    return
                if path == "/evolution":
                    q = query.get("q", [""])[0]
                    status = query.get("status", ["all"])[0] or "all"
                    link_type = query.get("link_type", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/evolution", api=False
                    ):
                        return
                    payload = build_evolution_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        query=q,
                        status=status,
                        link_type=link_type,
                    )
                    self._write_html(_render_evolution_browser_page(payload))
                    return
                if path == "/api/object":
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_object_page_payload(resolved_vault, object_id, pack_name=pack_name)
                    )
                    return
                if path in {"/object", "/object/fragment"}:
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_object_page_payload(
                        resolved_vault, object_id, pack_name=pack_name
                    )
                    page = _render_object_page(payload)
                    if path == "/object/fragment":
                        self._write_html(_fragment_from_page(page))
                    else:
                        self._write_html(page)
                    return
                if path == "/api/topic":
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_topic_overview_payload(resolved_vault, object_id, pack_name=pack_name)
                    )
                    return
                if path == "/topic":
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_topic_overview_payload(
                        resolved_vault, object_id, pack_name=pack_name
                    )
                    self._write_html(_render_topic_page(payload))
                    return
                if path == "/api/events":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/events", api=True
                    ):
                        return
                    self._write_json(
                        build_event_dossier_payload(resolved_vault, pack_name=pack_name, query=q)
                    )
                    return
                if path == "/events":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/events", api=False
                    ):
                        return
                    payload = build_event_dossier_payload(
                        resolved_vault, pack_name=pack_name, query=q
                    )
                    self._write_html(_render_events_page(payload))
                    return
                if path == "/api/atlas":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/atlas", api=True
                    ):
                        return
                    self._write_json(
                        build_atlas_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    )
                    return
                if path == "/atlas":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/atlas", api=False
                    ):
                        return
                    payload = build_atlas_browser_payload(
                        resolved_vault, pack_name=pack_name, query=q
                    )
                    self._write_html(_render_atlas_page(payload))
                    return
                if path == "/api/deep-dives":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/deep-dives", api=True
                    ):
                        return
                    self._write_json(
                        build_derivation_browser_payload(
                            resolved_vault, pack_name=pack_name, query=q
                        )
                    )
                    return
                if path == "/deep-dives":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/deep-dives", api=False
                    ):
                        return
                    payload = build_derivation_browser_payload(
                        resolved_vault, pack_name=pack_name, query=q
                    )
                    self._write_html(_render_derivations_page(payload))
                    return
                if path == "/api/production":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_production_browser_payload(
                            resolved_vault, pack_name=pack_name, query=q
                        )
                    )
                    return
                if path == "/production":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_production_browser_payload(
                        resolved_vault, pack_name=pack_name, query=q
                    )
                    self._write_html(_render_production_browser_page(payload))
                    return
                if path == "/api/clusters":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/clusters", api=True
                    ):
                        return
                    self._write_json(
                        build_cluster_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    )
                    return
                if path == "/clusters":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/clusters", api=False
                    ):
                        return
                    payload = build_cluster_browser_payload(
                        resolved_vault, pack_name=pack_name, query=q
                    )
                    self._write_html(_render_clusters_page(payload))
                    return
                if path == "/api/cluster":
                    cluster_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/cluster", api=True
                    ):
                        return
                    self._write_json(
                        build_cluster_detail_payload(
                            resolved_vault, cluster_id=cluster_id, pack_name=pack_name
                        )
                    )
                    return
                if path == "/cluster":
                    cluster_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/cluster", api=False
                    ):
                        return
                    payload = build_cluster_detail_payload(
                        resolved_vault, cluster_id=cluster_id, pack_name=pack_name
                    )
                    self._write_html(_render_cluster_detail_page(payload))
                    return
                if path == "/api/actions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_action_queue_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            status=status,
                            query=q,
                        )
                    )
                    return
                if path in {"/actions", "/actions/fragment"}:
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_action_queue_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        status=status,
                        query=q,
                    )
                    page = _render_actions_page(payload)
                    if path == "/actions/fragment":
                        self._write_html(_fragment_from_page(page))
                    else:
                        self._write_html(page)
                    return
                if path == "/api/summaries":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/summaries", api=True
                    ):
                        return
                    self._write_json(
                        build_stale_summary_browser_payload(
                            resolved_vault, pack_name=pack_name, query=q
                        )
                    )
                    return
                if path == "/summaries":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/summaries", api=False
                    ):
                        return
                    payload = build_stale_summary_browser_payload(
                        resolved_vault, pack_name=pack_name, query=q
                    )
                    self._write_html(_render_stale_summaries_page(payload))
                    return
                if path == "/note":
                    relative_path = self._required(query, "path")
                    pack_name = query.get("pack", [""])[0] or None
                    _, markdown = _read_vault_note(resolved_vault, relative_path)
                    payload = build_note_page_payload(
                        resolved_vault, note_path=relative_path, pack_name=pack_name
                    )
                    self._write_html(
                        _render_note_page(resolved_vault, relative_path, markdown, payload)
                    )
                    return
                if path == "/asset":
                    relative_path = self._required(query, "path")
                    body, content_type = _read_vault_asset(resolved_vault, relative_path)
                    self._write_bytes(body, content_type)
                    return
                if path == "/api/contradictions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/contradictions", api=True
                    ):
                        return
                    self._write_json(
                        build_contradiction_browser_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            status=status,
                            query=q,
                        )
                    )
                    return
                if path == "/contradictions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/contradictions", api=False
                    ):
                        return
                    payload = build_contradiction_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        status=status,
                        query=q,
                    )
                    self._write_html(_render_contradictions_page(payload))
                    return
                if path in {"/reuse", "/reuse/fragment", "/api/reuse"}:
                    # Default to the compatibility pack so the panel matches the
                    # emitter default in query_tool (which writes events with
                    # pack=DEFAULT_PACK_NAME unless --pack overrides).
                    pack_name = query.get("pack", [""])[0] or DEFAULT_PACK_NAME
                    payload = build_reuse_report_payload(resolved_vault, pack=pack_name)
                    if path == "/api/reuse":
                        self._write_json(payload)
                    elif path == "/reuse/fragment":
                        self._write_html(_render_reuse_report_fragment(payload))
                    else:
                        self._write_html(_render_reuse_report_page(payload))
                    return
                if path in {"/open-questions", "/open-questions/fragment", "/api/open-questions"}:
                    payload = _build_open_questions_payload(resolved_vault)
                    if path == "/api/open-questions":
                        self._write_json(payload)
                    elif path == "/open-questions/fragment":
                        self._write_html(_render_open_questions_fragment(payload))
                    else:
                        self._write_html(_render_open_questions_page(payload))
                    return
                if path in {"/writing-prompts", "/writing-prompts/fragment", "/api/writing-prompts"}:
                    payload = _build_writing_prompts_payload(resolved_vault)
                    if path == "/api/writing-prompts":
                        self._write_json(payload)
                    elif path == "/writing-prompts/fragment":
                        self._write_html(_render_writing_prompts_fragment(payload))
                    else:
                        self._write_html(_render_writing_prompts_page(payload))
                    return
                if path == "/workbench":
                    object_id = query.get("object_id", [""])[0]
                    pack_name = query.get("pack", [""])[0] or ""
                    self._write_html(
                        _render_workbench_page(
                            object_id=object_id, requested_pack=pack_name
                        )
                    )
                    return
                if path == "/pulse":
                    self._write_html(_render_pulse_page())
                    return
                if path == "/pulse/fragment":
                    self._write_html(_render_pulse_fragment())
                    return
                if path == "/pulse/stream":
                    raw_max = query.get("max_polls", [""])[0]
                    raw_interval = query.get("poll_interval", [""])[0]
                    max_polls = int(raw_max) if raw_max else None
                    poll_interval = float(raw_interval) if raw_interval else 1.0
                    if poll_interval <= 0:
                        self.send_error(400, "poll_interval must be > 0")
                        return
                    if max_polls is not None and max_polls < 0:
                        self.send_error(400, "max_polls must be >= 0")
                        return
                    self._stream_pulse_sse(
                        poll_interval=poll_interval, max_polls=max_polls
                    )
                    return
                self.send_error(404, "Not Found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                form = self._read_form()
                if path == "/api/contradictions/resolve":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/contradictions/resolve", api=True
                    ):
                        return
                    self._write_json(self._resolve_contradiction_action(form))
                    return
                if path == "/contradictions/resolve":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/contradictions/resolve", api=False
                    ):
                        return
                    self._resolve_contradiction_action(form)
                    self._redirect(
                        self._form_first(form, "next").strip() or "/contradictions?status=resolved"
                    )
                    return
                if path == "/api/summaries/rebuild":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/summaries/rebuild", api=True
                    ):
                        return
                    self._write_json(self._rebuild_summary_action(form))
                    return
                if path == "/summaries/rebuild":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/summaries/rebuild", api=False
                    ):
                        return
                    self._rebuild_summary_action(form)
                    self._redirect(self._form_first(form, "next").strip() or "/summaries")
                    return
                if path == "/api/evolution/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/evolution/review", api=True
                    ):
                        return
                    self._write_json(self._review_evolution_action(form))
                    return
                if path == "/evolution/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/evolution/review", api=False
                    ):
                        return
                    payload = self._review_evolution_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/candidates/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/candidates/review", api=True
                    ):
                        return
                    self._write_json(self._review_candidate_action(form))
                    return
                if path == "/candidates/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/candidates/review", api=False
                    ):
                        return
                    payload = self._review_candidate_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/actions/enqueue":
                    self._write_json(self._enqueue_signal_action(form))
                    return
                if path == "/actions/enqueue":
                    payload = self._enqueue_signal_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/actions/run-next":
                    safe_only = self._form_first(form, "safe_only").strip() == "1"
                    pack_name = self._form_first(form, "pack").strip() or None
                    self._write_json(
                        run_next_action_queue_item(
                            resolved_vault,
                            safe_only=safe_only,
                            pack_name=pack_name,
                        )
                    )
                    return
                if path == "/actions/run-next":
                    safe_only = self._form_first(form, "safe_only").strip() == "1"
                    pack_name = self._form_first(form, "pack").strip() or None
                    run_next_action_queue_item(
                        resolved_vault,
                        safe_only=safe_only,
                        pack_name=pack_name,
                    )
                    self._redirect(self._form_first(form, "next").strip() or "/actions")
                    return
                if path == "/api/actions/run-batch":
                    limit = int(self._form_first(form, "limit").strip() or "5")
                    safe_only = self._form_first(form, "safe_only").strip() == "1"
                    pack_name = self._form_first(form, "pack").strip() or None
                    self._write_json(
                        run_action_queue(
                            resolved_vault,
                            limit=limit,
                            safe_only=safe_only,
                            pack_name=pack_name,
                        )
                    )
                    return
                if path == "/actions/run-batch":
                    limit = int(self._form_first(form, "limit").strip() or "5")
                    safe_only = self._form_first(form, "safe_only").strip() == "1"
                    pack_name = self._form_first(form, "pack").strip() or None
                    run_action_queue(
                        resolved_vault,
                        limit=limit,
                        safe_only=safe_only,
                        pack_name=pack_name,
                    )
                    self._redirect(self._form_first(form, "next").strip() or "/actions")
                    return
                if path == "/api/actions/retry":
                    self._write_json(self._retry_action(form))
                    return
                if path == "/actions/retry":
                    payload = self._retry_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/actions/dismiss":
                    self._write_json(self._dismiss_action(form))
                    return
                if path == "/actions/dismiss":
                    payload = self._dismiss_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                self.send_error(404, "Not Found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def _required(self, query: dict[str, list[str]], key: str) -> str:
            values = query.get(key)
            if not values or not values[0]:
                raise ValueError(f"missing required query param: {key}")
            return values[0]

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            return parse_qs(raw, keep_blank_values=True)

        def _form_first(self, form: dict[str, list[str]], key: str) -> str:
            values = form.get(key, [])
            return values[0] if values else ""

        def _form_all(self, form: dict[str, list[str]], key: str) -> list[str]:
            return form.get(key, [])

        def _guard_research_route(
            self, *, pack_name: str | None, route_path: str, api: bool
        ) -> bool:
            requested_pack = pack_name or ""
            if _shell_supports_research_nav(requested_pack):
                return False
            payload = _unsupported_route_payload(route_path, requested_pack)
            if api:
                self._write_json(payload, status=409)
            else:
                self._write_html(_render_unsupported_route_page(route_path, requested_pack))
            return True

        def _resolve_contradiction_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            contradiction_ids = [
                item.strip() for item in self._form_all(form, "contradiction_id") if item.strip()
            ]
            status = self._form_first(form, "status").strip()
            note = self._form_first(form, "note").strip()
            if not contradiction_ids:
                raise ValueError("missing contradiction_id")
            if status not in {
                "resolved_keep_positive",
                "resolved_keep_negative",
                "dismissed",
                "needs_human",
            }:
                raise ValueError("invalid contradiction status")
            payload = resolve_contradictions(
                resolved_vault,
                contradiction_ids,
                status=status,
                note=note,
            )
            if payload["resolved_count"] and self._form_first(form, "rebuild_summaries") == "1":
                affected_object_ids = contradiction_object_ids(
                    resolved_vault, payload["contradiction_ids"]
                )
                rebuild_payload = rebuild_compiled_summaries(
                    resolved_vault, object_ids=affected_object_ids
                )
                payload["rebuilt_summary_count"] = rebuild_payload["objects_rebuilt"]
                payload["rebuilt_object_ids"] = rebuild_payload["object_ids"]
            else:
                affected_object_ids = contradiction_object_ids(
                    resolved_vault, payload["contradiction_ids"]
                )
                payload["rebuilt_summary_count"] = 0
                payload["rebuilt_object_ids"] = []
            if payload["resolved_count"]:
                payload["object_ids"] = affected_object_ids
                record_review_action(
                    resolved_vault,
                    event_type="ui_contradictions_resolved",
                    slug=affected_object_ids[0] if affected_object_ids else "",
                    payload={
                        "object_ids": affected_object_ids,
                        "contradiction_ids": payload["contradiction_ids"],
                        "status": status,
                        "note": note,
                        "rebuilt_object_ids": payload["rebuilt_object_ids"],
                    },
                )
            return payload

        def _rebuild_summary_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            object_ids = [
                item.strip() for item in self._form_all(form, "object_id") if item.strip()
            ]
            if not object_ids:
                raise ValueError("missing object_id")
            payload = rebuild_compiled_summaries(resolved_vault, object_ids=object_ids)
            if payload["objects_rebuilt"]:
                record_review_action(
                    resolved_vault,
                    event_type="ui_summaries_rebuilt",
                    slug=payload["object_ids"][0] if payload["object_ids"] else "",
                    payload={
                        "object_ids": payload["object_ids"],
                        "objects_rebuilt": payload["objects_rebuilt"],
                        "rebuilt_object_ids": payload["object_ids"],
                    },
                )
            return payload

        def _review_evolution_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            evolution_id = self._form_first(form, "evolution_id").strip()
            status = self._form_first(form, "status").strip()
            note = self._form_first(form, "note").strip()
            link_type = self._form_first(form, "link_type").strip() or None
            pack_name = self._form_first(form, "pack").strip() or None
            payload = review_evolution_candidate(
                resolved_vault,
                evolution_id=evolution_id,
                status=status,
                pack_name=pack_name,
                note=note,
                link_type=link_type,
            )
            payload["next_path"] = self._form_first(form, "next").strip() or _shell_href(
                "/evolution",
                pack_name or "",
            )
            return payload

        def _review_candidate_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            slug = self._form_first(form, "slug").strip()
            action = self._form_first(form, "action").strip()
            target_slug = self._form_first(form, "target_slug").strip() or None
            note = self._form_first(form, "note").strip()
            pack_name = self._form_first(form, "pack").strip() or None
            next_path = self._form_first(form, "next").strip() or _shell_href(
                "/candidates",
                pack_name or "",
            )
            try:
                payload = review_candidate_concept(
                    resolved_vault,
                    slug=slug,
                    action=action,
                    target_slug=target_slug,
                    note=note,
                    pack_name=pack_name,
                )
            except RuntimeError as exc:
                payload = self._candidate_review_rebuild_warning_payload(
                    slug=slug,
                    action=action,
                    target_slug=target_slug,
                    note=note,
                    error=exc,
                )
                if not payload["partial_success"]:
                    raise
            payload["next_path"] = next_path
            knowledge_index_error = str(payload.get("knowledge_index_error") or "")
            if knowledge_index_error:
                payload["next_path"] = _append_query_param(
                    next_path,
                    "candidate_warning",
                    knowledge_index_error,
                )
            return payload

        def _candidate_review_rebuild_warning_payload(
            self,
            *,
            slug: str,
            action: str,
            target_slug: str | None,
            note: str,
            error: RuntimeError,
        ) -> dict[str, object]:
            audit_event: dict[str, object] = {}
            for item in list_review_actions(resolved_vault, limit=20):
                if (
                    item.get("event_type") == "ui_candidate_reviewed"
                    and item.get("candidate_slug") == slug
                ):
                    audit_event = item
                    break
            knowledge_index_error = str(audit_event.get("knowledge_index_error") or error)
            return {
                "action": str(audit_event.get("action") or action),
                "slug": str(audit_event.get("candidate_slug") or slug),
                "target_slug": str(audit_event.get("target_slug") or target_slug or ""),
                "status": str(audit_event.get("status") or "applied_with_warning"),
                "note": str(audit_event.get("note") or note),
                "mutation": audit_event.get("mutation")
                if isinstance(audit_event.get("mutation"), dict)
                else {},
                "knowledge_index_rebuilt": bool(audit_event.get("knowledge_index_rebuilt")),
                "knowledge_index_error": knowledge_index_error,
                "warning": str(error),
                "partial_success": bool(audit_event),
                "audit_event": audit_event,
            }

        def _enqueue_signal_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            signal_id = self._form_first(form, "signal_id").strip()
            if not signal_id:
                raise ValueError("missing signal_id")
            payload = enqueue_signal_action(resolved_vault, signal_id=signal_id)
            payload["next_path"] = self._form_first(form, "next").strip() or "/actions"
            return payload

        def _retry_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            action_id = self._form_first(form, "action_id").strip()
            if not action_id:
                raise ValueError("missing action_id")
            payload = retry_action_queue_item(resolved_vault, action_id=action_id)
            payload["next_path"] = self._form_first(form, "next").strip() or "/actions"
            return payload

        def _dismiss_action(self, form: dict[str, list[str]]) -> dict[str, object]:
            action_id = self._form_first(form, "action_id").strip()
            if not action_id:
                raise ValueError("missing action_id")
            payload = dismiss_action_queue_item(resolved_vault, action_id=action_id)
            payload["next_path"] = self._form_first(form, "next").strip() or "/actions"
            return payload

        def _write_json(self, payload: dict, *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, html: str, *, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_pulse_sse(
            self,
            *,
            poll_interval: float = 1.0,
            max_polls: int | None = None,
        ) -> None:
            """Stream events from ``60-Logs/*.jsonl`` to the client as SSE.

            Long-lived response. ``max_polls`` is for tests; production callers
            leave it ``None`` (loop until the client disconnects). Each event
            becomes one SSE frame with ``event:`` set to its ``event_type`` and
            ``data:`` carrying the JSON-encoded payload — matching the closed
            vocabulary documented in :mod:`event_emitter`.
            """
            layout = VaultLayout.from_vault(resolved_vault)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            try:
                self.wfile.write(b": ovp pulse stream\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

            positions = initial_positions(layout)
            polls = 0
            while True:
                if max_polls is not None and polls >= max_polls:
                    return
                polls += 1
                events, positions = tail_events(layout, since_position=positions)
                for event in events:
                    event_id = str(event.get("event_id") or "")
                    event_type = str(event.get("event_type") or "message")
                    data = json.dumps(event, ensure_ascii=False)
                    frame = (
                        (f"id: {event_id}\n" if event_id else "")
                        + f"event: {event_type}\n"
                        + f"data: {data}\n\n"
                    ).encode("utf-8")
                    try:
                        self.wfile.write(frame)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                if not events:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                time.sleep(poll_interval)

        def _write_bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return ThreadingHTTPServer((host, port), Handler)


def _spawn_action_worker_process(vault_dir: Path | str, *, interval_seconds: float = 2.0) -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ovp_pipeline.commands.run_actions",
            "--vault-dir",
            str(resolve_vault_dir(vault_dir)),
            "--loop",
            "--interval",
            str(max(0.1, interval_seconds)),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _prewarm_ui_caches(vault_dir: Path | str) -> None:
    try:
        build_evolution_browser_payload(vault_dir, status="all")
    except Exception as exc:
        print(f"ui server cache pre-warming failed: {exc}", file=sys.stderr)
        return


def _start_ui_prewarm(vault_dir: Path | str) -> None:
    _prewarm_ui_caches(vault_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a minimal local UI over knowledge.db")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--with-action-worker", action="store_true", help="Spawn a detached action worker process"
    )
    parser.add_argument(
        "--action-worker-interval",
        type=float,
        default=2.0,
        help="Polling interval for the detached action worker",
    )
    args = parser.parse_args(argv)

    resolved_vault = resolve_vault_dir(args.vault_dir)
    server = create_server(resolved_vault, host=args.host, port=args.port)
    try:
        build_objects_index_payload(resolved_vault, limit=1, offset=0)
        ensure_signal_ledger_synced(resolved_vault)
        _start_ui_prewarm(resolved_vault)
        if args.with_action_worker:
            _spawn_action_worker_process(
                resolved_vault,
                interval_seconds=args.action_worker_interval,
            )
    except Exception as exc:
        print(f"ui server preflight failed: {exc}", file=sys.stderr)
        server.server_close()
        return 1

    print(
        json.dumps({"host": args.host, "port": args.port, "vault_dir": str(resolved_vault)}),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
