# Phase 20: Semantic Trust And Production Traceability

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** complete

**Goal:** Make the post-Phase-19 workbench trustworthy in its two weakest places: event/contradiction semantics and end-to-end knowledge production traceability.

**Architecture:** `Phase 19` made the workbench easy to enter. `Phase 20` should make it easier to trust. Do not introduce a new ontology store, hosted service, or memory backend. Reuse the existing truth store, compiled-page contract stack, and `research-tech` pack surfaces. Harden the payload contracts that leave `truth_api.py`, then spend those contracts on stronger compiled pages and provenance-first aggregate views. This phase intentionally supersedes the remaining product work implied by the older `Phase 10` and `Phase 11` docs, because the current contract/UI architecture is now materially different.

**Tech Stack:** Python 3.13, SQLite via stdlib `sqlite3`, `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, `knowledge_index.py`, current `research-tech` pack contracts, pytest.

## Why This Is The Right Next Phase

`Phase 17` solved graph exploration.  
`Phase 18` solved contract legibility.  
`Phase 19` solved entry products.

What is still weak is not “where do I click?” but:

- can I trust what `/events` means?
- can I trust what a contradiction row actually claims?
- can I tell what knowledge a source note or deep dive really produced?
- can I move through the production chain without reconstructing it in my head?

That is why the next move should **not** be:

- temporal truth modeling,
- harness/session memory,
- benchmark infrastructure,
- graph workspaces,
- or a wider shell redesign.

The next move should be to make the current workbench semantically harder and production-legible.

## Product Thesis

After `Phase 20`, a user should be able to answer five questions from the product itself:

1. What kind of timeline row am I looking at?
2. Why is this item treated as an event rather than only a dated note?
3. Why is this contradiction open, and what evidence anchors it?
4. What did this source note or deep dive actually produce?
5. Where in the production chain am I right now?

This phase is therefore about two things:

- **semantic trust**
- **production-chain legibility**

## Relationship To Earlier Plans

This phase should be treated as the active successor to the older plans:

- [[2026-04-14-phase10-event-contradiction-hardening]]
- [[2026-04-14-phase11-knowledge-production-traceability]]

Those plans remain useful historical slices, but they were written before:

- the current contract stack existed,
- `ovp-export` and `ovp-ui` shared assembly/governance explanations,
- `Phase 19` turned the workbench into an entry-product surface.

`Phase 20` should therefore **supersede their remaining work**, not compete with them.

## What Phase 20 Must Deliver

### 1. Event Semantics Contract v1

Add explicit event/timeline semantics to the product contract:

- row type
- semantic role
- anchor kind
- grouping kind
- event-vs-note explanation

Users should be able to tell whether they are looking at:

- a note-date projection,
- a heading-date projection,
- or a stronger grouped event summary.

### 2. Contradiction Semantics Contract v1

Add explicit contradiction semantics to the product contract:

- detection model
- confidence semantics
- polarity / tension explanation
- evidence summary
- status bucket semantics

This does **not** mean “replace the detector.”
It means the current detector must stop hiding behind prose.

### 3. Production Traceability Contract v2

Strengthen the stable production-chain contract for:

- notes
- deep dives
- objects
- topics
- Atlas/MOC pages

Users should be able to see:

- upstream source material
- intermediate deep dives / processed notes
- downstream objects
- downstream Atlas/topic reach
- missing links or weak spots in the chain

### 4. Compiled Trust Sections On Key Pages

Spend the stronger contracts on the pages that matter most:

- `/events`
- `/contradictions`
- `/note`
- `/object`
- `/topic`
- `/production`

These pages should answer:

- what this surface means
- what evidence anchors it
- what is ambiguous
- what this item produced
- where the user should go next

### 5. Provenance-First Aggregate Views

Upgrade aggregate surfaces so they explain contribution, not only membership:

- event grouping summaries
- contradiction queue summaries
- production-chain summaries
- Atlas/topic contribution summaries

This phase should make aggregate browsing feel like a compiled editorial surface, not a pile of filtered rows.

## What Phase 20 Should Not Do

Explicit deferrals:

- full temporal truth modeling (`valid_at / invalid_at / expired_at`)
- LLM-heavy contradiction rewrites
- session/harness memory capture
- graph workspaces or saved routes
- backend migration or new runtime store
- benchmark platform work
- external domain-pack expansion

Those all remain valid later. They are not the next highest-leverage move.

## Recommended Implementation Shape

### Task 1: Harden Event Payload Semantics

**Files:**
- Modify: `src/openclaw_pipeline/truth_api.py`
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- stable timeline/event contract fields
- explicit grouping semantics
- clearer event-level summaries on `/events` and event dossiers

### Task 2: Harden Contradiction Payload Semantics

**Files:**
- Modify: `src/openclaw_pipeline/truth_api.py`
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- stable contradiction contract fields
- explicit evidence and polarity summaries
- better zero/open/reviewed semantics on `/contradictions`

### Task 3: Upgrade Production Traceability Contracts

**Files:**
- Modify: `src/openclaw_pipeline/truth_api.py`
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Reference: existing note/object/topic/production builders
- Test: `tests/test_truth_api.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- stronger note/object/topic/deep-dive production summaries
- explicit chain counts and gap semantics
- easier chain navigation from compiled pages

### Task 4: Spend The Contracts On Product Surfaces

**Files:**
- Modify: `src/openclaw_pipeline/ui/view_models.py`
- Modify: `src/openclaw_pipeline/commands/ui_server.py`
- Test: `tests/test_ui_view_models.py`
- Test: `tests/test_ui_server.py`

**Deliverable:**

- `compiled_sections` for semantic trust and production traceability land on the key pages
- pages surface “why this appears” and “what this produced” without requiring CLI or DB inference

### Task 5: Verification And Plan Closeout

**Files:**
- Modify: `docs/plans/2026-04-14-local-knowledge-workbench-milestone.md`
- Modify: `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- Modify: `progress.md`
- Modify: `task_plan.md`

**Deliverable:**

- verify docs cover the upgraded trust/traceability pages
- milestone sequencing clearly points at `Phase 20` as the active next execution target
- the new phase is ready to hand off into implementation

## Exit Condition

`Phase 20` is complete when all of the following are true:

1. `/events` explains stable row and grouping semantics instead of relying on interpretation alone.
2. `/contradictions` explains stable detection/evidence/polarity semantics instead of relying on prose alone.
3. note/object/topic/production pages make the full knowledge production chain legible from the product itself.
4. users can answer “why is this here?” and “what did this produce?” without reconstructing the chain manually.
5. focused tests lock the payload contracts so later heuristic changes cannot silently weaken product meaning.

## Recommended Follow-Up Ordering

After `Phase 20`, the most plausible next options are:

1. finish the remaining product-shell/operator UX work only if real navigation gaps remain,
2. deepen the active signal loop where it materially improves traceability and review quality,
3. defer temporal truth and harness-memory work until the current workbench semantics are clearly strong enough to justify a harder model.
