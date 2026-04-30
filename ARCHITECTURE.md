# OVP Architecture

> Language: English | [简体中文](ARCHITECTURE.zh-CN.md)

**Status:** Review draft v2
**Updated:** 2026-04-29

This document does not introduce another competing layer model. It puts the terms already used in OVP back into their proper places so product narrative, runtime flow, code ownership, knowledge-state semantics, and storage trust boundaries do not collapse into one ambiguous architecture.

Core rule:

> OVP has one primary architecture: the four-layer persistent architecture.
> All other vocabularies are perspectives over that architecture, not parallel architectures.

## 0. Overview

OVP uses several vocabularies because they answer different questions.

| Vocabulary | What it explains | Position |
| --- | --- | --- |
| Capture -> Compile -> Reuse | Product narrative: why a user needs OVP | Product perspective |
| Ingest -> Interpret -> Absorb -> Refine -> Canonical -> Derived | Execution flow: how the pipeline runs | Runtime perspective |
| Core Platform / Domain Pack / Workflow Profile | Ownership: what core, packs, and profiles own | Ownership perspective |
| Canonical Knowledge / Derived Indexes / Context Assembly / Governance | Persistent architecture: truth, projection, access, and control boundaries | Primary architecture |
| KSR: source -> observation -> claim -> evidence -> validity -> projection -> permission | Long-term knowledge-state semantics | Semantic vocabulary |
| Authority / Derived state / Projection lifecycle | Storage trust boundary and projection repair mechanism | Storage/control perspective |

Canonical wording:

```text
The four-layer model governs architecture.
The six-stage pipeline governs execution.
Core/Pack/Profile governs ownership.
KSR governs knowledge-state language.
Capture/Compile/Reuse governs product narrative.
Authority/Derived/Projection lifecycle governs storage trust and repair.
```

Mapping table:

| Primary layer | Product narrative | Six-stage runtime | Ownership model | KSR semantics | Storage trust |
| --- | --- | --- | --- | --- | --- |
| Layer 1 Canonical Knowledge | Capture / Compile | Interpret / Absorb / Refine / Canonical | Core + pack semantics | source / observation / claim / evidence / validity | Authority |
| Layer 2 Derived Indexes / Views | Reuse substrate | Derived | Core projection infra + pack projection semantics | projection | Derived state |
| Layer 3 Context Assembly / Access | Reuse surface | Derived output / access commands | Core shell + pack view recipes | projection | Access projection |
| Layer 4 Governance / Control Plane | Compile gate / Reuse feedback | Absorb / Refine / Canonical / Derived controls | Core governance primitives + pack policies + profile routing | validity / permission | Projection lifecycle + audit |

## 1. Primary Architecture: Four Persistent Layers

All long-lived decisions about state ownership, trust boundaries, projections, access, and governance should land in these four layers.

```text
Layer 1: Canonical Knowledge
  The long-lived knowledge state OVP trusts and maintains.

Layer 2: Derived Indexes / Views
  Rebuildable indexes, graphs, query stores, and diagnostic views derived from Layer 1.

Layer 3: Context Assembly / Access
  Reader, operator, query, export, briefing, and agent-context surfaces assembled from persistent knowledge.

Layer 4: Governance / Control Plane
  Cross-cutting control over promotion, review, verification, routing, repair, audit, and workflow boundaries.
```

Layer 4 is not upstream of Layer 1, and it is not the last pipeline step. It is a cross-cutting control plane.

```text
+--------------------------------------------------------------------+
| Layer 4: Governance / Control Plane                                |
|                                                                    |
|  Policy  Promotion  Review  Verification  Dispatch  Repair  Audit  |
|                                                                    |
|  +------------------+     +------------------+     +-------------+ |
|  | Layer 1          | --> | Layer 2          | --> | Layer 3     | |
|  | Canonical        |     | Derived          |     | Access /    | |
|  | Knowledge        |     | Indexes / Views  |     | Context     | |
|  +------------------+     +------------------+     +-------------+ |
|                                                                    |
+--------------------------------------------------------------------+
```

### Layer 4 Subaxes

Layer 4 must not become a junk drawer. It is split into at least seven subaxes:

| Subaxis | Responsibility | Examples |
| --- | --- | --- |
| Policy | What may be written, promoted, or automated | promotion rules, who-can-write, high-risk gate |
| Promotion | The Policy + Review intersection that turns a candidate or derived proposal into accepted state | `promote_candidates.py`, `promotion_policy.py`, `promotion_audit.py`, `relation_promotion.py`, `workspace_promotion.py` |
| Review | Human review and queue lifecycle | candidate review, contradiction review, stale-summary review |
| Verification | Evidence, hash, freshness, and replay checks | evidence status, content hash check, review-state replay |
| Routing / Dispatch | Workflow path selection, task dispatch, and ambiguity routing | workflow profile routing, ambiguity dispatch, source routing preview JSON |
| Repair | Projection lifecycle and rebuild control | metadata repair marker, full rebuild marker, semantic reindex marker |
| Audit | Accountability chain and immutable event records | audit JSONL, promotion event, review event |

Promotion is not a new layer. It is the gate where Policy decides whether a candidate can enter canonical state, while Review/Audit record the acceptance process.

When saying "Layer 4 controls X", name the subaxis. Avoid using "Resolver" for Layer 4 routing in architecture prose; reserve resolver language for identity and concept resolution, such as `concept_resolver.py`.

## 2. Layer 1: Canonical Knowledge

Layer 1 is OVP's long-term trust boundary.

It answers:

- Which knowledge objects actually exist?
- What is their stable identity?
- Which factual claims have been accepted?
- Where is the evidence for each factual claim?
- Can the claim be traced to a source or explicit user attribution?
- Which writes passed review, promotion, or verification?
- If derived state is lost, can it be recomputed from here?

Current OVP Authority consists of:

- vault Markdown
- concept registry / alias registry
- source notes / deep-dive notes / evergreen notes
- evidence quote / locator / content hash
- audit JSONL / promotion event / verification event
- accepted state after review

Layer 1 is not "everything in the database". OVP Layer 1 should be file-native, evidence-backed, and user-owned.

Boundaries:

- `knowledge.db` is not Layer 1.
- `truth_store.py` is not Layer 1.
- `truth_store.py` defines projection schemas for queryable rows.
- candidate queues and review queues are not Layer 1 until review/promotion accepts them.
- LLM output is not Layer 1 unless it passes an explicit absorb, review, or promotion path.

Layer 1 may be slower, but it must be readable, diffable, backup-friendly, migratable, auditable, and replayable.

## 3. Layer 2: Derived Indexes / Views

Layer 2 is rebuildable state computed from Layer 1.

It answers:

- How can the UI list objects quickly?
- How can query/search run quickly?
- How can graph views render quickly?
- How can contradictions be queried quickly?
- How can briefing, dashboard, and context packs assemble quickly?
- Which local runtime store should MCP and prompt assembly read?

Current derived state includes:

- `knowledge.db`
- truth projection rows:
  - `objects`
  - `claims`
  - `claim_evidence`
  - `relations`
  - `contradictions`
- graph projection rows:
  - `graph_edges`
  - `graph_clusters`
- access/query rows:
  - `compiled_summaries`
  - `reuse_events`
  - search/query payloads
- generated views:
  - Atlas
  - MOC
  - graph views
  - lint outputs
  - daily delta

Layer 2 may index, aggregate, cache, denormalize, optimize schemas for UI/query/graph, and be deleted then rebuilt.

Layer 2 must not decide truth, overwrite Layer 1, promote semantic search results into canonical identity, or treat projection rows as source of truth.

Naming discipline:

- Say `truth projection`, not `truth source`.
- Say `graph projection`, not `canonical graph truth`.
- Say `search projection`, not `semantic truth`.
- Say `knowledge.db derived store`, not `knowledge.db authority`.

## 4. Layer 3: Context Assembly / Access

Layer 3 turns persistent knowledge into surfaces and context that users, agents, and operators can use.

It answers:

- What should a user see first?
- How should reader pages be organized?
- Which claims, evidence, and relations should object pages show?
- Is the graph page a spatial corpus map or a debug report?
- How should search results explain provenance?
- How should briefings, context packs, and prompts be assembled?
- Where should operators review and maintain the system?

Current Layer 3 surfaces include, non-exhaustively:

- `ovp-ui`
- reader atlas / future reader home
- object pages
- graph page
- `ovp-query`
- `ovp-export`
- `ovp-truth`
- `ovp-mcp` / MCP read tools
- `ovp-build-crystals`
- `ovp-working-memory`
- `ovp-link-suggest`
- briefing
- signals/actions
- context packs
- prompt assembly

Layer 3 normally reads Layer 2 for speed, but it must remain traceable to Layer 1.

Boundaries:

- search results are access, not authority.
- briefings are access, not authority.
- reader pages are projections, not independent truth stores.
- context packs are projections, not agent-memory authority.
- dashboards are projections, not workflow truth.
- Layer 3 must not silently promote displayed content into Layer 1.

LearnBuffett's product inspiration belongs here: readable object pages, concept extraction display, backlink/source rails, and spatial graph views. This changes product shape, not the Layer 1 truth model.

## 5. Layer 4: Governance / Control Plane

Layer 4 is OVP's control plane.

It answers:

- Can a candidate enter canonical state?
- Does a factual claim require review?
- Is evidence stale, broken, or verified?
- Which workflow should a source use?
- Which path should handle an ambiguity?
- Which users/agents may write which state?
- Is a projection stale, repairable, or rebuild-only?
- Can a workflow item be claimed, leased, retried, closed, or superseded?

Current Layer 4 components include:

- promotion policies
- review queues
- contradiction review
- stale-summary review
- evidence verification / replay
- relation promotion replay
- action queue
- focused action handlers
- signals/actions
- doctor checks
- pack contracts
- workflow/profile routing

Boundaries:

- governance rules should be explicit and testable.
- agent output must not silently become accepted truth.
- review state must survive derived rebuilds.
- projection repair must be governed separately from ordinary access.

## 6. Perspective 1: Capture -> Compile -> Reuse

This is the product story, not the storage architecture.

| Verb | User meaning | Architectural hook |
| --- | --- | --- |
| Capture | Bring material into OVP without losing provenance | Layer 1 source input + runtime ingest |
| Compile | Turn material into candidates, objects, claims, evidence, and relations | Layer 1 + Layer 4 Policy/Review |
| Reuse | Use compiled knowledge in reader pages, graph, search, briefings, prompts, and context packs | Layer 2 + Layer 3 |

Capture/Compile/Reuse mainly explains OVP to users, but it has two architecture hooks:

- Compile maps to Layer 4 Policy promotion gates.
- Reuse maps to Layer 3 access surface and context assembly choices.

Do not use these verbs as module boundaries in code. They are product and roadmap language.

## 7. Perspective 2: Six-Stage Runtime

The six-stage model explains pipeline execution order, not persistent ownership.

| Runtime stage | What it does | Persistent layers touched |
| --- | --- | --- |
| Ingest | Normalize incoming material | Layer 1 source input |
| Interpret | Produce extraction / interpretation | Layer 1 candidate |
| Absorb | Decide how interpretation enters the vault | Layer 1 + Layer 4 |
| Refine | Clean up or decompose existing notes | Layer 1 + Layer 4 |
| Canonical | Update stable registry / Atlas / MOC state | Layer 1 |
| Derived | Build query / graph / lint / UI / access projections | Layer 2 + Layer 3 + Layer 4 Repair |

The runtime may be refactored without changing the persistent architecture. Do not confuse a stage with an ownership layer.

## 8. Perspective 3: Core / Pack / Profile

This perspective defines who owns behavior.

| Owner | Owns | Must not own |
| --- | --- | --- |
| Core Platform | vault layout, runtime, CLI, registry framework, derived store, audit, plugin loading | domain semantics |
| Domain Pack | object kinds, workflow profiles, extraction semantics, schemas, templates, lint/refine rules | core trust boundary |
| Workflow Profile | executable DAG and routing defaults inside a pack | canonical identity or permission policy by itself |

Rules:

- core defines the frame.
- pack defines semantics.
- profile selects a path.
- pack cannot bypass audit.
- pack cannot turn semantic retrieval into canonical identity.
- profile cannot carry all domain semantics.

## 9. Perspective 4: KSR Semantic Vocabulary

KSR vocabulary:

```text
source -> observation -> claim -> evidence -> validity -> projection -> permission
```

It describes long-term knowledge state.

| KSR term | Meaning | Primary layer |
| --- | --- | --- |
| source | Original material or capture input | Layer 1 input |
| observation | Fact fragment observed or extracted from a source | Layer 1 candidate |
| claim | Structured statement about an object | Layer 1 |
| evidence | quote, locator, hash, source context, user attribution, derived chain | Layer 1 |
| validity | review status, freshness, conflict, confidence | Layer 1 + Layer 4 |
| projection | materialized view or access artifact | Layer 2 + Layer 3 |
| permission | who can read, write, promote, or route | Layer 4 |

Current implementation note: `evidence.py` is already a first-class module. `claim` and `validity` currently exist mainly as projection rows and schemas in `truth_store.py` / `truth_api.py`. Layer 1 claim expression is still carried by markdown + registry + audit JSONL. Future work may promote claim/validity into more explicit artifact contracts.

Use KSR for schema design, evidence spans, claim lifecycle, permission layers, review policy, and backlog task naming.

## 10. Perspective 5: Authority / Derived State / Projection Lifecycle

This perspective is narrower than the four-layer architecture. It focuses on storage trust and repair.

```text
+------------------------------------------------+
| Authority                                      |
| vault markdown + registry + evidence/audit     |
+----------------------+-------------------------+
                       |
                       | derive / project
                       v
+------------------------------------------------+
| Derived state                                  |
| knowledge.db truth/search/graph/access rows    |
+----------------------+-------------------------+
                       |
                       | detect drift / damage
                       v
+------------------------------------------------+
| Projection lifecycle                           |
| repair marker / rebuild marker / reindex marker|
+------------------------------------------------+
```

| Layer | Maps to | Can be rebuilt? |
| --- | --- | --- |
| Authority | Layer 1 | No. It is the durable trust boundary. |
| Derived state | Layer 2 + Layer 3 access caches | Yes. It is rebuildable projection state. |
| Projection lifecycle | Layer 4 Repair + Verification | It controls repair/rebuild. |

Current OVP mapping:

```text
Authority:
  vault markdown
  registry files
  evidence locators / hashes
  promotion and review audit JSONL

Derived state:
  knowledge.db
  truth/search/graph/access projections
  UI/search/query payloads

Projection lifecycle:
  lightweight repair marker
  full rebuild marker
  semantic reindex marker reserved for future heavy semantic projections
```

## 11. Projection Repair Markers

Projection repair should be a structured state machine, not loose marker filenames.

```python
class ProjectionRepairMarker:
    id: MarkerId
    kind: Literal["metadata_only", "full_rebuild", "semantic_reindex"]
    scope: Scope
    reason: str
    created_at: datetime
    caused_by: str

    authority_schema_version: int
    projection_schema_version: int

    superseded_by: Optional[MarkerId]
    claimed_by: Optional[str]
    claim_lease_until: Optional[datetime]
```

`scope` should be structured:

```python
class Scope:
    pack: Optional[str]
    profile: Optional[str]
    projection_kind: Optional[str]
    source_ids: list[str]
    object_ids: list[str]
    entity_types: list[str]
```

Lifecycle kinds:

- `metadata_only`: copy or fix projection metadata without recomputing embeddings or heavy derived artifacts.
- `full_rebuild`: rebuild the projection from Authority.
- `semantic_reindex`: reserved now as a first-class lifecycle kind for future heavy semantic projections; it does not commit OVP to LanceDB or any specific backend.

Operational rules:

- a broader marker can supersede a narrower marker.
- `claimed_by` and `claim_lease_until` prevent duplicate workers from handling the same marker.
- if the current Authority schema version is newer than the marker or projection version, promote to `full_rebuild`.

## 12. Canonical Scenarios

### Scenario A: User Clips An Article

```text
1. Capture handler writes raw source note
   - Runtime: Ingest
   - Architecture: Layer 1 input

1a. `ovp-absorb --dry-run --json` can emit `source_lifecycle.routing_preview`
   - Layer 4: Routing / Dispatch explains the planned source route before mutation
   - The preview does not move files, initialize LLMs, or create derived state

2. Ingest normalizes source metadata
   - Runtime: Ingest
   - Does not create accepted factual claims

3. Interpret creates candidates, observations, and possible claims
   - Runtime: Interpret
   - KSR: source / observation / candidate claim

4. Promotion policy decides whether each candidate can auto-promote
   - Architecture: Layer 4 Policy + Promotion

5. Review records accept/reject when needed
   - Architecture: Layer 4 Review + Audit

6. Accepted state lands in markdown / registry / audit
   - Architecture: Layer 1 Canonical Knowledge
   - KSR: claim / evidence / validity

7. Derived stage refreshes knowledge.db and graph rows
   - Architecture: Layer 2 Derived Indexes / Views

8. Reader/object/graph surfaces show the result
   - Architecture: Layer 3 Context Assembly / Access
```

### Scenario B: `knowledge.db` Is Deleted Or Corrupted

```text
1. doctor / startup check detects missing or invalid derived store
   - Architecture: Layer 4 Verification

2. repair controller writes ProjectionRepairMarker(kind="full_rebuild")
   - Architecture: Layer 4 Repair
   - reason records the concrete cause
   - caused_by records doctor_check / startup_check / user_override

3. rebuild worker claims the marker
   - claimed_by + claim_lease_until prevent duplicate work

4. Derived stage rebuilds knowledge.db from Authority
   - Architecture: Layer 2
   - reads Layer 1 only

5. Review/feed state is replayed from audit ledger
   - Architecture: Layer 1 audit -> Layer 2/3 feed state

6. marker is closed or superseded
   - Architecture: Layer 4 Repair + Audit

7. Layer 3 surfaces read the refreshed projections
```

### Scenario C: A New Relation Is Discovered

```text
1. Query/agent reads context from a Layer 3 access surface

2. It proposes a new relation
   - KSR: observation / candidate claim
   - Not accepted truth yet

3. Candidate risk layer scores evidence strength, identity ambiguity, sensitivity, and impact
   - Architecture: Layer 4 Policy

4. Low-risk relation may auto-promote; higher-risk relation goes to review
   - Architecture: Layer 4 Promotion + Review

5. Accepted relation writes evidence-backed canonical state
   - Architecture: Layer 1

6. Graph projection refreshes
   - Architecture: Layer 2

7. Reader graph and object pages show the relation
   - Architecture: Layer 3
```

## 13. Architectural Invariants

These rules are intended to be mechanically checkable. Violating them is a bug.

| ID | Invariant | Check direction |
| --- | --- | --- |
| I-1 | Any accepted factual claim must have resolvable evidence. Structural state is exempt. | evidence completeness check |
| I-2 | Layer 2 must be deterministically rebuildable from Layer 1 + schema. | rebuild test |
| I-3a | Audit ledger is append-only; state changes must emit new events. | audit append-only check |
| I-3b | Feed UI state patches must correspond to ledger append events. | feed event replay check |
| I-3c | Deleting feed UI tables must not lose current feed state; it must replay from ledger. | replay test |
| I-4 | Layer 3 writes to Layer 1 state must go through Layer 4 governance APIs. | code review + runtime audit |
| I-4b | Layer 3 modules should not directly import Layer 1 mutation symbols; use read-only views unless explicitly justified. | import lint |
| I-5 | review status must survive derived rebuild. | rebuild regression test |
| I-6 | naming discipline must stay consistent across docs and code. | naming lint |

### I-1: Factual Evidence

Accepted factual claims are statements about the world outside OVP. They must have at least one `evidence_kind`:

- `source_quote`
  - vault file pointer
  - offset or locator
  - content hash
- `user_attribution`
  - user id
  - signed audit record or equivalent append-only event
- `derived_chain`
  - referenced `claim_id`
  - the chain must eventually resolve to `source_quote` or `user_attribution`

Structural state is exempt:

- registry aliases
- routing state
- workflow status
- pack contract decisions
- projection lifecycle markers

### I-3: Audit Ledger Vs Feed UI State

```text
Audit ledger (Layer 1 append-only):
  /audit/promotions.jsonl
  /audit/reviews.jsonl
  /audit/projection_repair.jsonl

Derived feed UI state (Layer 2/3 mutable projection):
  feed_events.status
  feed_events.resolved_at
  feed_events.retry_count
```

The UI may patch mutable projection state, but every visible status change must correspond to an append-only audit event.

## 14. Architectural Fitness Functions

Each invariant should have a CI, doctor, pre-commit, or rebuild check.

| Invariant | Check | Likely implementation location |
| --- | --- | --- |
| I-1 | `verify_evidence_complete.py` | `ovp doctor` |
| I-2 | rebuild `knowledge.db` from fixture vault | rebuild test |
| I-3a | `verify_audit_jsonl_append_only.py` | pre-commit / CI |
| I-3b | feed patch must resolve to audit event | unit test |
| I-3c | replay feed state from audit ledger | rebuild test |
| I-4 | runtime write audit: Layer 3 write must call governance API | runtime audit / tests |
| I-4b | import-linter rule for direct mutation imports | CI lint |
| I-5 | review state survives derived rebuild | rebuild test |
| I-6 | naming lint for forbidden architecture phrases | CI lint |

These checks should become backlog items rather than prose only.

## 15. Schema Versioning

Authority and derived projection schemas evolve separately.

Rules:

- Authority schema version should live at vault root, for example `.ovp/schema_version`.
- Derived stores must record which Authority schema version they were built from.
- Derived projection schemas must record their own version.
- Startup checks compare current Authority version, projection schema version, and marker versions.
- If current Authority schema version is newer than the marker or projection version, write or promote to `ProjectionRepairMarker(kind="full_rebuild")`.
- Avoid silent version jumps; migrations should be explicit and monotonic.

Schema migration and projection lifecycle are connected. Migration should not be a separate hidden mechanism.

## 16. Naming Discipline

Do:

1. Say `Authority` for vault markdown + registry + evidence/audit JSONL.
2. Say `derived store` for `knowledge.db`.
3. Say `truth projection` for queryable truth rows.
4. Say `access surface` for UI/search/briefing/context.
5. Say `promotion gate` for candidate -> accepted transition.
6. Say `projection repair` for lightweight derived-store fix.
7. Say `full rebuild` for total derived-store recomputation.
8. Say `semantic reindex` for future heavy semantic projection refresh.
9. Say `Routing / Dispatch` for Layer 4 workflow routing.

Do not:

1. Do not call `knowledge.db` the source of truth.
2. Do not call `truth_store.py` Authority.
3. Do not call generated Atlas/MOC/wiki pages canonical truth by default.
4. Do not call LLM outputs accepted knowledge before promotion.
5. Do not use `Resolver` for Layer 4 routing.

Forbidden phrase handling:

- Wrong: "`knowledge.db` is source of truth."
  - Correct: "`knowledge.db` is the derived store."
- Wrong: "`truth_store.py` owns truth."
  - Correct: "`truth_store.py` owns truth projection schemas."
- Wrong: "Dashboard state is workflow truth."
  - Correct: "Dashboard state is a Layer 3 projection."
- Wrong: "KSR is a new architecture."
  - Correct: "KSR is the semantic vocabulary for long-term knowledge state."

## 17. Current State

Current implementation roughly maps as follows:

| Area | Current implementation | Architectural reading |
| --- | --- | --- |
| Vault markdown | source notes, deep dives, evergreen, Atlas/MOC | mostly Layer 1, with some generated projection artifacts |
| concept registry | `concept_registry.py`, aliases, identity helpers | Layer 1 identity support |
| `truth_store.py` | SQLite schema and rows | Layer 2 projection schema |
| `truth_api.py` | read/query interface | Layer 2/3 access |
| `ovp-ui` | dashboard, object pages, graph, signals/actions | Layer 3 + some Layer 4 controls |
| promotion modules | candidate/relation/workspace promotion | Layer 4 Promotion + Audit |
| source lifecycle routing preview | `ovp-absorb --dry-run --json` `source_lifecycle.routing_preview` | Layer 4 Routing / Dispatch |
| doctor/lint | checks and repair hints | Layer 4 Verification + Repair |
| packs | `research-tech`, `default-knowledge` | ownership perspective |

Main gaps:

- Layer 1 claim/evidence contracts now carry a first line/char span schema in derived evidence rows; factual evidence_kind enforcement and richer claim lifecycle fields still need hardening.
- Layer 2 / Layer 3 projection labels now exist on core access payloads and materialized reader artifacts; doctor/export enforcement and future surfaces still need to consume them consistently.
- Layer 4 fitness checks now cover the first hot-path, workflow-wiring, source routing preview, evidence span backfill, and candidate risk cases; deeper evidence completeness, projection replay, and import-boundary checks are still open.
- Projection lifecycle markers need structured schema, scope, lease, and supersession.
- Schema versioning is not yet wired into projection lifecycle.
- The reader-first home is now the default entry; object pages have a first reader profile/source rail; `/graph` has a first spatial map projection; search and deeper per-kind object layouts still need product shape.

## 18. Near-Term Architecture Actions

Recommended order:

1. Keep projection metadata attached to new access surfaces and add doctor/export checks that verify the labels are present.
2. Continue reader-first Layer 3 product work on deeper per-kind object layouts and search using the new evidence spans/risk tiers.
3. Add stricter factual evidence completeness checks before expanding automatic promotion.
4. Introduce structured `ProjectionRepairMarker` schema.
5. Add schema version fields to Authority and derived projection state.

## Appendix: Backlog Mapping

The architecture should not depend on backlog IDs to be valid. The table below is only the current implementation mapping and should be maintained in `BACKLOG.md`.

| Architecture work | Current backlog/task mapping |
| --- | --- |
| Projection marking | `BL-002`, `KSR-002` shipped in PR #78 |
| Dashboard/search hot-path audit | `BL-003`, `KSR-015` shipped in PR #77 |
| Workflow wiring eval suite | `BL-004`, `KSR-026` shipped in PR #77 |
| Article routing preview | `BL-005`, `KSR-014` done in PR #81 |
| Evidence span / factual evidence completeness | `BL-006`, `KSR-001`, `KSR-018` done in PR #82 |
| Candidate risk layering | `BL-007`, `KSR-003` done in PR #82 |
| Reader-first access surfaces | `BL-001`; `BL-008` partial and `BL-009` done in PR #79; `BL-010` done in PR #80 |
| Projection repair lifecycle | `BL-020` |
| Schema versioning and migration trigger | `BL-021` |
