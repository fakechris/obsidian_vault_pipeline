# OVP Next

Clean-core Rust rewrite of the Obsidian Vault Pipeline. **Status: article + paper paths real on disk; routed unified pipeline; live LLM behind a feature flag.**

This repo intentionally has zero dependency on the legacy Python `ovp_pipeline` package — no import, no subprocess, no embedded runtime. The old system is a frozen oracle for fixtures and contracts, not a runtime dependency.

## What works today

Five crates, 144 tests. Three acceptance fixtures (`article_clean`, `article_mixed_lang`, `paper_arxiv`) run through the pipeline offline against committed cassettes; the resulting `WritePlan` is applied to a tempdir vault and the round-trip fields match. A unified pipeline routes a mixed inbox (articles + papers) to the right interpreter by source kind. Concept promotion is driven by a loadable `ConceptRegistry`, not hardcoded constants. The live Anthropic client + cassette capture exist behind the `anthropic` feature (`docs/live-capture.md`); the default build and CI are offline and need no API key.

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
| `ovp-stores` | `PlanApplier` impls. Today: `VaultFsPlanApplier`. |
| `ovp-cli` | Wiring + subcommands: `interpret-article` (`--client replay|live`), `apply-plan`, `graph`. |

## Docs

- `docs/architecture.md` — current authoritative architecture + system primitives + crate responsibilities + deprecated vocabulary.
- `docs/legacy-alignment.md` — living gap matrix between this rewrite and the legacy Python OVP. Read before scoping any new stage.
- `docs/live-capture.md` — how to make live Anthropic calls + capture cassettes (`--features anthropic`, `--client live`).
- `docs/invariants.md` — the 12 invariants; CI-gated where possible.
- `docs/stage-c.md`, `docs/stage-d-plan-applier.md` — historical stage docs.
- `docs/calibration-r1.md`, `docs/calibration-r2.md` — historical calibration verdicts.
- `fixtures/` — frozen contracts captured from the legacy system.

## Landed

C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), and L3 (`ConceptRegistry` + `ConceptResolver` consuming it) are all done.

## Next

Driven by the legacy alignment baseline (`docs/legacy-alignment.md`). Order:

1. **EvergreenConceptWriter** *(next)* — extract *new* evergreen candidates (concepts not yet in the `ConceptRegistry`) and emit the first real `CanonicalUpsert` + evergreen `VaultCreate` write surface. This is the legacy "absorb" equivalent (the part beyond candidate→canonical promotion) and the prerequisite for the canonical store.
2. **Canonical store** *(gated on 1)* — a `PlanApplier` impl that applies `CanonicalUpsert`; convert the `CanonicalUpsertOp` string payload stub to typed data once `EvergreenConceptWriter` defines the concrete payload.
3. **L4/L5 MOC + knowledge index + TxnFsApplier** *(gated on 2)* — derived state rebuildable from canonical + vault; `TxnFsApplier` only if multi-file atomicity is actually required. Closes the first end-to-end cycle (raw → Evergreen → MOC → knowledge index).

See `docs/architecture.md` "What comes next" and `docs/legacy-alignment.md` for rationale.
