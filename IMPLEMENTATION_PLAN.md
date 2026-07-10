# M32 Level-3 执行计划 — Python 退役 + merge 回 main

> 依据 `docs/stage-m32-python-retirement-and-product-definition.md` §3 exit criteria + §10 execution order。
> 状态基线（2026-07-07 operator review 后刷新）。merge 回 main = Level 3，全部 Stage 完成前不 merge。
> 关键路径 = dogfood 时钟（07-06 重启，≥14 天 → 最早 go/no-go ≈ **2026-07-20**）∥ Stage P 产品原型链。
>
> **Operator 指令（2026-07-07，冻结令）**：到 Stage 5 merge 为止，只允许两类 PR——
> (1) 推进 Stage P 或六条 exit criteria 之一；(2) 修 dogfood 发现的缺陷。
> M34 基底 / M35+M36 review 规则校准 / skill-learning 全部进 post-merge backlog。
> 判据一句话："这推进哪一条？"答不上来就不做。

## Stage P: Criterion #0 — 完整产品原型验收（operator 亲自走通，merge 硬前提）

**Goal**: operator 作为最终用户，从安装开始完整走一遍产品闭环：
`安装（prebuilt）→ pinboard-sync --live → daily（clippings+pinboard+裸书签全消化）→ serve 打开 web portal 看到当天结果`。
走不通则 dogfood 天数满了也不 merge。

**P.1 Prebuilt 安装故事（~1-2 天）**
- [ ] **不接受源码 `cargo install`**（最终用户没有 Rust 环境）。方案：`cargo-dist` 生成
      GitHub Releases prebuilt 二进制 + curl-shell installer + Homebrew formula（tap 仓库）；
      npm wrapper 作为可选第二通道。二进制统一叫 `ovp2`（rename #289 已 merge，
      但 release build 一直没重编——07-07 已重编）。
- [x] README 增加 quick-start（≤10 步：装 → `.env` 配 LLM → 首次 `ovp2 daily` → `ovp2 serve`）。
- [x] 本地验证 installer→`ovp2 --version`（干净 prefix 模拟无 Rust 环境）。
**Status**: **PR #298 待 review**（2026-07-09）——cargo-dist 0.32 + shell installer + brew formula 生成 +
docs/install.md；prebuilt 默认编入 anthropic/pinboard-live/web-fetch-live/github-live。
遗留给 operator：私有仓库使 curl/brew 匿名 404（需 public 化或 token）；tap 仓库需创建；
release.yml 是 tag 触发的 CI 政策新增，随 PR 审。

**P.2 Pinboard 源真实端到端 — ✅ 完成（2026-07-09 live 验证）**
- 首跑事故：operator 的 Pinboard 有 ~50,700 条书签，无限流的首跑物化了全部 50,714 个文件
  （198MB）——当天回滚，催生 **PR #300 首跑护栏**（`--since`/`--max`/>500 中止 + `--yes-all`，
  daily 继承；已 merge）。Pinboard `posts/all` 限速 ≈5 分钟一次。
- 受控重同步 `--max 200` → 两次 daily 完成全链闭合：**书签 → 02-Pinboard → 抓取 enrich →
  01-Raw 摄入（184 条）→ reader packs（含 GitHub 仓库源 27 units/10 cards 实例）→
  ledger/report/index/console**。证据：`.run/p2p3-daily-20260709*.log`。
- 遗留决策（operator）：5 万存量书签是否做分批回填通道，还是只随 `--since` 窗口消化新增。

**P.3 裸书签 / GitHub 源网页抓取 — ✅ 完成（基础设施本已在 trunk，2026-07-09 live 验证过）**
- run1: 200 条裸书签 197 抓取成功（3 个合理失败）；GitHub 140 仓库 139 出 README。
- 小缺陷入 backlog：`text/markdown` content-type 被误判非文本而拒抓（web_fetch）。

**P.4 Web portal 重设计（operator 2026-07-07 定性：必做，当前版不可接受）**

Operator 走查结论：当前 M31 console 是一张平铺大表——无导航、无设置、无概念说明、
pipeline 各步没有入口；观感差于 KMEM，也差于重构前 M28 特意设计过的 console
（左侧导航 7 页 + "Why it matters" 卡片 + 行动按钮 + 双语，截图对比在
`.run/m32-portal-walkthrough/`）。**结论：M31 Rust 重写丢掉了 M28 的信息架构——
先捡回来，再补缺的。不抄 KMEM，但导航 + 设置 + 每个管线步骤一个入口是底线。**

**信息架构 v2（operator 2026-07-08 纠偏：产品思路，不是管线控制台）**——
用户不在乎 pipeline；产品面 = 用户的四件事：数据每天进去 / 能查 / 能看结晶 / 能对话。
管线是水管，收进"系统"页，不占主导航。

**三层内容模型（operator 2026-07-08 确认，用户必须三层都看得到且互链）**：
原文层 = vault md（clippings/pinboard 源文件）→ 记忆层 = 每源 reader pack
（units 带行号摘录 + cards 可读卡片；≈ KMEM 的 memory）→ 结晶层 = 跨源 claims
（durable 为主、caveated 标注；≈ KMEM 的 crystal）。
下钻链：claim → card → unit → 原文行号；反向：md 详情 → 它的记忆 → 引用它的 claims。

```
OVP2 Portal（主导航 = 用户任务 + 三层内容）
├── 今天 Today       当日活动流：今天捕获/摄入/读完/结晶了什么（digest 上屏）；
│                    attention 仅在需要人时出现；系统健康 = 右上角一颗状态灯
├── 资料 Library     md 列表（全量：pinboard/clippings 来源、状态、ingest/lifecycle 记录）
│                    → md view（渲染原文）→ 该源的记忆 view（cards+units）
│                    → 引用它的 claims（链到 Knowledge）
├── 搜索 Search      全库查询：sources/cards/claims/units，结果带证据与溯源跳转
├── 知识 Knowledge   结晶层：durable claims 按主题组织（caveated 标注），M33 viz 图谱挂此处；
│                    每条 claim 下钻到 card→unit→原文行号
├── 对话 Ask         带引用的问答（复用现成 `ovp2 ask` 的 RAG 通道，新增 /api/ask）
└── 系统 System      （角落入口，非主导航）runs/doctor/blocked、管线各步状态、
                     概念说明、设置（vault 路径/LLM/caps/语言，先只读）
```
**⚠ 底座决定已被推翻两次并最终定案（07-08）：全量 React SPA + operator 的 OVP Design System。**
混合方案因主题不可迭代 + /viz 导航割裂被 operator 否决。完整产品设计（用户故事→页面→跳转→
组件、三作用域图谱组件、双主题 Atelier/Vault、i18n 默认英文）见
**`docs/design/portal-v2-product-design.md`（唯一权威，operator 已签字 + mockup 已验收）**；
建造分期 B1-B5，每期截图验收。
- [x] 设计闸门：mockup 深浅两版验收通过（07-08，三项裁决全过）。
- [x] **B1 merged（#297）**（07-09）：SPA 壳 + 设计系统落库（IBM Plex 含中文）+ 双主题 +
      i18n（EN 默认/中文切换）+ Today + Library（集合×月×状态分面）+ server SPA 根路由 +
      codex 门禁 PASS（2 P2 已修）。/viz/ 尾斜杠已顺带修复。
- [ ] B2（进行中）：三层源详情 + `/api/source/:sha` + 邻域作用域 KnowledgeGraph + ClaimRow 日期。
- [ ] B3：Knowledge 页（主题/主张详情 + 全局/主题图谱收编，/viz 独立导航下线）+ ⌘K 搜索。
- [ ] B4：Ask 对话（/api/ask + 引用面板）。
- [x] **B2 merged（#302，原 #299 被 stacked base 删除误关后重建）**：三层源详情 + /api/source/:sha + 邻域图谱。
- [x] **B3 merged（#301）**：Knowledge 主题/详情页 + global/theme 图谱 + ⌘K 搜索 + /viz 独立导航下线（-1600 行死代码）。
- [x] **B4 merged（#304）**：Ask 对话（POST /api/ask + 引用面板 + 历史会话），三轮 codex 门禁加固
      （非阻塞 accept loop、读 body 前有界准入、CSRF 门、超时对齐 OVP_LLM_TIMEOUT_SECS、启动配置校验）。
- [x] B5（本分支）：System 页真身（runs 表 + attention + 管线入口 + 概念说明 + 只读设置
      `/api/settings`）；邻域图补记忆层（卡片节点 + `has_memory` 边——修 07-09 operator 发现的
      72% 无引用源孤点问题）；Flow/Monitor 收进 portal Shell；zustand 移除；web_fetch 接受
      markdown content-type；runbook 写入 serve 步骤。截图在 `.run/portal-v2-b5/`。
**Status**: B1-B4 merged；B5 本分支收尾——之后 P.5 operator 终验走查

**P.5 终验 demo**
- [ ] 全链演示由 operator 本人执行一遍（P.1-P.4 全绿后），录入 `.run/m32-product-walkthrough/`。
**Status**: Not Started

---

## Stage 0: 固化证据 + 全量跑前小加固（1 个 PR，1 天，可立即做）

**Goal**: live 复现的两个发现落地；全量跑不再可能静默截断。
**Success Criteria**:
- [ ] `fixtures/`（或 crate tests）新增 34→22 replay fixture：从 `.run/m32-live-repro/cassettes`
      （`crystal_synth` + `crystal_strength`）+ 34 packs 固化；e2e 断言：28 candidates → 3 ungrounded
      dropped → 25 verdicts → 22 durable；replay 重跑 0 新增（幂等）。替换/补充现有合成 2→1 e2e。
      （AGENTS.md 允许：operator 已明确要求 curate。提交前自查 cassette 内含的文章正文是否可入库。）
- [ ] `synth.rs::slice_cluster`：cluster 超 `max_cases_per_cluster` 时**必须 warn**（stderr +
      写入 run 产物，列出被丢 case ids）；新增 `--strict-cluster-cap`（超限即 fail）供 CI/全量跑用。单测覆盖。
- [ ] pack 缺 `reader.md`（title 退化为 hash）时 warn——这是第一次复现塌成 4 durable 的根因。
- [x] 门禁绿（test / clippy -D warnings / check_architecture.sh），feature branch → PR 进
      `codex/rust-migration`（codex 本地 pre-push gate → coderabbit/gemini bot review → merge）。
**Status**: **Complete**（PR #280 merged 2026-07-02）

## Stage 0.5: dogfood 前 P1 修复（深度 review 发现，1 个 PR，应在 dogfood 第一周内落地）

**来源**: 2026-07-01 三路深度 review（daily / read-source / crystal-synth）。
**Success Criteria**:
- [ ] **坏 cassette 钉死重试（P1，daily+read-source 同根）**: live 模式 Record 先查缓存，模型回复
      即使后续验证失败（截断 JSON、0 units、grounding 违规）也已落 cassette 且 key 无盐 →
      retry / 3-strikes / `--retry-blocked` 全部只会 replay 同一个坏回复，失败恢复语义失效。
      修复方向：验证失败不落 cassette，或失败重试路径先删对应 cassette。
      临时 workaround：手删 `.ovp/cassettes/daily` 下对应文件。
- [ ] **stale run.lock（P1）**: 锁仅靠 Drop 释放，无信号处理、无 PID 存活检查；Ctrl-C/断电后
      `.ovp/run.lock` 永久挡住后续每一次 daily。修复：acquire 时校验 PID 存活。
      临时 workaround：手删 `.ovp/run.lock`。
- [ ] （顺手）`today_iso()` 是 UTC：UTC+8 操作员 08:00 前跑会记昨天的日期 → 修或 dogfood 定在 08:00 后跑。
**Status**: **Complete**（commit 1dd1818c 已在 trunk；PR #281 关闭但代码经其他路径落地，
`ModelClient::invalidate` + RunLock 死 PID 回收均已验证在 `crates/ovp-llm/src/`）。
UTC 问题未修——dogfood 定在 08:00 后跑。

## Stage 1: Dogfood 时钟重启（今天启动，≥14 天，与所有 Stage 并行）

**Goal**: M32 exit criterion #5 —— ≥2 周真实每日 dogfood、无 Python 回退。时钟从第一次 daily run 起算。
**每日操作**（operator，~5 分钟）:
```bash
cd ~/Documents/obsidian-vault-pipeline
set -a; source .env.live; set +a   # MiniMax; 需要 OVP_LLM_NO_PROXY=1
ovp2 daily --vault-dir ~/Documents/ovp-vault --client live ...   # 以 M31 的实跑参数为准
```
- 首日会先消化排队的 29 clippings；per-source 失败自动下轮重试、3 次失败 block 待 review（M31 语义）。
- 每周跑一次 `ovp2 doctor`；blocked sources 当周处理（修复走 PR，或书面 waive）。
- 触发方式：先手动跑 3 天确认稳定，再决定是否 cron/launchd 化（M32 §9：cron over `daily` 可替代 daemon）。
**Success Criteria**:
- [ ] `.ovp/daily-runs.jsonl` 覆盖 ≥14 个自然日（允许零星缺天，但跨度 ≥14 天且 ≥12 天有 run）。
- [ ] 全程 0 次 Python 回退；期间发现的缺陷有 issue/PR 记录。
- [ ] 结束时 blocked/failed 源全部分类处理。
**Status**: **In Progress — 时钟 2026-07-06 重启**（06-15 后闲置了 3 周；07-06/07/09 已四次成功 run（07-09 两次），
07-07 报告：intake 3、reader 8/8 成功、1 blocked；backlog 55 planned / cap 每轮 8 → 约一周消化完）。
最早满足 ≥14 天 ≈ **07-19/20**。触发方式（手动 vs 定时）不是重点——**不断档**才是。

## Stage 2: 全量 corpus 低并发 re-run + failure triage（P0 #1，2-4 天壁钟）

**Goal**: 全库 reader packs；每个失败源被分类（transport vs content defect）。
**Runner 规则（来自 read-source 深度 review，写进脚本）**:
- env 显式设 `OVP_LLM_TIMEOUT_SECS=480~600`（默认 180s 总超时在 6-way 竞争下会误杀慢响应，且超时重试放大 3 倍负载）。
- 内置重试窗口仅 ~1.2s（2 次线性退避）→ runner 外层对 transport 失败加隔时重试。
- 非 0 退出/被中断的 out-dir 必须删除后再重跑——truncated `units.accepted.json` 会让整个
  crystal-synth run 硬失败（`SynthError::Parse` 中止全量）。
- triage 按 stderr 子串分类（`transport:` / `provider error` / `truth-layer error` / `card synthesis`），
  exit code 一律是 1，无法区分。注意：内容性失败已被 cassette 钉死，triage 需要"删 cassette 再跑"类别。
- 传绝对路径（默认 `.run/reader/*` 是 cwd 相对路径，xargs 共享 cwd）。

**准备**（agent）:
- [ ] 写 `scripts/corpus_rerun.sh`（**提交到仓库**——pilot 的 runner 已丢失，不能重蹈覆辙）：
  1. 枚举源：`~/Documents/ovp-vault/50-Inbox/03-Processed/**/*.md`（实测 1012）+ `02-Pinboard`
     归档（~390；确认纳入范围，见开放问题 Q3）；
  2. 跳过已有 pack 的源（pilot 的 34 个）；
  3. `xargs -P 6` fan-out `ovp2 read-source --input <src> --client live
     --out .run/corpus/<hash> --cache-dir <vault>/.ovp/cassettes/reader`——cassette/产物一律进
     `.run/` 或 vault `.ovp/`，**绝不进 /tmp**（AGENTS.md Data Hygiene）；
  4. 每源记录 exit code + 错误摘要到 `.run/corpus/results.jsonl`（append-only）。
**执行**（operator 挂机，agent 盯）:
- 吞吐估算：~9.5 min/源（99% 网络等待）÷ 6-way ≈ 1.6 min/源 → ~1400 源 ≈ 37 小时纯运行 →
  按月目录分批，2-4 天跑完。**并发钉死 ~6-way**（12-way 已证明打爆 MiniMax endpoint）。
- 失败源自动重试一轮（隔 >1h，排除瞬时 transport）。
**Triage**（agent）:
- [ ] 对重试后仍失败的源逐个分类：transport / 内容缺陷（超长、语言、格式、空文件）/ 管线 bug，
      产出 `.run/corpus/triage.md`；真缺陷修复（走 PR）或逐条书面 waive。禁止"都是网络问题"一笔带过（M32 §4 原话）。
**Success Criteria**: 无未处理 inbox backlog；`results.jsonl` 覆盖全部源；triage.md 每个失败源有结论。
**Status**: **Triage RECORDED（2026-07-09）→ `docs/stage-m32-corpus-triage.md`** —
07-02 全量跑重建：1012 attempted / 994 ok (98.2%) / 18 failed，18 个失败逐源分类
（11 JSON-parse 管线缺陷 / 3 provider 空回复 / 2 超长 / 2 空体裸书签；**0 transport**），
blocked 源 84fbf6dc 单列，含 waive/fix 表。仍 open：
(a) operator 对建议 waive 项（超长×2、84fbf6dc）签字；(b) 16 个可重试源
（JSON-parse + 空回复 + 空体）invalidate-cassette 后补跑（~1h 挂机）；
(c) `scripts/corpus_rerun.sh` 当时未提交、无 results.jsonl —— 文档已如实记录，07-02 不重跑；
(d) 02-Pinboard 归档未进 07-02 跑，现走 daily 队列消化（211 queued，归 criterion #1 跟踪）。

## Stage 3: 全量 crystallize（P0 #3，依赖 Stage 2，1-2 天）

**Goal**: crystal store 反映整个库；通过 turnkey 命令可复现。
**步骤**:
- [ ] 先 dry 探察 cluster 分布（catalog+clustering 是确定性阶段，不花钱）：~1400 packs 对
      `max_cases_per_cluster=16` 必然超限——依赖 Stage 0 的 cap 警告决定：调大 cap / 按主题分批 /
      接受截断并记录。**这一步必须在 Stage 0 merge 之后跑。**
- [x] ~~聚类策略决定~~ → **已被 2026-07-02 全量跑证实为 P0 硬阻塞，升级为 Stage 3a**：
      994 packs 下 agents=335 / misc=403 / coding=96，cap=16 共丢弃 ~866 case（87%）——当前
      单趟"每簇一次调用"架构在结构上无法做全量 crystallization，capped run 只能算 smoke。

## Stage 3a: crystal-synth 全量覆盖重构（P0，阻塞 Stage 3 真正的 crystallize）

**根因**：pilot 规模的单趟设计——topic 由 8 个英文关键词桶自顶向下硬分，每桶必须塞进一次模型调用
（所以有 cap），strength 又是单次批量调用（8192 tokens 上限）。对照 KMEM：它自底向上——每源先抽
memory（天然全覆盖）→ 在全体 memory 上建关系/社区 → 从 3+ 相关 memory 合成 crystal；分组与
"单次调用装得下"解耦，没有显式 hard-cap drop。
**Phase 1 — coverage（本周，1 个 PR）**：map-reduce 化，保留全部冻结 prompt/gate：
1. 簇内**确定性分批**（case_id 排序、切 ≤cap 批），每批一次 `crystal_synth/v1` 调用，
   claim id 命名 `<cluster>-b<k>-<n>`；每个 pack 恰好参与一批 → 全量覆盖。
2. 簇级 **reduce/去重**：先用确定性合并（claim 文本规范化相似 + citation 重叠 → 合并 citations），
   不引入新 prompt；评估重复率后再决定是否加 `crystal_merge/v1` 模型合并（走 evolution 候选流程）。
3. strength **分块调用**（≤20 claims/次），修掉 8192 上限。
4. 现有 lint/strength/write 门不动；幂等 by claim_key —— capped run 的 19 条 durable 合法保留，
   修复后全量重跑只会追加。
**Phase 2 — 已升级为 M34 架构轨道（operator 2026-07-02 拍板，见
`docs/stage-m34-knowledge-substrate-design.md`，PR #282）**：四层基底——L1 本地多语 embeddings
（内容哈希缓存）/ L2 涌现社区（临时，替换关键词桶）/ L3 实体=挣来的身份（证据门，M13 反事实
spike 决定生死）/ claim lineage 生命周期（strengthen/append/contradict + superseded_by）。
产品代码前先跑 3 个定量 spike（S1 社区一致性 ≥80%+双语社区；S2 实体候选人工 ≥80%；
S3 lineage 判定 ≥90%），产物入 `.run/m34-spikes/`。查询面维持 lexical；不引入 qdrant 服务。
Stage 3a 与此正交、先行——它是任何分组机制下的执行层。
- [ ] `ovp2 crystal-synth --vault-root ~/Documents/ovp-vault --client live --refresh --date <当日>`
      （store → `<vault>/.ovp/crystal`，cassettes → `<vault>/.ovp/cassettes/crystal`）。
- [ ] `project --write` 出全量 Crystal Notes；抽查 §7 的验收项：Crystal Notes 可浏览/可链接，
      覆盖旧 evergreen 用途（M32 §11 open decision，看过真实数据后确认）。
- [ ] replay 重跑验证幂等（0 新增）。
**Success Criteria**: 全库 durable claims 入 store；lint 0 fail-loud；console/`find --kind claims` 可见；幂等验证过。
**Status**: **Partial** — Stage 3a Phase 1（全覆盖分批）已 merge（PR #283，2026-07-03）；
真实 vault crystal store 有 173 条 ledger、console 显示 283 claims，但"修复后全量 crystallize +
replay 幂等验证"未记录在案。剩余 ~1-2 天。Phase 2 = M34 轨道，**已冻结至 merge 后**。

## Stage 3b: 语义主题系统 L1+L2（operator 指令，merge-blocking；分支 feat/semantic-themes-l1-l2）

**Goal**: 删除硬编码 8 桶英文关键词分类，替换为 spike 验证过的语义主题
（多语 embeddings + Louvain 社区 + c-TF-IDF 关键词 + 双语标签投影）。
**Recipe（.run/theme-spike-20260709/REPORT.md，994 真实 corpus 验证）**：
title+1500 字符 → 多语 sentence embedding（128 token 截断）→ 非互斥 kNN（k=10, cos≥0.5）→
Louvain（resolution 1.5, seed 42）→ 17 簇 / 96.6% 覆盖 / 3.2% 噪声 / 全簇双语。
**关键验证结论（2026-07-10，生产 ONNX 工件重跑 spike 门）**：
- 指定首选 multilingual-e5-small **FAIL**（93–100% 中文文档聚进一个纯中文簇，0–1/4 双语样例对）；
  mpnet / bge-m3 结构过但双语样例对 3/4；
- fastembed **确实**发行 spike 原胜者 `Xenova/paraphrase-multilingual-MiniLM-L12-v2`
  （REPORT "not shipped" 注记过时）——@128 tokens 与 sentence-transformers 逐文档 cosine
  parity = 1.0000，胜者行逐字节复现（17 簇/17/17 双语/4/4 样例对）→ **钉为生产模型**
  （对指令回退顺序的偏离，已在 commit + docs 记录）；
- 根因教训：fastembed 默认 512 token 截断 ≠ ST 的 128 —— token 上限现在是配方常量。
**落地面**：
- [x] `ovp-embed` crate：纯 Rust Louvain/kNN/c-TF-IDF + content-sha 嵌入缓存 + feature-gated
      fastembed embedder（`embed` feature，rustls-only）。
- [x] `ovp2 crystal-themes`：packs → 缓存嵌入 → 社区 → 关键词 → `.ovp/crystal/themes.json`
      （可重建投影，绝不进 ledger）；`--client live` = 每社区一次缓存 `theme_label/v1` 双语命名
      （evolution 候选已注册）；离线=关键词标签；无模型/无 feature = 优雅跳过。
- [x] index/server 投影：ClaimRow.theme = 被引 packs 多数社区标签（平局取字典序，无映射 =
      Unclassified）；无 themes.json = ledger 透传。/api/themes、graph、claim 页零前端改动生效；
      顺带修掉 url_decode 的多字节 UTF-8 mojibake。
- [x] crystal-synth 批次分组去硬编码：themes.json 社区分组，else 日期序确定性分批 + stderr 提示；
      M32 live-repro fixture 已忠实迁移（synth cassettes 字节不变）。
- [x] daily 提示（不自动跑，避免首跑模型下载惊吓）；dist features 加 `embed`。
- [x] 文档 `docs/stage-semantic-themes.md`（验证表 + 配方 + L3 后续）。
**Status**: **Complete**（分支内，待 PR/review）。L3 后续（KMEM 式 LLM-shaped 合成簇）超出本 Stage。

## Stage 4: KMEM AB on random real sample（P0 #4，依赖 Stage 2/3，2-3 天）

**Goal**: "87% vs 58%" 在**我们自己的真实随机样本**上重测，两层都有 recorded verdict——无论结果好坏都记录。
**步骤**:
- [ ] 随机抽样：从全量 corpus 随机抽（建议 ingest 层 30 源、crystal 层 20 源；记录随机种子入 repo）。
- [ ] **Ingest/unit 层**：`ovp2 compare-run` 跑样本（grounded units vs KMEM memories：
      grounding / coverage / concept overlap，5 lexical dims）。需要 KMEM 侧捕获
      （先例：`scripts/m21_capture_kmem.py` / `m15_capture_kmem.py`）。KMEM 是 eval-only、gate-fenced——不新增产品依赖。
- [ ] **Crystal/claim 层**：用 M26 AB workbench（`scripts/m26_*.py`）重跑 20 随机源
      （core-point coverage / factual issues / granularity / verdict，human+LLM judged）。
- [ ] verdict 写入 `docs/`（如 `stage-m32-ab-real-sample.md`），随 PR 提交。
**Success Criteria**: 两层各有一份 recorded verdict；产品差异化主张更新为真实样本数字。
**Status**: **Verdict RECORDED（2026-07-09）→ `docs/stage-m32-ab-real-sample.md`** —
两层判决已按"无论结果好坏都记录"落档：**摄入层 OVP 领先（中高置信**：M21.1 20/20 +
07-02 全库词法包含度 77.6%/41.4% + S2v3 supported 排序）；**结晶层分裂（中等置信）**：
事实性/可溯源性 OVP 明显领先（S2v3 KMEM 栈 43% unsupported），跨源综合广度 KMEM 仍领先
（S2v3 三轮收敛的残余赢面）。如实记录：07-02 compare-run 实际只有 n=1 可用双边样本
（KMEM 侧 space 报错 + 抽取 0 记忆），非随机、无种子。仍 open（doc §3 缺口表，
operator 二选一：书面 waive 或补跑 ~2-4 天）：带种子随机抽样（30/20）、KMEM 侧新鲜捕获、
M26 workbench 重判、双评审模型家族。

## Stage 5: Level-3 go/no-go + merge 回 main（全部依赖，~07-15 之后）

> **GO — 2026-07-10（operator 决定，含两项书面豁免）**
>
> | # | Exit criterion | 状态 |
> |---|---|---|
> | 1 | 全量 corpus run 无 backlog | ✅ 1012 attempted / 994 ok；当前 211 queued 为 07-09 新入 pinboard 书签（滚动产品队列，非欠账） |
> | 2 | 失败源全部分类/waive | ✅ docs/stage-m32-corpus-triage.md；operator 以 merge 决定签收其余 waiver |
> | 3 | 全库 crystallize + 幂等 | ✅ 329 durable / replay 重跑 0 新增 |
> | 4 | KMEM AB recorded verdict | ✅ docs/stage-m32-ab-real-sample.md；补跑项 operator 豁免 |
> | 5 | ≥14 天 dogfood | ⚠ **WAIVED**（operator 2026-07-10）：5 天（07-06..07-10）全部成功、0 Python 回退，产品走查通过后 operator 决定不再等满 14 天 |
> | 6 | 数据无丢失 | ✅ merge 不删除任何文件；vault md 原样；`60-Logs/knowledge.db` 实为 381MB（非计划假设的 0-byte）——按 Python 架构契约为可重建投影，原样留盘，doctor 标记为 legacy 产物待 operator 验证后自行处置 |
> | #0 | 产品原型走查 | ✅ operator 2026-07-10 走查通过（"看起来可以，没有太大的问题"） |

**Goal**: 按 M32 §3 六条 exit criteria 逐条验收，通过则执行 merge（= Python 正式退役）。
**Go/no-go 清单**（六条全绿才 merge）:
- [ ] 全量 corpus run 完成，无 backlog（Stage 2）
- [ ] 失败源全部分类，真缺陷修复或书面 waive（Stage 2）
- [ ] crystal-synth 可复现且全库已 crystallize（Stage 0/3）
- [ ] KMEM AB 两层 recorded verdict（Stage 4）
- [ ] ≥2 周 dogfood 无 Python 回退（Stage 1）
- [ ] 无数据丢失：旧 knowledge.db 是 0-byte shell，重建即迁移（M32 §4 已决定）——merge 前最后确认一次旧 vault 状态无遗漏
**Merge 前建议（不阻塞，但顺手做）**:
- [ ] M32 §8 hygiene 中的文档项：`legacy-alignment.md` 标 superseded、`architecture.md` 补到 M28+；
      substrate quarantine（feature-gate legacy crates）可 merge 后再做。
**Merge 执行**:
- [ ] 门禁最终跑一遍（test / clippy / arch check）+ codex review。
- [ ] `git checkout main && git merge --ff-only codex/rust-migration`（当前 169 commits ahead、0 behind，
      纯 fast-forward；Python 历史保留在 git history 里，无需归档分支）。
- [ ] 打 tag（如 `rust-mainline`），push 后 **`git ls-remote` 验证**（rtk 曾把失败 push 报成 ok）。
- [ ] 更新 memory / AGENTS.md：main = Rust trunk，PR flow 目标从 codex/rust-migration 切回 main。
**Status**: Not Started

---

## 依赖与并行

```
Stage 1 (dogfood, ≥14天) ────────────────────────────┐
Stage 0 (fixture+加固, 1天) ─→ Stage 2 (全量跑, 2-4天) ─→ Stage 3 (crystallize, 1-2天) ─→ Stage 4 (AB, 2-3天) ─┴─→ Stage 5 (go/no-go + merge)
```
Stage 1 与 0/2/3/4 完全并行；关键路径是 dogfood 的 14 天 → **最早 merge ≈ 2026-07-15**。

## 开放问题（不阻塞启动，执行中定）

1. **Q1 dogfood 触发**：手动 vs cron/launchd？（建议手动 3 天 → cron 化）
2. **Q2 AB 样本量**：ingest 30 / crystal 20 是建议值；KMEM 捕获通道是否仍可用需先验证。
3. **Q3 pinboard 归档 ~390 是否纳入全量跑**：M32 §4 写的是 "~1012 processed + ~390 pinboard archive"，
   默认纳入；若质量太差可在 triage 里整类 waive。
4. **Q4 paper routing A/B（M32 P1 #6）**：不阻塞 Level 3，建议 Stage 2 期间顺带挑 5-10 篇论文源做 A/B。
5. ~~Q5 portal 实现方式~~ → **已定案：混合（c）**（operator 2026-07-08）。
