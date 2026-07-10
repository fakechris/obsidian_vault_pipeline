# 从 OVP 到 OVP2 —— 改了什么、为什么、如何迁移

[English](ovp-to-ovp2.md)

OVP2（本仓库，二进制名 `ovp2`）是 Python 版 Obsidian Vault Pipeline 的
Rust 全量重写。本文是决策记录：旧系统是什么、为什么重建、哪些决策定义了
OVP2、用户的用法有何不同、现有 OVP vault 需要知道什么。

## 1. OVP 曾经是什么

Python 版 OVP（PyPI 上的 `obsidian-vault-pipeline`，最后版本 v0.22.0）是
围绕 Obsidian vault 构建的知识状态运行时：

- **六阶段管线**——Ingest → Interpret → Absorb → Refine → Normalize →
  Derive——由 `ovp --full` / `ovp --incremental` 编排，另有实时监听守护进程
  （`ovp-autopilot`）。
- **`knowledge.db` SQLite 投影**（页面索引、FTS、链接、embedding、审计事件、
  truth projection），供 `ovp-query`、`ovp-truth`、`ovp-doctor` 和 UI 读取。
- **Evergreen / MOC / canonical 概念的雄心**：absorb 抽取概念候选，晋升为
  canonical Evergreen 笔记，维护 Atlas MOC、实体层、概念注册表，后期还有
  Louvain + LLM 的 "crystal" 合成。
- 庞大的 CLI 面（约 90 个入口：absorb 时代的命令、`refine`、
  `ovp-build-crystals`、`ovp-ask`、`ovp-export`、`ovp-packs` 等）和一个
  **服务端渲染的 web UI**（`ovp-ui`，通常在 `127.0.0.1:8787`）：`/` 是
  reader-first 的 Knowledge Library，`/ops` 是运维仪表盘。

## 2. 为什么重建

诚实的三步弧线：

**概念图谱方向验证失败。** 重写起初是对 Python 架构的移植：急切的概念抽取、
canonical slug、Referent/实体层、MOC 重建、对铸造笔记的 RAG。当 v2 概念图谱
路径终于跑上真实模型和真实文章（M13）时，结果是 **3 张概念图 0 张被判定为
真实可用**——此前已投入约八个里程碑的基础设施。结论不是"再改改 prompt"，
而是"根就错了"：从单篇文档急切铸造 canonical 概念，产出的是自信但无法核查的
抽象。canonical / Referent / RAG 层被降级（仍可构建、仍有测试，在 `--help`
中标注 DEMOTED），产品路径不再依赖它们。

**转向：接地阅读主干，然后是结晶真相层。** 替代的根是逐字接地。每个源走一遍
**阅读主干**：原文 → 接地 **Unit**（每条携带原文的逐字引文和行号；
`accepted_without_quote = 0` 是硬 gate）→ critic 修复 → 可读 **Card** →
写入 vault 的 Reader Pack。其上是 **Crystal 真相层**：跨源 **Claim** 必须
通过机械化引用 gate（每个引用 → 已接受的 Unit → 逐字引文 → 原文行号），
LLM 强度裁决把每条主张路由到 **durable** 或 **caveated**，append-only 账本，
幂等写入。运行法则：*不能引用逐字证据的主张，不被持久化。* 在真实文章上与
商业记忆系统正面对比，接地层在覆盖率与可溯源性上胜出（M21、M26：17 优 /
3 平 / 0 负；核心覆盖率 87% vs 58%）。

**选 Rust，出于产品理由。** 一个预编译静态二进制（`ovp2`），curl 或
Homebrew 安装，用户机器上不需要 Python 环境；离线构建零网络依赖，测试链
确定性可复现；重写强制收敛为一个干净的核心，而不是 Python 代码库积累出的
脚本蔓延。

## 3. 关键决策

| 决策 | 为什么 |
|---|---|
| **真相层是护城河** | 图数据库、embedding、RAG 都是大路货的时尚；逐字接地、行号可引、人工可核查的主张不是。OVP2 的每个面（门户、`ask`、`find`、摘要）读的都是可以点进原文行号核对的证据。 |
| **fail-loud gate，拒绝静默修复** | 引用有缺陷的主张会以非零退出，而不是被悄悄修补或丢弃。静默修复会把数据损坏转化为信任损坏；各 gate（`crystal-lint`、强度裁决、`ask` 的引用校验）让每次拒绝都可见、可归因。 |
| **append-only 账本 + 可重建投影** | 权威状态是 vault 内的一组 append-only JSONL 账本加文件。索引、控制台、门户数据、主题视图都是投影：删掉不丢任何东西，全量重建就是全部的迁移故事。投影如果无法重建，那是架构 bug。 |
| **不用 SQLite** | Python OVP 的 `knowledge.db` 是会漂移的第二真相源，需要备份（`ovp-backup-db`），还把状态藏在 vault 之外。OVP2 决定用账本 + 文件，`ovp2 index` 产出纯 JSON 读模型——只有当日常查询之痛证明必要时才重新考虑。 |
| **演化内核治理** | prompt、解析器、gate、运行时面只能通过已验证的候选变更：注册的组件、事先陈述的假设、基于 cassette 的配对 A/B、append-only 演化账本记录（`ovp2 evolve`）。这防止用改 prompt 掩盖运行时 bug，让每次行为变更可归因、可回滚。 |
| **产品门户信息架构，不是管线控制台** | 用户看到今天 / 资料 / 搜索 / 知识 / 对话；管线内部（运行、flow、审计、候选）收进系统页。每个页面回答一个用户问题；回答不了问题的页面不存在。Python 时代 `/ops` 优先的仪表盘被倒转。 |
| **双主题设计系统** | 门户搭载 operator 自己的 OVP Design System：浅色 "Atelier"（暖羊皮纸 + 赤陶土）与深色 "Vault"（近黑 + 深蓝 + 青）平权双主题，IBM Plex 字体，quiet-utility 规则（1px 边框、零渐变、text-first）。图谱配色取自同一套 token，可视化不会分叉出第二套视觉语言。 |
| **国际化：默认英文 + 简体中文** | 一套界面、完整翻译——不是中英并排。语言与主题用同一机制（`localStorage`，UI 内切换）。用户面词汇用产品词（资料、记忆、主张、主题）；内部词（pack、cassette、unit）只出现在系统页与 CLI。 |
| **预编译分发 + 版本谱系** | 用户安装的是二进制，不是工具链：cargo-dist 按 tag 构建 curl 安装器和 Homebrew formula。v0.23.0 刻意延续仓库的版本编号（v0.22.0 是最后一个 Python 时代版本）；**v2.0.0 保留给合并主干 / Python 退役里程碑**。 |
| **Pinboard 首次同步防洪保护** | 真实事故（2026-07-09）：第一次 `pinboard-sync --live` 物化了 operator 的全部历史——50,714 条书签笔记、198 MB——因为 `posts/all` 返回一切。现在不带 `--since`/`--max` 时，任何将创建超过 500 条新笔记的运行都在写入前中止；`--yes-all` 是显式覆盖。 |
| **enrichment 让裸书签成为一等公民** | 没有正文的书签不是可读证据。网页抓取与 GitHub README enrichment（feature 门控 `web-fetch-live` / `github-live`，预编译二进制已内置）补全 `needs-content` 源，让它们能进入阅读主干，而不是烂在收件箱。 |
| **语义主题取代关键词分桶** | 主题分组正从硬编码关键词分桶迁移到 embedding + Louvain 社区投影——和其他视图一样，是真相层之上的可重建投影，永不权威。进行中。 |

## 4. 用户的用法有何不同

命令映射（旧 Python 入口 → `ovp2`）：

| Python OVP | OVP2 |
|---|---|
| `ovp --full` / `ovp --incremental`（六阶段运行） | `ovp2 daily --vault-root <vault> --client live` |
| `ovp-autopilot`（监听守护进程） | 按设计无守护进程——用 cron/launchd 调度 `ovp2 daily` |
| `pinboard-processor.py` / pinboard 阶段 | `ovp2 pinboard-sync --vault-root <vault> --live` |
| `ovp-ui --vault-dir <vault> --port 8787` | `ovp2 serve --vault-root <vault>`（门户在 `127.0.0.1:3141`） |
| `ovp-query` / `ovp-truth`（读 `knowledge.db`） | `ovp2 find --vault-root <vault> [term] [--kind --status --date --json]` |
| `ovp-ask` | `ovp2 ask --vault-root <vault> "问题"`（带引用校验） |
| `/digest` 每日合成 | `ovp2 digest --vault-root <vault>` |
| `ovp-build-crystals` | `ovp2 crystal-synth --vault-root <vault>`（带 gate，durable/caveated 路由） |
| Evergreen 晋升 / absorb 复核 | `ovp2 crystal-review-session` + `crystal-review-session-apply`（人工决策永不绕过 gate） |
| `ovp-doctor` / `ovp-lint` | `ovp2 doctor --vault-root <vault> [--fix]` |
| `ovp-export` | 门户 + `ovp2 find --json`；durable 主张写为笔记用 `ovp2 project --write` |
| `ovp-backup-db` | 不再需要——没有数据库；账本是 vault 内的普通文件 |
| MCP 面 | `ovp2 mcp --vault-root <vault>`（stdio JSON-RPC） |

产品面映射：

| 旧面 | 新面 |
|---|---|
| `ovp-ui` reader / maintainer 壳（服务端渲染） | 门户 SPA：今天 / 资料 / 搜索 / 知识 / 对话 / 系统 |
| `knowledge.db` | `.ovp/index/` JSON 投影（`index.json`、`evidence.json`），由 `ovp2 index` 重建 |
| Evergreen 笔记 + Atlas MOC | Crystal 主张（durable/caveated）+ 可选的 vault 笔记 `ovp2 project --write`（`10-Knowledge/Crystal/`，机器所有，`<!-- crystal-managed -->`） |
| Pack / profile（`--pack`、`--profile`） | 退役——只有一条 blessed 产品路径；变体由演化内核治理 |

注意参数变化：Python CLI 用 `--vault-dir`；每个 `ovp2` 命令都用
`--vault-root`。

## 5. 迁移现有 OVP vault

**原样保留的。** vault 本身：你的笔记、`Clippings/`、`50-Inbox/` 捕获物、
已处理的源、附件。OVP2 沿用同一 PARA 家族布局（`50-Inbox/01-Raw`、
`03-Processed`、`40-Resources`、`60-Logs`），永不改写捕获内容。

**重建的。** 全部投影。OVP2 从 `.ovp/` 里的空账本开始；想进入真相层的源要
走一遍阅读主干（`ovp2 daily` 会处理清扫进 `01-Raw` 的一切）。读模型、
控制台、门户数据随后都是确定性重建——没有导入步骤，因为没有可导入的对象：
投影永远从账本 + pack 重新生成。

**退役的。** `knowledge.db`（及其备份）、canonical 概念库与注册表、自动铸造
的 Evergreen 存根、pack 系统。原地留着或归档皆可；OVP2 不读它们。历史
Evergreen 笔记仍是普通 vault markdown——可读、可链接，只是不再被机器维护。

## 6. 当前状态与路线图

v0.23.0 已在真实 vault 上交付每日循环、门户、Crystal 合成与复核流程。
进行中：持续的真实 vault dogfood、语义主题投影、复核环路打磨。合并到
`main`——即正式的 Python 退役——由
`docs/stage-m32-python-retirement-and-product-definition.md` 的 Level-3
签核清单把关；在此之前 Rust 主干在 `codex/rust-migration` 分支。

延伸阅读：`docs/architecture.md`（系统如何构建）、
`docs/operator-runbook.md`（如何运行）、`docs/stage-m15-results.md` 与
`docs/stage-m17-grounded-reader-trunk.md`（转向的证据）、
`docs/mainline-return-matrix.md`（对照 legacy 的能力审计）。
