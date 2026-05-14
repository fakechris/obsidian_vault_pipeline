# Operational Lifecycle (M24.1)

> **Status**: design lock, 2026-05-14.  This is the vocabulary M24
> (Lifecycle Contract Layer) and M25 (Maintainer Control Plane)
> build against.  Anything that disagrees with this doc — code,
> UI labels, prompts, registry rows — is wrong and gets fixed.

## Why this document exists

The M24.0 event-evidence registry (PR #229) collapsed three drifted
intake allowlists into one.  It classifies *evidence*: when an audit
row of type `T` lands, what bucket does that row belong to?

That solved a real bug — same day reading 27 / 7 / many across three
surfaces — but it did **not** answer:

* What is the current state of *this specific source* / *this specific
  evergreen* / *this specific cluster*?
* If a card reads "0 Synthesized today", is that because (a) synthesis
  didn't run, (b) it ran and emitted nothing, or (c) the producer
  exists but the audit event is missing?
* When the operator clicks a card, which underlying items are they
  drilling into — events, or *the things events describe*?

Those questions are about **lifecycle truth**, not evidence
classification.  This document defines the lifecycle vocabulary.
M24.1 wires it into a kernel (`ops_lifecycle.py`).  M24.2 fixes the
producer audit so the kernel has clean evidence to read.  M25 builds
the Maintainer Control Plane on top.

## The five visible states (Maintainer Control Plane vocabulary)

These are the states the Maintainer surface in M25 will name.  Every
operator-visible item — a source, an evergreen, a cluster — sits in
exactly one of these five states at any moment.  The labels are
locked; M25 designs the cards around them, M24 builds the kernel that
derives them.

### 1. Received

> *"Something landed in the vault."*

Raw material entered, but no extraction has been attempted yet.
Concretely: a markdown file in `50-Inbox/01-Raw/`, a clippings batch
member that was split, or a source-authority entry pulled by an
intake processor — *before* the interpret/absorb stage runs on it.

**Evidence that puts an item here:** intake-category audit rows
(`article_intake_only`, `source_staged_for_processing`,
`clippings_processed`, `github_intake_completed`, …) where the source
has not yet emitted any absorb-stage event.

**Visible to operator:** yes (primary card).

### 2. Extracted

> *"The interpreter ran and produced something the absorber will read."*

The interpret/absorb stage has emitted at least one structured artifact
(a deep-dive article, a parsed `evergreen_candidate` row, a routed
absorb decision) for the source — but no *accepted* canonical
artifact exists yet.  Equivalent to the operator's mental model of
"we tried to understand it; the next step is keep / merge / reject".

**Evidence:** absorb-category audit rows (`absorb_route_decision`,
`evergreen_extraction_complete`, `candidates_upserted`,
`absorb_completed`) without a downstream `evergreen_auto_promoted`
or operator promote action against the same source/object.

**Visible to operator:** yes (primary card; this is where the
review-queue work lives).

### 3. Accepted

> *"A canonical artifact exists in the vault for this item."*

An evergreen has been promoted (auto or by operator), or the source
has been archived to `03-Processed` because every extracted artifact
reached a canonical form.  The item is no longer a candidate; it
exists in `10-Knowledge/Evergreen/` and is reachable from MOCs / the
graph.

**Evidence:** `evergreen_auto_promoted`, `promote_concept`,
`source_archived_to_processed` (after intake completed), or a
canonical-write event in the registry projection.

**Visible to operator:** yes (primary card).

### 4. Synthesized

> *"A higher-order artifact summarising / connecting this item exists
> and is fresh."*

A community crystal, contradiction crystal, MOC update, or other
synthesis-stage artifact references the item *and* is not stale
relative to the item's most recent canonical revision.  "Stale" is
defined the way M23's digest already defines it: `MAX(revision.derived_at
WHERE cluster_id = C) > crystal.synthesized_at`.

**Evidence:** `community_crystal_synthesized`,
`contradiction_crystal_synthesized`, `moc_updated`,
`moc_update_complete` — *plus* the freshness check against the
item's revisions.

**Visible to operator:** yes (primary card).

### 5. Needs Action

> *"Something is wrong, blocked, or waiting on the operator."*

Failure evidence is the most obvious source — `absorb_parse_error`,
`broken_link`, `pipeline_partial_failure`, `command_timeout` — but
the bucket also covers *governance-required* items: contradictions
without a resolution, candidates aged past a review SLA, evergreens
flagged for human review.

**Evidence:** failures-category rows, governance-category rows that
imply an open ask (`candidate_review_action` with action=`needs_review`,
unresolved contradictions, …).

**Visible to operator:** yes (primary card; this is the surface the
operator should be able to scan first thing in the morning).

## The two internal evidence states (not cards)

These exist in the kernel so it can reason about lifecycle precisely,
but they never become Maintainer Control Plane cards.  They are
debugging vocabulary, not workflow vocabulary.

### A. Prepared

> *"The producer believes it has done its work, but no canonical
> writer has consumed the output yet."*

Example: `evergreen_extraction_complete` fired, but the
`candidates_upserted` row that should follow is missing.  In the
five-state model this still appears as "Extracted" externally; the
**Prepared** sub-state lets the kernel say *"the producer thinks it
finished, the downstream stage hasn't picked it up yet"* — useful for
M24.2 producer-audit diagnostics, useless on a Maintainer card.

### B. Projected

> *"A derived/projected row claims the item reached state X, but the
> primary evidence we'd expect to find is missing."*

Example: the registry projection has a row saying an evergreen exists
at `10-Knowledge/Evergreen/Foo.md`, but `audit_events` shows no
`evergreen_auto_promoted` for that path.  Either the audit row was
never written (M24.2 producer gap) or the projection was written by
a path the kernel doesn't know about (likely a manual edit / vault
hand-touch).

**Projected** lets the kernel surface honest disagreements between
the evidence ledger and the derived projections — without those
disagreements bleeding into the operator's card view.

## Relationship to M24.0's five evidence categories

The M24.0 registry's categories (`intake`, `absorb`, `synthesis`,
`governance`, `failures`) are **how each audit row is classified**.
The five visible states above are **what an item's current position
in the workflow is**.

The mapping isn't 1:1:

| Evidence category | Contributes to lifecycle state |
|---|---|
| `intake`     | Received (always); Accepted via `source_archived_to_processed` |
| `absorb`     | Extracted (always); Accepted via `evergreen_auto_promoted` |
| `synthesis`  | Synthesized (when fresh); otherwise no state change |
| `governance` | Accepted (via `promote_concept`), Needs Action (via review actions) |
| `failures`   | Needs Action (always) |

Two implications:

* A single audit row can transition an item across states (a
  `promote_concept` row moves an item from Extracted → Accepted).
* The kernel must derive lifecycle state from the **set** of evidence
  rows about an item, not from any single row.  This is why M24.1
  needs a kernel module rather than a per-event classifier.

## Honest-zero principle

When any surface (digest layer, ops card, drilldown) reads a zero
count, the displayed text must make the **ambiguity of zero** legible:

> 0 Synthesized today
> *may mean: synthesis didn't run · ran with no output · instrumentation gap*

The kernel makes this honest by separating:

* **Zero with evidence of a run** — producer emitted "completed" but
  no per-item rows.  Surface as "ran with no output".
* **Zero with no run evidence** — no producer-level audit row.
  Surface as "didn't run today".
* **Zero with run evidence but a known producer gap** — M24.2
  audit identifies the missing producer.  Surface as
  "instrumentation gap".

M24 builds the first two; M24.2 unlocks the third.  Until then the
default surface message is the conservative "may mean: not run · no
output · missing instrumentation", which is honest but uninformative
— better than fabricated diagnosis.

## What this document does **not** decide

* **Card labels.** Today's Maintainer surface uses `Absorb /
  Governance / Synthesis`.  Those labels stay through M24.  M25
  renames them to the five-state vocabulary above; that rename is
  the M25 scope, not M24.
* **Producer-audit results.** Which producers emit which audit rows,
  and where the gaps are, is M24.2's deliverable.
* **`/ops/items` URL contract.** M25 designs `/ops/items` as the
  primary item-drilldown route.  M24 keeps `/ops/events` as the
  drilldown surface (M24.0 stop-gap behavior) and adds the
  honest-zero footer.

## Glossary

* **Item** — the unit a Maintainer card counts.  Usually a source,
  an evergreen, or a cluster.  Never an audit row.
* **Evidence row** — an `audit_events` row.  Classified by category
  via `event_evidence_registry` (M24.0); contributes to lifecycle
  state via `ops_lifecycle` (M24.1).
* **Projection** — a derived row in `knowledge.db` (e.g. an
  `objects` row, a `truth_projection` row) that summarises evidence.
  Projections can disagree with evidence; **Projected** captures
  exactly that disagreement.
* **Producer** — any module that writes audit rows.  Auditing them
  is M24.2's scope.
