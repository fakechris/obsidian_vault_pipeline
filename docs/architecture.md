# OVP2 — Architecture

This is the current authoritative description of the OVP2 system: the Rust
workspace at the repository root, the `ovp2` binary, and the product state it
maintains inside a vault. If this doc disagrees with the code, the code wins
and this doc needs a fix. Historical stage records live in `docs/stage-*.md`;
the OVP → OVP2 decision story is in [`ovp-to-ovp2.md`](./ovp-to-ovp2.md).

## 中文摘要

OVP2 是围绕三层模型构建的本地知识运行时：原文（Source，捕获的资料，永不
改写）→ 记忆（Memory，每源的接地 Unit 与可读 Card）→ 结晶（Knowledge，
跨源 Claim，durable/caveated，逐引用可溯源到原文行号）。权威状态 =
vault 内的 append-only 账本 + reader pack + Crystal store + 笔记本身；
索引、控制台、门户、主题视图都是可整体重建的投影。产品路径是
`daily`（每日循环）→ `serve`（门户）→ `crystal-synth` / 复核会话（真相层），
由机械化引用 gate 把关；prompt/gate/运行时的变更走演化内核治理。早期的
M7–M13 canonical/MOC/RAG 基底仍在树内构建与测试，但已整体降级（DEMOTED），
产品路径不依赖它。

## The three-layer model

| Layer | Objects | Where it lives |
|---|---|---|
| Source (原文) | Captured markdown: clippings, Pinboard notes, manual drops | `Clippings/`, `50-Inbox/` |
| Memory (记忆) | Grounded **Units** (verbatim quote + line numbers, `accepted_without_quote = 0`), readable **Cards** | Reader packs under `40-Resources/Reader/` |
| Knowledge (结晶) | Cross-source **Claims**, routed **durable** or **caveated**, every citation → Unit → quote → source lines | Crystal store under `.ovp/crystal/` |

Two state classes, one rule:

- **Authoritative**: the append-only JSONL ledgers (`.ovp/daily-runs.jsonl`,
  `intake.jsonl`, `pinboard-sync.jsonl`, the Crystal `ledger.jsonl`,
  `evolution-ledger.jsonl`), the reader packs, the run reports, and the vault
  notes themselves.
- **Derived**: everything under `.ovp/index/` and `.ovp/console/` (read model,
  evidence sidecar, console pages, portal data, theme views). Deleting a
  projection loses nothing; a full rebuild is the entire migration story.
  **If a projection cannot be rebuilt, that is an architecture bug.**

See [`product-state-layout.md`](./product-state-layout.md) for the on-disk
map and [`invariants.md`](./invariants.md) for the CI-gated invariants.

## Crate map

22 crates. The product path depends only on the foundations + product crates;
the demoted substrate still builds and tests but nothing on the product path
depends on it (CI-gated).

### Foundations (shared)

| Crate | Role |
|---|---|
| `ovp-core` | Sync kernel: `Record<B>`, filter traits, `GraphRunner`, `WritePlan`, `PlanApplier` trait, events. Domain-blind and I/O-blind. |
| `ovp-domain` | Domain types + transforms. Hosts the **reader trunk** (`reader` module: unit extraction, critic repair, card synthesis, pack rendering) and the **Crystal** logic (`crystal` module: gates, synthesis, strength routing), plus `VaultLayout` and the earlier article/paper pipeline nodes. |
| `ovp-llm` | `ModelClient` trait + Fixture / Cached (cassette) / NeverCalls impls; `AnthropicBlockingClient` behind the `anthropic` feature. Default build pulls zero HTTP deps. |
| `ovp-stores` | `PlanApplier` impls (vault fs, canonical fs, composite) + vault-scan helpers. The only layer that mutates real stores. |

### Product crates (the blessed path)

| Crate | Role |
|---|---|
| `ovp-intake` | Capture boundary: sweep `Clippings/` + `00-Capture` + `02-Pinboard` into normalized `01-Raw` with URL + content-sha256 dedup; append-only ledgers; `safe_move`/`write_new` that never overwrite; `RunLock`; the OVP_RULES write-log event. Pinboard live HTTP behind `pinboard-live`. |
| `ovp-daily` | The daily loop: plan (hash dedup + 3-failure block) → reader trunk per source → packs → lifecycle move (strictly after the success record is durable) → per-run report. |
| `ovp-enrich` | Enrichment for `needs-content` sources: web fetch, GitHub metadata, image download — behind `web-fetch-live` / `github-live` / `image-download-live`. |
| `ovp-index` | The read model: deterministic JSON projection (`.ovp/index/index.json` + `evidence.json`) folded from ledgers, packs, crystal store, and reports. Full rebuild every time; queried by `find`. No SQLite by decision. |
| `ovp-console` | Deterministic bilingual HTML console pages over the read model (`.ovp/console/`), including ops/audit/candidates depth pages. |
| `ovp-memory` | Ephemeral reuse surfaces: `digest`, `ask`, working memory. Budget-constrained LLM calls over the read model; never enters a ledger. |
| `ovp-server` | Synchronous HTTP server (`tiny_http`), localhost-only by default: portal SPA + console pages + the JSON API. |
| `ovp-mcp` | MCP stdio server (synchronous JSON-RPC): find/search/status/doctor tools, `ovp://index` and `ovp://working-memory` resources. |
| `ovp-evolve` | Evolution kernel: component registry, candidate validation, evolution ledger, deterministic root-cause diagnosis. |
| `ovp-cli` | Thin arg-parsing shell; owns the `ovp2` binary. Every verb labeled PRODUCT / DIAGNOSTIC / DEMOTED in `--help`. |

### Demoted substrate (M7–M13) and diagnostic harnesses

| Crate | Status |
|---|---|
| `ovp-app` (assembly), `ovp-run` (run-cycle), `ovp-query`, `ovp-lint`, `ovp-rag`, `ovp-auto` | **DEMOTED** — the eager canonical / MOC / concept-promotion / RAG substrate. Builds and tests; kept for reference; off the product path (M13 verdict: 0/3 real concept maps). |
| `ovp-review`, `ovp-eval` | **DIAGNOSTIC** — E2E review harness and the external comparator (Nowledge Mem AB). Not product paths. |

## The daily loop (dataflow)

`ovp2 daily --vault-root <vault> --client live` composes, in order:

```
Clippings/        50-Inbox/00-Capture/     Pinboard API / export
     │                    │                (--pinboard-live/-fixture,
     │                    │                 first-sync flood guard)
     └────────┬───────────┘                        │
              ▼                                    ▼
      ┌───────────────┐                 ┌────────────────────┐
      │ intake sweep  │◀────────────────│ 50-Inbox/02-Pinboard│
      └───────────────┘                 └────────────────────┘
              │  normalize + URL/sha256 dedup; duplicates parked,
              │  thin files flagged needs-content (enrichment re-queues)
              ▼
      50-Inbox/01-Raw/<YYYY-MM>/          ← the QUEUE
              │  up to --max-sources NEW sources per run
              ▼
      ┌──────────────────────────────────────────────┐
      │ reader trunk (per source)                    │
      │ Source → Grounded Units → Critic Repair →    │
      │ Reader Cards → Reader Pack                   │
      └──────────────────────────────────────────────┘
              │  packs: 40-Resources/Reader/<date>_<title>-<hash8>/
              ▼
      lifecycle move → 50-Inbox/03-Processed/<YYYY-MM>/
              │  (only after the success record is durable)
              ▼
      ledgers + report: .ovp/daily-runs.jsonl, .ovp/reports/<run_id>.json
      write log FIRST: 60-Logs/pipeline.jsonl (OVP_RULES ordering)
              ▼
      projections refreshed: .ovp/index/ + .ovp/console/
```

Failures are recorded and retried on the next run; 3 failures block a source
pending review (`--retry-blocked` to retry). Exit is non-zero if any source
failed. A `RunLock` with a dead-PID probe serializes concurrent runs.

## The reader trunk (memory layer)

Per source: parse the markdown → extract grounded **Units** — each must carry
a verbatim quote found in the source (hard gate: `accepted_without_quote = 0`)
with line anchors → a critic pass repairs or drops weak units → **Cards** are
synthesized only from accepted units → the pack renders `reader.html` /
`reader.md` with provenance intact, alongside `units.accepted.json`,
`cards.json`, `run-status.json`, and the raw model replies for audit.
`ovp2 read-source` runs the trunk on one source; `daily` runs it per queued
source.

## Crystal (knowledge layer): store + gates

- **Candidate synthesis** (`crystal-synth`): units catalog over reader packs →
  theme clusters → deterministic full-coverage sub-batches → cross-source
  synthesis (`crystal_synth/v1`) → grounded filter → citation-set dedup →
  chunked strength verdicts (`crystal_strength/v1`) → durable write. Reuses
  every gate/store function from `crystal-write` so the turnkey path cannot
  drift from the gated path.
- **The pre-write gate** (`crystal-lint`, reused by `crystal-write`):
  mechanical, fail-loud, no durable write. Every structured citation must
  resolve to an accepted Unit and its verbatim quote (the truth-layer
  matcher); provenance is scored deterministically; strength verdicts must be
  complete. Any gap → non-zero exit, nothing written.
- **The store** (`.ovp/crystal/`): append-only `ledger.jsonl` (idempotent by
  `claim_key`), rendered `crystal.md`, and `review.json` (the caveated queue).
  Only `durable` claims enter the ledger; caveated claims queue for review.
- **Human review** (`crystal-review-session` / `-apply`): a bounded session
  over the caveated queue with typed decision actions (`narrow`,
  `split_by_evidence`, `demote_to_source_insight`, `defer_until`,
  `reject_as_noise`, `keep_caveated`). Decisions author revised candidates;
  they never decide durability — every revision re-enters the strength gate.
- **Projection to notes** (`project --write` / `--rebuild`): durable claims as
  vault notes under `10-Knowledge/Crystal/`, machine-owned files marked
  `<!-- crystal-managed -->`, fully rebuildable from the ledger.

## Projections

All derived, all rebuilt in full, never authoritative:

| Projection | Path | Rebuilt by |
|---|---|---|
| Read model | `.ovp/index/index.json` | `ovp2 index` (also at the end of `daily`) |
| Evidence sidecar | `.ovp/index/evidence.json` | `ovp2 index`; feeds `ask` retrieval + citation verification |
| Console pages | `.ovp/console/*.html` | `ovp2 console` |
| Portal data | served live from the read model | `ovp2 serve` (+ `/api/refresh`) |
| Themes | theme groupings over claims (`/api/themes`) | index/serve; keyword clusters today — an embedding + Louvain **semantic theme** projection is in flight |

## The portal (SPA + API)

`ovp2 serve` hosts a React SPA (source in `console-ui/`, deployed to the
vault's `.ovp/console/app/`; a dev checkout can overlay any vault via
`--viz-dir console-ui/dist`). Six destinations — Today, Library (+ source
detail), Search (⌘K), Knowledge (themes, claim detail, scoped graph views),
Ask, System — with the legacy generated console still reachable at
`/legacy-index.html` and the ops/audit/candidates depth pages linked from
System.

Design system: dual equal-weight themes — light "Atelier", dark "Vault" —
IBM Plex Sans / Sans SC / Mono self-hosted, shared graph color tokens,
`data-theme` + `localStorage['ovp-theme']`. i18n: EN default with a full
简体中文 translation, `localStorage['ovp-lang']`.

JSON API (localhost by default): `/api/model`, `/api/find`, `/api/search`,
`/api/graph` (scoped), `/api/themes`, `/api/claim/:id`, `/api/source/:sha`,
`/api/flow`, `/api/chats`, `/api/settings`, `/api/refresh`, and `POST
/api/ask` (guarded: admission control, CSRF gates, in-flight cap).

## The evolution kernel

Behavioral change surfaces (prompt, parser, runtime, gate, model) change only
through governed candidates: every optimizable component is registered in
`evolution/components.json`; a candidate spec states its hypothesis and single
target surface; validation and paired A/B run over the cassette substrate; the
decision lands in the append-only `.ovp/evolution-ledger.jsonl` with rollback
instructions. Deterministic root-cause diagnosis attributes daily failures to
a surface (parser vs runtime vs prompt/model) so runtime bugs do not get
prompt-patched. CLI: `ovp2 evolve registry|validate|ledger|diagnose`. Design:
[`design/evolution-kernel.md`](./design/evolution-kernel.md).

## Feature flags

The offline build is the default: zero network dependencies, replay-only
model client, deterministic tests. Prebuilt release binaries compile all live
features in; runtime behavior stays opt-in via flags/env.

| Feature | Enables | Env |
|---|---|---|
| `anthropic` | `--client live` LLM calls (`AnthropicBlockingClient`) | `ANTHROPIC_API_KEY`, `OVP_LLM_TIMEOUT_SECS`, … |
| `pinboard-live` | `pinboard-sync --live`, `daily --pinboard-live` | `PINBOARD_TOKEN` |
| `web-fetch-live` | live web fetch for `needs-content` enrichment | — |
| `github-live` | GitHub README/metadata enrichment | — |

Embeddings for the semantic-theme projection are in flight and not yet a
shipped feature.

## Invariants (summary)

The full list is [`invariants.md`](./invariants.md); the ones that drive
day-to-day decisions:

1. **Append-only ledgers.** A malformed ledger line is a hard error; ledgers
   are never rewritten. Audit ordering: write → `pipeline.jsonl` event →
   ledger record — a crash can duplicate an event, never lose one.
2. **Never delete, never overwrite.** Lifecycle transitions are renames with
   collision suffixes; duplicates are parked, not removed.
3. **Idempotency.** Source identity = sha256 of bytes (URL as secondary dedup
   key); re-runs skip processed work; Crystal writes are idempotent by
   `claim_key`; `project --rebuild` only deletes machine-owned files.
4. **Projections are never authoritative** and must rebuild in full from
   authoritative state.
5. **Gates fail loud.** No silent repair; human review decisions never bypass
   the strength gate.
6. **`ovp-core` stays domain-blind and I/O-blind**; effect boundaries present
   sync surfaces; `PlanApplier` impls are the only store mutators.
