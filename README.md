# OVP Next

Clean-core Rust rewrite of the Obsidian Vault Pipeline. **Status: full legacy cycle closed — raw → note → evergreen → canonical → MOC + knowledge index, all derived state rebuildable; routed unified pipeline; live LLM behind a feature flag.**

This repo intentionally has zero dependency on the legacy Python `ovp_pipeline` package — no import, no subprocess, no embedded runtime. The old system is a frozen oracle for fixtures and contracts, not a runtime dependency.

## What works today

Six crates, 243 tests. Three acceptance fixtures (`article_clean`, `article_mixed_lang`, `paper_arxiv`) run through the pipeline offline against committed cassettes; the resulting `WritePlan` is applied to a tempdir vault and the round-trip fields match. Pipelines are **assembled** from a declarative manifest (node id + kind + config + edges) plus app `AppWiring` — the CLI and tests no longer hand-wire `register_*`. A unified pipeline routes a mixed inbox (articles + papers) to the right interpreter by source kind. Concept promotion is driven by a loadable `ConceptRegistry`, not hardcoded constants. New evergreen concepts mint through a single hardened `CanonicalSlug` rule, land in a canonical store, and rebuild derived MOC + knowledge-index artifacts. The live Anthropic client + cassette capture exist behind the `anthropic` feature (`docs/live-capture.md`); the default build and CI are offline and need no API key.

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
| `ovp-app` | Assembly layer: `GraphAssembler` builds a `GraphRunner` from a `DomainPipelineSpec` (node id + kind + config + edges) + `AppWiring`, via a compiled-in `NodeRegistry`. DirectShow-like, not a plugin system. |
| `ovp-cli` | Thin arg-parsing layer: builds `ModelClient` + `ConceptRegistry` + `AppWiring`, delegates assembly to `ovp-app`. Subcommands: `interpret-article` (`--client replay|live`), `apply-plan`, `graph`. |

## Docs

- `docs/architecture.md` — current authoritative architecture + system primitives + crate responsibilities + deprecated vocabulary.
- `docs/legacy-alignment.md` — living gap matrix between this rewrite and the legacy Python OVP. Read before scoping any new stage.
- `docs/live-capture.md` — how to make live Anthropic calls + capture cassettes (`--features anthropic`, `--client live`).
- `docs/invariants.md` — the 12 invariants; CI-gated where possible.
- `docs/stage-graph-assembly.md` — the Graph Assembly Layer (`ovp-app`): manifest shape, primitives, validation, acceptance.
- `docs/stage-c.md`, `docs/stage-d-plan-applier.md` — historical stage docs.
- `docs/calibration-r1.md`, `docs/calibration-r2.md` — historical calibration verdicts.
- `fixtures/` — frozen contracts captured from the legacy system.

## Landed

The full legacy cycle is closed: C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), L3 (`ConceptRegistry`), EvergreenConceptWriter (mints new evergreens + `CanonicalUpsert`), canonical store (`CanonicalFsStoreApplier` + typed `CanonicalConcept` payload), and L4/L5 (`MocBuilder` + `KnowledgeIndexBuilder`, derived + rebuildable). `TxnFsApplier` was assessed and deferred — every op is idempotent, so multi-file atomicity isn't required (re-apply recovers a partial run). Most recently, the **Graph Assembly Layer** (`ovp-app`) replaced hand-wired `register_*` calls with declarative manifest assembly.

## Next

Re-triaged from the legacy alignment baseline (`docs/legacy-alignment.md`) P1, against observed pain:

- `ovp-query` — read surface over the knowledge index.
- `ovp-lint` — WIGS-style health checks over canonical + vault.
- autopilot watcher (`InboxScanSource` is already the intake primitive).

See `docs/architecture.md` "What comes next" and `docs/legacy-alignment.md` for rationale.
