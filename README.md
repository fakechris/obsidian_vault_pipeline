---
schema_version: "1.0.0"
note_id: readme-dc2a69e8
title: "Obsidian Vault Pipeline"
description: "全自动知识管理流水线"
date: 2026-04-06
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Obsidian](https://img.shields.io/badge/Obsidian-Plugin-7C3AED?logo=obsidian)](https://obsidian.md)
[![PyPI](https://pypi.org/pypi/obsidian-vault-pipeline/)](https://pypi.org/project/obsidian-vault-pipeline/)

**生产级全自动化 Obsidian 知识管理流水线**

摄入 → 解读 → 吸收 → 整形 → 规范化 → 派生视图

[🇬🇧 English](README_EN.md)

</div>

---

## 这个项目解决什么问题？

**痛点：** 你收藏了大量书签、文章、论文，但它们散落各处，从未被真正消化。它们像代码一样躺在仓库里，从来没有被编译成可运行的知识。

**方案：** 把 LLM 当作知识库的"程序员"，把 Obsidian 当作 IDE，把 Wiki 当作代码库。自动化完成：
- 抓取原始内容
- 生成结构化深度解读
- 提取可复用的核心概念
- 维护知识之间的双向链接

> 🙏 **致敬**: [Andrej Karpathy 的 LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

## 架构图：工具脉络

### 六层运行模型

| 层 | 目标 | 代表命令 | 是否允许 LLM 主判断 |
|---|---|---|---|
| Ingest | 采集并规范化原始输入 | `ovp --step pinboard` `ovp --step clippings` `ovp-article` | 否，尽量 deterministic |
| Interpret | 把原始内容变成深度解读 | `ovp-article` `ovp-github` `ovp-paper` | 是，但输出格式受约束 |
| Absorb | 把解读吸收到知识库 | `ovp-absorb` `ovp-evergreen` `ovp-query-to-wiki` | 是，重大判断需走工作流 |
| Refine | 对既有知识库做 cleanup / breakdown | `ovp-cleanup` `ovp-breakdown` | 是，输出必须是结构化 proposal |
| Canonical | 维护 registry / alias / Atlas / MOC | `ovp-promote-candidates` `ovp-moc` `ovp-rebuild-registry` | 否，尽量 deterministic |
| Derived | 构图、delta、lint、报告 | `ovp-graph` `ovp-lint` | 否，只消费 canonical 状态 |

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户操作层                                      │
│  ovp --full          一键完整流程（日常使用）                                  │
│  ovp-autopilot       自动驾驶模式（持续监控）                                │
│  ovp --step X        单步执行（调试/定制）                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              工具链总览                                      │
│                                                                             │
│  输入源                                                                    │
│  ├── Pinboard 书签 ──────┐                                                 │
│  ├── Clippings 读书笔记 ──┼──► ovp --step pinboard/clippings               │
│  └── 50-Inbox/01-Raw/ ───┘                                                 │
│                                                                             │
│  内容处理                                                                   │
│  ├── ovp-article    处理 Raw → 生成深度解读                                  │
│  ├── ovp-github     GitHub 项目 → 13节深度解读                                │
│  └── ovp-paper      arXiv 论文 → 学术解读                                    │
│                                                                             │
│  质量保障                                                                   │
│  ├── ovp-quality    6维度质量评分（1-5分）                                   │
│  └── ovp-lint       提交前检查（行数/占位符/frontmatter）                      │
│                                                                             │
│  知识吸收与整形                                                              │
│  ├── ovp-absorb     从深度解读吸收概念，驱动 candidate / active 生命周期      │
│  ├── ovp-evergreen  兼容入口：直接从解读提取 Evergreen                        │
│  ├── ovp-cleanup    生成知识页重写 proposal                                   │
│  ├── ovp-breakdown  生成知识页拆分 proposal                                   │
│  └── ovp-query-to-wiki  从问答归档新概念                                      │
│                                                                             │
│  索引维护                                                                   │
│  ├── ovp-moc        更新 Area MOC / Atlas Index                              │
│  ├── ovp-migrate-links  扫描/修复断裂 wikilink                               │
│  └── ovp-rebuild-registry  对账 Evergreen 与 registry                        │
│                                                                             │
│  生命周期维护                                                                │
│  ├── ovp-promote-candidates  promote / merge / reject candidate              │
│  ├── ovp-graph      构建全量图谱 / daily delta                               │
│  └── ovp-repair     修复事务 / autopilot / registry 状态                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 工具命令参考

### 一键运行（日常）

| 命令 | 解决什么问题 | 使用场景 |
|------|-------------|----------|
| `ovp --full` | 一键执行完整 Pipeline | 每日定时任务 |
| `ovp --full --dry-run` | 预览将要处理的内容 | 变更前检查 |
| `ovp --check` | 验证 API Key 等配置 | 首次配置后 |

### AutoPilot 自动驾驶（完全自动）

| 命令 | 解决什么问题 | 使用场景 |
|------|-------------|----------|
| `ovp-autopilot --watch=inbox --parallel=1` | 监控目录，全自动处理 | 持续运行 |
| `ovp-autopilot --yes` | 跳过费用确认警告 | 确认后重复执行 |
| `ovp-autopilot --parallel=2 --quality=3.5` | 高并发+高质量阈值 | 批量处理（费用高） |

**AutoPilot 工作流：**
```
文件进入 50-Inbox/01-Raw/
        │
        ▼
  ┌─────────────┐
  │  监控检测    │  ← watchdog 监控目录
  └─────────────┘
        │
        ▼
  ┌─────────────┐
  │  任务入队    │  ← SQLite 持久化队列
  └─────────────┘
        │
        ▼
  ┌─────────────┐     ┌─────────────┐
  │  生成解读    │────▶│  质量评分    │
  └─────────────┘     └─────────────┘
        │                   │
        │  ✗ 不达标         │ ✓ 达标
        ▼                   ▼
  ┌─────────────┐     ┌─────────────┐
  │  自动重试    │     │  提取Evergreen│
  └─────────────┘     └─────────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │  更新MOC    │
                            └─────────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │  Git提交   │
                            └─────────────┘
```

### 单步执行（调试/定制）

| 命令 | 解决什么问题 |
|------|-------------|
| `ovp --step pinboard` | 获取 Pinboard 书签 |
| `ovp --step clippings` | 迁移 Kindle Clippings |
| `ovp --step articles` | 处理 Raw 生成解读 |
| `ovp --step quality` | 质量评分 |
| `ovp --step evergreen` | 提取核心概念 |
| `ovp --step moc` | 更新索引 |

### 专项处理器

| 命令 | 解决什么问题 |
|------|-------------|
| `ovp-github --single URL` | GitHub 项目 → 13节深度解读 |
| `ovp-paper --arxiv URL` | arXiv 论文 → 学术解读 |
| `ovp-absorb --recent 7 --dry-run --json` | 预览最近解读会如何被吸收到知识层 |
| `ovp-evergreen --recent 7` | 从最近解读提取 Evergreen |
| `ovp-cleanup --slug 概念 --json` | 生成单页 cleanup proposal |
| `ovp-cleanup --slug 概念 --write --json` | 对日记式知识页执行确定性结构清理 |
| `ovp-breakdown --all --json` | 生成全库 breakdown proposal |
| `ovp-breakdown --slug 概念 --write --json` | 生成子页并回写父页索引 |
| `ovp-moc --update-atlas-from-registry` | 从 registry 重建 Atlas Index |
| `ovp-quality --recent 7` | 批量质量评分 |

### 吸收与整形工作流

| 命令 | 契约 | 说明 |
|------|------|------|
| `ovp-absorb` | 输入深度解读，输出 candidate / active evergreen 的生命周期动作 | 非 `--dry-run` 会调用吸收流程并更新 canonical 层 |
| `ovp-cleanup` | 输入既有 Evergreen，输出 `rewrite_decision` proposal，并可在 `--write` 下执行确定性重排 | 当前只做可逆结构清理，不做自由文本重写；写入后会刷新 registry / Atlas |
| `ovp-breakdown` | 输入既有 Evergreen，输出 `split_decision` proposal，并可在 `--write` 下创建子页 | 当前只做增量派生，不删除父页原内容；写入后会刷新 registry / Atlas |

### 维护工具

| 命令 | 解决什么问题 |
|------|-------------|
| `ovp-lint` | 提交前强制检查 |
| `ovp-repair --transactions --autopilot --registry` | 修复卡住事务 / 队列状态 / registry 对账 |
| `ovp-migrate-links --scan` | 扫描断裂 wikilink |
| `ovp-migrate-links --write` | 应用高置信度链接修复 |
| `ovp-rebuild-registry --json` | 查看 Evergreen / registry 分叉 |
| `ovp-promote-candidates review` | 审核 candidate 生命周期 |
| `ovp-graph --daily today` | 生成当日增量知识图谱 |
| `ovp-query-to-wiki --create-evergreen "名称"` | 从问答创建新笔记 |

---

## AutoPilot 场景指南

### 场景1：日常增量处理（推荐）

```bash
# 每天早上跑一次
ovp --full

# 或者用 cron 自动化
# crontab -e
# 0 8 * * * /path/to/ovp --full --vault-dir /path/to/vault
```

### 场景2：完全自动驾驶

```bash
# 启动后台守护进程
ovp-autopilot --watch=inbox --parallel=1 --yes

# 推荐在 tmux / screen 中运行，或直接保存 stdout
ovp-autopilot --watch=inbox --parallel=1 --yes | tee autopilot.log
```

### 场景3：批量处理历史

```bash
# 处理 Pinboard 最近30天
ovp --pinboard-days 30

# 处理指定日期范围
ovp --pinboard-history 2026-01-01 2026-03-31
```

### 场景4：手动单步调试

```bash
# 只抓取书签，不处理
ovp --step pinboard

# 只生成解读，不质检
ovp --step articles

# 从质量检查开始
ovp --from-step quality
```

### 场景5：单一项目解读

```bash
# GitHub 项目
ovp-github --single https://github.com/anthropics/claude-code

# arXiv 论文
ovp-paper --arxiv https://arxiv.org/abs/2403.03367
```

---

## 目录结构（PARA 方法）

```
vault/
├── 50-Inbox/01-Raw/           # 【输入】原始文档（书签/文章/Raw）
├── 20-Areas/                   # 【输出】深度解读
│   └── {AI-Research,Tools,Investing,Programming}/
│       └── Topics/YYYY-MM/
├── 10-Knowledge/
│   ├── Evergreen/              # 【提炼】原子笔记
│   └── Atlas/                 # 【索引】MOC 知识地图
│       ├── Atlas-Index.md
│       ├── concept-registry.jsonl
│       └── alias-index.json
├── 60-Logs/
│   ├── pipeline.jsonl         # 结构化日志
│   ├── transactions/          # 事务状态
│   ├── quality-reports/       # 质检报告
│   └── daily-deltas/          # 每日图谱增量
└── 70-Archive/               # 【归档】完成的内容
```

---

## 6维度质量模型

每篇深度解读包含：

| 维度 | 说明 |
|------|------|
| 一句话定义 | 核心概念的精准概括 |
| 详细解释 | What/Why/How 完整分析 |
| 重要细节 | ≥3 个关键技术点 |
| 架构图 | ASCII 可视化（若有） |
| 行动建议 | ≥2 条可落地建议 |
| 关联知识 | [[双向链接]] |

---

## 30秒快速开始

```bash
# 1. 安装
pip install obsidian-vault-pipeline

# 2. 初始化
ovp --init

# 3. 放入文章
mkdir -p 50-Inbox/01-Raw
echo "# 测试\n\n内容" > 50-Inbox/01-Raw/test.md

# 4. 运行
ovp --full
```

---

## 配置参考

```bash
# .env 必需配置
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic

# 可选配置
PINBOARD_TOKEN=username:token
HTTP_PROXY=http://127.0.0.1:7897
```

---

## 相关资源

| 资源 | 说明 |
|------|------|
| [showcase](https://github.com/fakechris/obsidian_vault_showcase) | 完整效果展示 |
| [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | 核心理念 |
| [PyPI](https://pypi.org/project/obsidian-vault-pipeline/) | pip 安装包 |

---

*版本: 2.0 | 最后更新: 2026-04-06*
