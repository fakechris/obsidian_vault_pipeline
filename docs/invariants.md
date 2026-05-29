# OVP Next — Architecture Invariants

These rules are enforced by `scripts/check_architecture.sh` (CI grep gate) and by code review. They exist because the previous Python system drifted into a god-class + subprocess-self-call + registry-sprawl mess. If you find yourself wanting to violate one of these, **stop and discuss before patching the invariant**.

## 1. `ovp-core` is domain-blind AND I/O-blind

`ovp-core` knows about `Record<B>`, `Filter` traits, `WritePlan`, `Event`, `PipelineManifest`, `GraphRunner`. It does **not** know about Obsidian, Markdown, SQLite, LLMs, frontmatter, MOC, six-dimension quality scoring, or any business concept. It also does **not** know about HTTP clients, file system writes (beyond Source ingestion + Sink-emitted WriteOps), databases, or LLM providers. Effect clients (`ModelClient`, future `Store`, `Fetcher`) live in their own crates.

## 2. No `serde_json::Value` in core public API

`ovp-core`'s public types must be typed end to end. Dynamic JSON is fine inside test fixtures, plugin protocol boundaries, and debug dumps — not in record bodies, `WriteOp` payloads, or any function signature exported from `ovp-core`.

## 3. No `HashMap<String, _>` payloads in `Record<B>` body / `WriteOp`

Sum types over named structs, not maps. This is the single biggest defense against the legacy system's `Mapping[str, Any]` rot.

## 4. No subprocess to `python` or `ovp`

`Command::new("python")`, `Command::new("ovp")`, or any shell-out to the legacy CLI is forbidden. The legacy system exists only as fixture-generator, not as runtime dependency.

## 5. No `pyo3`, no embedded Python

Same reason as #4. Distribution must produce a self-contained binary.

## 6. No async runtime in `ovp-core`

`ovp-core` is sync. Forever. The runner is single-threaded. `async fn` / `.await` / `tokio::` / `futures::` are banned in `crates/ovp-core/src`. Effect-client crates (e.g. `ovp-llm`) MAY have async impls behind feature flags, but their async-ness never leaks into the pipeline trait signatures. The day there's a real concurrency need, the executor (not `ovp-core`) becomes async-aware and lifts the I/O call out of the pipeline at the `EffectfulTransform` boundary.

## 7. No legacy imports

No `from ovp_pipeline ...`, no Rust binding to `ovp_pipeline.*`. The grep check enforces this.

## 8. Pipeline topology is explicit

Production pipelines are constructed from a `PipelineManifest` (TOML). Auto-wiring may be useful for diagnostics/visualization, but never for production runs.

**Footnote**: a manifest describes **topology** (which nodes, which edges). It does NOT describe **wiring** (which `ModelClient` impl, which prompt asset version, which cache path, which model name). Wiring is the app layer's concern. The combined `(manifest topology, app wiring)` is the explicit single source of truth — neither alone is.

## 9. Transform is pure. EffectfulTransform is the only I/O-bearing node.

A `Transform<B>` impl must be a pure function from `Record<B>` to `FilterDecision<B>`. No file writes, no DB calls, no network, no spawned processes, no held effect clients. Same input → same output, every run.

If a node needs to call a network service, a database, the LLM, or any other effectful client, it implements `EffectfulTransform<B>` instead. EffectfulTransform is a sync facade over an injected effect client (`Box<dyn ModelClient>`, `Box<dyn Store>`, etc.). Replayable in tests when the client is a fixture or cached impl. The runner treats both traits identically; the split exists as a type-system signal of intent.

Side-effect categories by node kind:
- **`Source<B>`**: ingestion boundary — allowed to read files, poll external systems, anything that brings records INTO the pipeline.
- **`Transform<B>`**: pure. No I/O of any kind.
- **`EffectfulTransform<B>`**: sync facade over an effect client. Documented, type-distinguished.
- **`Sink<B>`**: produces `WriteOp` records ONLY. Does not perform the writes themselves.
- **`PlanApplier`** (post-v0.1, separate executor stage): the only thing allowed to actually mutate the vault, the canonical store, or the event log.

CI gate: any file that defines `impl Transform<...> for <T>` and `<T>` has a field of type `Box<dyn (.+Client|.+Store|.+Fetcher)>` is rejected. Use `EffectfulTransform` instead.

## 10. Writes happen only through `WritePlan`

No filter writes directly to any `Store`. The pipeline produces a `WritePlan`; a separate `PlanApplier` (in `ovp-core` as a trait, impl in `ovp-stores`) executes it. `VaultFsPlanApplier` is the v1 impl for filesystem vaults. Path safety, hash-matched idempotence, and `before_hash` checks on updates are enforced at the applier — every real write goes through this layer, is recorded in an `ApplyReport`, and is refusable.

## 11. Derived state is rebuildable

Any future search index, embedding store, or denormalized cache must be reconstructible from `CanonicalStore` + `VaultStore` alone. (Not relevant in v0.1 — listed here so it's not forgotten.)

## 12. `EventLog` is append-only

Events record what happened, in order. They are not a business query store.

## Known stubs (NOT invariants — explicit deferrals)

These would violate the spirit of #2/#3 if shipped long-term, but are acceptable stubs because no real *producer* of these ops exists yet:

- **`CanonicalUpsertOp.payload: String`** and **`EventAppendOp.payload: String`** in `crates/ovp-core/src/plan.rs`. The fields are typed-as-string-for-now because nothing emits these ops yet — the article + paper pipelines only produce `VaultCreate`. `VaultFsPlanApplier` reports both as `Unsupported`, and `ovp-cli apply-plan` warns loudly if a plan contains them (so the stub can't pass silently).

**Flag, raised E1 (2026-05):** we now have three crates past core (`ovp-domain`, `ovp-llm`, `ovp-stores`) and the stub is still here, which the original note said to flag. The deferral remains justified *for a specific, named reason*: typing the payload requires a concrete `CanonicalUpsert` producer to validate the shape against, and that producer is the **L3 absorb + canonical store** stage (see `docs/architecture.md` "What comes next", step 5). Resolving it before then would be guessing at the write surface. When the canonical store stage lands, this stub is converted to a typed payload as part of that work, not before.

## File budgets (soft, but enforced by review)

- `ovp-core` total: ≤1500 LOC (was 1200 in v0.1; bumped for the EffectfulTransform split + per-edge queue work)
- Single file: ≤400 LOC (was 300; `graph.rs` is structurally complex)
- Single function: ≤80 LOC
- Single type: ≤200 LOC

If a file blows past these, split it before merging.
