# Stage: Graph Assembly Layer

> Status: design + first implementation. Lands the `ovp-app` crate so pipelines
> are **assembled** from a declarative manifest (node id + kind + config + edges)
> plus app-supplied wiring, instead of hand-coded `register_*` calls duplicated
> across the CLI and every test.

## The problem

`GraphRunner<DomainBody>` works, but every caller builds it by hand:

```rust
let mut runner = GraphRunner::new(manifest, run_id.clone());
runner.register_source("markdown_inbox", MarkdownInboxSource::new("markdown_inbox", run_id.clone(), &input));
runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
runner.register_effectful_transform("llm_invoker", LLMInvoker::new("llm_invoker", client));
runner.register_transform("article_parser", ArticleParser::new("article_parser", &area, &date));
runner.register_transform("concept_resolver", ConceptResolver::new("concept_resolver", registry));
runner.register_sink("article_vault_plan", ArticleVaultPlanSink::new("article_vault_plan", run_id.clone()));
```

That exact block (modulo args) is copied in `ovp-cli/commands/interpret_article.rs`
and in **six** integration tests (`article_clean`, `article_mixed_lang`,
`paper_arxiv`, `e2e_interpret_then_apply`, `evergreen_e2e`, `knowledge_index_e2e`).
Current manifests describe *topology only* (`nodes` + `edges`); they do **not**
say which concrete node a `node_id` maps to, so that mapping lives implicitly in
the hand-coded `register_*` order. Adding the next business stage (query, lint,
process-inbox) multiplies this duplication. **This is the main architectural risk
right now:** without an assembly layer, `ovp-cli` and the test suite become a
wiring god-object before we add any more features.

Goal: make assembly **DirectShow-like in spirit** — a manifest names filters and
their static properties + the graph edges; the app supplies the live media
sources and device handles (the effect objects + per-run values). It is **not**
a plugin system: no dynamic loading, no ABI, the node set is compiled in.

## Proposed primitives (and name challenges)

Eight public nouns. Four are the user's proposal; four are the minimum support
they imply. Each is justified — nothing speculative.

| Noun | What it is | Notes / challenge |
|---|---|---|
| `NodeKind` | Stable identifier for a concrete node factory, e.g. `source.markdown_inbox`, `transform.source_resolver`, `effect.llm_invoker`, `sink.article_vault_plan`. A thin newtype over a string with a `<category>.<name>` convention. | Keep. Newtype (not bare `String`) so it is greppable and owns the category-prefix rule. |
| `NodeCategory` | `Source \| Transform \| Effect \| Sink`. | Needed for the category-vs-edges check (Q4). Tiny; folds the four `register_*` methods into one dimension. |
| `NodeRegistry` | In-process map `NodeKind → (NodeCategory, factory)`. Populated once with the compiled-in domain node set (`NodeRegistry::with_domain_nodes()`). | **Challenge:** "registry" is an overloaded word this project has fought (registry sprawl; `ConceptRegistry` is an *identity authority*). `NodeRegistry` is **assembly-only** — a factory catalog, holds no business state, no authority, never consulted at runtime. We keep the user's name but hard-document the boundary (see architecture.md "Deprecated vocabulary"). `NodeCatalog` was considered and rejected to keep the proposed vocabulary stable. |
| `GraphAssembler` | Builds a `GraphRunner<DomainBody>` from a `DomainPipelineSpec` + `AppWiring`. | Keep. The one place that knows how to turn declarative spec → live runner. |
| `AppWiring` | The runtime bag the app fills: `run_id`, `date_stamp`, `area`, `input_path`, named `ModelClient`s, named `ConceptRegistry`s. Things that *cannot* live in a static file. | Keep. |
| `DomainPipelineSpec` | App-layer enriched manifest: topology (`ovp_core::PipelineManifest`) + a per-node `kind`+`config` overlay. Lowers to / reuses `PipelineManifest` for topology. | Q2 answer. |
| `NodeConfig` | Per-node **static** config: `client`, `registry` — named `Option` fields each naming the `AppWiring` entry to bind, `deny_unknown_fields`. | Q3 answer. Not a `serde_json::Value`, not a `HashMap<String,_>` — invariant #2/#3 hold. |
| `AssemblyError` | Typed assembly failures (see Validation). | Keep. |

Internal-only (not public vocabulary): `NodeFactory` (the closure type),
`NodeBuildArgs` (what a factory receives).

## Crate placement (Q1)

**New crate `ovp-app`.** Rationale: `ovp-cli` must stay thin and CLI-specific
(arg parsing → call into `ovp-app`); and integration tests need to assemble
pipelines **without** going through `clap`. `ovp-app` depends on `ovp-core`,
`ovp-domain`, `ovp-llm` (for the `ModelClient` type in `AppWiring`) + `toml`/`serde`.
It does **not** depend on `ovp-stores` (assembly builds the runner; applying a
plan is a separate step) — `ovp-stores` is only a `dev-dependency` for the
end-to-end "assemble → run → apply" tests.

Dependency direction stays acyclic: `ovp-cli → ovp-app → {ovp-domain, ovp-llm, ovp-core}`.
`ovp-core` stays domain-blind and **does not know any concrete `NodeKind`** — the
node catalog lives entirely in `ovp-app`.

## Manifest shape (Q2) — backward-compatible overlay

`ovp-core::PipelineManifest` stays **topology only** and is unchanged in spirit.
The enriched manifest is the **same file** with an added `[assembly.<node_id>]`
section that the topology parser ignores (serde ignores unknown top-level keys):

```toml
[pipeline]                              # parsed by ovp-core::PipelineManifest (unchanged)
nodes = ["markdown_inbox", "source_resolver", "prompt_builder", "llm_invoker",
         "article_parser", "concept_resolver", "article_vault_plan"]
edges = [["markdown_inbox", "source_resolver"], ... ]

[assembly.markdown_inbox]               # parsed by ovp-app::DomainPipelineSpec
kind = "source.markdown_inbox"

[assembly.llm_invoker]
kind = "effect.llm_invoker"
config = { client = "default_llm" }     # binds to AppWiring's named client

[assembly.concept_resolver]
kind = "transform.concept_resolver"
config = { registry = "default" }        # binds to AppWiring's named registry
```

Why an overlay rather than a new node-table format:
- **Zero breakage.** The six existing tests + the `graph` subcommand keep calling
  `PipelineManifest::parse` and read `[pipeline]` exactly as before.
- **Two parsers, two slices, one file.** `PipelineManifest` reads `[pipeline]`
  (topology); `DomainPipelineSpec::parse` reads `[pipeline]` **and** `[assembly]`,
  and **validates they agree** (every topology node has an assembly entry and vice
  versa — catches drift). Topology stays the single source of node ids + edges.
- `DomainPipelineSpec` *wraps* `PipelineManifest` and hands it straight to
  `GraphRunner::new` for topology — it never re-derives edges.

`DomainPipelineSpec` reuses the `PipelineManifest` it already parsed from
`[pipeline]` for topology and hands it straight to `GraphRunner::new` — no new
`ovp-core` API, no re-serialization, and no second validation pass.

## Node config typing (Q3)

`NodeConfig` is a typed struct of named optional fields with `deny_unknown_fields`
(a typo in a manifest fails loudly):

```rust
pub struct NodeConfig {
    pub client: Option<String>,    // effect.llm_invoker → which AppWiring client to bind
    pub registry: Option<String>,  // transform.concept_resolver → which AppWiring registry
}
```

Every field is the **name** of an `AppWiring` entry to bind — never a value.
Per invariant #8, all runtime/wiring values (`run_id`, `date_stamp`, `area`,
`input_path`, the actual `ModelClient`/`ConceptRegistry`, and even a model-name
override) live in `AppWiring`, not in the static manifest. Config says only
*which* wiring a node binds to. This is the firewall against
`serde_json::Value`/`HashMap<String,_>` rot — and it keeps the "model name is
wiring, not topology" boundary (invariant #8's footnote) intact.

## Dependency binding for effectful nodes (Q5)

By **name**, not by value, in the manifest:

```toml
[assembly.llm_invoker]
kind = "effect.llm_invoker"
config = { client = "default_llm" }
```

`AppWiring` owns `clients: HashMap<String, Box<dyn ModelClient>>`. At assembly the
`effect.llm_invoker` factory **takes** (moves) the client named `default_llm` out
of wiring and into `LLMInvoker::new(...)`. A `ModelClient` is not `Clone`, so it is
bound exactly once; binding the same client to two nodes errors. `ConceptRegistry`
*is* `Clone`, so registries are cloned per use and may be shared.

## Validation (Q4)

`GraphAssembler::assemble` fails with a typed `AssemblyError` on:

| Check | Error |
|---|---|
| node `kind` not in the registry | `UnknownKind { node_id, kind }` |
| duplicate node id | `Manifest(CoreError)` (caught by core's `PipelineManifest::validate`) |
| edge references a missing node | `Manifest(CoreError)` (reused from `PipelineManifest::validate`) |
| `[pipeline]`/`[assembly]` node sets disagree | `SpecMismatch { detail }` |
| required config field absent (e.g. `effect.llm_invoker` with no `client`) | `MissingConfig { node_id, field }` |
| named wiring absent (e.g. `client = "x"` but no such client; or a source with no `input_path`) | `MissingWiring { node_id, name }` |
| category vs topology mismatch: a `source.*` with an inbound edge, or a `sink.*` with an outbound edge | `CategoryMismatch { node_id, kind, detail }` |

We deliberately **do not** statically type-check `DomainBody` variant
compatibility across edges in v1 (e.g. that a `Prompt`-emitting node only feeds a
`Prompt`-consuming node). The runner + per-node `FilterDecision` already drop
mismatched records observably; full static graph typing is a later stage if it
earns its keep.

## Migration (Q6)

- `manifests/article.pipeline.toml`, `article_evergreen.pipeline.toml`,
  `unified.pipeline.toml` gain an `[assembly]` section. `fake.pipeline.toml` stays
  topology-only (it is the v0.1 fake-runner relic).
- `ovp-cli interpret-article` drops its hand-wiring and calls
  `GraphAssembler::with_domain_nodes().assemble(spec, wiring)`.
- The `graph` subcommand is unchanged — it still reads `[pipeline]` for topology.

## Non-goals (unchanged from the brief)

No dynamic loading. No external plugin ABI. No async executor. No generic JSON
graph DSL in `ovp-core`. No new business stages (query/RAG/process-inbox) until
assembly is real. `ovp-core` stays domain-blind and learns no concrete node kinds.

## Acceptance tests (Q7) — all in `ovp-app`

1. `unknown kind → AssemblyError::UnknownKind` with a clear message.
2. `missing wiring → AssemblyError::MissingWiring` (manifest binds `client="default_llm"`, wiring has none).
3. **assembled `article.pipeline.toml` == manual behavior**: assemble + run against
   the `article_clean` cassette; assert one `VaultCreate` under
   `20-Areas/AI-Research/Topics/2026-05/`, `records_dropped == 0`, frontmatter
   round-trips — the same outcomes the hand-wired `article_clean` test asserts.
4. **assembled `article_evergreen.pipeline.toml` applies through `CompositePlanApplier`**:
   article note + N evergreen `VaultCreate`s + N `CanonicalUpsert`s, applied with
   zero failures/unsupported.
5. **assembled `unified.pipeline.toml` routes**: a paper fixture yields one paper
   note under `.../Papers/` and emits `source_routed{source_kind="paper"}`.
6. **CLI no longer hand-registers**: `interpret_article.rs` contains no
   `register_*` calls for the main path (it goes through `GraphAssembler`).

The existing six hand-wired tests stay green (they exercise the domain nodes
directly and are unaffected by the overlay).

## What must be true before this works (pre-implementation risks)

- **Borrow shape.** A factory needs `&mut GraphRunner` *and* `&mut AppWiring`
  (to move a client out). These are disjoint locals, passed as separate fields of
  a `NodeBuildArgs<'a>`; the factory reads config, takes the client (ends the
  wiring borrow), then registers (starts the runner borrow) — sequential, no
  overlap. Verified by the compiler.
- **Factory shape.** `NodeFactory = Box<dyn Fn(&mut NodeBuildArgs) -> Result<(), AssemblyError>>`.
  Each factory knows its concrete type and calls the matching `register_*` method,
  so the four typed registration methods are reached without `Box<dyn Source>`
  gymnastics.
- **Per-prompt cassette namespace.** In `unified`, two prompt builders fan into one
  `llm_invoker`. `LLMInvoker` tags each request with the prompt's own
  `cache_namespace`, so one shared client resolves article vs paper cassettes
  correctly — the factory must not override that. (Confirmed against the existing
  paper test.)
- **Overlay parse.** `[assembly]` must be invisible to `PipelineManifest` (it is —
  no `deny_unknown_fields` on `PipelineManifest`), and `DomainPipelineSpec` must
  reject a node present in one section but not the other.
