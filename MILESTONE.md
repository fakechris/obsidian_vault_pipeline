# OVP Milestone

> Language: English | [简体中文](MILESTONE.zh-CN.md)

**Updated:** 2026-05-01
**Status:** Current milestone sequence and implementation direction

This document is the stable milestone entry point. It summarizes the current product and engineering route; [BACKLOG.md](BACKLOG.md) remains the single active implementation queue.

## Inputs

The milestone sequence is a reconciled view over:

- shipped repo milestone history and phase docs
- [Vision & Roadmap: The Auditable Knowledge Compiler](docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md)
- recent KSR task extraction in `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- [reader-first product-shape research](docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md)
- kg-eval quality assessment and `OVP_FIX_PLAN.md` (2026-05-01)

The KSR vault page is a high-signal recent input, not the complete backlog authority. Implementation sequencing is maintained in [BACKLOG.md](BACKLOG.md).

## Product Thesis

OVP is evolving from a personal Zettelkasten into:

> A typed, evidence-backed knowledge platform — reader-first for humans, programmable for agents, extensible through domain packs.

Three target tiers:

1. **Personal knowledge atlas** — Zettelkasten-quality with typed entities (current + M8)
2. **Company second brain** — pack-driven domain ontology, team-shared typed knowledge (M9)
3. **Operational knowledge layer** — Palantir-style actions, decisions, audit (M10, evaluate ROI)

## Current Milestones

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Done | CLI, source lifecycle, pack/profile runtime, `knowledge.db`, first source-lifecycle idempotency slice |
| M1 Operator Workbench And Review Runtime | Done | truth UI, candidates, signals/actions, contradictions, action worker |
| M2 Roadmap And README Consolidation | Done | merged historical milestones, compiler roadmap, recent KSR input, reader-product research, and the English-primary docs structure |
| M3 Reader-First Knowledge Atlas | Done | reader home, `/ops` split, object source/backlink rail, visual graph map, kind-specific object reader lenses, and reader-oriented search shipped |
| M4 KSR Safety And Hot-Path Hardening | Done | projection labels, hot-path audit, wiring evals, article routing preview, evidence spans, candidate risk, JSONL streaming fix, projection lifecycle hardening, runtime-state API fixes (final PRs: #98, #99, #100) |
| M5 Context Pack And Operational Runtime | Done | session snapshots, context budget, runtime state in `/ops` and doctor, provider-facing runtime-state API, action queue health |
| M5a Quality And Dedup Hardening | Done | concept dedup pipeline integration with `scope_slugs`, promote semantic guard (trigram-Jaccard), historical data cleanup (71→61 Evergreens), `find_similar_slugs` utility (PR #101) |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer, claim lifecycle, conflict detection, policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor, query feedback, routines, notebook/raw-source mode |
| **M8 Type Unification And Extraction Quality** | **Active** | unified object kind taxonomy, Layer 1 `entity_type` frontmatter, body-size-aware extraction (P3), quote-grounding (P4), single-pass LLM refactor (P5), historical backfill |
| **M9 Pack As Domain Ontology** | **Next** | pack-defined object kind specs, typed relation constraints, schema registry, domain-specific extraction profiles |
| **M10 Operational Knowledge Layer** | **Later** | action types on objects, permission + contract, cross-entity aggregation, decision memory |

## Active Backlog Alignment

| Architecture / product work | Active backlog mapping |
| --- | --- |
| Reader shell route split | `BL-001` done in PR #75 |
| Projection marking | `BL-002`, `KSR-002` done in PR #78 |
| Dashboard/search hot-path audit | `BL-003`, `KSR-015` done in PR #77 |
| Workflow wiring eval suite | `BL-004`, `KSR-026` done in PR #77 |
| Article routing preview | `BL-005`, `KSR-014` done in PR #81 |
| Evidence span / factual evidence completeness | `BL-006`, `KSR-001`, `KSR-018` done in PR #82 |
| Candidate risk layering | `BL-007`, `KSR-003` done in PR #82 |
| Kind-aware object pages and backlink rail | `BL-008` and `BL-009` done through PR #79 and PR #83 |
| Visual graph MVP | `BL-010` done in PR #80 |
| Reader-oriented search | `BL-011` done in PR #84 |
| Trusted reuse context pack / OVP prime | `BL-012` and `BL-013` first implementation done in PR #89 plus PR #90 |
| Operational runtime state projection | `BL-014` first slice in PR #91; `/ops` / doctor / API integration in PR #92; action queue health and materialized read-side policy in the M5 closeout slice |
| Projection repair lifecycle | `BL-020` done in PR #87 |
| Schema versioning and migration trigger | `BL-021` done in PR #87 plus PR #88 |
| Architecture refactor (JSONL, truth_api, ui_server, projection) | PR #100 |
| Concept dedup pipeline + promote semantic guard | PR #101, `BL-025` through `BL-030` are M8 |

## Near-Term Sequence

Recommended order:

1. **M8 first**: execute `BL-025` (type unification) → `BL-026` (extraction output) → `BL-027` (P3) → `BL-028` (P4) → `BL-029` (P5) → `BL-030` (backfill).
2. **M9 second**: `BL-031` through `BL-034` once M8 type system is stable.
3. **M10 evaluate**: after M9 ships, decide scope based on real multi-pack adoption and company-brain use cases.
4. `BL-015` (permissions) when permission and claim lifecycle become the active blocker.
5. Keep workflow actions on the existing action worker lock; add generalized workflow leases only when multi-worker scheduling is introduced.

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
