# Stage M21 — Pre-release Knowledge Workflow Dashboard

> **M21.1 UPDATE (2026-06-07) — the original "Knowledge Mem UNAVAILABLE / INCONCLUSIVE"
> verdict below is SUPERSEDED.** A local Knowledge Mem service (`http://127.0.0.1:14242`)
> was brought up; the KMEM arm was captured source-scoped for all 20 sources and a real
> two-arm AB was run. See **§M21.1 — Real two-arm AB** at the end. The original M21 text is
> retained for the audit trail; where it says "unavailable / INCONCLUSIVE", read the M21.1
> result instead.

**Goal:** put the OVP reader-trunk output (and, where available, Knowledge Mem's) for the
fixed 20 held-out sources into a single shareable, auditable, AB-testable pre-release
review surface, and produce a pre-release verdict across source-level usefulness,
corpus-level synthesis / crystal readiness, and human+agent acceptance. **Not** prompt
tuning, **not** new pipeline development — an acceptance + review surface.

**Inputs:** the fixed M18/M20 20-set; OVP packs reused verbatim from the M20 live run
(`.run/m20/dogfood`, 20/20 packs, `accepted_without_quote=0`). No sample changes.

---

## Knowledge Mem availability — ~~UNAVAILABLE~~ → AVAILABLE (see §M21.1)

> **Corrected in M21.1.** At the time of the original M21 run no KMEM service was reachable
> from the session, so this was marked unavailable. That was an environment state, not a
> permanent fact: a local KMEM service was subsequently started and the arm captured. The
> original note is struck through below; the live result is in §M21.1.

~~Scouted thoroughly: no `kmem`/`knowledge-mem` binary on PATH, no Knowledge Mem MCP /
connector in the session, no KMEM env keys, no local KMEM config/data dirs. Knowledge Mem
source memories: unavailable. Per the M21 spec this is marked `unavailable`, which makes the
OVP-vs-KMEM head-to-head INCONCLUSIVE.~~

---

## Dashboard

**Location:** `.run/m21/dashboard/index.html` (gitignored — contains run data; the
*generators* are committed). Self-contained static HTML, no backend.

- **index.html** — overview + acceptance verdict banners, 20-case comparison table
  (OVP cards/units/awq · KMEM=unavailable · winner · rating · AB), corpus-synthesis
  section (14 cited items), crystal-readiness box, AB-test section, links to case pages.
- **cases/<case>.html** ×20 — source title/path; OVP cards (with collapsible evidence)
  side-by-side with the KMEM-unavailable panel; an **anonymized AB block** (Side A = card
  view, Side B = raw grounded-units readout) with a **Reveal arms** button; the agent
  verdict (6 scores + AB + rationale); a human-notes textarea.
- A human opens `index.html`, scans all cases, clicks one, compares, inspects evidence,
  reads the agent verdict, and can leave notes — no raw JSON, no folder hopping.

Built by `scripts/m21_pack_summary.py` (packs → `packs.json`) + `scripts/m21_build_dashboard.py`
(packs + reviews + synthesis → HTML). Both reusable; neither embeds run data.

---

## Layer 1 — Source-level (OVP cards/units/provenance vs KMEM memories)

All 20 cases evaluated by independent agents (each did adversarial 3-card provenance
spot-checks against the source). KMEM arm unavailable, so `winner =
ovp_only_kmem_unavailable` for all 20.

**OVP source-level: 17 good · 3 ok · 0 poor.** Mean scores (1–5):

| dimension | mean |
|-----------|------|
| readability | **5.00** |
| practical_usefulness | 4.85 |
| faithfulness | 4.85 |
| longterm_vault_usefulness | 4.80 |
| source_support (provenance/debuggability) | 4.75 |
| coverage | 4.65 |

- `accepted_without_quote = 0` across all 20; **19/20 provenance-checkable**.
- **No hallucinations / fabricated claims** in any of the 20. The non-"none"
  `unsupported_claims` notes are isolated card-view nits, same class as M19/M20:
  - m18-14 (ok, the one `provenance_checkable=false`): a card's bolded summary includes a
    source phrase ("via a multi-layer merge chain") that lives elsewhere in the source than
    the cited quote — a grounding-tightness slip, not a fabrication.
  - m18-17 (ok): one card over-generalizes the author's personal statement.
  - m18-12 (good): two cards paraphrase/reorder (card prose is synthesized; only unit
    quotes must be verbatim — those are).
- The 3 **ok** are coverage/framing nits (m18-10 fewer cards than the article's breadth;
  m18-14 the merge-chain slip; m18-17 the over-generalized card), not truth-layer issues.

**OVP's provenance advantage is concrete** (every card → cited unit → verbatim quote +
file-relative source line). It cannot be *compared* to KMEM (unavailable), but it is a
standalone strength a memory-blob system typically lacks.

---

## Layer 2 — Corpus synthesis draft + crystal readiness

A **review-only** OVP corpus synthesis draft was generated from the 20 packs (one agent
over `packs.json`). **NOT a durable Crystal:** nothing written to the vault/canonical
store. **14 synthesis items**, each citing supporting `case_id`s + concrete
card/unit evidence_refs + caveats/counterexamples, across 12 themes (memory architectures,
context engineering, memory-injection patterns, evaluation, agent design, RAG limits,
reliability, skills-as-primitive, …). Coverage: **18 of 20** cases cited.

Example items (each multi-source, with recorded tension):
- "Simple filesystem/markdown memory consistently matches or beats specialized
  vector/graph infrastructure" (m18-02/05/08/11) — *caveat:* m18-02 itself argues hybrid
  storage is optimal; benchmark/task-dependent.
- "The hard problem of memory is the retrieval/injection policy, not the storage layer"
  (m18-05/08/11).
- "Eval is a continuous flywheel, and automated metrics/LLM-judges must be human-calibrated
  or they get gamed" (m18-03/12/18).
- "One generalized agent with lazy-loaded skills beats a fleet of narrow bots" (m18-01/06/07)
  — *caveat:* high-stakes/regulated workflows (m18-04/14) deliberately split agents.

**Crystal-readiness review: `near` (confidence high), faithfulness_to_cards 5/5,
every_item_grounded = true.** The reviewer spot-checked ~7 items / ~25 citations against
the packs and found **no invented facts**; load-bearing numbers matched verbatim.

Real gap it found → **M22 work:** ~3 `evidence_refs` have line-number drift (quote text
correct, `Lnn` off) — a mechanical citation-linter would catch these. *(Methodology
caveat: the reviewer also reported the draft "truncated mid-item" — that was a **harness
artifact**, the review agent was handed a 12k-char slice of the synthesis; the persisted
`synthesis.json` has all 14 items complete.)*

---

## AB test surface

KMEM unavailable ⇒ a true OVP-vs-KMEM AB cannot run. The dashboard's AB surface instead
runs an **OVP-internal AB**: Side A = card view, Side B = raw grounded-units readout,
anonymized per case with a reveal button — validating whether card synthesis improves
readability over the raw truth layer. **Agent AB result: card view better in 20/20.** The
surface is real and human-usable; the OVP-vs-KMEM AB is *blocked on KMEM availability*, not
on the surface.

---

## Verdict

| question | answer |
|----------|--------|
| 1. OVP source cards reach KMEM source-memory usefulness? | **Cannot be compared — KMEM unavailable.** OVP standalone is strong (17 good/3 ok/0 poor, readability 5.0) with a provenance edge. |
| 2. Is the corpus synthesis enough to show crystal readiness? | **Near.** 14 grounded, faithful (5/5) items — useful enough to justify M22 Crystal work, not yet ship-grade. |
| 3. Is KMEM crystal/synthesis clearly better than OVP's? | **Unknown — KMEM unavailable.** No evidence KMEM outclasses OVP; none that it doesn't. |
| 4. What should formal OVP Crystal (M22) do? | See M22 below. |
| 5. Enter pre-release / AB stage? | **OVP-internal review/AB: yes** (dashboard works, card-view validated). **OVP-vs-KMEM AB: not yet** — needs a live KMEM arm. |

**Overall M21 = INCONCLUSIVE on the OVP-vs-KMEM comparison (Knowledge Mem unavailable —
the spec's explicit INCONCLUSIVE trigger), with OVP standalone PASS:** comparable-or-better
can't be *proven* without a KMEM arm, but OVP is independently strong on source-level
usefulness, has a clear provenance advantage, produced a useful grounded synthesis draft
(crystal readiness "near"), shows no major unsupported-claim pattern, and the dashboard is
usable for human review + the OVP-internal AB.

**Crystal readiness: `near`** (not `ready`). **AB surface: built and usable** (OVP-internal;
OVP-vs-KMEM pending a KMEM arm).

---

## Recommended M22

**"Crystallize the verified spine — with a citation-linter — before any durable write."**
1. **Citation-linter** (highest value): resolve every synthesis `evidence_ref`
   (`mXX: …(Lnn)`) against `packs.json` units; auto-correct or flag line-number drift
   (would have caught the 3 drifts mechanically). Pure offline check.
2. **Provenance-quality scoring per synthesis item** derived from the cited cases'
   `quote_not_found`/`needs_review` counts; quarantine/down-weight weakly-grounded items.
3. **Schema-validate the synthesis artifact** before review (avoid the slice/truncation
   confound seen here).
4. Only then consider a *durable* Crystal design — still gated, still grounded, still not
   reviving Referent/RAG.
5. **KMEM arm**: if a Knowledge Mem service becomes available, re-run M21's dashboard with
   `--kmem` to complete the head-to-head AB (the surface already supports it).

---

## Committed vs. intentionally not committed

**Committed (scripts + doc, no run data):**
- `scripts/m21_pack_summary.py` — pack → compact JSON summary.
- `scripts/m21_build_dashboard.py` — JSON → static dashboard (index + case pages + AB).
- `docs/stage-m21-pre-release-dashboard.md` (this file).

**Intentionally NOT committed (gitignored `.run/m21/`):** the generated dashboard
(`.run/m21/dashboard/`), `packs.json`, `synthesis.json`, `reviews.json`,
`synthesis_review.json`, the workflow script, and all reused `.run/m20` packs / model
replies. No `.env*`, no cassettes, no raw KMEM dumps (none exist). No durable Crystal; no
vault/canonical mutation.

---

## Verification

- `cargo test --workspace` → **534 passed, 1 ignored, 0 failed** (M21 added no Rust).
- `cargo clippy --workspace --all-targets -- -D warnings` → clean.
- `bash scripts/check_architecture.sh` → **Architecture check passed.**

**Confounds (labeled):** the synthesis generator and the agent judges share a model family
with the run client (MiniMax-class); ratings are agent judgments, not human. The AB is
OVP-internal (card vs units), not OVP-vs-KMEM. These bound the strength of the PASS to
"OVP standalone, agent-judged."

---

## §M21.1 — Real two-arm AB (Knowledge Mem attached)

This supersedes the original "unavailable / INCONCLUSIVE" verdict. A local Knowledge Mem
service was started at `http://127.0.0.1:14242`; the same fixed 20 sources were captured
**source-scoped** and judged head-to-head against the OVP reader cards, with the source
article as ground truth (no global search substitute).

### Two root causes fixed (why M21 first reported "unavailable")

1. **No live verdict, only wiring assumption.** The original review ran when no KMEM
   service was reachable, so every case was hard-coded `kmem unavailable`.
2. **Dashboard never consumed `--kmem`.** `m21_build_dashboard.py` accepted `--kmem` but
   the case-page renderer ignored it and always printed the unavailable panel — so even a
   populated `kmem.json` rendered as unavailable. Fixed: `render_kmem_case()` now renders
   source-scoped memories; the index banner/table/AB-summary now compute a real two-arm
   verdict.

### KMEM capture (source-scoped, not global search)

`scripts/m21_capture_kmem.py` per source: `POST /sources/ingest/file-path` →
`POST /sources/{id}/extract` → poll `GET /sources/{id}` → read that source's own
`memories[]`. **Fairness fix:** `GET /sources/{id}` returns memory `content` truncated to a
~200-char preview; the script now enriches each memory via `GET /memories/{id}` for the
**full body** (mean ~835 chars, 0 truncated). The first AB run scored KMEM on the truncated
stubs (unfair — it depressed KMEM readability/usefulness); the AB was re-run on full bodies.

**Coverage:** 20/20 sources captured, **123 source-scoped memories** total; lifecycle
18 extracted / 2 indexed-but-populated (m18-14, m18-19 — both have memories and were
evaluated; flagged `available_lifecycle_not_extracted` where applicable). No global search
used anywhere.

### Result — real two-arm AB (agent-judged, full KMEM content)

| dimension (mean 1–5) | OVP cards | KMEM memories |
|----------------------|:---------:|:-------------:|
| faithfulness | **4.95** | 4.35 |
| coverage | **4.90** | 2.85 |
| readability | 4.40 | 4.35 |
| provenance | **5.00** | 1.60 |
| usefulness | **4.75** | 3.35 |

- **Winner: OVP 20/20** (confidence high), **provenance_advantage: OVP 20/20**, OVP
  standalone rating **20/20 good**. 0 OVP unsupported claims; 0 KMEM hallucinations.
- **OVP's edge is coverage + provenance, NOT readability.** With full content,
  **readability is a tie** (4.40 vs 4.35; KMEM ≥ OVP on readability in 12/20 cases) — KMEM's
  abstracted prose reads well. OVP wins because (a) it captures far more of each article
  (4.90 vs 2.85 coverage — KMEM often extracts a handful of memories from one section, e.g.
  m18-02 captured only the "other systems" list, ~8% of the article), and (b) every card is
  traceable to a verbatim source line (5.00 vs 1.60 — KMEM stores summaries with no
  line-level provenance, only `chunk_index`).

### Verdict (M21.1)

| question | answer |
|----------|--------|
| 1. OVP source cards reach KMEM source-memory usefulness? | **Yes, and exceed it** — OVP wins usefulness 4.75 vs 3.35 and the head-to-head 20/20, on coverage + provenance. Readability is a genuine tie. |
| 2. Corpus synthesis enough for crystal readiness? | **Near** (unchanged from M21; 14 grounded items, faithfulness 5/5). |
| 3. KMEM crystal/synthesis clearly better than OVP? | **No.** KMEM source memories are concise and readable but lower-coverage and provenance-poor; no KMEM crystal/community layer was exposed by the service to compare. |
| 4. M22? | Synthesis citation-linter + provenance-quality scoring before durable Crystal (unchanged); optionally pull KMEM crystals if the service exposes them. |
| 5. Enter pre-release / AB stage? | **Yes** — the dashboard now supports a real OVP-vs-KMEM AB and a human can review both arms per case. |

**M21.1 = PASS (no longer INCONCLUSIVE).** OVP is **at least comparable to KMEM at
source-level usefulness and decisively better on coverage + provenance**, with readability
parity. The earlier INCONCLUSIVE was an availability artifact, now resolved.

### Honest confounds (unchanged + new)

- **Judge/model confound:** the OVP packs, the synthesis, and the AB judges all share the
  MiniMax-class model family; ratings are agent judgments, not human.
- **KMEM provenance scoring is structural, not a tuning artifact:** KMEM genuinely stores
  summaries without line-level citations, so its low provenance score reflects the system,
  not the prompt.
- **Two cases (m18-14, m18-19) were `indexed` not `extracted`** at capture time but had
  memories; they were evaluated on those memories.
- The **first AB run was unfair** (truncated KMEM content) and was discarded; the table
  above is the full-content re-run.

### Artifacts (M21.1)

Committed: `scripts/m21_capture_kmem.py` (new), `scripts/m21_build_dashboard.py` (now
consumes `--kmem` + renders the two-arm verdict), this doc. **Not committed** (gitignored
`.run/m21/`): `kmem/kmem.json` + per-case memories, `reviews_kmem.json`, the rebuilt
`dashboard-kmem/`, workflow scripts. No raw KMEM dumps, no `.env`, no vault/canonical
mutation, no durable Crystal. Gates (re-verified): `cargo test --workspace` 534 passed,
clippy clean, arch check passed.
