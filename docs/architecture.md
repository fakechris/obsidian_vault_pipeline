# OVP Next — Architecture

This doc describes the system as it actually exists today (post Stage D). It supersedes the v0.1 sketch that lived here before. Stage docs (`stage-c.md`, `stage-d-plan-applier.md`) and calibration docs (`calibration-r1.md`, `calibration-r2.md`) are historical snapshots — they captured intent at a point in time and are not maintained against current code.

## Status

Eleven crates. 301 tests. Three fixture-driven acceptance gates green (`article_clean`, `article_mixed_lang`, `paper_arxiv`). The CLI can read an Obsidian-style clipping from disk, run it through the real domain pipeline, and write the resulting note to a vault directory — all offline, all deterministic. A unified pipeline routes a mixed inbox (articles + papers) by source kind. `ConceptResolver` promotes known candidates to canonical via a `ConceptRegistry`; `EvergreenConceptWriter` mints *new* evergreen concepts, emitting `CanonicalUpsert` + evergreen `VaultCreate` ops. A `CompositePlanApplier` over `VaultFsPlanApplier` + `CanonicalFsStoreApplier` applies the full plan — vault notes, evergreen stubs, AND canonical records. Two **derived** artifacts rebuild from that state (invariant #11): the Atlas MOC index (`MocBuilder`, from the canonical store) and a knowledge index with backlinks (`KnowledgeIndexBuilder`, from canonical + a vault `[[wikilink]]` scan). The full legacy cycle — raw → note → evergreen → canonical → MOC + knowledge index — is closed and tested end-to-end, idempotent on re-apply. Canonical identity is hardened: a single `CanonicalSlug` rule (filename-safe, single-segment, aligned to `VaultLayout::sanitize_filename`) is shared across the store key, the evergreen path, and the record id; appliers validate declared content hashes before any write; `CompositePlanApplier` applies ops in plan order and halts on the first failure (so a failed `VaultCreate` can't leave its paired `CanonicalUpsert` half-written); and derived rebuilds parse the canonical store strictly (key == slug, slug valid, fail-loud on corruption). See `invariants.md` "Canonical-store integrity". Pipelines are now **assembled** declaratively: `ovp-app::GraphAssembler` builds the `GraphRunner<DomainBody>` from an enriched manifest (node `id` + `kind` + `config` + `edges`) plus `AppWiring` (the live `ModelClient` / `ConceptRegistry` + per-run `run_id` / `date` / `area` / `input path`), so the CLI and tests no longer hand-wire `register_*` calls. See `docs/stage-graph-assembly.md`. The live Anthropic client + cassette capture exist behind the `anthropic` feature; the default build / CI are offline.

Three closed loops:

1. **Record pipeline** — Source → Transform / EffectfulTransform → Sink, driven by `GraphRunner`. Records carry typed bodies; events log every step.
2. **LLM effect boundary** — `ModelClient` is a sync trait; `LLMInvoker` calls it from inside the pipeline; replay-only test gates have zero network deps; live runs go through `AnthropicBlockingClient` behind the `anthropic` feature (landed C9/C10).
3. **WritePlan → real stores** — `WritePlan` is dry-run; a `PlanApplier` is the only thing that mutates a store. `VaultFsPlanApplier` (vault files) + `CanonicalFsStoreApplier` (canonical records), composed by `CompositePlanApplier`. Hash-matched idempotence, path/key safety, before-hash checks, declared-hash validation, and ordered halt-on-failure all enforced at the applier layer.

## Target architecture layers

The system is a stack of layers, **one crate per layer**; higher layers depend only on lower ones. This is the north star — new work is placed against these layers, not scattered.

| Layer | Crate | Owns | Must NOT |
|---|---|---|---|
| **L0 Kernel** | `ovp-core` | `Record` / `Source` / `Transform` / `EffectfulTransform` / `Sink` / `GraphRunner` / `PipelineManifest` / `WritePlan` / `PlanApplier` trait. Sync. | know any domain type, Obsidian, LLM, HTTP, or filesystem layout |
| **L1 Domain node catalog** | `ovp-domain` | `DomainBody` + typed bodies; concrete nodes (resolver, prompt builders, parsers, concept resolver, evergreen writer, sinks); `VaultLayout`. | perform real writes; depend on a CLI |
| **L2 Assembly** | `ovp-app` | `DomainPipelineSpec` / `NodeKind` / `NodeRegistry` / `NodeConfig` / `AppWiring` / `GraphAssembler`. Manifest + wiring → `GraphRunner`. | apply plans; depend on `ovp-stores`; dynamic loading / async / JSON DSL |
| **L3 Store / apply** | `ovp-stores` | `VaultFsPlanApplier`, `CanonicalFsStoreApplier`, `CompositePlanApplier`, `read_all`, `walk_markdown`. All mutations via `WritePlan` → `PlanApplier`; derived state rebuildable. | own pipeline execution |
| **L4 Operational workflow** | `ovp-run` | the `run-cycle`: assemble → run → apply → rebuild MOC + knowledge index *as derived artifacts post-apply (not pipeline nodes)* → report; idempotent on re-run, fail-closed. | duplicate node-construction or apply logic |
| **L5 Read / health** | `ovp-query`, `ovp-lint` | read canonical / vault / knowledge index; report health. | own mutation or pipeline execution |
| **L6 RAG / automation** | `ovp-rag`, `ovp-auto` | RAG read path (`RagCorpus` + `Retriever` + `Ranker` + `ContextBuilder` + `Eval`) over the L5 read model; a one-shot automation sweep (`AutoRun`) that *calls* L4 `RunCycle` then L5 `Lint`. | mutate (RAG is read-only); duplicate L4 workflow logic; add async / a watcher daemon (v1) |

`ovp-cli` is a thin shell over L2/L4/L6 — it parses args and constructs clients/paths, nothing more. Dependency direction (acyclic): `ovp-cli → ovp-run → {ovp-app, ovp-stores, ovp-domain, ovp-core}`; `ovp-app → {ovp-domain, ovp-llm, ovp-core}`; `ovp-stores → ovp-core`; `ovp-domain → {ovp-llm, ovp-core}`; `ovp-rag → ovp-query`; `ovp-auto → {ovp-run, ovp-lint, ovp-stores}`. **Why L4 is its own crate (`ovp-run`), not folded into `ovp-app`:** it keeps the crate↔layer mapping 1:1 and L2 pure (assembly never applies plans or depends on L3); the operational workflow is the thing that wires L2+L3 together.

## System primitives

The kernel + domain + effect + store nouns the rest of the system is expressed in (the **assembly** primitives are a separate set — see the subsection below). Anything not on these lists is either a synonym (use the canonical name), a not-yet-introduced extension (don't anticipate it), or a deprecated term (see below).

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

### Assembly primitives (L2 — `ovp-app`)

A **separate** set from the kernel primitives above: they describe how a pipeline is *assembled*, and live entirely in `ovp-app`. `ovp-core` knows none of them (invariant #1).

| Primitive | One-line definition |
|---|---|
| `NodeKind` | Stable `<category>.<name>` id for a concrete node factory (e.g. `effect.llm_invoker`). |
| `NodeCategory` | `Source` / `Transform` / `Effect` / `Sink` — which `register_*` slot a node occupies. |
| `NodeRegistry` | Compiled-in catalog `NodeKind → factory`. **Assembly-only** — no authority, no runtime reads (see deprecated vocabulary). |
| `NodeConfig` | Per-node static config: the **names** of the `AppWiring` entries to bind (`client`, `registry`). Not values. |
| `DomainPipelineSpec` | Enriched manifest = `PipelineManifest` topology (`[pipeline]`) + a `[assembly.<id>]` kind/config overlay the topology parser ignores. |
| `AppWiring` | Runtime deps a static file can't hold: `run_id`, `date_stamp`, `area`, `input_path`, and named `ModelClient`s + `ConceptRegistry`s bound by config. |
| `GraphAssembler` | Validates (unknown-kind, category-vs-edges, per-kind config, required wiring, acyclic single-component source→sink shape) **before any build**, then builds a `GraphRunner` from spec + wiring. |

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

- **`ovp-stores`** — effect-boundary crate for `PlanApplier` impls + derived-state read helpers. `VaultFsPlanApplier` (filesystem markdown vaults), `CanonicalFsStoreApplier` (filesystem canonical-record store; domain-blind — persists the op payload bytes keyed by canonical key, with idempotence + `before_hash` optimistic-concurrency guard, and a `read_all` for rebuilds), `CompositePlanApplier` (fans a plan across backends handling disjoint op kinds), and the domain-blind vault-scan helpers `walk_markdown` / `backlinks_from_files` / `scan_backlinks` (the last two take the wikilink extractor as a closure, so the crate stays domain-blind while L4 `run-cycle` and L5 `lint` share one backlink-scan implementation). Future sibling: an event-log applier. Same shape as `ovp-llm`: sync trait satisfied here, impl details (sha256, filesystem) contained; depends only on `ovp-core`.

- **`ovp-app`** — the assembly layer (L2). Turns a declarative `DomainPipelineSpec` (topology + node `kind`/`config`) plus `AppWiring` (the live effect objects + per-run values) into a ready `GraphRunner<DomainBody>`, via a compiled-in `NodeRegistry` of node factories and a `GraphAssembler`. This is where the "which `node_id` is which concrete node" knowledge lives — once, instead of duplicated across the CLI and every test. DirectShow-like in spirit, **not** a plugin system: no dynamic loading, no ABI, the node set is compiled in. The assembler validates everything (unknown kind, category-vs-edges, per-kind config, required wiring, acyclic single-component source→sink shape) **before** building any node. Depends on `ovp-core`, `ovp-domain`, `ovp-llm` — **not** `ovp-stores` (assembly never applies plans). `ovp-core` knows none of its `NodeKind`s. See `docs/stage-graph-assembly.md`.

- **`ovp-run`** — the operational workflow layer (L4). One public concept, `RunCycle`, drives a full cycle: assemble (L2) → run (L0) → apply the plan via `CompositePlanApplier` (L3) → read the canonical store strictly → rebuild the MOC and knowledge index → one `RunCycleReport`. Idempotent on re-run; fail-closed (assembly/run failure → no derived rebuild; a not-clean main apply — any failed OR unsupported op — or a corrupt canonical / failed backlink scan → MOC/index left untouched, loud `derived_skipped_reason`). All derived reads happen before any derived write. Owns no domain logic — it wires L1–L3 together. Depends on `ovp-app`, `ovp-stores`, `ovp-domain`, `ovp-core` (not `ovp-llm` — the client is injected via `AppWiring`). See `docs/stage-operational-workflow.md`.

- **`ovp-query`** — the read layer (L5). A read-only `KnowledgeView` loads the canonical store (authority, strict parse) + the derived knowledge index (backlinks, if present) and answers `concepts` / `get` / `search` / `backlinks` / `stats`. Fail-loud on a corrupt store; never mutates, assembles, or runs. `ovp-lint` builds health checks on the same `KnowledgeView`. Depends on `ovp-domain`, `ovp-stores`, `ovp-core` — not `ovp-app`/`ovp-run`. See `docs/stage-read-health.md`.

- **`ovp-lint`** — the health layer (L5). `Lint::check` runs read-only WIGS-style checks over the loaded `KnowledgeView` + vault: missing evergreen notes (`error`), stale/absent knowledge index and MOC (`warning`), broken `[[wikilinks]]` (`warning`), orphan canonical concepts (`info`), and a load failure surfaced as a finding (not an abort). Returns a `LintReport` with a severity gate (`passed(threshold)`); it **reports, never fixes** — a fix is a write, and writes go through L3/L4. A load failure becomes a finding rather than aborting. Depends on `ovp-query` (for `KnowledgeView`) + `ovp-domain`, `ovp-stores`, `ovp-core`. See `docs/stage-read-health.md`.

- **`ovp-rag`** — the RAG read path (L6). A read-only retrieval surface over the L5 `KnowledgeView`: `RagCorpus` (concepts + backlinks + evergreen note bodies, read off `vault_root()` the same way lint stats evergreen files) → `Retriever` (deterministic, integer, explainable lexical scoring — title/slug token-or-substring, capped body hits, backlink substring; each contribution recorded as a `MatchReason`) → `Ranker` (drop-zero, `(score desc, slug asc)`, top-k) → `ContextBuilder` (a bounded `RagContext`: capped concepts, snippet chars, backlinks). `Eval` is the offline recall@k gate over fixtures with known expected slugs. Fail-loud corpus build (`RagError::Load` on a corrupt read model, `RagError::Body` on an *unreadable* — not merely absent — note). Never assembles, runs, applies, or writes (invariants #10/#11 untouched). Depends on `ovp-query` only. See `docs/stage-rag-automation.md`.

- **`ovp-auto`** — the automation path (L6). `AutoRun::sweep` is a one-shot directory sweep: discover markdown under an inbox root (via `ovp-stores::walk_markdown`, fail-loud), run the L4 `RunCycle` on each input, then the L5 `Lint` gate once, and emit one `AutoReport` (`considered` / `cycles` / `skipped` / `lint` / `lint_passed`; `succeeded()` = all cycles clean AND lint passed). It **calls** L4/L5 — it reimplements no assemble/run/apply/rebuild logic, and it builds no wiring itself: the caller passes a per-input factory that yields the fully-wired `RunCycleInputs` (so `ovp-auto` depends only on `ovp-run`, `ovp-lint`, `ovp-stores`). Sync; no async, no watcher daemon in v1. See `docs/stage-rag-automation.md`.

- **`ovp-cli`** — thin app layer: parses args, builds the `ModelClient` + `ConceptRegistry` + `AppWiring` + paths, and delegates assembly to `ovp-app::GraphAssembler` and the full cycle to `ovp-run::RunCycle` (no hand-wired `register_*` on the main path — CI-gated). No business logic. Subcommands: `run-cycle` (the L4 operational command), `query` + `lint` (the L5 read/health commands), `rag` (the L6 retrieval command) + `auto-run` (the L6 automation sweep — it owns the per-input wiring factory so `ovp-auto` need not), `interpret-article` (the v1 pipeline, assembled), `apply-plan` (`WritePlan` → vault), `graph` (manifest inspection). `run --fake` remains from v0.1 for fake-source smoke tests.

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

**Landed:** C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), L3 (`ConceptRegistry`); `EvergreenConceptWriter` + `EvergreenSink` (mint new evergreens → evergreen `VaultCreate` + `CanonicalUpsert`); the **canonical store** (`CanonicalFsStoreApplier` + typed `CanonicalConcept`, composed by `CompositePlanApplier` so a full plan applies with zero unsupported ops); the **derived rebuilds** (`MocBuilder` + `KnowledgeIndexBuilder`, rebuildable from canonical + vault, invariant #11); **canonical-identity hardening** (`CanonicalSlug` + strict rebuild parse — see `invariants.md` "Canonical-store integrity"); the **Graph Assembly Layer** (L2, `ovp-app`: a declarative manifest + `AppWiring` assemble into a `GraphRunner`, with up-front validation of graph shape, per-kind config, and required runtime wiring — see `docs/stage-graph-assembly.md`); and the **Operational Workflow Layer** (L4, `ovp-run` + the `run-cycle` command: one idempotent assemble→run→apply→rebuild cycle, fail-closed — see `docs/stage-operational-workflow.md`). `TxnFsApplier` was assessed and deferred — every op is idempotent, so multi-file atomicity isn't required (re-apply recovers a partial run).

The full legacy cycle (raw → note → evergreen → canonical → MOC + knowledge index) is closed, assembled declaratively, and runs end-to-end from one `run-cycle` command (idempotent on re-run).

L5 read/health is complete: `ovp-query` (a read-only `KnowledgeView` + `query` CLI) and `ovp-lint` (`Lint::check` + `lint` CLI with a severity gate).

L6 RAG/automation is landed: `ovp-rag` (a read-only `RagCorpus` → `Retriever` → `Ranker` → `ContextBuilder` + offline `Eval`, exposed as `rag`) and `ovp-auto` (`AutoRun::sweep` — a one-shot inbox sweep that *calls* L4 `RunCycle` + L5 `Lint`, exposed as `auto-run`). RAG stays read-only; automation owns no workflow logic. See `docs/stage-rag-automation.md`. **Genuine future work (explicit non-goals of v1):** an embedding/semantic ranker (a future `RetrievalWeights`-shaped extension — v1 is deterministic lexical scoring); a `--watch` polling daemon wrapping `sweep` (v1 is one-shot, sync, no async runtime); frontmatter-stripped RAG snippets. Re-triaged from `docs/legacy-alignment.md` against observed pain.

**Recommended next stage: M12 — Absorb Boundary v1, before RAG v1.1 (the semantic ranker).** The M11 necessity audit (`docs/processing-pipeline-audit.md`) found that `EvergreenConceptWriter` + `EvergreenSink` are canonical-identity *minting*, not absorb: every surviving candidate is AUTO-minted as a provenance-free **stub** ("Expand with an atomic definition and links"), with no enrichment, no policy lanes (mint/enrich/escalate/reject), and no cross-document dedup. Because RAG retrieves over evergreen note *bodies*, a semantic ranker built first would rank over empty stubs — retrieval quality is bottlenecked on note content, not the ranking algorithm. So the absorb boundary (real note bodies + decision lanes + dedup) should precede the embedding/semantic ranker above.

## What this doc is and isn't

- **Is:** the current authoritative description of the system. Update this doc when the code changes.
- **Isn't:** a list of historical stages (see `calibration-r1.md`, `calibration-r2.md`, `stage-c.md`, `stage-d-plan-applier.md`) or a wishlist of future features (those go in stage docs as they're scoped).

If this doc disagrees with the code, the code wins and this doc needs a fix.
