# M21 — Anchored Inquiry Surface

*Ask the vault about what you are reading.*

**Date:** 2026-05-12
**Status:** Active (plan only — no code yet)
**Depends on:** M20 (cognitive surface — `context_loader`, `OVP_RULES.md`,
QUEUE pattern, SSE infra from `/pulse/stream`)
**Independent of:** M18 (trust-aware compiler), M19 (live concept),
upcoming hygiene PRs

## Product primitive: anchored inquiry, not chat

Chat is a **UI form**, not the product primitive.  The OVP product
primitive M21 introduces is **anchored inquiry**:

> When the operator reads an OVP artifact (note, object, topic,
> digest, live concept) they should be able to ask the vault
> about it, get an answer grounded in real vault context, see
> exactly what was used, and optionally hand the result back into
> the absorb / promote flow.

The primary entry surface is "Ask about this" on `/note`,
`/object`, `/topic`, and digest pages — *not* a standalone chat
page.  A standalone `/chat` entry exists as a fallback for
"general inquiry without an anchor", but anchored inquiry is what
M21 optimises for.

The success metric is **anchor-bound inquiry ratio ≥ 60%**.  If
most M21 sessions are anchorless general chat, the surface has
collapsed into a generic ChatGPT clone and the milestone has
failed.

## Origin

PR #208 round-3 review opened up the next product question: when
the operator reads an OVP artifact, there's no way to
*interrogate* it.  Today's options are:

* Open `ovp-query` in a terminal — single-shot, no history, no
  awareness of which page the operator was looking at, no
  context manifest, no write-back path.
* Drop a `RESEARCH-*.md` task into `50-Inbox/02-Tasks/` — async,
  one-shot, no follow-up.

2026-05-12 Codex reviews (two passes) framed the boundary:

> M21 is OVP's next Reuse / Interpretation surface.  The product
> win is anchor-aware conversation: "I'm reading this artifact —
> talk to me about it using the vault context behind it".

> Don't make this a chat app.  Make it the anchored-inquiry
> primitive.  Chat history, list views, search are M21b/M21c —
> not blockers for the first useful thing.

This document is the M21 plan that locks the surface down before
any code is written.  Same convention as M20 — plan first, BL items
follow, implementation goes in PR order.

## Why not just `ovp-query --anchor`?

A reasonable question.  Short answer: `ovp-query` is a primitive
M21 reuses, but it cannot become M21 itself.  Detailed comparison:

| Capability | `ovp-query` today | `ovp-query --anchor` (hypothetical) | M21 anchored inquiry |
|---|---|---|---|
| Single-shot Q&A | ✅ | ✅ | ✅ |
| Anchor-aware context | ❌ | ✅ (would be the extension) | ✅ |
| Multi-turn history within a session | ❌ | ❌ | ✅ |
| Persistent transcript (canonical markdown) | ❌ | ❌ | ✅ |
| Per-turn context manifest (audit trail) | ❌ | ❌ | ✅ |
| Reader-UI surface with streaming | ❌ | ❌ | ✅ |
| Write-back into absorb / promote flow | ❌ | ❌ | ✅ |
| Cost guardrail w/ daily soft cap | partial | partial | ✅ |
| Per-turn vault retrieval (FTS + semantic + crystals) | ❌ | partial | ✅ |

**What M21 must *not* duplicate from `ovp-query`:** the retrieval
internals.  M21's context binder (BL-083) wraps `ovp-query`'s
existing FTS, semantic search, and crystal-score helpers — it
does not implement a parallel RAG stack.  If the binder needs a
capability `ovp-query` already has, the right move is to refactor
the shared helper out of `ovp-query` into a module both can call,
not to re-implement.

The product primitives that M21 cannot get from `ovp-query` —
turn history, write-back, manifest, Reader UI — are exactly the
ones that justify a new milestone instead of a flag.

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

## What an anchored inquiry session is, in OVP vocabulary

| OVP layer | Inquiry artifact |
|---|---|
| **Source** | The user's typed messages (capture: keyboard) |
| **Candidate** | The assistant's streamed reply *during* generation (`.pending`) |
| **Canonical State** | Persisted transcript markdown under `40-Resources/Chats/` |
| **Projection** | `chats` table in `knowledge.db` (rebuildable index over the markdown) |
| **Access Surface** | "Ask about this" on `/note` / `/object` / `/topic` / digest; `/chat` standalone fallback; `/chats` list (M21c) |
| **Governance** | `.ovp/llm_profiles.yaml` limits + `visibility: unindexed` opt-out + audit-ledger token accounting |

Sessions are **canonical artifacts, not canonical knowledge**.
Operator can edit / link / delete them in Obsidian.  Wikilinks
inside session bodies show up in the graph and backlinks.

> **Hard rule.**  An inquiry transcript is canonical *artifact*,
> not canonical *knowledge*.  Anything that should enter the
> knowledge state (`objects` / `claims` / `evergreens` / atlas)
> must go through the existing `task → absorb → promote → review`
> pipeline.  The write-back hook in BL-084b is how an inquiry
> turn enters that pipeline; assistant prose itself is never
> auto-promoted.

## Architecture

```
┌─ Capture ───────────────────────────────────────────┐
│  User clicks "Ask about this" on /note, /object,    │
│  /topic, or digest page — or opens /chat as a       │
│  standalone fallback.                               │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Context bind  (BL-083 — two layers) ───────────────┐
│  ANCHOR layer (fixed per session):                  │
│    note | object | crystal | standalone             │
│    → AnchorContext(included_anchor,                 │
│       included_evergreens, included_crystals,       │
│       token_estimate)                               │
│  RETRIEVAL layer (rebuilt per turn from user msg):  │
│    → wraps ovp-query FTS / semantic /               │
│      crystal_scores / contradictions helpers        │
│    → RetrievalContext(query, included_objects,      │
│       included_crystals, included_contradictions,   │
│       token_estimate)                               │
│  + load_llm_context()  (USER + RULES from M20)      │
│  Manifest serialised into transcript = audit only;  │
│  next turn always rebuilds context fresh.           │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Conversation runtime  (BL-084 + BL-086) ───────────┐
│  Profile → provider+model+limits via                │
│  .ovp/llm_profiles.yaml (BL-081).  No raw provider  │
│  strings in Reader UI — Fast/Balanced/Deep only.    │
│  System prompt = BL-075 prefix + handler frame.     │
│  litellm.completion(stream=True) → SSE token push.  │
│  Cost guardrail reads append-only audit_events      │
│  ledger; gates check input cap + output cap +       │
│  per-pack daily soft cap before the LLM call.       │
│  Write-back hook: ovp-ask absorb writes             │
│  ABSORB-chat-<id>-turn-<n>.md into the existing     │
│  absorb queue (BL-084b).                            │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─ Persistence  (BL-082) ─────────────────────────────┐
│  40-Resources/Chats/YYYY-MM/<topic-slug>-<short-    │
│  hash>.md                                           │
│  Frontmatter: type / status / visibility / anchor / │
│    profile / model / started_at / last_message_at / │
│    turn_count (NO token counts — those live in the  │
│    audit ledger).                                   │
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
│  last_message_at, turn_count, file_path,            │
│  input_tokens, output_tokens (lifetime totals,      │
│  metadata only — cap math reads the ledger).        │
│  For visibility: indexed sessions also write a      │
│  pages_index shadow row + page_fts row so /search   │
│  finds them.  visibility: unindexed sessions get    │
│  a chats row only — never reach search, never reach │
│  the context binder's retrieval layer.              │
│  Rebuildable: ovp-knowledge-index sweeps the dir.   │
└─────────────────────────────────────────────────────┘
```

## Three phases — ship in this order

The eight original BLs split into three sub-milestones.  The
**first phase ships the primitive**; each later phase adds an
optional surface that builds on it.  Don't ship M21b until
M21a's anchored CLI proves the primitive on the live vault; don't
ship M21c until M21b's Reader UI has produced ≥ a week of real
sessions.

### M21a — Anchored Inquiry MVP (~3.5 days)

Goal: an operator can ask the vault about the artifact they're
reading and get a grounded, audited, optionally-rerunnable answer
— without a Reader UI.  CLI-first, like `ovp-task` was before
`/chat` ever existed.

| BL | Effort | What |
|---|---|---|
| BL-081 | 0.5d | Provider profiles + loader |
| BL-082 | 1d | Inquiry markdown schema + fileops |
| BL-083 | 1d | Context binder — **anchor + per-turn retrieval** |
| BL-084 | 1d | Headless handler + `ovp-ask` CLI |
| **BL-084b** | included in BL-084 | **Write-back hook** — assistant turn can emit `ABSORB-chat-<id>-turn-<n>.md` into `50-Inbox/02-Tasks/` |

**Acceptance for M21a:** `ovp-ask --anchor note:<path> --message
"..."` on the live vault produces (a) a grounded answer, (b) a
full manifest in the transcript, (c) audit events for the token
spend, and (d) optionally a write-back task that enters the
existing absorb / promote flow.

### M21b — Conversational Reader UI (~3 days)

Goal: lift the MVP into the Reader surface.  Adds turn history
within a session, streaming, "Ask about this" entry, interrupt
recovery.

| BL | Effort | What |
|---|---|---|
| BL-086 | 1.5d | Reader `/chat` page + SSE + Fast/Balanced/Deep dropdown + interrupt recovery |
| BL-087 | 1d | "Ask about this" buttons on `/note` / `/object` / `/topic` / digest |
| (continued) | 0.5d | Composer support for follow-up turns w/ history binding |

**Acceptance for M21b:** the operator can start an inquiry from a
Reader page, see streaming tokens, ask follow-ups, and have the
session land cleanly in the M21a transcript schema (Stop → status:
interrupted, no torn writes).

### M21c — History and Library (~1 day)

Goal: enough surface to find old sessions and rebuild the
projection if the DB is wiped.

| BL | Effort | What |
|---|---|---|
| BL-085 | 0.5d | `knowledge.db.chats` projection + visibility-aware FTS |
| BL-088 | 0.5d | Reader `/chats` list view (active / pinned / archived) |

**Acceptance for M21c:** `ovp-knowledge-index` can rebuild the
`chats` projection from the markdown corpus; `/chats` lists
indexed sessions only; `/search` finds session bodies for
indexed sessions only.

**Total v1 (all three phases):** ~7.5 days.  Each BL ships its
own PR.  M21a alone is a useful, evaluable milestone.

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

UI: inquiry composer shows the dropdown

```text
Fast · Balanced · Deep
```

No Custom entry in the Reader UI — raw provider/model strings
stay out of chrome.  Operators who need a custom profile add it
to `.ovp/llm_profiles.yaml` and pass `ovp-ask --profile
my-custom` on the CLI.  Default profile is chosen by
`default_for.chat`.

### BL-082 — Chat markdown schema

Path: `40-Resources/Chats/YYYY-MM/<topic-slug>-<short-hash>.md`

Frontmatter:

```yaml
---
type: chat
schema_version: 1
chat_id: chat-a7b3
status: active                  # active | pinned | archived
visibility: indexed             # indexed | unindexed
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
# NOTE: token spend is NOT stored in frontmatter.  Source of truth
# is the append-only `audit_events` ledger (chat_turn_completed +
# chat_turn_failed) — the daily-cap check in BL-084 reads from
# there, never from the transcript or the projection.
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

### BL-083 — Context binder (two-layer: anchor + retrieval)

`src/ovp_pipeline/context_binder.py`:

```python
@dataclass(frozen=True)
class AnchorContext:
    """Fixed per-session.  Built once when the inquiry opens."""
    kind: str                          # note|object|crystal|standalone
    ref: str
    included_anchor: str
    included_evergreens: tuple[str, ...]
    included_crystals: tuple[str, ...]
    token_estimate: int

@dataclass(frozen=True)
class RetrievalContext:
    """Rebuilt per turn from the user's question.  Wraps the
    existing ``ovp-query`` retrieval stack — does NOT reimplement
    FTS / semantic / crystal-scoring."""
    query: str
    included_objects: tuple[str, ...]
    included_crystals: tuple[str, ...]
    included_contradictions: tuple[str, ...]
    token_estimate: int

@dataclass(frozen=True)
class ContextManifest:
    anchor: AnchorContext
    retrieval: RetrievalContext
    omitted_count: int
    omitted_reason: str
    token_estimate_total: int
    context_built_at: str              # ISO

def build_chat_context(
    vault_dir: Path,
    *,
    anchor_kind: str,
    anchor_ref: str,
    user_message: str,
    profile_input_cap: int,
) -> tuple[str, ContextManifest]:
    """Return (system_prompt_body, manifest).

    Two-layer construction:

    1. Anchor context (fixed per session): from the artifact the
       operator is reading.
    2. Retrieval context (rebuilt per turn): from
       ``ovp-query``'s existing helpers, scoped by ``user_message``.

    The same `_compute_query_results` helper that backs
    ``ovp-query`` is the source of truth for retrieval.  If the
    binder needs functionality that's currently locked inside
    ``query_tool``, the right refactor is to move that helper
    into a shared module both can import — not to fork retrieval.

    ``system_prompt_body`` is what the inquiry handler
    concatenates after BL-075's USER+RULES prefix."""
```

#### Anchor layer (per session, built once)

| Anchor | Anchor context (descending priority) |
|---|---|
| `note` | note body + wikilinked evergreen bodies + same-cluster neighbours |
| `object` | evergreen body + claims + source notes + cluster neighbours |
| `crystal` | crystal body + every underlying evergreen + projection scores |
| `standalone` | empty (USER + RULES from BL-075 is still prepended outside the manifest) |

#### Retrieval layer (per turn, from user message)

For each user turn, the binder calls into `ovp-query`'s existing
retrieval pipeline:

* **FTS** — `page_fts MATCH <query>` over indexed pages + crystals
* **Semantic** — `page_embeddings` cosine over the same scope
* **Open contradictions** — `contradictions WHERE status='open'`
  AND any matched object id
* **Crystal scores** — top-N from `crystal_scores` whose
  `subject_key` matches the query

These are existing helpers, not new code.  The binder's job is
*selection + budgeting*, not retrieval.  It picks the top results
per source up to a per-turn retrieval-context budget and records
them in `RetrievalContext.included_*`.

This is what lets the operator ask "does the vault have anything
against this?" and have the binder pull `contradictions` rows the
anchor doesn't reach.

#### Token budgeting

```
total_budget = profile_input_cap
             - len(USER + RULES prefix)
             - len(turn_history_window)        ← rolling, see below
             - margin

anchor_budget    = min(total_budget * 0.6, anchor_context_max)
retrieval_budget = total_budget - anchor_budget
```

Over budget on either layer: drop in this order — cluster
neighbours → distant evergreens → low-score retrieval hits.
Always keep the literal anchor body itself.  Every drop recorded
in `manifest.omitted_*` with the reason.

#### Turn-history compression (rolling window + summary)

Without compression, by turn 8–10 the turn history alone would
consume the retrieval budget and the binder would stop pulling
fresh vault context.  Strategy:

```
TURN_HISTORY_VERBATIM_K = 4    # keep last N turn pairs verbatim
TURN_HISTORY_SUMMARY_MAX_TOKENS = 600
```

* Most recent `K` user/assistant pairs are included verbatim
  (verbatim window).
* Earlier turns are folded into a single rolling summary
  paragraph:

  ```
  ## Earlier in this conversation
  <one-paragraph LLM summary, max 600 tokens>
  ```

* The summary is cached per `chat_id`; it's regenerated only
  when the verbatim window slides (i.e. on every K+1th turn),
  using a cheap Fast-profile LLM call.
* When the summary is regenerated, a `chat_summary_rebuilt`
  audit event records the old/new token counts so the operator
  can see the compression overhead.

This keeps the binder's behaviour stable across long
conversations: anchor + retrieval stay budget-honest, and the
operator never sees the surface degrade to "model can no longer
see the vault" after a long thread.

#### Manifest is a read-only audit snapshot

Codex review #7: the manifest persisted into the transcript is an
**audit record**, not a context-loading directive.  The handler
**never** reads an old manifest to reconstruct context.  Every
turn, the binder builds a fresh `ContextManifest` from current
vault state and writes a fresh manifest into the new assistant
turn.  Operators editing manifests in Obsidian has no effect on
the next turn's context — only on what future audits show.

This prevents schema drift and prevents operators from
inadvertently changing system behaviour via markdown edits.

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
    visibility: str = "indexed",    # "indexed" | "unindexed"
) -> ChatTurnResult:
    """..."""
```

CLI (matches the renamed product primitive — `ovp-ask`):

```bash
ovp-ask new   --anchor note:40-Resources/.../digests/2026-05-12-digest-daily.md \
              --profile balanced \
              --message "What does the system say about ..."
ovp-ask reply --id chat-a7b3 --message "Follow-up"
ovp-ask list  [--status active|pinned|archived]
ovp-ask show  --id chat-a7b3
```

#### Cost guardrail — three gates, audit-ledger backed

Codex review #5: token accounting must be **append-only** so the
daily cap can't drift between transcript frontmatter, projection
row, and reality.  All three sources of truth for cost belong to
the `audit_events` ledger — the projection (BL-085) and the
transcript frontmatter (BL-082) are *display* derivatives.

Three gates, all soft (return clear error, never silent fail):

1. **Per-request input cap** — `profile.max_input_tokens`.  Reject
   before calling the LLM.
2. **Per-response output cap** — passed as `max_tokens` to the
   provider.
3. **Per-pack daily soft cap** — computed by summing
   `audit_events.payload_json` `input_tokens + output_tokens`
   over the current UTC day for events of type
   `chat_turn_completed` and `chat_turn_failed` (counts spend
   even on failed attempts; failures still cost).  **Unindexed
   sessions still count** — privacy is about reuse, not cost.
   Over cap → reject with `"chat daily token cap reached
   (NNN/MMM); resume tomorrow or raise the limit in
   .ovp/llm_profiles.yaml"`.

`audit_events` emits:

| event_type | When | Payload (recorded for visibility=indexed) |
|---|---|---|
| `chat_turn_completed` | LLM call returned | profile, pack, visibility, input_tokens, output_tokens, anchor_kind |
| `chat_turn_failed` | LLM call errored | profile, pack, visibility, input_tokens (estimated), error_class |
| `chat_cap_hit` | Cap rejection | profile, pack, visibility, cap_kind, cap_value, today_total |

For `visibility: unindexed` rows the audit payload still records
the **counts** (so the cap stays honest) but omits the inquiry
body, response body, and any retrieved-object identifiers.

#### Write-back hook (BL-084b — bundled with BL-084)

Codex review #3 vetoed shipping evergreen extraction from
transcripts in v1, but required an **explicit write-back path**
so insights from inquiry can re-enter the knowledge pipeline.
The hook is intentionally minimal and routes through OVP's
existing absorb / promote / review machinery — never a parallel
fast-path into `objects`.

Surface (CLI in M21a, UI button in M21b):

```bash
ovp-ask absorb --id chat-a7b3 --turn 2
```

What it does:

1. Read the assistant body for turn N of session `chat-a7b3`
2. Write a new task file
   `50-Inbox/02-Tasks/ABSORB-chat-<chat_id>-turn-<n>.md` carrying:
   * `type: task`, `subtype: absorb-chat`
   * frontmatter pointing back at the originating chat + turn
   * the assistant prose as the task body, prefixed with a header
     `# Captured from inquiry <chat_id> turn <n>`
3. Emit `chat_writeback_handoff` audit event with `chat_id`,
   `turn`, `task_path`
4. **Stop there.**  The existing AutoPilot / task dispatcher
   picks up the file and runs the standard `absorb → promote →
   review` flow.  Nothing in M21 writes to `objects`, `claims`,
   `evergreens`, or atlas.

> **Invariant.**  Inquiry transcripts are canonical artifacts,
> not canonical knowledge.  The write-back hook is the *only*
> path from inquiry into knowledge state, and it is the same
> path operator-written notes take.  Inquiry can't shortcut
> review.

Default behaviour: **off**.  The operator must explicitly call
`ovp-ask absorb` (or click the button in M21b).  No turn enters
the absorb queue automatically.

### BL-085 — Projection table + visibility-aware FTS

```sql
CREATE TABLE chats (
  chat_id TEXT PRIMARY KEY,
  pack TEXT NOT NULL,
  file_path TEXT NOT NULL,
  status TEXT NOT NULL,           -- active | pinned | archived
  visibility TEXT NOT NULL,       -- indexed | unindexed
  anchor_kind TEXT NOT NULL,
  anchor_ref TEXT NOT NULL,
  profile TEXT NOT NULL,
  model TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_message_at TEXT NOT NULL,
  turn_count INTEGER NOT NULL,
  -- Lifetime totals.  Metadata only — used by /chats list view
  -- to show "this session has cost ~$0.12 total".  Daily cap math
  -- is computed independently by summing audit_events; never
  -- read from this table.
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_chats_pack_last ON chats(pack, last_message_at DESC);
```

The `chats` table is a **display / metadata** projection for the
`/chats` list view (BL-088).  Full-text search lives elsewhere
(`pages_index` + `page_fts`).  Cost guardrail in BL-084 reads
the audit-events ledger directly — `chats.input_tokens` /
`output_tokens` are display derivatives and **not** the source of
truth for cap enforcement.  This dependency direction is what
lets BL-084 ship before BL-085 (M21a runs without the projection;
M21c adds it for the list view).

#### Visibility field — Codex review #6

Internal field stays `visibility`.  Allowed values are
`indexed` (default) and `unindexed`.  The word "private" never
appears in code or UI — it overpromises.  UI copy (composer
toggle):

> **Don't index or reuse this inquiry.**  OVP won't include this
> session in search, the inquiry list, or future context-binder
> retrieval.  The selected LLM provider still receives the
> current request.

#### FTS integration — Codex review #8

`/search` is `page_fts JOIN pages_index`.  Adding a chat body to
`/search` therefore requires **two** writes per session:

1. `pages_index` shadow row with synthetic slug
   `chat:<chat_id>`, `kind: 'chat'`, title from frontmatter
2. `page_fts` row with the concatenated assistant + user prose

Both writes happen **only** when `visibility = 'indexed'`.
`unindexed` sessions:

* live on disk as canonical markdown
* have a `chats` row (so the operator can find them via direct
  navigation or `ovp-ask show`)
* are **never** written to `pages_index` or `page_fts`
* are **never** considered by `context_binder`'s retrieval layer
  (BL-083)

#### Rebuild

`ovp-knowledge-index` sweeps `40-Resources/Chats/**.md`, parses
frontmatter, and replaces:

* `chats` rows
* `pages_index` shadow rows for indexed sessions
* `page_fts` rows for indexed sessions

No mutable state in the DB; everything derives from the markdown
corpus + the audit-events ledger.

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
│ [composer with profile dropdown + send]     │
│   profile: Balanced ▼                       │
│   ◯ Don't index or reuse                    │
└─────────────────────────────────────────────┘
```

#### Profile dropdown — Codex review #9

The Reader UI dropdown only ever shows:

```
Fast · Balanced · Deep
```

No raw provider/model strings.  No "Custom" entry in the Reader
dropdown.  Operators who need a custom profile add it to
`.ovp/llm_profiles.yaml` and select it via `ovp-ask --profile
my-custom`; the Reader UI doesn't expose the picker.  Goal: keep
the product mental model abstract (cost / quality tier) so casual
operators never see "anthropic/claude-sonnet-4-6" in chrome.

#### Interrupt path

1. User clicks **Stop** mid-stream → `POST /chat/message?abort=1`
2. Server cancels the SSE stream
3. `chat_fileops.mark_interrupted(chat_id, partial_text)` writes
   the partial turn with `status: interrupted`
4. User can `POST /chat/message` again to retry — new assistant
   turn appended, old interrupted turn stays as history

### BL-087 — "Ask about this" button

`_render_note_page` (M20 thin shell + full shell both): add a
button next to the H1.

```html
<a class="btn ghost" href="/chat?anchor=note:<path>">
  Ask about this
</a>
```

Same on `_render_object_page`, `_render_topic_page`, and the
digest preamble card.  Anchor is auto-bound; new session starts
with the anchor manifest pre-loaded; first user message triggers
the BL-083 retrieval layer.

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

**Unindexed sessions never appear in this list.**  Operator
reaches them via direct file path or `ovp-ask show --id …`.  The
composer toggle that produces them carries the honest copy from
BL-085:

> **Don't index or reuse this inquiry.**  OVP won't include this
> session in search, the inquiry list, or future context-binder
> retrieval.  The selected LLM provider still receives the
> current request.

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

Read at **2026-06-16** (4 weeks after BL-088 ships, assuming v1
goes live around 2026-05-19; date moves with reality).  Codex
review #10 split these into *usage* metrics (does the operator
return?) and *quality / trust* metrics (is the system honest?).
Both bands must hit for the milestone to be considered worth
keeping; missing either band = pause M22 expansion, ship a
fix-up PR, reassess.

### Usage band

* **Inquiry creation rate ≥ 3/week** — the operator actually
  uses it.  Below = surface didn't take; demote `/chat` UI,
  keep CLI.
* **Anchor-bound inquiry ratio ≥ 60%** — most sessions start
  from an artifact.  Below = entry-point buttons aren't doing
  their job and we've shipped a ChatGPT clone.
* **Average turn count per session ≥ 3** — conversations
  develop.  Below = users single-shot it.
* **Write-back handoff count ≥ 1/week** — at least one inquiry
  feeds into the absorb pipeline.  Below = inquiries are
  isolated thought experiments; consider redesigning the
  write-back surface.

### Quality / trust band

* **Manifest completeness 100%** — every assistant turn has an
  inline manifest comment.  Verified via grep on the corpus.
* **Unresolved-wikilink rate 0** — every `[[slug]]` the assistant
  emits resolves against the vault or is flagged as speculative.
* **Out-of-context flagging present** — when the model uses
  knowledge outside the manifest, the answer says so.  Sampled
  manually each week.
* **Zero cap-bypass exceptions** — no audit ledger entry shows
  a turn that exceeded the cap without being rejected.
* **Zero unindexed-session leaks** — no unindexed session
  appears in `pages_index`, `page_fts`, or `chats` list view.
  Verified via SQL audit each week.

### Flywheel band (write-back conversion)

Closes the loop from inquiry back into the OVP knowledge state.
A surface that gets used and produces honest answers but never
re-enters the knowledge graph hasn't earned its keep — it's a
better ChatGPT, not OVP's next reuse node.

* **Write-back → absorb candidate ratio ≥ 50%** — of
  `chat_writeback_handoff` events, the resulting
  `ABSORB-chat-*` task should reach the `candidates` queue at
  least half the time.  Below = either operators handoff junk
  (reduce by training prompts), or absorb routes most of them as
  duplicates (good problem — means inquiry is converging on
  known evergreens; consider relaxing the rule once root cause
  identified).
* **Candidate → evergreen promotion ratio ≥ 20%** — of
  inquiry-derived candidates, at least 1-in-5 should clear
  promote review.  Below = inquiry isn't producing distillable
  insight, just commentary.
* **Time-to-knowledge ≤ 1 week median** — from
  `chat_writeback_handoff` audit event to either evergreen
  promote OR explicit dismissal.  Stuck candidates without a
  decision suggest the operator started something they didn't
  finish.

> **Red line.**  The assistant must never imply it has seen
> context that the manifest doesn't record.  This is the single
> non-negotiable trust property of the surface.  If quality
> sampling finds even one fabricated-context turn, the
> system-prompt frame is broken and M21 stops shipping new
> features until it's fixed.

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

Three phases — each is independently shippable, and **the
operator can decide to stop after M21a** if the CLI primitive
proves enough.

```text
M21a (Anchored Inquiry MVP)
  BL-081 ──┬──→ BL-082 ──→ BL-083 ──→ BL-084 (+BL-084b write-back)
           │
           └── (Provider profiles depended on by everything below)

M21b (Reader UI)
              ──→ BL-086 ──→ BL-087

M21c (History library)
                                       ──→ BL-085 ──→ BL-088
```

**Dependency note:** BL-084's daily cap reads from
`audit_events` (the append-only ledger), **not** from BL-085's
projection.  That's why BL-085 sits in M21c and not before
BL-084 — the order in the diagram matches the actual data-flow
dependency.

Acceptance gates:

* After **BL-084**, the operator can ask the live vault about an
  artifact from the CLI, audit the manifest, see the cost ledger,
  and hand insights back to absorb via `ovp-ask absorb`.  **This
  alone is the M21a milestone**; demote to optional if Reader UI
  doesn't earn its keep in evaluation.
* After **BL-087**, the operator can do the same from any Reader
  page.  M21b complete.
* After **BL-088**, sessions are searchable and listable from
  the Reader.  M21c complete.

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
| Visibility field uses `indexed` or `unindexed`; "private" banned in code + UI | Codex review #6 — "private" overpromises.  What we actually offer: OVP won't reuse / index.  Provider still receives the request.  Honest naming. |
| Reader-side `/chats`, no `/ops/chats` | Chat is consumer surface; Maintainer doesn't need a vocabulary mirror. |
