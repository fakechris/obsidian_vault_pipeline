# Stage M23 — Minimal Durable Crystal Store

**Goal:** close the pre-Crystal gate phase. M22 decides *which* Crystal claims may be
written; M23 defines *how* they are persisted, audited, and superseded/retracted. This is a
**minimal, auditable, accept-or-refuse durable-write contract** — not a big system. After
M23, the pre-Crystal-gate phase is over; the next phase is real Crystal content.

**Scope guardrails (held):** file-based store (no DB), Rust core + CLI (not a Python
script), no real-vault write, no graph, no graphics, no Referent revival, no RAG, no
auto-merge / semantic dedup, no daemon. The demo reuses the existing `.run/m22` artifacts
(no re-eval).

## The closed loop

```
source grounded units  →  M22 candidate (structured citations)
   →  citation/provenance gate  →  claim-strength gate  →  final_routing
   →  [final == Durable]  →  durable Crystal store (append-only ledger)
   →  human-readable Crystal view (crystal.md)        [caveated/reject → review.json]
```

`ovp-cli crystal-write --candidate <c> --packs-dir <d> --strength <v> --store <dir>` runs the
**full** pre-write gate and writes only on eligibility.

## Durable-write rules (enforced; refuses + writes nothing on any gap)

- Only `final == Durable` claims enter the store. `caveated` / `reject` go to `review.json`
  and the Crystal view's Review section — **never** durable truth.
- The writer **refuses** (non-zero, nothing written) on: no strength gate, incomplete /
  duplicate / unknown strength verdicts, `quarantine > 0` (citation defects), any
  `final == reject`, or any claim lacking a full provenance chain. Verified: incomplete
  strength → refused; a quarantine candidate → refused; no `ledger.jsonl` created in either.

## What a durable record preserves (full audit chain)

Each ledger record carries: `claim_key` (deterministic identity), `claim_id`, `claim` text,
`theme`, `source_cases`, `citations[{case_id, unit_id, quote, resolved_line}]`,
`provenance_score`, `provenance_class`, `strength`, `strength_rationale`, `final_class`,
`run_id`, `status`. Every durable claim is traceable: claim → cited unit → **verbatim
quote** → **source line** (the line resolved from the unit, not the model).

## Append-only + auditable + idempotent

- The store is an **append-only event ledger** (`ledger.jsonl`, one JSON `StoreEvent` per
  line). Nothing is overwritten in place; the full history is reconstructible by folding the
  events (`fold_ledger`, pure + tested). No naked rewrite.
- **Idempotent:** a claim's `claim_key` is `sha256(claim_text + sorted case:unit citation
  set)`. Re-running the same input appends nothing (the writer skips keys already Active).
  Verified: second identical run → "0 newly appended", ledger stays 8 lines.
- **run_id** is a deterministic hash of the written claim ids (no wall-clock), so re-runs are
  stable and tests are deterministic; `--run-id` can override.

## Supersede / retract (minimal mechanism, real — not just designed)

`StoreOp { Write | Supersede | Retract }` + `CrystalStatus { Active | Superseded | Retracted
| Draft }`. The fold honors them: a `Retract` event flips a claim to Retracted; a
`Supersede` event makes the new record Active and flips its predecessor (`supersedes` key) to
Superseded. The view renders only Active records. The data model + fold are implemented and
unit-tested (`fold_write_then_retract_then_supersede`); a `Retract`/`Supersede` *event*
appended to the ledger is honored on the next render. (A dedicated `crystal-retract` CLI verb
is a trivial follow-on over this same append-and-fold path — the mechanism, the audit
behavior, and the data contract are all already in place, so it does not block entering the
Crystal phase.)

## Demonstration on the real M22 candidate

```
crystal-write … → run_id=run-d476d236
  eligible: 8 durable claim(s) considered, 8 newly appended (0 already active)
  store: 8 active durable claim(s) total
  review (NOT durable): 6 caveated/reject claim(s)
```

The current real candidate lands exactly as M22 routed it: **8 durable claims in the store,
6 caveated claims in `review.json`** (visible, never durable). `crystal.md` shows each durable
claim with an expandable provenance block (e.g. claim 1 cites 5 units across m18-01/02/08/11,
each with a verbatim quote + source line) and a separate Review section listing the 6 caveated
claims with the judge's rationale (e.g. c12 "states a hedged 可能/may conditional as fact").

## Verification

- `cargo test --workspace` → **553 passed, 1 ignored, 0 failed** (crystal: 6 linter/scoring +
  5 claim-strength combiner + 3 strength-coverage + 5 durable-store = 19).
- `cargo clippy --workspace --all-targets -- -D warnings` → clean (also `-p ovp-cli --features anthropic`).
- `bash scripts/check_architecture.sh` → **Architecture check passed.**
- Forbidden-path audit: no `.run/` / `.env*` / cassettes / KMEM dumps / vault output staged.

## Committed vs not

**Committed (Rust + docs):** `crystal.rs` (store types + `claim_key`, `build_durable_record`,
`fold_ledger`, `active_keys`, `render_crystal_md`, `default_run_id`, `strength_coverage`),
`crystal_lint.rs` (completeness gate), `crystal_write.rs` (new), `mod.rs`/`main.rs` wiring,
this doc + the M22 closeout. **Not committed (gitignored `.run/`):** the candidate, strength
verdicts, the `.run/m23/store/` ledger + crystal.md + review.json, all M20/M22 artifacts.

## Final answers

1. **M22 gate blockers left?** None. M22 is a complete pre-write gate: citation linter +
   deterministic provenance + claim-strength gate + **verdict-completeness validation** that
   fails loud on partial/duplicate/unknown verdicts. `eligible_for_durable_write` is an
   explicit, machine-checkable flag.
2. **What does M23 write, where, audited how?** Durable claims → an append-only
   `ledger.jsonl` in a file store; each record carries the full chain (claim → unit → quote →
   line) + provenance + strength + final class + run id + status. Audit = fold the ledger;
   history is never overwritten; re-runs are idempotent by `claim_key`.
3. **Where do the real 8 durable / 6 caveated land?** 8 → durable ledger + `crystal.md`; 6 →
   `review.json` + the view's Review section, explicitly NOT durable truth.
4. **Why is this not Referent/graph/RAG revival?** No entity/relation extraction, no graph,
   no embeddings/retrieval, no canonical/Referent store. The store persists exactly the
   gated, quote-grounded claims the reader trunk already produced — it adds persistence +
   audit + lifecycle, nothing ontological.
5. **How to enter the Crystal phase now (not more gate iteration)?** The contract is fixed:
   produce structured-citation candidates → run the full gate → `crystal-write`. Crystal work
   is now *content* (more/better candidates, human review of the `caveated` set, an optional
   visual view derived from already-gated claims) on top of a frozen, audited write path — not
   further pre-gate tuning.

**M23 verdict: PASS (minimal durable store).** Pre-Crystal gates are closed; durable Crystal
write is a real, refusing, append-only, idempotent, fully-provenanced product path.
