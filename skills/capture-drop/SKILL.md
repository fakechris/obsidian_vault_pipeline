---
name: capture-drop
description: Drop bot/agent captures only into safe inbox folders (50-Inbox/00-Capture or Clippings). Never write crystal, ledgers, or rewrite Source notes.
---

# OVP Capture Drop

Safe drop-folder rules for bots and agents writing into the operator vault.

## Drop here only

- `50-Inbox/00-Capture/` — preferred for tagged/inbox captures
- `Clippings/` — optional alternate for clip-style notes

Write **ordinary markdown** notes under those folders only. Intake (`ovp2 daily` / pipeline) normalizes them into `50-Inbox/01-Raw/` and onward.

## Never do this

- Do **not** write under `.ovp/crystal`, evolution ledgers, or other `.ovp/` append-only product state
- Do **not** hand-edit Crystal / ledgers
- Do **not** rewrite Source notes; use product commands (`ovp2 …`) for pipeline mutations
- Do **not** commit secrets, API keys, or raw provider env files into the vault or this repo

## Pointers over copies

Prefer Involute INV ids and `ovp://` claim/source/theme pointers in agent-data instead of copying Crystal prose into local agent scratch.

## After dropping

Operators process captures via the daily loop (`ovp2 daily --vault-root …`). Agents should not invent alternate write paths into Reader/Crystal.
