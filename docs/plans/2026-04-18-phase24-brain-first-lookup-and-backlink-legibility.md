# Phase 24: Brain-First Lookup And Backlink Legibility

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining semantic gap inside `Milestone 7` by making object/link creation explicitly prefer existing vault truth before creating new downstream knowledge, and by making the resulting backlink trail visible enough that operators can tell what was reused, what was newly created, and what still needs review.

**Architecture:** Keep the current authoring/runtime split intact:

- vault Markdown remains the authoring and export surface,
- `knowledge.db` remains the truth-aware runtime store,
- `ovp-ui` remains the main inspection/review surface,
- the signal ledger remains deterministic and operator-visible.

Do **not** add a hidden memory layer, semantic auto-merge engine, or background ontology worker in this phase. This should spend existing truth/search/provenance data on stricter lookup semantics and clearer backlink explanation.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, current `truth_api.py`, `ui/view_models.py`, `commands/ui_server.py`, existing note/object/topic/event/provenance surfaces, pytest.

**Status:** Planned, but gated on `Phase 25`

## Status Note

`Phase 24` remains the right semantic follow-up inside `Milestone 7`, but it should no longer be the next execution slice.

The current runtime still behaves too much like a black box:

- operators cannot trust current workflow state from one source,
- long-running stages do not expose honest progress semantics,
- the product still needs transaction + event log + process table stitching to explain what is happening now.

Because of that, `Phase 25: Observable Runtime And Run Ledger` should land first.

This phase should begin only after runtime observability is strong enough that:

- active vs stale vs blocked runs are unambiguous,
- counted progress exists where real work-unit denominators exist,
- and operator surfaces are no longer inferring state from side effects.

## Why This Is The Right Next Phase

`Phase 22` and `Phase 23` made the signal loop legible:

- the operator can now see signal lifecycle,
- downstream impact is visible,
- inbound capture is visible.

But one important part of `Milestone 7` is still weak:

- when the system creates or suggests downstream knowledge, it is still too easy for the product to feel like it is inventing new objects before exhausting what the existing vault already knows,
- and when a new object/link does appear, the backlink trail is still not explicit enough for an operator to judge whether this was a justified reuse vs a likely duplicate.

That means the next step is **not** another intelligence layer. It is a tightening step:

- **brain-first lookup before creation**
- **backlink legibility after creation**

## Product Thesis

After `Phase 24`, an operator should be able to answer three questions from the product itself:

1. Did the system check existing knowledge before suggesting or creating a downstream object/link?
2. Was the result a reuse of known knowledge, a candidate creation, or an unresolved gap?
3. What backlink trail now connects the triggering note/deep dive to the downstream object/topic/Atlas surface?

This phase is therefore about:

- **lookup provenance**
- **reuse-vs-create semantics**
- **backlink visibility on the product surface**

## What Phase 24 Should Deliver

### 1. Brain-First Lookup Contract v1

`truth_api.py` should expose a deterministic lookup contract for note/object/topic production views.

The contract should make visible:

- whether an existing object/topic/Atlas target was found,
- whether the result is an explicit reuse,
- whether only a candidate match was found,
- whether no usable existing target was found,
- whether creation is still pending review.

Minimum fields:

- `lookup_status`
- `lookup_summary`
- `matched_object_count`
- `matched_topic_count`
- `matched_atlas_count`
- `candidate_match_count`
- `creation_needed`
- `review_needed`

### 2. Reuse-vs-Create Compiled Section

`note/page`, `object/page`, and `production/browser` should gain a stable compiled section that explains:

- what existing knowledge was considered,
- what was reused,
- what was not reused,
- what downstream item still appears to need creation or review.

This section should not pretend to be a general semantic search engine explanation. It should stay narrow and operational.

### 3. Backlink Legibility Contract v1

The UI should make backlink consequences explicit for new or candidate downstream knowledge.

Minimum visible questions:

- which source note/deep dive points into this object,
- whether the backlink is already present,
- whether the backlink is only implied by runtime truth,
- whether a downstream page is missing expected backlinks.

Minimum fields:

- `backlink_status`
- `backlink_summary`
- `source_backlink_count`
- `deep_dive_backlink_count`
- `atlas_backlink_count`
- `missing_backlink_count`
- `missing_backlink_targets`

### 4. Signal And Briefing Spending

`/signals` and `/briefing` should spend these new contracts selectively:

- only when a signal is truly about downstream knowledge creation/linking,
- only when the new lookup/backlink explanation materially helps the operator decide what to do next.

This phase should not flood the shell with more cards.

## What Phase 24 Intentionally Does Not Do

Explicit deferrals:

- temporal truth
- memory backends
- benchmark/eval framework
- new graph workspace modes
- generalized background autonomous linking

## Exit Condition

`Phase 24` is complete when all of the following are true:

1. the product can explain whether existing vault knowledge was checked before downstream creation/linking,
2. note/object/production surfaces expose stable reuse-vs-create compiled sections,
3. backlink status is visible enough to spot missing downstream linkage without opening raw Markdown,
4. `signals` and `briefing` only surface the new explanation where it changes operator action,
5. focused tests lock the new lookup/backlink contracts and rendering behavior.

## Planned Execution Sequence

1. Add lookup-provenance contract fields in `truth_api.py`
2. Spend them on `note/page`, `object/page`, and `production/browser`
3. Add backlink-legibility contract fields
4. Spend them on the same surfaces plus `signals` / `briefing` where useful
5. Close out `Milestone 7` if the remaining active-signal-loop exit condition is satisfied

## Closeout Target

If `Phase 24` lands cleanly, `Milestone 7` should be ready to close.

What should still remain **out of scope** even after this phase:

- temporal-truth modeling
- harness/session memory
- benchmark-driven memory evaluation
- broader autonomous intelligence loops
