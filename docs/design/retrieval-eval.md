# Retrieval Eval(检索评测:qrels + trace)

状态:R0 方案。填 [storage-read-model.md](storage-read-model.md) §2 里
"中文 Recall@20 / nDCG 需先建中英混合 qrels" 这个空位。

关联:[ask-vault-agent.md](ask-vault-agent.md)(A0 契约不动;评测对象是当前
`ask_vault_tools-v5` 工具面,不是旧 `ovp-rag` 的 concept-slug Recall@K——那套
只覆盖 evergreen 概念页,与 Ask 生产路径零交集)。

---

## 1. 为什么是第一优先级

后续每一项检索改造(chunks 表、query plan、reranker、dense)都需要回答
"改了之后哪个 cohort 变好、哪个变坏"。没有 qrels,所有"提升"不可证伪;
有了 qrels,才允许按 process rule(无价值实验不加持久层)推进。

现状可用的评测资产只有 `.run/a3d-eval/` 的 8 问 paired eval(agent vs
legacy,一次性验收默认翻转用),既无分级相关性也无 cohort 分桶。

## 2. 数据:gold 50–80 起步,不是 300

- **来源**:真实 ask-sessions transcript(`.ovp/ask-sessions/*.jsonl`,
  已有 40+ 会话)经确定性摘要(digest)→ LLM 起草(silver)→ 人工晋级(gold)。
  草稿由便宜模型批量产出,`confidence: needs_review` 强制;晋级 gold 必须
  operator 亲验 relevant 对象确实承载/不承载答案。
- **规模纪律**:第一版 gold 50–80 条。300 条是数周标注量,单人 dogfood
  产品撑不起,烂尾风险 > 统计增益。桶结构保留、每桶缩量,桶内不足 5 条时
  该桶指标只报不判。
- 流水在 `.run/retrieval-eval/`(gitignored);晋级后的 gold qrels 入库到
  `crates/ovp-retrieval-eval/fixtures/`(或先 `docs/eval/qrels/`,定实现时选)。

## 3. qrel 记录 schema

```json
{
  "schema": "ovp.retrieval_eval.qrel/v1",
  "id": "q-001",
  "question": "原问题全文",
  "source_session": "ask-session id(可溯源)",
  "language": "zh | en | mixed",
  "class": "exact | paraphrase | claim_evidence | compare | recent | source_scoped | meta | negative",
  "relevant": [
    {"surface": "source|claim|card|unit|chunk", "id": "…", "grade": 3, "why": "…"}
  ],
  "no_answer": false,
  "confidence": "gold | needs_review"
}
```

分级语义(区分 source recall 与 answer-bearing recall,不可合并):

- **3** = answer-bearing:该对象直接承载答案(unit / chunk / 具体 card);
- **2** = 导航相关:card / claim / section,能引到证据;
- **1** = 主题相关 source:找对了文章,不保证内含答案。

`negative` class + `no_answer: true` 是一等公民:库里没有的问题,正确行为是
带 coverage 声明的 abstain,不是硬答。

## 4. 指标:分桶,不设单一综合分

按 `class × language` 分桶各报:

| 层 | 指标 |
|---|---|
| 候选召回 | Recall@10/@20;answer-bearing(grade 3)Recall@20 单列 |
| 排序 | MRR@10、nDCG@10、Precision@5 |
| Agent 行为 | citation precision(服务端已验)、no-answer accuracy、平均搜索轮数、重复 query 率 |
| 成本 | 每问工具调用数、扫描字节、token |

聚合平均会掩盖"semantic 桶变好、exact-ID 桶退化"这类换血式回归——所以
验收永远看桶,不看总分。

## 5. Trace:扩展现有 transcript,不另造 observability

`agent_transcript.rs` 已有 turn 原子性 / 完整工具原始结果 / token 记账。
评测需要补的只有检索内部事件(只记 ID、rank、参数、latency、
projection generation,**不复制候选正文**——`ToolCalled.content` 已存全量):

```text
retrieval_snapshot_opened / lane_finished / fusion_finished / coverage_evaluated
```

重放约束:同一 projection generation + 同一配置 → 确定性重放。

## 6. 验收门(R0 完成的定义)

1. gold ≥ 50 条,8 桶每桶 ≥ 3 条,negative ≥ 6 条;
2. `retrieval-eval` 命令可对当前 v5 工具面跑出分桶 baseline 并落盘;
3. 任一失败问题能定位到层:候选生成 / 融合 / Agent 行为;
4. 每条 gold 可溯源到真实 session 或标注记录。

## 7. 后续改造的立项条件(用本评测触发,不用先验)

| 改造 | 触发条件 |
|---|---|
| chunks 表 + heading-aware source_map | body-only 桶 Recall@20 低于阈值(storage-read-model §T2 已规划) |
| query plan / 多查询并行 | compare/multi-part 桶 subtask 覆盖不足 |
| reranker | 候选进了 top-50 但 Precision@5 差(precision 问题,非 recall 问题) |
| dense embedding | lexical+fts 之后 paraphrase 桶仍显著 miss |

任何一项在触发条件出现前不立项——900 万页语料的工程先验不按原剂量搬到
1.6k sources 的单机产品上。
