---
name: theme-page
description: Read grounded topic pages (wiki-style narratives woven from durable claims). Use for theme overviews; every sentence cites [claim:…] resolvable via the claim tool.
---

# OVP Theme Page

Use the ovp2 MCP `theme_page` tool to read one grounded topic page, or list pages when no theme is given.

## When to use

- “What does the vault say about theme X?” as a wiki-style narrative
- Browsing available grounded topic pages
- After `ask` / `search` when you want the curated theme synthesis

## Arguments

| Arg | Notes |
|-----|-------|
| `theme` | Theme label (exact) or community id (`t000` / `0`). **Omit to list** available pages |

## Behavior

- Without `theme`: list pages with labels / `ovp://theme-page/…` ids.
- With `theme`: return the narrative; keep `[claim:…]` citations intact.
- Audit any citation with the `claim` skill — do not strip or invent keys.

## Notes

- Same payload as the `ovp://theme-page/<id>` resource.
