# OVP Active Backlog

**Updated:** 2026-05-03
**Status:** Active implementation backlog source

This file is the single current backlog entry point for implementation sequencing. Completed items are archived in [docs/backlog-archive.md](docs/backlog-archive.md).

It is not the only evidence source. It is the maintained merge view over:

- repo milestone history and shipped phase docs
- `docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md`
- recent KSR task extraction in `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`
- kg-eval quality assessment and `OVP_FIX_PLAN.md` (2026-05-01)

Rule: historical plans and vault research notes feed this file; they do not override it silently. When a PR lands or a KSR task changes state, update this file first, then mirror or annotate secondary docs as needed.

## Current Milestones

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0–M3 | Done | Foundation, operator workbench, roadmap consolidation, reader-first atlas (see [archive](docs/backlog-archive.md)) |
| M4 KSR Safety And Hot-Path Hardening | Done | projection labels, hot-path audit, wiring evals, article routing preview, evidence spans, candidate risk, JSONL streaming, projection lifecycle hardening, runtime-state API fixes (PR #98, #99, #100) |
| M5 Context Pack And Operational Runtime | Done | session snapshots, context budget, runtime state in `/ops` and doctor, provider-facing runtime-state API, action queue health |
| M5a Quality And Dedup Hardening | Done | concept dedup pipeline integration, promote semantic guard, historical data cleanup (PR #101) |
| M5b Slug-Level Dedup Apply | Done | concept_dedup apply at threshold 0.75 (non-default override; code default=0.82): 297 clusters, 334 duplicates archived to 70-Archive/dedup-merged/, vault reduced 7020→6686 Evergreens |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer, claim lifecycle, conflict detection, policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor, query feedback, routines, notebook/raw-source mode |
| M8 Type Unification And Extraction Quality | Active | unified object kind taxonomy, Layer 1 entity_type, body-size-aware extraction, quote-grounding, single-pass LLM refactor |
| M9 Pack As Domain Ontology | Next | pack-defined object kind specs, typed relation constraints, schema registry, domain-specific extraction profiles |
| M10 Operational Knowledge Layer | Later | action types on objects, permission + contract, cross-entity aggregation, decision memory |
| M11 Source Authority And Cross-Source Identity | Done | typed source-authority providers, entity layer, runtime resolver, refresh wrapper, db backup (PRs #112–#124) |
| M12 Extraction-Time Entity Prime And Auto-Wikilink | Done | entity_aliases view, LLM extractor primed with known entities, auto-wikilink CLI (BL-038/039/040, PRs #126–#128) |
| M13 Synthesis Layer (Crystal) | Next | Louvain communities + LLM-synthesized crystals + contradiction crystals + append-only versioning (BL-041/042/043/044) |

## Recently Shipped (PRs #98–#128)

| PR | What shipped |
| --- | --- |
| #98 | P0+P1 bug fixes from review: JSONL streaming, advisory file lock, runtime-state API JSON 400, reader UI resilience |
| #99 | Scoped incremental quality checks |
| #100 | Four-phase architecture refactor: JSONL read/write streaming, truth_api module boundary cleanup, ui_server route hardening (CSP/CSRF), projection lifecycle markers |
| #101 | P0-P2 from OVP_FIX_PLAN: concept_dedup pipeline integration with scope_slugs, promote semantic guard (trigram-Jaccard), historical Evergreen data cleanup (71→61), find_similar_slugs utility |
| #109 | Typed StepResult contracts — eliminate silent step-to-step fallbacks |
| #110 | Missing llm_client.py + dedup backfill against extraction log |
| #111 | Four pipeline guardrails — schema audit, kill silent imports, metrics, E2E |
| #112 | Liberate evergreen extractor prompt from 3-5 cap + null-escape (PR-A) |
| #113 | Source-authority subsystem PR-D1 + D2: domain rules, author whitelist, GitHub, arXiv |
| #114 | Source-authority discovery + LLM-judge + yaml overrides (PR-D3) — `ovp-source-coverage`, `ovp-score-domain`, `domain_overrides.yaml`, `author_overrides.yaml` |
| #115 | Twitter author entity layer + twitterapi.io backfill (PR-E1) — 521 entities, ~$0.10 |
| #117 | `ovp-backup-db` — point-in-time snapshots of knowledge.db via SQLite online backup API |
| #119 | GitHub project + user entity backfill (PR-E2) — 922 entities, $0; cross-platform `twitter_username` linkage harvested |
| #120 | Source-authority scorer reads entity table + identity merge (PR-E3) — 54 self-reported person merges auto-applied |
| #121 | GitHubSignalProvider entity-table fast path (PR-E4) — ingest-time scoring shortcuts to entity table, zero HTTP for backfilled repos |
| #122 | `ovp-refresh-source-authority` chained entity refresh + launchd plist (PR-E5) |
| #123 | `person` → `person + organization` split (PR-F1) — 54 → 37 person + 17 organization on real vault |
| #124 | 12 entity-layer review fixes: read-side write side effects, identity merge backlinks, lock-steal race, append-only history, excluded-host signal, GitHub bare profile URLs, source_coverage entity-aware unknowns, score_sources --domains-only honors overrides |
| #126 | `entity_aliases` view + `ovp-entity-aliases` CLI (BL-038, M12): unified read surface across authors.jsonl, author_overrides.yaml, entities table, github_user back-link |
| #127 | Extraction-time entity prime in `auto_evergreen_extractor` (BL-039, M12): top-N canonicals injected into LLM user prompt so name variants collapse to one handle |
| #128 | `ovp-link-entities` auto-wikilink CLI (BL-040, M12): scans evergreen prose, inserts `[[canonical_handle]]`, generates `10-Knowledge/Entity/<handle>.md` stubs; PreparedMatcher for batch reuse |

## Active Backlog

| ID | Priority | Status | Work item | Source links |
| --- | --- | --- | --- | --- |
| BL-025 | P0 | Active | Unified Object Kind Taxonomy: merge three type systems (concept_registry.kind, truth_store.object_kind, view_models labels) into one canonical set; add `entity_type` to Layer 1 frontmatter | M8 |
| BL-026 | P0 | Active | Extraction pipeline entity_type output: LLM prompt produces entity_type; promote writes to frontmatter + registry | M8 |
| BL-027 | P1 | Active | Body-size-aware extraction (P3): auto_evergreen_extractor prompt includes article body length and bounded target count | M8, OVP_FIX_PLAN P3 |
| BL-028 | P1 | Active | Quote-grounding requirement (P4): promote rejects candidates without source-grounded quotes | M8, OVP_FIX_PLAN P4 |
| BL-029 | P2 | Next | Single-pass LLM refactor (P5): merge link_resolution + promote into one LLM call; remove `_深度解读` companion files | M8, OVP_FIX_PLAN P5 |
| BL-030 | P1 | Next | Historical Evergreen entity_type backfill: one-time LLM batch to annotate existing Evergreens | M8 |
| BL-031 | P1 | Next | Pack object_kind_specs() API: BaseDomainPack defines object kinds with properties and reader layouts | M9 |
| BL-032 | P2 | Next | Pack typed relation constraints: relation types carry source/target kind constraints | M9 |
| BL-033 | P2 | Next | Schema registry and lint: `ovp-schema list`, `ovp-lint --schema` validates entity_type against pack schema | M9 |
| BL-034 | P2 | Next | Domain-specific extraction profiles: different packs produce different entity_type distributions | M9 |
| BL-015 | P1 | Later | Permission layer and claim lifecycle fields | M6, KSR-005, KSR-006 |
| BL-016 | P2 | Later | Conflict detection regularization, high-risk profile gate, candidate compaction with restore | M6, KSR-007, KSR-008, KSR-024 |
| BL-017 | P2 | Later | Schema-on-demand guard for new claim/candidate profiles | M6, KSR-028 |
| BL-018 | P2 | Later | Reviewed semantic relation extractor and query feedback loop | M7, April 22 roadmap |
| BL-019 | P2 | Later | Skill/routine extraction profile, notebook/raw-source mode, ingest ROI, hybrid retrieval, multimodal caption-first ingest, follow-up object model | M7, KSR-010, KSR-011, KSR-012, KSR-016, KSR-019, KSR-029 |
| BL-022 | P1 | Later | Decision context memory: first-class rationale, rejected alternatives, dissent, owner, participants, and validity windows for high-value decisions | M6/M7, KSR-030 |
| BL-023 | P1 | Later | Agent workspace substrate and information-health loop | M6/M7, KSR-031, KSR-032 |
| BL-024 | P1 | Later | Machine-facing memory substrate: typed scored memory records with freshness, evidence count, supersession, last-used telemetry, and token-budgeted selective injection | M6/M7, KSR-033 |
| BL-035 | P1 | Later | Action types on objects: typed operations (review, decide, verify) with preconditions, postconditions, audit trail | M10 |
| BL-036 | P2 | Later | Cross-entity aggregation: typed-mention index, property aggregation by kind | M10 |
| BL-037 | P1 | Next | Body-level semantic dedup: use page_embeddings cosine similarity to detect paraphrastic clones (same concept, different slug names); slug-trigram-Jaccard cannot reach these | M5b, OVP_FIX_PLAN |
| BL-038 | P0 | Done | entity_aliases view + `ovp-entity-aliases` CLI (PR #126). Single read surface for BL-039/040. | M12 |
| BL-039 | P0 | Done | Extraction-time entity prime in `auto_evergreen_extractor` (PR #127). Top-N entity_aliases injected into the LLM user prompt so name variants resolve to one canonical handle. | M12 |
| BL-040 | P1 | Done | `ovp-link-entities` auto-wikilink CLI (PR #128). Walks `10-Knowledge/Evergreen/`, replaces alias hits with `[[canonical_handle]]`, generates `10-Knowledge/Entity/<handle>.md` stubs. | M12 |
| BL-041 | P1 | Next | Louvain community detection over `relations` graph — replaces (or augments) the current 312 mechanical `graph_clusters` with semantically-meaningful communities. Closes the gap with NM 0.8's 41 communities. | M13 |
| BL-042 | P0 | Next | Crystal MVP — for each Louvain community: pick top-K evergreens by authority, LLM-synthesize a single crystal markdown (frontmatter + body) into `40-Resources/Crystals/<community>.md`, persist lineage in a `crystals` table (community_id, source_evergreen_slugs, synthesized_at, llm_model, prompt_version). MiniMax-M2.7-highspeed for cost. | M13 |
| BL-043 | P1 | Next | Contradiction crystals — for every cluster of evergreens flagged in the existing `contradictions` table, synthesize an "open question" crystal that lays out the X-vs-Y positions explicitly. Cheap LLM cost; very high signal. | M13 |
| BL-044 | P1 | Next | Crystal append-only versioning — when source evergreens change, generate a new crystal version; archive the old one to `70-Archive/Crystals/<community>/<timestamp>.md`; `crystals` table carries `superseded_by` pointer (mirrors NM 0.8's EVOLVES edge). | M13 |

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

## Evolution Strategy

OVP is evolving from a personal Zettelkasten into a typed knowledge platform capable of serving as a company second brain.

### Stage 1: Type Unification + Quality (M8)

Unify the three existing type systems (`concept_registry.kind`, `truth_store.object_kind`, `view_models._OBJECT_KIND_LABELS`) into one canonical taxonomy. Add `entity_type` to Layer 1 frontmatter so markdown remains the Authority for typing. Complete P3 (body-size-aware extraction), P4 (quote-grounding), and P5 (single-pass LLM refactor).

Key insight: OVP already has typed objects in Layer 2 (truth_store), typed relations (research_tech semantic_relations), and kind-specific UI (reader profiles). The gap is Layer 1 frontmatter and extraction pipeline output.

### Stage 2: Pack As Domain Ontology (M9)

Make packs the carrier of domain ontology. Each pack defines its own object kinds with properties, typed relation constraints, extraction profiles, and schema validation. This turns OVP from "one knowledge base" into "a platform that hosts domain-specific knowledge bases."

### Stage 3: Operational Knowledge Layer (M10, evaluate ROI after M9)

Add Palantir-style typed actions on objects, permission contracts, cross-entity aggregation, and decision memory. This is the "company second brain" operational layer. Evaluate need after Stage 2 ships.

### Stage 1.5: Source Authority + Cross-Source Identity (M11, Done)

Shipped through PRs #112–#124 in May 2026.  Three layers added on the read side without disturbing the existing pipeline:

* **Source authority providers (PR-D1/D2/D3)** — typed `SignalProvider` Protocol; deterministic `domain_rules` + `author_rules` whitelist, `github_stars` + `arxiv` + `twitter` (stub) live signals, soft never-gating combination rule, yaml-overrides + LLM-judge for the long tail.
* **Entity layer (PR-E1/E2/E3/E4/F1)** — `entities` + `entity_signals_history` SQLite tables holding `twitter_author`, `github_project`, `github_user`, `person`, `organization` rows.  twitterapi.io + GitHub REST backfills, identity merge with self-reported / exact-handle / fuzzy strategies, `person`/`organization` split driven by GitHub's `user.type`.  521 + 922 + 54 entities on the OVP vault, ~$0.10 one-shot.
* **Operational glue (PRs #117/#122)** — `ovp-backup-db` (SQLite online backup), `ovp-refresh-source-authority` (chained refresh wrapper, lock-protected, status JSON, launchd plist).

This is the foundation that lets M12 (extraction prime + auto-wikilink) close the loop from "we know who Karpathy is" to "the next ingest run uses that knowledge".

### Stage 4: Synthesis Layer / Crystal (M13)

OVP's L3 gap relative to NM 0.8.  NM has 240 LLM-synthesized crystals + 41 community summaries; OVP has only the mechanical 312-component `graph_clusters` and a single Atlas index page.  The plan:

1. Replace mechanical components with Louvain community detection on the relations graph (BL-041).
2. For each community, pick top-K evergreens by authority and LLM-synthesize a single markdown crystal into `40-Resources/Crystals/`; persist lineage in a `crystals` SQLite table (BL-042).
3. Add contradiction crystals using the existing `contradictions` table as seed (BL-043).
4. Append-only versioning so when source evergreens change, the new crystal version archives the old one (BL-044).

Cost target: ~¥0.001/crystal × 80-150 crystals = a few cents per full re-synthesis.  MiniMax-M2.7-highspeed.

## Next Decision

Two parallel execution lanes:

1. **M8 lane**: Execute BL-025 through BL-030.  Then evaluate M9 scope based on real pack adoption.
2. **M12 → M13 lane**: Execute BL-038 (entity_aliases) → BL-039 (extraction prime) → BL-040 (auto-wikilink), then BL-041 (Louvain) → BL-042 (Crystal MVP) → BL-043 (contradiction crystals) → BL-044 (versioning).

These two lanes don't conflict — M8 lives in the extraction prompt + frontmatter schema, M12/M13 live in the entity layer + synthesis surface.  Schedule M12 work whenever an M8 LLM call would benefit from primed entity context.
