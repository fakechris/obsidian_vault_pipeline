# Stage M12a — Rich Evergreen Minting

> **Status: landed.** Scoped follow-up to the M11 necessity audit
> (`docs/processing-pipeline-audit.md`), which found that minting an evergreen
> concept produced a bare stub. M12a makes a *freshly minted* evergreen note a
> grounded, reusable, source-linked knowledge unit. It is **not** full absorb
> and **not** RAG work — see "Out of scope" below.

## The problem

Before M12a, `EvergreenConceptWriter` minted a concept and `EvergreenSink` wrote
a provenance-free **stub**: frontmatter plus a single placeholder line, *"Stub
evergreen. Expand with an atomic definition and links."* Because the L6 RAG
corpus retrieves over evergreen note **bodies** (`ovp-rag::RagCorpus` reads each
note off disk), a semantic ranker built on top would have been ranking over
empty placeholders — retrieval quality was bottlenecked on note *content*, not
the ranking algorithm. The audit's recommendation was therefore: ground the
note bodies before RAG v1.1.

## What M12a does

When `EvergreenConceptWriter` mints a *new* concept from an interpreted article,
it now threads the article's grounding onto the `EvergreenConcept`, and
`EvergreenSink` renders a grounded note body instead of a stub.

**Output contract — a minted evergreen note carries:**

- title + slug (frontmatter; `status: minted`);
- a one-line **definition** (the article's `one_liner` — currently *article-level*,
  i.e. shared by every concept minted from the same article; concept-specific
  definitions are future, M12b+);
- **up to five** source-backed claims (usually 2-5 when the article has material;
  fewer if its claim pool is thin — a `## Source-backed claims` list);
- a **source link** (`## Source` → `[source_title](source_url)`);
- **related** concept wikilinks when available (`## Related` → `[[slug]]`);
- deterministic rendering, so re-minting the *same* concept is an idempotent
  `VaultCreate` (same content → same hash).

### Claim selection (deterministic, explainable)

`select_source_claims` is explainable, not scored. It builds a priority-ordered,
de-duplicated pool from the article's `details`, then `actions`, then the
what/why/how explanation; keeps claims that mention a slug token first (whole-
word match, mirroring the retriever's tokenizer, so a short token like `ai`
doesn't match inside `claim`); and if fewer than a small floor (≤3 distinct)
matched, tops up from the front of the pool so a note with material never
degrades to a bare definition. Capped at five. `select_related` takes the
article's `linked_concepts`, drops the concept's own slug, de-duplicates, caps
at eight. Both are pure and order-stable.

### Thin/rich split

`EvergreenConcept::try_mint(slug, &InterpretedDoc)` builds a rich concept;
`EvergreenSink::render_body` renders the grounded body. The legacy thin
constructors (`try_from_candidate` / `from_candidate`, used by fixtures/seeding)
leave the rich fields empty, and `render_body` then falls back to the unchanged
provenance-free `render_stub`. So the stub path — and its cross-document
idempotence property — is genuinely untouched; only production minting is rich.

## Where the grounding lives (and does not)

The grounding lives in the **vault note body only**. `CanonicalConcept` (the
`CanonicalUpsert` payload read by the MOC and knowledge-index rebuilds) is
**unchanged** — still slug, title, evergreen_path, provenance. So the canonical
store, MOC, knowledge index, `ovp-stores`, `ovp-query`, and the RAG
retriever/ranker are all unaffected; RAG simply reads a richer body off disk.
This keeps the blast radius to three `ovp-domain` source files
(`evergreen.rs`, `transforms/evergreen_concept_writer.rs`,
`sinks/evergreen_sink.rs`) plus tests.

## Same-slug handling (resolved in M12b)

The grounded body is **per-document** (it embeds the definition, claims, and
source link of the specific article), so two different articles surfacing the
same slug render *different* bodies. At the raw applier this means the second
`VaultCreate` would hit an existing path with a different hash and fail
(`OpResult::Failed`) rather than overwrite — correct low-level behavior, but in
M12a alone it would halt a multi-article run on a common slug (`rag`,
`ai-agent`, …). **M12b closes this**: before applying, the run-cycle reconciles
each minted evergreen `VaultCreate` against the on-disk note — MintNew when
absent, keep (idempotent-skip) when identical, **EnrichExisting** (a merge
`VaultUpdate`) when a different note already holds the slug, or skip when there
is nothing new to add. The merge unions source-backed claims / sources / related
links and keeps the first note's definition; a conflicting `CanonicalUpsert` is
dropped (first-writer-wins, preserving the original provenance — the merged note
body still carries every source). See `docs/stage-m12b-same-slug-reconcile.md`.
Still future (M12b+/M13): concept-specific definitions, mint/enrich/escalate/
reject policy lanes, semantic dedup of near-duplicate claims, and crystal
materialization.

## Out of scope (deliberately not in M12a)

Existing-note enrichment (no `VaultUpdate`); cross-document merge/dedup; crystal
materialization; mint/enrich/escalate/reject policy lanes (v1 is AUTO-all);
embeddings / semantic ranker / LLM judge; any watcher/automation expansion. No
changes to `ovp-eval` / `compare-run`. Invariants held: `ovp-core` stays
domain-blind + I/O-blind + sync; `EvergreenConceptWriter` stays a pure
`Transform` (no effect client); writes only via `WritePlan` → `PlanApplier`;
`ovp-stores` stays domain-blind.

## Tests

- `evergreen.rs`: definition/source-title/provenance from the article; claim
  selection (token-matched first, floor top-up, pool fallback to
  actions/explanation, cap at 5); related drops-self/dedups/caps; invalid slug
  still rejected; thin constructors leave rich fields empty.
- `evergreen_concept_writer.rs`: minted concepts carry definition + claims.
- `evergreen_sink.rs`: grounded (non-stub) body with all sections; deterministic
  body + hash; canonical payload stays minimal (no grounding leaks).
- `evergreen_note.rs`: parse↔render round-trip; merge unions + idempotence;
  `reconcile_evergreen_write` (MintNew / keep-identical / EnrichExisting / skip /
  unparseable-skip).
- `ovp-stores` e2e: minted notes are grounded not stub; reconcile enriches the
  same slug across documents; the raw applier still rejects a conflicting
  `VaultCreate` (the backstop the reconcile sits in front of).
- `ovp-run` e2e: a run-cycle over a pre-existing different-grounding note for the
  same slug enriches (succeeds) instead of failing, and is idempotent on re-run.
- `ovp-rag` e2e: a corpus built from a real minted note returns a grounded
  snippet (not the placeholder), retriever/ranker unchanged.

## Recommended next stage

The same-slug mainline risk is closed (M12b — `docs/stage-m12b-same-slug-reconcile.md`).
Before RAG v1.1 (the embedding/semantic ranker), the remaining absorb work is:
concept-specific definitions, mint/enrich/escalate/reject policy lanes, and
semantic dedup of near-duplicate claims across documents. Crystal
materialization is a separate later stage.
