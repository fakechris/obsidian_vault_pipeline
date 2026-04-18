# Phase 17: Research Graph Visualization And Exploration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the existing research graph into a first-class visual exploration surface with an infinite canvas, progressive drill-down, and usable graph navigation that does not collapse under data volume.

**Architecture:** `Phase 15` already built the graph substrate, cluster detail, crystal views, and research navigation hints. `Phase 16` made pack/runtime ownership explicit. `Phase 17` stays inside `research-tech` and spends that foundation on a graph visualization product layer: cluster-first entry points, bounded subgraph loading, canvas-native navigation, and rich side-panel detail instead of dumping the full graph at once.

**Tech Stack:** Existing `truth_api.py` graph endpoints, `ui/view_models.py`, `ui_server.py`, pack-owned `research-tech` truth projection and surfaces, compiled cluster artifacts, local UI shell.

## Why This Is The Right Next Phase

The platform is no longer the blocker.

What users still do **not** have is a strong visual graph experience:

- they can browse clusters and crystal pages, but not really explore the graph spatially
- they can inspect relationships, but not move through the graph the way a human research user expects
- the graph is rich enough now that a naive “show everything” visualization would become unreadable and slow

So `Phase 17` should not add Media Pack work and should not reopen multi-pack runtime hardening.
It should focus on one thing:

- make `research-tech` graph intelligence visually explorable, performant, and legible

## Product Direction

The right product shape is **not** “open a giant graph with every node visible.”

The right product shape is:

1. **cluster-first entry**
   - users start from ranked clusters, briefing, signals, or object pages
   - every entry opens a bounded graph context, not the whole vault

2. **infinite canvas as workspace**
   - the canvas should pan and zoom freely
   - the user can expand neighborhoods incrementally
   - the canvas is for structure and traversal, not for showing every field inline

3. **progressive disclosure**
   - zoomed out: show cluster/group shapes, labels, pressure badges
   - mid zoom: show objects and dominant relation structure
   - focused selection: show detailed metadata in a side panel, not inside every node

4. **text + graph together**
   - cluster crystal, detail panels, and source-backed summaries remain important
   - graph exploration should deepen reading, not replace it

## UX Model

### Primary Entry Points

Users should enter the graph from:

- `/clusters`
- `/cluster`
- `/object`
- `/briefing`
- `/signals`

Each of those should offer an “open in graph” action that lands in a scoped visual state.

### Canvas Composition

The canvas should have three layers:

1. **graph stage**
   - nodes
   - edges
   - cluster hulls / grouped regions

2. **selection rail**
   - object detail
   - cluster detail
   - relation explanation
   - review pressure
   - source / atlas / next-read context

3. **control bar**
   - zoom and recenter
   - relation filters
   - cluster/object mode
   - “expand neighbors”
   - “show review pressure”
   - “open crystal”

### Information Density

To keep the canvas rich without becoming unreadable:

- do not render full summaries directly on nodes by default
- do render:
  - label
  - kind
  - pressure badge
  - structural role cue
- move dense information into:
  - hover preview
  - side panel
  - crystal/detail jump targets

## Performance Strategy

The performance rule is simple:

- never render the entire graph by default

Instead:

1. **cluster-level overview mode**
   - start from cluster nodes and bridge strengths
   - object-level nodes appear only after drilling in

2. **bounded subgraph fetch**
   - fetch selected cluster members
   - fetch one-hop and optional two-hop neighborhoods on demand
   - cap expansion sizes and show explicit “expand more” controls

3. **viewport-aware simplification**
   - fewer labels when zoomed out
   - fewer edge decorations at low zoom
   - group edges where possible instead of drawing everything equally

4. **layout caching**
   - cache cluster-level layouts
   - cache recent neighborhood layouts
   - avoid recomputing force layouts on every open

5. **side-panel detail over node bloat**
   - heavy data should be displayed off-canvas
   - the canvas should stay navigable even on large neighborhoods

## Workstreams

### 1. Graph Data And API Shaping

**Owner:** core/platform + research graph layer

**Deliverables:**

- graph payloads for:
  - cluster overview graph
  - cluster-local object graph
  - object neighborhood graph
- bounded expansion endpoints
- relation filtering and graph mode parameters
- cached layout / simplified graph metadata where needed

### 2. Research Graph Semantics

**Owner:** research team

**Deliverables:**

- graph-visible structural labels
- dominant relation typing for display
- review pressure overlays
- stronger bridge / route explanations for graph traversal
- clean mapping between graph selection and crystal/detail content

### 3. Visual Graph Experience

**Owner:** UI/UX + frontend implementation

**Deliverables:**

- infinite canvas with pan / zoom / recenter
- cluster-first and object-first graph modes
- side panel for selection details
- graph controls for filters and expansion
- transitions between:
  - clusters
  - cluster detail
  - graph canvas
  - crystal views

## What Each Group Actually Does

### Core / Platform

- expose the right graph payloads
- keep fetches bounded and cacheable
- add graph-specific shell routes without reopening pack/runtime ownership

### Research Team

- decide what graph objects and relations deserve emphasis
- decide what metadata belongs on-node versus in the detail rail
- refine “why this node/cluster matters” and “where to go next”

### UI / UX

- design the canvas interaction model
- control density by zoom level and selection state
- make exploration feel rich without turning the screen into graph sludge

## User-Visible Outcome

After `Phase 17`, a research user should feel these changes:

1. They can **see** the graph, not just infer it from lists.
2. They can move through the graph spatially without being overwhelmed.
3. They can discover bridges, contradictions, stale zones, and synthesis routes faster.
4. They can open a graph from a research task and return to textual detail when needed.

The meaning is straightforward:

- the graph becomes an actual research instrument
- cluster intelligence becomes easier to trust and act on
- the product starts to feel substantially more powerful than a text-only local console
## Exit Condition

`Phase 17` is complete when all of the following are true:

- users can open a bounded graph canvas from cluster/object/research entry points
- the canvas supports real exploration:
  - pan
  - zoom
  - select
  - expand
- the default graph view stays performant on real vault data
- the graph uses progressive disclosure instead of flooding the screen
- graph exploration and crystal/detail reading reinforce each other
## What Comes After Phase 17

If `Phase 17` succeeds, the next major priorities are likely:

1. `Phase 18`: knowledge-compiler contract consolidation
   - explicit artifact contracts
   - explicit assembly contracts
   - explicit governance contracts
   - make the current system legible as a pack-declared knowledge compiler instead of a set of scattered runtime surfaces

Reference plan:

- [[2026-04-17-phase18-knowledge-compiler-contract-consolidation-plan]]

Why this comes first:

- `Phase 17` improves graph exploration as a product surface
- but OVP still needs a cleaner contract story for:
  - what artifacts it owns
  - what compiled products it assembles
  - what review/signal/action policy it exposes
- richer graph work after `Phase 17` should land on top of those clearer contracts, not before them

2. richer graph actions
   - pinning
   - saved workspaces
   - route bookmarking

3. stronger synthesis
   - generated narratives on top of the deterministic graph surfaces
   - richer cluster and neighborhood explanations

4. cross-pack product work
   - only after Media Pack is ready
   - only without reopening core ownership

5. collaboration and operational polish
   - onboarding into graph workflows
   - better local packaging and deployment ergonomics
