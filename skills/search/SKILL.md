---
name: search
description: Full-text search across OVP product state (sources, packs, claims). Use for keyword or phrase lookup when structured find filters are unknown.
---

# OVP Search

Use the ovp2 MCP `search` tool for full-text search across sources, packs, and claims.

## When to use

- Keyword / phrase hunt across product state
- You do not yet know the right `kind` / tag / entity for `find`
- Scouting before a deeper `ask` or `claim` audit

## Arguments

| Arg | Required | Notes |
|-----|----------|-------|
| `query` | yes | Search query |

## Behavior

- Interpret the query as keywords or a short phrase.
- Summarize hits with title/id, type, and a one-line snippet — no raw JSON dumps.
- For natural-language Q&A with verified receipts, prefer `ask`.
- For structured inventory filters, prefer `find`.
