# Phase 18: Knowledge Compiler Contract Consolidation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** complete / ready to close

**Goal:** Turn the current OVP architecture from a strong collection of runtime pieces into an explicit knowledge-compiler contract by adding first-class artifact, assembly, and governance declarations without rewriting the runtime.

**Architecture:** `Phase 17` is a research-graph product layer. It spends existing graph and UI capabilities on a better exploration surface. `Phase 18` should not compete with that product work and should not reopen `Phase 16` runtime ownership hardening. Instead it should consolidate what the system already is: keep the existing `Core / Pack / Profile` model and existing execution-contract stack, then add three pack-side contract families so canonical knowledge, access products, and governance rules stop living as implicit conventions spread across handlers, exports, views, signals, and docs.

**Tech Stack:** Existing pack runtime in `src/openclaw_pipeline/packs/base.py`, pack loader/registries, `research-tech` pack declarations, `ovp-doctor`, `ovp-export`, `ovp-ui`, `truth_api.py`, `operations/runtime.py`, pack docs in `docs/pack-api/` and `docs/research-tech/`.

## Why This Is The Right Next Phase

By the end of `Phase 17`, OVP can have:

- a real graph substrate,
- a visual graph exploration product surface,
- pack-aware runtime ownership,
- shared execution contracts,
- truth projection and observation surfaces.

What it still does **not** have is a clean product-and-architecture answer to:

- what persistent artifact families the pack really owns,
- what compiled access products the pack can reliably produce,
- what governance/routing policies the pack is allowed to enforce.

That gap is now the real source of architectural ambiguity.

Without `Phase 18`, OVP risks:

- continuing to grow through scattered runtime pieces,
- making `research-tech` feel more special-case than contract-driven,
- adding future features like orientation, richer briefings, and stronger review flows without a stable place to attach them.

So `Phase 18` should not add a new intelligence layer first.
It should make the current intelligence legible and pack-declarative.

## Relationship To Phase 17

`Phase 17` and `Phase 18` are related, but they are not the same kind of work.

`Phase 17`:

- product exploration layer
- graph canvas
- cluster/object spatial navigation
- progressive disclosure in UI

`Phase 18`:

- architecture consolidation layer
- artifact contract
- assembly contract
- governance contract

The dependency is one-way:

- `Phase 18` should build on the closed assumptions from `Phase 16`
- it should respect `Phase 17` product entry points and graph-facing access products
- but it should not force `Phase 17` to wait on a full architecture rewrite

So the sequencing rule is:

- let `Phase 17` finish the graph product surface
- then use `Phase 18` to consolidate the system contract that the graph and the rest of the workbench now depend on

## What Phase 18 Must Deliver

### 1. Artifact Contract v1

Add a pack-side declaration for canonical artifact families.

Minimum `research-tech` scope:

- `object`
- `claim`
- `evidence`
- `overview`
- `review_item`

This should answer:

- what the pack treats as persistent artifacts,
- how they are identified,
- what evidence policy they require,
- how they map to canonical storage versus projection/review storage.

### 2. Assembly Contract v1

Add a pack-side declaration for compiled access products.

Minimum `research-tech` scope:

- `operator_briefing`
- `topic_overview`
- `object_brief`
- `event_dossier`
- `contradiction_view`

This should unify today’s scattered surfaces:

- `WikiViewSpec`
- `ObservationSurfaceSpec`
- export targets
- briefing/product payloads

### 3. Governance Contract v1

Add a pack-side declaration for governance and routing policy.

Minimum `research-tech` scope:

- declared review queues,
- declared signal families,
- declared recommended action kinds,
- declared basic resolver/routing rules for product-facing maintenance flows.

This does **not** mean full autonomous resolver evolution.
It means the system stops hiding queue/signal/action policy inside incidental runtime code.

### 4. Doctor / Verification Surfacing

`ovp-doctor` should surface the new contract families explicitly so a pack can prove:

- what it claims to own,
- what it can assemble,
- what governance surfaces it exposes.

### 5. Research-Tech As The First Full Contract Pack

`research-tech` should become the first pack that fully declares:

- execution
- truth projection
- observation surfaces
- artifacts
- assembly products
- governance surfaces

This is the dogfooding goal of `Phase 18`.

## What Phase 18 Should Not Do

Do **not** expand this phase into every good idea from the external survey.

Explicit deferrals:

- full temporal truth model (`valid_at / invalid_at / expired_at`)
- agent/session memory backend
- retrieval benchmark suite
- generalized multi-pack graph semantics
- hosted runtime or remote execution
- heavy onboarding/product-shell redesign

These are all valid later tracks.
They are not required to make the current architecture coherent.

## Recommended Contract Shapes

`Phase 18` should implement or scaffold the following pack-side contracts:

- `ArtifactSpec`
- `AssemblyRecipeSpec`
- `GovernanceSpec`

These should be added in the same declarative style as:

- `ExtractionProfileSpec`
- `OperationProfileSpec`
- `WikiViewSpec`

The goal is not to invent a giant new schema language.
The goal is to extend the existing pack declaration style consistently.

## Implementation Order

### Task 1: Add `ArtifactSpec` To Core Pack Base

**Files:**
- Modify: `src/openclaw_pipeline/packs/base.py`
- Modify: `src/openclaw_pipeline/commands/doctor.py`
- Reference: `src/openclaw_pipeline/extraction/specs.py`
- Reference: `src/openclaw_pipeline/wiki_views/specs.py`

Deliverable:

- new `ArtifactSpec` family plus supporting nested policy specs
- `BaseDomainPack.artifact_specs()`
- doctor payload serialization for declared artifact specs

### Task 2: Declare Research-Tech Artifact Families

**Files:**
- Create: `src/openclaw_pipeline/packs/research_tech/artifacts.py`
- Modify: `src/openclaw_pipeline/packs/research_tech/pack.py`
- Reference: `src/openclaw_pipeline/truth_store.py`

Deliverable:

- `research-tech` declares artifact families for:
  - object
  - claim
  - evidence
  - overview
  - review_item

### Task 3: Add `AssemblyRecipeSpec` To Core Pack Base

**Files:**
- Modify: `src/openclaw_pipeline/packs/base.py`
- Modify: `src/openclaw_pipeline/commands/doctor.py`
- Reference: `src/openclaw_pipeline/wiki_views/specs.py`
- Reference: `src/openclaw_pipeline/observation_surface_registry.py`

Deliverable:

- new `AssemblyRecipeSpec`
- doctor serialization for declared assembly recipes

### Task 4: Declare Research-Tech Assembly Recipes

**Files:**
- Create: `src/openclaw_pipeline/packs/research_tech/assembly_recipes.py`
- Modify: `src/openclaw_pipeline/packs/research_tech/pack.py`
- Reference: `src/openclaw_pipeline/packs/research_tech/observation_surfaces.py`
- Reference: `src/openclaw_pipeline/packs/research_tech/shared.py`
- Reference: `src/openclaw_pipeline/commands/export_artifact.py`

Deliverable:

- pack-side declarations for:
  - operator briefing
  - topic overview
  - object brief
  - event dossier
  - contradiction view

### Task 5: Add `GovernanceSpec` To Core Pack Base

**Files:**
- Modify: `src/openclaw_pipeline/packs/base.py`
- Modify: `src/openclaw_pipeline/commands/doctor.py`
- Reference: `src/openclaw_pipeline/operations/specs.py`
- Reference: `src/openclaw_pipeline/truth_api.py`

Deliverable:

- declared review queues
- declared signal rules
- declared route/action rules

### Task 6: Declare Research-Tech Governance Contract

**Files:**
- Create: `src/openclaw_pipeline/packs/research_tech/governance.py`
- Modify: `src/openclaw_pipeline/packs/research_tech/pack.py`
- Reference: `src/openclaw_pipeline/operations/runtime.py`
- Reference: `src/openclaw_pipeline/packs/research_tech/surfaces.py`

Deliverable:

- review queue families
- signal families
- recommended action mappings
- minimum resolver-like routing declarations

### Task 7: Update Docs And Operator Verification

**Files:**
- Modify: `docs/pack-api/README.md`
- Modify: `docs/pack-api/manifest-and-hooks.md`
- Modify: `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`

Deliverable:

- updated pack contract docs
- explicit verification language for the new declarations

## Exit Condition

`Phase 18` is complete when all of the following are true:

1. `research-tech` explicitly declares artifact families.
2. `research-tech` explicitly declares compiled access products.
3. `research-tech` explicitly declares review/signal/action governance surfaces.
4. `ovp-doctor --pack research-tech --json` exposes those contract families.
5. The current system can be explained coherently as:
   - execution contracts
   - artifact contracts
   - assembly contracts
   - governance contracts

Closeout note:

- `research-tech` now declares all three pack-side contract families.
- `ovp-doctor`, `ovp-export`, `truth_api`, and the shared UI shell all consume those contracts.
- API payloads for `/api/briefing`, `/api/signals`, and `/api/actions` now have explicit endpoint assertions for contract provenance.
- `Phase 18` can be treated as closed architecture work unless a new contract family ambiguity appears.

## What Comes After Phase 18

Only after `Phase 18` should OVP seriously consider:

1. richer orientation products
2. stronger evaluation and benchmark framing
3. temporal truth hardening
4. deeper harness/session capture memory
5. richer graph actions and saved graph workspaces

That keeps the architecture growing in the right order:

- first explicit contracts,
- then stronger product semantics,
- then deeper intelligence layers.
