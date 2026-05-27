# Calibration R1 — Continue with Rust

**Date:** 2026-05-27
**Trigger:** integration test `fake_pipeline.rs` passes; core types are usable end-to-end.
**Decision:** **Continue with Rust.** No language switch.

## What was built

- `ovp-core` crate: `Record`/`RecordBody`, `Filter` traits + `FilterDecision`, `WritePlan`/`WriteOp`, `Event`/`EventLog`, `PipelineManifest` (TOML), `GraphRunner` (in-memory).
- `ovp-cli` crate: stub `main.rs` only.
- Architecture invariants doc + grep-based CI gate (`scripts/check_architecture.sh`).
- 10 tests pass: 4 manifest parser, 1 unit, 5 integration.
- `cargo clippy --all-targets -- -D warnings` clean.
- Arch check clean.

## Velocity signals

| Signal | Threshold (Reconsider) | Actual |
|---|---|---|
| Total Rust LOC | >1500 | 999 (core src) + 196 (test) = **1195** |
| `ovp-core/src/graph.rs` size | >300 | **314** (14 over, soft violation) |
| Compile-fight incidents >1 hour | ≥2 | **0** |
| `Box<dyn Trait>` proliferation | "everywhere" | **3 usages** (Source / Transform / Sink, each behind a `Node` enum variant — the only sane place) |
| Lifetime nightmares | "doesn't make sense at this level" | **0** |
| Fake test scaffolding LOC | >500 | **196** |
| Tests pass on first run after compile | yes | **yes (10/10)** |

## Compile fights

Two minor compile-time issues, both resolved in <5 min, both surfaced design lessons rather than compiler arguments:

1. **`code: &'static str` + `Deserialize`** on `DropReason` / `FilterError`. Can't deserialize a `'static` borrow from owned input. **Fix:** changed to `String`. Lesson: don't pre-optimize codes into `&'static str` if they have to round-trip through serde. If we later want a sealed code set, model it as a real enum.

2. **`#[derive(Default)]` on `EventLog`** required `Default` on `EventTs`. Added the derive. 30 seconds.

Neither qualifies as "fighting the compiler." Both were the type system pointing at a real design ambiguity.

## What the type system actually bought us

Three concrete wins where Rust's enum exhaustiveness changed how the code was shaped:

- **`FilterDecision`** as a 5-variant enum forced the runner to handle `Drop` / `Complete` / `Error` as first-class outcomes, not as "yield nothing." The integration test's drop case fell out for free.
- **`RecordBody::Fake`** as the sole variant during v0.1 means every `match` on a body is exhaustive — when we add real variants (`SourceDoc`, `InterpretedDoc`, ...) the compiler will surface every site that needs updating.
- **`WriteOp` as a sealed enum** kept payloads typed. There is no `HashMap<String, Value>` anywhere in the public API. Arch check confirms.

## Risks observed (not blockers)

- **LLM layer will need async.** Model APIs are inherently I/O-bound. The "no async in v0.1" rule holds for the core, but the LLM crate likely introduces `tokio` when it lands. We should revise invariant #6 then, not before.
- **Fan-out topology is untested.** `gather_inputs` should work for one-to-many edges, but no test exercises it. Add when needed.
- **`graph.rs` is 14 lines over budget.** Soft violation. Defer split until we add the next feature there; splitting prematurely just to hit a number is process theater.
- **Trait objects vs generics.** Current design uses `Box<dyn Source/Transform/Sink>` via the `Node` enum. Generic specialization is out. This is the right call for a runtime-registered graph; we'd only reach for generics if a hot path showed up, which it won't in v0.1.

## What this validates

- The 5-layer architecture sketch (`docs/architecture.md`) is implementable.
- Invariants 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12 from `docs/invariants.md` are now enforced (11 is N/A in v0.1).
- The Agent + Rust loop is acceptable: writing → `cargo build` → fix → done, with no extended compiler battles.

## Next

- Step 11: `ovp-cli graph` command.
- Step 12: `ovp-cli run --fake` command, emits `.run/plans/*.json` + `.run/events/*.jsonl`.
- After v0.1 is shipped: pause again, decide the order of `paper` vs `github` source for R3, decide LLM client crate choice (and re-examine async invariant).
