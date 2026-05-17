# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *




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
    <link rel="stylesheet" href="/static/ovp-chat-drawer.css" />
    <link rel="stylesheet" href="/static/ovp-digests-calendar.css" />
    <script src="/static/ovp-chat-drawer.js" defer></script>
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
    {_render_chat_drawer_shell()}
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
    # M22: the link is the no-JS fallback (still navigates to
    # /chat).  When the drawer JS loads, it hijacks the click and
    # opens the right-side drawer over the current Reader page
    # using these data-* attributes.
    return (
        f'<a class="btn ghost ask-about-this" href="{escape(href)}"'
        f' data-anchor-kind="{escape(anchor_kind)}"'
        f' data-anchor-ref="{escape(anchor_ref)}"'
        f' data-anchor-title="{escape(title)}"'
        f">💬 {escape(label)}</a>"
    )



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


__all__ = [
    '_layout',
    '_linkify_keywords',
    '_lookup_wikilink_target',
    '_render_ask_about_this_button',
    '_render_evolution_candidates',
    '_render_live_concept_preamble',
    '_render_named_note_links',
    '_render_object_links',
    '_render_source_backlink_rail',
    '_render_source_chain_card'
]
