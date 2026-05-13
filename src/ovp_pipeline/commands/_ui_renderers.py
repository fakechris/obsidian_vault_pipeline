"""HTML rendering functions for the OVP UI server.

All page, card, and fragment renderers live here.  They are pure
functions: each receives a ``payload`` dict (or a few scalar args)
and returns an HTML string.  The companion ``ui_server.py`` module
owns routing and the HTTP handler.
"""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import threading
from html import escape
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml
from markdown_it import MarkdownIt

from ..identity import canonicalize_note_id
from ..pack_resolution import iter_compatible_packs
from ..packs.loader import PRIMARY_PACK_NAME
from ..runtime import VaultLayout
from ..ui.view_models import (
    DEFAULT_CANDIDATE_BROWSER_LIMIT,
    build_runtime_home_payload,
)

_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")
_FENCED_FRONTMATTER_RE = re.compile(r"^```ya?ml\s*\n---\n(.*?)\n---\n```\s*\n?", re.DOTALL)
_GITHUB_REPO_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s#]+)")
_EVOLUTION_LINK_TYPES = ["challenges", "replaces", "enriches", "confirms"]

_request_ctx = threading.local()


# BL-050: shell selection.  Reader shell renders at ``/`` and any
# top-level reader-tree path; Maintainer shell renders under ``/ops``.
# The HTTP handler stores the current request path before dispatch so
# ``_layout()`` can pick the right shell + nav without each renderer
# threading a ``shell=`` parameter.
def set_request_path(path: str) -> None:
    """Set per-request URL path (called by the HTTP handler)."""
    _request_ctx.path = path


def _current_request_path() -> str:
    return getattr(_request_ctx, "path", "")


def _is_ops_path(path: str) -> bool:
    """Return True iff ``path`` belongs to the Maintainer shell."""
    return path == "/ops" or path.startswith("/ops/")


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


_CANDIDATE_MERGE_AUTOFILL_THRESHOLD = 0.7
_INLINE_MEMBER_LINK_LIMIT = 8


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


def _reader_nav_items(requested_pack: str = "") -> list[tuple[str, str]]:
    """Reader shell nav.  Strictly reading-focused — no maintainer
    routes.  ``Map`` only when the pack supports research nav."""
    items: list[tuple[str, str]] = [
        ("Library", "/"),
        ("Search", "/search"),
        ("Topics", "/topics"),
    ]
    if _shell_supports_research_nav(requested_pack):
        items.append(("Map", "/map"))
    return items


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


# Kept as a thin alias so existing renderers that import the symbol
# don't break during the migration; they always use the active-shell
# variant.  Removing the alias is a follow-up.
def _shell_nav_items(
    requested_pack: str = "", *, reader_mode: bool = True
) -> list[tuple[str, str]]:
    if reader_mode:
        return _reader_nav_items(requested_pack)
    return _ops_nav_items(requested_pack)


def _build_runtime_home_payload_from_query(vault_dir: Path, query: dict[str, list[str]]) -> dict:
    pack_name = query.get("pack", [""])[0] or None
    return build_runtime_home_payload(vault_dir, pack_name=pack_name)


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


def _layout(
    title: str, body: str, *, requested_pack: str = "", auto_refresh_seconds: int | None = None
) -> str:
    is_ops_shell = _is_ops_path(_current_request_path())
    if is_ops_shell:
        nav_pairs = _ops_nav_items(requested_pack)
        cross_link_label = "← Back to Library"
        cross_link_href = _shell_href("/", requested_pack)
    else:
        nav_pairs = _reader_nav_items(requested_pack)
        cross_link_label = "→ Maintenance"
        cross_link_href = _shell_href("/ops", requested_pack)
    nav_items = "".join(
        f'<a href="{escape(_shell_href(path, requested_pack))}">{escape(label)}</a>'
        for label, path in nav_pairs
    )
    cross_link = (
        f'<a href="{escape(cross_link_href)}" class="cross-link">' f"{escape(cross_link_label)}</a>"
    )
    refresh_meta = (
        f'    <meta http-equiv="refresh" content="{int(auto_refresh_seconds)}" />\n'
        if auto_refresh_seconds and auto_refresh_seconds > 0
        else ""
    )
    brand_href = escape(_shell_href("/", requested_pack))
    return f"""<!doctype html>
<html lang="en" data-theme="light">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
{refresh_meta}    <meta name="ovp-runtime-refresh" content="{int(auto_refresh_seconds or 0)}" />
    <title>{escape(title)}</title>
    <link rel="icon" type="image/svg+xml" href="/static/monogram.svg" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+SC:wght@400;600&display=swap" />
    <link rel="stylesheet" href="/static/ovp-tokens.css" />
    <link rel="stylesheet" href="/static/ovp-ui.css" />
    <link rel="stylesheet" href="/static/ovp-pages.css" />
    <style>
      /* Page-local additions only — anything reusable belongs in
         /static/ovp-ui.css (kit-faithful) or /static/ovp-pages.css
         (OVP-specific page components).  */
      main.page {{ display: block; }}
      .nav {{ align-items: center; }}
      .nav .brand-mark {{ display: inline-flex; align-items: center; gap: 0.45rem;
        font-weight: 700; color: var(--text); margin-right: 0.4rem;
        letter-spacing: -0.01em; font-size: 1.05rem; line-height: 1; }}
      .nav .brand-mark img {{ width: 22px; height: 22px; display: block;
        border-radius: 0; }}
      .nav .brand-mark .dot {{ color: var(--accent); }}
      .nav .brand-mark:hover {{ text-decoration: none; opacity: 0.92; }}
      .nav a {{ line-height: 1.4; }}
      .nav .nav-tail {{ margin-left: auto; display: inline-flex; align-items: center;
        gap: 0.6rem; }}
      .nav .nav-tail .cross-link {{ margin-left: 0; }}
      @media (max-width: 780px) {{ main.page {{ padding: 1rem 1rem 2rem; }} }}
    </style>
    <script>
      // Pre-paint theme: read localStorage before first paint to
      // avoid flash-of-wrong-theme.  ?theme=light|dark may pin
      // server-side later; for now localStorage is the only source.
      (function () {{
        try {{
          var saved = localStorage.getItem('ovp-theme');
          if (saved === 'light' || saved === 'dark') {{
            document.documentElement.dataset.theme = saved;
          }}
        }} catch (e) {{}}
      }})();
    </script>
  </head>
  <body>
    <main class="page">
      <div class="shell">
        <div class="shell-head">
          <nav class="nav">
            <a class="brand-mark" href="{brand_href}">
              <img src="/static/monogram.svg" alt="" width="22" height="22" />
              <span>obsidian vault pipeline<span class="dot">.</span></span>
            </a>
            {nav_items}
            <span class="nav-tail">
              <span class="theme-toggle" role="group" aria-label="Theme">
                <button type="button" data-theme-set="light" id="ovp-theme-light">LIGHT</button>
                <button type="button" data-theme-set="dark"  id="ovp-theme-dark">DARK</button>
              </span>
              {cross_link}
            </span>
          </nav>
        </div>
        <div class="shell-body">
          {body}
        </div>
      </div>
    </main>
    <script>
      (function () {{
        var root = document.documentElement;
        var current = root.dataset.theme === 'dark' ? 'dark' : 'light';
        var btnLight = document.getElementById('ovp-theme-light');
        var btnDark  = document.getElementById('ovp-theme-dark');
        function paint(t) {{
          root.dataset.theme = t;
          if (btnLight) btnLight.classList.toggle('active', t === 'light');
          if (btnDark)  btnDark.classList.toggle('active',  t === 'dark');
        }}
        paint(current);
        function setTheme(t) {{
          paint(t);
          try {{ localStorage.setItem('ovp-theme', t); }} catch (e) {{}}
        }}
        if (btnLight) btnLight.addEventListener('click', function () {{ setTheme('light'); }});
        if (btnDark)  btnDark.addEventListener('click',  function () {{ setTheme('dark');  }});
      }})();
    </script>
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


def _anchor_title_for_note(relative_path: str, markdown: str) -> str:
    """Return the friendly title for a note anchor.

    Prefers the H1 line from the markdown body, then the YAML
    ``title:`` field *from the frontmatter block only* (CodeRabbit
    M — searching the whole body would pick up an example code
    fence's ``title:`` line), then the path basename.
    """
    body = markdown
    try:
        frontmatter, body = _parse_frontmatter(markdown)
    except Exception:
        frontmatter = {}
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


def _render_ask_about_this_button(
    anchor_kind: str,
    anchor_ref: str,
    *,
    title: str = "",
    requested_pack: str = "",
    label: str = "Ask about this",
) -> str:
    """Return the HTML for an "Ask about this" entry button (BL-087).

    Empty ``anchor_ref`` short-circuits to an empty string so we
    don't render a broken link when the renderer has no valid
    anchor to bind.  Reader-side only.
    """
    if not anchor_ref:
        return ""
    href = _ask_about_this_href(
        anchor_kind,
        anchor_ref,
        title=title,
        requested_pack=requested_pack,
    )
    return f'<a class="btn ghost ask-about-this" href="{escape(href)}">' f"💬 {escape(label)}</a>"


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
    return (
        candidate.read_bytes(),
        mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
    )


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


_THIN_NOTE_TYPES: frozenset[str] = frozenset(
    {
        # Generated / autonomous-action outputs and user-declared
        # interpretation surfaces don't have provenance, production
        # chains, source notes, or inbound captures.  Rendering the
        # full evergreen scaffold around them produces a page of empty
        # cards with the actual content buried at the bottom.
        "digest",
        "live-concept",
        "user-profile",
    }
)

_THIN_NOTE_PATH_PREFIXES: tuple[str, ...] = (
    # Everything under GENERATED is by definition agent-produced
    # content, not a Canonical-State object — apply the thin shell
    # even if the frontmatter type is missing or unrecognised.
    "40-Resources/Generated/",
)


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


def _render_live_concept_preamble(
    relative_path: str,
    frontmatter: dict[str, object],
    *,
    requested_pack: str = "",
) -> str:
    live = frontmatter.get("live") if isinstance(frontmatter.get("live"), dict) else {}
    objective = str(live.get("objective") or "").strip()
    active = bool(live.get("active", True))
    scope = live.get("scope_evergreens") or []
    if not isinstance(scope, list):
        scope = []
    scope_slugs = [str(s) for s in scope if s]
    triggers = live.get("triggers") if isinstance(live.get("triggers"), dict) else {}
    last_run_at = str(live.get("lastRunAt") or "").strip()
    last_summary = str(live.get("lastRunSummary") or "").strip()
    last_error = str(live.get("lastRunError") or "").strip()

    objective_html = (
        f"<p>{escape(objective)}</p>"
        if objective
        else "<p class='muted'><em>(no objective declared)</em></p>"
    )

    scope_html = ""
    if scope_slugs:
        items = "".join(
            f"<li><a href='{escape(_note_href(f'10-Knowledge/Evergreen/{slug}.md', requested_pack))}'>{escape(slug)}</a></li>"
            for slug in scope_slugs
        )
        scope_html = (
            "<tr><th>Scope evergreens</th>"
            f"<td><ul class='list-tight' style='margin:0'>{items}</ul></td></tr>"
        )
    else:
        scope_html = "<tr><th>Scope evergreens</th>" "<td class='muted'>(none declared)</td></tr>"

    trigger_items: list[str] = []
    if isinstance(triggers.get("on_ingest_match"), dict):
        ig = triggers["on_ingest_match"]
        target = escape(str(ig.get("concept_similarity_to") or ""))
        # ``threshold`` is operator-supplied via YAML frontmatter and
        # may not be numeric (Codex P2: a crafted live-concept could
        # inject markup here otherwise — coerce to string + escape
        # like the surrounding fields).
        threshold = escape(str(ig.get("threshold")))
        trigger_items.append(
            f"<li><strong>on_ingest_match</strong> — when an absorbed "
            f"source matches <code>{target}</code> (cosine ≥ {threshold})</li>"
        )
    if triggers.get("on_contradiction_against_view"):
        trigger_items.append(
            "<li><strong>on_contradiction_against_view</strong> — when "
            "the truth store records a contradiction against any "
            "scope evergreen</li>"
        )
    if triggers.get("weekly_resynthesis"):
        when = escape(str(triggers["weekly_resynthesis"]))
        trigger_items.append(
            f"<li><strong>weekly_resynthesis</strong> — recurring at " f"<code>{when}</code></li>"
        )
    triggers_html = (
        "<tr><th>Triggers</th><td><ul class='list-tight' style='margin:0'>"
        + "".join(trigger_items)
        + "</ul></td></tr>"
        if trigger_items
        else "<tr><th>Triggers</th><td class='muted'>(no triggers configured)</td></tr>"
    )

    last_run_html = ""
    if last_run_at:
        status_pill = (
            "<span class='pill warn'>error</span>" if last_error else "<span class='pill'>ok</span>"
        )
        summary_block = f"<div class='muted'>{escape(last_summary)}</div>" if last_summary else ""
        error_block = (
            f"<div class='muted'>Last error: {escape(last_error)}</div>" if last_error else ""
        )
        last_run_html = (
            f"<tr><th>Last agent run</th><td>"
            f"{_ts(last_run_at)} {status_pill}{summary_block}{error_block}"
            "</td></tr>"
        )
    else:
        last_run_html = (
            "<tr><th>Last agent run</th>"
            "<td class='muted'>(never — run "
            "<code>ovp-live-concept-scan --fire</code> to populate "
            "the agent sections below)</td></tr>"
        )

    active_pill = (
        "<span class='pill'>active</span>" if active else "<span class='pill muted'>inactive</span>"
    )

    return (
        "<section class='card'>"
        "<h2 style='margin-top:0'>About this Live Concept "
        f"{active_pill}</h2>"
        "<p>A user-declared <strong>Interpretation surface</strong> — "
        "an evolving view on one topic that the agent maintains "
        "from scope evergreens.  You own <code>## My take</code>; "
        "the agent owns <code>## Current synthesis</code>, "
        "<code>## Recent evidence</code>, and "
        "<code>## Tensions</code>.</p>"
        f"{objective_html}"
        "<table class='kv'>"
        f"<tr><th>File</th><td><code>{escape(relative_path)}</code></td></tr>"
        f"{scope_html}"
        f"{triggers_html}"
        f"{last_run_html}"
        "<tr><th>Refresh</th><td>"
        "<code>ovp-live-concept-scan --fire</code> "
        "(or wait for the next trigger to fire)"
        "</td></tr>"
        "</table>"
        "<p class='muted tiny'>Edit <code>## My take</code> directly "
        "in Obsidian.  Add or remove scope evergreens by editing "
        "<code>live.scope_evergreens</code> in the frontmatter.</p>"
        "</section>"
    )


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
        return _layout(
            f"Markdown Note: {relative_path}",
            (
                "<h1>Markdown Note</h1>"
                f"<p class='muted'>{escape(relative_path)}</p>"
                + f"<div class='entry-actions'>{ask_button}</div>"
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


# Lineage-card CSS pulled to a module-level constant so the
# render function doesn't ship a multi-line literal in the middle
# of its body.  Scope is intentionally local — promoting these
# styles to ``_layout`` would make them load on every page even
# when the lineage card isn't rendered.  Visual rules now live in
# /static/ovp-pages.css (.lineage-flow / .lineage-row / .lineage-arrow);
# this constant stays as an empty string so callers that interpolate
# it stay shape-stable and we don't have to chase every f-string.
_LINEAGE_CARD_STYLE = ""


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
            ("atlas_pages", "Atlas / MOC pages"),
        )
    )
    return (
        "<section class='card'>"
        f"<h2>{escape(title)}</h2>"
        "<table class='kv'>"
        f"<tr><th>Objects in scope</th><td>{int(summary['object_count'])}</td></tr>"
        f"<tr><th>Top Source Notes</th><td>{_render_named_note_links(summary['top_source_notes'], requested_pack=requested_pack)}</td></tr>"
        f"<tr><th>Atlas / MOC Reach</th><td>{_render_named_note_links(summary['top_atlas_pages'], requested_pack=requested_pack)}</td></tr>"
        "</table>"
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
    runtime_state_card = _render_runtime_state_card(payload.get("runtime_state"))
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
        next_path=_shell_href("/ops/evolution", requested_pack),
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

    def _tile(label, value, *, warn=False):
        warn_cls = " warn" if warn else ""
        return (
            "<div class='card' style='margin:0'>"
            f"<div class='muted tiny'>{label}</div>"
            f"<div class='metric-num{warn_cls}' style='margin-top:4px'>{value}</div>"
            "</div>"
        )

    stats_cards = [
        _tile("Objects Indexed", payload["objects"]["count"]),
        _tile("Signal Count", payload["signals"]["count"]),
        _tile("Weak Point Count", payload["production"]["weak_point_count"]),
    ]
    if research_overview_supported:
        stats_cards[1:1] = [
            _tile(
                "Contradictions Open",
                payload["contradictions"]["open_count"],
                warn=int(payload["contradictions"]["open_count"]) > 0,
            ),
            _tile("Event Count", payload["events"]["count"]),
            _tile(
                "Stale Summary Count",
                payload["stale_summaries"]["count"],
                warn=int(payload["stale_summaries"]["count"]) > 0,
            ),
            _tile("Evolution Candidates", payload["evolution"]["candidate_count"]),
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
                f"<section class='card'><h2><a href='{escape(_shell_href('/ops/evolution', requested_pack))}'>Evolution</a></h2>{evolution_items}</section>",
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
    foyer = payload.get("foyer") or {}
    foyer_today_path = str(foyer.get("today_path") or "/ops/today")
    foyer_queue_path = str(foyer.get("queue_path") or "/ops/queue")
    foyer_runs_path = str(foyer.get("runs_path") or "/ops/runs")
    foyer_today_summary = str(foyer.get("today_summary") or "—")
    foyer_queue_summary = str(foyer.get("queue_summary") or "—")
    last_run = foyer.get("last_run") or {}
    if last_run:
        last_run_summary = (
            f"{escape(str(last_run.get('workflow_type', '')))}"
            f" — <strong>{escape(str(last_run.get('status', '')))}</strong>"
            f" {_ts(str(last_run.get('started_at', ''))[:19])}"
        )
        last_run_link = (
            f"<a href='{escape(str(last_run.get('detail_href') or foyer_runs_path))}'>open →</a>"
        )
    else:
        last_run_summary = "<span class='muted'>no runs yet</span>"
        last_run_link = f"<a href='{escape(foyer_runs_path)}'>open →</a>"
    foyer_block = (
        "<section class='card'>"
        "<h2>Maintainer Foyer</h2>"
        "<table class='kv'>"
        f"<tr><th>Today</th><td>{escape(foyer_today_summary)}"
        f" <a href='{escape(foyer_today_path)}'>see →</a></td></tr>"
        f"<tr><th>Queue</th><td>{escape(foyer_queue_summary)}"
        f" <a href='{escape(foyer_queue_path)}'>see →</a></td></tr>"
        f"<tr><th>Last run</th><td>{last_run_summary} {last_run_link}</td></tr>"
        "</table>"
        "</section>"
    )

    dashboard_body = "".join(
        [
            "<h1>OVP Truth UI</h1>",
            "<p class='muted'>Read-only browser over <code>knowledge.db</code>. JSON APIs remain available at <code>/api/*</code>, including <code>/api/objects</code>.",
            f"{' Pack scope: ' + escape(requested_pack) + '.' if requested_pack else ''}</p>",
            foyer_block,
            runtime_card,
            runtime_state_card,
            run_history_card,
            "<section class='grid stats'>",
            "".join(stats_cards),
            "</section>",
            "<section style='display:grid;gap:1rem'>",
            "<section class='card'><h2>Workflow Map</h2><p class='muted'>Start here if you do not yet know which route to open. Each group maps one common operator workflow onto the current shell.</p></section>",
            workflow_groups_html,
            "<section class='card'><h2>Where To Start</h2><p class='muted'>Use the workflow map above to choose a route, then inspect the attention queues and knowledge surfaces below.</p></section>",
            orientation_assembly_contract,
            orientation_governance_contract,
            entry_sections_html,
            "</section>",
            "<section class='grid two-col'>",
            "<div style='display:grid;gap:1rem'>",
            "".join(left_sections),
            "</div>",
            "<div style='display:grid;gap:1rem'>",
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


# BL-051: ``_render_reader_home`` lives in ``commands/reader_home.py``
# now (file-size cap on this module) — re-exported for back-compat.
from .reader_home import _render_reader_home  # noqa: E402,F401


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


_OBJECTS_INDEX_PAGE_SIZES = (10, 50, 100, 200)


# Type-facet chip-rail rules now live in /static/ovp-pages.css.
_TYPE_FACET_STYLE = ""

# Default chip-rail size for the type facet.  12 covers the common
# CORE_OBJECT_KINDS set with one or two long-tail entries; the long
# tail of rare kinds stays accessible via the search box / API.
_TYPE_FACET_DEFAULT_LIMIT = 12


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
    from ..object_kinds import display_label

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
        from ..object_kinds import display_label

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


def _render_source_backlink_rail(payload: dict, *, requested_pack: str) -> str:
    rail = payload.get("source_backlink_rail") or {}
    rail = rail if isinstance(rail, dict) else {}
    evergreen = rail.get("evergreen") or {}
    evergreen = evergreen if isinstance(evergreen, dict) else {}
    evergreen_path = str(evergreen.get("path") or "")
    evergreen_html = (
        f'<a href="{escape(str(evergreen.get("jump_path") or _note_href(evergreen_path, requested_pack)))}">{escape(evergreen_path)}</a>'
        if evergreen_path
        else "<span class='muted'>None</span>"
    )

    def render_source_note(item: dict) -> str:
        href = str(item.get("jump_path") or _note_href(str(item.get("path") or ""), requested_pack))
        title = str(item.get("title") or item.get("slug") or "Source")
        note_type = str(item.get("note_type") or "source")
        excerpt = str(item.get("excerpt") or "")
        return (
            "<li>"
            f'<a href="{escape(href)}">{escape(title)}</a>'
            f" <span class='muted'>({escape(note_type)})</span>"
            + (f"<p class='muted'>{escape(excerpt)}</p>" if excerpt else "")
            + "</li>"
        )

    def render_atlas_page(item: dict) -> str:
        href = str(item.get("jump_path") or _note_href(str(item.get("path") or ""), requested_pack))
        title = str(item.get("title") or item.get("slug") or "Atlas page")
        return f'<li><a href="{escape(href)}">{escape(title)}</a></li>'

    def render_related_object(item: dict) -> str:
        object_id = str(item.get("object_id") or "")
        href = _object_href(object_id, str(item.get("path") or ""), requested_pack=requested_pack)
        title = str(item.get("title") or object_id or "Object")
        relation_type = str(item.get("relation_type") or "related")
        return (
            "<li>"
            f'<a href="{escape(href)}">{escape(title)}</a>'
            f" <span class='muted'>({escape(relation_type)})</span>"
            "</li>"
        )

    source_notes = [item for item in rail.get("source_notes") or [] if isinstance(item, dict)]
    source_html = (
        "".join(render_source_note(item) for item in source_notes)
        or "<li class='muted'>No source notes linked yet.</li>"
    )
    atlas_pages = [item for item in rail.get("atlas_pages") or [] if isinstance(item, dict)]
    atlas_html = (
        "".join(render_atlas_page(item) for item in atlas_pages)
        or "<li class='muted'>No atlas pages link here yet.</li>"
    )
    related_objects = [item for item in rail.get("related_objects") or [] if isinstance(item, dict)]
    related_html = (
        "".join(render_related_object(item) for item in related_objects)
        or "<li class='muted'>No related objects yet.</li>"
    )
    # Section heading is "Discoverable from" (not "Sources &
    # Backlinks") so the difference between this rail and the new
    # Source chain card is structurally clear:
    #   - Source chain  = pipeline lineage (URL → file → stages → evergreen)
    #   - Discoverable from = inbound wikilinks + atlas membership
    # The DOM id stays ``sources`` so existing in-page anchors and
    # the Sources nav link keep working.
    return (
        "<section id='sources' class='card'><h2>Discoverable from</h2>"
        f"<p class='muted'>{escape(str(rail.get('summary') or 'No source links yet.'))}</p>"
        "<table class='kv'>"
        f"<tr><th>Evergreen</th><td>{evergreen_html}</td></tr>"
        f"<tr><th>Source Notes</th><td><ul class='list-tight'>{source_html}</ul></td></tr>"
        f"<tr><th>Atlas Pages</th><td><ul class='list-tight'>{atlas_html}</ul></td></tr>"
        f"<tr><th>Related Objects</th><td><ul class='list-tight'>{related_html}</ul></td></tr>"
        "</table></section>"
    )


def _render_source_chain_card(payload: dict, *, requested_pack: str) -> str:
    """Post-BL-029 ``/object`` provenance card.

    Surfaces the chain ``Source URL → Source File → Pipeline Stages
    → Evergreen Markdown`` in the order data flows.  Replaces the
    pre-BL-029 Provenance card that listed evergreen + atlas links
    only (those moved to the renamed ``Discoverable from`` rail).
    """
    chain = payload.get("source_chain") or {}
    chain = chain if isinstance(chain, dict) else {}

    source_url = str(chain.get("source_url") or "")
    domain = str(chain.get("source_url_domain") or "")
    if source_url:
        url_html = (
            f'<a href="{escape(source_url)}" rel="noopener noreferrer" target="_blank">'
            f"{escape(source_url)}</a>"
            + (f" <span class='muted'>({escape(domain)})</span>" if domain else "")
        )
    else:
        url_html = "<span class='muted'>None recorded</span>"

    source_file = str(chain.get("source_file_path") or "")
    if source_file:
        source_file_html = (
            f'<a href="{escape(_note_href(source_file, requested_pack))}">'
            f"{escape(source_file)}</a>"
        )
    elif source_url:
        source_file_html = (
            "<span class='muted'>No active staging file resolved for this URL "
            "(may have been archived or never ingested).</span>"
        )
    else:
        source_file_html = "<span class='muted'>None</span>"

    stages = chain.get("provenance_stages") or []
    stages = [item for item in stages if isinstance(item, dict)]
    if stages:
        stage_rows = "".join(
            "<li>"
            f"<strong>{escape(str(item.get('stage') or ''))}</strong>"
            f" <span class='muted'>{escape(str(item.get('derived_at') or ''))}</span>"
            + (
                f" <span class='muted'>via {escape(str(item.get('metadata', {}).get('via') or ''))}</span>"
                if isinstance(item.get("metadata"), dict) and item["metadata"].get("via")
                else ""
            )
            + "</li>"
            for item in stages
        )
        stages_html = f"<ul class='list-tight'>{stage_rows}</ul>"
    else:
        stages_html = (
            "<span class='muted'>No provenance rows yet "
            "(BL-055 backfill writes ``ingest`` rows; ``extract`` / "
            "``promote`` land in BL-056).</span>"
        )

    evergreen_path = str(chain.get("evergreen_path") or "")
    legacy = bool(chain.get("evergreen_path_legacy"))
    if evergreen_path:
        evergreen_html = (
            f'<a href="{escape(_note_href(evergreen_path, requested_pack))}">'
            f"{escape(evergreen_path)}</a>"
        )
        if legacy:
            evergreen_html += (
                " <span class='pill warn' title='Path matches the pre-BL-029 "
                "&quot;*_深度解读.md&quot; archive pattern.  Re-run absorb to refresh "
                "the canonical evergreen file.'>legacy archive</span>"
            )
    else:
        evergreen_html = "<span class='muted'>None</span>"

    return (
        "<section class='card'><h2>Source chain</h2>"
        "<p class='muted'>The post-BL-029 pipeline lineage for this object: "
        "URL → active staging file → recorded provenance stages → canonical "
        "evergreen markdown.</p>"
        "<table class='kv'>"
        f"<tr><th>Source URL</th><td>{url_html}</td></tr>"
        f"<tr><th>Source File</th><td>{source_file_html}</td></tr>"
        f"<tr><th>Pipeline Stages</th><td>{stages_html}</td></tr>"
        f"<tr><th>Evergreen Markdown</th><td>{evergreen_html}</td></tr>"
        "</table></section>"
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
    center_path = (
        payload.get("links", {}).get("center_object_path")
        or payload["center"].get("object_path")
        or ""
    )
    ask_button = _render_ask_about_this_button(
        "object",
        str(center_path),
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
                    + "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
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
        "<form method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
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
        f"/ops/contradictions?q={quote(query, safe='')}", requested_pack
    )
    contradiction_browser_path = _shell_href("/ops/contradictions", requested_pack)
    contradiction_entry = (
        f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'><a href='{escape(contradiction_query_path)}'>Review visible contradictions</a></div>"
        if payload["scoped_open_contradiction_ids"] and query
        else (
            f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'><a href='{escape(contradiction_browser_path)}'>Review visible contradictions</a></div>"
            if payload["scoped_open_contradiction_ids"]
            else "<p class='muted'>No open contradictions in the visible event scope.</p>"
        )
    )
    return _layout(
        "Event Dossier",
        "".join(
            [
                "<h1>Event Dossier</h1>",
                _render_page_help(
                    "Event dossier",
                    what=(
                        "Audit-event projection grouped by object and date."
                        "  Each row is one timeline event (page projection,"
                        " heading projection, contradiction outcome, etc.)"
                        " linked back to its truth object."
                    ),
                    can=(
                        "Filter by free text, date range (single ?date= or"
                        " ?from_date= + ?to_date=), or limit (25/50/100/200)."
                        "  Drill into any object to see its full provenance."
                    ),
                    effect=(
                        "Read-only — the page just queries the audit ledger."
                        "  The Quick Maintenance card at the bottom does"
                        " expose actions (resolve / queue summary rebuild)"
                        " that mutate the truth store; the help block on"
                        " those buttons explains the consequences."
                    ),
                ),
                "<p class='muted'>A timeline-oriented view over dated truth objects, not a separate event object model.</p>",
                "<form method='get' action='/ops/events'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter events' /> ",
                "<label class='muted' style='margin-left:.5rem'>From "
                f"<input type='date' name='from_date' value='{escape(payload.get('from_date',''))}' />"
                "</label> ",
                "<label class='muted'>To "
                f"<input type='date' name='to_date' value='{escape(payload.get('to_date',''))}' />"
                "</label> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['cluster_count']} event clusters from {payload['event_count']} timeline rows across {len(payload['dates'])} dates.",
                f" Pack scope: {escape(requested_pack)}." if requested_pack else "",
                _format_event_date_filter_summary(
                    str(payload.get("from_date", "") or ""),
                    str(payload.get("to_date", "") or ""),
                ),
                f"{escape(limit_note)}</p>",
                _render_compiled_sections(lead_sections),
                operator_rail_card,
                assembly_contract_card,
                (f"<nav class='subnav'>{section_nav}</nav>" if section_nav else ""),
                _render_compiled_sections(remaining_sections),
                f"<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{type_breakdown}</div>",
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
            + f" <span class='pill'>{item['traceability']['counts']['objects']} objects</span>"
            + f" <span class='pill'>{item['traceability']['counts']['atlas_pages']} atlas pages</span>"
            + f"<div class='muted'>Chain status: {escape(str(item['traceability'].get('chain_status') or ''))}</div>"
            + f"<div class='muted'>Missing stages: {escape(', '.join(str(value).replace('_', ' ') for value in item['traceability'].get('missing_stages', [])) or 'None')}</div>"
            + f"<div class='muted'>Chain summary: {escape(str(item['traceability'].get('chain_summary') or ''))}</div>"
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
                "<form method='get' action='/ops/production'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter source notes, objects, or atlas' /> ",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{payload['count']} production-chain entries. {payload['counts']['source_notes']} source notes.",
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


def _render_evolution_browser_page(payload: dict) -> str:
    query = payload.get("query", "")
    status = payload.get("status", "all")
    selected_link_type = payload.get("link_type", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = _shell_href("/ops/evolution", requested_pack)
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
                "<form method='get' action='/ops/evolution' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
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
                f"<section class='card'><h2>Link Types</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{type_counts}</div></section>",
                f"<section class='card'><h2>Accepted Links</h2>{_render_evolution_links(payload['accepted_links'], empty_text='No accepted evolution links yet.')}</section>",
                f"<section class='card'><h2>Rejected Links</h2>{_render_evolution_links(payload['rejected_links'], empty_text='No rejected evolution links yet.')}</section>",
                f"<section class='card'><h2>Candidate Links</h2>{_render_evolution_candidates(payload['candidate_items'], reviewable=True, requested_pack=requested_pack, next_path=next_path)}</section>",
            ]
        ),
        requested_pack=requested_pack,
    )


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


def _render_queue_overview_page(payload: dict) -> str:
    """Maintainer queue landing page.

    Surfaces the four pending-review queues (concept candidates,
    contradictions, signals, action queue) in one place so the
    operator can tell whether triage is done without visiting four
    pages.  Healthy state (productive signals, succeeded actions,
    evergreen total) is surfaced separately so "no action needed"
    is visible too.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    queues = payload.get("queues") or []
    healthy = payload.get("healthy") or {}
    pending_total = int(payload.get("pending_total") or 0)

    intro = _render_page_help(
        "Maintainer queue",
        what=(
            "The four queues that need a human decision before the"
            " pipeline can move on: <strong>concept candidates</strong>,"
            " <strong>contradictions</strong>,"
            " <strong>signals waiting</strong>, and"
            " <strong>actions failed/blocked</strong>."
        ),
        can=(
            "Click any row to open that queue's detail page."
            "  Empty queues are listed under Healthy so you can confirm"
            " &ldquo;no action needed&rdquo; rather than wonder whether"
            " the pipeline is broken."
        ),
        effect=(
            "This page is just an aggregator — counts come from the"
            " same builders the four detail pages use, so the foyer"
            " never goes stale."
        ),
    )

    if pending_total == 0:
        pending_html = (
            "<section class='card'><h2>Pending review</h2>"
            "<p class='muted'>Nothing waiting in any queue.</p></section>"
        )
    else:
        rows: list[str] = []
        for queue in queues:
            count = int(queue.get("count") or 0)
            if count == 0:
                # Skip empty queues from the pending list — the
                # healthy-state card carries the "0 waiting" signal.
                continue
            label = str(queue.get("label") or queue.get("id") or "")
            href = str(queue.get("browse_path") or "")
            oldest_subject = str(queue.get("oldest_subject") or "")
            oldest_at = str(queue.get("oldest_at") or "")[:19]
            oldest_html = ""
            if oldest_subject:
                oldest_html = f" <span class='muted'>(oldest: {escape(oldest_subject)}"
                if oldest_at:
                    oldest_html += f" @ {escape(oldest_at)}"
                oldest_html += ")</span>"
            rows.append(
                f"<li><strong>{count}</strong> {escape(label)}"
                f"{oldest_html}"
                f" — <a href='{escape(href)}'>review →</a></li>"
            )
        pending_html = (
            "<section class='card'><h2>Pending review</h2>"
            f"<ul class='list-tight'>{''.join(rows)}</ul></section>"
        )

    healthy_html = (
        "<section class='card'><h2>Healthy (no action needed)</h2>"
        "<ul class='list-tight'>"
        f"<li>{int(healthy.get('productive_signals') or 0)} productive signals</li>"
        f"<li>{int(healthy.get('succeeded_actions') or 0)} succeeded actions</li>"
        f"<li>{int(healthy.get('evergreen_total') or 0)} evergreen objects in the truth store</li>"
        "</ul></section>"
    )

    body = "<h1>Maintainer Queue</h1>" + intro + pending_html + healthy_html
    return _layout("Queue", body, requested_pack=requested_pack)


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
    pagination = _render_candidates_pagination(payload)
    return _layout(
        "Candidate Workbench",
        "".join(
            [
                "<h1>Candidate Workbench</h1>",
                _render_page_help(
                    "Concept candidates",
                    what=(
                        "Concept slugs the absorb pipeline thinks deserve their"
                        " own Evergreen note.  They are still proposals — only a"
                        " <strong>Promote</strong> turns one into a canonical"
                        " object."
                    ),
                    can=(
                        "<strong>Promote</strong> creates an Evergreen note from"
                        " the candidate.  <strong>Merge</strong> rewrites links"
                        " into an existing object (target slug required)."
                        "  <strong>Reject</strong> drops the candidate as"
                        " spurious or duplicate."
                    ),
                    effect=(
                        "Promote and Merge mutate the truth store (objects,"
                        " relations) and trigger a re-index; Reject only marks"
                        " the candidate as resolved.  All three are reversible"
                        " by re-running the absorb step on the source."
                    ),
                ),
                "<p class='muted'>Review registry candidates before they become canonical Evergreen objects. "
                "Promote creates an active note, merge rewrites candidate links into an existing object, "
                "and reject removes the pending candidate artifact.</p>",
                "<form method='get' action='/ops/queue/concepts' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='text' name='q' value='{escape(query)}' placeholder='Filter candidates' />",
                f"<input type='hidden' name='limit' value='{escape(str(payload.get('limit') or DEFAULT_CANDIDATE_BROWSER_LIMIT))}' />",
                "<button type='submit'>Search</button>",
                "</form>",
                f"<p class='muted'>{escape(str(payload.get('count') or 0))} candidate(s) in view.</p>",
                pagination,
                f"<section class='card'><h2>Status</h2><div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>{status_counts}</div></section>",
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
    next_path = "/ops/signals" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
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
                "<form method='post' action='/ops/actions/enqueue' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
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
                _render_page_help(
                    "Signals",
                    what=(
                        "Detection-only observations the pipeline emits when it"
                        " notices something worth a human look — stale summaries,"
                        " open contradictions, missing provenance, etc."
                        "  Signals are passive: nothing happens until you queue"
                        " an action."
                    ),
                    can=(
                        "Filter by status (productive / waiting / failed/stalled)"
                        " or signal type.  <strong>Queue action</strong> sends"
                        " the recommended command to the action worker."
                        "  <strong>Dismiss</strong> tags the signal as not worth"
                        " acting on; both the row and any attached evidence"
                        " stay live."
                    ),
                    effect=(
                        "Queueing an action adds a row to /ops/queue/actions —"
                        " the worker runs it on its next cycle.  Until then the"
                        " truth store is unchanged.  Dismissing only updates the"
                        " signal ledger."
                    ),
                ),
                "<form method='get' action='/ops/queue/signals' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
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


def _render_actions_page(payload: dict) -> str:
    query = payload.get("query", "")
    selected_status = payload.get("status", "")
    requested_pack = payload.get("requested_pack", "")
    next_path = _shell_href("/ops/actions", requested_pack)
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
                f"<div class='muted'>Created at {_ts(item['created_at'])}</div>"
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
                "<form method='post' action='/ops/actions/retry' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
                + f"<input type='hidden' name='action_id' value='{escape(str(item['action_id']))}' />"
                + f"<input type='hidden' name='next' value='{escape(next_path)}' />"
                + "<button type='submit'>Retry</button>"
                + "</form>"
                if item.get("status") in {"failed", "blocked", "obsolete"}
                else ""
            )
            + (
                "<form method='post' action='/ops/actions/dismiss' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
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
                _render_page_help(
                    "Action queue",
                    what=(
                        "Commands the workflow worker should run.  Items get"
                        " here from <strong>/ops/queue/signals</strong> "
                        "(<em>Queue action</em>), periodic pipeline jobs (e.g."
                        " backfill cron), or manual enqueue via the CLI."
                    ),
                    can=(
                        "<strong>Run next</strong> dequeues a single item to"
                        " the action worker.  <strong>Run batch</strong>"
                        " processes up to 5 in one pass.  <strong>Retry</strong>"
                        " requeues a failed action; <strong>Dismiss</strong>"
                        " removes it from the queue without running."
                    ),
                    effect=(
                        "Run/Retry actually executes the queued command (may"
                        " mutate the truth store, the vault, or external"
                        " services depending on the action).  Dismiss only"
                        " marks the row as dismissed; nothing else changes."
                    ),
                ),
                "<p class='muted'>Asynchronous queue consumption is opt-in. Run <code>python -m ovp_pipeline.commands.run_actions --vault-dir &lt;vault&gt; --loop</code> or start the UI with <code>--with-action-worker</code> to spawn a detached worker process.</p>",
                "<form method='post' action='/ops/actions/run-next' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run next queued action</button>",
                "</form>",
                "<form method='post' action='/ops/actions/run-batch' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
                "<input type='hidden' name='limit' value='5' />",
                (
                    f"<input type='hidden' name='pack' value='{escape(requested_pack)}' />"
                    if requested_pack
                    else ""
                ),
                f"<input type='hidden' name='next' value='{escape(next_path)}' />",
                "<button type='submit'>Run 5 queued actions</button>",
                "</form>",
                "<form method='post' action='/ops/actions/run-batch' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
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
                "<form method='get' action='/ops/actions' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
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
    next_path = "/ops/contradictions" + (
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
                    f"<li>{_ts(history['timestamp'])} <span class='pill'>{escape(str(history['event_type']))}</span>"
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
                "<form method='post' action='/ops/contradictions/resolve' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
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
                _render_page_help(
                    "Contradictions",
                    what=(
                        "Pairs of claims the contradiction detector flagged"
                        " as semantically incompatible.  Each row points at"
                        " a positive-claim set and a negative-claim set; only"
                        " a human can pick which side is canonical."
                    ),
                    can=(
                        "<strong>resolved_keep_positive</strong> marks the"
                        " positive claims as canonical and supersedes the"
                        " negative side.  <strong>resolved_keep_negative</strong>"
                        " is the mirror image."
                        "  <strong>dismissed</strong> closes the row as a"
                        " false alarm without changing either side."
                        "  <strong>needs_human</strong> leaves it open for"
                        " deeper review."
                    ),
                    effect=(
                        "Keep-positive / Keep-negative tag the rejected claims"
                        " as superseded and trigger a re-compile of any"
                        " downstream summaries that quoted them."
                        "  Dismissed only updates the contradiction row."
                        "  ‘Rebuild summaries’ kicks the compile queue once."
                    ),
                ),
                "<form method='get' action='/ops/queue/contradictions'>",
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
                "<form id='contradiction-batch-form' method='post' action='/ops/contradictions/resolve' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
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
    next_path = "/ops/summaries" + (
        f"?pack={quote(requested_pack, safe='')}" if requested_pack else ""
    )
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
                    f"<li>{_ts(history['timestamp'])} <span class='pill'>{escape(str(history['event_type']))}</span>"
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
            + "<form method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>"
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
                "<form method='get' action='/ops/summaries'>",
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
                "<form id='summary-batch-form' method='post' action='/ops/summaries/rebuild' style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.9rem'>",
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


def _render_reuse_report_page(payload: dict) -> str:
    return _render_fragment_shell("Reuse Report", _render_reuse_report_fragment(payload))


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
        return "<section class='open-questions'><p class='muted'><em>No open questions yet.</em></p></section>"
    items = "".join(
        f"<li><strong>{escape(str(row.get('question') or ''))}</strong>"
        f" <small>{escape(str(row.get('ts') or ''))}</small></li>"
        for row in rows
    )
    return f"<section class='open-questions'><ul>{items}</ul></section>"


def _render_open_questions_page(payload: dict) -> str:
    return _render_fragment_shell("Open Questions", _render_open_questions_fragment(payload))


def _render_writing_prompts_fragment(payload: dict) -> str:
    body = str(payload.get("body") or "").strip()
    if not body:
        return "<section class='writing-prompts'><p class='muted'><em>No writing prompts captured yet.</em></p></section>"
    return f"<section class='writing-prompts'><pre>{escape(body)}</pre></section>"


def _render_writing_prompts_page(payload: dict) -> str:
    return _render_fragment_shell("Writing Prompts", _render_writing_prompts_fragment(payload))


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


# Timeline / Lineage UI strings — pulled out for translation /
# constant-vs-magic-number hygiene.  Body copy is intentionally
# Chinese to match the rest of the maintainer surface.
_TIMELINE_NEW_EVERGREENS_LABEL = "新增 evergreens"
_TIMELINE_ERROR_SAMPLE_HEADING = "Errors / skips"
# Cap the per-error row's ``subject`` rendering so a 2KB JSON dump
# doesn't blow out the day card.  140 covers most "absorb_parse_error
# on /Users/chris/.../<long-path>.md" cases without truncation.
_TIMELINE_ERROR_SUBJECT_MAX_CHARS = 140

# Day-card CSS pulled to a module-level constant so
# ``_render_timeline_page`` doesn't ship a multi-line literal in the
# middle of its body.  Inline rather than promoted to ``_layout`` —
# the styles are scoped to one route, lifting them globally would
# Timeline day-card rules now live in /static/ovp-pages.css.
_TIMELINE_DAY_CARD_STYLE = ""


def _render_timeline_page(payload: dict) -> str:
    """Daily digest of audit events.

    Sister to ``/ops/pulse`` (live tail) and ``/ops/events``
    (object-keyed dossier).  Pulse shows what's happening now;
    Events lets you drill down per object; Timeline answers the
    operator's day-to-day "what got created / went wrong today
    or yesterday" question without making them grep
    ``60-Logs/pipeline.jsonl`` themselves.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    window = int(payload.get("window_days") or 14)
    days = payload.get("days") or []

    timeline_help = _render_page_help(
        "Timeline",
        what=(
            "Day-by-day rollup of <code>audit_events</code> for the last"
            " ~14 days.  Sister to <strong>/ops/today</strong> (single"
            " day) and <strong>/ops/pulse</strong> (live tail)."
        ),
        can=(
            "Click any date heading or its <strong>See all N →</strong>"
            " link to drop into <strong>/ops/events</strong> filtered"
            " to that day.  Pills show top event types per day."
        ),
        effect=(
            "Read-only.  Following a drill-down link opens the events"
            " dossier with the date filter applied."
        ),
    )

    if not payload.get("available", True):
        body = (
            timeline_help + "<section class='card'>"
            "<h2>Timeline unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "<p>Run <code>ovp-knowledge-index</code> to populate "
            "<code>audit_events</code>.</p>"
            "</section>"
        )
        return _layout("Timeline", body, requested_pack=requested_pack)

    if not days:
        body = (
            timeline_help + "<section class='card'>"
            f"<h2>No events in the last {window} days</h2>"
            "<p class='muted'>The pipeline hasn't run in this window — "
            "check <code>60-Logs/pipeline.jsonl</code> for last activity.</p>"
            "</section>"
        )
        return _layout("Timeline", body, requested_pack=requested_pack)

    sections: list[str] = [_TIMELINE_DAY_CARD_STYLE]
    sections.append(timeline_help)
    sections.append(
        f"<p class='muted'>Showing the last {window} days of "
        f"<code>audit_events</code>.  {len(days)} day(s) with activity.</p>"
    )
    for day in days:
        date = escape(str(day.get("date", "")))
        total = int(day.get("total", 0))
        by_type = day.get("by_type") or {}
        samples = day.get("samples") or []
        errors = day.get("errors") or []

        # Sort by-type counts: highlighted ones first (in their canonical
        # order), then everything else by frequency.
        ordered_pills: list[tuple[str, int, bool, bool]] = []
        seen: set[str] = set()
        for t in payload.get("highlighted_types") or []:
            if t in by_type:
                ordered_pills.append((t, by_type[t], True, "error" in t or "broken" in t))
                seen.add(t)
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            if t in seen:
                continue
            ordered_pills.append((t, n, False, False))

        pills_html = "".join(
            "<span class='pill {cls}'>{type}: <strong>{n}</strong></span>".format(
                cls=("error" if is_error else ("highlight" if is_highlight else "")),
                type=escape(t),
                n=n,
            )
            for t, n, is_highlight, is_error in ordered_pills
        )

        samples_html = ""
        if samples:
            items = "".join(
                "<li><a href='{href}'>{title}</a> <span class='muted'>"
                "<code>{slug}</code></span></li>".format(
                    href=escape(str(s.get("note_href", ""))),
                    title=escape(str(s.get("title", "(untitled)"))),
                    slug=escape(str(s.get("slug", ""))),
                )
                for s in samples
            )
            samples_html = (
                "<div style='margin:.4rem 0'>"
                f"<h3 style='font-size:.95rem;margin:.4rem 0 .2rem 0'>"
                f"{_TIMELINE_NEW_EVERGREENS_LABEL} "
                f"(sample {len(samples)} of {by_type.get('evergreen_auto_promoted', 0)})</h3>"
                f"<ul class='list-tight' style='margin-left:1.2rem'>{items}</ul></div>"
            )

        errors_html = ""
        if errors:
            items = "".join(
                "<li>[{type}] <strong>{subject}</strong></li>".format(
                    type=escape(str(e.get("event_type", ""))),
                    subject=escape(str(e.get("subject", ""))[:_TIMELINE_ERROR_SUBJECT_MAX_CHARS]),
                )
                for e in errors
            )
            errors_html = (
                f"<div class='samples errors'><h3>{_TIMELINE_ERROR_SAMPLE_HEADING} "
                f"(sample {len(errors)})</h3><ul>{items}</ul></div>"
            )

        # Drill-down: every day card carries an explicit "open the
        # events dossier scoped to this date" link so the operator
        # can move from the histogram pill straight into the row-
        # level audit list.
        date_str = str(day.get("date", ""))
        drill_path = "/ops/events?date=" + quote(date_str, safe="") + "&limit=200"
        if requested_pack:
            drill_path += "&pack=" + quote(requested_pack, safe="")
        drill_html = (
            "<div class='tiny' style='margin-top:.5rem'>"
            f"<a href='{escape(drill_path)}'>"
            f"See all {total} events for {date} →</a></div>"
            if total
            else ""
        )

        sections.append(
            "<section class='card'>"
            f"<h2 style='margin:0 0 .3rem 0;font-size:1.1rem'>"
            f"<a href='{escape(drill_path)}'>{date}</a></h2>"
            f"<div class='muted tiny mono' style='margin-bottom:.7rem'>{total} events</div>"
            "<div class='grid' style='grid-template-columns:repeat(auto-fit,minmax(220px,1fr));"
            f"gap:.4rem;margin-bottom:.7rem'>{pills_html}</div>"
            f"{samples_html}"
            f"{errors_html}"
            f"{drill_html}"
            "</section>"
        )

    body = "".join(sections)
    return _layout("Timeline", body, requested_pack=requested_pack)


# Number of by-type pills shown in the trail of each /ops/today
# card.  3 fits one line on most screens; the long tail lives in
# /ops/timeline.
_TODAY_CARD_TOP_TYPES_LIMIT = 3
# Truncate sample event_type / subject so a card stays scannable.
_TODAY_SAMPLE_EVENT_TYPE_MAX_CHARS = 30
_TODAY_SAMPLE_SUBJECT_MAX_CHARS = 80
# /ops/runs* renderers — clip txn_id and the per-row subject so the
# table doesn't word-wrap on long pipeline.jsonl strings.
_RUN_TXN_ID_DISPLAY_MAX_CHARS = 30
_RUN_DETAIL_SUBJECT_MAX_CHARS = 120
# ISO-8601 timestamps are sliced to YYYY-MM-DDTHH:MM:SS for display.
_TS_DISPLAY_LEN = 19


# Today digest cards now live in /static/ovp-pages.css.
_TODAY_DIGEST_STYLE = ""


def _render_today_digest_page(payload: dict) -> str:
    """5-card today digest — the maintainer's "what happened today" view.

    Replaces the old "open ``/ops`` and squint at recent activity"
    workflow with five explicit cards (one per pipeline macro-stage)
    that summarise *today's* audit events.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    date = str(payload.get("date") or "")
    cards = payload.get("cards") or []

    if not payload.get("available", True):
        body = (
            "<section class='card'>"
            "<h2>Today digest unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "<p>Run <code>ovp-knowledge-index</code> to populate "
            "<code>audit_events</code>.</p>"
            "</section>"
        )
        return _layout(f"Today — {date}", body, requested_pack=requested_pack)

    sections: list[str] = [_TODAY_DIGEST_STYLE]
    sections.append(
        _render_page_help(
            "Today digest",
            what=(
                "Five-card summary of what the pipeline did today (UTC),"
                " grouped by macro-stage: intake, absorb, synthesis,"
                " governance, failures.  Counts come from"
                " <code>audit_events</code>."
            ),
            can=(
                "Click <strong>See all N →</strong> on any card to drop"
                " into <strong>/ops/events</strong> filtered to that day."
                "  Use the prev/next pivots to step through history."
            ),
            effect=(
                "All links read from the audit ledger.  This page mutates"
                " nothing — it's a window onto the pipeline's day."
            ),
        )
    )
    prev_path = str(payload.get("prev_date_path") or "")
    next_path = str(payload.get("next_date_path") or "")
    prev_date = str(payload.get("prev_date") or "")
    next_date = str(payload.get("next_date") or "")
    pivot_parts: list[str] = []
    if prev_path:
        pivot_parts.append(f"<a href='{escape(prev_path)}'>← {escape(prev_date)}</a>")
    pivot_parts.append(f"<strong>Today: {escape(date)}</strong>")
    if next_path:
        pivot_parts.append(f"<a href='{escape(next_path)}'>{escape(next_date)} →</a>")
    sections.append(f"<p class='muted'>{' · '.join(pivot_parts)}</p>")
    sections.append(
        f"<p class='muted'>Audit events recorded on "
        f"<strong>{escape(date)}</strong> (UTC).  Click "
        f"<a href='/ops/timeline'>Timeline</a> for the multi-day view.</p>"
    )
    sections.append("<div class='grid stats' style='margin-top:1rem'>")
    for card in cards:
        card_id = str(card.get("id") or "")
        label = str(card.get("label") or card_id)
        total = int(card.get("total") or 0)
        by_type = card.get("by_type") or {}
        samples = card.get("samples") or []
        see_all_path = str(card.get("see_all_path") or "")

        # Failures get the warn-tinted big number; empty totals fade to
        # border-strong so they read as "nothing today" without
        # distracting from cards that DO have activity.
        warn_cls = " warn" if card_id == "failures" and total > 0 else ""
        empty_style = "color:var(--border-strong)" if total == 0 else ""

        # Type breakdown — top-N types as a tail line.
        type_pills = ""
        if by_type:
            top = sorted(by_type.items(), key=lambda x: -x[1])[:_TODAY_CARD_TOP_TYPES_LIMIT]
            type_pills = " · ".join(f"<code>{escape(t)}</code>×{n}" for t, n in top)

        sample_html = ""
        if samples:
            items = "".join(
                "<li><span class='muted'>{type}</span> <strong>{subject}</strong></li>".format(
                    type=escape(str(s.get("event_type", ""))[:_TODAY_SAMPLE_EVENT_TYPE_MAX_CHARS]),
                    subject=escape(str(s.get("subject", ""))[:_TODAY_SAMPLE_SUBJECT_MAX_CHARS]),
                )
                for s in samples
            )
            sample_html = (
                "<div style='margin-top:.6rem;padding-top:.6rem;"
                "border-top:1px solid var(--border)'>"
                f"<ul class='list-tight tiny'>{items}</ul></div>"
            )

        see_all_html = ""
        if see_all_path and total > len(samples):
            see_all_html = (
                "<div class='tiny' style='margin-top:.5rem'>"
                f"<a href='{escape(see_all_path)}'>See all {total} →</a>"
                "</div>"
            )

        sections.append(
            "<div class='card' style='margin:0'>"
            f"<div class='muted tiny'>{escape(label)}</div>"
            f"<div class='metric-num{warn_cls}' style='margin-top:4px;{empty_style}'>{total}</div>"
            f"<div class='muted tiny' style='margin-top:6px'>{type_pills}</div>"
            f"{sample_html}"
            f"{see_all_html}"
            "</div>"
        )
    sections.append("</div>")

    body = "".join(sections)
    return _layout(f"Today — {date}", body, requested_pack=requested_pack)


# Runs index table rules now live in /static/ovp-pages.css.
_RUNS_INDEX_STYLE = ""


def _render_runs_index_page(payload: dict) -> str:
    """List of recent transactions with status + click-through.

    Each row is one ``transaction_started`` event; the ``status``
    cell reflects whether a matching ``transaction_completed`` row
    was found (otherwise ``running`` for fresh, ``stale`` for >6h
    without a completion event).
    """
    requested_pack = str(payload.get("requested_pack") or "")
    runs = payload.get("runs") or []

    runs_help = _render_page_help(
        "Runs",
        what=(
            "Index of pipeline transactions (one row per"
            " <code>transaction_started</code> audit event) grouped"
            " by calendar day with status, workflow, and event count."
            "  ‘Idle’ markers surface days the pipeline did not run."
        ),
        can=(
            "Click any <code>txn_id</code> to inspect that run's"
            " event timeline.  Use the window pivot (Last 10 / 30 /"
            " 100) to widen the lens when triaging."
        ),
        effect=(
            "Read-only.  Per-run drill-down is also read-only — it"
            " just reads from <code>audit_events</code>."
        ),
    )

    if not payload.get("available", True):
        body = (
            runs_help + "<section class='card'>"
            "<h2>Runs index unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "</section>"
        )
        return _layout("Runs", body, requested_pack=requested_pack)

    if not runs:
        body = (
            runs_help + "<section class='card'>"
            "<h2>No transactions found</h2>"
            "<p class='muted'>No <code>transaction_started</code> events "
            "in <code>audit_events</code>.</p>"
            "</section>"
        )
        return _layout("Runs", body, requested_pack=requested_pack)

    def _row_html(r: dict) -> str:
        return (
            "<tr>"
            "<td><code>{ts}</code></td>"
            "<td>{type}</td>"
            "<td class='status-{status}'>{status}</td>"
            "<td>{events}</td>"
            "<td><a href='{href}'>{txn_id}</a></td>"
            "</tr>".format(
                ts=escape(str(r.get("started_at", "")[:_TS_DISPLAY_LEN])),
                type=escape(str(r.get("workflow_type", ""))),
                status=escape(str(r.get("status", ""))),
                events=int(r.get("event_count") or 0),
                href=escape(str(r.get("detail_href", ""))),
                txn_id=escape(str(r.get("txn_id", ""))[:_RUN_TXN_ID_DISPLAY_MAX_CHARS]),
            )
        )

    day_groups = payload.get("day_groups") or []
    limit_value = int(payload.get("limit", len(runs)) or len(runs))
    window_days = payload.get("window_days")
    if window_days is None:
        window_text = ""
    elif window_days == 0:
        window_text = " (oldest from today)"
    else:
        window_text = f" (oldest from {window_days} day{'s' if window_days != 1 else ''} ago)"

    if day_groups:
        sections: list[str] = []
        for group in day_groups:
            date = str(group.get("date") or "")
            count = int(group.get("count") or 0)
            if group.get("idle"):
                sections.append(f"<h3 class='muted'>{escape(date)} — Idle (no scheduled run)</h3>")
                continue
            day_runs = group.get("runs") or []
            sections.append(
                f"<h3>{escape(date)} — {count} run{'s' if count != 1 else ''}</h3>"
                "<table class='data-table'>"
                "<thead><tr><th>Started</th><th>Workflow</th><th>Status</th>"
                "<th>Events</th><th>Run</th></tr></thead>"
                f"<tbody>{''.join(_row_html(r) for r in day_runs)}</tbody>"
                "</table>"
            )
        runs_html = "".join(sections)
    else:
        runs_html = (
            "<table class='data-table'>"
            "<thead><tr><th>Started</th><th>Workflow</th><th>Status</th>"
            "<th>Events</th><th>Run</th></tr></thead>"
            f"<tbody>{''.join(_row_html(r) for r in runs)}</tbody>"
            "</table>"
        )

    # Window-size pivot links.  ``limit=`` exposes how the cap is
    # applied; the operator can widen the window when triaging
    # whether a regression is recent or longstanding.
    def _runs_href(new_limit: int) -> str:
        params: list[tuple[str, str]] = []
        if requested_pack:
            params.append(("pack", requested_pack))
        if new_limit and new_limit != 30:
            params.append(("limit", str(new_limit)))
        if not params:
            return "/ops/runs"
        return "/ops/runs?" + "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params
        )

    pivot_options = (
        (10, "Last 10"),
        (30, "Last 30"),
        (100, "Last 100"),
    )
    pivot_links = " · ".join(
        (
            f"<strong>{escape(label)}</strong>"
            if value == limit_value
            else f"<a href='{escape(_runs_href(value))}'>{escape(label)}</a>"
        )
        for value, label in pivot_options
    )

    body = (
        f"{_RUNS_INDEX_STYLE}"
        + runs_help
        + f"<p class='muted'>Showing last {limit_value} run(s){window_text}. "
        f"Click a <code>txn_id</code> to see the full event timeline.</p>"
        + f"<p class='muted'>Window: {pivot_links}</p>"
        + f"{runs_html}"
    )
    return _layout("Runs", body, requested_pack=requested_pack)


# Run detail rules now live in /static/ovp-pages.css.
_RUN_DETAIL_STYLE = ""

_BRACKET_EVENT_TYPES = frozenset(
    {
        "transaction_started",
        "transaction_completed",
    }
)

_ERROR_EVENT_TYPE_PREFIXES = (
    "absorb_parse_error",
    "absorb_schema_drift",
    "broken_link",
    "github_intake_error",
    "article_error",
    "image_download_error",
)


def _render_run_detail_page(payload: dict) -> str:
    """Per-transaction event timeline.

    Renders every event tagged with this run's ``txn_id`` (or
    sharing the bracketing ``session_id``) in chronological order
    so the operator can scan the full sequence of stages, successes
    and failures of one run on a single page.
    """
    requested_pack = str(payload.get("requested_pack") or "")
    txn_id = str(payload.get("txn_id") or "")
    workflow_type = str(payload.get("workflow_type") or "(unknown)")
    started_at = str(payload.get("started_at") or "")
    completed_at = str(payload.get("completed_at") or "(still running)")
    events = payload.get("events") or []

    if not payload.get("available", True):
        body = (
            "<section class='card'>"
            f"<h2>Run {escape(txn_id)} unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "</section>"
        )
        return _layout(
            f"Run {txn_id[:_RUN_TXN_ID_DISPLAY_MAX_CHARS]}",
            body,
            requested_pack=requested_pack,
        )

    header = (
        f"{_RUN_DETAIL_STYLE}"
        "<div class='card'>"
        "<dl>"
        f"<dt>Run id</th><td><code>{escape(txn_id)}</code></dd>"
        f"<dt>Workflow</th><td>{escape(workflow_type)}</dd>"
        f"<dt>Started</th><td><code>{escape(started_at)}</code></dd>"
        f"<dt>Completed</th><td><code>{escape(completed_at)}</code></dd>"
        f"<dt>Events</th><td>{len(events)}</dd>"
        "</dl></div>"
    )

    rows = []
    for ev in events:
        et = str(ev.get("event_type", ""))
        css_classes: list[str] = []
        if et == "transaction_started":
            css_classes.append("bracket")
        elif et == "transaction_completed":
            css_classes.append("bracket bracket-completed")
        elif any(et.startswith(p) for p in _ERROR_EVENT_TYPE_PREFIXES):
            css_classes.append("error")
        cls = (" class='" + " ".join(css_classes) + "'") if css_classes else ""
        rows.append(
            f"<tr{cls}>"
            f"<td class='ts'>{escape(str(ev.get('timestamp',''))[:_TS_DISPLAY_LEN])}</td>"
            f"<td class='type'>{escape(et)}</td>"
            f"<td class='subject'>{escape(str(ev.get('subject',''))[:_RUN_DETAIL_SUBJECT_MAX_CHARS])}</td>"
            "</tr>"
        )

    body = (
        f"{header}"
        "<table class='data-table'>"
        "<thead><tr><th>Time</th><th>Event</th><th>Subject</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )
    return _layout(
        f"Run {txn_id[:_RUN_TXN_ID_DISPLAY_MAX_CHARS]}",
        body,
        requested_pack=requested_pack,
    )


def _render_pulse_page(*, requested_pack: str = "") -> str:
    fragment = _render_pulse_fragment()
    help_banner = _render_page_help(
        "Pulse",
        what=(
            "Live tail of pipeline log files (<code>pipeline.jsonl</code>,"
            " <code>reuse.jsonl</code>, <code>evidence.jsonl</code>,"
            " <code>open-questions.jsonl</code>).  Polls once per second."
        ),
        can=(
            "Watch in real time as the absorb/intake pipeline runs."
            "  No interactive controls; this is purely a tail."
        ),
        effect=("Read-only.  The poll only reads from disk — nothing else."),
    )
    body = (
        "<h1>Pulse</h1>"
        + help_banner
        + "<p class='muted'>Live tail of <code>60-Logs/*.jsonl</code> (pipeline, reuse, "
        "evidence, open-questions). Polls once per second.</p>" + fragment
    )
    return _layout("Pulse", body, requested_pack=requested_pack)


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
