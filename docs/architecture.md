# OVP Next — Architecture

## Layers

```
CLI Layer          (ovp-cli)
  Thin. Parses args, calls App.

App Layer          (post-v0.1)
  Selects a pipeline manifest, constructs filters/stores, runs the graph,
  invokes the plan applier.

Core Layer         (ovp-core)
  Knows Record, Filter, FilterDecision, WritePlan, Event, PipelineManifest,
  GraphRunner. Knows nothing about Obsidian, Markdown, LLMs, or SQLite.

Domain Layer       (post-v0.1, separate crate)
  SourceDoc, InterpretedDoc, CandidateNote, CanonicalNote, Quality,
  Identity, VaultPath, frontmatter shapes. Real business types.

Filters Layer      (post-v0.1)
  Source / Transform / Sink implementations that bridge Core to Domain.

Stores Layer       (post-v0.1)
  VaultStore, CanonicalStore, DerivedIndex, EventLog. Real I/O.

LLM Layer          (post-v0.1)
  PromptAsset, ModelClient, Parser, Interpreter. Pluggable.
```

v0.1 implements **Core Layer + minimal CLI only**. Everything else is post-validation.

## Data flow (the design target — not v0.1)

```
sources → normalize → route → interpret → quality_gate
  → absorb → identity_resolve → note_plan_builder
  → [VaultWritePlanSink, CanonicalWritePlanSink, EventLogPlanSink]
  → WritePlan
  → PlanApplier
  → VaultStore + CanonicalStore + EventLogStore

(separately, on demand)
  CanonicalStore + VaultStore → DerivedIndexBuilder → DerivedIndex
```

## v0.1 data flow (what we actually build)

```
FakeSource → FakeTransform → FakeSink → WritePlan + Event[]
```

That's it. We're proving the **types** and the **runner**, not the business logic.

## Why these boundaries

1. **`ovp-core` has no domain types** → the runner can host real or fake filters indistinguishably, which is how we test.
2. **`FilterDecision` is a sealed enum** → drops, fan-outs, errors, completions are all first-class. No "yield nothing and hope someone notices."
3. **`WritePlan` is the only side-effect carrier** → no transform can silently write to a store. Audit, diff, and rollback all become possible.
4. **`EventLog` is append-only and separate from business state** → debugging and replay are first-class.
5. **`PipelineManifest` is explicit TOML** → no auto-wiring magic. The graph you ran is the graph you wrote.

See `invariants.md` for the enforced rules.
