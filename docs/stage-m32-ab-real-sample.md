# Stage M32 — KMEM AB on Real Sample: Recorded Verdict (Exit Criterion #4)

**Closes (with recorded gaps):** M32 Level-3 exit criterion #4 — "Random-sample **AB vs KMEM**
completed (ingest + crystal layers) with a recorded verdict"
(`docs/stage-m32-python-retirement-and-product-definition.md` §3) and IMPLEMENTATION_PLAN
Stage 4, whose own wording is: two layers, recorded verdict, **"无论结果好坏都记录"**
(record it whatever the result).

**Written:** 2026-07-09. This doc does three things: (1) inventories the comparison evidence
that actually exists on disk, (2) writes the layer-by-layer verdict that evidence supports
*today*, with explicit confidence and sample-selection caveats, and (3) lists exactly what
remains to satisfy the criterion **as written**, so the operator can decide "good enough,
waive the rest" or "run the remainder". No number below is derived fresh — each is read from
a named artifact or cited from a committed stage doc.

ZH 摘要：证据盘点——07-02 的 compare-run 实际只产出 1 个可用双边样本（KMEM 侧在测试
space 抽取了 0 条记忆，无法作为判决）；同日的全量词法对比（994 OVP packs vs 457 KMEM
源）给出摄入层密度与包含度的不对称证据；结晶层依赖 M26（17胜/3平/0负，87% vs 58%，
非随机 20 例）与 M34 S2v3 公平重测（KMEM 全栈 43% 无据、跨源综合仍是它唯一赢面）。
今日判决：**摄入层 OVP 领先（中高置信）；结晶层 OVP 在事实性/可溯源性领先、KMEM 在
跨源综合广度仍领先（中等置信）**。按判据原文（随机样本+双层）仍缺：记录随机种子的抽样、
KMEM 侧新鲜捕获、双评审模型家族。缺口清单附工作量，供 operator 决定豁免或补跑。

---

## 1. Inventory — what comparison evidence EXISTS

### 1a. The 2026-07-02 per-source compare-run (`.run/m32-stage123-20260702/stage3-compare/`)

**4 pack directories, 2 sources, of which 1 usable two-sided run:**

| Pack | Outcome |
|---|---|
| `35163299-obsidian-dashboard-default` | **Completed, both sides available.** |
| `35163299-obsidian-dashboard` | KMEM side failed: `HTTP 400 {"detail":"Unknown space: ovp-m32-stage3"}` |
| `2af6411a-nowledge-forget` | Empty scaffold — 0 files in `comparison/`, empty log |
| `2af6411a-nowledge-forget-url` | Empty scaffold — 0 files, empty log |

Shape of a pack (read from `35163299-obsidian-dashboard-default/`): `REVIEW.md` +
`comparison/{concept-overlap.md, claim-diff.md, grounding-audit.md, retrieval-comparison.md,
score.json, summary.md}` + `ovp/`, `nowledge/`, `canonical/` sides + shared
`grounding-reference.txt` / `input.md` (both systems ate the same local markdown). The pack's
own framing (verbatim): *"All cross-system metrics are LEXICAL — they flag things to inspect,
not semantic verdicts"* and *"this pack shows where they diverge, it does not declare a
winner."*

Result of the one completed run: on the shared input, **KMEM extracted 0 memories**
(`nowledge 0 memory-titles / 0 memories`), so every dimension degenerated: concept overlap
Jaccard 0.000 (9 ovp-only / 0 nowledge), grounding ovp 20/20 (1.00) vs nowledge 0/0, and all
3 scoped retrieval probes returned ovp-rag 5 vs nowledge 0. Also noted in the pack: KMEM has
**no source-scoped crystal API** (crystals are cross-source only), so a per-input crystal
comparison is structurally unavailable on the KMEM side.

**Honest reading: the 07-02 compare-run is an infrastructure smoke test, not an AB.** With
n=1 usable source and the KMEM arm extracting nothing in the freshly-provisioned space, it
cannot support any verdict except "the compare harness runs end-to-end".

**Sample selection:** no random seed, sample list, or selection rationale exists anywhere in
`stage3-compare*/`. Whether the 2 sources were randomly drawn is **unknown**; given one of
them is an article *about* the KMEM product itself ("nowledge-forget"), convenience selection
is the likelier explanation. Recorded as unknown.

### 1b. The 2026-07-02 broad lexical compare (`.run/m32-kmem-status-20260702/`)

A non-LLM, read-only proxy over the full corpora of both systems
(`broad-lexical-compare-v2.md`, self-labeled *"not a final AB judgment"*):

- Scale: **994 OVP packs** vs **457 KMEM-extracted sources**; 301 matched sources with KMEM
  memories (sha8 75 + basename 226); 124 matched sources where KMEM has 0 memories.
- Density: OVP **24.74 units/source** (median 23) vs KMEM **6.88 memories/source** (median 6);
  totals 7,448 OVP units (all quote-carrying) vs 2,071 KMEM memories (**0** quote-like fields).
- Containment asymmetry (lexical, threshold 0.25): **77.6%** of KMEM memories are covered by
  OVP units, but only **41.4%** of OVP units are covered by KMEM memories (at 0.40: 61.7% vs
  23.6%). I.e., on matched sources KMEM's content is largely a subset of OVP's, not vice versa.
- Also captured: `kmem-status.json` (KMEM v0.9.1 healthy on 2026-07-03) and a full
  `kmem-inventory.json` (KMEM crystals dump) — the KMEM capture channel works.

### 1c. The 2026-07-02 M26-style artifact (`.run/m32-stage123-20260702/stage3-m26/`)

`review-pack.json` (mtime 2026-07-02 17:29) + a rebuilt 20-case dashboard. Contents: the same
**20 m18-XX held-out cases** as M26, with **266 OVP cards** and **123 KMEM memories** — these
totals match M26's committed inputs exactly (M26 doc: cards from `.run/m21/packs.json` = 266;
KMEM memories from the M21.1 capture = 123 across 20 cases). Case records carry
`ovp_cards`/`kmem_memories` side by side but **no verdict fields**. **Honest reading: this is
a re-materialization of the M26 evidence surface on 07-02 — not a new adjudication and not a
fresh sample or fresh KMEM capture.**

### 1d. Committed historical verdicts (cited, not re-derived)

- **M21.1** (`docs/stage-m21-pre-release-dashboard.md`, live-KMEM re-run, 20 held-out
  sources): OVP wins source-level usefulness **4.75 vs 3.35**, head-to-head **20/20**,
  coverage **4.90 vs 2.85**; readability a genuine tie; "OVP's edge is coverage + provenance,
  NOT readability."
- **M26** (`docs/stage-m26-article-level-memory-review.md`, article-level AB, same 20 cases):
  verdict distribution **17 ovp_better / 3 tie / 0 kmem_better**; core-point coverage
  **OVP 180/206 (87%) vs KMEM 120/206 (58%)**; factual issues **OVP 5 vs KMEM 11**.
  Agent-judged, model confound labeled; cases were the M18/M20 held-out set, **not random**.
- **M34 S2v3** (`docs/stage-m34-knowledge-substrate-design.md` §7.4, 2026-07-04/05): the
  fairest round to date — **dual-frame sampling (50 KMEM-covered + 50 uniform whole-vault)**,
  100 sources, 36 tasks, 5 arms, separate source-truth pass, pre-registered spec, artifacts in
  `.run/m34-s2v3-20260704/`. Key rows: KMEM full stack (arm A) **unsupported 0.431** — "43% of
  A's answer claims could not be supported against source quotes — the first experimental
  measurement of KMEM-summary unfaithfulness"; utility wins A **5** vs OVP grounded-breadth
  stack B+ **12**; A's supported rate 0.317 vs B+ 0.559 / D (units) 0.619. A's *residual
  strength is real*: cross-source synthesis (3 wins) — exactly where the durable-claim layer
  is thinnest. Standing caveat from that doc: **single judge model family in both passes; the
  two-judge-family rule is unmet — nothing may be called "final" until it is satisfied.**

## 2. Recorded verdict — what the evidence supports TODAY

### Layer 1 — Ingest / unit layer (grounded units+cards vs KMEM source memories)

**Verdict: OVP ahead on coverage, grounding, and provenance; KMEM ahead on nothing at this
layer except conciseness/readability (tie at best).** Basis: M21.1's recorded live verdict
(coverage 4.90 vs 2.85, 20/20 head-to-head), corroborated at ~1000-source scale by the 07-02
lexical containment asymmetry (77.6% of KMEM lexically inside OVP vs 41.4% converse; 24.7 vs
6.9 items/source; 7,448 quote-carrying units vs 0 quote-like fields on the KMEM side), and by
S2v3's supported-rate ordering (units 0.619 / cards+claims+units 0.559 vs KMEM stack 0.317).

**Confidence: moderate-high.** Three independent measurement styles (human/LLM-judged 20-case,
whole-corpus lexical proxy, task-based fair eval) point the same way. Caveats: the M21.1/M26
sample was held-out but **not random**; the lexical compare is a proxy, not semantic; the
planned fresh per-source compare-run effectively did not execute (§1a, n=1, KMEM side empty);
single judge family in every judged round.

### Layer 2 — Crystal / claim layer (durable claims vs KMEM memories+crystals)

**Verdict, recorded whatever the result — it is genuinely split:**

- **Faithfulness / traceability: OVP clearly ahead.** S2v3: KMEM stack 43% unsupported vs
  source quotes; OVP durable claims are the best-factuality surface across all three M34
  rounds ("grounded durable claims: best factuality/traceability — BOTH rounds", then
  confirmed in round 3). M26's article-level result (17/3/0, 87% vs 58% coverage, fewer
  factual issues 5 vs 11) stands as the recorded claim-layer verdict on its 20-case sample.
- **Cross-source synthesis breadth: KMEM still ahead.** S2v3's explicit residual: A wins the
  cross_source_synthesis intent (3 wins); "the KMEM gap to close is synthesis coverage, not
  graphs." The 07-02 crystal run itself was capped (19 durable / 6 caveated from 112 of 978
  cases; ~87% of cases dropped by cap=16 — `stage2-crystal-run.log`), which is precisely this
  weakness at execution level; Stage 3a batching has since removed the cap (994-pack rebuild
  on 07-07 with 0 cap overflow; live store now 173 durable / 110 caveated per the 07-09 index).

**Confidence: moderate.** The pro-OVP faithfulness result comes from the fairest, largest,
pre-registered round (dual-frame, source-truth-checked) — but single judge family (M34's own
standing rule blocks "final"), and the M26 87/58 number is from a non-random 20-case sample
judged by one agent. The pro-KMEM synthesis-breadth result survived three rounds under
opposite biases, so it is the more robust of the two directions and is recorded here without
euphemism.

### Sample-selection caveat (applies to both layers)

No layer of this verdict rests on a sample that was **randomly drawn with a recorded seed**,
which is what the criterion literally asks for. M21/M26: held-out but curated 20-case set.
S2v3: dual-frame with a uniform whole-vault arm (closest to random; frames and spec recorded
in `.run/m34-s2v3-20260704/`), but it measures answer/task quality over stacks, not the
per-source ingest AB of the Stage 4 design. The 07-02 compare sample: selection basis unknown,
n=1 usable.

## 3. Gap list — what remains to satisfy the criterion as written

| # | Gap | What "done" looks like | Effort |
|---|---|---|---:|
| 1 | **Random sample with recorded seed** | Draw ingest-layer 30 + crystal-layer 20 sources from the 994-pack corpus with a seed committed to repo (plan Stage 4 numbers) | ~0.5 h |
| 2 | **KMEM space provisioning + fresh capture** | Fix/provision the compare space (07-02 failure: `Unknown space: ovp-m32-stage3`; and the "default" space extracted 0 memories on a fresh doc — root-cause whether ingest lag or config) then re-ingest the 30-source sample on the KMEM side. Capture channel itself is proven (kmem-inventory.json, M21.1 precedent) | 0.5–1 day |
| 3 | **Ingest-layer compare-run on the sample** | `compare-run` over the 30 sources; 5 lexical dims per pack; summarize | 0.5 day (mostly wall-clock) |
| 4 | **Crystal-layer M26-style adjudication on 20 random sources** | M26 workbench re-run with verdict fields filled (LLM-judged + operator skim), against the *current* 173-durable store | 1–2 days |
| 5 | **Two-judge-family rule** (M34 standing rule) | Second judge family on whichever adjudication round is called final | +0.5 day on top of #4 |

**Decision for the operator:** Option A — *waive #1–#5* on the grounds that three convergent
measurement styles (M21.1, M26, S2v3 — the last one dual-frame and source-truth-checked)
already give a recorded two-layer verdict, and the marginal information from a seeded 30/20
re-run is low; record the waiver in the Stage 5 go/no-go. Option B — *run the remainder*
(~2–4 days serial, #2 is the only risky step) to satisfy the criterion literally. Either way,
**this document is the recorded verdict** the criterion requires; the gaps above are the
delta between "recorded verdict on real data" (done) and "recorded verdict on a seeded random
sample with two judge families" (not done).

## Appendix — reproduction commands (run 2026-07-09)

```bash
R=~/Documents/obsidian-vault-pipeline/.run/m32-stage123-20260702
K=~/Documents/obsidian-vault-pipeline/.run/m32-kmem-status-20260702

ls $R/stage3-compare/                          # 4 pack dirs (2 sources x 2 attempts)
find $R/stage3-compare/2af6411a-* -type f | wc -l    # 0 — empty scaffolds
grep "Sides:" $R/stage3-compare/35163299-obsidian-dashboard*/REVIEW.md
# default: both available · non-default: nowledge UNAVAILABLE (Unknown space)
grep -A3 "At a glance" $R/stage3-compare/35163299-obsidian-dashboard-default/REVIEW.md
# Jaccard 0.000 · ovp 20/20 grounded vs nowledge 0/0 · nowledge 0 memories

sed -n '1,20p' $K/broad-lexical-compare-v2.md   # 994 vs 457 · 24.74 vs 6.88 · 77.6%/41.4%

python3 - <<'EOF'
import json
p = json.load(open('/Users/chris/Documents/obsidian-vault-pipeline/.run/m32-stage123-20260702/stage3-m26/review-pack.json'))
print(p['n_cases'],
      sum(c['ovp_card_count'] for c in p['cases']),
      sum(c['kmem_memory_count'] for c in p['cases']))   # 20 266 123 — matches M26 inputs
print('verdict' in p['cases'][0])                         # False — no adjudication fields
EOF

wc -l $R/stage2-crystal-store/ledger.jsonl      # 19 durable writes (capped run)
grep -oE "has [0-9]+ case" $R/stage2-crystal-run.log   # 335/69/96/31/24/20/403, cap 16

# Cited docs (committed): stage-m21-pre-release-dashboard.md, stage-m26-article-level-memory-review.md,
# stage-m34-knowledge-substrate-design.md §7.3–7.4; S2v3 artifacts in .run/m34-s2v3-20260704/
```
