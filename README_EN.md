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

Current document version: `v0.8.1`

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

## Why The Architecture Looks Like This

This repository started as a set of Obsidian automation scripts, but that model stopped scaling once the system grew:

- the main runtime and individual scripts drifted apart
- concepts, links, Atlas, graph, and retrieval indexes were tightly coupled without a clean truth boundary
- new domains like media, medical, or engineering research could not be modeled safely with a concept-only core

The current architecture is the direct answer to those failures:

- the six-layer runtime makes orchestration, canonical state, and derived state explicit
- `research-tech` makes the current engineering research semantics explicit
- `default-knowledge` is being reduced to a default compatibility layer instead of carrying every domain semantic
- Pack API turns future domains into installable packs rather than more hardcoded branches inside the runtime

So the project is no longer just a Vault automation repo. It is now:

> an extensible knowledge orchestration platform for Obsidian-style vault workflows

with:

- `research-tech` as the first explicit built-in standard pack
- `default-knowledge` retained as the default compatibility pack

## Domain Packs

The core runtime is now being formalized as a pack-aware platform.

- Built-in standard pack: `research-tech`
- Default compatibility pack: `default-knowledge`
- Runtime selection is exposed through `--pack` and `--profile`
- Third-party packs can be discovered through the `openclaw_pipeline.packs` entry point group or the `OPENCLAW_PACK_MANIFESTS` manifest list

Examples:

```bash
ovp-packs
ovp-doctor --pack research-tech --json
ovp --pack research-tech --profile full
ovp-autopilot --pack research-tech --profile autopilot --yes
ovp --pack default-knowledge --profile full
```

Pack API documentation for third-party developers lives in:

- `docs/pack-api/README.md`
- `docs/pack-api/manifest-and-hooks.md`
- `docs/pack-api/dogfooding-with-media-pack.md`

## Platform Architecture

From a platform perspective, the system now has three layers:

1. **Core Platform**
2. **Domain Pack**
3. **Workflow Profile**

### 1. Core Platform

Core owns the cross-domain pieces that must remain stable:

- runtime / vault layout
- CLI orchestration
- autopilot / queue / watcher
- canonical identity helpers
- registry framework
- derived `knowledge.db`
- graph / lint / audit infrastructure
- plugin / pack loading
- base evidence schema contracts

### 2. Domain Pack

A pack is not just a prompt bundle. It defines domain semantics:

- object kinds
- workflow profiles
- discovery boundaries
- absorb / refine / lint rules
- schemas / templates / prompt resources

The built-in packs are:

- `research-tech`: the explicit technical research pack and the default workflow pack
- `default-knowledge`: the compatibility layer

Future domains such as media or medical should arrive as external pack projects.

### 3. Workflow Profile

A workflow profile is an executable DAG under a pack.

The built-in profiles currently shipped are:

- `research-tech/full`
- `research-tech/autopilot`
- `default-knowledge/full`

## Research-Tech Operational Surface

`research-tech` is no longer only an internal pack. It now has a minimal operational surface:

- `ovp-doctor`
  reports default workflow pack, pack roles, operator docs, recipes, and optional vault health
- `ovp-export`
  exports minimal compiled artifacts:
  - `object-page`
  - `topic-overview`
  - `event-dossier`
  - `contradictions`
- `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- `docs/recipes/research-tech/*.md`

Examples:

```bash
ovp-doctor --pack research-tech --json
ovp-export --pack research-tech --target topic-overview --output-path /tmp/topic.md
```
- `default-knowledge/autopilot`

That is why the default workflow path now runs:

```bash
ovp --full
ovp-autopilot --yes
```

You can still select packs explicitly:

```bash
ovp --pack research-tech --profile full
ovp-autopilot --pack research-tech --profile autopilot --yes
# compatibility path
ovp --pack default-knowledge --profile full
```

## Plugin Design

The plugin / pack surface is no longer only a design memo. There is now a minimal working integration path.

Two discovery modes are supported:

1. Python entry point group: `openclaw_pipeline.packs`
2. Explicit manifest list: `OPENCLAW_PACK_MANIFESTS=/path/a.yaml:/path/b.yaml`

The minimum third-party loading chain is:

1. provide a manifest
2. declare `entrypoints.pack`
3. return a `BaseDomainPack`
4. pass `api_version` validation
5. select it through `--pack/--profile`

Hard boundaries currently enforced by core:

- a pack cannot turn semantic retrieval into canonical identity
- a pack cannot treat `knowledge.db` as source of truth
- a pack cannot bypass audit/logging
- all derived state must remain rebuildable

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
AUTO_VAULT_MODEL=anthropic/MiniMax-M2.7-highspeed

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

This document targets: `v0.8.1`
