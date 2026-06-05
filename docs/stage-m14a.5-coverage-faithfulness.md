# Stage M14a.5 — span-anchored faithfulness + coverage baseline

> **Status: gate run; result = DO NOT enter M14b yet.** M14a.4 proved the
> Source→Unit output is *grounded* (quote_found=100%). M14a.5 asks whether it's
> *useful*: are the units faithful, and do they cover the article's central
> points? **Faithfulness/attribution/modality are excellent (0 P0 across ~67
> units). Coverage is ~70-80% — below the ≥80% gate — with required misses
> (notably the article's coined concepts are referenced but never *defined*).**
> So the next step is a targeted prompt-COVERAGE iteration (M14a.6), NOT M14b /
> ReferentCandidate / SkillOpt.

## What this evaluates (and what it deliberately does NOT)

A new, **source-span-anchored** gold — `fixtures/unit_coverage/<case>/central_units.yml`
— independent of the concept-map slug benchmark. Each gold "central unit" is a
load-bearing point the article develops, anchored to a `quote_must_include`
verbatim phrase. Drafted by an independent reading of the **source** (not the
extraction output, to avoid circularity); every anchor verified to occur verbatim
in the source. NOT scored by label/slug/text equality.

Inputs: the M14a.4 outputs (`.run/m14.4/extract/<case>/units.accepted.json`),
the 3 fixtures' sources, and the gold. No re-record; offline.

## Two-tier scoring

**Tier 1 — deterministic (`scripts/m14a5_coverage.py`, committed, LLM-free).**
Coverage by SOURCE-SPAN OVERLAP: a gold point is covered if an accepted unit's
evidence location overlaps the gold anchor's paragraph (`strict`) or an adjacent
block (`adjacent` — articles split one point's topic sentence / bullets /
elaboration / images into separate blocks; verified on rag_wrong that the
strict-misses had a covering unit 79–100 bytes away in the next block). This is a
**lower bound**: it under-counts when the gold anchor sentence ≠ the unit's quoted
sentence and they aren't adjacent, and can over-count "in-region-but-not-the-point".

**Tier 2 — semantic, advisory (independent Claude judge; `.run/m14a.5/reviews.json`).**
For each gold point: is it *expressed* by a unit (covered/partial/missing)? For
each unit: faithful? attribution correct? modality correct? Independent of the
MiniMax extractor, so not self-confirming. Authoritative for the gate (the
specific misses it cites are concrete + human-verifiable in the review pack), but
LLM-advisory — confirm by hand in `unit-review-sheet.md`.

The two tiers **disagree instructively**: Tier-1 over-credited rag's
`blockify_ideablock` (units sit in that region) while Tier-2 caught that IdeaBlock
is *referenced but never defined*. Hence the gate rests on Tier-2 + faithfulness,
with Tier-1 as a floor.

## Results

| case | units | gold | det.strict | det.adj | semantic (c/p/m) | required missing | P0 |
|---|---|---|---|---|---|---|---|
| rag_wrong | 20 | 16 | 81% | 88% | **69%** (10/2/4) | structural_match, blockify_ideablock | 0 |
| eval_ai_agents | 33 | 19 | 74% | 79% | **76%** (13/3/3) | learn_from_production | 0 |
| agent_memory_zh | 14 | 15 | 47% | 53% | **73%** (10/2/3) | openclaw_extract_retrieve | 0 |

(c/p/m = covered / partial / missing, semantic ≈ (covered + 0.5·partial)/total.)

## Failure classification

The single failure class is **prompt under-extraction**, concentrated in:

1. **Definitions of coined concepts are skipped.** rag references "IdeaBlock"/
   "Blockify" in 4 units but *never defines* either — the model extracts facts
   ABOUT the concept, not the DEFINITION that introduces it. This is the most
   important gap.
2. **Central insight/thesis claims are skipped** in favour of supporting
   facts/results/steps. rag `structural_match` ("queries are already questions,
   so the match becomes structural, not just semantic") is absent; eval's
   `teach_agent_to_refuse` and `floor_raising_recommended` are only partial.
3. **A few method/observation points missed** (eval `learn_from_production`, zh
   `openclaw_extract_retrieve`; rag Stage 3 of the pipeline).

NOT observed: over-split (counts are reasonable), `Unit.text` paraphrase drift
(faithful=no: 0), attribution errors (=no: 0), modality errors (=no: 0). A few
minor `needs_review` (rag's vendor-benchmark numbers tagged `suggested` vs
`asserted`; eval u-005's Hamel framing) — not P0.

**agent_memory_zh (the v3→v4 14-unit question): reasonable compression, not
gutting.** 73% semantic coverage, one required miss (OpenClaw extract/retrieve)
and one partial (single-session compression). The 14 units carry the spine
(no-memory → compression → long-term system → Google taxonomy → two-questions
framework → OpenClaw → EverOS taxonomy/extract/update/retrieve/benchmark/skill).

## Gate verdict

| gate | target | result |
|---|---|---|
| sampled faithfulness | ≥90% | ✅ ~100% (0 faithful=no / ~67 units) |
| P0 attribution/modality | 0 | ✅ 0 |
| central_span_recall | ≥80% (ideal ≥90%) | ❌ ~69–76% semantic |
| required spans not systematically missed | — | ❌ 1–2 required missing per case |

**Verdict: do NOT enter M14b.** Grounding + faithfulness are excellent; coverage
is not yet at the bar. Per the M14a.5 rule, the next step is prompt coverage, not
Referent.

## Recommended next step — M14a.6 (prompt coverage iteration), NOT M14b

A single, targeted prompt iteration on `unit_extraction.md`, holding the M14a.4
hard gates (parse_error=0, accepted_without_quote=0, quote_found=100%,
deterministic replay) AND now gated on this M14a.5 coverage/faithfulness:

- **Require a DEFINITION unit for each concept the article introduces/coins** (a
  named system, product, or coined term) — not only facts about it.
- **Require the article's central thesis/insight claims**, not just the
  supporting results/steps.
- Re-record the 3 cases; re-run M14a.5; require semantic coverage ≥80% (≥90%
  ideal) and 0 new P0.

**SkillOpt-lite is still premature.** When it runs, its objective MUST be the
joint gate (hard: parse/quote/grounding; quality: central_span_recall +
faithfulness + attribution/modality) — never quote_found or fewer-units alone, or
it will learn to "extract less, extract easy sentences" and look better while
covering less. M14a.5 is the guard against exactly that.

## Artifacts

- Committed: `fixtures/unit_coverage/<case>/central_units.yml` (gold),
  `scripts/m14a5_coverage.py` (deterministic scorer), `scripts/m14a5_pack.py`
  (pack/summary assembler), this doc.
- NOT committed (operator output under `.run/m14a.5/`): per-case `REVIEW.md`,
  `coverage-report.{json,md}`, `unit-review-sheet.md`, `uncovered-spans.md`,
  `overcovered-spans.md`, `accepted-units.{json,md}`, `central-gold.json`,
  `reviews.json` (advisory), and `M14A5_SUMMARY.md`.
