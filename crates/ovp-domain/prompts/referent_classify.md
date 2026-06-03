# Referent classifier — v1 (M14b, local ReferentCandidates)

You classify the OBJECTS that already-accepted knowledge units talk about into
**local ReferentCandidates**. You are NOT judging whether a unit's claim is true —
only naming the thing each unit is ABOUT.

This is **NOT canonicalization**: no evergreen, no concept promotion, no canonical
slugs, no registry, no vault, no merge-into-canonical. `surface_names` are local
document strings exactly as they appear — **never** a slug.

## Where referents may come from

You are given the accepted units (id, kind/subtype, text, quote, arguments) and a
deterministic SEED surface list harvested from their arguments. You may:
- classify or discard any seed;
- ADD object surfaces that appear **literally** in a unit's `text` or `quote` but
  that the arguments missed (e.g. a product/system/file named only in the text).

You may NOT invent surfaces from outside the units. FORBIDDEN sources: the article
title, frontmatter/metadata, free keywords, any v2 `concepts[]`, MOC, KnowledgeMEM,
your own world knowledge. **Every `surface_name` must be a substring (or a close
morphological variant) of some supporting unit's `text`/`quote`/`arguments`** — a
deterministic gate DROPS any surface not found in a supporting unit, so an invented
or re-abstracted surface is wasted output. In `rationale`, say which unit field
each surface came from.

## The conservative classification LADDER

For each object, walk top-to-bottom and take the FIRST match. Default DOWN, never up.

- **GATE 0 — noise**: a bare property/predicate ("没有记忆", "match score"), a
  placeholder with no real anchor ("new approach", "fundamental challenge"), or
  metadata → `kind:"noise"`. Stop.
- **GATE 1 — entity**: names a SPECIFIC identifiable thing — product, system,
  library, file, person, org, vendor, model, benchmark — or a named feature/
  construct OF one (IdeaBlock, Blockify, Raindrop, Signals, OpenClaw, EverOS,
  memory.md, LoCoMo, GPT-4.1-mini, Agent Skill) → `kind:"entity"`, NO boundary. Stop.
- **GATE 2 — concept**: ONLY if ALL hold — (a) it names a reusable, re-implementable
  abstraction/mechanism (not one instance), (b) you can state a non-trivial
  `boundary` (includes, ideally excludes) **from the supporting units**, not from
  outside knowledge, (c) the surface is locatable in a supporting quote OR is an
  explicitly defined coined term. If you cannot write the boundary from the units,
  it is NOT a concept — fall through. Stop.
- **GATE 3 — local_phrase**: source-local wording, or a handle for an ACTION /
  recommendation (the gerund/verb-phrase subject of a directive/recommendation/
  procedure_step unit: "reading raw logs", "A/B testing in production", "adding
  eval cases"), or a rhetorical contrast ("a better unit") → `kind:"local_phrase"`. Stop.
- **GATE 4 — ambiguous**: could plausibly be entity OR concept and you cannot
  decide → `kind:"ambiguous"`, KEEP it, do NOT force concept. Stop.

## Three hard rules

- **R1 — concept is NEVER the default.** If in doubt between concept and anything
  else, it is NOT a concept — choose ambiguous or local_phrase. A human curator
  mints only ~2 true concepts per article; v1 minted 9 that ALL shared one
  definition. **More than ~2–3 concepts for one article means you are over-minting.**
- **R2 — a proposition is never a concept.** A claim, finding, result number,
  failure-mode, recommendation, stance, or procedure step is what the source SAYS;
  the referent is what it says it ABOUT. Never lift a proposition into a concept. A
  surface that paraphrases a whole sentence (especially a model-coined gerund) is
  `local_phrase`.
- **R3 — co-referent surfaces become ONE candidate.** Two surfaces denoting the
  same thing (IdeaBlock/IdeaBlocks; "floor raising" across 3 units; 语义记忆 across
  mentions) → ONE candidate with multiple `support_unit_ids` and both strings in
  `surface_names`. This is LOCAL grouping for THIS document — pick no canonical
  winner, assign no slug. When unsure whether two surfaces co-refer, do NOT group
  (a wrong merge silently loses a referent).

## Anti-regression guards (these are how v2 failed — do NOT repeat them)

- **TAXONOMY**: when several surfaces are the named members/buckets/subtypes of ONE
  classification the article presents together, emit ONE candidate
  (`kind:"concept"`, `subtype:"taxonomy"`) whose `surface_names` list the members —
  NOT one per member. (v2 wrongly split episodic/semantic/procedural memory; and
  Stumbles/Issues/Signals.)
- **METADATA**: NEVER emit a candidate for an author name, the author's employer/
  client list, the author's own promoted product, a social handle, a star/view
  count, or a marketing figure — even inside a unit's text. It is provenance, not
  the object → `kind:"noise"`.
- **THESIS / JARGON UMBRELLA**: NEVER emit the article's overall subject (it is the
  document itself, not a referent) or a term that only appears in an intro
  jargon list / "tools like X, Y, Z" grab-bag with no unit developing it → noise or
  local_phrase.
- **PROPER NOUN**: if units attribute a mechanism/multi-stage process to a named
  recurring product/system, the surface MUST be that proper noun (`kind:"entity"`),
  never a generic descriptor. (v2 emitted "pipeline-architecture" instead of the
  entity "Blockify".)
- **DISTINCTNESS**: a concept's rationale + evidence must distinguish it from its
  siblings by its OWN supporting evidence. If its support could equally describe a
  different candidate, downgrade to ambiguous.
- **SAME-DEFINITION**: if two candidates would carry the same supporting text /
  evidence, they are ONE referent — merge surfaces, do not mint duplicates.

## Boundary (concepts only)

`boundary = {includes: <from the defining unit>, excludes: <from a contrasting
sibling unit, if any>}`. Source it ONLY from supporting units, never world
knowledge. A boundary that merely restates the surface ("a thing about X") will be
rejected.

## Output

Return a SINGLE JSON object, no prose, no fences:

```json
{
  "referents": [
    {
      "surface_names": ["<local string>", "<variant>"],
      "kind": "entity | concept | ambiguous | local_phrase | noise",
      "subtype": "taxonomy | null",
      "support_unit_ids": ["u-...", "u-..."],
      "boundary": {"includes": "...", "excludes": "..."},
      "rationale": "which unit field each surface came from + why this kind"
    }
  ]
}
```

(`boundary` only for `kind:"concept"`; omit otherwise. Do not emit `id`,
`evidence_refs`, or `confidence` — those are derived deterministically downstream.)

## Accepted units
