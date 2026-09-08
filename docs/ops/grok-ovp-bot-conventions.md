# Grok × OVP bot conventions

Canonical vault root is whatever you set as `OVP_VAULT_ROOT` (example: `/path/to/your/vault`).

## Capture drop-folder (bots)

Drop bot captures here only:

- `50-Inbox/00-Capture/` — preferred for tagged/inbox captures
- `Clippings/` — optional alternate for clip-style notes

Rules:

- Write ordinary markdown notes under those folders only.
- Do **not** write under `.ovp/crystal`, evolution ledgers, or other `.ovp/` append-only product state.
- Do **not** rewrite Source notes; use product commands (`ovp2 …`) for pipeline mutations.
- Prefer Involute INV ids + `ovp://` pointers in agent-data instead of copying Crystal prose into local agent scratch.

## MCP

```bash
export OVP_VAULT_ROOT=/path/to/your/vault
ovp2 mcp --vault-root "$OVP_VAULT_ROOT"   # stdio
```

See root [`mcp.json`](../../mcp.json) and [`skills/`](../../skills/).

## Schedule (operators)

```bash
ovp2 schedule install --vault-root "$OVP_VAULT_ROOT"
ovp2 schedule status --vault-root "$OVP_VAULT_ROOT"
ovp2 schedule list --vault-root "$OVP_VAULT_ROOT"
```

Credentials and provider env belong in the vault’s private `.ovp/` config (chmod 600) — never in this repo or in skill files.
