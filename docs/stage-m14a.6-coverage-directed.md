# Stage M14a.6 — Coverage-Directed Unit Extraction

> **Status: coverage improved, but gate NOT passed — do NOT enter M14b.** A
> prompt-only iteration (v5, `unit_extract/v5`) added coverage discipline
> (definition units for coined terms + the article's thesis/insight spine) while
> holding the M14a.4 grounding rules. Coverage rose and required-MISSING dropped
> with no unit bloat — but it introduced **1 faithfulness P0** (a fabricated
> definition) and coverage is still ~77–82% (only eval clears 80%), with many
> required points now *partial* rather than fully covered.

## What changed (prompt-only; no schema / validator / accept-rule change)

`prompts/unit_extraction.md` v4 → v5 adds a **Coverage** section: emit a
`definition` unit for every term the article introduces/coins (anchored to the
defining sentence, verbatim quote required); capture the problem diagnosis, core
thesis, key insight/reversal, main method, limitations, recommendations; and an
explicit rule that **coverage never overrides grounding** (omit a point with no
copyable quote rather than fabricate/splice). Reframed the old "fewer units" line
that was driving under-extraction. Re-recorded all 3 cases live.

## M14a.5 (v4) → M14a.6 (v5)

| case | units | semantic coverage | required missing | P0 (faithful=no) |
|---|---|---|---|---|
| rag_wrong | 20 → **18** | 69% → **78%** | [structural_match, blockify_ideablock] → **[]** | 0 → 0 |
| eval_ai_agents | 33 → **33** | 76% → **82%** | [learn_from_production] → [eval_methods_unchanged] | 0 → **1** |
| agent_memory_zh | 14 → **25** | 73% → **77%** | [openclaw_extract_retrieve] → **[]** | 0 → 0 |

(Coverage = independent-Claude semantic judge, advisory; `quote_found` stayed
high — rag 94.7 / eval 97.1 / zh 92.6% — and `accepted_without_quote=0` on all 3;
the few coverage-reach units with non-verbatim/spliced quotes were correctly
**rejected**, not accepted.)

### Real progress
- **required-MISSING eliminated on rag + zh** (the previously-absent
  `structural_match`, `blockify_ideablock`, `openclaw_extract_retrieve` are now
  covered or partial). Most "missing → partial → covered" moves are genuine.
- **zh 14 → 25 is real coverage, not padding**: the new units are `definition`s
  for the three Google memory types, OpenClaw's three file types, EverOS's six
  subtypes, plus OpenClaw extract/retrieve — exactly the gaps M14a.5 flagged.
- **No bloat**: rag 20→18, eval 33→33. Counts didn't inflate to game coverage.
- Grounding held: 0 accepted-without-quote; coverage-reach splices rejected.

### What still fails the gate
1. **1 faithfulness P0 (eval u-001)** — the definition push made the model write
   a definitional clause in `text` ("Benchmark maxxing is optimizing for …
   performance metrics and test scores") that is **not in its quote** ("Benchmark
   maxxing is for augmenting experts.") nor the source. Quote-grounded but
   text-unfaithful. Gate requires P0 = 0.
2. **Coverage ~77–82%** — eval 82% clears ≥80%; rag 78% and zh 77% do not.
3. **Many required points only PARTIAL**, not covered — e.g. `blockify_ideablock`
   defines the IdeaBlock structure but omits "Blockify, a preprocessing layer
   from Iternal Technologies"; `chunk_structurally_neutral` regressed
   covered→partial (the v5 unit dropped the topic sentence).

## Gate verdict

| gate | target | result |
|---|---|---|
| accepted_without_quote | 0 | ✅ 0 |
| quote_found | =100% / ≥M14a.4 | ⚠ 92.6–97.1% (dipped; non-verbatim reach-units rejected, accepted-grounding still 100%) |
| faithfulness | ≥90% | ⚠ ~98% but **1 P0** |
| P0 attribution/modality/faithfulness | 0 | ❌ **1** (eval definition fabrication) |
| semantic central coverage | ≥80% (ideal ≥90%) | ❌ rag 78%, zh 77% (eval 82%) |
| required not systematically missed | — | ✅ missing→0 on rag/zh, but several now *partial* |
| no unit bloat | — | ✅ |

**Verdict: do NOT enter M14b.** Coverage improved materially, but the gate is not
met (1 P0 + borderline coverage + partial-coverage of required points).

## Failure classification

Primarily **D — the model does not yet balance coverage with faithful text**:
forced to produce definitions, it (a) fabricated one definitional clause in
`text` beyond the quote (the P0), and (b) frequently quotes ONE fragment when the
full point needs the defining sentence (the many *partials*: IdeaBlock without
Blockify/Iternal; chunk without its topic sentence). Secondary **A** — coverage
discipline improved but doesn't yet force the model to quote the article's actual
*defining* sentence. NOT B (gold validated, 0 invalid anchors) and NOT C (the
Unit-only schema was sufficient to lift coverage; no planning field needed).

## Recommended next step — M14a.7 (one more bounded prompt fix), still NOT M14b

Hold every M14a.4 grounding gate AND re-assert faithfulness, targeting D+A:
1. **Re-harden `text` ≤ `quote`**: `text` must not assert anything its
   `evidence_quote` does not contain — for a definition, the defining content must
   be IN the quote, not synthesized in `text`. (Directly kills the
   benchmark-maxxing P0.)
2. **Definitions must quote the article's actual defining sentence** (use the
   two-adjacent-span / paragraph-ref allowance to include the full definition,
   e.g. the "Blockify … implements this as … IdeaBlock" sentence), so definitions
   are complete, not partial.
3. Re-run M14a.5; require P0 = 0, faithfulness ≥90%, semantic coverage ≥80%
   (≥90% ideal), required not systematically partial.

SkillOpt remains premature; when it runs its objective must be the JOINT gate
(grounding + coverage + faithfulness), never coverage or quote_found alone — M14a.6
shows coverage and faithfulness can trade off, so both must be optimized together.

## Artifacts
- Committed: `prompts/unit_extraction.md` (v5), `src/units/prompt.rs` (v5 id +
  test), this doc. Gold + scorers unchanged from M14a.5.
- NOT committed (`.run/m14.6/`, `.run/m14a.6/`): v5 extract output, cassettes,
  coverage/review packs, advisory `reviews.json`.
