# Stage M26 — Article-level Memory Map AB Review

**This is the new MAIN review / acceptance surface.** The unit of judgment is a whole
article, not a single claim. M25's single-claim quote/citation review (the c04-style
micro-review) is **downgraded to a debug-only workflow** for gate-blocked claims — it is no
longer how we judge system quality.

EN: For each source article we compare, at the article level: the article's **core points**
(ground truth), **Knowledge Mem (KMEM) source memories** (a coarser-but-stable baseline),
and **OVP reader/memory cards**. Which side captures the important content, with fewer
factual problems, at a granularity worth keeping as long-term memory?
ZH: 每篇文章在「整篇」层面比较三样东西：文章**核心点**（ground truth）、**KMEM 记忆**（更粗但稳定的基线）、**OVP 卡片**。看谁抓住了重要内容、事实问题更少、颗粒度更适合长期记忆。

Ground truth = source article. KMEM is a reference arm, **not** ground truth. OVP is compared
via its **cards**, never raw units. Provenance is collapsed (debug detail), never the main UI.

## Inputs (reused, no re-extraction)

M18/M20 20 held-out sources; OVP cards from `.run/m21/packs.json` (266 cards); KMEM
source-scoped memories from M21.1 `.run/m21/kmem/kmem.json` (123 full-content memories, 20
cases). No truth extraction was re-run.

## Outputs

- **Dashboard (main surface):** `.run/m26/dashboard/index.html` + `cases/<case_id>.html` ×20
  — bilingual; one screen per article: core-points coverage checklist, KMEM memories vs OVP
  cards side-by-side, factual issues, granularity/usability notes, verdict + bilingual
  rationale. (gitignored runtime artifact.)
- **Structured result:** `.run/m26/article-review.json` (per case: core_points[], coverage
  counts, factual issues per arm, granularity notes, verdict, rationale_en/zh).
- **Scripts (committed):** `scripts/m26_review_pack.py` (article-level pack assembler),
  `scripts/m26_build_dashboard.py` (bilingual dashboard generator).
- This report.

All 20 articles have an article-level comparison page (20/20 covered).

## Results (agent-judged; model confound labeled)

**Verdict distribution: 17 `ovp_better` · 3 `tie` · 0 `kmem_better`.**

- **Core-point coverage: OVP 180 / 206 (87%) vs KMEM 120 / 206 (58%).** OVP covers ≥ KMEM
  on **every** article (0 cases where KMEM covered more).
- **Factual issues: OVP 5 vs KMEM 11.** KMEM's recurring problems were truncated/incomplete
  memories and one incomplete enumeration; OVP's were occasional over-synthesis / a quote
  placed in the wrong section (the same class M22's gate exists to catch).
- The 3 ties are the dense-technical / Chinese-eng articles — m18-05 (RAG no-escape theorem,
  8/7), m18-15 (GPU/CUDA, 9/9), m18-16 (WebRTC, 9/8) — where KMEM's conceptual abstraction is
  competitive and coverage is near-equal.

## Conclusions / 结论 (bilingual)

**1. Does OVP reach or exceed KMEM at the article level?**
EN: Yes, clearly. OVP wins or ties every article (17 win / 3 tie / 0 loss), covers more core
points on every article (87% vs 58%), and has fewer factual issues (5 vs 11).
ZH: 是，且明显。OVP 在每篇文章上都赢或平（17 胜 / 3 平 / 0 负），每篇的核心点覆盖都不低于 KMEM（整体 87% vs 58%），事实问题更少（5 vs 11）。

**2. OVP's main advantage / OVP 的主要优势:**
EN: Higher core coverage + actionable, self-contained cards (each a discrete principle/how-to)
that are directly reusable, plus collapsed provenance available on demand.
ZH: 核心覆盖更高 + 卡片可操作、自包含（每张是一个独立原则/做法），可直接复用；证据链折叠备查。

**3. KMEM's main advantage / KMEM 的主要优势:**
EN: Concise conceptual abstraction and readability — on dense or Chinese-eng articles its
fewer, broader memories are competitive and easy to scan. Its weakness is truncated/incomplete
memories and lower coverage.
ZH: 概念抽象简洁、可读性好——在密集或中文技术文章上，它更少更宽的记忆很有竞争力、易扫读。弱点是记忆被截断/不完整、覆盖率更低。

**4. Is OVP still over-engineered? / OVP 是否仍过度工程化?**
EN: At the OUTPUT (card) level, no — the cards win on article-level memory quality. The
over-engineering risk is in the REVIEW process (single-claim micro-review), which M26 fixes
by moving acceptance to the article level and demoting M25 to debug.
ZH: 在**产出（卡片）**层面，不过度——卡片在文章级记忆质量上胜出。过度工程化风险在于**评审流程**（逐条 claim 微观评审）；M26 把验收提升到文章级、把 M25 降级为调试，正是为了修这个。

**5. Should M25 be debug-only? / M25 是否应仅作调试?**
EN: Yes. M25's single-claim quote/citation review is now a debug/exception workflow for
gate-blocked Crystal claims, not the acceptance surface.
ZH: 是。M25 单条 claim 的 quote/citation 评审，现在只用于排查被 gate 拦下的 Crystal claim，不是验收入口。

**6. Next step / 下一步:**
EN: OVP is article-level ahead of KMEM, so do NOT keep doing micro gate-tuning. Proceed to
Crystal content expansion (cover the out-of-scope sources, grow durable claims) and reader/
product surface polish. The two narrow card-quality nits to watch (not blockers): occasional
two-layer-concept fragmentation across cards, and a few mis-placed/over-synthesized cards —
both already handled by the M22 strength gate before anything becomes durable.
ZH: OVP 在文章级已领先 KMEM，因此**不要**再做微观 gate 调参。下一步进入 Crystal 内容扩展（覆盖未覆盖的 source、扩充 durable claims）与 reader/产品界面打磨。两个要留意的小问题（非阻塞）：偶发的「两层概念被拆到不同卡片」、少数错位/过度综合的卡片——这些在 durable 之前都已由 M22 strength gate 拦截。

## M25's new positioning (also written to architecture.md)

- **M25 Crystal Review Workbench = debug / exception workflow.** Used to analyze
  gate-blocked Crystal claims (single-claim quote/citation/provenance). NOT the main human
  acceptance surface.
- **The main acceptance surface, from M26 on, is the article-level memory/card AB dashboard.**

## Evaluation principles honored

Judgment is at the article level on factual correctness, core coverage, useful memory
quality, readability, granularity, long-term reuse — not single-quote wording. KMEM is not
ground truth (the source article is). OVP is compared via cards, not raw units. Provenance is
collapsed, not the main UI. All human-facing pages and conclusions are bilingual (EN + 中文).

## Verification

M26 changed **only scripts + docs** (no Rust), so no new Rust tests are required. Sanity
re-run of the existing gates (unchanged from M25): `cargo test --workspace` → **556 passed**;
`bash scripts/check_architecture.sh` → **passed**. Forbidden-path audit: no `.run/` /
`.env*` / cassettes / KMEM dumps / vault output committed (only the two scripts + this doc +
the architecture note).
