# OVP Layer Contract

**Goal:** Clarify which OVP layers are truly core, which must become pack-aware, and which current `research-tech` semantics must be moved out of the generic runtime.

**Why this document exists:** OVP has already extracted pack metadata, workflow profile selection, and several operator surfaces. But deeper runtime layers are still partially coupled to `research-tech` assumptions. That is acceptable for the first in-repo pack, but it is not sufficient for external domain systems such as media or medical workflows.

## 1. Current Diagnosis

OVP is no longer a single-domain vault script bundle. It is already operating as:

- a local-first runtime,
- a derived SQLite truth and retrieval layer,
- a review and maintenance shell,
- a pack-capable workflow platform.

However, the current separation stops too early.

### What is already platform-like

- runtime and vault layout
- audit and logging
- pack loading and profile selection
- extraction / operation / wiki view specs
- queue and worker direction
- UI shell and local HTTP product surface

### What is still implicitly `research-tech`

- `knowledge.db` rebuild source assumptions
- truth projection rules
- contradiction detection semantics
- event / timeline semantics
- focused workflow handlers behind the action queue
- several UI payloads that look generic but actually encode current knowledge-pack assumptions

## 2. The Current Leakage

The following modules are still more domain-bound than their current names suggest.

### Leakage A: `knowledge_index.py` is not yet pack-aware

Current behavior:

- rebuild scans current evergreen, Atlas, and area directories
- rows are projected from page-centric Markdown assumptions
- truth rows are derived from current note layout and current registry semantics

This means OVP currently has a generic DB container but a non-generic rebuild contract.

### Leakage B: `truth_store.py` encodes one provisional knowledge model

Current behavior:

- one page becomes one object row
- one page summary becomes one main claim
- contradiction detection is built on page-summary polarity heuristics

This is a useful first truth layer, but it is not a domain-neutral truth projection contract.

### Leakage C: several UI surfaces are generic in name, specific in meaning

Examples:

- `Event Dossier` is currently a dated-note projection, not a domain event system
- `Production` means source note -> deep dive -> evergreen -> Atlas
- `Briefing` currently prioritizes maintenance and evolution over domain-specific desk work

These are valid product surfaces, but they are not yet generic pack surfaces.

### Leakage D: action handlers still dispatch into current knowledge workflows

The queue design is correct:

- many observation surfaces
- one execution surface

But the handler layer is still coupled to current deep-dive and evergreen extraction flows.

## 3. Target Layer Contract

OVP should be treated as six layers.

## Layer 1. Core Runtime

Owns:

- vault layout
- CLI entrypoints
- logging and audit
- queue and worker lifecycle
- plugin loading
- DB file lifecycle and rebuild orchestration
- UI shell, routing, auth-free local server

Must remain generic.

Must not know:

- media object semantics
- medical evidence semantics
- research-tech-only object semantics

## Layer 2. Pack Surface

Owns:

- object kind declarations
- workflow profiles
- extraction profiles
- operation profiles
- wiki view declarations

This is the layer OVP already has in usable form.

Constraint:

- this layer describes domain surfaces
- it does not itself execute domain logic

## Layer 3. Domain Execution Hooks

Owns:

- stage handler registry
- focused workflow handlers
- processor mode metadata
- processor input/output contracts
- quality and evaluation hooks

This layer decides:

- which stages are rule-based
- which are LLM-structured
- which are hybrid
- which require human review

Core should only orchestrate this layer. Core should not encode domain processor logic directly.

## Layer 4. Domain Truth Projection

Owns:

- which domain objects can be projected into the truth layer
- how claims, relations, evidence, summaries, and contradictions are derived
- which projections are deterministic vs review-gated

Core should own:

- the SQLite container
- rebuild transaction discipline
- read/query interfaces

Packs should own:

- projection rules
- projection validation
- truth-specific review semantics

Constraint:

- `knowledge.db` remains derived, never canonical
- but its truth projection cannot stay hard-coded to one in-repo pack forever

## Layer 5. Domain Product Surfaces

Owns:

- screen-level payload contracts
- domain browser semantics
- domain queue semantics
- domain dashboards and briefing semantics

Core should own:

- shell layout
- common widgets
- query plumbing
- review action mechanics

Packs or domain systems should own:

- what an event browser means
- what a briefing means
- what a production chain means
- what a stale object means

## Layer 6. Materialization And Exports

Owns:

- compiled Markdown exports
- object pages
- dossiers
- overview pages
- external operator artifacts

Constraint:

- materializers must read from canonical and derived state through stable contracts
- they must not become a second hidden execution engine

## 4. What Stays In Core

The following should stay centralized and become stricter over time:

- audit and logging contracts
- queue state machine
- idempotency rules
- plugin discovery
- deterministic IDs where core guarantees them
- rebuild discipline for derived stores
- navigation shell and generic review affordances
- search and query infrastructure

## 5. What Must Become Pack-Aware

The following should not remain `research-tech` defaults in disguise:

- truth projection builders
- contradiction builders
- event projection builders
- production-chain builders
- signal builders
- briefing builders
- action handler registry
- domain browser payload builders

If these remain core-owned and research-tech-shaped, external packs will only be able to register names while reimplementing the real system elsewhere.

## 6. What Should Move Out Of The Generic Runtime

The following semantics should be treated as `research-tech` defaults, not core truths:

- `source note -> deep dive -> evergreen -> Atlas` as the only production-chain model
- page-summary polarity as the default contradiction model
- dated-note projection as the default event model
- current object browser assumptions over evergreen-centric objects
- maintenance-first briefing as the only briefing model

These can remain the default in-repo implementation, but they should move behind pack-aware contracts.

## 7. Relationship To `research-tech`

`research-tech` should become:

- the first complete in-repo domain implementation
- the default compatibility baseline for current vaults
- the reference implementation of pack-aware truth, UI, and queue integration

It should not remain:

- the implicit meaning of core runtime concepts

In other words:

- `research-tech` should prove the contracts
- it should not define the platform boundary by accident

## 8. Relationship To External Media Systems

For an external media domain system:

- collection and normalization can stay fully outside OVP
- an external media pack can use OVP pack surfaces and execution shell
- domain truth projection and domain product surfaces should plug into OVP through contracts, not by patching core

This is the critical design outcome:

- OVP should host external domain systems
- it should not force them to pretend they are `research-tech`

## 9. Required Next Refactors

OVP does not need a big-bang rewrite. It needs a second platform extraction pass.

### Refactor A: Stage Handler Registry

Detailed design:

- [2026-04-15-stage-handler-registry-design.md](2026-04-15-stage-handler-registry-design.md)

Introduce a first-class handler registry between:

- action queue worker
- focused workflow execution
- pack/domain-specific handlers

The worker should dispatch by contract, not by importing current knowledge processors directly.

### Refactor B: Pack-Aware Truth Projection

Split:

- DB container and rebuild orchestration
- domain truth projection rules

`knowledge_index.py` should remain core-owned for rebuild lifecycle, but should stop owning every domain projection rule.

### Refactor C: Pack-Aware UI Payload Builders

Split:

- common shell and transport
- domain payload semantics

OVP can keep one UI process while still allowing pack-aware page semantics.

### Refactor D: Processor Control Plane

Promote a configuration layer that declares:

- processor name
- stage
- mode
- input and output objects
- implementation entry
- quality hooks

This is the missing bridge between pack metadata and real execution.

### Refactor E: Signal / Briefing / Production Builders

Generalize these from:

- current knowledge-maintenance signals

to:

- pack-aware observation surfaces built on domain truth and domain policy

## 10. Migration Principle

OVP should evolve under this rule:

1. keep current `research-tech` behavior working,
2. move one deeper layer at a time behind an explicit contract,
3. do not let external packs bypass audit, queue, or rebuild discipline,
4. do not let core keep domain semantics just because the first pack needed them.

## 11. Success Condition

This effort is successful when all three statements are true:

1. `research-tech` still works as the default in-repo knowledge pack.
2. OVP core no longer assumes that every useful domain has `evergreen`, `Event Dossier`, and current contradiction semantics.
3. An external domain system such as media can reuse the runtime, queue, audit, and UI shell without reimplementing the whole platform.
