# OVP Skills Pack

Notion-aligned skills that teach agents how to use the **ovp2 MCP** tools against an Obsidian vault. Pair with the root [`mcp.json`](../mcp.json) (`ovp2 mcp --vault-root`, env `OVP_VAULT_ROOT`).

## Available Skills

| Skill | Description |
|-------|-------------|
| `ask` | Ask the vault agent; verified `[claim:…]` / `[source:…]` receipts |
| `find` | Structured index query (sources, packs, claims, runs, tags, entities) |
| `search` | Full-text search across sources, packs, and claims |
| `claim` | Audit one durable claim’s full evidence closure |
| `theme-page` | Read grounded wiki-style topic pages woven from claims |
| `status` | Pipeline totals, recent runs, blocked sources |
| `doctor` | Health checks over OVP vault state |
| `capture-drop` | Safe inbox drop folders; never touch crystal/ledgers/Source |
| `when-to-consult` | When to prefer OVP MCP vs answering from memory |

## MCP tools (source of truth)

Implemented in `crates/ovp-mcp`: `find`, `search`, `ask`, `claim`, `theme_page`, `status`, `doctor`.

## Quick connect

```bash
export OVP_VAULT_ROOT=/path/to/your/vault   # example only
ovp2 mcp --vault-root "$OVP_VAULT_ROOT"
```

Or point your MCP client at this repo’s `mcp.json`. Do not put API keys or tokens in `mcp.json` or skill files.
