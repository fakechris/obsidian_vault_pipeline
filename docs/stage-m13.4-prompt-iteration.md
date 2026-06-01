# Stage M13.4 — v2 Prompt Iteration (slug drift, umbrella over-mint, abstract definitions)

> **Status: not started.** M13.3's live run against `MiniMax-M2.7-highspeed`
> went 0/3 on the concept-map bench. All three cases completed end-to-end
> with no transport / parse / resolver / writer errors, and minted healthy
> sets of promoted concepts (10 / 10 / 15). The 0/3 is a **prompt-quality**
> outcome, not a framework bug. This stage is the targeted iteration on
> `crates/ovp-domain/prompts/article_concept_map.md` to fix the three
> observed failure categories. **No production code change is expected**;
> if a new failure class shows up that does require code, that's M13.5.

## Scope — what changes, what does not

| In scope | Out of scope |
|---|---|
| `crates/ovp-domain/prompts/article_concept_map.md` (the prompt asset) | `crates/ovp-domain/src/transforms/article_parser.rs` |
| Per-case `fixtures/concept_map/*/expected/concept_map.yaml` (only if a definition is genuinely wrong about the article, with rationale) | `crates/ovp-domain/src/transforms/concept_resolver.rs` |
| | `crates/ovp-domain/src/transforms/evergreen_concept_writer.rs` |
| | `crates/ovp-domain/src/transforms/concept_resolver.rs` invariants |
| | The bench's `forbidden_mint` / `must_have` lists *as a target* — those are the *yardstick*, not a hard-code target |
| | The v1 path; v1 stays default until v2 is bench-green on real data |

**Hard rules** (re-stated from the M13.2 follow-up commit; do not break):

- **Never encode benchmark slugs in production.** Slugs come from the
  model's own output. The bench is the *test*, not a *target* the prompt
  should be told about.
- **Prefer prompt refinement over production-logic changes.** A
  gate-invariant change requires a general, documentable invariant;
  per-case drift goes in the prompt.
- **No default flip until the bench is green on real data** (per M13.3's
  deferral).

## Failure classification (from the M13.3 live run)

The bench report (`python3 scripts/concept_map_bench.py --ovp-root
.run/m13.3/live`) gives three concrete failure categories. Each
category is paired with the prompt clause to write next; nothing
production-side moves until the prompt has had a fair chance.

### Category A — slug drift (the dominant failure)

The model invents slug names that paraphrase the article instead of
using the article's own terminology. This is the single biggest source
of `missing` rows on every case.

| Case | LLM minted | bench `must_have` (missed) |
|---|---|---|
| `rag_wrong` | `chunk-as-unit-assumption` | `chunking-problem` |
| `rag_wrong` | `governance-in-data-layer` | `governance-metadata` |
| `rag_wrong` | (none) | `blockify`, `vector-redundancy`, `distillation-layer` |
| `agent_memory_zh` | (none of the article's product names) | `openclaw-memory-system`, `everos-system`, `everos-six-type-taxonomy`, `skill-self-evolution`, `active-retrieval` |

**Prompt clause to write:** the model MUST use a noun phrase that
appears in the article body for the slug (exact substring match, or
the article's own hyphenation if it uses one). If the article does
not name a concept at all, the model MUST NOT mint it as an evergreen.

### Category B — umbrella over-mint

The model mints a generic / umbrella concept the article only mentions
in passing, instead of treating it as background.

| Case | LLM minted | bench verdict |
|---|---|---|
| `agent_memory_zh` | `episodic-memory`, `procedural-memory`, `semantic-memory` | `forbidden` — the article's three-type taxonomy is a *background classification*, not a *developed concept* |
| `agent_memory_zh` | `agent-memory-taxonomy` (covered via the three above) | `covered_by_forbidden_alias` — same root cause |

**Prompt clause to write:** a concept qualifies as an evergreen only
if the article **develops** it (a definition, evidence, claims, an
example, a contrast). If the article merely *mentions* or *uses* a
term as background (a generic taxonomy, a competitor, a standard,
the article's own topic in the abstract), do NOT mint it. Concrete
test the model should apply: "would a careful reader of this article
write a separate note about this, or would they write one note that
mentions it in passing?"

### Category C — abstract definitions / missing content-guard phrases

The model's `definition` and `claims` are correct in spirit but use
general English instead of the article's own phrases, so the bench's
content-guard rules (`definition_must_include_any`,
`claims_must_include_any`) miss them.

| Case | LLM concept | bench content-guard failure |
|---|---|---|
| `eval_ai_agents` | `floor-raising` | definition hits `benchmark`/`score` (a sibling's signature); misses `reliability where`/`raising the floor`/etc. |
| `eval_ai_agents` | `golden-cases` | no claim includes `5 to 10` / `critical path` / `do not ship` |
| `eval_ai_agents` | `code-aware-evals` | definition lacks any of `tool calls` / `harness` / `pytest` / etc. |
| `eval_ai_agents` | `self-diagnostics` | definition matches the `production-monitoring-tiers` signature (`Signals`) — confused sibling |
| `rag_wrong` | `vector-redundancy` | no claim includes the article's specific framing (`fifteen near-duplicates` / `probability mass` / etc.) |

**Prompt clause to write:** `definition` MUST be a short phrase that
a careful reader would quote from the article's own discussion of
that concept (1-2 sentences, using the article's vocabulary, NOT a
generic restatement). `claims` MUST each contain at least one
article-specific phrase (a number, a name, a verbatim quote, a
specific framing). Generic phrasings like "improves X" or "is a key
concept" are not acceptable as `claims`.

## The cat B/C overlap — the prompt should not let a confused sibling in

The `self-diagnostics` and `floor-raising` rows show a sub-pattern:
the model writes a definition that *could* apply to a sibling
concept (`production-monitoring-tiers`, `benchmark-maxxing`) and
fires that sibling's signature phrase instead of the article's
own. The content-guard then catches it.

**Prompt clause to write:** for each concept, the `definition` and
the per-`claim` text MUST be distinguishable from the article's
OTHER concepts in the same map. If a careful reader could swap two
concepts' definitions and the article would still read sensibly, the
two concepts are not properly distinguished — the model must rewrite
to use the article's own anchor phrases for *this* concept (e.g.,
the section heading, a contrast the article draws, a quoted line).

## Operator loop (re-run for each prompt iteration)

The M13.3 runbook is the right starting point; the only changes
are the cassettes stay, the prompt is what changes.

```sh
set -a && . ./.env.live && set +a
export OVP_LLM_MAX_TOKENS=24000          # MiniMax-M2 reasoning headroom
export OVP_LLM_TIMEOUT_SECS=300          # M13.3 follow-up: 180s default, 300 here for safety

# wipe the v2 cassette namespace so the new prompt re-records;
# v1 cassettes under crates/ovp-domain/tests/cassettes are untouched.
rm -rf .run/m13.3/cassettes/article_concept_map

for case in rag_wrong eval_ai_agents agent_memory_zh; do
  input="$(cat fixtures/concept_map/$case/input_path.txt)"
  cargo run -q -p ovp-cli --features anthropic -- run-cycle \
    --manifest manifests/article_concept_map.pipeline.toml \
    --input "$input" \
    --vault-root ".run/m13.3/live/$case/ovp/vault" \
    --canonical-root ".run/m13.3/live/$case/ovp/canon" \
    --cache-dir .run/m13.3/cassettes --client live --date 2026-05-31 \
    --report ".run/m13.3/live/$case/report.json"
done

python3 scripts/concept_map_bench.py --ovp-root .run/m13.3/live
```

**Per-iteration checklist** (paste into the commit message body for
each prompt change so the trail is auditable):

1. The 3 prompt clauses above (A slug, B umbrella, C content-guard) are
   the *whole* diff against `article_concept_map.md`. No other lines
   touched.
2. Re-ran all 3 cases. The new bench report is in the commit body.
3. Failures remaining are reclassified A / B / C / D / E. The
   next-iteration target is whichever categories still have rows.
4. If a *new* failure category appears (e.g. the prompt tightening
   causes a parse failure, a structural regression, a new invariant
   miss), STOP and write a follow-up stage doc; do not keep iterating
   the prompt to mask a code-level bug.
5. **Until the bench is 3/3 on real data, v1 stays default.** No
   default flip on partial passes.

## Success criterion (M13.4 exit)

- `python3 scripts/concept_map_bench.py --ovp-root .run/m13.3/live`
  reports **3/3 cases pass** on real v2 output.
- All three v2 cases are `succeeded: true` in
  `.run/m13.3/live/*/report.json` with no parser / resolver / writer
  drops.
- v2 cassettes are recorded in
  `.run/m13.3/cassettes/article_concept_map/v2/`, deterministic on
  re-replay (no model-call variance on subsequent
  `CacheMode::ReplayOnly` runs).

When 3/3, then (and only then):

- Document the flip: change the v1 default to v2 in
  `manifests/article_evergreen.pipeline.toml` and
  `crates/ovp-cli/src/commands/run_cycle.rs`'s default `--manifest`
  arg, OR delete the v1 manifest path entirely (preferred, per the
  "v1 must sunset" principle — see the M13 review).
- Re-run the v1 baseline (M12Q2) on a separate branch and write a
  one-page comparison (`Nowledge/M12Q2` parity note, not committed)
  to retire the m12q2 record.
- Update the M13.3 doc's status banner to "real-green, default
  flipped".

## Anti-patterns (do not do these in M13.4)

- **Add a list of `must_have` slugs to the prompt** to teach the model
  the bench vocabulary. This is fake-green-by-construction. The
  fixture's `must_have` is the test; the prompt must be told
  *criteria*, not *answers*.
- **Move the v2 parser's `null_to_default` deserializer to v1 too**.
  v1 was not the failure mode; widening a fix is a regression risk.
- **Soften `ConceptResolver`'s grounding floor** to let a model with
  empty `evidence` mint anyway. The M13.2 follow-up made this fail
  loud on purpose; do not undo that.
- **Edit `InterpretationSchema` to silently coerce a v1 doc into a v2
  doc** because the model got the prompt_id wrong. A wrong
  `prompt_id` IS a parse-level bug and should surface as
  `transform.article_parser.wrong_prompt` so it's visible.
- **Commit the cassettes to the repo** to make the bench reproducible
  for CI. Cassettes are operator output; the bench is the
  committed test. (See the M13.3 doc's "Operator runbook" — `.run/`
  is gitignored on purpose.)
