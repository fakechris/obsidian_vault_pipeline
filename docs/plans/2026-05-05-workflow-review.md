---
tags:
  - OVP
  - Absorb
  - Evergreen
  - Dedup
  - Entity-extraction
people:
  - chris
projects:
  - BL-058
  - BL-059
  - BL-060
  - BL-061
  - BL-062
tools:
  - Obsidian
  - SQLite
  - Pinboard
  - MinHash
---
# 2026-05-05 — Workflow First-Principles Review

> **目的**:把 OVP 增量管线的 13 个步骤(BASE + refine)逐个拷问一遍。每步必须答出"解决了什么 / 引入了什么 / 砍掉会坏什么 / 该留该砍该挪"。这份文档**只做 audit + decision,不动代码**。
>
> **触发**:今天讨论 BL-058(深度解读拆解)时发现"步骤是历史叠加的、没人重审过"。本次 review 把同样的拷问对所有步骤做一遍。
>
> **约束**:今天我们刚定下的核心原则 ——**质量永远高于速度,LLM 不许臆测、不许扩写源文没有的内容**(`feedback_no_slop.md`)。本 review 的所有 verdict 都以这条为锚。

---

## 0. 当前管线全景(从代码核实)

源:`unified_pipeline_enhanced.py:459-477` 的 `BASE_PIPELINE_STEPS` + `OPTIONAL_PIPELINE_STEPS`。

| # | Step | LLM? | 来源/落点 |
|---|---|---|---|
| 1 | `pinboard` | No | Pinboard API → `50-Inbox/02-Pinboard/` |
| 2 | `pinboard_process` | No | `02-Pinboard/` → 路由到 `01-Raw/` |
| 3 | `clippings` | No | `Clippings/*.md` → `50-Inbox/01-Raw/` |
| 4 | `articles` (深度解读) | **Yes (heavy)** | `01-Raw/` → `40-Resources/Articles/*_深度解读.md` |
| 5 | `quality` | **Yes** | 评分深度解读 → 决定是否进 absorb |
| 6 | `fix_links` | No | 全 vault 扫描修 broken wikilinks |
| 7 | `absorb` | **Yes (heavy)** | `*_深度解读.md` → `10-Knowledge/Evergreen/` |
| 7b | `entity_extract` | **Yes** | 深度解读 → Entity NER |
| 7c | `dedup` | No (embedding) | 本轮 absorb 产出 → MinHash 0.82 阈值合并 |
| 8 | `note_type_normalize` | No | 全 vault 改写 frontmatter `type:` 字段 |
| 9 | `registry_sync` | No | 全 vault 扫 6587 文件 → registry 表 |
| 10 | `moc` | No | 重建 MOC 索引文件 |
| 11 | `knowledge_index` | No | 重建 SQLite 派生表 |
| (opt) | `refine` | **Yes** | cleanup + breakdown,批处理改写 evergreens |

---

## 1. Review 框架

每步 4 问:

```
SOLVES:     这步解决了管线主链路上的什么具体问题?
INTRODUCES: 这步引入了什么副作用 / fidelity 风险 / latency / coupling?
SKIP TEST:  砍掉这步,具体哪个下游会坏?坏成什么样?用户感知到吗?
VERDICT:    Keep | Refactor | Merge into <X> | Move to scheduled/on-demand | Remove
```

---

## 2. 逐步审

### Step 1 · `pinboard`

- **SOLVES**:从 Pinboard API 拉新书签到 `02-Pinboard/`。这是 OVP 唯一的"主动获取入口",其它都是被动入站。
- **INTRODUCES**:网络依赖(API 限速 / 鉴权失败会拖慢整条链路)。但本身 deterministic,无 LLM,无 fidelity 风险。
- **SKIP TEST**:砍掉 → Pinboard 用户的新书签不会进 vault。**用户立即感知**。
- **VERDICT**:**Keep**。但应**与 `pinboard_process` 评估合并**(见下)。**不该 blocking 在主管线**:网络抖动会卡住所有非 Pinboard 用户的增量。考虑改成"先尝试 fetch,失败 → log + 继续后续步骤,不阻断"。

### Step 2 · `pinboard_process`

- **SOLVES**:把 `02-Pinboard/` 里的原始 JSON/HTML 转成 markdown,路由到 `01-Raw/`。
- **INTRODUCES**:无明显副作用,纯转换。
- **SKIP TEST**:砍掉 → Pinboard 拉到的书签停在 `02-Pinboard/`,不进入主链路。
- **VERDICT**:**Merge into `pinboard`**。"fetch + transform + route"逻辑上是一件事,拆两步只是历史。

### Step 3 · `clippings`

- **SOLVES**:把 `Clippings/*.md` 迁到 `50-Inbox/01-Raw/`,做文件名 sanitize。
- **INTRODUCES**:
  - **Bug 已确认**:`scan_clippings()` 用非递归 `glob("*.md")`,**16 个 Twitter clippings(在 `Clippings/Twitter/`)从未被处理**(`clippings_processor.py:248-258`)。
  - 文件名碰撞无显式处理(同名文件直接覆盖)。
- **SKIP TEST**:砍掉 → 用户拖到 `Clippings/` 的文章永远不会进入主链路。
- **VERDICT**:**Refactor**。改 rglob + 加 dedupe + 名碰撞 skip+warn。本身保留。和 `pinboard*` 合成一个统一的"intake"概念阶段(见 §4)。

### Step 4 · `articles` (深度解读) ⚠

- **SOLVES**:把异构 source(Twitter/blog/PDF/RSS)用 LLM 重写成"标准化深度解读 markdown",理论上让下游 absorb 输入更整齐。
- **INTRODUCES** ⚠⚠⚠:
  - **核心 fidelity 风险**:absorb 的主输入是 LLM 重写过的版本,**任何 LLM 在重写时塞进的"幻觉论点"都会被 absorb 当真相**,污染 evergreen → crystal 整条链。
  - 短内容被强行扩写(120 字 Twitter → 800 字"深度解读")—— 信息量不增,fidelity 反降。
  - "解读 of 解读"问题:博客本身在解读他人观点时,这步会再 LLM 一遍。
  - 跨步骤耦合:quality / absorb 都依赖这步的产物,改这步要回归测试三步。
- **SKIP TEST**:砍掉 → absorb 直接读 raw source。**fidelity 提升**,但 absorb prompt 需要重新调(它现在是按"读结构化深度解读"的输入风格设计的)。
- **VERDICT**:**Refactor — 已立项 BL-058**。
  - 拆成 `normalize`(0 LLM,只整 frontmatter + 文件名)+ `interpret`(opt-in lens,默认关,**不喂 absorb**)。
  - absorb 改读 raw source。
  - 历史数据全员打 `derived_via_stage='absorb_from_interpretation'` 标签,reader UI 显示 legacy 横幅,crystal 评分降权 ×0.7。

### Step 5 · `quality` ⚠

- **SOLVES**:用 LLM 给"深度解读"打分(0-5),`>= 3.0` 才进 absorb,作为 fidelity 闸门。
- **INTRODUCES**:
  - **评估对象(深度解读)在 BL-058 后即将消失**,这步的存在意义随之蒸发。
  - 它评的是"重写的语言流畅度",**不是 evergreen 对 source 的忠实度** — 跟我们关心的 fidelity 不是同一件事。
  - LLM 评分主观性大,3.0 阈值 magic number 来源不清。
- **SKIP TEST**:砍掉 → absorb 会处理所有源,包括"质量差"的源。但"差"的判定本身可疑。
- **VERDICT**:**Refactor (重定义) + 时机绑定 BL-058**。
  - 旧定义("评分深度解读")废除。
  - 新定义("evergreen 对 source 的 fidelity 检查"):用 grep 校验 `attribution_evidence` 必须在 source body 中逐字出现 — **deterministic 检查,不再用 LLM 评分**。失败的 evergreen 进 review 队列。
  - 这是 BL-058 的子项,跟着 absorb 改造一起做。

### Step 6 · `fix_links`

- **SOLVES**:全 vault 扫 broken wikilinks,改写到正确目标(只做 exact-match,不模糊匹配)。
- **INTRODUCES**:
  - **症状级处理**:broken links 多半来自 absorb / dedup / refine 改名时没回写引用。修这一步等于"在血迹上贴创可贴",根因在上游。
  - 全 vault 扫 6587 文件,每次都跑 — latency 显著。
  - exact-only 模式安全,但只能修能匹的;模糊的留下来等下次。
- **SKIP TEST**:砍掉 → broken links 累积,reader UI 出现死链。但 dedup 已经会回写引用(`concept_dedup.py:apply_proposal`),fix_links 主要捕获的是非 dedup 路径产生的断链。
- **VERDICT**:**Move to scheduled (daily)**。
  - 每次 incremental 都跑 → daily cron。
  - 同步立 BL 调查:**每周新产生的 broken links 来自哪个上游**?根因修了,这步使用率应该衰减。
  - 长期目标:ensure absorb / dedup / refine 都正确回写,fix_links 退化成"罕见情况下的人工运维工具"。

### Step 7 · `absorb` ⚠

- **SOLVES**:从深度解读(BL-058 后:从 raw source)里抽取原子 evergreen,落到 `10-Knowledge/Evergreen/`。
- **INTRODUCES**:
  - 当前主输入是 LLM 重写过的"深度解读" → fidelity 风险见 Step 4。
  - prompt 没有显式的 attribution chain 要求(BL-058 要补)—— 现在所有 evergreens 默认把 source URL 当 first-hand origin,即便源是 commentary。
  - 长 source 切分逻辑放在这一步内部,跟"提取"语义混在一起。
- **SKIP TEST**:砍掉 → 没有 evergreens 产出,主产物消失。**这是核心步骤**。
- **VERDICT**:**Refactor — 已立项 BL-058**。
  - 主输入改 raw source(BL-058a 后)。
  - prompt 加三档 attribution rule(`self / <named entity> / unknown`),每条 claim 必带 `attribution_evidence` 字段(verbatim 短语,可 grep 校验)。
  - 长文切分抽出成独立的 normalize 子步骤(确定性切分,非 LLM)。

### Step 7b · `entity_extract` ⚠

- **SOLVES**:从深度解读抽 PERSON/CONCEPT/TOOL/METHOD/SYSTEM/EVENT 实体 + Mentions 边。
- **INTRODUCES**:
  - 跟 `articles`/`absorb` 一样,**LLM 抽取没显式约束"实体名必须在 source body 出现"** — 模型可能补全成 wikidata 条目中的"全称",用户没写过。
  - entity_type 推测同样是语义判断,要走 BL-058 同一套 attribution rule。
  - 输入仍是深度解读,BL-058 后要切到 raw source。
- **SKIP TEST**:砍掉 → 没有 typed entity 层,reader UI 的"按人/概念/工具浏览"消失,crystal 抽取里 entity 信号也消失。
- **VERDICT**:**Refactor (跟随 BL-058)**。
  - 输入切换 raw source。
  - 加入 attribution_evidence 字段:每个 entity 必须能在 source body grep 到 surface form。
  - entity_type 推测加约束:仅当源文显式上下文(称谓/职位/动作)能定型时才打类型,否则 `unknown`。

### Step 7c · `dedup` ⚠

- **SOLVES**:scope 限于"本轮 absorb 产出的 slugs"(`promoted_slugs`),用 MinHash + 阈值 0.82 找重复 evergreens,合并 + 回写 wikilinks。
- **INTRODUCES**:
  - **0.82 阈值 magic number,来源不清,无 false-positive 数据**(`concept_dedup.py:47`)。
  - 合并不可逆 — 错合的概念回不来(虽然有 archive_applied_proposal 留底)。
  - scope 限本轮看似安全,但跨轮的"等价 evergreens"永远不会被对齐(轮 A 产 X,轮 B 产 X' 相似度 0.85,本轮 dedup 看不到 X)。
- **SKIP TEST**:砍掉 → evergreens 数量膨胀,同概念多文件,reader UI 出现"重复条目"。
- **VERDICT**:**Refactor**。
  - 立 BL 做"dedup threshold 校准实验":取过去 1 个月 dedup 决策样本,人评 100 条,出 precision/recall vs threshold 曲线。
  - 把"跨轮全量 dedup"挪到 daily scheduled(目前只跑本轮,跨轮债务越积越大)。
  - 当前 step 保留(本轮 dedup 抓住"absorb 同时产出的多条 paraphrase"是有价值的近场清理)。

### Step 8 · `note_type_normalize` ⚠

- **SOLVES**:把 frontmatter `type:` 字段统一到 8 种 canonical 值,原值存到 `original_note_type:`。
- **INTRODUCES**:
  - **每次跑都 normalize 905 条**(我刚刚 dry-run 看到的数字)—— 说明**新 evergreens 持续产出非 canonical type**。这是症状级修复,根因在上游(absorb 没在产出时就用 canonical type)。
  - 全 vault 扫 + 改写,latency 不低。
- **SKIP TEST**:砍掉 → frontmatter type 字段五花八门,downstream 查询(SQLite `objects.note_type`)要做 fuzzy match,reader 分类页混乱。
- **VERDICT**:**Move to scheduled + 修上游**。
  - 修 absorb prompt + entity_extract,让产出端就用 canonical type → 每日脏数据归零。
  - 这步降级为 daily cron + alert("今日产生 N 条非 canonical,根因待查"),不在 incremental 主链路。
  - 一次性 backfill migration 已经在历史里跑过,不需要每轮再做。

### Step 9 · `registry_sync` ⚠

- **SOLVES**:全 vault 扫 6587 文件,跟 SQLite `registry` 表比对,补/删 entries。
- **INTRODUCES**:
  - **120s timeout 易超**(刚才 dry-run 就超了,管线被阻断)。
  - 全表扫,**O(N) 跟 vault 大小线性增长**,只会越来越慢。
  - 跟其它步骤无强耦合 —— 不修 broken links、不改 evergreen、不动 absorb 输入。它就是在事后做"账实核对"。
- **SKIP TEST**:砍掉 → registry 表会跟文件系统漂移(204 not in registry / 525 not in filesystem,即当前现状)。但管线核心产出(evergreen / crystal)不依赖 registry sync。
- **VERDICT**:**Move to scheduled (daily) + 不阻断**。
  - 完全踢出 incremental 主链路。
  - daily cron 跑全表 reconcile,`--write` 模式。
  - 增量主链路只做"本轮 absorb 产出的 slugs upsert",constant time。

### Step 10 · `moc`

- **SOLVES**:重建 MOC(Map of Content)文件 —— Obsidian 桌面端用的导航 markdown。
- **INTRODUCES**:
  - 全 vault 重建,**latency 跟 evergreen 数线性**。
  - **可疑**:reader UI 已经从 SQLite 取数据(`/topics`/`/map`),MOC markdown 文件是不是只剩 Obsidian 桌面端用?如果是,这步对**线上产出无贡献**。
- **SKIP TEST**:砍掉 → 用 Obsidian 桌面端打开 vault 的人会看到导航文件不更新。reader Web UI 不受影响。
- **VERDICT**:**Move to scheduled** 或 **Decouple to on-demand**。
  - 不该跑在 incremental 主链路。
  - 立 BL 调查:还有哪些角色在用 MOC markdown?如果只剩 Obsidian 桌面端,改成 daily cron 或 `ovp-moc --rebuild` 手动触发。

### Step 11 · `knowledge_index`

- **SOLVES**:重建 `60-Logs/knowledge.db` 的派生表(truth projection / crystal_scores / 等)—— 这是 reader UI 和所有查询的底层。
- **INTRODUCES**:
  - 全量 rebuild 而非 incremental — vault 大了之后会变慢。
  - 但已经做了 `INDEPENDENT_CANONICAL_TABLE_COLUMNS` 保护(BL-055),crystal / provenance 等不会被 rebuild 抹掉。
- **SKIP TEST**:砍掉 → reader UI 看不到本轮新 evergreens。**用户立即感知**。
- **VERDICT**:**Keep**。但立 BL 做"incremental rebuild":只 upsert 本轮新/改 slugs 进派生表,不 drop 重建。当前全量 rebuild 是债务但不紧急。

### Step (opt) · `refine`

- **SOLVES**:cleanup(去掉重复段落 / 修小错)+ breakdown(把超长 evergreens 拆成原子)。LLM 批处理。
- **INTRODUCES**:
  - 又一个 LLM 改写步骤 → fidelity 风险跟 articles 同源。
  - 改写后的 evergreens 跟原 source 的关系变弱(改了一层模型抽,再改一层模型 refine)。
  - 默认不在 incremental 跑(--with-refine 才开),已经是 opt-in。
- **SKIP TEST**:砍掉 → 老 evergreens 不会被自动 refine。但手动 `ovp-cleanup` / `ovp-breakdown` 仍可调。
- **VERDICT**:**Keep as opt-in,加 attribution check**。
  - 已经是 OPTIONAL_PIPELINE_STEPS,默认不跑,OK。
  - 但 LLM 改写时**必须保留 attribution chain**(改写后的内容仍能 grep 回 source body),否则等于在做隐式幻觉注入。
  - 是 BL-058 attribution 规则的扩展应用对象。

---

## 3. 缺失的步骤

review 不只问"砍哪些",也要问"缺哪些步骤导致了打补丁式的修复":

| 缺失 | 现在被谁打补丁 | 应该是 |
|---|---|---|
| **attribution_evidence 校验** | 无(完全靠 LLM 自觉) | BL-058 引入,deterministic post-check,grep 不到即标红 review |
| **fidelity replay** | 无 | 抽样 evergreen,从 source 重抽,diff,飘出阈值的进 review |
| **inbox sweep** | 无,堆积无限 | 01-Raw 超 N 天没动 → archive 或 explicit-skip,避免无限堆积 |
| **schema versioning** | `note_type_normalize` 全 vault 扫修 | frontmatter 字段加版本号,absorb 产出端就用 canonical,migrations 一次性 |
| **broken-link root-cause logging** | `fix_links` 全扫修 | 每次产生 broken link 时 log 上游(哪个 step 哪条 evergreen 引入的),便于事后审 |
| **incremental knowledge_index** | 全量 rebuild | 只 upsert 本轮 changed slugs,O(changed) 而非 O(vault) |

---

## 4. 重排后的目标管线

按 review verdict 重组,主链路变得**更短、更纯、fidelity-first**:

### 4.1 每次增量必须跑(blocking,串行)

```
intake          (合并 pinboard + pinboard_process + clippings;rglob + dedupe + sanitize)
  ↓
normalize       (新,0 LLM:frontmatter 校验/补齐 + 长文确定性切分;BL-058)
  ↓
absorb          (LLM,主输入 raw source,产出带 attribution chain 的 evergreens;BL-058)
  ↓
attribution_check  (新,deterministic:grep 校验 attribution_evidence;BL-058)
  ↓
entity_extract  (LLM,同 attribution rule;BL-058)
  ↓
dedup_local     (本轮 absorb 产出,scope 限本轮;立 BL 做 threshold 校准)
  ↓
knowledge_index_incremental  (新:只 upsert changed slugs;立 BL)
```

### 4.2 Daily scheduled(后台,非阻断)

```
fix_links              (全 vault broken-link 扫修)
note_type_normalize    (兜底,主修上游;长期 905 → 0)
registry_sync          (账实核对)
dedup_global           (跨轮全量 dedup;立 BL)
moc_rebuild            (调查后再决定是否保留)
fidelity_replay_sample (新:抽 N 条 evergreen 重抽对比)
```

### 4.3 On-demand(显式 CLI,不在管线)

```
interpret      (BL-058:opt-in lens,纯人读,不喂下游)
refine         (cleanup + breakdown,已是 OPTIONAL)
inbox_sweep    (新:archive / skip 长期未处理 raw;立 BL)
ovp-reabsorb   (BL-058:单条 evergreen 从 source 重抽,审核用)
```

---

## 5. 与 BL-058 的关系

BL-058 在 review 之前已经讨论清楚,本 review 把同样的拷问推到所有步骤后,反过来**确认 BL-058 是 review 框架的应用而非孤立改造**。

BL-058 已覆盖:Step 4 (articles)、Step 5 (quality 重定义)、Step 7 (absorb)、Step 7b (entity_extract attribution)、新增 attribution_check / fidelity_replay。

review 新发现需要立项的:Step 6 (fix_links 根因)、Step 7c (dedup threshold 校准 + 跨轮 dedup)、Step 8 (note_type 上游修)、Step 9 (registry_sync 出主链路)、Step 10 (moc 可疑性调查)、Step 11 (knowledge_index incremental)、新增 inbox_sweep / schema_versioning / broken-link root-cause logging。

---

## 6. 后续 BL 拆解建议

review 出的 verdict 落到具体 BL,排在 BL-058 之后(避免改动叠加):

| BL | 名 | 优先级 | 描述 |
|---|---|---|---|
| BL-058 | normalize / absorb / interpret 解耦 + attribution chain | P0(进行中) | 已讨论,出设计稿 |
| BL-059 | registry_sync / moc / note_type_normalize 移出主链路 | P1 | 改成 daily scheduled,主链路 latency 降 |
| BL-060 | fix_links 根因调查 + broken-link logging | P2 | 找出"每周谁制造 broken links",修上游 |
| BL-061 | dedup threshold 校准实验 | P2 | 0.82 是否合理,出 precision/recall 数据 |
| BL-062 | knowledge_index incremental rebuild | P2 | 主链路 O(N) → O(changed) |
| BL-063 | intake 合并 (pinboard + pinboard_process + clippings) | P3 | 历史上的人为分割,合并降低概念负担 |
| BL-064 | inbox_sweep + schema versioning | P3 | 从根源避免堆积和补丁式 normalize |
| BL-065 | moc 必要性调查 + 可能下沉为 on-demand | P3 | 调研后决策,可能直接 remove |

---

## 7. 验证

review 文档本身不动代码。验证手段是:

1. 每条 verdict 必须能映射到一个具体的 BL,且 BL 描述跟 review 出的"INTRODUCES" 项对得上 ——**没有"凭印象重构"**。
2. 用户(chris)审完此文档,可以对任一条 verdict 说"不同意/需要更多数据",我们再补 review。
3. BL-058 完成后,回头跑一遍这份 review 的 §4.1 主链路,看 latency 和 fidelity 是不是符合预期(预期:latency 从 ~90 min 降到 ~30 min,fidelity 显著提升)。

---

## 附录 A:本次 review 用的代码核实点

- `unified_pipeline_enhanced.py:459-477` — STEPS 定义
- `unified_pipeline_enhanced.py:2046-2240` — `step_quality` 实现(走 `batch_quality_checker`)
- `unified_pipeline_enhanced.py:2241-2282` — `step_fix_links` 实现(走 `migrate_broken_links --exact-only`)
- `unified_pipeline_enhanced.py:2284-2303` — `step_registry_sync` 实现(120s timeout)
- `unified_pipeline_enhanced.py:2835-2906` — `step_dedup` 实现(MinHash 0.82 阈值,scope 限本轮)
- `unified_pipeline_enhanced.py:2908-2929` — `step_moc` 实现
- `unified_pipeline_enhanced.py:2983-3011` — `step_note_type_normalize` 实现(单次 dry-run normalize 905 条)
- `clippings_processor.py:248-258` — `scan_clippings` 非递归 bug
- `concept_dedup.py:47` — DEFAULT_THRESHOLD = 0.82
- pipeline-report-20260504-230057.md — 实际 dry-run latency / 输出
