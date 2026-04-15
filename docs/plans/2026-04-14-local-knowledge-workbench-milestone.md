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

Status: **Complete**

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

### Milestone 3: Review Workbench Completion

Status: **Complete**

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

Status: **In Progress**

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

Status: **In Progress**

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

Current progress:

- `Production Chain` is visible on `note` and `object` pages
- `/atlas` and `/deep-dives` show contribution summaries
- `/production` exists as a provenance-first aggregate browser over source notes and deep dives

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

## From Local Knowledge Workbench To Active Knowledge System

The next transition is not “add more pages.” It is a mode change:

- a **local knowledge workbench** lets a user inspect, trace, and maintain knowledge,
- an **active knowledge system** keeps improving what the user can see, recall, and act on even when the user is not manually driving every step.

That transition should stay constrained by a few hard product rules:

1. **Thin harness, fat skills.**
   The central runtime should stay small. New intelligence should land as explicit extraction, maintenance, synthesis, and briefing layers, not as an opaque monolith.
2. **Brain-first lookup before external lookup.**
   The product should prefer the user’s own vault and truth store before searching the web or re-deriving context from scratch.
3. **Compiled truth plus evidence trail.**
   Every “smart” synthesis must keep a visible path back to source notes, deep dives, evergreen objects, and review history.
4. **Async intelligence, synchronous trust.**
   Detection, linking, clustering, and briefing can run in the background, but the user-facing product must show what changed and why.
5. **First useful sign before broad rollout.**
   New intelligence layers must prove one non-obvious, source-cited win before expanding scope.

### Milestone 7: Active Signal Loop

Status: **In Progress**

Goal:

Turn new notes, updated notes, and review actions into first-class signals that can enrich the system automatically without blocking the main user flow.

Core deliverables:

- signal detection on every meaningful inbound note/save/update,
- explicit capture of notable entities, concepts, projects, and original observations,
- mandatory back-links from extracted entities/objects to the triggering source,
- brain-first lookup rules before creating new objects or links,
- signal audit entries showing what was detected, created, linked, or skipped.

Exit condition:

- saving or updating a note can improve future retrieval and navigation without requiring a manual full pipeline run every time.

Current slice:

- deterministic signal ledger over existing trusted maintenance surfaces,
- signal browser in `ovp-ui`,
- dashboard signal surfacing,
- persisted signal rows in logs plus mirrored `audit_events`,
- review-action-derived change signals for contradiction resolution and summary rebuild,
- extraction-trigger signals for missing deep dives and missing downstream objects,
- briefing-ready snapshot payloads over recent signals, unresolved issues, changed objects, and active topics.

### Milestone 8: Knowledge Evolution Layer

Status: **Complete**

Goal:

Model how understanding changes over time instead of only surfacing “contradiction” and “stale” as isolated maintenance states.

Core deliverables:

- typed evolution links:
  - `replaces`
  - `enriches`
  - `confirms`
  - `challenges`
- richer typed entity and semantic relation extraction for `research-tech`,
- evolution views on object/topic pages,
- explicit explanations for “what changed” and “what stayed stable”.

Exit condition:

- users can trace how a topic evolved across notes and reviews, not just detect isolated conflicts.

Current progress:

- deterministic evolution candidates now exist for:
  - `replaces`
  - `enriches`
  - `confirms`
  - `challenges`
- `/evolution` and `/api/evolution` expose candidate plus reviewed evolution links,
- object and topic pages render `Evolution` sections,
- evolution review actions persist accepted and rejected links,
- the layer is reviewable without pretending candidate links are already truth.

### Milestone 9: Background Intelligence

Status: **In Progress**

Goal:

Make the product proactively surface useful findings instead of waiting for the user to open every queue manually.

Core deliverables:

- source-cited insights surfaced from the graph and timeline,
- working-memory style briefings:
  - active topics
  - unresolved flags
  - recent changes
  - priority items
- explicit “first useful sign” checks proving the layer is adding value,
- controls for enabling, throttling, and verifying background processing.

Exit condition:

- the system can surface at least one relevant contradiction, one useful synthesis, or one actionable priority the user likely would not have found unaided.

Current progress:

- `/briefing` is no longer a raw snapshot page; it now surfaces:
  - `First Useful Sign`
  - `Insights`
  - `Priority Items`
- `signals` and `briefing` now attach explicit `Recommended Action` metadata,
- deterministic briefing intelligence is grounded in existing signals and evolution links,
- UI cold-start no longer blocks on full evolution recomputation because caches are prewarmed at startup.

### Milestone 9A: Background Intelligence Orchestration Integration

Status: **Planned**

Goal:

Keep `signals`, `briefing`, and `recommended actions` as observation and prioritization surfaces while unifying all execution behind one action queue plus worker layer that dispatches into the existing `ovp` runtime.

Core deliverables:

- one execution surface:
  - action queue
  - worker
  - workflow handler registry
- explicit relationship between focused queue actions and broad batch execution through `ovp --full`,
- policy-driven auto-queue for low-risk deterministic actions:
  - `source_needs_deep_dive`
  - `deep_dive_needs_objects`
- queue state surfaced back into `signals` and `briefing`,
- worker-side precondition checks and idempotent dedupe.

Exit condition:

- the product has many observation surfaces but only one execution surface, and `ovp --full` remains the batch reconciler instead of competing with a second workflow engine.

Reference plan:

- [2026-04-15-phase14-orchestration-integration-plan.md](2026-04-15-phase14-orchestration-integration-plan.md)
- [2026-04-15-stage-handler-registry-design.md](2026-04-15-stage-handler-registry-design.md)

Architecture follow-up:

- [2026-04-15-ovp-layer-contract.md](2026-04-15-ovp-layer-contract.md)
  OVP now needs a deeper separation between core runtime, pack surfaces, domain execution hooks,
  domain truth projection, and domain UI semantics so the product does not remain implicitly bound
  to `research-tech` while it grows beyond the first in-repo pack.

Implementation sequence:

1. `Stage Handler Registry`
   Extract execution dispatch from current in-repo handler coupling so profile execution,
   autopilot, and queue actions share one handler contract.
2. `Pack-Aware Truth Projection`
   Move truth-building semantics behind domain-aware contracts instead of keeping them implicitly
   bound to `research-tech`.
3. `Pack-Aware Observation Surfaces`
   Generalize `signals`, `briefing`, `production`, and related product semantics after execution
   and truth layers are no longer hard-coded.

### Milestone 10: Graph Intelligence And Synthesis

Status: **Not Started**

Goal:

Use the existing truth graph as a substrate for topic clustering, cross-domain discovery, and higher-order synthesis.

Core deliverables:

- community detection over objects and relations,
- cluster labels and topic maps,
- cross-domain connection surfacing,
- “crystal”-style synthesized reference views that update when related knowledge changes,
- visual and query surfaces that explain why items belong together.

Exit condition:

- users can see coherent topic clusters, cross-domain links, and evolving synthesized references grounded in source-cited knowledge.

## Recommended Next Sequence

### Finish Phase 10: Event + Contradiction Hardening

Do first.

Reason:

- the review workbench is now strong enough that model weaknesses are visible,
- event and contradiction semantics need to become trustworthy before we add more intelligence on top.

### Phase 11: Knowledge Production Traceability

Do next.

Reason:

- this is still the most legible product-value surface,
- it closes the source -> deep dive -> evergreen -> Atlas chain,
- it prepares the evidence trail required for later active intelligence.

### Phase 12: Active Signal Loop

Do after Phase 11.

Reason:

- once production traceability is strong, asynchronous signal capture can safely create backlinks, entities, and updates,
- this is the first real step from “workbench” to “active system.”

### Phase 13: Knowledge Evolution Layer

Do after Phase 12.

Reason:

- evolution links depend on better signal capture and graph linking,
- they turn the current maintenance model into a more complete model of changing understanding.

### Phase 14: Background Intelligence

Do after Phase 13.

Reason:

- briefings, insights, and proactive flags should sit on top of a stable evolution layer,
- otherwise they become noisy product theater instead of useful intelligence.

### Phase 15: Graph Intelligence And Synthesis

Do after Phase 14.

Reason:

- community detection, clusters, and crystal-like synthesis become much more useful once the graph and evolution layers are already trustworthy.

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
- Milestone 2: complete
- Milestone 3: complete
- Milestone 4: in progress
- Milestone 5: in progress
- Milestone 6: not started
- Milestone 7: in progress
- Milestone 8+: not started

So the honest product statement is:

> OVP is already a usable local knowledge workbench core with a real review console, but it is not yet an active knowledge system.

The remaining gap is no longer “can this architecture support it?” It is now a sequence problem:

- finish semantic hardening,
- make the production chain legible,
- then add signal capture, evolution, and background intelligence in that order.

## PR Review Gate

Every milestone PR must now pass an explicit review-wait step before merge. The sequence is:

1. fix all known blocking review findings,
2. run fresh verification on the exact branch head,
3. wait for review automation and comments to settle,
4. re-check PR comments and mergeability after that wait,
5. merge only if no new blocking feedback appears.

This is a required completion gate, not an informal judgment call. A green local test run is necessary but not sufficient if review automation is still actively producing new findings.

## External Reference Projects And What To Borrow

This milestone should stay grounded in concrete product references instead of inventing abstract architecture in isolation.

### GBrain

Reference:

- `gbrain` repository

What matters:

- `THIN_HARNESS_FAT_SKILLS`: keep the orchestration layer small and push domain behavior into explicit skills and operating rules,
- `brain-agent-loop`: the agent should repeatedly check the brain first, then enrich it, then sync it back into future use,
- `compiled-truth`: pages should feel like stable synthesized truth with an evidence/history layer underneath,
- `brain-first-lookup`: lookup order is a product invariant, not an implementation detail,
- `entity-detection`: every inbound signal can update the brain asynchronously,
- `source-attribution`: every claim should be auditable back to origin.

What OVP should borrow:

- stronger brain-first lookup and write-back protocol,
- active signal detection that compounds the vault over time,
- clearer separation between thin runtime harness and richer maintenance/synthesis skills,
- compiled-truth page contracts that keep synthesis and evidence visibly paired.

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

What OVP should additionally borrow from its advanced-features model:

- explicit extraction targets:
  - entities
  - relationships
  - links to existing knowledge
- explicit knowledge-evolution links:
  - `replaces`
  - `enriches`
  - `confirms`
  - `challenges`
- clear “first useful sign” acceptance criteria for new intelligence,
- briefings and insights that are source-cited and intentionally sparse,
- graph intelligence as a later layer on top of a trusted graph, not as the first feature.

## Cross-Project Product Principles

Across `gbrain`, `Nia Vault`, `agentmemory`, and `Nowledge Mem`, the consistent lessons are:

1. The product must prove one useful loop quickly.
2. The system must expose why it knows something, not just what it knows.
3. Maintenance and background processing must feel like first-class product behavior.
4. Search and browsing are not enough by themselves; the user must also see what changed and why.
5. Integration quality must be visible and testable from the product surface.
6. Active intelligence should arrive in layers:
   signal capture first, evolution second, briefing/insight third, graph synthesis last.

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

### Milestone 7: Active Signal Loop

Add:

- async signal detection on note create/update and selected review actions,
- brain-first lookup before object creation,
- mandatory backlinks from detected entities/concepts to source notes,
- signal audit logs showing what changed and why.

This is reinforced by GBrain’s `brain-agent-loop`, `brain-first-lookup`, and `entity-detection` guides.

### Milestone 8: Knowledge Evolution Layer

Add:

- typed evolution links:
  - `replaces`
  - `enriches`
  - `confirms`
  - `challenges`
- richer semantic relation extraction for the `research-tech` pack,
- object/topic views that show how understanding changed over time.

This is reinforced by Nowledge Mem’s knowledge-evolution model and Nia’s stronger page contracts.

### Milestone 9: Background Intelligence

Add:

- working-memory briefings,
- sparse source-cited insights,
- explicit “first useful sign” checks,
- queue and graph-derived priority surfacing.

This is reinforced by Nowledge Mem’s background-intelligence model and GBrain’s ongoing maintenance framing.

### Milestone 10: Graph Intelligence And Synthesis

Add:

- community detection,
- cluster labeling,
- crystal-like synthesized reference pages,
- cross-domain connection surfacing.

This is reinforced by Nowledge Mem’s graph features and Nia’s graph/product page ambitions.
