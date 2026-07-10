# Stage M18 — Rust Trunk Dogfood Acceptance

**Goal:** run `ovp-next read-source` (the M17 Grounded Reader Trunk) over 20 held-out
real articles, review the resulting reader packs as a batch, and decide whether the
Rust reader trunk should be the default product entry — without per-article prompt
tuning, new ontology, ReferentResolver revival, RAG, or Python parity.

**Pipeline under test:**
`Source → Grounded Units (v5) → Critic Repair (v1) → Reader Cards (card_synth/v3) → Reader Pack`

**Run conditions:**
- Branch `codex/rust-migration`, Rust at repo root, binary `ovp-next` built with `--features anthropic`.
- Live client: provider behind `.env.live` (`ANTHROPIC_BASE_URL` → `api.minimaxi.com`, `OVP_LLM_MODEL` = MiniMax-M2 class), `OVP_LLM_MAX_TOKENS=24000`, `OVP_LLM_TIMEOUT_SECS=300`.
- Each case = 3 live model calls (base extraction, critic repair, card synthesis); 20 cases ≈ 60 live calls.
- Raw artifacts (packs, cassettes, model replies) live under `.run/m18/` and are **not committed** (gitignored).

---

## 1. Sample (20 held-out sources)

| case | category | status | rating |
|------|----------|--------|--------|
| m18-01 | en-essay | ✅ pack | good |
| m18-02 | en-survey | ✅ pack | good |
| m18-03 | en-docs | ✅ pack | good |
| m18-04 | en-essay | ❌ no pack | — (fail) |
| m18-05 | en-opinion | ✅ pack | ok |
| m18-06 | en-essay | ❌ 0 cards | — (fail) |
| m18-07 | en-eng | ✅ pack | good |
| m18-08 | en-eng-entity | ✅ pack | good |
| m18-09 | en-essay | ✅ pack | good |
| m18-10 | en-eng | ✅ pack | good |
| m18-11 | en-eng | ✅ pack | ok |
| m18-12 | en-eng | ✅ pack | good |
| m18-13 | zh-opinion | ✅ pack | good |
| m18-14 | en-docs | ✅ pack | good |
| m18-15 | zh-eng | ✅ pack | good |
| m18-16 | zh-eng | ✅ pack | good |
| m18-17 | en-opinion | ✅ pack | good |
| m18-18 | en-eng | ✅ pack | ok |
| m18-19 | en-eng-entity | ❌ no pack | — (fail) |
| m18-20 | en-essay | ✅ pack | good |

Full input paths: `.run/m18/sample.tsv`. All 20 source files were readable; no substitutions made.

**Overall success rate: 17/20 produced a full reader pack (cards > 0).** 1 further case
(m18-06) wrote a pack with 0 cards (card-synthesis JSON parse failure); 2 cases
(m18-04, m18-19) produced no pack (unit-extraction JSON parse failure).

---

## 2. Per-pack metrics

`awq` = `accepted_without_quote`. `qnf` = `quote_not_found` (quotes the critic could
**not** re-locate in source and therefore rejected — the truth layer working, not failing).

| case | cards | accepted units | awq | dropped_uncited | qnf | needs_review | parse_error |
|------|------:|---------------:|----:|----------------:|----:|-------------:|-------------|
| m18-01 | 12 | 36 | 0 | 0 | 0 | 0 | — |
| m18-02 | 17 | 18 | 0 | 0 | 8 | 2 | — |
| m18-03 | 15 | 50 | 0 | 0 | 0 | 0 | — |
| m18-04 | — | — | — | — | — | — | **units: invalid `\escape`** |
| m18-05 | 10 | 24 | 0 | 0 | 5 | 1 | — |
| m18-06 | 0 | 30 | 0 | 0 | 0 | 0 | **cards: no JSON object** |
| m18-07 | 14 | 33 | 0 | 0 | 7 | 0 | — |
| m18-08 | 11 | 29 | 0 | 0 | 0 | 0 | — |
| m18-09 | 14 | 30 | 0 | 0 | 1 | 1 | — |
| m18-10 | 13 | 27 | 0 | 1 | 1 | 1 | — |
| m18-11 | 15 | 29 | 0 | 0 | 2 | 0 | — |
| m18-12 | 11 | 25 | 0 | 0 | 2 | 0 | — |
| m18-13 | 16 | 26 | 0 | 0 | 0 | 0 | — |
| m18-14 | 14 | 36 | 0 | 0 | 6 | 0 | — |
| m18-15 | 12 | 54 | 0 | 0 | 0 | 0 | — |
| m18-16 | 17 | 36 | 0 | 0 | 6 | 1 | — |
| m18-17 | 10 | 32 | 0 | 1 | 0 | 1 | — |
| m18-18 | 11 | 22 | 0 | 0 | 3 | 0 | — |
| m18-19 | — | — | — | — | — | — | **units: malformed array (73KB)** |
| m18-20 | 13 | 32 | 0 | 0 | 0 | 0 | — |

**Totals (17 full packs): 225 cards, 569 accepted units, `accepted_without_quote` = 0
everywhere, 41 quotes correctly rejected by the critic (qnf).**

---

## 3. Review (batch, no per-article prompt tuning)

17 full packs were reviewed by independent agents. Each agent read `reader.md`,
`run-status.json`, `source-support.md`, and the **original source**, then performed an
**adversarial provenance spot-check**: pick 3 cards, search the source for their cited
quotes verbatim.

- **Ratings: 14 good · 3 ok · 0 poor.**
- **Usable without raw JSON: 17/17.** `reader.md` (numbered cards + collapsible Evidence) conveys the article's substance with no need to open any `.json`.
- **Provenance checkable: 17/17.** Across the 17 packs, **51/51 spot-checked cited quotes were located verbatim in the source.** Zero hallucinated provenance.
- **Unsupported claims / hallucination: none systematic** (0/17). One isolated card-citation misalignment (m18-11, see below).
- **Chinese stability: clean.** All three zh packs (m18-13, m18-15, m18-16) rated good with `chinese_ok = ok`; no mojibake, truncation, or quote-mismatch.
- **Object-index need: none.** `object_index_needed = false` on all 17, including the entity-dense `en-eng-entity` pack that succeeded (m18-08). No pack implicated the flat card-view design or called for a Referent/object index.

### Reviewer-found issues in passing packs (productization, not truth-layer)

- **m18-18 (ok, source-input):** line numbers in `source-support.md` are systematically off by ~28–30 lines (quote *text* is present verbatim; the *line refs* are wrong). Likely a front-matter / line-offset bug in span→line mapping. **→ M19.**
- **m18-11 (ok, card-view):** Card 9 attributes a claim about Claude Code's typed-file taxonomy to a Hermes quote (line 90) rather than the Claude Code quote (line 146) — a single mis-citation in an entity-dense multi-subject article. **→ M19 (object-index gate candidate; not a Referent revival).**
- **m18-05 (ok, card-view):** `qnf=5` — five accepted units whose quotes the critic could not re-locate, lowering confidence even though the 3 spot-checked quotes verified. General signal: the base extractor sometimes paraphrases quotes (drift), which the critic then rejects (qnf). **→ M19 quote-fidelity, not a truth-layer failure.**

---

## 4. The three hard failures — root cause: model-reply JSON robustness

All three failures are the **live model emitting invalid JSON**, confirmed independently
(Rust serde and Python `json` agree on the same byte offsets). None is a grounding /
truth-layer concept failure — wherever units were produced, `accepted_without_quote = 0`.

| case | stage | exact defect | byte/loc |
|------|-------|--------------|----------|
| m18-04 | units | source path `tengu\session\memory` copied verbatim → invalid `\s` escape in a JSON string | char 15082 |
| m18-06 | cards | dropped opening quote on a `cited_unit_ids` element: `u-025-244667fe"` instead of `"u-025-…"` | line 7 |
| m18-19 | units | structural malformation in a 73 KB reply (stray `"surface": …` outside object); entity-dense repo article | char 66451 |

Observations:
- The card/unit parsers already strip ```json fences (successful packs are fenced too), so fencing is **not** the cause.
- Defect classes are the classic LLM-JSON failure modes — unescaped backslashes from source content, missing string quotes, and structural breakage on very long replies — and are more frequent on this provider (MiniMax-M2) than on Claude.
- The current trunk **fails loud** on all three (no silent bad pack), which is correct behavior; the gap is the absence of a **repair / re-ask** path before failing.

---

## 5. Failure classification

| class | cases | nature |
|-------|-------|--------|
| truth-layer (grounding concept) | **0** | no `awq>0`, no unsupported claims anywhere |
| truth-layer (fail-loud on bad model JSON) | m18-04, m18-19 | model emitted invalid JSON at the units stage |
| card-view (bad model JSON) | m18-06 | model emitted invalid JSON at the cards stage |
| card-view (minor, pack still usable) | m18-05, m18-11 | quote-relocation gap; one mis-citation |
| reader-ui | 0 | reader.md/html usable in all 17 |
| source-input | m18-18 | line-number offset in source-support.md |
| object-index needed | 0 | no entity-dense pack required an object index |

**Single dominant failure mode: model-reply JSON validity (3/3 hard failures).**

---

## 6. Acceptance scorecard

| # | criterion | result |
|---|-----------|--------|
| 1 | ≥ 18/20 produce a reader pack | ❌ **17/20** — NOT MET (short by 1) |
| 2 | ≥ 15/20 rated good or ok | ✅ 17/17 reviewed are good/ok |
| 3 | `accepted_without_quote` all 0 | ✅ 0 everywhere |
| 4 | no systematic unsupported claim | ✅ 0/17 (one isolated mis-citation) |
| 5 | Chinese: no systematic parse/quote/render failure | ✅ 3/3 zh good |
| 6 | provenance checkable in pack | ✅ 17/17, 51/51 spot-checks verbatim |
| 7 | report gives clear M19 direction (not prompt tuning) | ✅ see §7 |

**Verdict: FAIL on the strict 18/20 ship gate (criterion 1); PASS on every quality and
truth-layer criterion.**

This is an honest fail of the hard gate — **not fixed to pass.** The single blocking
metric is failure *count*, and 3/3 failures share one narrow, non-architectural root
cause: model-reply JSON validity. The strategic signal (direction) is strongly positive.

---

## 7. Decisions

**1. Does the Rust reader trunk continue as the main line? — YES.**
Truth layer was 100% clean (0 `awq`, 569 grounded units), provenance is real (51/51
verbatim spot-checks), 14 good / 3 ok / 0 poor, usable without JSON in 17/17, Chinese
stable. The moat (grounded, quote-anchored truth layer) held on real, held-out inputs.

**2. Should `read-source` become the default product entry? — NOT YET (gate on M19).**
It is the correct trunk surface, but a 3/20 (15%) hard-failure rate on real inputs is
too high for a *default* entry. Every failure is model-JSON brittleness, so the fix is a
robustness/repair layer (§7-M19), not prompt tuning or redesign. Promote to default once
M18-class inputs run with ≥18/20 (target 20/20) after the repair layer lands.

**3. Is Referent/Resolver still demoted? — YES, stays demoted.**
No pack implicated the card-view design; `object_index_needed = false` on all 17 incl.
the entity-dense pack that succeeded. The one entity-dense failure (m18-19) failed on
JSON robustness, not on a missing object index. No evidence to revive the Referent main
path. (Caveat: m18-19 produced no pack, so the entity-dense object-index question is
*partially unobserved*; re-evaluate after it parses cleanly under M19.)

**4. What should M19 be? — "Model-reply JSON robustness / repair layer."** In priority order:
   1. **Tolerant JSON recovery before fail-loud** for the three observed defect classes: (a) re-escape stray backslashes inside string values (m18-04), (b) repair missing string quotes in arrays (m18-06), (c) structural recovery on malformed/oversized arrays (m18-19).
   2. **Bounded "re-ask the model to repair its JSON" retry** on parse failure (1–2 attempts) before erroring — cheaper and safer than lenient parsing for structural breakage.
   3. **Provider/decoding hardening:** MiniMax-M2 JSON brittleness on long replies — consider stricter/JSON-mode decoding, or chunked extraction for very long entity-dense sources (m18-19 was 73 KB).
   4. **Fix the line-offset bug** (m18-18, off ~28–30 lines) in span→line mapping.
   5. **Quote-fidelity** to reduce `qnf` (base extractor paraphrase drift; m18-02/05/07/14/16).
   Explicitly **out of scope for M19:** Unit/critic/card prompt tuning, benchmark threshold changes, ReferentResolver, semantic RAG, crystal, Python parity.

**5. Old pipeline surfaces — freeze / keep-advanced / delete** (CLI commands in `ovp-cli`):

| surface | recommendation | rationale |
|---------|----------------|-----------|
| `read-source` | **PROMOTE (trunk, default candidate post-M19)** | validated this stage |
| `extract-units`, `copy-probe` | **KEEP (advanced/diagnostic)** | debug the trunk truth layer |
| `read-source --render-only` | **KEEP (advanced)** | re-render packs from artifacts, no model call |
| `lint`, `apply-plan` | **KEEP (utility/plumbing)** | vault lint; plan application |
| `compare-run`, `review-run` | **KEEP (eval, off-trunk)** | offline eval/diagnostics |
| `interpret-article`, `run-cycle`, `run`, `auto-run` | **FREEZE** | legacy v1 article / L4 canonical pipeline; not re-validated this stage — keep building, do not extend, until re-validated against the reader trunk |
| `extract-referents`, `graph` | **FREEZE (demoted)** | Referent / GraphAssembler path; demoted, do not develop |
| `query`, `rag` | **FREEZE (off-trunk)** | RAG/query surfaces; out of scope per M18 constraints |

> No surfaces deleted in M18 — deletion needs explicit operator sign-off. The
> recommendation is **freeze**, not remove, so history and crates stay intact.

---

## 8. Verification commands (run from repo root)

- `cargo metadata --no-deps --format-version 1` → 13 workspace crates.
- `bash scripts/check_architecture.sh` → **Architecture check passed.**
- `cargo test --workspace` → **507 passed, 1 ignored, 0 failed** (49 suites).
- `cargo clippy --workspace --all-targets -- -D warnings` → clean, no warnings.
- Live dogfood: `ovp-next read-source --client live` over `.run/m18/sample.tsv` (20 cases) → 17 full packs, 3 JSON-robustness failures.

**Migration note:** in a proxied environment the ambient `HTTP(S)_PROXY` resets the
provider host; live runs require `OVP_LLM_NO_PROXY=1` (already documented in
`docs/live-capture.md`). Direct egress works; the proxy does not.

---

## 9. Bottom line

The grounded reader trunk is the right main line and its truth layer is sound on real,
held-out content. `read-source` is **not yet** the default entry: M18 fails the 18/20
ship gate by one, and 3/3 failures are model-reply JSON validity — a robustness/repair
problem, not a truth-layer, card-view, UI, source-type, or object-index gap. **M19 = JSON
robustness / repair layer.** Referent/Resolver stays demoted.
