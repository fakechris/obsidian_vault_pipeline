# BL-052 — Maintainer Vocabulary Audit

**Status**: Proposed (audit-first, no code changes in this BL)
**Author**: 2026-05-04
**Milestone**: M16 (Surface Reshape) — follow-up to BL-050 + BL-051

## 1. Problem

After BL-050 split Reader / Maintainer at the URL level and BL-051 cleans the
Reader-side vocabulary, the **Maintainer shell still has its own term chaos**:
many overlapping nouns, some pointing at different DB entities, some pointing at
the *same* DB entity through different surfaces, some genuinely distinct but
named in a way that hides the distinction.

Examples (representative, not exhaustive):

| Surface noun | Looks like another | Actually backed by |
|---|---|---|
| `Candidates` (`/ops/candidates`) | `Evolution Candidates` (`/ops/evolution`) | `candidate_concepts` table vs `evolution_candidates` table — two tables, same word |
| `Contradictions` (`/ops/contradictions`) | `Open Questions` (`/ops/open-questions/fragment`) | `contradictions` table vs `60-Logs/open-questions.jsonl` — concept overlap, source split |
| `Pulse` (`/ops/pulse`) | `Events` (`/ops/events`) + `Audit` nav label | `pulse_events` (in-memory) vs `audit_events` (table) — three labels for two streams |
| `Signals` (`/ops/signals`) | `Actions` (`/ops/actions`) | `signals.jsonl` vs `action_queue` table — chained but distinct |
| `Briefing`, `Summaries`, `Deep Dives` | each other | `briefing` (orientation), `compiled_summaries` table, `deep_dive_derivations` view — three real things |
| `Production` (`/ops/production`) | `Evolution` (`/ops/evolution`) | `production_chains` view vs `evolution_candidates` table |
| `Workbench` (`/ops/workbench`) | the dashboard at `/ops` | both self-describe as "operator hub" |

This is real chaos.  The user (and future maintainers) cannot keep these apart
without reading source.  But:

* it **does not match what BL-051 fixes** (BL-051 = Reader-side; this is
  Maintainer-side)
* it touches **more code** (every operator surface)
* the right consolidations are not all obvious — some pairs *are* truly
  different and just need disambiguation, others are genuine duplicates and
  should merge into one surface

So this BL is **audit-first**: produce a matrix that names every surface and its
backing entity, classify the overlaps, then decide cuts in a follow-up BL-053.

## 2. Scope of this BL

**In scope:**
- Walk every Maintainer surface (`/ops/...` HTML route + JSON twin where it
  exists + corresponding fragment route)
- For each, record:
  - the URL
  - the renderer function
  - the view-model function
  - the DB / file source(s) it reads
  - the human-facing surface label currently shown
  - whether/how it overlaps with another surface
- Classify each overlap as one of:
  - **R — Real duplicate** (same data, two surfaces, merge candidate)
  - **C — Concept overlap, distinct sources** (two entities that genuinely
    talk about the same idea — needs naming clarity)
  - **N — Distinct but mis-named** (two real things, current names don't
    differentiate them)
  - **K — Keep** (real distinction, current names already clear)
- Produce a recommended action per row: rename / merge / leave / promote-doc
- Save the matrix as `docs/plans/2026-05-04-bl-052-maintainer-vocab-matrix.md`

**Out of scope (defer to BL-053):**
- Any actual code change
- Any URL rename or 301
- Any DB column rename
- Any test changes

## 3. Inventory targets

The matrix should cover, at minimum, every Maintainer-shell route from BL-050:

```
/ops                      Overview (dashboard)
/ops/candidates           candidate_concepts queue
/ops/candidates/review    promotion mutation
/ops/candidates/fragment  embedded fragment
/ops/contradictions       contradictions queue
/ops/contradictions/resolve  resolution mutation
/ops/signals              signals queue
/ops/actions              action_queue
/ops/actions/run-next, /run-batch, /retry, /dismiss, /enqueue
/ops/actions/fragment
/ops/evolution            evolution candidates
/ops/evolution/review     evolution mutation
/ops/production           production_chains view
/ops/runtime-state        (API only — runtime probe)
/ops/pulse                live activity stream
/ops/pulse/fragment, /pulse/stream
/ops/events               audit_events
/ops/reuse/fragment       reuse_events
/ops/open-questions/fragment   open-questions JSONL
/ops/writing-prompts/fragment  writing-prompts file
/ops/summaries            compiled_summaries
/ops/summaries/rebuild    summaries mutation
/ops/deep-dives           deep_dive_derivations
/ops/briefing             daily orientation
/ops/briefing/fragment    embedded fragment
/ops/workbench            review actions
/ops/clusters             graph_clusters listing
/ops/cluster?id=          single-cluster detail
/ops/objects              typed-object audit table
```

Plus **CLI verbs that share words** with the surfaces:

```
ovp-promote*, ovp-doctor, ovp-list-crystals, ovp-truth, ovp-source-coverage,
ovp-refresh-source-authority, ovp-rerender-crystals, ovp-rescore-crystals
```

## 4. Suspected overlap groups (preview — to be confirmed by audit)

Best guess at what the matrix will surface, so we know the audit is exhaustive:

### Group A — "candidate" word collision

* `candidate_concepts` (concept-level promotion proposals)
* `evolution_candidates` (relation/object-level promotion proposals)

Likely: rename `evolution_candidates` surfaces to something like
**`Relation Proposals`** to stop sharing the word "candidate".  `candidate_concepts`
keeps "Candidates" since it landed first.

### Group B — "what's happening" triplet

* `Pulse` (live, in-memory)
* `Events` (audit log, table)
* `Audit` (current Maintainer-nav label that points at /ops/events)

Likely: collapse `Audit` nav label into `Events` (one fewer name), and keep
`Pulse` distinct — it's the live stream, not the persisted log.

### Group C — `Open Questions` vs `Contradictions`

* `contradictions` (typed binary tension between two claims, detected at
  extraction time)
* `60-Logs/open-questions.jsonl` (free-form unresolved questions, written by
  query feedback loop — Phase 36 territory, not yet wired)

Likely: rename the JSONL surface to **`Query Feedback Questions`** or just
**`Followups`**, freeing "Open Questions" to map to contradiction crystals on
the Reader side.

### Group D — three forms of compiled content

* `Briefing` — daily orientation, one document per pack
* `Compiled Summaries` — per-object summary text
* `Deep Dives` — derivation notes (long-form analysis)

Likely: keep all three, but document the distinction explicitly in
`PRODUCT_SURFACES.md` and `GLOSSARY.md`.  These are not duplicates.

### Group E — "Workbench"

* `/ops` (dashboard, has been called "Workbench" in copy)
* `/ops/workbench` (specific reviewer surface)

Likely: rename one or absorb the smaller into the larger.

## 5. Process + estimate

1. **Audit pass** (estimated 2–3 hrs): walk the routes, fill the matrix, mark
   every overlap as R/C/N/K.  Code-grep for actual sources + renderers.  Save
   matrix doc.
2. **Stakeholder review** (you + me reading the matrix together): mark each row
   as do-now / defer / leave.  Likely happens in one chat turn.
3. **BL-053 implementation plan**: derived from step 2.  Sized by what we
   actually decide to change.

This BL produces only the matrix doc.  No PR.

## 6. Why audit before action

Because rushing maintainer renames creates *new* chaos.  The Reader side was
small enough to rename in one PR (BL-051), but the Maintainer side touches
~25 routes, ~30 helper functions, and three append-only tables that load-bear
audit metadata.  An audit-first split:

* keeps the "what's actually wrong?" question separate from "what to do?"
* lets us catch genuine semantic distinctions we'd otherwise mass-rename away
* gives a doc artifact future maintainers can re-read

If after the audit it turns out 80% of the chaos is in 2-3 surfaces, a small
BL-053 PR follows.  If it's structural across many surfaces, BL-053 may itself
need to split.

## 7. Sequencing

1. **BL-051** (Reader vocab + map) — implement now
2. **BL-052** (this audit) — start once BL-051 is in review.  Produces the
   matrix doc.
3. **BL-053** (Maintainer vocab cleanup) — derived from BL-052 audit;
   exact scope decided after step 2.

This audit doc is itself the BL-052 deliverable.  Implementation starts at
BL-053.
