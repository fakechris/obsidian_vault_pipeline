# OVP Milestone

> 语言： [English](MILESTONE.md) | 简体中文

**更新时间：** 2026-05-01
**状态：** 当前 milestone 顺序与实施方向

这份文档是稳定的 milestone 入口。它总结当前产品和工程路线；[BACKLOG.md](BACKLOG.md) 仍然是唯一 active implementation queue。

## 输入来源

当前 milestone 顺序合并以下输入：

- repo 已交付 milestone 历史和 phase 文档
- [Vision & Roadmap: The Auditable Knowledge Compiler](docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md)
- `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md` 里的近期 KSR task extraction
- [reader-first 产品形态研究](docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md)
- kg-eval 质量评估和 `OVP_FIX_PLAN.md` (2026-05-01)

vault 里的 KSR 项目页是高信号的近期输入，但不是完整 backlog authority。实施顺序由 [BACKLOG.md](BACKLOG.md) 维护。

## 产品判断

OVP 正在从个人 Zettelkasten 演进为：

> 类型化、证据支撑的知识平台 —— 对人 reader-first，对 Agent 可编程，通过领域 Pack 可扩展。

三个目标层级：

1. **个人知识地图** — 带类型化实体的 Zettelkasten 质量（当前 + M8）
2. **公司第二大脑** — Pack 驱动的领域本体，团队共享的类型化知识（M9）
3. **运营知识层** — Palantir 式的 Action、Decision、Audit（M10，按 ROI 评估）

## 当前 Milestones

| Milestone | 状态 | 含义 |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Done | CLI、source lifecycle、pack/profile runtime、`knowledge.db`、第一段 source-lifecycle idempotency |
| M1 Operator Workbench And Review Runtime | Done | truth UI、candidates、signals/actions、contradictions、action worker |
| M2 Roadmap And README Consolidation | Done | 已合并历史 milestones、compiler roadmap、近期 KSR 输入、reader-product 研究，以及英文主文档结构 |
| M3 Reader-First Knowledge Atlas | Done | reader home、`/ops` 拆分、object source/backlink rail、visual graph map、按 kind 区分的 object reader lens、reader-oriented search 已交付 |
| M4 KSR Safety And Hot-Path Hardening | Done | projection labels、hot-path audit、wiring evals、evidence spans、candidate risk、JSONL 流式化、projection lifecycle 加固、runtime-state API 修复 (最终 PRs: #98, #99, #100) |
| M5 Context Pack And Operational Runtime | Done | session snapshots、context budget、`/ops` 和 doctor 可见的 runtime state、provider-facing runtime-state API、action queue health |
| M5a Quality And Dedup Hardening | Done | concept dedup pipeline 集成（scope_slugs）、promote semantic guard (trigram-Jaccard)、历史数据清理 (71→61 Evergreens)、`find_similar_slugs` 工具 (PR #101) |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer、claim lifecycle、conflict detection、policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor、query feedback、routines、notebook/raw-source mode |
| **M8 Type Unification And Extraction Quality** | **Active** | 统一 object kind 分类体系、Layer 1 `entity_type` frontmatter、body-size-aware extraction (P3)、quote-grounding (P4)、single-pass LLM 重构 (P5)、历史回填 |
| **M9 Pack As Domain Ontology** | **Next** | Pack 定义 object kind specs、typed relation constraints、schema registry、domain-specific extraction profiles |
| **M10 Operational Knowledge Layer** | **Later** | object 上的 action types、permission + contract、跨实体聚合、decision memory |

## Active Backlog 对齐

| 架构 / 产品工作 | Active backlog 映射 |
| --- | --- |
| Reader shell route split | `BL-001` 已在 PR #75 交付 |
| Projection marking | `BL-002`, `KSR-002` 已在 PR #78 交付 |
| Dashboard/search hot-path audit | `BL-003`, `KSR-015` 已在 PR #77 交付 |
| Workflow wiring eval suite | `BL-004`, `KSR-026` 已在 PR #77 交付 |
| Article routing preview | `BL-005`, `KSR-014` 已在 PR #81 交付 |
| Evidence span / factual evidence completeness | `BL-006`, `KSR-001`, `KSR-018` 已在 PR #82 交付 |
| Candidate risk layering | `BL-007`, `KSR-003` 已在 PR #82 交付 |
| Kind-aware object pages and backlink rail | `BL-008`、`BL-009` 已通过 PR #79 和 PR #83 交付 |
| Visual graph MVP | `BL-010` 已在 PR #80 交付 |
| Reader-oriented search | `BL-011` 已在 PR #84 交付 |
| Trusted reuse context pack / OVP prime | `BL-012`、`BL-013` 第一版已在 PR #89 和 PR #90 交付 |
| Operational runtime state projection | `BL-014` 第一片在 PR #91 落地；`/ops` / doctor / API 集成在 PR #92；M5 closeout slice 收口 action queue health 和物化读侧策略 |
| Projection repair lifecycle | `BL-020` 已在 PR #87 落地 |
| Schema versioning and migration trigger | `BL-021` 已在 PR #87 和 PR #88 中补完 |
| 架构重构 (JSONL、truth_api、ui_server、projection) | PR #100 |
| Concept dedup pipeline + promote semantic guard | PR #101，`BL-025` 至 `BL-030` 是 M8 范围 |

## 近期顺序

建议顺序：

1. **M8 优先**：执行 `BL-025`（类型统一）→ `BL-026`（extraction 输出）→ `BL-027`（P3）→ `BL-028`（P4）→ `BL-029`（P5）→ `BL-030`（回填）。
2. **M9 随后**：M8 类型体系稳定后，执行 `BL-031` 至 `BL-034`。
3. **M10 评估**：M9 交付后，基于真实多 Pack 采用情况和公司大脑用例决定范围。
4. `BL-015`（permissions）当 permission 和 claim lifecycle 成为主动瓶颈时再进入。
5. workflow action 继续使用现有 action worker lock；只有引入多 worker 调度时才新增通用 workflow lease。

## 文档规则

- `README.md` 是英文主 README。
- `README.zh-CN.md` 是中文 README。
- `ARCHITECTURE.md` 是英文主 architecture contract。
- `ARCHITECTURE.zh-CN.md` 是中文 architecture contract。
- `MILESTONE.md` 是英文主 milestone 入口。
- `MILESTONE.zh-CN.md` 是中文 milestone 入口。
- [BACKLOG.md](BACKLOG.md) 是 active implementation backlog source。
- 历史 phase 文档保留为 evidence/context；除非被 `BACKLOG.md` 引用，否则不是 active execution source。

## 详细依据

更完整的合并和排序理由见：

- [Consolidated Product Roadmap](docs/plans/2026-04-29-consolidated-product-roadmap.md)
- [Reader Product Shape And Backlog Reconciliation](docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md)
- [Architecture](ARCHITECTURE.md)
