# Storage & Retrieval Read-Model(存储与检索规模化设计)

状态:方案定稿,分阶段落地。T0(耐久性 + CJK 召回修复)已随本文档同 PR 落地。

关联:[ask-vault-agent.md](ask-vault-agent.md)(A0 已签——tool loop、渐进披露、
coverage 语义**不动**,本设计只替换工具下方的存储与检索层)。

---

## 1. 问题:不是 JSON 格式,是"每次把整个世界重读一遍"的运行模型

实测现状(真实 vault,N ≈ 1,470 sources):

- `.ovp/index/evidence.json` **51.4 MB** 单 JSON 文档(19K cards + 40K units),
  `index.json` 5.4 MB。整读、整写、整 parse。
- server 缓存命中路径 `freshen()` 返回**整个 model 的深拷贝**;`/api/source/:sha`
  每请求 clone 51 MB EvidenceModel + 冷读 11.3 MB `cards_zh.json`。
- crystal ledger 每请求全量读 + parse + fold,无缓存;GET 单线程串行 dispatch。
- 全 workspace 无任何持久索引结构(无 sqlite / tantivy / 倒排 / ANN)。
- agent 检索路径中文查询不分词(整句 = 一个 substring term),召回静默塌陷。

断点次序(按规模外推):`/api/source` ~5–10x 死;ledger 端点 ~10–20x;100x 时
evidence.json ≈ 5.1 GB、console HTML ≈ 440 MB,进程 OOM,不可行。

## 2. 目标规模与验收 gate

规模定义:sources 1.5k(1x)→ 15k → 147k(100x);检索行(cards+units)
60k → 600k → 6M;dense 卡片 19k → 193k → 1.9M。

| Gate | 要求 | 实测锚点(M2 Pro / 32GB,方法见 §5) |
|---|---|---|
| 词法检索 warm p95 | 100k 行 < 50 ms;1M 行 < 150 ms | FTS5 @600k = 2.7 ms;@6M = 31 ms |
| 点查 source/claim p95 | < 10 ms | < 0.01 ms |
| 增量投影(100 新源) | < 2 s(不含 embedding) | 插入吞吐 ~20k 行/s |
| 灾备全量重建 | 100x < 15 min | 6M 行 FTS 全建 6.4 min |
| ANN Recall@20 vs 精确 | ≥ 0.95 | (引入 ANN 时测) |
| 中文 Recall@20 / nDCG | 显著优于 substring 基线 | 需先建中英混合 qrels |
| 崩溃语义 | 任何崩溃点重启后只允许 last-good 或显式 degraded | 禁止静默空结果 |

1000x(1.47M sources)不在本设计承诺内:权威层磁盘就是 TB 级,先于软件成为
硬件与分层检索问题;本设计把它诚实暴露,而不是被大 JSON 提前杀死。

## 3. 架构:文件 ledger 唯一权威,SQLite 事务化 read-model,索引是有版本的投影

```
Notes / packs / JSONL ledgers      ← 权威层,可同步、可审计(不变,A0 契约)
        │
        ▼
增量 projector(cursor + 输入 hash,SQLite 事务内推进)
        │
        ├─► read-model.sqlite      关系表 + 游标 + 向量 BLOB(替代 index/evidence.json)
        ├─► FTS5 四表              sources / evidence / claims / chunks(预分词)
        └─► ANN(触发条件驱动)     USearch base+delta,可随时重建
        │
        ▼
ProjectionSnapshot(有界、一致、带 epoch/coverage)
        │
        ▼
A0 tool loop(9 工具签名不变,RRF/citation verifier 不变)
```

拍板要点:

1. **ledger 不迁库。** notes/packs/ledgers 仍是唯一权威;SQLite 是可删可重建的
   投影。增量投影 = 读 cursor → 校验 ledger anchor → seek 旧 EOF → parse 新增行
   → upsert → 同事务推进 cursor → commit。commit 前崩溃则完整重放。
2. **无 ORM。** rusqlite + 手写 SQL。桌面单用户、性能敏感,ORM 只添层。
3. **派生库放本机 cache 目录**(`~/Library/Caches/ovp/<vault-id>/` 类),不进
   vault 同步——SQLite WAL 的 `.db/-wal/-shm` 不能走云盘;vault 里只留权威层。
4. **FTS5 是基线而非过渡。** 实测 6M 行 p95 31 ms,超验收线 5 倍余量。中文用
   版本化 `tokenize_for_fts`:jieba-rs 主 lane + **保序** bigram 兜底 lane,索引端
   与查询端同 analyzer version。注意不能复用现有 `tokenize_for_search`(BTreeSet
   去重丢词频词序,会毁 BM25)。tantivy 是质量升级路径,由中文 qrels 触发,不排期。
5. **向量分层、卡片级、先精确后 ANN。** 只 embed cards/sections(不 embed 全部
   units——100x 时全量 units 是 22.5 GiB 向量,卡片级 256 维只要 2 GB);向量存
   SQLite BLOB(256 维 MRL,f32),检索先用精确扫描(实测 193k×256d ≈ 6 ms,
   **精确无损**);卡片数 > ~50 万才引入 USearch base+delta(delta 精排实测
   20k×1024d = 1.5 ms)。ANN 永远是可重建 sidecar,不是 source of truth。
6. **图层就是 SQLite 边表**((src,type,dst) 双向索引),PageRank/社区批算回写。
   当前查询模式(claim→source→unit 闭包、邻域)不需要图数据库。
7. **搜索热路径无 LLM。** 每个工具调用内部执行有界 hybrid(FTS top-N + 向量
   top-N + RRF + 精确重排 + 去重),LLM rerank/意图分类只进显式 deep 模式。
8. **generation 切换。** 破坏性 schema 升级构建 `read-model.gN.sqlite`,校验
   (quick_check + 抽样 parity)后原子切 `CURRENT` 指针,旧代延迟清理。
9. **结果必须带 coverage。** 每次检索返回 `projection_epoch` / `indexed_through` /
   `degraded_lanes`,零命中可区分"真没有"与"索引没覆盖"(A0 coverage 语义的延伸)。

业界依据(公开来源):tool-loop 优于一次性 RAG 但 grep-only 不可扩展,共识是
"把检索系统作为工具暴露给 agent"(Augment/SWE-bench 系;Cursor 语义索引+grep
双工具 +12.5% 检索精度);Zed 曾建成本地向量索引又于 2025-09 删除转 agentic,
支持"词法索引先行、向量按实测需要引入"的排序;Turso 弃 FTS5 重建于 tantivy 之上
说明 FTS5 的天花板存在,但在我们的延迟预算内未触及。

## 4. 明确不做什么

- **PGLite**:WASM 单连接 Postgres,Rust 侧无成熟嵌入,排除。
- **图数据库**(含 Kuzu 系:上游 2025-10 归档):SQLite 边表足够。
- **Qdrant Edge**:截至 2026-08 仍 Beta、无已知生产用户;只做影子验证,GA 且
  crash-fuzz 通过前不承载任何唯一投影。
- **LanceDB**:Arrow/DataFusion/tokio 依赖树对当前同步 workspace 过重;小写放大
  fragment 需要 optimize 运维;暂不引入。
- **sqlite-vec ANN**:DiskANN 仅 alpha(2026-03 起),按暴力扫规划即可,正好够用。
- **全量 unit 级 dense embedding**:容量上最大的一刀,见 §3.5。

## 5. 实测方法与估算

基准脚本(本机可复跑,产物 gitignored):`.run/storage-bench/{corpus.py,bench_fts.py,bench_vec.py}`。
语料模型:~330 B/行、中英各半,FTS 侧 CJK 预分词为保序 overlapping bigram,
contentless FTS5 + 原文 external-content 表,WAL,10k 行/事务。

| | 1x | 10x | 100x |
|---|---|---|---|
| read-model.sqlite | ~45 MB | ~450 MB | 4–6 GB(实测 654 B/行) |
| 向量(卡片级 256d f32) | 20 MB | 200 MB | 2 GB(+ANN ~0.6 GB) |
| 运行时 RSS(server) | <150 MB | <300 MB | 0.5–2.5 GB(向量 mmap) |
| 冷启动至首查询 | ~10 ms | ~10 ms | ~10–30 ms |
| 词法 warm p50/p95 | 0.04/0.2 ms | 0.2/2.7 ms | 1.9/31 ms |
| 向量检索 | 精确 ~1 ms | 精确 ~6 ms | ANN 1–5 ms + delta 1.5 ms |
| 每日增量投影(100 源) | <1 s | <1 s | ~1–2 s |
| 灾备全量重建 | ~3 s | ~37 s | ~6.4 min |
| 【对照】现行 JSON 方案 | 正常 | /api/source 濒死 | OOM |

已知红区:FTS5 单 term + `ORDER BY rank`(bm25)在 6M 行 p95 42 ms——常见词
posting 全量打分是 FTS5 的已知病灶,是切 tantivy 的观测指标之一。
本机(磁盘余量 ~11 GiB)容不下 100x 的**权威层**(~230 GB);cassettes
(261 MB / 31k 文件)应最先加保留策略。

## 6. 落地阶段

1. **T0(已落地,本 PR)**:agent 检索路径 CJK bigram 分词(`tokenize_search_terms`
   镜像 `tokenize_for_search` 粒度,`search_source_chunks` 对齐,term 上限 8→16);
   `write_index`/`write_evidence` 原子写(tmp+rename);`append_jsonl` fsync。
2. **server 卫生**(不换引擎,买回 10–30x):`freshen` 返回 `Arc<T>`;ledger fold
   与 `cards_zh.json` 入 mtime 缓存;VaultTools 复用 server 缓存;GET 线程池;
   console HTML 行数上限。
3. **SQLite shadow**:`ovp-index` 内新增 store/projector/snapshot/cursor 模块,
   双写并做 row-count/抽样/API parity 比对;同步建立中英混合 qrels。
4. **切换**:`OVP_INDEX_BACKEND=json|sqlite|shadow` 逐端点切;`/api/model` 改
   summary+分页;console/publish 流式化。
5. **检索增强**:chunks 表 + 四张 FTS 面;卡片级向量入库(BLOB)+ 精确扫描 lane
   接入既有 RRF 挂点(`retrieve.rs`,A0 预留的插入点)。
6. **收尾**:稳定两个发布周期后停止默认生成 `index.json`/`evidence.json`,保留
   `--export-json` 调试出口。
7. **触发条件驱动(非日程)**:卡片 >50 万 → USearch;中文 qrels 不达标或单 term
   尾延迟越线 → tantivy;Qdrant Edge GA 且通过 crash-fuzz → 重估合并 lexical+dense。
