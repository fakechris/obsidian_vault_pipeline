# Processing Pipeline Necessity Audit (M11)

> **What this is:** a read-only audit of every node in the current ovp-next
> processing pipeline, answering one question per node — *is it necessary,
> and at what layer does it belong?* No code changes; the only edits this stage
> produces are this doc plus a narrow clarification in `architecture.md`.
>
> **What this is not:** it is **not** RAG work and **not** an absorb
> implementation. It exists to decide what the *next* implementation stage
> should be, and to draw the absorb boundary precisely before anyone builds on
> top of it.

> **Update (M12a + M12b — landed):** the headline finding below — that
> `EvergreenConceptWriter` + `EvergreenSink` mint a **stub only** — has been
> *partially* addressed. **M12a** "rich evergreen minting" now renders a grounded
> note body (one-line definition + up to five source-backed claims + source link
> + related wikilinks, selected deterministically from the interpreted article),
> so RAG retrieves over real content, not the "Expand with an atomic
> definition…" placeholder. **M12b** then makes a slug surfaced by a second
> article *enrich* its note (a merge `VaultUpdate`) instead of failing the run.
> The grounding lives in the vault note body only; the canonical store / MOC /
> knowledge index are unchanged. **Still open** (the rest of the absorb boundary,
> before RAG v1.1): concept-specific definitions, mint/enrich/escalate/reject
> policy lanes, semantic dedup of near-duplicate claims, and crystal
> materialization. See `docs/stage-m12a-rich-evergreen-minting.md` +
> `docs/stage-m12b-same-slug-reconcile.md`.

## Scope

Audited: the three real manifests (`article`, `article_evergreen`, `unified`),
every node they reference, and the two derived rebuilds the `run-cycle` performs
after apply. The `fake.pipeline.toml` smoke harness is out of scope (it carries
no domain nodes). Each verdict below is grounded in `file:line` evidence read
this stage; where the plan's pre-written guess diverged from the code, the row
is marked **corrected** or **refined**.

### Working vocabulary

| Label | Meaning |
|---|---|
| **Required** | The pipeline cannot produce its delivery artifact without this node. |
| **Conditional** | Load-bearing only for some inputs / some manifests; a structural pass-through otherwise. |
| **Policy** | A decision/promotion boundary whose behavior is data-driven (registry contents), not a fixed transform. |
| **Scaffold** | Present and wired, but a deliberately minimal v1 stand-in for a larger capability not yet built. |
| **Merge candidate** | Two nodes that could collapse into one without losing a boundary. |
| **Derived** | Not a pipeline node at all — a post-apply rebuild computed from persisted state. |

## Current chains

```
article.pipeline.toml
  markdown_inbox → source_resolver → prompt_builder → llm_invoker
    → article_parser → concept_resolver → article_vault_plan

article_evergreen.pipeline.toml
  markdown_inbox → source_resolver → prompt_builder → llm_invoker
    → article_parser → concept_resolver → evergreen_concept_writer
        ├─ (Interpreted)      → article_vault_plan
        └─ (EvergreenConcept) → evergreen_sink

unified.pipeline.toml
  markdown_inbox → source_resolver → route_by_source_kind ─┬─ article_prompt_builder ┐
                                                           └─ paper_prompt_builder   │
                              (broadcast; each builder drops the wrong kind)         │
    → llm_invoker ─┬─ article_parser → concept_resolver → article_vault_plan
                   └─ paper_parser   → paper_vault_plan
```

A subtlety the manifests rely on: `route_by_source_kind` and `llm_invoker`
**broadcast** to all downstream edges; correctness comes from each kind-specific
node *dropping* the wrong kind/prompt-id rather than from explicit branch
selection. `prompt_builder` drops `Paper`; `paper_prompt_builder` drops
`Article`; `article_parser` drops non-`article_interpret/v1`; `paper_parser`
drops non-`paper_interpret/v1`. This is type-safe routing expressed at the
manifest layer, with no extra branch-selector node.

## Layer boundary

The pipeline is the **record graph only** — `Source → Transform / EffectfulTransform → Sink`,
ending at a `WritePlan`. The MOC and knowledge index are **not** pipeline nodes;
they are derived artifacts the L4 `RunCycle` rebuilds *after* the plan is applied,
read strictly from the persisted canonical store + a vault `[[wikilink]]` scan
(invariant #11). This is enforced structurally:

- `MocBuilder` / `KnowledgeIndexBuilder` live in `ovp-run`'s post-apply step
  (`run_cycle_e2e.rs:116-159` — `moc.applied == 1`, `knowledge_index.applied == 1`
  on the first run, both idempotent on the second), not in any manifest.
- They are **fail-closed**: a successful main apply but corrupt canonical store
  leaves existing MOC/index untouched and reports a loud `derived_skipped_reason`
  (`run_cycle_e2e.rs:263-313`).

Treating them as derived (not processors) is the single most important boundary
this audit defends: it keeps the graph's job ("interpret one source → write one
note + mint identity") separate from "recompute the whole atlas from authority."

## Processor decision table

| Node | Layer | In → Out | Role | Decision | Evidence | Used by |
|---|---|---|---|---|---|---|
| **MarkdownInboxSource** | Source | file → `Source` | Reads YAML frontmatter + body; classifies `SourceKind` (`source_type: arxiv-paper` → `Paper`, else `Article`) | **Required** | `sources/markdown_inbox.rs:69-89` (emit), `:181-206` (frontmatter) | all |
| **SourceResolver** | Transform | `Source` → `Source` | Twitter/X clip URL → underlying article URL; emits `SourceResolution` only on an actual rewrite | **Conditional** — structural no-op on clean URLs (forwards a rebuilt-but-unchanged record, no event) | `transforms/source_resolver.rs:48-56` (non-Twitter forward unchanged), `:78` (event only on rewrite) | all |
| **RouteBySourceKind** | Transform | `Source` → `Source` | Reads `SourceKind`, emits `source_routed`, forwards unchanged; the observable routing decision point | **Conditional** — `unified` only; article-only manifests omit it | `transforms/route_by_source_kind.rs:31-51`; absent from `article*.pipeline.toml` | unified |
| **PromptBuilder** (article) | Transform | `Source` → `Prompt` | Fills `article_interpret/v1` template; drops `Paper` (`wrong_kind`) | **Required** (article branch) | `transforms/prompt_builder.rs:83` (kind gate), `:90-98` | all article |
| **PaperPromptBuilder** | Transform | `Source` → `Prompt` | Fills `paper_interpret/v1` template (max_tokens 8192 vs 4096); drops `Article` | **Required** (paper branch) | `transforms/paper_prompt_builder.rs:72` (exhaustive `Paper(_)`), `:17` (tokens) | unified |
| **LLMInvoker** | EffectfulTransform | `Prompt` → `Model` | The only I/O node. Calls `ModelClient`; `MaxTokens` → drop; any `CallError` → `FilterError` (fail-loud) | **Required effect boundary** — *corrected:* fail-loud lives **here**; **retry does not** (see Suspicious Areas) | `transforms/llm_invoker.rs:60-63` (error→`FilterDecision::Error`), `:78-82` (max_tokens), `:96-108` (translation) | all |
| **ArticleParser** | Transform | `Model` → `Interpreted` | Parses LLM JSON into the 6 article dimensions; validates `prompt_id` + `schema_version`; drops empty one-liner/details/actions | **Required** (article) | `transforms/article_parser.rs:44-92` | all article |
| **PaperParser** | Transform | `Model` → `InterpretedPaper` | Parses the 10 paper sections; validates `prompt_id` + `schema_version` + `origin` is `Paper`; **no evergreen minting** | **Required** (paper) | `transforms/paper_parser.rs:33-87` (`:64-71` origin check) | unified |
| **ConceptResolver** | Transform | `Interpreted` → `Interpreted` | Promotes `concept_candidates` → `canonical_concepts` via `ConceptRegistry`; pure (same input + same registry → same output) | **Conditional / Policy** — a registry-driven near no-op when the registry is empty | `transforms/concept_resolver.rs:6-12` (purity), `:140-154` (empty no-op test) | all |
| **EvergreenConceptWriter** | Transform | `Interpreted` → FanOut(`Interpreted` + `EvergreenConcept`…) | Mints *new* evergreens from the **unpromoted** candidates; v1 policy is AUTO-all (mint every survivor, no gate) | **Scaffold (M12 boundary)** — the "legacy absorb equivalent for the mint-new half"; **not full absorb** | `transforms/evergreen_concept_writer.rs:8-19` (docstring: "legacy 'absorb' equivalent", "AUTO-all") | article_evergreen |
| **ArticleVaultPlanSink** | Sink | `Interpreted` → `VaultCreate` | Renders the full 6-dimension article note | **Required** (core delivery artifact) | `sinks/article_vault_plan.rs`; test `:221-244` | all article |
| **PaperVaultPlanSink** | Sink | `InterpretedPaper` → `VaultCreate` | Renders the 10-section paper note | **Required** (paper delivery) | `sinks/paper_vault_plan.rs`; test `:172-195` | unified |
| **EvergreenSink** | Sink | `EvergreenConcept` → `VaultCreate`(stub) + `CanonicalUpsert` | Writes a minimal evergreen stub + registers canonical identity; stub is provenance-free (idempotent), provenance rides in the `CanonicalUpsert` payload | **Required** (for what it does) — but what it does is *minting*, not absorb | `sinks/evergreen_sink.rs:66-92` (emits both ops, no `VaultUpdate`), `:96-108` (stub body), test `:178-202` | article_evergreen |

### Derived (out of the graph)

| Builder | In → Out | Decision | Evidence |
|---|---|---|---|
| **MocBuilder** | canonical store (strict) → `VaultCreate` MOC-Index | **Derived** (post-apply rebuild, not a processor) | `run_cycle_e2e.rs:116-159`, `:263-313` (fail-closed) |
| **KnowledgeIndexBuilder** | canonical + vault backlinks → knowledge-index | **Derived** (post-apply rebuild, not a processor) | `run_cycle_e2e.rs:116-159` (idempotent) |

## Suspicious areas

1. **`LLMInvoker` retry attribution (corrected).** The M11 plan said "M10 added
   retry/fail-loud" to `LLMInvoker`. Only **fail-loud** lives in the node: it
   translates every `CallError` into a `FilterDecision::Error` (`llm_invoker.rs:60-63,96-108`),
   which the runner counts as `records_errored` and the `RunCycle` turns into a
   non-zero exit. **Retry is not in the transform.** It is `RetryingModelClient`
   (`ovp-llm/src/client.rs`, `is_transient` classifier) wrapping the *live*
   client at construction time (the M10 `build_live_client` wiring), inside the
   cache so a cache hit never retries. The boundary is right where it should be
   (the node stays pure-ish and synchronous; transient-fault policy lives in the
   client), but the audit table must say so precisely.

2. **`SourceResolver` is a no-op on the common path.** On a clean (non-Twitter)
   URL it rebuilds the record with every field copied and forwards it unchanged,
   emitting no event (`source_resolver.rs:48-56`). It is structurally a no-op
   (output ≡ input) but not a *performance* no-op. It earns its place only for
   Twitter/X redirect extraction — hence **Conditional**, not Required. Do not
   delete it (the redirect case is real), but do not treat it as load-bearing on
   the article happy path.

3. **`ConceptResolver` adds nothing with an empty registry.** Its value is
   entirely a function of `ConceptRegistry` contents. With an empty registry it
   is a verified pass-through (`concept_resolver.rs:140-154`). This is correct as
   a **policy boundary** (promotion is data-driven), but it means "does the
   pipeline promote concepts?" is really "is the registry populated?" — a
   provisioning question, not a code question.

4. **`RouteBySourceKind` + the prompt builders are a broadcast-and-drop trio.**
   The router forwards to *both* prompt builders and relies on each to drop the
   wrong kind. This is intentional and tested, but it means "routing" is spread
   across three nodes, none of which alone selects a branch. It is **not** a
   merge candidate (the router's `source_routed` event is the audit point), but a
   reader should know the selection is emergent, not centralized.

5. **No merge candidates found.** Every node draws a distinct boundary
   (I/O, parse-shape, identity-promotion, render). The closest pair —
   `EvergreenConceptWriter` (decide what to mint) and `EvergreenSink` (write the
   stub + upsert) — should *stay* split, because M12 will grow the writer into a
   real decision lane (mint / enrich / escalate / reject) while the sink stays a
   dumb op-emitter.

## Absorb / crystal boundary

This is the load-bearing finding of the audit.

**`EvergreenConceptWriter` + `EvergreenSink` are canonical-identity *minting*,
not absorb.** Concretely, today:

- The evergreen `VaultCreate` body is a **stub only** — frontmatter
  (`title`, `type: evergreen`, `slug`, `status: stub`) plus a single placeholder
  line: *"Stub evergreen. Expand with an atomic definition and links."*
  (`evergreen_sink.rs:96-108`).
- The stub is **deterministic from slug/title alone and provenance-free**, so it
  is idempotent across documents that surface the same concept
  (`evergreen_sink.rs:97`, test `:178-202`). The actual source URL rides only in
  the `CanonicalUpsert` payload (`CanonicalConcept.provenance_source_url`), where
  the canonical store is expected to merge it later.
- The mint policy is **AUTO-all**: every candidate `ConceptResolver` did *not*
  promote becomes a new evergreen, with **no human gate, no escalate lane, no
  reject lane** (`evergreen_concept_writer.rs:16-19`).
- **No node anywhere updates, enriches, synthesizes, or merges an existing
  evergreen note.** `EvergreenSink` emits only `VaultCreate` + `CanonicalUpsert`
  — never `VaultUpdate`. The only `VaultUpdate` emitters in the system are the
  derived MOC/index builders, and they touch derived artifacts, not evergreen
  prose. Cross-document dedup of the same slug is *promised to the canonical
  store* in the docstring but is **not implemented** in `ovp-domain`.

**Absorb parity status:** the legacy Python `absorb` did note enrichment,
candidate promotion *with policy lanes*, and cross-document synthesis/dedup.
ovp-next implements only the **mint-new-stub** sliver of that. `architecture.md`
already records this honestly in its deprecated-vocabulary table ("`Absorb` …
our v1.1 implementation is more limited. Adopt the legacy name only if/when we
match its semantics"). This audit confirms that row is still accurate.

**Crystal materialization is absent.** The legacy system had
`ovp-build-crystals` materializing persistent briefings (`40-Resources/Crystals/`).
ovp-next has no equivalent. That is fine for now, but it belongs in the same
"absorb/refine/crystal gap" ledger as the enrichment gap above — it is not a
forgotten processor, it is unbuilt scope.

## Recommended next stage

**M12 — Absorb Boundary v1, sequenced *before* RAG v1.1 (the semantic ranker).**

Why this ordering, not the reverse:

> RAG retrieves over the corpus of evergreen note bodies + canonical concepts
> (`ovp-rag::RagCorpus`). Today those bodies are stubs whose entire content is
> *"Expand with an atomic definition and links."* A semantic/embedding ranker
> (RAG v1.1) retrieving over empty stubs has nothing meaningful to embed or rank
> — retrieval quality is bottlenecked on note **content**, not on the ranking
> algorithm. Building the ranker first optimizes search over empty pages. Absorb
> (filling the stubs, adding mint/enrich/reject policy, implementing cross-doc
> dedup) must come first so RAG v1.1 has real text to rank.

**M12 scope sketch (non-binding — this is an audit, not a design):**

- Grow `EvergreenConceptWriter` from AUTO-all stub-minting into a real absorb
  decision with explicit lanes (mint-new / enrich-existing / escalate / reject).
- Give `EvergreenSink` (or a new enrich node) the ability to emit `VaultUpdate`
  so an existing evergreen body can gain an atomic definition + links — closing
  the "stub forever" gap, while keeping the provenance-free idempotent stub as
  the floor.
- Implement the cross-document slug dedup currently only *promised* to the
  canonical store.
- Keep crystal materialization out of M12 v1 (likely M13+); just track it.

**Non-actions for this stage (and for M12 v1):** no RAG semantic ranker, no
embeddings, no LLM judge, no `--watch` daemon, no comparator expansion. Those
wait behind the absorb boundary.

## Forbidden-path audit

This stage commits **docs only**. The commit stages exactly two paths:

- `docs/processing-pipeline-audit.md` (this file, new)
- `docs/architecture.md` (narrow clarification: L4 derived rebuilds are
  post-apply not pipeline nodes; "what comes next" recommends M12 absorb before
  RAG v1.1)

Explicitly **not** staged (verified against `git status` before commit):
`docs/eval/`, `.run/`, `.env` / `.env.live` / any `.env*`,
`crates/ovp-eval/`, `crates/ovp-cli/src/commands/compare_run.rs`. No source,
manifest, or test file is touched — the offline gauntlet result is unchanged by
this stage by construction, and is re-run as a guard anyway.
