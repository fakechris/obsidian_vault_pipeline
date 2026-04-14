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
            <a href="/">Home</a>
            <a href="/objects">Objects</a>
            <a href="/events">Event Dossier</a>
            <a href="/contradictions">Contradictions</a>
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
            "<section class='hero'>"
            "<h1>OpenClaw Truth UI</h1>"
            "<p class='muted'>Read-only browser over <code>knowledge.db</code>. JSON APIs remain available at <code>/api/*</code>, including <code>/api/objects</code>.</p>"
            "</section>"
            "<section class='grid stats'>"
            "<div class='card'><h2>Objects Indexed</h2>"
            f"<p>{payload['objects']['count']}</p></div>"
            "<div class='card'><h2>Contradictions Open</h2>"
            f"<p>{payload['contradictions']['open_count']}</p></div>"
            "<div class='card'><h2>Recent Events</h2>"
            f"<p>{payload['events']['count']}</p></div>"
            "</section>"
            "<section class='grid two-col'>"
            "<div class='section-stack'>"
            f"<section class='card'><h2>Recent Objects</h2><ul class='list-tight'>{object_items}</ul></section>"
            f"<section class='card'><h2>Recent Events</h2><ul class='list-tight'>{event_items}</ul></section>"
            "</div>"
            f"<section class='card'><h2>Contradiction Queue</h2><ul class='list-tight'>{contradiction_items}</ul></section>"
            "</section>"
        ),
    )


def _render_objects_index(payload: dict) -> str:
    query = payload.get("query", "")
    items = "".join(
        f'<li><a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a> '
        f'<span class="muted">({escape(item["object_id"])})</span></li>'
        for item in payload["items"]
    )
    return _layout(
        "Objects",
        (
            "<h1>Objects</h1>"
            "<form method='get' action='/objects'>"
            f"<input type='text' name='q' value='{escape(query)}' placeholder='Search objects' /> "
            "<button type='submit'>Search</button>"
            "</form>"
            f"<p class='muted'>{payload['count']} objects in current page.</p>"
            f"<section class='card'><ul class='list-tight'>{items}</ul></section>"
        ),
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
    section_nav = "".join(
        f'<a href="{escape(item["href"])}">{escape(item["label"])}</a>' for item in payload["section_nav"]
    )
    return _layout(
        f"Object: {payload['object']['title']}",
        (
            f"<section class='hero'><h1>Object: {escape(payload['object']['title'])}</h1>"
            f"<p class='muted'>{escape(payload['object']['object_id'])}</p>"
            "<div class='link-row'>"
            f"<a href='{escape(payload['links']['topic_path'])}'>Explore topic</a>"
            f"<a href='{escape(payload['links']['events_path'])}'>Related events</a>"
            f"<a href='{escape(payload['links']['contradictions_path'])}'>Contradictions</a>"
            "</div></section>"
            f"<nav class='subnav'>{section_nav}</nav>"
            "<section class='grid stats'>"
            f"<div class='card'><h2>Claims</h2><p>{payload['claim_count']}</p></div>"
            f"<div class='card'><h2>Relations</h2><p>{payload['relation_count']}</p></div>"
            f"<div class='card'><h2>Contradictions</h2><p>{payload['contradiction_count']}</p></div>"
            "</section>"
            "<section class='grid two-col'>"
            "<div class='section-stack'>"
            f"<section id='summary' class='card'><h2>Compiled Summary</h2><p>{escape(summary_text)}</p></section>"
            f"<section id='claims' class='card'><h2>Claims</h2><ul class='list-tight'>{claims}</ul></section>"
            "</div>"
            "<div class='section-stack'>"
            "<section class='card'><h2>Context</h2><dl class='meta-list'>"
            f"<div><dt>Object Kind</dt><dd>{escape(payload['context']['object_kind'])}</dd></div>"
            f"<div><dt>Source Slug</dt><dd>{escape(payload['context']['source_slug'])}</dd></div>"
            f"<div><dt>Canonical Path</dt><dd>{escape(payload['context']['canonical_path'])}</dd></div>"
            "</dl></section>"
            f"<section id='relations' class='card'><h2>Relations</h2><ul class='list-tight'>{relations}</ul></section>"
            f"<section id='contradictions' class='card'><h2>Contradictions</h2><ul class='list-tight'>{contradictions}</ul></section>"
            "</div>"
            "</section>"
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
            f"<section class='hero'><h1>Topic: {escape(payload['center']['title'])}</h1>"
            f"<p class='muted'>{payload['neighbor_count']} neighbors, {payload['edge_count']} edges.</p>"
            "<div class='link-row'>"
            f"<a href='{escape(payload['links']['center_object_path'])}'>Open center object</a>"
            f"<a href='{escape(payload['links']['events_path'])}'>Related events</a>"
            f"<a href='{escape(payload['links']['contradictions_path'])}'>Contradictions</a>"
            "</div></section>"
            "<section class='grid two-col'>"
            f"<section class='card'><h2>Center Summary</h2><p>{escape(payload['center_summary'])}</p></section>"
            f"<section class='card'><h2>Neighbors</h2><ul class='list-tight'>{neighbors}</ul></section>"
            "</section>"
        ),
    )


def _render_events_page(payload: dict) -> str:
    query = payload.get("query", "")
    date_nav = "".join(
        f"<a href='#date-{escape(section['date'])}'>{escape(section['date'])}</a>"
        for section in payload["date_sections"]
    )
    events = "".join(
        f'<section id="date-{escape(section["date"])}" class="card"><h2>{escape(section["date"])}</h2><ul class="list-tight">'
        + "".join(
            f"<li>{escape(item['event_type'])} - "
            f'<a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
            for item in section["events"]
        )
        + "</ul></section>"
        for section in payload["date_sections"]
    ) or "<li>None</li>"
    return _layout(
        "Event Dossier",
        (
            "<h1>Event Dossier</h1>"
            "<form method='get' action='/events'>"
            f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter events' /> "
            "<button type='submit'>Search</button>"
            "</form>"
            f"<p class='muted'>{payload['event_count']} events across {len(payload['dates'])} dates.</p>"
            f"<nav class='subnav'>{date_nav}</nav>"
            f"{events}"
        ),
    )


def _render_contradictions_page(payload: dict) -> str:
    status = payload.get("status", "")
    query = payload.get("query", "")
    items = "".join(
        "<li>"
        f"<span class='pill'>{escape(item['status'])}</span>{escape(item['subject_key'])}"
        + (
            " <span class='muted'>"
            + ", ".join(
                f'<a href="{escape(link["path"])}">{escape(link["object_id"])}</a>' for link in item["object_links"]
            )
            + "</span>"
            if item["object_links"]
            else ""
        )
        + "</li>"
        for item in payload["items"]
    ) or "<li>None</li>"
    return _layout(
        "Contradictions",
        (
            "<h1>Contradictions</h1>"
            "<form method='get' action='/contradictions'>"
            "<select name='status'>"
            f"<option value=''{' selected' if not status else ''}>all</option>"
            f"<option value='open'{' selected' if status == 'open' else ''}>open</option>"
            f"<option value='resolved'{' selected' if status == 'resolved' else ''}>resolved</option>"
            "</select> "
            f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter contradictions' /> "
            "<button type='submit'>Filter</button>"
            "</form>"
            f"<p class='muted'>{payload['count']} records, {payload['open_count']} open.</p>"
            f"<section class='card'><ul class='list-tight'>{items}</ul></section>"
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
                    q = query.get("q", [""])[0]
                    self._write_json(
                        build_objects_index_payload(resolved_vault, limit=limit, offset=offset, query=q)
                    )
                    return
                if path == "/objects":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    q = query.get("q", [""])[0]
                    payload = build_objects_index_payload(
                        resolved_vault, limit=limit, offset=offset, query=q
                    )
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
                    q = query.get("q", [""])[0]
                    self._write_json(build_event_dossier_payload(resolved_vault, query=q))
                    return
                if path == "/events":
                    q = query.get("q", [""])[0]
                    payload = build_event_dossier_payload(resolved_vault, query=q)
                    self._write_html(_render_events_page(payload))
                    return
                if path == "/api/contradictions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    self._write_json(
                        build_contradiction_browser_payload(resolved_vault, status=status, query=q)
                    )
                    return
                if path == "/contradictions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    payload = build_contradiction_browser_payload(resolved_vault, status=status, query=q)
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
