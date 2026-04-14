---
schema_version: "1.0.0"
note_id: readme-dc2a69e8
title: "Obsidian Vault Pipeline"
description: "六层 Obsidian 知识流水线"
date: 2026-04-07
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/obsidian-vault-pipeline.svg)](https://pypi.org/project/obsidian-vault-pipeline/)

面向 Obsidian Vault 的生产级知识流水线  
Ingest → Interpret → Absorb → Refine → Canonical → Derived

[🇬🇧 English](README_EN.md)

</div>

## 这是什么

这不是一个“多脚本拼装包”，而是一套围绕 Obsidian Vault 构建的知识编排系统：

- 输入层负责接收 Pinboard、Clippings、Raw Markdown
- 解释层负责生成深度解读
- 吸收层负责把解读编入 Evergreen 生命周期
- 整形层负责 cleanup / breakdown
- 规范层负责 registry / alias / Atlas / MOC
- 派生层负责 `knowledge.db`、graph、lint、daily delta

当前版本已经把这 6 层真正接入到了日常运行链路里：

- `ovp --full` 默认跑到 `knowledge_index`
- `ovp --full --with-refine` 会在 `moc` 后追加 `refine`
- `ovp-autopilot` 默认实时跑 `absorb -> moc -> knowledge_index`
- `ovp-autopilot --with-refine` 会在实时链路里追加 `refine`

## 为什么会变成现在这套架构

这个项目最早是围绕 Obsidian Vault 的自动化整理脚本发展起来的，但随着能力增加，出现了三个典型问题：

- 运行时主流程和单个脚本各自演化，难以保证契约一致
- 概念、链接、Atlas、graph、检索索引彼此耦合，但真相边界不清楚
- 一旦要支持媒体、医疗、工程研究这类不同领域，原来的 concept-only 模型会失真

现在这套设计的目标就是把这些问题拆开：

- 用六层运行模型明确“什么是编排层、什么是真相层、什么是派生层”
- 先把当前技术研究语义正式化为 `research-tech`
- 再把 `default-knowledge` 收敛成默认兼容层，而不是继续承担所有领域语义
- 用 Pack API 让不同领域通过 pack 接入，而不是继续往 core 里硬编码特例

这意味着当前仓库已经不只是一个 Vault 自动化项目，而是一个：

> 面向 Obsidian/Vault 工作流的可扩展知识编排平台

其中：

- `research-tech` 是第一套显式内置标准 pack
- `default-knowledge` 当前仍保留为默认兼容 pack

## Domain Packs

当前 core 已经开始 pack 化。

- 内置标准 pack：`research-tech`
- 默认兼容 pack：`default-knowledge`
- 运行时可通过 `--pack` 和 `--profile` 选择 workflow
- 第三方 pack 可以通过 `openclaw_pipeline.packs` entry point 或 `OPENCLAW_PACK_MANIFESTS` manifest 列表接入

示例：

```bash
ovp-packs
ovp-doctor --pack research-tech --json
ovp --pack research-tech --profile full
ovp-autopilot --pack research-tech --profile autopilot --yes
ovp --pack default-knowledge --profile full
```

面向第三方 pack 作者的 API 文档在：

- `docs/pack-api/README.md`
- `docs/pack-api/manifest-and-hooks.md`
- `docs/pack-api/dogfooding-with-media-pack.md`

## 平台架构

从平台视角看，当前系统分成三层：

1. **Core Platform**
2. **Domain Pack**
3. **Workflow Profile**

### 1. Core Platform

core 负责通用且必须稳定的部分：

- runtime / vault layout
- CLI orchestration
- autopilot / queue / watcher
- canonical identity helper
- registry framework
- derived `knowledge.db`
- graph / lint / audit 基础设施
- plugin / pack loader
- evidence schema 基础契约

### 2. Domain Pack

pack 负责领域语义，而不是只放几段 prompt。它定义：

- object kinds
- workflow profile
- discovery boundary
- absorb / refine / lint 规则
- schema / template / prompt 资源

当前内置的是：

- `research-tech`：当前技术研究知识流的显式 pack，也是默认 workflow pack
- `default-knowledge`：兼容层

未来媒体、医疗这类领域，应该各自作为外部 pack 工程接入。

### 3. Workflow Profile

profile 是某个 pack 下的一条可执行 DAG。

当前已经实现的标准 profile：

- `research-tech/full`
- `research-tech/autopilot`
- `default-knowledge/full`
- `default-knowledge/autopilot`

## 研究技术 Pack 的运营面

`research-tech` 现在不只是一个内部 pack，也已经有最小运营面：

- `ovp-doctor`
  检查默认 workflow pack、pack 角色、operator docs、recipes，以及可选的 vault 健康状态
- `ovp-export`
  导出最小 compiled artifacts：
  - `object-page`
  - `topic-overview`
  - `event-dossier`
  - `contradictions`
- `ovp-truth`
  直接读取 `knowledge.db` 中的 object / contradiction / neighborhood truth rows
- `ovp-ui`
  启动一个本地只读 DB 浏览面，直接查看 object / topic / event / contradiction
- `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- `docs/research-tech/RESEARCH_TECH_VERIFY.md`
- `docs/recipes/research-tech/*.md`

示例：

```bash
ovp-doctor --pack research-tech --json
ovp-truth objects --vault-dir /path/to/vault
ovp-ui --vault-dir /path/to/vault --port 8787
ovp-export --pack research-tech --target topic-overview --output-path /tmp/topic.md
```

这也是为什么现在默认就会跑：

```bash
ovp --full
ovp-autopilot --yes
```

也可以显式指定：

```bash
ovp --pack research-tech --profile full
ovp-autopilot --pack research-tech --profile autopilot --yes
# 兼容路径
ovp --pack default-knowledge --profile full
```

## 插件设计

当前插件/pack 接入面已经有最小闭环，不再只是设计稿。

支持两种发现方式：

1. Python entry point 组：`openclaw_pipeline.packs`
2. 显式 manifest 列表：`OPENCLAW_PACK_MANIFESTS=/path/a.yaml:/path/b.yaml`

最小接入链路是：

1. 第三方 pack 提供 manifest
2. manifest 声明 `entrypoints.pack`
3. entrypoint 返回 `BaseDomainPack`
4. core 校验 `api_version`
5. 用户通过 `--pack/--profile` 运行

当前已经实现的硬边界：

- pack 不能把 semantic retrieval 直接升级成 canonical identity
- pack 不能把 `knowledge.db` 当 source of truth
- pack 不能绕过 audit/logging
- 所有 derived state 都必须可重建

## 真实运行模型

### Source of Truth

这套系统当前坚持以下边界：

- **canonical truth**：Vault Markdown + concept registry
- **derived views**：Atlas、MOC、graph、`knowledge.db`、lint、daily delta
- **不是 source of truth**：`knowledge.db`

`knowledge.db` 是借鉴 GBrain 思路加入的派生索引层，用来承载：

- FTS 页面索引
- 结构化链接
- raw sidecar 镜像
- timeline / audit 事件
- deterministic section embeddings
- 只读 query / serve 接口

它可以被重建，不参与 canonical 身份决策。

### 六层职责

| 层 | 职责 | 代表命令 | 是否允许 LLM 直接做重大判断 |
|---|---|---|---|
| Ingest | 采集并规范化输入 | `ovp --step pinboard` `ovp --step clippings` `ovp-article` | 否 |
| Interpret | 把输入变成深度解读 | `ovp-article` `ovp-github` `ovp-paper` | 是，但输出受格式约束 |
| Absorb | 把解读编入概念生命周期 | `ovp-absorb` `ovp-evergreen` | 是，但要输出结构化结果 |
| Refine | 对现有知识页做 cleanup / breakdown | `ovp-cleanup` `ovp-breakdown` | 是，但执行受控 |
| Canonical | 维护 registry / alias / Atlas / MOC | `ovp-rebuild-registry` `ovp-moc` `ovp-promote-candidates` | 否 |
| Derived | 派生检索、图谱和检查 | `ovp-knowledge-index` `ovp-graph` `ovp-lint` | 否 |

## `ovp --full` 现在到底跑什么

默认完整流程：

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

带整形批处理：

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

关键点：

- `absorb` 现在走的是 `openclaw_pipeline.commands.absorb`
- `refine` 是 `cleanup + breakdown` 的批处理包装
- `knowledge_index` 永远放最后，保证 `knowledge.db` 反映最终 canonical 状态
- `--step evergreen` / `--from-step evergreen` 仍然接受，内部会映射到 `absorb`

## `ovp-autopilot` 现在到底跑什么

默认实时链路：

```text
interpretation
→ quality
→ absorb
→ moc
→ knowledge_index
→ auto_commit(optional)
```

启用整形：

```bash
ovp-autopilot --watch=inbox --with-refine --yes
```

这会把链路变成：

```text
interpretation
→ quality
→ absorb
→ moc
→ refine
→ knowledge_index
→ auto_commit(optional)
```

默认不打开 `refine` 的原因不是“还没接上”，而是为了避免每来一篇新内容就自动重写全库结构；现在它已经接进编排，但需要显式 opt-in。

## 命令速览

### 日常主入口

| 命令 | 说明 |
|---|---|
| `ovp --check` | 检查运行环境 |
| `ovp --full` | 完整日常流水线 |
| `ovp --full --with-refine` | 完整流水线 + cleanup/breakdown |
| `ovp --step absorb` | 单独跑吸收层 |
| `ovp --step refine` | 单独跑批处理整形 |
| `ovp --from-step absorb` | 从吸收层之后继续跑 |

### 内容处理

| 命令 | 说明 |
|---|---|
| `ovp-article --process-inbox --vault-dir <vault>` | 处理 Raw 文档 |
| `ovp-github --process-single <file> --vault-dir <vault>` | 处理 GitHub 类型输入 |
| `ovp-paper --process-single <file> --vault-dir <vault>` | 处理论文类型输入 |

### Absorb / Refine / Canonical

| 命令 | 说明 |
|---|---|
| `ovp-absorb --recent 7 --json` | 吸收最近解读 |
| `ovp-evergreen --recent 7 --json` | `ovp-absorb` 的兼容别名 |
| `ovp-cleanup --all --json` | 生成 cleanup proposal |
| `ovp-cleanup --all --write --json` | 执行确定性 cleanup |
| `ovp-breakdown --all --json` | 生成 breakdown proposal |
| `ovp-breakdown --all --write --json` | 执行增量 breakdown |
| `ovp-rebuild-registry --json` | 对账 Evergreen 与 registry |
| `ovp-promote-candidates review` | 审核 candidate 生命周期 |
| `ovp-moc --scan --vault-dir <vault>` | 更新 MOC / Atlas |

### Derived 层

| 命令 | 说明 |
|---|---|
| `ovp-knowledge-index --json` | 重建 `knowledge.db` |
| `ovp-knowledge-index --search "query" --json` | FTS 搜索 |
| `ovp-knowledge-index --query "question" --json` | embedding chunk query |
| `ovp-knowledge-index --get slug --json` | 读取 canonical 页面 |
| `ovp-knowledge-index --stats --json` | 查看索引统计 |
| `ovp-knowledge-index --audit-recent --json` | 查看最近审计事件 |
| `ovp-knowledge-index --tools-json` | 输出工具发现 JSON |
| `ovp-knowledge-index --serve` | 启动只读 stdio JSONL 服务 |
| `ovp-graph daily today --vault-dir <vault>` | 生成 daily delta |
| `ovp-lint --check --vault-dir <vault>` | 运行链接/结构检查 |

### AutoPilot

| 命令 | 说明 |
|---|---|
| `ovp-autopilot --watch=inbox --parallel=1 --yes` | 默认实时链路 |
| `ovp-autopilot --watch=inbox,pinboard --yes` | 同时监听多个来源 |
| `ovp-autopilot --with-refine --yes` | 在实时链路追加 refine |
| `ovp-autopilot --no-commit --yes` | 禁用自动提交 |

## 目录结构

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

## `knowledge.db` 提供什么

`knowledge.db` 是可重建的本地派生索引，当前包含：

- `pages_index`
- `page_fts`
- `page_links`
- `raw_data`
- `timeline_events`
- `audit_events`
- `page_embeddings`

它由 `ovp-knowledge-index` 重建，供以下读取场景使用：

- 关键词搜索
- embedding 检索
- canonical slug 读取
- 审计事件浏览
- 工具发现与只读服务

默认 discovery 也已经统一到这里：

- `ovp-query` 默认走 `knowledge.db`
- 关键词检索使用 FTS5 BM25
- 语义检索使用本地 deterministic embeddings
- QMD 不再是默认检索依赖，只能通过显式 `--engine qmd` 启用

## 快速开始

```bash
curl -fsSL https://raw.githubusercontent.com/fakechris/obsidian_vault_pipeline/main/scripts/install-user.sh | bash

mkdir -p my-vault
cd my-vault

ovp --check
ovp --full
```

如果你更偏好显式的 PyPI 两步安装：

```bash
python3 -m pip install --user obsidian-vault-pipeline
python3 -m openclaw_pipeline.installer
```

安装器会优先把 `ovp*` 命令写入当前 `PATH` 里可写的安全 bin 目录；如果找不到，则退回 `~/.local/bin`，不会修改你的 shell 配置。

如果你要显式看到整形层：

```bash
ovp --full --with-refine
```

如果你要开守护进程：

```bash
ovp-autopilot --watch=inbox --parallel=1 --yes
```

## 配置

在 vault 根目录放 `.env`：

```bash
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
AUTO_VAULT_MODEL=anthropic/MiniMax-M2.7-highspeed

# Optional
PINBOARD_TOKEN=username:token
HTTP_PROXY=http://127.0.0.1:7897
```

## 设计原则

- 先保证身份系统一致，再增加功能
- `registry` 与文件系统共同定义 canonical 状态
- `knowledge.db` 只做 derived retrieval，不做第二真相源
- 吸收是日常自动化的一部分；整形是强能力，但默认 opt-in
- 文档必须描述“现在真实能跑的东西”，不是未来路线图

## 相关资源

- [Showcase Vault](https://github.com/fakechris/obsidian_vault_showcase)
- [PyPI](https://pypi.org/project/obsidian-vault-pipeline/)
- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

当前文档对应版本：`v0.8.2`
