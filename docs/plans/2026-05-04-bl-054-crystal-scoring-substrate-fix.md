# BL-054 — Crystal Scoring Substrate Fix

**Status**: Active
**Author**: 2026-05-04
**Milestone**: M14 follow-up
**Prerequisite of**: any future scoring change (BL-049b reuse, BL-046b facets, etc.)

## 1. Problem (diagnostic)

User asked an algorithmic question: *should the ranking penalize a topic
whose evergreens all came from one source article?* Investigation surfaced
**three substrate bugs**, not just the missing signal:

### Bug 1 — `objects.source_slug` is not a source-article identifier

`source_slug` is the evergreen's **own** slug, not the source article it
came from.  Verify:

```sql
SELECT object_id, source_slug FROM objects WHERE pack='research-tech' LIMIT 3;
-- 10-star-product-discovery|10-star-product-discovery|...
-- 12-layer-harness-architecture|12-layer-harness-architecture|...
```

Every evergreen "owns" its slug, so any `unique_sources / member_count`
metric is structurally always 1.0.  100% of communities scored 1.0 in a
diagnostic pass — not because diversity is universal but because the
signal is structurally degenerate.

### Bug 2 — `source_authority` table never created during rebuild

`crystal_scoring._load_source_credibility` queries
`SELECT source_id, authority FROM source_authority`, but the table is
**not created** by `rebuild_knowledge_index`.  Schema lives in
`source_authority.py:_SCHEMA_SQL` with an `ensure_schema()` helper, but
nothing in the index pipeline calls it.  Verify:

```bash
$ sqlite3 knowledge.db "SELECT * FROM source_authority LIMIT 1"
Error: no such table: source_authority
```

Net: `credibility_norm` is **uniformly 0.0** across all 575 crystals.
Verify:

```sql
SELECT MIN(credibility_norm), MAX(credibility_norm), AVG(credibility_norm) FROM crystal_scores;
-- 0.0 | 0.0 | 0.0
```

The 30% weight on `credibility_norm` in `DEFAULT_WEIGHTS` does
**nothing** today.  Score formula effectively runs on 70% of its budget.

### Bug 3 — Evergreen frontmatter `source_*` fields are always empty

The frontmatter schema declares `source_url`, `source_domain`,
`source_fingerprint`, `source_authors`, etc.  Of 6,584 evergreens in
the live vault, **0 have any source-provenance field populated**.
The extractor never writes them.

This means even if Bug 2 is fixed (table exists), there is **no key**
to look up — `_load_source_credibility` returns a populated dict but
the lookup `source_credibility[evergreen_slug]` would still miss
because the dict is keyed by source URL, not by evergreen slug.

## 2. Why these are coupled

The naive fix to Bug 2 (wire `ensure_schema`) is dead on arrival
without Bug 3 (provenance to populate the lookup key) and Bug 1
(make sure the lookup uses the right key).  All three must land
together for `credibility_norm` to be a real signal.

## 3. Goal

By the end of this BL, on the live vault:

* `source_authority` table exists and contains 50+ rows from the
  JSONL log.
* Every evergreen has `source_url` (or `source_fingerprint`)
  populated in its frontmatter.  82% via link-resolution JSON
  backfill, remaining 18% via fallback heuristic OR explicitly tagged
  `source_unknown` (better than silently empty).
* `crystal_scoring._load_source_credibility` keys correctly by URL
  and `_credibility_sum` dedupes by source URL (not by per-evergreen
  count).
* `crystal_scores.credibility_norm` is no longer uniformly 0 — it
  varies meaningfully across topics.
* New `source_diversity_norm` signal lifts truly cross-source topics
  above single-article-dominated topics.
* New evergreens going forward carry source provenance from the
  extractor — no more silent gaps.

## 4. Phases

### A. `source_authority` schema + replay (1-2h)

1. `knowledge_index.rebuild_knowledge_index`: call
   `source_authority.ensure_schema(conn)` after `_initialize_database`.
2. After ensure: replay rows from `60-Logs/source_authority.jsonl`
   into the table.  Or, if the JSONL is the source of truth and no
   reads happen between rebuild and refresh, leave it to
   `ovp-refresh-source-authority`.
3. `INDEPENDENT_CANONICAL_TABLE_COLUMNS` (BL-049 fix) gets
   `source_authority` so the next rebuild preserves it.

### B. Provenance backfill (4-6h)

1. New script `scripts/backfill_evergreen_provenance.py`:
   * Walk `60-Logs/link-resolution/*.json`.
   * For every `create_candidate` decision, map
     `proposed_slug → article_url + fingerprint`.
   * Resolve `proposed_slug` → actual evergreen slug (via
     `concept_resolver` / dedup history if the candidate was renamed
     during promotion).
   * Update `pages_index.frontmatter_json` AND the on-disk
     evergreen markdown frontmatter — the markdown is canonical.
   * Coverage reporting: how many evergreens got attributed, how
     many didn't.
2. For unattributed (~18%), tag frontmatter
   `source_attribution_status: 'unknown'` so the gap is explicit
   instead of silent.
3. New CLI `ovp-backfill-evergreen-provenance` to invoke the script
   reproducibly.

### C. `objects` table provenance (1h)

1. Add column `objects.source_url TEXT NOT NULL DEFAULT ''`.
2. `knowledge_index.rebuild_knowledge_index` populates it from
   `pages_index.frontmatter_json.source_url` for evergreen rows.
3. Schema version 6 → 7.

### D. Forward data quality (extractor) (2-3h)

1. `auto_evergreen_extractor.py`: when creating new evergreens, set
   `source_url`, `source_domain`, `source_title`, `source_fingerprint`
   from the source article being processed.
2. Pipeline absorb stage already knows the source URL — thread it
   through.
3. Add a CI/test guard: any evergreen lacking `source_url` in
   frontmatter is a lint warning (initially), error after a grace
   period.

### E. Credibility refactor (2h)

1. `_load_source_credibility` returns `dict[str, float]` keyed by
   source URL (the same key as `source_authority.source_id`).
2. `_load_object_metadata` also returns `source_url` per evergreen.
3. `_credibility_sum`:

```python
def _credibility_sum(member_object_ids: list[str]) -> float:
    sources = {object_source_url[oid] for oid in member_object_ids
               if oid in object_source_url}
    sources.discard("")
    return sum(source_credibility.get(url, 0.0) for url in sources)
```

   Note the `set()` — same source counted **once**.  This kills the
   "20-evergreens-same-article" inflation.

### F. Source diversity signal (1h)

1. New helper `_source_diversity_signal(member_object_ids, object_source_url)`:

```python
SOURCE_DIVERSITY_TARGET = 3

def _source_diversity_signal(member_ids, object_source_url):
    if not member_ids: return 0.0
    sources = {object_source_url.get(oid) for oid in member_ids}
    sources.discard(None); sources.discard("")
    if not sources: return 0.0
    return min(1.0, len(sources) / SOURCE_DIVERSITY_TARGET)
```

2. Add `source_diversity_norm REAL DEFAULT 0` to `crystal_scores`.
3. Rebalance `DEFAULT_WEIGHTS`:

| Signal | Was | New |
|---|---|---|
| size_norm | 0.25 | 0.20 |
| credibility_norm | 0.30 | 0.20 |
| **source_diversity_norm** | — | **0.20** |
| contradiction_norm | 0.20 | 0.15 |
| reuse_recency_norm | 0.15 | 0.15 |
| evergreen_recency_norm | 0.10 | 0.10 |
| Sum | 1.00 | 1.00 |

4. Schema version bump 7 → 8.

### G. Re-rescore + diagnostic (30min)

1. `ovp-rescore-crystals` on the live vault.
2. Re-run diagnostic; show top-30 before/after diff in PR description.
3. Update plan doc with measured shift.

## 5. Out of scope (deferred)

* BL-052 maintainer vocabulary audit — separate PR.
* Re-synthesizing community crystals — no LLM cost in this BL.
* Migrating contradiction crystal source attribution — same
  treatment but the table layout differs slightly; can be a small
  follow-up if needed.
* Removing the `source_*` frontmatter fields that aren't being
  used (`source_authors`, `source_published_at`, …) — schema
  cleanup, not blocking.

## 6. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Backfill misattributes evergreen → wrong source | Use `proposed_slug` exact match; for ambiguous slugs (multiple JSONs claim the same `proposed_slug`), pick the earliest by article date; log every collision. |
| Rewriting evergreen frontmatter loses user edits | Read-modify-write via YAML round-trip; only set fields that are currently empty.  Diff before-after on a sample. |
| Extractor change breaks pipeline | Touch only the frontmatter writer; thread source URL through unchanged elsewhere; new test for "extractor populates source frontmatter". |
| Schema bumps require careful preserve-rows handling | Schema 6 (already added by BL-045) → 7 (objects.source_url) → 8 (crystal_scores.source_diversity_norm).  Both columns nullable defaults; preserve via the BL-049 INDEPENDENT_CANONICAL_TABLE_COLUMNS path. |
| 18% unattributed evergreens skew the diversity signal | Treat `source_url=""` as "unknown source" rather than "another unique source"; the diversity helper discards empty before counting. |

## 7. Test plan

* New: `test_source_authority_replay.py` — schema exists after rebuild, JSONL rows present.
* New: `test_evergreen_provenance_backfill.py` — fixture link-resolution JSON + 3 evergreens, backfill writes source_url to all 3.
* New: `test_extractor_writes_source_provenance.py` — synthetic extraction, assert `source_url` populated in created evergreen frontmatter.
* Updated: `test_crystal_scoring.py` — credibility test no longer over-counts duplicate sources; new test for `_source_diversity_signal`.
* Updated: `test_crystal_scoring.py` — `DEFAULT_WEIGHTS` test verifies new sum=1.0 and presence of `source_diversity` weight.
* Re-run full repo suite after each phase.

## 8. PR shape

Single PR, ~12 files:

1. `knowledge_index.py` — wire ensure_schema; replay JSONL; objects.source_url backfill from frontmatter; INDEPENDENT_CANONICAL_TABLE_COLUMNS adds source_authority; schema bumps 6→8.
2. `truth_store.py` — schema additions for `objects.source_url`, `crystal_scores.source_diversity_norm`.
3. `auto_evergreen_extractor.py` — populate source frontmatter on creation.
4. `crystal_scoring.py` — refactored credibility lookup, new diversity signal, weight rebalance.
5. `scripts/backfill_evergreen_provenance.py` — one-shot backfill (or absorbed into knowledge_index as an opt-in flag).
6. `commands/backfill_provenance.py` — CLI entry.
7. `pyproject.toml` — register `ovp-backfill-evergreen-provenance`.
8. New tests (4-5 files).
9. Plan doc (this file).
10. BACKLOG.md — BL-054 row.

Estimate: **10-14h**. No LLM cost.

## 9. Success criteria

* `sqlite3 knowledge.db "SELECT COUNT(*) FROM source_authority"` ≥ 50
* `evergreens with source_url populated` ≥ 80% of 6584
* `SELECT MIN, MAX, AVG FROM crystal_scores credibility_norm` is non-degenerate (max > 0.5, avg > 0.05)
* Top-30 by score after re-rescore differs from before; report the diff in PR.
* New evergreen extraction writes source_url (smoke test on a single fixture article).
