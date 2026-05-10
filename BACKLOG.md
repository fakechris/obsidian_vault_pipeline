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
| M8 Type Unification And Extraction Quality | Active | unified object kind taxonomy, Canonical-State `entity_type` frontmatter, body-size-aware extraction, quote-grounding, single-pass LLM refactor |
| M9 Pack As Domain Ontology | Next | pack-defined object kind specs, typed relation constraints, schema registry, domain-specific extraction profiles |
| M10 Operational Knowledge Layer | Later | action types on objects, permission + contract, cross-entity aggregation, decision memory |
| M11 Source Authority And Cross-Source Identity | Done | typed source-authority providers, entity layer, runtime resolver, refresh wrapper, db backup (PRs #112–#124) |
| M12 Extraction-Time Entity Prime And Auto-Wikilink | Done | entity_aliases view, LLM extractor primed with known entities, auto-wikilink CLI (BL-038/039/040, PRs #126–#128) |
| M13 Synthesis Layer (Crystal) | Done | Louvain communities + LLM-synthesized community crystals + contradiction crystals + append-only versioning + sampling-aware renderer (BL-041/042/043/044, PRs #130-#136) |
| M14 Curated Atlas (Crystal Read Model) | Done | `crystal_scores` Projection (#142) + Curated Atlas markdown export (#143) + page_fts indexing of crystal bodies (#144) + big-community splitter inside `_detect_communities` (#145) + reuse-feedback wired into BL-045 scoring + `/atlas/curated` HTTP route + `/api/atlas/curated` JSON endpoint (BL-045/046/047/048/049). Tag + entity facets deferred to a small follow-up. See `docs/plans/2026-05-04-m14-curated-atlas.md` |
| M15 Architecture Language Cleanup | Done | Six-term architecture contract (Source / Candidate / Canonical State / Projection / Access Surface / Governance), ARCHITECTURE.md rewrite, doc split into RUNTIME / PACKS / PRODUCT_SURFACES / GLOSSARY, doc lint with phased rollout (PR #139) |
| M16 Reader / Maintainer Surface Split | Active | Hard-split the UI shell into a Reader product (`/`, `/search`, `/topics`, `/object`, `/note`, `/topic`, `/map`, `/explore`) and a Maintainer product (`/ops/...`).  URL prefix decides shell; old top-level maintainer paths 301 to `/ops/<same>`.  BL-050 split the routes; BL-051 unified Reader vocabulary (Topic / Featured Topics / open question), folded the redundant Curated Atlas card into Top Topics, and capped the visual map.  BL-052 (audit-only, no code) covers Maintainer-side vocabulary chaos. |
| M17 Intake Hardening (BL-058) | Done (v0.13.0) | URL preservation through deep-dive (C1), deprecate legacy 13-section LLM rewrite as default (C2 — `skip_deep_dive=True`), `ovp-dedup-cleanup` CLI for existing dups, **global URL dedup across the active staging chain** (Clippings + 02-Pinboard + 01-Raw + 02-Processing + 03-Processed) wired at every intake site with downstream-first priority; `source_dedup_skipped` audit events carry `stage`; `ovp-fidelity-sample` + `ovp-prompt-ab` measurement CLIs for the absorb v2 review (PRs #170, #171, #172). |
| M18 Trust-Aware Compiler Hardening | Next | Lock the fidelity foundations before scaling Interpretation features. Three locks: (a) **single-writer invariant** for canonical fields — every `objects` / `provenance` / `claims` / `relations` column has exactly one owner module (BL-060); (b) **prose-level revision history** for evergreens so any LLM rewrite is reviewable + rollback-able (BL-061); (c) **two-pass absorb routing** — replace the single big LLM call with a cheap router (decides update-or-create against existing evergreens) + per-target focused extractors, moving dedup from after-the-fact to write-time (BL-062). Depends on no other milestone. Origin: 2026-05-09 strategy review against Arkon + Rowboat. |
| M19 Live Concept Interpretation Surface | Later | Give the **Interpretation ledger** (the missing fourth ledger) a first-class surface. User-declared `30-Projects/Tracking/<topic>.md` files with `type: live-concept` frontmatter — agent maintains the synthesis sections from scope_evergreens, user owns `## My take`, contradictions detected against the user's stated view (not globally). Depends on M18 landing first (single-writer + revisions enable agent-only patches; two-pass routing improves scope-evergreen quality). Origin: 2026-05-09 strategy review. |

## Recently Shipped (PRs #98–#139)

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
| #129 | M12 review-pass: path-traversal slug allowlist on canonical_handle; CJK alias boundary fix (ASCII-only boundary for non-ASCII aliases); collision-logging fires regardless of winner; M12 docs sync |
| #130 | Louvain community detection (BL-041, M13): replaces connected-component clustering in `truth_projection.py`; `cluster_kind="louvain_community"`; deterministic seed |
| #131 | Community Crystal MVP (BL-042, M13): `ovp-synthesize-community-crystals` + `community_crystals` table; MiniMax-M2.7-highspeed default; append-only PK |
| #132 | Contradiction crystals (BL-043, M13): `ovp-synthesize-contradiction-crystals` + `contradiction_crystals` table; deliberately preserves tension as "open question" crystal; resolved contradictions skipped |
| #133 | Crystal append-only versioning (BL-044, M13): `superseded_by_synthesized_at` on both crystal tables; archive helper moves prior live markdown to `70-Archive/Crystals/<safe-id>/<ts>.md`; `ovp-list-crystals` surfaces version chains |

## Active Backlog

| ID | Priority | Status | Work item | Source links |
| --- | --- | --- | --- | --- |
| BL-025 | P0 | Done (Phase 1) | Unified Object Kind Taxonomy.  The "three type systems" (concept_registry.kind / truth_store.object_kind / view_models labels) were already collapsed into `object_kinds.py` pre-BL-025; this PR fixes the remaining gap — `auto_evergreen_extractor` was binary-collapsing v2's 10 unit-type values into `method` or `concept` for `entity_type`, producing 89% `entity_type: concept` on the live vault.  Added `V2_UNIT_TYPES` frozenset + labels + identity mapping in extractor and promote.  Reader filters and pack `object_kind_specs()` (M9) can now actually discriminate. | M8 |
| BL-026 | P0 | Done (Phase 1) | Extraction pipeline entity_type output.  v2 absorb already produced `unit_type`; the conflated `entity_type` now matches it (each of fact / method / procedure / tradeoff / failure_mode / counterexample / case_detail / learning / decision / quote is a valid `entity_type`).  Promote (`write_evergreen_file`) carries the mapping forward.  Out of scope: historical backfill (BL-030) and Reader type-facet UI (separate UX). | M8 |
| BL-027 | P1 | Active | Body-size-aware extraction (P3): auto_evergreen_extractor prompt includes article body length and bounded target count | M8, OVP_FIX_PLAN P3 |
| BL-028 | P1 | Active | Quote-grounding requirement (P4): promote rejects candidates without source-grounded quotes | M8, OVP_FIX_PLAN P4 |
| BL-029 | P2 | Done + UX-cleanup landed (PR #182, PR #183) | Deep-dive layer deletion — `auto_article_processor` is now intake-only (image download + frontmatter parse + lifecycle archive).  Removed: `class ArticleProcessor` (6-dim LLM rewrite + classify_article + create_embedded_evergreens), `LiteLLMClient` (article copy), `init_llm`, `_resolve_article_links`, `_write_resolution_sidecar`, `_upsert_candidates`, `_augment_frontmatter`, `_prepare_interpretation_source`, `_clean_body_text`, `_has_substantive_content`, `_looks_like_paper_source`, `_extract_html_text`, `_fetch_url_text`, `_fetch_docs_fallback_text`, `--keep-deep-dive` CLI flag, `skip_deep_dive` constructor param.  ~600 lines removed.  PR #182 swept the remaining UX vestiges across truth_api / view_models / renderers / packs (119 references in 31 files) and added pagination + D3 cluster viz; PR #183 rewrote the `/object` provenance card around the post-BL-029 chain (Source URL → Source File → Pipeline Stages → Evergreen).  Page IA documented in `docs/page-ia-post-bl029.md`. | M8 |
| BL-030 | P1 | Done (Phase 1) | Historical Evergreen entity_type backfill.  Two-phase design — Phase 1 deterministic ``unit_type → entity_type`` rewrite for v2 evergreens whose ``entity_type`` was set by the pre-BL-025 collapse (zero LLM cost), Phase 2 LLM classification for v1 evergreens with no ``unit_type``.  Live run rewrote 1841 v2 evergreens in 0.7s; ``entity_type: concept`` share dropped from 89% → 72.5%.  Phase 2 (re-classify the remaining 6848 v1 ``concept``-typed evergreens via LLM) deferred to a separate run since it costs ~$30-50 + several hours. | M8 |
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
| BL-041 | P1 | Done | Louvain community detection replaces the connected-component clustering in `graph_clusters`.  Communities are now the default product output of the truth projection; `cluster_kind="louvain_community"` (internal label).  Wired into `_build_graph_seeds`, no separate CLI — runs on every `rebuild_knowledge_index`. | M13 |
| BL-042 | P0 | Done | Community Crystal MVP — `ovp-synthesize-community-crystals` reads Louvain communities from `graph_clusters`, sends top-K member evergreens (deterministic order; authority weighting deferred to v2) to MiniMax-M2.7-highspeed, writes `40-Resources/Crystals/<sha>.md` and persists lineage in the new `community_crystals` table.  Append-only schema (PK includes `synthesized_at`) so BL-044 versioning lands without migration. | M13 |
| BL-043 | P1 | Done | `ovp-synthesize-contradiction-crystals` reads `status='open'` rows from the `contradictions` table, sends each side's claim_text + source evergreen body to the LLM, and writes `40-Resources/Crystals/contradiction-<sha>.md` plus a `contradiction_crystals` lineage row.  System prompt is deliberately framed as "open question" — does not try to resolve, only to expose the tension.  Resolved contradictions are skipped. | M13 |
| BL-044 | P1 | Done | Crystal append-only versioning — both `community_crystals` and `contradiction_crystals` carry `superseded_by_synthesized_at`; supersede helper flips the prior current row's pointer + archives its markdown to `70-Archive/Crystals/<safe-id>/<timestamp>.md` before the new row lands.  `ovp-list-crystals` surfaces the version chain. | M13 |
| BL-045 | P0 | Done | `crystal_scores` Projection — per-crystal weighted score (size_norm + credibility_norm + contradiction_norm + reuse_recency_norm + evergreen_recency_norm) persisted alongside its component signals.  Auto-rebuilt by `ovp-knowledge-index`; standalone `ovp-rescore-crystals` for ad-hoc re-scoring.  Schema-version 5 → 6.  reuse_recency_norm is a forward-compat placeholder (always 0 until BL-049 lands). | M14 |
| BL-046 | P0 | Done | Curated Atlas Access Surface — markdown export at `40-Resources/CuratedAtlas.md` (PR #143) plus the `/atlas/curated` HTTP route + `/api/atlas/curated` JSON twin.  Top-N (default 30, max 100) crystals ranked by `crystal_scores`; each entry carries label, teaser, score breakdown, and a click-through to the underlying crystal markdown.  `/atlas` carries a back-link to the curated view.  Pure read against the DB — no LLM cost, no vault writes. | M14 |
| BL-047 | P1 | Done (FTS slice) | `page_fts` extended to index every current crystal body so `/search` returns crystals alongside evergreen pages.  Synthetic slugs `crystal:<safe-id>` / `contradiction:<safe-id>` distinguish the kinds.  Tag + entity facets deferred to a small follow-up — current crystals share uniform tags so the tag-facet has no signal yet, and the entity facet is ~3× the implementation cost. | M14 |
| BL-048 | P1 | Done | Big-community splitter integrated into `_detect_communities` itself.  Communities above `_SPLIT_THRESHOLD` (50 members) trigger a second Louvain pass on the sub-graph induced by their members; each sub-community becomes its own `graph_clusters` row + downstream community crystal.  Best-effort: communities with no internal edges or that re-merge into a single sub-community are kept whole.  Naturally idempotent on every projection rebuild. | M14 |
| BL-049 | P2 | Done | Reuse-feedback loop wired: BL-045's `reuse_recency_norm` now reads `reuse_events` rows tagged `object_kind IN ('community_crystal','contradiction_crystal')` over a rolling 30-day window, normalized by per-pack max.  Cold start (no events) keeps the signal at 0 — same as the BL-045 v0 placeholder.  As surfaces emit crystal-tagged events, the signal lifts naturally; no further code change required to scale. | M14 |
| BL-050 | P0 | Done | Reader / Maintainer hard split — URL prefix decides shell, Reader home rewritten around `crystal_scores` + Curated Atlas + Recent Crystals, 27 maintainer routes relocated under `/ops/*` with 301 back-compat, `?mode=operator` toggle removed, single cross-link in each shell footer.  See `docs/plans/2026-05-04-bl-050-reader-maintainer-split.md` (PR #150) | M16 |
| BL-051 | P0 | Done | Reader vocabulary unification + map cleanup — `/atlas/curated` → `/topics` (301 back-compat), page title `Curated Atlas` → `Featured Topics`, FTS prefix `[crystal]` → `[topic]` and `[contradiction]` → `[open question]`, Reader nav `Atlas` link → `Topics`, home drops the redundant Curated Atlas card and adds `See all N featured topics →` link inside Top Topics, `Recent Crystals` → `Recent Topics`, `/map` capped at 3 members per cluster with `?show_all=1` escape hatch + hover-only labels.  See `docs/plans/2026-05-04-bl-051-vocabulary-and-map-cleanup.md` (PR #151) | M16 |
| BL-052 | P1 | Done (audit) | Maintainer Vocabulary Audit — matrix doc enumerates every `/ops/*` surface (URL → renderer → view-model → backing entity → overlap class R/C/N/K → action).  Surfaces: 1 R-class duplicate (`Audit` nav label vs `/ops/events`), 4 C-class concept overlaps (Candidates/Evolution Candidates, Contradictions/Open Questions, Production/Evolution), 1 N-class mis-naming, 12 K-class.  See `docs/plans/2026-05-04-bl-052-maintainer-vocab-matrix.md`.  Implementation backlog → BL-053. | M16 |
| BL-053 | P1 | Done (Phase 2) | Ops 工作台 IA 重构 + 词汇清理.  **Phase 1**: by-time pivots ahead of the toolbox — new `/ops/today` (5-card daily digest), new `/ops/runs` index + `/ops/runs/<txn_id>`, nav rebuild (`Audit`→`Events`, `Candidates`→`Concept Candidates`).  **Phase 2 (PR #179)**: drop `/ops/deep-dives` (BL-029 producer gone); pulse navless fix; `/ops/objects` pagination + alpha/most-linked sort; `/ops/clusters` total + per-page + Show all; `/ops/events` date filter; `/ops/today` prev/next pivots + per-card "See all"; `/ops/timeline` day-card drill-down; `/ops/runs` by-day grouping + Idle markers + window pivot; queue consolidation under `/ops/queue/*` with 301s for legacy paths; `/ops` foyer (today/queue/last-run); page-help banners on every `/ops/*` surface; `docs/maintainer-workflow.md`. | M16 |
| BL-054 | P0 | Done | Crystal scoring substrate fix — `source_authority` schema wired into rebuild + JSONL replay; `objects.source_url` column populated from frontmatter; `ovp-backfill-provenance` CLI achieves 99.9% coverage on the live vault; `auto_evergreen_extractor` writes source provenance forward; `_credibility_sum` dedupes by source URL; new `source_diversity_norm` ratio signal; weight rebalance.  See `docs/plans/2026-05-04-bl-054-crystal-scoring-substrate-fix.md` (PR #152) | M14 |
| BL-055 | P0 | Done | Provenance spine — first-class `provenance` table with append-only PK `(pack, object_id, stage, derived_at)`; rebuild populates `stage='ingest'` rows from frontmatter; preserved across rebuilds; `ovp-doctor` reports provenance health.  See `docs/plans/2026-05-04-bl-055-provenance-spine.md` (PR #152) | M14 |
| BL-056 | P0 | Done | Stage emit hooks — every stage that creates / modifies a Canonical-State object writes a provenance row beyond the rebuild's ingest baseline.  Wired: `synthesize_community_crystal`, `synthesize_contradiction_crystal`, `promote` (concept review), and now `extract` (backdated to the candidate's ``absorbed_at`` so chain timestamps reflect when ``auto_evergreen_extractor`` produced the candidate, not when the human reviewed it).  Shared helper at `src/ovp_pipeline/provenance.py`.  See `docs/plans/2026-05-04-bl-056-stage-emit-hooks.md` | M14 |
| BL-057 | P2 | Next | Provenance lint enforcement — extend `ovp-doctor` to fail (not warn) when orphan_objects grows unexpectedly; `ovp-lint --provenance` rule that any newly-promoted evergreen without a `stage='promote'` row is an error.  Depends on BL-056. | M14 |
| BL-046b | P2 | Next | Crystal **tag facet** for `/search` — group results by crystal `tags` frontmatter.  Deferred from BL-047. | M14 |
| BL-047b | P2 | Next | Crystal **entity facet** for `/search` — join through `entity_aliases` to filter crystals by mentioned entity.  Deferred from BL-047. | M14 |
| BL-049b | P2 | Next | Surface-side `reuse_events` emission — Reader clicks on `/atlas/curated`, `/note?path=…/Crystals/...`, and FTS crystal hits write a `reuse_events` row tagged `object_kind='community_crystal'` so `reuse_recency_norm` lifts above zero.  Deferred from BL-049. | M14 |
| BL-060 | P0 | Next | **Single-writer invariant for canonical fields** — every column in `objects` / `provenance` / `claims` / `relations` gets exactly one **owner module** that's allowed to write it; other modules call the owner's helper instead of writing directly.  Origin: PR #185 root cause was three modules (frontmatter rebuild, `ovp-backfill-provenance`, `ovp-backfill-objects-source-url`) writing `objects.source_url` independently → reconciliation race.  Scope: (a) audit current write sites for each canonical column; (b) define owner-module map in `docs/canonical-write-ownership.md`; (c) refactor violations to call owners; (d) add `tests/test_architecture_fitness.py` rule that prevents new direct writes.  Out of scope: locking inside owners (already covered by `withFileLock` / `knowledge_db_write_lock`).  Sized: 2 PRs, ~1500–2000 LOC. | M18 |
| BL-061 | P1 | Next | **Prose-level evergreen revision history** — new `evergreen_revisions(pack, object_id, version, content_md, change_type, changed_by, derived_at, change_note)` table, immutable append-only.  Write hooks at each owner module from BL-060 covering stages: `extract`, `promote`, `editor_edit`, `llm_rewrite`, `rollback`.  Mirrors Arkon's `WikiPageRevision` model.  Enables: "what did this evergreen say before absorb v2 rewrote it" + per-revision diff in `/object?id=…&tab=history` + `ovp-rollback-evergreen <slug> <version>` CLI.  This is the load-bearing table for fidelity audit work.  Depends on BL-060 (so each owner module owns its own revision write).  Sized: 1 PR, ~600–1000 LOC. | M18 |
| BL-062 | P0 | Next | **Two-pass absorb routing** — decompose `auto_evergreen_extractor`'s single big LLM call into:  (Pass 1) cheap structured-output router that reads source + a pre-built **evergreen index** (slug + summary + key claims, compact) and emits `{updates: [{slug, evidence_segments}], creates: [{title, rationale}]}`;  (Pass 2) per-target focused extractor that update-or-create against the routed target with full context of which other evergreens are in scope (so wikilinks resolve to real slugs, not fabricated ones).  Primary win is **quality, not cost**: write-time dedup replaces after-the-fact `concept_dedup` → all source_anchors accumulate on the same evergreen instead of being lost in dedup-merge → cross-source consistency for repeated concepts → wikilinks resolve to existing slugs.  Measurable success: source_anchor count per evergreen up; new-evergreen-with-similar-existing rate down; concept_dedup archive volume down.  Depends on BL-060 (writes go through cleaner owner module).  Retire `concept_dedup` as primary path post-shipping (keep as belt-and-suspenders).  Sized: 1 PR, ~1000–1500 LOC. | M18 |
| BL-063 | P1 | Next | **Live Concept primitive (精选派 subset)** — declarative `30-Projects/Tracking/<slug>.md` files with `type: live-concept` frontmatter carrying `objective`, `scope_evergreens` (list of evergreen slugs this concept tracks), and `triggers` (`on_ingest_match` / `on_contradiction_against_view` / `weekly_resynthesis`).  Body has user-owned sections (`## My take`) and agent-owned sections (`## Current synthesis` / `## Recent evidence` / `## Tensions`); section ownership enforced via HTML-comment markers.  Agent uses Rowboat-style **patch-style edits** restricted to agent-owned sections — single-writer invariant from BL-060 applies at section granularity.  Gives the **Interpretation ledger** (the missing 4th ledger of the Artifact/Claim/Interpretation/Commitment model) its first-class surface.  *Excludes:* narrow firehose (BL-059), automatic scope expansion, multi-objective per concept.  Depends on BL-060/061/062 landing first.  Sized: 3 PRs (data model + scheduler; 3 trigger implementations; agent prompt + section-aware edits), ~2500 LOC total. | M19 |
| BL-059 | P3 | Later | **Narrow firehose for Live Concepts** — declarative RSS / Twitter-list / keyword pull *bound to a specific Live Concept*, populating only that concept's `## Inbox` region (NOT the main truth store).  User does promote-or-discard decision per item; only promoted items run through absorb v2 → evergreen.  Keeps human attention curation as the canonical signal while solving the精选派盲区 ("you don't know what you don't know" within a declared topic).  Defer until BL-063 ships and we have actual Live Concept usage data — premature without that.  See 2026-05-09 strategy review for the精选派 vs 广撒网 framing. | M19+ |

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

Unify the three existing type systems (`concept_registry.kind`, `truth_store.object_kind`, `view_models._OBJECT_KIND_LABELS`) into one canonical taxonomy. Add `entity_type` to Canonical State frontmatter so vault markdown remains the trust boundary for typing. Complete P3 (body-size-aware extraction), P4 (quote-grounding), and P5 (single-pass LLM refactor).

Key insight: OVP already has typed objects in Projections (truth_store), typed relations (research_tech semantic_relations), and kind-specific UI (reader profiles). The gap is Canonical State frontmatter and extraction pipeline output.

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
