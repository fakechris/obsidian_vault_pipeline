# OVP 架构

> 文档索引: [README](./README.zh-CN.md) | **ARCHITECTURE** | [RUNTIME](./RUNTIME.md) | [PACKS](./PACKS.md) | [PRODUCT_SURFACES](./PRODUCT_SURFACES.md) | [GLOSSARY](./GLOSSARY.md)
>
> 语言: 中文 | [English](./ARCHITECTURE.md)
>
> **本文件解释:** 持久知识存放在哪里、什么是可重建的、治理控制面控制什么。
> **本文件不解释:** 产品路线图、UI 细节、Pack 开发、命令执行流程、Backlog 状态。每一项都在文档索引顶端的对应文件里。

---

## 一句话

OVP 把外部 **来源 (Sources)** 转成可审阅的 **候选 (Candidates)**,把通过审阅的内容提升为 **正式知识状态 (Canonical State)**,从中构建可重建的 **投影 (Projections)**,再通过 **使用界面 (Access Surfaces)** 把这些投影暴露出去。**治理控制面 (Governance Control Plane)** 控制每一次写入、提升、修复和审计边界。

## 六个核心词

整个架构只有六个一级词。其它所有概念(Crystal、Atlas、KSR、Capture/Compile/Reuse、各种运行阶段等等)要么是这六个的一种,要么放在 [词汇表](./GLOSSARY.md) 里。

| 词 | 来源 | 可变性 | 是否定义真相? |
| --- | --- | --- | --- |
| **Source** | 外部(网页、论文、repo、剪藏) | 原文不可改;可附加元数据 | 否 |
| **Candidate** | 系统提出(LLM、解析器、agent) | 可改、可拒、可合并 | 否 |
| **Canonical State** | 经过 promotion 接受 | 通过 Governance + 审计可修订 | **是** |
| **Projection** | 从 Canonical State 派生 | 可自由删除并重建 | 否 |
| **Access Surface** | UI / 搜索 / 图 / 简报 / 导出 / MCP | 主要是读;写必须经 Governance | 否 |
| **Governance** | 控制面(横切) | 配置规则,本身不持有知识 | 控制 |

## 数据流

```text
Inputs / Sources                          (外部,不可改)
        |
        v
Candidates                                (系统提出,等待审阅)
        |        ^
        |        |  promotion / 审阅 / 验证 / 审计
        v        |
Canonical State                           (已接受,有证据,长期持久)
        |
        v
Projections                               (knowledge.db、graph、search、crystals)
        |
        v
Access Surfaces                           (reader / ops / search / briefing / MCP / export)


Governance Control Plane (横切)
        promotion · 审阅 · 验证 · 修复 · 权限 · 审计
```

Governance **不是** 数据流上的"第四步"。它横跨所有四个状态,控制每一次提升 Candidate、修复 Projection、或者 Surface 触及 Canonical State 的写操作。

---

## 词条: Source(来源)

**含义:** 用户带来的或外部抓取的原始材料 — 剪藏文章、PDF 论文、GitHub repo 快照、Pinboard 条目、手写笔记。

**存储位置:** `50-Inbox/03-Processed/<YYYY-MM>/`、`60-Logs/raw_data/`、`aliases.json`、内容哈希。

**由谁产生:** `ovp-article` / `ovp-paper` / `ovp-github` / `ovp-clippings` 以及用户手动粘贴 markdown。

**能删除吗?** 处理后副本可归档,文件系统上的原始记录是 Inputs 的来源。

**能定义真相吗?** 否。Source 是 *原始输入*,真相需要"证据 + 接受"。

**失败模式:** 原文丢失、改名、编码无法解析。

**如何修复:** 从源头重新摄取、重新计算内容哈希、手动重新关联元数据。

**测试:** 一个 Source 的 `(content_hash, ingestion_timestamp)` 唯一标识它;同一 URL 重新摄取(内容未变)必须幂等。

## 词条: Candidate(候选)

**含义:** 系统提出但 *尚未被接受* 的内部状态。包括建议的 object、claim、relation、entity 合并。

**存储位置:** `60-Logs/knowledge.db` 的 candidate 表;`_Candidates/` 目录里 promotion 前的 evergreen 草稿 frontmatter。

**由谁产生:** `auto_evergreen_extractor`、语义关系抽取器、LLM 身份合并提议器、歧义路由。**永远是从一个 Source 或另一个 Candidate 派生,从不凭空生成。**

**能删除吗?** 可以。被拒绝的 Candidate 直接丢弃是正常流程。

**能定义真相吗?** 否。必须经过 promotion 才能进入 Canonical State。

**能写入 Canonical State 吗?** 不能,除非通过 Governance promotion。

**失败模式:** 过期(源文已变)、孤儿(源被删)、重复、被拒但无审计记录。

**如何修复:** 从当前 Source 重新抽取、标为被取代、走审阅流程。

**测试:** Canonical State 的每一行都必须能通过审计链回溯到一个或多个被接受的 Candidate。

## 词条: Canonical State(正式知识状态)

**含义:** 有证据支撑、用户拥有、经过接受流程的长期知识状态。OVP 的信任边界。

**存储位置:** Vault Markdown(`10-Knowledge/Evergreen/**`、`10-Knowledge/Entity/**`、`40-Resources/`)、concept/alias registry、evidence chains、audit log。

**由谁产生:** Governance promotion;用户直接编辑 vault markdown(带审计 hook)。

**能删除吗?** 仅通过 Governance 显式删除并审计;从不静默删除。

**能定义真相吗?** **可以。** 这就是 OVP 架构里"真相"的定义。

**失败模式:** 冲突的 claim 未解决、缺失证据、孤儿身份合并、损坏的 markdown。

**如何修复:** 审阅队列、矛盾解决、证据重新关联、带审计的手动修正。

**测试:** 删掉所有 Projection → 从 Canonical State 重建 → 所有 Projection 都能恢复。**如果某个 Projection 重建不出来,说明那一层承载了本应在 Canonical State 的真相 — 这是架构 bug。**

## 词条: Projection(投影)

**含义:** 从 Canonical State 派生计算出来的状态。索引、图、搜索表、合成的 crystal、view-model JSON。

**存储位置:** `60-Logs/knowledge.db`、`40-Resources/Crystals/`、runtime-state JSON、`compiled_views/`。

**由谁产生:** `ovp-knowledge-index`、`ovp-build-views`、`ovp-synthesize-community-crystals`、各种运行时投影器。

**能删除吗?** 可以。删除 Projection 是正常操作,重建就是答案。

**能定义真相吗?** 否。Projection 永远是派生视图,从不权威。

**能写入 Canonical State 吗?** 不能,除非通过 Governance(例如 Projection 发现的矛盾要进入审阅队列,而不是直接写 Canonical State)。

**失败模式:** 过期(Canonical State 变了 Projection 没跟上)、schema 不匹配、丢失。

**如何修复:** 从 Canonical State 重建。每个 Projection 必须有一条确定的重建路径;没有就是 bug。

**测试:** `rm -rf` 投影存储 → 跑 `ovp-knowledge-index` → audit / reuse 状态都保住 → 没丢真相。

## 词条: Access Surface(使用界面)

**含义:** 人或 agent 看到并操作的入口。UI 路由、MCP 工具、读类 CLI、搜索结果、简报、导出的 context pack。

**存储位置:** `commands/ui_server.py`(HTTP 路由)、`commands/mcp.py`、`40-Resources/` 的导出、briefing JSON。

**由谁产生:** 在 Projection 之上的只读组合。

**能写入 Canonical State 吗?** **不能。** 想改 Canonical State 的 Surface 必须经过 Governance —— 例如 MCP 的"批准 candidate"工具发出的是 Governance promotion 事件,不是直接写。

**失败模式:** 显示过期 Projection、绕过 Governance、把 Candidate 当 Canonical State 显示。

**如何修复:** 重建 Projection;改写 Surface 让写操作走 Governance;在 UI 显式标注 Candidate 状态。

**测试:** 关掉所有 Surface → Canonical State 不变。(Surface 是纯消费者,缺失它们不能丢失真相。)

## 词条: Governance(治理控制面)

**含义:** 拥有"任何进入或修改 Canonical State"的转换的控制面。

**存储位置:** `promotion_policy.py`、`relation_promotion.py`、`commands/promote*`、audit JSONL log、审阅队列、projection-lifecycle marker。

**子轴:** Policy · Promotion · Review · Verification · Routing · Repair · Audit。每个子轴是独立的 concern;不要把它们打包成"Governance 这一层"。

**能持有知识吗?** 不能。Governance 配置规则、运行 gate、发出审计事件。被 gate 的知识仍存放在 Candidate / Canonical State。

**失败模式:** 静默 promotion(无审计)、缺审阅队列、没有重建记录的 repair、模糊的路由。

**如何修复:** 拿审计日志重放 Canonical State;对账审阅队列;确保每次 promotion 都有审计事件。

**测试:** 在 Canonical State 里随便挑一行 → 审计日志要能回答 *谁提升了它、何时、来自哪个 Candidate、依据哪条证据*。如果答不出来,Governance 就有漏洞。

---

## 迁移说明

旧版 OVP 文档和代码使用以下旧词;新架构词汇取代它们。

| 旧词 | 新词 |
| --- | --- |
| Authority(架构含义) | Canonical State | <!-- lint-allow: migration table -->
| Layer 1 / Layer 1 Canonical Knowledge | Canonical State | <!-- lint-allow: migration table -->
| Layer 2 / Derived Indexes / Derived state | Projections | <!-- lint-allow: migration table -->
| Layer 3 / Context Assembly / Access | Access Surfaces | <!-- lint-allow: migration table -->
| Layer 4 | Governance Control Plane(子轴显式命名) | <!-- lint-allow: migration table -->
| 运行阶段 `Canonical` | 运行阶段 `Normalize`(见 [RUNTIME](./RUNTIME.md)) |
| `source_authority` 表(代码) | 暂保留;迁移到 `source_credibility_score` 是低优先级。**`source_authority` 衡量的是"信源可信度",和 Canonical State 无关。** |

`Authority` 在新架构里不再代表"状态";`source_authority` 表保留原意("这个信源有多可信"),不指代架构的真相边界。

---

## 不在本文件里的内容

- **运行阶段**(Ingest / Interpret / Absorb / Refine / Normalize / Derive)— 见 [RUNTIME](./RUNTIME.md)。
- **Pack 模型**(Core / Domain Pack / Workflow Profile,pack 发现,schema)— 见 [PACKS](./PACKS.md)。
- **UI / MCP / CLI Surface 细节** — 见 [PRODUCT_SURFACES](./PRODUCT_SURFACES.md)。
- **来自旧文档的其它词汇**(Capture/Compile/Reuse、KSR、Crystal、Atlas、Briefing、Working Memory、Context Pack、Runtime State、Synthesis Layer)— 见 [GLOSSARY](./GLOSSARY.md)。它们都是上面六个核心词的某种 *kind*,不是并列的架构概念。

## 怎样新增一个词

只有当你能填出"机械模板"里全部七项时,新词才属于本文件。如果你答不出:

- **它存在哪里?**
- **谁产生它?**
- **能删除吗?**
- **能定义真相吗?**
- **失败模式是什么?**
- **怎么修复?**
- **什么测试守住边界?**

…那它不是架构词。它属于 `GLOSSARY.md`、`PRODUCT_SURFACES.md` 或 `RUNTIME.md`。首页词汇预算锁死在六个。
