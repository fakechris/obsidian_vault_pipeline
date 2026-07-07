# Skill Learning from Coding-Agent Transcripts — 设计 v1

> **状态:PARKED(operator 决定,2026-07-06)。** 本文档 = 完整设计 + 已执行的 S1 价值实验数据,
> 不展开开发。unpark 条件见 §0.3。
> 目标:从本地 coding-agent 会话记录(Claude Code / Codex JSONL transcripts)沉淀
> **可复用、有逐字证据、经 eval 验证的 skill**,编译回 agent harness(`.claude/skills`)。
> v0→v1 变更:KMEM 会话导入链代码级确认(§2);S1 全库扫描已执行、预注册规则已判(§3);
> 决策 9(Claude P0/Codex P1)被 S1 数据推翻修正(§8.9);新增语料易腐性事实与镜像建议(§3.3);
> eval 协议具体化为行为预测对判(§6.L5);闭环指纹机制具体化(§6.L5)。

---

## 0. 战略定位 —— 先回答"这事歪没歪"

### 0.1 事实

OVP 主线是 **vault 知识抽取**:M32 Level-3 关账(dogfood 时钟未启动、全量 corpus 未跑)+
M34 knowledge substrate 实验轨道。关键路径上没有一寸属于 skill 学习。

### 0.2 判断

**这个 track 不是主线,但也不是无关的歪路 —— 它是同一台机器对准第二种语料。**

- 结构同构:transcript 是一种 source,case 是一种 grounded unit,skill 是一种 commitment。
  四账本(Artifact→Claim→Interpretation→Commitment)的映射是逐层严格的(§5),
  grounding 纪律(逐字 quote 机械校验)可从 ovp-core 直接平移。
- 市场信号:KMEM 把 coding-agent session 导入做成了第一公民(§2 代码级证实,
  cursor/claude/codex/gemini/opencode 五家 parser + 自动 discovery)——
  "个人知识底座"产品都会走到这一步,这不是我们的臆想。
- 但**现在开工是错的**:它抢 Level-3 关账的操作员带宽;它的闭环验证
  (skill 激活后数周的 recurrence 观察)比 crystal dogfood 还慢;
  且 S2 之前它的产品价值只是假设(S1 只证明了信号密度,没证明教训质量)。

### 0.3 决定

**PARKED。** 本次产出固化为:本设计 + `scripts/skill_s1_scan.py` + `.run/skill-s1/` 数据。
unpark 需同时满足:
1. M32 Level 3 merge 完成(IMPLEMENTATION_PLAN.md Stage 5);
2. 操作员显式点头启动 S2(§10);
3. 启动时先重跑 S1(语料是滚动窗口,数字会变,见 §3.3)。

唯一不等 unpark 的事:**语料镜像**(§3.3)——Claude Code 30 天自动清理是实测事实,
parked 每一天都在丢主 session。镜像是一条 rsync 级命令,不算开发展开。

---

## 1. 调研输入与证据等级

| 来源 | 证据等级 |
|---|---|
| `~/source/crack/nowledge-test/research/kmem-skill-learning-research.md` | 运行时从加密模块内存抽出的原文(prompt 全文 + Cypher + 函数名),非猜测 |
| `.../research/nowledge-mem-extraction-pipeline-research.md` | Pyarmor 解密后 pycdc 反编译代码,标注了直接证据/推断/占位 |
| `unpack/recovered/nowledge_graph_server/tools/parsers/*` + `services/discovery/*` | **本次(2026-07-06)直接读反编译源码**,§2 全部结论出自这里 |
| EverMe/EverOS、Hermes | 前期对话调研(操作员提供),二手,只用于 §4 的"偷什么"层面 |
| 本地语料 + S1 数据 | 本机实测,脚本已提交,可复现 |

---

## 2. KMEM 会话导入链 —— 代码级确认(2026-07-06)

操作员的问题:"KMEM 是不是就扫这种 session?" **是,且比预想更彻底。**

### 2.1 发现与解析(直接证据)

- `services/discovery/ConversationDiscoveryService.discover_all()` 自动发现四家本地会话:
  `claude`(`~/.claude/projects/<encoded>/*.jsonl`,含 Windows/WSL 路径变体解码)、
  `codex`、`cursor`(vscdb + export 两种)、`opencode`(文件 + sqlite)。供 import API 用。
- `tools/parsers/` 有 8 个 parser:claude / codex / cursor / gemini / opencode /
  generic json / markdown,`handler.py:ThreadFileHandler` 统一入口,
  append 幂等键 = `file:<path>:<message_count>`。
- Claude parser(`parsers/claude.py`)**包含 subagent**:
  `<session>/subagents/agent-*.jsonl` 按 mtime 排序并入 metadata。

### 2.2 压平语义 —— 我们最重要的对照事实

`parse_claude_code_session_streaming` 把事件流压成 `{role, content}` 文本消息:

- `tool_use` → 字符串 `"[Tool: Bash] $ <command>"`(输入摘要,非结构);
- `tool_result` → 字符串化拼接,`>10000` 字符截断;
- **`is_error` 字段被丢弃**(`_format_claude_content` 不读它);
- tool_use_id ↔ tool_result 的配对关系被丢弃;
- 时间戳保留在 message 级,行号不保留。

### 2.3 KMEM 从 session 到 skill 的完整链条

```
JSONL(结构化事件) ─压平→ Thread/Message(纯文本)
  ─LLM 抽取(CreateMemory,paraphrase,self-contained)→ Memory(unit_type 分类)
  ─5 路信号 recall + EVOLVES/community 拓扑→ 候选簇
  ─target_review LLM(4 门 + non_obvious_delta)→ Skill candidate
```

设计含义(本设计的差异化根基,不是姿态而是结构):

1. KMEM 的 skill 证据离原始日志**隔两次 LLM 转写**(memory 抽取 + skill 合成)。
   它的 `non_obvious_delta` 诚实门锚在第一次转写的 paraphrase 上——而那一层
   连 quote 字段都没有(抽取管线调研 §5.6),SOURCED_FROM 靠时间窗补丁回填。
2. `is_error` 与 tool 配对在入图前就丢了,KMEM **结构上无法**做确定性 error→fix
   检测。它的 procedural 信号完全依赖 LLM 恰好把教训写成了 procedure/learning
   类型的 memory——漏写即不可见(其审计 D2 承认了这一点,为此加 learning 门补漏)。
3. 它对 session 语料的 occasion 单位 = thread = 一个 session 文件——
   与我们的 session occasion 同构(§6.L4),该规则直接借用。

---

## 3. 本地语料与 S1 实测(2026-07-06,预注册规则已判)

### 3.1 语料盘点(python os.walk 实测,非 rtk 过滤输出)

| 来源 | 位置 | 规模 | 结构要点 |
|---|---|---|---|
| Claude Code 主 session | `~/.claude/projects/<slug>/*.jsonl` | **72 个**(38 项目目录,滚动 30 天窗口) | 每行一事件;`is_error` 的 tool_result 以 user-role 事件出现;tool_use_id 可与 assistant 侧 tool_use 配对 |
| Claude Code subagent | `<session>/subagents/**/agent-*.jsonl` | **667 个** | 同方言;归属父 session 的 occasion |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | **700 个**(保留 333 天) | `response_item.payload.type ∈ {message, reasoning, function_call, function_call_output, custom_tool_call(_output), web_search_call}`;输出含 `Process exited with code N` 可机械解析退出码;`turn_context.cwd` 给出项目归属 |

### 3.2 S1 结果(`scripts/skill_s1_scan.py`,28 秒全库,零 LLM)

| 指标 | 数值 |
|---|---|
| tool errors 总数 | 13,099 |
| D1 失败连段(紧口径:同 tool 连续失败到下次成功折叠为 1 窗口) | **9,940** |
| D1 error→fix 配对(松口径上界) | 12,793 |
| D2 用户纠正(中英标记,<500 字符) | **288** |
| D3 跨 session 重复命令头(同项目 ≥3 sessions) | 98 |
| D4 权限拒绝 | 12 |
| D5 hook 反馈 | 0(见下方 caveat) |
| **L3 窗口估计(紧口径)** | **10,338** |
| 90 天活跃项目 | 75 个,其中 **80% 有 D1/D2 信号**(60/75) |

预注册规则判定(§10 v0 原文:覆盖 <30% 或窗口 >5 万则砍/停):
**两条都 PASS**——覆盖 80%、窗口 1.03 万。S1 通过。

诚实标注(caveat):
- S1 数的是 **raw detector hit,精度未知**。D1 的大头必然是良性错误
  (file-not-found、grep 无命中、lint 失败即修)——判断"这里面有没有值得沉淀的教训"
  正是 L3 LLM 门的职责,不是 S1 的。S2 才量化 hit→case 的转化率。
- D5=0 是 detector 定义缺陷而非信号不存在:hook 反馈不一定以 `is_error` 出现,
  应从 `system` 事件里找。S2 前修。
- D4 仅 12:权限拒绝常直接终结轮次,窗口稀少,但每个都是高价值样本(用户意图边界)。
- 分布长尾:top 项目 codex:openclaw-template 24 sessions 就有 1,027 errors + 94 corrections;
  也有大量近零信号的 scratch 项目(cumora agents 等)。per-project 明细在
  `.run/skill-s1/summary.json`。

### 3.3 语料是易腐的(设计硬约束)

实测:`~/.claude/settings.json` 无 `cleanupPeriodDays` 覆盖 → 默认 30 天清理生效,
主 session 最老 29 天。**Claude Code 语料是滚动 30 天窗口,parked 期间每天都在丢。**
Codex 实测保留 333 天,暂无此问题。

缓解(唯一建议在 parked 状态下就做的事):每日增量镜像
`~/.claude/projects` → `~/Documents/ovp-vault/.ovp/skill/raw-mirror/claude/`
(`rsync -a --ignore-existing`;JSONL 是 append-only,`--ignore-existing` 对已封存
session 安全,对活跃 session 用 `-a` 覆盖亦可)。注意:**镜像内容含密钥级敏感信息
(§6.L1),raw-mirror 必须在 vault 的 gitignore 内,永不进 git、永不离机。**

---

## 4. 对照系统:三家各偷一招,并修掉它们的坑

| 偷什么 | 从谁 | 我们的修正 |
|---|---|---|
| `non_obvious_delta` + `delta_evidence_ids` + 工具层硬 reject 空/无据 delta | KMEM(三家最强诚实机制) | 证据锚从「paraphrase 的 memory id」升级为「transcript 行级逐字 span」(§2.3 第 1 条是它做不到的原因) |
| occasion 计数作为价值货币;同 incident 多 phase 算 1 次;计数 EXACT 不让 LLM 重估 | KMEM | 映射 session > project+day(§6.L4);同 subject 判定放合成期 LLM 门 |
| 4 道硬门 + naming test + 「propose 太少是主要失败模式」的 step-budget 措辞 | KMEM 主 prompt(12,711 字符原文在调研档) | 原文借用,冻结进 repo,改版走 evolution 候选流程 |
| case 中间原子层 + 采集/合成异步解耦 | EverOS | case 必须带 evidence span 且入 append-only 账本(EverOS 只有 cluster_id provenance) |
| eval-gated promotion | Hermes | 默认**开**且不可关(Hermes 把 GEPA optimizer 放独立 repo 默认关 → LLM 自写自验自批,其 issue #25833) |

三家共同的死穴:**成功度量是自报的**(KMEM `/skills/{id}/outcome` 靠调用方上报、
Hermes reviewer 自评、EverOS 无闭环)。我们的编译目标就是产生语料的那个 harness,
闭环指标可以完全客观(§6.L5 recurrence-after-skill)——这是本设计相对三家唯一的
原创增量,其余都是「借最强的 + 用我们的 grounding 纪律补它们的洞」。

---

## 5. 立场:skill 是 Commitment

| 账本 | 对应物 | 不变式 |
|---|---|---|
| Artifact | transcript 事件窗口(session 文件 + 行区间) | 只读;脱敏归一化流是校验锚 |
| Claim | case:窗口里「发生了什么/教训是什么」 | 每条 quote 逐字命中归一化流,机械校验,不匹配即 reject |
| Interpretation | eval verdict:两个 judge family 的行为预测对判 | 记录而非覆盖;两家都过才 `eval_passed` |
| Commitment | active skill:编译成 SKILL.md 进 harness | 只能由 `eval_passed` + 操作员 activate 产生;patch 重走 eval |

三条不可谈判项:
1. **证据 = 逐字 span,机械校验**(truth-layer 纪律平移);
2. **candidate 永不自动 active**;
3. **无价值实验不落持久层**(M34 过程规则;S1 已过,S2 是 unpark 后第一件事)。

---

## 6. 五层架构

```
L1 采集 ──→ L2 信号扫描 ──→ L3 case 抽取 ──→ L4 skill 合成 ──→ L5 信任门/编译/闭环
(确定性)     (确定性)         (LLM 门)          (LLM+硬门)        (eval+人工+确定性闭环)
                                                                      │
      └──────────────── 新 transcript 回流(编译目标=harness) ←────────┘
```

### L1 采集(确定性,无 LLM)

**WorkEvent 归一化 schema**(两方言适配到同一形状):

```json
{
  "ev": "tool_call | tool_result | user_text | assistant_text | meta",
  "session": "<session_id>", "agent": "main | subagent:<id>",
  "source": "claude | codex", "project": "<decoded cwd>",
  "seq": 123, "raw_line": 456, "ts": "...",
  "tool": "Bash", "call_id": "toolu_xx",
  "ok": false, "err_class": "exit_nonzero | is_error | timeout",
  "text": "<脱敏后归一化文本>"
}
```

- 方言适配:Claude(`message.content[]` 里的 tool_use/tool_result,以 `tool_use_id` 配对;
  subagent 文件归属父 session)/ Codex(`function_call`/`function_call_output` 按序配对,
  退出码从输出文本解析;`turn_context.cwd` → project)。
- 增量游标:`cursor.json` 记 `file → (size, line)`;JSONL append-only,天然支持。
  文件被清理(30 天窗口滑出)只影响未来扫描,已产出的 signal/case 不回收。
- **脱敏(硬门,先于一切下游)**:确定性规则 —— 已知 pattern(`sk-`、`ghp_`、`AKIA`、
  `Bearer `、`.env` dump 的 `KEY=value` 行)+ 高熵 token(base64/hex,长度 ≥20 且
  香农熵 >4.5)。替换为保形占位 `«redacted:<sha1-8>»`(同值同占位,跨窗口可对齐)。
  **quote 校验锚定在脱敏后的归一化流上**,raw transcript 只读不复制
  (镜像除外,见 §3.3,镜像不出机不入 git)。
- 方言漂移防御:两家 JSONL 都无版本号字段。parser 版本化(`parser: claude/v1`),
  未知事件类型计数上报而非丢弃;计数突增 = 方言变了,fail-loud。

### L2 信号扫描(确定性 detector ≈ KMEM 5 路 Cypher recall 的 transcript 版)

| detector | 定义 | S1 实测密度 | 已知误报模式 |
|---|---|---|---|
| D1 error→fix | 同 tool 失败连段(失败→…→下次成功折叠为 1 窗口) | 9,940 | 良性错误占大头(file-not-found、试探性 grep);由 L3 门过滤 |
| D2 用户纠正 | assistant 动作后 <500 字符 user 文本含纠正标记(中英词表) | 288 | 标记词表召回/精度均未校准;"不要"类词高误报 |
| D3 重复 procedure | 归一化命令头(前 2 token,去 env 前缀)在同项目 ≥3 sessions 复现 | 98 | 命令头碰撞(`git status` 类日常操作不是 skill);L4 的 recurrence≠价值,门在 naming test |
| D4 权限拒绝→改道 | is_error 文本命中拒绝标记 | 12 | 低量高值;拒绝后 session 常终止,"改道"半窗口 |
| D5 hook/harness 反馈 | hook block、CLAUDE.md 违规提醒后的行为修正 | 0(定义缺陷) | 当前只查 is_error 分支;应改查 `system` 事件。S2 前修 |

产物 `SignalWindow{detector, session, span:[seq_from, seq_to], strength, fingerprint}`。
`fingerprint` = detector 签名(如 `d1:Bash:exit_nonzero:cargo test`、`d2:<subject-hash>`),
是 L5 闭环的对账键。signals.jsonl 可全量重建 = 投影,不是账本。

为什么坚持确定性:免费(全库 28 秒)、可重建、可对账(闭环用同一套 detector)、
且是 KMEM 结构上做不到的(§2.3)。LLM 永远只看被圈出的窗口,绝不通读 404MB。

### L3 case 抽取(LLM 门,逐窗口)

- 输入:窗口 ± 上下文事件(截断 ~4k tokens);
- prompt 职责(`skill_case/v1`,冻结):三选一 —— REFUSE(无教训:良性错误/一次性事故)
  / lesson(一段话:什么情境、什么坑、正确做法)+ 1-3 条逐字 quote + subject 短语;
- 机械校验:每条 quote 必须是该窗口脱敏归一化流的**连续子串**,不匹配整条 case reject
  (计数上报,不静默);
- 幂等:`case_key = sha256(session, span, prompt_version)`,重跑 0 新增;
- cassette:`<vault>/.ovp/cassettes/skill/`,replay 可复现(crystal-synth 纪律平移);
- 预期:REFUSE 率应当很高(D1 的良性错误都在这里死掉)。**REFUSE 率本身是 S2 的
  关键读数**——太低说明门松,太高说明 detector 圈错了地方。

### L4 skill 合成(跨 case 聚类 + 硬门)

- 聚类:case.lesson + subject 的 lexical/embedding 聚类(embedding 走 M34 L1 的
  本地多语 embeddings,若彼时已落地;否则 lexical 起步——不为此引入新依赖);
- **occasion 计数**(KMEM 规则平移):每 case 记入恰一个 canonical anchor,
  优先级 **session > project+day**;subagent 归属父 session;
  同 subject 跨 session(同一 bug 修了三个晚上)由合成 prompt 按 KMEM Step 1.2
  语义判为 1 occasion("同 incident 的 phases 算一次");
- 四道硬门(KMEM 主 prompt 原文语义,映射到 case 语料):
  1. procedural form —— lesson 是可复用的"how",不是一次性叙事;
  2. recurrence —— ≥2 **不同 subject** 的 occasions(计数是地板,EXACT,不许 LLM 重估);
  3. non-redundancy —— 先查现有 candidates/active skills,同域默认 enrichment
     (工具层硬 block 近重复);
  4. cohesion / naming test —— 一句不含 and 的话说出共同 procedure,
     名字必须是 class 级 kebab-case,不得是 incident/ticket/日期;
- **诚实门**:`non_obvious_delta`(1-3 条「competent agent 也会搞错」)+
  `delta_evidence_ids` 必须指向具体 case 的具体 quote;工具层 reject 空/无据 delta
  ——KMEM 的原话适用:「if you cannot point to the specific memory that demonstrates
  the rule, you are inventing it (REFUSE)」,只是我们的终点是原文而非 memory;
- kind 枚举沿用:procedure / decision-policy / checklist / debugging-playbook / quality-bar。

### L5 信任门 + 编译 + 闭环

**eval 协议(counterfactual 行为预测,不是观点打分)**:对每条 delta,取其证据窗口的
**前文**(错误发生前的事件流,不含 delta 不含结局),问 judge:
「一个 competent agent 接下来最可能怎么做?」——judge 复现了 evidence 里的错误
= delta 是真 gotcha(没有 skill 会翻车);judge 直接给出正确做法 = delta 不非显然,
砍掉。两个 judge family(不同供应商)独立跑,**双过才 `eval_passed`**
(两-judge-family 规则,M34 过程纪律)。这比「你觉得这条 skill 好吗」强:
它是可判对错的行为预测,且 judge 看不到答案。

**激活**:`eval_passed` → console review 面 → 操作员逐条 activate。无自动通道。

**编译**:candidate → `SKILL.md`(name = naming-test 通过的 kebab-case;
description = 触发面一行;body = 教训 + 具体规则,从证据泛化但每条规则脚注
case id)→ 按 scope 落 `~/.claude/skills/<name>/` 或 `<project>/.claude/skills/`;
镜像存 `compiled/` 供 diff/回滚;`version` 每次 recompile +1。

**闭环(客观,零自报)**:
- **use_count**:新 transcript 里 Skill 工具调用有记录,L1 直接数;
- **recurrence-after-skill**:skill 激活时把其 `delta_evidence` 对应的 detector
  `fingerprint` 集合写入 skills.jsonl;此后 L2 每个新 hit 与所有 active skill 的
  fingerprint 对账,命中即 `recurrence_after += 1`。降到 0 = skill 真的改变了行为;
- **curator**(周期跑,全确定性触发 + LLM 起草):
  - rot:激活 ≥90 天 且 use=0 且 fingerprint 亦不再出现 → 环境变了,提议 archive;
  - refine:use>0 但 recurrence 未降 → skill 被触发但没拦住,提议改写(重走 eval);
  - merge:delta 语义重合的 active skills → 提议合并(重走 eval)。
- 归因诚实性:fingerprint 消失 ≠ skill 的功劳(可能任务分布变了)。所以 curator
  只做**保守动作**(提议而非执行,archive 而非 delete),且 recurrence 指标
  只用于排序 refine 队列,不用于自动奖惩。

---

## 7. 数据模型(projections over ledgers;无图库、无 SQLite)

与 M23/M31/M34 一致:append-only JSONL 账本 + 可重建投影。KMEM 用 Kuzu 图
(Skill 节点 + SYNTHESIZED_FROM 边);我们用账本得到同样查询面,且天然可 replay、可审计。

```
<vault>/.ovp/skill/
├─ raw-mirror/           # §3.3 镜像(gitignore,永不离机)
├─ cursor.json           # L1 游标
├─ signals.jsonl         # L2 投影(可全量重建,非账本)
├─ cases.jsonl           # 账本:case 一经写入不改,幂等 by case_key
├─ skills.jsonl          # 账本:skill 生命周期事件流
│    events: proposed | evidence_added | eval_run | eval_passed | activated |
│            use_observed | recurrence_observed | refine_proposed |
│            rot_proposed | merged | archived
├─ compiled/             # SKILL.md 镜像(真身在 ~/.claude/skills)
└─ read-model/           # 投影:当前 skill 状态表(console/find 消费)
```

stage machine:`candidate → eval_passed → active → (archived)`;
`draft` 不需要(KMEM 的 draft 是「编译完等激活」,我们的编译在激活时才发生,少一态)。
字段沿用 KMEM 验证过的:`kind` / `non_obvious_delta` / `delta_evidence_ids` /
`evidence_count`(=occasion 数)/ `triggers` / `version` / `scope`;
新增:`fingerprints[]`、`recurrence_after`、`eval_verdicts{family→verdict}`。

---

## 8. 关键设计决策(含被否决的替代方案)

1. **逐字 span vs paraphrase 证据**。否决 paraphrase:KMEM 全链是反例
   (quote 字段不存在、SOURCED_FROM 靠补丁、诚实门锚在转写上)。代价:脱敏与
   归一化必须先行且稳定,否则 quote 校验会大面积误杀——所以锚定义在
   「脱敏后归一化流」上,一次定义,校验与存储同锚。
2. **确定性信号层在前,LLM 门在后**。否决「LLM 通读 transcript 选窗口」:
   404MB/28 秒 vs 数百美元;且 detector 可重建、可与闭环对账。
   KMEM 同构(Cypher recall → LLM 门),只是它的 recall 跑在丢了结构的图上。
3. **counterfactual 行为预测 vs replay harness(v1)**。完整 replay(带 skill 重跑
   历史任务)是最强验证但要可重放任务集,是整个系统最贵的骨头。v1 用 §6.L5 的
   行为预测对判(可判对错、judge 盲于答案、双 family)+ 人工激活;replay 降为 P2,
   由闭环数据决定是否值得建。**否决 Hermes 式「reviewer 自评即通过」**。
4. **编译目标 = harness 本身**。这不是实现细节而是闭环的成立条件:skill 落进
   `.claude/skills` 后新语料自动回流 L1,use/recurrence 全部确定性可测。
   三家都没有这个性质(KMEM 的 skill 活在自己 app 里,outcome 靠上报)。
5. **脱敏是 L1 硬门**。transcript 含 env dump、token、密钥。保形占位使同一
   secret 跨窗口可对齐(聚类不受损),quote 校验不受损。
6. **账本 + 投影 vs 图库**。M34 已判实体/图层 DEAD for answers;skill 系统的
   查询面(列表、对账、review)用投影全覆盖。否决 Kuzu/SQLite。
7. **prompt 冻结 + cassette + 幂等**,crystal-synth 全套纪律平移;prompt 改版走
   evolution 候选流程。
8. **niche 手动口子**:`ovp2 skill-mint --session <id> --span a..b` 直通 L3→L4
   (等价 Hermes /learn)。接受「selective 周期跑覆盖不了 niche」是设计行为。
9. **两方言采集都做(v0 决策被 S1 推翻的修正)**。v0 说 Claude P0/Codex P1;
   S1 实测 Codex 语料量 10 倍(700 vs 72)、保留期 11 倍(333d vs 30d)、
   错误信号同样丰富——采集层双方言(S1 脚本已双方言,归一化成本低),
   **编译目标仍只是 Claude Code skills**(闭环在哪就编到哪;Codex 侧闭环
   等它有稳定 skill 机制再说)。
10. **cron 而非 daemon**(对齐 M32 §9):`skill-scan` 增量幂等,cron 即可。

## 9. 成本模型(S1 实测推导)

- 一次性 backfill 上界:10,338 窗口 × ~4k input tokens ≈ **~40M input tokens**
  (输出短,case JSON)。不做全量 backfill:recency-first + 项目白名单
  (top 信号项目见 `.run/skill-s1/summary.json`),S2 只碰 2-3 个项目。
- 稳态增量:两库合计 ~4-5 sessions/天(72/30d + 700/333d)→ 数十窗口/天
  → L3 日成本在分币级。L4 按周批;L5 eval 只对过了四门+诚实门的 candidate 跑
  (KMEM 每轮 ≤8 candidate 的预算语义借用),eval 是小头。
- 结论:成本瓶颈不在钱,在**操作员 review 带宽**——这正是 parked 的原因之一。

## 10. 价值实验

**S1 — 信号密度(已执行,2026-07-06,PASS)**:见 §3.2。
脚本 `scripts/skill_s1_scan.py`(已提交),产物 `.run/skill-s1/{summary.json,sessions.jsonl}`。
预注册规则(覆盖 ≥30%、窗口 ≤5 万)双过:80% / 1.03 万。

**S2 — 端到端教训质量(unpark 后第一件事,规则预注册如下,跑前不许改)**:
取 2-3 个高信号项目(S1 数据点名:`openclaw-template`(1,027 err/94 corr)、
`meituan`(459/24)、本仓库(328/1))的全部 sessions,脚本化 L2→L3→L4,
产出 top-10 candidates(delta + 逐字证据)。判定:
1. 操作员盲评「这条我愿意 activate 吗」:**≥3/10 yes 才立项**;
2. 每条 yes 的 delta 过两个 judge family 的行为预测对判(§6.L5 协议);
3. 召回下限:操作员记得的 ≥1 个真实反复踩的坑必须出现在 candidates 里。
   现成对照样例(memory/计划有档,transcript 必有对应窗口):rtk 把失败 push
   报成 ok、cassette 钉死坏回复、stale run.lock;
4. 附加读数(不判生死,但记录):L3 REFUSE 率、hit→case 转化率、case→candidate 收敛比。

S2 不过 → 归档本设计并记录死因(与 M13 concept-map 同一处置)。

## 11. 阶段划分(全部 blocked on unpark;Stage 0.5 除外)

```
Stage 0   : S1 信号密度扫描            [DONE 2026-07-06, PASS]
Stage 0.5 : 语料每日镜像(rsync 级)     [建议立即,parked 不阻塞——防 30 天窗口丢数据]
Stage 1   : S2 端到端价值实验          [blocked on unpark;预注册规则见 §10]
Stage 2   : L1+L2 产品化               [blocked on S2] ovp-intake importer + ovp-skill scan
Stage 3   : L3+L4+L5(mint/review/compile) [blocked on 2] 三命令 + console skills 页
Stage 4   : 闭环 + curator             [blocked on 3] fingerprint 对账 + rot/refine/merge 提案
```

CLI 面(定名,防止未来漂移):`ovp2 skill-scan / skill-mint / skill-review /
skill-compile / skill-curator`,turnkey 形态对齐 daily/crystal-synth。
crate 落点:新 `ovp-skill`(L2-L5)+ `ovp-intake` 加 transcript importer(L1)。
**Stage 2 之前不建任何 crate、不加任何产品代码。**

## 12. Non-goals

- 不做 Hermes 式 inline background review(采集/合成异步解耦)。
- 不做图数据库/实体层(M34 判决)。
- 不做自动 activate;不做 active skill 的自动改写(patch 重走 eval + 人工)。
- 不做通用对话记忆:fact/preference 是 crystal/memory 层的事,这里只做 procedural。
- v1 不做 replay harness(P2,闭环数据决定)。
- 不做 Cursor/Gemini/OpenCode 方言(KMEM 做了五家是因为它卖导入;我们只吃自己产的语料)。
- 不重启对 KMEM 的逆向(调研冻结,证据等级已标)。

## 13. 风险与开放问题

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 语料易腐(Claude 30 天窗口) | 高(正在发生) | Stage 0.5 镜像;unpark 时重跑 S1 |
| JSONL 方言漂移(两家都无版本号) | 中 | parser 版本化 + 未知事件计数 fail-loud(§6.L1) |
| 镜像/账本含敏感信息 | 高 | 脱敏硬门;raw-mirror gitignore 且不离机;case/skill 层只存脱敏后文本 |
| D1 良性错误淹没 L3 | 中(成本+噪声) | REFUSE 率作为 S2 读数;detector 加白名单(err_class 分层)在 S2 数据上做,不预优化 |
| D2 标记词表召回/精度未知 | 中 | S2 抽样人工标注一批 user 消息校准;不追求完备,漏的教训会以 D1 重现 |
| skill 生效归因谬误(fingerprint 消失≠skill 功劳) | 中 | curator 只提议不执行;recurrence 只排序不奖惩(§6.L5) |
| 操作员 review 带宽(真瓶颈) | 高 | 每轮 candidate ≤8(KMEM 预算语义);console 一屏一条 delta+证据 |
| 多机(transcript 只在本机) | 低 | 明确 scope=本机;不做同步 |

开放问题(unpark 时先答):
1. S2 的 judge family 选型(需两家不同供应商,且都能走 cassette);
2. subagent 事件是否降权(667 个文件里 workflow 型 agent 的失败可能系统性偏机械);
3. D3 命令头之外,是否加「文件路径 n-gram」维度抓重复 procedure(先看 S2 的 D3 转化率);
4. compiled skill 的 scope 判定(个人级 vs 项目级)——从 case 的 project 分布推,还是让操作员选。

---

## 附录:本次验证产物索引

| 产物 | 位置 |
|---|---|
| S1 扫描脚本(双方言,可复现) | `scripts/skill_s1_scan.py` |
| S1 全库数据 | `.run/skill-s1/summary.json`(per-project)、`.run/skill-s1/sessions.jsonl`(per-session) |
| KMEM parser/discovery 源码(反编译) | `~/source/crack/nowledge-test/unpack/recovered/nowledge_graph_server/{tools/parsers,services/discovery}/` |
| KMEM skill 系统调研(prompt 原文) | `~/source/crack/nowledge-test/research/kmem-skill-learning-research.md` |
| KMEM 抽取管线调研 | `~/source/crack/nowledge-test/research/nowledge-mem-extraction-pipeline-research.md` |
