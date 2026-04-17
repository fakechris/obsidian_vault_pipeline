from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import re
import subprocess
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import yaml
from markdown_it import MarkdownIt

from ..identity import canonicalize_note_id
from ..knowledge_index import contradiction_object_ids, rebuild_compiled_summaries, resolve_contradictions
from ..pack_resolution import iter_compatible_packs
from ..packs.loader import PRIMARY_PACK_NAME
from ..runtime import VaultLayout, resolve_vault_dir
from ..ui.view_models import (
    build_action_queue_payload,
    build_atlas_browser_payload,
    build_briefing_payload,
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
    build_search_payload,
    build_signal_browser_payload,
    build_stale_summary_browser_payload,
    build_truth_dashboard_payload,
    build_topic_overview_payload,
)
from ..truth_api import (
    dismiss_action_queue_item,
    enqueue_signal_action,
    ensure_signal_ledger_synced,
    record_review_action,
    retry_action_queue_item,
    review_evolution_candidate,
    run_action_queue,
    run_next_action_queue_item,
)

_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_GITHUB_REPO_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s#]+)")
_EVOLUTION_LINK_TYPES = ["challenges", "replaces", "enriches", "confirms"]


def _shell_href(path: str, requested_pack: str = "") -> str:
    if not requested_pack:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}pack={quote(requested_pack, safe='')}"


def _shell_supports_research_nav(requested_pack: str = "") -> bool:
    try:
        return any(pack.name == PRIMARY_PACK_NAME for pack in iter_compatible_packs(requested_pack or None))
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
    ]
    if _shell_supports_research_nav(requested_pack):
        items.extend(
            [
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


def _layout(title: str, body: str, *, requested_pack: str = "") -> str:
    nav_items = "".join(
        f'<a href="{escape(_shell_href(path, requested_pack))}">{escape(label)}</a>'
        for label, path in _shell_nav_items(requested_pack)
    )
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
    error_text = str(payload.get("surface_error") or "").strip()
    extra = f"<p class='muted'>{escape(error_text)}</p>" if error_text else ""
    return f"<section class='card'><h2>Surface Contract</h2><p class='muted'>{detail}</p>{extra}</section>"


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
    resolver_rule_names = [str(item) for item in contract.get("resolver_rule_names", []) if str(item)]
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
                + (f" · {escape(', '.join(resolver_rule_names[:4]))}" if resolver_rule_names else "")
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
    return candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"


def _lookup_wikilink_target(vault_dir: Path, target: str, *, requested_pack: str = "") -> tuple[str, str] | None:
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
                    _shell_href(f"/object?id={quote(canonicalize_note_id(stem), safe='')}", requested_pack),
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


def _replace_wikilinks_with_markdown_links(vault_dir: Path, markdown: str, *, requested_pack: str = "") -> str:
    def replace_match(match: re.Match[str]) -> str:
        raw_inner = match.group(1)
        target_part, _, label_part = raw_inner.partition("|")
        label = label_part.strip() or target_part.split("#", 1)[0].strip()
        resolved = _lookup_wikilink_target(vault_dir, target_part, requested_pack=requested_pack)
        href = resolved[0] if resolved else _search_href(target_part.split("#", 1)[0].strip() or label, requested_pack)
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


def _linkify_related_knowledge_section(vault_dir: Path, markdown: str, *, requested_pack: str = "") -> str:
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
            output_lines.append(f'- [{emoji} {concept}]({href}) — {remainder}')
            continue
        output_lines.append(line)

    return "\n".join(output_lines)


def _render_markdown_note(vault_dir: Path, markdown: str, *, requested_pack: str = "") -> tuple[str, str]:
    frontmatter, body = _parse_frontmatter(markdown)
    github_repo_base = _infer_github_repo_base(frontmatter, body)
    rendered_body = _convert_box_table_fences(body, github_repo_base=github_repo_base)
    rendered_body = _rewrite_local_image_links(vault_dir, rendered_body)
    rendered_body = _replace_wikilinks_with_markdown_links(vault_dir, rendered_body, requested_pack=requested_pack)
    rendered_body = _linkify_related_knowledge_section(vault_dir, rendered_body, requested_pack=requested_pack)
    rendered_body = _linkify_keywords(rendered_body, requested_pack=requested_pack).strip()
    if not rendered_body:
        html_body = "<p class='muted'>Empty note.</p>"
    else:
        html_body = _MARKDOWN_RENDERER.render(rendered_body)
    return _render_frontmatter(frontmatter), html_body


def _render_note_page(vault_dir: Path, relative_path: str, markdown: str, payload: dict | None = None) -> str:
    requested_pack = payload.get("requested_pack", "") if payload else ""
    frontmatter_html, note_html = _render_markdown_note(vault_dir, markdown, requested_pack=requested_pack)
    source_note = None
    derived_notes: list[dict[str, str]] = []
    production_chain = None
    if payload:
        source_note = payload.get("provenance", {}).get("original_source_note")
        derived_notes = payload.get("provenance", {}).get("derived_deep_dives", [])
        production_chain = payload.get("production_chain")
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
        production_chain_html = (
            "<section class='card'>"
            "<h2>Production Chain</h2>"
            "<dl class='meta-list'>"
            f"<div><dt>Current Note</dt><dd>{escape(production_chain['note']['title'])}<div class='muted'>{escape(production_chain['note']['path'])}</div></dd></div>"
            f"<div><dt>Source Notes</dt><dd>{_render_named_note_links(production_chain['source_notes'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Deep Dives</dt><dd>{_render_named_note_links(production_chain['deep_dives'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Derived Objects</dt><dd>{_render_object_links(production_chain['objects'], requested_pack=requested_pack)}</dd></div>"
            f"<div><dt>Atlas / MOC Reach</dt><dd>{_render_named_note_links(production_chain['atlas_pages'], requested_pack=requested_pack)}</dd></div>"
            "</dl>"
            "</section>"
        )
    return _layout(
        f"Markdown Note: {relative_path}",
        (
            "<section class='hero'>"
            "<h1>Markdown Note</h1>"
            f"<p class='muted'>{escape(relative_path)}</p>"
            "</section>"
            f"{frontmatter_html}"
            f"{provenance_html}"
            f"{production_chain_html}"
            f"<section class='card'>{note_html}</section>"
        ),
        requested_pack=requested_pack,
    )


def _render_search_page(payload: dict) -> str:
    query = payload["query"]
    requested_pack = payload.get("requested_pack", "")
    object_items = "".join(
        f'<li><a href="{escape(item.get("object_path") or _object_href(item["object_id"], requested_pack=requested_pack))}">{escape(item["title"])}</a> '
        f'<span class="muted">({escape(item["object_id"])})</span></li>'
        for item in payload["objects"]
    ) or "<li class='muted'>No object hits.</li>"
    note_items = "".join(
        f'<li><a href="{escape(item.get("note_path") or _note_href(item["path"], requested_pack))}">{escape(item["title"])}</a> '
        f'<span class="pill">{escape(item["note_type"])}</span></li>'
        for item in payload["notes"]
    ) or "<li class='muted'>No note hits.</li>"
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
                f"<p class='muted'>{payload['object_count']} object hits, {payload['note_count']} note hits.</p>",
                "<section class='grid two-col'>",
                f"<section class='card'><h2>Objects</h2><ul class='list-tight'>{object_items}</ul></section>",
                f"<section class='card'><h2>Notes</h2><ul class='list-tight'>{note_items}</ul></section>",
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
    return "<select name='link_type'>" + "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected else ''}>{escape(option)}</option>"
        for option in _EVOLUTION_LINK_TYPES
    ) + "</select>"


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
        source_paths = ", ".join(
            f'<a href="{escape(_note_href(path, requested_pack))}">{escape(path)}</a>'
            for path in item["source_paths"]
        ) or "<span class='muted'>None</span>"
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


def _render_review_context_card(context: dict[str, object], *, title: str = "Review Context") -> str:
    latest_event_date = str(context.get("latest_event_date") or "")
    latest_event_html = escape(latest_event_date) if latest_event_date else "<span class='muted'>None</span>"
    stale_summary_ids = ", ".join(str(item) for item in context.get("stale_summary_object_ids", [])) or "None"
    contradiction_object_ids = ", ".join(str(item) for item in context.get("contradiction_object_ids", [])) or "None"
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
    signal_items = "".join(
        f"<li>{escape(str(signal['label']))}: {int(signal['count'])}</li>"
        for signal in summary["signals"]
    ) or "<li class='muted'>No production-chain gaps surfaced for this scope.</li>"
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


def _render_dashboard(payload: dict) -> str:
    requested_pack = payload.get("requested_pack", "")
    research_overview = payload.get("research_overview", {})
    research_overview_supported = research_overview.get("status") == "supported"
    signals_surface_contract = _render_surface_contract_card(payload["signals"])
    production_surface_contract = _render_surface_contract_card(payload["production"])
    object_items = "".join(
        f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", "")))}">{escape(item["title"])}</a></li>'
        for item in payload["objects"]["items"]
    ) or "<li>None</li>"
    contradiction_items = "".join(
        f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
        for item in payload["contradictions"]["items"]
    ) or "<li>None</li>"
    event_items = "".join(
        f"<li>{escape(item['event_date'])} - "
        f'<a href="{escape(item["object_path"])}">{escape(item["title"])}</a></li>'
        for item in payload["events"]["items"]
    ) or "<li>None</li>"
    stale_summary_items = "".join(
        f'<li><a href="{escape(item["object_path"])}">{escape(item["title"])}</a> '
        f"<span class='muted'>({escape(item['summary_text'])})</span></li>"
        for item in payload["stale_summaries"]["items"]
    ) or "<li>None</li>"
    evolution_items = _render_evolution_candidates(
        payload["evolution"]["items"],
        compact=False,
        requested_pack=requested_pack,
        next_path=_shell_href("/evolution", requested_pack),
    )
    production_gap_items = "".join(
        f'<li><span class="pill">{escape(item["stage_label"].replace("_", " "))}</span> '
        f'<a href="{escape(_note_href(item["note_path"], requested_pack))}">{escape(item["title"])}</a>'
        f"<div class='muted'>Missing: {escape(item['detail'])}</div></li>"
        for item in payload["production"]["weak_points"]
    ) or "<li class='muted'>No production-chain weak points surfaced.</li>"
    signal_items = "".join(
        f'<li><span class="pill">{escape(item["signal_type"])}</span> '
        f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
        f"<div class='muted'>{escape(item['detail'])}</div></li>"
        for item in payload["signals"]["items"]
    ) or "<li class='muted'>No active signals surfaced.</li>"
    priority_items = "".join(
        f'<li><span class="pill">{escape(item["kind"].replace("_", " "))}</span> '
        f'<a href="{escape(item["path"])}">{escape(item["label"])}</a>'
        f"<div class='muted'>{escape(item['detail'])}</div></li>"
        for item in payload["priorities"]
    ) or "<li class='muted'>No urgent maintenance items surfaced.</li>"
    stats_cards = [
        "<div class='card'><h2>Objects Indexed</h2>"
        f"<p>{payload['objects']['count']}</p></div>",
        "<div class='card'><h2>Signals</h2>"
        f"<p>{payload['signals']['count']}</p></div>",
        "<div class='card'><h2>Production Weak Points</h2>"
        f"<p>{payload['production']['weak_point_count']}</p></div>",
    ]
    if research_overview_supported:
        stats_cards[1:1] = [
            "<div class='card'><h2>Contradictions Open</h2>"
            f"<p>{payload['contradictions']['open_count']}</p></div>",
            "<div class='card'><h2>Recent Events</h2>"
            f"<p>{payload['events']['count']}</p></div>",
            "<div class='card'><h2>Stale Summaries</h2>"
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
    right_sections.append(_render_review_history(payload['recent_review_actions'], title='Recent Review Actions'))
    dashboard_body = "".join(
        [
            "<section class='hero'>",
            "<h1>OpenClaw Truth UI</h1>",
            "<p class='muted'>Read-only browser over <code>knowledge.db</code>. JSON APIs remain available at <code>/api/*</code>, including <code>/api/objects</code>.",
            f"{' Pack scope: ' + escape(requested_pack) + '.' if requested_pack else ''}</p>",
            "</section>",
            "<section class='grid stats'>",
            "".join(stats_cards),
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
        "OpenClaw Truth UI",
        dashboard_body,
        requested_pack=requested_pack,
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
    research_shell_enabled = bool(payload.get("research_shell_enabled", _shell_supports_research_nav(requested_pack)))
    next_path = _shell_href(f"/object?id={quote(str(payload['object']['object_id']), safe='')}", requested_pack)
    assembly_contract_card = _render_assembly_contract_card(payload)
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
    claims = "".join(f"<li>{escape(item['claim_text'])}</li>" for item in payload["claims"]) or "<li>None</li>"
    relations = "".join(
        f'<li><a href="{escape(_object_href(item["target_object_id"], item.get("target_path", ""), requested_pack=requested_pack))}">{escape(item.get("target_title", item["target_object_id"]))}</a>'
        f' <span class="muted">({escape(item["relation_type"])})</span></li>'
        for item in payload["relations"]
    ) or "<li>None</li>"
    contradictions = "".join(
        f'<li><span class="pill">{escape(item["status"])}</span>{escape(item["subject_key"])}</li>'
        for item in payload["contradictions"]
    ) or "<li>None</li>"
    stale_summary_signals = "".join(
        f"<li>{escape(reason)}</li>"
        for item in payload["stale_summary_details"]
        for reason in item["reason_texts"]
    ) or "<li class='muted'>No stale summary signals for this object.</li>"
    source_notes = "".join(
        f'<li><a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a> '
        f"<span class='muted'>({escape(item['note_type'])})</span></li>"
        for item in payload["provenance"]["source_notes"]
    ) or "<li>None</li>"
    mocs = "".join(
        f'<li><a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a></li>'
        for item in payload["provenance"]["mocs"]
    ) or "<li>None</li>"
    summary_text = payload["summary"]["summary_text"] if payload["summary"] else ""
    evolution = payload.get(
        "evolution",
        {"candidate_items": [], "accepted_links": [], "accepted_count": 0, "candidate_count": 0, "link_types": []},
    )
    section_nav_items = [
        item for item in payload["section_nav"] if research_shell_enabled or item["href"] != "#contradictions"
    ]
    section_nav = "".join(
        f'<a href="{escape(item["href"])}">{escape(item["label"])}</a>' for item in section_nav_items
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
        stats_cards.append(f"<div class='card'><h2>Contradictions</h2><p>{payload['contradiction_count']}</p></div>")
    right_sections = []
    if research_shell_enabled:
        right_sections.extend(
            [
                _render_review_context_card(payload['review_context']),
                _render_review_history(payload['review_history']),
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
            + assembly_contract_card
            + f"<nav class='subnav'>{section_nav}</nav>"
            + f"<section class='grid stats'>{''.join(stats_cards)}</section>"
            "<section class='grid two-col'>"
            "<div class='section-stack'>"
            f"<section id='summary' class='card'><h2>Compiled Summary</h2><p>{escape(summary_text)}</p></section>"
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
    research_shell_enabled = bool(payload.get("research_shell_enabled", _shell_supports_research_nav(requested_pack)))
    next_path = _shell_href(f"/topic?id={quote(str(payload['center']['object_id']), safe='')}", requested_pack)
    assembly_contract_card = _render_assembly_contract_card(payload)
    neighbors = "".join(
        f'<li><a href="{escape(_object_href(item["object_id"], item.get("object_path", ""), requested_pack=requested_pack))}">{escape(item["title"])}</a></li>'
        for item in payload["neighbors"]
    ) or "<li>None</li>"
    mocs = "".join(
        f'<li><a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a></li>'
        for item in payload["provenance"]["mocs"]
    ) or "<li>None</li>"
    evolution = payload.get(
        "evolution",
        {"candidate_items": [], "accepted_links": [], "accepted_count": 0, "candidate_count": 0, "link_types": []},
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
                _render_review_context_card(payload['review_context']),
                _render_review_history(payload['review_history']),
                "<section class='card'><h2>Quick Maintenance</h2>"
                f"{contradiction_entry}"
                f"{summary_form}"
                "</section>",
            ]
        )
    else:
        right_sections.append(_render_research_scope_notice(requested_pack))
    right_sections.append(_render_production_summary_card(payload['production_summary'], requested_pack=requested_pack))
    return _layout(
        f"Topic: {payload['center']['title']}",
        (
            f"<section class='hero'><h1>Topic: {escape(payload['center']['title'])}</h1>"
            f"<p class='muted'>{payload['neighbor_count']} neighbors, {payload['edge_count']} edges."
            + (f" Pack scope: {escape(requested_pack)}." if requested_pack else "")
            + "</p>"
            + f"<div class='link-row'>{''.join(hero_links)}</div></section>"
            + assembly_contract_card
            + "<section class='grid two-col'>"
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
        + "".join(
            f"<li>Row type {escape(str(row_type))}: {count}</li>"
            for row_type, count in timeline_contract["row_type_counts"].items()
        )
        + "".join(
            f"<li>Semantic role {escape(str(role))}: {count}</li>"
            for role, count in timeline_contract["semantic_roles"].items()
        )
    )
    model_notes = "".join(f"<li>{escape(note)}</li>" for note in payload["model_notes"])
    date_nav = "".join(
        f"<a href='#date-{escape(section['date'])}'>{escape(section['date'])}</a>"
        for section in payload["cluster_sections"]
    )
    events = "".join(
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
    ) or "<li>None</li>"
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
    contradiction_query_path = _shell_href(f"/contradictions?q={quote(query, safe='')}", requested_pack)
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
                assembly_contract_card,
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
    items = "".join(
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
    ) or "<li>None</li>"
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
    items = "".join(
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
    ) or "<li>None</li>"
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
    limit_note = (
        f" Showing the most recent {payload['limit']} production-chain entries in this browser window."
        if payload.get("is_limited")
        else ""
    )
    items = "".join(
        "<li>"
        f'<a href="{escape(_note_href(item["path"], requested_pack))}">{escape(item["title"])}</a>'
        + f" <span class='pill'>{escape(item['stage_label'].replace('_', ' '))}</span>"
        + f" <span class='pill'>{item['traceability']['counts']['deep_dives']} deep dives</span>"
        + f" <span class='pill'>{item['traceability']['counts']['objects']} objects</span>"
        + f" <span class='pill'>{item['traceability']['counts']['atlas_pages']} atlas pages</span>"
        + f"<div class='muted'>Deep Dives: {_render_named_note_links(item['traceability']['deep_dives'], requested_pack=requested_pack)}</div>"
        + f"<div class='muted'>Objects: {_render_object_links(item['traceability']['objects'], requested_pack=requested_pack)}</div>"
        + f"<div class='muted'>Atlas / MOC Reach: {_render_named_note_links(item['traceability']['atlas_pages'], requested_pack=requested_pack)}</div>"
        + "</li>"
        for item in payload["items"]
    ) or "<li class='muted'>No production chains found.</li>"
    weak_points = "".join(
        "<li>"
        f'<span class="pill">{escape(item["stage_label"].replace("_", " "))}</span> '
        f'<a href="{escape(_note_href(item["note_path"], requested_pack))}">{escape(item["title"])}</a>'
        f"<div class='muted'>Missing: {escape(item['detail'])}</div>"
        "</li>"
        for item in payload["weak_points"]
    ) or "<li class='muted'>No production-chain weak points surfaced.</li>"
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
                surface_contract_card,
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
    kind_counts = "".join(
        f"<span class='pill'>{escape(cluster_kind)}: {count}</span>"
        for cluster_kind, count in payload["cluster_kind_counts"].items()
    ) or "<span class='muted'>None</span>"
    items = "".join(
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
    ) or "<li class='muted'>No graph clusters found.</li>"
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
    edge_kind_counts = "".join(
        f"<span class='pill'>{escape(edge_kind)}: {count}</span>"
        for edge_kind, count in payload["edge_kind_counts"].items()
    ) or "<span class='muted'>None</span>"
    object_kind_counts = "".join(
        f"<span class='pill'>{escape(object_kind)}: {count}</span>"
        for object_kind, count in payload["object_kind_counts"].items()
    ) or "<span class='muted'>None</span>"
    summary_bullets = "".join(
        f"<li>{escape(item)}</li>"
        for item in payload["summary_bullets"]
    ) or "<li class='muted'>No cluster summary available.</li>"
    members = "".join(
        f'<li><a href="{escape(member["path"])}">{escape(member["title"])}</a></li>'
        for member in cluster["member_links"]
    ) or "<li class='muted'>No members.</li>"
    edges = "".join(
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
    ) or "<li class='muted'>No internal edges for this cluster.</li>"
    top_source_notes = "".join(
        f"<li>{escape(item['title'])} <span class='pill'>{item['object_count']} objects</span></li>"
        for item in payload["top_source_notes"]
    ) or "<li class='muted'>No source-note coverage.</li>"
    top_mocs = "".join(
        f"<li>{escape(item['title'])} <span class='pill'>{item['object_count']} objects</span></li>"
        for item in payload["top_mocs"]
    ) or "<li class='muted'>No atlas coverage.</li>"
    open_contradictions = "".join(
        f"<li><a href=\"{escape(item['path'])}\">{escape(item['subject_key'])}</a> <span class='pill'>{len(item['object_ids'])} objects</span></li>"
        for item in payload["open_contradictions"]
    ) or "<li class='muted'>No open contradictions in this cluster.</li>"
    stale_summaries = "".join(
        f"<li><a href=\"{escape(item['object_path'])}\">{escape(item['title'])}</a> <span class='pill'>{', '.join(escape(code) for code in item['reason_codes'])}</span></li>"
        for item in payload["stale_summaries"]
    ) or "<li class='muted'>No stale summaries in this cluster.</li>"
    related_clusters = "".join(
        "<li>"
        f"<a href=\"{escape(item['detail_path'])}\">{escape(item['display_title'])}</a> "
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
    ) or "<li class='muted'>No related clusters surfaced for this scope.</li>"
    related_cluster_groups = "".join(
        f"<li>{escape(item['display_name'])} <span class='pill'>{item['count']}</span>"
        + (
            f"<div class='muted'>{escape(', '.join(item['cluster_titles'][:3]))}</div>"
            if item["cluster_titles"]
            else ""
        )
        + "</li>"
        for item in payload["related_cluster_groups"]
    ) or "<li class='muted'>No neighborhood groups surfaced for this cluster.</li>"
    reading_routes = "".join(
        "<li>"
        f"<span class='pill'>#{item['route_rank']}</span> "
        f"{escape(item['display_name'])}: "
        f"<a href=\"{escape(item['detail_path'])}\">{escape(item['display_title'])}</a> "
        f"<span class='pill'>{escape(item['bridge_kind'])}</span> "
        f"<span class='pill'>{escape(item['bridge_band'])}</span>"
        f"<div class='muted'>Score: {item['route_score']} · {escape(item['route_reason'])}</div>"
        f"<div class='muted'>Bridge evidence: {escape(item['reason'])}</div>"
        "</li>"
        for item in payload["reading_routes"]
    ) or "<li class='muted'>No reading routes derived for this cluster.</li>"
    next_read_cluster = payload.get("next_read_cluster")
    next_read_route = (
        "<p>"
        f"<a href=\"{escape(next_read_cluster['detail_path'])}\">{escape(next_read_cluster['display_title'])}</a> "
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
    ) if next_read_cluster else "<p class='muted'>No next reading route surfaced for this cluster.</p>"
    relation_patterns = "".join(
        f"<li>{escape(item['display_name'])} <span class='pill'>{item['count']}</span></li>"
        for item in payload["relation_pattern_items"]
    ) or "<li class='muted'>No relation patterns in this cluster.</li>"
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
    type_counts = "".join(
        f"<span class='pill'>{escape(link_type)}: {count}</span>"
        for link_type, count in payload["type_counts"].items()
    ) or "<span class='muted'>None</span>"
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


def _render_signals_page(payload: dict) -> str:
    query = payload.get("query", "")
    selected_type = payload.get("signal_type", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = "/signals" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    surface_contract_card = _render_surface_contract_card(payload)
    governance_contract_card = _render_governance_contract_card(payload)
    options = ["", *sorted(payload["signal_type_explanations"].keys())]
    option_html = "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected_type else ''}>"
        f"{escape(option or 'all signal types')}</option>"
        for option in options
    )
    items = "".join(
        "<li>"
        f'<span class="pill">{escape(item["signal_type"])}</span> '
        f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
        f"<div class='muted'>{escape(item['detail'])}</div>"
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
            if item.get("recommended_action") and not item["recommended_action"].get("queue_status")
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
    ) or "<li class='muted'>No active signals found.</li>"
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
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
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
    first_useful_sign = payload.get("first_useful_sign")
    first_useful_sign_html = (
        "<li>"
        + f"<span class='pill'>{escape(str(first_useful_sign['kind']))}</span> "
        + f"<a href=\"{escape(str(first_useful_sign['path']))}\">{escape(str(first_useful_sign['title']))}</a>"
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
    insights = "".join(
        "<li>"
        + f"<span class='pill'>{escape(str(item['link_type']))}</span> "
        + f"<a href=\"{escape(str(item['path']))}\">{escape(str(item['title']))}</a>"
        + f"<div class='muted'>{escape(str(item['detail']))}</div>"
        + (
            f"<div class='muted'>Sources: {escape(', '.join(item.get('source_paths', [])))}</div>"
            if item.get("source_paths")
            else ""
        )
        + "</li>"
        for item in payload["insights"]
    ) or "<li class='muted'>No evolution insights surfaced.</li>"
    priority_items = "".join(
        "<li>"
        + f"<span class='pill'>{escape(str(item['kind']))}</span> "
        + f"<a href=\"{escape(str(item['path']))}\">{escape(str(item['title']))}</a>"
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
            if item.get("signal_id") and item.get("recommended_action") and not item["recommended_action"].get("queue_status")
            else ""
        )
        + "</li>"
        for item in payload["priority_items"]
    ) or "<li class='muted'>No priority items surfaced.</li>"
    recent_signals = "".join(
        f'<li><span class="pill">{escape(item["signal_type"])}</span> '
        f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a>'
        f"<div class='muted'>{escape(item['detail'])}</div></li>"
        for item in payload["recent_signals"]
    ) or "<li class='muted'>No recent signals.</li>"
    unresolved = "".join(
        f'<li><span class="pill">{escape(item["signal_type"])}</span> '
        f'<a href="{escape(item["source_path"])}">{escape(item["title"])}</a></li>'
        for item in payload["unresolved_issues"]
    ) or "<li class='muted'>No unresolved issues.</li>"
    changed_objects = "".join(
        f'<li><a href="{escape(item["path"])}">{escape(item["title"])}</a></li>'
        for item in payload["changed_objects"]
    ) or "<li class='muted'>No recent changed objects.</li>"
    active_topics = "".join(
        f'<li><a href="{escape(item["path"])}">{escape(item["title"])}</a> '
        f"<span class='muted'>({item['signal_count']} signals)</span></li>"
        for item in payload["active_topics"]
    ) or "<li class='muted'>No active topics surfaced.</li>"
    queue_summary = payload.get("queue_summary", {})
    failure_buckets = "".join(
        f"<li><span class='pill'>{escape(bucket)}</span> {count}</li>"
        for bucket, count in queue_summary.get("failure_buckets", {}).items()
    ) or "<li class='muted'>No failed actions.</li>"
    return _layout(
        "Working Memory Snapshot",
        "".join(
            [
                "<h1>Working Memory Snapshot</h1>",
                f"<p class='muted'>Generated at {escape(payload['generated_at'])}. {payload['recent_signal_count']} recent signals, {payload['unresolved_issue_count']} unresolved issues.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                "</p>",
                surface_contract_card,
                assembly_contract_card,
                governance_contract_card,
                f"<section class='card'><h2>First Useful Sign</h2><ul class='list-tight'>{first_useful_sign_html}</ul></section>",
                f"<section class='card'><h2>Insights</h2><ul class='list-tight'>{insights}</ul></section>",
                f"<section class='card'><h2>Priority Items</h2><ul class='list-tight'>{priority_items}</ul></section>",
                "<section class='card'><h2>Execution Surface</h2>",
                f"<p class='muted'>{queue_summary.get('queued_count', 0)} queued, ",
                f"{queue_summary.get('safe_queued_count', 0)} safe to auto-run, ",
                f"{queue_summary.get('running_count', 0)} running, ",
                f"{queue_summary.get('failed_count', 0)} failed.</p>",
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
    options = ["", "queued", "running", "succeeded", "failed", "dismissed", "obsolete"]
    option_html = "".join(
        f"<option value='{escape(option)}' {'selected' if option == selected_status else ''}>"
        f"{escape(option or 'all statuses')}</option>"
        for option in options
    )
    items = "".join(
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
            if item.get("status") in {"failed", "obsolete"}
            else ""
        )
        + (
            "<form method='post' action='/actions/dismiss' class='link-row'>"
            + f"<input type='hidden' name='action_id' value='{escape(str(item['action_id']))}' />"
            + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
            + "<button type='submit'>Dismiss</button>"
            + "</form>"
            if item.get("status") in {"queued", "failed", "obsolete", "running"}
            else ""
        )
        + "</li>"
        for item in payload["items"]
    ) or "<li class='muted'>No queued actions yet.</li>"
    return _layout(
        "Action Queue",
        "".join(
            [
                "<h1>Action Queue</h1>",
                "<p class='muted'>Asynchronous queue consumption is opt-in. Run <code>python -m openclaw_pipeline.commands.run_actions --vault-dir &lt;vault&gt; --loop</code> or start the UI with <code>--with-action-worker</code> to spawn a detached worker process.</p>",
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
                f"<p class='muted'>{payload['count']} actions in the current execution surface. {payload.get('queued_safe_count', 0)} queued safe actions. {payload.get('failed_count', 0)} failed actions.</p>",
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
    next_path = "/contradictions" + (f"?pack={quote(requested_pack, safe='')}" if requested_pack else "")
    assembly_contract_card = _render_assembly_contract_card(payload)
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
        + "".join(
            f"<li>Status bucket {escape(str(bucket))}: {count}</li>"
            for bucket, count in detection_contract["status_buckets"].items()
        )
        + "".join(
            f"<li>Status {escape(str(status_name))}: {escape(text)}</li>"
            for status_name, text in detection_contract["status_explanations"].items()
        )
    )
    items = "".join(
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
    ) or f"<li>{escape(payload['empty_state'])}</li>"
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
                assembly_contract_card,
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
    items = "".join(
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
    ) or "<li class='muted'>No stale summaries detected.</li>"
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
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_truth_dashboard_payload(resolved_vault, pack_name=pack_name)
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
                    self._write_json(build_search_payload(resolved_vault, query=q, pack_name=pack_name))
                    return
                if path == "/search":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_search_payload(resolved_vault, query=q, pack_name=pack_name)
                    self._write_html(_render_search_page(payload))
                    return
                if path == "/api/briefing":
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(build_briefing_payload(resolved_vault, pack_name=pack_name))
                    return
                if path == "/briefing":
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_html(_render_briefing_page(build_briefing_payload(resolved_vault, pack_name=pack_name)))
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
                if path == "/api/evolution":
                    q = query.get("q", [""])[0]
                    status = query.get("status", ["all"])[0] or "all"
                    link_type = query.get("link_type", [""])[0] or None
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/evolution", api=True):
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
                    if self._guard_research_route(pack_name=pack_name, route_path="/evolution", api=False):
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
                    self._write_json(build_object_page_payload(resolved_vault, object_id, pack_name=pack_name))
                    return
                if path == "/object":
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_object_page_payload(resolved_vault, object_id, pack_name=pack_name)
                    self._write_html(_render_object_page(payload))
                    return
                if path == "/api/topic":
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(build_topic_overview_payload(resolved_vault, object_id, pack_name=pack_name))
                    return
                if path == "/topic":
                    object_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_topic_overview_payload(resolved_vault, object_id, pack_name=pack_name)
                    self._write_html(_render_topic_page(payload))
                    return
                if path == "/api/events":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/events", api=True):
                        return
                    self._write_json(build_event_dossier_payload(resolved_vault, pack_name=pack_name, query=q))
                    return
                if path == "/events":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/events", api=False):
                        return
                    payload = build_event_dossier_payload(resolved_vault, pack_name=pack_name, query=q)
                    self._write_html(_render_events_page(payload))
                    return
                if path == "/api/atlas":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/atlas", api=True):
                        return
                    self._write_json(build_atlas_browser_payload(resolved_vault, pack_name=pack_name, query=q))
                    return
                if path == "/atlas":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/atlas", api=False):
                        return
                    payload = build_atlas_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    self._write_html(_render_atlas_page(payload))
                    return
                if path == "/api/deep-dives":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/deep-dives", api=True):
                        return
                    self._write_json(build_derivation_browser_payload(resolved_vault, pack_name=pack_name, query=q))
                    return
                if path == "/deep-dives":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/deep-dives", api=False):
                        return
                    payload = build_derivation_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    self._write_html(_render_derivations_page(payload))
                    return
                if path == "/api/production":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    self._write_json(build_production_browser_payload(resolved_vault, pack_name=pack_name, query=q))
                    return
                if path == "/production":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_production_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    self._write_html(_render_production_browser_page(payload))
                    return
                if path == "/api/clusters":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/clusters", api=True):
                        return
                    self._write_json(build_cluster_browser_payload(resolved_vault, pack_name=pack_name, query=q))
                    return
                if path == "/clusters":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/clusters", api=False):
                        return
                    payload = build_cluster_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    self._write_html(_render_clusters_page(payload))
                    return
                if path == "/api/cluster":
                    cluster_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/cluster", api=True):
                        return
                    self._write_json(build_cluster_detail_payload(resolved_vault, cluster_id=cluster_id, pack_name=pack_name))
                    return
                if path == "/cluster":
                    cluster_id = self._required(query, "id")
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/cluster", api=False):
                        return
                    payload = build_cluster_detail_payload(resolved_vault, cluster_id=cluster_id, pack_name=pack_name)
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
                if path == "/actions":
                    status = query.get("status", [""])[0] or None
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    payload = build_action_queue_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        status=status,
                        query=q,
                    )
                    self._write_html(_render_actions_page(payload))
                    return
                if path == "/api/summaries":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/summaries", api=True):
                        return
                    self._write_json(build_stale_summary_browser_payload(resolved_vault, pack_name=pack_name, query=q))
                    return
                if path == "/summaries":
                    q = query.get("q", [""])[0]
                    pack_name = query.get("pack", [""])[0] or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/summaries", api=False):
                        return
                    payload = build_stale_summary_browser_payload(resolved_vault, pack_name=pack_name, query=q)
                    self._write_html(_render_stale_summaries_page(payload))
                    return
                if path == "/note":
                    relative_path = self._required(query, "path")
                    pack_name = query.get("pack", [""])[0] or None
                    _, markdown = _read_vault_note(resolved_vault, relative_path)
                    payload = build_note_page_payload(resolved_vault, note_path=relative_path, pack_name=pack_name)
                    self._write_html(_render_note_page(resolved_vault, relative_path, markdown, payload))
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
                    if self._guard_research_route(pack_name=pack_name, route_path="/contradictions", api=True):
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
                    if self._guard_research_route(pack_name=pack_name, route_path="/contradictions", api=False):
                        return
                    payload = build_contradiction_browser_payload(
                        resolved_vault,
                        pack_name=pack_name,
                        status=status,
                        query=q,
                    )
                    self._write_html(_render_contradictions_page(payload))
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
                    if self._guard_research_route(pack_name=pack_name, route_path="/contradictions/resolve", api=True):
                        return
                    self._write_json(self._resolve_contradiction_action(form))
                    return
                if path == "/contradictions/resolve":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/contradictions/resolve", api=False):
                        return
                    self._resolve_contradiction_action(form)
                    self._redirect(self._form_first(form, "next").strip() or "/contradictions?status=resolved")
                    return
                if path == "/api/summaries/rebuild":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/summaries/rebuild", api=True):
                        return
                    self._write_json(self._rebuild_summary_action(form))
                    return
                if path == "/summaries/rebuild":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/summaries/rebuild", api=False):
                        return
                    self._rebuild_summary_action(form)
                    self._redirect(self._form_first(form, "next").strip() or "/summaries")
                    return
                if path == "/api/evolution/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/evolution/review", api=True):
                        return
                    self._write_json(self._review_evolution_action(form))
                    return
                if path == "/evolution/review":
                    pack_name = self._form_first(form, "pack").strip() or None
                    if self._guard_research_route(pack_name=pack_name, route_path="/evolution/review", api=False):
                        return
                    payload = self._review_evolution_action(form)
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

        def _guard_research_route(self, *, pack_name: str | None, route_path: str, api: bool) -> bool:
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
            contradiction_ids = [item.strip() for item in self._form_all(form, "contradiction_id") if item.strip()]
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
                affected_object_ids = contradiction_object_ids(resolved_vault, payload["contradiction_ids"])
                rebuild_payload = rebuild_compiled_summaries(resolved_vault, object_ids=affected_object_ids)
                payload["rebuilt_summary_count"] = rebuild_payload["objects_rebuilt"]
                payload["rebuilt_object_ids"] = rebuild_payload["object_ids"]
            else:
                affected_object_ids = contradiction_object_ids(resolved_vault, payload["contradiction_ids"])
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
            object_ids = [item.strip() for item in self._form_all(form, "object_id") if item.strip()]
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
            "openclaw_pipeline.commands.run_actions",
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
    parser.add_argument("--with-action-worker", action="store_true", help="Spawn a detached action worker process")
    parser.add_argument("--action-worker-interval", type=float, default=2.0, help="Polling interval for the detached action worker")
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
