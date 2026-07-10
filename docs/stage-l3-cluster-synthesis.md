# L3 — LLM-shaped synthesis clusters (`crystal-synth --cluster-mode llm`)

Status: shipped on `feat/l3-cluster-synthesis` (2026-07-10). Follow-up to the
semantic theme system (`stage-semantic-themes.md` §L3 follow-up); operator-
approved KMEM-hybrid design. Default UNCHANGED: `--cluster-mode batch` keeps
Stage 3a byte-for-byte — the A/B below decides any default flip.

## Why

Batch mode cuts themes.json communities into deterministic ≤cap batches and
synthesizes each blindly: every pack gets exactly one shot inside whatever
community Louvain put it in, boundary packs get grouped with weak partners,
and the synth call is spent whether or not a claim-worthy cluster exists.
Real-vault state at design time: **1077 packs with accepted units, 585 (54.3%)
uncovered** — not cited by any ACTIVE durable claim. L3 replaces only the
*grouping* decision with a model that may also say "no opportunity here".

## Pipeline

```
coverage-first sweep: uncovered packs (not cited by any ACTIVE durable claim),
                      ascending case_id (deterministic)
  → seed's kNN neighborhood from cached embeddings
    (ovp-embed content-cache; cross-community allowed; top --neighborhood=12 by cosine)
  → NEW LLM call cluster_select/v1: seed + numbered neighbor digests
    (title + card titles — never quotes), picks 3..cap diverse case_ids that
    form ONE claim-worthy cross-source cluster, or REFUSES
    ("no opportunity" is a first-class answer)
  → mechanical validation: ids ⊆ offered set, ≥3, ≤cap
    (violation = fail-loud on that seed, recorded, sweep continues)
  → superset guard: an ACTIVE claim's source_cases ⊇ the chosen set → skip
    (logged, no synth spend); ditto a cluster already attempted this run
  → EXISTING crystal_synth/v1 + grounded filter + exact-citation dedup +
    crystal_strength/v1 + provenance/strength gates + idempotent durable
    write — completely unchanged
  → seeds covered by claims routed durable this run drop out mid-sweep
```

Layer separation is the same as L2: the selector shapes GROUPS ONLY. It can
never invent evidence (the synth prompt still only sees accepted units), never
touch the gates, and never write. A worst-case selector costs synth calls; it
cannot corrupt the ledger.

## `cluster_select/v1` prompt contract

- **Input** (pretty-JSON in the user message, so cassette keys are a pure
  function of digests + caps): `{min_cases: 3, max_cases: <cap>, seed:
  {case_id, title, card_titles}, neighbors: [<same shape>...]}`.
- **Output**: strict JSON, either `{"selected_case_ids": [...], "rationale":
  "..."}` or `{"refuse": true, "reason": "..."}`.
- Instructions demand: ids copied verbatim from the offered digests; 3..cap
  distinct cases; content diversity (different sources/angles — no
  same-source rehash / translation / serialized-parts clusters unless
  corroboration is the point); prefer including the seed but MAY exclude it
  when the neighbors form a genuinely stronger cluster; ONE tight cluster,
  not a grab-bag; refusal is explicitly framed as a good answer.
- Registered as `prompt.cluster_select` in `evolution/components.json`;
  candidate `evolution/candidates/cluster_select-v1.json` (passes
  `ovp2 evolve validate --candidate ...`). Cassette namespace
  `cluster_select/v1`, recorded/replayed like every other call.

## Guard semantics

- **Selection validation** (mechanical, `validate_selection`): selected ids
  are deduped + sorted; every id must be in the offered set (seed +
  neighbors); ≥3 and ≤cap distinct ids. A violation records outcome `failed`
  for that seed (with the reason), invalidates the cassette under a recording
  cache (a rerun re-asks), and the sweep continues. Infra failures (cassette
  miss, unrecoverable JSON after one repair) stay run-fatal — same fail-loud
  contract as batch mode.
- **Superset guard**: before the synth spend, if any ACTIVE durable record's
  `source_cases` ⊇ the selected set, outcome `guarded` (logs the guarding
  `claim_key`). Note the guard can only fire on selections that exclude the
  seed — a selection containing an uncovered seed is never a subset of an
  active claim's sources. Repeat clusters within one run are also `guarded`.
- **Mid-run coverage**: after each cluster's strength verdicts, claims are
  routed with the SAME `final_routing` the write path uses; cases cited by
  durable-routed claims join the covered set, and later seeds already covered
  record outcome `covered` without spending budget.
- **Budget**: `--max-seeds` (default 25) caps `cluster_select/v1` calls per
  run; hitting it sets `budget_exhausted` in the stats (rerun to continue —
  coverage state comes from the ledger, so sweeps are resumable and
  idempotent).
- **Zero grounded claims** is a SUCCESS in llm mode (refusals/guards
  everywhere are a legitimate sweep result), still a gate error in batch mode.

## Run report

- stdout: one `l3[<seed>]: <outcome>` line per seed + sweep totals + coverage
  delta (`N → M uncovered pack(s)`).
- `<work-dir>/l3-sweep.jsonl` (written incrementally): per-seed
  `{seed, outcome: selected|refused|guarded|failed|covered, selected?,
  rationale?, reason?, error?, guarded_by?, claims?, grounded?,
  durable_routed?, newly_covered?}`.
- `<work-dir>/l3-sweep-stats.json`: counters incl. `uncovered_before/after`,
  `select/synth/strength_calls`, `budget_exhausted`.
- The usual artifacts (`candidate.json`, `candidate.grounded.json`,
  `deduped-claims.json`, `strength.json`, `warnings.json`) are written in both
  modes.

## Requirements / degradation

llm mode needs an embedding cache (`--vault-root` → `.ovp/cache/embeddings`,
or `--embed-cache-dir`). Text derivation is IDENTICAL to `crystal-themes`
(title + cleaned reader.md head, `EMBED_MODEL_ID`, cap 128), so a themes run
warms the sweep. Missing vectors: embedded in `--features embed` builds,
otherwise a **clear error naming the remedy** — llm mode is meaningless
without neighborhoods, so there is deliberately NO graceful skip (unlike
crystal-themes' degradation contract). Themes.json is optional: when present
it only names the synthesis-context theme (seed community's deterministic
c-TF-IDF keywords — never display labels); absent → `"cross-source"`.

## A/B experiment harness (`--experiment`)

`ovp2 crystal-synth --experiment` samples a deterministic seeded slice
(`--experiment-slice`, `--experiment-seed`, SplitMix64 Fisher–Yates) of the
UNCOVERED packs from the real store (READ-ONLY — the experiment never writes
the vault store), materializes the slice into `<work>/slice-packs/`, and runs
both arms against the SAME packs:

| arm | mode | work dir | store | cassettes |
|---|---|---|---|---|
| A | batch | `<work>/arm-a` | fresh `<work>/arm-a/store` | `<work>/cassettes-arm-a` |
| B | llm | `<work>/arm-b` | fresh `<work>/arm-b/store` | `<work>/cassettes-arm-b` |

Arm B's `--max-seeds` defaults to the slice size. One `--client live` run
records both cassette sets; every rerun replays offline. Output: comparison
table on stdout + `<work>/comparison.json` with, per arm: **durable yield per
synth call, gate pass rate, mean distinct sources per durable claim, refusal
rate, total LLM calls** (select + synth + strength), synthesized/grounded/
durable/review counts, and coverage delta (arm B).

## Tests (fixture/replay only — no live calls)

`ovp-domain` `crystal::select`: digest extraction (card titles, ordinal +
`_tag_` stripping, CJK), request purity (cap in the key), parse
selected/refused/garbage, validation bounds. `ovp-cli`
`crystal_synth_llm`: sweep-order determinism; full e2e
(selected/covered/refused/failed in one sweep + idempotent rerun adds 0);
superset guard fires BEFORE the synth spend and leaves the ledger untouched;
`--max-seeds` budget; missing-embeddings clear error; missing cache-dir
config error; neighborhood ranking; theme = keywords never labels.
`crystal_synth_ab`: seeded sample determinism; full two-arm replay experiment
(real store untouched, comparison.json metrics). All pre-existing batch-mode
tests unchanged and green.

## RESULTS (TODO — operator live run)

> Not yet run against the live model. Record once, then replays are free.
> Baseline at design time: 1077 packs / 585 uncovered (54.3%).

```bash
# 0) one-time prep (warms embeddings + themes; skip if already fresh)
ovp2 crystal-themes --vault-root ~/Documents/ovp-vault

# 1) record the A/B once (30-pack slice, seeded) — LIVE calls
OVP_LLM_NO_PROXY=1 ovp2 crystal-synth --experiment \
  --vault-root ~/Documents/ovp-vault \
  --work-dir ~/Documents/ovp-vault/.ovp/l3-ab \
  --experiment-slice 30 --experiment-seed 42 \
  --client live

# 2) offline re-analysis any time (same slice, replayed cassettes)
ovp2 crystal-synth --experiment \
  --vault-root ~/Documents/ovp-vault \
  --work-dir ~/Documents/ovp-vault/.ovp/l3-ab \
  --experiment-slice 30 --experiment-seed 42

# 3) fill in this table from .ovp/l3-ab/comparison.json
```

| metric | arm A (batch) | arm B (llm) |
|---|---|---|
| total LLM calls | TODO | TODO |
| synth calls | TODO | TODO |
| durable claims | TODO | TODO |
| durable yield / synth call | TODO | TODO |
| gate pass rate | TODO | TODO |
| mean distinct sources / durable | TODO | TODO |
| refusal rate | — | TODO |
| uncovered before → after (slice) | — | TODO |

Decision rule (pre-registered in `evolution/candidates/cluster_select-v1.json`):
flip the default to llm only if arm B ≥ 1.3× durable yield per synth call AND
≥ arm A on mean distinct sources AND total calls ≤ 3× arm A. Otherwise keep
batch and iterate the prompt as `cluster_select/v2` through the evolution flow.

After a GO, the production sweep is:

```bash
OVP_LLM_NO_PROXY=1 ovp2 crystal-synth --vault-root ~/Documents/ovp-vault \
  --cluster-mode llm --max-seeds 25 --client live \
  --refresh --date $(date +%F)
# resumable: rerun until `coverage: N → M` stops moving or refusals dominate
```
