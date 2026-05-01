---
schema_version: "1.0.0"
note_id: readme_en-5d661efc
title: "Obsidian Vault Pipeline"
description: "An auditable knowledge state runtime for Obsidian"
date: 2026-04-07
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/obsidian-vault-pipeline.svg)](https://pypi.org/project/obsidian-vault-pipeline/)

Auditable knowledge state runtime for Obsidian Vaults<br>
Capture → Compile → Reuse

[🇨🇳 简体中文](README.zh-CN.md)

</div>

Current document version: `v0.9.2`

Primary docs:

- [Architecture](ARCHITECTURE.md) ([简体中文](ARCHITECTURE.zh-CN.md))
- [Milestone](MILESTONE.md) ([简体中文](MILESTONE.zh-CN.md))
- [Active Backlog](BACKLOG.md)

## What This Is

Obsidian Vault Pipeline is not a loose collection of scripts, and it is not only RAG over Markdown. It is a local knowledge state runtime built around an Obsidian vault:

- **Capture** receives Pinboard, Clippings, raw Markdown, papers, GitHub repos, and web pages while keeping source lifecycle traceable.
- **Compile** turns material into deep dives, candidates, claims, evidence, relations, contradictions, registry rows, and graph rows.
- **Reuse** projects compiled knowledge into reader atlas pages, object pages, graph views, briefings, search, context packs, writing prompts, and the operator workbench.

Internally the engineering model still uses six layers: Ingest -> Interpret -> Absorb -> Refine -> Canonical -> Derived. The product narrative is Capture -> Compile -> Reuse.

The current release wires those layers into the actual runtime:

- `ovp --full` runs through `knowledge_index` by default
- `ovp --incremental` is the daily incremental entry point, including recent Pinboard + Clippings and downstream stages
- `ovp --full --with-refine` inserts `refine` before the final derived refresh
- `ovp-autopilot` runs real-time `absorb -> moc -> knowledge_index`
- `ovp-autopilot --with-refine` adds `refine` to that path
- `ovp-ui` provides a local UI. The default `/` entry is now a reader-first Knowledge Library, the operator dashboard lives under `/ops`, object pages expose source/backlink context, and `/graph` (also `/map`) renders a reader-facing knowledge map.

## Why The Architecture Looks Like This

This repository started as a set of Obsidian automation scripts, but that model stopped scaling once the system grew:

- the main runtime and individual scripts drifted apart
- concepts, links, Atlas, graph, and retrieval indexes were tightly coupled without a clean truth boundary
- new domains like media, medical, or engineering research could not be modeled safely with a concept-only core

The current architecture is the direct answer to those failures:

- Capture -> Compile -> Reuse explains the product value
- source -> observation -> claim -> evidence -> validity -> projection -> permission explains the long-term knowledge state
- the six-layer runtime makes orchestration, canonical state, and derived state explicit
- `research-tech` makes the current engineering research semantics explicit
- `default-knowledge` is being reduced to a default compatibility layer instead of carrying every domain semantic
- Pack API turns future domains into installable packs rather than more hardcoded branches inside the runtime

So the project is no longer just a Vault automation repo. It is now:

> a reader-first, evidence-backed knowledge atlas over an auditable knowledge state runtime

with:

- `research-tech` as the first explicit built-in standard pack
- `default-knowledge` retained as the default compatibility pack
- `knowledge.db` as a derived store, never Authority
- vault markdown + registry + evidence chains as the long-term trust boundary

## Current Roadmap

The current roadmap is consolidating repo milestone history, the April 22 compiler roadmap, the recent KSR backlog in the dogfooding vault, and reader-first product-shape research. The KSR page is a recent task-extraction input, not the complete backlog authority:

- active backlog: `BACKLOG.md`
- recent KSR backlog input: `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- current milestone: `MILESTONE.md`
- current merged roadmap rationale: `docs/plans/2026-04-29-consolidated-product-roadmap.md`
- reader product-shape note: `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

Current milestone sequence:

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Complete | CLI, source lifecycle, pack/profile runtime, `knowledge.db`, first KSR-013 slice |
| M1 Operator Workbench And Review Runtime | Complete enough | truth UI, candidates, signals/actions, contradictions, action worker |
| M2 Roadmap And README Consolidation | Complete | merged historical milestones, compiler roadmap, recent KSR input, and reader-product research |
| M3 Reader-First Knowledge Atlas | Done / iterate | reader home, `/ops` split, object source/backlink rail, visual graph map, kind-specific object reader lenses, and reader-oriented search shipped |
| M4 KSR Safety And Hot-Path Hardening | Active | projection labels, hot-path audit, wiring evals, article routing preview, evidence spans, and candidate risk tiers have shipped; deeper enforcement remains |
| M5 Context Pack And Operational Runtime | Active / closeout | session snapshots, context budget, operational runtime state in `/ops` and doctor, provider-facing runtime-state API |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer, claim lifecycle, conflict detection, policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor, query feedback, skill/routine extraction, notebook/raw-source mode |

Current active backlog focus:

- Shipped: `KSR-001` evidence spans, `KSR-002` projection labels, `KSR-003` candidate risk tiers, `KSR-004` session snapshots/context packs, `KSR-014` article routing preview, `KSR-015` dashboard/search hot-path audit, `KSR-017` explicit context budgets, `KSR-018` markdown-aware evidence span backfill, `KSR-022` OVP prime context packs, `KSR-026` workflow wiring eval suite, and the first structured projection repair marker lifecycle.
- Product shipped: readable object page profiles, source/backlink rail, kind-specific reader lenses, visual `/graph` map, and reader-oriented search grouped by kind, evidence, and reason.
- Current: `BL-014` wires runtime state into `/ops`, `ovp doctor`, and `/api/runtime-state` so users can see system health without reading raw logs.
- Product track: reader-first Knowledge Atlas stays a projection layer, not a new state system.

## Domain Packs

The core runtime is now being formalized as a pack-aware platform.

- Built-in standard pack: `research-tech`
- Default compatibility pack: `default-knowledge`
- Runtime selection is exposed through `--pack` and `--profile`
- Third-party packs can be discovered through the `ovp.packs` entry point group or the `OVP_PACK_MANIFESTS` manifest list

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
- `ovp-truth`
  reads object / contradiction / neighborhood truth rows directly from `knowledge.db`
- `ovp-ui`
  launches a local UI. The default `/` entry is the reader-first Knowledge Library; the operator dashboard lives under `/ops`.
- `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- `docs/recipes/research-tech/*.md`

Examples:

```bash
ovp-doctor --pack research-tech --json
ovp-truth objects --vault-dir /path/to/vault
ovp-ui --vault-dir /path/to/vault --port 8787
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

1. Python entry point group: `ovp.packs`
2. Explicit manifest list: `OVP_PACK_MANIFESTS=/path/a.yaml:/path/b.yaml`

The minimum third-party loading chain is:

1. provide a manifest
2. declare `entrypoints.pack`
3. return a `BaseDomainPack`
4. pass `api_version` validation
5. select it through `--pack/--profile`

Hard boundaries currently enforced by core:

- a pack cannot turn semantic retrieval into canonical identity
- a pack cannot treat `knowledge.db` as Authority
- a pack cannot bypass audit/logging
- all derived state must remain rebuildable

## Runtime Model

### Authority Boundary

The system keeps a hard boundary:

- **Authority**: vault markdown + concept registry
- **derived views**: Atlas, MOC, graph, `knowledge.db`, lint, daily delta
- **not Authority**: `knowledge.db`

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

- `absorb` now shells to `ovp_pipeline.commands.absorb`
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
| `ovp-absorb --file <source.md> --dry-run --json` | Preview source lifecycle routing before moving or processing source material |
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

### Operations

| Command | Purpose |
|---|---|
| `ovp-runtime-state --vault-dir <vault> --write --json` | Build the operational runtime state projection from repair markers, pipeline events, and reuse events; writes `60-Logs/runtime-state/current.{json,md}` |
| `/api/runtime-state?write=1` | Local UI/API read endpoint for the same provider-facing runtime-state projection |

### Context packs

| Command | Purpose |
|---|---|
| `ovp-working-memory --vault-dir <vault>` | Write the daily budgeted context pack to `60-Logs/working-memory/YYYY-MM-DD.md` and emit trusted reuse events for selected objects |
| `ovp-prime --vault-dir <vault> --session-id <id>` | Write an OVP Prime session snapshot to `60-Logs/session-snapshots/<id>.md`, refresh `latest.md`, and emit `ovp_prime` reuse events |

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
│   ├── working-memory/
│   ├── session-snapshots/
│   ├── runtime-state/
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
curl -fsSLO https://raw.githubusercontent.com/fakechris/obsidian_vault_pipeline/main/scripts/install-user.sh
less install-user.sh
bash install-user.sh

mkdir -p my-vault
cd my-vault

ovp --check
ovp --full
```

If you prefer the explicit PyPI two-step flow:

```bash
python3 -m pip install --user obsidian-vault-pipeline
python3 -m ovp_pipeline.installer
```

If your Python installation enforces PEP 668, prefer:

```bash
pipx install obsidian-vault-pipeline
```

The installer prefers a writable, safe bin directory that is already on `PATH`; if none is available, it falls back to `~/.local/bin`. It does not edit your shell configuration.

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
- `knowledge.db` is derived retrieval, never a second Authority
- absorb is part of daily automation; refine is powerful and opt-in by default
- Wiki, MOC, dashboard, briefing, graph, reader pages, and context packs are projections that carry explicit projection metadata and must trace back to source/evidence
- reader-facing UI should explain knowledge first, then expose operator/debug detail
- docs must describe what actually ships, not a future architecture sketch

## Related Resources

- [Showcase Vault](https://github.com/fakechris/obsidian_vault_showcase)
- [PyPI](https://pypi.org/project/obsidian-vault-pipeline/)
- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

This document targets: `v0.9.2`
