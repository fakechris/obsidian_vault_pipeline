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
- **D5 — Evaluation is held-out and pre-registered.** Made binding by the sections
  below: *Phase 0* gates the comparison, *Sampling plan* fixes the pool/seed/N,
  *Frozen prompt* bans tuning, *Stop / Pass–Fail criteria* fixes the executable
  thresholds, *Judging protocol* fixes the split-blind judging. No goalpost-moving,
  no per-article tuning, no post-hoc "explained" pass.

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

## Phase 0 — KnowledgeMEM evidence audit (GATE — must pass before any baseline run)

A read-only evidence report (`docs/m15/knowledgemem-evidence.md`) MUST be produced
and accepted BEFORE any comparison run. **No OVP-vs-KMEM conclusion may rest on
unlabeled inference.** Every claim is tagged `recovered_code | runtime_output |
runbook | inference`. It must answer:

- the actual source-extraction prompt shape (tool-driven `CreateMemory`, not a
  strict output schema);
- the `CreateMemory` / `CreateCrystal` / `CreateEVOLVES` tool schemas (fields,
  the `unit_type` enum);
- the actual `source-detail` memories for the sampled cases, verbatim (from the
  `…/nowledge/source-detail.json` runtime outputs);
- when entity / KG / crystal are created (extraction-time vs post-processing /
  backfill);
- whether `MemoryNode` carries any quote / evidence / span field (the grounding
  asymmetry — the load-bearing fact).

Gate: baseline runs are BLOCKED until Phase 0 is written and its
inference-vs-evidence labels are reviewed. The earlier one-line framing of
KnowledgeMEM in this doc is itself `inference` until Phase 0 replaces it.

## M15 experiment design

### Fair baseline (do NOT compare raw Units to KnowledgeMEM memories)

```
KnowledgeMEM:    Source ─────────────────────────────→ memories
OVP (simplified): Source → repaired grounded Units → memory cards (with unit citations)
```

The OVP arm MUST include a real **card-synthesis pass** (group/gloss units into
5–8 cards). Comparing KnowledgeMEM's finished memories against our raw intermediate
units would be unfair and is forbidden.

### Sampling plan (FIXED before any run — no "reasonably pick 12")

- **Candidate pool:** `/Users/chris/Documents/ovp-vault/50-Inbox/03-Processed/`
  (the operator's processed articles).
- **Draw:** order the pool by a STABLE key (full path, lexicographic); draw **N=12**
  (range 10–15) with a **fixed seed**, recorded in the run log. The seed + the
  resulting file list are written down BEFORE producing either arm.
- **Exclusions:** ONLY a file that cannot be read by BOTH arms (KMEM ingest fails
  AND/OR OVP source read fails). Each exclusion is logged with its reason and
  replaced by the next file in stable order (also logged). No other exclusions.
- **The tuned 3** (`rag_wrong`, `eval_ai_agents`, `agent_memory_zh`) are EXCLUDED
  from the primary sample; they may appear only as labelled calibration examples,
  never counted in the primary metrics.

### Frozen prompt / no-tuning rule

- M15 introduces exactly ONE OVP card-synthesis prompt. Its full text + a version
  id are recorded (in `prompts/` or this doc) BEFORE the evaluation run.
- **NO tuning against M15 outputs. NO per-article prompt edits.** If the cards come
  out worse than KMEM, that is a RESULT, not a trigger to edit the prompt.
- Only parse/transport failures may be fixed; any such fix forces a clean re-run,
  noted in the run log. The KMEM arm is likewise run as-configured (no edits to
  make it look better or worse).

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

### Judging protocol (split, to avoid pseudo-blinding)

OVP cards carry unit citations and KMEM memories do not, so a single judge would
trivially identify the arm. So the two judge roles are SEPARATE:

- **Subjective axes (readability, usefulness for search/query): BLIND on a STRIPPED
  view.** Strip citations / provenance / formatting tells from BOTH arms, present
  items in randomized order; the judge does not know the arm. Independent judge(s),
  not the author.
- **Faithfulness / source-support: UNBLINDED, by a SEPARATE auditor.** This axis
  needs the citations + span evidence to run the support/entailment oracle, so it
  is run by a different judge (or human) than the readability judge — keeping the
  subjective scores uncontaminated by seeing provenance.

### Models / config (record; do NOT force-equalize)

Record the actual model + config for BOTH arms. OVP uses the configured MiniMax
(`.env.live`). KMEM uses its running service's own agent/prompt/model config UNLESS
that is controllable. Do NOT edit KMEM to match OVP's model — if they cannot be put
on the same model, record the difference as a stated **confounder** in the results.

## Stop / Pass–Fail criteria (PRE-REGISTERED thresholds)

Registered BEFORE any run. Changeable ONLY before the first run, with the change
logged here. After the run the result is read against these AS-IS — no new
thresholds, no re-interpretation, no "explained" pass. Definitions: `supported-rate`
= items labelled `supported` ÷ all factual items (per arm); subjective axes scored
by the blind stripped-view judge as **pairwise** win/loss/tie per article.

**Faithfulness** (the axis OVP must win — keeping grounding is the whole point):
- PASS needs BOTH: OVP `(unsupported + over_synthesized)` rate ≤ KMEM's, AND OVP
  supported-rate ≥ KMEM's + 10 pts (or OVP ≥ 90% while KMEM < 90%).
- **Hard floor:** OVP `(unsupported + over_synthesized)` ≤ **5%** of OVP card
  factual sentences (the synthesis pass must not leak un-cited facts). Breaching
  this FAILS H1 regardless of KMEM.
- OVP `attribution_or_modality_wrong` ≤ KMEM's.

**Readability / usefulness** (OVP must be no worse than KMEM beyond a margin):
- PASS: KMEM wins the blind pairwise readability comparison on ≤ **1/3** of sampled
  articles. A KMEM blow-out (> **1/2**) FAILS H1 even if OVP wins faithfulness.

**Coverage** (comparable within margin):
- PASS: OVP central-point coverage ≥ KMEM coverage − **10 pts**.

**H1 verdict:** HOLDS iff faithfulness PASS AND readability PASS AND coverage PASS.
OVP winning faithfulness but losing readability beyond the margin ⇒ H1 **FAILS**
(record it honestly; do not relax the margin after the fact).

**H2 verdict** (on a fixed set of downstream probe tasks — search/query + navigation,
listed before the run):
- HOLDS iff OVP cards WITHOUT Referent are comparable to KMEM (within the
  readability/usefulness margin) on those tasks.
- FAILS iff specific object/navigation tasks fail BECAUSE object structure is
  missing (not merely readability). Then Referent returns ONLY in minimal form
  (`important_objects[] / promotion_suggestions[] / do_not_promote[]`).

## Decision rule (what the verdicts trigger)

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
