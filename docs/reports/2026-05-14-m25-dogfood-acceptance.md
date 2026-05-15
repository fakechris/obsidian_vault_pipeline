# M25 Dogfood Acceptance Report (template)

> **Status**: template, awaiting operator fill-in.  Run the smoke
> script against the operator vault and paste the JSON-table rows
> into the verification tables below.  Land the completed report
> on `main` as the formal close of the M24/M25 sequence.

## Purpose

M24/M25 unit tests proved the code is internally consistent (2926
passing).  This report proves the code is *externally honest*
against a real vault — the dogfood gate the M25 plan §M25.6
requires before declaring M25 shipped.

## How to run

```sh
# From the operator's vault clone:
python scripts/smoke_m25_control_plane.py \
    --vault-dir "$OPERATOR_VAULT" \
    --pack research-tech \
    --date $(date -u +%Y-%m-%d) \
    --out 60-Logs/m25-acceptance.json
```

The script:

* Runs `ovp-producer-audit` and surfaces missing must-emit
  producer events.
* Runs `ovp-ops-state --rebuild` so all numbers below come from
  a fresh projection.
* Walks every lifecycle state and verifies **card N === drilldown
  N** on both the primary (items) and secondary (audit-events)
  axes.
* Renders `/ops/events/audit` and `/ops/events` against the live
  payloads to confirm both role banners are present.

Exit code `0` means every check passed; `2` means at least one
failed and a row below should be marked `❌`.

## Verification table — copy from JSON

The `card_n_equals_drilldown_n_table` array in the JSON output
maps 1:1 to this table.  Paste row-by-row.

| State          | current items | today evidence | primary rows | audit rows | primary match | audit match |
|----------------|---------------|----------------|--------------|------------|---------------|-------------|
| Received       |               |                |              |            |               |             |
| Extracted      |               |                |              |            |               |             |
| Accepted       |               |                |              |            |               |             |
| Synthesized    |               |                |              |            |               |             |
| Needs Action   |               |                |              |            |               |             |

**Pass criterion:** every `primary_match` and `audit_match` cell
reads `True`.  A `False` is a card-N-vs-drilldown-N regression
that must be triaged before sign-off.

## Per-check acceptance

Tick each box once verified.

### producer_audit

* [ ] Ran `ovp-producer-audit` against the operator vault.
* [ ] `missing_count == 0` for hot-path producers
      (article processor, clippings, github intake, absorb
      router, evergreen extractor, promote command, community
      crystal synthesizer).
* [ ] No undeclared `unknown_event_types` (drift entries) the
      operator can't explain.

Notes — paste the JSON `data.findings` rows here if anything is
missing:

```
(paste here)
```

### ops_state_rebuild

* [ ] `ovp-ops-state --rebuild` completed without error.
* [ ] Per-state counts in the JSON `data.counts` block match
      what `/ops/today` shows (compare to the verification
      table above).
* [ ] Two consecutive rebuilds produced identical counts
      (idempotency check — re-run `--rebuild` once more and
      diff the JSON `data.counts`).

### card_n_equals_drilldown_n

* [ ] Five rows in the verification table.
* [ ] Every `primary_match` is `True`.
* [ ] Every `audit_match` is `True`.

### audit_page_banner

* [ ] `/ops/events/audit` carries the **Raw audit evidence**
      banner.
* [ ] The banner cross-links to `/ops/events`.

### dossier_reciprocal_banner

* [ ] `/ops/events` carries the **Timeline projection view**
      banner.
* [ ] The banner cross-links to `/ops/events/audit`.

## Manual UI checks

The script proves the data layer.  These checks prove the
UI surfaces match the data layer in the operator's actual
browser.

| Surface                              | Pass? | Notes |
|--------------------------------------|-------|-------|
| `/ops/today` renders five cards      |       |       |
| Card labels are the locked vocabulary (Received / Extracted / Accepted / Synthesized / Needs Action) |       |       |
| Card primary number reads the `ops_state` count |       |       |
| Card secondary text uses per-state verbs ("5 arrived today", "3 extracted today", etc.) |       |       |
| Primary CTA "Open N items →" lands on `/ops/items?state=…` |       |       |
| Items page row count equals the card primary number |       |       |
| Secondary CTA "View today's N evidence events →" lands on `/ops/events/audit?…` |       |       |
| Audit page row count equals the card secondary number |       |       |
| Card samples are item slugs/object_ids, NOT event_types |       |       |
| Honest-zero footer appears on cards with both 0/0 |       |       |
| `/ops/events/audit` role banner visible |       |       |
| `/ops/events` reciprocal banner visible |       |       |

## Open findings

M25.6 surfaced one product question that is **not** a regression
but should be answered before M26 starts.

### Dual-count on the Accepted card

The kernel classifies BOTH the source slug AND the object_id as
"Accepted" when a promote event fires.  For 18 promotions:

```
Accepted primary_count  = 36   (18 sources + 18 objects)
items page row count     = 36   (18 source rows + 18 object rows)
```

Card N === drilldown N holds, but the card reads 36 even though
the operator promoted 18 things.

The `test_acceptance_accepted_card_n_equals_items_page_n`
fixture locks this dual-count behaviour so a future PR that
drops one kind from `ops_state` fails loudly.  M26 picks the
side: collapse to 18 (operator mental model) or keep 36
(kernel-truth dual-classification).  No code change in M25.6.

## Operator sign-off

After every box above is ticked:

* Operator initials: _____________
* Date completed: _____________
* Smoke JSON committed to: `60-Logs/m25-acceptance.json`
* Outcome:  ☐ PASS  ☐ PASS with findings  ☐ FAIL — triage required

If PASS, link this completed report from the M25.5 PR description
or the next milestone's plan.  M24/M25 is then formally shipped;
M26 (Actionable Ops Items) can begin.

## What this report does NOT cover

These are deliberately out of M25.6 scope (M25 plan §"Out of
scope"):

* Option B clean-room rebuild of `audit_events`.  Decide after
  1-2 days of real-vault observation that the new pipeline
  emits the M24.2 producer rows reliably.
* `/ops` cockpit redesign.
* Inline actions on `/ops/items`.
* Cross-pack item view.

Each gets its own scoping pass when (and if) the dogfood
evidence motivates them.
