---
name: find
description: Query the OVP index with structured filters (sources, packs, claims, runs, tags, entities). Use for precise inventory lookups rather than open-ended prose search.
---

# OVP Find

Use the ovp2 MCP `find` tool to query the index: sources, packs, claims, runs, tags, entities.

## When to use

- “What sources/packs/claims match X?”
- Filter by kind, status, date prefix, tag, or entity id
- Prefer precision; use `search` for free-form full-text

## Arguments

| Arg | Notes |
|-----|-------|
| `term` | Free-text search term |
| `kind` | `sources` \| `packs` \| `claims` \| `runs` \| `tags` \| `entities` |
| `status` | Status filter |
| `date` | Date prefix (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`) |
| `tag` | Canonical tag over sources (`kind=tags` lists vocabulary) |
| `entity` | URL entity id, e.g. `github:owner/repo` (`kind=entities` lists index) |

## Behavior

- Return a short, scannable list (id/title, kind, status, one-line hint).
- Prefer 5–15 high-signal hits over dumping the whole index.
- If empty, suggest alternate `kind` / `term` or fall back to `search` / `ask`.
