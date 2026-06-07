# Stage M24 — Crystal Content Phase (Crystal v1)

**This stage starts producing Crystal content.** It is NOT more pre-Crystal gate work. The
M22 (full pre-write gate) + M23 (durable append-only store) contract is frozen; M24 uses it
as-is to produce the first auditable, human-readable, reusable Crystal. Only one tiny M23
polish (P2, review-section readability) was allowed — no gate-semantics or threshold changes.

## Task A — M23 P2 polish (review-section readability)

Caveated/rejected claims were previously shown as `claim_id + final + rationale`, forcing a
reviewer back to the candidate to see the claim. Now `review.json` and `crystal.md`'s Review
section carry, per claim: **claim_id, claim text, theme, final class, strength,
evidence_sufficient, rationale** (`ReviewEntry`). Durable ledger schema is **unchanged** (only
the review output / view changed — caveated claims still never enter durable truth). Test
`render_separates_durable_from_review` now asserts the caveated claim's text is present.

## Task B — Crystal v1

### 1. Scope (explicit)

**OVP Crystal v1 — Agent Memory & Context Systems:** agent memory architectures, context
engineering, evaluation discipline, and durable knowledge systems, synthesized from **15 of
20** held-out 2026 sources. **Out of scope / weakly represented (not cited by any v1 claim):**
m18-09 (connecting agents to decisions), m18-15 (GPU/CUDA, zh), m18-16 (WebRTC chatroom, zh),
m18-17 (why I don't vibe code), m18-18 (agents from first principles). These are *declared
out-of-scope*, not silently dropped — a later Crystal can extend coverage.

### 2. Pipeline (existing M22/M23 contract, unchanged)

Structured-citation candidate (`.run/m22/candidate.json`, 14 claims) → full M22 gate
(citation/provenance + claim-strength verdicts `.run/m22/strength-verdicts.json`) →
`crystal-write` → durable store at `.run/m24/store/` (gitignored). Only `final == Durable`
entered the ledger; caveated → review. Run id `m24-v1`; re-running is idempotent (second run:
0 newly appended, ledger stays 8 lines).

### 3. Durable truth — 8 claims

Each is grounded (claim → accepted unit → verbatim quote → source line) and passed BOTH gates:

| # | theme | sources |
|---|-------|---------|
| c01 | Filesystem-as-memory beats bespoke infra | m18-01/02/08/11 |
| c03 | Pure semantic retrieval is insufficient at scale | m18-02/05 |
| c05 | Freeze memory snapshot at session start | m18-08/11 |
| c06 | Offline "dreaming" consolidation pattern | m18-02/04 |
| c09 | Context rot motivates compression | m18-05/08 |
| c11 | On-demand skills / progressive disclosure | m18-01/07 |
| c13 | Bi-temporal modeling for knowledge updates | m18-02/19 |
| c14 | Human governance gate on durable memory | m18-06/08/20 |

### 4. Review (caveated) — 6 claims, NOT durable

All have real, verbatim citations (provenance-durable) but the claim-strength judge flagged
the synthesis as overreaching the cited evidence, so they route to `caveated` — visible in
`review.json` + the Review section, never durable truth:

| id | theme | strength | why not durable |
|----|-------|----------|-----------------|
| c02 | Retrieval policy is the real problem | over_synthesized | "multiple sources independently" rests on a single author |
| c04 | Prompt-cache economics drive memory design | over_synthesized | "first-class memory-engineering constraint" framing not in quotes |
| c07 | LLM-as-judge needs human calibration | over_synthesized | "scalable" + "discriminative-task bias" appear in no cited quote |
| c08 | Eval-driven development over vibes | over_synthesized | causal "because brittle/nondeterministic" not jointly asserted |
| c10 | Sandbox + least-privilege injection defense | over_synthesized | welds in "ephemeral sandboxes" present in no quote |
| c12 | Context layer as the durable moat | opinion_as_fact | rests on a hedged "可能/may" conditional stated as established fact |

### 5. Auditing to source quote

Every durable claim in `crystal.md` has an expandable Provenance block listing each citation
as `(case_id) unit_id · line N: "verbatim quote"`. The line is resolved from the cited unit
(not the model). The ledger record carries the same chain plus provenance score/class,
strength verdict, final class, run id, status — so any claim is traceable from the Crystal
view down to the exact accepted unit and source line.

### 6. Why this is NOT Referent / graph / RAG

No entity/relation extraction, no graph, no embeddings/retrieval, no canonical/Referent
store, no semantic dedup, no merge. M24 is *content selection + persistence*: it runs the
already-built gate over a structured candidate and writes the surviving quote-grounded claims
to an append-only store. Nothing ontological was added.

## Verification

- `cargo test --workspace` → **553 passed, 1 ignored, 0 failed**.
- `cargo clippy --workspace --all-targets -- -D warnings` → clean (also `--features anthropic`).
- `bash scripts/check_architecture.sh` → **Architecture check passed.**
- Idempotency: re-run of `crystal-write` appends 0, ledger stays 8 lines.
- Forbidden-path audit: no `.run/` / `.env*` / cassettes / KMEM dumps / vault output staged.

## Committed vs not

**Committed (Rust + doc):** `crystal.rs` (`ReviewEntry`, `CrystalHeader`, richer
`render_crystal_md`), `crystal_write.rs` (review enrichment + header flags), `main.rs` flags,
this doc. **Not committed (gitignored `.run/m24/`):** the candidate, strength verdicts, the
`.run/m24/store/` ledger + crystal.md + review.json. No raw model replies / cassettes / KMEM
dumps / real vault output.

## Final answers

1. **Scope?** Agent memory architectures, context engineering, evaluation discipline, durable
   knowledge systems — from 15/20 sources. (Title: "OVP Crystal v1 — Agent Memory & Context
   Systems".)
2. **Durable truth?** **8 claims** (c01, c03, c05, c06, c09, c11, c13, c14) — table §3.
3. **Caveated/review?** **6 claims** (c02, c04, c07, c08, c10, c12) — table §4. Not durable
   because the claim-strength gate found the synthesis overreached its cited evidence
   (5 over_synthesized, 1 opinion_as_fact); citations are real, the framing is too strong.
4. **Not covered?** m18-09, m18-15 (GPU), m18-16 (WebRTC), m18-17 (vibe-coding), m18-18
   (first principles) — declared out-of-scope for v1.
5. **Audit to source quote?** Each durable claim's Provenance block + ledger record gives
   `case_id → unit_id → verbatim quote → source line`; line resolved from the unit.
6. **Why not Referent/graph/RAG?** No extraction/graph/embeddings/canonical store — only
   gated, quote-grounded claims persisted append-only.
7. **Next step?** **Human review of the 6 caveated claims** is the highest-value next move:
   each is one author-framing fix away from durable (e.g. c02 re-scoped to one author, c12
   restored to its hedged form). That converts review insight → durable truth with no new
   architecture. Content expansion (cover the 5 out-of-scope sources) and an optional
   read-only visual view (derived strictly from gated claims) come after.

**M24 verdict: PASS — Crystal content phase has begun.** OVP Next has moved from reader trunk
through a frozen pre-write gate into a durable Crystal content workflow: a real, scoped,
audited Crystal v1 (8 durable / 6 caveated) exists and is reproducible. Further work is
content + human review, not pre-Crystal gate iteration.
