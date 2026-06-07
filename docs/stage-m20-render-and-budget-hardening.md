# Stage M20 — Thinking-Model Budget Exhaustion + Render Fidelity Hardening

**Goal:** close the two pre-existing, non-JSON issues M19's stochastic re-run surfaced,
without prompt tuning, threshold changes, Referent/RAG/crystal, or Python parity, and
without weakening the grounding invariant:

- **A — thinking-model budget exhaustion** (m18-17): a reasoning model spent its whole
  `max_tokens` on a thinking block and emitted no text → unrecoverable decode error.
- **B — render-fidelity** (m18-02): `render_plain` inlined citation-link anchors
  (`[Medium](medium.com)` → "LongMemEval Medium") and stripped code-identifier
  underscores (`message_agent` → `messageagent`, the M17-documented bug).

---

## A. Thinking-model budget exhaustion → bounded higher-budget retry

**Root cause (m18-17, audited).** The Anthropic decode returned only a `thinking` block
with `stop_reason=max_tokens` and no `text` block; the client raised a generic
`CallError::Decode` (treated as unrecoverable). Retrying at the same budget can't help —
the model needs *more* room to emit text after thinking.

**Fix (`crates/ovp-llm`):**
1. **Classify** (`anthropic.rs`): a no-text reply with `stop_reason=max_tokens` is now a
   distinct `CallError::BudgetExhausted` ("thinking_budget_exhausted: …"). A no-text reply
   for any *other* stop reason stays `Decode` (genuinely not retryable).
2. **Recover** (`client.rs::BudgetEscalatingModelClient`): on `BudgetExhausted`, retry
   **exactly once** at a raised `max_tokens` (`escalated_max_tokens`); if it still
   exhausts, the error surfaces (fail loud). `BudgetExhausted` is **not** transient, so the
   existing `RetryingModelClient` never loops it.
3. **Make the budget reach the wire** (`anthropic.rs`): the env `max_tokens` override now
   sends `max(request.max_tokens, override)`, so an escalated request (which bumps
   `request.max_tokens` above the override) actually raises the budget. Normal requests
   (whose `max_tokens` ≤ override) are unchanged.
4. **Wire** (`ovp-cli::build_live_client`): `Cached( BudgetEscalating( Retrying( live ) ) )`
   — escalation OUTSIDE retry (non-transient), INSIDE the cache (a successful escalated
   reply records once under the original request key). Target = 2× the effective budget,
   capped at 96k (base 16k when no override).

**Why it can't corrupt anything:** escalation only changes `max_tokens`; the reply still
flows through the normal parser + validator. It changes *whether we get text at all*,
never what passes grounding.

**Tests** (`ovp-llm`): `parse_thinking_only_response_is_budget_exhausted`,
`parse_empty_text_non_budget_stop_is_decode_error`, `escalates_budget_then_succeeds`
(verifies the retry raises `max_tokens`), `budget_escalation_is_one_shot_then_fails_loud`,
`escalator_passes_through_non_budget_errors_and_success`, and the `is_transient` guard.

---

## B. Render fidelity — `render_plain` / `strip_markdown_links`

Two deterministic source-rendering fixes (no prompt change, no validator change). Because
the rendered view feeds BOTH the model prompt and the validator, the model and the
validator stay in lockstep — `accepted_without_quote` remains 0.

**B1 — citation links dropped (m18-02).** A markdown link whose anchor merely names its own
source/host is a citation, not prose. `is_citation_link(anchor, url)` is true iff the
anchor — normalized to lowercase alphanumerics, a trailing ` +N` reference-count suffix
removed — **exactly equals one of the URL host's domain labels** (minus `www`/common TLDs).
Such links are dropped; all others keep their visible text.

- `91.4% on LongMemEval [Medium](https://…medium.com/…) with Gemini-3 Pro` →
  `91.4% on LongMemEval with Gemini-3 Pro` (was "…LongMemEval Medium with…").
- `[arXiv](arxiv.org)`, `[Emergent Mind](emergentmind.com)`, `[GitHub +2](github.com)` → dropped.
- Content links kept: `Use [Claude Code](docs.anthropic.com)` → "Use Claude Code";
  `[vitest-evals](https://x/y)` → "vitest-evals". Exact-match keeps it conservative.

**B2 — code-identifier underscores preserved (M17 bug).** An underscore BETWEEN two
alphanumerics is part of an identifier, not Markdown emphasis: `message_agent`, `tool_call`,
`shared_content` are kept; word-boundary emphasis underscores (`_italic_`) are still
stripped (as are `*`/`` ` ``/`~`).

**Tests** (`source_map`): `render_plain_drops_citation_links_keeps_content_links`,
`is_citation_link_classification`, `render_plain_preserves_code_identifier_underscores`.
(Out of scope, noted: a single leading `~` in `~/path` is still stripped as a strikethrough
marker — separate from the underscore fix.)

---

## Gates

- `cargo test --workspace` → **534 passed, 1 ignored, 0 failed** (+7 M20 tests).
- `cargo clippy --workspace --all-targets -- -D warnings` → clean (also `-p ovp-cli --features anthropic`).
- `bash scripts/check_architecture.sh` → **Architecture check passed.**

Commits: `aa5cab9` (fix(llm): budget) · `9d01988` (fix(reader): render fidelity).

---

## M18/M19 re-run (same 20 held-out sources, live → `.run/m20/`, uncommitted)

**20/20 full packs · 266 cards · 634 accepted units · `accepted_without_quote` = 0 across
all 20 · 38 quotes correctly rejected by the critic.**

Review (independent agents, 3+ adversarial quote spot-checks each): **19 good · 1 ok · 0
poor.** All 20 usable without JSON, all provenance checkable, **zero unsupported claims**,
Chinese (m18-13/15/16) all good, `object_index_needed = false` everywhere.

| run | full packs | good/ok/poor | notes |
|-----|-----------|--------------|-------|
| M18 | 17/20 | 14 / 3 / — (3 failed) | 3 JSON-robustness failures |
| M19 | 19/20 | 16 / 2 / 1 | m18-04/06/19 fixed; m18-17 (budget) + m18-02 (render) surfaced |
| **M20** | **20/20** | **19 / 1 / 0** | m18-17 recovered, m18-02 poor→good |

**Targeted validation of the two fixes (on real M20 artifacts):**
- **m18-17 recovered** (good). This run its base extraction returned text with a JSON
  defect that the M19 repair handled (`json_repaired=true`); the M20 budget-escalation net
  is in place + unit-tested. *Honesty note:* budget exhaustion is stochastic and did not
  re-occur this run, so the live evidence for escalation is the unit tests, not this run.
- **m18-02 poor → good.** Its quotes are now clean — `LongMemEval with Gemini-3 Pro`,
  `LongMemEval has emerged as`, etc.; zero occurrences of the M19 offenders ("LongMemEval
  Medium", "Cognee and Cognee"). The reviewer flagged no unsupported claims.
- **Code identifiers preserved on real data:** m18-19 quotes retain `CORE_VALUES`,
  `bootstrap_vault`, `vault_health`, `PROPAGATION_PAYLOAD` (mangled in M18/M19).

The lone **ok** (m18-07): one unit grounded by only its 4-word headline phrase while its
expanded text claims slightly more (`needs_review=1`) — a card-view grounding-tightness
nit, not a hallucination; provenance still checks out (3/3 verbatim).

---

## Verdict + remaining risks

**Acceptance scorecard:**

| criterion | result |
|-----------|--------|
| A: m18-17 recovered | ✅ good (20/20); classification + one-shot escalation unit-tested |
| B: m18-02 no longer poor / offending quote corrected | ✅ poor→good, pollution gone |
| `accepted_without_quote` all 0 | ✅ 0 across all 20 |
| provenance checkable | ✅ 20/20 |
| ≥18/20 packs · ≥15/20 good-or-ok | ✅ 20/20 · 20/20 |
| content links + code identifiers intact | ✅ (tests + live m18-19) |

**M20 = PASS.** Both targeted issues are closed; the run reached 20/20 with the cleanest
ratings of the series (19 good / 1 ok / 0 poor) and the truth layer intact everywhere.

**Remaining risks / future (small, none blocking):**
1. **Budget escalation is unexercised live** — its correctness rests on unit tests; the
   real m18-17 path that fired this run was the M19 JSON repair. Worth a synthetic live
   probe (a deliberately tiny `OVP_LLM_MAX_TOKENS`) to exercise escalation end-to-end.
2. **Single leading `~`** in `~/path` is still dropped (strikethrough marker heuristic) —
   out of M20's stated scope; fix if it surfaces in a real quote.
3. **`quote_not_found` drift** (38/20 packs): base extractor paraphrase the critic rejects
   — grounding holds; a yield/quality lever, not robustness.
4. **`render_plain` is now load-bearing for fidelity**; any future markdown construct
   (footnote refs, nested links) should add a focused `source_map` test.

**Trunk position:** the grounded reader trunk is the main line; with the M19 JSON net, the
M20 budget net, file-relative provenance lines, and citation/identifier render fidelity,
`read-source` is at its strongest — a clean 20/20 on the held-out set. Referent/Resolver
stays demoted (`object_index_needed = false` on all 20). Next candidate entry-default
decision can be made on this footing.
