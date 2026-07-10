# Stage M14a.7 — faithful-coverage prompt fix (ATTEMPTED, reverted)

> **Status: the bounded prompt fix (v6) over-corrected and is WORSE than v5 —
> reverted to v5.** M14a.7 tried to kill v5's single faithfulness P0 by hardening
> `text ⊆ quote` and requiring a definition's quote to BE the full defining
> sentence. Live re-record + M14a.5 re-eval showed it traded that P0 for a
> coverage crash on eval/zh and over-extraction on rag. The committed prompt
> stays at **v5 (M14a.6)** — the best balance achieved. The real lesson is
> structural (below): single-pass prompt tuning oscillates across
> coverage/faithfulness/conciseness and is not converging.

## What was tried (v6, prompt-only)

`unit_extract/v6`: re-hardened `text ⊆ quote` (text may only state what its quote
contains — no completing from context); required definition units to quote the
article's actual defining sentence (use the adjacent-span window) or emit an
assertion instead; restated "coverage never overrides grounding OR faithfulness".

## Result — v4 → v5 → v6 (semantic coverage / required-missing / P0)

| case | v4 (M14a.5) | v5 (M14a.6) | v6 (M14a.7) |
|---|---|---|---|
| rag_wrong | 20u · 69% · miss[struct, blockify] · P0 0 | 18u · **78%** · miss[] · P0 0 | **44u** · 91%* · miss[headline_gains] · P0↑ (over-split) |
| eval_ai_agents | 33u · 76% · P0 0 | 33u · **82%** · P0 1 | 37u · **58%** ↓ · miss 1→3 · P0 1 |
| agent_memory_zh | 14u · 73% · P0 0 | 25u · **77%** · P0 0 | 18u · **63%** ↓ · dropped 6 defs · miss 0→4 |

(*rag 91% is inflated: 44 units blanket the article. The reviewer judged ~25–30%
of eval's units low-value/over-split; rag split the "knows nothing about: …"
3-bullet list into 3 separate units.)

### Why v6 regressed
- The strict "**definition quote must BE the full defining sentence, else emit an
  assertion**" rule made the model **drop** definition units it couldn't fully
  quote → eval and zh coverage crashed (eval 82→58%, zh 25→18 units, 77→63%),
  with required spans going missing again.
- The strict "**text ⊆ quote**" rule, combined with finer splitting, pushed the
  model to emit many fragment-quote units whose `text` completes the sentence
  from context → MORE `text`-beyond-quote flags, not fewer, plus rag over-split
  to 44 units. (The P0 *count* is partly reviewer-strictness on coreference; the
  coverage crash and over-split are robust, instruction-independent facts.)
- One genuine win: rag's `blockify_ideablock` went partial→covered (u-000 now
  quotes the "Blockify … IdeaBlock" defining sentence). But it cost the rest.

## Conclusion — failure-class D confirmed; next step is PROTOCOL (C), not prompt

Across v4 (under-extract) → v5 (balanced, 1 P0) → v6 (over-correct), tuning ONE
axis in a single-pass prompt regresses another: coverage ↔ faithfulness ↔
conciseness trade off and the prompt is **not converging** to all-gates-pass.
This is failure-class **D**, and it now points to **C — the single-pass
Unit-only protocol is the limiter**, not the wording.

**Recommendation (operator's call — do NOT keep tuning the prompt, do NOT enter
M14b):**
- **Keep v5 as the baseline** (coverage 78/82/77, required-missing ≈ 0, no bloat,
  1 isolated definition-fabrication P0). It is the best single-pass result.
- **Break the oscillation with a two-pass PROTOCOL** (this is a protocol/harness
  change, deliberately out of M14a.7's "prompt-only" scope — propose as M14a.8):
  1. **Pass 1 — faithful extraction**: the strict `text ⊆ quote` discipline, no
     coverage pressure (high precision, may under-cover).
  2. **Pass 2 — coverage closer**: given Pass-1 units + the source, identify
     *uncovered* coined terms / central claims and extract ONLY those, each with a
     full-sentence verbatim quote. Definitions handled by a dedicated step so the
     coverage objective never contaminates the faithfulness objective.
  This separates the two objectives that fight in one prompt. Still Unit-only,
  still grounded, no fuzzy accept, no validator change.
- Alternatively, accept v5 and push the residual P0 to a review/critic layer
  (the M14a.5 faithfulness reviewer already catches it).

SkillOpt remains premature and, when run, must optimize the JOINT gate — M14a.6/.7
are direct evidence that optimizing coverage or faithfulness alone regresses the
other.

## Gate verdict: NOT passed (and v6 worse than v5)

P0 ≠ 0, eval/zh coverage < 80% in v6, over-extraction on rag. Do NOT enter M14b.
Committed prompt reverted to v5. v6 cassettes/output live under `.run/m14.7/` /
`.run/m14a.7/` (not committed) for reference.
