# Stage M34 — Knowledge Substrate Design: similarity · grouping · identity · lifecycle

**Type:** Architecture design anchor (like [`stage-m32`](./stage-m32-python-retirement-and-product-definition.md)
is the product anchor). NOT a feature epic; implementation is gated on the spikes in §7.
**Status:** Direction operator-approved 2026-07-02. Spikes not yet run.
**Companions:** [`stage-m32-python-retirement-and-product-definition.md`](./stage-m32-python-retirement-and-product-definition.md) ·
2026-07-02 full-corpus evidence in `.run/m32-stage123-20260702/` (gitignored, operator machine).

> **Read this first.** This document deliberately re-opens one settled-looking question — "does OVP
> need entities?" — from first principles, at the operator's direction. The M13 demotion is NOT
> re-litigated as a *method* verdict (eager, ungrounded ontology stays dead); §5 explains precisely
> what is different this time and §7-S2 defines the experiment that decides it.

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

## 2. Decisions (operator-approved 2026-07-02)

1. **Embeddings: YES** — local multilingual model (bge-m3 / multilingual-e5 class; runnable via
   qmd/ollama), as **internal, rebuildable synthesis infrastructure**, NOT the query surface.
   Vectors are content-hash cached on disk (same discipline as cassettes; provider swappable).
   This exercises M32 §9's "deferred, not cut — revisit only if real usage proves the need"
   clause: 87% drop + 40% misc IS the proof. M32 §7's "RAG — lexical is the product" stays true
   for the **query/reuse surface** (`ask`/`find`/`digest` remain lexical).
2. **Entities: YES, as *earned identity*** (§4 L3, §5) — gated the same way claims are. Eager
   extraction, model-as-identity-authority, and ontology-first product surfaces stay dead.
3. **Claim lifecycle**: lineage keyed by (subject, intent); strengthen / append / contradict
   lanes; the store invariant migrates from batch-replay determinism to **event-sourced
   auditability** (§6).
4. **No vector DB service (qdrant etc.) for now** — in-memory kNN over cached vectors is enough at
   10^3–10^4 cases. Same pain-proves-need discipline as the M31 NO-SQLite decision. Qdrant remains
   the designated L1 backend IF scale proves the pain.

## 3. First-principles: does a growing knowledge base need entities?

Derived from product functions, not from technology choices:

| Function | Can similarity (embeddings) deliver it? | Needs identity? |
|---|---|---|
| Aggregation: "everything about X" | No — similarity is soft, thresholded, non-referable; you cannot "click open" a similarity blob | **Yes** |
| Stable navigation anchors | No — communities shift on every re-cluster; last month's "community #7" is gone | **Yes** |
| Cross-language binding (中英 same thing) | Candidate signal only — cosine proximity is not sameness | **Yes** (evidence-adjudicated) |
| Claim lifecycle: supersede / contradiction | No — content-hash too strict, cosine too soft; needs "same subject, same intent" structure | **Yes** |
| Grouping for synthesis | **Yes** — communities suffice; identity not required here | No |

Conclusion: a **read-only reader** product needs no entities; a product that promises
ask/digest/browse over a **continuously growing** library — ours, per the M32 product definition —
cannot ship those functions without an identity layer. Entities are required by the product, not
by the graph technology.

## 4. The four-layer substrate

```
L0 truth      (exists, FROZEN)  units: verbatim quote + line; fail-loud gate; the moat
L1 similarity (new)             embeddings over cases/units; soft structure; content-hash
                                cached; rebuildable at any time; carries NO identity, NO
                                authority, writes NO product state
L2 grouping   (new)             communities on the L1 kNN graph (deterministic label
                                propagation, sort-based tie-breaks); EPHEMERAL — exists only
                                to feed synthesis; rebuilt freely; never user-facing identity
L3 identity   (new, slow)       grounded entities via the T1→T4 promotion ladder (§5):
                                cheap tiers (topics/common anchors) exist from day one at
                                zero LLM cost; durable identity ONLY through the T4
                                evidence gate; claims subject-bind progressively
lifecycle     (redesign)        claim lineage = (subject, intent); strengthen / append /
                                contradict; append-only ledger + superseded_by (§6)
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

## 5. Entities: a promotion ladder toward earned identity (revised 2026-07-02, operator review)

What M13/M14b actually falsified (0/3 on real models): **eager** extraction at ingest +
**model-as-identity-authority** (no evidence gate) + **product surfaces stacked on the ungrounded
ontology** (MOC/Atlas/evergreen). What it did not test: entities subjected to the same evidence
discipline that makes claims durable.

**But "every entity must be earned" does not productize** (operator review, 2026-07-02): a student
importing ONE lecture PDF must immediately see browsable topics; common-sense proper nouns
(`Qdrant`, `BGE-M3`, `Attention Is All You Need`) must not wait for a ≥3-source gate; and judging
every mention with an LLM explodes token cost. The answer is not loosening the durable gate — it
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
2. **Genericity ceiling (deterministic).** A surface form whose document frequency exceeds a
   corpus threshold (`AI`, `agent`, `context`, `模型`…) is capped at T1 forever and never enters
   the candidate queue. Zero tokens, pure statistics — this is the guard KMEM lacks (its live
   graph carries `context`/`Agent`/`Skills` as high-confidence entities; observed 2026-07-02).
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

**Relations need no extractor — the claim IS the edge.** A durable claim citing units under two
anchors is an evidence-backed edge between them, by construction. The graph is
entity–claim–entity with every edge quote-chained. KMEM must ask the LLM for
`{relationships}` (unevidenced); OVP gets a stronger graph structurally, for free. A separate
relation-extraction stage is a **non-goal** (§9).

**Product surface:** users see three natural things — **Topics** (fast, allowed to be imperfect),
**Crystals** (cross-source conclusions), **Evidence** (one click to the quote). "Durable
identity", tiers, and gates are internal mechanics, never user-facing vocabulary.

**Cut from v1 (anti-over-engineering, operator-directed):** entity type taxonomy
(person/org/tool… — metadata, not gate input; an open tag at most), relation extraction (see
above), and merge/split governance UI (the append-only ledger records enough to build it later).

This EXTENDS the moat rather than betraying it: "every durable thing traces to source quotes" now
covers claims AND entities. The design sentence: **KMEM proved the product value of
entity/community/crystal; OVP proves those can exist under quote-grounded, audit-first
constraints** — fast tiers carry the product experience, earned identity carries the long-term
trusted structure.

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

## 7. Spikes — data decides, before any product code

All three run offline on the real 994-pack corpus; artifacts under `.run/m34-spikes/` (never /tmp).

- **S1 — community quality (L1/L2).** Embed 994 cases (title + representative units) → kNN →
  deterministic communities. Accept iff: 20 sampled communities show **≥80% member topical
  coherence** (human-judged) AND **≥3 genuinely bilingual communities** exist (中英 same-topic
  sources actually co-clustered). Compare directly against keyword-bucket assignment on the same
  sample.
- **S2 — entity candidates (the M13 counterfactual).** Bottom-up mining through the T1→T3 funnel
  (deterministic mining → genericity ceiling → frequency/value filters → evidence packs); batch
  judge to T4. Accept iff ALL of:
  (a) **precision** — ≥80% of 20 sampled T4 candidates judged "a real thing worth aggregating by";
  (b) **recall** — against a human-built reference set (a person lists the identity anchors they
  would expect from a sampled slice of the corpus BEFORE seeing system output), the funnel
  recovers **≥70%**; precision-only would let the system flatter itself (operator requirement);
  (c) alias binding correct, incl. at least one 中英 pair;
  (d) **cold-start check** — on ONE single source (the student scenario), T1/T2 immediately
  surface ≥80% of the topics/proper nouns a human lists for that source, with zero LLM calls.
  Explicitly contrast with M13's 0/3: same corpus family, opposite method.
- **S3 — claim lineage.** Take the 22 (repro) + 19 (capped full run) durable claims; simulate a
  second synthesis pass; hand-label ground truth; measure lineage adjudication
  (strengthen/append) accuracy — accept at **≥90%**. Contradiction lane is measured but not
  gated (needs L3).

Failed spike → the corresponding layer does not get built; this document is amended with the
evidence. Passed spikes → Stage 3b (L1/L2 productization), Stage 3c (L3 + lifecycle), each a
normal PR-gated stage.

## 8. Execution order

1. **Stage 3a (independent, ships first):** map-reduce execution layer — deterministic sub-batches
   per cluster → per-batch `crystal_synth/v1` → deterministic citation-overlap dedup → chunked
   strength (≤20 claims/call). Gives the current corpus a full-coverage crystal store NOW and is
   the machinery under any future grouping. (A model `crystal_merge/v1` reduce is added only if
   the deterministic dedup's residual duplicate rate proves the need — via the evolution flow.)
2. **S1 → S2 → S3 spikes** (offline, no product writes).
3. **Stage 3b:** replace keyword buckets with L1/L2 communities behind the same crystal-synth CLI.
4. **Stage 3c:** L3 entity gate + lineage/supersede ledger semantics + contradiction lane.
5. M32 Level-3 exit criterion #3 ("crystallize the full corpus") is satisfied by Stage 3a
   coverage; quality re-crystallization after 3b is a product improvement, not a retirement gate.

## 9. Non-goals (unchanged or newly explicit)

- Query-surface semantic RAG (ask/find/digest stay lexical until query pain proves otherwise).
- MOC/Atlas/evergreen revival; eager ingest-time concept extraction; ConceptRegistry as authority.
- Importing KMEM's KG/relations as a dependency (eval-only stays eval-only).
- Vector DB service, graph DB, or any new daemon.
- **A relation-extraction stage** — the claim is the edge (§5); LLM-asserted, unevidenced
  `{relationships}` à la KMEM are structurally inferior to entity–claim–entity and are not built.
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
