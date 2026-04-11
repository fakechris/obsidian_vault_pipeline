# OVP Knowledge System Execution Plan

## Goal

把 `/Users/chris/Downloads/ovp_knowledge_system_docs` 里的架构分析，收敛成当前 `openclaw-template` 可执行的大项目实施稿。

目标不是重写仓库，而是把现有的 `pack/profile/extraction/knowledge.db/review` 半成品补成强运行时。

核心判断：

- 外部文档的方向是对的。
- 当前 repo 已经比外部文档假设的状态更靠前。
- 真正缺的不是更多概念，而是三件事：
  - extraction 结果可见
  - truth layer 成为一等运行时
  - review/materialize/query 围绕 typed truth 闭环

## Repo Snapshot

当前 repo 已经具备的骨架：

- pack/plugin 基础设施：
  - `src/openclaw_pipeline/packs/base.py`
  - `src/openclaw_pipeline/packs/default_knowledge/pack.py`
- extraction 基础类型与运行时：
  - `src/openclaw_pipeline/extraction/specs.py`
  - `src/openclaw_pipeline/extraction/results.py`
  - `src/openclaw_pipeline/extraction/runtime.py`
  - `src/openclaw_pipeline/extraction/artifacts.py`
- object projection 的最小兼容层：
  - `src/openclaw_pipeline/object_registry.py`
- existing derived/query surface：
  - `src/openclaw_pipeline/knowledge_index.py`
  - `src/openclaw_pipeline/discovery.py`
  - `src/openclaw_pipeline/evidence.py`
- pack-aware operation / wiki view 早期版本：
  - `src/openclaw_pipeline/packs/default_knowledge/operation_profiles.py`
  - `src/openclaw_pipeline/packs/default_knowledge/wiki_views.py`

当前明确还不够的部分：

- `src/openclaw_pipeline/commands/extract_profiles.py`
  - 仍然是 `NoopExtractor`，没有真正的 extractor runtime 接口落地。
- 缺少 `truth_store.py`
  - `knowledge.db` 仍然是 index-first，不是 truth-first。
- 缺少 materializer 层
  - object page / topic view / event dossier 还没有真正的构建器。
- 缺少 review queue runtime
  - 目前只有零散结果文件和命令，不是持续性的队列系统。
- `object_registry.py`
  - 当前仍是 `concept_registry` 的 projection，不是真正的 typed object authority。

## What To Keep

以下模块保留并向前演化，不重写：

- six-layer pipeline 总体结构
- vault markdown 作为 canonical 界面
- `concept_registry.py` 的保守 identity 规则
- `knowledge.db` 作为 derived-first 本地数据库
- pack/profile/plugin 机制
- candidate lifecycle
- evidence/discovery 的分层思想

这意味着：

- 不做 big-bang migration
- 不把 SQLite 立刻升成 canonical truth
- 不把 semantic retrieval 引入 automatic identity merge
- 不先做重 UI web app

## What To Change

以下模块需要明确升级，不再停留在“有文件但不成系统”的状态：

### 1. Extraction Runtime

目标：

- profile 不再只是 metadata
- extractor 不再是 `NoopExtractor`
- extraction result 有 preview、dashboard、validator
- derived artifacts 能被 review 和 truth merge 消费

主要改动：

- `src/openclaw_pipeline/commands/extract_profiles.py`
  - 替换 `NoopExtractor`
  - 接入真实 extractor interface
- 新增：
  - `src/openclaw_pipeline/extraction/llm_extractor.py`
  - `src/openclaw_pipeline/extraction/prompt_builder.py`
  - `src/openclaw_pipeline/extraction/validator.py`
- 扩展：
  - `src/openclaw_pipeline/extraction/runtime.py`
  - `src/openclaw_pipeline/extraction/results.py`
  - `src/openclaw_pipeline/extraction/artifacts.py`
- 新增命令：
  - `src/openclaw_pipeline/commands/extract_preview.py`
  - `src/openclaw_pipeline/commands/extraction_dashboard.py`

### 2. Truth Layer

目标：

- `knowledge.db` 从“检索索引层”升级为“truth-aware derived store”
- claim / relation / evidence / contradiction / compiled summary 成为一等对象

新增核心模块：

- `src/openclaw_pipeline/truth_store.py`

第一版 truth tables：

- `objects`
- `claims`
- `claim_evidence`
- `relations`
- `event_ledger`
- `compiled_summaries`
- `contradictions`

边界：

- truth store 仍然是 derived-first
- canonical authority 仍然是 vault + registry
- truth store 只能支持 merge/review/materialize/query，不隐式改 canonical

### 3. Materializers

目标：

- typed truth 能落成人能读的页面，而不是只停在 JSON/SQLite

新增模块：

- `src/openclaw_pipeline/materializers/object_page.py`
- `src/openclaw_pipeline/materializers/topic_view.py`
- `src/openclaw_pipeline/materializers/event_dossier.py`

并扩展：

- `src/openclaw_pipeline/packs/default_knowledge/wiki_views.py`
- `src/openclaw_pipeline/commands/build_views.py`

### 4. Review Runtime

目标：

- review 从“偶尔命令”变成“持续队列”

新增模块：

- `src/openclaw_pipeline/review_queue/items.py`
- `src/openclaw_pipeline/review_queue/runtime.py`
- `src/openclaw_pipeline/review_queue/materializers.py`
- `src/openclaw_pipeline/commands/review_queue.py`

第一波 queue types：

- `candidate_review`
- `contradiction_review`
- `stale_summary_review`
- `extraction_validation_review`

### 5. Object System

目标：

- 从 concept-centric 走向 typed object system
- 但不在第一阶段推翻 `concept_registry`

演化方式：

- 先扩 `object_registry.py`
- 让 `concept` 仍是第一个稳定 object kind
- 再逐步加入：
  - `paper`
  - `workflow_step`
  - `benchmark`
  - `claim`
  - `event`

明确不做的事：

- 不直接废弃 `concept_registry.py`
- 不一上来把所有历史 candidate/evergreen 迁成多对象系统

## Phase Plan

### Phase 1: Make Extraction Visible

目标：

- extraction 能跑
- 结果能看
- 结果能审

范围：

- `extract_profiles.py` 替换 `NoopExtractor`
- `llm_extractor.py`
- `prompt_builder.py`
- `validator.py`
- `extract_preview.py`
- `extraction_dashboard.py`

完成标准：

- 至少 4 个默认 extraction profiles 能真实跑出 derived artifacts
- 单文件 preview 可用
- dashboard 能展示最近 extraction runs
- extraction evidence 能进入 `evidence.py`

### Phase 2: Add Truth Store

目标：

- typed extraction 不再只停在 artifacts
- claim/relation/evidence 能进入 SQLite truth layer

范围：

- `truth_store.py`
- `knowledge_index.py` 扩展 truth-aware schema
- `auto_evergreen_extractor.py` / `promote_candidates.py` 接入 truth merge

完成标准：

- 至少支持 object/claim/relation/evidence/compiled summary 的写入和读取
- query 不再只返回 page/chunk，还能返回 typed claim/relation hits
- truth rebuild 不破坏既有 `knowledge.db` read surfaces

### Phase 3: Materialize + Review

目标：

- truth layer 能产出稳定页面
- review 能形成闭环

范围：

- object page
- topic view
- event dossier
- review queue runtime
- review queue materializers

完成标准：

- Obsidian 内能看到 extraction dashboard、candidate inbox、contradiction inbox
- 至少一个 compiled object page 能从 truth store 重建
- review queue item 可以被创建、列出、消费

### Phase 4: Split Domain Packs

目标：

- 先把我们当前技术研究向 pack 正式化
- 再为 media pack 留清晰接口

第一优先 pack：

- `research_tech`

第二优先 pack：

- `media_intel`

范围：

- 新 pack 目录
- object kinds
- extraction profiles
- wiki views
- operation profiles

完成标准：

- `default-knowledge` 不再承担所有领域语义
- `research_tech` 能稳定覆盖当前技术知识流
- `media_intel` 以 pack 形式接入，而不是污染 core

## Immediate File Map

第一批最值得动的文件：

- `src/openclaw_pipeline/commands/extract_profiles.py`
- `src/openclaw_pipeline/extraction/runtime.py`
- `src/openclaw_pipeline/extraction/results.py`
- `src/openclaw_pipeline/evidence.py`
- `src/openclaw_pipeline/knowledge_index.py`
- `src/openclaw_pipeline/object_registry.py`
- `src/openclaw_pipeline/packs/default_knowledge/extraction_profiles.py`
- `src/openclaw_pipeline/packs/default_knowledge/wiki_views.py`

第一批新增文件：

- `src/openclaw_pipeline/extraction/llm_extractor.py`
- `src/openclaw_pipeline/extraction/prompt_builder.py`
- `src/openclaw_pipeline/extraction/validator.py`
- `src/openclaw_pipeline/commands/extract_preview.py`
- `src/openclaw_pipeline/commands/extraction_dashboard.py`
- `src/openclaw_pipeline/truth_store.py`

## Risks

### Risk 1: Truth Store Too Early

如果 extraction visibility 没先做好，truth store 只会把脏结果结构化。

控制方式：

- 先做 Phase 1
- 没有 preview/dashboard/validator，不进入 Phase 2

### Risk 2: Object Model Overreach

如果过早把所有东西都变成 object kind，会把当前稳定的 concept identity 打散。

控制方式：

- `concept_registry` 继续保守
- `object_registry` 先做 projection-plus
- 新 object kinds 渐进加入

### Risk 3: UI Overbuilding

如果太早做 review console，会把项目拉去做 app，而不是做 knowledge runtime。

控制方式：

- 先 Obsidian/Markdown native
- 浏览器 review console 只做轻量 layer，不先做平台产品

## Definition Of Done

这个“大项目”完成，不以“多了几个模块”来判断，而以以下状态判断：

- extraction profile 不再是摆设，能稳定产生 typed derived artifacts
- derived artifacts 能进入 truth store
- truth store 能 materialize 成 object/topic/event 页面
- review queue 是可运行系统，不是零散日志
- query 能返回 object/claim/relation 级结果，而不是只返回 page/chunk
- `research_tech` 成为第一标准 pack
- `media_intel` 能作为独立 pack 接入，而不是要求 core 改写

## Recommended Next Move

下一步不应该直接做 media pack。

下一步应该是：

1. 执行 Phase 1
2. 把 extraction 结果做成可见、可审、可比较的 runtime
3. 在此基础上再进入 truth store

原因很直接：

- 现在最危险的不是“对象模型不够丰富”
- 而是“提取结果还没有成为稳定可观察系统”

所以这个大项目的第一刀，应该落在 extraction visibility，不是 UI，也不是 media。
