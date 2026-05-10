# Canonical Write Ownership (BL-060)

**Status:** Audit complete (PR #1 of 2). Refactor to enforce ownership lands in BL-060 PR#2.

**Why this exists.** PR #185's review surfaced the root cause of a class of bugs: three independent modules wrote `objects.source_url` (rebuild from frontmatter, `ovp-backfill-provenance`, `ovp-backfill-objects-source-url`) with no single source of truth. When their behaviours drifted, reconciliation became impossible.

The fix is the **single-writer invariant**: every column in every canonical table has exactly one *owner module* that's allowed to write it. Other modules call the owner's helper. Locking inside owners is already covered by `withFileLock` / `knowledge_db_write_lock`; this doc is about *who's allowed to issue the write at all*.

---

## Canonical tables in scope

The four tables in `knowledge.db` that hold canonical OVP state:

| Table | Holds | Append-only? |
| --- | --- | --- |
| `objects` | Evergreen registry — one row per (pack, object_id) | No (rebuild replaces) |
| `provenance` | Stage emit log (BL-055) — one row per (pack, object_id, stage, derived_at) | Yes |
| `claims` | Atomic claim records | No (rebuild replaces) |
| `relations` | Typed object → object edges | No (rebuild replaces; `relation_promoted` events replay) |

Out of scope (governance / projection tables — different lifecycle, not "canonical"):

- `audit_events`, `signal_ledger`, `action_queue` — append-only logs, multiple writers expected
- `compiled_summaries`, `community_crystals`, `contradiction_crystals` — projections (rebuild rewrites)
- `pages_index`, `page_links`, `page_fts`, `page_embeddings` — projections
- `graph_clusters`, `graph_edges` — projections
- `evolution_*`, `contradictions`, `concept_dedup_*` — derived

`evergreen_revisions` (BL-061, not yet built) will follow the same ownership pattern when it lands.

---

## Owner module map

| Table | Column scope | Owner | Allowed entry points |
| --- | --- | --- | --- |
| `objects` | full-row insert | `truth_store_writers.objects_writer` *(new module, BL-060 PR#2)* | `bulk_replace_objects(conn, pack, rows)` — used by `rebuild_knowledge_index` |
| `objects` | column update: `source_url` | `truth_store_writers.objects_writer` | `update_object_source_url(conn, pack, object_id, source_url, *, source=...)` |
| `provenance` | every row | `provenance.upsert_provenance` *(already exists)* | `upsert_provenance(conn, ...)` — single canonical helper, idempotent on PK |
| `provenance` | bulk insert (rebuild path) | `provenance.bulk_upsert_provenance` *(new helper, BL-060 PR#2)* | wraps `executemany` over `upsert_provenance` for the rebuild's per-pack flush |
| `claims` | every row | `truth_store_writers.claims_writer` *(new module)* | `bulk_replace_claims(conn, pack, rows)` — used by rebuild only today |
| `relations` | every row | `relation_writer.upsert_relation` *(promote `_ensure_relation_row` to owner)* | `upsert_relation(conn, *, pack, source_object_id, target_object_id, ...)` |
| `relations` | bulk replace (rebuild path) | `relation_writer.bulk_replace_relations(conn, pack, rows)` | rebuild only |

**Single-writer invariant:** every other module that wants to insert / update / delete one of these tables MUST go through the owner above. Direct SQL like `conn.execute("INSERT INTO objects ...")` outside the owner module is a violation.

---

## Audit results — current violations (as of 2026-05-10)

Five sites bypass the owner pattern today. BL-060 PR#2 refactors them all.

### Critical: `relations` table — 3 writers, full-row overlap

| Site | Function | Writes via | Trigger |
| --- | --- | --- | --- |
| `knowledge_index.py:1159` | `rebuild_knowledge_index` | raw `INSERT INTO relations` | rebuild |
| `relation_promotion.py:89` | `_ensure_relation_row` | raw `INSERT INTO relations` | candidate review |
| `relation_promotion.py:228` | `replay_relation_promotions` | raw `INSERT INTO relations` | rebuild's replay step |

Eleven columns (`pack`, `source_object_id`, `target_object_id`, `relation_type`, `evidence_source_slug`, `quote_text`, `locator`, `content_hash`, `retrieval_context`, `status`, `verified_at`) are written by all three with near-identical SQL. Worst-case drift surface in the codebase.

**Refactor target (PR#2):** `_ensure_relation_row` becomes the owner (rename → `upsert_relation`). The other two sites call it. Rebuild's bulk path goes through a `bulk_replace_relations` wrapper so it stays performant.

### High: `provenance` table — 2 writers via copy-pasted SQL with dedup guard

| Site | Function | Writes via | Trigger |
| --- | --- | --- | --- |
| `knowledge_index.py:1110` | `rebuild_knowledge_index` | raw `INSERT INTO provenance ... WHERE NOT EXISTS` | rebuild ingest baseline |
| `commands/backfill_objects_source_url.py:277` | `main` (via `--write-provenance`) | raw `INSERT INTO provenance ... WHERE NOT EXISTS` | backfill CLI |

Same 8-column row, same dedup guard, written from two places. The owner — `provenance.upsert_provenance` — already exists and uses `INSERT OR IGNORE` against the same PK. **Both sites should call it.**

**Refactor target (PR#2):** rebuild path switches to `bulk_upsert_provenance(conn, rows)` (new helper that wraps `executemany`). Backfill CLI calls `upsert_provenance(conn, ...)` directly.

### High: `objects.source_url` — 2 writers

| Site | Function | Writes via | Trigger |
| --- | --- | --- | --- |
| `knowledge_index.py:1093` | `rebuild_knowledge_index` | raw `INSERT INTO objects (...source_url)` | rebuild from frontmatter |
| `commands/backfill_objects_source_url.py:264` | `main` | raw `UPDATE objects SET source_url = ?` | backfill CLI |

This is the original PR #185 root cause. The two sites compute `source_url` from different inputs (frontmatter vs. audit-event walk + provenance lookup); without a shared helper their conventions diverged.

**Refactor target (PR#2):** introduce `truth_store_writers.objects_writer.update_object_source_url(conn, pack, object_id, source_url, *, source: str)` where `source` ∈ `{rebuild, backfill, ...}` for audit. Rebuild's full-row insert stays in the writer module; the backfill CLI calls the column-update helper.

### Clean: `claims` — 1 writer

`knowledge_index.py:1140` is the only site that touches `claims`. No violation today, but BL-060 PR#2 still hoists it into the owner module so future writers can't be added without hitting the fitness test.

---

## Architecture-fitness test

The test in `tests/test_architecture_fitness.py` enforces this map at import time:

1. Walk every `.py` file under `src/ovp_pipeline/`.
2. Parse string literals; flag any that match
   ```regex
   (?i)\b(INSERT|UPDATE|DELETE)\s+(OR\s+(IGNORE|REPLACE)\s+)?(INTO\s+)?(objects|provenance|claims|relations)\b
   ```
3. Allow the match only when the file is one of the owner modules listed above (`OWNER_FILES` constant in the test).
4. Fail with a pointer to this doc on any other site.

This catches *new* violations at PR-review time. Existing violations are tracked in this doc and removed in PR#2; once that lands, the test goes from advisory to strict.

---

## What this doc is not

- Not about row-level locking. That's `withFileLock` / `knowledge_db_write_lock` / per-row PK serialization, all already in place.
- Not about idempotence. Owners may or may not be idempotent; that's per-table policy (`upsert_provenance` is, `bulk_replace_objects` is not).
- Not a constraint on *reads*. Any module can read any canonical table.
- Not a constraint on derived / projection tables (see "Out of scope" above).
