# M21 — Chat Surface (anchored vault conversation)

**Date:** 2026-05-12
**Status:** Active (plan only — no code yet)
**Depends on:** M20 (cognitive surface — `context_loader`, `OVP_RULES.md`,
QUEUE pattern, SSE infra from `/pulse/stream`)
**Independent of:** M18 (trust-aware compiler), M19 (live concept),
upcoming hygiene PRs

## Origin

PR #208 round-3 review opened up the next product question: when the
operator reads an OVP artifact (a note, an object, a digest, a
live concept), there's no way to *interrogate* it.  Today's options
are:

* Open `ovp-query` in a terminal — single-shot, no history, no
  awareness of which page the operator was looking at.
* Drop a `RESEARCH-*.md` task into `50-Inbox/02-Tasks/` — async,
  one-shot, no follow-up.

Neither lets the operator *converse* with the vault about the
thing in front of them.

Codex review from 2026-05-12 framed the gap exactly:

> Chat is not "add a chat box".  It is OVP's next Reuse /
> Interpretation surface.  The product win is anchor-aware
> conversation: "I'm reading this artifact — talk to me about it
> using the vault context behind it".

This document is the M21 plan that locks the surface down before
any code is written.  Same convention as M20 — plan first, BL items
follow, implementation goes in PR order.

## Non-Goals (locked)

M21 v1 explicitly does **not** ship:

* **Branching conversations** — forking from turn N gets confusing
  fast; ship linear first.
* **Multi-model side-by-side panels** — interesting but cosmetic
  before linear chat proves itself.
* **Voice input** — capture-layer work, separate milestone.
* **Local model (Ollama / MLX) provider** — env-var change later;
  cloud-first for v1.
* **Auto-archive** — Codex review #3 explicitly vetoed it.
  Chats can be the most important thinking trail; we never move
  them automatically.
* **Evergreen extraction from chat transcripts** — high-leverage
  follow-up, but tied to BL-029-era promote logic.  v1 just
  preserves the markdown; downstream extraction is a separate BL.
* **Anchor strictness as a hard wall** — Codex review #1.  Default
  is *no* hard limit: the user can ask anything; the system prompt
  asks the model to flag answers that depart from anchored context.
* **Maintainer `/ops/chats` surface** — chat is Reader-side.
  Listing chats sits at `/chats` under the Reader nav.  Avoids
  the Maintainer vocabulary creep BL-052 spent a milestone
  cleaning up.

## What `chat` actually is, in OVP vocabulary

| OVP layer | Chat artifact |
|---|---|
| **Source** | The user's typed messages (capture: keyboard) |
| **Candidate** | The assistant's streamed reply *during* generation (`.pending`) |
| **Canonical State** | Persisted transcript markdown under `40-Resources/Chats/` |
| **Projection** | `chats` table in `knowledge.db` (rebuildable index over the markdown) |
| **Access Surface** | `/chat`, `/chats`, "💬 Chat about this" buttons |
| **Governance** | `.ovp/llm_profiles.yaml` limits + `visibility: private` opt-out |

Chats are **canonical**, not projection.  Operator can edit / link /
delete them in Obsidian.  Wikilinks inside chat bodies show up in
the graph and backlinks.

## Architecture

```
┌─ Capture ───────────────────────────────────────────┐
│  User clicks "💬 Chat about this" on /note,         │
│  /object, /topic — or opens /chat standalone.       │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Context bind  (BL-083) ────────────────────────────┐
│  anchor (note | object | crystal | standalone)      │
│      │                                              │
│      ▼                                              │
│  context_binder.build_manifest(anchor)              │
│    → {included_anchor, included_evergreens,         │
│       included_crystals, omitted_items,             │
│       token_estimate, context_built_at}             │
│  + load_llm_context() (USER + RULES from M20)       │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Conversation runtime  (BL-084 + BL-086) ───────────┐
│  Profile resolves to provider+model+limits via      │
│  .ovp/llm_profiles.yaml (BL-081).                   │
│  System prompt = BL-075 prefix + handler frame.     │
│  litellm.completion(stream=True) → SSE token push.  │
│  Cost guardrail enforced before call.               │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Persistence  (BL-082) ─────────────────────────────┐
│  40-Resources/Chats/YYYY-MM/<slug>-<short-hash>.md  │
│  Frontmatter: type / status / visibility / anchor   │
│  Body:                                              │
│    ## User · <ISO>                                  │
│    ## Assistant · <ISO> · turn-N                    │
│    <!-- context-manifest ... -->                    │
│  Stream-safe: assistant turn buffers to memory,     │
│  appends atomically on completion; mid-stream       │
│  failure writes status: interrupted turn.           │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Projection  (BL-085) ──────────────────────────────┐
│  knowledge.db.chats: chat_id, anchor_path, model,   │
│  profile, status, visibility, started_at,           │
│  last_message_at, turn_count, file_path.            │
│  page_fts indexes body (private excluded).          │
│  Rebuildable: ovp-knowledge-index sweeps the dir.   │
└─────────────────────────────────────────────────────┘
```

## Eight Backlog Items

| ID | Stage | Effort | Description |
|---|---|---|---|
| BL-081 | provider | 0.5d | `.ovp/llm_profiles.yaml` + `llm_profiles.py` loader.  4 built-in profiles (fast/balanced/deep/custom).  Per-use-case defaults + per-pack limits. |
| BL-082 | persistence | 1d | `40-Resources/Chats/` markdown schema + `chat_fileops.py`: read / append_turn / mark_interrupted / pending lock.  Schema fixed before any handler writes. |
| BL-083 | context | 1d | `context_binder.py` — anchor → manifest.  Four anchor kinds.  Token-budget cap.  Manifest serialised as HTML comment in transcript so it survives operator edits. |
| BL-084 | runtime | 1d | `chat_handler.py` headless runner + `ovp-chat new --anchor note:<path>` CLI.  Non-streaming first.  Cost guardrail enforced before call (input cap + output cap + daily soft cap). |
| BL-085 | projection | 0.5d | `knowledge.db.chats` table + sync (`ovp-knowledge-index --audit-sync-only`-style fast path).  `page_fts` integration (privacy-aware). |
| BL-086 | UI runtime | 1.5d | Reader `/chat` page + SSE `/chat/stream?id=…` + Fast/Balanced/Deep/Custom dropdown + "Context anchored to X" indicator + mid-stream interrupt + resume. |
| BL-087 | UI entry | 1d | "💬 Chat about this" button on `/note`, `/object`, `/topic`.  Anchor auto-bound from page context. |
| BL-088 | UI list | 0.5d | Reader `/chats` list view grouped by status (active / pinned / archived).  Private chats hidden.  No Maintainer mirror. |

**Total v1:** ~7 days end-to-end.  Each BL ships its own PR.

## Architecture details

### BL-081 — Provider config

New file `.ovp/llm_profiles.yaml`:

```yaml
profiles:
  fast:
    provider: minimax
    model: M2.7-highspeed
    max_tokens: 1500
    temperature: 0.6
    cost_per_1k_in: 0.0001     # informational, used for budget math
    cost_per_1k_out: 0.0003
  balanced:
    provider: anthropic
    model: claude-sonnet-4-6
    max_tokens: 4000
    temperature: 0.7
    cost_per_1k_in: 0.003
    cost_per_1k_out: 0.015
  deep:
    provider: anthropic
    model: claude-opus-4-7
    max_tokens: 6000
    temperature: 0.7
    cost_per_1k_in: 0.015
    cost_per_1k_out: 0.075

default_for:
  chat: balanced
  extraction: fast
  digest: balanced
  router: fast

limits:
  chat_input_tokens_per_request: 16000
  chat_output_tokens_per_request: 4000
  chat_daily_tokens_per_pack: 200000
```

Loader: `src/ovp_pipeline/llm_profiles.py`.  Exposes
`resolve_profile(name) → ProfileConfig` (frozen dataclass) and
`profile_for_use_case("chat") → ProfileConfig`.  Falls back to
existing `AUTO_VAULT_*` env vars if the file is missing — legacy
vaults see no change.

`00-Polaris/MODELS.md` (optional, hand-authored): plain-English
operator notes on "when to use which profile".  **Not parsed**.
Documentation only.

UI: chat composer shows dropdown
```
Fast · Balanced · Deep · Custom →
```
"Custom" reveals the raw provider/model picker.  Default profile is
chosen by `default_for.chat`.

### BL-082 — Chat markdown schema

Path: `40-Resources/Chats/YYYY-MM/<topic-slug>-<short-hash>.md`

Frontmatter:

```yaml
---
type: chat
schema_version: 1
chat_id: chat-a7b3
status: active                  # active | pinned | archived
visibility: indexed             # indexed | private
save_policy: persistent         # persistent | ephemeral
anchor:
  kind: note                    # note | object | crystal | standalone
  path: "40-Resources/Generated/digests/2026-05-12-digest-daily.md"
  title: "Digest — 2026-05-12"
profile: balanced
model: anthropic/claude-sonnet-4-6
temperature: 0.7
started_at: "2026-05-12T11:00:00Z"
last_message_at: "2026-05-12T11:23:45Z"
turn_count: 6
daily_token_usage:
  input: 14823
  output: 3201
---
```

Body (alternating H2 headings, exactly the way Live Concept
sections are structured so `_split_frontmatter` + heading-match
helpers from `live_concept_fileops` apply):

```markdown
# Chat — <human-readable title>

## User · 2026-05-12T11:00:14Z

Free-form user text.  Wikilinks resolve through
``_replace_wikilinks_with_markdown_links`` like every other
markdown surface.

## Assistant · 2026-05-12T11:00:18Z · turn-2

<!-- context-manifest
  context_built_at: 2026-05-12T11:00:15Z
  token_estimate: 8421
  included_anchor: 40-Resources/Generated/digests/2026-05-12-digest-daily.md
  included_evergreens:
    - emergent-memory-systems
    - memory-indexing-with-limits
  included_crystals:
    - contradiction-4ca412a2040a
  omitted_items:
    count: 4
    reason: token_budget
-->

Assistant reply text with [[wikilinks]] resolved at render time.

(Note: any claim outside the anchored context is flagged inline.)
```

Status `interrupted` writes a partial assistant turn with a footer:

```markdown
## Assistant · 2026-05-12T11:01:30Z · turn-3 · interrupted

<!-- status: interrupted, reason: client_disconnected -->
... partial text that did stream ...
```

`chat_fileops.py` mirrors the `live_concept_fileops` pattern:
`read_chat`, `append_turn`, `mark_interrupted`, `_pending_block`
(write to `.pending` first, atomic rename on commit).

### BL-083 — Context binder

`src/ovp_pipeline/context_binder.py`:

```python
@dataclass(frozen=True)
class ContextManifest:
    anchor_kind: str               # note|object|crystal|standalone
    anchor_ref: str
    included_anchor: str
    included_evergreens: tuple[str, ...]
    included_crystals: tuple[str, ...]
    omitted_count: int
    omitted_reason: str
    token_estimate: int
    context_built_at: str          # ISO

def build_chat_context(
    vault_dir: Path,
    *,
    anchor_kind: str,
    anchor_ref: str,
    profile_input_cap: int,
) -> tuple[str, ContextManifest]:
    """Return (system_prompt_body, manifest).
    system_prompt_body is what the chat handler concatenates after
    BL-075's USER+RULES prefix."""
```

Per anchor:

| Anchor | Context included (descending priority) |
|---|---|
| `note` | note body + wikilinked evergreen bodies + same-cluster neighbours |
| `object` | evergreen body + claims + source notes + cluster neighbours |
| `crystal` | crystal body + every underlying evergreen + projection scores |
| `standalone` | only USER + RULES (BL-075 prefix); user pastes manually |

Token budget cap = `profile_input_cap - len(USER+RULES) - len(turn
history) - margin`.  Over budget: drop neighbours first → drop
distant evergreens → keep anchor + most-cited wikilinks.  Always
record the drop in the manifest's `omitted_items`.

### BL-084 — Headless chat handler

`src/ovp_pipeline/commands/chat_handler.py`:

```python
def run_turn(
    vault_dir: Path,
    chat_id: str | None,            # None → new chat
    *,
    user_message: str,
    anchor_kind: str = "standalone",
    anchor_ref: str = "",
    profile: str = "balanced",
    stream: bool = False,
    private: bool = False,
) -> ChatTurnResult:
    """..."""
```

CLI:

```bash
ovp-chat new   --anchor note:40-Resources/.../digests/2026-05-12-digest-daily.md \
               --profile balanced \
               --message "What does the system say about ..."
ovp-chat reply --id chat-a7b3 --message "Follow-up"
ovp-chat list  [--status active|pinned|archived]
ovp-chat show  --id chat-a7b3
```

Cost guardrail — three gates, all soft (return clear error, never
silent fail):

1. **Per-request input cap** — `profile.max_input_tokens`.  Reject
   before calling the LLM.
2. **Per-response output cap** — passed as `max_tokens` to the
   provider.
3. **Per-pack daily soft cap** — read from
   `chats` projection (`SUM(daily_token_usage)` over today).
   Over cap → reject with `"chat daily token cap reached
   (NNN/MMM); resume tomorrow or raise the limit in
   .ovp/llm_profiles.yaml"`.

`audit_events` emits `chat_turn_completed` / `chat_turn_failed` /
`chat_cap_hit` rows.  Same shape as `task_dispatched`.

### BL-085 — Projection table

```sql
CREATE TABLE chats (
  chat_id TEXT PRIMARY KEY,
  pack TEXT NOT NULL,
  file_path TEXT NOT NULL,
  status TEXT NOT NULL,           -- active|pinned|archived
  visibility TEXT NOT NULL,       -- indexed|private
  anchor_kind TEXT NOT NULL,
  anchor_ref TEXT NOT NULL,
  profile TEXT NOT NULL,
  model TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_message_at TEXT NOT NULL,
  turn_count INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_chats_pack_last ON chats(pack, last_message_at DESC);
```

`page_fts` indexes the chat body **only when** `visibility =
'indexed'`.  Private chats never appear in `/search`.

Rebuild: `ovp-knowledge-index` sweeps `40-Resources/Chats/`, parses
frontmatter, replaces the projection rows.  No mutable state in
the DB.

### BL-086 — Reader `/chat` page + SSE

Routes:

```
GET  /chat?id=<chat_id>          → page (existing chat OR new with anchor)
POST /chat/message               → user-turn payload + start streaming
GET  /chat/stream?id=<chat_id>   → SSE event stream of assistant tokens
GET  /chats                      → list view
```

Layout:

```
┌─────────────────────────────────────────────┐
│ ▽ Context anchored to <title>            ⨯ │  ← collapsible card
│   • included: anchor + 3 evergreens         │     showing context
│   • omitted: 4 evergreens (token_budget)    │     manifest
├─────────────────────────────────────────────┤
│   ## User · 11:00:14                        │
│   The digest says memory emerges...         │
│                                             │
│   ## Assistant · 11:00:18                   │
│   Looking at [[memory-emergence...]]...     │  ← wikilinks live
│                                             │
│   ## User · 11:02:01                        │
│   ...                                       │
├─────────────────────────────────────────────┤
│ [composer with model dropdown + send]       │
│   profile: Balanced ▼                       │
│   ◯ private (don't index)                   │
└─────────────────────────────────────────────┘
```

Interrupt path:

1. User clicks **Stop** mid-stream → `POST /chat/message?abort=1`
2. Server cancels the SSE stream
3. `chat_fileops.mark_interrupted(chat_id, partial_text)` writes
   the partial turn with `status: interrupted`
4. User can `POST /chat/message` again to retry — new assistant
   turn appended, old interrupted turn stays as history

### BL-087 — "💬 Chat about this" button

`_render_note_page` (M20 thin shell + full shell both): add a
button next to the H1.

```html
<a class="btn ghost" href="/chat?anchor=note:<path>">
  💬 Chat about this
</a>
```

Same on `_render_object_page` and `_render_topic_page`.  Anchor is
auto-bound; new chat starts with the anchor manifest pre-loaded.

### BL-088 — `/chats` Reader list

Single page, grouped by status:

```
Pinned (3)
  • 2026-05-12 · Digest review · anchor: digest
  • 2026-05-09 · Memory architecture deep-dive · anchor: crystal
  • 2026-05-07 · M20 design conversation · anchor: standalone

Active (12)
  • 2026-05-12 · ... (newest first)
  • ...

Archived (45)
  ⌃ Show archived (collapsed by default)
```

Each row: anchor pill (note/object/crystal/standalone) + last-
message timestamp + profile pill (Fast/Balanced/Deep) + turn
count.  Click → `/chat?id=<chat_id>`.

Private chats **never** appear in this list.  Operator gets to
them via direct file path or `ovp-chat show --id …`.  UI text on
the composer's "private" checkbox is honest:

> "Private chats are not indexed by OVP and won't appear in
> search, the chats list, or the context binder.  They are still
> sent to the LLM provider — privacy means **OVP doesn't reuse
> them**, not 'this stays on your machine'."

## Cost Estimate

| Component | Per-request typical | Monthly typical |
|---|---|---|
| Balanced profile chat (8K in / 1K out) | ~$0.04 | $20-60 (5-15 chats/day) |
| Deep profile chat (12K in / 2K out) | ~$0.20 | $0-30 (rare) |
| Fast profile chat (4K in / 500 out) | ~$0.002 | $1-2 |
| Daily soft cap default | 200K tokens / pack / day | hard ceiling ~$3/day |

Soft cap default ($3/day) is generous for v1.  Operator can lower
via `.ovp/llm_profiles.yaml` once they have data.

## Success Metrics (4-week check)

Read at **2026-06-09** (4 weeks after BL-088 ships, assuming v1
goes live around 2026-05-19):

* **Chat creation rate ≥ 3/week** — the operator actually uses
  it.  Below that = surface didn't take, kill /chat (keep
  fileops + handler as a CLI).
* **Anchor-bound chat ratio ≥ 50%** — most chats start from an
  artifact, not standalone.  Below = the entry-point buttons
  aren't doing their job.
* **Average turn count per chat ≥ 3** — conversations actually
  develop.  Below = users single-shot it and bounce.
* **Zero cap-bypass exceptions** — no cost guardrail bypassed.
* **Zero context-manifest losses** — every assistant turn in
  the corpus has a manifest comment.  Verify via grep.

Below any one of these = **pause M22 expansion**, ship a single
fix-up PR, reassess.

## Open behavioural questions (deferred until v1 ships data)

* Should chats older than 90 days roll out of the context binder
  by default?  (Probably yes — accumulating chat context creates
  the same "vault is dead" failure mode the digest is meant to
  surface.)
* Should the binder pull *other chats* into context when relevant?
  (Powerful — but feedback-loop risk.  Decide after watching real
  usage.)
* Should we cache LLM responses by `(profile, system, user)` hash
  for replay / regression testing?  (Useful for prompt iteration,
  not urgent.)

## Implementation order (BL order = PR order)

```
BL-081 ──┐
         ├──→ BL-082 ──→ BL-083 ──→ BL-084 ──→ BL-085 ──┐
         │                                              │
         │                                              ├──→ BL-086 ──→ BL-087 ──→ BL-088
         └─────────────── (none required) ──────────────┘
```

Each PR is independently shippable.  After BL-084 the chat CLI
works end-to-end on the live vault — UI sits on top.

## Cross-references

* M20 plan: `docs/plans/2026-05-11-m20-cognitive-surface.md`
  (provides `context_loader`, USER.md, OVP_RULES.md, SSE infra
  patterns, QUEUE pattern this milestone reuses)
* BL-075 / BL-076 / BL-077 (M20) — the three pieces M21 builds on
* `live_concept_fileops._split_frontmatter` /
  `read_section_body` — chat_fileops shares the same heading-
  matcher contract
* `auto_evergreen_extractor.LiteLLMClient.generate` — provider
  client that the chat handler will reuse for non-streaming;
  streaming path adds `stream=True` to the existing
  `litellm.completion` kwargs

## Out of scope, recorded for future BLs if M21 succeeds

* **BL-089 (future):** Chat-to-evergreen extraction — when a chat
  ends in a real insight, lift candidate evergreens via the same
  promote pipeline as `ovp-absorb`
* **BL-090 (future):** Branching conversations (fork from turn N)
* **BL-091 (future):** Multi-model side-by-side
* **BL-092 (future):** Local model provider (Ollama / MLX) — env
  switch + per-profile override
* **BL-093 (future):** Voice input — captures land in
  `50-Inbox/01-Raw/` as transcribed markdown, then `/chat`
  consumes them via the regular note anchor
* **BL-094 (future):** "Chat with cluster" — anchor an entire
  cluster (not just one crystal), let the LLM see the whole
  neighbourhood

## Decision log

| Decision | Rationale |
|---|---|
| Markdown is canonical, DB is projection | Operator must own their conversations.  Survives index rebuilds.  Wikilinks integrate with graph. |
| Path: `40-Resources/Chats/` not `50-Inbox/02-Chats/` | Chats are long-lived artifacts, not intake.  `40-Resources/` matches OVP's reuse semantics (Codex review). |
| `.ovp/llm_profiles.yaml` is the config source; `MODELS.md` is documentation | Avoids parsing human-authored markdown for config.  UI shows abstract names (Fast/Balanced/Deep). |
| Context manifest as HTML comment in transcript | Survives operator edits; auditable later when vault has changed. |
| Stream to memory, append on completion | Reflows / refreshes / abort retries don't corrupt the transcript. |
| Anchor strictness is soft (model flags departures) | Hard limits make the surface useless for follow-up questions.  Operator decides. |
| Three-tier cost guardrail v1 (input + output + daily) | Not distrust — defends against streaming retries, big-anchor accidents, multi-tab triggers. |
| No auto-archive | Chats are thinking trails; archival is operator-directed. |
| `private: true` is OVP-side opt-out, not provider opt-out | Honest naming.  Privacy = "OVP won't reuse this".  Provider transmission still happens. |
| Reader-side `/chats`, no `/ops/chats` | Chat is consumer surface; Maintainer doesn't need a vocabulary mirror. |
