# Stage M13.3 — Real v2 Concept-Map Loop (wiring complete; live run pending)

> **Status: wiring-complete + verified offline; real-green PENDING an operator
> live run.** Everything needed to run the real v2 concept-map loop is now built
> and tested: the empty-map fail-loud guard, the v2 prompt builder, the assembly
> node kind, and the `article_concept_map.pipeline.toml` manifest. The one thing
> this branch could NOT do is the live MiniMax call itself — the build/CI
> environment has **no network egress** to the model provider (see "Live
> boundary"). So the milestone is **synthetic-green (unchanged) + wiring-complete**,
> **NOT real-green**. Real-green requires the operator to run the live command
> below on a network that can reach the API.

## What M13.3 was solving

M13.2 proved the *pipeline* can carry a correct concept map given an ideal v2
response (synthetic-green). M13.3 is the harder question: **can a real LLM under
the v2 prompt produce a concept map that passes the committed benchmark?** That
requires wiring v2 into the real article pipeline + a live re-record, then
scoring real output against `scripts/concept_map_bench.py`.

## Phase 1 — empty-map fallback guard (DONE)

The blocker M13.2 flagged: `EvergreenConceptWriter` chose v1-vs-v2 by
`concepts.is_empty()`, so a v2 doc whose map gated to empty would silently mint
the v1 candidate path. Fixed:

- `InterpretedDoc` carries an explicit `schema: InterpretationSchema`
  (`ArticleV1` | `ConceptMapV2`), set by `ArticleParser` from the response
  `prompt_id`, preserved by `ConceptResolver`.
- `EvergreenConceptWriter` branches on the **marker**, not `concepts.is_empty()`.
  A `ConceptMapV2` doc with an empty map returns
  `FilterDecision::Error(transform.evergreen.empty_concept_map)` →
  `records_errored++` → `RunReport::is_clean()` / `RunCycleReport::succeeded()` /
  `review-run` all report the run as **not clean**. It never falls back to v1.

## Phases 2-3 — v2 prompt builder + manifest (DONE)

- `PromptBuilder` gained a `PromptVariant`; `PromptBuilder::concept_map(step)`
  emits `article_concept_map/v2` (distinct prompt_id + schema_version → own
  cassette namespace) from `prompts/article_concept_map.md`. v1
  `PromptBuilder::new` is unchanged and stays the default.
- New assembly node kind `transform.concept_map_prompt_builder` →
  `PromptBuilder::concept_map`.
- `manifests/article_concept_map.pipeline.toml` mirrors
  `article_evergreen.pipeline.toml` with that builder swapped in. Selectable by
  `run-cycle` / `review-run` via `--manifest` — no CLI hand-wiring.

Verified: workspace tests, `--features anthropic`, clippy (both feature sets,
`-D warnings`), and `scripts/check_architecture.sh` all green; the synthetic
benchmark is still 1/1; the v2 manifest assembles through the same
`llm_invoker → article_parser → concept_resolver → evergreen_concept_writer →
sinks` topology.

## Phase 4 — live re-record: BLOCKED by environment network egress

A live `run-cycle` for `rag_wrong` through the v2 manifest was attempted. It
reached the provider boundary correctly and failed at **transport**:

```
records_errored: 1
first error: transform.llm_invoker.transport: send: error sending request for
url (https://api.minimaxi.com/anthropic/v1/messages)
```

Diagnosis (not masked): `api.minimaxi.com` resolves to `198.18.0.218` (the
RFC-2544 benchmarking range used by network intercepts) and a bare `curl` to it
returns `404` — the domain is intercepted by this environment's network layer;
there is no route to the real MiniMax API. This is **environmental**, not a
wiring bug: the request was built with the right `prompt_id`
(`article_concept_map/v2`), routed through the assembled v2 graph, and the live
client POSTed to the configured endpoint. Only the network hop is missing.

**This means real-green cannot be produced in CI/this sandbox.** It is an
operator action on a network that can reach the API.

## Operator runbook — record v2 cassettes + score (run on a networked host)

```sh
# secrets stay in .env.live (gitignored); never echoed
set -a && . ./.env.live && set +a
export OVP_LLM_MAX_TOKENS=24000          # MiniMax-M2 reasoning headroom

# one run-cycle per benchmark article, v2 manifest, live client
for case in rag_wrong eval_ai_agents agent_memory_zh; do
  input="$(cat fixtures/concept_map/$case/input_path.txt)"
  cargo run -q -p ovp-cli --features anthropic -- run-cycle \
    --manifest manifests/article_concept_map.pipeline.toml \
    --input "$input" \
    --vault-root ".run/m13.3/live/$case/ovp/vault" \
    --canonical-root ".run/m13.3/live/$case/ovp/canon" \
    --cache-dir .run/m13.3/cassettes \
    --client live --date 2026-05-31 \
    --report ".run/m13.3/live/$case/report.json"
done

# score real output against the committed benchmark
python3 scripts/concept_map_bench.py --ovp-root .run/m13.3/live
```

`find_evergreen_dir` reads `<case>/ovp/vault/10-Knowledge/Evergreen`, so
`--ovp-root .run/m13.3/live` scores all three cases. `.run/` is gitignored — do
NOT commit it or the raw live packs.

## Phases 5-8 — pending the live run

Once the operator has live output, classify each benchmark failure before
touching code (Phase 5): **A prompt** (fix the asset, re-record) / **B
parser-schema** (only if general) / **C resolver gate** (only if a general
invariant) / **D fixture** (only with article-grounded rationale) / **E model
limitation** (document, do not fake green). Prefer prompt refinement over
production-logic changes (Phase 6); never encode benchmark slugs in production.
Then decide the default flip (Phase 7) and write the
`.run/m13.3/REAL_V2_SUMMARY.md` Nowledge/M12Q2 comparison (Phase 8, not
committed).

## Default flip — DEFERRED (correctly)

The article pipeline default is **still v1**. Flipping to v2 requires v2
cassettes for the committed fixtures (or tests adjusted to v2), which requires
the live run above. Until then v2 is explicit-only (`--manifest
manifests/article_concept_map.pipeline.toml`). Do **not** claim default
real-green.

## Benchmark scorecard

| Path | Result |
|---|---|
| v1 `.run/m12q2` baseline (the defect) | **0/3** |
| Synthetic v2 (ideal response, M13.2) | **1/1** (`rag_wrong`), deterministic |
| **Real v2 (live MiniMax)** | **NOT RUN — blocked by network egress; operator action** |
