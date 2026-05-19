# Maintainer Workflow — `/ops/*` Surface Guide

This is the operator-facing reference for the BL-053 Phase 2
maintainer UX. Each section below mirrors one of the `/ops/*`
surfaces and answers four questions:

- **Job to be done** — what the page exists to help you decide
- **Decision** — the buttons / inputs the page exposes and what each does
- **Consequence** — the downstream effects of clicking
- **When to come here** — the workflow situations that send you to this page

The page-help `<details>` banner at the top of each surface
reflects this same content; this doc is the source-of-truth and
the place to look when the inline banner is too terse.

---

## `/ops` — Maintainer Foyer

**Job to be done.** Answer "is anything broken right now?" in one
screen.

**Decision.** Three blocks, each with a deep-link:
- **Today** — ingested + failure counts for the current operator-local day → `/ops/today`
- **Queue** — pending counts across the four review queues → `/ops/queue`
- **Last run** — most recent transaction's workflow + status → `/ops/runs`

**Consequence.** Read-only summary; clicks take you to the source
pages. Counts come from the same builders the source pages use,
so the foyer never goes stale.

**When to come here.** Every morning, after each pipeline run,
and any time you want a 10-second status check.

---

## `/ops/today` — Today digest

**Job to be done.** See what the pipeline did today, grouped by
macro-stage so you can spot regressions in any one stage quickly.

**Decision.** Five cards (intake / absorb / synthesis / governance
/ failures) with totals + top event types + a sample of recent
events. Each card has:
- **See all N →** — drops into `/ops/events?date=…` for the full audit list
- Prev / next-day pivots at the top to step through history

**Consequence.** Read-only. The audit-events ledger is queried
on every load.

**When to come here.** Daily standup; immediately after a
suspicious pipeline run; when triaging "what changed today?".

---

## `/ops/timeline` — Timeline

**Job to be done.** Day-by-day rollup over the last ~14 days for
multi-day pattern spotting (failure spikes, missing days, etc.).

**Decision.** Per-day cards with by-type histograms + sample
events. Each date heading and the **See all N →** link drop into
`/ops/events?date=…`.

**Consequence.** Read-only.

**When to come here.** Weekly review; verifying the cron actually
ran every day; correlating two events that aren't on the same day.

---

## `/ops/pulse` — Pulse

**Job to be done.** Watch the pipeline run in real time.

**Decision.** No interactive controls — purely a live tail of
`60-Logs/{pipeline,reuse,evidence,open-questions}.jsonl`, polled
once per second.

**Consequence.** Read-only. The poll only reads from disk.

**When to come here.** During an absorb / intake batch you
kicked off; while debugging why a pipeline run feels slow.

---

## `/ops/events` — Event dossier

**Job to be done.** Drill into individual audit events tied to
the truth objects they touched.

**Decision.**
- Free-text filter (`?q=`)
- Single-day filter (`?date=YYYY-MM-DD`) or range (`?from_date=` + `?to_date=`)
- Per-page limit (25 / 50 / 100 / 200)
- Quick-Maintenance card at the bottom exposes resolve / queue
  summary rebuild — these mutate the truth store; the buttons
  carry their own consequence text

**Consequence.** The browse view itself is read-only. The Quick
Maintenance buttons mutate (resolve writes a contradiction
outcome; rebuild summary kicks the compile queue). All button
effects are explained inline.

**When to come here.** From a card on `/ops/today` or `/ops/timeline`
when you want every row, not just the sample. Or when you have
a slug from elsewhere and want its event history.

---

## `/ops/objects` — Objects

**Job to be done.** Browse every canonical Evergreen object in
the pack-scoped truth store.

**Decision.**
- Filter by text
- Sort: alpha (default) or most-linked
- Pagination (10 / 50 / 100 / 200 per page)

**Consequence.** Read-only browser. Mutations live on the
per-object page (`/object?id=…`), and on
`/ops/queue/contradictions` for contradiction resolution.

**When to come here.** Auditing the global pack-scoped object set;
finding the most-linked objects (canon-rich neighborhoods);
looking up an object whose slug you don't fully remember.

---

## `/ops/clusters` — Graph clusters

**Job to be done.** Browse pack-scoped graph clusters
(neighborhoods of related objects).

**Decision.**
- Per-page (15 / 50 / 200)
- **Show all** — drops the limit so you can audit the full set
- Free-text filter

**Consequence.** Read-only. Cluster scoring rebuilds when you
regenerate the graph index, not on click.

**When to come here.** When asking "which neighborhoods are
contradiction-heavy?" or "what's the largest cluster in this
pack?" Each row links to a cluster detail page with member
objects and structural label.

---

## `/ops/runs` — Runs

**Job to be done.** See every pipeline transaction grouped by
calendar day, with status + event count.

**Decision.**
- Window pivot (Last 10 / 30 / 100)
- Click any `txn_id` for the per-run event timeline
- `Idle` markers show days the pipeline did not run

**Consequence.** Read-only. Per-run drill-down is also
read-only.

**When to come here.** Triage ("when was the last successful
run?"); incident postmortem; verifying the cron actually ran
every day.

---

## `/ops/queue` — Maintainer queue (overview)

**Job to be done.** Tell at a glance whether triage is done.

**Decision.** Lists pending counts across the four review queues
(concept candidates, contradictions, signals waiting, actions
failed/blocked) with the oldest-row hint per queue. Empty queues
move to the **Healthy (no action needed)** card.

**Consequence.** Pure aggregator — counts come from the same
builders the four detail pages use.

**When to come here.** Every triage session start. Use this to
decide which sub-page to open first.

---

## `/ops/queue/concepts` — Concept candidates

**Job to be done.** Promote, merge, or reject absorb-pipeline
concept proposals.

**Decision.** Per-row buttons:
- **Promote** — creates an Evergreen note from the candidate
- **Merge** — rewrites links into an existing object (target
  slug is required; the form pre-fills the highest-similarity
  match if one exceeds the auto-fill threshold)
- **Reject** — drops the candidate as spurious or duplicate

**Consequence.** Promote and Merge mutate the truth store
(objects, relations) and trigger a re-index. Reject only marks
the candidate as resolved. All three are reversible by re-running
the absorb step on the source.

**When to come here.** After every absorb run; when a signal of
type `concept_candidate_proposed` appears in the queue overview.

---

## `/ops/queue/contradictions` — Contradictions

**Job to be done.** Resolve open contradictions detected over
pack-scoped truth.

**Decision.** Per-row status options:
- `resolved_keep_positive` — positive claims are canonical;
  negative side is superseded
- `resolved_keep_negative` — mirror image
- `dismissed` — false alarm; nothing else changes
- `needs_human` — leave open for deeper review
- Optional: `rebuild summaries` checkbox kicks the compile queue

**Consequence.** Keep-positive / keep-negative tag the rejected
claims as superseded and trigger a downstream summary recompile
(if the box is checked). Dismissed only updates the contradiction
row.

**When to come here.** When `/ops/queue` shows pending
contradictions; before any major synthesis run (avoid compiling
contradictions into a brief).

---

## `/ops/queue/signals` — Signals

**Job to be done.** Inspect detected-but-not-acted-on
observations the pipeline has accumulated.

**Decision.** Filter by signal type or status (productive /
waiting / failed/stalled). Per-row buttons:
- **Queue action** — sends the recommended command to the action
  worker; until the worker runs it, nothing else changes
- **Dismiss** — tags the signal as not worth acting on; row +
  evidence stay live

**Consequence.** Queueing adds a row to `/ops/queue/actions` —
the action worker runs queued items on its next cycle. Dismiss
only updates the signal ledger.

**When to come here.** After a backfill or governance-rule
change; when `/ops/queue` shows N signals waiting.

---

## `/ops/queue/actions` — Action queue

**Job to be done.** Run, retry, or dismiss queued worker tasks.

**Decision.**
- **Run next** — dequeues a single item to the worker
- **Run batch** — processes up to 5 in one pass
- **Retry** (per failed row) — requeues the action
- **Dismiss** (per row) — removes from the queue without running

Items get here from `/ops/queue/signals` Queue-action,
periodic pipeline jobs (e.g. backfill cron), or manual enqueue
via the CLI.

**Consequence.** Run / Retry actually executes the queued
command. Effects depend on the action — may mutate the truth
store, the vault, or external services. Dismiss only marks the
row as dismissed.

**When to come here.** When `/ops/queue` shows actions
failed/blocked; after queueing one or more signals; daily, to
clear successful runs.

---

## Legacy redirects

For backwards-compatibility, the four bare paths 301 to their
canonical `/ops/queue/<sub>` form (preserving the query string):

| Legacy path             | Canonical path               |
|-------------------------|------------------------------|
| `/ops/candidates`       | `/ops/queue/concepts`        |
| `/ops/contradictions`   | `/ops/queue/contradictions`  |
| `/ops/signals`          | `/ops/queue/signals`         |
| `/ops/actions`          | `/ops/queue/actions`         |

Fragment paths (`/ops/candidates/fragment`,
`/ops/actions/fragment`) and form-POST endpoints
(`/ops/contradictions/resolve`, `/ops/actions/enqueue`,
`/ops/actions/retry`, `/ops/actions/dismiss`,
`/ops/actions/run-next`, `/ops/actions/run-batch`,
`/ops/candidates/review`) keep their old paths so the workbench
iframes and form submitters don't churn.

`/ops/deep-dives` (and `/api/deep-dives`) 301 to `/ops/today`
since BL-029 removed the deep-dive producer.  See
[`docs/page-ia-post-bl029.md`](page-ia-post-bl029.md) for the
full post-deep-dive page IA — what each `/object`, `/topic`,
`/note`, and `/ops/*` surface is for and how the new pipeline
chain (Source URL → Source File → Pipeline Stages → Evergreen)
maps onto them.
