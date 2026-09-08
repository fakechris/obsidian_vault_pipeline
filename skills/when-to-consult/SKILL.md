---
name: when-to-consult
description: Decide when to consult the OVP vault via MCP versus answering from model memory. Prefer ask/find/search/claim/theme-page for vault-grounded facts.
---

# When to Consult OVP

Use this skill to choose the right OVP MCP surface — or to skip the vault when it cannot help.

## Prefer the vault (call MCP)

| Need | Skill / tool |
|------|----------------|
| Natural-language answer with verified receipts | `ask` |
| Structured inventory (kind/tag/date/entity) | `find` |
| Keyword / phrase hunt | `search` |
| Audit a `[claim:…]` citation | `claim` |
| Wiki-style theme narrative | `theme-page` |
| Pipeline pulse | `status` |
| Health / index problems | `doctor` |
| Save a note into the vault safely | `capture-drop` |

## Answer without the vault

- Pure coding / repo questions about this Rust workspace (use `AGENTS.md`, crates, tests)
- General knowledge unrelated to the operator’s notes
- When `ask` already returned honest empty coverage — do not fabricate vault citations

## Hygiene

- Set `OVP_VAULT_ROOT` (example: `/path/to/your/vault`); never embed secrets in MCP config
- Run `ovp2 mcp --vault-root "$OVP_VAULT_ROOT"` (see root `mcp.json`)
- After content changes, operators may need `ovp2 index` before search/ask quality recovers
- Keep ops conventions in skills + `docs/ops/`; do not casually delete operator docs
