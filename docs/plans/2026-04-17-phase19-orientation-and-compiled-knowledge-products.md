# Phase 19: Orientation And Compiled Knowledge Products

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** complete / ready to close

**Goal:** Turn the now-explicit knowledge-compiler contracts into user-facing entry products so OVP feels like an active knowledge system, not just a powerful local console.

**Architecture:** `Phase 17` delivered graph exploration. `Phase 18` delivered explicit artifact, assembly, and governance contracts. `Phase 19` should spend those contracts on product semantics: better orientation, stronger compiled pages, and clearer “where do I go next?” loops. This phase should stay inside the existing `research-tech` pack and reuse the contract stack rather than opening new infrastructure tracks.

**Tech Stack:** Existing `AssemblyRecipeSpec` / `GovernanceSpec`, `research-tech` assembly recipes, `ovp-ui`, `ovp-export`, `ui/view_models.py`, `ui_server.py`, pack-owned wiki views and observation surfaces.

## Why This Is The Right Next Phase

The current architecture is now in a good state:

- graph exploration exists,
- contract boundaries are explicit,
- review and runtime provenance are visible,
- export/UI/doctor all speak the same pack language.

But the product still has a real gap:

- a new or returning user still has to infer where to start,
- object/topic/event pages are useful but not yet strong “knowledge entry products,”
- the workbench still behaves more like a capable operator console than a system that orients you immediately.

So the next move should **not** be:

- temporal truth hardening,
- harness/session memory,
- benchmark infrastructure,
- or another backend-heavy subsystem.

The next move should be:

- make the current knowledge state easier to enter,
- make compiled products feel deliberate,
- and make the next useful action obvious.

## Product Thesis

After `Phase 19`, a user should be able to land in OVP and answer three questions quickly:

1. What does the system currently know that matters?
2. Where should I start reading?
3. What should I do next?

That means the primary output of this phase is not a new database or runtime abstraction.
It is a set of **entry products**:

- an orientation brief,
- stronger object/topic/event/contradiction pages,
- and a workbench home that explains the current state of the vault in one screen.

## What Phase 19 Must Deliver

### 1. Orientation Brief v1

Add a first-class entry artifact that compiles:

- active topics
- recent changes
- unresolved issues
- recommended next reads
- recommended next actions

This should be the productized version of what the current `/briefing` route hints at, not a second parallel dashboard.

### 2. Compiled Page Contracts v1

Strengthen the product shape of:

- object pages
- topic overviews
- event dossiers
- contradiction views

Each of these should feel like a deliberate compiled page with stable sections, not only a raw data surface.

Minimum page-contract expectations:

- current state
- why it matters
- evidence / traceability
- open issues or tensions
- where to go next

### 3. Workbench Home / Entry Screen

Add a clear default product entry surface that answers:

- what changed recently
- what is important right now
- what deserves review
- what the system recommends next

This should use the same assembly/governance contract stack rather than bypassing it.

### 4. Cross-Surface Next-Step Links

Pages should explicitly route users between:

- orientation -> topic
- topic -> object
- object -> event / contradiction / deep dive
- contradiction -> review
- signals/actions -> relevant compiled page

The goal is not more links. The goal is better **navigation intent**.

### 5. Exportable Entry Products

The new orientation artifact and strengthened compiled pages should be exportable through `ovp-export`, not trapped inside the local UI.

## What Phase 19 Should Not Do

Explicit deferrals:

- full temporal truth semantics
- pack-agnostic memory backend work
- graph workspaces / pinning / route bookmarking
- benchmark platform work
- multi-user or hosted product shell
- external domain pack expansion

Those can all be valid later.
They are not the highest-leverage next move.

## Recommended Implementation Shape

### Task 1: Add Orientation Recipe To Research-Tech

**Files:**
- Modify: `src/openclaw_pipeline/packs/research_tech/assembly_recipes.py`
- Modify: `src/openclaw_pipeline/packs/research_tech/pack.py`
- Modify: `src/openclaw_pipeline/commands/export_artifact.py`
- Test: `tests/test_export_command.py`

Deliverable:

- a new `orientation_brief` assembly recipe
- export target support for the orientation artifact

Status:

- complete

### Task 2: Strengthen Briefing Payload Into Orientation Product

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

Deliverable:

- `/briefing` evolves into a true orientation page
- stable sections for:
  - what changed
  - what matters
  - what needs review
  - what to read / do next

Status:

- complete

### Task 3: Add Compiled Page Section Contracts

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Reference: existing page builders and contract cards
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

Deliverable:

- object/topic/event/contradiction pages expose stable compiled sections
- each page explicitly answers:
  - what is this
  - why does it matter
  - what evidence anchors it
  - what tensions remain
  - what should the user open next

Status:

- complete

### Task 4: Add Workbench Home Entry Surface

**Files:**
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Test: `tests/test_ui_server.py`

Deliverable:

- the root/home experience becomes a real workbench entry point
- it routes clearly into orientation, production, review, graph, and compiled pages

Status:

- complete

### Task 5: Verification And Pack Docs

**Files:**
- Modify: `docs/pack-api/README.md`
- Modify: `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`

Deliverable:

- docs explain the new orientation product and page contract expectations
- verify docs include export + UI checks for the new entry products

Status:

- complete

## Implementation Notes

What shipped in this phase:

- `research-tech` now declares a first-class `orientation_brief` assembly recipe
- `ovp-export --target orientation-brief` exports a compiled JSON entry product
- `/briefing` now resolves through the same contract stack and behaves as an orientation page
- `/` workbench home now exposes entry sections and routes users into the orientation surface
- object/topic/event/contradiction payloads now expose stable `compiled_sections` and `section_nav`
- shared UI pages render those compiled sections instead of relying on raw list surfaces alone
- docs and verify checklists now treat orientation and compiled-page sections as part of the pack contract

## Exit Condition

`Phase 19` is complete when all of the following are true:

1. OVP has a clear orientation artifact.
2. `/briefing` behaves like a real knowledge entry product.
3. object/topic/event/contradiction pages expose stable compiled sections.
4. the home/workbench entry clearly routes the user into the most useful next surfaces.
5. export and UI both surface the new entry products through the same contract stack.

## What Comes After Phase 19

Only after `Phase 19` should OVP seriously consider:

1. saved graph workspaces and route bookmarking
2. richer synthesis overlays on graph surfaces
3. temporal truth hardening
4. deeper signal capture / session memory
5. benchmark and evaluation framing

That keeps the system growing in the right order:

- first graph,
- then explicit contracts,
- then entry products,
- then deeper intelligence and automation.
