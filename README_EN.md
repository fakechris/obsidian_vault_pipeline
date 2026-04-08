---
schema_version: "1.0.0"
note_id: readme_en-5d661efc
title: "Obsidian Vault Pipeline"
description: "A six-layer Obsidian knowledge pipeline"
date: 2026-04-07
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/obsidian-vault-pipeline.svg)](https://pypi.org/project/obsidian-vault-pipeline/)

Production-grade knowledge orchestration for Obsidian Vaults  
Ingest → Interpret → Absorb → Refine → Canonical → Derived

[🇨🇳 中文](README.md)

</div>

## What This Is

This project is not a loose collection of scripts. It is a layered knowledge system built around an Obsidian vault:

- Ingest normalizes incoming raw material
- Interpret turns raw material into deep-dive notes
- Absorb compiles those notes into evergreen lifecycle actions
- Refine performs cleanup and breakdown on existing notes
- Canonical maintains registry, aliases, Atlas, and MOC
- Derived builds `knowledge.db`, graph views, lint, and daily deltas

The current release wires those layers into the actual runtime:

- `ovp --full` runs through `knowledge_index` by default
- `ovp --full --with-refine` inserts `refine` before the final derived refresh
- `ovp-autopilot` runs real-time `absorb -> moc -> knowledge_index`
- `ovp-autopilot --with-refine` adds `refine` to that path

## Runtime Model

### Source of Truth

The system keeps a hard boundary:

- **canonical truth**: vault markdown + concept registry
- **derived views**: Atlas, MOC, graph, `knowledge.db`, lint, daily delta
- **not canonical truth**: `knowledge.db`

`knowledge.db` is the GBrain-inspired derived index layer. It stores:

- page FTS
- structured links
- mirrored raw sidecars
- timeline / audit events
- deterministic section embeddings
- read-only query / serve surfaces

It is rebuildable and does not own canonical identity resolution.

### The Six Layers

| Layer | Responsibility | Representative commands | Can the LLM make major decisions here? |
|---|---|---|---|
| Ingest | Normalize incoming material | `ovp --step pinboard` `ovp --step clippings` `ovp-article` | No |
| Interpret | Produce deep interpretations | `ovp-article` `ovp-github` `ovp-paper` | Yes, with constrained output |
| Absorb | Compile interpretations into lifecycle actions | `ovp-absorb` `ovp-evergreen` | Yes, but only through structured results |
| Refine | Cleanup and breakdown existing notes | `ovp-cleanup` `ovp-breakdown` | Yes, but execution is controlled |
| Canonical | Maintain registry / aliases / Atlas / MOC | `ovp-rebuild-registry` `ovp-moc` `ovp-promote-candidates` | No |
| Derived | Build retrieval / graph / lint views | `ovp-knowledge-index` `ovp-graph` `ovp-lint` | No |

## What `ovp --full` Actually Runs

Default full pipeline:

```text
pinboard
→ pinboard_process
→ clippings
→ articles
→ quality
→ fix_links
→ absorb
→ registry_sync
→ moc
→ knowledge_index
```

With refine enabled:

```text
pinboard
→ pinboard_process
→ clippings
→ articles
→ quality
→ fix_links
→ absorb
→ registry_sync
→ moc
→ refine
→ knowledge_index
```

Important details:

- `absorb` now shells to `openclaw_pipeline.commands.absorb`
- `refine` is a batch wrapper over `cleanup + breakdown`
- `knowledge_index` always runs last so `knowledge.db` reflects final canonical state
- `--step evergreen` and `--from-step evergreen` are still accepted and map to `absorb`

## What `ovp-autopilot` Actually Runs

Default real-time path:

```text
interpretation
→ quality
→ absorb
→ moc
→ knowledge_index
→ auto_commit(optional)
```

Enable refine explicitly:

```bash
ovp-autopilot --watch=inbox --with-refine --yes
```

That changes the path to:

```text
interpretation
→ quality
→ absorb
→ moc
→ refine
→ knowledge_index
→ auto_commit(optional)
```

Refine is not hidden or missing. It is wired in, but opt-in by default to avoid silent real-time structural rewrites of the whole knowledge base.

## Command Overview

### Daily entry points

| Command | Purpose |
|---|---|
| `ovp --check` | Validate runtime configuration |
| `ovp --full` | Run the full daily pipeline |
| `ovp --full --with-refine` | Run full pipeline plus cleanup/breakdown |
| `ovp --step absorb` | Run only the absorb layer |
| `ovp --step refine` | Run only the batch refine layer |
| `ovp --from-step absorb` | Resume from absorb onward |

### Content processors

| Command | Purpose |
|---|---|
| `ovp-article --process-inbox --vault-dir <vault>` | Process raw documents |
| `ovp-github --process-single <file> --vault-dir <vault>` | Process GitHub inputs |
| `ovp-paper --process-single <file> --vault-dir <vault>` | Process paper inputs |

### Absorb / Refine / Canonical

| Command | Purpose |
|---|---|
| `ovp-absorb --recent 7 --json` | Absorb recent deep dives |
| `ovp-evergreen --recent 7 --json` | Compatibility alias for `ovp-absorb` |
| `ovp-cleanup --all --json` | Generate cleanup proposals |
| `ovp-cleanup --all --write --json` | Apply deterministic cleanup |
| `ovp-breakdown --all --json` | Generate breakdown proposals |
| `ovp-breakdown --all --write --json` | Apply incremental breakdown |
| `ovp-rebuild-registry --json` | Reconcile evergreen notes and registry |
| `ovp-promote-candidates review` | Review candidate lifecycle |
| `ovp-moc --scan --vault-dir <vault>` | Refresh MOC / Atlas |

### Derived layer

| Command | Purpose |
|---|---|
| `ovp-knowledge-index --json` | Rebuild `knowledge.db` |
| `ovp-knowledge-index --search "query" --json` | Run FTS search |
| `ovp-knowledge-index --query "question" --json` | Run embedding chunk query |
| `ovp-knowledge-index --get slug --json` | Read a canonical page |
| `ovp-knowledge-index --stats --json` | Read index stats |
| `ovp-knowledge-index --audit-recent --json` | Read recent audit events |
| `ovp-knowledge-index --tools-json` | Emit tool discovery JSON |
| `ovp-knowledge-index --serve` | Start read-only stdio JSONL service |
| `ovp-graph daily today --vault-dir <vault>` | Build daily graph delta |
| `ovp-lint --check --vault-dir <vault>` | Run structure/link checks |

### AutoPilot

| Command | Purpose |
|---|---|
| `ovp-autopilot --watch=inbox --parallel=1 --yes` | Default real-time pipeline |
| `ovp-autopilot --watch=inbox,pinboard --yes` | Watch multiple sources |
| `ovp-autopilot --with-refine --yes` | Add refine to the real-time path |
| `ovp-autopilot --no-commit --yes` | Disable auto-commit |

## Directory Layout

```text
vault/
├── 50-Inbox/
│   ├── 01-Raw/
│   ├── 02-Pinboard/
│   └── 03-Processed/
├── 10-Knowledge/
│   ├── Evergreen/
│   └── Atlas/
│       ├── Atlas-Index.md
│       ├── concept-registry.jsonl
│       └── alias-index.json
├── 20-Areas/
│   └── {AI-Research, Investing, Programming, Tools}/Topics/YYYY-MM/
├── 60-Logs/
│   ├── pipeline.jsonl
│   ├── refine-mutations.jsonl
│   ├── transactions/
│   ├── quality-reports/
│   ├── daily-deltas/
│   └── knowledge.db
└── 70-Archive/
```

## What `knowledge.db` Provides

`knowledge.db` is a rebuildable local derived index. It currently includes:

- `pages_index`
- `page_fts`
- `page_links`
- `raw_data`
- `timeline_events`
- `audit_events`
- `page_embeddings`

It exists to power:

- keyword retrieval
- embedding retrieval
- canonical page reads
- audit browsing
- tool discovery and read-only serving

Default discovery now routes through this layer:

- `ovp-query` uses `knowledge.db` by default
- keyword retrieval uses FTS5 BM25
- semantic retrieval uses local deterministic embeddings
- QMD is no longer the default runtime dependency; it is opt-in via `--engine qmd`

## Quick Start

```bash
pip install obsidian-vault-pipeline

mkdir -p my-vault
cd my-vault

ovp --check
ovp --full
```

If you want to see the refine layer explicitly:

```bash
ovp --full --with-refine
```

If you want a daemon:

```bash
ovp-autopilot --watch=inbox --parallel=1 --yes
```

## Configuration

Put `.env` in the vault root:

```bash
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic

# Optional
PINBOARD_TOKEN=username:token
HTTP_PROXY=http://127.0.0.1:7897
```

## Design Principles

- identity consistency before feature growth
- vault files + registry define canonical state
- `knowledge.db` is derived retrieval, never a second truth source
- absorb is part of daily automation; refine is powerful and opt-in by default
- docs must describe what actually ships, not a future architecture sketch

## Related Resources

- [Showcase Vault](https://github.com/fakechris/obsidian_vault_showcase)
- [PyPI](https://pypi.org/project/obsidian-vault-pipeline/)
- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

This document targets: `v0.7.x`
