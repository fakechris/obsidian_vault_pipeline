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
[![PyPI](https://img.shields.io/pypi/v/obsidian-vault-pipeline.svg)](https://pypi.org/project/obsidian-vault-pipeline/)

**生产级全自动化 Obsidian 知识管理流水线**

输入 → 解读 → 质检 → 提炼 → 索引 → 可审计的全自动工作流

[🇬🇧 English](README_EN.md)

</div>

---

## 这是什么？

Obsidian Vault Pipeline 是一套**生产级自动化知识管理系统**，帮你把碎片信息（书签、文章、笔记）自动转化为结构化的永恒知识。

**核心流程：**

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  输入源   │───▶│ 深度解读  │───▶│  质检    │───▶│ 知识提炼  │───▶│ 索引更新  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
   书签/文章      LLM 6维度       自动评分       Evergreen      自动MOC
   自动抓取       深度分析       1-5分          原子笔记       反向链接
```

**一句话：** 自动抓取你的阅读内容，AI 生成深度解读，提取核心概念，构建可导航的知识网络。

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

### 6维度质量模型

每篇深度解读包含：
1. **一句话定义** - 核心概念清晰表述
2. **详细解释** - What/Why/How 完整
3. **重要细节** - ≥3 个技术点
4. **架构图** - ASCII 可视化
5. **行动建议** - ≥2 条可落地
6. **关联知识** - [[双向链接]]

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

## 两种使用方式

| 方式 | 推荐场景 | 上手难度 |
|------|----------|----------|
| **[obsidian_vault_showcase](https://github.com/fakechris/obsidian_vault_showcase)** | 想先看效果，或基于现有内容继续 | ⭐ 开箱即用 |
| **[obsidian_vault_pipeline](https://github.com/fakechris/obsidian_vault_pipeline)**（本项目） | 想从零开始，完全自定义 | ⭐⭐ 需要配置 |

---

## pip安装（推荐）

```bash
pip install obsidian-vault-pipeline
```

安装后可用命令：

| 命令 | 功能 |
|------|------|
| `ovp --init` | 初始化配置（交互式向导） |
| `ovp --check` | 检查环境配置 |
| `ovp --full` | 运行完整 Pipeline |
| `ovp-article --process-inbox` | 处理 50-Inbox/01-Raw/ 中的文章 |
| `ovp-evergreen --recent 7` | 提取最近7天的 Evergreen 笔记 |
| `ovp-moc --scan` | 扫描并更新 MOC 索引 |
| `ovp-quality --recent 7` | 质量检查 |

---

## 30秒快速开始

```bash
# 1. 安装
pip install obsidian-vault-pipeline

# 2. 创建 vault 目录并进入
mkdir my-vault && cd my-vault

# 3. 初始化配置（向导式）
ovp --init

# 4. 放入文章到 50-Inbox/01-Raw/
mkdir -p 50-Inbox/01-Raw
echo "# 测试文章\n\n内容..." > 50-Inbox/01-Raw/test.md

# 5. 运行 Pipeline
ovp --full
```

**效果**：自动生成深度解读到 `20-Areas/`，提取 Evergreen 到 `10-Knowledge/`，更新 MOC 索引。

---

## Claude Code Skill（可选）

本项目包含 **Claude Code Skill**，支持自然语言触发 Pipeline 操作。

**使用方法：**

```bash
# 克隆仓库后，Claude Code 自动加载 skill
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault
claude  # 启动 Claude Code，skill 自动生效
```

**触发关键词：**

| 你说 | Claude 执行 |
|------|------------|
| "运行 WIGS 流程" | `./60-Logs/scripts/check-consistency.sh` |
| "整理 Obsidian Vault" | `ovp --full` |
| "处理文章" | `ovp-article --process-inbox` |
| "提取 Evergreen" | `ovp-evergreen --recent 7` |
| "更新 MOC" | `ovp-moc --scan` |
| "质量检查" | `ovp-quality --recent 7` |

---

## 目录结构（PARA方法）

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
├── 80-Views/                  # 【自动】数据视图
├── 90-Templates/              # 【内置】模板库
└── .claude/
    ├── skills/                # 【内置】Claude Code Skill
    └── precommit-check.sh     # 提交前检查脚本
```

---

## 详细使用指南

### 首次使用

```bash
# Step 0: 交互式初始化（配置 API Key）
ovp --init

# 验证环境是否配置正确
ovp --check
```

### 日常操作

```bash
# 每日自动处理（建议添加到crontab）
ovp --full

# 预览模式（查看会处理什么，但不执行）
ovp --full --dry-run

# 处理最近30天（批量历史）
ovp --pinboard-days 30
```

### 单步操作

```bash
# Step 1: 获取Pinboard书签
ovp --step pinboard --pinboard-days 7

# Step 2: 迁移Clippings
ovp --step clippings

# Step 3: 生成深度解读
ovp --step articles

# Step 4: 质量检查
ovp-quality --recent 7

# Step 5: 提取Evergreen
ovp-evergreen --recent 7

# Step 6: 更新MOC索引
ovp-moc --scan
```

### 特殊内容处理

```bash
# GitHub项目深度解读
ovp-github --single https://github.com/tw93/kaku

# arXiv论文解读
ovp-paper --arxiv https://arxiv.org/abs/2401.12345
```

---

## 质量门禁

### 提交前强制检查

```bash
./.claude/precommit-check.sh
```

**检查内容：**
- ✅ 文件行数 ≥ 150行（可配置）
- ✅ 无禁止占位符（中英文）
- ✅ Frontmatter格式正确
- ✅ 单次提交 ≤ 10个文件

---

## WIGS完整性检查

**Workflow Integrity Guarantee System** - 保证数据处理流程完整性的5层检查架构。

```bash
# 运行5层一致性检查
./60-Logs/scripts/check-consistency.sh

# 预览修复方案
./60-Logs/scripts/repair.sh --dry-run

# 自动修复低风险问题
./60-Logs/scripts/repair.sh --auto
```

| 层级 | 检查内容 | 自动修复 |
|------|----------|----------|
| **L1** | 未完成事务 | ❌ 需手动确认 |
| **L2** | 孤儿Evergreen/断裂链接 | ⚠️ 部分自动 |
| **L3** | Ingestion一致性 | ✅ 自动（重复文件） |
| **L4** | Areas完整性/Git提交 | ❌ 需手动 |
| **L5** | Archive层 | ❌ 需手动 |

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
| 每日 | 运行Pipeline | `ovp --full` |
| 每日 | 检查系统状态 | `./60-Logs/scripts/check-consistency.sh` |
| 每周 | 更新Top of Mind | 编辑 `00-Polaris/README.md` |
| 每周 | 审查质检报告 | 查看 `60-Logs/quality-reports/*.md` |
| 每月 | 归档旧文件 | `obsidian move` 到 `70-Archive/` |

---

## 相关仓库

| 仓库 | 用途 | 链接 |
|------|------|------|
| **obsidian_vault_showcase** | 完整效果展示（带Demo） | [GitHub](https://github.com/fakechris/obsidian_vault_showcase) |
| **obsidian_vault_pipeline** | 模板项目（本仓库） | [GitHub](https://github.com/fakechris/obsidian_vault_pipeline) |
| **PyPI** | pip安装包 | [PyPI](https://pypi.org/project/obsidian-vault-pipeline/) |

---

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

*版本: 1.0 | 最后更新: 2026-04-03*
