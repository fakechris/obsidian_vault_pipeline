# OVP Next

This repository is the **Rust OVP Next trunk**. The Rust workspace now lives at the repository root (`Cargo.toml`, `crates/`, `fixtures/`, `manifests/`, `scripts/`, `docs/`) and is no longer nested under `rust/ovp-next/`.

Clean-core Rust rewrite of the Obsidian Vault Pipeline — the **current trunk and future mainline**.

**Status (accurate framing):** the validated **reader / truth-layer loop** (`Source → Grounded Units → Critic Repair → Reader Cards → Reader Pack`, M14a–M20) is now wrapped in a **complete daily operator workflow on the real vault** (M30/M31): capture (clippings + pinboard) → normalize/dedup/lifecycle → grounded reader packs → durable ledgers/reports → a queryable read model → a bilingual product console. One command (`ovp-next daily --vault-root <vault> --client live`) runs the whole cycle — see `docs/operator-runbook.md`. This is **NOT yet a full-functionality equivalent of the legacy Python OVP.** The earlier L0–L6 canonical / MOC / concept-promotion layers (M7–M13, described below) exist in-tree and still build + test, but the M14–M17 line found that **eager concept/canonical extraction was the wrong root** (0/3 on real models across ~8 milestones) and **demoted it** — the grounded truth-layer + reader view is the trunk going forward. See `docs/stage-m15-results.md` → `docs/stage-m17-grounded-reader-trunk.md`.

This repo intentionally has zero dependency on the legacy Python `ovp_pipeline` package: no import, no subprocess, no embedded runtime. The legacy Python implementation has been removed from this branch's working tree **to keep architecture judgments clean — the Rust crates are the single source of truth — NOT because the Rust trunk is yet feature-equivalent to legacy Python.** Historical fixtures and docs may describe legacy behavior as a frozen contract, but current development happens in the Rust crates. Any `scripts/*.py` here are offline eval/diagnostic helpers, not a runtime architecture source.

## What works today

17 crates; **603 tests pass (1 ignored)** + a binary-level end-to-end dogfood. The blessed product path (M30/M31):

```
ovp-next daily --vault-root ~/Documents/ovp-vault --client live   # the whole daily cycle
ovp-next intake | pinboard-sync | index | find | console          # the pieces, separately
```

Capture dirs (`Clippings/`, `50-Inbox/00-Capture`, `50-Inbox/02-Pinboard`) are swept into a normalized raw queue with URL/content dedup; new sources run through the grounded reader trunk into vault-local packs (`40-Resources/Reader/`); succeeded sources move to `03-Processed/`; every attempt lands in append-only ledgers with an OVP_RULES write log; a JSON read model and a bilingual console are rebuilt from product state. Failures retry; 3 failures block a source pending review. Every CLI verb is labeled PRODUCT / DIAGNOSTIC / DEMOTED in `--help`. (The validated current path is the grounded **reader trunk** — M14a–M17, `read-source` command + the `ovp-domain::reader` module; the canonical / MOC / concept-promotion machinery described in the rest of this section is the earlier M7–M13 substrate — still built and tested, but demoted off the main path per the status note above.) Three acceptance fixtures (`article_clean`, `article_mixed_lang`, `paper_arxiv`) run through the pipeline offline against committed cassettes; the resulting `WritePlan` is applied to a tempdir vault and the round-trip fields match. Pipelines are **assembled** from a declarative manifest (node id + kind + config + edges) plus app `AppWiring` — the CLI and tests no longer hand-wire `register_*`. A single **`run-cycle`** command drives the whole thing — inbox file → vault note + evergreen + canonical + MOC + knowledge index — with a run report and idempotent re-runs. Read-only **`query`** and **`lint`** commands read the result back (list / get / search / backlinks / stats) and health-check it (missing notes, stale index/MOC, broken wikilinks, orphan concepts). A read-only **`rag`** command retrieves over that read model — deterministic, explainable lexical scoring → ranking → a bounded context — and an **`auto-run`** sweep discovers an inbox, runs the `run-cycle` per file, then lints the result, all offline. A unified pipeline routes a mixed inbox (articles + papers) to the right interpreter by source kind. Concept promotion is driven by a loadable `ConceptRegistry`, not hardcoded constants. New evergreen concepts mint through a single hardened `CanonicalSlug` rule, land in a canonical store, and rebuild derived MOC + knowledge-index artifacts. The live Anthropic client + cassette capture exist behind the `anthropic` feature (`docs/live-capture.md`); the default build and CI are offline and need no API key.

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
| `ovp-intake` | **(M31)** Capture boundary: clippings/pinboard sweep → normalized `01-Raw`, URL+sha256 dedup, non-destructive lifecycle moves, append-only intake/pinboard ledgers, `RunLock`, OVP_RULES write-log primitives. Pinboard live HTTP behind `pinboard-live` feature. |
| `ovp-daily` | **(M30/M31)** The daily loop: plan (hash dedup + 3-failure retry cap) → reader trunk per source → packs → lifecycle move (after the success record is durable) → per-run report. |
| `ovp-index` | **(M31)** Read model: deterministic JSON projection over ledgers + packs + crystal store + reports (`.ovp/index/index.json`); full rebuild = migration story; queried by `find`. No SQLite/embeddings by decision. |
| `ovp-console` | **(M31)** Product console: deterministic bilingual (EN+中文) HTML over the read model (`.ovp/console/index.html`) — attention feed, runs, sources, packs, crystal claims, provenance links. |
| `ovp-eval` | External E2E comparator (M8): `CompareRun::execute` runs one input through BOTH the ovp-next pipeline (reusing the M7 review harness) and an **external** Nowledge Mem HTTP service (`NowledgeClient` adapter, `reqwest::blocking`), normalizes both into a shared `NormalizedSubject`, and writes a deterministic comparison pack across five explicitly-lexical dimensions (concept overlap, claim diff, grounding, structure, retrieval). Evaluation/orchestration layer only — Nowledge Mem is a comparator, NOT legacy OVP and NOT a trunk dependency (gate-enforced); the adapter fails loud and the pack is partial when a side fails. Real-LLM + the network call are explicit, manual operations (offline tests use a fake client + replay cassettes; the live test is `#[ignore]`d). |
| `ovp-review` | E2E review harness (M7): `ReviewRun::execute` *calls* the L4 cycle on one input, reads it back via L5 (`ovp-query` / `ovp-lint`) + L6 (`ovp-rag`), and writes a deterministic, human-inspectable **review pack** (processor chain, run report, apply summary, files written, canonical summary, lint, query stats, RAG preview, and an `--expected-dir` comparison that defers to `ovp-domain`'s contract engine). It is a quality *gate*, not just an observability dump: the **review** verdict (and the CLI exit code) is `cycle_succeeded() && contract MUST-clean`, so a clean run whose output violates its frozen contract still fails. Read / orchestrate only — the only vault/canonical content writes go through `RunCycle`; the harness writes just the pack (and the empty store-root dirs). Reimplements no pipeline logic. |
| `ovp-cli` | Thin arg-parsing layer: builds `ModelClient` + `ConceptRegistry` + `AppWiring`, delegates assembly to `ovp-app`, the cycle to `ovp-run`, reads to `ovp-query` / `ovp-lint` / `ovp-rag`, the sweep to `ovp-auto`, the review pack to `ovp-review`. PRODUCT subcommands: `daily`, `intake`, `pinboard-sync`, `index`, `find`, `console`, `read-source`, `crystal-lint`/`crystal-write`/`crystal-review`; demoted/diagnostic: `run-cycle`, `query`, `lint`, `rag`, `auto-run`, `review-run`, `compare-run`, `interpret-article`, `apply-plan`, `graph`, `extract-units`, `extract-referents`, `copy-probe`. |

## Docs

- `docs/architecture.md` — current authoritative architecture + system primitives + crate responsibilities + deprecated vocabulary.
- `docs/operator-runbook.md` — **how to run the Rust daily workflow on the real vault** (M31).
- `docs/product-state-layout.md` — where product state lives, what is authoritative vs derived (M31).
- `docs/stage-m31-mainline-capability-closure.md` — the M31 epic: capture → daily workflow → index → console.
- `docs/mainline-return-matrix.md` — legacy-vs-Rust capability matrix (M29, updated M31: P0 set closed).
- `docs/legacy-alignment.md` — living gap matrix between this rewrite and the legacy Python OVP. Read before scoping any new stage.
- `docs/live-capture.md` — how to make live Anthropic calls + capture cassettes (`--features anthropic`, `--client live`).
- `docs/invariants.md` — the 12 invariants; CI-gated where possible.
- `docs/stage-graph-assembly.md` — the Graph Assembly Layer (`ovp-app`): manifest shape, primitives, validation, acceptance.
- `docs/stage-operational-workflow.md` — the L4 `run-cycle` (`ovp-run`): flow, idempotence, fail-closed semantics, dry-run.
- `docs/stage-read-health.md` — the L5 read/health layer (`ovp-query` + `ovp-lint`): `KnowledgeView`, queries, planned lint checks.
- `docs/stage-rag-automation.md` — the L6 RAG read path (`ovp-rag`) + automation sweep (`ovp-auto`): crate boundaries, public nouns, data flow, acceptance tests, non-goals.
- `docs/stage-m13.2-v2-concept-map.md` — M13.2: the additive v2 concept-map path (synthetic-green; foundation for real-model work).
- `docs/stage-m13.3-v2-live-loop.md` — M13.3: real `MiniMax-M2.7-highspeed` run executed; **0/3 bench on real data, all 3 cases complete end-to-end**. Three framework fixes (reqwest timeout, `OVP_LLM_TIMEOUT_SECS`, parser null-tolerance) landed; the remaining gap is prompt-quality, scoped to M13.4.
- `docs/stage-m13.4-prompt-iteration.md` — M13.4: the prompt-first iteration plan (slug drift, umbrella over-mint, abstract definitions); no production code change expected.
- `docs/stage-c.md`, `docs/stage-d-plan-applier.md` — historical stage docs.
- `docs/calibration-r1.md`, `docs/calibration-r2.md` — historical calibration verdicts.
- `fixtures/` — frozen contracts captured from the legacy system.

## Landed

The earlier L0–L6 rewrite is in-tree and built/tested (it is NOT a full-functionality equivalent of legacy Python — see the status note at the top): C9/C10 (live Anthropic + capture), L0/L1 (intake + `VaultLayout`), v1.2 (paper routing), L3 (`ConceptRegistry`), EvergreenConceptWriter (mints new evergreens + `CanonicalUpsert`), the canonical store (`CanonicalFsStoreApplier` + typed `CanonicalConcept`), the derived rebuilds (`MocBuilder` + `KnowledgeIndexBuilder`), the **Graph Assembly Layer** (L2, `ovp-app`), the **Operational Workflow Layer** (L4, `ovp-run` + `run-cycle`), the **Read / Health Layer** (L5, `ovp-query` + `ovp-lint`), and the **RAG / Automation Layer** (L6, `ovp-rag` + `ovp-auto`). `TxnFsApplier` was assessed and deferred — every op is idempotent, so multi-file atomicity isn't required.

**M14–M17 pivot (current direction).** The L0–L6 work above centred on *eager* concept/canonical extraction; the M14–M17 line found that root was wrong (0/3 real-green across ~8 milestones), and pivoted to the **grounded reader trunk**: `Source → Grounded Units (M14a) → Critic Repair (M14a.8) → Reader Cards (card_synth/v3, M16.1) → Reader Pack (M17, the `read-source` command)`. The grounded units are the *truth layer* (verbatim-quote-anchored, `accepted_without_quote = 0`); reader cards are the *view layer* (collapsible HTML/MD with provenance intact). The Referent / Resolver / canonical-concept ontology is **demoted** — only a narrow, entity-density-gated object-index helper is contemplated, not a main-path stage. Validated against KnowledgeMEM on held-out articles (M15/M16.1/M17): the truth layer wins faithfulness + coverage + provenance; the reader pack is human-usable with collapsed-but-intact evidence. See `docs/stage-m15-results.md`, `docs/stage-m16.1-card-view-v3.md`, `docs/stage-m17-grounded-reader-trunk.md`.

## Next

Work is placed against the target layers (`docs/architecture.md` "Target architecture layers"). L0–L6 are landed. Genuine future work (explicit non-goals of the L6 v1):

1. **Embedding / semantic ranker** for `ovp-rag` — a future `RetrievalWeights`-shaped extension; v1 is deterministic lexical scoring (no model, no network).
2. **A `--watch` polling daemon** wrapping `AutoRun::sweep` — v1 automation is one-shot and sync (no async runtime).
3. **Frontmatter-stripped RAG snippets** — v1 snippets are the first chars of the raw note body.

See `docs/stage-rag-automation.md` (L6 non-goals), `docs/architecture.md` "What comes next", and `docs/legacy-alignment.md` for rationale.
