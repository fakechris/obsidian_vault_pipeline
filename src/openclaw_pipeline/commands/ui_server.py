from __future__ import annotations

import argparse
import json
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..runtime import resolve_vault_dir
from ..ui.view_models import (
    build_contradiction_browser_payload,
    build_event_dossier_payload,
    build_object_page_payload,
    build_objects_index_payload,
    build_truth_dashboard_payload,
    build_topic_overview_payload,
)


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; line-height: 1.5; max-width: 980px; }}
      nav {{ margin-bottom: 1.5rem; }}
      nav a {{ margin-right: 1rem; }}
      h1, h2 {{ margin-bottom: 0.5rem; }}
      ul {{ padding-left: 1.2rem; }}
      pre {{ background: #f4f4f5; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
      .muted {{ color: #52525b; }}
      .card {{ border: 1px solid #e4e4e7; border-radius: 10px; padding: 1rem; margin-bottom: 1rem; }}
      .pill {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; background: #eef2ff; margin-right: 0.5rem; }}
    </style>
  </head>
  <body>
    <nav>
      <a href="/">Home</a>
      <a href="/objects">Objects</a>
      <a href="/events">Event Dossier</a>
      <a href="/contradictions">Contradictions</a>
    </nav>
    {body}
  </body>
</html>
"""


def _render_dashboard(payload: dict) -> str:
    object_items = "".join(
        f'<li><a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
        for item in payload["objects"]["items"]
    ) or "<li>None</li>"
    contradiction_items = "".join(
        f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
        for item in payload["contradictions"]["items"]
    ) or "<li>None</li>"
    event_items = "".join(
        f"<li>{escape(item['event_date'])} - "
        f'<a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
        for item in payload["events"]["items"]
    ) or "<li>None</li>"
    return _layout(
        "OpenClaw Truth UI",
        (
            "<h1>OpenClaw Truth UI</h1>"
            "<p class='muted'>Read-only browser over <code>knowledge.db</code>. JSON APIs remain available at <code>/api/*</code>, including <code>/api/objects</code>.</p>"
            "<section class='card'>"
            "<h2>Objects Indexed</h2>"
            f"<p>{payload['objects']['count']}</p>"
            f"<ul>{object_items}</ul>"
            "</section>"
            "<section class='card'>"
            "<h2>Contradictions Open</h2>"
            f"<p>{payload['contradictions']['open_count']}</p>"
            f"<ul>{contradiction_items}</ul>"
            "</section>"
            "<section class='card'>"
            "<h2>Recent Events</h2>"
            f"<p>{payload['events']['count']}</p>"
            f"<ul>{event_items}</ul>"
            "</section>"
        ),
    )


def _render_objects_index(payload: dict) -> str:
    items = "".join(
        f'<li><a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a> '
        f'<span class="muted">({escape(item["object_id"])})</span></li>'
        for item in payload["items"]
    )
    return _layout(
        "Objects",
        f"<h1>Objects</h1><p class='muted'>{payload['count']} objects in current page.</p><ul>{items}</ul>",
    )


def _render_object_page(payload: dict) -> str:
    claims = "".join(f"<li>{escape(item['claim_text'])}</li>" for item in payload["claims"]) or "<li>None</li>"
    relations = "".join(
        f'<li><a href="/object?id={escape(item["target_object_id"])}">{escape(item.get("target_title", item["target_object_id"]))}</a>'
        f' <span class="muted">({escape(item["relation_type"])})</span></li>'
        for item in payload["relations"]
    ) or "<li>None</li>"
    contradictions = "".join(
        f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
        for item in payload["contradictions"]
    ) or "<li>None</li>"
    summary_text = payload["summary"]["summary_text"] if payload["summary"] else ""
    return _layout(
        f"Object: {payload['object']['title']}",
        (
            f"<h1>Object: {escape(payload['object']['title'])}</h1>"
            f"<p class='muted'>{escape(payload['object']['object_id'])}</p>"
            f"<section class='card'><h2>Compiled Summary</h2><p>{escape(summary_text)}</p></section>"
            f"<section class='card'><h2>Claims</h2><ul>{claims}</ul></section>"
            f"<section class='card'><h2>Relations</h2><ul>{relations}</ul></section>"
            f"<section class='card'><h2>Contradictions</h2><ul>{contradictions}</ul></section>"
        ),
    )


def _render_topic_page(payload: dict) -> str:
    neighbors = "".join(
        f'<li><a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
        for item in payload["neighbors"]
    ) or "<li>None</li>"
    return _layout(
        f"Topic: {payload['center']['title']}",
        (
            f"<h1>Topic: {escape(payload['center']['title'])}</h1>"
            f"<p class='muted'>{payload['neighbor_count']} neighbors, {payload['edge_count']} edges.</p>"
            f"<section class='card'><h2>Neighbors</h2><ul>{neighbors}</ul></section>"
        ),
    )


def _render_events_page(payload: dict) -> str:
    events = "".join(
        f"<li>{escape(item['event_date'])} - "
        f'<a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
        for item in payload["events"]
    ) or "<li>None</li>"
    return _layout(
        "Event Dossier",
        (
            f"<h1>Event Dossier</h1>"
            f"<p class='muted'>{payload['event_count']} events across {len(payload['dates'])} dates.</p>"
            f"<section class='card'><ul>{events}</ul></section>"
        ),
    )


def _render_contradictions_page(payload: dict) -> str:
    items = "".join(
        f"<li><span class='pill'>{escape(item['status'])}</span>{escape(item['subject_key'])}</li>"
        for item in payload["items"]
    ) or "<li>None</li>"
    return _layout(
        "Contradictions",
        (
            f"<h1>Contradictions</h1>"
            f"<p class='muted'>{payload['count']} records, {payload['open_count']} open.</p>"
            f"<section class='card'><ul>{items}</ul></section>"
        ),
    )


def create_server(vault_dir: Path | str, *, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
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
                    payload = build_truth_dashboard_payload(resolved_vault)
                    self._write_html(_render_dashboard(payload))
                    return
                if path == "/api/objects":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    self._write_json(build_objects_index_payload(resolved_vault, limit=limit, offset=offset))
                    return
                if path == "/objects":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    payload = build_objects_index_payload(resolved_vault, limit=limit, offset=offset)
                    self._write_html(_render_objects_index(payload))
                    return
                if path == "/api/object":
                    object_id = self._required(query, "id")
                    self._write_json(build_object_page_payload(resolved_vault, object_id))
                    return
                if path == "/object":
                    object_id = self._required(query, "id")
                    payload = build_object_page_payload(resolved_vault, object_id)
                    self._write_html(_render_object_page(payload))
                    return
                if path == "/api/topic":
                    object_id = self._required(query, "id")
                    self._write_json(build_topic_overview_payload(resolved_vault, object_id))
                    return
                if path == "/topic":
                    object_id = self._required(query, "id")
                    payload = build_topic_overview_payload(resolved_vault, object_id)
                    self._write_html(_render_topic_page(payload))
                    return
                if path == "/api/events":
                    self._write_json(build_event_dossier_payload(resolved_vault))
                    return
                if path == "/events":
                    payload = build_event_dossier_payload(resolved_vault)
                    self._write_html(_render_events_page(payload))
                    return
                if path == "/api/contradictions":
                    self._write_json(build_contradiction_browser_payload(resolved_vault))
                    return
                if path == "/contradictions":
                    payload = build_contradiction_browser_payload(resolved_vault)
                    self._write_html(_render_contradictions_page(payload))
                    return
                self.send_error(404, "Not Found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def _required(self, query: dict[str, list[str]], key: str) -> str:
            values = query.get(key)
            if not values or not values[0]:
                raise ValueError(f"missing required query param: {key}")
            return values[0]

        def _write_json(self, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a minimal local UI over knowledge.db")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)

    resolved_vault = resolve_vault_dir(args.vault_dir)
    server = create_server(resolved_vault, host=args.host, port=args.port)
    try:
        build_objects_index_payload(resolved_vault, limit=1, offset=0)
    except Exception as exc:
        print(f"ui server preflight failed: {exc}", file=sys.stderr)
        server.server_close()
        return 1

    print(json.dumps({"host": args.host, "port": args.port, "vault_dir": str(resolved_vault)}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
