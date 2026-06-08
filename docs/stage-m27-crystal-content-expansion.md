# Stage M27 — Crystal Content Expansion + Product Review Surface

Crystal v2: expand durable cross-article knowledge beyond agent-memory (covering M24's
out-of-scope sources where the evidence allows), and ship a **product** reading surface
(bilingual, durable/caveated separated, provenance collapsed) — using the existing M22/M23
gate as the only durable-write path. Not micro-review, not graphics, not Referent/RAG.

## What was produced

- **Durable store (M27/v2):** `.run/m27/store/{ledger.jsonl,crystal.md,review.json}` —
  append-only, idempotent (re-run appends 0), only `final==durable` in the ledger.
- **Product dashboard (bilingual):** `.run/m27/dashboard/{index,crystal,coverage}.html` —
  durable claims grouped by theme, caveated separated with "why not durable + next step",
  source/theme coverage matrix, evidence collapsed, EN + 中文 throughout.
- **Structured reports:** `.run/m27/{crystal-candidate.json, crystal-lint.json,
  strength-verdicts.json, crystal-coverage.json}`.
- **Committed:** `scripts/m27_review_pack.py`? (no — reused M26), `scripts/m27_coverage.py`,
  `scripts/m27_build_dashboard.py`, this doc. (Run artifacts gitignored under `.run/m27/`.)

## How v2 was built (existing gate, no new architecture)

1. **Candidate generation** (5 theme-cluster AI agents over a units catalog): 24 new
   cross-source structured-citation claims, OOS-prioritized. All 5 OOS sources cited.
2. **Citation/provenance gate** (`crystal-lint`): 3 claims dropped for non-verbatim citations.
3. **Claim-strength gate** (`crystal-write` strength verdicts): **the first pass rejected all
   24** as over-reaching — the gate did its job. Per the rules the gate was NOT tuned.
4. **Narrowing pass** (AI, allowed candidate rewrite): each over-reaching claim narrowed to
   what its quotes actually support (honor hedged modality; drop invented mechanisms; forced
   cross-domain GPU↔WebRTC analogies dropped as unfixable). **After narrowing, 15/18 new
   claims passed the strength gate.**
5. **Merge + durable write:** v2 candidate = 8 M24 durable spine + 18 narrowed → gate →
   **12 durable / 14 caveated / 0 reject** in `.run/m27/store`.

The honest signal: first-pass AI cross-source synthesis systematically overreaches its
evidence; the gate catches it; a faithful-narrowing pass converts the genuine ones to
durable. That is the intended M22/M23 contract working, not gate-tuning.

## Results

**Crystal v2 = 12 durable claims (8 M24 spine + 4 new) + 14 caveated candidates.**

New durable claims (each ≥2 distinct sources, strength-supported):
- `engrt-2` (m18-07, m18-14): author-once / two-wrapper deployment (plugin + headless API).
- `engrt-4` (m18-04, m18-07, m18-16): supervisor restart + sandbox isolation → crashes don't propagate.
- `engrt-5` (m18-04, m18-10, m18-16): replay + runtime observability for multi-hour trajectories.
- `agdes-5` (m18-01, m18-20): enterprise context layer turns knowledge/expertise/norms into machine-usable context.

New themes beyond agent-memory: Architectural Separation, Fault Containment, Runtime
Observability, Context-layer-as-infrastructure.

**Coverage:** durable claims now cite **13/20 sources**. OOS sources: **m18-16 (WebRTC) is
now in durable** (via engrt-4/engrt-5); the other four OOS (m18-09, m18-15, m18-17, m18-18)
are present as **caveated candidates** (single-source or narrowed-to-hedged), surfaced in the
review section, not durable. Themes: 12.

## Conclusions / 结论 (bilingual)

**Crystal v2 vs v1 / v2 比 v1 多了什么:**
EN: v2 adds 4 new durable claims (v1 was 8) and 4 new themes (engineering runtime, fault
containment, observability, context-layer), and brings an OOS source (WebRTC, m18-16) into
durable truth. It also produces a product reading surface, not just a store.
ZH: v2 在 v1 的 8 条基础上新增 4 条 durable、4 个新主题（工程运行时、容错、可观测性、上下文层），并把一个原 OOS 源（WebRTC, m18-16）纳入 durable。同时产出了产品阅读界面，而不只是存储。

**Still missing / 仍缺什么:**
EN: Durable coverage is engineering/agent-design heavy; dense-technical sources (GPU m18-15,
WebRTC) and single-author opinion sources (vibe-coding m18-17, decisions m18-09, first-
principles m18-18) mostly land as caveated single-source candidates — genuinely cross-source
durable synthesis for them does not exist in this 20-set. m18-03/m18-12 (eval) core points
live in reader cards but their cross-source claims narrowed to single-source.
ZH: durable 覆盖偏工程/agent 设计；密集技术源（GPU、WebRTC）和单作者观点源（vibe-coding、decisions、first-principles）多为单源 caveated——这 20 篇里它们确实缺乏可跨源 durable 的综合。eval 的核心点在 reader cards 里，但其跨源 claim 收窄成了单源。

**Did v2 keep the M26 article-level direction & avoid M25 micro-review regression? / 是否保持 M26 方向、避免 M25 回潮:**
EN: Yes. The product surface reads at claim/theme/coverage level with evidence collapsed;
single-quote review never drives the page. M25 stays debug-only.
ZH: 是。产品界面以 claim/主题/覆盖层阅读，证据折叠；单 quote 评审不主导页面。M25 仍仅作调试。

**Next step / 下一步:**
EN: Product UI polish + targeted content expansion (find genuine cross-source partners for
the caveated single-source insights, or accept them as caveated). Do NOT go to graphics yet,
and do NOT loosen the gate — the caveated set is correct, not a gate defect.
ZH: 产品 UI 打磨 + 有针对性的内容扩展（为单源 caveated 洞见找真正的跨源搭档，或接受其为 caveated）。暂不进入图形化，也不要放松 gate——caveated 集是正确结果，不是 gate 缺陷。

## Acceptance check

- ✅ Durable store via the unchanged M22/M23 gate; idempotent; append-only.
- ✅ Covers part of M24's OOS (m18-16 durable; all 5 OOS surfaced as caveated) and adds 4 new
  durable claims beyond the 8 spine — not a repeat of v1.
- ✅ Bilingual product dashboard; durable vs caveated visually separated; provenance collapsed.
- ✅ Source/theme coverage matrix (13/20 durable; reader-only/uncovered listed).
- ✅ No M25 single-claim micro-review as main path; no Referent/Resolver/RAG/graph.

## Verification

M27 changed **only scripts + docs** (no Rust), so no new Rust tests are required. Sanity:
`cargo test --workspace` → **556 passed**; `bash scripts/check_architecture.sh` → **passed**.
Forbidden-path audit: only the two scripts + this doc are tracked; `.run/m27/` (store,
dashboard, candidate, KMEM-derived inputs) stays gitignored — no raw model replies, cassettes,
`.env`, or vault output committed.

## Honest caveats

- Candidate generation, narrowing, and the strength gate share a model family (labeled model
  confound); durability is gate-decided but the judge is an LLM.
- "12 durable" means each claim's citation chain is verbatim-verifiable AND it does not
  overreach its cited evidence — not that the claims are exhaustive or the deepest possible.
- The narrowing pass is a legitimate candidate rewrite (allowed by the spec), not gate-tuning;
  the gate thresholds and rules were unchanged throughout.
