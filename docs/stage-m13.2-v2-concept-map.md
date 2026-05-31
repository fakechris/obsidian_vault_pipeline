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
2. **Versioned prompt.** `prompts/article_concept_map.md` is a distinct asset
   with its own id + schema version: `article_concept_map/v2`,
   `CONCEPT_MAP_SCHEMA_VERSION = 2`. A v2 response can never replay against a v1
   cassette and vice-versa. The v2 **prompt-builder** + manifest wiring + live
   cassettes are M13.3 — v1 is still the only prompt the builder emits.
3. **Parser.** `ArticleParser` claims both prompt ids and branches on
   `is_v2`. A v2 envelope with no `concepts[]` drops **loud**
   (`transform.article_parser.empty_concepts`) — it never silently falls back to
   the v1 shared-`one_liner` path. v1 responses still produce `concepts: []`.
4. **Resolver gate.** When `doc.concepts` is non-empty, `ConceptResolver` gates
   the map in place (no-op for v1). It drops — with observable `FilterDropped`
   events — concepts that are invalid-slug / `promote=false` / carry a
   `reject_reason` / lack a definition, evidence, *or* an owned claim; and
   collapses duplicate slugs + `merge_with` targets, **first survivor wins**,
   deterministic and order-stable. It normalizes the surviving slug back onto the
   concept, so the writer mints the canonical spelling.
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

## What is still NOT done (deliberately) — M13.3

- **v2 prompt-builder + manifest wiring** — the builder still emits v1 only.
- **Live cassette re-record** under `article_concept_map/v2`.
- **Default flip** to v2 + a full real-model gauntlet pass.
- **Real benchmark green** on actual model output (the real test of prompt
  quality) — the synthetic proof is necessary but not sufficient.

## Tests

- `ovp-domain` (`article_parser.rs`): v2 parses a distinct-definition map; a v2
  envelope missing `concepts[]` drops loud; wrong schema_version drops; a v1
  response yields empty `concepts`.
- `ovp-domain` (`concept_resolver.rs`): the gate drops bad concepts with the
  expected event codes; merges + dedups (first survivor wins); an all-valid map
  forwards with no events; v1 docs are untouched.
- `ovp-domain` (`evergreen_concept_writer.rs`): two v2 concepts from one article
  get **distinct** definitions and **non-recycled** claims; the v2 path ignores
  v1 candidates; a blank title derives from the slug.
- `ovp-domain` (`tests/concept_map_v2_synthetic.rs`): the synthetic e2e proof
  above (gates noise, mints seven, distinct defs + owned claims + no forbidden
  phrase), emitting notes under `OVP_M13_OUT` for offline benchmark scoring.
