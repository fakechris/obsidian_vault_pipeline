# Local Knowledge Workbench Milestone Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Define the remaining product and engineering milestones from the current OVP state to a real local knowledge workbench.

**Architecture:** Keep the existing architecture intact: vault Markdown remains the authoring and export surface, `knowledge.db` remains the truth-aware runtime store, and `ovp-ui` becomes the primary local inspection and review surface. The next work should deepen the bridge between these three layers instead of replacing any of them.

**Tech Stack:** Python 3.13, SQLite via stdlib `sqlite3`, current `knowledge_index.py`, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, pytest, lightweight local HTTP UI.

---

## Product Definition

“本地知识工作台” at this project stage means a local-first system where a user can:

1. ingest and process source material into deep dives and evergreen notes,
2. inspect the truth layer directly from `knowledge.db`,
3. move from DB rows back to the originating Markdown notes without guessing,
4. review and maintain contradictions / stale summaries from one UI,
5. understand how source notes, deep dives, evergreen objects, Atlas/MOC pages, and timeline entries connect.

This does **not** require:

- multi-user collaboration,
- a hosted web product,
- replacing Obsidian as the authoring surface,
- replacing SQLite,
- or a complete ontology for every future domain.

## Current State

### What already exists

- `research-tech` is the default workflow pack.
- Pipeline is stable end-to-end for the current vault.
- `knowledge.db` contains:
  - `objects`
  - `claims`
  - `relations`
  - `compiled_summaries`
  - `timeline_events`
  - `contradictions`
  - `pages_index / page_links`
- Local product surfaces already exist:
  - `ovp-truth`
  - `ovp-ui`
  - `ovp-doctor`
  - `ovp-export`
- `ovp-ui` already supports:
  - objects browser
  - unified search
  - note rendering
  - Atlas / MOC browser
  - Deep Dive derivation browser
  - event browser
  - contradiction browser
  - stale summary browser
  - contradiction resolution actions
  - summary rebuild actions
  - batch operations for contradictions and stale summaries

### What this means in product terms

The system is already past “debugging UI for the DB.” It is now a working local browser + maintenance tool over the truth layer.

But it is **not yet** a full local knowledge workbench, because key product loops are still incomplete or weak.

## Gap Analysis

### Gap 1: Review workbench is partial

Current status:

- contradictions can be reviewed,
- stale summaries can be rebuilt,
- review context is visible on object/topic/event pages.

Missing:

- richer evidence for why an item is in queue,
- review history and audit visibility in the UI,
- direct review entry points from more surfaces,
- queue-level operator ergonomics beyond the current minimal forms.

### Gap 2: Event model is still shallow

Current status:

- `Event Dossier` is usable,
- provenance is visible,
- users can jump from events back to object / deep dive / Atlas.

Missing:

- stronger distinction between “dated note” and “event entity,”
- better grouping and filtering semantics,
- better summary context for why an item appears on the timeline,
- less ambiguity around whether `/events` is a note timeline or an event system.

### Gap 3: Contradiction model is weak

Current status:

- contradiction queue exists,
- UI can resolve contradictions,
- zero-result explanations are visible.

Missing:

- stronger detection quality,
- better evidence display,
- clearer subject/object scope,
- confidence that an empty contradiction queue means something useful.

### Gap 4: Knowledge production traceability is still thin

Current status:

- source note <-> deep dive bridge exists,
- deep dive -> derived object browser exists,
- object -> evergreen/source note/Atlas bridge exists.

Missing:

- a clear end-to-end production chain view:
  - source -> processed -> deep dive -> evergreen -> Atlas
- per-note and per-object “what did this produce / what produced this” explanations,
- better aggregate views for editorial or knowledge operations.

### Gap 5: Product shell is still utilitarian

Current status:

- UI is usable,
- search works,
- review queues are visible.

Missing:

- stronger navigation IA,
- denser dashboards,
- better page-level context summaries,
- stronger operator affordances,
- a clearer “start here / next action” experience.

## Milestone Map

### Milestone 0: Knowledge Runtime Foundation

Status: **Complete**

Includes:

- Phase 1: extraction visibility
- Phase 2: truth store
- Phase 3: materializers + review loops
- Phase 4: pack split
- Phase 5: pack E2E hardening
- Phase 6: operationalization and engine decision

Exit condition:

- `research-tech` is the default pack
- runtime is stable
- full pytest suite is green

### Milestone 1: DB Surface And Local Browsing

Status: **Complete**

Includes:

- Phase 7
- `truth_api`
- `ovp-truth`
- DB-backed view models
- `ovp-ui`
- object/topic/event/contradiction browsing

Exit condition:

- user can inspect truth rows from the DB without raw SQL

### Milestone 2: Provenance-Aware Review Workbench

Status: **In Progress**

Includes:

- Phase 8
- provenance-aware events
- provenance-aware contradictions
- Atlas / Deep Dive bridge pages
- note rendering and asset serving
- unified search
- contradiction and stale-summary actions
- batch review operations

Exit condition:

1. Every major truth surface can point back to Markdown provenance.
2. The user can perform the two current truth maintenance loops from the UI.
3. The UI no longer feels like a raw DB viewer.

Remaining work inside this milestone:

- tighten queue evidence and review explanation
- expose review history / audit visibility
- improve dashboard and page-level next-action context

### Milestone 3: Review Workbench Completion

Status: **Not Started**

Goal:

Make the current UI feel like a real operator console for knowledge maintenance.

Core deliverables:

- contradiction evidence drill-down
- stale summary rationale drill-down
- review history / audit trail in UI
- queue actions reachable from object/topic/event pages
- better dashboard guidance:
  - what is urgent,
  - what is noisy,
  - what changed recently

Exit condition:

- an operator can use `ovp-ui` to review and maintain the current truth queues without dropping back to CLI for normal flows

### Milestone 4: Event And Contradiction Model Hardening

Status: **Not Started**

Goal:

Improve the underlying semantic quality of `events` and `contradictions`, not just the UI.

Core deliverables:

- clearer timeline row semantics
- better event grouping
- stronger contradiction evidence / polarity rules
- better empty-state semantics
- fewer false-positive and false-negative workbench items

Exit condition:

- users can trust that these surfaces are semantically meaningful, not just technically present

### Milestone 5: Knowledge Production Traceability

Status: **Not Started**

Goal:

Make the full production chain legible:

- source
- processed note
- deep dive
- evergreen object
- Atlas/MOC placement

Core deliverables:

- source-centric provenance views
- deep-dive production summaries
- object derivation summaries
- Atlas/topic rollups that explain knowledge contribution, not just membership

Exit condition:

- users can answer “where did this knowledge come from?” and “what did this note produce?” from the product itself

### Milestone 6: Product Shell And Operator UX

Status: **Not Started**

Goal:

Turn the current local UI into a clearer product surface.

Core deliverables:

- better homepage / dashboard IA
- stronger navigation between object/topic/event/review/search
- more intentional layout and information density
- improved affordances for common workflows

Exit condition:

- first-time users can understand how to navigate and operate the system without reading code or plan docs

## Recommended Next Sequence

### Phase 9: Review Workbench Completion

Do next.

Reason:

- it deepens the surfaces that already exist,
- it improves user trust immediately,
- and it avoids moving to more speculative model work too early.

Recommended scope:

1. review history / audit panels,
2. contradiction evidence context,
3. stale summary explanation panels,
4. action entry points from object/topic/event pages,
5. dashboard prioritization for review work.

### Phase 10: Event + Contradiction Hardening

Do after Phase 9.

Reason:

- once the workbench is operational, semantic weaknesses become more visible,
- then it is worth hardening the underlying model.

### Phase 11: Knowledge Production Traceability

Do after Phase 10 or in parallel if product pressure is stronger than model pressure.

Reason:

- this is the most legible “value surface” for users,
- but it depends on the earlier provenance and review foundation being stable.

## Non-Recommended Paths Right Now

Do **not** prioritize these before Milestones 3-5:

- external domain packs,
- PGlite migration,
- hosted product shell,
- multi-user features,
- heavy frontend rewrite,
- speculative graph visualization for its own sake.

Those are downstream, not current blockers.

## Milestone Exit Criteria For “Real Local Knowledge Workbench”

The project can claim that label when all of the following are true:

1. Users can browse truth rows directly.
2. Users can trace truth rows back to source Markdown.
3. Users can perform routine knowledge maintenance from the UI.
4. Users can understand why an item appears in events / contradictions / stale summaries.
5. Users can inspect the full production chain from source to evergreen organization.
6. The local UI feels like the primary operational surface, not a debug sidecar.

## Current Assessment

As of this plan:

- Milestone 0: complete
- Milestone 1: complete
- Milestone 2: mostly complete, but not fully closed
- Milestone 3+: not started

So the honest product statement is:

> OVP is already a usable local truth browser and partial review workbench, but it is not yet a fully mature local knowledge workbench.

That remaining gap is now specific and manageable. It is no longer architectural uncertainty; it is milestone execution.

## External Reference Projects And What To Borrow

This milestone should stay grounded in concrete product references instead of inventing abstract architecture in isolation.

### GBrain

Reference:

- `gbrain` repository

What matters:

- knowledge system framed as an operator workflow, not just a storage engine,
- strong maintenance / recipe / skillpack story,
- clear “the system keeps working after you save” mental model.

What OVP should borrow:

- stronger operator protocol for recurring maintenance,
- clearer ongoing maintenance narratives beyond one-shot commands,
- more productized workflows around curation and upkeep.

### Nia Vault

Reference:

- `Nia Vault` docs

What matters:

- stronger page contract,
- clearer distinction between compiled truth and ongoing timeline/history,
- product-level graph and workflow center concepts.

What OVP should borrow:

- object/topic/event pages should feel more like stable knowledge pages, not generic data views,
- stronger workflow-center experience for lint / maintenance / sync operations,
- eventually a more explicit graph surface for typed relation browsing.

### agentmemory

Reference:

- `agentmemory` repository

What matters:

- excellent runtime observability story,
- clear integration packaging for many agent hosts,
- benchmark / measurable-value framing.

What OVP should borrow:

- better visibility into what the system captured, indexed, and changed,
- clearer integration health surfaces,
- stronger quality and maintenance metrics over time.

### Nowledge Mem

Reference:

- `Nowledge Mem` docs

What matters:

- very strong first-loop product framing:
  - save one thing,
  - find it again,
  - let one real tool use it,
- explicit “how to know it is working” verification language,
- background intelligence framed as:
  - contradictions,
  - clusters,
  - briefings,
  - graph connections,
- search described as a ranking pipeline, not just a textbox,
- browse / automation tools packaged as part of the product surface, not hidden implementation detail.

What OVP should borrow:

- a stronger “first useful loop” for local workbench onboarding,
- explicit verification surfaces that prove:
  - this came from my vault,
  - this object came from my source chain,
  - this review queue reflects real system state,
- a more productized explanation of background maintenance:
  - why an item is stale,
  - why a contradiction exists,
  - what changed since last run,
- eventually a richer ranking / relevance story for unified search instead of raw matching alone.

## Cross-Project Product Principles

Across `gbrain`, `Nia Vault`, `agentmemory`, and `Nowledge Mem`, the consistent lessons are:

1. The product must prove one useful loop quickly.
2. The system must expose why it knows something, not just what it knows.
3. Maintenance and background processing must feel like first-class product behavior.
4. Search and browsing are not enough by themselves; the user must also see what changed and why.
5. Integration quality must be visible and testable from the product surface.

OVP should treat these as milestone guardrails, not optional polish.

## Milestone Adjustments From External References

### Milestone 3: Review Workbench Completion

Add:

- “How to know this queue is working” operator checks,
- explicit queue rationale and evidence panels,
- dashboard surfacing of recent maintenance effects.

This is directly reinforced by Nowledge Mem’s verification model and GBrain’s operator framing.

### Milestone 4: Event And Contradiction Model Hardening

Add:

- contradiction explanation quality as a first-class product requirement,
- clearer event semantics and reason-for-appearance messaging,
- better graph-aware relation context where appropriate.

This is reinforced by Nia’s page contract and Nowledge Mem’s background-intelligence framing.

### Milestone 5: Knowledge Production Traceability

Add:

- explicit “first useful loop” surfaces:
  - source saved,
  - deep dive generated,
  - evergreen derived,
  - Atlas placement updated,
- verification views that let users prove the pipeline really produced the knowledge they are seeing.

This is reinforced by Nowledge Mem’s “save / find / use” loop and verification framing.

### Milestone 6: Product Shell And Operator UX

Add:

- better onboarding / “start here” paths,
- “how to know it is working” checks inside the product,
- observability panels for indexing, search, maintenance, and review state,
- more explicit product-level integration and automation surfaces.

This is reinforced by agentmemory’s observability and Nowledge Mem’s onboarding / browse-now packaging.
