# Stage M25 — Crystal Review Workbench

**Goal:** make the human review of caveated Crystal claims efficient by splitting the work
by role — **AI moves evidence + proposes rewrites, the human judges, the gate decides** — so
nobody reads raw JSON or hunts for quotes, and Knowledge Mem is used as 旁证 (reference) not
as a judge. This operationalizes the next step from M24 (the 6 caveated claims).

## Role separation (the whole point)

```
OVP caveated claim + cited quotes + source excerpt + KMEM source memories
  → [AI] comparative evidence review (support vs over-strong, KMEM 旁证, suggested rewrite)
  → [Human] judgment (accept rewrite / split / keep caveated / reject) in the workbench
  → [Gate] revised candidate → citation linter → claim-strength gate → crystal-write
  → durable / still-caveated / reject  (the GATE decides, never the human or the AI)
```

The human never marks something durable; they can only author a **revised structured
candidate** that re-enters the same M22/M23 gate. KMEM is reference-only: it has no
sentence-level provenance and never feeds the linter / score / strength gate.

## What shipped

1. **`crystal-review apply` (Rust CLI)** — `crystal.rs::apply_decisions` turns reviewer
   decisions (`Rewrite` / `Split` / `KeepCaveated` / `Reject`, each carrying full structured
   revision claims) into a revised `CrystalCandidate`. New ids are traceable (`c02` → `c02r`,
   `c10` → `c10s1/s2`). Fail-loud on unknown claim ids. It authors a candidate; it does NOT
   decide durability. (+3 tests.)
2. **`scripts/m25_review_pack.py`** — assembles, per caveated claim: claim + why-not-durable
   + OVP cited quotes with resolved line + a **source excerpt that locates the verbatim
   quote** (robust to paragraph-anchor line drift) + KMEM source-scoped comparable memories
   (旁证, labeled). Reuses `.run/m22` + `.run/m21` + KMEM at `127.0.0.1:14242`.
3. **AI evidence-review workflow** — one judge per caveated claim, seeing ONLY the claim +
   its cited quotes + KMEM 旁证. Outputs `supported_parts`, `overstrong_terms`,
   `kmem_relation` (supports/weakly_supports/missing/contradicts), `recommendation`
   (promote/rewrite/split/keep/reject), a `suggested_claim`, `suggested_citations_drop`,
   `risk`. Comparative, not free-association.
4. **`scripts/m25_build_workbench.py`** — a self-contained HTML workbench: one card per
   claim (claim, why-not-durable, OVP evidence quotes+line+excerpt, KMEM box visually fenced
   as reference-only, AI recommendation + suggested rewrite, human-decision field) + a
   `decisions.template.json` prefilled with the AI suggestion for the human to edit.

## Demonstration — the loop closes, and the gate re-decides

Using the AI suggestions as **stand-in demo decisions** (clearly NOT human authority) over
the 6 M24 caveated claims, written to a **separate demo store** (`.run/m25/demo-store`) so the
real v1 store is untouched:

- `crystal-review apply` → 6 revised claims (5 rewrite, 1 split).
- revised candidate re-linted → 6/6 citations still ground verbatim.
- **claim-strength gate re-judged:** c02r/c04r/c07r/c08r/c12r → `supported`; **c10r →
  `over_synthesized`** (the malformed multi-claim "split" blob was correctly caught — the gate
  does NOT rubber-stamp rewrites).
- `crystal-write` → **5 promoted to durable, 1 held caveated.**

So the workbench converts review insight → durable truth **only through the gate**. If a human
accepted these (real run), v1 would grow 8 → 13 durable, with c10 still needing a proper
structured split. The real M24 v1 store stayed at 8 (verified) — the demo wrote elsewhere.

Representative AI calls (faithful, not lenient): c07 "drop the fragment citation
`u-003-...`, remove 'scalable'/'discriminative-task bias' (in no quote)"; c12 "restore the
hedged 'may' — the source says 可能"; c02 "it's one author, not 'multiple sources'".

## Verification

- `cargo test --workspace` → **556 passed, 1 ignored, 0 failed** (+3 apply_decisions tests).
- `cargo clippy --workspace --all-targets -- -D warnings` → clean (also `--features anthropic`).
- `bash scripts/check_architecture.sh` → **Architecture check passed.**
- Forbidden-path audit: no `.run/` / `.env*` / cassettes / KMEM dumps / vault output staged.
- Real M24 v1 durable store untouched (8 claims); demo wrote to `.run/m25/demo-store`.

## Committed vs not

**Committed (Rust + scripts + doc):** `crystal.rs` (`apply_decisions` + review-decision
types), `crystal_review.rs` (new CLI), `mod.rs`/`main.rs` wiring, `scripts/m25_review_pack.py`,
`scripts/m25_build_workbench.py`, this doc. **Not committed (gitignored `.run/`):** the
review pack, AI reviews, workbench HTML, decisions, revised candidate/strength, demo store,
all KMEM data. No raw model replies / cassettes / KMEM dumps / vault output.

## How a real review runs (efficient path for the human)

1. `m25_review_pack.py` → `m25-evidence-review` workflow → `m25_build_workbench.py`.
2. Open `.run/m25/workbench/index.html`: per claim, read the AI recommendation + suggested
   rewrite, one-click see the quote + line + source excerpt and the KMEM 旁证.
3. Edit `decisions.template.json` (accept / adjust the rewrite, author splits, keep, reject).
4. `crystal-review apply` → re-run claim-strength gate → `crystal-write` (target the real
   v1 store). The gate decides what becomes durable.

**M25 verdict: PASS.** The review is now efficient and role-correct: AI does evidence
movement + rewrites, the human judges on a workbench without reading JSON, KMEM is 旁证 not
judge, and durability is still decided only by the gate. The loop is demonstrated end-to-end
(5/6 rewrites would promote; the bad split correctly blocked). Next: run a real human pass to
promote the genuinely-good rewrites into the v1 store, then content expansion.
