# Stage M32 — Python Retirement Sign-off Checklist + Product Definition (the anchor)

**Type:** Level-3 (delete-Python) sign-off checklist + product-definition anchor. NOT a normal
stage summary and NOT a feature epic.
**Purpose:** After M29 (mainline-return audit) + M30/M31 (daily-loop closure), give future agents a
single anchor that shows, at a glance: **what is decided** (don't re-litigate), **what is unproven**
(needs data/dogfood), and **what needs the operator** (design calls).
**Companions:** [`stage-m29-mainline-return-audit.md`](./stage-m29-mainline-return-audit.md) ·
[`stage-m31-mainline-capability-closure.md`](./stage-m31-mainline-capability-closure.md) ·
[`mainline-return-matrix.md`](./mainline-return-matrix.md) (an M31 snapshot; **predates post-M31
product-surface updates** — several "missing" rows have since shipped, e.g. ask/digest/doctor/mcp).
**Date:** 2026-07-01.

> **Read this first.** Rust is **not a 1:1 port of Python** — it is a deliberate re-architecture.
> Python's heart (eager concept/entity/canonical/MOC/evergreen ontology) was found to be the
> **wrong root** (0/3 on real models across ~8 milestones) and was **demoted, not ported**.
> "Retire Python" means **ship the grounded vertical + migrate the data + prove it in dogfood**,
> NOT "re-implement the 84 legacy commands."
>
> **Visualization is a separate track** from this retirement track (operator-confirmed). The graph
> viz work (committed `b2b0692`) does not gate, and is not gated by, Python retirement.

---

## 1. Product definition (钉死 — do not re-litigate)

**Decision:** OVP is a **grounded, auditable durable-memory layer over your reading**.

```
Source ─▶ Grounded Units      each unit bound to a verbatim source quote + line number
       ─▶ Crystal claims      cross-source, citation-chained, fail-loud gate (ungrounded dies)
       ─▶ Browse / Query (find, ask, digest) / Visualize (truth-provenance knowledge graph)
```

**The moat is the truth layer:** every durable claim traces, one click, to the exact source quote
(with line). Nothing ungrounded survives the pre-write gate.

**Student promise (user benefit, not eng-speak):** a student drops in a paper / web article /
lecture note and gets back — a **reader pack** (the source distilled into grounded cards with
quotes), **durable Crystal claims** (the cross-source takeaways worth keeping), a **review queue**
(what needs a human call), **digest / ask** (daily synthesis + Q&A over their own library), and
**traceable Crystal notes** (every claim clickable down to the original sentence). The payoff is
*trust*: they can defend any note back to its source.

**What OVP is NOT:** a concept/entity ontology, a MOC/Atlas builder, a semantic-embedding RAG store,
or a "collect more memories" tool.

## 2. Differentiation vs Knowledge Mem

**Decision:** we compete on **verifiable truth**, not memory volume.

| Dimension | Knowledge Mem | OVP |
|---|---|---|
| Unit of memory | atomic-memory titles | grounded claims + citation chain to quote+line |
| Grounding | retrieval over memories | fail-loud gate: ungrounded claims can't be written |
| Graph | dense unlabeled hairball | labeled communities, one-click claim→quote provenance |
| Core-point coverage (M26 AB) | 58% (120/206) | 87% (180/206) |
| Verdict (M26 AB) | — | 17 ovp_better · 3 tie · 0 kmem_better |
| Maturity / breadth | high (semantic search, chat, views, scale) | narrower; validated on truth, not yet at scale |

**Honest weaknesses:** KMEM has semantic search / chat / multiple views / polish / proven scale;
OVP has none of those yet and is **unproven at their data volume**.

**Caveat on the numbers:** 87% vs 58% is from **20 curated cases**. This differentiation must be
**re-measured on a random sample of our own real corpus** (§6) before it is a product claim.

## 3. Where we are — Level ladder + Level-3 exit criteria

**Current:**
- [x] **Level 1** — active Rust dev branch; current HEAD gates green (`cargo test --workspace`,
      `clippy -D warnings`, `check_architecture.sh`). *(Do not anchor on a test count — it moves.)*
- [x] **Level 2 P0 set — SHIPPED (M30/M31, `770dbd6`/`754011e`).** `ovp-next daily` runs the real
      vault loop: intake (URL+sha256 dedup) → lifecycle L0→L4 → grounded reader trunk → durable
      ledger+audit → JSON read index → console + `find`. Plus `ask` / `digest` / `doctor` / `mcp` /
      `project` / `serve` + graph viz are PRODUCT.
- [ ] **Level 3 — delete the Python mainline.** Tracked by this doc.

**Level-3 exit criteria (all must be true to delete Python):**
- [ ] Full corpus run complete (no unprocessed inbox backlog).
- [ ] Every `blocked`/failed source **classified** (transport vs real content defect); real defects fixed or explicitly waived.
- [ ] `crystal-synth` is **reproducible** (turnkey, not bespoke scripts) and the corpus is crystallized through it.
- [ ] Random-sample **AB vs KMEM** completed (ingest + crystal layers) with a recorded verdict.
- [ ] **≥2 weeks** real daily dogfood on Rust with **no Python fallback**.
- [ ] **No data loss**: existing vault/knowledge state migrated or explicit sign-off to abandon.

## 4. Data migration + real test (P0 — the priority)

**Decision:** the full corpus run **is** the knowledge.db→Rust migration (old SQLite is a 0-byte
shell; rebuilding IS the migration story).

**Current:** a **100-source pilot** ran (2026-06): **34 succeeded, 66 failed**; the failures that
were **sampled/inspected by operator audit were all transport-level** (`error sending request`,
i.e. endpoint overload under 12-way concurrency), not content/quality defects. The 34 successes →
795 grounded units → 30 candidate claims (MiniMax `MiniMax-M2.7-highspeed`) →
**85/88 citations verbatim-grounded → 18 durable claims**, visualized. Grounding quality on real
data is good; throughput (~9.5 min/source, ~99% network wait) is the only constraint → parallelism.

**Gap:**
- Full corpus (~1012 processed + ~390 pinboard archive) not yet run.
- Failures not yet exhaustively classified.
- **`crystal-synth` is bespoke.** `crystal-lint` and `crystal-write` (and `crystal-review`) already
  exist and work. What is missing is the **synthesis + orchestration** around them:
  1. generate a **structured Crystal candidate** from reader packs (units-catalog → clustered
     cross-source synthesis) — today this is `.run/m27/m27-candidate-gen.js` + ad-hoc Python;
  2. **generate/collect strength verdicts** (the model-based strength judgment) — today hand/heuristic;
  3. **orchestrate** lint → strength → write → `project` → index/console refresh into one path.

**Done when:** low-concurrency full re-run finishes; every still-failing source is classified;
`crystal-synth` runs the corpus reproducibly; the crystal store reflects the whole library.

- [ ] Re-run full corpus at **low concurrency (~6-way)** (12-way overloaded the endpoint).
- [ ] **Failure triage:** after the rerun, isolate any source that **still** fails, classify by
      type/language/length; only those are real problems. Do not blanket-blame the network.
- [ ] Build `crystal-synth` (synthesis + verdicts + orchestration; wraps existing lint/write).
- [ ] Crystallize the full corpus through it.

## 5. Ingest gap breakdown (paper / github / web-fetch / images)

**Decision:** the enrichment machinery mostly **exists and is already wired into `daily`**
(`ovp-enrich`: `github.rs` / `web_fetch.rs` / `image_download.rs`; full paper path in
`ovp-domain`). **The remaining gaps are the paper-routing decision plus live validation of the
enrichment paths** — not greenfield rewrites.

| Capability | Status |
|---|---|
| Paper deep-dive (arXiv) | needs-decision + wiring |
| GitHub enrichment | wired; validate-live |
| Web-fetch (bare bookmarks / needs-content) | wired; validate-live |
| Images / attachments | wired (Phase 4.5); validate-live |

- **Paper deep-dive** — Current: full transforms + prompt + arxiv fixture exist; `daily` has **no
  paper routing** (everything goes through the generic grounded-reader). Gap: decide + (maybe) wire.
  Done when: an A/B decides generic-vs-paper reader (see §11).
- **GitHub enrichment** — Current: wired into `daily` (`enrich_github_repos`, `--github-fixture` /
  `--github-live`). Gap: live not validated on real repos; enriched→reader quality untested.
- **Web-fetch** — Current: wired (`enrich_needs_content`, `--web-fetch-fixture` / `--web-fetch-live`).
  Gap: live fetch success rate + content quality unknown.
- **Images / attachments** — Current: **wired into `daily` (Phase 4.5)** behind `--image-fixture` /
  `--image-live` (`image_download.rs`). Gap: live download validation, **attachment path-rewrite**
  validation, and failure behavior (what happens when an image 404s / times out).

## 6. KMEM quality comparison (two layers, on real data)

**Decision:** measure both the ingest layer and the crystal layer against KMEM on **our** real data.

**Current:** tooling exists — `compare-run` / `ovp-eval` (5 lexical dims: concept overlap, claim
diff, grounding, structure, retrieval; per-source, real-LLM + network) + the M26 article-level AB
workbench (human+LLM judged). The only prior result is the 20 curated cases (§2).

**Done when:** both layers have a recorded verdict on a random real sample.

- [ ] **Ingest/unit layer** — `compare-run` on a real sample: our grounded units vs KMEM memories
      (grounding, coverage, concept overlap).
- [ ] **Crystal/claim layer** — re-run the **M26 AB** on a **random real sample** from the full
      corpus (core-point coverage, factual issues, granularity, verdict). Proves 87%-vs-58% on *our* data.

## 7. Cut-decision re-confirmation (逐个确认)

**Decision:** the following are settled — do not re-litigate as product roots.

- [x] **Concept/entity detection (concept-map v2, M13) — DEAD.** 0/3 real; "synthetic-green, not
      real-green"; no writer patch fixes it. Not revived.
- [x] **Referent candidates (M14b) — DEAD (0/3).**
- [x] **Evergreen ≠ concept-detection** (coupled in legacy). Evergreen = minting atomic permanent
      notes; concept-detection = the failed ontology. **Evergreen's value is delivered by Crystal +
      `project --write`**, reshaped and renamed **"Crystal Notes"** (not a legacy-evergreen revival).
  - Gap: if students need better browsing, **do not** revive legacy evergreen — **extend the
    projection template** instead: topic · claim · evidence · related claims · source list · review
    prompts. (See §11.)
  - [ ] Verify Crystal Notes (`project --write`) cover the old evergreen use (browsable / linkable).
- [x] **RAG — lexical is the product; semantic embeddings deferred (not permanently cut).** Reuse
      surface = `ask` / `find` / `digest` over the JSON index (lexical). Add BM25+embedding+RRF
      only if daily query pain proves the need.
      *(2026-07-02: the "revisit if proven" clause was exercised for the **synthesis grouping
      layer** — 87% cluster-cap drop + 40% misc on the full corpus. See
      [`stage-m34-knowledge-substrate-design.md`](./stage-m34-knowledge-substrate-design.md).
      The query surface stays lexical.)*

## 8. Hygiene (refactor, not features — lower priority than migration)

**Decision:** **data migration is a higher priority than code deletion.** Quarantine before delete.

- [ ] Quarantine / feature-gate the demoted M7–M13 substrate (`canonical`/`moc`/`knowledge_index`/
      `concept_registry`/`evergreen`, and `referents/`) behind e.g. `--features legacy-substrate`.
- [ ] **Delete `referents/` only after** arch gates prove no product crate imports it.
- [ ] Group diagnostic/eval CLI verbs distinctly from product verbs.
- [ ] Refresh docs: mark `legacy-alignment.md` superseded; extend `architecture.md` to M27/M28.

## 9. Explicit non-goals for Level 3

**Permanently cut** (do not rebuild): concept/entity canonicalization · ConceptRegistry · MOC/Atlas
· legacy Evergreen minting ontology · Referent candidates · SQLite `knowledge.db` · KMEM as a
product dependency (eval-only, fenced).

**Deferred, not cut** (revisit only if real usage proves the need): semantic RAG
(embeddings/RRF/FTS5) · contradiction detection · task dispatcher · export artifact · `ovp-autopilot`
watch daemon (cron over `daily` may suffice).

## 10. Execution order

**P0 (blocks Level 3):**
1. [ ] Low-concurrency full re-run + failure triage (§4).
2. [ ] `crystal-synth` turnkey (synthesis + verdicts + orchestration) (§4).
3. [ ] Crystallize full corpus (§4).
4. [ ] `compare-run` + M26 AB on a random real sample (§6).
5. [ ] ≥2-week dogfood → Level-3 go/no-go (§3 exit criteria).

**P1 (quality / breadth, parallelizable):**
6. [ ] Paper routing A/B + decision (§5, §11).
7. [ ] Live validation: github / web-fetch / images (§5).
8. [ ] Verify Crystal Notes cover evergreen use; extend projection template if needed (§7, §11).
9. [ ] Hygiene: quarantine substrate, doc refresh (§8).

## 11. Open decisions — operator-guided direction

- **Paper routing** — *Direction:* do **not** wire the old paper vault-plan into `daily` directly.
  First run a **5–10 paper A/B** (generic reader vs paper-specific reader). Wire the paper path
  **only if** it is clearly better for student learning, and even then it must emit
  **reader-pack-compatible artifacts** — do **not** revive the demoted vault-plan mainline.
  - [ ] Run the A/B and record the decision.
- **Evergreen replacement** — *Direction:* `Crystal + project --write` is accepted as sufficient
  for legacy Evergreen's core value, **renamed/reshaped as "Crystal Notes."** If students need
  richer browsing, **extend the projection template** (topic · claim · evidence · related claims ·
  source list · review prompts) rather than reviving legacy evergreen.
  - [ ] Confirm after seeing Crystal Notes on real data.
