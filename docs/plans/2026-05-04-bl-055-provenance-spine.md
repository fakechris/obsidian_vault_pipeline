# BL-055 — Provenance Spine

**Status**: Active (in same PR as BL-054)
**Author**: 2026-05-04
**Milestone**: M14 follow-up

## 1. Why a spine

BL-054 fixed three coupled scoring bugs by patching three independent
data paths.  That works, but it's fragile: every future feature that
needs to know "where did this object come from?" has to re-derive
the answer from frontmatter or audit logs.  Provenance becomes a
de-facto schema concept that every stage has to remember.

The user's observation in conversation:

> 靠每个环节修复太不可靠了

> Fixing each stage separately is unreliable.

The fix is to elevate provenance to a **first-class projected
table** with one strict rule:

> Every Canonical-State object has at least one provenance row that
> answers ``derived_via_stage`` + ``parent_object_id`` (or NULL for
> root objects).

This is the spine.  Every stage writes to it; doctor validates it;
crystal_scoring (and any future feature) reads from it.

## 2. Schema

```sql
CREATE TABLE provenance (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  -- The source URL (article URL).  Empty for fully-derived objects
  -- whose immediate parent is itself an object (e.g., a claim
  -- derived from a concept; trace back via parent_object_id).
  source_url TEXT NOT NULL DEFAULT '',
  -- Stable hash of source_url; used as a join key against
  -- source_authority.source_id where available.
  source_fingerprint TEXT NOT NULL DEFAULT '',
  -- The pipeline stage that produced this row.  Free-form for now
  -- so new stages don't need a schema bump:
  --   ``ingest`` (raw article landed in 50-Inbox)
  --   ``extract`` (LLM extractor produced a candidate concept)
  --   ``promote`` (candidate → canonical evergreen)
  --   ``synthesize_community_crystal``
  --   ``synthesize_contradiction_crystal``
  --   ``backfill`` (BL-054 backfill from audit_events)
  derived_via_stage TEXT NOT NULL,
  derived_at TEXT NOT NULL,
  -- Lineage upward.  NULL for root objects (sources that came from
  -- outside the system).  When set, references another object_id
  -- in the same pack.
  parent_object_id TEXT,
  -- Free-form JSON metadata (e.g. session_id, llm_model used).
  -- Cheap to store, occasional to read; doctor keys off the top
  -- level for stats.
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (pack, object_id, derived_via_stage, derived_at)
);

CREATE INDEX idx_provenance_object ON provenance(pack, object_id);
CREATE INDEX idx_provenance_source ON provenance(pack, source_url);
CREATE INDEX idx_provenance_stage ON provenance(pack, derived_via_stage);
```

**Append-only**: PK includes ``derived_at`` so each stage that
touches an object writes a new row.  Old rows stay as audit history.

**Read pattern for "what's the source URL for this object?"**:

```sql
SELECT source_url FROM provenance
 WHERE pack=? AND object_id=? AND source_url != ''
 ORDER BY derived_at DESC LIMIT 1
```

Cheaper alternative for the hot path (crystal_scoring):
denormalize the latest source_url into ``objects.source_url`` (BL-054
already did this — kept).

## 3. What lands in this PR

Single PR with BL-054 (already committed), this BL-055 layered on:

1. **Schema**: ``provenance`` table in ``truth_store.py``.
2. **Helper**: ``provenance.upsert_provenance(conn, ...)`` thin
   wrapper.
3. **Rebuild**: ``rebuild_knowledge_index`` writes one ``stage='ingest'``
   provenance row per evergreen using the frontmatter ``source_url``
   that BL-054 backfill populated.
4. **Doctor**: ``ovp-doctor`` adds an "Provenance health" section:
   * ``X / Y objects have at least one provenance row``
   * ``Z objects with empty source_url AND empty parent_object_id``
     (orphans — flagged)
   * ``Distinct source_urls covered: N`` (vault-level diversity)
5. **Tests**:
   * Schema test: provenance table exists after rebuild.
   * Population test: every evergreen with frontmatter ``source_url``
     gets a provenance row.
   * Doctor test: provenance health appears in doctor JSON.

## 4. What's deliberately NOT in this PR (BL-056 territory)

* Wiring ``promote_candidates``, ``synthesize_community_crystals``,
  and ``synthesize_contradiction_crystals`` to write provenance
  rows on each touch.  Right now provenance is hydrated from
  frontmatter on rebuild — sufficient because evergreens are the
  granularity scoring keys off.  Adding stage-level emit is a
  follow-up.
* Lint rule "every newly-promoted object MUST have a provenance row" —
  good north star but needs the stage emit hooks above.
* Crystal-scoring switching from ``objects.source_url`` to a
  ``provenance`` join — performance overhead without functional
  win until the broader spine work lands.
* ``parent_object_id`` chains for claims/relations/crystals — the
  schema supports them but rebuild doesn't write them yet.

The point of this PR is to **install the spine** — the table, the
rebuild population, the doctor check.  Subsequent BLs flesh out
which stages emit and how lint enforces.

## 5. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Append-only growth — every rebuild adds rows | PK on ``(object_id, stage, derived_at)`` prevents duplicates from rebuilds with the same timestamp; rebuilds are usually <1/min so growth is bounded |
| Doctor adds work to the orientation page | Single SELECT COUNT — sub-millisecond |
| Frontmatter ``source_url`` becomes load-bearing | Already true after BL-054 — this BL just makes it explicit |
| Future stages forget to write provenance | BL-056 adds lint to enforce |

## 6. Success criteria

After this PR + ``ovp-knowledge-index`` rebuild on the live vault:

* ``SELECT COUNT(*) FROM provenance`` ≥ 6500 (one per backfilled evergreen)
* ``SELECT COUNT(*) FROM provenance WHERE source_url != ''`` ≥ 6500
* ``ovp-doctor`` JSON output has a ``provenance`` section with
  non-degenerate stats.
