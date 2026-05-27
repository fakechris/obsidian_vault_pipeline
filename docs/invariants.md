# OVP Next — Architecture Invariants

These rules are enforced by `scripts/check_architecture.sh` (CI grep gate) and by code review. They exist because the previous Python system drifted into a god-class + subprocess-self-call + registry-sprawl mess. If you find yourself wanting to violate one of these, **stop and discuss before patching the invariant**.

## 1. `ovp-core` is domain-blind

`ovp-core` knows about `Record`, `Filter`, `WritePlan`, `Event`, `PipelineManifest`. It does **not** know about Obsidian, Markdown, SQLite, LLMs, frontmatter, MOC, six-dimension quality scoring, or any business concept.

## 2. No `serde_json::Value` in core public API

`ovp-core`'s public types must be typed end to end. Dynamic JSON is fine inside test fixtures, plugin protocol boundaries, and debug dumps — not in `RecordBody`, `WriteOp` payloads, or any function signature exported from `ovp-core`.

## 3. No `HashMap<String, _>` payloads in `RecordBody` / `WriteOp`

Sum types over named structs, not maps. This is the single biggest defense against the legacy system's `Mapping[str, Any]` rot.

## 4. No subprocess to `python` or `ovp`

`Command::new("python")`, `Command::new("ovp")`, or any shell-out to the legacy CLI is forbidden. The legacy system exists only as fixture-generator, not as runtime dependency.

## 5. No `pyo3`, no embedded Python

Same reason as #4. Distribution must produce a self-contained binary.

## 6. No `async` / `tokio` / `async-std` in v0.1

Sync only. We add async if and only if a real workload demonstrates it's needed — and not before the core is validated.

## 7. No legacy imports

No `from ovp_pipeline ...`, no Rust binding to `ovp_pipeline.*`. The grep check enforces this.

## 8. Pipeline topology is explicit

Production pipelines are constructed from a `PipelineManifest` (TOML). Auto-wiring may be useful for diagnostics/visualization, but never for production runs.

## 9. Transforms are pure

A `Transform` impl must not write files, write DB, mutate a `Store`, spawn processes, or do network I/O. It takes a `Record`, returns a `FilterDecision`. Side effects belong to `Sink` (which only produces `WritePlan` fragments) and `PlanApplier` (the only thing allowed to actually write).

## 10. Writes happen only through `WritePlan`

No filter writes directly to any `Store`. The pipeline produces a `WritePlan`; a separate `PlanApplier` step (post-v0.1) executes it.

## 11. Derived state is rebuildable

Any future search index, embedding store, or denormalized cache must be reconstructible from `CanonicalStore` + `VaultStore` alone. (Not relevant in v0.1 — listed here so it's not forgotten.)

## 12. `EventLog` is append-only

Events record what happened, in order. They are not a business query store.

## File budgets (soft, but enforced by review)

- `ovp-core` total: ≤1200 LOC in v0.1
- Single file: ≤300 LOC
- Single function: ≤60 LOC
- Single type: ≤200 LOC

If a file blows past these, split it before merging.
