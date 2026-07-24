# Ask = Vault Agent — 产品设计（A0 已签）

> **状态：A0 已签（2026-07-24）。** 产品定位、工具边界、迁移顺序与三项拍板成立；**不再重开**「仅主张开关 / body 阶段 / 同步 HTTP 是否可做 A1 内部」讨论。  
>  
> **签 A0 ≠ 直接改产品代码。** 正确开工动作：**先写 A1a Candidate Spec**（`model.ask_tool_protocol` 等 surface），把下文 **§5 Candidate 验收契约** 写入该 Spec 的 hard gates；A1b 编码前必须先写死 deadline 与 transcript 契约。  
>  
> **废案（生产主路径）：** 关键词/正则穷举用户说法再走固定流水线（含扩展 `intent.rs` 词表）。  
> **允许：** 检索系统**内部** query planning / 权重调整——不是产品顶层路由。  
>  
> **对标机制：** Claude Code / Codex 类 tool loop；产品纪律对照同类记忆产品的公开行为（对标研究为**本地文档，不入库**）。  
> **不照搬：** 通用办公 Skills、削弱 claim 门槛、双导航、静默写 vault。  
>  
> **关联：** 对标论据见本地研究文档（不入库；阶段表以**本文**为准）。

---

## 0. 一句话产品定义

**Ask 不是证据搜索框，而是住在 vault 里的 agent。**

用户用自然语言交付**任务**；agent 在 **tool loop** 中决定调用哪些工具、读哪一层、何时回答或追问。  
**同一 runtime 自然退化为 0 / 1 / N 次工具调用**（元问题 0 步、主张问答常 1 步、找资料/深读多步）——**不**让用户选择内部 ontology（不做「仅主张」开关）。

| 旧定位（过窄） | 新定位 |
|----------------|--------|
| US5：问问题 → 强制带引用的学术短文 | **Vault Agent**：找资料、答主张、读原文、解释能力、多轮收束 |
| 一次 bag-of-evidence + 一次 completion | **模型 → tool_calls? → 执行 → 观察 →（循环）→ 交付** |
| 只见 claim/card/unit 切片 | **源可回读 + claim 可核查**；工具暴露用户可理解的对象与动作 |
| 系统 prompt 逼「ONLY evidence + 必须 cite」 | **政策随工具结果变化**；引用是回执，不是唯一合法文体 |

---

## 1. 对标什么 / 不对标什么

### 1.1 成熟 agent 的硬机制（必须）

```
User message + 服务端 session transcript
        │
        ▼
┌─ Agent Runtime ─────────────────────────────────────┐
│  system: role + policy + tool catalog                 │
│  loop: model ⇄ tool_calls → executor → results        │
│  stop: final | need_user | max_rounds | timeout |     │
│        tool_error | refusal                           │
│  budget: **agent 总 deadline**（权威）· rounds ·      │
│          remaining per call · per-result cap          │
│  side-effect: read_only 默认可跑；write/external 需确认 │
└───────────────────────────────────────────────────────┘
```

| 原则 | 含义 |
|------|------|
| **模型选工具** | 生产主路径无正则 intent |
| **多步闭环** | 搜完可再读、可换 query；单次 completion 只是 0/1 步退化 |
| **工具是真相入口** | 没调工具的事实不得装作「全库结论」；coverage 由 runtime 计算 |
| **服务端 transcript** | 完整审计轨迹权威；模型上下文是其**投影**（有上限） |
| **总 deadline 安全** | 权威预算支配每轮；HTTP guard 后不得开新轮/写迟到 turn |
| **答案优先的 trail** | 默认紧凑进度；完整 args/result 按需展开 |
| **可测可回放** | fixture/cassette；evolution **一次 Candidate 一个 change surface** |

### 1.2 同类产品的纪律（吸收；对标研究本地不入库）

详见本地对标研究。要点：Source 保真、融合检索、轻问答与长任务同底座不同交互重量、写操作确认、进度摘要。

### 1.3 OVP 必须保留

- claim strength / durable vs caveated  
- `accepted_without_quote == 0` 等硬门槛  
- claim → source/unit 可验证链  
- Candidate Spec + cassette；prompt | parser | **model** | **runtime** | gate **单 surface**  
- 静态发布与可重建派生  

---

## 2. 用户任务（能力覆盖，不是说法词表）

| ID | 任务 | 成功标准 |
|----|------|----------|
| **T1 找资料** | 定位文章/源 | 可打开 Library 引用，或诚实 miss + 追问 |
| **T2 主张问答** | 知识库信什么 | 可点开 claim/unit + verifier |
| **T3 原文深挖** | 原文怎么说 | A2 起 body/chunk；可回链源 |
| **T4 探索** | 开放讨论 | 允许不确定；不强制论文体 |
| **T5 元能力** | Ask 能做什么 | 不查 vault 冒充能力 |
| **T6 多轮** | 补线索 / 继续搜 | 同一服务端 session 续跑 |

评测可穷举说法；**禁止**写回 production pattern。

---

## 3. 架构

### 3.1 Agent loop

```
POST /api/ask  { question, chat?, idempotency_key? }
        │
        ▼
  加载服务端 session（锁/版本）+ 剩余 deadline
        │
        ▼
┌──────────────────┐
│ tool-capable     │◄──────────────────────────┐
│ model（剩余时间） │                           │
└────────┬─────────┘                           │
         │ tool_calls?                         │
    yes  │          no → final / need_user /   │
         │               refusal               │
         ▼                                     │
  executor（剩余时间 · result cap · capability）│
         │                                     │
         ▼                                     │
  tool results → 原子 append transcript ───────┘
  （剩余预算不足 → 不再开下一轮）
         │
         ▼
  verify · coverage（runtime 计算）· export md
```

**硬规则：**

1. 生产主路径 **没有** 关键词 intent 路由。  
2. 工具 schema 注册；模型 **structured tool calls**（见 §5.1 A1a 线协议）。  
3. Transcript **服务端权威**（见 §5.2 持久化契约）。  
4. **Agent 总 deadline 是权威预算**（见 §5.2）；`max_rounds` 默认 ≈ 6 为**辅**约束。  
5. Claim/card/unit 引用跑 verifier；源引用必须可打开。  
6. Coverage **由 runtime 根据实际工具执行计算**，禁止模型自报「全库完成」。

### 3.2 工具目录（用户对象 × 动作）

| Tool | 输入（要点） | 返回（要点） |
|------|----------------|--------------|
| `search_sources` | `query`, `limit` | source id、title/path/url、summary、match_reason |
| `get_source` | `source_id` | metadata、打开引用、可用读能力 |
| `search_source_chunks` | `source_id`, `query`, `limit` | passage、chunk 索引、score |
| `read_source_body` | `source_id`, **`cursor`**, `limit` | 文本、`truncated`, `next_cursor`（见下） |
| `search_claims` | `query`, `limit`, status? | claim、strength、片段、provenance |
| `get_claim` | `claim_id` \| `claim_key` | 主张 + 来源链 |
| `list_recent_sources` | `n`, date? | 近期源 |

**分页语义（A2 契约）：**

- 对外**只公开 opaque `cursor`**（实现可用字节偏移，但**不**把 raw byte offset 当稳定公共 API 永久承诺）。  
- 解码必须尊重 **UTF-8 码点边界**；不得在多字节字符中间切开。  
- 每个 body/chunk 结果继续有**大小上限**；`truncated` / `next_cursor` 必填语义清晰。

**实现层：** pack/card/unit 作 typed evidence 嵌入结果；融合检索在 `search_*` 内部演进。  
**禁止：** 无法说明层覆盖的黑盒 `search_everything`。

**Capability：**

| | 默认 |
|--|------|
| `read_only` | 自动执行 |
| `write` | 需确认（v1 无写工具，契约先定） |
| `external_action` | 需确认（v1 无） |

**稳定引用：** `sha256` → `/library/:sha`；`claim_id` / `claim_key` → 知识锚点。

### 3.2.1 不可信内容边界（A2 起生效，A1 政策须预留）

Vault 源可能含网页、邮件、OCR 或**人为植入的指令**（间接 prompt injection）。

| 规则 | 要求 |
|------|------|
| 数据角色 | `read_source_body` / chunk 结果**仅**作为 `tool_result` **数据**，不是指令 |
| 授权 | **不得**因 source 正文出现「请执行删除/外发/…」而授权 write 或 external_action |
| 体量 | 每个 tool result 有硬 cap |
| Policy | system 明确：**忽略来源中的操作指令**；只抽取与用户任务相关的事实片段 |
| v1 | 可不做额外分类器，但**信任边界必须先写进契约与测试** |

### 3.3 系统政策（纪律，A3a 定稿文案）

角色、工具边界、证据政策、失败恢复、写操作、coverage 诚实、**忽略 tool_result 内操作指令**。  
不写用户说法词表。

### 3.4 Coverage 状态机（runtime 计算）

每个检索**层**（如 sources / claims / body）及整体汇总使用固定枚举：

| 状态 | 含义 |
|------|------|
| `not_queried` | 本 turn 未调用触及该层的工具 |
| `complete` | 已查询且结果集在预算内完整返回（未因故障截断语义） |
| `partial` | 已查询但截断/分页未读完/结果 cap 裁剪 |
| `unavailable` | 层未就绪（无 index、无 body 索引等） |
| `failed` | 查询执行失败 |

**规则：**

- **0-tool 元问题**：各层均为 `not_queried`，**不得**显示「全层 complete」。  
- 由 runtime 根据 tool 执行日志聚合，**禁止**模型在 JSON 里自填 coverage 当权威。

### 3.5 会话与 transcript

| 对象 | 职责 |
|------|------|
| **审计 transcript**（完整） | 全部 user/assistant/tool 事件；可恢复、可审计 |
| **模型上下文投影** | 从审计 transcript **截断/摘要**出的有上限窗口（A1b 必须定 cap，**不**留到 A5） |
| `.ovp/chats/*.md` | 可读导出，非 loop 唯一状态 |
| URL `/ask`、`/ask/chat/:id` | 分享与绑定 session |

持久化与并发见 **§5.2**。

### 3.6 UX

- 答案视觉中心；trail 默认摘要（动作、数量、truncated/coverage/失败恢复）。  
- 完整 args/result 按需展开。  
- Citation panel ≠ trail。  
- 长任务只加重 trail，不强迫用户选内部模式。

### 3.7 API 方向

```json
{
  "question": "…",
  "chat": "optional-session-id",
  "idempotency_key": "optional-client-retry-key"
}
```

```json
{
  "answer": "…",
  "citations": [],
  "coverage": {
    "sources": "complete",
    "claims": "not_queried",
    "body": "partial",
    "notes": []
  },
  "tool_trace": [
    { "tool": "search_sources", "summary": "3 hits", "ok": true }
  ],
  "chat": "…",
  "turn_id": "…",
  "stopped_reason": "final" | "need_user" | "max_rounds" | "timeout" | "tool_error" | "refusal"
}
```

**进度事件（A3 对外最低，可先于 token stream）：**  
`started` → `tool_started` → `tool_finished` → `final` | `error`

用户 cancel / resume / checkpoint → **A5**（不等于 A1b 可以没有 deadline 安全）。

---

## 4. 与现有代码 / 迁移

| 现状 | 处置 |
|------|------|
| `ModelMessage` 仅 User/Assistant 文本；`ModelReply` 仅 text；空 text 在 anthropic 路径可当 decode 失败 | **A1a** 必须改：tool-only 合法（见 §5.1） |
| 单次模型超时 ~180s + HTTP guard ~210s；504 **不取消**后台且可能仍写 chat | **A1b** 总 deadline 接管；guard 后禁止新轮与迟到写入当前 turn |
| 客户端 history ≤32 + append Markdown | 升级为服务端 structured transcript + 幂等/并发契约 |
| `GET /api/source/:sha` + 200 KiB | A2 抽成 `read_source_body`（opaque cursor） |
| `intent.rs` 词表 | 停止扩展；flag 达标后删主路径 |
| `evolution/components.json` 无 ask tool/runtime 组件 | **A1a Candidate 验证前** registry bootstrap：`model.ask_tool_protocol`、`runtime.ask_agent`（及后续 prompt.ask_agent_policy 等） |

### 迁移顺序

```
1. evolution registry bootstrap（组件 id）
2. A1a Candidate Spec → 实现（flag；旧路径默认）
3. A1b Candidate Spec（含 deadline + transcript 契约）→ 实现
4. A2 工具 + 不可信边界测试
5. A3a–A3d 分 surface 交付；最低事件流
6. paired eval → 切默认 → 短期回退 → 删 intent 主路径
```

---

## 5. Phase 与 Candidate：验收契约（签 A0 后的硬门槛）

> **Phase = 产品 milestone，不等于一个 Candidate 或一个 PR。**  
> 每个 Candidate **恰好一个** change surface（prompt | parser | runtime | gate | model）。  
> 下列条款必须进入**相应** Candidate Spec 的 hard gates / 测试清单。

### 5.1 A1a — Tool-capable 模型协议（surface: **model**）

**目标组件 id（建议）：** `model.ask_tool_protocol`  
**现状锚点：** `crates/ovp-llm`（`request.rs` / `reply.rs` / `anthropic.rs`）；当前仅文本，且 anthropic 解析在 content 无 text 时倾向失败——**与 tool-only 冲突**，必须修正。

#### Content blocks（必完整）

| 要求 | 说明 |
|------|------|
| **tool_use-only 成功** | 响应仅含 `tool_use`、无 `text` → **成功**，不是「无文本」decode 错误 |
| **顺序保留** | text / tool_use 块保持 provider **原始顺序** |
| **多 tool calls** | 同一 assistant turn 可含多个 tool_use；各自 **稳定 tool call id** |
| **tool_result 邻接** | 对应结果 必须紧邻该 assistant turn，位于**下一条 user** content 的**最前**（Anthropic Messages 硬约束） |
| **is_error** | tool_result 支持错误标记，供模型恢复 |
| **Stop reason** | 至少映射 `ToolUse`、`EndTurn`/`final`、`Refusal`、`MaxTokens`；**未知不得伪装 final** |
| **Cassette** | 新增 optional tool 字段后：旧 cassette **兼容**（缺省=无 tools）；新字段不破坏 request key 稳定性策略写进 Spec |

**参考：** [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)、[handle tool calls / result 顺序](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)。

#### 回放确定性与协议边界（2026-07-24 代码实证补强；已写入 `ask_tool_protocol-v1` guardrails）

| 条款 | 要求 | 依据 |
|------|------|------|
| **tool_call_id 确定性** | id 由 provider 签发，**原样**穿透 reply → transcript → 下一请求的 tool_result；runtime **永不**自造 id——否则录制会话回放时第二轮请求体与录制不一致，整条 cassette 链断 | `ovp-llm/src/key.rs` 回放机制 |
| **request_key 策略** | tools 以 **(工具名, 协议版本)** 进 key，**绝不**放完整 JSON schema——否则每次 schema 微调作废全部 cassette；schema 演进走 `model.ask_tool_protocol` 版本号 | theme-pages 曾因 display label 进 prompt 导致 key 漂移 |
| **MaxTokens 截断 tool_use** | stop=MaxTokens 且 tool_use 块被截断 → 该调用**无效**：禁止执行，按协议错误处理（提额重试是调用方决策） | 截断 JSON 可执行 = 半条命令落库 |
| **全量回填** | 同一 assistant turn 的**所有** tool_use id 必须在下一 user 消息**最前**全部得到 tool_result（失败的用 `is_error`）；部分回填 = 协议违规（类型上不可表示或 builder 拒绝） | Anthropic API 缺一个 result 直接 4xx；executor 部分失败最易漏 |

#### A1a 最低测试闸门

- 纯文本往返兼容（旧路径/旧 cassette）  
- tool-only 响应  
- text + 多个 tool_use（顺序与 id）  
- tool_result 成功与 `is_error`  
- stop reason：ToolUse / Refusal / 未知不伪装 final  
- 旧 cassette replay 不炸  

**开工：** 写 A1a Candidate Spec → validate → 实现；**不要**在同一 Candidate 里做 runtime loop。

---

### 5.2 A1b — Agent runtime（surface: **runtime**）

**目标组件 id（建议）：** `runtime.ask_agent`  
**编码前必须先把本小节进 Spec**（deadline + transcript），再写 loop。

#### 总 deadline（权威预算）

现状：单次模型 ~180s、API guard ~210s；**guard 超时不取消后台**，仍可能写 chat。对 agent 多轮这是安全漏洞。

| 规则 | 要求 |
|------|------|
| **总 deadline** | 每个 ask turn 有单一 **agent_deadline**（权威） |
| **剩余时间** | 每次模型调用、每次工具调用只拿 **remaining**；超时参数取 min(配置, remaining) |
| **不再开轮** | remaining 不足以安全完成「下一模型调用或下一工具」时 **停止**，`stopped_reason=timeout` |
| **HTTP guard 后** | **禁止**再开下一轮 tool/model；**禁止**把迟到结果**悄悄写入当前 turn**（可记审计 side log，不得当成功交付） |
| **与 A5 分界** | A5 = 用户 cancel/resume/checkpoint；**不是**「A1b 可以没有 deadline」 |

`max_rounds ≈ 6` 为辅助；**不能**只靠它替代 wall-clock 总预算。

**追加契约（2026-07-24 竞品生产实证；细节在本地研究，不入库）：**

| 条款 | 要求 | 依据 |
|------|------|------|
| **Invalid-arguments 熔断** | 同一工具**连续 N 次（建议 2）参数校验失败** → 停止重试该工具，`stopped_reason=tool_error` 或换策略；参数错误作为结构化 `is_error` result 喂回模型一次即可 | 竞品生产 agent 实测：同类参数错误连续重试 3+ 次无熔断，任务失败但成本照烧 |
| **逐调用用量记账** | transcript 每条模型调用记录 `{in_tokens, out_tokens, src, scope}`（append-only），turn 汇总进响应 | 竞品同型 append-only 用量账本；其后台单次调用可达数十万 input tokens——无预算 loop 的实价 |

#### Transcript 持久化与并发

| 契约 | 要求 |
|------|------|
| **Schema** | 带 `schema` / `version` 的 structured 文件或 store |
| **ID** | `session_id`、`turn_id`、`tool_call_id`、`result_id` 全局可关联 |
| **幂等** | 请求 `idempotency_key`（或等价）：重试**不得**产生两次 user turn |
| **并发** | 同 session **串行化**或乐观版本（CAS）；双标签页不得交错写坏轨迹 |
| **原子落盘** | 单 turn 提交原子；崩溃可恢复到上一完整 turn |
| **双视图** | **完整审计 transcript** vs **有上限的模型上下文投影**（投影 cap A1b 必须定义：如 max messages / max chars / 优先保留最近 tool 对） |

Checkpoint 用户体验可 A5；**上下文上限与原子性不能留到 A5**。

#### A1b 最低测试闸门

- 0 / 1 / 2 tool 成功路径  
- 未知工具名、重复 tool 调用  
- **总超时**（模拟 short deadline）  
- max rounds  
- 同 session 并发提交  
- 崩溃恢复（kill 在 tool 后、final 前）  
- 重复 idempotency_key 不双写  

---

### 5.3 A2 — 工具与不可信边界（surface: 多为 **runtime** 工具实现；可拆多个 Candidate）

- §3.2 工具集；opaque cursor；UTF-8 安全分页  
- coverage 五态由 runtime 填  
- **恶意 source 正文**不得驱动 write/external；policy + 测试  
- 单索引故障 → 该层 `failed`/`unavailable`，整体诚实 partial  
- **与 ovp-mcp 工具目录统一（2026-07-24 补）**：`search_sources / get_claim / read_source_body …` 与已 ship 的 MCP 工具面（find/search/ask/claim/theme_page）高度重叠——**一个工具注册表，两个投影**（内部 agent loop + MCP server），数据访问代码共享；否则两套证据表面必然漂移。ask agent 化后 MCP 的 `ask` 变成"工具后面套 agent"，**嵌套 deadline 传播**在 A1b 契约留接口  
- **claim_key 为规范键（2026-07-24 补）**：`get_claim` 以 `claim_key` 规范、`claim_id` 仅 legacy；歧义 id 一律降级不链接——与已 ship 的 ask/v3 citation keys（#351 codex P1 教训）对齐  

#### A2 最低测试闸门

- CJK 分页不截断半字符  
- truncated + next_cursor  
- 缺失 source  
- 恶意 source 指令不触发副作用  
- 单层故障 + 诚实 coverage  

---

### 5.4 A3 — 产品 milestone，**拆多个 Candidate**

| 子阶段 | Surface（建议） | 内容 |
|--------|-----------------|------|
| **A3a** | prompt | agent policy 文案（无说法词表） |
| **A3b** | runtime | coverage/citation 组装、进度事件 API |
| **A3c** | （UI / portal） | 紧凑 trail + 答案中心 |
| **A3d** | runtime / gate | 默认 flag 切换、rollout、回滚 |

#### A3 最低测试闸门

- 事件顺序 `started → tool_* → final/error`  
- 0-tool 元问题 + coverage 全 `not_queried`  
- partial 展示  
- 旧路径回退  
- 默认切换与回滚  

---

### 5.5 A4 / A5（摘要）

| | |
|--|--|
| **A4** | 页码/行号/OCR/表格/revision/stale；引用窗口 body 重放 |
| **A5** | token stream、用户 cancel/resume、checkpoint；**不**替代 A1b deadline |

---

## 6. 成功指标（产品）

| 指标 | 门槛 |
|------|------|
| 找资料 | 可打开源或清晰 miss |
| 元能力 | 不误搜 vault 当能力说明 |
| 主张问答 | verifier 与可点引用不回退 |
| 快问答退化 | 不强制多轮 |
| Source 深读 | A2 起分页 body |
| Coverage | 五态正确；0-tool 不假 complete |
| Deadline | 超时无迟到 turn 污染 |
| 幂等/并发 | 无双写 turn |
| 注入边界 | source 正文不能提权 |
| Trail | 默认无巨量 JSON |

---

## 7. 明确不做什么

1. ❌ 生产关键词 intent  
2. ❌ 「仅主张」用户开关  
3. ❌ pack/card/unit 平铺为必学 search 工具  
4. ❌ A4 才首次 body  
5. ❌ 先删旧路径再验证  
6. ❌ 一个 Phase = 一个巨型 PR（model+runtime+prompt+UI）  
7. ❌ tool-only 当 decode 失败  
8. ❌ 无总 deadline 的多轮 loop  
9. ❌ 仅靠客户端 history + append md 当审计真相  
10. ❌ 静默写 vault；source 正文授权副作用  
11. ❌ 公开永久双分页语义（offset 与 cursor 并列承诺）  
12. ❌ 削弱 claim 门槛  

---

## 8. 已拍板（不再讨论）

| # | 决定 |
|---|------|
| 1 | 默认 **100% agent runtime**；无「仅主张」开关；0/1/N 退化 |
| 2 | **body/chunk 在 A2**；A4 = 高保真深读 |
| 3 | **A1 同步 loop 仅内部**；**A3 最低状态流**；stream/cancel/resume → A5 |
| 4 | `max_rounds ≈ 6` **辅**；**总 deadline 权威**（A1b 契约） |

---

## 9. 文档权威关系

| 文档 | 角色 |
|------|------|
| **本文** | **A0 唯一开工权威**（阶段、契约、拍板） |
| 对标研究（本地文档，不入库） | 历史建议；以本文为准 |
| `intent.rs` | 过渡回退；禁止扩展词表 |

---

## 10. 总结与下一步

| | |
|--|--|
| **A0** | **已签** |
| **产品** | Vault Agent，tool loop，0/1/N |
| **下一动作** | **A1a Candidate Spec**（非直接大改 ask 产品路径） |
| **A1b 编码前** | Spec 写死 **总 deadline + transcript 持久化/幂等/并发/投影 cap** |
| **A2 前** | 不可信 tool_result 边界 + opaque cursor |
| **A3** | 拆 A3a–A3d；Phase ≠ 单 PR |

**最终判定（与第二轮评审一致）：**  
A0 通过，可签；**从 A1a Candidate Spec 开工。** A1b 编码前必须先写死 deadline 与 transcript 契约。
