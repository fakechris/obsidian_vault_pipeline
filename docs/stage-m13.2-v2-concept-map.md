# Stage M13.2 — Additive v2 Concept-Map Path (synthetic-green)

> **Status: landed (capability proof, additive).** The pipeline can now carry a
> real, per-concept knowledge map — model response → parser → resolver gate →
> per-concept evergreen notes — and a committed synthetic e2e proof shows that
> **given a correct v2 response**, the minted notes pass the M13 concept-map
> benchmark (`rag_wrong` → 1/1). This is **synthetic-green, not real-green**:
> the v1 prompt path and its cassettes are untouched and still default; closing
> the real-model loop (prompt-builder wiring, live cassette re-record, default
> flip) is **M13.3**.

## The problem M13 surfaced

The v1 article prompt emits an article-level interpretation plus a flat
`linked_concepts` slug list. The model is never asked for concept-*owned* data,
so the writer synthesizes every evergreen note from the same article
`one_liner` (shared definitions) and token-matched article claims (recycled /
mis-filed claims). The M13 benchmark (`scripts/concept_map_bench.py`) catches
exactly this — the v1 `.run/m12q2` baseline scores **0/3**. No writer patch can
manufacture data the schema never carried; the only fix is a schema rebuild.

## What M13.2 does (Option A — additive, no new DomainBody)

A new field carries the concept map alongside the existing interpretation; no
`DomainBody::ConceptMap` variant, so node count and topology are unchanged.

1. **Domain model.** `InterpretedDoc` gains `concepts: Vec<ExtractedConcept>`
   (serde `default`, so v1 deserializes to empty). `ExtractedConcept` owns its
   `definition`, `evidence`, `claims`, `related`, `merge_with`, `reject_reason`,
   `promote`, plus `slug` / `title` / `aliases` / `kind` (`ConceptKind`).
   `promote` is **required** (no serde default): a real model omitting it must
   fail loud at parse, not silently default to `false` and drop every concept.
2. **Versioned prompt.** `prompts/article_concept_map.md` is a distinct asset
   with its own id + schema version: `article_concept_map/v2`,
   `CONCEPT_MAP_SCHEMA_VERSION = 2`. A v2 response can never replay against a v1
   cassette and vice-versa. The v2 **prompt-builder** + manifest wiring + live
   cassettes are M13.3 — v1 is still the only prompt the builder emits.
3. **Parser.** `ArticleParser` claims both prompt ids and branches on
   `is_v2`. A v2 envelope with no `concepts[]` drops **loud**
   (`transform.article_parser.empty_concepts`) — it never silently falls back to
   the v1 shared-`one_liner` path. v1 responses still produce `concepts: []`.
4. **Resolver gate (two-phase, order-independent).** When `doc.concepts` is
   non-empty, `ConceptResolver` gates the map in place (no-op for v1).
   **Phase 1 (structural validity):** drop — with observable `FilterDropped`
   events — concepts that are invalid-slug / `promote=false` / carry a
   `reject_reason` / lack a definition, evidence, *or* an owned claim. The
   evidence/claim floor is **content-aware** (≥1 non-whitespace entry, matching
   how `from_extracted` trims), not a bare `Vec` length test, so a model emitting
   `["   "]` can't sneak an ungrounded note past it. **Phase 2 (dedup + merge):**
   a survivor whose `merge_with` names *any other surviving* slug/alias is a
   synonym → drop it, **regardless of emission order**; the first survivor of a
   duplicate slug wins. Dedup + merge matching compare a normalized key
   (ASCII case-fold + `_`/space → `-`) so a model that drifts spelling across
   `slug` / `aliases` / `merge_with` (`Idea-Block`, `idea_block`) still collapses
   to one identity instead of minting duplicates. Dedup keys are **slugs only**,
   so a distinct concept whose slug equals another's alias is not a false
   duplicate. It normalizes the surviving slug back onto the concept, so the
   writer mints the canonical spelling.
5. **Writer.** `EvergreenConceptWriter` branches: v2 mints each note from its
   **own** concept (`EvergreenConcept::from_extracted` — definition = the
   concept's definition, claims = its owned claims, related = its related), with
   **no** fallback to the article `one_liner`; the v1 candidate path
   (`try_mint`) is unchanged. The render path (`EvergreenSink` →
   `EvergreenNote::render`) is shared, so v2 notes are byte-identical in format
   to v1 minting.

## Why these boundaries

- **The gate encodes only GENERAL rules** — slug validity, promotion, evidence
  floor, dedup/merge. **No benchmark slugs, no Nowledge terms, no article
  specifics** live in production code. *What* to mint is the prompt's judgment;
  the benchmark validates that judgment offline.
- **`knowledge.db` / canonical identity unaffected.** The concept map flows
  through the same `EvergreenSink` `CanonicalUpsert`; semantic retrieval is never
  promoted to canonical identity.
- **All writes still go `WritePlan` → `PlanApplier`**; the M12b same-slug
  reconcile applies to v2 notes unchanged.
- **Additive = v1 stays green.** v1 prompt id / schema / cassettes / gauntlet are
  untouched; v1 is still the default. M13.2 adds capability, it does not flip
  behavior.

## Synthetic-green vs real-green (read this before claiming the benchmark passes)

The committed proof (`crates/ovp-domain/tests/concept_map_v2_synthetic.rs`)
feeds an **ideal** v2 response — definitions taken from the benchmark fixture's
`expected_meaning`, claims from its `acceptable_claims` — through the real
parser → gate → writer → sink. It is a *simulated correct model*, **test input,
not production logic**. It proves the **pipeline carries** a correct map:

- the three planted noise concepts (`knowledge-unit` merge, `data-pipeline` +
  `rag` reject) are gated out;
- exactly the seven expected notes mint, each with a distinct definition, an
  owned claim, and no case-level forbidden phrase;
- `scripts/concept_map_bench.py --case rag_wrong` → **1/1 PASS**, deterministic.

It does **not** prove the real model emits such a response. A green here is
**synthetic-green**. The v1 `.run/m12q2` baseline still scores **0/3** —
unchanged — which is the correct "before" state.

## Post-review adversarial audit (M13.2 follow-up)

After the first cut, a reviewer found two real gate bugs — `merge_with` was
order-dependent, and `promote` silently defaulted to `false`. Both were fixed
(two-phase gate; `promote` required). An adversarial audit then found four more
of the *same class*, all now fixed + regression-tested:

1. **merge-target spelling drift** — byte-exact matching leaked a synonym whose
   `merge_with` varied case/separator (`Idea-Block` vs `idea-block`). Fixed by the
   normalized key.
2. **dedup spelling drift** — `vector-database` vs `Vector_Database` both survived
   → duplicate identities. Same fix.
3. **whitespace-only grounding** — `evidence: ["   "]` passed a `Vec`-length check
   but `from_extracted` trims it to empty → claim-less note. Fixed by the
   content-aware floor.
4. **slug == another concept's alias** — wrongly dropped as a "duplicate",
   order-dependently. Fixed by keying dedup on slugs only.

The audit also **refuted** two non-issues (alias-as-merge-target is designed
behavior; the `promote`-required whole-article drop is intended loudness).

## What is still NOT done (deliberately) — M13.3

- **v2 prompt-builder + manifest wiring** — the builder still emits v1 only.
- **⚠️ Empty-map fallback guard — REQUIRED before the default flip.** The writer
  selects v1 vs v2 by `interp.concepts.is_empty()`. If a *v2* doc's map gates to
  empty (all concepts rejected/merged), the writer would silently fall to the v1
  `concept_candidates` / `one_liner` path. Not reachable today (v2 isn't wired and
  the v2 prompt emits no `linked_concepts`), but M13.3 must carry an explicit
  "this is a concept-map doc" marker on `InterpretedDoc` and emit a **loud** event
  when a v2 map produces zero mintable concepts — never a silent v1 fallback.
- **Live cassette re-record** under `article_concept_map/v2`.
- **Default flip** to v2 + a full real-model gauntlet pass.
- **Real benchmark green** on actual model output (the real test of prompt
  quality) — the synthetic proof is necessary but not sufficient.

## Tests

- `ovp-domain` (`article_parser.rs`): v2 parses a distinct-definition map; a v2
  envelope missing `concepts[]` drops loud; a concept missing the required
  `promote` field drops loud (`json_parse`, naming the field); wrong
  schema_version drops; a v1 response yields empty `concepts`.
- `ovp-domain` (`concept_resolver.rs`): the gate drops bad concepts with the
  expected event codes; merges + dedups (first survivor wins); an all-valid map
  forwards with no events; v1 docs are untouched. **Order/spelling regression
  tests:** a synonym emitted *before* its canonical target still collapses; a
  mutual A↔B merge drops both (never leaks a duplicate); a self-referential
  merge is ignored; a merge into a Phase-1-dropped target does not merge;
  case/separator drift on a `merge_with` target and on a duplicate slug both
  still collapse; whitespace-only evidence/claims drop `low_evidence`; a slug
  equal to another concept's alias is not a false duplicate (order-independent).
- `ovp-domain` (`evergreen_concept_writer.rs`): two v2 concepts from one article
  get **distinct** definitions and **non-recycled** claims; the v2 path ignores
  v1 candidates; a blank title derives from the slug.
- `ovp-domain` (`tests/concept_map_v2_synthetic.rs`): the synthetic e2e proof
  above (gates noise, mints seven, distinct defs + owned claims + no forbidden
  phrase), emitting notes under `OVP_M13_OUT` for offline benchmark scoring.
