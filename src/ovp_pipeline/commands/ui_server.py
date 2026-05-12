from __future__ import annotations

import argparse
import importlib.resources as importlib_resources
import json
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..knowledge_index import (
    contradiction_object_ids,
    rebuild_compiled_summaries,
    resolve_contradictions,
)
from ..packs.loader import DEFAULT_PACK_NAME
from ..pulse import initial_positions, tail_events
from ..runtime import VaultLayout, resolve_vault_dir
from .reuse_report import build_reuse_report_payload
from ..ui.view_models import (
    DEFAULT_CANDIDATE_BROWSER_LIMIT,
    build_action_queue_payload,
    build_atlas_browser_payload,
    build_briefing_payload,
    build_curated_atlas_payload,
    build_reader_home_payload,
    build_candidate_browser_payload,
    build_cluster_browser_payload,
    build_cluster_detail_payload,
    build_contradiction_browser_payload,
    build_evolution_browser_payload,
    build_event_dossier_payload,
    build_graph_map_payload,
    build_note_page_payload,
    build_object_page_payload,
    build_objects_index_payload,
    build_production_browser_payload,
    build_queue_overview_payload,
    build_search_payload,
    build_signal_browser_payload,
    build_stale_summary_browser_payload,
    build_timeline_payload,
    build_today_digest_payload,
    build_runs_index_payload,
    build_run_detail_payload,
    build_topic_overview_payload,
)
from ..truth_api import (
    dismiss_action_queue_item,
    enqueue_signal_action,
    ensure_signal_ledger_synced,
    get_operational_runtime_state,
    get_runtime_status,
    list_review_actions,
    record_review_action,
    review_candidate_concept,
    retry_action_queue_item,
    review_evolution_candidate,
    run_action_queue,
    run_next_action_queue_item,
)

from ._ui_renderers import (  # noqa: F401 — all renderers
    _append_query_param,
    _build_open_questions_payload,
    _build_runtime_home_payload_from_query,
    _build_writing_prompts_payload,
    _event_matches_object,
    _fragment_from_page,
    _FRAGMENT_BRIDGE_SCRIPT,
    _read_vault_asset,
    _read_vault_note,
    _render_actions_page,
    _render_atlas_page,
    _render_briefing_page,
    _render_curated_atlas_page,
    _render_reader_home,
    _render_candidate_items,
    _render_candidates_page,
    _render_cluster_detail_page,
    _render_clusters_page,
    _render_contradictions_page,
    _render_dashboard,
    _render_events_page,
    _render_evolution_browser_page,
    _render_explore_fragment,
    _render_explore_page,
    _render_graph_atlas_page,
    _render_library_home,
    _render_note_page,
    _render_object_page,
    _render_objects_index,
    _render_open_questions_fragment,
    _render_open_questions_page,
    _render_production_browser_page,
    _render_pulse_fragment,
    _render_pulse_page,
    _render_queue_overview_page,
    _render_timeline_page,
    _render_today_digest_page,
    _render_runs_index_page,
    _render_run_detail_page,
    _render_reuse_report_fragment,
    _render_run_history_card,
    _render_runtime_card,
    _render_reuse_report_page,
    _render_search_page,
    _render_signals_page,
    _render_source_backlink_rail,
    _render_stale_summaries_page,
    _render_topic_page,
    _render_unsupported_route_page,
    _render_workbench_page,
    _render_writing_prompts_fragment,
    _render_writing_prompts_page,
    _safe_redirect_path,
    _SHELL_BODY_OPEN,
    _shell_href,
    _shell_supports_research_nav,
    _unsupported_route_payload,
    set_request_path,
)


_CRYSTAL_NOTE_PREFIX = "40-Resources/Crystals/"


def _crystal_kind_and_id_from_note_path(relative_path: str) -> tuple[str, str] | None:
    """Reverse-derive ``(crystal_kind, crystal_id)`` from a Reader
    note path under ``40-Resources/Crystals/``.

    File-name conventions (mirrors ``synthesis/_versioning.py`` +
    ``synthesis/contradiction_crystal.py``):

      * community crystal:    ``<safe-id>.md`` →
            crystal_kind = ``community_crystal``,
            crystal_id   = ``cluster::<safe-id>``
      * contradiction crystal: ``contradiction-<safe-id>.md`` →
            crystal_kind = ``contradiction_crystal``,
            crystal_id   = ``contradiction::<safe-id>``

    Returns ``None`` for anything else so the caller short-circuits
    without writing a stray reuse event.
    """
    rel = relative_path.replace("\\", "/").lstrip("./")
    if not rel.startswith(_CRYSTAL_NOTE_PREFIX):
        return None
    stem = rel[len(_CRYSTAL_NOTE_PREFIX):]
    if not stem.endswith(".md"):
        return None
    stem = stem[:-len(".md")]
    if not stem:
        return None
    if stem.startswith("contradiction-"):
        safe_id = stem[len("contradiction-"):]
        if not safe_id:
            return None
        return ("contradiction_crystal", f"contradiction::{safe_id}")
    return ("community_crystal", f"cluster::{stem}")


def _maybe_emit_crystal_note_reuse(
    vault_dir: Path | str, relative_path: str, pack_name: str | None,
) -> None:
    """Best-effort: when a Reader ``/note?path=`` request resolves a
    synthesized-crystal markdown, write a ``reuse_events`` row so
    ``crystal_scoring._reuse_recency_signal`` has a producer.

    Pre-fix, the only producer for crystal-kind reuse events was
    test-fixture seeds, so the ``reuse_recency_norm`` weight in the
    default scoring formula contributed exactly zero in production
    no matter how often a topic was opened.
    """
    derived = _crystal_kind_and_id_from_note_path(relative_path)
    if derived is None:
        return
    try:
        from ..reuse_emitter import emit_crystal_reuse_events
        emit_crystal_reuse_events(
            vault_dir,
            pack=(pack_name or DEFAULT_PACK_NAME),
            crystals=[derived],
            surface="reader_note",
            consumer_ref=relative_path,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort instrumentation
        # Log so a regression in the emitter doesn't get
        # swallowed silently — see test_no_silent_imports.
        print(
            f"[ui_server] crystal reuse-event emission failed for "
            f"/note?path={relative_path}: {exc}",
            file=sys.stderr,
        )


def _parse_optional_int(query: dict[str, list[str]], key: str) -> int | None:
    """Read an optional integer query param.  Returns ``None`` for an
    absent / empty / non-numeric value so the downstream payload
    builder applies its own default."""
    raw = query.get(key, [""])[0]
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# BL-050: legacy top-level maintainer paths now live under ``/ops/<same>``.
# A request to any of these gets a 301 redirect so existing bookmarks /
# inline JS hrefs keep working.  ``/api/*`` paths are deliberately not
# in the table — APIs are unchanged in this migration.
_LEGACY_MAINTAINER_PATHS: frozenset[str] = frozenset({
    "/candidates",
    "/candidates/review",
    "/candidates/fragment",
    "/contradictions",
    "/contradictions/resolve",
    "/signals",
    "/actions",
    "/actions/run-next",
    "/actions/run-batch",
    "/actions/retry",
    "/actions/dismiss",
    "/actions/enqueue",
    "/actions/fragment",
    "/evolution",
    "/evolution/review",
    "/production",
    "/pulse",
    "/pulse/fragment",
    "/pulse/stream",
    "/events",
    "/reuse/fragment",
    "/open-questions/fragment",
    "/writing-prompts/fragment",
    "/summaries",
    "/summaries/rebuild",
    "/briefing",
    "/briefing/fragment",
    "/workbench",
    "/clusters",
    "/cluster",
    "/objects",
})


# Suffix → Content-Type map for the /static/<path> endpoint.  Kept
# narrow so the route can never be coaxed into serving arbitrary
# files: any suffix not in this map returns 404.
_STATIC_CONTENT_TYPES: dict[str, str] = {
    ".css":   "text/css; charset=utf-8",
    ".js":    "application/javascript; charset=utf-8",
    ".svg":   "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff":  "font/woff",
    ".png":   "image/png",
    ".jpg":   "image/jpeg",
    ".jpeg":  "image/jpeg",
    ".webp":  "image/webp",
    ".ico":   "image/x-icon",
    ".txt":   "text/plain; charset=utf-8",
}


def create_server(
    vault_dir: Path | str, *, host: str = "127.0.0.1", port: int = 8787
) -> ThreadingHTTPServer:
    resolved_vault = resolve_vault_dir(vault_dir)
    csrf_token = secrets.token_hex(16)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # pragma: no cover
            return

        def _permanent_redirect(self, status: int, target: str) -> None:
            """Send a permanent redirect with the standard headers.

            The status-method-body contract here is load-bearing:

              * ``301`` for GETs — browsers update bookmarks.
              * ``308`` for POSTs — preserves method + body so a
                form submission is reissued, not silently demoted
                to a GET.

            Pre-fix this 4-line response was copy-pasted across
            three sites and a regression that swapped 308→301 on a
            POST path would silently destroy form submissions.
            ``Cache-Control: no-store`` is included on every
            redirect so an upstream proxy can't shortcut the new
            target.
            """
            self.send_response(status)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _legacy_target(self, parsed) -> str:
            """Compose ``/ops/<same>?<query>`` for a legacy path."""
            target = "/ops" + parsed.path
            if parsed.query:
                target = f"{target}?{parsed.query}"
            return target

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            # BL-050: legacy maintainer paths → 301 to /ops/<same>.
            # Preserves any existing query string verbatim.
            if path in _LEGACY_MAINTAINER_PATHS:
                self._permanent_redirect(301, self._legacy_target(parsed))
                return

            # /static/<path> serves package data from
            # src/ovp_pipeline/static/.  Handled before
            # set_request_path() so static loads don't pollute the
            # Reader/Maintainer shell-selection thread-local.
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
                return

            # BL-050: shell selection is decided by URL prefix.  The
            # renderer's ``_layout`` reads this thread-local to pick
            # Reader vs Maintainer nav.
            set_request_path(path)

            try:
                if path == "/":
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_reader_home_payload(
                        resolved_vault, pack_name=pack_name,
                    )
                    self._write_html(_render_reader_home(payload))
                    return
                if path == "/ops":
                    payload = _build_runtime_home_payload_from_query(resolved_vault, query)
                    self._write_html(_render_dashboard(payload))
                    return
                if path == "/api/objects":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    q = query.get("q", [""])[0]
                    kind = query.get("kind", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(
                        build_objects_index_payload(
                            resolved_vault,
                            limit=limit,
                            offset=offset,
                            query=q,
                            object_kind=kind,
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
                if path == "/api/runtime-state":
                    try:
                        limit = int(query.get("limit", ["20"])[0])
                    except ValueError as exc:
                        self._write_json(
                            {
                                "type": "operational_runtime_state",
                                "status": "invalid_request",
                                "error": "invalid_runtime_state_limit",
                                "detail": str(exc),
                                "metrics": {},
                                "attention": [],
                            },
                            status=400,
                        )
                        return
                    try:
                        self._write_json(
                            get_operational_runtime_state(
                                resolved_vault,
                                recent_limit=limit,
                            )
                        )
                    except (OSError, sqlite3.Error) as exc:
                        self._write_json(
                            {
                                "type": "operational_runtime_state",
                                "status": "unavailable",
                                "error": "runtime_state_unavailable",
                                "detail": str(exc),
                                "metrics": {},
                                "attention": [],
                            },
                            status=503,
                        )
                    return
                if path == "/ops/objects":
                    # Page-size whitelist guards against pathological
                    # ``?limit=99999`` queries; mirrors the per-page UI
                    # selector below.  Out-of-range values fall back
                    # silently to the default (100).
                    raw_limit = query.get("limit", ["100"])[0]
                    try:
                        limit = int(raw_limit)
                    except ValueError:
                        limit = 100
                    if limit not in (10, 50, 100, 200):
                        limit = 100
                    raw_offset = query.get("offset", ["0"])[0]
                    try:
                        offset = max(0, int(raw_offset))
                    except ValueError:
                        offset = 0
                    sort = query.get("sort", ["alpha"])[0] or "alpha"
                    q = query.get("q", [""])[0]
                    # BL-030 follow-up: ``kind`` is the legacy alias
                    # (used by the type-facet chip rail in PR #177);
                    # ``object_kind`` is the canonical name.  Accept
                    # both — preferring ``object_kind`` — so existing
                    # bookmarks and chip-rail links keep filtering.
                    object_kind = (
                        query.get("object_kind", [""])[0]
                        or query.get("kind", [""])[0]
                        or None
                    )
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_objects_index_payload(
                        resolved_vault,
                        limit=limit,
                        offset=offset,
                        query=q,
                        object_kind=object_kind,
                        pack_name=pack_name,
                        sort=sort,
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
                if path in {"/ops/briefing", "/ops/briefing/fragment"}:
                    pack_name = query.get("pack", [""])[0] or None
                    page = _render_briefing_page(
                        build_briefing_payload(resolved_vault, pack_name=pack_name)
                    )
                    if path == "/ops/briefing/fragment":
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
                if path == "/ops/queue/signals":
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
                    limit = int(query.get("limit", [str(DEFAULT_CANDIDATE_BROWSER_LIMIT)])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/candidates", api=True
                    ):
                        return
                    self._write_json(
                        build_candidate_browser_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            query=q,
                            limit=limit,
                            offset=offset,
                        )
                    )
                    return
                if path in {"/ops/queue/concepts", "/ops/candidates/fragment"}:
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    limit = int(query.get("limit", [str(DEFAULT_CANDIDATE_BROWSER_LIMIT)])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    candidate_warning = query.get("candidate_warning", [""])[0]
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/queue/concepts", api=False
                    ):
                        return
                    payload = build_candidate_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        query=q,
                        limit=limit,
                        offset=offset,
                    )
                    payload["candidate_warning"] = candidate_warning
                    page = _render_candidates_page(payload)
                    if path == "/ops/candidates/fragment":
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
                        pack_name=pack_name, route_path="/ops/evolution", api=True
                    ):
                        return
                    # Cache miss/hit serialisation is handled inside
                    # ``_all_evolution_candidates`` (double-checked
                    # locking around ``_EVOLUTION_CANDIDATE_CACHE``),
                    # so request handlers no longer need to hold a
                    # handler-level lock and cache hits stay fully
                    # concurrent.
                    api_payload = build_evolution_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        query=q,
                        status=status,
                        link_type=link_type,
                    )
                    self._write_json(api_payload)
                    return
                if path == "/ops/evolution":
                    q = query.get("q", [""])[0]
                    status = query.get("status", ["all"])[0] or "all"
                    link_type = query.get("link_type", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/evolution", api=False
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
                        pack_name=pack_name, route_path="/ops/events", api=True
                    ):
                        return
                    self._write_json(
                        build_event_dossier_payload(resolved_vault, pack_name=pack_name, query=q)
                    )
                    return
                if path == "/ops/events":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/events", api=False
                    ):
                        return
                    # ``date`` is shorthand for a single-day query
                    # (``from_date == to_date``).  Explicit ``from_date`` /
                    # ``to_date`` win when both forms are supplied.
                    single_date = query.get("date", [""])[0] or ""
                    from_date = query.get("from_date", [single_date])[0] or single_date or None
                    to_date = query.get("to_date", [single_date])[0] or single_date or None
                    raw_evt_limit = query.get("limit", [""])[0]
                    try:
                        evt_limit = int(raw_evt_limit) if raw_evt_limit else None
                    except ValueError:
                        evt_limit = None
                    if evt_limit is not None and evt_limit not in (25, 50, 100, 200):
                        evt_limit = None
                    payload = build_event_dossier_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        query=q,
                        limit=evt_limit,
                        from_date=from_date,
                        to_date=to_date,
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
                # BL-051: ``/topics`` is the canonical reader URL for
                # the Featured Topics page.  Old ``/atlas/curated`` and
                # ``/api/atlas/curated`` 301 to the new paths so any
                # bookmark from PR #148 keeps working.
                if path in {"/atlas/curated", "/api/atlas/curated"}:
                    target = "/api/topics" if path.startswith("/api/") else "/topics"
                    if parsed.query:
                        target = f"{target}?{parsed.query}"
                    self._permanent_redirect(301, target)
                    return
                if path == "/api/topics":
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/topics", api=True
                    ):
                        return
                    self._write_json(
                        build_curated_atlas_payload(
                            resolved_vault,
                            pack_name=pack_name,
                            top_n=_parse_optional_int(query, "top_n"),
                        )
                    )
                    return
                if path == "/topics":
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/topics", api=False
                    ):
                        return
                    payload = build_curated_atlas_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        top_n=_parse_optional_int(query, "top_n"),
                    )
                    self._write_html(_render_curated_atlas_page(payload))
                    return
                # BL-029 deleted the deep-dive producer, so the
                # ``/ops/deep-dives`` index page is permanently
                # empty.  Redirect any existing bookmarks to
                # ``/ops/today``; preserve query string so a
                # ``?pack=...`` link survives.  The ``/api`` twin
                # is dropped (no JSON consumers in production).
                if path in {"/ops/deep-dives", "/api/deep-dives"}:
                    target = "/ops/today"
                    if parsed.query:
                        target = f"{target}?{parsed.query}"
                    self._permanent_redirect(301, target)
                    return
                # BL-053 Phase 2: ``/ops/queue/*`` is the new home
                # for the four pending-review queues.  The bare
                # legacy paths 301 to the queue routes so existing
                # bookmarks survive.  Fragment paths
                # (``/ops/candidates/fragment``,
                # ``/ops/actions/fragment``) and form-POST sub-routes
                # (``/ops/contradictions/resolve``,
                # ``/ops/actions/enqueue`` etc.) are NOT redirected —
                # they remain reachable at their old paths so the
                # workbench iframes and form submitters keep working.
                _OPS_QUEUE_REDIRECTS = {
                    "/ops/candidates": "/ops/queue/concepts",
                    "/ops/contradictions": "/ops/queue/contradictions",
                    "/ops/signals": "/ops/queue/signals",
                    "/ops/actions": "/ops/queue/actions",
                }
                if path in _OPS_QUEUE_REDIRECTS:
                    target = _OPS_QUEUE_REDIRECTS[path]
                    if parsed.query:
                        target = f"{target}?{parsed.query}"
                    self._permanent_redirect(301, target)
                    return
                if path == "/ops/queue":
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_queue_overview_payload(
                        resolved_vault, pack_name=pack_name
                    )
                    self._write_html(_render_queue_overview_page(payload))
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
                if path == "/ops/production":
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
                        pack_name=pack_name, route_path="/ops/clusters", api=True
                    ):
                        return
                    self._write_json(
                        build_cluster_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    )
                    return
                if path == "/ops/clusters":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/clusters", api=False
                    ):
                        return
                    show_all = query.get("show_all", [""])[0] == "1"
                    raw_cluster_limit = query.get("limit", [""])[0]
                    try:
                        cluster_limit = int(raw_cluster_limit) if raw_cluster_limit else 15
                    except ValueError:
                        cluster_limit = 15
                    if cluster_limit not in (15, 50, 200):
                        cluster_limit = 15
                    raw_cluster_offset = query.get("offset", ["0"])[0]
                    try:
                        cluster_offset = max(0, int(raw_cluster_offset))
                    except ValueError:
                        cluster_offset = 0
                    payload = build_cluster_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        query=q,
                        limit=cluster_limit,
                        show_all=show_all,
                        offset=cluster_offset,
                    )
                    self._write_html(_render_clusters_page(payload))
                    return
                if path == "/api/graph":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    show_all = query.get("show_all", [""])[0] == "1"
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/graph", api=True
                    ):
                        return
                    self._write_json(
                        build_graph_map_payload(
                            resolved_vault, pack_name=pack_name, query=q,
                            show_all=show_all,
                        )
                    )
                    return
                if path == "/graph":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    show_all = query.get("show_all", [""])[0] == "1"
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/graph", api=False
                    ):
                        return
                    payload = build_graph_map_payload(
                        resolved_vault, pack_name=pack_name, query=q,
                        show_all=show_all,
                    )
                    self._write_html(_render_graph_atlas_page(payload, action_path="/graph"))
                    return
                if path == "/map":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    show_all = query.get("show_all", [""])[0] == "1"
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/map", api=False
                    ):
                        return
                    payload = build_graph_map_payload(
                        resolved_vault, pack_name=pack_name, query=q,
                        show_all=show_all,
                    )
                    self._write_html(_render_graph_atlas_page(payload, action_path="/map"))
                    return
                if path == "/api/cluster":
                    cluster_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/cluster", api=True
                    ):
                        return
                    self._write_json(
                        build_cluster_detail_payload(
                            resolved_vault, cluster_id=cluster_id, pack_name=pack_name
                        )
                    )
                    return
                if path == "/ops/cluster":
                    cluster_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/cluster", api=False
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
                if path in {"/ops/queue/actions", "/ops/actions/fragment"}:
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
                    if path == "/ops/actions/fragment":
                        self._write_html(_fragment_from_page(page))
                    else:
                        self._write_html(page)
                    return
                if path == "/api/summaries":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/summaries", api=True
                    ):
                        return
                    self._write_json(
                        build_stale_summary_browser_payload(
                            resolved_vault, pack_name=pack_name, query=q
                        )
                    )
                    return
                if path == "/ops/summaries":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/summaries", api=False
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
                    # BL-058 follow-up: when the note is a synthesized
                    # crystal, emit a reuse event so crystal_scoring's
                    # ``reuse_recency_norm`` has a producer.  Best-effort.
                    _maybe_emit_crystal_note_reuse(
                        resolved_vault, relative_path, pack_name,
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
                        pack_name=pack_name, route_path="/ops/contradictions", api=True
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
                if path == "/ops/queue/contradictions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(
                        pack_name=pack_name,
                        route_path="/ops/queue/contradictions",
                        api=False,
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
                    elif path == "/ops/reuse/fragment":
                        self._write_html(_render_reuse_report_fragment(payload))
                    else:
                        self._write_html(_render_reuse_report_page(payload))
                    return
                if path in {"/open-questions", "/open-questions/fragment", "/api/open-questions"}:
                    payload = _build_open_questions_payload(resolved_vault)
                    if path == "/api/open-questions":
                        self._write_json(payload)
                    elif path == "/ops/open-questions/fragment":
                        self._write_html(_render_open_questions_fragment(payload))
                    else:
                        self._write_html(_render_open_questions_page(payload))
                    return
                if path in {
                    "/writing-prompts",
                    "/writing-prompts/fragment",
                    "/api/writing-prompts",
                }:
                    payload = _build_writing_prompts_payload(resolved_vault)
                    if path == "/api/writing-prompts":
                        self._write_json(payload)
                    elif path == "/ops/writing-prompts/fragment":
                        self._write_html(_render_writing_prompts_fragment(payload))
                    else:
                        self._write_html(_render_writing_prompts_page(payload))
                    return
                if path == "/ops/workbench":
                    object_id = query.get("object_id", [""])[0]
                    pack_name = query.get("pack", [""])[0] or ""
                    self._write_html(
                        _render_workbench_page(object_id=object_id, requested_pack=pack_name)
                    )
                    return
                if path in ("/ops/timeline", "/api/timeline"):
                    pack_name = query.get("pack", [""])[0] or None
                    raw_days = query.get("days", [""])[0]
                    days = int(raw_days) if raw_days.isdigit() else None
                    payload = build_timeline_payload(
                        resolved_vault, pack_name=pack_name, days=days,
                    )
                    if path == "/api/timeline":
                        self._write_json(payload)
                    else:
                        self._write_html(_render_timeline_page(payload))
                    return
                # BL-053: by-day "today" digest
                if path in ("/ops/today", "/api/today"):
                    pack_name = query.get("pack", [""])[0] or None
                    target_date = query.get("date", [""])[0] or None
                    payload = build_today_digest_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        target_date=target_date,
                    )
                    if path == "/api/today":
                        self._write_json(payload)
                    else:
                        self._write_html(_render_today_digest_page(payload))
                    return
                # BL-053: by-run "runs" index
                if path in ("/ops/runs", "/api/runs"):
                    pack_name = query.get("pack", [""])[0] or None
                    raw_limit = query.get("limit", [""])[0]
                    limit = int(raw_limit) if raw_limit.isdigit() else None
                    payload = build_runs_index_payload(
                        resolved_vault, pack_name=pack_name, limit=limit,
                    )
                    if path == "/api/runs":
                        self._write_json(payload)
                    else:
                        self._write_html(_render_runs_index_page(payload))
                    return
                # BL-053: per-run event timeline.  Path shape:
                # ``/ops/runs/<txn_id>`` (HTML) or
                # ``/api/runs/<txn_id>`` (JSON).
                if path.startswith("/ops/runs/") or path.startswith("/api/runs/"):
                    is_api = path.startswith("/api/runs/")
                    txn_id = path.split("/", 3)[-1] if path.count("/") >= 3 else ""
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_run_detail_payload(
                        resolved_vault, txn_id, pack_name=pack_name,
                    )
                    if is_api:
                        self._write_json(payload)
                    else:
                        self._write_html(_render_run_detail_page(payload))
                    return
                if path == "/ops/pulse":
                    pack_name = query.get("pack", [""])[0] or ""
                    self._write_html(_render_pulse_page(requested_pack=pack_name))
                    return
                if path == "/ops/pulse/fragment":
                    self._write_html(_render_pulse_fragment())
                    return
                if path == "/ops/pulse/stream":
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
                    self._stream_pulse_sse(poll_interval=poll_interval, max_polls=max_polls)
                    return
                if path == "/explore":
                    object_id = query.get("object_id", [""])[0]
                    self._write_html(_render_explore_page(object_id=object_id))
                    return
                if path == "/explore/stream":
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
                    object_id = query.get("object_id", [""])[0].strip()
                    self._stream_agent_decisions_sse(
                        poll_interval=poll_interval,
                        max_polls=max_polls,
                        object_id=object_id or None,
                    )
                    return
                self.send_error(404, "Not Found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path

            # BL-050: legacy maintainer POST paths → 308 to preserve
            # method + body during the redirect (301 would force GET).
            if path in _LEGACY_MAINTAINER_PATHS:
                self._permanent_redirect(308, self._legacy_target(parsed))
                return

            set_request_path(path)
            try:
                form = self._read_form()
                cookie_header = self.headers.get("Cookie", "")
                if f"_csrf={csrf_token}" in cookie_header:
                    submitted_token = (form.get("_csrf") or [""])[0]
                    if submitted_token != csrf_token:
                        self._write_json({"error": "csrf_token_mismatch"}, status=403)
                        return
                if path == "/api/contradictions/resolve":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/contradictions/resolve", api=True
                    ):
                        return
                    self._write_json(self._resolve_contradiction_action(form))
                    return
                if path == "/api/runtime-state":
                    try:
                        limit = int(self._form_first(form, "limit").strip() or "20")
                    except ValueError as exc:
                        self._write_json(
                            {
                                "type": "operational_runtime_state",
                                "status": "invalid_request",
                                "error": "invalid_runtime_state_limit",
                                "detail": str(exc),
                                "metrics": {},
                                "attention": [],
                            },
                            status=400,
                        )
                        return
                    try:
                        self._write_json(
                            get_operational_runtime_state(
                                resolved_vault,
                                recent_limit=limit,
                                write_projection=True,
                                prefer_materialized=False,
                            )
                        )
                    except (OSError, sqlite3.Error) as exc:
                        self._write_json(
                            {
                                "type": "operational_runtime_state",
                                "status": "unavailable",
                                "error": "runtime_state_unavailable",
                                "detail": str(exc),
                                "metrics": {},
                                "attention": [],
                            },
                            status=503,
                        )
                    return
                if path == "/ops/contradictions/resolve":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/contradictions/resolve", api=False
                    ):
                        return
                    self._resolve_contradiction_action(form)
                    self._redirect(
                        self._form_first(form, "next").strip() or "/ops/queue/contradictions?status=resolved"
                    )
                    return
                if path == "/api/summaries/rebuild":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/summaries/rebuild", api=True
                    ):
                        return
                    self._write_json(self._rebuild_summary_action(form))
                    return
                if path == "/ops/summaries/rebuild":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/summaries/rebuild", api=False
                    ):
                        return
                    self._rebuild_summary_action(form)
                    self._redirect(self._form_first(form, "next").strip() or "/ops/summaries")
                    return
                if path == "/api/evolution/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/evolution/review", api=True
                    ):
                        return
                    self._write_json(self._review_evolution_action(form))
                    return
                if path == "/ops/evolution/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/evolution/review", api=False
                    ):
                        return
                    payload = self._review_evolution_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/candidates/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/candidates/review", api=True
                    ):
                        return
                    self._write_json(self._review_candidate_action(form))
                    return
                if path == "/ops/candidates/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(
                        pack_name=pack_name, route_path="/ops/candidates/review", api=False
                    ):
                        return
                    payload = self._review_candidate_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/actions/enqueue":
                    self._write_json(self._enqueue_signal_action(form))
                    return
                if path == "/ops/actions/enqueue":
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
                if path == "/ops/actions/run-next":
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
                if path == "/ops/actions/run-batch":
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
                if path == "/ops/actions/retry":
                    payload = self._retry_action(form)
                    self._redirect(str(payload["next_path"]))
                    return
                if path == "/api/actions/dismiss":
                    self._write_json(self._dismiss_action(form))
                    return
                if path == "/ops/actions/dismiss":
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
                "mutation": (
                    audit_event.get("mutation")
                    if isinstance(audit_event.get("mutation"), dict)
                    else {}
                ),
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
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, html: str, *, status: int = 200) -> None:
            csrf_snippet = (
                f'<meta name="csrf-token" content="{csrf_token}" />\n'
                "<script>\n"
                "document.addEventListener('DOMContentLoaded', function() {\n"
                "  document.querySelectorAll('form[method=\"post\"]').forEach(function(f) {\n"
                "    if (!f.querySelector('input[name=\"_csrf\"]')) {\n"
                "      var i = document.createElement('input');\n"
                "      i.type = 'hidden'; i.name = '_csrf';\n"
                "      i.value = document.querySelector('meta[name=\"csrf-token\"]').content;\n"
                "      f.appendChild(i);\n"
                "    }\n"
                "  });\n"
                "});\n"
                "</script>\n"
            )
            html = html.replace("</head>", csrf_snippet + "</head>", 1)
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Set-Cookie",
                f"_csrf={csrf_token}; SameSite=Strict; HttpOnly; Path=/",
            )
            # `https://unpkg.com` is allowed to source D3 v7 for the
            # /ops/cluster?id=... force-directed graph.  This is the
            # only third-party JS the maintainer UI loads; if the CDN
            # is unreachable the page falls back to the tabular
            # members/edges sections rendered server-side.  Google
            # Fonts is allowed for the IBM Plex stack: stylesheet
            # arrives from fonts.googleapis.com, woff2 from
            # fonts.gstatic.com.  Both fail soft to the
            # ``ui-sans-serif, system-ui`` chain declared in
            # ``--ovp-font-sans``.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com",
            )
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

        def _stream_agent_decisions_sse(
            self,
            *,
            poll_interval: float = 1.0,
            max_polls: int | None = None,
            object_id: str | None = None,
        ) -> None:
            """Phase 38 Stage C — tail ``60-Logs/agent-decisions.jsonl``.

            Same poll/SSE machinery as ``_stream_pulse_sse``, but scoped to a
            single log written by graph_ops invocations through the MCP
            server. Each frame uses the ``agent_decision`` event name so the
            UI subscribes selectively. When ``object_id`` is provided, only
            events whose top-level or ``arguments.object_id`` matches are
            forwarded — otherwise the consumer would see decisions for
            unrelated objects in the timeline pane.
            """
            layout = VaultLayout.from_vault(resolved_vault)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            try:
                self.wfile.write(b": ovp explore stream\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

            logs = ("agent-decisions.jsonl",)
            positions = initial_positions(layout, logs=logs)
            polls = 0
            while True:
                if max_polls is not None and polls >= max_polls:
                    return
                polls += 1
                events, positions = tail_events(layout, since_position=positions, logs=logs)
                emitted = False
                for event in events:
                    if object_id and not _event_matches_object(event, object_id):
                        continue
                    event_id = str(event.get("event_id") or "")
                    data = json.dumps(event, ensure_ascii=False)
                    frame = (
                        (f"id: {event_id}\n" if event_id else "")
                        + "event: agent_decision\n"
                        + f"data: {data}\n\n"
                    ).encode("utf-8")
                    try:
                        self.wfile.write(frame)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    emitted = True
                if not emitted:
                    # Send a keepalive whenever no frame was written this poll
                    # — including the case where every tailed event was
                    # filtered out by ``object_id``. Without this, a client
                    # watching an idle object behind a chatty log never
                    # triggers a wfile.write and we can't detect disconnects,
                    # leaking one thread + socket per stale viewer.
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
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _serve_static(self, asset_path: str) -> None:
            """Serve a file from the package's ``static/`` directory.

            Reject any path that contains ``..`` segments, an absolute
            prefix, or whose suffix is not in the small allow-list —
            this is a static-asset endpoint, not a generic file server.
            URL-decode before validation so percent-encoded traversal
            attempts (``%2e%2e``) hit the same checks as literal ones.
            Binary assets get a 1-day cache (no ``immutable``: filenames
            aren't content-hashed yet, so revalidation must remain
            possible).  CSS/JS get ``no-cache`` while the design system
            is iterating.
            """
            asset_path = unquote(asset_path)
            if (
                not asset_path
                or asset_path.startswith("/")
                or ".." in asset_path.split("/")
                or "\x00" in asset_path
            ):
                self._write_html("<h1>404</h1>", status=404)
                return
            suffix = Path(asset_path).suffix.lower()
            content_type = _STATIC_CONTENT_TYPES.get(suffix)
            if content_type is None:
                self._write_html("<h1>404</h1>", status=404)
                return
            try:
                resource = (
                    importlib_resources.files("ovp_pipeline")
                    .joinpath("static", asset_path)
                )
                body = resource.read_bytes()
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError):
                self._write_html("<h1>404</h1>", status=404)
                return
            # Static URLs are not yet content-hashed (no
            # ``/static/<hash>/<name>`` versioning), so ``immutable``
            # would freeze stale assets in users' browsers for the
            # full TTL whenever we ship a release.  Drop ``immutable``
            # and keep a short TTL until URL versioning lands.
            cache = (
                "public, max-age=86400"
                if suffix in {".woff2", ".woff", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico"}
                else "no-cache"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str) -> None:
            safe_location = _safe_redirect_path(location)
            self.send_response(303)
            self.send_header("Location", safe_location)
            self.send_header("Content-Length", "0")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'",
            )
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
    """Warm the evolution-browser payload cache.

    On the live operator vault this single call costs ~4 minutes
    (``_compute_evolution_candidates`` reads every open-contradiction
    claim's source markdown to extract dates, in an N*M nested loop).
    Running it synchronously inside ``main()`` blocks port-binding
    and makes every ``ovp-ui`` restart feel broken.

    Deduplication with concurrent request handlers happens one layer
    down inside ``_all_evolution_candidates``'s double-checked lock
    around ``_EVOLUTION_CANDIDATE_CACHE`` — so the background prewarm
    can race against the first ``/ops/evolution`` request safely.
    """
    try:
        build_evolution_browser_payload(vault_dir, status="all")
    except Exception as exc:
        print(f"ui server cache pre-warming failed: {exc}", file=sys.stderr)
        return


def _start_ui_prewarm(vault_dir: Path | str) -> None:
    """Kick off the prewarm in a daemon thread.

    The HTTP server can start accepting requests immediately.  The
    first request to ``/ops/evolution`` may wait briefly if the
    background thread hasn't finished yet — but every other page is
    free.  Crash-tolerant: prewarm failures are logged inside
    ``_prewarm_ui_caches`` and don't bring the server down.
    """
    thread = threading.Thread(
        target=_prewarm_ui_caches,
        args=(vault_dir,),
        name="ovp-ui-prewarm",
        daemon=True,
    )
    thread.start()


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
        if not VaultLayout.from_vault(resolved_vault).signals_log.exists():
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
