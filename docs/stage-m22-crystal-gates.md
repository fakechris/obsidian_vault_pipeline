# Stage M22 — Crystal pre-write gates (citation linter + provenance scoring)

**Goal:** before any future *durable* Crystal (cross-source synthesis), put every
candidate through two gates so a Crystal can never become a pretty-but-untraceable
summary — the exact failure that separates OVP from a memory-blob system. **Scope: gates
only** — no durable write, no graph, no Referent/RAG, no prompt tuning, no threshold tuning
after the run.

This stage acts on the M21.1 finding and the operator's M22 direction. A resolvability
probe of the M21 synthesis was the trigger: of its 46 prose citations, all 46 named a real
`case_id`, but only **3 of 14** quoted refs verified verbatim and **2 of 36** line numbers
matched a real unit. The citations *looked* professional but were mostly not mechanically
verifiable. Root cause: the synthesis emitted **prose** citations, not resolvable ones.

## What shipped

### 1. Structured, resolvable citation schema (the prerequisite)

`crates/ovp-domain/src/crystal.rs` — a Crystal candidate is `{ items: [ { id, claim, theme,
citations: [{ case_id, unit_id, quote, claimed_line? }], caveat } ] }`. A citation names a
specific accepted `unit_id` and a **verbatim** `quote`; `claimed_line` is advisory only. The
authoritative line is resolved **from the unit**, never trusted from the model — which makes
line-number drift (the 2/36 problem) **impossible by construction**.

### 2. Citation linter — mechanical, reuses the truth-layer matcher

`lint_candidate()` resolves each citation: `case_id` → `unit_id` → the unit is **Accepted**
→ the cited `quote` is a **verbatim substring** of the unit's already-source-verified quote,
checked with the SAME `deterministic_contains` the validator uses (so the Crystal gate can
never drift from the reader trunk). Defects are classified: `case_not_found`,
`unit_not_found`, `unit_not_accepted`, `quote_not_in_unit`. A claim is `fully_grounded` iff
it has ≥1 citation and **every** citation grounds cleanly. No model call.

### 3. Provenance scoring — deterministic signals only

`score_candidate()` scores each claim from mechanical signals: distinct supporting sources,
grounded fraction, citation concentration (all-one-unit / all-one-case / spread). Frozen
weights (grounding 0.5, source-diversity 0.3 capped at 3, spread 0.2) and **frozen
thresholds set before the run** (`DURABLE_MIN_SCORE=0.70`, `DURABLE_MIN_SOURCES=2`).
Recommendation: `Durable | Caveated | Quarantine`. **Fail-loud:** a claim that fails the
linter is forced to `Quarantine` regardless of score — it can never be written durably (the
Crystal analog of the trunk's `accepted_without_quote=0`).

**Deliberately NOT in this deterministic score:** the semantic "does the claim's strength
exceed its evidence?" / over-synthesis judgment. That is a SEPARATE, model-based, clearly
labeled gate (a review workflow) — kept out so the score stays auditable. It is scoped for a
later stage, **not built here.**

### 4. Runnable gate (Rust CLI, not a script)

`ovp-cli crystal-lint --candidate <json> --packs-dir <dir> --out <json>` builds the
grounding index from each case's committed `units.accepted.json`, runs the linter + scoring,
prints a summary, writes a structured report. It lives in the typed core (reuses the
validator), per the rule that load-bearing gates are Rust, not offline scripts.

## Demonstration on real data (the falsifiable milestone)

A structured-citation synthesis was regenerated over the same 20 M20 packs (one agent,
review-only, copying real `unit_id`s + verbatim quote fragments from the packs) and linted:

| metric | M21 prose citations | M22 structured citations |
|--------|:---:|:---:|
| citations resolving verbatim | **3 / 14** quoted refs | **52 / 52** |
| line numbers matching a real unit | 2 / 36 | resolved from unit (drift impossible) |
| claims fully grounded | — | **14 / 14** |
| provenance class | — | 14 durable (2–4 sources each) · 0 caveated · 0 quarantine |

The linter **discriminates** (smoke test on hand-built cases: cross-source→durable,
single-source→caveated, broken/nonexistent citation→quarantine). The all-durable result for
this candidate reflects that the agent was asked for cross-source claims and copied exact
units — i.e. **when the synthesis emits structured citations they resolve 100%**, vs ~21%
for prose. That confirms the M21 gap was citation *format*, not model capability.

**Honest reading of "14 durable":** it means each claim's citation chain is mechanically
verifiable AND draws on ≥2 distinct sources AND is spread across units. It does **not** mean
the claims are semantically deep or free of over-synthesis — that is the separate LLM
claim-strength gate (§3), not yet built. So "durable-eligible by provenance" ≠ "durable
truth"; the second judgment is still owed before any real durable write.

## Verification

- `cargo test --workspace` → **540 passed, 1 ignored, 0 failed** (+6 crystal tests).
- `cargo clippy --workspace --all-targets -- -D warnings` → clean (also `-p ovp-cli --features anthropic`).
- `bash scripts/check_architecture.sh` → **Architecture check passed.**

## Committed vs not

**Committed (Rust + doc):** `crates/ovp-domain/src/crystal.rs`, its `lib.rs` registration,
`crates/ovp-cli/src/commands/crystal_lint.rs` + `mod.rs`/`main.rs` wiring, this doc.
**Not committed (gitignored `.run/m22/`):** the generated candidate, lint report, smoke
artifacts, workflow scripts. No raw model replies, no `.env`, no vault/canonical mutation,
no durable Crystal written.

## What still must exist before durable Crystal + graphics

1. **LLM claim-strength gate** (labeled, separate from the deterministic score): claim
   strength ≤ evidence strength; over-synthesis / hidden-vs-surfaced counter-evidence;
   author-opinion vs system-fact. Routes weak claims to `caveated`/`review`.
2. **Durable Crystal store design** (M23+): append-only, provenance-carrying, **not** the
   demoted canonical/Referent store — a real placement decision, intentionally deferred.
3. **Graphics/graph LAST and gated:** a graph edge IS a cross-source claim. Every node/edge
   must carry a citation that passes this linter, or render as draft/uncertain; the graph
   must be **derived from already-gated Crystal claims**, never a new entity/relation
   extraction (that would revive the demoted Referent path). Without this rule, a visual
   layer silently erodes the verifiable-grounding moat KMEM lacks.

**M22 verdict: PASS (gates only).** The citation chain is now mechanically enforceable and
fail-loud; structured citations resolve 100% where prose resolved ~21%; line drift is
designed out. The semantic claim-strength gate and durable store remain owed before a real
Crystal write.
