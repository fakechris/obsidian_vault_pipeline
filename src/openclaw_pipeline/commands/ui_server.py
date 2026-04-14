from __future__ import annotations

import argparse
import json
import sqlite3
import re
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import yaml
from markdown_it import MarkdownIt

from ..identity import canonicalize_note_id
from ..runtime import VaultLayout, resolve_vault_dir
from ..ui.view_models import (
    build_atlas_browser_payload,
    build_contradiction_browser_payload,
    build_derivation_browser_payload,
    build_event_dossier_payload,
    build_object_page_payload,
    build_objects_index_payload,
    build_truth_dashboard_payload,
    build_topic_overview_payload,
)

_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_GITHUB_REPO_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s#]+)")


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
            <a href="/atlas">Atlas</a>
            <a href="/deep-dives">Deep Dives</a>
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


def _note_href(path: str) -> str:
    return f"/note?path={quote(path, safe='')}"


def _objects_search_href(query: str) -> str:
    return f"/objects?q={quote(query, safe='')}"


def _read_vault_note(vault_dir: Path, relative_path: str) -> tuple[Path, str]:
    candidate = (vault_dir / relative_path).resolve()
    try:
        candidate.relative_to(vault_dir.resolve())
    except ValueError as exc:
        raise ValueError("invalid note path") from exc
    if not candidate.is_file():
        raise ValueError(f"note not found: {relative_path}")
    return candidate, candidate.read_text(encoding="utf-8")


def _lookup_wikilink_target(vault_dir: Path, target: str) -> tuple[str, str] | None:
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
                return (f"/object?id={quote(canonicalize_note_id(stem), safe='')}", canonicalize_note_id(stem))
            return (_note_href(relative_path), relative_path)
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
        return (f"/object?id={quote(slug, safe='')}", slug)
    return (_note_href(relative_path), relative_path)


def _is_search_href(href: str) -> bool:
    return href.startswith("/objects?q=")


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
            return (
                f'<a href="{escape(value)}" target="_blank" rel="noopener noreferrer">{escape(value)}</a>'
            )
        if isinstance(value, (list, dict)):
            return escape(json.dumps(value, ensure_ascii=False))
        return escape(str(value))

    if not frontmatter:
        return ""
    rows = "".join(
        "<tr>"
        f"<th>{escape(str(key))}</th>"
        f"<td>{render_value(value)}</td>"
        "</tr>"
        for key, value in frontmatter.items()
    )
    return (
        "<section class='card'>"
        "<h2>Frontmatter</h2>"
        "<table><tbody>"
        f"{rows}"
        "</tbody></table>"
        "</section>"
    )


def _replace_wikilinks_with_markdown_links(vault_dir: Path, markdown: str) -> str:
    def replace_match(match: re.Match[str]) -> str:
        raw_inner = match.group(1)
        target_part, _, label_part = raw_inner.partition("|")
        label = label_part.strip() or target_part.split("#", 1)[0].strip()
        resolved = _lookup_wikilink_target(vault_dir, target_part)
        href = resolved[0] if resolved else _objects_search_href(target_part.split("#", 1)[0].strip() or label)
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
            if body and any("│" in row for row in body) and any("┌" in row or "├" in row or "└" in row for row in body):
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


def _linkify_keywords(markdown: str) -> str:
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
            rendered.append(_smart_markdown_link(keyword, _objects_search_href(keyword)))
        output.append(f"{prefix}：{'，'.join(rendered)}")
    return "\n".join(output)


def _linkify_related_knowledge_section(vault_dir: Path, markdown: str) -> str:
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
            resolved = _lookup_wikilink_target(vault_dir, concept)
            href = resolved[0] if resolved else _objects_search_href(concept)
            emoji = "🔍" if _is_search_href(href) else "🎯"
            output_lines.append(f'- [{emoji} {concept}]({href}) — {remainder}')
            continue
        output_lines.append(line)

    return "\n".join(output_lines)


def _render_markdown_note(vault_dir: Path, markdown: str) -> tuple[str, str]:
    frontmatter, body = _parse_frontmatter(markdown)
    github_repo_base = _infer_github_repo_base(frontmatter, body)
    rendered_body = _convert_box_table_fences(body, github_repo_base=github_repo_base)
    rendered_body = _replace_wikilinks_with_markdown_links(vault_dir, rendered_body)
    rendered_body = _linkify_related_knowledge_section(vault_dir, rendered_body)
    rendered_body = _linkify_keywords(rendered_body).strip()
    if not rendered_body:
        html_body = "<p class='muted'>Empty note.</p>"
    else:
        html_body = _MARKDOWN_RENDERER.render(rendered_body)
    return _render_frontmatter(frontmatter), html_body


def _render_note_page(vault_dir: Path, relative_path: str, markdown: str) -> str:
    frontmatter_html, note_html = _render_markdown_note(vault_dir, markdown)
    return _layout(
        f"Markdown Note: {relative_path}",
        (
            "<section class='hero'>"
            "<h1>Markdown Note</h1>"
            f"<p class='muted'>{escape(relative_path)}</p>"
            "</section>"
            f"{frontmatter_html}"
            f"<section class='card'>{note_html}</section>"
        ),
    )


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
    evergreen_path = payload["provenance"]["evergreen_path"]
    evergreen_html = (
        f'<a href="{escape(_note_href(evergreen_path))}">{escape(evergreen_path)}</a>'
        if evergreen_path
        else "<span class='muted'>None</span>"
    )
    canonical_path = payload["context"]["canonical_path"]
    canonical_path_html = (
        f'<a href="{escape(_note_href(canonical_path))}">{escape(canonical_path)}</a>'
        if canonical_path
        else "<span class='muted'>None</span>"
    )
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
    source_notes = "".join(
        f'<li><a href="{escape(_note_href(item["path"]))}">{escape(item["title"])}</a> '
        f"<span class='muted'>({escape(item['note_type'])})</span></li>"
        for item in payload["provenance"]["source_notes"]
    ) or "<li>None</li>"
    mocs = "".join(
        f'<li><a href="{escape(_note_href(item["path"]))}">{escape(item["title"])}</a></li>'
        for item in payload["provenance"]["mocs"]
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
            f"<a href='/deep-dives?q={escape(payload['object']['object_id'])}'>Source deep dives</a>"
            f"<a href='/atlas?q={escape(payload['object']['object_id'])}'>Atlas / MOC</a>"
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
            f"<div><dt>Canonical Path</dt><dd>{canonical_path_html}</dd></div>"
            "</dl></section>"
            "<section class='card'><h2>Provenance</h2><dl class='meta-list'>"
            f"<div><dt>Evergreen Markdown</dt><dd>{evergreen_html}</dd></div>"
            f"<div><dt>Source Notes</dt><dd><ul class='list-tight'>{source_notes}</ul></dd></div>"
            f"<div><dt>Atlas / MOC</dt><dd><ul class='list-tight'>{mocs}</ul></dd></div>"
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
            f"<a href='/deep-dives?q={escape(payload['center']['object_id'])}'>Source deep dives</a>"
            f"<a href='/atlas?q={escape(payload['center']['object_id'])}'>Atlas / MOC</a>"
            "</div></section>"
            "<section class='grid two-col'>"
            f"<section class='card'><h2>Center Summary</h2><p>{escape(payload['center_summary'])}</p></section>"
            f"<section class='card'><h2>Neighbors</h2><ul class='list-tight'>{neighbors}</ul></section>"
            f"<section class='card'><h2>Atlas / MOC</h2><ul class='list-tight'>{''.join(f'<li>{escape(item['title'])}</li>' for item in payload['provenance']['mocs']) or '<li>None</li>'}</ul></section>"
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
            (
                f"<li>{escape(item['event_type'])} - "
                f'<a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
                if item["event_type"] != "page_date"
                else f'<li><a href="/object?id={escape(item["object_id"])}">{escape(item["title"])}</a></li>'
            )
            for item in section["events"]
        )
        + "</ul></section>"
        for section in payload["date_sections"]
    ) or "<li>None</li>"
    return _layout(
        "Event Dossier",
        (
            "<h1>Event Dossier</h1>"
            "<p class='muted'>A timeline-oriented view over dated truth objects, not a separate event object model.</p>"
            "<form method='get' action='/events'>"
            f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter events' /> "
            "<button type='submit'>Search</button>"
            "</form>"
            f"<p class='muted'>{payload['event_count']} events across {len(payload['dates'])} dates.</p>"
            f"<nav class='subnav'>{date_nav}</nav>"
            f"{events}"
        ),
    )


def _render_atlas_page(payload: dict) -> str:
    query = payload.get("query", "")
    items = "".join(
        "<li>"
        f"{escape(item['title'])}"
        + (
            " <span class='muted'>"
            + ", ".join(
                f'<a href="/object?id={escape(member["object_id"])}">{escape(member["title"])}</a>'
                for member in item["members"]
            )
            + "</span>"
        )
        + "</li>"
        for item in payload["items"]
    ) or "<li>None</li>"
    return _layout(
        "Atlas / MOC Browser",
        (
            "<h1>Atlas / MOC Browser</h1>"
            "<form method='get' action='/atlas'>"
            f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter MOCs or objects' /> "
            "<button type='submit'>Search</button>"
            "</form>"
            f"<p class='muted'>{payload['count']} atlas/moc pages linked to indexed objects.</p>"
            f"<section class='card'><ul class='list-tight'>{items}</ul></section>"
        ),
    )


def _render_derivations_page(payload: dict) -> str:
    query = payload.get("query", "")
    items = "".join(
        "<li>"
        f"{escape(item['title'])}"
        + (
            " <span class='muted'>"
            + ", ".join(
                f'<a href="/object?id={escape(member["object_id"])}">{escape(member["title"])}</a>'
                for member in item["derived_objects"]
            )
            + "</span>"
        )
        + "</li>"
        for item in payload["items"]
    ) or "<li>None</li>"
    return _layout(
        "Deep Dive Derivations",
        (
            "<h1>Deep Dive Derivations</h1>"
            "<form method='get' action='/deep-dives'>"
            f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter deep dives or objects' /> "
            "<button type='submit'>Search</button>"
            "</form>"
            f"<p class='muted'>{payload['count']} deep dive notes linked to indexed objects.</p>"
            f"<section class='card'><ul class='list-tight'>{items}</ul></section>"
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
                if path == "/api/atlas":
                    q = query.get("q", [""])[0]
                    self._write_json(build_atlas_browser_payload(resolved_vault, query=q))
                    return
                if path == "/atlas":
                    q = query.get("q", [""])[0]
                    payload = build_atlas_browser_payload(resolved_vault, query=q)
                    self._write_html(_render_atlas_page(payload))
                    return
                if path == "/api/deep-dives":
                    q = query.get("q", [""])[0]
                    self._write_json(build_derivation_browser_payload(resolved_vault, query=q))
                    return
                if path == "/deep-dives":
                    q = query.get("q", [""])[0]
                    payload = build_derivation_browser_payload(resolved_vault, query=q)
                    self._write_html(_render_derivations_page(payload))
                    return
                if path == "/note":
                    relative_path = self._required(query, "path")
                    _, markdown = _read_vault_note(resolved_vault, relative_path)
                    self._write_html(_render_note_page(resolved_vault, relative_path, markdown))
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
