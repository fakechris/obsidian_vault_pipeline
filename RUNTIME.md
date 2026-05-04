# OVP Runtime

> Architecture index: [README](./README.md) | [ARCHITECTURE](./ARCHITECTURE.md) | **RUNTIME** | [PACKS](./PACKS.md) | [PRODUCT_SURFACES](./PRODUCT_SURFACES.md) | [GLOSSARY](./GLOSSARY.md)
>
> **This file explains:** how OVP commands actually run — the six pipeline stages, what each stage reads/writes, and which CLIs invoke them.
> **This file does not explain:** the durable state model (see [ARCHITECTURE](./ARCHITECTURE.md)), pack semantics (see [PACKS](./PACKS.md)), or surface details (see [PRODUCT_SURFACES](./PRODUCT_SURFACES.md)).

---

## Stages

OVP execution flows through six stages. Each stage is defined by what it consumes from and produces for the [Canonical State / Projection / Access Surface](./ARCHITECTURE.md) model.

```text
Ingest -> Interpret -> Absorb -> Refine -> Normalize -> Derive
```

| Stage | Consumes | Produces | Crosses the Canonical State boundary? |
| --- | --- | --- | --- |
| **Ingest** | external Source (URL / file / clipping) | raw record + content hash + ingestion metadata | no — produces a Source row, not Canonical State |
| **Interpret** | raw record | normalized markdown, parsed metadata, identified entities, attribution | no — refines the Source representation |
| **Absorb** | interpreted material | Candidate evergreens, claim drafts, extraction artifacts | no — Candidates only; review still pending |
| **Refine** | Candidates | reviewed Candidates with evidence quotes, attribution, deduped concepts | no — Candidates that have passed semantic review |
| **Normalize** | reviewed Candidates | canonical handles, identity merges, alias resolutions, contradiction detection | gated by Governance — promotion happens here when policy allows |
| **Derive** | Canonical State | Projections (`knowledge.db`, graph, search index, materialized views, crystals) | never writes Canonical State |

The fifth stage was previously named `Canonical` in older docs; it is now called **Normalize** to remove the name collision with the architecture term `Canonical State`.

## CLIs by stage

| Stage | Primary CLI | Notes |
| --- | --- | --- |
| Ingest | `ovp-article` `ovp-paper` `ovp-github` `ovp-clippings` | per-source-kind ingest |
| Interpret | bundled inside `ovp` and `ovp-extract*` | rarely called standalone |
| Absorb | `ovp-absorb` `ovp-evergreen` | candidate emission |
| Refine | `ovp` (with `--with-refine`) `ovp-promote*` | review + dedup + promotion gate |
| Normalize | `ovp-merge-identities` `ovp-link-entities` `ovp-resolve-contradictions` | identity + alias + conflict resolution |
| Derive | `ovp-knowledge-index` `ovp-build-views` `ovp-synthesize-community-crystals` | projection rebuilders |

The orchestrators `ovp` (full / incremental) and `ovp-autopilot` chain the stages. See `commands/run_operations.py` for the wiring.

## Schema versioning

`KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION` (in `knowledge_index.py`) is bumped whenever the Projection schema changes. On the next `ovp-knowledge-index` run the projection_lifecycle marker triggers a full rebuild from Canonical State, which is exactly the design property the [ARCHITECTURE](./ARCHITECTURE.md) `Test:` line for Projections asserts.

## Idempotency

- **Ingest** is idempotent on `(content_hash, ingestion_timestamp)`.
- **Absorb / Refine** are idempotent on the underlying Source content: re-running on an unchanged Source produces an equivalent set of Candidates (post-LLM-determinism caveats).
- **Normalize** is idempotent on `(canonical_handle, alias)` pairs — re-running a wikilink pass on an already-linked vault is a no-op.
- **Derive** is idempotent by definition: deleting and rebuilding a Projection is the safe path.

## Stage failure modes

A failure in any stage must not corrupt Canonical State. The runtime relies on this invariant:

- Ingest / Interpret failures: leave the Source raw, retry-safe.
- Absorb / Refine failures: discard partial Candidates; queue for review.
- Normalize failures: never half-promote — the supersede + INSERT is one DB transaction (see `synthesis/_versioning.py` for the crystal example of this pattern).
- Derive failures: leave the Projection in the partial state, but Governance can flag and `rm -rf` + rebuild.

If any stage's failure can leave Canonical State inconsistent, that is a runtime bug, not a feature.
