# OVP Milestone

> Language: English | [简体中文](MILESTONE.zh-CN.md)

**Updated:** 2026-04-30
**Status:** Current milestone sequence and implementation direction

This document is the stable milestone entry point. It summarizes the current product and engineering route; [BACKLOG.md](BACKLOG.md) remains the single active implementation queue.

## Inputs

The milestone sequence is a reconciled view over four inputs:

- shipped repo milestone history and phase docs
- [Vision & Roadmap: The Auditable Knowledge Compiler](docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md)
- recent KSR task extraction in `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- [reader-first product-shape research](docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md)

The KSR vault page is a high-signal recent input, not the complete backlog authority. Implementation sequencing is maintained in [BACKLOG.md](BACKLOG.md).

## Product Thesis

OVP is moving from a document-processing pipeline into:

> A reader-first, evidence-backed knowledge atlas over an auditable knowledge state runtime.

That means the user-facing product should make compiled knowledge easy to read first, while the operator dashboard remains available under maintenance-oriented surfaces such as `/ops`.

## Current Milestones

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Done | CLI, source lifecycle, pack/profile runtime, `knowledge.db`, first source-lifecycle idempotency slice |
| M1 Operator Workbench And Review Runtime | Done / maintain | truth UI, candidates, signals/actions, contradictions, action worker |
| M2 Roadmap And README Consolidation | Done | merged historical milestones, compiler roadmap, recent KSR input, reader-product research, and the English-primary docs structure |
| M3 Reader-First Knowledge Atlas | Active | reader home, `/ops` split, and first object source/backlink rail shipped; graph and deeper kind-specific object layouts remain |
| M4 KSR Safety And Hot-Path Hardening | Active | projection labels, hot-path audit, and wiring evals shipped; routing preview, evidence spans, and candidate risk remain |
| M5 Context Pack And Operational Runtime | Later | session snapshots, context budget, claim leases, provider facade, observability |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer, claim lifecycle, conflict detection, policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor, query feedback, routines, notebook/raw-source mode |

## Active Backlog Alignment

| Architecture / product work | Active backlog mapping |
| --- | --- |
| Reader shell route split | `BL-001` done in PR #75 |
| Projection marking | `BL-002`, `KSR-002` done in PR #78 |
| Dashboard/search hot-path audit | `BL-003`, `KSR-015` done in PR #77 |
| Workflow wiring eval suite | `BL-004`, `KSR-026` done in PR #77 |
| Article routing preview | `BL-005`, `KSR-014` |
| Evidence span / factual evidence completeness | `BL-006`, `KSR-001`, `KSR-018` |
| Candidate risk layering | `BL-007`, `KSR-003` |
| Kind-aware object pages and backlink rail | `BL-008` partial and `BL-009` done in PR #79 |
| Visual graph MVP | `BL-010` |
| Projection repair lifecycle | `BL-020` |
| Schema versioning and migration trigger | `BL-021` |

## Near-Term Sequence

Recommended order:

1. Implement `BL-010`: ship the visual `/graph` MVP as a spatial corpus map.
2. Implement `BL-005`: add article routing preview before source lifecycle changes.
3. Implement `BL-006 + BL-007`: evidence spans and candidate risk layering.
4. Continue `BL-008`: deeper per-kind object layouts beyond the first reader profile.

## Documentation Rules

- `README.md` is the English primary project entry point.
- `README.zh-CN.md` is the Chinese README.
- `ARCHITECTURE.md` is the English primary architecture contract.
- `ARCHITECTURE.zh-CN.md` is the Chinese architecture contract.
- `MILESTONE.md` is the English primary milestone entry point.
- `MILESTONE.zh-CN.md` is the Chinese milestone entry point.
- [BACKLOG.md](BACKLOG.md) is the active implementation backlog source.
- Historical phase docs remain evidence and context, not active execution sources unless referenced by `BACKLOG.md`.

## Detailed Rationale

For the longer reconciliation narrative, see:

- [Consolidated Product Roadmap](docs/plans/2026-04-29-consolidated-product-roadmap.md)
- [Reader Product Shape And Backlog Reconciliation](docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md)
- [Architecture](ARCHITECTURE.md)
