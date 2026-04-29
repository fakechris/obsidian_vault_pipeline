# OVP Architecture

> 语言： [English](ARCHITECTURE.md) | 简体中文

**状态：** Review draft v2
**更新时间：** 2026-04-29

这份文档的目的不是再发明一套新分层，而是把 OVP 已经出现过的几套说法放回各自的位置，避免产品叙事、运行流程、代码所有权、知识状态语义、存储可信边界混在一起。

核心规则：

> OVP 只有一个主架构：四层持久架构。
> 其它说法都是不同截面，不是并列架构。

## 0. 总览

OVP 现在使用多套词汇，但它们解释的是不同问题。

| 说法 | 解释什么 | 后续定位 |
| --- | --- | --- |
| Capture -> Compile -> Reuse | 产品叙事：用户为什么需要 OVP | 产品截面 |
| Ingest -> Interpret -> Absorb -> Refine -> Canonical -> Derived | 执行流程：pipeline 怎么跑 | 执行截面 |
| Core Platform / Domain Pack / Workflow Profile | 所有权：core、pack、profile 各负责什么 | 所有权截面 |
| Canonical Knowledge / Derived Indexes / Context Assembly / Governance | 持久架构：真相、投影、访问、治理分别在哪里 | 主架构 |
| KSR: source -> observation -> claim -> evidence -> validity -> projection -> permission | 长期知识状态怎么表达 | 语义词汇表 |
| Authority / Derived state / Projection lifecycle | 存储可信边界和投影修复机制 | 存储/控制截面 |

统一口径：

```text
四层模型管架构。
六层 pipeline 管执行。
Core/Pack/Profile 管所有权。
KSR 管知识状态语言。
Capture/Compile/Reuse 管产品叙事。
Authority/Derived/Projection lifecycle 管存储可信和修复。
```

总映射表：

| 主架构层 | 产品叙事 | 六层执行 | 所有权模型 | KSR 语义 | 存储可信 |
| --- | --- | --- | --- | --- | --- |
| Layer 1 Canonical Knowledge | Capture / Compile | Interpret / Absorb / Refine / Canonical | Core + Pack semantics | source / observation / claim / evidence / validity | Authority |
| Layer 2 Derived Indexes / Views | Reuse substrate | Derived | Core projection infra + pack projection semantics | projection | Derived state |
| Layer 3 Context Assembly / Access | Reuse surface | Derived output / access commands | Core shell + pack view recipes | projection | Access projection |
| Layer 4 Governance / Control Plane | Compile gate / Reuse feedback | Absorb / Refine / Canonical / Derived controls | Core governance primitives + pack policies + profile routing | validity / permission | Projection lifecycle + audit |

## 1. 主架构：四层持久架构

所有关于“状态归属、可信边界、投影、访问、治理”的长期架构决策，都应该落到这四层里。

```text
Layer 1: Canonical Knowledge
  OVP 长期相信和维护的知识状态。

Layer 2: Derived Indexes / Views
  从 Layer 1 派生出来的可重建索引、图谱、查询和检查视图。

Layer 3: Context Assembly / Access
  把持久知识组装成 reader、operator、query、export、briefing、agent context 等可用界面。

Layer 4: Governance / Control Plane
  横向控制 promotion、review、verification、routing、repair、audit 和 workflow 边界。
```

Layer 4 不是 Layer 1 的上游，也不是 pipeline 的最后一步。它是 cross-cutting control plane。

```text
+--------------------------------------------------------------------+
| Layer 4: Governance / Control Plane                                |
|                                                                    |
|  Policy  Promotion  Review  Verification  Dispatch  Repair  Audit  |
|                                                                    |
|  +------------------+     +------------------+     +-------------+ |
|  | Layer 1          | --> | Layer 2          | --> | Layer 3     | |
|  | Canonical        |     | Derived          |     | Access /    | |
|  | Knowledge        |     | Indexes / Views  |     | Context     | |
|  +------------------+     +------------------+     +-------------+ |
|                                                                    |
+--------------------------------------------------------------------+
```

### Layer 4 子轴

Layer 4 不能变成“什么都往里塞”的 junk drawer。它内部至少拆成七个子轴：

| 子轴 | 负责什么 | 例子 |
| --- | --- | --- |
| Policy | 什么可以被写入、promote、自动处理 | promotion rules, who-can-write, high-risk gate |
| Promotion | Policy + Review 的交集，把 candidate / derived proposal 变成 accepted state | `promote_candidates.py`, `promotion_policy.py`, `promotion_audit.py`, `relation_promotion.py`, `workspace_promotion.py` |
| Review | 人工审阅和队列生命周期 | candidate review, contradiction review, stale summary review |
| Verification | evidence、hash、freshness、replay 的验证 | evidence status, content hash check, review state replay |
| Routing / Dispatch | workflow 走向、任务派发和歧义分流 | workflow profile routing, ambiguity dispatch, source routing preview |
| Repair | projection lifecycle 和 rebuild 控制 | metadata repair marker, full rebuild marker, semantic reindex marker |
| Audit | 责任链和不可变事件记录 | audit JSONL, promotion event, review event |

Promotion 不是新 layer。它是 Policy 判断候选是否可进入 canonical、Review/Audit 记录接受过程的门禁动作。

后续说“Layer 4 控制某件事”时，应尽量指出属于哪个子轴。文档里避免把 Layer 4 routing 叫 Resolver；Resolver 这个词保留给 identity / concept resolver 语义，例如 `concept_resolver.py`。

## 2. Layer 1: Canonical Knowledge

Layer 1 是 OVP 的长期可信边界。

它回答这些问题：

- 哪些知识对象真实存在？
- 这些对象的稳定 identity 是什么？
- 哪些 factual claim 被接受？
- 每个 factual claim 的 evidence 在哪里？
- 能不能追溯到原始 source 或明确用户归因？
- 哪些写入经过 review、promotion、verification？
- Derived state 丢了以后，能不能从这里重新推导？

当前 OVP 的 Authority 是：

- vault Markdown
- concept registry / alias registry
- source note / deep dive / evergreen note
- evidence quote / locator / content hash
- audit JSONL / promotion event / verification event
- review 之后的 accepted state

Layer 1 不是“数据库里的一切”。OVP 的 Layer 1 应该是 file-native、evidence-backed、user-owned 的。

明确边界：

- `knowledge.db` 不是 Layer 1。
- `truth_store.py` 不是 Layer 1。
- `truth_store.py` 定义的是 projection schema，用来把 Layer 1 投影成可查询 rows。
- candidate queue / review queue 不是 Layer 1，除非经过 review/promotion。
- LLM 输出不是 Layer 1，除非经过明确的吸收、审阅或 promotion 路径。

Layer 1 可以慢，但必须：

- 可读
- 可 diff
- 可备份
- 可迁移
- 可审计
- 可 replay

## 3. Layer 2: Derived Indexes / Views

Layer 2 是从 Layer 1 计算出来的可重建状态。

它回答这些问题：

- UI 怎么快速列出 objects？
- query 怎么快速搜？
- graph 怎么快速画？
- contradiction 怎么快速查？
- briefing / dashboard / context pack 怎么快速组装？
- MCP / prompt assembler 应该读哪个本地运行时 store？

当前 OVP 的 Derived state 主要是：

- `knowledge.db`
- truth projection rows:
  - `objects`
  - `claims`
  - `claim_evidence`
  - `relations`
  - `contradictions`
- graph projection rows:
  - `graph_edges`
  - `graph_clusters`
- access/query rows:
  - `compiled_summaries`
  - `reuse_events`
  - search/query payload
- generated views:
  - Atlas
  - MOC
  - graph views
  - lint outputs
  - daily delta

Layer 2 可以：

- 做索引
- 做聚合
- 做缓存
- 做 denormalization
- 为 UI/query/graph 优化 schema
- 被删除后重建

Layer 2 不能：

- 自己决定什么是真相
- 反向覆盖 Layer 1
- 把 semantic search result 直接升级成 canonical identity
- 把 projection row 当成 source of truth

命名纪律：

- 说 `truth projection`，不要说 `truth source`。
- 说 `graph projection`，不要说 `canonical graph truth`。
- 说 `search projection`，不要说 `semantic truth`。
- 说 `knowledge.db derived store`，不要说 `knowledge.db authority`。

## 4. Layer 3: Context Assembly / Access

Layer 3 把持久知识变成用户、agent、operator 能使用的界面和上下文。

它回答这些问题：

- 用户打开产品时应该先看到什么？
- reader page 怎么组织？
- object page 显示哪些 claim/evidence/relation？
- graph page 是空间地图还是 debug report？
- search result 怎么解释来源？
- briefing/context pack/prompt 怎么组装？
- operator 应该在哪里 review 和维护？

当前 OVP 的 Layer 3 surface 包括（非穷举，按用户/agent 可触达面列出）：

- `ovp-ui`
- reader atlas / future reader home
- object pages
- graph page
- `ovp-query`
- `ovp-export`
- `ovp-truth`
- `ovp-mcp` / MCP read tools
- `ovp-build-crystals`
- `ovp-working-memory`
- `ovp-link-suggest`
- briefing
- signals/actions
- context packs
- prompt assembly

Layer 3 通常为了速度读取 Layer 2，但必须能追溯到 Layer 1。

明确边界：

- search result 是 access，不是 authority。
- briefing 是 access，不是 authority。
- reader page 是 projection，不是独立真相源。
- context pack 是 projection，不是 agent memory authority。
- dashboard 是 projection，不是 workflow truth 本身。
- Layer 3 不能把展示内容静默 promotion 到 Layer 1。

LearnBuffett 给 OVP 的启发属于 Layer 3：

- readable object pages
- backlink/mention rail
- spatial graph map
- reader-first home
- operator surfaces 放到 `/ops`

这不是改变 Layer 1 的 truth model，而是改变 Layer 3 的产品形态。

## 5. Layer 4: Governance / Control Plane

Layer 4 是 OVP 的控制面。

它回答这些问题：

- candidate 能不能进 canonical？
- factual claim 是否需要 review？
- evidence 是否 stale / broken / verified？
- source 应该走哪条 workflow？
- ambiguity 应该派发到哪条处理路径？
- 哪些 agent/user 可以写哪些状态？
- projection 是 stale、repairable，还是必须 rebuild？
- workflow item 能否 claim、lease、retry、close、supersede？

当前 OVP 的 Layer 4 组件包括：

- promotion policies
- review queues
- contradiction review
- stale-summary review
- evidence verification / replay
- relation promotion replay
- action queue
- focused action handlers
- signals/actions
- doctor checks
- pack contracts
- workflow/profile routing

明确边界：

- governance rule 应该显式、可测试。
- agent output 不能静默变成 accepted truth。
- review state 必须能跨 derived rebuild 保留。
- projection repair 和普通 access 要分开治理。
- resolver / routing / permission 不能散落在 prompt 里。
- audit ledger 与 feed UI state 是两类状态，不能混用。

## 6. 截面 1: 产品叙事

产品叙事是：

```text
Capture -> Compile -> Reuse
```

它用来解释 OVP 给用户带来的价值。

| Product verb | 含义 | 主要对应层 |
| --- | --- | --- |
| Capture | 接收文章、clipping、paper、repo、网页、笔记，并保留 source lifecycle | Layer 1 input + Layer 4 Routing |
| Compile | 把资料编译成 candidate、object、claim、evidence、relation | Layer 1 + Layer 4 Policy/Review |
| Reuse | 把已编译知识用于 reader page、graph、search、briefing、prompt、context pack | Layer 2 + Layer 3 |

适用场景：

- README
- 产品定位
- roadmap narrative
- 给用户解释 OVP 是什么

不适用场景：

- 代码所有权
- 数据可信边界
- pipeline stage 命名

重要说明：

Capture/Compile/Reuse 主要是产品叙事，但它有两个明确架构钩子：

- Compile 对应 Layer 4 Policy / Review 的 promotion gate。
- Reuse 对应 Layer 3 access surface 和 context assembly 选择。

写代码时不应依赖这三个 verb 做模块边界，但产品话术和 roadmap 会使用它们。

## 7. 截面 2: 运行流程

执行流程是：

```text
Ingest -> Interpret -> Absorb -> Refine -> Canonical -> Derived
```

它用来解释 pipeline 怎么跑。

| Runtime stage | 职责 | 主要接触层 |
| --- | --- | --- |
| Ingest | 采集并规范化输入 | Layer 1 input + Layer 4 Routing |
| Interpret | 生成 deep dive 和结构化中间材料 | Layer 1 candidate input |
| Absorb | 把解释材料纳入知识生命周期 | Layer 1 + Layer 4 Policy/Review |
| Refine | cleanup / breakdown / normalize 现有知识 | Layer 1 + Layer 4 Policy/Verification |
| Canonical | 维护 registry / alias / Atlas / MOC consistency | Layer 1 + Layer 4 Verification |
| Derived | 生成 query / graph / lint / UI / access projections | Layer 2 + Layer 3 + Layer 4 Repair |

适用场景：

- command docs
- processor contracts
- stage handlers
- workflow execution
- `ovp --full` / `ovp --incremental`

不适用场景：

- 持久状态归属
- source of truth 判断

重要说明：

`Canonical` runtime stage 不等于整个 Layer 1。它只是维护 Layer 1 的一个执行阶段。

`Derived` runtime stage 不等于所有 Layer 2/3。它只是刷新 projection 的执行阶段。

## 8. 截面 3: 所有权模型

所有权模型是：

```text
Core Platform / Domain Pack / Workflow Profile
```

它用来决定代码、语义、运行路径分别放在哪里。

| Ownership unit | 负责 | 不负责 |
| --- | --- | --- |
| Core Platform | runtime framework、identity helpers、audit、projection infrastructure、UI shell、command shell、registries | domain-specific object meaning |
| Domain Pack | object kinds、relation vocabulary、extraction semantics、promotion policy、view recipes | global execution engine |
| Workflow Profile | 哪些 stage 跑、顺序是什么、使用哪个 pack/profile setting | 新领域语义 |

适用场景：

- 新能力应该进 core 还是 pack
- pack manifest 怎么扩展
- workflow profile 是否应该变复杂
- domain semantics 是否应该硬编码

不适用场景：

- 判断哪个数据是 authority
- 判断 projection 是否可丢

关键边界：

- pack 可以定义语义。
- pack 不能绕过 audit。
- pack 不能把 semantic retrieval 变成 canonical identity。
- profile 只能选择运行路径，不能承担所有 domain semantics。

## 9. 截面 4: KSR 语义词汇表

KSR 词汇是：

```text
source -> observation -> claim -> evidence -> validity -> projection -> permission
```

它用来描述长期知识状态。

| KSR term | 含义 | 主要对应层 |
| --- | --- | --- |
| source | 原始材料或捕获输入 | Layer 1 input |
| observation | 从 source 中观察/抽取出的事实片段 | Layer 1 candidate |
| claim | 关于对象的结构化陈述 | Layer 1 |
| evidence | quote、locator、hash、source context、user attribution、derived chain | Layer 1 |
| validity | review status、freshness、conflict、confidence | Layer 1 + Layer 4 |
| projection | 物化视图或访问产物 | Layer 2 + Layer 3 |
| permission | 谁能读、写、promote、route | Layer 4 |

当前实现说明：`evidence.py` 已经是一等模块；`claim` / `validity` 目前主要以 `truth_store.py` / `truth_api.py` 的 projection row 和 schema 形态存在。Layer 1 的 claim 表达仍由 markdown + registry + audit JSONL 承载；未来可以把 claim / validity 提升为更显式的 artifact contract。

适用场景：

- schema 设计
- evidence span
- claim lifecycle
- permission layer
- review policy
- backlog task 命名

不适用场景：

- 系统主架构分层
- command execution stage
- core/pack/profile ownership

KSR 是知识状态语言，不是另一套系统架构。

## 10. 截面 5: 存储可信和投影生命周期

存储可信截面是：

```text
Authority
Derived state
Projection lifecycle
```

它是四层架构里关于“谁可信、谁可丢、坏了怎么修”的窄切面。

| Storage/control term | 对应 | 含义 |
| --- | --- | --- |
| Authority | Layer 1 + Layer 4 的 audit/review 部分 | durable truth boundary |
| Derived state | Layer 2 + Layer 3 的 access caches | rebuildable projections |
| Projection lifecycle | Layer 4 Repair 管理 Layer 2/3 | repair / rebuild / reindex / hot-path safety |

### Authority

OVP 的 Authority 是：

```text
vault markdown + registry + evidence/audit JSONL
```

它是长期可信、用户可读、可迁移、可审计的状态。

### Derived State

OVP 的 Derived state 是：

```text
knowledge.db truth/search/graph/access projections
```

它用于快速查询、展示、组装和检查。

### Projection Lifecycle

Projection lifecycle 负责判断投影坏了以后怎么修。

Lifecycle kinds 现在就预留，不锁具体 backend：

```text
metadata_only
  只修 derived metadata，不跑 LLM，不全量 rebuild

full_rebuild
  从 Authority 重建 knowledge.db projection

semantic_reindex
  重算昂贵语义索引。当前可以 optional / not implemented，但 schema 必须预留。
```

Marker 应该是结构化对象，而不是一个裸文件名。

```python
class ProjectionRepairMarker:
    kind: Literal["metadata_only", "full_rebuild", "semantic_reindex"]
    scope: ProjectionScope
    reason: str
    created_at: datetime
    caused_by: str

    authority_schema_version: int
    projection_schema_version: int

    superseded_by: Optional[str]
    claimed_by: Optional[str]
    claim_lease_until: Optional[datetime]


class ProjectionScope:
    pack: Optional[str]
    profile: Optional[str]
    projection_kind: Optional[str]
    source_ids: list[str]
    object_ids: list[str]
    space_id: Optional[str]
```

Operational rules:

- 后写入的更宽 marker 可以 supersede 更窄 marker。
- `full_rebuild` 可以 supersede `metadata_only`。
- `semantic_reindex` 不应隐式 supersede `full_rebuild`，除非 resolver 明确判定两者 scope 等价。
- `claimed_by` 和 `claim_lease_until` 防止两个 worker 同时处理同一个 marker。
- 如果 marker 的 `authority_schema_version` 小于当前 Authority schema version，resolver 必须提升到 `full_rebuild`。
- 如果 projection backend 支持 partial rebuild，scope 必须被用于限制 rebuild 范围。

当前 OVP 对 “derived 可重建” 已经有原则和习惯，但 marker/control plane 还不够显式。

## 11. Canonical Scenarios

抽象分层必须能落到真实路径。下面的 scenarios 是架构咬合面的参考。

### 场景 A: 用户剪藏一篇文章

```text
1. Capture handler 接收文章或 clipping
   - 产品截面: Capture
   - 运行截面: Ingest
   - 架构层: Layer 1 input
   - Layer 4: Routing 决定进入哪条 workflow

2. Ingest stage normalize 文件和 source metadata
   - 写入 source note / raw material
   - 不直接产生 accepted factual claim

3. Interpret 生成 deep dive 或 structured observation
   - 架构层: Layer 1 candidate
   - KSR: observation

4. Layer 4 Policy / Review 判断哪些内容可进入 accepted state
   - promotion gate
   - evidence completeness check
   - high-risk gate if applicable

5. Accepted artifact 落地到 vault markdown / registry / audit event
   - 架构层: Layer 1 Canonical Knowledge
   - KSR: claim / evidence / validity

6. Derived stage 刷新 knowledge.db
   - 架构层: Layer 2 Derived Indexes / Views
   - 产生 objects / claims / evidence / relations / graph rows

7. Reader/UI/search/graph 展示新内容
   - 架构层: Layer 3 Context Assembly / Access
   - 展示内容必须能回溯到 Layer 1 evidence
```

### 场景 B: `knowledge.db` 被删除或损坏

```text
1. doctor / startup check 发现 derived store 缺失或不可用
   - 架构层: Layer 4 Verification

2. repair controller 写入 ProjectionRepairMarker(kind="full_rebuild")
   - 架构层: Layer 4 Repair
   - reason 记录具体原因
   - caused_by 记录 doctor_check / startup_check / user_override

3. rebuild worker claim marker
   - claimed_by + claim_lease_until 防止重复处理

4. Derived stage 从 Authority 重建 knowledge.db
   - 架构层: Layer 2
   - 不重新决定 truth

5. review/audit state replay
   - audit ledger 是 Layer 1/4
   - review_status / verification status 必须 replay 到 Layer 2 projection

6. rebuild 成功后清理或 supersede marker
   - 架构层: Layer 4 Repair / Audit

7. Layer 3 surfaces 恢复读取
   - UI/search/graph 不应在用户请求路径里偷偷触发重活
```

### 场景 C: Query 或 agent 发现一个新关系

```text
1. Query/agent 从 Layer 3 access surface 读到上下文
   - search result / context pack 不是 authority

2. agent 生成 proposed relation
   - 架构层: Layer 1 candidate
   - KSR: observation / candidate claim

3. proposal 写入 review queue
   - 架构层: Layer 4 Review
   - 不允许直接写 accepted relation

4. reviewer 或 policy 决定 accept / reject / request evidence
   - 架构层: Layer 4 Policy + Review

5. accept 后写入 Layer 1 authority + audit event
   - factual relation 必须有 evidence

6. Derived stage materialize relation / graph edge
   - 架构层: Layer 2

7. graph/object page 显示新关系
   - 架构层: Layer 3
```

## 12. Architectural Invariants

这些 invariants 是架构契约。违反即 bug，除非 PR 明确说明例外和替代约束。

| ID | Invariant | 可机械检查方向 |
| --- | --- | --- |
| I-1 | 任何 accepted factual claim 必须有可解析 evidence。结构性状态不适用本条。 | evidence completeness check |
| I-2 | Layer 2 删除后，必须能从 Layer 1 + projection schema 确定性重建。 | rebuild test |
| I-3a | Audit ledger 是 append-only；任何状态变化必须 emit 新 event。 | append-only audit check |
| I-3b | Feed UI 上的 resolved/retry/dismissed patch 必须对应 audit ledger append event。 | feed projection check |
| I-3c | 删除 Feed UI projection 后，必须能从 audit ledger 重建当前 feed state。 | feed replay test |
| I-4 | Layer 3 对 Layer 1 状态的写入，必须经过 Layer 4 governance API。 | code review + runtime audit |
| I-4b | Layer 3 模块不应直接 import Layer 1 mutation symbols；只能 import read-only views。 | import lint |
| I-5 | review_status / verification status 在 derived rebuild 前后必须保持。 | replay/rebuild test |
| I-6 | 命名纪律必须被 lint 或 review checklist 强制执行。 | naming lint |
| I-7 | Projection repair 不能隐式触发 LLM、raw source scan、semantic reindex。 | hot-path test |
| I-8 | semantic search result 不能自动成为 canonical identity。 | promotion boundary test |

### I-1: Factual Evidence

Accepted factual claim 指“关于 OVP 之外世界的断言”。它必须至少有一个 evidence_kind，且值为以下之一：

- `source_quote`
  - vault 文件指针
  - offset 或 locator
  - content_hash
- `user_attribution`
  - user_id
  - signed audit record
  - 表示用户手工录入本身就是 evidence
- `derived_chain`
  - 引用其它 claim_id
  - 链条最终必须落到 `source_quote` 或 `user_attribution`

结构性状态不属于 factual claim，豁免本条：

- registry alias
- routing decision
- workflow status
- pack contract decision
- projection marker
- review queue status

## 13. Architectural Fitness Functions

每条重要 invariant 都应该能变成 CI、doctor、pre-commit 或 rebuild test。

| Invariant | Check | 建议实现位置 |
| --- | --- | --- |
| I-1 | `verify_evidence_complete.py` | `ovp doctor` |
| I-2 | `verify_projection_rebuild.py` | rebuild integration test |
| I-3a | `verify_audit_jsonl_append_only.py` | pre-commit / CI |
| I-3b | `verify_feed_patch_has_audit_event.py` | `ovp doctor` |
| I-3c | `verify_feed_replay.py` | rebuild test |
| I-4 | runtime write audit: Layer 3 write must call governance API | runtime audit / tests |
| I-4b | `import_linter.toml` or ruff rule for forbidden mutation imports | CI lint |
| I-5 | `verify_review_state_replay.py` | rebuild test |
| I-6 | `naming_lint.py` scanning banned architecture phrases | CI lint |
| I-7 | `verify_hot_path_no_heavy_work.py` | UI/search test |
| I-8 | `verify_semantic_search_no_promotion.py` | promotion boundary test |

Fitness functions should be introduced incrementally. The architecture doc defines the target; implementation can land in separate PRs.

## 14. Schema Versioning And Projection Compatibility

Layer 1 schema and Layer 2 projection schema will evolve. This must be explicit.

Suggested rules:

- Authority schema version should be recorded near the vault root, for example `.ovp/schema_version`.
- Projection schema version should be recorded inside `knowledge.db` metadata.
- Every derived projection should record which Authority schema version and projection schema version it was built from.
- Startup/doctor checks compare current versions against projection versions.
- If current Authority schema version is newer than the marker or projection version, write or promote to `ProjectionRepairMarker(kind="full_rebuild")`.
- Schema migrations should be monotonic and explicit.
- Projection rebuild must not erase Layer 1/4 review or audit state.

Version compatibility connects schema migration with projection lifecycle:

```text
Authority schema changed
  -> projection compatibility check
  -> full_rebuild marker if needed
  -> derived rebuild
  -> audit/review replay
  -> marker resolved
```

Do not rely on ad hoc "just rerun the pipeline" behavior for schema changes.

## 15. 命名纪律

为了避免继续混乱，后续文档和代码注释应遵守这些命名规则。

1. 不要把 `knowledge.db` 叫 source of truth。
   - 正确：`knowledge.db` 是 derived store。
   - 正确：`knowledge.db` 包含 truth projections。

2. 不要把 `truth_store.py` 叫 Authority。
   - 正确：`truth_store.py` 定义 projection schema。

3. 不要把 semantic search result 叫 canonical identity。
   - 正确：semantic search 是 access/search projection。

4. 不要把 reader page / dashboard / briefing 叫 truth。
   - 正确：它们是 Layer 3 projections。

5. 不要用 Capture/Compile/Reuse 判断代码归属。
   - 正确：代码归属用 Core/Pack/Profile。

6. 不要用六层 runtime model 判断持久状态所有权。
   - 正确：状态所有权用四层主架构。

7. 不要把 KSR task list 当完整架构。
   - 正确：KSR 是知识状态词汇和 backlog 语义输入。

8. 不要让 derived rebuild 擦掉 review/audit 决策。
   - 正确：review/audit state 属于 Layer 1/4，应 replay 到 Layer 2。

9. 不要让 projection repair 隐式触发昂贵流程。
   - 正确：lightweight repair、full rebuild、semantic reindex 必须分开。

## 16. 当前现状

已经比较强的部分：

- 六层 execution pipeline 已经存在并能跑。
- Core/Pack/Profile 已经是真实 ownership model。
- `research-tech` 已经是 reference pack。
- `knowledge.db` 已经承载较多 derived projections。
- truth API、object browsing、signals/actions、contradictions、graph、query、UI 已经存在。
- audit/replay 概念已经在 evidence、relation promotion 等路径里出现。

仍然太隐式的部分：

- canonical artifact contract 还不够一等公民化。
- Layer 4 子轴还没有完全反映到代码结构。
- projection labels 没有贯穿 dashboard、MOC、wiki、briefing、graph、reader page、context pack。
- projection lifecycle marker 还没有明确区分 metadata_only / full_rebuild / semantic_reindex。
- dashboard/search hot path 还需要显式保证不触发重 raw scan / LLM / embedding。
- context assembly recipes 还需要收束。
- governance / resolver contracts 还需要显式化。
- schema versioning 和 projection compatibility 还需要工程化。
- fitness functions 还没有落地到 CI/doctor/pre-commit。

## 17. 近期架构动作

不要再新增一套架构模型。近期应该做的是把现有四层模型工程化。

优先顺序：

1. 明确并文档化 Layer 4 子轴：Policy / Review / Verification / Routing / Repair / Audit。
2. 给关键路径补 canonical scenarios，并以 scenarios 推导 invariants。
3. 建立最小 architectural fitness functions。
4. 明确哪些文件、registry、audit event 是 Authority。
5. 给 `knowledge.db` 内部 projection 分类。
6. 给 dashboard、MOC、wiki、briefing、graph、reader page、context pack 加 projection label。
7. 引入结构化 ProjectionRepairMarker。
8. 给 dashboard/search 加 hot-path eval。
9. 把 routing、promotion、review、permission 从 prompt/散落代码中收束为显式 governance/dispatch contract。
10. 预留 semantic_reindex lifecycle kind，但不提前锁定 LanceDB 或其它 backend。

## Appendix: Backlog Mapping

这份文档本身不应依赖 backlog ID 才成立。下面只是当前实现映射，后续可迁到 `BACKLOG.md` 或单独 open-items 文档。

| 架构工作 | 当前 backlog/task 对应 |
| --- | --- |
| Projection marking | `BL-002`, `KSR-002` |
| Dashboard/search hot-path audit | `BL-003`, `KSR-015` |
| Workflow wiring eval suite | `BL-004`, `KSR-026` |
| Evidence span / factual evidence completeness | `BL-006`, `KSR-001`, `KSR-018` |
| Candidate risk layering | `BL-007`, `KSR-003` |
| Reader-first access surfaces | `BL-001`, `BL-008`, `BL-009`, `BL-010` |
| Projection repair lifecycle | `BL-020` |
| Schema versioning and migration trigger | `BL-021` |

## Bottom Line

以后解释 OVP 架构时，统一用这句话：

```text
四层模型是主架构。
其它都是截面。
```

完整版本：

```text
Four-layer architecture controls state ownership.
Six-layer pipeline controls execution.
Core/Pack/Profile controls code and semantic ownership.
KSR controls knowledge-state vocabulary.
Capture/Compile/Reuse controls product narrative.
Authority/Derived/Projection lifecycle controls storage trust and repair.
```

这能保留已有概念的价值，同时避免它们互相抢“主架构”的位置。
