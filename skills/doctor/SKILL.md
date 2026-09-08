---
name: doctor
description: Run health checks over OVP vault state. Use when status looks off, index/tools misbehave, or before trusting ask coverage.
---

# OVP Doctor

Use the ovp2 MCP `doctor` tool to run health checks over OVP vault state.

## When to use

- `status` shows anomalies or blocked sources
- `ask` / `find` / `search` report missing index layers
- Preflight after vault path or install changes

## Arguments

None.

## Behavior

- Report each check clearly (pass / warn / fail) with the shortest actionable next step.
- Prefer operator commands (`ovp2 index`, `ovp2 daily`, …) over hand-editing product state.
- Never “fix” health by rewriting `.ovp/crystal`, evolution ledgers, or Source notes by hand.
