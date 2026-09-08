---
name: status
description: Get OVP pipeline status — totals, recent runs, blocked sources. Use for operational pulse checks before deeper doctor or ask work.
---

# OVP Status

Use the ovp2 MCP `status` tool for a compact pipeline pulse: totals, recent runs, blocked sources.

## When to use

- “Is the vault / pipeline healthy enough to ask?”
- Quick ops check before a daily run review
- Seeing blocked sources without a full doctor pass

## Arguments

None.

## Behavior

- Summarize totals, latest runs, and blocked sources in plain language.
- If blocked sources dominate, suggest `doctor` next.
- Do not mutate vault state; this is read-only.
