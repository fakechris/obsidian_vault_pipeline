---
schema_version: "1.0.0"
note_id: readme-dc2a69e8
title: "Obsidian Vault Pipeline"
description: "面向 Obsidian 的可审计知识状态运行时"
date: 2026-04-07
type: meta
---

# Obsidian Vault Pipeline

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/obsidian-vault-pipeline.svg)](https://pypi.org/project/obsidian-vault-pipeline/)

面向 Obsidian Vault 的可审计知识状态运行时<br>
Capture → Compile → Reuse

[🇬🇧 English](README.md)

</div>

主文档：

- [Architecture](ARCHITECTURE.md)（[简体中文](ARCHITECTURE.zh-CN.md)）
- [Milestone](MILESTONE.md)（[简体中文](MILESTONE.zh-CN.md)）
- [Active Backlog](BACKLOG.md)

## 这是什么

Obsidian Vault Pipeline（OVP）不是一个“多脚本拼装包”，也不只是 RAG over Markdown。它是一套围绕 Obsidian Vault 构建的本地知识状态运行时：

- **Capture**：接收 Pinboard、Clippings、Raw Markdown、论文、GitHub、网页等资料，并保持 source lifecycle 可追踪。
- **Compile**：把资料编译成 deep dive、candidate、claim、evidence、relation、contradiction、registry 和 graph。
- **Reuse**：把已编译知识投影成 reader atlas、object page、graph、briefing、search、context pack、writing prompt 和 operator workbench。

内部仍保留六层工程模型（Ingest → Interpret → Absorb → Refine → Canonical → Derived），但产品叙事收敛为 Capture → Compile → Reuse。

当前版本已经把主要运行链路接入到日常工作流：

- `ovp --full` 默认跑到 `knowledge_index`
- `ovp --incremental` 是日常增量入口，包含近期 Pinboard + Clippings + 后续步骤
- `ovp --full --with-refine` 会在 `moc` 后追加 `refine`
- `ovp-autopilot` 默认实时跑 `absorb -> moc -> knowledge_index`
- `ovp-autopilot --with-refine` 会在实时链路里追加 `refine`
- `ovp-ui` 提供本地 UI。默认 `/` 入口现在是 reader-first Knowledge Library，operator dashboard 放在 `/ops`；object page 已有 source/backlink 上下文，`/graph`（也包括 `/map`）已是面向读者的 knowledge map。

## 为什么会变成现在这套架构

这个项目最早是围绕 Obsidian Vault 的自动化整理脚本发展起来的，但随着能力增加，出现了三个典型问题：

- 运行时主流程和单个脚本各自演化，难以保证契约一致
- 概念、链接、Atlas、graph、检索索引彼此耦合，但真相边界不清楚
- 一旦要支持媒体、医疗、工程研究这类不同领域，原来的 concept-only 模型会失真

现在这套设计的目标就是把这些问题拆开：

- 用 Capture → Compile → Reuse 解释产品价值
- 用 source → observation → claim → evidence → validity → projection → permission 解释长期知识状态
- 用六层运行模型明确“什么是编排层、什么是真相层、什么是派生层”
- 用 `research-tech` 把当前技术研究语义正式化
- 用 `default-knowledge` 保留默认兼容层
- 用 Pack API 让不同领域通过 pack 接入，而不是继续往 core 里硬编码特例

这意味着当前仓库已经不只是一个 Vault 自动化项目，而是一个：

> reader-first, evidence-backed knowledge atlas over an auditable knowledge state runtime

其中：

- `research-tech` 是第一套显式内置标准 pack
- `default-knowledge` 当前仍保留为默认兼容 pack
- `knowledge.db` 是 derived store，不是 Authority
- vault markdown + registry + evidence chain 才是长期可信边界

## 当前路线图

当前路线图正在合并 repo 历史 milestone、4 月 22 compiler roadmap、vault 内近期 KSR backlog，以及 reader-first 产品形态研究。这里的 KSR 页是近期任务抽取输入，不是完整 backlog authority：

- 当前 active backlog：`BACKLOG.md`
- 近期 KSR backlog 输入：`/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md`
- 当前 milestone：`MILESTONE.md`
- 当前合并路线图依据：`docs/plans/2026-04-29-consolidated-product-roadmap.md`
- reader 产品形态记录：`docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md`

当前 milestone 顺序：

| Milestone | 状态 | 说明 |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Complete | CLI、source lifecycle、pack/profile、`knowledge.db`、KSR-013 第一版 |
| M1 Operator Workbench And Review Runtime | Complete enough | truth UI、candidates、signals/actions、contradictions、action worker |
| M2 Roadmap And README Consolidation | Complete | 已合并历史 milestone、compiler roadmap、近期 KSR 输入与 reader-product 研究，重整 README |
| M3 Reader-First Knowledge Atlas | Done / iterate | reader home、`/ops` 拆分、object source/backlink rail、visual graph map、按 kind 区分的 object reader lens、reader-oriented search 已交付 |
| M4 KSR Safety And Hot-Path Hardening | Active | projection 标注、hot-path audit、wiring eval、article routing preview、evidence span、candidate 风险分层已交付；更深的 enforcement 仍待推进 |
| M5 Context Pack And Operational Runtime | Active / closeout | session snapshot、context budget、`/ops` 和 doctor 可见的 runtime state、provider-facing runtime-state API |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer、claim lifecycle、conflict detection、policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor、query feedback、skill/routine extraction、notebook/raw-source mode |

当前 active backlog 重点：

- 已交付：`KSR-001` Evidence span 化、`KSR-002` Projection 标注、`KSR-003` Candidate 风险分层、`KSR-004` Session snapshot/context pack、`KSR-014` Article routing preview、`KSR-015` Dashboard/search hot-path audit、`KSR-017` Explicit context budget、`KSR-018` Markdown-aware evidence span backfill、`KSR-022` OVP prime/context pack、`KSR-026` Workflow wiring eval suite，以及第一版结构化 projection repair marker lifecycle。
- 产品侧已交付：readable object page profile、source/backlink rail、按 kind 区分的 reader lens、visual `/graph` map，以及按 kind、evidence、reason 组织的 reader-oriented search。
- 当前：`BL-014` 已把 runtime state 接入 `/ops`、`ovp doctor` 和 `/api/runtime-state`，用户不用读 raw log 也能看到系统健康状态。
- 产品线：Reader-first Knowledge Atlas 作为 projection layer 实现，不另建状态系统。

## Domain Packs

当前 core 已经开始 pack 化。

- 内置标准 pack：`research-tech`
- 默认兼容 pack：`default-knowledge`
- 运行时可通过 `--pack` 和 `--profile` 选择 workflow
- 第三方 pack 可以通过 `ovp.packs` entry point 或 `OVP_PACK_MANIFESTS` manifest 列表接入

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
  启动一个本地 UI。默认 `/` 入口是 reader-first Knowledge Library；operator dashboard 放在 `/ops`
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

1. Python entry point 组：`ovp.packs`
2. 显式 manifest 列表：`OVP_PACK_MANIFESTS=/path/a.yaml:/path/b.yaml`

最小接入链路是：

1. 第三方 pack 提供 manifest
2. manifest 声明 `entrypoints.pack`
3. entrypoint 返回 `BaseDomainPack`
4. core 校验 `api_version`
5. 用户通过 `--pack/--profile` 运行

当前已经实现的硬边界：

- pack 不能把 semantic retrieval 直接升级成 canonical identity
- pack 不能把 `knowledge.db` 当 Authority
- pack 不能绕过 audit/logging
- 所有 derived state 都必须可重建

## 真实运行模型

### Authority Boundary

这套系统当前坚持以下边界：

- **Authority**：Vault Markdown + concept registry
- **derived views**：Atlas、MOC、graph、`knowledge.db`、lint、daily delta
- **不是 Authority**：`knowledge.db`

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

- `absorb` 现在走的是 `ovp_pipeline.commands.absorb`
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
| `ovp --incremental` | 日常增量流水线（包含近期 Pinboard + Clippings + 后续步骤） |
| `ovp --full --with-refine` | 完整流水线 + cleanup/breakdown |
| `ovp --step absorb` | 单独跑吸收层 |
| `ovp --step refine` | 单独跑批处理整形 |
| `ovp --from-step absorb` | 从吸收层之后继续跑 |

`ovp --incremental` 是推荐的日常入口。
它和 `ovp --from-step clippings` 不同：前者会先跑 `pinboard -> pinboard_process`，后者会显式跳过 Pinboard。

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
| `ovp-absorb --file <source.md> --dry-run --json` | 在移动或处理 source material 前预览 source lifecycle 路由 |
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
| `ovp-graph build --layered --seed-match <pattern> --output <out.html>` | 子图浏览（HTML 交互式） |
| `ovp-lint --check --vault-dir <vault>` | 运行链接/结构检查 |
| `ovp-query "..." --engine fused` | 默认即 fused（BM25 + 向量 RRF + Recency/Frequency/Importance 衰减），`--engine bm25 \| vector` 显式回退 |

### 链接密度 / 语义层 / Crystal / Exploration

Phase 38 把链接密度和语义层补齐，并给 reviewer 一个图原生的探索面。

| 命令 | 说明 |
|---|---|
| `ovp-link-suggest --vault-dir <vault> --dry-run` | 为 `link_out_count < 3` 的 evergreen / deep_dive 跑 BM25+向量混合检索，输出 JSONL 候选到 `60-Logs/link-suggestions/<run_id>.jsonl` |
| `ovp-link-suggest --apply --confirm` | 把候选写回原 markdown body（默认 `--dry-run`，必须 `--confirm` 才能落盘） |
| `ovp-link-suggest --llm-gate` | 增加 LLM 二次裁判，按 `link \| skip` 分类；客户端不可用时打印 stderr 警告并回退到 RRF-only |
| `ovp-build-crystals --vault-dir <vault>` | 物化 briefing 快照到 `40-Resources/Crystals/<crystal_id>.md`，frontmatter 记录 `source_object_ids` 与 `evolves_relations` |
| `ovp-working-memory --vault-dir <vault>` | 写入今日 `60-Logs/working-memory/YYYY-MM-DD.md`：Top of Mind / Fresh Crystals / Pending Decisions / EVOLVES Today / Pulse Highlights；幂等覆盖 |
| `ovp-prime --vault-dir <vault> --session-id <id>` | 写入会话启动快照 `60-Logs/session-snapshots/<id>.md`，刷新 `latest.md`，并为注入会话的对象记录 `ovp_prime` reuse events |
| `ovp-runtime-state --vault-dir <vault> --write --json` | 从 repair marker、pipeline event、reuse event 生成 operational runtime state projection，写入 `60-Logs/runtime-state/current.{json,md}` |
| `/api/runtime-state?write=1` | 本地 UI/API 读取同一份 provider-facing runtime-state projection |
| `ovp-concept-dedup --propose` / `--apply --confirm` | 基于 cosine 相似度合并近义 evergreen，`--apply` 写回链接并归档失败者到 `70-Archive/dedup-merged/` |
| `ovp-mcp --vault-dir <vault>` | stdio JSON-RPC 服务，暴露 `graph_node_details / graph_neighborhood / graph_shortest_path / graph_bridge_nodes / graph_communities` 等 MCP 工具；`graph_neighborhood` 接受 `render: "json" \| "html"` |
| `ovp-ui` 路由 `/explore?object_id=<id>` | 三栏 reviewer UI：图谱 canvas + agent timeline (SSE) + Crystal 合成面板 |

#### `ovp-graph build` 推荐组合

`--layered` 走 hop1=evergreen / hop2=source-md 的两层 BFS，是浏览子图的默认形态。在它之上叠加两层质量过滤：hop1 节点度过滤（按 seed-degree 筛选）+ HTML 视图的 hop2 自动折叠。

| 子图规模 | 推荐 flag 组合 | 说明 |
|---|---|---|
| <100 节点（聚焦查询，如 `agent memory`） | 默认即可 | cose-bilkent 一次能铺清楚，加 prune 反而会过度损失 |
| 100–500 节点（中等主题，如 `MCP`） | `--min-seed-degree 2` | 丢掉只挂一个 seed 的弱 hop1 节点，抑制 concept drift |
| >500 节点 / hop1 fan-out 极宽 | `--min-seed-degree 2 --top-k-per-seed 5` | 横向再裁剪每个 seed 的 hop1 邻居数 |
| 任何规模 → HTML | 自动启用 | 节点 >300 时 hop2 默认折叠，点 hop1 按需展开；URL 加 `?collapse_hop2=always` 或 `?collapse_hop2=never` 可强制覆盖 |

实测 `MCP` 子图（85 seed）：baseline 400 节点 / 656 边 → `--min-seed-degree 2` 砍到 204 节点 / 280 边（-49% / -57%）。


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
├── 40-Resources/
│   └── Crystals/                    # ovp-build-crystals 物化的持久化 briefing
├── 60-Logs/
│   ├── pipeline.jsonl
│   ├── refine-mutations.jsonl
│   ├── transactions/
│   ├── quality-reports/
│   ├── daily-deltas/
│   ├── link-suggestions/            # ovp-link-suggest 输出 JSONL
│   ├── working-memory/              # ovp-working-memory 每日 distill
│   ├── session-snapshots/           # ovp-prime 会话启动快照
│   ├── runtime-state/               # ovp-runtime-state 当前运行状态投影
│   └── knowledge.db
└── 70-Archive/
    └── dedup-merged/                # ovp-concept-dedup --apply 归档
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
- `page_metrics`（last_seen_ts / reuse_count / citation_count，供 `search_fused` 衰减排序）

它由 `ovp-knowledge-index` 重建，供以下读取场景使用：

- 关键词搜索
- embedding 检索
- canonical slug 读取
- 审计事件浏览
- 工具发现与只读服务

默认 discovery 也已经统一到这里：

- `ovp-query` 默认走 `knowledge.db` 的 `search_fused`：BM25 + 向量 RRF (k=60)，再叠 Recency / Frequency / Importance 衰减
- `--engine bm25` / `--engine vector` 可显式回退到单引擎
- QMD 不再是默认检索依赖，只能通过显式 `--engine qmd` 启用

## 快速开始

```bash
curl -fsSLO https://raw.githubusercontent.com/fakechris/obsidian_vault_pipeline/main/scripts/install-user.sh
less install-user.sh
bash install-user.sh

mkdir -p my-vault
cd my-vault

ovp --check
ovp --full
```

如果你更偏好显式的 PyPI 两步安装：

```bash
python3 -m pip install --user obsidian-vault-pipeline
python3 -m ovp_pipeline.installer
```

如果你的 Python 环境启用了 PEP 668，优先使用：

```bash
pipx install obsidian-vault-pipeline
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
- Wiki、MOC、Dashboard、Briefing、Graph、Reader Page、Context Pack 都是 projection，已带显式 projection metadata，并且必须能追回 source/evidence
- Reader-facing UI 应先让用户理解知识，再暴露 operator/debug 细节
- 文档必须描述“现在真实能跑的东西”，不是未来路线图

## 相关资源

- [Showcase Vault](https://github.com/fakechris/obsidian_vault_showcase)
- [PyPI](https://pypi.org/project/obsidian-vault-pipeline/)
- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

---

当前文档对应版本：`v0.9.2`
