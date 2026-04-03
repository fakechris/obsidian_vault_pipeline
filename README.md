---
title: "Obsidian Vault Pipeline"
description: "全自动知识管理流水线"
date: 2026-04-03
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Obsidian](https://img.shields.io/badge/Obsidian-Plugin-7C3AED?logo=obsidian)](https://obsidian.md)

**生产级全自动化 Obsidian 知识管理流水线**

输入 → 解读 → 质检 → 提炼 → 索引 → 可审计的全自动工作流

[📺 查看效果](#查看效果) • [🚀 快速开始](#30秒快速开始) • [📖 使用指南](#使用指南) • [🔧 功能特性](#核心特性)

</div>

---

## 两个项目，两种选择

| 项目 | 定位 | 适合场景 |
|------|------|----------|
| [**obsidian_vault_showcase**](https://github.com/fakechris/obsidian_vault_showcase) | **带Demo的开箱即用版本** | 想先看效果，或基于现有内容继续 |
| **obsidian_vault_pipeline** | **纯代码模板（本项目）** | 想从零开始，理解Pipeline实现 |

### 如何选择？

| 你的需求 | 推荐项目 | 原因 |
|----------|----------|------|
| 想先看看效果再决定是否使用 | [obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase) | 有76篇真实内容可浏览 |
| 想开箱即用，在上面改 | [obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase) | 克隆后直接Obsidian打开 |
| 想从零开始，完全自定义 | **本项目** | 空白模板，无demo数据 |
| 想了解Pipeline技术实现 | **本项目** | 代码结构更清晰 |
| 想基于现有内容继续生成 | [obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase) | 已有内容+完整脚本 |

---

## 查看效果

**想看看 Pipeline 的最终效果？**

👉 **[obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase)** - 完整效果展示

这个 showcase 包含：
- 🌳 **8 个 Evergreen 原子笔记** - AI Agent、Agent Architecture 等核心概念
- 📚 **76 篇深度解读** - GitHub 项目分析、技术文章解读
- 🗺️ **3 个 MOC 知识地图** - AI、工具、编程领域导航
- 🔗 **完整的双向链接网络** - 概念之间的关联关系

**使用方式：**
1. **只看效果** → 直接在 GitHub 上浏览
2. **下载体验** → Clone 到本地用 Obsidian 打开
3. **在此基础上开发** → 修改内容，连接你自己的 API Key 继续生成

---

## 30秒快速开始（模板项目）

想要从空白开始建立自己的系统？使用本模板：

```bash
# 1. 克隆模板项目
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault

# 2. 初始化配置（交互式向导）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --init
# 根据提示输入 API Key 即可

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行完整流水线
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

**效果**：自动抓取书签、生成深度解读、提取常青笔记、更新索引，所有环节可审计。

### 全新特性：智能初始化与环境检查

```bash
# 交互式初始化（一键配置）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --init

# 环境检查（验证配置是否正确）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --check
```

**特点**：
- 无需手动编辑 `.env` 文件，向导式配置
- 自动检测 API Key、Python 依赖、目录结构
- 支持 MiniMax / Anthropic / OpenAI 多种提供商
- 未配置时给出明确错误提示和修复指引

---

## 核心特性

### 5大自动维护系统

| 系统 | 核心能力 | 自动化程度 |
|------|----------|-----------|
| **质量门禁** | 提交前强制检查行数/占位符/frontmatter | 100% |
| **WIGS完整性** | 5层一致性检查 + 自动修复 | 95% |
| **反向链接维护** | 自动检测断裂链接，更新MOC | 95% |
| **Evergreen提取** | LLM自动提取核心概念 | 90% |
| **运行审计** | JSONL结构化日志 + 事务追踪 | 100% |

### 模板系统（90-Templates/）

预置 5 套专业模板：

| 模板 | 用途 | 输出位置 |
|------|------|----------|
| **文章深度解读** | 6维度分析模板 | 20-Areas/ |
| **Evergreen笔记** | 原子化知识模板 | 10-Knowledge/Evergreen/ |
| **项目笔记** | PARA项目管理 | 30-Projects/ |
| **MOC地图** | 知识导航模板 | 10-Knowledge/Atlas/ |
| **每日日志** | 日志记录模板 | 60-Logs/Daily/ |

### 视图目录（80-Views/）

手动维护的数据视图索引：

| 视图 | 内容 | 维护方式 |
|------|------|----------|
| **最近新增** | 本周/月新增内容汇总 | 手动更新 |
| **Evergreen索引** | 所有概念笔记的中央索引 | 手动整理 |
| **MOC索引** | 知识地图导航 | 手动维护 |

| 特性 | 说明 | 优势 |
|------|------|------|
| **动态超时** | 根据文章长度自动计算超时时间（1000字符=10秒，60-300秒自适应） | 避免固定超时导致的误判 |
| **产出检测** | 基于实际文件产出判断成功，而非进程退出码 | 超时也能正确识别成功 |
| **自动加载** | 自动加载 `.env`，无需手动 export | 简化使用流程 |
| **事务恢复** | 中断后可从中断点恢复 | 可靠性提升 |

### 3种深度解读模式

| 内容类型 | 脚本 | 输出 | 特殊能力 |
|----------|------|------|----------|
| 普通文章 | `auto_article_processor.py` | 6维度分析 | 自动分类 |
| GitHub项目 | `auto_github_processor.py` | 13节深度解读 | README解析 + ASCII架构图 |
| 学术论文 | `auto_paper_processor.py` | 10节学术结构 | arXiv API + 复现指南 |

### 成熟度对标

| 特性 | 本方案 | 说明 |
|------|--------|------|
| 质量门禁系统 | ✅ | `.claude/precommit-check.sh` 强制检查 |
| Claude Code集成 | ✅ | `.claude/settings.local.json` 完整权限 |
| 导航系统 | ✅ | `Home.md` + `Atlas/` 完整MOC |
| WIGS检查+修复 | ✅ | `check-consistency.sh` + `repair.sh` |
| 事务管理 | ✅ | `txn.sh` JSON状态追踪 |
| GitHub Actions | ✅ | `.github/workflows/` 自动化CI/CD |

---

## 架构详解

### 数据流

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  输入源   │───▶│ 深度解读  │───▶│  质检    │───▶│ 知识提炼  │───▶│ 索引更新  │───▶│ 日志记录  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │              │               │               │               │
   Pinboard      LLM 6维度       自动评分       Evergreen      自动MOC        JSONL
   Clippings     分析           1-5分          原子笔记       反向链接       结构化
```

### 目录结构（PARA方法）

```
my-vault/
├── 00-Polaris/
│   ├── README.md              # Top of Mind（每周手动更新）
│   └── Home.md                # 【入口导航】Obsidian首页
├── 10-Knowledge/
│   ├── Evergreen/             # 【自动】LLM提取的原子笔记
│   └── Atlas/
│       ├── MOC-Index.md       # 【自动】全局MOC索引
│       ├── MOC-AI-Research.md # 【自动】AI研究领域地图
│       ├── MOC-Tools.md       # 【自动】工具领域地图
│       ├── MOC-Investing.md   # 【自动】投资领域地图
│       └── MOC-Programming.md # 【自动】编程领域地图
├── 20-Areas/                  # 【自动+手动】深度解读输出
│   ├── AI-Research/Topics/    # YYYY-MM/ 子目录
│   ├── Tools/
│   ├── Investing/
│   └── Programming/
├── 30-Projects/               # 【手动】有截止日的项目
├── 40-Resources/              # 【手动】参考资料库
├── 50-Inbox/
│   ├── 01-Raw/               # 【自动】原始文章
│   └── Processing-Queue.md   # 【手动】处理队列
├── 60-Logs/
│   ├── scripts/               # 【直接使用】核心脚本
│   ├── pipeline.jsonl        # 【自动】统一结构化日志
│   └── transactions/         # 【自动】事务状态
├── 70-Archive/                # 【手动】归档完成项目
├── 80-Views/                  # 【自动】数据视图（最近新增、Evergreen索引等）
├── 90-Templates/              # 【内置】模板库（文章解读、项目、日志等）
└── .claude/
    ├── QUALITY_STANDARDS.md   # 内容质量标准
    ├── precommit-check.sh     # 提交前检查脚本
    └── settings.local.json    # Claude Code权限配置
```

---

## 使用指南

### 首次使用（推荐）

```bash
# Step 0: 交互式初始化（配置 API Key）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --init

# 验证环境是否配置正确
python3 60-Logs/scripts/unified_pipeline_enhanced.py --check
```

### 日常操作

```bash
# 每日自动处理（建议添加到crontab）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full

# 预览模式（查看会处理什么，但不执行）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full --dry-run

# 处理最近30天（批量历史）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --pinboard-days 30
```

### 单步操作

```bash
# Step 1: 获取Pinboard书签
python3 60-Logs/scripts/unified_pipeline_enhanced.py --step pinboard --pinboard-days 7

# Step 2: 迁移Clippings
python3 60-Logs/scripts/unified_pipeline.py --step clippings

# Step 3: 生成深度解读
python3 60-Logs/scripts/unified_pipeline.py --step articles

# Step 4: 质量检查
python3 60-Logs/scripts/batch_quality_checker.py --recent 7

# Step 5: 提取Evergreen
python3 60-Logs/scripts/auto_evergreen_extractor.py --recent 7

# Step 6: 更新MOC索引
python3 60-Logs/scripts/auto_moc_updater.py --scan
```

### 特殊内容处理

```bash
# GitHub项目深度解读
python3 60-Logs/scripts/auto_github_processor.py \
  --single https://github.com/tw93/kaku

# arXiv论文解读
python3 60-Logs/scripts/auto_paper_processor.py \
  --arxiv https://arxiv.org/abs/2401.12345
```

---

## 质量门禁

### 提交前强制检查

**每次提交前必须运行：**

```bash
./.claude/precommit-check.sh
```

**检查内容：**
- ✅ 文件行数 ≥ 150行（可配置）
- ✅ 无禁止占位符（中英文）
- ✅ Frontmatter格式正确
- ✅ 单次提交 ≤ 10个文件

**常用选项：**
```bash
./.claude/precommit-check.sh --lines-only      # 只检查行数
./.claude/precommit-check.sh --placeholders    # 只检查占位符
./.claude/precommit-check.sh --min-lines 200   # 设置最低200行
./.claude/precommit-check.sh file1.md file2.md  # 检查指定文件
```

### 6维度质量评分

| 维度 | 权重 | 合格标准 |
|------|------|----------|
| 一句话定义 | 5分 | 存在且清晰 |
| 详细解释 | 5分 | What/Why/How完整 |
| 重要细节 | 5分 | ≥3个技术点 |
| 架构图 | 5分 | 有ASCII图 |
| 行动建议 | 5分 | ≥2条可落地 |
| 关联知识 | 5分 | 有[[wikilink]] |

**总分≥18为合格**（平均3+）

---

## WIGS完整性检查

**Workflow Integrity Guarantee System** - 保证数据处理流程完整性的5层检查架构。

### 运行检查

```bash
# 运行5层一致性检查
./60-Logs/scripts/check-consistency.sh

# 预览修复方案
./60-Logs/scripts/repair.sh --dry-run

# 自动修复低风险问题
./60-Logs/scripts/repair.sh --auto

# 交互式修复
./60-Logs/scripts/repair.sh
```

### 5层检查架构

| 层级 | 检查内容 | 自动修复 |
|------|----------|----------|
| **L1** | 未完成事务 | ❌ 需手动确认 |
| **L2** | 孤儿Evergreen/断裂链接 | ⚠️ 部分自动 |
| **L3** | Ingestion一致性 | ✅ 自动（重复文件） |
| **L4** | Areas完整性/Git提交 | ❌ 需手动 |
| **L5** | Archive层 | ❌ 需手动 |

### 事务管理

```bash
# 查看未完成事务
./60-Logs/scripts/txn.sh list

# 查看事务详情
./60-Logs/scripts/txn.sh show txn-20260403-120000-abc123

# 创建新事务
./60-Logs/scripts/txn.sh start pipeline "处理最近7天"

# 更新事务步骤
./60-Logs/scripts/txn.sh step txn-xxx articles completed "8 interpretations"

# 完成事务
./60-Logs/scripts/txn.sh complete txn-xxx
```

---

## 故障排查

### 快速诊断

| 问题 | 诊断命令 |
|------|----------|
| 环境未配置 | `python3 60-Logs/scripts/unified_pipeline_enhanced.py --check` |
| Pipeline中断 | `./60-Logs/scripts/txn.sh list` |
| 断裂链接 | `./60-Logs/scripts/check-consistency.sh` |
| 质量检查失败 | `./.claude/precommit-check.sh <file>` |
| API Key无效 | `python3 60-Logs/scripts/unified_pipeline_enhanced.py --check` |

### 常见问题

**Q: 首次运行提示 API Key 未配置？**
```bash
# 运行交互式初始化向导
python3 60-Logs/scripts/unified_pipeline_enhanced.py --init

# 验证配置
python3 60-Logs/scripts/unified_pipeline_enhanced.py --check
```

**Q: Pipeline中断如何恢复？**
```bash
./60-Logs/scripts/txn.sh list                    # 查看中断点
python3 60-Logs/scripts/unified_pipeline_enhanced.py --from-step articles
```

**Q: 如何修复检测到的问题？**
```bash
./60-Logs/scripts/repair.sh --dry-run            # 预览
./60-Logs/scripts/repair.sh --auto               # 自动修复低风险
./60-Logs/scripts/repair.sh                      # 交互式修复
```

**Q: 质量检查失败怎么办？**
```bash
./.claude/precommit-check.sh path/to/file.md     # 查看具体问题
cat .claude/QUALITY_STANDARDS.md                 # 查看质量标准
```

---

## 配置参考

### .env配置模板

```bash
# LLM API（必需）
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
AUTO_VAULT_MODEL=minimax/MiniMax-M2.5

# Pinboard（可选）
PINBOARD_TOKEN=username:token

# 代理（可选）
HTTP_PROXY=http://127.0.0.1:7897
```

### 成本估算

| 提供商 | 成本 | 中文支持 | 推荐场景 |
|--------|------|----------|----------|
| **MiniMax** | ¥0.01/1K tokens | 优秀 | 日常批量 |
| **Anthropic** | $0.03/1K tokens | 良好 | 高质量深度 |
| **OpenAI** | $0.01-0.03/1K tokens | 良好 | 备选 |

- 处理10篇文章：约 ¥1-3 元
- 处理100篇GitHub项目：约 ¥10-30 元

---

## 手动维护清单

| 频率 | 任务 | 命令/文件 |
|------|------|----------|
| 每日 | 运行Pipeline | `python3 60-Logs/scripts/unified_pipeline_enhanced.py --full` |
| 每日 | 检查系统状态 | `./60-Logs/scripts/check-consistency.sh` |
| 每周 | 更新Top of Mind | 编辑 `00-Polaris/README.md` |
| 每周 | 审查质检报告 | 查看 `60-Logs/quality-reports/*.md` |
| 每月 | 归档旧文件 | `obsidian move` 到 `70-Archive/` |

---

## GitHub Actions

已配置 `.github/workflows/daily-pipeline.yml`：

```yaml
name: Daily Knowledge Pipeline
on:
  schedule:
    - cron: '0 9 * * *'      # 每天上午9点
  workflow_dispatch:          # 支持手动触发
```

配置Secrets：
1. 仓库 Settings → Secrets and variables → Actions
2. 添加 `PINBOARD_TOKEN`、`AUTO_VAULT_API_KEY`、`AUTO_VAULT_API_BASE`

---

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

*版本: 1.0 | 最后更新: 2026-04-03*
