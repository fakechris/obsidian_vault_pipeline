# BL-056 — Stage Emit Hooks for Provenance Spine

**Status**: Active
**Author**: 2026-05-04
**Milestone**: M14 follow-up — closes the provenance spine started in BL-055

## 1. Why

BL-055 installed the `provenance` table and populated it from
frontmatter on every `ovp-knowledge-index` rebuild.  That covers
**ingest** (the moment an evergreen lands on disk and the rebuild
sees it) but the audit trail is silent on the *upstream* events:

* When the LLM extractor proposes a concept candidate.
* When a candidate is promoted to canonical evergreen (the moment
  truth is committed).
* When a community crystal is synthesized.
* When a contradiction crystal is synthesized.

Each of those is a moment where new Canonical State (or a Projection
materialized from Canonical State) appears.  Each should write a
`provenance` row with the right `derived_via_stage` so doctor /
search / scoring can trace lineage.

Without this, the spine stays at one row per object — useful for
"who is your source?" but not for "what stages have touched you?".

## 2. Goal

After this BL, on the live vault:

* `provenance.derived_via_stage` distribution shows at least:
  `ingest`, `promote`, `synthesize_community_crystal`,
  `synthesize_contradiction_crystal` — with non-zero counts on
  every stage that ran since the merge.
* `ovp-doctor` provenance section reports the breakdown.
* Every newly-promoted evergreen and synthesized crystal has at
  least one corresponding provenance row written by its stage of
  origin (in addition to the rebuild's `ingest` row).

## 3. Concrete changes

### A. Shared helper

New module-level helper (or extension of `source_authority` style):

```python
# src/ovp_pipeline/provenance.py (new file)

def upsert_provenance(
    conn, *, pack, object_id, source_url, source_fingerprint,
    derived_via_stage, parent_object_id=None, metadata=None,
    derived_at=None,
) -> None:
    """Idempotent insert into the provenance table.  PK
    (pack, object_id, derived_via_stage, derived_at) means each
    distinct stage-emit at a distinct timestamp gets its own row;
    same-second re-emits are silently dropped via INSERT OR IGNORE.
    """
```

Pure SQL; no extra deps.  The helper is the canonical write path —
every stage uses it; the rebuild uses it; tests use it.

### B. Stage hookups

#### B1. `promote_candidates`

When `review_candidate_concept` writes a new evergreen (action
`promote` or `merge`), call `upsert_provenance` with
`derived_via_stage='promote'`, `source_url` carried over from the
candidate's frontmatter (or empty if none).

Touch points:
- `promote_candidates.review_candidate_concept` — the centralized
  promotion entry point.

Test: synthetic candidate → promote → assert one
`stage='promote'` row exists for the new evergreen.

#### B2. `synthesize_community_crystal`

When `commit_crystal_version` lands a new community crystal row,
emit:
- `derived_via_stage='synthesize_community_crystal'`
- `parent_object_id` = the cluster_id the crystal was synthesized
  from (informational; no FK)
- `metadata_json` = `{"llm_model": "...", "prompt_version": "...",
  "sample_size": N}`

Touch points:
- `synthesis._versioning.commit_crystal_version` — the atomic write
  helper that already lands community + contradiction crystals to
  disk + DB.

Test: synthesize one community crystal → assert one
`stage='synthesize_community_crystal'` row exists for it.

#### B3. `synthesize_contradiction_crystal`

Same pattern as B2 but with `stage='synthesize_contradiction_crystal'`
and `parent_object_id` = the contradiction_id.

### C. Doctor reporting (small)

`provenance.stage_breakdown` already exists in
`ovp-doctor --json`.  No code change needed; verify the new stage
names appear after a real promote / synthesize run.

## 4. Out of scope (deferred)

* **Lint enforcement** ("every newly-promoted object MUST have a
  provenance row of stage='promote'") — needs the audit-log review
  workflow to mature, can sit in BL-057.
* **`parent_object_id` chains for claims/relations/evidence** —
  currently we only set parent_object_id on synthesis crystals.
  Claim provenance would let queries trace "this fact came from
  which evergreen, which came from which source" — useful but a
  separate piece.
* **Cross-pack lineage** — provenance is pack-scoped; following a
  source-URL across packs is doable but not required today.
* **Re-emit on every rebuild** — keeping the ingest row dedup
  guard from BL-055 review fixes; BL-056 stages emit at write time,
  not rebuild time.

## 5. Risks + mitigations

| Risk | Mitigation |
|---|---|
| A failed `upsert_provenance` aborts the stage's main commit | Wrap in try/except; provenance write is best-effort.  ovp-doctor's orphan count surfaces failures. |
| Stage hook in `commit_crystal_version` doubles up with rebuild's `ingest` row | Different `derived_via_stage`; PK includes stage so they coexist. |
| Multi-pack deployments miss the wire-up | Each stage hook lives at the same call site that already writes the canonical row; either both happen or neither. |
| Tests need a `provenance` table fixture | Added to the existing test SCHEMA in `test_crystal_scoring.py`-style fixtures. |

## 6. Test plan

* `test_provenance_emit.py` (new):
  * promote_candidates → produces `stage='promote'` row.
  * commit_crystal_version (community) → produces
    `stage='synthesize_community_crystal'` row.
  * commit_crystal_version (contradiction) → produces
    `stage='synthesize_contradiction_crystal'` row.
* Existing `test_knowledge_index_preserves_crystals.py`: verify
  ingest rows still survive cross-rebuild after BL-055 review fix.
* `ovp-doctor --json` smoke after a synthesize run shows the new
  stage in `stage_breakdown`.

## 7. PR shape

This BL ships in the same PR as BL-052 (vocab audit matrix doc).
The two are unrelated work-streams but BL-052 is doc-only and
BL-056 is code+tests; combining keeps the review window short and
both pieces of work visible in one place.
