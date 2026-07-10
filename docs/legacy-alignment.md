# Legacy OVP Alignment

> **⚠️ Superseded for the reader-trunk era (M29).** This matrix was written from the pre-pivot
> M7–M13 canonical-store roadmap and predates the M14–M28 reader-trunk + Crystal pivot, so its
> P0 framing (canonical store / absorb / knowledge.db as the next milestones) no longer reflects
> the active direction. For the current product-capability comparison and mainline-return verdict,
> read [`docs/mainline-return-matrix.md`](./mainline-return-matrix.md) and
> [`docs/stage-m29-mainline-return-audit.md`](./stage-m29-mainline-return-audit.md). The gap rows
> below remain accurate as a legacy *inventory*; only their roadmap priority is stale.

> Living gap matrix between the legacy Python `ovp_pipeline` and the Rust rewrite. Updated when stages land. See `docs/architecture.md` for the current Rust state; this doc is about what the legacy does that we don't yet match.

## How we use this doc

A gap here is a legacy capability we have committed to matching. If REDESIGN_REVIEW explicitly drops a behavior (e.g. the 13-section deep-dive for articles per BL-029/BL-066, or the subprocess shellout pattern), it goes in **Explicit non-goals**, not the matrix. Everything else is triaged into P0/P1/P2 by blast radius: P0 = the next end-to-end ingest cycle (one article + one github source + one paper, raw → Evergreen → MOC → knowledge.db) cannot finish without it; P1 = real legacy users will notice it missing within a week; P2 = needed eventually but gated on a P0/P1 landing. We update this doc whenever a stage closes (move row from a gap matrix to the coverage summary) or when a legacy survey turns up surface we didn't know about. Stage planning reads the P0 list to pick the next milestone; CEO/design review reads "Roadmap alignment" to decide if the locked order still holds.

## Coverage summary

| Dimension | Total surveyed | covered | partial | not_covered | out_of_scope | rust_equivalent_count |
|---|---|---|---|---|---|---|
| CLI commands | 30 (of 90 in pyproject) | 1 | 2 | 27 | 0 | 3 |
| Core modules | 13 | 0 | 6 | 7 | 0 | 6 |
| Ingest flow stages | 8 | 0 | 0 | 8 | 0 | 0 |

Notes:
- CLI "covered" = `ovp-graph` (Rust `graph` subcommand ships graph export). "Partial" = `ovp` (`run --fake`) and `ovp-article` (`interpret-article` single-source path).
- Core "partial" = the 6 modules whose Rust counterparts exist as scaffolding (GraphRunner, VaultFsPlanApplier, handler registration, CanonicalKey, ConceptResolver trait, RunId/StepId) but lack the legacy's full semantics.
- Ingest stages are uniformly `not_covered` because no end-to-end ingest exists in Rust yet — `interpret-article` covers part of L2 but does not feed L3 absorb.

## Gap matrix — P0 (blocks the next ingest cycle)

- **L1 article/github intake (`ovp-clippings` + `auto_article_processor` intake-only path + `auto_github_processor`)** — Rust has no Source filter that watches `50-Inbox/01-Raw/` or `50-Inbox/Clippings/`, normalizes filenames, downloads remote images, and lifecycles files into `50-Inbox/03-Processed/YYYY-MM/`. Without this nothing reaches the rest of the pipeline. Host in **ovp-domain** (Source + Transform filters) plus **ovp-stores** (filesystem move/lock primitives extending `VaultFsPlanApplier`). Dependencies: requires `VaultLayout` port (see core gap below) and image-download side service.
- **L2 paper deep-dive (`auto_paper_processor.generate_paper_analysis`)** — Papers are the only surviving LLM deep-dive producer post-BL-029. Rust `interpret-article` covers the article shape but not the paper shape (arXiv ID extraction, paper-specific prompt, token counting, write to `20-Areas/AI-Research/Papers/`). Host in **ovp-domain** as a `PaperInterpret` transform sharing `LLMInvoker` with article. Depends on: prompt asset + cassette fixture for paper.
- **L3 absorb (`run_absorb_workflow` + `absorb_router` + `auto_evergreen_extractor` + `promote_candidates`)** — The L2 → L3 evergreen lifecycle. No Rust equivalent exists. This is the single highest-cognitive-load step in legacy (only step that calls a Python function instead of shelling out) and is the gate to canonical knowledge. Host in **ovp-domain** as `Absorb` + `IdentityResolve` + `CandidatePromote` transforms with an `EvergreenWriter` sink. Depends on: ConceptRegistry data model (P0 below), LLM cassette infrastructure (already in `ovp-llm`).
- **ConceptRegistry data model + reader (`concept_registry.py`)** — Legacy `ConceptRegistry` (entries, alias index, surface index, trigram index, `ResolutionAction` taxonomy, registry JSONL + alias JSON loaders) is the canonical-identity authority. Rust has only the `ConceptResolver` trait shell and `CanonicalKey` newtype. Without the registry model, absorb cannot resolve mentions and `ovp-rebuild-registry` cannot be ported. Host in **ovp-domain** (data model + reader) with a dedicated `concept_registry` module; writes go through **ovp-stores** when we add a canonical applier. Depends on: `canonicalize_note_id` slug rule (small but missing).
- **MOC generation (`auto_moc_updater`)** — L3 → L4 Atlas update. Without it Evergreens land but Atlas/MOC indices go stale, breaking the navigation contract a single cycle promises to leave intact. Host in **ovp-domain** as a `MOCMaterialize` sink emitting `WriteOp`s consumed by `VaultFsPlanApplier`. Depends on: ConceptRegistry reader (above) and Evergreen file writes (absorb).
- **Knowledge index rebuild (`knowledge_index.py` + `truth_store.py` schema)** — `knowledge.db` SQLite projection (pages_index, page_fts FTS5, page_links, page_embeddings, audit_events, truth_projections, ops_state) plus the additive/recompute/breaking migration discipline (`KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION=9`). Without this every downstream read surface (query, ops_state, doctor, working-memory) is dead. Host in a new **ovp-index** crate (sibling to `ovp-stores`) so that SQLite is contained at the effect boundary. Depends on: ConceptRegistry reader, MOC outputs, absorb outputs (event_emitter has to be wired through `EventLog`).
- **VaultLayout port (`runtime.py:VaultLayout`)** — The frozen dataclass that maps ~25 directory paths under a vault root. Every legacy module reads from it. `VaultFsPlanApplier` currently resolves vault-relative paths ad hoc; without a `VaultLayout` value object the L1 intake filters can't agree on where Pinboard archive vs. Clippings vs. attachments live. Host in **ovp-core** as a typed value (no I/O) consumed by sources/sinks. No upstream dependency — should land first.
- **TransactionManager / run ledger (`txn.py`)** — Durable per-run JSON at `60-Logs/transactions/` with `progress_mode` (`counted` vs `indeterminate`), heartbeat, blocked reason. The Rust `EventLog` is in-memory only. Without durable transactions a partial cycle on real LLM credit cannot be resumed or audited, so the first live end-to-end run is unsafe. Host in **ovp-core** (transaction model + classify_run_ledgers) with persistence in **ovp-stores** as a new `TxnFsApplier`. Depends on: nothing — could land in parallel with VaultLayout.

## Gap matrix — P1 (high value, low cost)

- **`canonicalize_note_id` slug function (`identity.py`)** — One pure function; trivial to port to **ovp-core** alongside `CanonicalKey`. Used everywhere wikilink → slug mapping happens. No deps.
- **Pinboard L0 source (`pinboard-processor.py` + `step_pinboard_process` routing)** — Second source channel; `detect_pinboard_processor` heuristic (github/paper/article/website/social-skip) is the natural Source filter boundary. Host in **ovp-domain**. Depends on: L1 intake (P0).
- **`ovp-lint` WIGS 5-layer checks (`lint_checker.py`)** — Read-only health check; high value for the first humans running the Rust system. Host in **ovp-domain** as a `LintCheck` reader pulling from registry + filesystem + (when ready) `knowledge.db`. Depends on: ConceptRegistry reader (P0); deepens once knowledge index ships.
- **`ovp-query` BM25 + embedding retrieval (`query_tool.py`)** — User-facing read surface. Useless until `knowledge.db` exists. Host in **ovp-domain** (query construction) + **ovp-index** (FTS5/embedding readers). Depends on: knowledge index (P0).
- **`ovp-rebuild-registry` (`commands/rebuild_registry.py`)** — Authority recovery: rebuild registry from Evergreen frontmatter + alias tables. Needed for disaster recovery and any registry schema change. Host in **ovp-domain**. Depends on: ConceptRegistry data model (P0).
- **`ovp-migrate-links` (`migrate_broken_links.py`)** — Whole-vault wikilink rewrite using registry + aliases. Also drives the `fix_links` step in the legacy DAG. Host in **ovp-domain** as a Transform that emits `VaultUpdateOp`s. Depends on: ConceptRegistry reader (P0).
- **`ovp-ops-state` projection (`ops_state.py`)** — Kernel-pure derivation over `audit_events` in `knowledge.db`; cheap once knowledge index lives. Host in **ovp-index**. Depends on: knowledge index (P0).
- **`ovp-refresh-ops` codified post-absorb refresh** — Encodes the "audit-sync-only + ops_state rebuild" rule from the user's MEMORY.md. Cheap script-level command. Host in **ovp-cli**. Depends on: ops_state (P1 above).
- **`ovp-autopilot` daemon (`autopilot/daemon.py`)** — Long-running watcher tying L0 → L5. Needed for the "drop file, walk away" UX that drives daily use. Host in a new **ovp-autopilot** crate (or **ovp-cli** subcommand) sharing `GraphRunner`. Depends on: full L0-L5 plus TransactionManager (P0).
- **Stage artifact caching (`STAGE_CACHE_CHECKOUT` / `RECORD_ONLY` policies)** — Fingerprint = hash(input files + algorithm version + pack + profile). Quality/absorb/moc/knowledge_index all participate. Without it incremental runs re-do everything. Host in **ovp-core** (cache policy on FilterDecision) + **ovp-stores** (artifact store). Depends on: at least one stage that benefits (absorb is the natural first).
- **`ovp-doctor` cross-cutting registry health (`doctor.py`)** — Touches every registry (artifact / assembly_recipe / governance / observation_surface / execution_contract / processor_contract / semantic_relation / truth_projection) plus pack compatibility. Worth porting once registries exist in Rust. Host in **ovp-domain**. Depends on: ConceptRegistry + knowledge index (P0).
- **`ovp-entity-extract` + entity registry (`entity_extractor.py`)** — Post-absorb auxiliary that writes `10-Knowledge/Entity/*.md`. Cheap if Absorb is ported. Host in **ovp-domain**. Depends on: absorb (P0).
- **`note_type_normalize` step (`note_type_normalize.py`)** — Frontmatter rewrite for Evergreen drift. Small, idempotent. Host in **ovp-domain**. Depends on: ConceptRegistry reader.
- **Pack/profile resolution + plugin discovery (`pack_resolution.py` + `plugins.py`)** — `BaseDomainPack` + `compatibility_base` chain + `OVP_PACK_MANIFESTS` env + entry-point `ovp.packs` group. Needed before third-party packs can ship against Rust. Host in **ovp-core** (`PackId` + manifest) + **ovp-domain** (built-in `research-tech` and `default-knowledge` packs). Depends on: PipelineManifest already exists; needs extension to pack scope.
- **Workflow handler adapter layer (`workflow_handlers.py`)** — Thin handler functions mapping pack stage manifest entry-points to pipeline methods. Direct analog needed once pack manifests land. Host in **ovp-core** alongside `GraphRunner::register_*`. Depends on: pack resolution above.

## Gap matrix — P2 (future, depends on prior gaps)

- **`ovp-truth` read-only truth-store CLI** — Objects/object/topic/contradictions/clusters queries over `knowledge.db`. Wait until truth store tables land in Rust knowledge index. Host in **ovp-domain** + **ovp-index**. Depends on: knowledge index (P0) plus truth-store DDL.
- **`ovp-link-suggest` RRF-fused BM25+vector link backfill** — Phase 38 link-density backfill with LLM gate. Useful but slow; needs vector index + LLM gate cache. Host in **ovp-domain**. Depends on: knowledge index, query infra.
- **`ovp-merge-duplicates` + `ovp-concept-dedup`** — Trigram clustering + alias merge + archive losers + wikilink rewrite. Both depend on registry being live and migrate-links being ported. Host in **ovp-domain**. Depends on: ConceptRegistry + migrate-links + knowledge index.
- **`ovp-promote` policy lanes (AUTO / ESCALATE / REJECT)** — Phase 34 promotion CLI on top of `concept_registry` candidates. Depends on candidate model first. Host in **ovp-domain**. Depends on: absorb (P0), candidate model.
- **`ovp-cleanup` + `ovp-breakdown` refine transforms** — Deterministic per-Evergreen cleanup and breakdown proposals. Lower urgency once absorb is stable. Host in **ovp-domain**. Depends on: absorb, registry.
- **`ovp-build-crystals` Crystal materializer** — Persists operator briefing observation surface to `40-Resources/Crystals/`. Depends on `truth_api` briefing snapshot. Host in **ovp-domain**. Depends on: truth API surface (below).
- **`ovp-working-memory` daily distill** — Top of Mind / Fresh Crystals / Pending Decisions / EVOLVES / Pulse. Depends on Crystals + truth API + working-memory model. Host in **ovp-domain**. Depends on: Crystals, knowledge index.
- **`ovp-prime` session context pack** — Working-memory-driven session snapshot for agent hand-off. Tiny on top of working-memory. Host in **ovp-cli**. Depends on: working-memory.
- **`ovp-evidence` verifier + backfill (Phase 33)** — Re-hashes claim_evidence locators, updates status/verified_at. Needs claim_evidence schema in Rust truth store. Host in **ovp-domain**. Depends on: truth store schema.
- **`ovp-score-sources` source-authority scorer** — Batch scoring over `50-Inbox/03-Processed/`. Niche but small. Host in **ovp-domain**. Depends on: L1 intake (P0).
- **`ovp-ops` / `ovp-build-views` / `ovp-extract` pack-driven generic dispatch** — Pack-defined operation profiles, view materializers, extraction profiles. Only meaningful once pack API ships in Rust. Host in **ovp-domain**. Depends on: pack resolution (P1) and at least one downstream stage to dispatch.
- **`truth_api` mega-facade (233KB read/query surface)** — Unifies truth store + ledgers + registry + governance + runtime state for UI/CLI consumers. Should be broken into per-domain readers in Rust, not ported wholesale. Host in **ovp-domain** as several focused readers. Depends on: knowledge index, ops_state, working-memory.
- **`runtime_processes.py` external-process awareness** — `ps` introspection for pipeline/daemon/action_worker/observer classification. Only needed once an autopilot daemon ships and a UI wants liveness. Host in **ovp-cli**. Depends on: autopilot (P1).
- **Query write-back loop (`ovp-query --save-to`)** — Closes the Karpathy "knowledge compounding" cycle by re-feeding query outputs into `20-Areas/Queries/`. One flag once query exists. Host in **ovp-domain**. Depends on: query (P1).
- **Migration discipline for knowledge.db schema (`SCHEMA_MIGRATIONS` registry)** — Additive vs recompute vs breaking buckets. Not blocking for first build but must be in place before schema changes ship. Host in **ovp-index**. Depends on: knowledge index (P0).

## Roadmap alignment

The previously locked order — codex review → C9/C10 (live Anthropic + cassette capture) → v1.2 (paper) → canonical store — holds with one insertion and one renaming.

**(c) Proposed additions/insertions:**

1. **C9/C10 — live Anthropic + cassette capture** (unchanged). Keep first. Without it every downstream stage that calls an LLM has to be fixture-fed, which is fine for tests but blocks any real ingest.
2. **NEW: Stage L0/L1 — Inbox+Github intake source + VaultLayout port.** Insert before v1.2. Motivated by P0 gaps "L1 article/github intake" and "VaultLayout port". Rationale: papers (v1.2) can be hand-fed during development, but if we land v1.2 before intake we'll have built two LLM transforms (article + paper) before a single Source filter exists. Doing intake next forces the Source contract to settle and gives v1.2 a real upstream.
3. **v1.2 — paper deep-dive transform** (unchanged in scope, now slots after intake). Reuses the C9/C10 ModelClient and the L0/L1 source.
4. **NEW: Stage L3 — Absorb + ConceptRegistry data model.** Insert between v1.2 and canonical store. Motivated by P0 gaps "L3 absorb" and "ConceptRegistry data model". Rationale: the canonical store work needs a concrete consumer to validate its shape against; building the canonical store before absorb leaves us guessing at the write surface. Absorb is also the single most complex legacy step and surfacing it early de-risks the schedule.
5. **Canonical store** (unchanged in intent, now informed by absorb + registry). At this point `CanonicalUpsertOp` has real producers (absorb) and a real reader (MOC + knowledge index next).
6. **NEW: Stage L4/L5 — MOC + knowledge index (with TxnFsApplier).** New explicit stage after canonical store. Motivated by P0 gaps "MOC generation", "Knowledge index rebuild", "TransactionManager". Rationale: closes the first end-to-end cycle (raw → Evergreen → MOC → knowledge.db) and unlocks the bulk of P1 reader surfaces (query, lint, ops_state, doctor).

After step 6 we have a real cycle and can re-triage P1 based on observed pain.

## Explicit non-goals (legacy features we won't match)

- **13-section LLM deep-dive for articles + github sources.** Legacy `auto_article_processor.process_single_file` historically generated a heavy LLM interpretation per article; post-BL-029/BL-066 it returns `intake_only` and absorb v2 reads the raw processed source directly. We're keeping the new behavior: articles + github skip L2 entirely, papers remain the only LLM deep-dive producer. The Rust `interpret-article` subcommand exists for the paper-shaped path and for compatibility tests, not as the steady-state article path.
- **Subprocess shellout for every pipeline step.** Legacy `unified_pipeline_enhanced` shells out to a separate CLI for every step except `step_absorb`. This is the dominant cognitive-load and perf cost in the legacy code. The Rust rewrite calls transforms in-process via `GraphRunner` and we will not reintroduce subprocess fan-out.
- **`migrate_pack_provenance` one-shot pack rename helper.** Legacy survey explicitly flags this as a one-shot migration used during a single pack rename. It has no recurring purpose; we'll do equivalent migrations as ad-hoc scripts if and when a pack rename happens in Rust.
- **`note_type_normalize` as a permanent step.** Legacy notes call out that this exists "mostly because frontmatter drifted historically." We will write frontmatter correctly the first time in Rust (sinks own the schema) so this step becomes a one-off cleanup, not a recurring DAG node. We'll port it as a CLI utility but not wire it into the default graph.
- **Dedup in the autopilot profile.** Legacy intentionally skips dedup in autopilot because there's no reliable per-round promoted set in event-driven mode. The Rust autopilot will do the same: dedup is a manual / batch CLI, not a daemon step.
- **QMD as a default discovery engine.** Legacy `query_tool.py` supports `--engine qmd` as an external adapter; CLAUDE.md already states QMD is "explicitly optional" and not part of canonical link resolution. The Rust default goes straight through `knowledge.db`; QMD support is not planned and is not a gap.
- **In-process direct call for one specific step (absorb).** The legacy quirk where `step_absorb` is the only step that calls a Python function directly while all others shell out is a workaround, not a contract. The Rust system makes all steps in-process uniformly, so the asymmetry disappears rather than being preserved.
- **`truth_api.py` as a single 233KB facade.** Legacy bundled briefings, signals, actions, runs, governance, observation surfaces, and focused-action dispatch into one module. We will decompose this in Rust into focused readers per surface (see P2 entry) rather than rebuild the monolith.
