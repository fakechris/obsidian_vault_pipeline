# Domain Pack Platform Design

## Goal

Turn the current repository from a single knowledge-pipeline product into a platform with three explicit layers:

1. **Core platform**
2. **Default domain pack**
3. **Workflow profiles**

The first standard pack is **not** media. It is the current repository's existing technical/knowledge workflow, formalized as `default-knowledge`.

Media, medical, and other specialized domains should become **separate plugin-pack projects** that can be installed into the core platform rather than hard-coded into it.

---

## Executive Decision

The correct platform shape is:

```text
openclaw-core
  + default-knowledge pack
  + workflow profiles
  + plugin loader
  + derived runtime / audit / registry infra

external packs
  + media-editorial pack
  + engineering-research pack
  + medical-evidence pack
  + future domain packs
```

This means:

- the repository stops assuming one universal ontology
- `concept` stops being the only first-class semantic object
- domain-specific extraction, absorb, refine, lint, and scoring rules move into packs
- the core remains responsible for runtime integrity, identity, auditing, orchestration, and derived indexes

---

## Why This Refactor Is Necessary

The current codebase has already evolved beyond a simple “evergreen note pipeline”.

It now contains:

- a six-layer architecture
- canonical identity rules
- absorb and refine workflows
- a derived `knowledge.db`
- graph, lint, and audit infrastructure

But it still carries one hidden assumption:

> the dominant semantic unit is a concept-like evergreen note

That assumption works for the current technical knowledge workflow, but it does not generalize cleanly to:

- media editorial systems
- medical evidence systems
- legal or regulatory knowledge systems
- research or analyst desks

In those domains, the meaningful objects differ:

- media cares about events, evidence packets, angles, writing sheets, briefs, drafts, feedback
- medicine cares about claims, evidence grades, contraindications, protocols, safety summaries
- engineering research cares about repos, patterns, tradeoffs, benchmarks, failure modes, design memos

So the right move is not “insert media into the current concept model”.

The right move is:

> make the ontology pluggable, while keeping execution integrity in the core.

---

## Platform Model

### 1. Core Platform

The core platform owns infrastructure that should remain domain-agnostic.

It includes:

- runtime layout resolution
- pipeline execution engine
- autopilot / watcher / queue
- identity helpers
- registry framework
- derived `knowledge.db`
- graph and lint infrastructure
- audit/event logging
- plugin discovery and loading
- base evidence schema contract

The core does **not** decide:

- which object kinds a domain uses
- what “high quality” means inside a domain
- which extraction prompts or writing rules apply
- which workflow stages are required for a domain

### 2. Domain Pack

A domain pack defines domain semantics.

Each pack should declare:

- supported object kinds
- frontmatter schemas
- extraction policies
- absorb policies
- refine policies
- lint rules
- templates
- prompts
- scoring heuristics
- workflow stages or stage presets

Examples:

- `default-knowledge`
- `media-editorial`
- `engineering-research`
- `medical-evidence`

### 3. Workflow Profile

A workflow profile is the operational DAG for a domain.

Examples:

- `default-full`
- `default-autopilot`
- `media-daily-desk`
- `media-weibo-fastlane`
- `medical-briefing`
- `engineering-weekly-research`

Workflow profiles are allowed to be pack-specific.

The core only guarantees that profiles execute through a stable orchestration contract.

---

## The First Standard Pack: `default-knowledge`

The current repository should be formalized as the first standard pack, named:

```text
default-knowledge
```

This pack preserves current semantics:

- raw input
- deep-dive interpretation
- concept / entity / evergreen-oriented absorb
- cleanup / breakdown refine
- registry / alias / Atlas canonical state
- derived `knowledge.db`, graph, lint, daily delta

### Why `default-knowledge` should be the first pack

1. It already exists in code.
2. It is the only pack we can fully validate today.
3. It provides the reference contract for future external packs.
4. It prevents media from distorting core abstractions too early.

This is important:

> media should be the first strong external validation pack, not the seed pack that defines the platform.

---

## External Pack Model

Media and medical should be separate engineering projects.

Recommended repo pattern:

```text
openclaw-core                    # current repo evolved into platform
openclaw-pack-default-knowledge  # may live in-tree first, then optionally split
openclaw-pack-media-editorial
openclaw-pack-medical-evidence
openclaw-pack-engineering-research
```

### Why separate repos are preferable for specialized packs

- domain cadence differs from core cadence
- prompts and schemas will churn faster than runtime infra
- domain evaluation datasets and fixtures are pack-specific
- domain packs may have different governance and owners
- pack release cycles should not force core releases

The installation model should be plugin-based:

```bash
ovp-plugin install openclaw-pack-media-editorial
ovp --pack media-editorial --profile daily-desk
```

---

## Object System

The current concept registry needs to evolve into an **object system**.

This does **not** mean deleting concept logic.

It means promoting it into a more general model:

```text
KnowledgeObject
  - id
  - kind
  - title
  - aliases
  - status
  - pack
  - schema_version
  - canonical_path
  - metadata
```

### Core object kinds

The core should only know a small stable base:

- `entity`
- `concept`
- `evergreen`
- `note`
- `document`

### Pack-defined object kinds

Packs can add their own kinds.

For example media:

- `raw_source`
- `evidence_packet`
- `event`
- `angle`
- `analogue`
- `writing_sheet`
- `topic_card`
- `research_brief`
- `outline`
- `draft`
- `feedback`

For medical:

- `claim`
- `guideline`
- `protocol`
- `evidence_grade`
- `contraindication`
- `patient_summary`

### Registry implication

The current `concept_registry.py` should eventually become:

```text
object_registry.py
```

With `concept_registry.py` surviving as a compatibility wrapper or a pack-local adapter for `default-knowledge`.

---

## Discovery Model

The current discovery work should also be generalized.

Today we have already separated:

- canonical identity resolution
- retrieval discovery
- evidence buckets

The next step is to move from:

```text
concept discovery
```

to:

```text
object discovery
```

### Core discovery responsibilities

The core should provide:

- deterministic identity lookup
- shared retrieval layer (`knowledge.db`)
- evidence schema
- shared discovery facade

### Pack discovery responsibilities

Each pack decides:

- what kinds of objects can be discovered
- which discovery outputs are valid
- what abstain means in that domain
- how retrieval evidence influences review
- how object candidates are promoted, merged, split, or rejected

For example:

- `default-knowledge` may discover concepts and evergreen enrich targets
- `media-editorial` may discover events, angles, analogues, and writing sheets
- `medical-evidence` may discover claims, evidence conflicts, and protocol candidates

---

## Workflow Architecture

### Core stages

The core should expose generic stage categories:

- ingest
- normalize
- interpret
- absorb
- refine
- canonicalize
- derive
- review
- publish
- feedback

Not every profile uses every stage.

### `default-knowledge` profile

Maps approximately to the current six-layer runtime:

```text
ingest -> interpret -> absorb -> refine(optional) -> canonicalize -> derive
```

### `media-editorial` profile

Would map to:

```text
ingest
-> normalize
-> event_cluster
-> topic_card
-> desk_review
-> research_brief
-> outline
-> neutral_draft
-> style_pass
-> fact_lint
-> style_lint
-> editor_review
-> publish
-> feedback
```

The important point:

> these stages are profile-defined, not hard-coded into core.

---

## Plugin Contract

Each pack plugin should export a manifest plus Python entrypoints.

Example conceptual manifest:

```yaml
name: media-editorial
version: 0.1.0
api_version: 1
object_kinds:
  - event
  - angle
  - writing_sheet
workflow_profiles:
  - daily-desk
  - weibo-fastlane
schemas:
  - schemas/event.yaml
  - schemas/topic_card.yaml
templates:
  - templates/topic-card.md
prompts:
  - prompts/topic-card-generator.md
entrypoints:
  pack: openclaw_pack_media.plugin:get_pack
```

And the runtime-facing Python object should supply:

- pack metadata
- object kind definitions
- schema validators
- stage handlers
- lint hooks
- discovery hooks
- evidence enrichers

---

## Pack Boundaries

### Core owns

- runtime correctness
- stable plugin API
- state/audit durability
- canonical identity framework
- retrieval substrate
- cross-pack orchestration and safety

### Packs own

- ontology
- templates and prompts
- domain lint rules
- workflow DAGs
- scorecards and domain gates
- object lifecycle semantics

### Packs must not do

- bypass audit logging
- write arbitrary derived state without core hooks
- invent their own incompatible identity model
- directly replace the core runtime contract

---

## Identity Rules

This is a hard boundary.

Even after packs exist:

- canonical IDs must still be deterministic
- derived retrieval must not become identity truth
- semantic similarity must never silently become canonical linking

Pack-specific logic can influence:

- review
- candidate generation
- enrichment suggestions
- editorial proposals

But not:

- silent auto-identity assignment without deterministic rules

---

## Recommended Migration Path

### Phase 1: Platform extraction inside current repo

- define plugin/pack interfaces
- formalize `default-knowledge` as the first pack
- move current prompts/templates/rules under pack-aware structure
- keep behavior unchanged

### Phase 2: Registry and discovery generalization

- introduce object registry abstractions
- keep concept registry compatibility
- generalize discovery and evidence hooks to pack-aware interfaces

### Phase 3: Workflow profile system

- let packs register profiles
- make `ovp --pack ... --profile ...` first-class
- keep legacy command aliases for default pack

### Phase 4: External pack extraction

- create `media-editorial` as a separate repo
- install it back into core via plugin loading
- validate that the platform really supports a non-default ontology

### Phase 5: Second external pack

- build another domain pack, ideally different enough to stress the model
- `medical-evidence` or `engineering-research`

If the second external pack works cleanly, the platform model is real.

---

## Non-Goals

This design does not propose:

- turning the current repo into a media product
- making media the core ontology
- replacing Obsidian markdown as the durable store
- replacing `knowledge.db` with domain-specific databases as canonical truth
- building all future packs now

---

## Final Recommendation

The correct strategic move is:

1. turn this repository into a **knowledge workflow platform**
2. formalize the current semantics as `default-knowledge`
3. define a real plugin-pack interface
4. build media as the first serious **external** validation pack

That gives us:

- a stable core
- a clean default pack
- real extensibility
- no pressure to force every domain into a concept-only model

This is the architecture that can support media, medicine, engineering, and future domains without collapsing into one giant special-case codebase.
