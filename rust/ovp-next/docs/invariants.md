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

## 13. The eval layer never re-enters the trunk

`ovp-eval` (the M8 external comparator) is an evaluation/orchestration layer **above** the trunk: it calls the M7 review harness and reaches an *external* Nowledge Mem service over HTTP. It may depend on trunk crates; **no trunk crate may depend on `ovp-eval`**. Nowledge Mem is a comparator, not legacy OVP and not a runtime dependency of the pipeline — all of its access is isolated behind the `NowledgeClient` HTTP adapter inside `ovp-eval`, which is the only place `reqwest` is used outside `ovp-llm`. CI gate: `check_architecture.sh` rejects any `^ovp-eval =` line in a trunk crate's `Cargo.toml`. (Same direction-of-dependency discipline as #1, applied at the top of the stack instead of the bottom.)

## Payload typing at the WriteOp boundary

`CanonicalUpsertOp.payload` and `EventAppendOp.payload` are `String` in `crates/ovp-core/src/plan.rs`. This is **not** an untyped stub — it is a deliberate serialization boundary, the same kind invariant #2 permits for protocol/cassette boundaries:

- `CanonicalUpsert.payload` carries a serialized **`ovp-domain::CanonicalConcept`** (a typed struct: slug, title, evergreen_path, provenance). `EvergreenSink` produces it via `CanonicalConcept::to_payload()`; readers parse it via `CanonicalConcept::from_payload()`. The *type* lives in `ovp-domain` because `ovp-core` is domain-blind (invariant #1) — core transports the bytes, the domain owns the schema.
- `CanonicalFsStoreApplier` (ovp-stores) is itself domain-blind: it persists the payload bytes keyed by canonical key, without interpreting them. Domain meaning is the producer's/reader's concern.
- `EventAppendOp.payload` is still unexercised (no producer yet). It will follow the same pattern — a typed domain event serialized at the boundary — when an event producer lands.

The earlier "stub, flag if still here in three crates" note is resolved: the canonical payload now has a concrete typed producer (`EvergreenSink` → `CanonicalConcept`) and consumer round-trip, with core staying domain-blind by design.

## Canonical-store integrity (apply ordering + rebuild discipline)

The canonical store and its derived rebuilds (MOC, knowledge index) carry three correctness guarantees beyond payload typing. They are not new invariants — they are how invariants #10 and #11 are kept honest at the canonical boundary:

- **One slug rule, single segment.** A concept's slug keys three coupled places — the canonical store file (`<store>/<slug>.json`), the evergreen page (`10-Knowledge/Evergreen/<slug>.md`), and the record id (`evg-<slug>`). `ovp-domain::CanonicalSlug` is the *one* gate that validates a slug is a single safe path segment (no `/`, `\`, `..`, empty, interior whitespace, or control chars; Unicode segments allowed). `EvergreenConceptWriter` drops candidates that fail it with an observable `FilterDropped` event rather than minting a divergent concept. `CanonicalFsStoreApplier` independently rejects keys with a separator (`key_nested`), so every key the store can *write* is a key `read_all` can *read back* — a record can never hide in a subdirectory the rebuild never scans. (This is the legacy `canonicalize_note_id` slug rule; it landed domain-owned, not in core, because the slug is a domain concept.)
- **Declared-hash integrity before I/O.** Both appliers verify `sha256(body|payload) == after_hash` *before* touching disk. A corrupt or mislabeled op `Failed`s without writing, and the idempotence/`before_hash` checks downstream stay trustworthy.
- **Apply ordering is load-bearing.** `CompositePlanApplier` applies ops one at a time in plan order and routes each to the first backend that handles it. The first `Failed` op HALTS the run — later ops record `Skipped { reason: "previous op failed" }` and perform no I/O. This is what stops an evergreen's `CanonicalUpsert` from registering identity for a page whose paired `VaultCreate` just failed. Recovery is re-apply (every op is idempotent).
- **Rebuilds fail loudly.** `CanonicalConcept::try_parse_pairs` (the `Result`-returning parser) is the rebuild path: a corrupt canonical record aborts the rebuild and names the offending key, so a derived index never silently shrinks. The permissive `parse_pairs` survives for diagnostics only — never call it from a rebuild.

## File budgets (soft, but enforced by review)

- `ovp-core` total: ≤1500 LOC (was 1200 in v0.1; bumped for the EffectfulTransform split + per-edge queue work)
- Single file: ≤400 LOC (was 300; `graph.rs` is structurally complex)
- Single function: ≤80 LOC
- Single type: ≤200 LOC

If a file blows past these, split it before merging.
