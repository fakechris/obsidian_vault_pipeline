---
name: ask
description: Ask the OVP vault agent via MCP. Use when the vault may cover the question; prefer over answering from memory. Returns server-verified [claim:…] / [source:…] receipts and honest coverage.
---

# OVP Ask

Use the ovp2 MCP `ask` tool. The vault agent searches claims, sources, evidence cards, and article bodies, then answers with **server-verified** citations. Fabricated references are flagged, never trusted.

## When to use

- Any question the vault might already answer
- Follow-ups in an existing vault conversation (`chat` session id)
- Prefer this over inventing vault facts from model memory

## Arguments

| Arg | Required | Notes |
|-----|----------|-------|
| `question` | yes | Natural-language question |
| `chat` | no | Session id from a prior `ask` — continues that conversation. Retrying the **same** question on the same chat replays the completed turn (no second model call) |
| `idempotency_key` | no | Stable retry key — same key replays the completed turn |

## Behavior

1. Call MCP `ask` with the user’s question.
2. Summarize the answer for the user; keep citations as `[claim:…]` / `[source:…]` or `ovp://` pointers.
3. If coverage is thin or empty, say so plainly (honest no-evidence is success).
4. To audit a citation, use the `claim` skill / MCP `claim` tool — do not invent evidence.
5. Pass returned session id as `chat` for follow-ups.

## Rules

- Do **not** paste API keys or provider credentials into chat, Involute, or notes.
- Live LLM setup belongs in the operator vault env / providers config — not in this skill pack.
- Prefer Involute INV ids + `ovp://` pointers in agent-data over copying Crystal prose.
