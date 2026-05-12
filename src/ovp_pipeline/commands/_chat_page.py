"""Reader ``/chat`` page renderer (M21b / BL-086).

The Reader-side surface for anchored inquiry.  Wraps the M21a
primitives (``chat_handler.run_turn``, ``chat_fileops.parse_chat``)
in an HTML page + form so an operator can carry on an inquiry
without dropping to the CLI.

This module owns *only* the renderer; the route handlers live in
:mod:`ovp_pipeline.commands.ui_server`.  Keeping them separate
matches the existing ``_ui_renderers`` / ``ui_server`` split and
lets the renderer be unit-tested without spinning up the server.

What this v1 ships:

* ``/chat`` page with composer + manifest card + transcript body
* Synchronous POST submit (page refresh per turn).  SSE streaming
  is deferred to a follow-up — the M21 plan calls it out, but the
  chat_handler refactor needed to thread tokens through SSE is
  larger than the BL-086 budget can absorb in one PR.

Profile dropdown shows Fast / Balanced / Deep only — never raw
provider strings.  Visibility toggle uses the honest copy from
the M21 plan ("Don't index or reuse this inquiry").
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import urlencode

from ovp_pipeline.chat_fileops import ChatFrontmatter, parse_chat
from ovp_pipeline.llm_profiles import load_profiles

# Built-in profile order in the dropdown.  Custom profiles defined
# in ``.ovp/llm_profiles.yaml`` show up by name after the
# canonical three so an operator who added ``my-custom`` sees it
# in the dropdown — but the plan's "no raw provider strings in
# Reader UI" rule still holds because operators only see the
# profile *name*, never provider/model.
_CANONICAL_PROFILE_ORDER = ("fast", "balanced", "deep")


def _profile_options(vault_dir: Path | str, current: str) -> str:
    """Build the ``<option>`` list for the profile dropdown.

    Canonical order first (Fast / Balanced / Deep when defined),
    then any custom profiles sorted by name.  ``current`` is
    pre-selected.
    """
    book = load_profiles(vault_dir)
    names = list(book.profiles.keys())
    canonical = [n for n in _CANONICAL_PROFILE_ORDER if n in names]
    extras = sorted(n for n in names if n not in canonical)
    ordered = canonical + extras
    parts: list[str] = []
    for name in ordered:
        selected = " selected" if name == current else ""
        label = name.capitalize() if name in _CANONICAL_PROFILE_ORDER else name
        parts.append(f'<option value="{escape(name)}"{selected}>{escape(label)}</option>')
    return "\n".join(parts)


def _anchor_badge(fm: ChatFrontmatter | None) -> str:
    """Small pill describing what the session is anchored to."""
    if fm is None or fm.anchor.kind == "standalone":
        return '<span class="pill ghost">Standalone (no anchor)</span>'
    title = escape(fm.anchor.title or fm.anchor.path or fm.anchor.kind)
    kind = escape(fm.anchor.kind)
    return f'<span class="pill">{kind}: {title}</span>'


def _render_manifest_card(fm: ChatFrontmatter | None) -> str:
    """Collapsible 'Context anchored to ...' card."""
    if fm is None:
        return ""
    title = escape(fm.anchor.title or fm.anchor.path or "this inquiry")
    kind = escape(fm.anchor.kind)
    profile_pill = escape(fm.profile.capitalize())
    return (
        '<details class="card chat-manifest" open>'
        f"<summary>Context anchored to <strong>{title}</strong> "
        f"<span class='pill ghost'>{kind}</span> "
        f"<span class='pill'>{profile_pill}</span></summary>"
        "<p class='muted'>"
        "Each assistant turn is rebuilt from current vault state; "
        "the inline <code>&lt;!-- context-manifest --&gt;</code> "
        "comment is an audit snapshot, not a directive."
        "</p>"
        "</details>"
    )


def _render_transcript_body(text: str) -> str:
    """Render the markdown body of a chat transcript as HTML.

    Plain-text rendering preserved as ``<pre>``-style blocks under
    explicit ``## User`` / ``## Assistant`` cards.  Real markdown
    rendering (wikilinks, bold, etc.) is deferred — the plan
    expects this, and the renderer in ``_ui_renderers.py`` already
    has the wikilink helpers we'll wire up next.
    """
    if not text:
        return "<p class='muted'>No turns yet — send the first message below.</p>"
    sections: list[str] = []
    current_role: str | None = None
    current_buffer: list[str] = []
    in_block_comment = False
    title_line = ""
    for raw in text.splitlines():
        if raw.startswith("# "):
            title_line = raw[2:].strip()
            continue
        if raw.startswith("## User ·"):
            sections.append(_flush_block(current_role, current_buffer))
            current_role = "user"
            current_buffer = [raw]
            in_block_comment = False
            continue
        if raw.startswith("## Assistant ·"):
            sections.append(_flush_block(current_role, current_buffer))
            current_role = "assistant"
            current_buffer = [raw]
            in_block_comment = False
            continue
        if current_role is None:
            continue
        if in_block_comment:
            if "-->" in raw:
                in_block_comment = False
            continue
        if "<!--" in raw and "-->" not in raw:
            in_block_comment = True
            continue
        # Strip inline single-line comments (the manifest snapshot).
        if "<!--" in raw and "-->" in raw:
            stripped = raw.split("<!--", 1)[0]
            if stripped.strip():
                current_buffer.append(stripped)
            continue
        current_buffer.append(raw)
    sections.append(_flush_block(current_role, current_buffer))
    title_html = f"<h1>{escape(title_line)}</h1>" if title_line else "<h1>Inquiry</h1>"
    return title_html + "\n".join(s for s in sections if s)


def _flush_block(role: str | None, lines: list[str]) -> str:
    if role is None or not lines:
        return ""
    header = lines[0]
    body = "\n".join(lines[1:]).strip()
    role_class = "chat-turn-user" if role == "user" else "chat-turn-assistant"
    return (
        f'<section class="card chat-turn {role_class}">'
        f"<p class='muted small'>{escape(header[3:])}</p>"
        f"<pre class='chat-body'>{escape(body)}</pre>"
        "</section>"
    )


def render_chat_page_body(
    vault_dir: Path | str,
    *,
    chat_id: str | None = None,
    chat_path: Path | None = None,
    anchor_kind: str = "standalone",
    anchor_ref: str = "",
    anchor_title: str = "",
    profile: str = "balanced",
    error_message: str = "",
    csrf_token: str = "",
) -> str:
    """Render the body HTML for ``/chat``.

    For an existing session, the transcript is loaded and the
    composer prefills the same profile / visibility.  For a new
    session, the composer offers the anchor pre-bound via the
    ``?anchor=<kind>:<ref>`` query string from BL-087's entry
    buttons (or operator-typed for a standalone chat).
    """
    fm: ChatFrontmatter | None = None
    body_html = ""
    if chat_path and chat_path.is_file():
        fm = parse_chat(chat_path)
        body_html = _render_transcript_body(chat_path.read_text(encoding="utf-8"))
    elif chat_id:
        # The route handler couldn't find the chat — render the
        # error message but still surface the composer for retry.
        error_message = error_message or f"Inquiry session {chat_id!r} not found."

    if fm is not None:
        anchor_kind = fm.anchor.kind
        anchor_ref = fm.anchor.path
        anchor_title = fm.anchor.title
        profile = fm.profile or profile
        visibility = fm.visibility
    else:
        visibility = "indexed"

    manifest_card = _render_manifest_card(fm)
    anchor_badge = (
        _anchor_badge(fm) if fm else _new_anchor_badge(anchor_kind, anchor_ref, anchor_title)
    )
    profile_options = _profile_options(vault_dir, profile)

    # Visibility toggle copy per M21 plan.
    visibility_indexed_checked = " checked" if visibility == "indexed" else ""
    visibility_unindexed_checked = " checked" if visibility == "unindexed" else ""

    error_block = ""
    if error_message:
        error_block = f'<div class="card error">{escape(error_message)}</div>'

    csrf_field = (
        f'<input type="hidden" name="_csrf" value="{escape(csrf_token)}" />' if csrf_token else ""
    )

    chat_id_hidden = (
        f'<input type="hidden" name="chat_id" value="{escape(fm.chat_id if fm else "")}" />'
        if fm
        else ""
    )
    anchor_hidden = (
        f'<input type="hidden" name="anchor" '
        f'value="{escape(f"{anchor_kind}:{anchor_ref}" if anchor_ref else "")}" />'
    )

    return f"""
<header class="chat-header">
  <h1>Ask the vault</h1>
  {anchor_badge}
</header>
{manifest_card}
{body_html}
<section class="card chat-composer">
  <form method="POST" action="/chat/message">
    {csrf_field}
    {chat_id_hidden}
    {anchor_hidden}
    <label class="block">
      <span class="muted small">Profile</span>
      <select name="profile">{profile_options}</select>
    </label>
    <label class="block visibility-toggle">
      <input type="radio" name="visibility" value="indexed"{visibility_indexed_checked} />
      <strong>Index this inquiry</strong>
      <span class="muted small">Show up in /search and the binder's retrieval layer.</span>
    </label>
    <label class="block visibility-toggle">
      <input type="radio" name="visibility" value="unindexed"{visibility_unindexed_checked} />
      <strong>Don't index or reuse this inquiry.</strong>
      <span class="muted small">
        OVP won't include this session in search, the inquiry list,
        or future context-binder retrieval. The selected LLM provider
        still receives the current request.
      </span>
    </label>
    <label class="block">
      <span class="muted small">Your message</span>
      <textarea name="message" rows="5" required
        placeholder="Ask something about the anchored artifact, or any vault topic."></textarea>
    </label>
    <button type="submit" class="primary">Send</button>
  </form>
  {error_block}
</section>
"""


def _new_anchor_badge(kind: str, ref: str, title: str) -> str:
    if kind == "standalone" or not ref:
        return '<span class="pill ghost">Standalone (no anchor)</span>'
    display = escape(title or ref)
    return f'<span class="pill">{escape(kind)}: {display}</span>'


def chat_page_redirect_target(chat_id: str) -> str:
    """Where to send the browser after a successful POST."""
    return "/chat?" + urlencode({"id": chat_id})
