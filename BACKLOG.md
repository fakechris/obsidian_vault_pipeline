# OVP Active Backlog

**Updated:** 2026-04-30
**Status:** Active implementation backlog source

This file is the single current backlog entry point for implementation sequencing.

It is not the only evidence source. It is the maintained merge view over:

- repo milestone history and shipped phase docs
- `docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md`
- recent KSR task extraction in `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

Rule: historical plans and vault research notes feed this file; they do not override it silently. When a PR lands or a KSR task changes state, update this file first, then mirror or annotate secondary docs as needed.

## Current Milestones

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Done | CLI, source lifecycle, pack/profile runtime, `knowledge.db`, first source-lifecycle idempotency slice |
| M1 Operator Workbench And Review Runtime | Done / maintain | truth UI, candidates, signals/actions, contradictions, action worker |
| M2 Roadmap And README Consolidation | Done | merged historical milestones, compiler roadmap, recent KSR input, reader-product research, and English-primary docs |
| M3 Reader-First Knowledge Atlas | Active | reader home and `/ops` split shipped; object pages, backlinks, and graph still need product shape |
| M4 KSR Safety And Hot-Path Hardening | Active | projection labels, hot-path audit, and wiring evals shipped; routing preview, evidence spans, and candidate risk remain |
| M5 Context Pack And Operational Runtime | Later | session snapshots, context budget, claim leases, provider facade, observability |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer, claim lifecycle, conflict detection, policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor, query feedback, routines, notebook/raw-source mode |

## Active Implementation Backlog

| ID | Priority | Status | Work item | Source links |
| --- | --- | --- | --- | --- |
| BL-000 | P0 | Done | Commit current roadmap/README/backlog consolidation, including English-primary README/Architecture/Milestone docs with Chinese alternates | M2, PR #74 |
| BL-001 | P0 | Done | Reader shell route split: make `/` a Knowledge Atlas home and move current dashboard to `/ops` | M3, reader-product note, PR #75 |
| BL-002 | P0 | Done | Projection marking: label dashboard, MOC, wiki, briefing, reader pages, graph, and context packs as projections | M4, KSR-002, PR #78 |
| BL-003 | P0 | Done | Dashboard/search hot-path audit: default UI/search paths must not scan raw/PDF/Office sources | M4, KSR-015, `docs/plans/2026-04-30-bl-003-004-hot-path-wiring-safety.md`, PR #77 |
| BL-004 | P0 | Done | Workflow wiring eval suite for lifecycle routing, promote gates, projection labels, hot paths, and read/write boundaries | M4, KSR-026, `docs/plans/2026-04-30-bl-003-004-hot-path-wiring-safety.md`, PR #77 |
| BL-005 | P0 | Next | Article routing preview before source lifecycle changes | M4, KSR-014 |
| BL-006 | P0 | Next | Evidence span schema and markdown-aware locator backfill | M4, KSR-001, KSR-018 |
| BL-007 | P0 | Next | Candidate risk layering by evidence strength, identity ambiguity, sensitivity, and impact | M4, KSR-003 |
| BL-008 | P1 | Next | Kind-aware object pages for people, concepts, companies/tools/projects, events, and claims | M3 |
| BL-009 | P1 | Next | Mention/backlink rail with excerpts, source jumps, and relation context | M3, reader-product note |
| BL-010 | P1 | Next | Visual `/graph` MVP as a spatial corpus map; keep analytical clusters under ops/debug | M3 |
| BL-011 | P1 | Later | Reader-oriented search grouped by kind, summary, evidence, and reason | M3/M4 |
| BL-012 | P1 | Later | Trusted reuse event instrumentation for downstream use of accepted/cited knowledge | April 22 roadmap |
| BL-013 | P1 | Later | Session snapshot / OVP context pack / explicit context budget | M5, KSR-004, KSR-017, KSR-022 |
| BL-014 | P1 | Later | Operational runtime graph, claim lease, observability metrics, provider facade | M5, KSR-020, KSR-021, KSR-023, KSR-025 |
| BL-015 | P1 | Later | Permission layer and claim lifecycle fields | M6, KSR-005, KSR-006 |
| BL-016 | P2 | Later | Conflict detection regularization, high-risk profile gate, candidate compaction with restore | M6, KSR-007, KSR-008, KSR-024 |
| BL-017 | P2 | Later | Schema-on-demand guard for new claim/candidate profiles | M6, KSR-028 |
| BL-018 | P2 | Later | Reviewed semantic relation extractor and query feedback loop | M7, April 22 roadmap |
| BL-019 | P2 | Later | Skill/routine extraction profile, notebook/raw-source mode, ingest ROI, hybrid retrieval, multimodal caption-first ingest, follow-up object model | M7, KSR-010, KSR-011, KSR-012, KSR-016, KSR-019, KSR-029 |
| BL-020 | P1 | Later | Projection repair lifecycle: structured marker kind/scope/reason, supersession, claim lease, and repair audit events | M4/M5, Architecture |
| BL-021 | P1 | Later | Authority/projection schema versioning and migration-triggered full rebuild markers | M4/M5, Architecture |

## KSR Task Coverage

| KSR ID | Backlog mapping | Current status in this backlog |
| --- | --- | --- |
| KSR-001 Evidence span 化 | BL-006 | Next |
| KSR-002 Projection 标注 | BL-002 | Done |
| KSR-003 Candidate 风险分层 | BL-007 | Next |
| KSR-004 Session snapshot/context pack | BL-013 | Later |
| KSR-005 Permission layer 分离 | BL-015 | Later |
| KSR-006 Claim lifecycle 字段 | BL-015 | Later |
| KSR-007 Conflict detection 常规化 | BL-016 | Later |
| KSR-008 High-risk user profile gate | BL-016 | Later |
| KSR-009 Cost-aware workflow routing | BL-003/BL-005 | Partial: hot-path guard done; article routing preview remains |
| KSR-010 Skill/routine extraction profile | BL-019 | Later |
| KSR-011 Notebook/raw-source mode | BL-019 | Later |
| KSR-012 Ingest ROI metrics | BL-019 | Later |
| KSR-013 Source lifecycle idempotency | M0/M4 | Repo first slice done; verify and mirror vault status |
| KSR-014 Article routing preview | BL-005 | Next |
| KSR-015 Dashboard/search hot-path audit | BL-003 | Done |
| KSR-016 Hybrid retrieval experiment | BL-019 | Later |
| KSR-017 Explicit context budget | BL-013 | Later |
| KSR-018 Markdown-aware evidence chunking | BL-006 | Next |
| KSR-019 Multimodal caption-first ingest | BL-019 | Later |
| KSR-020 Operational runtime graph | BL-014 | Later |
| KSR-021 Claim lease for workflow items | BL-014 | Later |
| KSR-022 OVP prime/context pack | BL-013 | Later |
| KSR-023 Pipeline observability metrics | BL-014 | Later |
| KSR-024 Candidate compaction with restore | BL-016 | Later |
| KSR-025 Context provider facade | BL-014 | Later |
| KSR-026 Workflow wiring eval suite | BL-004 | Done |
| KSR-027 Live-source routing policy | BL-005/BL-014 | Later |
| KSR-028 Schema-on-demand guard | BL-017 | Later |
| KSR-029 Follow-up object model | BL-019 | Later |

## Next Decision

`BL-001` is shipped in PR #75, `BL-003 + BL-004` are shipped in PR #77, and `BL-002` is shipped in PR #78. The default UI now has a reader-first entry point, `/ops` owns the operator dashboard, and core access/materialized surfaces carry explicit projection labels.

The next implementation PR should move back to the reader product shape: **BL-008 + BL-009**. The projection boundary is now explicit enough to improve object pages and backlink/source rails without making those surfaces look like canonical truth.

Recommended order:

1. BL-008 + BL-009
2. BL-010
3. BL-005
4. BL-006 + BL-007
