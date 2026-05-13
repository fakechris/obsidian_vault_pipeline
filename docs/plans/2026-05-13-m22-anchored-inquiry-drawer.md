# M22 — Anchored inquiry as a sidebar, not a destination

*Plus daily-digest history, because the operator can't navigate
across days today.*

**Date:** 2026-05-13
**Status:** Active (plan only — no code yet)
**Depends on:** M21 (BL-081..088 — the underlying primitives stay)
**Independent of:** M18 / M19

## Origin: M21b user-story failure

M21b shipped "Ask about this" buttons that navigate to a separate
`/chat` page.  Real first-use on 2026-05-13 surfaced four
concrete UX failures (operator feedback, verbatim):

1. **跳新页面丢上下文** — clicking jumps away from the artifact
   the operator was reading.  The thing they wanted to ask
   *about* is gone, and they can't formulate the question well.
2. **profile 选择器但根本没得选** — three "tiers" in the
   composer dropdown today all point at the same MiniMax model.
   Showing the dropdown is noise.
3. **还没看到答案就要选 index / unindex** — visibility toggle
   asks the operator to decide whether to keep / index this
   inquiry *before they've seen the answer*.  Decision is
   impossible without information.
4. **textarea 太小** — composer is a few-line box stretched
   across a narrow column.  Hard to type a paragraph-length
   question.

Meta: M21b didn't actually walk the read→inquire user story.
The plan-doc was Codex-shaped + my own elaboration, and neither
pass put the operator in the seat.

Separate but adjacent failure on the digest surface:

- **Daily digest 无历史导航** — only today's digest has a
  stable URL.  Yesterday's is reachable only by hand-typing
  the date in `?path=`.  No `/digests` list, no prev/next
  on the digest page itself.

This plan fixes all five in one milestone.

## The product primitive — refined

M21 introduced "anchored inquiry" as a primitive.  M22 doesn't
change the primitive; it changes the surface.

| M21b shipped | M22 ships |
|---|---|
| Click button → navigate to /chat?anchor=… | Click button → open right-side **drawer** on the same page |
| Composer asks for visibility before sending | Visibility decided **after** the answer (Save / Absorb / Discard) |
| Profile dropdown shown by default | Hidden behind ⚙️ — defaults work |
| Standalone /chat page + /chats history | **Kept** — direct URL access, history listing |

The drawer is a presentation layer over the same `chat_handler.run_turn`.
No change to BL-082 / BL-083 / BL-084 backend.

## What an inquiry actually looks like

Operator reads digest, eyes stop on "today's tension cluster:
emergent memory architecture vs token cost".  They want to ask:

> "why did the system flag this as a tension when I resolved a
> similar one last week?"

What they want at that moment:

1. **The digest still in view** — they're going to reference it
   while typing.
2. **A roomy place to type** — half-formed thoughts, multiple
   sentences, paste in a quote from the digest.
3. **No upfront ceremony** — no profile picker, no privacy
   toggle, no "create session" flow.  Just type + send.
4. **An answer that arrives quickly and stays anchored** — when
   they see it, the digest is still right there to compare
   against.
5. **A decision *after* the answer**: useful → save it (so
   `/chats` finds it later); really useful → kick it into
   absorb (BL-084b write-back); not useful → close the drawer,
   nothing persists beyond the audit-events token count.

## Architecture

```text
┌─ Reader page (digest / note / object / topic) ───────────────┐
│                                                              │
│  Operator sees the artifact as before.                       │
│  H1 area gains "Ask about this" pill.                        │
│                                                              │
│         ┌─────────────── inquiry drawer (right) ─┐           │
│         │ Anchor: digest — 2026-05-13   ⚙ ✕     │           │
│         ├─────────────────────────────────────────┤           │
│         │ [User]  why did it flag this as ...     │           │
│         │ [Assist] Looking at [[crystal-x]], …    │           │
│         │                                         │           │
│         │ [💾 Save to history] [📥 Send to       │           │
│         │  absorb] [✕ Discard]                    │           │
│         ├─────────────────────────────────────────┤           │
│         │ ┌───────────────────────────────────┐   │           │
│         │ │ Your follow-up...                 │   │           │
│         │ │ (auto-grows to 60% of viewport)   │   │           │
│         │ └───────────────────────────────────┘   │           │
│         │                          [Send →]       │           │
│         └─────────────────────────────────────────┘           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

Drawer mechanics:

- Right side; 380px default; user can drag left edge to resize
  (persisted via `localStorage["ovp-inquiry-drawer-width"]`).
- Page content reflows: the underlying article keeps its
  60-ch reading width when the drawer is open.
- Esc closes it.  Closing without saving discards the session
  (markdown file isn't written — only audit events for token
  spend remain).

## What changes vs. M21

| Aspect | M21b behaviour | M22 behaviour |
|---|---|---|
| Entry from `/note` / `/object` / `/topic` / digest | Anchor `<a href="/chat?anchor=…">` (full page nav) | `<a href="/chat?anchor=…">` wrapping a `<button>`; JS intercepts the click and opens the drawer.  No-JS path follows the `<a>` normally — `/chat` page stays as the fallback. |
| Persistence at first prompt | `create_chat_file` runs immediately | **Ephemeral session in memory.**  `create_chat_file` runs only on Save / Absorb. |
| Visibility prompt | Pre-submit radio | Post-answer affordance |
| Profile picker | Always visible | Hidden by default; `⚙` expands |
| Textarea | 5 rows, fixed | `autosize`, min 5 rows, max 60vh |
| `/chat?id=<id>` page | Primary surface | **Kept** — direct URL access, `/chats` row click, no-JS fallback |
| `/chats` list | Same | Same; this PR adds nothing here |
| `/digests` list | Doesn't exist | **New** (see below) |

## Digest history

The digest renders today as a regular thin-shell `/note?path=
40-Resources/Generated/digests/2026-05-13-digest-daily.md`.
What's missing:

- **`/digests`** — list view, newest-first, one row per file
  under `40-Resources/Generated/digests/`.  Each row: date,
  one-line summary (from the digest's first paragraph), link
  to `/note?path=…`.
- **Prev / next nav on each digest page** — "← yesterday's
  digest" / "tomorrow's digest →" links injected into the
  digest preamble card.  Disabled when the file doesn't exist.

No new projection needed.  Filesystem scan over
`40-Resources/Generated/digests/*.md` is millisecond-cheap and
runs lazily on `/digests` open.  An optional `ovp-knowledge-index`
cache can come later if scale demands.

## BL breakdown

Five BLs, ~3.5 days total.  Each independently shippable.

| BL | Effort | What |
|---|---|---|
| BL-089 | 0.5d | Ephemeral-first `run_turn` — split the existing function into `run_turn_in_memory()` + `persist_turn(session, kind={save\|absorb})` |
| BL-090 | 1d | Drawer renderer + static JS; replace "Ask about this" buttons to open it inline |
| BL-091 | 0.5d | Post-answer Save / Absorb / Discard affordance + wired to BL-089 persist |
| BL-092 | 0.5d | Composer cleanup — autosize textarea, hide profile picker behind ⚙, drop pre-submit visibility radio |
| BL-093 | 0.5d | `/digests` list view + prev/next nav on digest pages |

### BL-089 — ephemeral-first run_turn

Today `run_turn` does two things at once: writes the transcript
file *and* runs the LLM call.  The drawer needs them split.

```python
def run_turn_in_memory(
    vault_dir, *, user_message, anchor_kind, anchor_ref,
    profile_name=None, history=(),
) -> InMemoryTurnResult:
    """Run one turn without writing to disk.  Still emits
    audit_events for token cost — that's how the daily cap stays
    honest even for sessions the operator later discards."""

def persist_turn(
    vault_dir, *, session: InMemoryTurnResult, kind: Literal["save", "absorb"],
) -> Path:
    """Write the transcript.  ``save`` → indexed=true, lands in
    /chats.  ``absorb`` → also writes the ABSORB-chat-… task to
    50-Inbox/02-Tasks/ via BL-084b's write-back hook."""
```

`run_turn` (the existing top-level function) stays as a thin
wrapper that does `run_turn_in_memory()` + `persist_turn(kind="save")`
so M21a CLI keeps working unchanged.

### BL-090 — drawer renderer + JS

New `_chat_drawer.py` module mirrors `_chat_page.py`'s renderer
helpers but emits a `<aside>` block + small JS that:

- Opens / closes the drawer
- Persists width to `localStorage`
- Submits the composer form via `fetch` to a new
  `POST /chat/drawer/message` endpoint
- Streams the response (SSE later — for v1, full POST + render
  the panel result in place)
- Calls `POST /chat/drawer/save` / `/absorb` / `/discard` on the
  three affordances

"Ask about this" buttons stay as `<a href="/chat?anchor=…">`
(the existing BL-087 output) wrapping the button label.  A
single delegated click handler intercepts the anchor's
`click` event, calls `event.preventDefault()`, and opens the
drawer instead.  No-JS path: the `<a>` follows its `href`
normally → user lands on the standalone `/chat` page that BL-086
already serves.  This is the same progressive-enhancement
pattern Linear / GitHub use for "open in panel" affordances.

### BL-091 — Save / Absorb / Discard

Three affordances under each completed assistant turn.  Wired to
new POST endpoints that:

- `save` → `persist_turn(kind="save")` → writes
  `40-Resources/Chats/YYYY-MM/<topic>-<hash>.md` with
  `visibility: indexed`
- `absorb` → `persist_turn(kind="absorb")` → does Save + then
  invokes the BL-084b `writeback_to_absorb_queue`
- `discard` → just close the drawer; nothing written.  Audit
  events already emitted for token spend.

After Save / Absorb, the drawer flips its banner to "Saved as
[[chat-id]] · open in history" — operator can click through to
`/chats`.

### BL-092 — composer cleanup

- Textarea: 5 rows min, `autosize` to 60vh max, full drawer
  width.  No JS dep — use CSS `field-sizing: content` with a
  fallback `oninput` handler.
- Profile picker: hide by default; render under a `<details>`
  `⚙ Advanced` summary, BUT only when the operator's profile
  book actually has multiple distinct models.  Render condition:
  `len({p.litellm_model for p in book.profiles.values()}) > 1`.
  When the condition is false (current state — all three
  profiles point at the same MiniMax model) the disclosure
  isn't rendered at all; showing a picker with no real choice
  is the M21b user-story failure #2.
- Visibility radio: **removed** from the composer entirely.
  Logic moves to post-answer Save / Absorb / Discard
  affordances (BL-091).

### BL-093 — digest history + prev/next

- New `_digests_list_page.py` — sweep
  `40-Resources/Generated/digests/*.md`, emit rows by date desc.
  First-paragraph summary extracted from each file's body.
- `/digests` route in `ui_server.py`.
- Reader nav gains a `Digests` entry next to `Chats`.
- Digest preamble card (currently rendered by
  `_render_digest_preamble`) gains "← yesterday" / "tomorrow →"
  nav based on filesystem siblings.  Disabled (gray) when the
  sibling doesn't exist.

## Non-goals

- **SSE streaming.**  Still deferred; same rationale as
  M21b — the chat_handler streaming refactor is a separate
  piece of work.  Drawer responds in full after the LLM
  completes (typically <30s on Balanced).  Add SSE in a follow-up.
- **Mobile-first drawer geometry.**  Right-side drawer assumes
  desktop reading.  Below 768px, drawer goes full-screen modal
  (CSS `@media` only — no JS branch).
- **Cross-drawer state.**  Opening two `/note?path=` tabs each
  with a drawer → two independent sessions.  No shared state.

## Success metrics (4-week eval after BL-093 ships)

| Metric | Target | Failure mode |
|---|---|---|
| Drawer open count / week | ≥ 5 | Operators don't use the surface |
| Inquiry → Save ratio | ≥ 30% | Most sessions discarded → bad answers, or surface confuses operators |
| Inquiry → Absorb ratio | ≥ 10% | Flywheel back into knowledge graph (the M21 plan's flywheel-band metric) |
| Days with operator-initiated `/digests` navigation | ≥ 3/week | Digest archive isn't worth keeping if no one navigates it |
| Drawer width drift | median width remains in 320–600px window | Default sizing is wrong |

## Implementation order

```text
BL-089 (ephemeral run_turn split)
  ├─→ BL-090 (drawer renderer + JS)
  │    └─→ BL-091 (Save/Absorb/Discard) — completes the drawer flow
  └─→ BL-092 (composer cleanup) — independent of drawer; could ship first

BL-093 (digest history) — fully independent; can interleave
```

Acceptance after each:

- After **BL-089**: existing `ovp-ask` CLI + `/chat` page still
  work; new `run_turn_in_memory` covered by tests but unreferenced
  in shipping code.
- After **BL-090 + BL-091**: drawer works end-to-end on at least
  `/note?path=…&type=digest`.  Old `/chat` page still reachable.
- After **BL-092**: composer in both `/chat` page and drawer is
  the new cleaner shape.
- After **BL-093**: `/digests` works.  Reader nav includes
  Digests entry.

## Decision log

| Decision | Rationale |
|---|---|
| Drawer, not modal | Modal blocks the underlying artifact — defeats the "anchored" half of anchored inquiry |
| Right side, not bottom | Desktop-first surface; horizontal reflow preserves more of the article |
| Ephemeral by default | Operator can't make an informed Index/Discard decision before seeing the answer (M21b user-story failure #3) |
| Keep `/chat` page | Direct URL access, history-row click-through, no-JS fallback |
| No new projection for digests | 3600 files / decade on the file system isn't worth a projection table; `rglob` on a tight schema is fast enough |
| Profile picker conditional on multi-model book | Show the dropdown only when there's a real choice to make |
| SSE deferred (again) | Same as M21b — the chat_handler refactor exceeds this milestone's budget |

## Cross-references

- M21 plan: `docs/plans/2026-05-12-m21-chat-surface.md`
- BL-082 (`chat_fileops`), BL-083 (`context_binder`), BL-084
  (`chat_handler`) stay unchanged for backend
- BL-086 (`_chat_page.py`) → reuse `_profile_options`,
  `parse_anchor_string`
- BL-087 (`_render_ask_about_this_button`) → emits an
  `<a href="/chat?anchor=…">` wrapping a `<button>` (matches the
  progressive-enhancement contract in the "What changes vs. M21"
  table).  Delegated JS intercepts the anchor's `click`,
  `preventDefault`s, and opens the drawer.  No-JS path: the
  anchor follows its `href` normally → BL-086's standalone page.
- BL-088 (`_chats_list_page.py`) → unchanged; drawer's Save
  affordance writes through the same `create_chat_file` →
  projection rebuild path
