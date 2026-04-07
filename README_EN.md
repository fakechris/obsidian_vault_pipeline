---
schema_version: "1.0.0"
note_id: readme_en-5d661efc
title: "Obsidian Vault Pipeline"
description: "Production-grade automated knowledge management pipeline"
date: 2026-04-06
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Obsidian](https://img.shields.io/badge/Obsidian-Plugin-7C3AED?logo=obsidian)](https://obsidian.md)
[![PyPI](https://pypi.org/pypi/obsidian-vault-pipeline/)](https://pypi.org/project/obsidian-vault-pipeline/)

**Production-grade fully automated Obsidian knowledge management pipeline**

Input → Interpret → Quality → Refine → Index → Fully auditable workflow

[🇨🇳 中文](README.md)

</div>

---

## What Problem Does This Solve?

**Problem:** You have hundreds of bookmarks, articles, and papers scattered everywhere, never truly digested. They sit in your vault like code never compiled into running knowledge.

**Solution:** Treat LLM as the "programmer" of your knowledge base, Obsidian as the IDE, and the Wiki as the codebase. Automate:
- Fetch raw content
- Generate structured deep interpretations
- Extract reusable core concepts
- Maintain bidirectional links between knowledge pieces

> 🙏 **Credit**: [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

## Architecture: Tool Lineage

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Operation Layer                            │
│  ovp --full          One-command full pipeline (daily use)                   │
│  ovp-autopilot       Auto-pilot mode (continuous monitoring)                 │
│  ovp --step X        Single step execution (debug/customize)                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Tool Chain Overview                             │
│                                                                             │
│  Input Sources                                                              │
│  ├── Pinboard Bookmarks ──────┐                                             │
│  ├── Kindle Clippings ────────┼──► ovp --step pinboard/clippings            │
│  └── 50-Inbox/01-Raw/ ───────┘                                             │
│                                                                             │
│  Content Processing                                                         │
│  ├── ovp-article    Raw → Deep Interpretation                               │
│  ├── ovp-github    GitHub Project → 13-Section Deep Dive                    │
│  └── ovp-paper     arXiv Paper → Academic Analysis                          │
│                                                                             │
│  Quality Assurance                                                          │
│  ├── ovp-quality    6-Dimension quality scoring (1-5)                      │
│  └── ovp-lint       Pre-commit checks (lines/placeholders/frontmatter)      │
│                                                                             │
│  Knowledge Refinement                                                      │
│  ├── ovp-evergreen  Extract atomic notes from interpretations                │
│  └── ovp-query-to-wiki  Archive Q&A to new concepts                         │
│                                                                             │
│  Index Maintenance                                                          │
│  ├── ovp-moc        Update Area MOCs / Atlas Index                         │
│  ├── ovp-migrate-links  Scan/fix broken wikilinks                          │
│  └── ovp-rebuild-registry  Reconcile Evergreen and registry                │
│                                                                             │
│  Lifecycle Maintenance                                                      │
│  ├── ovp-promote-candidates  promote / merge / reject candidates           │
│  ├── ovp-graph      Build full graph / daily delta                         │
│  └── ovp-repair     Repair transactions / autopilot / registry state       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tool Command Reference

### One-Command Run (Daily)

| Command | Solves What | Use Case |
|---------|-------------|----------|
| `ovp --full` | One-command full pipeline | Daily scheduled task |
| `ovp --full --dry-run` | Preview what will be processed | Pre-change check |
| `ovp --check` | Verify API Key and config | Post-setup verification |

### AutoPilot Mode (Fully Automated)

| Command | Solves What | Use Case |
|---------|-------------|----------|
| `ovp-autopilot --watch=inbox --parallel=1` | Monitor directory, auto-process | Continuous running |
| `ovp-autopilot --yes` | Skip cost warning confirmation | Repeated execution after confirmation |
| `ovp-autopilot --parallel=2 --quality=3.5` | High concurrency + high quality | Batch processing (expensive) |

**AutoPilot Workflow:**
```
File enters 50-Inbox/01-Raw/
        │
        ▼
  ┌─────────────┐
  │  Watcher    │  ← watchdog monitors directory
  └─────────────┘
        │
        ▼
  ┌─────────────┐
  │  Queue      │  ← SQLite persistent queue
  └─────────────┘
        │
        ▼
  ┌─────────────┐     ┌─────────────┐
  │  Interpret  │────▶│  Quality    │
  └─────────────┘     └─────────────┘
        │                   │
        │  ✗ Below threshold│ ✓ Pass
        ▼                   ▼
  ┌─────────────┐     ┌─────────────┐
  │  Auto-retry │     │ Extract     │
  └─────────────┘     │ Evergreen   │
                       └─────────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │ Update MOC │
                            └─────────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │ Git commit  │
                            └─────────────┘
```

### Single Step Execution (Debug/Customize)

| Command | Solves What |
|---------|-------------|
| `ovp --step pinboard` | Fetch Pinboard bookmarks |
| `ovp --step clippings` | Migrate Kindle Clippings |
| `ovp --step articles` | Process Raw → generate interpretations |
| `ovp --step quality` | Quality scoring |
| `ovp --step evergreen` | Extract core concepts |
| `ovp --step moc` | Update MOC index |

### Specialized Processors

| Command | Solves What |
|---------|-------------|
| `ovp-github --single URL` | GitHub project → 13-section deep dive |
| `ovp-paper --arxiv URL` | arXiv paper → academic analysis |
| `ovp-evergreen --recent 7` | Extract Evergreen from recent interpretations |
| `ovp-moc --update-atlas-from-registry` | Rebuild Atlas Index from registry |
| `ovp-quality --recent 7` | Batch quality scoring |

### Maintenance Tools

| Command | Solves What |
|---------|-------------|
| `ovp-lint` | Pre-commit mandatory checks |
| `ovp-repair --transactions --autopilot --registry` | Repair stuck transactions / queue state / registry drift |
| `ovp-migrate-links --scan` | Scan broken wikilinks |
| `ovp-migrate-links --write` | Apply high-confidence link fixes |
| `ovp-rebuild-registry --json` | Inspect Evergreen / registry drift |
| `ovp-promote-candidates review` | Review candidate lifecycle |
| `ovp-graph --daily today` | Generate daily graph delta |
| `ovp-query-to-wiki --create-evergreen "name"` | Create new note from Q&A |

---

## AutoPilot Scenario Guide

### Scenario 1: Daily Incremental Processing (Recommended)

```bash
# Run every morning
ovp --full

# Or automate with cron
# crontab -e
# 0 8 * * * /path/to/ovp --full --vault-dir /path/to/vault
```

### Scenario 2: Fully Autonomous AutoPilot

```bash
# Start background daemon
ovp-autopilot --watch=inbox --parallel=1 --yes

# Recommended: run in tmux/screen, or persist stdout yourself
ovp-autopilot --watch=inbox --parallel=1 --yes | tee autopilot.log
```

### Scenario 3: Batch Historical Processing

```bash
# Process last 30 days of Pinboard
ovp --pinboard-days 30

# Process specific date range
ovp --pinboard-history 2026-01-01 2026-03-31
```

### Scenario 4: Manual Single-Step Debugging

```bash
# Only fetch bookmarks, don't process
ovp --step pinboard

# Only generate interpretations, don't quality check
ovp --step articles

# Start from quality check
ovp --from-step quality
```

### Scenario 5: Single Project Analysis

```bash
# GitHub project
ovp-github --single https://github.com/anthropics/claude-code

# arXiv paper
ovp-paper --arxiv https://arxiv.org/abs/2403.03367
```

---

## Directory Structure (PARA Method)

```
vault/
├── 50-Inbox/01-Raw/           # [Input] Raw documents (bookmarks/articles)
├── 20-Areas/                   # [Output] Deep interpretations
│   └── {AI-Research,Tools,Investing,Programming}/
│       └── Topics/YYYY-MM/
├── 10-Knowledge/
│   ├── Evergreen/              # [Refined] Atomic notes
│   └── Atlas/                 # [Indexed] MOC knowledge maps
│       ├── Atlas-Index.md
│       ├── concept-registry.jsonl
│       └── alias-index.json
├── 60-Logs/
│   ├── pipeline.jsonl         # Structured logs
│   ├── transactions/          # Transaction states
│   ├── quality-reports/       # Quality reports
│   └── daily-deltas/          # Daily graph deltas
└── 70-Archive/               # [Archived] Completed content
```

---

## 6-Dimension Quality Model

Every interpretation includes:

| Dimension | Description |
|-----------|-------------|
| One-sentence definition | Precise core concept summary |
| Detailed explanation | Complete What/Why/How analysis |
| Key details | ≥3 technical points |
| Architecture diagram | ASCII visualization (if applicable) |
| Actionable advice | ≥2 practical recommendations |
| Related knowledge | [[Bidirectional links]] |

---

## 30-Second Quick Start

```bash
# 1. Install
pip install obsidian-vault-pipeline

# 2. Initialize
ovp --init

# 3. Add articles
mkdir -p 50-Inbox/01-Raw
echo "# Test\n\nContent" > 50-Inbox/01-Raw/test.md

# 4. Run
ovp --full
```

---

## Configuration Reference

```bash
# .env required config
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic

# Optional config
PINBOARD_TOKEN=username:token
HTTP_PROXY=http://127.0.0.1:7897
```

---

## Related Resources

| Resource | Description |
|----------|-------------|
| [showcase](https://github.com/fakechris/obsidian_vault_showcase) | Complete demo vault |
| [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | Core philosophy |
| [PyPI](https://pypi.org/project/obsidian-vault-pipeline/) | pip install package |

---

*Version: 2.0 | Last Updated: 2026-04-06*
