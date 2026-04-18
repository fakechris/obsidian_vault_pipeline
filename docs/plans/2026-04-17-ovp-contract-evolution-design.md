# OVP Contract Evolution Design

## Purpose

Design the next interface step for OVP without discarding the current architecture.

This document assumes the current system stays intact:

- six-layer runtime flow stays
- `Core Platform / Domain Pack / Workflow Profile` stays
- the current pack/runtime registries stay

The question is only:

**what new pack-side contracts should be added so the architecture becomes more explicit and easier to evolve?**

## The Recommendation

Use **parallel contract expansion**, not a rewrite.

That means:

1. keep the current runtime/execution contract stack
2. add three new pack-side spec families
3. bind them into current commands and registries incrementally

Recommended new spec families:

1. `ArtifactSpec`
2. `AssemblyRecipeSpec`
3. `GovernanceSpec`

This is the cleanest path because it matches how OVP already works:

- `ExtractionProfileSpec` already packages extraction semantics
- `OperationProfileSpec` already packages review/ops semantics
- `WikiViewSpec` already packages compiled markdown views

So the next step should look like those specs, not replace them with a new meta-framework.

## Three Design Options

### Option A: Put More Into `WorkflowProfile`

This would extend profile so it also owns:

- artifact declarations
- view declarations
- governance hooks
- routing rules

Why this is wrong:

- profile should stay a DAG selector
- it would mix execution order with semantics and operator policy
- it would make one profile change too expensive and too risky

Verdict: reject.

### Option B: Build One Big Unified “Pack Schema”

This would create one huge manifest-like object that declares everything:

- object kinds
- stages
- processors
- views
- review queues
- signals
- actions

Why this is tempting:

- it looks architecturally clean

Why it is wrong right now:

- it skips over the very good modularity already present in code
- it would require rewriting registries that already work
- it would make pack authoring harder before the real needs are stable

Verdict: premature.

### Option C: Add Parallel Pack-Side Contracts

Keep existing contracts, then add:

- `ArtifactSpec` for persistent knowledge shapes
- `AssemblyRecipeSpec` for user/agent-facing compiled context products
- `GovernanceSpec` for routing, review, signals, and action policy

Why this is best:

- minimal breakage
- aligns with current code style
- lets us bind one layer at a time
- keeps `Core / Pack / Profile` intact

Verdict: recommended.

## Design Goal By Missing Layer

### Layer 1: Canonical Knowledge

Current problem:

- object kinds exist
- absorb outputs exist
- truth projection tables exist

But there is no explicit contract saying what persistent artifacts the pack really owns.

Design goal:

- declare artifact families explicitly
- keep truth projection downstream
- stop relying on `truth_store` row families as implicit architecture

### Layer 3: Context Assembly / Access

Current problem:

- wiki views exist
- observation surfaces exist
- UI payload builders exist
- export targets exist

But there is no single pack-side declaration for “what are the user-facing compiled products of this pack?”

Design goal:

- define assembly recipes as first-class pack declarations
- unify markdown export, UI payload, and briefing-like surfaces under one architecture language

### Layer 4: Governance / Resolver / Review

Current problem:

- operation profiles exist
- focused actions exist
- signals exist
- action queue exists

But the policy layer is still implicit.

Design goal:

- make routing/governance declarative
- make review lifecycle explicit
- give packs a clean place to declare resolver and signal semantics

## Contract 1: `ArtifactSpec`

### Why It Should Exist

Right now OVP has:

- `ObjectKindSpec`
- absorb processors
- truth projections

But no contract that says:

- what persistent artifact families exist
- how they are identified
- which ones are canonical
- which ones require evidence
- which ones are operator-facing

That gap is why the architecture still feels more implicit than explicit.

### Proposed Shape

```python
@dataclass(frozen=True)
class ArtifactFieldSpec:
    name: str
    field_type: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class ArtifactIdentityPolicy:
    id_strategy: str = "deterministic"
    id_fields: list[str] = field(default_factory=list)
    subject_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ArtifactEvidencePolicy:
    requires_evidence: bool = True
    require_quote: bool = True
    require_source_slug: bool = True
    require_traceability_links: bool = True


@dataclass(frozen=True)
class ArtifactStoragePolicy:
    storage_mode: str
    canonical_path_template: str | None = None
    truth_row_family: str | None = None
    review_queue_name: str | None = None


@dataclass(frozen=True)
class ArtifactLifecyclePolicy:
    mutable: bool = True
    review_required_on_create: bool = False
    review_required_on_update: bool = False
    projection_rebuild_policy: str = "on_derived_refresh"


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    pack: str
    layer: str
    family: str
    object_kind: str | None = None
    description: str = ""
    fields: list[ArtifactFieldSpec] = field(default_factory=list)
    identity_policy: ArtifactIdentityPolicy = field(default_factory=ArtifactIdentityPolicy)
    evidence_policy: ArtifactEvidencePolicy = field(default_factory=ArtifactEvidencePolicy)
    storage_policy: ArtifactStoragePolicy = field(
        default_factory=lambda: ArtifactStoragePolicy(storage_mode="markdown_note")
    )
    lifecycle_policy: ArtifactLifecyclePolicy = field(default_factory=ArtifactLifecyclePolicy)
```

### Recommended v1 Artifact Families

Start with only five:

- `object`
- `claim`
- `evidence`
- `overview`
- `review_item`

That is enough to make the current architecture much more explicit without overfitting.

### Example: Research-Tech

`research-tech` should declare at least:

- `object`
  - maps to canonical evergreen entities/concepts/evergreens
- `claim`
  - maps to truth-store claim projection inputs
- `evidence`
  - source quote / source slug / offsets / note path
- `overview`
  - topic/object/event compiled summaries that are still traceable artifacts
- `review_item`
  - contradiction, stale summary, frontmatter, extraction review queue items

### Why This Is Better Than Extending `ObjectKindSpec`

`ObjectKindSpec` should keep answering:

- what kind of thing exists in this domain

`ArtifactSpec` should answer:

- what persistent artifact shapes the pack owns

Those are not the same question.

## Contract 2: `AssemblyRecipeSpec`

### Why It Should Exist

Current Layer 3 is split across:

- `WikiViewSpec`
- `ObservationSurfaceSpec`
- UI payload builders
- `ovp-export` target maps

The missing architecture language is:

**what are the standard compiled context products this pack can produce?**

### Proposed Shape

```python
@dataclass(frozen=True)
class AssemblyInputSpec:
    source_kind: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class AssemblyAudienceSpec:
    audience: str
    interaction_mode: str = "read_only"


@dataclass(frozen=True)
class AssemblyFreshnessPolicy:
    cache_mode: str = "on_demand"
    invalidation_signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssemblyOutputSpec:
    output_mode: str
    publish_target: str


@dataclass(frozen=True)
class AssemblyRecipeSpec:
    name: str
    pack: str
    recipe_kind: str
    description: str
    inputs: list[AssemblyInputSpec] = field(default_factory=list)
    builder: str = ""
    traceability_policy: TraceabilityPolicy = field(default_factory=TraceabilityPolicy)
    audience: AssemblyAudienceSpec = field(default_factory=lambda: AssemblyAudienceSpec("operator"))
    freshness_policy: AssemblyFreshnessPolicy = field(default_factory=AssemblyFreshnessPolicy)
    output: AssemblyOutputSpec = field(
        default_factory=lambda: AssemblyOutputSpec(output_mode="markdown", publish_target="compiled_markdown")
    )
```

### Recommended v1 Recipe Kinds

Start with:

- `orientation_brief`
- `object_brief`
- `topic_overview`
- `event_dossier`
- `contradiction_view`
- `delta_digest`
- `operator_briefing`

### Relationship To Existing Interfaces

Do not delete anything yet.

Instead:

- `WikiViewSpec` stays as the markdown publishing contract
- `ObservationSurfaceSpec` stays as the shell/UI surface contract
- `AssemblyRecipeSpec` becomes the architecture-level declaration that can point to one or both

In practice:

- some recipes will compile to markdown views
- some will compile to JSON/UI payloads
- some will do both

### Why This Is The Right Abstraction

If OVP wants a real user-facing “access layer”, this is the contract that should name it.

Without this, the system will keep having a lot of good UI/export pieces but no clean language for what they are.

## Contract 3: `GovernanceSpec`

### Why It Should Exist

Current governance is real but spread out:

- `OperationProfileSpec`
- focused actions
- signal ledgers
- action queue
- contradiction review
- stale summary review

The missing piece is a pack-side policy contract that says:

- what issues exist
- what signals can be emitted
- what action is recommended
- how review lifecycle works

### Proposed Shape

```python
@dataclass(frozen=True)
class ReviewQueueSpec:
    queue_name: str
    subject_kind: str
    description: str
    review_required: bool = True


@dataclass(frozen=True)
class SignalRuleSpec:
    signal_type: str
    title_template: str
    description: str
    source_operation_profiles: list[str] = field(default_factory=list)
    recommended_action_kind: str | None = None
    auto_queue: bool = False
    priority: int = 0


@dataclass(frozen=True)
class ResolverRuleSpec:
    rule_name: str
    match_kind: str
    match_value: str
    route_kind: str
    route_target: str
    description: str = ""


@dataclass(frozen=True)
class GovernanceSpec:
    name: str
    pack: str
    review_queues: list[ReviewQueueSpec] = field(default_factory=list)
    signal_rules: list[SignalRuleSpec] = field(default_factory=list)
    resolver_rules: list[ResolverRuleSpec] = field(default_factory=list)
```

### Why `OperationProfileSpec` Alone Is Not Enough

`OperationProfileSpec` answers:

- what check to run
- when to run it
- what queue artifact it can emit

It does **not** answer:

- how those issues become signals
- which action is recommended
- how routing should happen
- which unresolved issues are important enough for briefing

That is a different layer.

### Relationship To Existing Runtime

This contract should bind to existing runtime pieces instead of replacing them:

- queue artifacts still written by `operations/runtime.py`
- focused actions still executed through `StageHandlerSpec + ProcessorContractSpec`
- signal ledger still built by `truth_api.py`

The new part is only that packs will be able to declare:

- what queue names exist
- what signal types exist
- what routes/action kinds are valid

## What To Implement First

Do not implement all three contracts at once.

### Phase 1: `ArtifactSpec`

Reason:

- it will clarify Layer 1 immediately
- it gives better language for absorb/refine
- it lets `truth_store` stop carrying too much implicit architecture meaning

Minimum runtime work:

- add `artifact_specs()` to `BaseDomainPack`
- expose in `ovp-doctor`
- add `research-tech` declarations only

### Phase 2: `AssemblyRecipeSpec`

Reason:

- it will unify `ovp-export`, `ovp-ui`, `briefing`, and view builders
- it is the missing product-language layer

Minimum runtime work:

- add `assembly_recipes()` to `BaseDomainPack`
- map existing export targets and shell surfaces into declared recipes

### Phase 3: `GovernanceSpec`

Reason:

- it is the highest leverage but also the most coupled
- it should come after artifacts and assembly are explicit

Minimum runtime work:

- add `governance()` or `governance_specs()` to `BaseDomainPack`
- surface queue/signal/action/resolver declarations in doctor

## Which External Projects Matter For Which Module

There is no single “best project” overall.

The landscape is modular. Different projects are strong on different layers.

### 1. Runtime Discipline

Best reference: `alive`

Why:

- explicit `save/load` protocol
- authored files vs generated state separation
- stale-context detection
- multi-session safety

What to borrow:

- projection discipline
- “agent may read this, may not write that” boundaries
- stale context safeguards

### 2. Temporal Truth / Fact Evolution

Best reference: `Graphiti`

Why:

- `valid_at / invalid_at / expired_at`
- episode lineage
- fact invalidation as a first-class operation

What to borrow:

- time-aware claim lifecycle
- explicit historical vs active truth modeling

Deepest insight:

- truth systems should not only overwrite; they should express validity windows

### 3. File-Canonical Access Layer

Best reference: `MemSearch`

Why:

- files are canonical
- vector index is downstream
- progressive disclosure is clean

What to borrow:

- canonical vs shadow-index discipline
- `search -> section -> raw transcript` style disclosure

Deepest insight:

- retrieval is an access layer, not the truth layer

### 4. Reviewed Curation And Background Maintenance

Best reference: `ByteRover CLI`

Why:

- `search / query / curate` separation
- real review queue
- dream pipeline split into `consolidate / synthesize / prune`
- explicit versioning over context artifacts

What to borrow:

- curate as a first-class user verb
- background maintenance split by responsibility

Deepest insight:

- context systems should not only recall; they should periodically rewrite and prune themselves under governance

### 5. Routing / Governance

Best reference: Garry Tan resolver system

Why:

- explicit resolver table
- trigger evals
- `check-resolvable`
- reachability as a system property

What to borrow:

- routing tests
- skill capability reachability audit
- filing rules as a central policy layer

Deepest insight:

- a capability that exists but cannot be reached is more dangerous than a missing capability

### 6. Orientation / Entry Artifact

Best reference: `hv-analysis` / `khazix-skills`

Why:

- strong “orientation report” packaging
- object-type-sensitive research framing

What to borrow:

- orientation brief design
- subject-type-sensitive assembly recipes

Deepest insight:

- users often need a map before they need a truth model

### 7. Bootstrap / Operating Contract

Best reference: `arscontexta`

Why:

- derivation/onboarding logic
- runtime methodology packaging

What to borrow:

- pack bootstrap docs
- operational contract visibility

Deepest insight:

- the way a system explains how to use itself is part of the system

### 8. Automated Capture / Open Harness Memory Layer

Best reference: `agentmemory`

Why:

- hooks
- access-layer retrieval engineering
- multi-agent coordination primitives

What to borrow:

- capture and coordination ideas only

What not to borrow:

- KV-backed memory core as OVP’s primary architecture

### 9. Memory Construction And Evaluation

Best reference: `EverOS`

Why:

- typed construction
- benchmark worldview

What to borrow:

- explicit construction schemas
- memory/artifact evaluation thinking

### 10. Constraint / Anti-Pattern

Best reference: `no-escape`

Why:

- it sets the limit conditions

Deepest insight:

- semantic retrieval always carries interference tax, so OVP should never make semantic recall the only organizing principle

## Best Overall Insights

If forced to pick the deepest external insights for OVP specifically:

1. `alive`
   - because it treats runtime discipline as a first-class architecture problem
2. `Graphiti`
   - because it treats temporal truth evolution as a first-class modeling problem
3. Garry Tan resolver system
   - because it treats routing/governance as a first-class systems problem
4. `ByteRover CLI`
   - because it treats reviewed curation and background maintenance as product primitives
5. `no-escape`
   - because it explains what semantic-memory systems cannot solve away

Those five together are more useful for OVP than any single “all-in-one” project.

## Concrete Next Step

The next concrete move should be:

1. add `artifact_specs()` to `BaseDomainPack`
2. implement `research-tech` v1 artifact families
3. teach `ovp-doctor` to show artifact families beside object kinds and processor contracts
4. only after that, add `assembly_recipes()`

That sequence keeps the architecture grounded in the current codebase and avoids prematurely redesigning the whole runtime.

## Necessity Ranking

The ten borrowed modules should **not** be read as ten equally necessary roadmap items.

That would absolutely become over-engineering.

The right reading is:

- some are **core product requirements**
- some are **good next-step refinements**
- some are **advanced depth layers**
- some are **constraints, not modules**

### Must Exist Soon

These are necessary for OVP to feel coherent as a knowledge compiler rather than a pile of scripts:

1. `file-canonical + access layer`
2. `reviewed curation + background maintenance`
3. `routing / governance` at least in a basic form
4. `typed construction`
5. `bootstrap / operating contract` in lightweight form
6. `runtime discipline` in lightweight form

Important nuance:

- “lightweight form” does not mean “ignore”
- it means “do the minimum contract version, not the full external-project version”

### Should Come Later

These are high leverage, but not required for the architecture to become coherent:

1. `orientation artifact`
2. `benchmark worldview`

They improve usability and rigor, but they should come after artifact and assembly contracts are explicit.

### Advanced / Defer For Now

These are real problems, but they are not the next bottleneck:

1. `temporal truth`
2. `capture / access-layer memory`

These become worth building only after OVP’s canonical artifact and governance layers are stable.

Otherwise the system risks solving a more advanced problem on top of still-fuzzy foundations.

### Constraint, Not A Build Track

`no-escape` style insight should not become its own subsystem.

It should become an architectural guardrail:

- do not make semantic retrieval the only organizing principle
- do not let embeddings define canonical truth
- keep explicit structure, review, and provenance in the loop

## The Simpler Way To See It

The problem is not “do we need ten modules?”

The real question is:

**what are the minimum contracts needed so OVP becomes a coherent knowledge compiler?**

That minimum set is smaller:

1. a clean canonical/derived boundary
2. explicit artifact construction
3. basic review/routing/governance
4. a usable access layer
5. lightweight runtime and operating discipline

Everything else is either:

- a refinement
- a future specialization
- or a warning label
