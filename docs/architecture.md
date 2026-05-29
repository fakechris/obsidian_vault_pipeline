# OVP Next — Architecture

This doc describes the system as it actually exists today (post Stage D). It supersedes the v0.1 sketch that lived here before. Stage docs (`stage-c.md`, `stage-d-plan-applier.md`) and calibration docs (`calibration-r1.md`, `calibration-r2.md`) are historical snapshots — they captured intent at a point in time and are not maintained against current code.

## Status

Five crates. 144 tests. Three fixture-driven acceptance gates green (`article_clean`, `article_mixed_lang`, `paper_arxiv`). The CLI can read an Obsidian-style clipping from disk, run it through the real domain pipeline, and write the resulting note to a vault directory — all offline, all deterministic. A unified pipeline routes a mixed inbox (articles + papers) by source kind. `ConceptResolver` promotes candidates to canonical via a `ConceptRegistry` (loadable from a JSON file or an evergreen-dir scan), not hardcoded constants. The live Anthropic client + cassette capture exist behind the `anthropic` feature; the default build / CI are offline.

Three closed loops:

1. **Record pipeline** — Source → Transform / EffectfulTransform → Sink, driven by `GraphRunner`. Records carry typed bodies; events log every step.
2. **LLM effect boundary** — `ModelClient` is a sync trait; `LLMInvoker` calls it from inside the pipeline; replay-only test gates have zero network deps; live runs land later (C9/C10).
3. **WritePlan → real filesystem** — `WritePlan` is dry-run; `VaultFsPlanApplier` is the only thing that mutates a vault. Hash-matched idempotence + path safety + before-hash checks all enforced at the applier.

## System primitives

The twelve nouns the rest of the system must be expressed in. Anything that isn't on this list is either a synonym (use the canonical name), a not-yet-introduced extension (don't anticipate it), or a deprecated term (see below).

| Primitive | Crate | One-line definition |
|---|---|---|
| `Record<B>` | ovp-core | Typed envelope carrying a body `B` through the pipeline. Generic over body so domain types don't leak into core. |
| `DomainBody` | ovp-domain | Sealed enum: `Source` \| `Prompt` \| `Model` \| `Interpreted` (article) \| `InterpretedPaper`. The body type the domain pipeline uses. `Source` carries a typed `SourceKind` (see below). |
| `Source<B>` | ovp-core | Node that brings records INTO the pipeline. Impls in ovp-domain: `MarkdownInboxSource` (single file), `InboxScanSource` (directory sweep, one record per file per tick). |
| `Transform<B>` | ovp-core | Pure node. `Record<B>` → `FilterDecision<B>`. No I/O, no held effect clients, deterministic. |
| `EffectfulTransform<B>` | ovp-core | Sync facade over an injected effect client (e.g. `Box<dyn ModelClient>`). Same signature as `Transform<B>`; distinct trait identity. |
| `Sink<B>` | ovp-core | Consumes records, emits `WriteOp`s. Does not perform real writes. |
| `FilterDecision<B>` | ovp-core | Sealed enum: `Forward` \| `Drop` \| `FanOut` \| `Complete` \| `Error` \| `ForwardWithEvents`. Every per-record outcome is first-class. |
| `Event` / `EventKind` | ovp-core | Append-only observation log entries. Discriminator is snake_case (`source_resolution`, `filter_dropped`, ...). |
| `PipelineManifest` | ovp-core | TOML (nodes + edges). Describes topology only. Wiring (which client, which inventory, which prompt) is app-layer. |
| `ModelClient` | ovp-llm | Sync trait: `&ModelRequest → Result<ModelReply, CallError>`. Impls: `FixtureModelClient`, `CachedModelClient` (per-request cassette namespacing via `ModelRequest.cache_namespace`), `NeverCallsClient`, `AnthropicBlockingClient` (behind `anthropic`). |
| `WritePlan` / `WriteOp` | ovp-core | The pipeline's side-effect output. `WriteOp` is a sealed enum: `VaultCreate` \| `VaultUpdate` \| `CanonicalUpsert` \| `EventAppend`. v1 only the first two are applied; the others are `Unsupported`. |
| `PlanApplier` / `ApplyReport` | ovp-core (trait) / ovp-stores (impl) | The single type allowed to mutate a real store. `VaultFsPlanApplier` is the v1 impl. Every op produces an `OpOutcome` (`Applied` / `Skipped` / `Failed` / `Unsupported`). |

## Source kinds & routing (v1.2)

`RouteBySourceKind` and `SourceKind` were deprecated placeholders in v1 ("don't anticipate paper routing that doesn't exist yet"). v1.2 makes them real — the deprecation's premise (no second source kind) has expired. Why the existing primitives don't cover it: a mixed inbox (articles + papers) needs each record dispatched to the interpreter that matches its kind, and `DomainBody::Source(SourceDoc)` alone carries no typed discriminator to dispatch on. Sniffing frontmatter fields at every downstream node would be exactly the untyped rot invariant #3 forbids.

- **`SourceKind`** (ovp-domain): a field on `SourceDoc`. `Article` | `Paper(PaperMeta)`. `PaperMeta` carries the paper-specific frontmatter (`arxiv_id`, `authors`, `categories`, `published`) as named fields — a sum type over structs, not optional grab-bag fields. `MarkdownInboxSource` classifies by the clipping's `source_type` (`arxiv-paper` → `Paper`, absent/other → `Article`). GitHub (terminal-raw) is a later stage and not yet a variant.
- **`RouteBySourceKind`** (ovp-domain, pure `Transform`): classifies the `Source` record, emits a `source_routed` event recording the chosen route, and forwards unchanged. The actual kind-filtering is done by the kind-specific prompt builders / parsers (each drops records whose kind or prompt id it doesn't handle), since the runner broadcasts a node's output to all downstream edges. `RouteBySourceKind` is the observable, auditable routing decision point.
- **`InterpretedPaper`** (`PaperDoc`, ovp-domain): papers have a different output shape than articles (10 sections vs. the 6 article dimensions), so they get their own `DomainBody` variant + sink rather than overloading `InterpretedDoc`. The article path (`Interpreted`) is untouched.

## Data flow (current real pipeline)

```
fixtures/<case>/input.md
    │
    ▼
┌────────────────────┐
│ MarkdownInboxSource│   reads YAML frontmatter + body, strips tracker URLs
└────────────────────┘
    │  Record<DomainBody::Source>
    ▼
┌────────────────────┐
│   SourceResolver   │   Twitter clip → underlying article URL; emits SourceResolution event
└────────────────────┘
    │  Record<DomainBody::Source>  (possibly with resolved source_url)
    ▼
┌────────────────────┐
│   PromptBuilder    │   versioned prompt asset + SourceDoc → PromptRequest
└────────────────────┘
    │  Record<DomainBody::Prompt>
    ▼
┌────────────────────┐
│    LLMInvoker      │   EffectfulTransform: Box<dyn ModelClient>.call()
└────────────────────┘     ↑
    │                      └── Cached / Fixture / NeverCalls
    │  Record<DomainBody::Model>
    ▼
┌────────────────────┐
│   ArticleParser    │   parses LLM JSON into InterpretedDoc; validates schema_version
└────────────────────┘
    │  Record<DomainBody::Interpreted>
    ▼
┌────────────────────┐
│  ConceptResolver   │   promotes candidates → canonical via ConceptRegistry (alias-aware)
└────────────────────┘
    │  Record<DomainBody::Interpreted>
    ▼
┌────────────────────┐
│ArticleVaultPlanSink│   renders InterpretedDoc to markdown + frontmatter; emits WriteOp
└────────────────────┘
    │
    ▼
WritePlan { ops: [VaultCreate(...)] }
    │
    ▼ (separate process step; ovp-cli apply-plan)
┌────────────────────┐
│VaultFsPlanApplier  │   path safety, hash idempotence, before_hash check
└────────────────────┘
    │
    ▼
<vault-root>/20-Areas/AI-Research/Topics/YYYY-MM/YYYY-MM-DD_<title>_深度解读.md
    │
    └─→ ApplyReport (Applied | Skipped | Failed | Unsupported per op)
```

Events flow alongside records, recorded by the runner: `RunStarted` → `SourceProduced` → `SourceResolution` → `RecordSeen` × N → `RecordForwarded` × N → `SinkEmitted` → `PlanFinalized` → `RunCompleted`. Filter drops record their reason code. The `EventLog` is append-only and not used for business queries.

## Crate responsibilities

- **`ovp-core`** — the small kernel. Owns the type contract: `Record<B>`, the four filter traits, `FilterDecision<B>`, `WritePlan`, `WriteOp`, `Event`, `EventLog`, `EventKind`, `PipelineManifest`, `GraphRunner`, `PlanApplier` trait + `ApplyReport`. Knows nothing about Obsidian, Markdown, HTTP, SQLite, LLMs, or specific domain shapes. Sync, single-threaded, ~1400 LOC. Architecture invariant #1.

- **`ovp-domain`** — typed bodies + the transforms / sources / sinks for the v1 article pipeline. Owns `DomainBody`, `SourceDoc`, `PromptRequest`, `ModelResponse`, `InterpretedDoc`, and the concrete transforms/sources/sinks above. Also owns **`VaultLayout`** — the single source of vault path conventions (PARA directory layout + `_深度解读.md` filename rules). It's a pure, root-agnostic value type returning vault-*relative* `VaultPath`s; it lives here rather than `ovp-core` precisely because the layout is Obsidian/domain knowledge that invariant #1 keeps out of the kernel. Sinks call `VaultLayout` instead of hardcoding paths. Also hosts the contract-assertion engine (behind the `testing` feature). Depends on `ovp-core` + `ovp-llm`. Article-shaped today; paper/github will eventually live here or in sibling domain crates.

- **`ovp-llm`** — effect-boundary crate for LLM calls. `ModelClient` trait + provider-neutral wire types (`ModelRequest`, `ModelReply`). Impls: `FixtureModelClient` (in-memory map), `NeverCallsClient` (errors on call), `CachedModelClient<C>` (file-backed cassette over an inner client; namespace is chosen per-request from `ModelRequest.cache_namespace`, falling back to the constructor namespace, so one client serves multiple prompt namespaces), and `AnthropicBlockingClient` (live, behind `--features anthropic`). `reqwest` is feature-gated; the default build pulls zero HTTP deps. The request/response mapping is pure and tested offline.

- **`ovp-stores`** — effect-boundary crate for `PlanApplier` impls. Today only `VaultFsPlanApplier` (filesystem markdown vaults). Future siblings: canonical store applier, event log applier. Same shape as `ovp-llm`: sync trait satisfied here, impl details (sha256, filesystem) contained.

- **`ovp-cli`** — thin app layer. Parses args, constructs the right `ModelClient` + transforms + applier, runs the pipeline. No business logic. Three subcommands today: `graph` (manifest inspection), `interpret-article` (the v1 pipeline), `apply-plan` (`WritePlan` → vault). `run --fake` remains from v0.1 for fake-source smoke tests.

## Deprecated vocabulary

Don't use these in new code or docs. They were considered or used early but are explicitly out-of-favor now.

| Don't say | Say instead | Why |
|---|---|---|
| `Interpreter` (as a code type) | `PromptBuilder` + `LLMInvoker` + `ArticleParser` | "Interpreter" packs three different jobs into one word. Spoken use ("the interpreter pipeline") is fine; in code/types, name the actual node. |
| `Store` (as a code type) | `PlanApplier` (the trait); a specific applier struct (`VaultFsPlanApplier`) for the impl | The thing that mutates a backend isn't a "Store" — Stores are the backends. The thing that talks to them via `WritePlan` is an applier. |
| `VaultStore` | `VaultFsPlanApplier` | Pre-Stage-D placeholder name. The current concrete impl has a precise name; use it. |
| `SourceBody` | `DomainBody::Source(SourceDoc)` | A parallel body enum is unnecessary — kind lives as a `SourceKind` field on `SourceDoc`, not as a separate body type. |
| `Effect` (as an architectural primitive) | `ModelClient` (or the specific client trait) | "Effect" is a category, not a primitive. Each effect boundary has a concrete trait name. |
| `Absorb` (as a stage/transform name) | `ConceptResolver` | The legacy Python system used "absorb"; our v1.1 implementation is more limited. Adopt the legacy name only if/when we match its semantics. |
| `Quality gate`, `MOC writer`, `Identity resolver` | (don't introduce until needed) | Speculative names from the original design doc. Each one is its own design problem when its fixture lands. |

If you find yourself reaching for a deprecated term, that's a signal the design is drifting — pause and check whether the existing primitive covers it.

## Boundaries we hold (architecture invariants summary)

The 12 invariants in `invariants.md` are the source of truth + CI-gated where possible. The five that drive day-to-day decisions:

1. **`ovp-core` is domain-blind AND I/O-blind.** No knowledge of Obsidian, Markdown, LLMs, filesystem layouts. Effect clients live in their own crates.
2. **Transform is pure; EffectfulTransform is the only sync facade over an injected effect client.** CI greps any `impl Transform<...> for T` for `Box<dyn .+Client>` fields and fails.
3. **`PlanApplier` is the only mutator.** Side effects to real stores happen here and nowhere else. Every op records an `OpOutcome`.
4. **Manifest = topology. Wiring = app layer.** The single source of truth is `(manifest, app wiring)`, not the manifest alone.
5. **All effect boundaries present sync surfaces.** Impls may hide async machinery (`Handle::block_on(...)`) — the executor doesn't need to be async.

## What comes next

Roadmap is driven by the legacy alignment baseline (see `docs/legacy-alignment.md`). **Landed:** C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), L3 (`ConceptRegistry`). Remaining:

1. **EvergreenConceptWriter** *(next)*. Extracts *new* evergreen candidates — `concept_candidates` that the `ConceptRegistry` does **not** yet know — and emits the first real `CanonicalUpsert` + evergreen `VaultCreate` write surface. This is the legacy "absorb" equivalent for the **mint-new-evergreen** half (L3 already does the candidate→canonical promotion half). It's a pure transform that decides what to write; the `WriteOp`s land through the existing sink/applier boundary. Prerequisite for the canonical store.
2. **Canonical store** *(gated on 1)*. A `PlanApplier` impl that applies `CanonicalUpsert`. Until `EvergreenConceptWriter` defines the concrete payload, the store has no producer and building it would be guessing at the write surface (invariant + alignment constraint). When (1) lands, the `CanonicalUpsertOp.payload: String` stub becomes typed data as part of this step.
3. **L4/L5 MOC + knowledge index + TxnFsApplier** *(gated on 2)*. Derived state, rebuildable from canonical + vault (invariant #11). Implemented against observed canonical/vault state, not guessed shapes. `TxnFsApplier` only if multi-file atomicity is actually required. Closes the first end-to-end cycle (raw → Evergreen → MOC → knowledge index).

After step 3 we have a real cycle; P1 gets re-triaged against observed pain.

## What this doc is and isn't

- **Is:** the current authoritative description of the system. Update this doc when the code changes.
- **Isn't:** a list of historical stages (see `calibration-r1.md`, `calibration-r2.md`, `stage-c.md`, `stage-d-plan-applier.md`) or a wishlist of future features (those go in stage docs as they're scoped).

If this doc disagrees with the code, the code wins and this doc needs a fix.
