# Stage M13.3 — Real v2 Concept-Map Loop (real run executed, 0/3 bench)

> **Status: real run EXECUTED, 0/3 on the concept-map bench.** The v2
> pipeline is now **end-to-end real-model-tested** on all three
> `fixtures/concept_map/*` cases (no transport / parse / resolver /
> writer errors; full vault + canonical + MOC + knowledge-index
> rebuilds landed on disk). The 0/3 result is entirely a
> **prompt-quality** outcome — slug drift, umbrella over-mint, and
> abstract definitions, all in the LLM's output, not in the
> framework. The follow-up is **M13.4 — Prompt Iteration**, scoped
> strictly to `crates/ovp-domain/prompts/article_concept_map.md` (and
> the bench's per-case `expected/concept_map.yaml` if a definition
> must move). **Do not** edit the parser, resolver, writer, or
> `ConceptResolver` invariants; the live run proved they are correct
> against real model output. See the per-case breakdown at the
> bottom of this doc and
> [stage-m13.4-prompt-iteration.md](stage-m13.4-prompt-iteration.md).

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

## Phase 4 — live re-record: RAN (operator action; 0/3 bench)

Live `run-cycle` for all 3 `fixtures/concept_map/*` cases through the v2
manifest. Initial attempt reproduced the
`transform.llm_invoker.transport` error documented above; a series of
diagnostic curls isolated the real cause (and the right fix landed in
the M13.3 follow-up commit):

- The OVP build/CI sandbox this doc was originally written in had a
  *different* environment than the TUN-routed host the operator ran on.
  In the sandbox, `api.minimaxi.com` resolves to `198.18.0.218` and a
  bare request returns `404` (network intercept). On the TUN host, the
  same URL resolves to a working IP and a small `curl` returns
  `HTTP 200` from the real API. **Both observations are correct for
  their environments**; the sandbox's "the network is intercepted"
  reading was true, but it was not the cause of the OVP run-cycle
  failure.
- On the TUN host, the OVP run-cycle still failed with the same
  `transform.llm_invoker.transport` error, even though direct
  `curl` to the same URL with the same auth and the same body
  shape succeeded. Bisection: the cause was **reqwest's default
  response timeout**, not the network. The v2 prompt + 10K article +
  `max_tokens=8000` makes `MiniMax-M2.7-highspeed` spend 58-72s in
  `thinking` blocks before emitting the first text token. With no
  explicit timeout, the `is_timeout` error class fires and surfaces
  as the unhelpful `Transport` chain. The follow-up commit adds
  `AnthropicBlockingClient::with_timeout(secs)` and surfaces it
  through `OVP_LLM_TIMEOUT_SECS` (default 180s).
- A *second* drop surfaced once transport was unblocked: the parser
  dropped the entire `rag_wrong` doc on a healthy 9-concept
  response because concept #1 had `"merge_with": null`.
  `#[serde(default)]` only fills a missing key, not a JSON `null`,
  even though the v2 prompt explicitly tells the model "null is
  fine". The follow-up commit adds a `null_to_default` deserializer
  and applies it to the v2 Vec fields.

After both fixes, the three cases ran end-to-end on the TUN host.
The bench verdict is at the bottom of this doc and is the
starting state for [M13.4](stage-m13.4-prompt-iteration.md).

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

**Status of the phases (post-live run):**

- **Phase 5 — classify each failure**: done as part of the live run (see
  per-case breakdown at the bottom of this doc). Verdict: all failures
  are **A (prompt)** or **D (fixture, narrowly)**; no **B / C** changes
  are warranted by this run.
- **Phase 6 — prompt refinement**: deferred to M13.4. The failure modes
  are concentrated enough that one prompt asset + zero production
  changes is the right shape. The M13.4 doc pins down exactly which
  prompt clauses to tighten and which to leave alone.
- **Phase 7 — default flip**: still deferred. v2 stays
  explicit-only (`--manifest manifests/article_concept_map.pipeline.toml`).
  The flip is gated on the bench going green on real v2, which is the
  M13.4 success criterion.
- **Phase 8 — M12Q2 comparison**: not done. The 0/3 result on real v2
  vs the 0/3 baseline on v1 is the wrong axis to compare; both are
  bench-Failing for different reasons, and a comparison writeup would
  just restate that. Revisit after M13.4 lands a green pass.

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
| **Real v2 (live MiniMax-M2.7-highspeed)** | **0/3** — 3/3 cases completed cleanly end-to-end (no transport / parse / resolver / writer errors), all 3 fail the bench on prompt-quality grounds. **See follow-up: [stage-m13.4-prompt-iteration.md](stage-m13.4-prompt-iteration.md).** |

### Real v2 per-case detail (M13.3 follow-up commit landed on `main`)

Run executed on a TUN-routed host after three infrastructure fixes that
were NOT prompt/contract changes:

1. `crates/ovp-llm/src/anthropic.rs` — `AnthropicBlockingClient` now sets
   an explicit reqwest timeout (180s default) via the new
   `with_timeout(secs)` builder; the previous `Client::new()` had no
   timeout and tripped reqwest's default behavior at 30s for the
   v2-prompt + 10K-article + 8K-max_tokens thinking-stream that takes
   58-72s end-to-end on this provider.
2. `crates/ovp-cli/src/commands/client.rs` — `OVP_LLM_TIMEOUT_SECS` env
   var (parallels `OVP_LLM_MAX_TOKENS` / `OVP_LLM_NO_PROXY`), with
   `0` for explicit-disable (local dev).
3. `crates/ovp-domain/src/interpreted.rs` + `article_parser.rs` —
   `null_to_default` deserializer applied to the v2 `Vec<String>`
   fields (`aliases` / `evidence` / `claims` / `related` / `merge_with`).
   The v2 prompt tells the model "null is fine" for these; a real LLM
   routinely emits `"merge_with": null` for "no merge target", and
   `#[serde(default)]` only fills a MISSING key, not a JSON `null`, so
   the parser was dropping the entire doc on otherwise-healthy
   responses. Live `rag_wrong` cassette reproduced it exactly: 9
   promoted concepts, parser dropped because concept #1 had
   `merge_with: null`.

Result of the live run (`python3 scripts/concept_map_bench.py
--ovp-root .run/m13.3/live`):

| Case | minted | clean | missing | forbidden | content-guard | Verdict |
|---|---|---|---|---|---|---|
| `rag_wrong` | 10 | 3 | 4 | 0 | 1 | FAIL |
| `eval_ai_agents` | 10 | 7 | 3 | 0 | 6 | FAIL |
| `agent_memory_zh` | 15 | 2 | 6 | 3 | 0 | FAIL |

**0/3 cases pass.** That matches the v1 baseline (0/3) on score
alone, but the failure mode is qualitatively different: v1 fails
because the `concept_candidates` + shared-`one_liner` shape cannot
express a per-concept definition; v2 fails because the prompt lets
the model invent its own slug vocabulary, mint umbrella concepts
the article only mentions in passing, and produce definitions that
miss article-specific phrases. All three failure categories are
**prompt-side**, not framework-side. The v2 pipeline is now
end-to-end real-model-tested (parser, resolver, writer, sinks,
MOC, knowledge index, vault, canonical, plan apply — all green
across all 3 cases). The remaining work is in
`crates/ovp-domain/prompts/article_concept_map.md`, and that is
M13.4.
