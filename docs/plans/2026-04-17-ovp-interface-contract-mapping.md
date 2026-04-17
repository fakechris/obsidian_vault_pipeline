# OVP Interface Contract Mapping

## Purpose

Clarify how the current interface layer already packages:

- the six-layer runtime flow
- `Core Platform / Domain Pack / Workflow Profile`
- the proposed persistent four-layer architecture view

The goal is not to replace the current architecture. The goal is to explain which interfaces already carry which responsibilities, and where new ideas should attach.

## The Core Answer

OVP already has a real interface stack. It is just split across several contract types instead of one big architecture object.

The current packaging looks like this:

1. **Entry interfaces**
   - CLI commands
   - pack entry points
   - manifest discovery
2. **Pack declaration interfaces**
   - `BaseDomainPack`
   - object kinds
   - workflow profiles
   - extraction / operation / wiki view declarations
3. **Runtime execution interfaces**
   - `WorkflowProfile`
   - `StageHandlerSpec`
   - `ProcessorContractSpec`
   - `ExecutionContractSpec`
4. **Derived / access interfaces**
   - `TruthProjectionSpec`
   - `ObservationSurfaceSpec`
   - wiki-view based exports
5. **Governance interfaces**
   - operation profiles
   - focused actions
   - signals / review queue / contradiction workflows
   - doctor integrity checks

So the right framing is:

- the six-layer model describes **what kind of work happens**
- `Core / Pack / Profile` describes **who owns the semantics and runtime**
- the current contract objects describe **how this is actually wired**

## 1. External Entry Interfaces

The outside world does not touch `BaseDomainPack` directly first. It hits CLI entry surfaces and plugin discovery.

Current runtime entry surfaces:

- `pyproject.toml` exposes the main operator commands:
  - `ovp`
  - `ovp-autopilot`
  - `ovp-packs`
  - `ovp-doctor`
  - `ovp-export`
  - `ovp-truth`
  - `ovp-ui`
  - `ovp-ops`
- builtin and external packs are discovered through:
  - entry point group `openclaw_pipeline.packs`
  - explicit manifests via `OPENCLAW_PACK_MANIFESTS`

This means the first packaging boundary is already explicit:

- **Core** owns discovery and command surfaces
- **Pack** enters only through a validated plugin/entrypoint contract

## 2. Pack Declaration Interface

The real center of the current architecture is `BaseDomainPack`.

It already bundles more than the docs initially suggest. A pack can currently declare:

- `object_kinds()`
- `workflow_profiles()`
- `extraction_profiles()`
- `operation_profiles()`
- `wiki_views()`
- `stage_handlers()`
- `truth_projection()`
- `observation_surfaces()`
- `processor_contracts()`

That matters because it means pack is not just “schema + prompts”. Pack is already the place where OVP stores:

- domain object vocabulary
- runnable workflow shapes
- transformation contracts
- export/view recipes
- UI/access surfaces
- part of the governance layer

In other words:

- **Core** does not own domain semantics
- **Pack** already owns far more of the meaningful architecture than the older docs imply

## 3. How The Six-Layer Runtime Flow Is Packaged Today

The important thing here is that the six layers are **conceptual runtime phases**, while the actual interface layer uses **concrete stage names**.

For `research-tech`, the current profile stages are:

- `pinboard`
- `pinboard_process`
- `clippings`
- `articles`
- `quality`
- `fix_links`
- `absorb`
- `registry_sync`
- `moc`
- `knowledge_index`

Those concrete stages map to the six-layer model like this:

### Ingest

Current concrete stages:

- `pinboard`
- `pinboard_process`
- `clippings`

These stages bring raw material into the vault and normalize it into source notes.

### Interpret

Current concrete stages:

- `articles`
- `quality`

`articles` performs structured interpretation into deep dives.
`quality` evaluates those interpretive outputs before they are allowed to propagate.

### Absorb

Current concrete stage:

- `absorb`

This is the main bridge where interpreted material becomes evergreen/truth-like artifacts.

### Refine

Current concrete stages:

- `fix_links`
- `refine` in autopilot / focused follow-up flows

This phase is partly explicit and partly distributed across cleanup-oriented handlers.

### Canonical

Current concrete stages:

- `registry_sync`
- `moc`

This is where the system stabilizes filesystem/registry identity and rebuilds canonical navigation structures.

### Derived

Current concrete stage:

- `knowledge_index`

This rebuilds `knowledge.db` and graph/truth projections.

So the six-layer model is already present, but it is currently expressed through **stage naming + processor modes**, not through one monolithic “layer enum”.

## 4. How `Profile` Packages Runtime

`WorkflowProfile` is the interface that packages the execution DAG.

Today it is intentionally small:

- `name`
- `description`
- `stages`
- `supports_autopilot`

That is the right design.

Profile should stay a **routing object**, not become the place where all semantics live.

Its job is only:

- pick a stage sequence
- declare whether the sequence supports autopilot
- let core validate that every listed stage has a valid execution contract

The important part is that profile execution is not just `for stage in stages`.

`resolve_workflow_profile(...)` in the loader validates every stage against the runtime adapter by resolving:

- `StageHandlerSpec`
- `ProcessorContractSpec`
- bundled as `ExecutionContractSpec`

That means profile is already a typed execution contract boundary, not a loose list of strings.

## 5. How `StageHandlerSpec` And `ProcessorContractSpec` Split Responsibilities

This is the key interface split in the current architecture.

### `StageHandlerSpec` packages runtime behavior

It answers:

- which runtime adapter executes this stage
- which Python entrypoint is called
- whether this is `pipeline_step`, `autopilot_stage`, or `focused_action`
- operational flags such as:
  - `supports_autopilot`
  - `safe_to_run`
  - `requires_truth_refresh`
  - `requires_signal_resync`

So `StageHandlerSpec` is the **runtime adapter contract**.

### `ProcessorContractSpec` packages semantic transformation

It answers:

- what kind of processor this is
- `mode`
  - `external_ingest`
  - `rule_based`
  - `llm_structured`
  - `hybrid`
  - `evaluation`
  - `projection_rebuild`
- declared `inputs`
- declared `outputs`
- `quality_hooks`
- semantic entrypoint description

So `ProcessorContractSpec` is the **dataflow / transformation contract**.

### `ExecutionContractSpec` packages both together

This is the real bridge object:

- handler = how runtime calls it
- processor = what the stage semantically does

That is the current answer to “how do interfaces package the six-layer flow?”

Answer:

**not by putting everything into `WorkflowProfile`, but by resolving each profile stage into `ExecutionContractSpec = handler + processor`.**

## 6. How `Core / Pack / Profile` Is Actually Packaged

### Core

Core currently owns:

- pack discovery and loading
- compatibility inheritance
- runtime adapters
- handler / processor / truth-projection / observation-surface registries
- queueing and autopilot infrastructure
- truth-store and derived projections
- shell commands and operator surfaces
- integrity checks via `ovp-doctor`

Core is the place that makes contracts executable and auditable.

### Pack

Pack currently owns:

- object model
- workflow options
- extraction/operation/view semantics
- stage handlers
- processor contracts
- truth projection builder
- observation surfaces

Pack is already the main semantic unit.

### Profile

Profile owns only:

- which DAG to run
- which runtime context it is valid for

That is exactly where it should sit.

So the current architecture is already mature on this point:

- **Core** = execution/governance framework
- **Pack** = semantic bundle
- **Profile** = chosen DAG over pack-defined stage contracts

## 7. Compatibility Packs Show The Intended Extension Model

`default-knowledge` is important because it proves the system is already using layered pack inheritance.

It declares:

- its own object kinds
- its own profiles
- its own extraction / operation / wiki-view declarations

But it inherits execution contracts from `research-tech` through:

- `role="compatibility"`
- `compatibility_base="research-tech"`

Then the registries resolve effective handlers / processors / surfaces through `iter_compatible_packs(...)`.

This means the extension model is already:

- declare only what you override
- inherit the rest from a base pack

That is a very good sign. It means future architectural work should extend this contract system, not replace it.

## 8. How The Four Persistent Layers Fit Into Existing Interfaces

The proposed four-layer model is useful because it tells us where the current contracts belong.

### Layer 1: Canonical Knowledge

Current interface anchors:

- `ObjectKindSpec`
- absorb/refine related `ProcessorContractSpec`
- pack-owned schema / templates
- vault markdown + registry state

Important nuance:

`truth_store.py` is not Layer 1 itself.
It is the projection schema that shows where Layer 1 wants to go:

- `objects`
- `claims`
- `claim_evidence`
- `relations`
- `compiled_summaries`
- `contradictions`

So Layer 1 is present, but still more implicit than explicit.

### Layer 2: Derived Indexes / Views

Current interface anchors:

- `TruthProjectionSpec`
- `knowledge_index` processor contract
- graph/truth projection builders
- `WikiViewSpec`
- `ovp-export`
- `ovp-truth`

This is the strongest current layer.

### Layer 3: Context Assembly / Access

Current interface anchors:

- `ObservationSurfaceSpec`
- shell surfaces:
  - `signals`
  - `briefing`
  - `production_chains`
- UI payload builders
- export targets:
  - object page
  - topic overview
  - event dossier
  - contradictions

This layer already exists. It is just not yet named as a first-class subsystem.

### Layer 4: Governance / Resolver / Review

Current interface anchors:

- `OperationProfileSpec`
- focused action handlers
- review-queue writers
- contradiction review
- stale-summary review
- signals and actions surfaces
- doctor integrity checks for contracts and observation support

This layer is not absent. It is present but spread across operations, truth API, and runtime signaling.

## 9. What Interfaces Are Still Missing Or Too Implicit

The current system is more mature than it first looks, but three contracts are still under-explicit.

### A. Canonical artifact contract

Right now canonical vocabulary is split across:

- `ObjectKindSpec`
- absorb processor outputs
- truth projection tables

What is still missing is an explicit pack-level artifact contract for things like:

- `Object`
- `Claim`
- `Evidence`
- `Overview`
- `ReviewItem`

### B. Assembly recipe contract

Right now context assembly is spread across:

- wiki views
- observation surfaces
- UI payload builders
- export targets

What is still missing is a clean pack-facing way to say:

- this is an orientation brief
- this is an object brief
- this is a topic overview
- this is a delta digest

### C. Governance / resolver contract

Right now governance is split across:

- operation profiles
- focused actions
- signal generation
- review queue
- truth API actions

What is still missing is an explicit routing/governance contract for:

- resolver rules
- reachability checks
- action routing
- review lifecycle policies

## 10. The Right Upgrade Path

The wrong move would be to invent a totally separate new architecture stack.

The right move is:

1. keep `WorkflowProfile` small
2. keep `StageHandlerSpec + ProcessorContractSpec` as the execution bridge
3. keep `Core / Pack / Profile` as the ownership model
4. make missing persistent-layer concepts explicit by extending pack declarations

In practice that means:

### Keep as-is

- `WorkflowProfile`
- handler registry
- processor registry
- truth projection registry
- observation surface registry
- pack compatibility inheritance

### Extend next

- add an explicit artifact contract layer on the pack side
- add an explicit assembly recipe contract layer on the pack side
- add an explicit governance / resolver contract layer on the pack side

These should probably live as new pack declarations, not as ad hoc flags inside core commands.

## 11. Bottom Line

The current interfaces already package the architecture in a fairly coherent way.

Short version:

- **six-layer runtime flow** is packaged as concrete stages resolved through `ExecutionContractSpec`
- **Core / Pack / Profile** is already a real ownership model, not just documentation language
- **the four-layer persistent architecture view** should be added as explicit pack-side contracts, not as a replacement for the current runtime model

So the mature architectural direction is:

- do not rewrite the execution model
- do not collapse everything into `profile`
- do not shove new ideas straight into core
- instead, make pack-side semantic contracts richer while leaving core as the execution and audit framework
