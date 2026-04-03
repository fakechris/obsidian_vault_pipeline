# PARA + Zettelkasten Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Obsidian](https://img.shields.io/badge/Obsidian-Plugin-7C3AED?logo=obsidian)](https://obsidian.md)

**中文** | [English](#english-documentation)

全自动知识管理 Pipeline，支持 Pinboard + Obsidian Web Clipper 双输入源

</div>

---

## 🚀 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault
```

### 2. 配置环境

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 填入你的 API 密钥
nano .env
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 运行 Pipeline

```bash
# 处理最近 7 天的内容（Pinboard + Clippings）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

---

## 📚 目录结构

```
📁 00-Polaris/               # 北极星层 - 当前关注重点
   └── README.md            # Top of Mind
📁 10-Knowledge/             # 知识层
   ├── Atlas/              # 知识地图 (MOC)
   ├── Evergreen/          # 常青笔记（原子化概念）
   ├── Literature/         # 文献笔记
   └── Sources/            # 原文存储
📁 20-Areas/                 # 责任领域
   ├── AI-Research/       # AI 研究
   ├── Tools/             # 工具评测
   ├── Investing/          # 投资思考
   └── Programming/        # 编程技术
📁 30-Projects/              # 活跃项目
📁 40-Resources/             # 资源
📁 50-Inbox/                 # 收集箱（三层捕获）
   ├── 00-Capture/        # 快速捕获
   ├── 01-Raw/           # 原始文章
   ├── 02-Processing/    # 待处理
   └── 03-Processed/     # 已处理
📁 60-Logs/                  # 日志和自动化脚本
   └── scripts/          # 8 个核心脚本
📁 70-Archive/               # 归档
```

---

## 🤖 自动化 Pipeline

### 6 步完整流程

```bash
# 完整 Pipeline（推荐）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

| 步骤 | 脚本 | 功能 | 说明 |
|------|------|------|------|
| 1 | `pinboard-processor.py` | 获取 Pinboard 书签 | 按天查询，自动去重 |
| 2 | `clippings_processor.py` | 处理 Obsidian Web Clipper | 清理文件名，obsidian move 迁移 |
| 3 | `auto_article_processor.py` | 生成深度解读 | LLM 6 维度分析 |
| 4 | `batch_quality_checker.py` | 质量检查 | 自动评分（1-5 分）|
| 5 | `auto_evergreen_extractor.py` | 提取 Evergreen | 原子化概念笔记 |
| 6 | `auto_moc_updater.py` | 更新 MOC | 自动索引新文件 |

### 常用命令

```bash
# 处理最近 N 天（Pinboard + 完整流程）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --pinboard-days 30

# 处理历史日期范围
python3 60-Logs/scripts/unified_pipeline_enhanced.py \
  --pinboard-history 2026-02-01 2026-02-28

# 仅处理 Clippings
python3 60-Logs/scripts/unified_pipeline.py --step clippings

# 仅更新 MOC 索引
python3 60-Logs/scripts/unified_pipeline.py --step moc

# 预览模式（不实际执行）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full --dry-run
```

---

## 🎯 核心方法论

### PARA 方法

| 层级 | 用途 | 示例 |
|------|------|------|
| **Projects** | 有明确目标和时间 | "完成 AI Agent 调研" |
| **Areas** | 持续维护的领域 | AI 研究、工具评测 |
| **Resources** | 参考资料 | 模板、代码片段 |
| **Archive** | 归档 | 已完成项目 |

### 三层笔记架构

1. **Session Memory (00-Polaris)**
   - Top of Mind：当前关注重点
   - 每周回顾更新

2. **Knowledge Graph (10-Knowledge + 20-Areas)**
   - Evergreen：原子化永久笔记
   - Literature：文献笔记
   - Atlas：知识地图

3. **Ingestion Pipeline (50-Inbox)**
   - 快速捕获 → 原始文章 → 待处理 → 已处理

---

## 📝 深度解读标准

### 6 维度质量模型

每篇深度解读必须包含：

```markdown
# 一句话定义
[核心概念的精准概括]

# 详细解释
## 是什么？
## 为什么重要？
## 如何工作？

# 重要细节
## 细节 1
## 细节 2
## 细节 3

# 架构图 / 流程图
```
[ASCII 图表]
```

# 行动建议
1. 建议 1
2. 建议 2

# 关联知识
- [[Related Concept]]
```

### 质量评分

| 维度 | 权重 | 合格标准 |
|------|------|---------|
| 一句话定义 | 1-5 分 | 有 |
| 详细解释 | 1-5 分 | 完整 |
| 重要细节 | 1-5 分 | ≥3 个 |
| 架构图 | 1-5 分 | 有（如适用）|
| 行动建议 | 1-5 分 | ≥2 条 |
| 关联知识 | 1-5 分 | 有 [[wikilink]] |

**总分 ≥ 18**（平均 3+）为合格

---

## 🛡️ WIGS 原则

**W**orkflow **I**ntegrity **G**uarantee **S**ystem

### 核心规则

1. **强制使用 `obsidian move`**
   ```bash
   # ✅ 正确
   obsidian move file="source.md" to="dest/"

   # ❌ 错误 - 会破坏 wiki-links
   mv source.md dest/
   ```

2. **显式状态文件**
   - 使用 `Processing-Queue.md`（版本控制）
   - 不使用 `.hidden_state.json`（易丢失）

3. **事务完整性**
   - 每个 Pipeline 创建事务
   - 中断后可恢复

4. **幂等处理**
   - 自动跳过已处理文件
   - 可安全重跑

---

## ⚙️ 环境配置

### 必需配置

```bash
# .env 文件
PINBOARD_TOKEN=your_username:your_api_token
AUTO_VAULT_API_KEY=your_llm_api_key
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
```

### API 提供商选择

| 提供商 | 成本 | 质量 | 配置 |
|--------|------|------|------|
| MiniMax | 低 | 高 | `api.minimaxi.com/anthropic` |
| Anthropic | 中 | 最高 | `api.anthropic.com` |
| OpenAI | 中 | 高 | `api.openai.com/v1` |

---

## 📊 日志与监控

### 统一日志

位置：`60-Logs/pipeline.jsonl`

```json
{
  "timestamp": "2026-04-02T12:00:00",
  "session_id": "20260402-120000-abc123",
  "event_type": "pipeline_completed",
  "results": {...}
}
```

### 事务管理

```bash
# 列出未完成事务
./60-Logs/scripts/txn.sh list

# 查看事务详情
./60-Logs/scripts/txn.sh show <txn-id>
```

### 一致性检查

```bash
# 5 层架构检查
./60-Logs/scripts/check-consistency.sh

# 自动修复
./60-Logs/scripts/repair.sh --auto
```

---

## 🔄 GitHub Actions

`.github/workflows/daily-pipeline.yml`：

```yaml
# 每天上午 9 点自动运行
schedule:
  - cron: '0 9 * * *'
```

配置 Secrets：
- `PINBOARD_TOKEN`
- `AUTO_VAULT_API_KEY`
- `AUTO_VAULT_API_BASE`

---

## 🛠️ 开发指南

### 添加新脚本

1. 放入 `60-Logs/scripts/`
2. 遵循 WIGS 原则
3. 使用 `PipelineLogger` 记录日志
4. 集成到 `unified_pipeline_enhanced.py`

### Skill 开发

```markdown
# skills/your-skill.md

## 触发条件
当用户说...时触发

## 执行步骤
1. 步骤 1
2. 步骤 2

## 示例
用户: "..."
→ 执行...
```

---

## ❓ 常见问题

### Q: 没有 Pinboard 账号怎么办？
A: 可以仅使用 Clippings 功能：
```bash
python3 60-Logs/scripts/unified_pipeline.py --full
```

### Q: 如何跳过某个步骤？
A: 使用 `--from-step`：
```bash
python3 60-Logs/scripts/unified_pipeline_enhanced.py --from-step articles
```

### Q: 质量检查不合格怎么办？
A: 查看报告：
```bash
cat 60-Logs/quality-reports/quality-report-*.md
```
然后手动补充缺失维度。

### Q: 如何备份？
A: 定期 git commit + push：
```bash
git add -A
git commit -m "backup: $(date)"
git push
```

---

## 📜 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

<div id="english-documentation"></div>

# English Documentation

<div align="center">

**English** | [中文](#中文文档)

Fully Automated Knowledge Management Pipeline for Obsidian

</div>

---

## 🚀 Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault
```

### 2. Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API keys
nano .env
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run Pipeline

```bash
# Process last 7 days (Pinboard + Clippings)
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

---

## 📚 Directory Structure

```
📁 00-Polaris/               # Session Memory - Current focus
📁 10-Knowledge/             # Knowledge Layer
   ├── Atlas/              # Maps of Content (MOC)
   ├── Evergreen/          # Atomic permanent notes
   ├── Literature/         # Literature notes
   └── Sources/            # Raw sources
📁 20-Areas/                 # Areas of Responsibility
   ├── AI-Research/       # AI research
   ├── Tools/             # Tool reviews
   ├── Investing/          # Investment thoughts
   └── Programming/        # Programming
📁 30-Projects/              # Active projects
📁 40-Resources/             # Resources
📁 50-Inbox/                 # Inbox (3-layer capture)
📁 60-Logs/                  # Logs & automation scripts
📁 70-Archive/               # Archive
```

---

## 🤖 Automation Pipeline

### 6-Step Complete Workflow

```bash
# Full pipeline (recommended)
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

| Step | Script | Function | Description |
|------|------|------|------|
| 1 | `pinboard-processor.py` | Fetch Pinboard bookmarks | Day-by-day query, auto-dedup |
| 2 | `clippings_processor.py` | Process Obsidian Web Clipper | Sanitize names, obsidian move |
| 3 | `auto_article_processor.py` | Generate deep interpretation | LLM 6-dimension analysis |
| 4 | `batch_quality_checker.py` | Quality check | Auto scoring (1-5) |
| 5 | `auto_evergreen_extractor.py` | Extract Evergreen notes | Atomic concept notes |
| 6 | `auto_moc_updater.py` | Update MOC | Auto-index new files |

### Common Commands

```bash
# Process last N days (Pinboard + full pipeline)
python3 60-Logs/scripts/unified_pipeline_enhanced.py --pinboard-days 30

# Process historical date range
python3 60-Logs/scripts/unified_pipeline_enhanced.py \
  --pinboard-history 2026-02-01 2026-02-28

# Process only Clippings
python3 60-Logs/scripts/unified_pipeline.py --step clippings

# Update MOC only
python3 60-Logs/scripts/unified_pipeline.py --step moc

# Dry run (preview without execution)
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full --dry-run
```

---

## 🎯 Core Methodology

### PARA Method

| Layer | Purpose | Example |
|------|------|------|
| **Projects** | Clear goals & deadlines | "Complete AI Agent research" |
| **Areas** | Ongoing responsibilities | AI research, Tool reviews |
| **Resources** | Reference materials | Templates, code snippets |
| **Archive** | Archive | Completed projects |

### Three-Layer Note Architecture

1. **Session Memory (00-Polaris)**
   - Top of Mind: Current focus
   - Weekly review updates

2. **Knowledge Graph (10-Knowledge + 20-Areas)**
   - Evergreen: Atomic permanent notes
   - Literature: Literature notes
   - Atlas: Maps of Content

3. **Ingestion Pipeline (50-Inbox)**
   - Quick capture → Raw articles → Processing → Processed

---

## 📝 Deep Interpretation Standard

### 6-Dimension Quality Model

Every deep interpretation must contain:

```markdown
# One-sentence Definition
[Precise summary of core concept]

# Detailed Explanation
## What is it?
## Why does it matter?
## How does it work?

# Important Details
## Detail 1
## Detail 2
## Detail 3

# Architecture / Flow Chart
```
[ASCII diagram]
```

# Action Recommendations
1. Recommendation 1
2. Recommendation 2

# Related Knowledge
- [[Related Concept]]
```

### Quality Scoring

| Dimension | Weight | Pass Criteria |
|------|------|---------|
| One-sentence definition | 1-5 | Present |
| Detailed explanation | 1-5 | Complete |
| Important details | 1-5 | ≥3 items |
| Architecture diagram | 1-5 | Present (if applicable) |
| Action recommendations | 1-5 | ≥2 items |
| Related knowledge | 1-5 | Has [[wikilink]] |

**Total ≥ 18** (avg 3+) is passing

---

## 🛡️ WIGS Principles

**W**orkflow **I**ntegrity **G**uarantee **S**ystem

### Core Rules

1. **Mandatory `obsidian move`**
   ```bash
   # ✅ Correct
   obsidian move file="source.md" to="dest/"

   # ❌ Wrong - breaks wiki-links
   mv source.md dest/
   ```

2. **Explicit State Files**
   - Use `Processing-Queue.md` (version controlled)
   - Don't use `.hidden_state.json` (easily lost)

3. **Transaction Integrity**
   - Each pipeline creates a transaction
   - Recoverable after interruption

4. **Idempotent Processing**
   - Auto-skip processed files
   - Safe to re-run

---

## ⚙️ Environment Configuration

### Required

```bash
# .env file
PINBOARD_TOKEN=your_username:your_api_token
AUTO_VAULT_API_KEY=your_llm_api_key
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
```

### API Provider Options

| Provider | Cost | Quality | Config |
|--------|------|------|------|
| MiniMax | Low | High | `api.minimaxi.com/anthropic` |
| Anthropic | Medium | Highest | `api.anthropic.com` |
| OpenAI | Medium | High | `api.openai.com/v1` |

---

## 📊 Logging & Monitoring

### Unified Logs

Location: `60-Logs/pipeline.jsonl`

```json
{
  "timestamp": "2026-04-02T12:00:00",
  "session_id": "20260402-120000-abc123",
  "event_type": "pipeline_completed",
  "results": {...}
}
```

### Transaction Management

```bash
# List incomplete transactions
./60-Logs/scripts/txn.sh list

# Show transaction details
./60-Logs/scripts/txn.sh show <txn-id>
```

### Consistency Check

```bash
# 5-layer architecture check
./60-Logs/scripts/check-consistency.sh

# Auto repair
./60-Logs/scripts/repair.sh --auto
```

---

## 🔄 GitHub Actions

`.github/workflows/daily-pipeline.yml`:

```yaml
# Auto-run daily at 9 AM
schedule:
  - cron: '0 9 * * *'
```

Configure Secrets:
- `PINBOARD_TOKEN`
- `AUTO_VAULT_API_KEY`
- `AUTO_VAULT_API_BASE`

---

## 🛠️ Development Guide

### Adding New Scripts

1. Place in `60-Logs/scripts/`
2. Follow WIGS principles
3. Use `PipelineLogger` for logging
4. Integrate into `unified_pipeline_enhanced.py`

### Skill Development

```markdown
# skills/your-skill.md

## Trigger Conditions
Trigger when user says...

## Execution Steps
1. Step 1
2. Step 2

## Examples
User: "..."
→ Execute...
```

---

## ❓ FAQ

### Q: Don't have Pinboard account?
A: Use Clippings only:
```bash
python3 60-Logs/scripts/unified_pipeline.py --full
```

### Q: How to skip a step?
A: Use `--from-step`:
```bash
python3 60-Logs/scripts/unified_pipeline_enhanced.py --from-step articles
```

### Q: Quality check failed?
A: Check report:
```bash
cat 60-Logs/quality-reports/quality-report-*.md
```
Then manually add missing dimensions.

### Q: How to backup?
A: Regular git commit + push:
```bash
git add -A
git commit -m "backup: $(date)"
git push
```

---

## 📜 License

MIT License - See [LICENSE](LICENSE)

---

## 🙏 Acknowledgments

- PARA Method: [Tiago Forte](https://fortelabs.com)
- Zettelkasten: [Niklas Luhmann](https://en.wikipedia.org/wiki/Zettelkasten)
- Obsidian: [Dynalist](https://obsidian.md)
