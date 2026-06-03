# Stage M15 — Methodology Root-Cause Audit (DECISION RECORD)

> **Status: DECISION + pre-registered protocol. Zero implementation in this doc.**
> Supersedes the planned `M14b → M14c ReferentResolver`. The Source→Unit grounding
> layer is kept and frozen; the eager Referent/Concept ontology is paused; before
> any new layer we run a held-out, pre-registered comparison against KnowledgeMEM
> at the FINAL-PRODUCT level. Nothing here is built until this protocol is agreed.

## Why we are stopping (the two-layer diagnosis)

v4→v5→v6→M14a.8→M14b kept oscillating not because the model is weak (the M14a.4
copy-probe proved verbatim copy; KnowledgeMEM uses the same MiniMax and produces
useful memories) and not because grounding is wrong. It oscillated because **the
main path tried to do TRUTH and PRESENTATION and a fine semantic ontology all at
once**, and because faithfulness and concept-vs-claim are **irreducibly semantic**
— no deterministic gate separates them (verified twice: M14a.7, M14b).

The sharpest framing, confirmed against the real recovered KnowledgeMEM code/runs:

> **KnowledgeMEM puts complexity AFTER memory** (Source→Memory; Entity/KG/Crystal
> are post-processing/backfill; `Concept` is just `EntityNode(entity_type=concept)`;
> `Crystal` is just `MemoryNode(is_crystal=true)`). **OVP put complexity BEFORE and
> DURING memory** (Source→Unit→Referent→Resolver→Concept on the main path). That is
> why KnowledgeMEM converges and we thrash.

But the convergence has a price that must be recorded, not admired:

> **KnowledgeMEM trades verifiable grounding for a short main path and readable
> memory output.** `MemoryNode` has NO quote/evidence/source-span field — its
> memories are self-contained *paraphrases*, not source-grounded statements. It
> does not solve our quote-grounding problem; it **side-steps** it, and in exchange
> gives up the type-level no-hallucination guarantee and accumulates silent drift
> risk over long-term auto-maintenance. **OVP must NOT discard grounding; OVP
> should use grounding as the truth layer and COMPILE readable cards from it.**

## The five decisions

- **D1 — Freeze M14c / ReferentResolver.** Do not build the canonicalizing
  resolver or promotion stage now. Do not keep tuning `Issues/Signals/Agents/
  end-to-end-evaluation` on the three articles.
- **D2 — Grounding is OVP's core differentiator; keep it.** `accepted_without_quote
  = 0` / `referents_ungrounded = 0` are deterministic, article-independent,
  type-level properties — the one thing that converged and the one thing
  KnowledgeMEM lacks. Frozen, not iterated.
- **D3 — Split the truth layer from the view layer.**
  - **Truth layer = grounded Units** (M14a.8 repaired): verifiable, quote-anchored,
    non-hallucinating, auditable, coarse-typed. NOT optimized for readability.
  - **View layer = memory cards / primary note**: readable, reusable,
    queryable, navigable — COMPILED from the truth layer, every factual sentence
    citing back to units→quotes.
  The oscillation came from demanding truth AND presentation in one layer.
- **D4 — Compare FINAL views, not intermediate ontology.** The success criterion is
  the end product a human/consumer sees, judged on faithfulness + usefulness — not
  `quote_found` / `concept_rate` / `ambiguous_rate` (those drop to diagnostics).
- **D5 — Evaluation is held-out and pre-registered.** 10–15 random/held-out
  articles (not the tuned 3), metrics and success thresholds fixed BEFORE running,
  judged blind by independent judges. No goalpost-moving, no per-article tuning.

## What is kept, frozen, paused

| | |
|---|---|
| **Keep** | Source→Unit extraction; coarse types (`assertion/directive/relation/question`, subtype `fact/procedure/learning/decision/...`); critic-assisted repair (TRIM/ADD); the grounding validator + invariants. |
| **Keep (KnowledgeMEM-aligned)** | A coarse `unit_type` is fine and useful — KnowledgeMEM has one too (8 values). Coarse typing at extraction is NOT the problem. |
| **Pause (off the main path)** | The fine ontology: `entity/concept/ambiguous/local_phrase/noise`, `boundary`, concept-vs-claim, `promotion_candidate`, `fold_into_parent`, canonical merge. May exist later as OPTIONAL review/suggestion output, but must NOT block ingestion. |
| **Stop** | M14c, ReferentResolver, canonical promotion as a pipeline stage. |

## Hypotheses (pre-registered)

- **H1:** OVP grounded Units → *cited* memory cards can MATCH KnowledgeMEM's
  readability while OUTPERFORMING it on faithfulness / source-provenance.
- **H2:** A Referent/Resolver stage is NOT needed in the ingestion main path —
  *unless* the simplified Unit→card baseline measurably fails a downstream use
  case (search / navigation / maintenance).

## M15 experiment design

### Fair baseline (do NOT compare raw Units to KnowledgeMEM memories)

```
KnowledgeMEM:    Source ─────────────────────────────→ memories
OVP (simplified): Source → repaired grounded Units → memory cards (with unit citations)
```

The OVP arm MUST include a real **card-synthesis pass** (group/gloss units into
5–8 cards). Comparing KnowledgeMEM's finished memories against our raw intermediate
units would be unfair and is forbidden.

### OVP card-synthesis hard constraints

- Card `content` MAY paraphrase for readability.
- BUT every factual sentence MUST cite the `unit_id`(s) it derives from, and every
  cited unit MUST trace to a verbatim quote/span (the truth layer).
- **No new facts** beyond the cited units. A card asserting something no cited unit
  supports is a failure, not a feature.

### Faithfulness oracle (applied SYMMETRICALLY to both systems' outputs)

NOT a `content` substring match (unfair to a paraphrase system). For each output
item (a KnowledgeMEM memory OR an OVP card sentence):

1. Retrieve candidate source support span(s) via the OVP span map + quote retrieval.
2. Judge (LLM and/or human) whether the item is **supported / entailed** by those
   spans.
3. Label: `supported` · `partially_supported` · `unsupported` · `over_synthesized`
   · `attribution_or_modality_wrong`.

This makes "OVP grounding" the measuring instrument for BOTH arms — its intended
diagnostic use ("source is truth; can each memory be found in the source?").

### Pre-registered metrics (primary)

- faithfulness (supported-rate; count of `unsupported` + `over_synthesized`)
- source support (can each item be located to span(s))
- coverage (central source points captured)
- readability / usefulness for search & query
- long-term maintenance risk (drift exposure without provenance)
- number of unsupported claims

NOT primary (diagnostics only): `concept_rate`, `ambiguous_rate`, `quote_found`,
unit/referent counts.

### Protocol

- Sample: 10–15 articles drawn at random / held out from the tuned 3.
- Pre-register metrics + success thresholds in this doc before any run.
- Blind, independent judging (judge does not know which arm produced an output).
- Both arms produced from the SAME articles with the SAME live model.

## Decision rule (what the result triggers)

- **If H1 + H2 hold** (cards match readability, beat faithfulness/provenance; no
  downstream loss without Referent): ship the **truth layer + card view** pipeline;
  demote the Referent ontology to an OPTIONAL review helper, not a main-path stage.
- **If the simplified baseline fails a downstream use case**: restore Referent in
  its MINIMAL form only — `important_objects[]`, `promotion_suggestions[]`,
  `do_not_promote[]` — never the full kind/boundary/promotion state machine.

## Explicitly NOT doing in M15

No ReferentResolver, no canonical promotion, no evergreen writes, no new ontology,
no prompt/gate tuning on the original 3 articles, no learning to hit the gold slugs.

## One-line thesis

**Do not copy KnowledgeMEM by abandoning grounding; copy it by SHORTENING the main
path. OVP's right shape is likely a grounded truth layer + a readable memory-card
view — not an ever-deeper ontology pipeline.**
