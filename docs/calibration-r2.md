# Calibration R2 — C v1 done

**Date:** 2026-05-27
**Trigger:** `cargo test -p ovp-domain --test article_clean` is green. The full v1 article pipeline reads `fixtures/article_clean/input.md`, replays a cassette offline, and the contract assertion engine confirms every MUST + SHOULD clause passes.
**Decision:** **Continue with Rust**. C v1 architecture holds.

## What got built

| Step | What | LOC (src) | Tests |
|---|---|---|---|
| C1 | `EffectfulTransform` trait + invariant docs + arch gate | +35 | unchanged |
| C2 | `ovp-llm` scaffold (ModelClient + Fixture/Cached/NeverCalls) | +250 | +8 |
| C3 | `ovp-domain` body types | +200 | +8 |
| C4 | `MarkdownInboxSource` + `PromptBuilder` + prompt asset | +425 | +6 |
| C5 | `LLMInvoker` (effectful) + `ArticleParser` | +560 | +10 |
| C6 | `ArticleVaultPlanSink` | +295 | +3 |
| C7 | Contract assertion engine | +375 | +5 |
| C8 | Manifest + CLI subcommand + hand-rolled cassette | +105 | smoke only |
| C11a | URL tracker normalization in source | +85 | +5 |
| C11b | Article integration test (MUST + SHOULD green) | +130 | +3 |
| **Total** | | **~2460 src** | **+48** (11 → 59) |

Cumulative LOC across all crates: **~4650 src + ~720 tests**. Budget pre-C was 1500 for v0.1; C added 2x the v0.1 codebase across three crates. Well within "reasonable for a first real domain slice" — the alarm threshold would have been more like 6000.

## Architecture: what held up

1. **The three-layer model.** `ovp-core` stayed sync. `ovp-llm` is the only crate that would ever pull in `reqwest`, and it's behind a feature flag that defaults off — the v1 test build pulls zero HTTP deps. The article pipeline's only effect (the LLM call) lives in `LLMInvoker`, an `EffectfulTransform` that holds `Box<dyn ModelClient>`. The pipeline's Transform trait never saw a network call.

2. **`Record<B>` generic** absorbed the introduction of `DomainBody` without changing `ovp-core` at all. The runner is monomorphic at the application level (`GraphRunner<DomainBody>`); transforms match on the variant they expect. Wrong-variant records drop with named reasons that surface in the event log — exactly the diagnostic story the v0.1 type design promised.

3. **`EffectfulTransform` split** felt right from day one. The CI gate (`impl Transform<...> for T` + `Box<dyn .+Client>` field heuristic) caught nothing because we used the right trait, but the alternative — relaxing #9 — would have left a soft fence that future PRs would erode.

4. **`ResponseContent` enum from day one** cost two extra lines and got us forward-compat insurance. The day we want a `Stored(ResponseId)` variant for large payloads, no API breaks.

5. **Provider-neutral wire types in `ovp-llm`** (`ModelRequest` / `ModelReply`, separate from `ovp-domain::ModelResponse`) prevented the crate cycle codex flagged in stage-c review. `LLMInvoker` is the only place that maps wire ↔ domain, exactly where you'd want that mapping.

6. **Hand-rolled cassette + replay-only test gate.** The integration test is fully offline, deterministic, no API key needed in CI. Live runs are a separate build (`cargo build --features anthropic` once C9 lands). This separation is more valuable than I expected — it means every contributor can re-run the gate locally without secrets.

## What surprised us

1. **URL normalization is real.** The contract demands `?source=post_button` stripped from `source`. Adding `strip_tracker_params()` to `MarkdownInboxSource` regenerated the prompt → request hash → cassette key. We caught this immediately because the smoke test produced zero ops; if we had set up the integration test against the un-normalized URL, we'd have shipped a brittle contract. Lesson: **anything in the SourceDoc that flows into the prompt is part of the cache key**. Normalize at ingest, not later.

2. **The `origin: Box<SourceDoc>` sidecar on `PromptRequest` + `ModelResponse`** was not in the original stage-c plan. It became obvious once writing `ArticleParser` — the parser needs `source_url`, `author`, etc., none of which the LLM returns. The plan-level fix (carry SourceDoc as a sidecar through the LLM boundary) was cheaper than the alternatives (asking the LLM to echo metadata back, or threading a separate side channel through the runner). For paper / github later, the same pattern probably applies.

3. **Boxing every variant in `DomainBody`** was forced by clippy's `large_enum_variant`. Not a problem — was actually the right call architecturally — but it added `Box::new(...)` noise to every transform's emit site. Future me will probably write a small `body!()` macro to hide it.

4. **`pub(crate)` doesn't work for tests in `tests/`.** Tests in the `tests/` directory are external consumers of the crate, so they only see `pub`. The `article_clean.rs` test had to re-drive the pipeline via the public `Source` + `Transform` API rather than reaching into `parse_clipping` directly. Slight duplication of work; clean public surface in exchange.

5. **`ovp-domain` ended up bigger than `ovp-core`** (~1890 vs ~1250 src). That's the right ratio — core is the small, slow-moving abstraction; domain is where business logic actually lives.

## What we'd change for v1.1

- **`article_mixed_lang`** is next. It adds: UTF-8 throughout (already partially exercised — Chinese tags work), source URL canonicalization beyond tracker stripping (Twitter → article — needs a `SourceResolution` event), and the two-tier canonical/candidate split (parser currently puts everything in candidates; absorb stage adds canonical promotion).

- **`PromptBuilder` / `ArticleParser` need versioning discipline.** Right now `ARTICLE_SCHEMA_VERSION = 1` is a const. When we revise the prompt for mixed_lang, that version bumps to 2, the parser refuses v1 responses, and the cassette regenerates. Make sure the cassette path includes the version (`cassettes/v2/<hash>.json`) so old cassettes are visible to humans but not silently accepted.

- **Routing layer.** v1 has one source kind and one interpreter. mixed_lang stays in the same lane; `paper` and `github` need a `RouteBySourceKind` transform that dispatches based on `source_type`. Don't build it speculatively — wait until paper actually lands.

- **Sink hash should be over the *content*, not the rendered body.** Currently `ContentHash` covers the full markdown body including frontmatter. If two pipeline runs differ only in `pipeline_run_id` (which we don't emit, but might), they'd hash differently. Defer until PlanApplier exists.

## What stayed deferred

- **C9 (AnthropicBlockingClient)**: not blocking v1. The test gate has no live-LLM dependency. Ship when someone needs to do a real interpretation run from the CLI; gate behind `--features anthropic`.
- **C10 (cassette capture)**: hand-rolled cassette works for v1. The day we want real LLM-quality validation, run `CachedModelClient(AnthropicBlockingClient, Record)` once and commit the result. The infrastructure is ready; only the API key is missing.
- **PlanApplier**: still deferred. WritePlan is dry-run only. The day we want to actually mutate a vault, this is the next step.
- **More fixtures** (mixed_lang, paper, github): captured but not exercised by integration tests yet. Each one is a v1.x increment.

## Invariant audit

All 12 invariants from `docs/invariants.md` hold:

| # | Invariant | Status | Gate |
|---|---|---|---|
| 1 | ovp-core is domain-blind AND I/O-blind | ✅ | cargo deps + grep |
| 2 | no serde_json::Value in core public API | ✅ | grep |
| 3 | no HashMap<String,_> in body payloads | ✅ | grep |
| 4 | no subprocess to python/ovp | ✅ | grep |
| 5 | no pyo3, no embedded Python | ✅ | grep |
| 6 | no async runtime in ovp-core | ✅ | grep |
| 7 | no legacy imports | ✅ | grep |
| 8 | pipeline topology is explicit (+ wiring is app-layer) | ✅ | review |
| 9 | Transform pure, EffectfulTransform isolated | ✅ | grep heuristic + review |
| 10 | Writes only through WritePlan | ✅ | review |
| 11 | Derived state rebuildable | N/A | (no derived state yet) |
| 12 | EventLog append-only | ✅ | review (EventLog has no remove method) |

Arch gate: 11/11 checks green.

## Verdict

**Ship C v1.** The vertical slice is real:

```sh
cargo test                  # 59/59 green
cargo clippy --all-targets --workspace -- -D warnings   # clean
bash scripts/check_architecture.sh   # 11/11 invariants
cargo run -p ovp-cli -- interpret-article \
  --input fixtures/article_clean/input.md \
  --out .run/article \
  --cache-dir crates/ovp-domain/tests/cassettes
# → produces .run/article/plans/demo-article.json with 1 VaultCreate op
# → produces .run/article/events/demo-article.jsonl with 12 events
# → satisfies fixtures/article_clean/expected/contract.yaml
```

Next session: pick v1.1 target (`article_mixed_lang` recommended), or shift focus to PlanApplier if "actually write to a vault" matters before adding fixture coverage.
