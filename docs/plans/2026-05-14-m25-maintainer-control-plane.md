# M25 — Maintainer Control Plane

> **Status**: design lock, 2026-05-14.  This plan completes the
> 4-6 week pipeline-coherence overhaul that M24 started.  M24
> rebuilt the kernel + projection (ops_lifecycle + ops_state) and
> made every surface honest about zeros; M25 takes the operator
> from "two coexisting surfaces I have to mentally reconcile"
> (event-window cards + lifecycle-state strip) to a **single
> coherent Maintainer Control Plane** keyed on lifecycle state.

## Context

After M24, ``/ops/today`` has two orthogonal surfaces that count
the same vault from two different angles:

* The 5 **event cards** (Intake / Absorb / Synthesis / Governance
  / Failures) count *audit-event rows* in a date window — "what
  happened today".  Lives on top of ``event_evidence_registry``.
* The **lifecycle backlog strip** counts *items by current state*
  (Received / Extracted / Accepted / Synthesized / NeedsAction)
  from ``ops_state``.  Lives on top of ``ops_lifecycle``.

The PR notes on M24.4 explicitly called this out as a transitional
state — "M25 will rename the cards onto the lifecycle vocabulary
deliberately; M24 leaves the swap unfired."  Two surfaces on the
same page is exactly the kind of split the operator complained
about ("Article 7" lands in a raw event dump whose organisation
doesn't match the card I clicked).

M25 collapses the two surfaces into one set of five cards keyed on
the lifecycle vocabulary, with today's evidence shown as
*secondary* activity context — not as the primary count.

---

## Card contract (locked)

Each Maintainer card on ``/ops/today`` after M25 lands has this
shape:

```text
┌─ Received ──────────────────────────────┐
│ 47 current items                        │  ← primary, from ops_state
│ 5 arrived today                         │  ← secondary, from audit_events
│                                         │
│ How memory agents decay over time       │  ← samples from ops_state items
│ Karpathy: Software Is Changing          │
│ Self-evolving skill libraries           │
│                                         │
│ Open 47 items →                         │  ← primary CTA → /ops/items
│ View today's 5 evidence events →        │  ← secondary CTA → /ops/events
└─────────────────────────────────────────┘
```

| Field | Source | Meaning |
|---|---|---|
| Label | lifecycle vocabulary | one of ``Received / Extracted / Accepted / Synthesized / Needs Action`` |
| **Primary number** | ``ops_state.counts_from_projection`` | items currently in this state |
| **Secondary activity** | ``event_evidence_registry`` + ``audit_events`` | evidence events for this state in the operator's date window |
| Samples | ``ops_state`` rows | newest items in this state — **not** event rows |
| Primary CTA | ``/ops/items?state=…`` | drill into the items themselves |
| Secondary CTA | ``/ops/events?event_types=…&date=…`` | drill into the raw audit evidence (forensic) |
| Empty state | ``ops_honest_zero`` | M24.3 ambiguity messaging when both numbers are 0 |

### Hard locks on phrasing

The two numbers are **not additive** — current-state count and
event-window count measure different sets.  A source can be
Received today and Extracted by lunch; it shows up in *today's
evidence* but no longer in the *current* Received bucket.

Phrasing that pretends they're the same number is forbidden:

* **Wrong** — ``47 +5 today`` (implies 47 + 5 = 52)
* **Wrong** — ``52 (47 baseline + 5 today)``
* **Right** — ``47 current items`` / ``5 arrived today``
* **Right** when uncertain — ``5 evidence events today``

Per-state secondary verbs (default; fall back to the conservative
"N evidence events today" if the kernel can't establish the more
specific framing):

| State | Secondary text |
|---|---|
| Received | ``5 arrived today`` |
| Extracted | ``3 extracted today`` |
| Accepted | ``2 accepted today`` |
| Synthesized | ``1 synthesized today`` |
| Needs Action | ``2 new blockers today`` |

### Samples come from items, not events

Samples on the card must reference the *items* in the state, not
the *events* about them.  Otherwise the primary number says
"47 items" but the visible rows are picked from event timestamps
— exactly the M23-era confusion this plan exists to remove.

When ``ops_state`` is missing rows (projection not yet built), the
samples row degrades to a single-line "ops_state projection not
built yet — run ``ovp-ops-state --rebuild``" message.  No fallback
to event samples; that would resurrect the two-ledger problem.

### Empty-state copy

When both numbers are 0, the card writes the M24.3 honest-zero
message.  When *primary is 0 but secondary > 0* — i.e. today moved
items through this state but no items are currently sitting in it
— the copy must explain that:

```text
Received
0 current items
5 arrived today

No current Received backlog.
5 items arrived today and moved onward.
```

The "moved onward" framing is only safe when the kernel can prove
each of the 5 sources crossed into a downstream state.  When the
kernel can't prove it (e.g. ``absorb_pending_upsert`` exists but
no matching ``candidates_upserted``), the conservative copy is:

```text
No current Received backlog.
5 Received evidence events today.
```

## What this plan rejects

The decision discussion turned up three alternative shapes; the
plan rejects each explicitly:

1. **State-count cards only** (drop the event count from cards
   entirely).  Cleanest semantically but loses the "today moved"
   signal from the dashboard; ``/ops/today`` would degrade to a
   backlog snapshot.
2. **Two card rows** (one for backlog, one for today's activity).
   Doubles the visual footprint and permanently codifies the
   exact two-surface confusion M25 exists to remove.
3. **Renaming cards in place** — keep them event-windowed,
   relabel them to the lifecycle vocabulary.  Worst option: the
   labels would lie about what the number actually counts.

## Hard locks across M25

These apply to every M25 PR.  A change that violates one is a
review block, not a discussion.

* **No new lifecycle states.**  Five visible (Received,
  Extracted, Accepted, Synthesized, Needs Action) plus two
  internal sub-states (Prepared, Projected).  Adding a sixth
  visible state requires a separate scoping doc.
* **Prepared / Projected stay internal.**  They never become
  cards; ``ovp-lifecycle-show`` is the operator-facing surface for
  them.
* **Cards read ``ops_state`` only for primary numbers.**
  Anything that touches ``/ops/today``'s primary count and reaches
  back to ``audit_events`` is a regression to the M23 era.
* **``/ops/events`` is forensic.**  It keeps its UI but loses its
  status as the primary drilldown from Maintainer cards.  M25.4
  adds an explicit banner saying so.
* **No "+N today" phrasing anywhere.**  The two numbers are
  parallel, not stacked.
* **Honest-zero stays.**  Every zero-count surface (card, item
  list, drilldown) still reads from ``ops_honest_zero``.

## Stages

### M25.1 — Plan doc (this PR)

* This file + entries in MEMORY.md if needed.
* No code changes.

### M25.2 — ``/ops/items`` minimal route

Build the route the card primary CTAs need.  Without it, the
hybrid card has nowhere to go.

* New route ``/ops/items?state=<state>[&date=<YYYY-MM-DD>]``.
* Backed by a new ``build_items_list_payload`` in
  ``view_models.py`` that reads ``ops_state``.
* Columns: title, item_kind, state, sub_state (when set),
  last_evidence_at, top-3 evidence event_types, primary link
  (``/note?path=…`` for sources, ``/object?id=…`` for objects,
  ``/ops/cluster?id=…`` for clusters).
* Pagination — first page caps at 50 rows; older rows paginate.
* No actions in v1 (no inline "promote", "synthesize").  Drilldown
  page; actions land in M25.5 if useful.
* Tests for the four read paths (state filter, date filter,
  pagination, empty-state).

### M25.3 — ``/ops/today`` rewrite to hybrid cards

* Card label: replace existing event-category labels with the
  lifecycle vocabulary.
* Primary number reads ``ops_state.counts_from_projection``.
* Secondary number reads the existing audit-event query (already
  in ``build_today_digest_payload``) — keep that code, demote its
  role.
* Primary CTA: ``/ops/items?state=…&date=…``.
* Secondary CTA: keep the existing ``/ops/events?event_types=…``
  link with the new "View today's N evidence events →" label.
* Drop the M24.4 standalone lifecycle backlog strip — the cards
  now carry the same info.
* Tests cover: rename, primary/secondary number split, primary
  CTA pointing at ``/ops/items``, samples coming from ops_state.

### M25.4 — ``/ops/events`` forensic banner

* Add an explanatory banner at the top of ``/ops/events`` (the
  existing event dossier) that names its role:
  > Forensic audit evidence.  This page shows raw audit-event
  > rows; it is **not** the operational-state surface.  Use
  > [/ops/items] to see current lifecycle state and act on items.
* No structural changes to the page beneath.  The cross-surface
  warning banner from M24.0 stays (operators landing here from
  a stale link still get the explanation).
* Tests for the banner presence + link target.

### M25.5 — Digest / calendar alignment

* Daily digest layer headings already use the M24 vocabulary
  implicitly; double-check no operator-visible text still uses
  "Absorb" / "Synthesis" / etc. as state labels (event categories
  in registry can keep their names — those are *evidence
  classifications*, not state names).
* ``/digests`` calendar legend already honest-zero-corrected in
  M24.3.  No structural change needed; cover with a regression
  test that asserts the legend's wording stays aligned with the
  M25 vocabulary.

## Out of scope (deferred)

* **``/ops`` cockpit redesign.**  ``/ops`` today is a static
  dashboard; M25 keeps it but doesn't restructure.  Any operator
  who currently lands there sees the same content; the M25 cards
  live under ``/ops/today``.
* **Inline actions on ``/ops/items``** (promote/synthesize
  buttons).  M25.2 builds the read-only drilldown.  Actions are a
  separate scoping pass.
* **Option B clean-room rebuild** of ``audit_events``.  Decision
  deferred until real-vault data from ``ovp-producer-audit``
  shows whether the M24.2 producer fixes closed the gaps.  M25
  doesn't pre-commit either way.
* **Cross-pack item view.**  Items remain pack-scoped; the
  ``?pack=`` query param feeds through ``/ops/items`` the same way
  it already does on ``/ops/today``.

## Open issues (to flag during implementation)

* **Item samples ordering.**  ``ops_state`` rows have
  ``last_evidence_at``; the card samples should be newest-first
  for most states but for **Needs Action** the operator probably
  cares about oldest-blocker-first.  Lock per-state ordering
  during M25.3.
* **Sub-state surfacing.**  Prepared / Projected sub-states stay
  off the cards but should appear on the ``/ops/items`` rows so
  the operator can see them inline.  Lock the visual treatment
  during M25.2 review (probably a muted pill next to the state).
* **``Needs Action`` severity.**  Today every failure-category
  event is equally weighted.  M24.2's producer audit now writes
  ``absorb_pending_upsert`` etc.; some are operator-actionable
  immediately, others are diagnostic.  M25.2 surfaces them all,
  but the card sample order should put truly-blocked items first.
  Detail deferred to M25.2 implementation review.

## Acceptance checklist

* [ ] ``docs/plans/2026-05-14-m25-maintainer-control-plane.md``
      merged on main.
* [ ] ``/ops/items`` route + tests merged.
* [ ] ``/ops/today`` cards renamed + reshaped + tests merged.
* [ ] ``/ops/events`` forensic banner + tests merged.
* [ ] Digest / calendar alignment regression test merged.
* [ ] Full ``pytest`` stays green (modulo the two pre-existing
      architecture-fitness violations).
* [ ] Manual smoke: log into the operator vault, click each
      card, confirm the primary CTA lands on ``/ops/items`` and
      the row count matches the card primary number; click the
      secondary CTA, confirm the event count matches.

---

*Plan author: Claude (M25), with operator design feedback
incorporated 2026-05-14.  Constraints captured from the codex
consultation thread; review notes folded in inline rather than as
trailing footnotes.*
