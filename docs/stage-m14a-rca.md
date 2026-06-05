# Stage M14a.RCA — root cause of the v2 quote failures

> **Status: done (offline, no live calls).** Verdict: the M14a.1 quote failures
> are **overwhelmingly our pipeline (representation + segmentation + validator),
> not model capability.** A validator-only fix (faithful plain-text render),
> verified offline by replaying the recorded cassettes, recovered the English
> representation failures with **no re-record**. The residual is segmentation +
> the model compressing dense Chinese lists. Do NOT RLHF, do NOT change the
> model, do NOT kill Source → Unit.

## Why an RCA instead of more tuning

M14a.1 went 63.6% / 78.0% / 27.3% quote_found — below the gate. Before iterating
blind, classify *why* each quote failed. Decisive prior: the benchmark gold was
produced by the **same** model, so a model-capability ceiling is implausible —
the cause must be in our harness / prompt / segmentation / validator.

## Method

`scripts/m14a_rca.py` (offline; reads the recorded review packs + the source
articles; no network). For every quote the validator did NOT locate, classify:

| code | meaning | side |
|---|---|---|
| `A_render` | matches after a faithful plain-text render (markdown link text, smart quotes, fullwidth-CJK fold) the validator didn't do | pipeline |
| `A_near` | near-verbatim (one punctuation diff / a dropped trailing word), similarity ≥ 0.90 | pipeline |
| `B_boundary` | matches the whole article rendered, but no single paragraph (quote spans paragraphs / list items) | pipeline (segmentation) |
| `E_validator` | matches the validator's own normalization in the ref paragraph but was marked not-found | pipeline (bug) |
| `D_compress` | model condensed a list, still grounded (similarity ≥ 0.70) | model |
| `D_paraphrase` | genuine rewrite (similarity < 0.70) | model |

## Finding: 68% pipeline, 32% model — and the 32% is concentrated + grounded

Across the 3 cases' 37 unlocated quotes:

```
 7  B_boundary      7  A_near      6  A_render      4  A_render_wrongpara
 1  E_validator     7  D_compress  5  D_paraphrase
 pipeline (A/B/E): 25/37 = 68%     model (C/D): 12/37 = 32%
```

- **English (rag_wrong, eval_ai_agents): ~95% pipeline.** The model's quotes are
  near-verbatim (similarity 0.9–0.99 to source); the misses are markdown link
  syntax (`[text](url)`), smart apostrophes (`it's` vs `it's`), bold, and
  paragraph boundaries. The model copied correctly; our validator rejected it.
- **Chinese (agent_memory_zh): the genuinely harder case.** Its failures are
  near-verbatim fuzzy diffs, list/paragraph **boundary** spans, and the model
  **compressing dense multi-item lists** (`Episode（…）；EventLog（…）；…` collapsed
  into one summary quote). That is segmentation + task-framing, not
  representation — and even the compressions are grounded (sim 0.6–0.8), not
  fabricated.

## Pipeline re-review — where the detail diverges

1. **Validator normalization gaps (biggest, cheapest).** The matcher compared
   raw markdown bytes. It did **not** fold smart quotes/dashes, did **not**
   NFKC-fold fullwidth CJK punctuation (`：，、` vs `:,,`), and stripped link
   *syntax* but left the URL instead of extracting the visible link text. The
   model normalizes as it reads/copies → mismatch at the validate step.
2. **Source representation mismatch.** We feed the model raw markdown and
   validate against that same raw markdown — but the model copies the *rendered*
   text. The two ends disagree on form.
3. **Segmentation.** `paragraphs()` splits on blank lines; dense lists and
   sentence groups get cut where the model quotes across them → `B_boundary`,
   especially in the Chinese list sections.
4. **Task framing (residual, model-side).** For very dense list paragraphs the
   model condenses several `；`-separated items into one quote rather than
   quoting one item.

## Fix applied this stage: faithful-render validator (offline-verified)

The validator now matches in tiers Exact → Whitespace → **Rendered**, where
Rendered renders BOTH the quote and the source paragraph to plain text (extract
markdown link text, fold smart quotes/dashes + fullwidth CJK, strip emphasis,
case-fold). All faithful, reversible normalizations, so a Rendered match is
grounded → accepted (located at paragraph granularity). **No model change, no
re-record** — verified by replaying the existing cassettes:

| case | quote_found before | after render-fix | Δ |
|---|---|---|---|
| rag_wrong | 63.6% | **77.3%** | +13.7 (accepted 9→12) |
| eval_ai_agents | 78.0% | **83.1%** | +5.1 |
| agent_memory_zh | 27.3% | **27.3%** | 0 |

English recovered (its failures were representation). Chinese did not move — its
failures are fuzzy/boundary/compression, which an exact-after-render substring
match does not catch. This both proves the diagnosis and isolates what's left.

## Recommendation (prioritized; the gate is still not met — decision is yours)

1. **Source plain-text view + span map (needs a re-record).** Feed the model the
   same rendered plain text the validator matches, with a map back to the
   original source span. Then "what the model sees == what we validate" and the
   `A_near` tiny-diff cases stop happening at the source. Highest leverage for
   the residual English + some Chinese.
2. **Finer segmentation (re-record).** Split paragraphs into sentence / list-item
   spans so a quote anchors to one span and boundary-spanning is rare. Directly
   targets `B_boundary` and the Chinese lists.
3. **Span-id schema for the residual list-compression (the user's id-only idea).**
   `evidence_ref` + `evidence_span_id`, with `evidence_quote` demoted to an
   optional preview. Right fix for the ~32% where the model condenses a list —
   stop asking it to transcribe, let it point. Lowest priority (smallest bucket,
   and grounded).
4. **NOT:** RLHF, change of model, or killing Source → Unit. The model copies
   fine (English ~95% pipeline); the work is in our representation/segmentation.

Optional confirmation (not needed for the verdict, deferred): a live **copy-only
probe** (model copies a substring from a given paragraph) and an **id-only
probe** (model emits span ids, no quote). The offline data already shows the
model's quotes are near-verbatim, so these would confirm, not decide.

## What was NOT done (per instruction)

No M14a.2 schema change, no ReferentCandidate, no v2 changes, no KnowledgeMEM
comparison, no RLHF. Deliverables: this report + `scripts/m14a_rca.py` + the
faithful-render validator fix. `.run/` (cassettes, packs) is gitignored and not
committed.
