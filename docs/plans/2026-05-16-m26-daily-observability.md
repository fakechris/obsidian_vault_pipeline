# M26 — Daily Observability And Explainable Zeros

> **Status**: ✅ COMPLETE — shipped 2026-05-16.  All of BL-100…109
> merged to `main` (PRs #251, #252, #253, #254, #255, #256, #258,
> #259, #260); issue #250 closed by #259.  Dogfooded on the operator
> vault; the three date-axes (Activity / intake cohort / workflow
> progress) reconcile.  Architecture-hygiene tail (mega-file split,
> sqlite boundary) is deliberately OUT of M26 scope → tracked as
> milestone **M27** (BL-110/111/112).
>
> **Original status**: backlog lock, 2026-05-16.
>
> **Problem**: `/ops/today` currently renders what its SQL asks for,
> but the product meaning is wrong.  The Activity cards count raw
> `audit_events` rows grouped by `date(timestamp)`, while the labels
> read like item-level state movement.  A zero means only "no evidence
> row in this bucket"; it does not tell the operator whether the stage
> did not run, ran with no input, ran with no output, failed, or failed
> to emit telemetry.

## Observed Failure

The live vault day-by-day audit-event table is internally consistent:

```text
date   Received  Extracted  Accepted  Synthesized  NeedsAction
05-10       104         16        23           1            2
05-11        27         38         0           1            4
05-12         9          0         0           1            1
05-13        58          0         0           0            0
05-14         2          0         0           0            1
05-15        48          0         0           0            2
05-16         0         33         0           3            0
```

But the table answers the forensic question:

> "How many evidence rows of this category were recorded on this day?"

The operator is asking product questions:

* "How many items entered the system today?"
* "Which of today's intakes have moved forward?"
* "Did absorb run, and if not, why?"
* "Did synthesis not run, run with no input, or run and produce no output?"
* "Is this zero healthy, stale, or instrumentation drift?"

Those questions require multiple time axes plus run-level evidence.

## Product Model

Daily surfaces must stop treating "daily" as one thing.  OVP needs
four distinct daily views:

| View | Primary question | Time axis | Data source |
| --- | --- | --- | --- |
| **Activity** | What did the system do on this day? | event time | `audit_events`, normalized to operator-local day |
| **New Intake** | What content entered my vault on this day? | intake time | source-class audit identity + source metadata |
| **Workflow Progress** | Which items moved state on this day? | state transition time | lifecycle transition projection |
| **Current Backlog** | What is waiting right now? | current projection refresh time | `ops_state` |

`/ops/today` may show all four, but they must be visually separated.
Only Current Backlog is not date-driven.

The page also needs a single top-line verdict above the four zones:

```text
Pipeline healthy
16 items moved forward today · 0 blockers · audit/ops projections current
```

or:

```text
Status unknown
audit_events is stale relative to pipeline.jsonl; refresh before trusting daily counts
```

This is the "read in ten seconds" layer.  The four zones are the
explanation, not the first thing the operator must parse.

## Time Axes

The system should name these timestamps explicitly:

| Name | Meaning | Examples |
| --- | --- | --- |
| `event_time` | When an audit row was emitted | `article_intake_only`, `candidates_upserted` |
| `intake_time` | When a source first entered durable OVP intake | `source_staged_for_processing`, `article_processed` |
| `transition_time` | When an item crossed lifecycle state | Received -> Extracted, Extracted -> Accepted |
| `content_published_at` | When the external source claims it was published | article metadata, GitHub release date |
| `projection_refreshed_at` | When a derived view was rebuilt | `ops_state.refreshed_at` |

Default day grouping should be operator-local, not mixed
SQLite `date(timestamp)` over UTC-Z and naive-local strings.

The copy must explicitly relate the two intake-looking numbers:

* **Activity / Received**: sources with Received evidence on the
  selected event day.
* **New Intake**: sources whose durable intake time is the selected
  day, regardless of when later events fire.

They can differ because event day is not intake day.  The UI must say
that instead of making the operator infer it.

## Zero Reason Taxonomy

A zero count must be diagnosable.  The visible reason set should be:

| Reason | Meaning |
| --- | --- |
| `not_run` | No run record exists for that stage on that day |
| `ran_no_input` | Stage ran and found zero eligible inputs |
| `ran_no_output` | Stage ran inputs but produced zero outputs |
| `failed` | Stage failed before completing |
| `telemetry_missing` | Inputs/outputs imply activity but required stage events are missing |
| `audit_sync_stale` | JSONL has newer rows than `knowledge.db.audit_events` |
| `projection_stale` | `ops_state` / derived projection is older than the relevant audit rows |

Until the system can prove one of these, the UI must not display an
unqualified "nothing here" message.  It should say "No evidence
recorded; run status unknown."

## Backlog

| ID | Priority | Work item | Notes |
| --- | --- | --- | --- |
| BL-100 | P0 | **Daily vocabulary, hierarchy, and view contract** | Define `/ops/today` as one top-line health verdict plus four zones: Activity, New Intake, Workflow Progress, Current Backlog.  Document the time axes above and update page copy so users are not asked to infer them.  Explicitly explain why Activity / Received and New Intake can differ. |
| BL-101 | P0 | **Activity item-count semantics** | Change Activity cards from raw event counts to distinct item counts per state.  Identity depends on state: source slug for Received/Extracted, object id for Accepted, cluster id for Synthesized, blocker id for NeedsAction.  The secondary CTA still drills into raw evidence rows.  Add regression tests that compute the card count and the drilldown distinct-item count independently and assert equality. |
| BL-102 | P0 | **Timestamp normalization + pack scoping for daily cards** | Replace SQLite `date(timestamp)` bucketing with a shared parser that handles UTC-Z and naive-local audit rows, then groups by operator-local day.  Apply pack filtering consistently: matching payload pack included, different pack excluded, legacy pack-less rows included only under the default pack. |
| BL-103a | P0 | **Staleness zero reasons** | Ship the cheap zero reasons that need no new stage telemetry: `audit_sync_stale` and `projection_stale`.  Compare JSONL max timestamp / mtime, `knowledge.db.audit_events` max timestamp, and `ops_state.refreshed_at`.  This answers "is this number current?" before the heavier run-ledger retrofit lands. |
| BL-103b | P0 | **Stage run ledger and full zero reasons** | Add run-level telemetry for major stages: `stage_started`, `stage_input_counted`, `stage_output_counted`, `stage_completed`, `stage_failed`, `stage_skipped`, all carrying `stage`, `run_id`, `pack`, counts, and timestamps.  Materialize `ops_stage_runs` and attach the full zero taxonomy (`not_run`, `ran_no_input`, `ran_no_output`, `failed`, `telemetry_missing`) to `/ops/today`. |
| BL-104 | P1 | **Workflow Progress projection** | Materialize item-level state transitions by comparing lifecycle evidence over time.  `/ops/today` can then say "16 sources moved Received -> Extracted today" rather than "16 extraction evidence rows". |
| BL-105 | P0 | **Intake cohort view** | Add "Flow by intake day": for sources whose `intake_time` falls on day D, show current distribution across Received / Extracted / Accepted / Synthesized / NeedsAction, plus age and stalled counts.  This answers "what happened to the articles I saved that day?"  It can ship before the full stage-run ledger because it needs intake identity + current `ops_state`, both already exist after PR-B. |
| BL-106 | P1 | **Digest uses intake cohorts, not only event-day activity** | Feed M23 digest Layer 0/3 from New Intake + Intake Cohorts so a daily digest reflects articles saved that day even when absorb/synthesis happens later. |
| BL-107 | P2 | **Refresh-ops heavy-rebuild watermark** | Make `ovp-refresh-ops` idempotent for canonical evidence warnings by tracking the last successful projection/full rebuild watermark in a `knowledge.db` metadata row written only after a successful full/projection rebuild.  Do not use `ops_state.refreshed_at`; that can suppress a real canonical-change warning before the heavy rebuild has handled it.  Supersedes issue #250's initial `ops_state.refreshed_at` proposal. |
| BL-108 | P2 | **Streaming audit ingest/read path** | Replace remaining `read_text().splitlines()` audit JSONL reads with streaming iteration before daily rollups scale further.  This is performance hygiene, not a blocker for the semantic fixes. |
| BL-109 | P1 | **Defensive timestamp normalization for digest windows** | BL-102 analog for the M23 digest — cross-surface consistency infrastructure, not a visible bug fix.  Digest Layer 0 (and its preflight gate) filtered the window with a raw SQL `timestamp >= ? AND timestamp <= ?` string comparison over mixed UTC-`Z` ISO and naive-local `audit_events.timestamp`.  `_utc_iso`'s own docstring concedes this compare is "lexicographic, not time-correct"; it is a known bad pattern that will re-diverge `/ops/today` and Digest as formats drift.  Replace the SQL bound with the shared `audit_time.parse_audit_ts` + an in-Python window compare, exactly as `/ops/today`'s Activity / cohort paths do, so the digest and the daily surfaces bucket identically.  **Honest scope:** the 0-count observed while dogfooding BL-106 was traced to a backdated probe hitting the `last-successful-digest` window watermark, *not* this comparison; current vault data is uniformly `T`-form and happens not to trip the lexicographic hazard.  No expected visible change on the current operator vault — this is correctness hardening + M26/M23 semantic alignment so the daily model stays unified. |

## Implementation Sequence

1. **BL-100 first**: lock names and copy so the UI can stop lying even
   before the deeper projection exists.
2. **BL-101 + BL-102 together**: fix the current Activity numbers'
   semantics and the date/pack bugs in the same slice.
3. **BL-103a next**: stale audit / stale projection reasons are cheap
   and immediately explain the "can I trust this number?" case.
4. **BL-105 next**: ship the highest-value user story early — "I saved
   articles on Tuesday; where are they now?"
5. **BL-103b**: add the heavier stage-run ledger and the remaining
   zero reasons.
6. **BL-104**: add state-transition progress once item activity and
   cohort views are no longer raw-event based.
7. **BL-106**: wire the new daily model back into Digest.
8. **BL-109**: normalize the digest's own window timestamps (BL-102
   analog) — defensive cross-surface consistency so Digest and
   `/ops/today` keep one timestamp model.  Surfaced (mis-attributed
   then corrected) while dogfooding BL-106; no visible change on
   current data, but keeps the daily model from re-diverging.
9. **BL-107 / BL-108**: operational follow-ups; valuable, but not the
   core product gap.

## Non-Goals

* Do not add a generic "group by any timestamp" selector to the first
  version.  That is an analyst tool, not a daily product surface.
* Do not treat raw event count as an item count by changing the label
  only.  The product should answer item-level questions.
* Do not use `ops_state.refreshed_at` as a heavy-rebuild watermark.
  It can suppress a real canonical-change warning before a projection
  rebuild has actually handled it.  Issue #250's original
  `ops_state.refreshed_at` approach is superseded by BL-107.

## Success Criteria

* A zero on `/ops/today` always has one of the zero reasons above, or an
  explicit "run status unknown" fallback.
* The same day can be explained in plain language:
  "05-13 had 20 new intake sources; absorb did not run until 05-16,
  when 16 of them moved to Extracted."  The specific numbers come from
  distinct source identities, not raw event rows.
* Activity card count and drilldown distinct-item count agree by
  regression test: one test computes the card count and the drilldown's
  distinct item identities independently, then asserts equality for
  Received / Extracted / Accepted / Synthesized / NeedsAction.
* Digest can mention same-day saved articles without waiting for
  crystal synthesis.
