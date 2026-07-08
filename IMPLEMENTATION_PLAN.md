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
- [ ] README 增加 quick-start（≤10 步：装 → `.env` 配 LLM → 首次 `ovp2 daily` → `ovp2 serve`）。
- [ ] 在一台"干净"环境（无 Rust toolchain）验证安装可用。
**Status**: Not Started

**P.2 Pinboard 源真实端到端（~1 天）**
- [ ] 真实 vault 从未跑过 pinboard（无 `.ovp/pinboard-sync.jsonl`，`02-Pinboard/` 空）。
      需要 `--features pinboard-live` build + `PINBOARD_TOKEN`；跑 `pinboard-sync --live` →
      书签 materialize 进 `02-Pinboard/` → 下一次 `daily` 消化 → ledger/report 可见。
**Status**: Not Started

**P.3 裸书签 / GitHub 源网页抓取（唯一真开发，2-4 天）**
- [ ] M31 明确缺口："No web-page fetching for bare bookmarks; no GitHub/arXiv enrichment"。
      没有它 pinboard 链等于装饰（裸书签全部卡在 needs-content）。实现 fetch → 可读 markdown →
      needs-content 解除，带测试；GitHub 链接至少 README/正文可读。原 M31 P1 升级为 merge 阻塞。
**Status**: Not Started

**P.4 Web portal 完善 + operator 首次走查（~1 天起步）**
- [ ] serve 冒烟已过（2026-07-07 实测：`GET /` 200、`/api/find` 283 claims）。
      但 viz SPA（M33）从未部署到真实 vault；"打开 web 看今天结果"从未进 runbook。
- [ ] 部署 viz 到真实 vault serve；operator 首次完整走查 portal（classic console 4 页 + viz），
      产出功能缺口清单 → 缺口修复排进本 Stage（operator 反馈驱动，不自行发明需求）。
- [ ] `serve` 步骤写进 operator-runbook 的每日流程。
**Status**: In Progress（07-07 冒烟 + 截图走查中）

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
ovp-next daily --vault-dir ~/Documents/ovp-vault --client live ...   # 以 M31 的实跑参数为准
```
- 首日会先消化排队的 29 clippings；per-source 失败自动下轮重试、3 次失败 block 待 review（M31 语义）。
- 每周跑一次 `ovp-next doctor`；blocked sources 当周处理（修复走 PR，或书面 waive）。
- 触发方式：先手动跑 3 天确认稳定，再决定是否 cron/launchd 化（M32 §9：cron over `daily` 可替代 daemon）。
**Success Criteria**:
- [ ] `.ovp/daily-runs.jsonl` 覆盖 ≥14 个自然日（允许零星缺天，但跨度 ≥14 天且 ≥12 天有 run）。
- [ ] 全程 0 次 Python 回退；期间发现的缺陷有 issue/PR 记录。
- [ ] 结束时 blocked/failed 源全部分类处理。
**Status**: **In Progress — 时钟 2026-07-06 重启**（06-15 后闲置了 3 周；07-06/07-07 已连续两天，
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
  3. `xargs -P 6` fan-out `ovp-next read-source --input <src> --client live
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
**Status**: **Partial** — 2026-07-02 已实跑全量（产物在 `.run/m32-stage123-20260702/`，994 packs），
但收尾未做：`scripts/corpus_rerun.sh` 未提交、无 results.jsonl、**无 triage.md**。
剩余 = 文档/分类工作（~1-2 天），不是重跑。

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
- [ ] `ovp-next crystal-synth --vault-root ~/Documents/ovp-vault --client live --refresh --date <当日>`
      （store → `<vault>/.ovp/crystal`，cassettes → `<vault>/.ovp/cassettes/crystal`）。
- [ ] `project --write` 出全量 Crystal Notes；抽查 §7 的验收项：Crystal Notes 可浏览/可链接，
      覆盖旧 evergreen 用途（M32 §11 open decision，看过真实数据后确认）。
- [ ] replay 重跑验证幂等（0 新增）。
**Success Criteria**: 全库 durable claims 入 store；lint 0 fail-loud；console/`find --kind claims` 可见；幂等验证过。
**Status**: **Partial** — Stage 3a Phase 1（全覆盖分批）已 merge（PR #283，2026-07-03）；
真实 vault crystal store 有 173 条 ledger、console 显示 283 claims，但"修复后全量 crystallize +
replay 幂等验证"未记录在案。剩余 ~1-2 天。Phase 2 = M34 轨道，**已冻结至 merge 后**。

## Stage 4: KMEM AB on random real sample（P0 #4，依赖 Stage 2/3，2-3 天）

**Goal**: "87% vs 58%" 在**我们自己的真实随机样本**上重测，两层都有 recorded verdict——无论结果好坏都记录。
**步骤**:
- [ ] 随机抽样：从全量 corpus 随机抽（建议 ingest 层 30 源、crystal 层 20 源；记录随机种子入 repo）。
- [ ] **Ingest/unit 层**：`ovp-next compare-run` 跑样本（grounded units vs KMEM memories：
      grounding / coverage / concept overlap，5 lexical dims）。需要 KMEM 侧捕获
      （先例：`scripts/m21_capture_kmem.py` / `m15_capture_kmem.py`）。KMEM 是 eval-only、gate-fenced——不新增产品依赖。
- [ ] **Crystal/claim 层**：用 M26 AB workbench（`scripts/m26_*.py`）重跑 20 随机源
      （core-point coverage / factual issues / granularity / verdict，human+LLM judged）。
- [ ] verdict 写入 `docs/`（如 `stage-m32-ab-real-sample.md`），随 PR 提交。
**Success Criteria**: 两层各有一份 recorded verdict；产品差异化主张更新为真实样本数字。
**Status**: **Partial** — 07-02 有 compare 产物（`.run/m32-stage123-20260702/stage3-compare/`），
M34 期间也测得 KMEM-surface 43% unsupported，但 **docs/ 里没有 verdict 文档**——按计划原话
"无论结果好坏都记录"，剩余 = 整理 + 写 `stage-m32-ab-real-sample.md`（~1-2 天）。

## Stage 5: Level-3 go/no-go + merge 回 main（全部依赖，~07-15 之后）

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
