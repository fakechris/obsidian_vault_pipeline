# OVP Next — Architecture

This doc describes the system as it actually exists today (post Stage D). It supersedes the v0.1 sketch that lived here before. Stage docs (`stage-c.md`, `stage-d-plan-applier.md`) and calibration docs (`calibration-r1.md`, `calibration-r2.md`) are historical snapshots — they captured intent at a point in time and are not maintained against current code.

## Status

Five crates. 99 tests. Two fixture-driven acceptance gates green (`article_clean`, `article_mixed_lang`). The CLI can read an Obsidian-style clipping from disk, run it through the real domain pipeline, and write the resulting note to a vault directory — all offline, all deterministic.

Three closed loops:

1. **Record pipeline** — Source → Transform / EffectfulTransform → Sink, driven by `GraphRunner`. Records carry typed bodies; events log every step.
2. **LLM effect boundary** — `ModelClient` is a sync trait; `LLMInvoker` calls it from inside the pipeline; replay-only test gates have zero network deps; live runs land later (C9/C10).
3. **WritePlan → real filesystem** — `WritePlan` is dry-run; `VaultFsPlanApplier` is the only thing that mutates a vault. Hash-matched idempotence + path safety + before-hash checks all enforced at the applier.

## System primitives

The twelve nouns the rest of the system must be expressed in. Anything that isn't on this list is either a synonym (use the canonical name), a not-yet-introduced extension (don't anticipate it), or a deprecated term (see below).

| Primitive | Crate | One-line definition |
|---|---|---|
| `Record<B>` | ovp-core | Typed envelope carrying a body `B` through the pipeline. Generic over body so domain types don't leak into core. |
| `DomainBody` | ovp-domain | Sealed enum: `Source` \| `Prompt` \| `Model` \| `Interpreted`. The body type the v1 article pipeline uses. |
| `Source<B>` | ovp-core | Node that brings records INTO the pipeline. The only Source impl today is `MarkdownInboxSource`. |
| `Transform<B>` | ovp-core | Pure node. `Record<B>` → `FilterDecision<B>`. No I/O, no held effect clients, deterministic. |
| `EffectfulTransform<B>` | ovp-core | Sync facade over an injected effect client (e.g. `Box<dyn ModelClient>`). Same signature as `Transform<B>`; distinct trait identity. |
| `Sink<B>` | ovp-core | Consumes records, emits `WriteOp`s. Does not perform real writes. |
| `FilterDecision<B>` | ovp-core | Sealed enum: `Forward` \| `Drop` \| `FanOut` \| `Complete` \| `Error` \| `ForwardWithEvents`. Every per-record outcome is first-class. |
| `Event` / `EventKind` | ovp-core | Append-only observation log entries. Discriminator is snake_case (`source_resolution`, `filter_dropped`, ...). |
| `PipelineManifest` | ovp-core | TOML (nodes + edges). Describes topology only. Wiring (which client, which inventory, which prompt) is app-layer. |
| `ModelClient` | ovp-llm | Sync trait: `&ModelRequest → Result<ModelReply, CallError>`. Impls: `FixtureModelClient`, `CachedModelClient`, `NeverCallsClient` (`AnthropicBlockingClient` lands in C9). |
| `WritePlan` / `WriteOp` | ovp-core | The pipeline's side-effect output. `WriteOp` is a sealed enum: `VaultCreate` \| `VaultUpdate` \| `CanonicalUpsert` \| `EventAppend`. v1 only the first two are applied; the others are `Unsupported`. |
| `PlanApplier` / `ApplyReport` | ovp-core (trait) / ovp-stores (impl) | The single type allowed to mutate a real store. `VaultFsPlanApplier` is the v1 impl. Every op produces an `OpOutcome` (`Applied` / `Skipped` / `Failed` / `Unsupported`). |

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
│  ConceptResolver   │   promotes linked_concepts → canonical_concepts via inventory
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

- **`ovp-domain`** — typed bodies + the transforms / sources / sinks for the v1 article pipeline. Owns `DomainBody`, `SourceDoc`, `PromptRequest`, `ModelResponse`, `InterpretedDoc`, and the six concrete transforms above. Also hosts the contract-assertion engine (behind the `testing` feature) used by fixture acceptance tests. Depends on `ovp-core` + `ovp-llm`. Article-shaped today; paper/github will eventually live here or in sibling domain crates.

- **`ovp-llm`** — effect-boundary crate for LLM calls. `ModelClient` trait + provider-neutral wire types (`ModelRequest`, `ModelReply`). Three impls: `FixtureModelClient` (in-memory map), `NeverCallsClient` (errors on call), `CachedModelClient<C>` (file-backed namespaced cassette over an inner client). `AnthropicBlockingClient` will land behind `--features anthropic` in C9. `reqwest` is feature-gated; the default build pulls zero HTTP deps.

- **`ovp-stores`** — effect-boundary crate for `PlanApplier` impls. Today only `VaultFsPlanApplier` (filesystem markdown vaults). Future siblings: canonical store applier, event log applier. Same shape as `ovp-llm`: sync trait satisfied here, impl details (sha256, filesystem) contained.

- **`ovp-cli`** — thin app layer. Parses args, constructs the right `ModelClient` + transforms + applier, runs the pipeline. No business logic. Three subcommands today: `graph` (manifest inspection), `interpret-article` (the v1 pipeline), `apply-plan` (`WritePlan` → vault). `run --fake` remains from v0.1 for fake-source smoke tests.

## Deprecated vocabulary

Don't use these in new code or docs. They were considered or used early but are explicitly out-of-favor now.

| Don't say | Say instead | Why |
|---|---|---|
| `Interpreter` (as a code type) | `PromptBuilder` + `LLMInvoker` + `ArticleParser` | "Interpreter" packs three different jobs into one word. Spoken use ("the interpreter pipeline") is fine; in code/types, name the actual node. |
| `Store` (as a code type) | `PlanApplier` (the trait); a specific applier struct (`VaultFsPlanApplier`) for the impl | The thing that mutates a backend isn't a "Store" — Stores are the backends. The thing that talks to them via `WritePlan` is an applier. |
| `VaultStore` | `VaultFsPlanApplier` | Pre-Stage-D placeholder name. The current concrete impl has a precise name; use it. |
| `SourceBody` / `SourceKind` | `DomainBody::Source(SourceDoc)` | These names anticipate paper/github routing that doesn't exist yet. Introducing them now leaks future structure into v1. |
| `Effect` (as an architectural primitive) | `ModelClient` (or the specific client trait) | "Effect" is a category, not a primitive. Each effect boundary has a concrete trait name. |
| `RouteBySourceKind` | (don't name it yet) | Will exist when v1.2 (paper) lands. Premature now. |
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

Roadmap is now driven by the legacy alignment baseline (see `docs/legacy-alignment.md` — living gap matrix between this rewrite and the legacy Python `ovp_pipeline`). The previously-locked order holds with two insertions surfaced by the P0 gaps:

1. **C9 + C10** — live `AnthropicBlockingClient` + real cassette capture. Unchanged. Unblocks any stage that calls a model from doing real work.
2. **L0/L1 intake + VaultLayout port** *(new)*. P0 gaps `L1 article/github intake` + `VaultLayout port`. The first real Source filters land here; without them the rest of the pipeline is fixture-fed. Bringing intake in before paper forces the Source contract to settle.
3. **v1.2 — paper deep-dive transform**. Same scope as before, now slotted after intake so it has a real upstream.
4. **L3 absorb + ConceptRegistry data model** *(new)*. P0 gaps `L3 absorb` + `ConceptRegistry data model`. The single highest-cognitive-load legacy step; surfacing it before canonical store gives the store a concrete consumer to validate against.
5. **Canonical store**. Same intent, now informed by absorb's actual write surface. `CanonicalUpsertOp` gets real producers (absorb) and a real reader (MOC + index, next).
6. **L4/L5 MOC + knowledge index + TxnFsApplier** *(new)*. P0 gaps `MOC generation` + `Knowledge index rebuild` + `TransactionManager`. Closes the first end-to-end cycle (raw → Evergreen → MOC → knowledge.db) and unlocks the bulk of P1 readers (query, lint, ops_state, doctor).

After step 6 we have a real cycle; P1 gets re-triaged against observed pain.

Codex review of Stage D + the architecture/alignment docs is queued but no longer the gating next step — it runs against whichever stage is in flight when it next makes sense.

## What this doc is and isn't

- **Is:** the current authoritative description of the system. Update this doc when the code changes.
- **Isn't:** a list of historical stages (see `calibration-r1.md`, `calibration-r2.md`, `stage-c.md`, `stage-d-plan-applier.md`) or a wishlist of future features (those go in stage docs as they're scoped).

If this doc disagrees with the code, the code wins and this doc needs a fix.
