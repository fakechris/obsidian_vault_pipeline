# OVP Next — Stage C v1

## Context

v0.1 + B fixtures + R1 codex fixes give us a validated type design (`Record<B>`, `Filter<B>`, `GraphRunner<B>`, `WritePlan`, `EventLog`) and a frozen behavioral contract (4 fixtures). What we don't yet have is **a single real document making it through the pipeline end-to-end** — no real domain types, no real LLM call, no real interpretation.

C v1 builds the smallest vertical slice that proves it: a single English article (`fixtures/article_clean`) flows from `input.md` through real domain types, through the real `ModelClient` trait (replay mode against a captured cassette in tests, live mode behind a feature flag for actual API calls), and the output satisfies that fixture's `contract.yaml` MUST clauses.

C v1 is **not** about prompt coverage, fixture breadth, or production polish. It's about proving the three-layer architecture (pure pipeline core + I/O effect boundary + app layer) holds up under real domain types. If C v1 is solid, C v1.1 (mixed_lang) is incremental; if not, we'd rather find out on one fixture than four.

## Locked decisions (from the discussion this turn)

1. **Three-layer model** (final):
   - `ovp-core` stays sync forever. The Transform trait is pure (no I/O). Source is the ingestion boundary (allowed to do I/O to bring records into the pipeline — file reads, future network polls). Sink emits `WriteOp`s only — the actual writes happen in a future `PlanApplier`, not in the Sink itself.
   - Effect boundary (`ModelClient`, future `Store` / `Fetcher`) is a separate trait in its own crate. **NOT a sub-trait of Transform.**
   - App layer drives I/O, feeds typed records back into the pipeline. Today sync CLI; later possibly tokio executor that lifts the actual I/O call out of the pipeline at the LLMInvoker boundary (suspend-and-resume).

2. **Invariant #9 split (option b)**: introduce `EffectfulTransform<B>` as a distinct trait in `ovp-core`. `Transform<B>` = pure, deterministic, no I/O. `EffectfulTransform<B>` = sync facade over an effect client, replayable, executor-owned in future. The runner treats both identically; the type system distinguishes intent. CI gate greps `impl Transform.*for` and rejects structs that hold `*Client` / `*Store` / `*Fetcher` fields.

3. **Invariant #8 footnote**: explicit topology = `manifest.toml + app-layer wiring`. Manifest alone is not the single source of truth. Effect client wiring, prompt asset versions, cache paths, model choice — all app-layer.

4. **ArticleInterpreter split into three nodes.** All records in the pipeline share one body type — `Record<DomainBody>` — where `DomainBody` is an enum. Each transform matches on its expected variant, processes it, and emits a different variant. Wrong-variant records drop with a `runner.transform.wrong_variant` reason (impossible in practice given correct manifest wiring; the runner stays homogeneous in `B`).
   - `PromptBuilder` (Transform, pure): `DomainBody::Source(SourceDoc)` → `DomainBody::Prompt(PromptRequest)`
   - `LLMInvoker` (EffectfulTransform): `DomainBody::Prompt(PromptRequest)` → `DomainBody::Model(ModelResponse)` via injected `Box<dyn ModelClient + Send + Sync>`. Pipeline-sync; client impl may be blocking or `Handle::block_on(async)`.
   - `ArticleParser` (Transform, pure): `DomainBody::Model(ModelResponse)` → `DomainBody::Interpreted(InterpretedDoc)`

   A future `RouteByVariant` transform could remove the per-transform variant matching by dispatching to the right next node — defer to v1.1+ when there's a second source kind.

5. **C v1 fixture target**: `article_clean` only. mixed_lang / paper / github come in C v1.1, v1.2, v1.3 — each as a separate increment.

6. **Crate split**: two new crates.
   - `ovp-domain`: types + transforms + source + sink for v1 article path. Zero LLM HTTP deps.
   - `ovp-llm`: `ModelClient` trait + `FixtureModelClient` + `CachedModelClient` + `AnthropicBlockingClient`. `reqwest` only here.
   - `ovp-cli` (existing): wires `ModelClient` into `LLMInvoker`, runs the graph.
   - **No `ovp-filters` split** until a second app consumer exists.

7. **`ResponseContent` enum from day one** (single-variant `Inline(String)`). Leaves room for `Stored(ResponseId)` later without API break.

8. **LLMInvoker concurrency ceiling acknowledged**: the "sync Transform internally calls block_on" approach works for v0.1/v1 single-pipeline runs. When concurrent / batched LLM calls become a real need, the actual call moves **out of** the Transform and **into** the executor (suspend-and-resume model). That's a future migration point, not a v1 problem — but it's documented so it's not a surprise.

## What gets built

### Changes to existing crates

**`ovp-core`:**
- Add `EffectfulTransform<B>` trait in `filter.rs` (~15 LOC). Same shape as `Transform<B>` (`step_id()` + `process()`), distinct trait identity.
- Add `Node::EffectfulTransform` variant in `graph.rs`; runner handles it identically to `Transform` (same FilterDecision dispatch).
- Register helper: `register_effectful_transform(name, tx)`.

**`docs/invariants.md`:**
- Rewrite #9: "Transform is pure. EffectfulTransform is a sync facade over an injected effect client, replayable in tests, executor-owned in future. Effect clients (`*Client`, `*Store`, `*Fetcher`) never live in plain Transforms."
- Add footnote to #8: "Manifest describes topology; effect client wiring and node configuration are app-layer concerns. Single source of truth = `(manifest, app wiring)`."
- Move "no async runtime" from "Known v0.1 stubs" — it's now a permanent invariant on `ovp-core`, not a stub.

**`scripts/check_architecture.sh`:**
- New gate: scan `crates/ovp-core/src/**.rs` (and any `impl Transform<.*> for` in `ovp-domain`) — fail if the struct fields name `*Client` / `*Store` / `*Fetcher`. (Heuristic; misses obscure cases but catches the obvious ones.)

### New crate: `ovp-domain`

```
crates/ovp-domain/
├── Cargo.toml
└── src/
    ├── lib.rs
    ├── body.rs          # DomainBody enum (Source | Prompt | Model | Interpreted)
    ├── source_doc.rs    # SourceDoc + frontmatter parsing
    ├── prompt.rs        # PromptRequest + schema versioning
    ├── response.rs      # ModelResponse (domain body type) + ResponseContent enum
    ├── interpreted.rs   # InterpretedDoc (6-dimension shape, matches article contract)
    ├── sources/
    │   └── markdown_inbox.rs  # MarkdownInboxSource (reads input.md from a path)
    ├── transforms/
    │   ├── prompt_builder.rs   # SourceDoc → PromptRequest (variant transition)
    │   ├── llm_invoker.rs      # EffectfulTransform; calls ovp-llm::ModelClient, wraps wire reply
    │   └── article_parser.rs   # ModelResponse → InterpretedDoc
    ├── sinks/
    │   └── article_vault_plan.rs  # InterpretedDoc → VaultCreateOp
    ├── testing/                # behind `testing` feature; not built in prod
    │   └── contract.rs         # parse + assert contract.yaml (limited op set)
    └── prompts/
        └── article_interpret.md   # versioned prompt asset, loaded via include_str!
```

Deps: `ovp-core`, `ovp-llm` (for the `ModelClient` trait + wire types), `serde`, `serde_yaml`.

**Dependency direction is one-way:** `ovp-domain` → `ovp-llm`. `ovp-llm` does **not** depend on `ovp-domain`. Two `ModelResponse` types exist by design — `ovp-llm::ModelReply` is provider-neutral wire data (text, stop_reason, token counts); `ovp-domain::ModelResponse` is the domain body type that wraps it in `ResponseContent::Inline(...)` plus prompt provenance. `LLMInvoker` is the only place the mapping happens.

LOC budget: ≤1500 across the crate. Single transform ≤250.

### New crate: `ovp-llm`

```
crates/ovp-llm/
├── Cargo.toml
└── src/
    ├── lib.rs
    ├── client.rs        # ModelClient trait (sync), CallError, NeverCallsClient
    ├── request.rs       # ModelRequest, ModelMessage — provider-neutral wire shape
    ├── reply.rs         # ModelReply (wire shape — text, stop_reason, usage counts)
    ├── fixture.rs       # FixtureModelClient (maps a key → canned ModelReply)
    ├── cache.rs         # CachedModelClient (wraps inner client, file-backed cache, record/replay)
    └── anthropic.rs     # AnthropicBlockingClient — gated behind `anthropic` feature
```

**Provider-neutral on purpose.** `ovp-llm` defines `ModelReply` as a wire-level reply (text + stop_reason + usage). It knows nothing about domain shapes. `ovp-domain::ModelResponse` (which carries `ResponseContent::Inline(...)`) is a separate type — `LLMInvoker` does the mapping. This keeps `ovp-llm` reusable for any future I/O effect and prevents the crate cycle codex flagged.

**Feature-gated provider impl.** `reqwest` is **NOT** a default dep. `AnthropicBlockingClient` lives behind `--features anthropic`:

```toml
[features]
default = []
anthropic = ["dep:reqwest"]

[dependencies]
serde = { workspace = true }
sha2 = "0.10"
reqwest = { version = "0.12", default-features = false, features = ["blocking", "json"], optional = true }
```

`FixtureModelClient`, `CachedModelClient`, `NeverCallsClient` need zero HTTP. Tests build `ovp-llm` with default features (no reqwest). Only the CLI's live-run path enables `anthropic`.

**No tokio. No async.** If we ever want async, it's a new module behind a feature flag.

LOC budget: ≤800.

### `ovp-cli` changes

- New subcommand: `ovp-next interpret-article --input <path> --out <path> --client <fixture|cached|anthropic>`.
  - `--client fixture --fixture-dir <dir>`: uses `FixtureModelClient` reading canned responses from disk.
  - `--client cached --cache-dir <dir>`: uses `CachedModelClient` wrapping Anthropic; records first run, replays after.
  - `--client anthropic`: live, requires `ANTHROPIC_API_KEY`.
- Internally: parse manifest, construct `ModelClient` from flags, register transforms + sources + sinks, run GraphRunner.

### Contract assertion engine

A small library in `ovp-domain` (or a separate `ovp-fixtures-test` crate if it grows — defer):

- `pub fn assert_contract(contract_yaml: &Path, produced: &InterpretedDoc, events: &[Event], plan: &WritePlan) -> Result<(), ContractFailure>`
- Implements the limited op set documented in `fixtures/README.md` (equals, contains, type, length_gte, body_sections_present, event_emitted, writeplan_constraint, etc.).
- Doesn't try to be a general assertion engine. ~15 ops total, covers the fixtures we have.

### Pipeline manifest

`manifests/article.pipeline.toml`:
```toml
[pipeline]
nodes = [
  "markdown_inbox",
  "prompt_builder",
  "llm_invoker",
  "article_parser",
  "article_vault_plan",
]
edges = [
  ["markdown_inbox", "prompt_builder"],
  ["prompt_builder", "llm_invoker"],
  ["llm_invoker", "article_parser"],
  ["article_parser", "article_vault_plan"],
]
```

### Integration test

`crates/ovp-domain/tests/article_clean.rs`:

1. Load `manifest = manifests/article.pipeline.toml`.
2. Construct `FixtureModelClient` pointing at `crates/ovp-domain/tests/cassettes/article_clean.json` (a pre-recorded response).
3. Construct `MarkdownInboxSource` pointing at `fixtures/article_clean/input.md`.
4. Run the pipeline.
5. Assert against `fixtures/article_clean/expected/contract.yaml` via `assert_contract`.

The cassette file is committed to the repo. It's a real Anthropic response captured once, then frozen. Tests are fully offline + deterministic.

## Implementation order (one commit per step)

1. **C1**: `ovp-core` adds `EffectfulTransform`; invariants doc updates (#8 footnote, #9 split, Source/Transform/Sink wording); arch gate update (effect-client-in-Transform heuristic). No new functionality, just the trait + docs. Verify v0.1 tests still pass.
2. **C2**: `ovp-llm` crate scaffold — `ModelClient` trait, `ModelRequest`/`ModelReply` wire types, `FixtureModelClient`, `NeverCallsClient`, `CachedModelClient` (record + replay modes). No `reqwest` yet — `anthropic` feature not enabled. Unit tests against hand-rolled cassettes.
3. **C3**: `ovp-domain` crate scaffold — body types (`SourceDoc`, `PromptRequest`, `ModelResponse`, `InterpretedDoc`) + `DomainBody` enum + `ResponseContent::Inline(...)`. No transforms yet. Unit tests for serde round-trips + variant matching helpers.
4. **C4**: `ovp-domain` — `MarkdownInboxSource` + `PromptBuilder` + `prompts/article_interpret.md` (via `include_str!`). Unit tests: prompt builder produces correct `PromptRequest` given `SourceDoc`.
5. **C5**: `ovp-domain` — `LLMInvoker` (EffectfulTransform) + `ArticleParser`. `LLMInvoker` maps `PromptRequest` → `ovp-llm::ModelRequest` → `ModelClient.call()` → `ModelReply` → `DomainBody::Model(ModelResponse)`. Unit tests: parser handles a canned `ModelResponse` correctly.
6. **C6**: `ovp-domain` — `ArticleVaultPlanSink`. Unit tests: produces a `VaultCreate` op with the right path/body shape.
7. **C7**: Contract assertion engine in `ovp-domain::testing::contract` (behind `testing` feature). Unit tests: each op (`equals`, `contains`, `type`, `body_sections_present`, `event_emitted`, `writeplan_constraint`, etc.) on hand-rolled `InterpretedDoc` + `WritePlan` + event list.
8. **C8**: `manifests/article.pipeline.toml` + CLI subcommand `interpret-article` + wiring. Smoke run with `FixtureModelClient` hitting a hand-rolled cassette under `tests/cassettes/`. End-to-end pipeline works offline.
9. **C9**: `AnthropicBlockingClient` behind `anthropic` feature. Includes the `--client anthropic` and `--client cached` CLI paths. Documented as the "live run" build of the CLI: `cargo build --features anthropic`.
10. **C10**: One-time cassette capture. Run `CachedModelClient(AnthropicBlockingClient, cache_dir=tests/cassettes, mode=Record)` against `fixtures/article_clean/input.md`. Inspect the resulting cassette by hand; commit. From this point on, tests can replay deterministically without an API key.
11. **C11**: Integration test `crates/ovp-domain/tests/article_clean.rs`. Uses `CachedModelClient(NeverCallsClient, mode=ReplayOnly)` against the committed cassette. Asserts `fixtures/article_clean/expected/contract.yaml` via the assertion engine. Green = **C v1 done**.
12. **C12**: `docs/calibration-r2.md` — what worked, what we'd change for C v1.1, LOC report, invariant audit, sequencing notes.

Each step ≤1 commit. If any step balloons past ~400 LOC, split.

**Sequencing rationale (post-codex-review):** Anthropic + cassette capture both land before the integration test gate. The test gate itself uses replay-only mode (`NeverCallsClient`), so it has zero network dependencies and zero API-key requirements. Live runs require `--features anthropic` at build time and `ANTHROPIC_API_KEY` at runtime — never invoked by `cargo test`.

## Verification gauntlet (post-C11)

```sh
cd ~/Documents/ovp-next
# Default build — no anthropic feature, no reqwest, no network
cargo test                                                    # all tests pass, incl. article_clean
cargo clippy --all-targets --workspace -- -D warnings         # zero warnings
bash scripts/check_architecture.sh                            # arch invariants hold (11+ gates now)
cargo run -p ovp-cli -- interpret-article \
  --input fixtures/article_clean/input.md \
  --out .run/article_clean \
  --client cached \
  --cache-dir crates/ovp-domain/tests/cassettes \
  --mode replay-only
# expected: .run/article_clean/plans/*.json contains a VaultCreate op
#           whose body satisfies fixtures/article_clean/expected/contract.yaml

# Live build (separate; not part of CI gate)
cargo build --features anthropic
ANTHROPIC_API_KEY=... cargo run --features anthropic -p ovp-cli -- interpret-article \
  --input <some-real-article.md> \
  --client anthropic
```

Plus `docs/calibration-r2.md` with the verdict.

## What this plan does NOT commit to

- mixed_lang / paper / github — separate increments (v1.1, v1.2, v1.3).
- Async executor — when concurrency is a real need, not now.
- Plugin protocol — Pack architecture is still data-only.
- Live production runs — `AnthropicBlockingClient` exists by C11 but isn't part of the test gate.
- Persistent prompt/response stores — `ResponseContent::Stored(...)` variant deferred until we hit memory pressure.
- Multi-version prompt asset handling — single version, hardcoded path.
- `PlanApplier` (the A in the original A/B/C menu). Still deferred. WritePlan stays dry-run.

## Open questions

None on the critical path. Decided:

- **Client injection**: App layer constructs `Box<dyn ModelClient + Send + Sync>` and passes it to `LLMInvoker::new(client)` at registration time. Manifest doesn't reference it. Matches invariant #8 footnote (topology in manifest; wiring in app layer).

## Estimated cost

5-7 days at one-person + Agent pace. Most of the time is on C4-C8 (the actual transforms + prompt design + parser). LOC after C v1: ~2800-3300 across all crates (was 1456 at v0.1).
