# OVP Architecture Mapping

## Purpose

Clarify how the proposed four-layer model relates to the current OVP architecture, and how it should fit with the existing `Core Platform / Domain Pack / Workflow Profile` structure instead of replacing it.

## The Core Point

The current system and the proposed four-layer model describe **different axes**.

- The current **six-layer OVP pipeline** describes **execution stages over time**
- `Core Platform / Domain Pack / Workflow Profile` describes **ownership and extension boundaries**
- The proposed **four-layer model** describes **persistent architectural boundaries and state ownership**

These are not competing diagrams. They should stack.

## Axis 1: Current OVP Runtime Pipeline

This is the execution DAG the system already uses:

`Ingest -> Interpret -> Absorb -> Refine -> Canonical -> Derived`

This axis answers:

- what runs first
- what transforms source into knowledge
- what is allowed to use LLM judgment
- where canonical decisions stop and derived rebuildable state begins

This is still correct and should stay.

## Axis 2: Current Platform Ownership Model

The project already defines:

- `Core Platform`
- `Domain Pack`
- `Workflow Profile`

This axis answers:

- what belongs to stable core
- what belongs to domain semantics
- what belongs to a runnable DAG/profile

This is also correct and should stay.

## Axis 3: Proposed Persistent Layer Model

The proposed four layers are:

1. `Canonical Knowledge`
2. `Derived Indexes / Views`
3. `Context Assembly / Access`
4. `Governance / Resolver / Review`

This axis answers:

- what is source of truth
- what is rebuildable
- what turns persistent knowledge into agent-readable context
- what governs routing, review, contradiction handling, and operator control

This is the layer model that is currently only partially explicit.

## How They Map Together

### A. Six-layer pipeline -> four-layer model

`Ingest`
- Feeds candidate material into Layer 1 and Layer 4 inputs
- Does not itself own truth

`Interpret`
- Produces structured intermediate material that can become Layer 1 candidates
- Often feeds reviewable absorb inputs

`Absorb`
- Main entry point into Layer 1 `Canonical Knowledge`

`Refine`
- Edits and normalizes Layer 1 artifacts
- Often under Layer 4 governance

`Canonical`
- Maintains Layer 1 consistency
- Also triggers Layer 4 review / identity discipline

`Derived`
- Builds Layer 2 `Derived Indexes / Views`
- Also supports Layer 3 `Context Assembly / Access`

### B. Platform / Pack / Profile -> four-layer model

`Core Platform`
- Owns the framework for all four layers
- Provides runtime, identity helpers, truth projection infra, UI shell, export shell, audit, queueing, and governance primitives

`Domain Pack`
- Supplies the semantics inside the layers
- Defines object kinds, schemas, absorb/refine rules, overview types, contradiction heuristics, assembly recipes, and governance policies

`Workflow Profile`
- Chooses which execution stages populate or refresh which layers, and in what order

## What The Current Architecture Already Has

### Layer 1: Canonical Knowledge

Already present, but not fully explicit as a first-class artifact model.

Current components:

- Vault Markdown
- concept registry
- pack-owned schemas and templates

Important nuance:

- `truth_store.py` already contains `objects / claims / claim_evidence / contradictions`
- but these are still **projections**, not the canonical source itself

That means the future artifact model is already visible in code, but it is not yet explicitly made the main architectural language.

### Layer 2: Derived Indexes / Views

Already strong.

Current components:

- `knowledge.db`
- truth projections
- graph views
- clusters
- lint outputs
- compiled summaries
- UI payload builders

This is probably the most mature of the four layers today.

### Layer 3: Context Assembly / Access

Already exists, but is still fragmented.

Current components:

- `ovp-export`
- `ovp-truth`
- `ovp-ui`
- object / topic / event / contradiction view models
- shell surfaces like briefing / signals / production

This means OVP is already doing context assembly, but it has not yet been declared as a core architectural layer with a clean contract.

### Layer 4: Governance / Resolver / Review

This exists in fragments and is the least explicit.

Current components:

- concept `review_state`
- candidate promotion / rejection
- review queue artifacts
- contradictions review
- stale summary review
- action queue
- signals log / signal ledger
- truth API review actions

So governance is not missing. It is **present but not yet unified**.

## The Architectural Upgrade Path

The right move is not to replace the current architecture.

The right move is:

1. Keep the current six-layer execution pipeline
2. Keep the current `Core / Pack / Profile` extension model
3. Make the four persistent layers explicit across both

In other words:

- six layers remain the runtime DAG
- core/pack/profile remain the ownership model
- four layers become the stable architectural contract

## What Should Change Next

### 1. Make Layer 1 explicit

Define artifact schema v1 in architectural language, not just projection language.

Recommended starting artifacts:

- `Object`
- `Claim`
- `Evidence`
- `Overview`
- `ReviewItem`

### 2. Make Layer 3 explicit

Treat context assembly as a first-class subsystem.

Recommended surfaces:

- orientation brief
- object brief
- topic overview
- event dossier
- contradiction view
- delta digest

### 3. Make Layer 4 explicit

Turn scattered review/governance behaviors into a declared architecture.

Recommended primitives:

- resolver
- review queue
- routing/reachability checks
- contradiction lifecycle
- audit trail

## How New Ideas Should Enter The System

New ideas should not go straight into core as ad hoc features.

They should enter through this sequence:

1. `Pack semantics first`
   - object kind
   - schema
   - absorb/refine rule
   - assembly recipe
   - review rule

2. `Profile wiring second`
   - when does this run
   - on full vs autopilot vs special profile

3. `Core graduation last`
   - only after a pattern is clearly shared across packs

This keeps OVP from turning back into a monolithic domain-specific system.

## Bottom Line

The four-layer model is **not a replacement** for the current architecture.

It is the missing architectural interpretation layer that explains:

- where truth lives
- what is derived
- how agents get assembled context
- how governance actually works

Short version:

- `OVP six layers` = execution pipeline
- `Core / Pack / Profile` = extension / ownership model
- `Four layers` = persistent architecture contract

All three should coexist.
