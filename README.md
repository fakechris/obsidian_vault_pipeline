# OVP Next

Clean-core Rust rewrite of the Obsidian Vault Pipeline. **Status: full legacy cycle closed — raw → note → evergreen → canonical → MOC + knowledge index, all derived state rebuildable; routed unified pipeline; live LLM behind a feature flag; L6 RAG read path + one-shot automation sweep landed.**

This repo intentionally has zero dependency on the legacy Python `ovp_pipeline` package — no import, no subprocess, no embedded runtime. The old system is a frozen oracle for fixtures and contracts, not a runtime dependency.

## What works today

Eleven crates, 299 tests. Three acceptance fixtures (`article_clean`, `article_mixed_lang`, `paper_arxiv`) run through the pipeline offline against committed cassettes; the resulting `WritePlan` is applied to a tempdir vault and the round-trip fields match. Pipelines are **assembled** from a declarative manifest (node id + kind + config + edges) plus app `AppWiring` — the CLI and tests no longer hand-wire `register_*`. A single **`run-cycle`** command drives the whole thing — inbox file → vault note + evergreen + canonical + MOC + knowledge index — with a run report and idempotent re-runs. Read-only **`query`** and **`lint`** commands read the result back (list / get / search / backlinks / stats) and health-check it (missing notes, stale index/MOC, broken wikilinks, orphan concepts). A read-only **`rag`** command retrieves over that read model — deterministic, explainable lexical scoring → ranking → a bounded context — and an **`auto-run`** sweep discovers an inbox, runs the `run-cycle` per file, then lints the result, all offline. A unified pipeline routes a mixed inbox (articles + papers) to the right interpreter by source kind. Concept promotion is driven by a loadable `ConceptRegistry`, not hardcoded constants. New evergreen concepts mint through a single hardened `CanonicalSlug` rule, land in a canonical store, and rebuild derived MOC + knowledge-index artifacts. The live Anthropic client + cassette capture exist behind the `anthropic` feature (`docs/live-capture.md`); the default build and CI are offline and need no API key.

```
ovp-next interpret-article \
  --input fixtures/article_clean/input.md \
  --out .run/article \
  --cache-dir crates/ovp-domain/tests/cassettes

ovp-next apply-plan \
  --plan .run/article/plans/demo-article.json \
  --vault-root .run/vault
```

→ `.run/vault/20-Areas/AI-Research/Topics/<YYYY-MM>/<YYYY-MM-DD>_<title>_深度解读.md` lands on disk.

## Crates

| Crate | Role |
|---|---|
| `ovp-core` | Sync kernel: `Record<B>`, `Filter` traits, `GraphRunner`, `WritePlan`, `Event`, `PlanApplier` trait. Knows nothing about Obsidian / LLM / HTTP. |
| `ovp-domain` | Domain types + transforms: `DomainBody` (`Source`/`Prompt`/`Model`/`Interpreted`/`InterpretedPaper`), `SourceDoc` (typed `SourceKind`), `PaperDoc`, `VaultLayout`, `ConceptRegistry`, `RouteBySourceKind`, article + paper builders/parsers/sinks, `MarkdownInboxSource` / `InboxScanSource`. |
| `ovp-llm` | `ModelClient` trait + Fixture / Cached / NeverCalls impls (per-request cassette namespacing). `AnthropicBlockingClient` behind `--features anthropic`. |
| `ovp-stores` | `PlanApplier` impls: `VaultFsPlanApplier` (vault files), `CanonicalFsStoreApplier` (canonical records), `CompositePlanApplier` (routes ops by kind, halts on first failure); `walk_markdown` for backlink scans. |
| `ovp-app` | Assembly layer (L2): `GraphAssembler` builds a `GraphRunner` from a `DomainPipelineSpec` (node id + kind + config + edges) + `AppWiring`, via a compiled-in `NodeRegistry`. DirectShow-like, not a plugin system. |
| `ovp-run` | Operational workflow layer (L4): `RunCycle` drives one full cycle — assemble → run → apply → rebuild MOC + knowledge index → report — idempotent on re-run. |
| `ovp-query` | Read layer (L5): a read-only `KnowledgeView` over the canonical store (authority) + knowledge index (backlinks) — list / get / search / backlinks / stats. No mutation. |
| `ovp-lint` | Health layer (L5): read-only WIGS-style checks over canonical + vault + index (missing notes, stale index/MOC, broken wikilinks, orphan canonical). Reports findings with a severity gate; never fixes. |
| `ovp-rag` | RAG read path (L6): read-only `RagCorpus` → `Retriever` (deterministic, explainable scoring) → `Ranker` → `ContextBuilder` (bounded context) + offline `Eval`, all over `ovp-query::KnowledgeView`. Never mutates. |
| `ovp-auto` | Automation (L6): `AutoRun::sweep` — discover an inbox, *call* `ovp-run::RunCycle` per input, then `ovp-lint::Lint`, and report. Duplicates no workflow logic; the caller supplies the per-input wiring. |
| `ovp-cli` | Thin arg-parsing layer: builds `ModelClient` + `ConceptRegistry` + `AppWiring`, delegates assembly to `ovp-app`, the cycle to `ovp-run`, reads to `ovp-query` / `ovp-lint` / `ovp-rag`, the sweep to `ovp-auto`. Subcommands: `run-cycle`, `query`, `lint`, `rag`, `auto-run`, `interpret-article` (`--client replay|live`), `apply-plan`, `graph`. |

## Docs

- `docs/architecture.md` — current authoritative architecture + system primitives + crate responsibilities + deprecated vocabulary.
- `docs/legacy-alignment.md` — living gap matrix between this rewrite and the legacy Python OVP. Read before scoping any new stage.
- `docs/live-capture.md` — how to make live Anthropic calls + capture cassettes (`--features anthropic`, `--client live`).
- `docs/invariants.md` — the 12 invariants; CI-gated where possible.
- `docs/stage-graph-assembly.md` — the Graph Assembly Layer (`ovp-app`): manifest shape, primitives, validation, acceptance.
- `docs/stage-operational-workflow.md` — the L4 `run-cycle` (`ovp-run`): flow, idempotence, fail-closed semantics, dry-run.
- `docs/stage-read-health.md` — the L5 read/health layer (`ovp-query` + `ovp-lint`): `KnowledgeView`, queries, planned lint checks.
- `docs/stage-rag-automation.md` — the L6 RAG read path (`ovp-rag`) + automation sweep (`ovp-auto`): crate boundaries, public nouns, data flow, acceptance tests, non-goals.
- `docs/stage-c.md`, `docs/stage-d-plan-applier.md` — historical stage docs.
- `docs/calibration-r1.md`, `docs/calibration-r2.md` — historical calibration verdicts.
- `fixtures/` — frozen contracts captured from the legacy system.

## Landed

The full legacy cycle is closed: C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), L3 (`ConceptRegistry`), EvergreenConceptWriter (mints new evergreens + `CanonicalUpsert`), the canonical store (`CanonicalFsStoreApplier` + typed `CanonicalConcept`), the derived rebuilds (`MocBuilder` + `KnowledgeIndexBuilder`), the **Graph Assembly Layer** (L2, `ovp-app` — declarative manifest assembly replacing hand-wired `register_*`), the **Operational Workflow Layer** (L4, `ovp-run` + the `run-cycle` command — one idempotent ingest→apply→rebuild cycle), the **Read / Health Layer** (L5, `ovp-query` + `ovp-lint`), and the **RAG / Automation Layer** (L6, `ovp-rag` + `ovp-auto` — a read-only retriever/ranker/context builder/eval, and a one-shot sweep that calls L4 + L5). `TxnFsApplier` was assessed and deferred — every op is idempotent, so multi-file atomicity isn't required (re-apply recovers a partial run).

## Next

Work is placed against the target layers (`docs/architecture.md` "Target architecture layers"). L0–L6 are landed. Genuine future work (explicit non-goals of the L6 v1):

1. **Embedding / semantic ranker** for `ovp-rag` — a future `RetrievalWeights`-shaped extension; v1 is deterministic lexical scoring (no model, no network).
2. **A `--watch` polling daemon** wrapping `AutoRun::sweep` — v1 automation is one-shot and sync (no async runtime).
3. **Frontmatter-stripped RAG snippets** — v1 snippets are the first chars of the raw note body.

See `docs/stage-rag-automation.md` (L6 non-goals), `docs/architecture.md` "What comes next", and `docs/legacy-alignment.md` for rationale.
