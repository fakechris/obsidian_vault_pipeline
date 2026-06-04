# Stage M16.1 — Card Synthesis v3 (modality-preserving) — LAST prompt iteration

> **Verdict: faithfulness RECOVERED to an OVP win, coverage held, readability held
> at near-parity (KMEM 7 / OVP 5) — but still one case short of the bar. Frozen H1 =
> FAIL.** v2 and v3 landed at the IDENTICAL 7-5 readability split, so the
> prompt-language ceiling is reached: per the pre-agreed meta-rule, **card-prompt
> tuning STOPS here** — the residual one-case readability gap is a product/UI
> problem (render-time density, citation UX, expand/collapse), not language.

## Scope (presentation-only, as specced)

`card_synth/v2` → `card_synth/v3` (`docs/m16/card-synthesis-prompt.v3.md`): v2 plus a
**modality-fidelity policy** (titles punchy but at the source's modality; preserve
maybe/could/can; avoid requires/proves/causes/guarantees/must/eliminates unless the
source supports them; prefer "X can…" when hedged). Nothing else changed — Units,
critic-repair, grounding, Referent all untouched; synthesized over the **same M15
units**, judged vs the **same M15 KMEM arm**. Frozen before the run; not tuned after.

## Results (12 cases; 73 KMEM memories vs 90 OVP v3 cards; 125 central points)

| axis | KMEM | OVP v3 | vs v2 | frozen verdict |
|---|---|---|---|---|
| Faithfulness supported-rate | 0.918 | **0.944** | 0.908 → **0.944** (now > KMEM) | **PASS** (ABC route B) |
| Faithfulness bad-rate | 0.055 | **0.044** | 0.046 → 0.044 (< KMEM) | ≤5% floor ✓ |
| Coverage of central points | 0.640 | **0.808** | 0.811 → 0.808 | **PASS** (+16.8 vs KMEM) |
| Readability (blind pairwise) | **7 wins** | 5 wins | 7-5 → **7-5** (unchanged) | **FAIL** (needed ≤6) |
| attribution-wrong | 0 | 0 | — | OK |
| **H1** | | | | **FAIL** |

OVP readability wins: s01, s02, s04, s05, s08 (v3 gained s04, lost s10 vs v2 — churn
at the margin, same 5-7 split).

## What this establishes

1. **The faithfulness regression is fixed, the right way.** v2 traded faithfulness
   for punchiness (supported 0.919→0.908, below KMEM). v3's modality policy
   recovered it to an **OVP win** (0.944 > 0.918; bad-rate 0.044 < 0.055) with no
   coverage loss — confirming a card can be confident *and* modality-faithful. The
   v2→v3 delta is exactly the intended one.
2. **Readability has hit a prompt-language ceiling.** Two materially different
   prompt strategies (v2 punchy-max; v3 modality-preserving) produced the **same
   KMEM 7 / OVP 5** split. The gap is no longer about wording — it is the structural
   difference between KMEM's flat one-fact cards and OVP's grounded thematic+cited
   cards. OVP already wins 5/12 outright and is at near-parity; the last case is not
   a language problem.
3. **Architecture is reinforced again.** With v3, OVP now wins faithfulness AND
   coverage AND carries provenance KMEM structurally lacks — the truth-layer +
   card-view pipeline is the right trunk; Referent stays demoted.

## Decision (honoring the meta-rule)

**STOP card-prompt iteration.** v3 is the final card-synthesis prompt. The reader
surface is: faithfulness-winning, coverage-winning, provenance-carrying, readability
at near-parity (5/12 OVP wins, 7/12 KMEM, 0 short blowouts). The remaining
readability increment is a **product/UI problem**, to be addressed at the render
layer, NOT with a v4 prompt:
- card density / length budget at render time (KMEM's edge is terse one-fact cards);
- citation UX (Evidence footer placement / collapse) so provenance never taxes reading;
- progressive disclosure (title + takeaway visible, detail expand-on-demand);
- optionally an entity-density-gated object-index view (the demoted-Referent helper).

Do NOT reopen Unit extraction, critic-repair, Referent ontology, or the M15/M16
thresholds.

## Honest caveats

- Frozen thresholds applied as-is; H1 is a registered FAIL (readability 7/12).
- N=12, single live recording per arm, single-judge no-ties pairwise → the 7-5 split
  is high-variance ("within plausible judge noise"); the *stability* of 7-5 across
  v2 and v3 is the load-bearing signal, more than the exact number.
- Faithfulness margins (OVP 0.944 vs KMEM 0.918) are within judge noise — read as
  "at least as faithful, with provenance," a robust direction across M15/M16/M16.1.
- KMEM full-content re-fetch likely flatters KMEM on readability/coverage, so OVP's
  wins are conservative.

## Artifacts
- Committed: `docs/m16/card-synthesis-prompt.v3.md` (frozen), this doc,
  `scripts/m16_1_synth.sh`, `scripts/m16_prep_judge.py` (parameterized). Raw arms +
  judging under `.run/m16_1/` (gitignored; no KMEM dumps committed).
