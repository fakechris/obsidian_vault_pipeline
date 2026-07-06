# Stage M34 — Knowledge Substrate Design: similarity · grouping · identity · lifecycle

**Type:** Architecture design anchor (like [`stage-m32`](./stage-m32-python-retirement-and-product-definition.md)
is the product anchor). NOT a feature epic; implementation is gated on the experiments in §7.
**Status:** REVISED 2026-07-02 (second operator review): §2 now separates evidence-backed
decisions from **hypotheses**. The entity ladder (§5) is a HYPOTHESIS — candidate design C —
awaiting the §7 comparative experiment. Nothing in §5 may be implemented before that verdict.
**Companions:** [`stage-m32-python-retirement-and-product-definition.md`](./stage-m32-python-retirement-and-product-definition.md) ·
[`product-pipeline.md`](./product-pipeline.md) (the mature-system pipeline map; this doc covers
only its G5-undecided box) · 2026-07-02 full-corpus evidence in `.run/m32-stage123-20260702/`
(gitignored, operator machine).

> **Read this first.** This document re-opens "does OVP need entities?" — but its first draft
> repeated the M7–M13 process failure: designing mechanism (ladders, invariants, ledgers) before
> proving product value. The operator caught it. The M13 demotion is NOT re-litigated as a
> *method* verdict (eager, ungrounded ontology stays dead); §5 is now explicitly a **candidate
> design**, and §7 defines the multi-arm experiment that decides between it, a KMEM-style route,
> a no-entity route, and a query-time-aggregation route.
>
> **Process rule (learned twice now, M13 and this doc's first draft):** architecture docs must
> separate evidence-backed decisions from hypotheses, and any new *persistent* state layer
> requires a product-value experiment BEFORE design detail. Prefer projections (rebuildable
> derived state) over ledgers until data proves statefulness is needed.

---

## 1. Why now — the evidence

The 2026-07-02 full-corpus run (994/1012 reader packs) proved the current synthesis grouping is a
pilot artifact, not an architecture:

- **87% hard-dropped**: keyword buckets gave agents=335 / misc=403 / coding=96 vs
  `max_cases_per_cluster=16` — ~866 of 994 cases were excluded from synthesis entirely.
- **40% in `misc`**: the fixed 8-bucket English taxonomy cannot even fit today's corpus, let alone
  the product scenario (a user's vault with **dozens to hundreds of concurrent topics**).
- **Bilingual corpus**: Chinese and English sources on the same topic share almost no lexical
  tokens — any lexical grouping fails across the language boundary.
- Batching alone (Stage 3a) fixes *coverage*, not *quality*: batches inside `misc` are random
  seatings of unrelated sources; cross-source synthesis over unrelated sources yields forced or
  empty claims.

Three problems raised in review turned out to be one architecture problem:
**embeddings (similarity), entities (identity), and claim lifecycle (time)** — because
supersede/contradiction detection requires deciding "are these two claims about the same thing",
which is an identity question, which needs a similarity substrate.

## 2. Decisions vs hypotheses (revised 2026-07-02, second operator review)

### 2a. Decisions — standing on existing evidence

1. **Embeddings: YES** — local multilingual model (bge-m3 / multilingual-e5 class; runnable via
   qmd/ollama), as **internal, rebuildable synthesis infrastructure**, NOT the query surface.
   Vectors are content-hash cached on disk (same discipline as cassettes; provider swappable).
   Evidence: 87% cap-drop + 40% misc + bilingual corpus — keyword grouping is falsified by data.
   M32 §7's "RAG — lexical is the product" stays true for the query surface.
2. **Stage 3a (map-reduce execution layer) ships** — needed under every candidate route.
3. **No vector DB service** — pain-proves-need, same as NO-SQLite.
4. **The claim-layer moat is NOT in question.** Quote-backed units + gated claims are shipped,
   validated (M26 AB: 17/3/0, coverage 87% vs 58%, fewer factual issues), and research-aligned
   (§X). Nothing in this document may weaken the existing gates.

### 2b. Hypotheses — require the §7 comparative experiment before ANY implementation

- **H1 (entities):** an explicit identity layer improves the knowledge-work product (browse,
  review, trust) enough to justify its machinery. Counter-hypothesis: query-time LLM aggregation
  over a good index (arm D) delivers the same product functions with no persistent identity at all
  — current industry practice increasingly favors compute-at-read over precomputed structure, and
  a well-prompted model may simply beat a rule ladder.
- **H2 (ladder shape):** IF entities win, the T1–T4 ladder (§5) is the right shape — and even
  then, **projection-first**: anchors derived (rebuildable) from durable claims' citations,
  upgraded to a stateful ledger only if projections prove insufficient.
- **H3 (lifecycle-by-subject):** supersede/contradict need durable subjects. Near-term
  counter-hypothesis: retrieval-based lineage (judge sees candidate duplicates + evidence at
  synthesis time) covers strengthen/dedup without any identity layer; only contradiction —
  already deferred — truly wants stable subjects.

## 3. First-principles: does a growing knowledge base need entities?

Derived from product functions, not from technology choices:

| Function | Can similarity (embeddings) deliver it? | Needs identity? |
|---|---|---|
| Aggregation: "everything about X" | No — similarity is soft, thresholded, non-referable; you cannot "click open" a similarity blob | **Yes** |
| Stable navigation anchors | No — communities shift on every re-cluster; last month's "community #7" is gone | **Yes** |
| Cross-language binding (中英 same thing) | Candidate signal only — cosine proximity is not sameness | **Yes** (evidence-adjudicated) |
| Claim lifecycle: supersede / contradiction | No — content-hash too strict, cosine too soft; needs "same subject, same intent" structure | **Yes** |
| Grouping for synthesis | **Yes** — communities suffice; identity not required here | No |

What this table establishes — and its limit: a **read-only reader** product needs no entities,
and several functions of a growing ask/digest/browse product plausibly want identity. But this is
a **functional argument, not a product verdict**: arm D of the §7 experiment (query-time LLM
aggregation over a good index, answers verified post-hoc) may deliver the same functions with no
persistent identity at all. This table MOTIVATES hypothesis H1; it does not decide it. No agent
may cite this section as justification for implementing an entity layer ahead of the §7 verdict.

## 4. The four-layer substrate

```
L0 truth      (exists, FROZEN)  units: verbatim quote + line; fail-loud gate; the moat
L1 similarity (new)             embeddings over cases/units; soft structure; content-hash
                                cached; rebuildable at any time; carries NO identity, NO
                                authority, writes NO product state
L2 grouping   (new)             communities on the L1 kNN graph (deterministic label
                                propagation, sort-based tie-breaks); EPHEMERAL — exists only
                                to feed synthesis; rebuilt freely; never user-facing identity
L3 identity   (hypothesis)      grounded entities via the T1→T4 promotion ladder (§5), only if
                                the §7 experiment selects C-lite:
                                cheap tiers (topics/common anchors) exist from day one at
                                zero LLM cost; durable identity ONLY through the T4
                                evidence gate; claims subject-bind progressively
lifecycle     (hypothesis)      claim lineage = (subject, intent); strengthen / append /
                                contradict; append-only ledger + superseded_by (§6), otherwise
                                retrieval-based lineage stays the near-term mechanism
```

Layer discipline (each layer is FORBIDDEN to do the next layer's job):

- **L1 must not name things.** Cosine similarity is never presented as "same as".
- **L2 must not persist.** Communities are synthesis batching input; they never become browsable
  product objects (that is L3's job, gated).
- **L3 must not be eager.** No entity is minted at ingest time; entities emerge from repeated,
  cross-source, quote-anchored mentions and pass a gate. The model proposes; evidence disposes.
- **Execution layer (Stage 3a: sub-batching, reduce/dedup, chunked strength) is orthogonal** and
  ships first — it is the machinery under ANY grouping mechanism (KMEM's "crystal from 3+ related
  memories" is also a bounded group).

## 5. DEAD (as an answer-quality layer) — candidate design C: promotion ladder to earned identity

> **Status: DEAD for answer quality, per §7.4 (2026-07-05).** Three rounds under opposite biases
> (0/6 · 4/30 · 0/36-worst-scores in the fairest round) produced NO evidence that an
> anchor/entity projection improves answers. Per the pre-registered decision rule, H1 is dead and
> this section is retained as a record of the candidate design only. The narrower question of
> identity for *navigation/browse UX* was never separately tested; it stays open but
> deprioritized and may only be revived by a navigation-task experiment, not by re-reading this
> spec. The invariants below (split-only merging, genericity ceiling) remain good ideas if that
> day ever comes.

What M13/M14b actually falsified (0/3 on real models): **eager** extraction at ingest +
**model-as-identity-authority** (no evidence gate) + **product surfaces stacked on the ungrounded
ontology** (MOC/Atlas/evergreen). What it did not test: entities subjected to the same evidence
discipline that makes claims durable.

**But "every entity must be earned" does not productize** (operator review, 2026-07-02): the
target user is a **general knowledge worker** (the student is one archetype scenario, not the
product boundary) — someone importing ONE document must immediately see browsable topics;
common-sense proper nouns (`Qdrant`, `BGE-M3`, `Attention Is All You Need`) must not wait for a
≥3-source gate; and judging every mention with an LLM explodes token cost. The answer is not loosening the durable gate — it
is a **promotion ladder**. (Terminology: L0–L3 are the substrate *Layers*; T1–T4 below are the
promotion *Tiers* within/toward L3. Do not conflate them.)

| Tier | What | How it exists | Gate | Ledger | May subject-bind lifecycle? |
|---|---|---|---|---|---|
| T1 Topic | emergent browse/cluster labels | L1/L2 derived, ephemeral | none | no | no |
| T2 Common Anchor | proper nouns, tools/models/papers/orgs, code symbols | deterministic mention mining + normalized-surface-form dedup | form heuristics only, zero LLM | no | no |
| T3 Candidate | anchors accumulating cross-source, quote-backed mentions | auto-nominated by frequency/value filters | evidence pack assembled | not yet | no |
| T4 Durable | identity anchors for Crystal lifecycle | batch entity-strength judge over evidence packs | strict (below) | append-only, idempotent | **yes** |

**Three hard invariants across the ladder:**

1. **Split-only below Durable.** T1–T3 are keyed by normalized surface form and may NEVER merge
   across surface forms. Redundancy at low tiers is harmless ("LLM memory" and "agent memory" as
   two topics is fine); a wrong merge is harmful and sticky. **Cross-surface-form merging —
   including 中英 alias binding — is exclusively a T4 operation**, done only on co-mention evidence
   + embedding candidacy + judge, never model assertion alone. This is what makes T1/T2 safe at
   zero LLM cost: common sense needs no proof because low tiers assert no identity.
2. **Genericity ceiling (deterministic).** A naked, unqualified surface form whose document
   frequency exceeds a corpus threshold (`AI`, `agent`, `context`, `模型`…) is capped at T1 and
   never enters the candidate queue by itself. Scoped phrases, titles, code symbols, and
   disambiguated anchors (`attention mechanism`, `agent architecture`, `memory system`,
   `Attention Is All You Need`, `BGE-M3`) are scored separately and may progress to T2/T3 if their
   own evidence supports it. Zero tokens, pure statistics — the guard is against promoting
   undifferentiated generic words, not against core course/project concepts.
3. **Graceful degradation of lifecycle by tier.** Claims may *display-reference* anchors of any
   tier from day one (navigation/aggregation work immediately); **supersede/contradict activate
   only when the subject is T4**. Until then, lineage falls back to soft dedup
   (community + citation overlap) for strengthen-only, as §6 already specifies. Binding is
   progressive, never blocking.

**T4 entity-write gate (draft, calibrated by S2):** mentions in ≥3 *independent* sources (same
author/article syndicated ≠ independent), each mention bound to a verbatim quote + line; stable
surface forms over time; evidence-bound aliases; passes a **batched** entity-strength judgment
(20–50 candidates per call, evidence-quoted, same shape as `crystal_strength`; negative verdicts
cached so rejected candidates are not re-judged); append-only entity ledger, idempotent by key.

**Token budget (why this is affordable):** T1/T2 mining and dedup are deterministic (0 tokens);
embeddings are local (0 API tokens); frequency/value filters cut ~30k units to O(10²) candidates
on the 994-pack corpus; batch judging puts a full-corpus pass at ~5–15 LLM calls, and the
incremental daily path at amortized near-zero. LLM spend concentrates exclusively on high-value
candidates at the T3→T4 boundary.

**Relation extraction remains unproven; C-lite tests whether claims can serve as edges.** In the
candidate-C design, a durable claim citing units under two anchors can be projected as an
evidence-backed edge between them. That would give an entity–claim–entity graph where every edge
quote-chains through the claim, without a separate LLM relation extractor. Whether this provenance
advantage creates enough user value versus KMEM-style relationship extraction is exactly what the
§7 experiment must measure. A separate relation-extraction stage is a **non-goal** unless the
experiment falsifies this assumption (§9).

**Product surface:** users see three natural things — **Topics** (fast, allowed to be imperfect),
**Crystals** (cross-source conclusions), **Evidence** (one click to the quote). "Durable
identity", tiers, and gates are internal mechanics, never user-facing vocabulary.

**Cut from v1 (anti-over-engineering, operator-directed):** entity type taxonomy
(person/org/tool… — metadata, not gate input; an open tag at most), relation extraction (see
above), and merge/split governance UI (the append-only ledger records enough to build it later).

If C-lite wins, it extends the moat rather than betraying it: "every durable thing traces to source
quotes" would cover claims and anchors. The design sentence becomes: **KMEM demonstrates the
product value of entity/community/crystal surfaces; OVP must prove those surfaces can exist under
quote-grounded, audit-first constraints** — fast tiers carry the product experience, earned
identity carries the long-term trusted structure.

**Kill criterion:** if S2 (§7) fails human check the way M13 did, T3/T4 stay out (T1/T2 are
harmless and keep working), this section is marked DEAD, and we document why bottom-up +
evidence-gated was still not enough. The demotion of eager ontology is permanent either way.

## 6. Incremental architecture + claim lifecycle

Steady-state event flow (replaces "one global batch run" as the primary mode):

```
new source → units (L0) → embed (L1, cache) → incremental community assignment (L2)
  → mark dirty communities (membership churn > threshold)
  → re-synthesize ONLY dirty communities (Stage 3a machinery: batches → reduce → strength)
  → lineage adjudication per output claim vs active claims of that community/subject:
      same finding, more/better citations  → STRENGTHEN: append new version, mark old superseded_by
      genuinely new finding                → APPEND
      opposing finding, same subject       → CONTRADICT lane (M32's deferred contradiction
                                             detection returns here as a lifecycle branch, not
                                             a separate feature)
  → append-only ledger; nothing is ever deleted or rewritten
periodic full re-cluster = maintenance action (churn-bounded, never mid-day), not the daily path
```

**Invariant shift (stated explicitly):** today's guarantee is batch determinism via cassette
replay. In the incremental world, global state depends on arrival order; the invariant becomes
**event-sourced auditability** — every mutation is a ledger event; any lineage's history is
replayable end-to-end; `accepted_without_quote=0` and the crystal gates hold at every step. This
matches the existing append-only posture (OVP_RULES) and must be enforced by review going forward.

**Lineage key problem** (the hard part, decided by S3): lineage = (subject entity, normalized
intent). Until L3 exists, an interim lineage key of (community, claim-embedding proximity +
citation overlap) is acceptable for strengthen/dedup but NOT for contradiction (too soft) — one
more reason lifecycle depends on identity.

## 6.5 Research grounding — what the literature does and does not support

Supports the **shipped moat** (claim-level attribution + atomic verification): AIS
(attributable-to-identified-sources framing), ALCE (citation quality must be *verified* — models'
citations are routinely incomplete; exactly why our verbatim gate exists), FActScore (atomic-fact
decomposition ≈ our units/claims), FEVER (claim+evidence is a mature, hard problem), GopherCite
(verified quotes build trust; citation alone is not sufficient). Supports the **product value of
the graph route** (KMEM/GraphRAG style): GraphRAG — entity/community summaries measurably help
corpus-level sensemaking — while Microsoft's own discussions acknowledge the summary→source
**traceability gap** (the exact weakness OVP attacks). Emerging but not settled: provenance-anchored
KG extraction (e.g. AEVS: unprovenanced triples make faithfulness unverifiable).

**What NO literature validates:** the specific
`unit → claim → T1–T4 entity ladder → subject-keyed lifecycle` product structure. That chain is
our hypothesis to prove, with data, in §7. The moat sentence, corrected accordingly:
**the moat is claim-level attribution + evidence-verifiable synthesis + sensemaking — the entity
ladder is one candidate implementation, not the moat itself.**

## 7. The comparative experiment — product value decides, before any product code

One experiment replaces the former S2/S3 mechanism-spikes. S1 is kept (it serves every arm).

- **S1 — grouping quality (unchanged, needed by ALL arms).** Embed 994 cases → kNN →
  deterministic communities. Accept iff 20 sampled communities show ≥80% member topical coherence
  AND ≥3 genuinely bilingual communities. Baseline: keyword buckets on the same sample.

- **S2′ — four-arm product comparison** on 50–100 real historical sources (papers, web, notes,
  GitHub, 中文材料 — a general knowledge-work mix), artifacts under `.run/m34-spikes/`:
  - **A — KMEM-style:** memory → LLM entity/relations → community → crystal summary. Use the live
    Nowledge instance where its 0.9.1 stability allows; fall back to existing captures (M21/M26
    machinery) — sample size may shrink, record it.
  - **B — OVP minimal (no entities):** units → gated claims → community crystals (Stage 3a
    output). Retrieval-based lineage for dedup/strengthen. This arm mostly EXISTS already.
  - **C-lite — grounded anchors as projection:** B + deterministic mention mining + genericity
    ceiling + ONE batched judge pass → anchors derived from claim citations as a rebuildable
    view. NO ladder state machine, NO entity ledger — the cheapest faithful slice of §5. (Testing
    full C would require building it first — the exact trap this revision exists to avoid.)
  - **D — query-time aggregation (no precomputed structure):** B's index + an agentic query-time
    LLM that aggregates "everything about X" / "what does this corpus say" on demand, answers
    verified through the existing citation gate. The "a good prompt beats a rule ladder"
    null-hypothesis arm.
### 7.1 Round-1 result (2026-07-03) — DIRECTIONAL ONLY; bias audit appended

Run: 50 KMEM-aligned sources, 6 designer-written tasks, bundles A=386 KMEM memories /
B=14 durable claims / C=80 anchors projected from B / D=1402 accepted units (top-8 retrieved per
task per arm); answers synthesized by the same model per arm; single LLM judge. Winners:
**B 4/6, A 1/6 (breadth/workflow task), D 1/6 (product task), C 0/6.** t5's numeric scores
excluded (scale bug; winner kept). Artifacts: `.run/m34-spikes/20260703-s1-qmd-embeddinggemma/`.
Separate result: **S1 (kNN+Louvain grouping) FAILED its acceptance criteria** — identity/community
remains unproven and Stage 3b is NOT unblocked.

**Verified bias audit (operator-driven; each item checked in the artifacts):**
1. **Task prompts embed OVP's philosophy** — t1 literally instructs "Return only claims with
   cited evidence": the task asks every arm to produce B-shaped output. Designer-written tasks,
   n=6.
2. **Rubric double-counts groundedness** (quote_traceability AND unsupported_claims as scored
   dimensions) while breadth/latency stay qualitative.
3. **No blinding** — the judge saw arm names (`B_ovp_durable_claim`, `A_kmem_memory`) and cited
   them in rationales; single judge; judge model = the answer-writing model (self-preference).
4. **A is not KMEM** — memories only; no crystals/entities/communities surface.
5. **C was strangled at birth** — 80 anchors projected from B's 14 claims inherit B's scarcity;
   its 0/6 says nothing about mature entity value.
6. **Coverage confound, and the round's REAL headline:** the durable gate yielded **14 claims
   from 50 sources (~0.28/source)**. B won 4/6 under a rubric that barely punishes tiny corpus
   coverage — and lost exactly the breadth tasks (t3 to A, t6 to D). The honest reading:
   *grounded durable synthesis wins reliability-flavored asks; its coverage is OVP's key product
   weakness; the breadth layer above durable claims (or a verified read-time layer) is the gap.*

**What round 1 may be cited for:** the three directional signals above and the S1 failure.
**What it may NOT be cited for:** "OVP beats KMEM", "entities have no value", or any Stage 3c
decision. Decision rules remain unfired until S2v2.

### 7.2 S2v2 — the pre-registered fair protocol (supersedes the metric/judging spec above)

Design principle, from the operator: users don't inherently care that content is source-backed —
they care "I imported my stuff; can it answer my questions; what did it build for me."
Groundedness must earn its value **through measured correctness and trust behavior**, never
through rubric points. Do not let the system's design philosophy write the exam it grades itself
on.

1. **Pre-registration.** Tasks, metrics, judge prompts, and decision rules are committed to the
   repo BEFORE any arm output is generated. No post-hoc metric additions. The source/sample plan
   is also pre-registered: corpus window, eligibility rules, exclusion rules, strata, random seed,
   and replacement policy. Otherwise a fair task set can still be biased by a hand-picked source
   set.
2. **Tasks come from users, not designers.** Three sources, frozen before arm outputs exist:
   (a) the operator's real historical questions (ask/digest/chat logs); (b) blind elicitation —
   people (or an LLM given only source titles/TOC, never any arm's representation) asked "what
   would you want to ask this library?"; (c) source-derived reader questions generated from full
   source text. Stratified over a fixed intent taxonomy: orientation ("这批资料讲了什么") ·
   specific lookup · cross-source synthesis · decision support · review/refresh · provenance
   check (a real user intent — ONE stratum among six, not the rubric). **N ≥ 30, ≥5 per
   stratum**, bilingual mix; report bootstrap CIs over tasks; no prompt may instruct
   evidence-formatting.
3. **Arms are products, not layers — and the evaluation lane must be explicit.**
   - **Product-native lane (primary product verdict):** each system fields its honest full stack:
     OVP = its real answer path (durable claims + fallback to units/packs via the index); KMEM =
     its real query surface (memories + entities + communities + crystals). Native model choices
     and orchestration are allowed because they are part of the product. Report model, context,
     latency, and token/cost per answer.
   - **Evidence-normalized lane (diagnostic only):** the same answer model, same context-token
     budget, and same generation budget are used across arms to isolate the quality of each
     evidence surface. Conclusions from this lane must be labeled as evidence-surface findings,
     not product findings.
   If a full surface is unavailable, the arm is RENAMED (e.g. "KMEM-memories-only") and every
   conclusion scoped accordingly. Do not mix product-native and evidence-normalized outputs in
   one win/loss table.
4. **Primary metrics are user-neutral:** (a) task success — does the answer satisfy the
   information need (0–3); (b) **factual correctness verified against the sources** — penalize
   statements that are WRONG, not statements that are uncited (this is where grounding must earn
   its keep); (c) key-point coverage vs a per-task reference built from the sources before any
   answers are seen. Factual correctness is operationalized as: decompose each answer into atomic
   claims, verify each claim against blind source excerpts, and label it
   `correct | wrong | unsupported | unverifiable`; only `wrong` is a hard correctness penalty,
   while `unsupported` and `unverifiable` are reported separately. **Traceability is measured
   separately and behaviorally** — a trust probe on a subset: pick 2 statements per answer,
   "show me why", score whether/how fast the original passage is reachable. Output is a
   **multi-dimensional scorecard + per-stratum winners** — no single weighted score; the operator
   makes the tradeoff visible-eyes-open.
5. **Judge hygiene:** arm identity blinded (neutral labels; formatting normalized — citations
   stripped for the usefulness pass, restored for the verification pass); ≥2 judge models from a
   different family than the answer model; answer-order randomized per task; anchored scales with
   calibration examples (kills the t5 scale-bug class); operator blind-judges a 20% sample;
   inter-judge agreement reported.
6. **Coverage and cost are first-class columns:** each arm reports % of corpus represented in its
   evidence layer, tokens per answer, wall latency. Round 1's hidden confound becomes a visible
   metric.
7. **Entry conditions before running S2v2:** (a) Stage 3a full-corpus crystallize done — B fields
   its real coverage, not the 14-claim artifact; (b) KMEM full surface reachable or arm renamed;
   (c) C-lite either rebuilt over the full unit corpus (not projected from scarce claims) or
   DROPPED from this round — testing it before B's substrate is fixed is meaningless; (d) S1
   grouping passes for any grouping-dependent verdict. If S1 fails, S2v2 may still compare
   non-grouping answer paths (for example B/D/A breadth and correctness), but it may NOT trigger
   Stage 3b, entity/community decisions, or any claim that grouping quality is solved.
8. **Decision rules (unchanged in substance, now fire only on S2v2 after the relevant entry
   conditions pass):** C-lite not clearly > B →
    **no entity layer** (H1 dies; §5 marked DEAD). D ≈ B/C on product value → prefer D's
    simplicity for reuse surfaces. A clearly wins user value AND the grounding gap demonstrably
    doesn't hurt real use → reassess the architecture-purity premium honestly. C-lite clearly
    wins on trusted-crystal / review / evidence-lookback → THEN design durable anchors,
    projection-first (ledger still last-resort).

Failed arm → its layer is not built; this document is amended with the evidence either way.

### 7.3 Round-2 result (2026-07-04, S2v2 partial compliance) — scoped citability + audit

Run: 100 sources · 30 corpus-derived tasks (6 intents × 5) · 4 arms · single blinded judge ·
0 parse errors. Winners: **A 16/30 · B 5/30 · D 5/30 · C 4/30.** Averages: B best
factual_correctness (3.83) + traceability (3.67); A best task_success (3.93) + coverage (3.90).
Full-corpus substrate facts: OVP durable = **158 active claims / 994 packs (~0.16/source)** + 120
caveated pending review; KMEM extracted memories on only **223/994 sources (22%)**, crystals
touch 82. Artifacts: `.run/m34-s2v2-20260704/`.

**Genuine improvements over round 1 (credit where due):** neutral task phrasing (no
evidence-format instructions), blinded randomized labels, judge told not to reward citations per
se, C-lite rebuilt over units (not claims), B fielded full-corpus claims + unit fallback,
stratified deterministic sampling with bucket balancing, retries → zero empty answers.

**Verified deviations — this round's biases run PRO-A (mirror image of round 1):**
1. **Sample conditioned on KMEM coverage** (`eligible = memories > 0` in the script): 100% of the
   sample has KMEM memories vs a 22% base rate. A's 16/30 is a home-game score on the fifth of
   the vault KMEM covers; OVP packs cover 98%. No whole-vault frame was reported.
2. **factual_correctness judged against the arm's OWN evidence, not the sources** (§7.2 requires
   source-truth). A's evidence is itself LLM-written summaries whose faithfulness was never
   checked; OVP arms carry verbatim quotes. The moat dimension was effectively removed from the
   metric. Also: judge = the answer-writing model family, single judge (acknowledged).
3. **OVP was fielded without its own breadth layer.** Reader cards — grounded per-source
   summaries with cited units, SHIPPED since M17 — are the direct analog of KMEM memories, and no
   arm included them. B fought orientation/review tasks with 29 claims + raw units against A's
   776 memories + 71 crystals. The fair OVP stack is claims + cards + units.
4. Minor: A's entity/community items were corpus-wide (not sample-scoped); §7.2's behavioral
   trust probe and per-arm corpus-coverage columns were not implemented; `unsupported_risk`
   naming ambiguous (rename next round).

**Cross-round convergent signals (robust — survived opposite biases in rounds 1 and 2):**
- Grounded durable claims: best factuality/traceability, worst coverage — BOTH rounds.
- Orientation/breadth tasks favor summary-style surfaces — BOTH rounds.
- C (anchors): weak in both rounds even when rebuilt over units — H1 still has NO supporting
  evidence; the entity route remains unproven.
- D: useful for lookup/evidence tasks, unstable without synthesis/ranking — both rounds.

**May be cited for:** the convergent signals; the substrate facts (0.16 claims/source; KMEM 22%
coverage); "on KMEM-covered sources, judged without source-truth verification, KMEM's surface
wins user-facing breadth tasks."
**May NOT be cited for:** "KMEM beats OVP" (home-game sample + no source-truth check), any
whole-vault verdict, or killing/keeping the entity layer.

**Round-3 (S2v3) required fixes:** dual-frame sampling (50 KMEM-covered + 50 uniform whole-vault,
both frames reported) · factual_correctness verified against source quotes/packs for ALL arms
(exposes summary unfaithfulness symmetrically) · B+ arm = claims + reader cards + units (the real
product stack) · second judge from a different model family · per-arm corpus-coverage and
whole-vault expected-score columns · trust probe · rename `unsupported_risk` →
`unsupported_penalty`.

**Product actions safe under ALL remaining outcomes** (no further eval needed to justify):
(a) surface reader cards in the retrieval/ask/index path — the breadth layer largely EXISTS and
was simply not fielded; (b) the review queue is now 120 caveated claims (was 6 at pilot scale) —
the G4 weekly review loop is urgent, exactly as `product-pipeline.md` predicted.

### 7.4 Round-3 result (2026-07-04/05, S2v3 full run) — DECISION RULES FIRED

Run: 100 sources in **two frames (K=50 KMEM-covered · U=50 uniform)** · 36 tasks (6 intents,
incl. trust_probe) · 5 arms · 180 answers · pre-registered spec (`S2V3_SPEC.md`, `protocol.json`)
· separate **source-truth pass** checking answer claims against opportunity sets carrying
verbatim quotes (case/unit/quote) · 0 parse errors. Artifacts: `.run/m34-s2v3-20260704/`.

| Arm | Utility wins | Task success | Supported | Unsupported | Opp coverage |
|---|---:|---:|---:|---:|---:|
| B+ cards+claims+units | **12** | 3.667 | 0.559 | 0.196 | 0.365 |
| D units only | 11 | 3.528 | **0.619** | 0.209 | 0.372 |
| Bcard cards only | 8 | 3.667 | 0.452 | 0.289 | **0.395** |
| A KMEM mem+crystal+entity | 5 | 2.667 | 0.317 | **0.431** | 0.214 |
| C-lite anchors | 0 | 1.528 | 0.332 | 0.513 | 0.105 |

With the round-2 biases fixed (source-truth restored symmetrically; OVP fielding its real stack;
uniform frame present), round 2's "A 16/30" inverted: A wins only 3/18 even in its home frame K.
**43% of A's answer claims could not be supported against source quotes** — the first
experimental measurement of KMEM-summary unfaithfulness, and the moat claim's first fair datum.
A's residual strength is real and specific: cross_source_synthesis (3 wins) — exactly where our
durable-claim layer is thinnest (claim overlap 0.287 of sample).

**Remaining caveats (disclose whenever citing):** single judge model family in BOTH passes
(the two-family rule is still unmet after three rounds — it must be satisfied before any result
is called final); the opportunity reference sets are built from OVP's unit layer, so content
KMEM captured outside unit coverage can be marked unverifiable (a pro-OVP tilt in opp_coverage;
S2v4 should build opportunities from raw source text independently); task mix is
per-source-question heavy, which rewards per-source coverage (honest for whole-vault reality,
but favors the 100%-coverage arms on orientation/lookup); tasks corpus-derived, not user logs;
13 short answers post-retry (one truncated Bcard answer, correctly penalized).

**Decision rules — fired (three rounds, converging under opposite biases):**
- **H1 DIES for the answer layer; §5 is marked DEAD as an answer-quality mechanism.** C-lite:
  0/6 → 4/30 → 0/36-with-worst-scores across three rounds, including this fairest one. Scope of
  death: anchor/entity projection as an answer-quality surface. Identity for *navigation/browse
  UX* was never separately tested and stays an open-but-deprioritized question — it may NOT be
  revived without a new experiment type (navigation tasks, not answer tasks).
- **Stage 3c branch selected: "B+, with D as verifier."** The product surface is the **grounded
  breadth stack** — retrieval over reader cards + durable claims + units — with read-time units
  as the verification/fallback core (D had the highest supported rate). No new persistent layer.
- **The KMEM gap to close is synthesis coverage, not graphs:** grow durable-claim breadth
  (grouping quality + synthesis batching + review-queue throughput), because cross-source
  synthesis is the one intent where A still wins.

## 8. Execution order

1. **Stage 3a (independent, ships first):** map-reduce execution layer — deterministic sub-batches
   per cluster → per-batch `crystal_synth/v1` → deterministic citation-overlap dedup → chunked
   strength (≤20 claims/call). Gives the current corpus a full-coverage crystal store NOW and is
   the machinery under any future grouping. (A model `crystal_merge/v1` reduce is added only if
   the deterministic dedup's residual duplicate rate proves the need — via the evolution flow.)
2. **S1** (grouping quality — needed by every arm), then **S2′ four-arm comparison** (offline,
   no product writes). The experiment verdict decides which of B / C / D becomes Stage 3b+.
3. **Stage 3b:** replace keyword buckets with L1/L2 communities behind the same crystal-synth CLI
   (this much is safe under every arm that wins).
4. **Stage 3c — SELECTED by §7.4 (2026-07-05): the grounded breadth stack ("B+, with D as
   verifier").** Productize retrieval/ask over reader cards + durable claims + units, with
   read-time unit verification as the fallback core. NO entity/anchor layer (H1 dead for
   answers). The competitive gap to close vs KMEM is cross-source synthesis coverage — grouping
   quality, synthesis batching, review-queue throughput — not graphs.
5. M32 Level-3 exit criterion #3 ("crystallize the full corpus") is satisfied by Stage 3a
   coverage; quality re-crystallization after 3b is a product improvement, not a retirement gate.

## 9. Non-goals (unchanged or newly explicit)

- Query-surface semantic RAG (ask/find/digest stay lexical until query pain proves otherwise).
- MOC/Atlas/evergreen revival; eager ingest-time concept extraction; ConceptRegistry as authority.
- Importing KMEM's KG/relations as a dependency (eval-only stays eval-only).
- Vector DB service, graph DB, or any new daemon.
- **A relation-extraction stage** — IF an anchor layer is ever built (§7 verdict pending), edges
  come from claims (entity–claim–entity, quote-chained by construction) rather than a separate
  LLM relation extractor. Whether that provenance advantage over KMEM-style `{relationships}`
  translates into USER value is exactly what S2′ measures — it is not assumed here.
- **Entity type taxonomy and merge/split governance UI in v1** (§5 cuts).
- **Per-mention LLM judging** — LLM spend is confined to batched T3→T4 judgments.

## Appendix A — KMEM observed facts (2026-07-02 inspection; docs + API + live state)

Recorded here because §2/§5 differentiation claims rest on them. KMEM (Nowledge Mem) pipeline:
deterministic parse/chunk/index → LLM agent extracts 3–8 self-contained memories per source →
entities are **post-processed** from `memory.title + memory.content` (NOT ingest-inline — note
this matches our "never eager at ingest" principle; convergent evolution), with the LLM directly
emitting `{name, type, description, aliases, relationships}`; dedup = FTS prefilter + LLM
duplicate-verify + `lower(name)+entity_type` at the write layer; crystals are
`MemoryNode(is_crystal=true)` linked `CRYSTALLIZED_FROM` source memories. **No quote/evidence-span
storage anywhere — provenance is source/chunk- or memory-level, never verbatim-quote-level.**
Live graph shows generic-entity pollution (`context`, `Agent`, `Skills` as high-confidence
entities). Strengths to respect: fast, productized, rich graph, mature retrieval/visualization.
The weakness OVP attacks: identity and claims without hard evidence chains.
