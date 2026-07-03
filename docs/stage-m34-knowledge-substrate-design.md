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
L3 identity   (new, slow)       grounded entities: mined bottom-up from repeated L1/L2
                                cohesion; durable ONLY through the entity-write gate (§5);
                                claims reference entities via subject binding
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

## 5. Entities: earned identity, and the boundary with M13

What M13/M14b actually falsified (0/3 on real models): **eager** extraction at ingest +
**model-as-identity-authority** (no evidence gate) + **product surfaces stacked on the ungrounded
ontology** (MOC/Atlas/evergreen). What it did not test: entities subjected to the same evidence
discipline that makes claims durable. Claims earn durability (verbatim quote, ≥2 sources,
strength gate, fail-loud); entities were never given the equivalent.

**Entity-write gate (draft, to be calibrated in S2):** an entity candidate becomes durable only
when ALL hold:

- mentions in **≥3 sources**, each mention bound to a verbatim quote + line (the units already
  carry these);
- **stable surface forms** across time (not a one-week flash), aliases (incl. 中英) bound only by
  co-mention evidence + embedding candidacy, never by model assertion alone;
- passes an entity-strength judgment (model-assisted, evidence-quoted, same shape as
  `crystal_strength`);
- append-only entity ledger with full provenance (who/what/when/quotes), idempotent by entity key.

This EXTENDS the moat rather than betraying it: "every durable thing traces to source quotes" now
covers claims AND entities — a stronger differentiation vs KMEM, not a weaker one.

**Kill criterion:** if S2 (§7) fails human check the way M13 did, entities stay out, this section
is marked DEAD, and we document why bottom-up + evidence-gated was still not enough. The
demotion of eager ontology is permanent either way.

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
- **S2 — entity candidates (the M13 counterfactual).** Bottom-up mining: recurring quote-anchored
  mention clusters across ≥3 sources → candidates with evidence chains. Accept iff **≥80% of 20
  sampled candidates** are judged "a real thing worth aggregating by" AND alias binding (incl. one
  中英 pair) is correct. Explicitly contrast with M13's 0/3: same corpus family, opposite method.
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
