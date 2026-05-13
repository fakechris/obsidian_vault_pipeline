# M23 — Digest Redesign: Crystal-only → Daily Knowledge Feedback

## Context

M20 shipped the daily digest as **"vault talks back"**, with the bet that
filtering ruthlessly to *already-synthesized* crystals would give the operator
high-density morning provocation. Three weeks of real use proved the bet
wrong, and the failure is structural — not in the prompt, in the **input layer**.

What the operator (one user, daily ingestion, 8–13 articles/day) actually sees:

| Day | Articles dropped | Evergreens touched | Crystals synthesized | Digest input rows | Digest LLM passes |
|-----|------------------|--------------------|-----|--------|------|
| 5/11 | ~8 | many (audit log) | 0 | 1 contradiction (from 5/4) | 1 |
| 5/12 | ~10 (memory-themed) | many | 0 | 1 contradiction (from 5/4) | **5** |
| 5/13 | (today) | — | 0 | 1 contradiction (from 5/4) | 0 so far |

The digest read the same single 5/4 contradiction every time and generated
5 distinct LLM paragraphs of the same insight on 5/12. The operator's
verdict: *"this digest doesn't relate to what I put in."* Correct, and the
data backs it up.

### Two root causes, not one

1. **Crystal-only input + synthesis lag.** Crystals are synthesized weekly
   (or less). Ingestion runs daily. The digest reads from a stage 3–4 layers
   downstream of raw markdown, so daily new articles never reach it.

2. **24h theme filter.** Even when synthesis runs, community crystals fall
   out of the digest 24h later. So the digest has a perpetually-empty themes
   section unless synthesis runs in the immediate prior day.

The original M20 framing — *"a digest is a curated rail, not a feed"* — was
right in spirit but applied to the wrong layer. A curated rail on top of
stale weekly synthesis is just a stale rail.

### User decisions captured (this conversation)

- **Digest is daily knowledge feedback, not crystal provocation.** New product
  definition: *"每日知识反馈：让用户知道今天喂进去的内容如何进入了知识系统、
  连接了哪些旧想法、产生了哪些新问题，以及下一步最值得推进什么。"*
- **Multi-layer input.** Crystals stay as a high-quality layer; they're no
  longer the *only* layer.
- **Article-aware, not article-summary.** Don't list 10 article titles.
  Do acknowledge that 10 came in and what they connected to.
- **Drop the "no call to action" stance.** End with one specific question
  whose answer would change operator behavior.
- **Honest no-data path.** When nothing changed, say so — don't fabricate
  insight from old crystals.
- **Plan first, code second.** This doc gets agreed before any handler
  changes.

### Codex review feedback folded in (2026-05-13)

The first plan draft used UTC midnight as the window, assumed
`evergreen_revisions` would exist in every vault's `knowledge.db`, and
proposed POST action buttons in v1. Codex flagged five risks; all are
incorporated below:

- **Time window**: not UTC midnight. Window is
  `[last_successful_digest_at, as_of]`; fallback to operator-local day
  boundary when no prior digest exists. Local timezone is configurable
  (see §Configuration).
- **Stage 2 entry condition**: explicit data-readiness preflight before
  the input collector lands. M23 cannot ship a "correct" digest that has
  no data to render in production.
- **Layer 0 broader intake events**: not only `article_processed` /
  `source_archived_to_processed`. Also covers `source_staged_for_processing`,
  source-authority intakes (`github_*`, `arxiv_*`), and clippings batch
  events. Configurable via `digest.yaml`.
- **Layer 0 and Layer 1 are independent.** Empty Layer 0 does not skip
  Layer 1 — manual edits, rollbacks, LLM rewrites, and promotion events
  all produce evergreen revisions without going through intake.
- **Layer 3 "unsynthesized" semantics**: a cluster's `community_crystal`
  existing isn't enough. The check is `MAX(evergreen_revisions.derived_at
  WHERE cluster_id = C) > community_crystals.synthesized_at WHERE
  cluster_id = C` — i.e. *crystal is stale relative to its inputs*.
- **`input_hash` semantics tightened**: see §Stage 3.
- **Stage 4 action buttons are link-only in v1.** POST-driven "Run
  synthesis" defers to M23.1.
- **Mid-day regeneration**: explicit "Regenerate digest now" button on
  the maintainer/digest page, not cron-only.

---

## Outcome (what's different after M23 ships)

The morning digest answers four questions in this order:

1. **What did I feed in?** — today's intake counts, themes, representative sources.
2. **What new thinking landed in the vault?** — new + materially updated evergreens.
3. **How does today connect to yesterday?** — which existing crystals / topics / contradictions today's intake touched.
4. **What's worth doing next?** — one concrete question or action (resolve contradiction X, synthesize cluster Y, read 3 articles that converged on Z).

When the answer to (1) is "nothing", the digest says so honestly and skips
sections rather than padding with stale crystals.

---

## Window semantics (applies to every layer)

The digest window is `[window_start, window_end]`, computed at dispatch
time:

- `window_end = as_of` — the dispatch timestamp (operator-local).
- `window_start = max_of(`
  - last `digest_generated` audit event's `generated_at` (so a regenerate
    after lunch covers only what landed since the morning run),
  - operator-local start of the day containing `as_of` (fallback for
    first-ever digest or when audit history is missing)
  - `)`.

UTC midnight is **never** the window boundary. Operator local tz is the
product semantic; UTC is only for storage. Configurable via
`digest.yaml.tz`; see §Configuration.

## Layered input schema

Each layer is a deterministic SQL/audit query over `[window_start,
window_end]` feeding the LLM as structured input. The LLM's job is
*interpretation across layers*, not invention.

**Layer 0 and Layer 1 are independent.** Either may be empty without
the other being empty (codex review feedback): manual evergreen edits,
LLM rewrites, rollbacks, and promotion events produce Layer 1 rows
without any intake; intake events land in Layer 0 without producing
evergreens for hours.

### Layer 0 — Window's intake (acknowledgment)

Source: `audit_events` filtered by `timestamp ∈ window`, event_type
matching the configurable allowlist (default below).

| Output field | Computation |
|--|--|
| `intake_events_processed` | count of events in the allowlist |
| `topic_distribution` | top-N keyword groupings over event titles / slugs |
| `authors_or_sources` | unique authors / source domains |
| `representative_samples` | 3–5 source titles to surface in the LLM input |

Default intake event allowlist (configurable via `digest.yaml`):
`article_processed`, `source_archived_to_processed`,
`source_staged_for_processing`, `clippings_batch_processed`,
`github_source_ingested`, `arxiv_source_ingested`. Adding a new source
authority requires extending this allowlist or the digest will silently
under-count.

Honest no-data behavior: when 0, Layer 0 renders as *"No new intake in
this window."* and the LLM is told so explicitly. **Does not affect
Layer 1.**

### Layer 1 — Evergreen delta (new thinking, real in window)

Source: `evergreen_revisions` filtered by `derived_at ∈ window`.

| Output field | Computation |
|--|--|
| `new_evergreens` | rows with `version = 1` AND `change_type = 'created'` |
| `updated_evergreens` | rows with `version > 1` or `change_type = 'updated'` |
| `change_summary` | from `change_note` when present + non-empty + ≥ 20 chars; otherwise from `change_type` + content-diff length (see §Data readiness preflight) |
| `cluster_membership` | join `objects.object_id` → `graph_clusters` to attach `cluster_id` per new/updated evergreen |

This is the spine. It's where the operator sees the system acknowledging
that their input produced something concrete.

**Hard prerequisite**: `evergreen_revisions` table must exist and have
recent rows. Currently it does **not** exist in the operator's local
`knowledge.db` (checked 2026-05-13). Stage 2 must verify before any
M23 code reaches the operator. See §Data readiness preflight.

**Not in scope for M23 v1** (deferred to v2; tracked as BL-098):
new claims, relations, entity_mentions. Those tables exist but have no
timestamp column — adding `created_at` is an ADDITIVE schema migration
(M22 three-bucket policy).

### Layer 2 — Connection to existing knowledge

Source: cross-join Layer 1 evergreens with `community_crystals` /
`contradiction_crystals` via cluster membership.

| Output field | Computation |
|--|--|
| `connected_communities` | `community_crystals` whose `cluster_id` matches any Layer 1 evergreen's cluster |
| `touched_contradictions` | `contradiction_crystals` whose `source_object_ids_json` overlaps any Layer 1 evergreen |
| `recent_crystals` | top-N crystals by `crystal_scores.score` (independent of window — global signal) |

This is where *"connects to your existing thinking on X"* comes from. The
LLM gets explicit "today connects to crystal Y, see body" pairs.

### Layer 3 — Pipeline state (backpressure visibility)

Source: aggregates over `evergreen_revisions`, `community_crystals`,
`graph_clusters`.

| Output field | Computation |
|--|--|
| `unsynthesized_evergreens` | count of evergreens whose cluster has either (a) no `community_crystal`, OR (b) a `community_crystal` whose `synthesized_at < MAX(evergreen_revisions.derived_at)` for that cluster. "Stale crystal" counts as unsynthesized. |
| `last_synthesis_at` | `MAX(community_crystals.synthesized_at)` |
| `clusters_at_threshold` | clusters with ≥ `cluster_threshold` evergreens AND (no crystal OR stale crystal). Default `cluster_threshold = 5`, pack-configurable via `digest.yaml`. |
| `open_contradictions_count` | unchanged from current digest |

The "stale crystal counts as unsynthesized" check is critical (codex
review). Without it, a cluster that synthesized 7 days ago with 8 new
evergreens added since would be falsely reported as "covered". The
operator would never see "go re-synthesize this cluster".

The digest can then honestly say *"12 evergreens are waiting for synthesis
or re-synthesis; last synthesis was 8 days ago; clusters at threshold:
3."* This turns the synthesis lag — currently invisible — into a
steerable signal.

### Layer 4 — Next question (LLM-generated, schema-constrained)

Not a data layer; a prompt instruction. After consuming Layers 0–3, the
LLM emits **one** of these question shapes:

- *"3 articles on X converged today. Want to synthesize them into a crystal?"*
- *"Today's new evergreen Y challenges existing contradiction Z. Resolve?"*
- *"Cluster W has 7 unsynthesized evergreens. Run synthesis?"*
- *"No new intake today. Last meaningful change: <date>. Continue with prior tensions?"*

The question is the digest's behavior-change lever. Success metric: how
often does the operator click the action surfaced here? (See §Success
metrics.)

---

## New digest body shape

```markdown
---
type: digest
schema_version: 2                                # bumped from 1
generated_at: 2026-05-13T08:00:00-07:00          # operator-local tz
window_start: 2026-05-12T08:30:00-07:00          # last successful digest
window_end:   2026-05-13T08:00:00-07:00
tz: America/Los_Angeles
pack: research-tech
input_hash: <sha256>                             # Stage 3 idempotency gate
preflight:                                       # data-readiness preflight result
  evergreen_revisions_table: ok
  evergreen_revisions_recent: ok
  audit_events_layer0: ok
  change_note_quality: degraded  # falls back to v{n}+diff length
  graph_clusters: ok
  community_crystals: ok
---

# Daily Knowledge Feedback — 2026-05-13

## Window's intake (since last digest, 23h 30m)
12 articles absorbed, concentrated around: memory systems (7), AI agents (3), operations (2).
Representative: "Skill Curation for Self-Evolving Agents", "Agent Memory Is Blind to Time", "How to become AI-Native".

## New thinking
- **New evergreen**: "Emergent memory beats designed memory" — converged from 3 of these articles, joined cluster `memory-systems`.
- **Updated evergreen**: "Skill curation for agents" — 2 new revisions (v3 → v5), content delta ~340 lines.

## How this window connects
- The `memory-systems` cluster additions touch the open contradiction *"index-based vs emergent memory"* (from 5/4). New evidence leans toward "emergent" — the contradiction may be tipping.
- Updated evergreen on skill curation strengthens existing community crystal *"Agent self-improvement loops"*.

## Pipeline state
- 12 evergreens awaiting synthesis (or re-synthesis). Last synthesis: 5/5, 8 days ago.
- 3 clusters at the `cluster_threshold ≥ 5`: `memory-systems` (9 ev, no crystal), `agent-skills` (6 ev, **stale crystal** from 5/4), `team-management` (5 ev, no crystal).

## Worth doing next
The memory-systems cluster now has 9 unsynthesized evergreens converging on a single tension. Synthesize this cluster, or open the 5/4 contradiction and resolve it against today's evidence?

[Resolve contradiction in `/ops/queue/contradictions` →]   [Open `/ops/cluster?id=memory-systems` →]
```

Two things about this example body that differ from the first draft:

* **Window is operator-local**, not UTC. The header shows
  `window_start … window_end` so the operator knows exactly what slice
  the digest covers, including mid-day regenerations ("since last
  digest, 23h 30m").
* **Stale-crystal flag** is visible in pipeline state. The
  `agent-skills` cluster has a crystal — but it's older than the
  cluster's newest evergreen, so it counts as unsynthesized. Without
  this surfacing, the operator would think it's "done" and never
  re-synthesize.
* **Links, not POST buttons.** The square-bracket affordances at the
  bottom navigate to existing maintainer pages, not mutate vault state.

Compare with today's output: it answers acknowledgment + continuity + next
question, and stops fabricating provocation from old data.

---

## Configuration

New file: `<vault>/.ovp/digest.yaml`. Follows the existing
`llm_profiles.yaml` precedent — one bespoke yaml per feature, loaded by
a per-feature loader, with template shipped in
`src/ovp_pipeline/data/digest_template.yaml`. `.ovp/` is gitignored.

```yaml
# <vault>/.ovp/digest.yaml
# M23 daily digest configuration.  Missing file → defaults below.

# Operator-local timezone for window boundaries.  Defaults to system
# locale via Python's tzlocal.  Override here to pin behavior across
# machines.  IANA tz name (e.g. "America/Los_Angeles", "Asia/Shanghai").
tz: ""                                  # default: system locale → UTC fallback

# Cluster size at which a cluster is considered "ready for synthesis".
cluster_threshold: 5

# Layer 0 intake event allowlist.  Add new event types here when new
# source authorities ship; otherwise they're silent-ignored.
intake_event_types:
  - article_processed
  - source_archived_to_processed
  - source_staged_for_processing
  - clippings_batch_processed
  - github_source_ingested
  - arxiv_source_ingested

# When true, the maintainer/digest page exposes a "Regenerate digest
# now" button (Stage 4).  Defaults to true.
mid_day_regenerate_button: true

# When true and a prior digest exists for the current window with the
# same input_hash, skip the LLM call (Stage 3 idempotency gate).
skip_unchanged: true
```

Loader contract (mirrors `llm_profiles.load_profiles`):

* `load_digest_config(vault_dir) -> DigestConfig` — frozen dataclass.
* Missing file → all defaults.
* Empty `tz` → resolve via `tzlocal.get_localzone()`; if unavailable,
  log once and fall back to UTC.

**Future cleanup, NOT in M23**: when we have 3–4 feature configs
(`llm_profiles.yaml`, `digest.yaml`, eventually `prompts.yaml`, …), a
unified `<vault>/.ovp/settings.yaml` with root-level `tz:` plus
feature-scoped subsections becomes worth the refactor. Track as a
separate BL after M23 lands.

---

## Data readiness preflight (Stage 2 entry condition)

M23 cannot ship a "correct" digest that has no data to render in
production. Before Stage 2 lands any code that depends on a layer,
verify against the operator's actual `knowledge.db`:

| Check | Pass condition | Failure handling |
|---|---|---|
| `evergreen_revisions` table exists | `SELECT name FROM sqlite_master WHERE name = 'evergreen_revisions'` returns a row | Run `ovp-knowledge-index` to backfill; if still empty, Layer 1 ships as "data not available in this vault — Layer 1 will populate after the next absorb run". Surface in /ops/digest-health. |
| `evergreen_revisions` has rows in the last 7 days | `SELECT COUNT(*) FROM evergreen_revisions WHERE derived_at >= NOW - 7d` > 0 | Same — Layer 1 honest no-data. |
| `audit_events` has Layer 0 intake events for the window | count of allowlist events > 0 | Layer 0 says "No intake in this window". Independent of Layer 1. |
| `change_note` quality | sample 10 most recent `evergreen_revisions.change_note`; ≥ 50% have ≥ 20 chars of non-generic text | If fails, Layer 1's `change_summary` falls back to "v{n}: {change_type}" + content-diff length, not the raw `change_note`. Document in plan + log audit event `digest_change_note_quality_low`. |
| `graph_clusters` non-empty | `SELECT COUNT(*) FROM graph_clusters` > 0 | Layer 2/3 ship as "clusters not built yet — run `ovp-knowledge-index --rebuild-graph`". |
| `community_crystals` non-empty | `SELECT COUNT(*) FROM community_crystals` > 0 | Layer 2's connections degrade to contradictions-only. Layer 3's `last_synthesis_at` is null and reads "never". |

This preflight runs as part of `collect_digest_inputs()` and emits a
`digest_preflight` audit event with per-check pass/fail. The handler
NEVER raises on preflight failure — it just renders degraded sections.
Operators see honest empty states instead of crashing dispatchers.

---

## Stages

### Stage 1 — Plan agreement (this doc)

Goal: lock the product definition + layered schema + success metrics before
touching code. Exit when operator says "ship it" on this doc.

### Stage 2 — Layered input collector (one PR, one BL)

New module `src/ovp_pipeline/digest_inputs.py`:

```python
@dataclass(frozen=True)
class DigestInputs:
    intake: IntakeLayer         # Layer 0
    evergreen_delta: DeltaLayer  # Layer 1
    connections: ConnectionLayer # Layer 2
    pipeline_state: PipelineState# Layer 3

def collect_digest_inputs(
    vault_dir: Path,
    pack: str,
    *,
    as_of: datetime | None = None,
) -> DigestInputs: ...
```

Pure data layer — no LLM call. Unit-testable with a fixture vault that
mocks `evergreen_revisions` rows directly. The handler keeps the LLM
call but consumes the new dataclass instead of the old three-list dict.

Tests must include the no-data path: empty `evergreen_revisions` since
midnight UTC → digest skips Layer 1 and the LLM gets explicit "no intake".

### Stage 3 — Prompt + handler rewrite (one PR, one BL)

* New `_DIGEST_SYSTEM_PROMPT_V2` — drops "no call to action" rule, adds
  the four-question structure, demands article-awareness without
  article-summary.
* Handler emits the new body shape (above) with `schema_version: 2` in
  frontmatter.
* **Input-hash idempotency gate** (tightened semantics per codex review):

  ```
  input_hash = sha256(canonical_json({
      "window_start": window_start.isoformat(),    # NOT current time
      "window_end":   window_end.isoformat(),      # NOT current time
      "layer0":       sorted(intake_event_ids),
      "layer1":       sorted(evergreen_revision_ids),
      "layer2":       sorted(connected_crystal_ids + touched_contradiction_ids),
      "layer3":       (unsynthesized_count, last_synthesis_at, clusters_at_threshold_ids),
  }))
  ```

  Key rules:

  * Hash only includes **stable identifiers**, never prose ("8 days ago")
    that drifts with wall-clock time.
  * Hash is keyed by `(digest_date, window_start, window_end)` — a
    yesterday-vs-today comparison is always a different hash.
  * The gate applies *within the same digest file* only: if today's run
    matches today's prior input_hash, skip the LLM call. **It does
    not block today's first run when yesterday's hash happened to
    match** (no-data days still produce a no-data digest for the
    current day).

* No-data path: when Layer 0 + Layer 1 + Layer 2 are all empty *and*
  Layer 3 hasn't changed since yesterday, the digest is a 2-line
  honest acknowledgment, not a fabricated brief.
* **Overwrite question answered here**: with the input-hash gate,
  redundant same-day runs collapse to one LLM pass per real input
  change. Overwriting the same filename in place is correct; no
  versioning needed.

### Stage 4 — Surface integration (one PR, one BL)

**v1 is link-only.** No POST-driven mutations from the digest page in
M23. Adding "Run synthesis on cluster X" buttons that mutate vault
state is the right product direction but its own scope (M23.1) — v1
keeps the digest cheap to ship and observable before we wire
maintainer mutations onto a Reader-side surface.

* Reader home digest banner reflects new schema:
  the teaser pulls from `## Window's intake` rather than the first body
  paragraph (M22 BL-093's `_extract_teaser` change).
* `/digests` list shows intake counts (Layer 0 summary) inline so
  operators can scan multi-day patterns without opening each file.
* New affordances on the digest page: **plain links**, not POST buttons.
  - *"Resolve contradiction"* → links to the existing
    `/ops/queue/contradictions?status=open` page filtered to the relevant
    row.
  - *"Run synthesis on cluster X"* → links to
    `/ops/cluster?id=<cluster_id>` where the operator triggers synthesis
    via the existing maintainer affordance.
  - *"Read source"* on each Layer 0 sample → existing `/note?path=…`.

  Each link click records a `digest_clicked_through` audit event
  carrying the action shape (`resolve_contradiction`, `run_synthesis`,
  `read_source`) so Stage 5's metrics can measure follow-through
  without needing POST routes.
* **Mid-day "Regenerate digest now" button** (codex review requirement):
  the maintainer-side `/ops/today` page (or `/digests`, TBD) gets an
  explicit `POST /ops/digest/regenerate` button. The input-hash gate
  keeps the cost bounded — if nothing changed since the last run, the
  button returns `{ok: true, skipped: true}` and no LLM call fires.
  Without this button, the operator who drops 8 articles at 4pm has
  no way to see their reflection until tomorrow's cron.

### Stage 5 — Instrumentation + success metrics (one PR, one BL)

* New audit events: `digest_clicked_through` (slug = source clicked),
  `digest_question_acted_on` (action = the next-question shape).
* `/ops/digest-health` page or section: weekly chart of
  click-through-rate and intake-reflection-rate (see §Success metrics).
* Acceptance: after one week of M23 in production, the digest hits both
  thresholds. If not, the design failed — iterate.

### Stage 6 — Schema migration for claim / relation / entity timestamps (DEFERRED)

Out of M23 v1. Tracked as a follow-up BL because adding `created_at` to
`claims` / `relations` / `entity_mentions` is a non-trivial schema
migration. M23 v2 picks this up and extends Layer 1 to include claim /
relation deltas.

---

## Success metrics (measurable, time-bounded)

Two thresholds, measured over a 14-day window after M23 ships:

| Metric | Threshold | Source |
|---|---|---|
| **Click-through rate** | ≥ 50% of digest reads produce ≥ 1 outbound click to a source / evergreen / crystal | new `digest_clicked_through` audit event |
| **Intake reflection rate** | when a day has ≥ 3 articles processed, the digest mentions today's intake in 100% of cases | new field on `digest_generated` event |
| **Question follow-through rate** | ≥ 20% of digests result in the operator acting on Layer 4's "worth doing next" question | new `digest_question_acted_on` audit event |

If click-through < 30% at end of week 2, the digest is failing its core
job and design needs revision, not the prompt.

---

## Backwards compatibility

* Filename format unchanged: `40-Resources/Generated/digests/YYYY-MM-DD-digest-daily.md`.
* Reader home banner card unchanged in placement; only body teaser
  source shifts (Stage 4).
* M22 BL-093 `/digests` list + prev/next nav unchanged.
* Old digest files (`schema_version: 1`) continue to render — the new
  Reader integration handles both schemas.
* No backfill — the M22 digest at 5/12 stays as-is.

---

## Open issues to flag

### Resolved during plan iteration (codex review)

* **~~Time window UTC midnight~~** → resolved: window is
  `[last_successful_digest_at, as_of]` in operator-local tz; see
  §Window semantics.
* **~~`evergreen_revisions` may not exist~~** → resolved: data-readiness
  preflight is Stage 2's entry condition; Layer 1 ships honest no-data
  if the table or rows are missing. See §Data readiness preflight.
* **~~Layer 0 too narrow~~** → resolved: intake event allowlist is
  config-driven via `digest.yaml`; defaults cover article / clippings /
  github / arxiv intake.
* **~~Layer 0 == 0 skips Layer 1~~** → resolved: independent. Manual
  edits and rollbacks produce Layer 1 rows without Layer 0 events.
* **~~"Cluster has crystal" ≠ "synthesis is current"~~** → resolved:
  Layer 3 explicitly compares
  `MAX(evergreen_revisions.derived_at) > community_crystals.synthesized_at`
  per cluster.
* **~~`input_hash` semantics drift~~** → resolved: hash is keyed by
  `(window_start, window_end, stable ids)` only, never wall-clock prose.
  Gate applies within same digest file only.
* **~~Stage 4 POST buttons~~** → resolved: v1 is link-only. POST defers
  to BL-099.
* **~~Mid-day regenerate via cron only~~** → resolved: explicit
  maintainer button (`mid_day_regenerate_button` config flag, default
  true).

### Still open

* **Layer 1 v2 needs schema work.** `claims` / `relations` /
  `entity_mentions` lack timestamps. Adding `created_at` is ADDITIVE per
  the M22 three-bucket policy. Tracked as BL-098.
* **Cluster-threshold default is a guess.** Plan defaults to 5,
  pack-configurable. Stage 2 must run a histogram against the operator's
  actual `graph_clusters` to validate; if median cluster size is far from
  5, change the default before Stage 3 ships.
* **`change_note` content quality** — preflight handles this with a
  fallback path (sample 10 rows; if < 50% have meaningful text, Layer 1
  falls back to `v{n} + content-diff length`). Whether the M18 router
  ever populates `change_note` well is a separate question, not blocking.
* **Outbound link tracking instrumentation** requires Stage 5 audit
  events. Click-through measurement starts the day Stage 5 lands, not
  the day M23 ships.
* **LLM cost.** New Layer 0–3 input is larger than today's three-list
  format. Estimate prompt tokens during Stage 2's collector tests; if
  > 2× today's cost, trim Layer 0's `topic_distribution` first.
* **Timezone library dependency.** `tzlocal` adds a new dep. Acceptable;
  it's a small pure-Python package. Pin in pyproject.toml during BL-094.
* **Unified `<vault>/.ovp/settings.yaml`** with root-level `tz:` is the
  right end-state once we have 3–4 feature configs. Tracked as a future
  cleanup BL, not in M23.

---

## Why this gets its own milestone (M23), not folded into M22

M22 is the anchored-inquiry drawer + digest *history*. M22 doesn't touch
digest *generation*. Folding digest redesign into M22 would expand the
PR surface by ~600 lines across 4 new modules and delay the M22 drawer
ship the operator is actively using. M23 is sequenced strictly after
M22 merges.

## Plan vs Backlog

This plan covers M23 design intent. After agreement, the BL breakdown
lands in `BACKLOG.md` as:

| BL | What |
|---|---|
| BL-094 | Stage 2 — `digest_inputs.py` data collector + data-readiness preflight + `digest.yaml` loader + tz resolution + tests |
| BL-095 | Stage 3 — prompt v2 + input-hash idempotency gate + no-data path |
| BL-096 | Stage 4 — Reader integration (home teaser, /digests intake column, **link-only** affordances, mid-day regenerate button) |
| BL-097 | Stage 5 — click-through + intake-reflection + question-follow-through audit events + `/ops/digest-health` |
| BL-098 | (deferred to M23.1) — claims / relations / entity_mentions `created_at` ADDITIVE migration + Layer 1 v2 |
| BL-099 | (deferred to M23.1) — POST-driven action buttons (Run synthesis, Resolve contradiction in-place) |

Six BLs (one extra for the deferred POST buttons that codex review
pushed out of v1). v1 scope: BL-094 → BL-097, smaller than M21 or M22.
Estimated 2 days end-to-end once this plan is agreed.
