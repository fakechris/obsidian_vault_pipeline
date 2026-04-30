# OVP Milestone

> 语言： [English](MILESTONE.md) | 简体中文

**更新时间：** 2026-04-30
**状态：** 当前 milestone 顺序与实施方向

这份文档是稳定的 milestone 入口。它总结当前产品和工程路线；[BACKLOG.md](BACKLOG.md) 仍然是唯一 active implementation queue。

## 输入来源

当前 milestone 顺序是四类输入的合并视图：

- repo 已交付 milestone 历史和 phase 文档
- [Vision & Roadmap: The Auditable Knowledge Compiler](docs/plans/2026-04-22-vision-and-roadmap-trusted-reuse-compiler.md)
- `/Users/chris/Documents/ovp-vault/30-Projects/Active/OVP-Knowledge-State-Runtime.md` 里的近期 KSR task extraction
- [reader-first 产品形态研究](docs/plans/2026-04-29-reader-product-shape-and-backlog-reconciliation.md)

vault 里的 KSR 项目页是高信号的近期输入，但不是完整 backlog authority。实施顺序由 [BACKLOG.md](BACKLOG.md) 维护。

## 产品判断

OVP 正在从 document-processing pipeline 变成：

> reader-first, evidence-backed knowledge atlas over an auditable knowledge state runtime.

这意味着面向用户的产品应该先让“编译后的知识”容易阅读和理解；operator dashboard 仍然保留，但应放到 `/ops` 这类维护型界面下。

## 当前 Milestones

| Milestone | 状态 | 含义 |
| --- | --- | --- |
| M0 Pipeline And Pack Foundation | Done | CLI、source lifecycle、pack/profile runtime、`knowledge.db`、第一段 source-lifecycle idempotency |
| M1 Operator Workbench And Review Runtime | Done / maintain | truth UI、candidates、signals/actions、contradictions、action worker |
| M2 Roadmap And README Consolidation | Done | 已合并历史 milestones、compiler roadmap、近期 KSR 输入、reader-product 研究，以及英文主文档结构 |
| M3 Reader-First Knowledge Atlas | Active | reader home、`/ops` 拆分、第一版 object source/backlink rail、visual graph map 已交付；更深的 kind-specific object layout 仍待推进 |
| M4 KSR Safety And Hot-Path Hardening | Active | projection labels、hot-path audit、wiring evals 已交付；routing preview、evidence spans、candidate risk 仍待推进 |
| M5 Context Pack And Operational Runtime | Later | session snapshots、context budget、claim leases、provider facade、observability |
| M6 Policy, Permission, And Knowledge Evolution | Later | permission layer、claim lifecycle、conflict detection、policy promotion |
| M7 Semantic Extraction And Query Feedback Loop | Later | relation extractor、query feedback、routines、notebook/raw-source mode |

## Active Backlog 对齐

| 架构 / 产品工作 | Active backlog 映射 |
| --- | --- |
| Reader shell route split | `BL-001` 已在 PR #75 交付 |
| Projection marking | `BL-002`, `KSR-002` 已在 PR #78 交付 |
| Dashboard/search hot-path audit | `BL-003`, `KSR-015` 已在 PR #77 交付 |
| Workflow wiring eval suite | `BL-004`, `KSR-026` 已在 PR #77 交付 |
| Article routing preview | `BL-005`, `KSR-014` |
| Evidence span / factual evidence completeness | `BL-006`, `KSR-001`, `KSR-018` |
| Candidate risk layering | `BL-007`, `KSR-003` |
| Kind-aware object pages and backlink rail | `BL-008` partial，`BL-009` 已在 PR #79 交付 |
| Visual graph MVP | `BL-010` 已在 PR #80 交付 |
| Projection repair lifecycle | `BL-020` |
| Schema versioning and migration trigger | `BL-021` |

## 近期顺序

建议顺序：

1. 实施 `BL-005`：在 source lifecycle 继续变化前补 article routing preview。
2. 实施 `BL-006 + BL-007`：推进 evidence span 和 candidate risk layering。
3. 继续 `BL-008`：在第一版 reader profile 之后补更深的 per-kind object layout。
4. 实施 `BL-011`：按 kind、summary、evidence、reason 重做 reader-oriented search。

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
