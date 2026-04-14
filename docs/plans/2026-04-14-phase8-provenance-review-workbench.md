# Phase 8: Provenance Review Workbench

**Goal:** Turn the current DB browser into a provenance-aware knowledge workbench. Users should be able to move from truth rows to the originating Markdown world without guessing, and the review surfaces should expose enough context to act on contradictions and timeline entries.

## Current Gap

The system already has:

- DB-backed object/topic/event/contradiction views
- Atlas / Deep Dive bridge pages
- Markdown note rendering with smart wikilinks

But the product still feels split:

- `/events` shows dated objects, but not where they came from
- `/contradictions` shows conflict rows, but not the source notes or MOCs that contextualize them
- Atlas and Deep Dive pages list links, but do not yet summarize what each source page is contributing
- The user still has to manually connect DB rows back to Evergreen, deep dives, and Atlas notes

## Scope

### Slice A: Provenance-Aware Events

Make `/events` and `/api/events` carry provenance:

- object page link
- Evergreen markdown link
- source deep dive links
- Atlas / MOC links

The page should make it obvious that events are timeline entries over objects, and give a direct path back to source notes.

### Slice B: Provenance-Aware Contradictions

Make `/contradictions` and `/api/contradictions` carry workbench context:

- related object links
- source deep dive links
- Atlas / MOC links
- resolution metadata when present

This is still read-only, but it should feel like a review queue, not just a table dump.

### Slice C: Richer Atlas / Deep Dive Browsers

Upgrade `/atlas` and `/deep-dives` from flat membership lists into source summaries:

- note title + note link
- object count
- first derived/member objects inline
- short summary of “what this page contributes”

### Slice D: Model Hardening

Clarify and harden:

- event rows vs event objects
- contradiction detection limits
- UI copy so users understand why contradiction counts may be zero

## Non-Goals

- No write actions from the UI yet
- No full event ontology rebuild
- No LLM-driven contradiction detection changes in this slice

## Milestone Definition

Phase 8 reaches its first stable checkpoint when:

1. `/events` and `/contradictions` expose provenance that links back to Markdown sources.
2. Atlas and Deep Dive pages show enough summary context to explain how they relate to indexed objects.
3. Users can navigate DB truth rows back to Evergreen, source notes, and Atlas pages without manual filesystem lookup.

## Execution Order

1. Add failing tests for provenance-aware event and contradiction payloads.
2. Implement truth/view-model support for provenance batching.
3. Render richer event and contradiction cards in `ovp-ui`.
4. Enrich Atlas / Deep Dive browsers with counts and summary context.
5. Re-run targeted UI suites and then the full pytest suite.
