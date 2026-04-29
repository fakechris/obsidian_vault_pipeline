# Consolidated Product Roadmap

**Date:** 2026-04-29
**Status:** Draft synthesis / reconciliation target

**Active backlog:** `BACKLOG.md`

## Roadmap Inputs

This roadmap is a synthesis over several planning layers. No single input below should be treated as complete on its own.

1. **Historical workbench milestone record**
   - `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
   - Covers the shipped workbench phases, graph/truth/operator surfaces, and older milestone numbering.

2. **April 22 compiler roadmap**
   - `docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md`
   - Covers Capture -> Compile -> Reuse, trusted reuse, evidence v2, policy promotion, semantic extraction, and query feedback.

3. **Recent KSR backlog input**
   - `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
   - Created from recent article/project research on 2026-04-29.
   - Covers detailed Knowledge State Runtime tasks such as evidence spans, projection labels, candidate risk, routing preview, context packs, permission, and workflow wiring evals.
   - This is a high-signal current input, but it is not an exhaustive source for all prior roadmap history.

4. **Reader product-shape input**
   - `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`
   - Covers the LearnBuffett-inspired reader-first product form: readable object pages, backlink rails, and visual graph.

This document is the roadmap rationale and merge view. `BACKLOG.md` is the active to-do list for implementation sequencing. Both should be revised as older roadmap gaps are re-read and reconciled, rather than assuming the latest KSR page supersedes all previous history.

## Backlog Reconciliation Rule

Do not treat the vault KSR page as the backlog source of truth.

It is a recent, useful task extraction from 2026-04-29 research. It is strongest for naming Knowledge State Runtime gaps, but it does not contain all previously shipped work, older product bets, or the April 22 compiler roadmap. Roadmap decisions should therefore merge four inputs:

- shipped/history record from repo milestone docs
- future architecture from the April 22 trusted-reuse/compiler roadmap
- recent KSR task extraction from the dogfooding vault
- reader-first product-shape research from LearnBuffett comparison

When these disagree, prefer the merged roadmap here for implementation sequencing, and preserve the original source notes as evidence rather than rewriting history.

## Product Thesis

OVP is moving from a document-processing pipeline into:

> **A reader-first, evidence-backed knowledge atlas over an auditable knowledge state runtime.**

This combines two threads that were previously split:

- **Knowledge State Runtime (KSR):** source -> observation -> claim -> evidence -> validity -> projection -> permission.
- **Reader Product Shape:** people, concepts, companies, topics, graph maps, backlinks, and evidence rails that make compiled knowledge understandable before exposing operator internals.

The internal architecture remains Capture -> Compile -> Reuse:

- **Capture:** source lifecycle, raw materials, clippings, papers, GitHub repos, web pages.
- **Compile:** deep dives, candidates, claims, evidence, relations, contradictions, graph rows.
- **Reuse:** reader atlas, object pages, graph, briefing, search, context packs, writing prompts, operational routines.

## Current State

Completed or substantially implemented:

- pack-aware pipeline and `research-tech` reference pack
- source lifecycle and absorb path hardening
- `knowledge.db` as rebuildable derived truth/query store
- truth API, object browsing, signals, actions, candidates, contradictions
- action queue and focused worker surface
- semantic relation contract boundary
- local `ovp-ui` operator shell

Still messy:

- README and milestone docs still emphasize historical six-layer/operator terms.
- recent KSR task extraction lives in the vault, while repo docs have older phase roadmaps.
- The UI starts as an operator dashboard instead of a reader-facing knowledge product.
- Existing object and graph pages expose internal data structures before explaining knowledge.

## Roadmap Milestones

### M0. Pipeline And Pack Foundation

**Status:** Complete

What it means:

- installable CLI
- vault layout resolution
- source lifecycle
- pack/profile runtime
- `research-tech` and `default-knowledge`
- `knowledge.db` rebuild path

Representative completed work:

- `ovp --full`, `ovp --incremental`, `ovp-absorb`
- pack API and doctor/export/truth commands
- source lifecycle idempotency first slice, corresponding to `KSR-013`

### M1. Operator Workbench And Review Runtime

**Status:** Complete enough / maintain

What it means:

- truth/object browsing
- review queues and candidate lifecycle
- signals/actions/briefing
- contradictions/stale summaries
- action queue and action worker
- run ledger and runtime observability baseline

This is valuable, but it should become an `/ops` surface rather than the product homepage.

### M2. Roadmap And README Consolidation

**Status:** Active now

Goal:

- merge repo roadmap, older phase history, recent vault KSR backlog, and reader-product research into one understandable route
- make README describe the current product direction
- mark old milestone docs as historical foundations
- define the next implementation wave without creating another competing backlog

Deliverables:

- consolidated roadmap doc
- README refresh
- master milestone doc pointer to this roadmap
- KSR/Reader/history mapping in `task_plan.md`

### M3. Reader-First Knowledge Atlas

**Status:** Next product wave

Related KSR tasks:

- `KSR-002 Projection 标注`
- `KSR-015 Dashboard/search hot-path audit`
- `KSR-026 Workflow wiring eval suite`
- Reader-product note: `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

Goal:

- `/` becomes a reader-facing Knowledge Atlas.
- Current runtime dashboard moves to `/ops`.
- Objects become readable pages, not database rows.
- `/graph` becomes a spatial corpus map, not only a cluster/debug report.

First PR slice:

- add `/ops` preserving current dashboard
- make `/` render a reader home from existing payloads
- group navigation into Read / Understand / Maintain
- add tests for root and `/ops`
- do not add new data models yet

Second PR slice:

- kind-aware object pages for person, concept, company/tool/project, event, claim
- backlink/mention rail with excerpts and source jumps
- raw audit/provenance kept secondary and expandable

Third PR slice:

- visual `/graph` MVP using current graph/truth data
- type legend, bounded node count, search/filter, click side panel
- keep analytical clusters available as an ops/debug route

### M4. KSR Safety And Hot-Path Hardening

**Status:** Near-term engineering wave

Related KSR tasks:

- `KSR-001 Evidence span 化`
- `KSR-003 Candidate 风险分层`
- `KSR-014 Article routing preview`
- `KSR-015 Dashboard/search hot-path audit`
- `KSR-018 Markdown-aware evidence chunking`
- `KSR-026 Workflow wiring eval suite`

Goal:

- every important claim/candidate can point to source path, content hash, heading/paragraph anchor, quote hash, line span, and relation/evidence type
- dashboard/search never trigger heavy raw/PDF/Office scans on default paths
- candidate review is grouped by risk and evidence strength
- routing decisions are previewed/explained before changing lifecycle behavior
- no-LLM wiring evals lock critical invariants

First likely implementation order:

1. hot-path audit for dashboard/search
2. wiring eval suite for source lifecycle, projection marking, promote gate, hot path, read/write boundaries
3. article routing preview payload
4. evidence span schema and markdown-aware locator backfill
5. candidate risk grouping

### M5. Context Pack And Operational Runtime

**Status:** Next after M4, some pieces can be pulled forward if needed

Related KSR tasks:

- `KSR-004 Session snapshot/context pack`
- `KSR-017 Explicit context budget`
- `KSR-020 Operational runtime graph`
- `KSR-021 Claim lease for workflow items`
- `KSR-022 OVP prime/context pack`
- `KSR-023 Pipeline observability metrics`
- `KSR-025 Context provider facade`
- `KSR-027 Live-source routing policy`

Goal:

- agent sessions can load a small, explicit, versioned OVP context pack
- source/candidate/pipeline tasks expose ready/blocked/claimed/closed/superseded states
- workflow items can be claimed/leased to avoid duplicate concurrent processing
- context providers expose stable `query_*` / `update_*` facades instead of raw tool sprawl
- pipeline/LLM/dashboard operations produce useful duration, retry, cost, and queue metrics

### M6. Policy, Permission, And Knowledge Evolution

**Status:** Later, after M3/M4 clarify the user-facing product and safety invariants

Related KSR tasks:

- `KSR-005 Permission layer 分离`
- `KSR-006 Claim lifecycle 字段`
- `KSR-007 Conflict detection 常规化`
- `KSR-008 High-risk user profile gate`
- `KSR-024 Candidate compaction with restore`
- `KSR-028 Schema-on-demand guard`
- older roadmap: Phase 32 Trusted Reuse, Phase 33 Evidence v2, Phase 34 Policy Promotion

Goal:

- distinguish ordinary memory writes from fact-authority writes
- support claim validity windows, supersession, contradiction state, and confidence decay
- keep high-risk user/profile claims out of automatic canonical paths
- compact low-quality stale candidates without losing restore ability
- require schema/review registration for new claim types and candidate profiles

### M7. Semantic Extraction And Query Feedback Loop

**Status:** Later

Related tasks:

- older roadmap: Phase 35 Reviewed Semantic Extractor
- older roadmap: Phase 36 Query Feedback Loop
- `KSR-010 Skill/routine extraction profile`
- `KSR-011 Notebook/raw-source mode`
- `KSR-012 Ingest ROI metrics`
- `KSR-016 Hybrid retrieval experiment`
- `KSR-019 Multimodal caption-first ingest`
- `KSR-029 Follow-up object model`

Goal:

- relation extraction produces candidates only, with evidence
- query can emit reuse events, open questions, writing prompts, candidate concepts, and proposed relations
- action-oriented sources can produce checklists, routines, playbooks, and review prompts
- low-risk transient sources can stay in notebook/raw-source mode instead of always becoming high-cost wiki/evergreen projections

## Current P0 Backlog

The current P0 set is:

| ID | Task | Roadmap milestone | Notes |
| --- | --- | --- | --- |
| KSR-013 | Source lifecycle idempotency | M0/M4 | First implementation merged in PR #72; vault backlog should be marked complete/reviewed |
| KSR-002 | Projection marking | M3/M4 | Must apply to dashboard, MOC, wiki, briefing, reader atlas |
| KSR-015 | Dashboard/search hot-path audit | M3/M4 | Needed before making UI the default product surface |
| KSR-026 | Workflow wiring eval suite | M4 | Prevents regressions in lifecycle, promote gate, projection marking, hot paths, write boundaries |
| KSR-014 | Article routing preview | M4 | Preview/evaluate only at first; do not route sources invisibly |
| KSR-001 | Evidence span | M4 | Foundation for reader trust and future policy promotion |
| KSR-003 | Candidate risk layering | M4 | Needed to keep review workload small |

Reader-first Knowledge Atlas is product P0, but it should be implemented as a projection layer over the same runtime facts, not as a separate state system.

## Documentation Rules Going Forward

- `OVP-Knowledge-State-Runtime.md` in the vault records the current KSR task extraction, but it is a recent backlog input, not a complete history or source of truth.
- This repo roadmap owns milestone grouping and implementation sequence after reconciling old milestone docs, April 22 roadmap, KSR, and reader-product research.
- README should explain product direction and runnable commands, not duplicate every KSR row.
- Old phase docs remain historical records unless explicitly referenced by the current roadmap.
- Any new research note should link to the vault KSR project if it adds KSR tasks.
- Any new implementation plan should cite the KSR IDs it advances.

## Immediate Next Step

After this docs consolidation, the next implementation PR should be either:

1. **Reader-first Knowledge Atlas shell**
   - `/ops` for current dashboard
   - `/` for reader home
   - no new data model

or:

2. **KSR hot-path/wiring safety**
   - dashboard/search hot-path audit
   - wiring eval suite
   - projection marking tests

The better sequence is likely:

1. reader shell route split (`/` vs `/ops`)
2. hot-path/wiring evals
3. kind-aware object pages and backlink rail
4. visual graph MVP
