# OVP Product Pipeline Map — the mature-system design anchor

**Type:** System-design anchor. Sits between the product anchor
([`stage-m32`](./stage-m32-python-retirement-and-product-definition.md): what OVP is and why) and
the stage docs (how/when things shipped). [`stage-m34`](./stage-m34-knowledge-substrate-design.md)
remains a HYPOTHESIS doc for the sensemaking group and does not override this map.
**Answers exactly one question:** what does mature OVP run — per ingest, daily, weekly, on demand —
and for each pipeline: input, output, authority-or-derived, cost class, failure states, status today.
**Scope boundary:** single operator, single vault. Multi-user/multi-vault is out of scope for this map.
**Date:** 2026-07-03.

---

## 1. The foundation (inverted vs KMEM)

```
KMEM:  memory → LLM entity graph → community → crystal/insight     (graph is the base)
OVP:   source → quote-backed units → gated crystal claims          (truth is the base)
       → every surface (search, digest, graph, notes, UI) DERIVES from it
```

Entity/graph/community may become a sensemaking layer (M34 experiment decides); they can never
become a second, unverified truth store.

## 2. Architecture principles (each is load-bearing; violations are review-blockers)

1. **Explicit authority set — everything else is a projection.** Authorities (append-only /
   never-overwrite; the backup set; the ONLY inputs disaster recovery needs):
   - **Source files** in the vault — user-owned ground truth; OVP never rewrites content, only
     moves with collision-safe, ledgered `safe_move`.
   - **Event ledgers** (`.ovp/*.jsonl`, `60-Logs/pipeline.jsonl`) — what happened, in order.
   - **Reader packs** (`40-Resources/Reader/<case>/`, esp. `units.accepted.json`) — the EVIDENCE
     authority. Claims cite into packs; deleting a pack severs citation chains. (Correction to the
     "crystal ledger is the only authority" framing: packs are co-equal.) A pack is an evidence
     *artifact*, not a ledger, so "append-only" is not a sufficient contract — its **integrity
     contract** is: pack identity bound to (source content sha, run id); `units.accepted.json`
     present and parseable; every crystal citation back-resolvable to a pack unit and its verbatim
     quote; and `doctor` verifies the full claim→pack→unit→quote chain. A pack failing this
     contract counts as evidence loss, not as a stale derived view.
   - **Crystal ledger** (`.ovp/crystal/ledger.jsonl`) — the durable-knowledge authority.
   Everything else — index, console, digest, working memory, vault projections, graphs, ask
   answers — is **derived: deletable, rebuildable, forbidden to flow back into authorities**.
   *Projection is not truth. Digest is not truth. Working memory is not truth. An ask answer is
   not truth.*
2. **Read/write split.** Authorities are the write side (append-only). All user surfaces read
   from derived read models rebuilt from authorities (`index` is the canonical rebuild). This is
   what makes "what can I delete?" and "how do I recover?" trivial questions.
3. **Every pipeline is idempotent, crash-resumable, and has ENUMERATED failure states** surfaced
   in doctor + console. Learned the hard way (2026-07 review: cassette pinning, stale locks, torn
   ledger lines, orphan packs — all "failure state nobody designed"). A pipeline without a
   written failure table is not done.
4. **Cost class is a pipeline property.** Three classes: `0` zero-token (fs/deterministic),
   `L` local compute (embeddings), `$` LLM-spending. The daily loop's `$` spend is bounded and
   reported (cost-report pipeline). New `$` pipelines need justification.
5. **Contracts at group boundaries are versioned.** `units.accepted.json`, claim records, ledger
   event schemas, index schema. Prompt changes already go through the evolution flow; artifact
   schema changes get the same discipline (additive-preferred, version-marked).
6. **Truth-gate invariants hold at every step:** `accepted_without_quote = 0`; nothing ungrounded
   is written durable; every durable object traces to verbatim quote + line.

## 3. Pipeline groups

Legend — Auth: A=authority-writing, D=derived-writing. Cost: 0/L/$. Status: ✅ shipped ·
🟡 partial · ⬜ missing.

### G1 Capture / Intake — get material in, safely

| Pipeline | In → Out | Auth | Cost | Status | Failure states |
|---|---|---|---|---|---|
| pinboard-sync | API/export → capture files + `pinboard-sync.jsonl` | A | 0 | ✅ | fetch-fail (retry next run) |
| intake sweep | Clippings/00-Capture/02-Pinboard → 01-Raw, normalize | A | 0 | ✅ | needs_content · duplicate(parked) · thin/broken(flagged) |
| dedup | URL + content-sha256 | A | 0 | ✅ | — (dupes parked, ledgered) |
| web-fetch enrich | bare bookmarks → body | A | 0 | 🟡 wired, live unvalidated; same-run vs next-run reader pickup needs fixture verification | fetch-fail → needs_content persists |
| github enrich | repo URLs → README/metadata | A | 0 | 🟡 wired, live unvalidated | api-fail → unenriched, readable anyway |
| image/attachment | download to attachments, source hash untouched | A | 0 | 🟡 wired (Phase 4.5), live unvalidated | 404/timeout behavior undefined — validate |
| paper route | arXiv → dedicated reader? | — | — | ⬜ decision pending (M32 §11 A/B) | — |

Principles: source identity stable · never overwrite user content · never lose data.

### G2 Grounded Reader — source → verifiable units (the moat's first half)

| Pipeline | In → Out | Auth | Cost | Status | Failure states |
|---|---|---|---|---|---|
| read-source | source → spans/lines → units → quote validation → critic repair → cards → pack | **A (packs)** | $ | ✅ (994/1012 on real corpus) | transport(retry-free via cassette) · truth-layer(unit/card json, 0-units, grounding) — bad replies un-pinned since Stage 0.5 · context_window · no_text_blocks |

Requirements at maturity: every accepted unit has a verbatim, findable quote; attribution/modality
preserved; the pack is a user-facing product, not intermediate data. This is where OVP diverges
from KMEM: their memory is a self-contained summary; our unit is an evidence-anchored fragment.

### G3 Crystal Truth — units → durable knowledge (the moat's second half)

| Pipeline | In → Out | Auth | Cost | Status | Failure states |
|---|---|---|---|---|---|
| grouping | packs → clusters/communities | D | L (target) / 0 (today) | 🟡 keyword buckets falsified at scale; embeddings = M34 decided, S1 pending | title-fallback · low-quality `misc` communities |
| crystal-synth | clusters → candidate claims | D | $ | 🟡 Stage 3a full-coverage batching implemented; 994-corpus live run pending | unrecoverable JSON (un-pinned) · oversized internal batch · residual duplicates |
| citation-lint | candidate → per-citation verbatim check | D | 0 | ✅ | defect → claim dropped, audited |
| strength-judge | grounded claims → supported/caveated/reject | D | $ | ✅ chunked ≤20 claims/call | incomplete coverage → fail-loud |
| crystal-write | durable claims → ledger, append-only, idempotent | **A** | 0 | ✅ | — (gate unsatisfied = no write) |

### G4 Review / Lifecycle — uncertainty and evolution, at the CLAIM layer

| Pipeline | In → Out | Auth | Cost | Status | Failure states |
|---|---|---|---|---|---|
| review-queue | caveated/reject → human decision → revised candidate → RE-GATE | A (via write) | 0+human | ✅ loop shipped: bounded `crystal-review-session` prepare + turnkey `crystal-review-session-apply` (decisions → strength gate → durable write → project/index/console refresh; reviewed entries retire, unprocessed queue preserved) | stale queue (weekly SLA is now an operator ritual, not a tooling gap) |
| lineage: dedup/strengthen | new claims vs active (text+citation overlap+grouping) | A (via write) | 0/$ | ⬜ near-term, minimal form | wrong-merge (conservative default: append) |
| supersede | strengthened claim replaces old, `superseded_by` | A | 0 | ⬜ mid-term | — |
| contradiction | opposing claims, same subject | A | $ | ⬜ long-term; needs stable subject — **M34 experiment decides** | — |

Rule: no entity ledger before the M34 verdict; lifecycle starts in its minimal viable form.

### G5 Sensemaking — understand a corpus (decided vs undecided kept separate)

Decided: embeddings-for-grouping (L) · communities for synthesis batching · crystal view ·
search/ask/digest over the index · graph visualization over claims+evidence (✅ M33 console SPA).
Undecided (M34 four-arm experiment): durable entities/anchors · promotion ladder ·
entity-based lifecycle · query-time aggregation as the primary reuse surface (arm D).
Default mature route until data says otherwise: `claims + evidence → index → find/ask/digest →
console/graph`.

### G6 Projection / Output — what the user touches daily (ALL derived)

| Pipeline | Out | Cost | Status |
|---|---|---|---|
| index rebuild | `.ovp/index/` read model | 0 | ✅ |
| console | `.ovp/console/` bilingual UI + graph | 0 | ✅ |
| project --write | `10-Knowledge/Crystal/*.md` (machine-managed marker) | 0 | ✅ |
| digest | `.ovp/digests/<date>.md` — ops digest (new packs/blocked/claim counts) | 0 today · $ optional (`render_llm_digest` exists, unwired) | ✅ plain |
| working-memory | `.ovp/working-memory.md` | 0 today | ✅ |
| ask / find | on-demand answers. `find` is deterministic lexical search over the read model and evidence sidecar. `ask/v2` is retrieval-constrained over claims + cards + units and runs a deterministic citation verifier after the model answer: cited evidence ids must be among the blocks supplied to the model; unit citations must retain quote-backed evidence; card citations must retain cited units. Strict mode can fail an uncited/unverified answer. This is **citation verification, not whole-answer semantic truth-gating**. | $/0 | 🟡 |
| serve / MCP | localhost UI · MCP tools (find/search/status + shallow doctor only; no ask/project/crystal-status) | 0 | ✅ minimal |

Boundary: these can be deleted and rebuilt at any time and MUST NOT write back into authorities.

### G7 Ops / Maintenance — keep it runnable for years

| Pipeline | Mature duty | Cost | Status |
|---|---|---|---|
| doctor | ledger↔fs consistency · orphan packs · stale index · **crystal integrity (claim→pack citation chains)** | 0 | 🟡 exists; crystal-chain + orphan checks to add |
| retry/blocked | failed sources, 3-strikes, `--retry-blocked` (real retries since Stage 0.5) | 0 | ✅ |
| evolution | prompt/parser/gate changes: candidate spec + AB + ledger + rollback | $ | ✅ |
| cassette replay | regression: replay recorded model IO | 0 | ✅ |
| cost report | per-run/per-pipeline token + wall-time + failure distribution | 0 | ⬜ |
| periodic recluster | regroup for synthesis; NEVER silently rewrites truth | L | ⬜ (with dirty-marking) |
| scheduler | the single entry that sequences everything below | 0 | ⬜ (today: manual `daily` + hand-run commands) |

OVP maintenance ≠ KMEM compaction. Ours is: keep evidence chains intact · keep derived views
rebuildable · keep failures explainable · keep prompt evolution rollbackable. Evidence is never
compacted.

## 4. Orchestration (the layer that makes it a product, not a CLI collection)

Design: **batch, single-entry, tiered scheduling** — not a daemon (M32: cron over `daily`
suffices until proven otherwise). All pipelines idempotent + crash-resumable (RunLock with stale
reclaim; append-only ledgers; cassette-resumable model calls). **Dirty-marking is the one
incremental mechanism** shared by index/console/synth/recluster — no pipeline invents its own.

Do not conflate what `daily` does TODAY with the mature scheduler — the gap is the work:

| Tier | **Current** (`ovp2 daily` today) | **Mature scheduler** (target) |
|---|---|---|
| per ingest | intake → dedup → enrich(fixture-gated) → reader → lifecycle move → ledgers ✅ | same, with live-validated enrich + paper route |
| daily | report → index → console → plain digest → working-memory | + **incremental crystal-synth on dirty groups** (entry conditions below) + doctor summary |
| weekly | — (manual) | recluster (churn-bounded) → dirty-community resynth → **review-queue session** |
| on demand | ask · find · project · serve · MCP · compare-run ✅ | same, with deeper answer verification |
| on prompt change | evolve candidate → AB → cassette replay gate ✅ | same |

**Entry conditions before auto-synth joins the daily tier** (it does NOT get scheduled by
default): Stage 3a merged (full-coverage batching) · `--strict-cluster-cap` on ·
a per-run token budget cap · the dirty-group spec implemented · synth failure states
designed and doctor-visible. Until all five hold, crystal-synth stays a manual command.

## 5. KMEM's 12 pipelines → OVP mapping

| KMEM | OVP counterpart |
|---|---|
| kg-extraction | NOT foundational; candidate = grounded anchor projection (M34 arm C-lite) |
| community-detection | embedding grouping for synthesis batching (G3) |
| crystallization | crystal-synth + lint + strength + write (G3, gated) |
| insight-detection | **ops digest today** (new packs/blocked/counts — NOT cross-source pattern/contradiction/emerging-topic detection); claim-pattern detection is future work |
| evolves | claim lifecycle (G4) |
| memory-compaction | claim dedup/strengthen/supersede — evidence itself is NEVER compacted |
| label-consolidation | tags/labels as projections, never authority |
| unit-type-reclassification | unit/card type refinement via evolution flow |
| decay-refresh | derived ranking scores in query/digest — no truth decay |
| daily-briefing | digest + working-memory (G6) |
| wm-refresh | working-memory projection (G6) |
| rule/skill review | out of scope unless OVP extends to agent-ops |

## 6. Gap list — current → mature (the actionable part)

P0 (product doesn't hold long-term without): scheduler tiers (§4) · Stage 3a 994-corpus synth
run/signoff · review-queue apply loop with weekly cadence · doctor crystal-integrity checks.
P1: incremental dirty-group synth · cost report · minimal lineage (dedup/strengthen) ·
**ask semantic verifier** (the shipped deterministic citation verifier checks evidence ids and
quote-backed unit presence; it does not yet prove every answer sentence is semantically entailed) ·
**MCP mature surface** (ask/project/crystal-status; deep doctor) ·
web-fetch same-run fix · UTC→local date · live validation of enrich paths · paper-route A/B.
Gated on M34 experiment: everything in G5-undecided, supersede-by-subject, contradiction.
