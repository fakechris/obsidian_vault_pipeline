# Stage M19 — Model-Reply JSON Robustness / Repair Layer

**Goal:** make `read-source`'s model-reply JSON handling production-grade so the three
M18 failures that lost packs to fixable JSON-format problems no longer fail loud —
without weakening any grounding/truth-layer invariant, and without prompt tuning,
threshold changes, Referent/RAG, or Python parity.

**Inputs:** the M18 dogfood (`docs/stage-m18-rust-trunk-dogfood.md`) lost 3/20 packs:
m18-04, m18-06, m18-19 — all model-reply JSON validity, none a grounding failure.

---

## 1. Root cause per failed M18 case (audited, not guessed)

Each failure was reproduced from its captured raw reply; serde and Python's `json`
agree on the same byte offset.

| case | stage | exact defect | byte/loc |
|------|-------|--------------|----------|
| **m18-04** | unit extraction (`parse_envelope`) | a source path `tengu\session\memory` copied verbatim into a JSON string → invalid `\s`/`\m` escape | char 15082 |
| **m18-06** | card synthesis (`parse_cards` → `extract_object`) | dropped opening quote on a `cited_unit_ids` element (`u-025-244667fe"` not `"u-025-…"`); the brace-matcher desyncs on the unbalanced quote → "no JSON object in reply" | line 7 |
| **m18-19** | unit extraction (`parse_envelope`) | structural break in a 73 KB reply — a stray `"surface": …` outside its object → `expected ',' or ']'` | char 66451 |

None reached the per-unit / per-card validators: all three broke the *whole-envelope*
`serde_json::from_str` first. The truth layer never conceptually failed
(`accepted_without_quote = 0` wherever units were produced).

---

## 2. Parser-local tolerant recovery (`crates/ovp-domain/src/model_reply.rs`)

A shared, pure layer used by unit extraction (`units::parser`), the critic, and card
synthesis (`reader::cards`). For each reply it:

1. **Strips** a ```json / ``` fence or surrounding prose (`strip_code_fence`).
2. **Locates** the outermost balanced `{…}`/`[…]` envelope, tracking JSON string state
   so braces inside strings don't miscount (`locate_envelope`).
3. **Parses**; on failure, applies ONE well-defined fix — doubling backslashes inside
   strings that are not valid JSON escapes (`escape_stray_backslashes`) — and retries.

What it handles (safely, no content guessing):
- **Unescaped backslash from source text** (the m18-04 class) → recovered locally, no
  model call. *Verified on the real captured m18-04 reply: it fails `serde` as-is and
  parses to 30 units after the escape repair.*
- Markdown-fence / prose wrappers around otherwise-valid JSON.

What it deliberately does NOT do: it never guesses a dropped quote, never fuzzily
"completes" truncated/structural JSON, never accepts malformed output. Those return a
classified `JsonDefect` (`NoEnvelope` / `Unrecoverable(serde-detail)`) so the caller
fails loud with a precise message — or escalates to a bounded model repair.

---

## 3. Bounded model JSON repair — when it is invoked

If parser-local recovery returns an unrecoverable defect (the m18-06 / m18-19 classes),
the harness makes **exactly one** follow-up call (`model_reply::json_repair_request`):

- The repair prompt is **syntax-only**: "preserve every field name, string value, number,
  array element exactly; only fix JSON syntax; do not add, remove, reorder, summarize,
  translate, or rephrase; do not complete truncated values." It is NOT the
  extraction/critic/card prompt and cannot re-extract content.
- Wired in `units::harness::resilient_unit_extract` (base extraction) and
  `reader::cards::run_card_synthesis` (cards). The critic path is unchanged.
- At most once. If the repair call errors (replay cache-miss / transport) or its reply
  still doesn't parse → fail loud with the original defect (never silent-accept).
- A successful salvage is recorded: `run-status.json` gains `json_repaired: true` and a
  `json_repairs: [{stage, method}]` list (e.g. `model-repair (input defect: …)`), and the
  CLI prints a `json-repair[…]` line.

## 4. Why repair can NOT bypass the validator / grounding

The repaired text re-enters the **same** path as a normal reply:
`parse_reply_value → units_from_value → validate` (units) and
`parse_reply_value → cards_from_value → validate_cards` (cards). Repair only ever yields
a `serde_json::Value`; it never constructs a `Unit`/`Card` or a location. So:

- A repaired unit whose `evidence_quote` is not located in the source is **rejected** by
  the validator — `accepted_without_quote` stays 0. (Regression test:
  `repaired_units_still_pass_through_grounding_validator`.)
- A repaired card citing no real accepted unit is **dropped** by the citation invariant.
  (Regression test: `repaired_cards_still_pass_citation_validator`.)

Repair changes only whether we get *valid JSON to validate at all*, never what passes.

---

## 5. m18-18 line-offset bug — confirmed and fixed

**Confirmed real.** `validator::line_of` counted newlines in `body_markdown`, which
`split_frontmatter` strips of its YAML frontmatter — so evidence lines were
**body-relative**. m18-18's frontmatter is 26 lines + two `---` = a constant **+28**
offset; every cited line in M18 was off by 28–34 (the residual from multi-line
paragraphs). This affected *every* source with frontmatter, not just m18-18.

**Fix.** `SourceDoc.body_line_offset` (set by the inbox reader = lines before the body)
is threaded into the validator; reported `line = line_of(body) + body_line_offset`, i.e.
**file-relative**. Byte ranges stay body-relative (used for re-rendering). It is a
display/provenance aid only — it does not touch accept logic.

**Verified on the M19 m18-18 pack:** the systematic +28 offset is gone — 12/20 cited
lines now land exactly on the source line, the rest within ≤8 (residual intra-paragraph
drift inherent to span-granular anchoring; `loc_at` already documents that sub-offsets
aren't usable). Regression tests: `line_is_file_relative_with_frontmatter_offset`,
`body_line_offset_counts_frontmatter_lines`.

---

## 6. Tests + gates

New tests (20; workspace 507 → **527**, 0 failed):
- `model_reply` (10): fence strip, envelope locate (incl. brace-in-string, dropped-quote
  desync), backslash repair (fixes source paths, idempotent, preserves valid escapes),
  recovery note vs. unrecoverable classification, syntax-only repair request.
- `units::harness` (4): parser-local backslash recovery; bounded model repair on a
  structural defect; fail-loud when repair also bad; **repaired output still gated by
  grounding**.
- `reader::cards` (4): model repair on dropped-quote card JSON; parser-local backslash in
  cards; **repaired cards still gated by citation validator**; clean reply has no note.
- line-offset (2) + inbox offset (2) as above.

Gates (from repo root):
- `cargo test --workspace` → **527 passed, 1 ignored, 0 failed**.
- `cargo clippy --workspace --all-targets -- -D warnings` → clean (also `-p ovp-cli --features anthropic`).
- `bash scripts/check_architecture.sh` → **Architecture check passed.**

---

## 7. M18 re-run (same 20 held-out sources, same threshold, live)

Re-ran the identical 20-set live into `.run/m19/` (uncommitted). Totals: **19/20 full
packs, 241 cards, 643 accepted units, `accepted_without_quote` = 0 across all 19**, 36
quotes correctly rejected by the critic.

| case | M18 | M19 | rating (M19) | note |
|------|-----|-----|--------------|------|
| m18-01 | good | ✅ | good | |
| m18-02 | good | ✅ | **poor** | `render_plain` inlines citation-link anchors → misleading quote (see §8) |
| m18-03 | good | ✅ | good | |
| **m18-04** | ❌ no pack | ✅ | good | was: invalid `\escape` — now parses |
| m18-05 | ok | ✅ | good | |
| **m18-06** | ❌ 0 cards | ✅ | good | was: card JSON missing quote — now parses |
| m18-07 | good | ✅ | good | |
| m18-08 | good | ✅ | good | |
| m18-09 | good | ✅ | good | |
| m18-10 | good | ✅ | good | |
| m18-11 | ok | ✅ | good | |
| m18-12 | good | ✅ | ok | one card's opening sentence mis-frames its title (card-view) |
| m18-13 | good | ✅ | good | zh — stable |
| m18-14 | good | ✅ | good | |
| m18-15 | good | ✅ | good | zh — stable |
| m18-16 | good | ✅ | good | zh — stable |
| m18-17 | good | ❌ **no pack** | — | NEW: thinking model spent all `max_tokens` on a reasoning block → no text block (decode error). Not JSON; see §8 |
| m18-18 | ok | ✅ | good | line numbers now file-relative |
| **m18-19** | ❌ no pack | ✅ | ok | was: malformed 73 KB array — now parses; one card prose has an email transcription slip (quote is correct) |
| m18-20 | good | ✅ | good | |

**Ratings (19 packs): 16 good · 2 ok · 1 poor.**

**Important honesty note:** no pack triggered a repair this run (`json_repaired=false`
everywhere) — the live model emitted valid JSON for the previously-failing inputs this
time. Live output is stochastic, so the re-run *count* (19/20) is not by itself proof the
repair works; it shows the trunk now clears the gate and the original defects are absent.
The **proof the repair handles the real defect classes** is §2–§4 (regression tests +
the real-m18-04 replay). The right framing: M19 adds a **safety net** validated by tests,
not a one-time patch — which is exactly why a stochastic re-run still benefits.

---

## 8. Verdict + remaining risks

**Acceptance scorecard:**

| criterion | result |
|-----------|--------|
| ≥ 18/20 produce a reader pack | ✅ **19/20** |
| ≥ 15/20 good or ok | ✅ **18/20** (16 good + 2 ok) |
| `accepted_without_quote` all 0 | ✅ 0 across all 19 packs |
| no systematic unsupported claim | ✅ no systematic hallucination; isolated card-prose slips (m18-12, m18-19) + one render-artifact pack (m18-02) |
| Chinese: no systematic parse/quote/render failure | ✅ m18-13/15/16 all good |
| provenance checkable | ✅ 18/19 |
| clear M20 direction (not prompt tuning) | ✅ below |

**M19 = PASS.** The three targeted JSON-robustness failures are resolved (proven by
regression tests + real-reply replay; live re-run clears 18/20 → actually 19/20), the
truth layer is intact everywhere (`accepted_without_quote = 0`), repair provably cannot
bypass grounding, and the m18-18 line bug is fixed.

**Remaining risks (→ M20, all pre-existing and outside M19's JSON scope):**

1. **Thinking-model token exhaustion (m18-17).** A reasoning model can spend the entire
   `max_tokens` budget on a thinking block and return *no text block*
   (`stop_reason=max_tokens`) — a decode `CallError` before any JSON exists, which the
   repair layer cannot help (nothing to repair). Stochastic (m18-17 passed in M18 at the
   same budget). M20: a bounded retry that raises `max_tokens` on a "no-text / max_tokens"
   stop, and/or a higher default budget for thinking providers. The error message already
   tells the operator to raise `OVP_LLM_MAX_TOKENS`.
2. **`render_plain` citation-link inlining (m18-02).** `strip_markdown_links` inlines a
   link's anchor text; when the anchor is a citation source name (`[Medium](url)`,
   `[Emergent Mind](url)`, `[Cognee]`), the rendered text reads e.g. "91.4% on LongMemEval
   Medium" — a misleading quote, even though it is verbatim against the rendered view and
   `accepted_without_quote = 0`. This is the deferred `render_plain` fidelity issue, latent
   in any citation-link-dense source; it surfaced now because this run's stochastic output
   quoted link-adjacent passages. M20 (a truth-layer/rendering change, deliberately not
   done here to avoid scope creep / fixing-to-pass): drop or footnote citation-link anchors
   in `render_plain` instead of inlining them. Same family as the deferred underscore
   handling for code identifiers.
3. **`quote_not_found` drift** (36 across 19 packs): the base extractor still proposes some
   quotes the critic can't locate (paraphrase drift). The critic correctly rejects them
   (grounding holds), but lowering it would raise accepted-unit yield. Quality, not
   robustness.

**Trunk position unchanged:** the grounded reader trunk continues as the main line;
`read-source` is materially closer to default-entry readiness (JSON-robustness net in
place, line provenance fixed); Referent/Resolver stays demoted (`object_index_needed =
false` on all 19, including entity-dense m18-08/m18-19).
