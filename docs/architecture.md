# OVP Next — Architecture

This doc describes the system as it actually exists today (post Stage D). It supersedes the v0.1 sketch that lived here before. Stage docs (`stage-c.md`, `stage-d-plan-applier.md`) and calibration docs (`calibration-r1.md`, `calibration-r2.md`) are historical snapshots — they captured intent at a point in time and are not maintained against current code.

## Status

Six crates. 243 tests. Three fixture-driven acceptance gates green (`article_clean`, `article_mixed_lang`, `paper_arxiv`). The CLI can read an Obsidian-style clipping from disk, run it through the real domain pipeline, and write the resulting note to a vault directory — all offline, all deterministic. A unified pipeline routes a mixed inbox (articles + papers) by source kind. `ConceptResolver` promotes known candidates to canonical via a `ConceptRegistry`; `EvergreenConceptWriter` mints *new* evergreen concepts, emitting `CanonicalUpsert` + evergreen `VaultCreate` ops. A `CompositePlanApplier` over `VaultFsPlanApplier` + `CanonicalFsStoreApplier` applies the full plan — vault notes, evergreen stubs, AND canonical records. Two **derived** artifacts rebuild from that state (invariant #11): the Atlas MOC index (`MocBuilder`, from the canonical store) and a knowledge index with backlinks (`KnowledgeIndexBuilder`, from canonical + a vault `[[wikilink]]` scan). The full legacy cycle — raw → note → evergreen → canonical → MOC + knowledge index — is closed and tested end-to-end, idempotent on re-apply. Canonical identity is hardened: a single `CanonicalSlug` rule (filename-safe, single-segment, aligned to `VaultLayout::sanitize_filename`) is shared across the store key, the evergreen path, and the record id; appliers validate declared content hashes before any write; `CompositePlanApplier` applies ops in plan order and halts on the first failure (so a failed `VaultCreate` can't leave its paired `CanonicalUpsert` half-written); and derived rebuilds parse the canonical store strictly (key == slug, slug valid, fail-loud on corruption). See `invariants.md` "Canonical-store integrity". Pipelines are now **assembled** declaratively: `ovp-app::GraphAssembler` builds the `GraphRunner<DomainBody>` from an enriched manifest (node `id` + `kind` + `config` + `edges`) plus `AppWiring` (the live `ModelClient` / `ConceptRegistry` + per-run `run_id` / `date` / `area` / `input path`), so the CLI and tests no longer hand-wire `register_*` calls. See `docs/stage-graph-assembly.md`. The live Anthropic client + cassette capture exist behind the `anthropic` feature; the default build / CI are offline.

Three closed loops:

1. **Record pipeline** — Source → Transform / EffectfulTransform → Sink, driven by `GraphRunner`. Records carry typed bodies; events log every step.
2. **LLM effect boundary** — `ModelClient` is a sync trait; `LLMInvoker` calls it from inside the pipeline; replay-only test gates have zero network deps; live runs go through `AnthropicBlockingClient` behind the `anthropic` feature (landed C9/C10).
3. **WritePlan → real stores** — `WritePlan` is dry-run; a `PlanApplier` is the only thing that mutates a store. `VaultFsPlanApplier` (vault files) + `CanonicalFsStoreApplier` (canonical records), composed by `CompositePlanApplier`. Hash-matched idempotence, path/key safety, before-hash checks, declared-hash validation, and ordered halt-on-failure all enforced at the applier layer.

## System primitives

The twelve nouns the rest of the system must be expressed in. Anything that isn't on this list is either a synonym (use the canonical name), a not-yet-introduced extension (don't anticipate it), or a deprecated term (see below).

| Primitive | Crate | One-line definition |
|---|---|---|
| `Record<B>` | ovp-core | Typed envelope carrying a body `B` through the pipeline. Generic over body so domain types don't leak into core. |
| `DomainBody` | ovp-domain | Sealed enum: `Source` \| `Prompt` \| `Model` \| `Interpreted` (article) \| `InterpretedPaper` \| `EvergreenConcept` (a new evergreen to mint). The body type the domain pipeline uses. `Source` carries a typed `SourceKind` (see below). |
| `Source<B>` | ovp-core | Node that brings records INTO the pipeline. Impls in ovp-domain: `MarkdownInboxSource` (single file), `InboxScanSource` (directory sweep, one record per file per tick). |
| `Transform<B>` | ovp-core | Pure node. `Record<B>` → `FilterDecision<B>`. No I/O, no held effect clients, deterministic. |
| `EffectfulTransform<B>` | ovp-core | Sync facade over an injected effect client (e.g. `Box<dyn ModelClient>`). Same signature as `Transform<B>`; distinct trait identity. |
| `Sink<B>` | ovp-core | Consumes records, emits `WriteOp`s. Does not perform real writes. |
| `FilterDecision<B>` | ovp-core | Sealed enum: `Forward` \| `Drop` \| `FanOut` \| `Complete` \| `Error` \| `ForwardWithEvents`. Every per-record outcome is first-class. |
| `Event` / `EventKind` | ovp-core | Append-only observation log entries. Discriminator is snake_case (`source_resolution`, `filter_dropped`, ...). |
| `PipelineManifest` | ovp-core | TOML (nodes + edges). Describes **topology only** — no node kind, no config. The app-layer `ovp-app::DomainPipelineSpec` overlays node `kind`+`config` (an `[assembly.<id>]` section the topology parser ignores) on the same file; core stays topology-only. |
| `ModelClient` | ovp-llm | Sync trait: `&ModelRequest → Result<ModelReply, CallError>`. Impls: `FixtureModelClient`, `CachedModelClient` (per-request cassette namespacing via `ModelRequest.cache_namespace`), `NeverCallsClient`, `AnthropicBlockingClient` (behind `anthropic`). |
| `WritePlan` / `WriteOp` | ovp-core | The pipeline's side-effect output. `WriteOp` is a sealed enum: `VaultCreate` \| `VaultUpdate` \| `CanonicalUpsert` \| `EventAppend`. `VaultCreate`/`VaultUpdate` apply via `VaultFsPlanApplier`, `CanonicalUpsert` via `CanonicalFsStoreApplier`; `EventAppend` has no applier yet (reported `Unsupported`). |
| `PlanApplier` / `ApplyReport` | ovp-core (trait) / ovp-stores (impls) | The only type allowed to mutate a real store. Impls: `VaultFsPlanApplier` (vault files), `CanonicalFsStoreApplier` (canonical records), `CompositePlanApplier` (routes ops by kind across backends). Every op produces an `OpOutcome` (`Applied` / `Skipped` / `Failed` / `Unsupported`). |

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

- **`ovp-stores`** — effect-boundary crate for `PlanApplier` impls + derived-state read helpers. `VaultFsPlanApplier` (filesystem markdown vaults), `CanonicalFsStoreApplier` (filesystem canonical-record store; domain-blind — persists the op payload bytes keyed by canonical key, with idempotence + `before_hash` optimistic-concurrency guard, and a `read_all` for rebuilds), `CompositePlanApplier` (fans a plan across backends handling disjoint op kinds), and `walk_markdown` (domain-blind recursive `*.md` reader for backlink scans). Future sibling: an event-log applier. Same shape as `ovp-llm`: sync trait satisfied here, impl details (sha256, filesystem) contained; depends only on `ovp-core`.

- **`ovp-app`** — the assembly layer. Turns a declarative `DomainPipelineSpec` (topology + node `kind`/`config`) plus `AppWiring` (the live effect objects + per-run values) into a ready `GraphRunner<DomainBody>`, via a compiled-in `NodeRegistry` of node factories and a `GraphAssembler`. This is where the "which `node_id` is which concrete node" knowledge lives — once, instead of duplicated across the CLI and every test. DirectShow-like in spirit, **not** a plugin system: no dynamic loading, no ABI, the node set is compiled in. Depends on `ovp-core`, `ovp-domain`, `ovp-llm`. `ovp-core` knows none of its `NodeKind`s. See `docs/stage-graph-assembly.md`.

- **`ovp-cli`** — thin app layer, now thinner: parses args, builds the `ModelClient` + `ConceptRegistry` + `AppWiring`, and delegates pipeline construction to `ovp-app::GraphAssembler` (no hand-wired `register_*` on the main path — CI-gated). No business logic. Subcommands: `graph` (manifest inspection), `interpret-article` (the v1 pipeline, assembled), `apply-plan` (`WritePlan` → vault). `run --fake` remains from v0.1 for fake-source smoke tests.

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
| `NodeRegistry` *as a business/identity authority* | `NodeRegistry` is **assembly-only** | `ovp-app::NodeRegistry` maps `NodeKind → node factory` and is consulted ONLY during `GraphAssembler::assemble`. It holds no domain state, no canonical authority, and is never read at runtime. "Registry" otherwise stays a guarded word here (the legacy system drowned in registry sprawl); do not grow `NodeRegistry` into a place that resolves identity, stores concepts, or persists anything. Canonical identity lives in `ConceptRegistry` + the canonical store, nowhere else. |

If you find yourself reaching for a deprecated term, that's a signal the design is drifting — pause and check whether the existing primitive covers it.

## Boundaries we hold (architecture invariants summary)

The 12 invariants in `invariants.md` are the source of truth + CI-gated where possible. The five that drive day-to-day decisions:

1. **`ovp-core` is domain-blind AND I/O-blind.** No knowledge of Obsidian, Markdown, LLMs, filesystem layouts. Effect clients live in their own crates.
2. **Transform is pure; EffectfulTransform is the only sync facade over an injected effect client.** CI greps any `impl Transform<...> for T` for `Box<dyn .+Client>` fields and fails.
3. **`PlanApplier` is the only mutator.** Side effects to real stores happen here and nowhere else. Every op records an `OpOutcome`.
4. **Manifest describes the pipeline; wiring supplies the runtime.** The manifest carries node `id` + `kind` + `config` + `edges` (topology in `[pipeline]`, kind/config in the `[assembly]` overlay); `ovp-core::PipelineManifest` still reads only topology. `AppWiring` supplies the runtime dependencies a static file can't hold (the live `ModelClient`/`ConceptRegistry`, `run_id`, dates, input path). The single source of truth is `(manifest, AppWiring)`, assembled by `ovp-app::GraphAssembler` — never hand-wired on the main path.
5. **All effect boundaries present sync surfaces.** Impls may hide async machinery (`Handle::block_on(...)`) — the executor doesn't need to be async.

## What comes next

Roadmap is driven by the legacy alignment baseline (see `docs/legacy-alignment.md`).

**Landed:** C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), L3 (`ConceptRegistry`); `EvergreenConceptWriter` + `EvergreenSink` (mint new evergreens → evergreen `VaultCreate` + `CanonicalUpsert`); the **canonical store** (`CanonicalFsStoreApplier` + typed `CanonicalConcept`, composed by `CompositePlanApplier` so a full plan applies with zero unsupported ops); **L4/L5** (`MocBuilder` + `KnowledgeIndexBuilder`, derived + rebuildable from canonical + vault, invariant #11); **canonical-identity hardening** (`CanonicalSlug` + strict rebuild parse — see `invariants.md` "Canonical-store integrity"); and the **Graph Assembly Layer** (`ovp-app`: a declarative manifest + `AppWiring` assemble into a `GraphRunner`, with up-front validation of graph shape, per-kind config, and required runtime wiring — see `docs/stage-graph-assembly.md`). `TxnFsApplier` was assessed and deferred — every op is idempotent, so multi-file atomicity isn't required (re-apply recovers a partial run).

The full legacy cycle (raw → note → evergreen → canonical → MOC + knowledge index) is closed and assembled declaratively.

**Next:** an **operational end-to-end command** (process-inbox / run-cycle) that assembles a manifest and drives an inbox file → vault note + evergreen + canonical + MOC + knowledge index in one shot, idempotent on re-run — the real test of whether the assembly layer holds up under the main flow. Then the P1 read/health surfaces (`ovp-query` over the knowledge index, `ovp-lint`) and the autopilot watcher, re-triaged from `docs/legacy-alignment.md` against observed pain.

## What this doc is and isn't

- **Is:** the current authoritative description of the system. Update this doc when the code changes.
- **Isn't:** a list of historical stages (see `calibration-r1.md`, `calibration-r2.md`, `stage-c.md`, `stage-d-plan-applier.md`) or a wishlist of future features (those go in stage docs as they're scoped).

If this doc disagrees with the code, the code wins and this doc needs a fix.
