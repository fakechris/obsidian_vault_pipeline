# Product dates — three axes (do not collapse)

Knowledge work mixes **when content is about**, **when you captured it**, and
**when the pipeline processed it**. OVP keeps these separate so the calendar
and backfill never invent history.

## Axes

| Axis | Name | Meaning | Stored where |
|------|------|---------|--------------|
| **A** | Content / capture time | When the bookmark was pinned, or the article's publish day on the note | Source frontmatter `published`; pinboard `posted_at`; filename day; projected as `SourceRow.content_date` |
| **B** | Pipeline time | When *our* system ran intake / reader / crystallize | Intake ledger `date`, daily ledger `date`, pack dir prefix, `run_id`, `RunRow.date` → `SourceRow.captured_on` / `processed_on` / `ClaimRow.run_date` |
| **C** | Subject time | What period the content is *about* (FY2025 Q2) | **Not stored yet** — never derived from A or B |

## Projection fields (`ovp.index`)

### `SourceRow`

| Field | Axis | Notes |
|-------|------|--------|
| `content_date` | A | From FM `published` (preferred), else first `YYYY-MM-DD` in path |
| `captured_on` | B | Intake ledger day |
| `processed_on` | B | Last daily-run ledger day |
| `last_run_id` | B | e.g. `daily-2026-07-26` |
| `date` | B (legacy) | `processed_on ?? captured_on` — keep for old clients |

### `PackRow.date`

Axis **B** only — day the reader run wrote the pack (`cfg.date` of that daily).

### `ClaimRow.run_date` / `run_id`

Axis **B**. Crystal ledger has no written-at; we surface the day embedded in
`run_id` when present.

### `RunRow.date`

Axis **B** — the daily (or report) calendar day.

## UI rules (Today / day browser)

- **「捕获 / content」** → group by `content_date` (A)
- **「已读 / processed」** → group by pipeline run day (B)
- **Never** move today's reader work onto last month's calendar day just
  because the article is old
- Axis **C** stays out of the product until an explicit subject-period field
  exists

## Backfill implication

Pinboard historical materialize may set A (bookmark day) correctly while B is
"the day we ran backfill". Both are honest; the calendar must show both, not
one blended `date`.

## Out of scope (for now)

- Axis C fields
- Rewriting ledgers (ledgers stay append-only with their own `date` = B of that write)
- Migrating old index files (rebuild with `ovp2 index` fills the new fields)
