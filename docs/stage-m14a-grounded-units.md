# Stage M14a — Grounded Unit Extraction Spike

> **M14a.2 (Rendered Source View + span ids) done — big English win, Chinese
> improved, residual is pipeline not model.** The model is shown the rendered
> plain-text span view (`[p017.s002] text`) and the validator matches the SAME
> view; arguments are advisory (non-gating); metrics split (ref_found /
> quote_found / ref_mismatch / quote_not_found / arg_drift_advisory). Live v3:
>
> | case | M14a.1 | **M14a.2** | target | met |
> |---|---|---|---|---|
> | rag_wrong | 63.6% | **100%** | ≥90% | ✅ |
> | eval_ai_agents | 78.0% | **93.3%** | ≥95% | ✗ (1 unit) |
> | agent_memory_zh | 27.3% | **41.7%** | ≥70% | ✗ |
>
> `accepted_without_quote=0` all; `needs_review=0` (args no longer gate); zh
> parses cleanly. The eval + zh residual is **near-verbatim quotes that straddle
> segmentation boundaries** (similarity 0.85–0.97 to source; the model starts
> mid-span / ends in the next) — NOT model paraphrase/compression and NOT a
> transcription limit. So the next step is segmentation/matching granularity (a
> paragraph-spanning / conservative near-match), still pipeline — not a schema
> downgrade or model change. Decision is the operator's.
>
> ---
> **Earlier: M14a.RCA (see `docs/stage-m14a-rca.md`).** Root cause is
> **our pipeline, not the model**: 68% of quote failures are representation /
> segmentation / validator (the model copies near-verbatim; we mis-normalized).
> A faithful-render validator fix recovered the English cases offline (rag
> 63.6%→77.3%, eval 78.0%→83.1%, zh flat) with **no re-record**; the residual is
> segmentation + the model compressing Chinese lists. NOT a kill / RLHF / model
> change. M14a is an experimental, parallel, **deletable** hand-harness that
> answers ONE question:
>
> > Given a source, can OVP extract minimal knowledge **Units** each backed by a
> > verbatim quote found in the source text?
>
> It does NOT write the vault, does NOT touch v1/v2 defaults, does NOT go through
> GraphAssembler / RunCycle / WritePlan / DomainBody. It produces a human-
> inspectable **review pack** under `.run/m14/<case>/`.

## Live run 2 — M14a.1 evidence-ref hardening (`unit_extract/v2`, MiniMax-M2.7-highspeed)

The model is shown a paragraph-tagged body (`[pNNN]`) and must emit
`evidence_ref` (the paragraph id) + a short `evidence_quote` from that paragraph;
the validator scopes quote matching to the referenced paragraph.

| case | total | accepted | needs_review | rejected | quote_found | arg_locatable |
|---|---|---|---|---|---|---|
| rag_wrong | 22 | 9 | 5 | 8 | **63.6%** | 45.2% |
| eval_ai_agents | 59 | 5 | 45 | 9 | **78.0%** | 24.5% |
| agent_memory_zh | 22 | 1 | 5 | 16 | **27.3%** | 11.8% |

(quote_found numbers are after a validator fix found mid-run — see below.)

**Two real wins:**
- **JSON transport fixed.** `agent_memory_zh` previously failed to parse (the
  model embedded unescaped ASCII `"` in a long Chinese quote). With short,
  paragraph-anchored, escape-instructed quotes it now parses cleanly (0 parse
  errors). Evidence-ref hardening did what it was for.
- **The ref mechanism works.** `ref_mismatch` ≈ 0 across all cases (rag 0, eval 4,
  zh 0): when the model cites a real quote it gets the *paragraph* right. The
  model can **locate**.

**But the gate is not met — and the root cause is now precisely isolated.** The
misses are not ref errors; they are `quote_not_found` — the model picks the right
paragraph then **paraphrases the quote text instead of copying it verbatim**
(rag 8/22, eval 8/59, zh ~15/22). The model can locate but cannot reliably
**transcribe**. Issue histogram: rag = 8 quote_not_found + 5 arg_drift; eval = 41
arg_drift + 8 quote_not_found + 5 fuzzy + 4 ref_mismatch; zh = 16 quote_not_found
+ 4 arg_drift.

**Integrity note — matcher bug found + fixed mid-run.** Spot-checking zh
"not found" quotes showed some WERE in the article modulo whitespace. The
`normalize()` step collapsed whitespace to a single space, but the model (esp. in
CJK, which has no inter-word spaces) drops whitespace the source has → real quotes
scored as not-found. Fixed to whitespace-INSENSITIVE matching and re-measured
offline by replaying the recorded cassettes (zh 22.7% → 27.3%; rag/eval
unchanged, confirming their misses are genuine paraphrase, not whitespace). The
numbers above are post-fix and honest.

**`arg_locatable` over-gating** is now the dominant needs_review driver (eval
41/59): arguments are paraphrased topic words that don't character-match. This is
validator calibration, NOT a grounding failure, and is orthogonal to the
quote_found verdict — recommend making `arguments` advisory (not status-gating)
to de-noise the review pack regardless of the decision below.

### Verdict + decision (operator's call, per the kill gate)

Acceptance after M14a.1: ✅ `agent_memory_zh` parse_error = none · ✅
`accepted_without_quote = 0` (all 3) · ✅ `model-reply.txt` present · ✅ CLI
non-zero on parse/empty · ❌ `rag_wrong ≥ 90%` (63.6%) · ❌ `eval_ai_agents ≥ 95%`
(78.0%). **The quote-fidelity bar is not met.** Per the gate ("if still far below,
kill or change model/output-format"), and given the now-isolated cause (locate-ok,
transcribe-unreliable), the evidence points to the **output-format** branch the
plan foreshadowed: make `evidence_quote` an **optional preview** and promote
`evidence_ref` to the primary grounding anchor (validate "ref exists + the unit is
supported by that paragraph"; keep the quote as a relaxed, non-gating hint). That
directly fits "the model can point at the paragraph but not free-copy the string."
Alternatives: try a non-reasoning model (may transcribe better), or kill
`Source → Unit`. **NOT auto-iterated** — this is the schema decision the plan
reserved for the operator now that ref-round evidence exists.

## Live run 1 (MiniMax-M2.7-highspeed, `unit_extract/v1`) — superseded by run 2

| case | total | accepted | needs_review | rejected | quote_found | arg_locatable |
|---|---|---|---|---|---|---|
| rag_wrong | 15 | 6 | 2 | 7 | **53.3%** | 48.5% |
| eval_ai_agents | 42 | 21 | 16 | 5 | **88.1%** | 72.1% |
| agent_memory_zh | 0 | 0 | 0 | 0 | parse error | — |

**The harness mechanism is sound:** `accepted_without_quote == 0` and
`duplicate_groups == []` on all three (the grounding + dedup invariants hold), and
the validator's location-derivation / classification worked. But **the model under
this prompt did not clear the kill gate** (`quote_found_rate ≥ 95%`): on
`rag_wrong` 9/15 units cited a quote NOT in the article — the model paraphrased in
`evidence_quote` instead of copying verbatim (the "paraphrase memory" failure the
gate exists to catch). `agent_memory_zh` fully failed to parse: the model embedded
**unescaped ASCII `"`** inside a Chinese `evidence_quote`, closing the JSON string
early — a model-side escaping error, correctly surfaced as a reviewable
`parse_error` (not a silent drop).

**Both dominant failures are GENERAL and prompt-addressable, not article-specific
hacks:** (1) verbatim-quote fidelity — strengthen the prompt to copy
`evidence_quote` character-for-character and omit a unit if no exact span exists;
(2) JSON-string safety — instruct the model to escape inner quotes / prefer the
source's own quote characters. The `arg_locatable` softness (eval 16/42
needs_review) is calibration: arguments are paraphrased topic words that don't
character-match — candidate to make arguments advisory rather than status-gating.

**Decision (operator's, per the kill gate — NOT auto-iterated to avoid a
treadmill):** either (a) ONE general prompt iteration targeting verbatim-quote
fidelity + JSON escaping, then re-record and re-apply the gate; or (b) conclude
the model can't reliably do verbatim grounding under this approach and kill.
Recommendation: (a) is warranted once — the fixes are general, the harness is
proven, and `accepted_without_quote == 0` shows the mechanism is trustworthy. If a
second iteration still sits far below the gate, kill.

## Why M14a (and why we stopped patching v2)

v1 hard-split article interpretation into concepts; v2 asked the LLM for
`concepts[]`; M13 then spent itself on aliases / slug / prompt / scorer. The
repeated pattern says the **extraction root is wrong**: `concepts[]` as the first
object keeps pulling entities, claims, actions, relations, and evidence into one
"concept" container (note `ConceptKind` literally already has `Claim`,
`Procedure`, `Taxonomy`). M14a tests a different root — **Source → Unit** — where
extraction is grounded in verbatim quotes and *classification is deferred*.

## Scope — Source + Unit ONLY

No `ReferentCandidate`, no entity/concept classification, no canonical promotion,
no artifact rendering. Those are M14b+, gated on M14a succeeding.

### Unit (the one object)

```
Unit {
  kind          // assertion | directive | relation | question
  subtype?      // definition | observation | result | limitation | recommendation | procedure_step | ...
  text          // a faithful, lightly-normalized restatement of ONE point
  evidence      // { quote, location derived by the validator }
  attribution   // author | quoted_person | system_interpretation   (REQUIRED)
  modality      // asserted | suggested | uncertain | contested | negated  (REQUIRED)
  arguments[]   // { surface, role } — the objects this unit is about
  status        // accepted | rejected | needs_review
  issues[]      // why it was rejected / flagged
}
```

### Hard constraints (the teeth)

1. `evidence_quote` must be findable in the source markdown — else **rejected**.
2. No `evidence_quote` ⇒ cannot be `accepted`.
3. `evidence.location` is **derived by the validator** searching for the quote;
   the model's offsets are ignored entirely.
4. `attribution` and `modality` are required (a missing/invalid value rejects that
   unit, not the whole doc — units are parsed individually).
5. `arguments` must be locatable in the quote or a bounded near-context window,
   else the unit is `needs_review` (argument drift).
6. A Unit is not a paraphrase memory: `text` is a *light* normalization of the
   quote (pronoun resolution, trimming), never a synthesis across sentences.

The validator enforces **grounding + structure** (quote-found, fields-present/enum,
args-locatable). It does **not** and cannot enforce *semantic correctness* —
faithfulness of `text` to the quote, and whether `attribution`/`modality` *values*
are right — those go to the review pack for human judgement.

## Pipeline (hand-harness, not the typed graph)

```
markdown file → read_source_doc → SourceDoc
  → build_unit_prompt → ModelRequest (cache_namespace = unit_extract/v1)
  → ModelClient (replay cassette | live)
  → parse_envelope (raw units JSON)
  → validate (quote match + location + arg locatability → accepted/rejected/needs_review)
  → write_unit_review_pack → .run/m14/<case>/
```

Reuses the real `ovp-llm` client (replay + live), so cassettes and live recording
work exactly as in M13. No DomainBody variant, no manifest, no GraphAssembler.

## Output — review pack `.run/m14/<case>/`

```
input.md                  copy of the source body
source.json               source_id, fingerprint, title/url
model-reply.txt           the RAW model reply, verbatim (so a parse error /
                          malformed unit is diagnosable as model- vs parser-side)
units.all.json            every emitted unit WITH its validation verdict
                          (accepted ∪ needs_review ∪ rejected)
units.accepted.json
units.rejected.json
units.needs-review.json
validation-report.json    metrics (below) + per-unit issues + parse_error
REVIEW.md                 human-inspectable: each accepted unit with its quote,
                          derived location/line, attribution, modality, arguments;
                          then needs-review and rejected with reasons; duplicates
```

(The truly-raw model output lives in `model-reply.txt`; `units.all.json` is the
structured, verdict-annotated view of the same units.)

## Evaluation — facts first, no fancy metrics

**Automatic** (`validation-report.json`):
- `quote_found_rate` — fraction of raw units whose quote is found in source.
- `accepted_without_quote == 0` — hard invariant.
- `argument_locatable_rate`.
- duplicate / near-duplicate units surfaced.
- determinism under replay (same cassette → byte-identical pack).
- `gold_span_overlap_recall` — if a few span-anchored gold units exist (optional).

**Human** (reading REVIEW.md):
- Is `Unit.text` faithful to the source? Attribution correct? Modality correct?
- Is a view the author *disputes* mislabelled as the author's own assertion?
- Are central units missing? Is the output just paraphrase memory?

**P0:** source didn't say it but a Unit does; quote doesn't match source;
attribution wrong; a disputed view minted as the author's; an accepted unit with
no evidence. **P1:** central unit missing; `text`/`argument` drift; many
duplicates; systematic modality error.

## Kill gate (write it before iterating — avoid a new M13 treadmill)

**Kill M14a if:** `quote_found_rate < 95%`; `accepted_without_quote > 0`;
human-sampled faithfulness `< 80%`; systematic attribution/modality errors; the
output is mostly paraphrase memory rather than quote-grounded units; or it needs
article-specific prompt hacks to pass.

**Proceed to M14b only if:** quote grounding is stable; accepted Units are
reviewable; central-unit coverage is reasonable; errors are local and classifiable;
and reading the review pack is clearly faster than reading the source.

## Benchmark design (how M14a avoids the M13 curator-slug trap)

Match on **quote-span overlap, never on slug/text equality.** Tier 1 is gold-free
(the invariants above). Tier 2 (optional) annotates each source with a handful of
*central* claims **anchored to source spans, not named**, and scores whether an
accepted Unit's quote span overlaps a gold span — two faithful paraphrases of the
same sentence both anchor to the same span, so vocabulary never enters scoring.

## Comparison with KnowledgeMEM (diagnostic only, source is truth)

Compare **source-level extraction**, not concept maps: OVP `accepted Units +
evidence_quote` vs KnowledgeMEM source-scoped memories. Questions: can each
KnowledgeMEM memory be found in the source? Are OVP Units traceable by
construction? Which ideas overlap / are unique to each side? Which is more
faithful vs over-synthesised; which too fine vs too coarse? KnowledgeMEM is not
treated as truth — the source article is.

## File layout (all deletable)

```
crates/ovp-domain/prompts/unit_extraction.md      v1 unit-extraction prompt
crates/ovp-domain/src/units/                       experimental module
  mod.rs         types: Unit, UnitKind, Attribution, Modality, Argument,
                 UnitEvidence, EvidenceLocation, UnitStatus, ValidationIssue,
                 SourceExtraction, ValidationReport + consts
  prompt.rs      build_unit_prompt(source) -> (system, user); ModelRequest helper
  parser.rs      parse_envelope(raw) -> Vec<serde_json::Value>; RawUnit
  validator.rs   validate(raw, source) -> SourceExtraction (quote match, location,
                 arg locatability, dedup, metrics)
  review_pack.rs write_unit_review_pack(out_dir, input_md, &SourceExtraction)
  harness.rs     extract_units(reply_text, source); run_unit_extraction(source, client)
crates/ovp-domain/tests/m14a_units.rs              offline parser/validator/pack tests
crates/ovp-cli/src/commands/extract_units.rs       thin live/replay entry (reuses client.rs)
```

## Operator runbook (live — run on a networked host)

```sh
set -a && . ./.env.live && set +a
export OVP_LLM_MAX_TOKENS=24000
export OVP_LLM_TIMEOUT_SECS=300

for case in rag_wrong eval_ai_agents agent_memory_zh; do
  input="$(cat fixtures/concept_map/$case/input_path.txt)"
  cargo run -q -p ovp-cli --features anthropic -- extract-units \
    --input "$input" \
    --out ".run/m14/$case" \
    --cache-dir .run/m14/cassettes \
    --client live
done

# then read each .run/m14/<case>/REVIEW.md and apply the kill gate.
```

`.run/` is gitignored; do NOT commit cassettes or review packs.

## Non-goals (explicit)

No v2 alias-aware scorer, no v2 default flip, no ConceptResolver/EvergreenWriter
changes, no canonical store / MOC / RAG / Crystal, no ReferentCandidate, no
GraphAssembler wiring, no DomainBody variant, no WritePlan/PlanApplier changes.
