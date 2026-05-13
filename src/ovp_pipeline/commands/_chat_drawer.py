"""Anchored inquiry drawer (M22 / BL-090).

The right-side slide-in surface that replaces the M21b full-page
``/chat`` jump for ``Ask about this`` buttons.  Owns *only* the
HTML shell rendered into the page layout; the routes that drive
it live in :mod:`ovp_pipeline.commands.ui_server` and the
client-side behaviour lives in
``src/ovp_pipeline/static/ovp-chat-drawer.js``.

Why a separate module
---------------------

The chat page (``_chat_page.py``) renders a stand-alone history
view at ``/chat?id=...``; the drawer renders a *floating* view
that opens over whatever Reader page the operator is on.  The
two share the transcript rendering helpers from ``_chat_page``
(``_render_transcript_body``) but diverge sharply on layout
(no anchor badge / manifest card / page header — the drawer
is small and quiet) and on the form submit target (the drawer
posts to ``/chat/drawer/message`` and consumes JSON; the page
posts to ``/chat/message`` and consumes a full HTML refresh).

Ephemeral-first contract
------------------------

When the drawer creates a new inquiry session, the backend uses
``visibility="unindexed"`` so the session stays out of /search
and the /chats list during composition.  Three explicit choices
the operator can make after seeing the assistant's reply:

* **Save** — flip the file to ``indexed``; it now shows up in
  /search and /chats.
* **Absorb** — flip to ``indexed`` AND enqueue the chat for the
  absorb pipeline (writeback_to_absorb_queue).
* **Discard** — delete the file entirely.

Closing the drawer without choosing leaves the file as
``unindexed`` — invisible to /search and /chats.  A future
janitor cron sweeps stale unindexed files after N days; for now
they accumulate harmlessly and can be reviewed via a future
``/chats?filter=unindexed`` view.
"""

from __future__ import annotations

from html import escape


# Container id used by the static JS to find / show / hide the
# drawer.  Kept as a module constant so the renderer + JS agree.
DRAWER_ID = "ovp-chat-drawer"


def render_drawer_shell() -> str:
    """Return the closed-state drawer markup.

    Always injected on Reader pages by the page-shell so the
    drawer is one DOM update away from any "Ask about this"
    click — no extra round-trip to fetch a partial.  The drawer
    is hidden via ``hidden`` until JS removes the attribute.

    Layout (top → bottom):

    * Header: anchor badge + close (×)
    * Transcript body (scrollable)
    * Composer (textarea + send button)
    * Action bar (Save / Absorb / Discard) — hidden until at
      least one assistant turn has landed
    """
    return f"""
<aside id="{DRAWER_ID}" class="chat-drawer" hidden aria-hidden="true" role="dialog" aria-label="Anchored inquiry">
  <div class="chat-drawer-backdrop" data-drawer-close="1" aria-hidden="true"></div>
  <div class="chat-drawer-panel" role="document">
    <header class="chat-drawer-header">
      <div class="chat-drawer-anchor">
        <span class="pill ghost" data-drawer-anchor-kind>standalone</span>
        <span class="chat-drawer-anchor-title" data-drawer-anchor-title>Inquiry</span>
      </div>
      <button type="button" class="chat-drawer-close" data-drawer-close="1" aria-label="Close inquiry drawer">×</button>
    </header>

    <div class="chat-drawer-status" data-drawer-status hidden></div>

    <div class="chat-drawer-transcript" data-drawer-transcript>
      <p class="muted small">Ask about this artifact — the answer is rebuilt from current vault state every turn.</p>
    </div>

    <form class="chat-drawer-composer" data-drawer-composer>
      <textarea
        name="message"
        class="chat-drawer-textarea"
        rows="3"
        required
        placeholder="What would you like to know?"
        data-drawer-message
      ></textarea>
      <div class="chat-drawer-composer-row">
        <span class="muted small" data-drawer-profile-hint></span>
        <button type="submit" class="primary" data-drawer-send>Send</button>
      </div>
    </form>

    <div class="chat-drawer-actions" data-drawer-actions hidden>
      <button type="button" class="btn ghost" data-drawer-action="discard">Discard</button>
      <div class="chat-drawer-actions-right">
        <button type="button" class="btn" data-drawer-action="save">Save to /chats</button>
        <button type="button" class="btn primary" data-drawer-action="absorb">Save & absorb</button>
      </div>
    </div>
  </div>
</aside>
"""


def render_drawer_assets() -> str:
    """Return the ``<link>`` + ``<script>`` tags for the drawer.

    Loaded once from the page shell.  CSS uses ``defer``-less
    ``<link>`` so the drawer's hidden state is styled before
    first paint; JS uses ``defer`` so it runs after the DOM is
    parsed (and after the drawer shell is in the document).
    """
    return (
        '<link rel="stylesheet" href="/static/ovp-chat-drawer.css">'
        '<script src="/static/ovp-chat-drawer.js" defer></script>'
    )


def render_turn_html(role: str, body: str, header: str = "") -> str:
    """Render a single turn for drawer injection (JSON response).

    Mirrors the ``_chat_page._flush_block`` shape but with the
    drawer's narrower CSS classes.  ``header`` is a short label
    rendered as a muted small caption (e.g. the timestamp).

    The assistant typically returns markdown (lists, bold, code,
    links).  Render it through ``MarkdownIt`` with ``html=False``
    so embedded HTML is treated as plain text — no XSS path even
    when an upstream LLM emits ``<script>``.  User turns stay as
    plain text wrapped in ``<pre>`` so the operator sees their
    message exactly as typed.
    """
    role_class = "chat-drawer-turn-user" if role == "user" else "chat-drawer-turn-assistant"
    caption = f"<p class='muted small'>{escape(header)}</p>" if header else ""
    if role == "user":
        body_html = f"<pre class='chat-drawer-body'>{escape(body)}</pre>"
    else:
        from markdown_it import MarkdownIt

        renderer = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")
        body_html = (
            "<div class='chat-drawer-body chat-drawer-body-md'>"
            f"{renderer.render(body or '')}"
            "</div>"
        )
    return (
        f'<section class="chat-drawer-turn {role_class}">'
        f"{caption}"
        f"{body_html}"
        "</section>"
    )
