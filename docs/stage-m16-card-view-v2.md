# Stage M16 — Reader Surface: Card Synthesis v2

> **Registered verdict (frozen thresholds, not moved): H1 = FAIL — a near-miss.**
> One frozen prompt change (`card_synth/v1`→`v2`) moved blind readability from a
> **0-of-11 wins blowout (v1) to 5-of-12 wins (v2)** — near parity — confirming the
> M15 diagnosis that readability is a fixable **presentation** problem, NOT the
> truth layer or a missing Referent. It fell **one case short** of the inconclusive
> band (KMEM 7/12; needed ≤6) and traded a small amount of faithfulness for
> punchiness. Coverage stayed a decisive OVP win. The architecture decision is
> unchanged and reinforced: keep truth-layer + card-view; Referent stays demoted.

## Scope (exactly as the operator specced)

Only the **presentation compiler** changed. New frozen `card_synth/v2`
(`docs/m16/card-synthesis-prompt.v2.md`): atomic cards (one claim each),
**thesis-sentence titles** that carry the takeaway, takeaway-first concise bodies
with concrete source specifics, citations kept in a footer field (not inline).
**Unchanged:** Unit extraction, critic-repair, grounding, Referent (not touched, not
added). The v2 cards were synthesized over the **same M15 repaired
`units.accepted.json`** (no re-extraction); judged against the **same M15 KMEM
memories** (KMEM never re-run). Split-blind judging reused (the blind readability
judge sees only title+content — so v1's loss was never about citation noise).

Design input: the M15 blind judges' own reasons (KMEM won every case on the SAME
axes — thesis-style titles, atomicity, concrete searchable detail, signal density).
v2 was written to that *pattern*, not patched per-article. 12/12 cases scored
(s06 re-synthesized after one transient failure; 0 cards dropped for missing
citations on any case).

## Results (12 cases; 73 KMEM memories vs 87 OVP v2 cards; 127 central points)

| axis | KMEM | OVP v2 | v1→v2 move | frozen verdict |
|---|---|---|---|---|
| Readability (blind pairwise) | **7 wins** | 5 wins | KMEM **11-0 → 7-5** | **FAIL** (7 ≥ fail-7; one short of inconclusive) |
| Faithfulness supported-rate | 0.932 | 0.908 | OVP 0.919 → 0.908 | **FAIL** (ABC: OVP < KMEM) |
| Faithfulness bad-rate (unsup+oversynth) | 0.041 | 0.046 | OVP 0.014 → 0.046 | hard floor ≤5% **OK** |
| Coverage of central points | 0.646 | **0.811** | OVP 0.878 → 0.811 | **PASS** (−6.7pts vs v1, +16.5 vs KMEM) |
| attribution-wrong | 0 | 0 | — | OK |
| **H1** | | | | **FAIL** |

OVP **flipped 5 cases** it lost in v1 (s01, s02, s05, s08, s10 — all OVP wins are
flips, since v1 was 0-11). The 7 KMEM wins are the remaining gap.

## Honest reading

- **The lever is confirmed.** Readability is a presentation-compiler problem: a
  single frozen prompt edit (thesis titles + atomicity + concrete specifics) took
  OVP from "loses every case" to "wins 5/12, near parity." This is the strongest
  possible evidence that the M15 readability fail was NOT the truth layer and NOT a
  missing Referent. The architecture (Grounded Units = truth, cards = view) is right.
- **It missed the bar by one case.** KMEM 7 / OVP 5 lands in the frozen FAIL band
  (≥7); ≤6 would have been inconclusive, ≤4 a pass. Per the no-moving rule the
  threshold stands — M16 is a registered FAIL, honestly a near-miss.
- **A real, small faithfulness cost.** v2's punchier/atomic style nudged bad-rate
  0.014→0.046 (still under the 5% floor) and supported-rate 0.919→0.908 — enough to
  drop just below KMEM (0.932) and fail the ABC composite. The crisper a card's
  thesis title/takeaway, the easier it is to harden a hedged source claim. This is
  the genuine trade the v2 format introduced and must be recovered, not ignored.
- **Coverage and provenance are unaffected wins.** OVP still covers far more
  central points (0.811 vs 0.646) and is the only arm with claim→source provenance.

## Decision

Unchanged from M15 and reinforced: **keep the truth-layer + card-view simplified
trunk; Referent/Resolver stays demoted to an optional helper.** Coverage,
provenance, and H2 all point the same way; M16 only sharpens *where* the remaining
work is — the card prose, and now specifically the readability⇄faithfulness balance.

**Next (a future bounded step — NOT done here, to avoid tuning v2 against these
results):** a single frozen `card_synth/v3` that keeps v2's readability gains while
restoring faithfulness — i.e. thesis titles that state the claim *at the source's
modality* (don't harden "potentially dozens" into "requires dozens"), atomic bodies
that stay tight to the cited quote. Then one fresh registered run. The remaining
readability gap (7→≤6) and the faithfulness dip (0.908→back above KMEM) are plausibly
the same fix: titles faithful to the quote read as confident *and* stay supported.

## Honest caveats

- Frozen thresholds applied as-is; M16 is a near-miss FAIL, reported as such.
- N=12, single live recording per arm; judge variance is real (the faithfulness
  margins — OVP 0.908 vs KMEM 0.932 — are within plausible judge noise, but the
  *direction* moved against OVP vs M15, which is consistent with the punchier style).
- KMEM content was re-fetched full (likely flatters KMEM on readability/coverage),
  so OVP's 5 readability wins and coverage win are conservative.
- The M15 vs M16 case sets differ by one (M15 dropped s03 to a judge failure; M16
  scored all 12), so the v1↔v2 deltas are directional, not paired-exact.

## Artifacts
- Committed: `docs/m16/card-synthesis-prompt.v2.md` (frozen), this doc,
  `scripts/m16_synth.sh`, `scripts/m16_prep_judge.py`. Raw arms + judging under
  `.run/m16/` (gitignored; no KMEM dumps committed). v1/M15 unchanged.
