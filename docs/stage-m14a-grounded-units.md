# Stage M14a — Grounded Unit Extraction Spike

> **Status: built (offline-verifiable), live run pending operator.** M14a is an
> experimental, parallel, **deletable** hand-harness that answers ONE question:
>
> > Given a source, can OVP extract minimal knowledge **Units** each backed by a
> > verbatim quote found in the source text?
>
> It does NOT write the vault, does NOT touch v1/v2 defaults, does NOT go through
> GraphAssembler / RunCycle / WritePlan / DomainBody. It produces a human-
> inspectable **review pack** under `.run/m14/<case>/`.

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
units.raw.json            every unit the model emitted (pre-validation)
units.accepted.json
units.rejected.json
units.needs-review.json
validation-report.json    metrics (below) + per-unit issues
REVIEW.md                 human-inspectable: each accepted unit with its quote,
                          derived location/line, attribution, modality, arguments;
                          then needs-review and rejected with reasons; duplicates
```

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
