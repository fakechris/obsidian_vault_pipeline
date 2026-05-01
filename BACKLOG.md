# OVP Active Backlog

**Updated:** 2026-04-30
**Status:** Active implementation backlog source

This file is the single current backlog entry point for implementation sequencing. Completed items are archived in [docs/backlog-archive.md](docs/backlog-archive.md).

It is not the only evidence source. It is the maintained merge view over:

- repo milestone history and shipped phase docs
- `docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md`
- recent KSR task extraction in `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

Rule: historical plans and vault research notes feed this file; they do not override it silently. When a PR lands or a KSR task changes state, update this file first, then mirror or annotate secondary docs as needed.

## Current Milestones

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0–M3 | Done | Foundation, operator workbench, roadmap consolidation, reader-first atlas (see [archive](docs/backlog-archive.md)) |
| M4 KSR Safety And Hot-Path Hardening | Active | projection labels, hot-path audit, wiring evals, article routing preview, evidence spans, and candidate risk shipped; deeper enforcement remains |
| M5 Context Pack And Operational Runtime | Done | session snapshots, context budget, runtime state in `/ops` and doctor, provider-facing runtime-state API, action queue health |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer, claim lifecycle, conflict detection, policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor, query feedback, routines, notebook/raw-source mode |

## Active Backlog

| ID | Priority | Status | Work item | Source links |
| --- | --- | --- | --- | --- |
| BL-015 | P1 | Later | Permission layer and claim lifecycle fields | M6, KSR-005, KSR-006 |
| BL-016 | P2 | Later | Conflict detection regularization, high-risk profile gate, candidate compaction with restore | M6, KSR-007, KSR-008, KSR-024 |
| BL-017 | P2 | Later | Schema-on-demand guard for new claim/candidate profiles | M6, KSR-028 |
| BL-018 | P2 | Later | Reviewed semantic relation extractor and query feedback loop | M7, April 22 roadmap |
| BL-019 | P2 | Later | Skill/routine extraction profile, notebook/raw-source mode, ingest ROI, hybrid retrieval, multimodal caption-first ingest, follow-up object model | M7, KSR-010, KSR-011, KSR-012, KSR-016, KSR-019, KSR-029 |
| BL-022 | P1 | Later | Decision context memory: first-class rationale, rejected alternatives, dissent, owner, participants, and validity windows for high-value decisions | M6/M7, KSR-030 |
| BL-023 | P1 | Later | Agent workspace substrate and information-health loop: expose AGENTS/MEMORY/Skills/Heartbeat/autonomy policy as runtime state and run approval-based stale/conflict/term-drift reviews | M6/M7, KSR-031, KSR-032 |
| BL-024 | P1 | Later | Machine-facing memory substrate: typed scored memory records with freshness, evidence count, supersession, last-used telemetry, and token-budgeted selective injection | M6/M7, KSR-033 |

## KSR Task Coverage

| KSR ID | Backlog mapping | Status |
| --- | --- | --- |
| KSR-001 Evidence span 化 | BL-006 | Done |
| KSR-002 Projection 标注 | BL-002 | Done |
| KSR-003 Candidate 风险分层 | BL-007 | Done |
| KSR-004 Session snapshot/context pack | BL-013 | Done |
| KSR-005 Permission layer 分離 | BL-015 | Later |
| KSR-006 Claim lifecycle 字段 | BL-015 | Later |
| KSR-007 Conflict detection 常规化 | BL-016 | Later |
| KSR-008 High-risk user profile gate | BL-016 | Later |
| KSR-009 Cost-aware workflow routing | BL-003/BL-005 | Partial |
| KSR-010 Skill/routine extraction profile | BL-019 | Later |
| KSR-011 Notebook/raw-source mode | BL-019 | Later |
| KSR-012 Ingest ROI metrics | BL-019 | Later |
| KSR-013 Source lifecycle idempotency | M0/M4 | Done |
| KSR-014 Article routing preview | BL-005 | Done |
| KSR-015 Dashboard/search hot-path audit | BL-003 | Done |
| KSR-016 Hybrid retrieval experiment | BL-019 | Later |
| KSR-017 Explicit context budget | BL-013 | Done |
| KSR-018 Markdown-aware evidence chunking | BL-006 | Done |
| KSR-019 Multimodal caption-first ingest | BL-019 | Later |
| KSR-020 Operational runtime graph | BL-014 | Done |
| KSR-021 Claim lease for workflow items | BL-014 | Done |
| KSR-022 OVP prime/context pack | BL-013 | Done |
| KSR-023 Pipeline observability metrics | BL-014 | Done |
| KSR-024 Candidate compaction with restore | BL-016 | Later |
| KSR-025 Context provider facade | BL-014 | Done |
| KSR-026 Workflow wiring eval suite | BL-004 | Done |
| KSR-027 Live-source routing policy | BL-005/BL-014 | Later |
| KSR-028 Schema-on-demand guard | BL-017 | Later |
| KSR-029 Follow-up object model | BL-019 | Later |
| KSR-030 Decision context memory | BL-022 | Later |
| KSR-031 Agent workspace substrate | BL-023 | Later |
| KSR-032 Information health loop | BL-023 | Later |
| KSR-033 Machine-facing memory substrate | BL-024 | Later |

## Next Decision

BL-015 when permission and claim lifecycle become the active blocker. Generalized workflow leases only when action processing moves beyond the current single-worker lock model. BL-012/BL-013 follow-up only when a new context-pack surface needs the same contract.
