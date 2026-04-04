---
title: "Obsidian Vault Pipeline"
description: "Production-grade automated knowledge management pipeline"
date: 2026-04-03
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Obsidian](https://img.shields.io/badge/Obsidian-Plugin-7C3AED?logo=obsidian)](https://obsidian.md)
[![PyPI](https://img.shields.io/pypi/v/obsidian-vault-pipeline.svg)](https://pypi.org/project/obsidian-vault-pipeline/)

**Production-grade fully automated Obsidian knowledge management pipeline**

Input → Interpret → Quality Check → Refine → Index → Fully auditable workflow

🤖 **NEW: AutoPilot Mode** — Drop files into directory, everything else happens automatically

[🇨🇳 中文](README.md)

</div>

---

## What is this?

Obsidian Vault Pipeline is a **production-grade automated knowledge management system** that transforms fragmented information (bookmarks, articles, notes) into structured evergreen knowledge.

**Core Workflow:**

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Input   │───▶│ Interpret│───▶│  Quality │───▶│ Refine   │───▶│  Index   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
  Bookmarks/      LLM 6-dimension  Auto-scoring   Evergreen       Auto-MOC
  Articles        deep analysis    1-5 points     atomic notes    backlinks
  Auto-fetch
```

**In one sentence:** Automatically fetch your reading content, AI generates in-depth interpretations, extracts core concepts, and builds a navigable knowledge network.

---

## Core Features

### 5 Automated Maintenance Systems

| System | Capability | Automation |
|--------|------------|------------|
| **Quality Gate** | Pre-commit mandatory checks (lines/placeholders/frontmatter) | 100% |
| **WIGS Integrity** | 5-layer consistency check + auto-repair | 95% |
| **Backlink Maintenance** | Auto-detect broken links, update MOC | 95% |
| **Evergreen Extraction** | LLM auto-extract core concepts | 90% |
| **Runtime Audit** | JSONL structured logs + transaction tracking | 100% |
| **🆕 AutoPilot** | Directory watcher + auto-queue + LLM quality scoring | Fully Auto |

### 6-Dimension Quality Model

Every interpretation includes:
1. **One-sentence definition** - Clear core concept statement
2. **Detailed explanation** - Complete What/Why/How
3. **Key details** - ≥3 technical points
4. **Architecture diagram** - ASCII visualization
5. **Actionable advice** - ≥2 practical recommendations
6. **Related knowledge** - [[Bidirectional links]]

---

## View Demo

**Want to see the final result of this Pipeline?**

👉 **[obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase)** - Complete demo

This showcase contains:
- 🌳 **8 Evergreen atomic notes** - Core concepts (AI Agent, Agent Architecture, etc.)
- 📚 **76 in-depth interpretations** - GitHub projects, technical articles
- 🗺️ **3 MOC knowledge maps** - AI, Tools, Programming navigation
- 🔗 **Complete bidirectional link network** - Concept relationships

**Usage:**
1. **View only** → Browse directly on GitHub
2. **Download & experience** → Clone and open in Obsidian
3. **Develop on top** → Modify content, connect your own API Key to continue generating

---

## Two Ways to Use

| Method | Recommended For | Difficulty |
|--------|-----------------|------------|
| **[obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase)** | Want to see results first, or build on existing content | ⭐ Out-of-the-box |
| **[obsidian_vault_pipeline](https://github.com/fakechris/obsidian_vault_pipeline)** (this project) | Want to start from scratch, fully customize | ⭐⭐ Requires setup |

---

## pip Install (Recommended)

```bash
pip install obsidian-vault-pipeline
```

Available commands after installation:

| Command | Function |
|---------|----------|
| `ovp --init` | Initialize configuration (interactive wizard) |
| `ovp --check` | Check environment configuration |
| `ovp --full` | Run full Pipeline |
| `ovp-article --process-inbox` | Process articles in 50-Inbox/01-Raw/ |
| `ovp-evergreen --recent 7` | Extract Evergreen notes from last 7 days |
| `ovp-moc --scan` | Scan and update MOC index |
| `ovp-quality --recent 7` | Quality check |
| `ovp-autopilot` | 🆕 Start AutoPilot daemon mode |

---

## 30-Second Quick Start

```bash
# 1. Install
pip install obsidian-vault-pipeline

# 2. Create vault directory and enter
mkdir my-vault && cd my-vault

# 3. Initialize configuration (wizard)
ovp --init

# 4. Place articles in 50-Inbox/01-Raw/
mkdir -p 50-Inbox/01-Raw
echo "# Test Article\n\nContent..." > 50-Inbox/01-Raw/test.md

# 5. Run Pipeline
ovp --full
```

**Result:** Auto-generate interpretations to `20-Areas/`, extract Evergreen to `10-Knowledge/`, update MOC index.

---

## 🚀 AutoPilot Mode

> 🤖 **Drop files into directory, everything else happens automatically**

AutoPilot is the fully automated form of the Pipeline. Once started, it will:
1. **Monitor** `50-Inbox/01-Raw/` for new files
2. **Auto-process** - Generate interpretation → LLM quality scoring → Extract Evergreen → Update MOC
3. **Quality gate** - Auto-retry if below threshold, ensuring output quality
4. **Auto-commit** - Automatically `git commit` when complete

### Start AutoPilot

```bash
# Basic start (with cost warning confirmation)
ovp-autopilot --watch=inbox --parallel=1

# Skip confirmation (if you understand the cost risks)
ovp-autopilot --yes

# Multi-concurrency processing (watch the costs!)
ovp-autopilot --parallel=2 --quality=3.5
```

### ⚠️ Cost Warning

AutoPilot mode consumes **SIGNIFICANT TOKENS**:
- 3-4 LLM calls per article
- Deep interpretation: ~4K-8K tokens
- Quality scoring: ~2K-4K tokens
- Evergreen extraction: ~2K-4K tokens

**Recommendations**:
- Use monthly Coding Plan
- Start with `--parallel=1` for testing
- Test with small batches first

---

## Claude Code Skill (Optional)

This project includes a **Claude Code Skill** that supports natural language triggering of Pipeline operations.

**Usage:**

```bash
# After cloning the repository, Claude Code automatically loads the skill
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault
claude  # Start Claude Code, skill activates automatically
```

**Trigger Phrases:**

| You Say | Claude Executes |
|---------|-----------------|
| "run WIGS workflow" | `./60-Logs/scripts/check-consistency.sh` |
| "organize Obsidian Vault" | `ovp --full` |
| "process articles" | `ovp-article --process-inbox` |
| "extract Evergreen" | `ovp-evergreen --recent 7` |
| "update MOC" | `ovp-moc --scan` |
| "quality check" | `ovp-quality --recent 7` |

---

## Directory Structure (PARA Method)

```
my-vault/
├── 00-Polaris/
│   ├── README.md              # Top of Mind (manual weekly update)
│   └── Home.md                # [Entry navigation] Obsidian homepage
├── 10-Knowledge/
│   ├── Evergreen/             # [Auto] LLM-extracted atomic notes
│   └── Atlas/
│       ├── MOC-Index.md       # [Auto] Global MOC index
│       ├── MOC-AI-Research.md # [Auto] AI research field map
│       ├── MOC-Tools.md       # [Auto] Tools field map
│       ├── MOC-Investing.md   # [Auto] Investing field map
│       └── MOC-Programming.md # [Auto] Programming field map
├── 20-Areas/                  # [Auto+Manual] Interpretation output
│   ├── AI-Research/Topics/    # YYYY-MM/ subdirectories
│   ├── Tools/
│   ├── Investing/
│   └── Programming/
├── 30-Projects/               # [Manual] Projects with deadlines
├── 40-Resources/              # [Manual] Reference library
├── 50-Inbox/
│   ├── 01-Raw/               # [Auto] Raw articles
│   └── Processing-Queue.md   # [Manual] Processing queue
├── 60-Logs/
│   ├── scripts/               # [Direct use] Core scripts
│   ├── pipeline.jsonl        # [Auto] Unified structured logs
│   └── transactions/         # [Auto] Transaction states
├── 70-Archive/                # [Manual] Archived completed projects
├── 80-Views/                  # [Auto] Data views
├── 90-Templates/              # [Built-in] Template library
└── .claude/
    ├── skills/                # [Built-in] Claude Code Skill
    └── precommit-check.sh     # Pre-commit check script
```

---

## Detailed User Guide

### First Time Setup

```bash
# Step 0: Interactive initialization (configure API Key)
ovp --init

# Verify environment is configured correctly
ovp --check
```

### Daily Operations

```bash
# Daily automatic processing (recommend adding to crontab)
ovp --full

# Preview mode (see what will be processed without executing)
ovp --full --dry-run

# Process last 30 days (batch history)
ovp --pinboard-days 30
```

### Step-by-step Operations

```bash
# Step 1: Fetch Pinboard bookmarks
ovp --step pinboard --pinboard-days 7

# Step 2: Migrate Clippings
ovp --step clippings

# Step 3: Generate interpretations
ovp --step articles

# Step 4: Quality check
ovp-quality --recent 7

# Step 5: Extract Evergreen
ovp-evergreen --recent 7

# Step 6: Update MOC index
ovp-moc --scan
```

### Special Content Processing

```bash
# GitHub project deep interpretation
ovp-github --single https://github.com/fakechris/obsidian_vault_pipeline

# arXiv paper interpretation
ovp-paper --arxiv https://arxiv.org/abs/2401.12345
```

---

## Quality Gate

### Pre-commit Mandatory Checks

```bash
./.claude/precommit-check.sh
```

**Check contents:**
- ✅ File lines ≥ 150 (configurable)
- ✅ No forbidden placeholders (CN/EN)
- ✅ Correct frontmatter format
- ✅ Single commit ≤ 10 files

---

## WIGS Integrity Check

**Workflow Integrity Guarantee System** - 5-layer check architecture ensuring data processing integrity.

```bash
# Run 5-layer consistency check
./60-Logs/scripts/check-consistency.sh

# Preview repair plan
./60-Logs/scripts/repair.sh --dry-run

# Auto-repair low-risk issues
./60-Logs/scripts/repair.sh --auto
```

| Layer | Check Content | Auto-repair |
|-------|---------------|-------------|
| **L1** | Incomplete transactions | ❌ Manual confirmation |
| **L2** | Orphan Evergreen / broken links | ⚠️ Partial auto |
| **L3** | Ingestion consistency | ✅ Auto (duplicate files) |
| **L4** | Areas integrity / Git commit | ❌ Manual |
| **L5** | Archive layer | ❌ Manual |

---

## Configuration Reference

### .env Configuration Template

```bash
# LLM API (Required)
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
AUTO_VAULT_MODEL=minimax/MiniMax-M2.5

# Pinboard (Optional)
PINBOARD_TOKEN=username:token

# Proxy (Optional)
HTTP_PROXY=http://127.0.0.1:7897
```

### Cost Estimation

| Provider | Cost | Chinese Support | Recommended For |
|----------|------|-----------------|-----------------|
| **MiniMax** | ¥0.01/1K tokens | Excellent | Daily batch |
| **Anthropic** | $0.03/1K tokens | Good | High-quality deep |
| **OpenAI** | $0.01-0.03/1K tokens | Good | Alternative |

- Process 10 articles: ~¥1-3 RMB
- Process 100 GitHub projects: ~¥10-30 RMB

---

## Manual Maintenance Checklist

| Frequency | Task | Command/File |
|-----------|------|--------------|
| Daily | Run Pipeline | `ovp --full` |
| Daily | Check system status | `./60-Logs/scripts/check-consistency.sh` |
| Weekly | Update Top of Mind | Edit `00-Polaris/README.md` |
| Weekly | Review quality reports | View `60-Logs/quality-reports/*.md` |
| Monthly | Archive old files | `obsidian move` to `70-Archive/` |

---

## Related Repositories

| Repository | Purpose | Link |
|------------|---------|------|
| **obsidian_vault_showcase** | Complete demo (with sample data) | [GitHub](https://github.com/fakechris/obsidian_vault_showcase) |
| **obsidian_vault_pipeline** | Template project (this repo) | [GitHub](https://github.com/fakechris/obsidian_vault_pipeline) |
| **PyPI** | pip install package | [PyPI](https://pypi.org/project/obsidian-vault-pipeline/) |

---

## License

MIT License - See [LICENSE](LICENSE) for details

---

*Version: 1.0 | Last Updated: 2026-04-03*
