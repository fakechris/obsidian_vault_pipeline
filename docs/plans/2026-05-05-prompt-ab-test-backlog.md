# Prompt evolution Phase 2-4 — backlog

> **Status**: deferred until evidence demands.  Phase 1 (prompt
> registry, this PR) shipped; Phase 2-4 are tracked here so the
> design intent doesn't drift, but **no implementation work** is
> planned until we have a concrete prompt iteration that needs
> them.
>
> Why this is a backlog doc, not a BL:
>
> * BL-058 (single prompt change, no A/B) revealed enough about
>   prompt-iteration patterns to design the registry, but **not**
>   enough about A/B routing semantics to commit to specifics.
> * Building an A/B framework before the first real A/B experiment
>   risks designing for imagined needs.  Current 1-stable-prompt
>   reality has zero use for routing.
> * The registry alone (Phase 1) lets us do offline comparison via
>   ``ovp-fidelity-sample`` + ``ovp-prompt-ab`` (both already exist),
>   which covers the small-N evaluation case without runtime A/B.

## What "evidence demands" looks like

We pick this back up when **one or more** of these triggers fires:

1. We have **two viable production-candidate versions of the same
   prompt** and need data on which is better — not "which is
   prettier", actual user-fidelity scores from a sample large enough
   to discriminate.
2. We're shipping an absorb v3 (or any major rewrite) and want a
   gradual rollout instead of cutover-and-pray.
3. The metrics aggregator from Phase 3 surfaces a regression we
   couldn't have caught without per-version stats.

Until one of those, Phase 1 + offline A/B tooling is enough.

## Phase 2 — A/B routing (deferred)

### What it would do

Within a single pipeline run, route X% of sources through prompt A
and (100−X)% through prompt B.  Each output carries a
`prompt_version` field on its frontmatter (already true via Phase 1)
PLUS an `extraction_prompt_experiment` field naming the experiment
arm so metrics can split rates by arm.

### Sketch

```yaml
# <vault>/.ovp/prompts.yaml
absorb:
  default: v2
  experiments:
    - name: v3-spec-rollout
      version: v3-spec
      percent: 10                # 10% of sources
      seed: 20260505             # deterministic split — same source,
                                  # same arm, across runs
      end_at: 2026-06-01         # auto-promote-or-kill date
```

Routing uses `hash(source_url) % 100 < percent` so the same source
deterministically lands in the same arm across runs.  Hash with the
seed mixed in so multiple concurrent experiments don't collide.

### CLI surface

```bash
ovp-prompt-experiment start absorb v3-spec --percent 10
ovp-prompt-experiment status absorb
ovp-prompt-experiment promote absorb v3-spec      # → stable
ovp-prompt-experiment kill absorb v3-spec         # → deprecated
```

### What it doesn't do

- **No per-source manual override**.  If the user wants to force a
  specific source through a specific version, they use
  `ovp-prompt-replay` (Phase 3).
- **No multi-armed bandit / contextual routing**.  Just stratified
  random assignment.

### Open questions

- Do we route at the LLM-call layer (cleanest) or at the
  pipeline-step layer (more flexible)?  Latter lets us run
  different prompts on different source types in the same run.
- How do we handle a source that gets re-extracted (e.g. via
  `ovp-reabsorb`) — does it stay in its original arm or roll the
  dice fresh?  Probably the original arm, to preserve longitudinal
  comparison.
- Does the rebuild_knowledge_index path care about experiment arms?
  Probably not (it just reads frontmatter), but we should verify
  before we have many concurrent experiments.

---

## Phase 3 — metrics & replay (deferred)

### Metrics aggregator: `ovp-prompt-metrics`

Reads `pipeline.jsonl` audit events + frontmatter `extraction_prompt_version`
field on every evergreen.  Produces a per-version report:

```
ABSORB METRICS (rolling 7d)

                          v2 (production)    v3-spec (experiment)
                          ───────────────    ────────────────────
count                     127                 14
skip_reason rate          12%                 8%
parse_error rate           0%                 7%      ← regression
avg specifics per unit    2.1                 3.4
avg unit_count per source 3.4                 2.8
```

The "avg specifics" number is the cheap proxy for fidelity that
captures the abstraction-inflation failure mode (BL-058's core
worry).

### Per-version fidelity sampling

Extension of `ovp-fidelity-sample` to **stratify by prompt version**.
Right now the tool samples evenly across all evergreens; in a
post-Phase-2 world, we want to compare like-for-like:

```bash
ovp-fidelity-sample --stratify-by extraction_prompt_version --per-version 20
```

Then the human-review HTML shows a v2 column and a v3-spec column
side by side.

### Replay tool: `ovp-prompt-replay`

Given a saved source body, re-run any registered prompt version
against it WITHOUT writing canonical state.  Outputs a JSON +
optional side-by-side HTML diff against another version.

```bash
ovp-prompt-replay \
  --source 50-Inbox/03-Processed/2026-04/foo.md \
  --prompt absorb \
  --version v3-spec \
  --diff-against v2 \
  --output 60-Logs/replay/<run-id>/
```

This is a pure superset of the current `ovp-prompt-ab` experiment
tool, generalized to any prompt by name and any pair of versions.
Existing `prompt_ab.py` would be deleted in favor of `prompt_replay`.

### Open questions

- Where does fidelity score come from?  Manual rubric (the HTML
  reviewer fills in) is the only honest answer for now.  An
  "automated fidelity check" via separate LLM call is the kind of
  bootstrap we deliberately rejected during BL-058 design.
- Does the metrics aggregator live in `truth_api` (queries
  knowledge.db), in a new `prompt_metrics.py`, or as a CLI that
  reads pipeline.jsonl directly?  Probably the third — keeps
  knowledge.db schema unchanged.

---

## Phase 4 — promotion gate (deferred)

Codify the criteria for promoting `experimental` → `stable`:

```yaml
# prompt_promotion_criteria.yaml
absorb:
  required_for_stable:
    min_runs: 100
    parse_error_rate_lt: 0.05
    skip_reason_rate_lt: 0.30   # too high = LLM is being lazy
    skip_reason_rate_gt: 0.05   # too low = LLM never skips, suspect
    fidelity_review_rate_gt: 0.85
    fidelity_review_n_gt: 20
```

`ovp-prompt-experiment promote absorb v3-spec` checks every
criterion and refuses (with the failing line) if any are unmet.

Phase 4 is straightforward once Phase 3 metrics exist.

---

## Why we're not building any of this now

1. **One stable prompt, no live A/B**.  The infrastructure has no
   user.  Building it would be premature abstraction for
   hypothetical future experiments.
2. **Phase 1 alone gets us 80% of the value** of moving prompts to
   versioned files: readable diffs, frontmatter introspection,
   audit-event tagging.  The remaining 20% (runtime routing) only
   matters when we have ≥ 2 candidate versions to compare in
   production.
3. **Offline A/B tooling already exists** — `ovp-prompt-ab` runs
   side-by-side comparison on a fixed source list, and
   `ovp-fidelity-sample` does the human-review side.  These cover
   the "evaluate a new prompt before shipping" need.
4. **Building it later is cheap**.  The Phase 1 audit-event fields
   (`prompt_name`, `prompt_version`) and frontmatter field
   (`extraction_prompt_version`) are exactly what Phase 3 metrics
   need to read; no schema migration required to add Phase 2/3.

## What to revisit when

- **When BL-058d (article-rewriter v2) lands**: re-evaluate.  If
  cutting over without an A/B period feels risky, that's when we
  build Phase 2.
- **When the offline `ovp-prompt-ab` workflow becomes a frequent
  manual chore**: the friction is the trigger to invest in Phase 3
  replay tooling.
- **Quarterly check-in**: even without a trigger, look at this
  doc every 3 months and ask "has anything changed that makes this
  worth building now?"
