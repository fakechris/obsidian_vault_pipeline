# Stage M14a.8 — critic-assisted bounded repair

> **Status: PASSES the stability bar — first iteration to fix the v5 faithfulness
> P0 with ZERO axis regression and NO padding. Eligible to enter ReferentCandidate
> with two tracked follow-ups (not blockers).** A protocol change, not another
> prompt tune: freeze the v5 generator, add ONE independent critic pass that TRIMs
> over-asserting text to a verbatim substring of its own grounded quote and ADDs
> grounded units for missed central points, then re-validate once. Grounding /
> accept rules / `accepted_without_quote = 0` untouched.

## 1. Root cause of the v4→v5→v6 oscillation — and a refuted fix

A single generation step was asked to jointly satisfy four objectives that trade
off — grounded, faithful, covered, concise. Tuning one regressed another: v4
(faithful, under-covered) → v5 (coverage up, +1 faithfulness P0 at eval u-001) →
v6 (hard `text ⊆ quote` → coverage crashed, units bloated to 44, P0 not zeroed).

**Key empirical finding that killed the design's first instinct.** Every
candidate (incl. the design workflow's top pick) proposed a *deterministic*
`text ⊆ quote` faithfulness gate. Measured on the 76 real v5 accepted units it is
**dead on arrival**: 97% (74/76) of *faithful* unit texts are NOT literal
substrings of their quote (a `text` is a paraphrase — pronoun resolution,
compression), and a token-subset variant flags 91–93%. The one real P0 (eval
u-001) has 7 unsupported tokens while many *faithful* CJK compressions have 20–39
— **no threshold separates them.** Faithfulness here is irreducibly semantic; it
cannot be gated deterministically from `text` vs `quote`. So the faithfulness
mechanism must be an LLM judge, and the only *sound deterministic* part is
re-grounding (quote ∈ source) + making a repaired text a verbatim substring of an
already-grounded quote.

## 2. The protocol (`crates/ovp-domain/src/units/critic.rs`, `prompts/unit_critic.md`)

`run_unit_extraction_repaired` = **frozen v5 base** (replayed from its committed
cassette → deterministic) + ONE independent critic call (`unit_critic/v1`,
live/record) over the same rendered span view, then bounded repairs re-validated
by the UNCHANGED validator exactly once:

- **TRIM** (faithfulness): for a unit whose `text` over-asserts, rewrite `text` to
  a verbatim contiguous substring of that unit's OWN grounded `quote`
  (`deterministic_contains(quote, suggestion)`; fall back to the whole quote if
  the critic's suggestion is not a substring). Quote / ref / location are never
  touched ⇒ **faithful by construction, coverage unmovable by a trim.** Critic
  over-flagging costs at worst readability, never correctness.
- **ADD** (coverage): for a central point no unit covers, append a candidate whose
  quote the validator re-grounds; a non-verbatim add is **dropped, never
  accepted** (no fuzzy accept, invariant intact).

**Conservative floor:** empty/malformed critic reply ⇒ merged == base ⇒ extraction
byte-identical to frozen v5. The protocol can never score below the committed
baseline — the property v4→v5→v6 lacked. Verified: rag base replay = 18 accepted
(= v5); eval replay is byte-identical to the live record (deterministic); 10/10
critic unit tests pass incl. the floor + fabricated-add-dropped cases.

## 3. v4 / v5 / v6 / new — comparison (Tier-1 deterministic scorer, 3 cases)

Metric = M14a.5 `central_span_recall(adjacent)` / `required_recall` / accepted
unit count, plus the faithfulness P0 count (Tier-2) and the hard invariants.

| case | v4 (m14.4) | v5 (m14.6) | v6 (m14.7) | **new (m14.8)** |
|---|---|---|---|---|
| rag_wrong | 88% / 83% / 20u | 81% / 75% / 18u | 100% / 100% / **44u** | **88% / 83% / 21u** |
| eval_ai_agents | 79% / 86% / 33u | 79% / 86% / 33u | 68% / 71% / 37u | **79% / 86% / 36u** |
| agent_memory_zh | 53% / 58% / 14u | 80% / 83% / 25u | 53% / 50% / 18u | **80% / 83% / 27u** |
| **P0 (accepted)** | 0 | **1** (eval u-001) | multiple | **0** |
| accepted_without_quote | 0 | 0 | 0 | **0** |

(v6's rag 100% is the padding tell — 44 units — while it regressed eval and zh.
new lifts rag coverage *and* holds eval/zh with +3/+3/+2 units, no padding.)

Required-missing: rag **3→2** (v5→new; gained `less_data_more_accuracy`); eval and
zh held. quote_found on accepted: rag 95.5%, eval 97.3%, zh 93.1% (≈ v5). parse
errors: none. near_match/fuzzy-accepted: 0.

## 4. Tier-2 semantic review (independent Claude judge + adversarial P0 verify)

Authoritative faithfulness/coverage adjudication, independent of the MiniMax
extractor (breaks circularity); every claimed P0 re-checked by a second skeptic
defaulting to not-a-P0.

- **0 new confirmed P0** across all 3 cases (rag/zh structured-verified = 0; eval
  independently confirmed: u-001 `text` now == its quote; the accepted set
  contains no attribution-style over-assertion).
- **eval u-001 P0 fixed.** 29 trims total (rag 13, eval 15, zh 1) — each a real
  v5 text-over-reach (e.g. eval u-015 packed three claims beyond its one quote;
  rag u-007 spliced four spans) reduced to its grounded quote. All 29 verified
  substring-of-quote; all 8 accepted adds verified text == verbatim quote.
- **Coverage:** up on rag (0.812→0.875, +`less_data_more_accuracy`), **flat** on
  eval (0.789) and zh (0.80). The eval/zh adds landed as verbatim-grounded
  accepted units but cover points the hold-out gold does not enumerate → 0 *gold*
  coverage gained there. Honest read: the eval/zh win is **faithfulness-only**.
- **Zero gold spans lost** on any case (trims never touch a quote/location).

## 5. Honest caveats

1. Coverage improved **only on rag** (1/3). eval/zh coverage is flat — their adds
   are grounded and central by the critic's judgment but off the gold checklist,
   so they don't move the measured metric (defensible, not padding, but not a
   coverage win).
2. The critic misses **attribution-style P0s**: eval u-033 (`"Hamel Husain
   describes…"`, no "Hamel" in source) was not trimmed. It is held at
   `needs_review` by the validator (so NOT an accepted-unit P0), but the critic
   prompt should target attribution/modality over-assertion — TRIM would fix it
   for free.
3. Two rag required spans (`chunk_structurally_neutral`, `structural_match`) stay
   uncovered: the source states them across a non-contiguous bulleted list, so no
   single-span verbatim quote exists. The critic correctly OMITs rather than
   splices — a source-structure coverage ceiling inherited from v5, not a
   regression.
4. Evidence is Tier-1 deterministic + one Tier-2 pass on **N=3** with a single
   live critic recording per case. The floor guarantees no sub-v5 score;
   generalization beyond these 3 is unproven. Cost ≈ 2× LLM calls/run.

## 6. The five questions

1. **What protocol & why?** Critic-assisted bounded repair (freeze v5 + an
   independent TRIM/ADD audit, re-validated once). Chosen because faithfulness is
   not deterministically gateable and prompt-tuning oscillates; an audit pass with
   a hard conservative floor can only raise the score or leave it untouched.
2. **Tradeoffs vs two-pass / plan-first / critic-repair / schema / accept-v5.**
   Two-pass/plan-first generate freely in a second pass (higher coverage ceiling
   but no floor; relocate the fabrication incentive). A deterministic gate /
   schema field was refuted (97% false-positive). accept-v5 is the floor but never
   fixes the generator. Critic-repair takes the floor *and* fixes dirty cases
   in-process; cost is a 2nd LLM call + an over/under-flagging critic (over =
   terser text only; under = caught by the validator gate).
3. **Better, worse, or a different failure?** **Better, and a regression on no
   axis.** Faithfulness improved on all 3 (P0 1→0, 29 over-reaches trimmed);
   coverage up on rag, flat on eval/zh; no gold lost; no padding. This is the
   first iteration since v4 that did not trade one failure for another.
4. **Enter ReferentCandidate?** **Yes — eligible**, with the two follow-ups below
   tracked into M14b (not blockers): it clears the bar (strictly more stable than
   v5, 0 new P0, accepted_without_quote=0, v5 P0 fixed, required-missing reduced or
   held, floor proven). The honest asterisk: the eval/zh improvement is
   faithfulness-only, so this is "Unit layer is stable enough to build referents
   on," not "coverage is solved."
5. **Next lever if not perfect: prompt / schema / protocol / gold / model?**
   **Critic prompt first** (add attribution/modality over-assertion to the
   faithfulness-defect rule — fixes the one near-miss for free), **then gold**
   (audit whether the off-gold eval/zh adds are genuinely central; if so, expand
   the checklist so real adds are credited and future padding is exposable). NOT
   the generator prompt (correctly frozen), NOT schema, NOT model.

## 7. Verdict

M14a.8 is the first protocol to **fix the v5 faithfulness P0 while regressing no
axis and adding no padding**, with a proven conservative floor and grounding
untouched. Unit layer is stable enough to enter ReferentCandidate; carry the
critic-attribution and gold-credit follow-ups into M14b. Still NO SkillOpt, NO
KnowledgeMEM, NO RAG, NO fuzzy accept, validator accept rules unchanged.

## Artifacts
- Committed: `crates/ovp-domain/src/units/critic.rs`, `prompts/unit_critic.md`,
  `harness.rs` (`run_unit_extraction_repaired`), `mod.rs`, `extract_units.rs`
  (`--repair`), `main.rs`, this doc.
- NOT committed (`.run/m14.8/`, `.run/m14/cassettes/unit_critic/`): repaired
  output, packs, critic cassette, scorer reports.
