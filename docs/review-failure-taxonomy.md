# Review-Queue Failure Taxonomy — and the decidability rule (M35)

**Origin:** the first human review batch (20 of 120 caveated claims, 2026-07-06 AI-suggestion
audit) was treated as a failure audit of the SYSTEM, not as 20 items to fill in. This doc fixes
the taxonomy, the layer each failure class belongs to, and the iteration rule that keeps fixes
from becoming a patch tower.

## The decidability rule (how we choose between "harness rules" and "prompt/LLM")

The choice is never "more rules vs more prompt". It is: **does this check have a decidable
ground truth?**

- **Decidable** (a quote is verbatim or not; distinct sources are 1 or 2; two claims cite the
  same unit or not) → deterministic code. These are orthogonal AND-filters: independently
  testable, they do not accumulate into a conflicting pile.
- **Not decidable** (is this wording overbroad? is this an implementation detail?) → the LLM
  judge — constrained by structured output, calibrated against a labeled set
  (`crates/ovp-domain/tests/fixtures/review-hygiene-m35/`), versioned via the evolution flow.
- **Never**: natural-language rule piles inside prompts ("don't say commonly/most/should") —
  unfalsifiable, conflict-prone, unmeasurable. When tempted, either find the decidable proxy or
  hand it to the judge and measure.

History backs this: M7–M13 (rules doing the LLM's job) failed; the verbatim gate (LLM proposes,
code disposes) is the moat; S2v3's good-prompt arm beat its rule-ladder arm.

## The five failure classes and where each is fixed

| # | Class | Example | Decidable? | Layer / fix |
|---|---|---|---|---|
| 1 | Single-source Supported claims occupying the human queue | 14 of the batch of 20 | YES (`distinct_sources` + verdict) | `review_lane()` routing: lane `source_insight`, parked outside the human queue until more sources arrive (this PR) |
| 2 | Single-source observation worded as a universal law | "literature converges", `agents-b002-3` | Partially — **the judge already catches most** (that IS why they were caveated); residual wording quality | Routing puts judge-flagged items in the human queue (this PR); judge criteria upgrade = Phase 1 evolution candidate; synth prompt scope-discipline LAST, only if still needed |
| 3 | Duplicates entering the queue | `agents-b003-8` vs `agents-b010-7` | **Split finding** — see below | Shared-evidence dups: deterministic `collapse_review_duplicates` (this PR). Semantic dups: lineage/judge, NOT Phase 0 |
| 4 | Implementation detail promoted toward Crystal | `agents-b002-6` | NO | Judge classification (Phase 1). Note: on real data the judge had already marked it `overreach`, so it correctly stays in the human queue |
| 5 | Review artifacts not self-contained (no citations) | reviewer had to dig in `.run/` | Tooling bug | `ReviewEntry.citations` + review-sheet rendering (this PR) |

**The class-3 boundary discovery (recorded so nobody "fixes" it wrongly):** the audit's flagship
duplicate pair shares **zero** cited units and has claim-text jaccard 0.27. It is the SAME idea
supported by DIFFERENT evidence — which makes it a *strengthen/lineage* candidate (merging the
two evidence sets makes a stronger claim), not a deduplication bug. A threshold loose enough to
collapse it deterministically would be a wording heuristic in disguise. The regression test
asserts it does NOT collapse.

## The regression set

`crates/ovp-domain/tests/fixtures/review-hygiene-m35/{claims.json,labels.json}` — the 20 real
claims with citations + strength verdicts, labeled with audit actions, tags
(`single_source`, `overgeneralized_caught_by_judge`, `implementation_detail`,
`semantic_duplicate_pair`) and expected lanes. Tests
(`crates/ovp-domain/tests/review_hygiene_m35.rs`) pin, with zero model calls:
human queue 20 → 6 · judge-flagged items never parked · real batch has no decidable duplicates ·
an injected cross-run shared-evidence duplicate collapses.

## Iteration metrics (a change "worked" only if these move)

Compared on the NEXT 20-item batch: human-queue review-yield (share of items getting a non-keep
decision) ↑ · queue size ↓ · decidable duplicates in queue = 0 · durable-claim grounding
unchanged (34→22 fixture untouched) · spot-check finds no valuable claim wrongly parked.

## Phase 1 (gated — each one an evolution candidate, one surface at a time)

1. `crystal_strength/v2`: overreach definition explicitly includes source-scope (single-source
   evidence worded as a universal = overreach) + an `implementation_detail` classification.
   Evaluated against the labeled 20.
2. `crystal_synth/v2` scope-discipline wording — ONLY if the next batch still shows overbroad
   generation after 1 lands.

## Migration note

Pre-M35 queue entries have no citations/lane (serde defaults keep them loading as lane=review).
To populate: re-run the vault crystal-synth replay (cassettes make it free) — `write_durable`
rebuilds `review.json` with citations + lanes.
