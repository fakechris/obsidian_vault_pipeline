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

Current document version: `v0.13.0`

Primary docs:

- [Architecture](ARCHITECTURE.md) — durable knowledge model (six core terms; 250-line cap)
- [Runtime](RUNTIME.md) — pipeline stages and CLIs by stage
- [Packs](PACKS.md) — Core / Domain Pack / Workflow Profile
- [Product Surfaces](PRODUCT_SURFACES.md) — UI / MCP / CLI / export
- [Glossary](GLOSSARY.md) — every other domain term, mapped back to the six core
- [Milestone](MILESTONE.md) ([简体中文](MILESTONE.zh-CN.md))
- [Active Backlog](BACKLOG.md)
- 简体中文: [架构](ARCHITECTURE.zh-CN.md)

## What This Is

Obsidian Vault Pipeline is not a loose collection of scripts, and it is not only RAG over Markdown. It is a local knowledge state runtime built around an Obsidian vault:

- **Capture** receives Pinboard, Clippings, raw Markdown, papers, GitHub repos, and web pages while keeping source lifecycle traceable.
- **Compile** turns material into deep dives, candidates, claims, evidence, relations, contradictions, registry rows, and graph rows.
- **Reuse** projects compiled knowledge into reader atlas pages, object pages, graph views, briefings, search, context packs, writing prompts, and the operator workbench.

Internally the runtime executes six pipeline stages: Ingest → Interpret → Absorb → Refine → Normalize → Derive (see [RUNTIME](RUNTIME.md)). The product narrative is Capture → Compile → Reuse. The state model — Sources, Candidates, Canonical State, Projections, Access Surfaces, with Governance as the cross-cutting control plane — is documented in [ARCHITECTURE](ARCHITECTURE.md).

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

- Capture → Compile → Reuse explains the product value
- The state model (Source / Candidate / Canonical State / Projection / Access Surface, with Governance as cross-cutting control) makes the trust boundary explicit; see [ARCHITECTURE](ARCHITECTURE.md)
- The six-stage runtime makes orchestration, identity normalization, and projection rebuilds explicit; see [RUNTIME](RUNTIME.md)
- `research-tech` is the first standard built-in domain pack
- `default-knowledge` is retained as a compatibility pack for older vaults
- The Pack API turns future domains into installable packs rather than hardcoded branches; see [PACKS](PACKS.md)

So the project is no longer just a Vault automation repo. It is now:

> a reader-first, evidence-backed knowledge atlas over an auditable knowledge state runtime

with:

- `research-tech` as the first explicit built-in standard pack
- `default-knowledge` retained as the default compatibility pack
- `knowledge.db` as a Projection (rebuildable from Canonical State, never authoritative)
- vault markdown + registry + evidence chains as Canonical State (the long-term trust boundary)

## Current Roadmap

OVP is evolving from a personal Zettelkasten into a typed knowledge platform — reader-first for humans, programmable for agents, extensible through domain packs.

- active backlog: `BACKLOG.md`
- current milestone: `MILESTONE.md`
- current merged roadmap rationale: `docs/plans/2026-04-29-consolidated-product-roadmap.md`
- reader product-shape note: `docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

Current milestone sequence:

| Milestone | Status | Meaning |
| --- | --- | --- |
| M0–M3 | Done | Foundation, operator workbench, roadmap consolidation, reader-first atlas |
| M4 KSR Safety And Hot-Path Hardening | Done | projection labels, hot-path audit, wiring evals, evidence spans, candidate risk, JSONL streaming, projection lifecycle hardening |
| M5 Context Pack And Operational Runtime | Done | session snapshots, context budget, runtime state, runtime-state API, action queue health |
| M5a Quality And Dedup Hardening | Done | concept dedup pipeline integration, promote semantic guard, historical data cleanup |
| M8 Type Unification And Extraction Quality | Active | unified object kind taxonomy, Layer 1 entity_type, body-size-aware extraction, quote-grounding, single-pass LLM refactor |
| M9 Pack As Domain Ontology | Next | pack-defined object kind specs, typed relation constraints, schema registry |
| M10 Operational Knowledge Layer | Later | action types, permissions, cross-entity aggregation, decision memory |
| M11 Source Authority And Cross-Source Identity | Done | typed source-authority providers, entity layer (twitter_author / github_project / github_user / person / organization), runtime resolver, refresh wrapper, db backup (PRs #112–#124) |
| M12 Extraction-Time Entity Prime And Auto-Wikilink | Done | entity_aliases view, LLM extractor primed with known entities, auto-wikilink CLI (PRs #126–#128) |
| M13 Synthesis Layer (Crystal) | Done | Louvain communities + LLM-synthesized crystals + contradiction crystals + append-only versioning (PRs #130–#133, closes the L3 gap with NM 0.8) |
| M14 Intake Hardening (BL-058) | Done | URL preservation through deep-dive, deprecate legacy 13-section LLM rewrite, **global URL dedup across the active staging chain** (Clippings + 4 50-Inbox stages), audit-event `stage` field, fidelity-sample + prompt-ab measurement CLIs (PRs #170–#172, v0.13.0) |

Recent major changes (PRs #98–#124):

- JSONL streaming hardening, advisory file locks, runtime-state API fixes (#98–#100)
- Concept dedup pipeline + promote semantic guard, historical Evergreen cleanup (#101)
- Typed StepResult contracts + 4 pipeline guardrails (#109–#111)
- Liberate evergreen extractor prompt (#112) — no more 3-5 cap on atomic units per article
- **Source authority subsystem** (#113/#114): typed SignalProvider Protocol, domain/author whitelists, GitHub/arXiv/Twitter/Substack signals, yaml overrides, LLM-judge for new domains
- **Entity layer** (#115/#119/#120/#121/#123): twitter_author + github_project + github_user backfills, identity merge with person/organization split, runtime resolver — 1497 entities total on the OVP vault (521 twitter + 922 github + 54 person/organization), ~$0.10 one-shot
- **Operational glue** (#117/#122): `ovp-backup-db` SQLite online-backup snapshots, `ovp-refresh-source-authority` chained refresh + launchd plist
- 12 entity-layer review fixes (#124): read-side write side effects, identity-merge backlinks, lock race, append-only history, GitHub bare profile URLs, etc.

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
- a pack cannot treat `knowledge.db` as Canonical State
- a pack cannot bypass audit/logging
- all Projections must remain rebuildable

## Runtime Model

### Canonical State Boundary (full definition: [ARCHITECTURE](ARCHITECTURE.md))

The system keeps a hard boundary:

- **Canonical State**: vault markdown + concept registry + evidence + audit
- **Projections**: Atlas, MOC, graph, `knowledge.db`, lint, daily delta, crystals
- **not Canonical State**: `knowledge.db`

`knowledge.db` is a Projection. It stores:

- page FTS
- structured links
- mirrored raw sidecars
- timeline / audit events
- deterministic section embeddings
- read-only query / serve surfaces

It is rebuildable and does not own canonical identity resolution.

### The Six Pipeline Stages (full description: [RUNTIME](RUNTIME.md))

| Stage | Responsibility | Representative commands | Can the LLM make major decisions here? |
|---|---|---|---|
| Ingest | Normalize incoming material | `ovp --step pinboard` `ovp --step clippings` `ovp-article` | No |
| Interpret | Produce deep interpretations | `ovp-article` `ovp-github` `ovp-paper` | Yes, with constrained output |
| Absorb | Compile interpretations into lifecycle actions | `ovp-absorb` `ovp-evergreen` | Yes, but only through structured results |
| Refine | Cleanup and breakdown existing notes | `ovp-cleanup` `ovp-breakdown` | Yes, but execution is controlled |
| Normalize | Maintain registry / aliases / identity merges / contradiction detection (formerly Canonical) | `ovp-rebuild-registry` `ovp-merge-identities` `ovp-link-entities` `ovp-resolve-contradictions` | No |
| Derive | Build Projections — retrieval / graph / crystals / lint | `ovp-knowledge-index` `ovp-graph` `ovp-synthesize-community-crystals` `ovp-lint` | No |

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
→ dedup
→ note_type_normalize
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
→ dedup
→ note_type_normalize
→ registry_sync
→ moc
→ refine
→ knowledge_index
```

Important details:

- `absorb` shells to `ovp_pipeline.commands.absorb` and emits `promoted_slugs` for downstream steps
- `dedup` runs post-absorb concept deduplication scoped to recently promoted slugs (trigram-Jaccard similarity)
- `note_type_normalize` normalizes note_type metadata across Evergreen files
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
| `ovp-concept-dedup --vault-dir <vault> --threshold 0.82` | Find and propose concept deduplication clusters |
| `ovp-concept-dedup --vault-dir <vault> --apply` | Apply deduplication proposal (archive losers, rewrite wikilinks) |
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
| `ovp-runtime-state --vault-dir <vault> --write --json` | Build the operational runtime state projection from repair markers, workflow actions, pipeline events, and reuse events; writes `60-Logs/runtime-state/current.{json,md}` |
| `GET /api/runtime-state` | Local read endpoint for the provider-facing runtime-state projection; prefers the materialized `60-Logs/runtime-state/current.json` and falls back to rebuild when missing |
| `POST /api/runtime-state` | Refresh and write the materialized runtime-state projection |

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
- vault files + registry define Canonical State
- `knowledge.db` is a Projection, never an additional Canonical State
- absorb is part of daily automation; refine is powerful and opt-in by default
- Wiki, MOC, dashboard, briefing, graph, reader pages, and context packs are projections that carry explicit projection metadata and must trace back to source/evidence
- reader-facing UI should explain knowledge first, then expose operator/debug detail
- docs must describe what actually ships, not a future architecture sketch

## Related Resources

- [Showcase Vault](https://github.com/fakechris/obsidian_vault_showcase)
- [PyPI](https://pypi.org/project/obsidian-vault-pipeline/)
- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

This document targets: `v0.9.3`
