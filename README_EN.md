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

[📦 PyPI Install](#pip-install) • [🤖 Claude Code Skill](#claude-code-skill) • [📺 View Demo](#view-demo) • [🚀 Quick Start](#quick-start) • [📖 User Guide](#user-guide) • [🇨🇳 中文](README.md)

</div>

---

## Claude Code Skill

This project includes a **Claude Code Skill** that supports natural language triggering of Pipeline operations.

### Usage

```bash
# After cloning the repository, Claude Code automatically loads the skill
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault
claude  # Start Claude Code, skill activates automatically
```

### Trigger Phrases

| You Say | Claude Executes |
|---------|-----------------|
| "run WIGS workflow" | `./60-Logs/scripts/check-consistency.sh` |
| "organize Obsidian Vault" | `ovp --full` |
| "process articles" | `ovp-article --process-inbox` |
| "extract Evergreen" | `ovp-evergreen --recent 7` |
| "update MOC" | `ovp-moc --scan` |
| "quality check" | `ovp-quality --recent 7` |
| "check consistency" | `./60-Logs/scripts/check-consistency.sh` |

### Manual Skill Installation

```bash
# Method 1: Install directly from GitHub
claude skill add https://github.com/fakechris/obsidian_vault_pipeline

# Method 2: Download .skill file
claude skill add ./obsidian-vault-pipeline.skill
```

---

## Two Projects, Two Choices

| Project | Purpose | Best For |
|---------|---------|----------|
| [**obsidian_vault_showcase**](https://github.com/fakechris/obsidian_vault_showcase) | **Demo version with sample data** | Viewing results first, or building on existing content |
| **obsidian_vault_pipeline** | **Pure code template (this project)** | Starting from scratch, understanding Pipeline implementation |

### How to Choose?

| Your Need | Recommended | Reason |
|-----------|-------------|--------|
| Want to see results before committing | [obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase) | 76 real interpretations to browse |
| Want out-of-the-box experience | [obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase) | Clone and open in Obsidian |
| Want complete customization | **This project** | Clean template, no demo data |
| Want to understand implementation | **This project** | Cleaner code structure |
| Want to build on existing content | [obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase) | Content + full scripts |

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

## pip Install

```bash
pip install obsidian-vault-pipeline
```

Available commands after installation:
- `ovp --init` - Initialize configuration
- `ovp --full` - Run full Pipeline
- `ovp-article` - Article processor
- `ovp-github` - GitHub project processor
- `ovp-evergreen` - Evergreen extractor
- `ovp-moc` - MOC updater
- `ovp-quality` - Quality checker

---

## Quick Start (Template Project)

Want to build your own system from scratch? Use this template:

```bash
# 1. Clone the template project
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault

# 2. Initialize configuration (interactive wizard)
ovp --init
# Enter your API Key when prompted

# 3. Install dependencies (if using local scripts)
pip install -r requirements.txt

# 4. Run full pipeline
ovp --full
```

**Result:** Automatically fetch bookmarks, generate interpretations, extract Evergreen notes, update index, all auditable.

### New Feature: Smart Initialization & Environment Check

```bash
# Interactive initialization (one-click config)
ovp --init

# Environment check (verify configuration)
ovp --check
```

**Features:**
- No manual `.env` editing needed, wizard-style configuration
- Auto-detect API Key, Python dependencies, directory structure
- Support for MiniMax / Anthropic / OpenAI providers
- Clear error messages and fix guidance when not configured

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

### Template System (90-Templates/)

5 professional templates included:

| Template | Purpose | Output Location |
|----------|---------|-----------------|
| **Article Interpretation** | 6-dimension analysis | 20-Areas/ |
| **Evergreen Notes** | Atomic knowledge template | 10-Knowledge/Evergreen/ |
| **Project Notes** | PARA project management | 30-Projects/ |
| **MOC Maps** | Knowledge navigation template | 10-Knowledge/Atlas/ |
| **Daily Logs** | Log recording template | 60-Logs/Daily/ |

### View Directory (80-Views/)

Manually maintained data view indexes:

| View | Content | Maintenance |
|------|---------|-------------|
| **Recently Added** | This week/month new content summary | Manual update |
| **Evergreen Index** | Central index of all concept notes | Manual organization |
| **MOC Index** | Knowledge map navigation | Manual maintenance |

| Feature | Description | Benefit |
|---------|-------------|---------|
| **Dynamic Timeout** | Auto-calculate timeout based on article length (1000 chars = 10s, 60-300s adaptive) | Avoid fixed timeout misjudgment |
| **Output Detection** | Judge success based on actual file output, not exit code | Correct identification even on timeout |
| **Auto Loading** | Auto-load `.env`, no manual export needed | Simplified workflow |
| **Transaction Recovery** | Resume from interruption point | Improved reliability |

### 3 Interpretation Modes

| Content Type | Script | Output | Special Capabilities |
|--------------|--------|--------|---------------------|
| Articles | `auto_article_processor.py` | 6-dimension analysis | Auto-categorization |
| GitHub Projects | `auto_github_processor.py` | 13-section deep interpretation | README parsing + ASCII architecture |
| Academic Papers | `auto_paper_processor.py` | 10-section academic structure | arXiv API + reproduction guide |

---

## User Guide

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

---

## License

MIT License - See [LICENSE](LICENSE) for details

---

*Version: 1.0 | Last Updated: 2026-04-03*
