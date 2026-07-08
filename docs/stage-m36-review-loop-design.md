# Stage M36 — Review Loop Redesign: from failure queue to verifiable repair workshop

**Type:** Design anchor for G4 (review/lifecycle), same discipline as
[`stage-m34`](./stage-m34-knowledge-substrate-design.md): decisions separated from hypotheses,
implementation gated on experiments, kill criteria per phase.
**Status:** Direction designed 2026-07-07 against the operator's open brief. Phase R0 is a
no-code experiment; nothing beyond R0 may start before its numbers land.
**Required context (proposals contradicting these need a NEW experiment type, not re-argument):**
M34 §7.4 fired verdicts (no entity layer for answers; projections over ledgers) ·
[`review-failure-taxonomy.md`](./review-failure-taxonomy.md) (the decidability rule) ·
M35 phase 0 (lane routing; the semantic-duplicate boundary) · S2v3 (KMEM surface 43% unsupported
vs sources).

**Hard constraints (operator-set, non-negotiable):** the mechanical grounding gate is never
replaced by an LLM · nothing ungrounded enters durable truth · no silent dropping of valuable
claims to shrink a queue · every automated action is auditable, replayable, and
reversible-or-rebuildable · experiment before productization · prompt rule piles are not
architecture.

---

## 1. What the Review lane actually is (redefinition — DECISION)

After M35 routed single-source Supported claims out (real queue: review=79, insight=41), what
remains is NOT a failure queue. Every item in it is **grounded** (it passed the mechanical gate)
and was flagged by the judge for one thing: **the claim TEXT asserts more than the cited
EVIDENCE supports** (overreach 14 / over_synthesized 5 / opinion_as_fact 1 in the current
batch). The evidence is good; the text/evidence pairing is broken.

> **The Review lane is a repair workshop for text↔evidence mismatches.** Every mismatch has
> exactly two repair directions: **shrink the text** (narrow/split until it matches the
> evidence) or **grow the evidence** (find more sources until it matches the text).

Three consequences:

1. **Repair success is DECIDABLE.** A repaired claim either passes lint + strength or it
   doesn't. This is what makes agent automation safe here: the gate that protects the moat is
   the same machinery that adjudicates agent work. *Human decisions never bypass the gate —
   and neither do agent decisions.*
2. **Not product-facing.** Users see Topics / Crystals / Evidence (M34); the workshop is
   internal. Its OUTPUTS surface: durable claims, source insights, and (future) "wanted
   sources" capture priorities.
3. **SourceInsight is not review debt AND not waste** — it is the promotion pool: a claim
   whose text already matches its (single-source) evidence, waiting for the evidence side to
   grow. The highest-value automation in this whole design is feeding it (§3, backfill).

## 2. Critique of `rewrite / split / keep_caveated / reject` (DECISION: replace, additively)

The M25-era vocabulary was built for a human workbench and has four structural faults:

1. **It conflates text operations with evidence operations.** `rewrite` covers both "narrow
   the wording" and "re-cite different evidence" — different risk classes, different automation
   levels.
2. **`keep_caveated` is a non-decision.** It records no trigger condition, so the same item
   re-presents in every future session — queue rot by design.
3. **`reject` conflates "wrong" with "not crystal material".** An implementation detail is
   true and useful — it belongs demoted to the reader/source-insight surface, not deleted.
4. **There are no evidence-side or lineage actions at all** — no way to say "this needs a
   second source" or "this strengthens an existing claim". The M35 fixture proved the flagship
   "duplicate" pair (zero shared units, jaccard 0.27) is a *strengthen* case; the current
   vocabulary cannot even express it.

**Typed action vocabulary v2** (additive schema; old four kept as aliases during migration).
Every action is machine-executable, replayable, and ledger-audited:

| Action | Kind | Auto? | Semantics |
|---|---|---|---|
| `narrow` | text | agent-executable | one narrower claim, same evidence → re-gate |
| `split_by_evidence` | text | agent-executable | N claims, evidence partitioned → re-gate each |
| `backfill_evidence` | evidence | agent-executable | targeted retrieval over the EXISTING corpus (evidence sidecar / `find --kind units`) → add citations → re-gate |
| `merge_into` / `strengthen` | lineage | agent-executable | union citations into an existing claim → re-gate (the semantic-dup resolution) |
| `demote_to_source_insight` | lane | agent-executable | true-but-narrow (impl detail) → parked insight, not deleted |
| `defer_until <trigger>` | parking | agent-executable | trigger comes from a CLOSED, checkable vocabulary (e.g. `new_sources_in_theme`, `corpus_grows_by_n`) — no free-text never-firing triggers; deferred items stay visible in queue accounting and the spot-audit sample, so deferral can never become silent value loss |
| `request_external_source` | capture | drafts a "wanted source" entry | feeds capture priorities; no claim mutation |
| `reject_as_noise` | destructive | **human-confirmed, always** | permanent removal with reason |

## 3. Division of labor — by the state-write principle (DECISION)

The safety boundary is not *which* actions an agent may take; it is **what state an action can
write to**:

- **Anything whose output re-enters the mechanical gate + judge is agent-safe** (all text /
  evidence / lineage / lane / parking actions above). A bad draft costs one gate rejection,
  never a bad durable claim.
- **Anything that permanently removes material from consideration** (`reject_as_noise`) or
  commits an outward-facing promise is **human-confirmed** — batched, not per-item.
- **Deterministic code keeps its monopoly** (unchanged): citation lint, quote containment,
  unit resolution, schema validation, idempotent ledger writes, lane accounting,
  shared-evidence dedup — plus one NEW decidable guard (below).

**Anti-gaming guard (new, decidable).** The known failure mode of "agent narrows until it
passes" is a claim collapsed into a worthless quote restatement. Triviality proxy:
**token containment, not symmetric jaccard** — flag a repaired claim when ≥ ~80% of its
content tokens are contained in its own citations' quotes (symmetric jaccard is length-biased:
a short claim against long concatenated quotes scores low even when it is a pure restatement).
Also flag when a repair lost the cross-source property its parent had. Flag → route to human
sample, never auto-durable. Backstop: the human spot audit (§5). This follows the decidability
rule: a decidable proxy plus audit, not a prompt admonition.

**The human's irreducible role** (end state = exception-only): confirm rejects (batch) ·
theme-level value/priority calls (is this theme crystal-worthy at all) · spot audits of the
system (not of every item) · the metrics review that decides each phase gate.

## 4. Connection to the M34 graph question (DECISION, boundary-setting)

- The review lane produces **no anchors and no identity** — that would be eager ontology
  through the back door. Anchors, if they ever exist, derive from durable claims only
  (M34: projection-first).
- SourceInsight is legitimate raw material for future topic/community *surfaces* (display
  grouping), never for identity authority.
- Semantic duplicates ARE lineage (fixture-proven). `strengthen` is the first lineage verb to
  ship; subject-keyed lifecycle stays gated behind M34's experiment verdicts.

## 5. Evaluation protocol (DECISION)

- **Labels-from-decisions:** every applied batch freezes as regression labels (extends the
  `review-hygiene-m35` fixture pattern). Human labeling costs nothing extra — the decisions ARE
  the labels.
- **Per-batch scorecard:** review-yield (non-keep decision share) · queue net flow (in vs out)
  · durable-added-via-repair · backfill hit rate · triviality-flag rate · human minutes per
  item · **tokens per resolved item** (economics is a first-class column) · false-reject and
  false-durable from a 20% human spot audit.
- **Two-frame quality check (S2v3 pattern):** spot-audit agent-repaired durable claims against
  synth-direct durable claims — repaired claims must be indistinguishable on grounding and
  usefulness.
- A phase advances ONLY on its scorecard; "queue got smaller" alone is never sufficient (hard
  constraint: no silent value loss).

## 6. Roadmap — subordinate to the M32 clock (go/no-go ~2026-07-20; nothing below destabilizes the daily loop)

| Phase | What | Automation | Ledger | Human | Kill criteria |
|---|---|---|---|---|---|
| **R0 — experiment, NO code (this week)** | (a) current 20-item session: agent drafts decisions with existing vocabulary, operator confirms, apply via existing command — baselines human-minutes + yield; (b) **backfill probe**: for **10** SourceInsights (¼ of the pool — enough to distinguish ~0% from ~20% hit rates; 3–5 could zero out by chance), hunt second sources in the existing corpus via `find --kind units` BY HAND | drafts only | normal apply path only | confirms everything | backfill hits 0–1 of 10 → deprioritize corpus-internal backfill (external capture outranks it); drafting saves <50% human time → rethink R1 scope |
| **R1 — operator assist (1 PR, after R0 numbers)** | typed action vocabulary (additive `ReviewDecision` schema) + `review-session suggest` (agent drafts into the template, citations included) + triviality proxy (warn-only) + `defer_until` trigger checking in prepare | agent drafts, human applies | apply path unchanged | confirms per batch | yield/time metrics flat vs R0 → stop |
| **R2 — semi-auto (gated on R1 scorecard AND M32 go/no-go PASSED)** | gate-re-entrant actions auto-apply when lint+strength pass AND triviality clean; rejects remain human-batched; weekly scheduler tier runs the loop; every auto action = audited ledger event | narrow/backfill/merge auto | auto events, replayable | rejects + 20% spot audit | false-durable > 0 in audit → back to R1; triviality flags trending up → tighten proxy or stop |
| **R3 — productionized (≥2 weeks of clean R2 metrics)** | exception-only review; `request_external_source` feeds capture priorities ("wanted sources"); `strengthen` automated for the zero-shared-unit semantic-dup class | full loop | full | exceptions + audits | any hard-constraint breach → freeze to R1 |

## 7. OVP vs KMEM under agent labor (JUDGMENT)

The quote-grounded route's complexity premium was historically paid in **human review time**.
Agent labor changes the calculus asymmetrically, in our favor:

- Our repairs are **verifiable**, so agents can do them safely at scale — the mechanical gate
  is not just the moat, it is **the enabling infrastructure for agent self-operation**. The
  premium's cost collapses while its value (trust, 19.6% vs 43% unsupported, S2v3) stands.
- KMEM's 43% unsupported rate is structural: without evidence anchors there is nothing for an
  agent to repair *against* — agent labor can generate more summaries, not make them
  verifiable after the fact.

Verdict: in the agent era the quote-backed route becomes MORE attractive, not less. The review
loop is where that advantage compounds: every repaired claim is new durable knowledge that a
summary-first system cannot mint safely.

## 8. Hypotheses (NOT decisions — each dies by its phase's kill criteria)

- **H-R1:** agent drafting cuts human minutes/item by ≥50% at equal-or-better yield.
- **H-R2:** ≥30% of the current review lane is auto-repairable (narrow/backfill/merge passing
  the gate + triviality) without any false-durable in audit.
- **H-R3:** corpus-internal backfill promotes ≥20% of SourceInsights to durable (if not, the
  promotion pool depends on external capture, and `request_external_source` outranks it).
