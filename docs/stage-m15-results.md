# Stage M15 — results: OVP truth-layer + card-view vs KnowledgeMEM

> **Registered verdict (frozen thresholds, no goalpost-moving): H1 = FAIL, H2 =
> INCONCLUSIVE.** But the *fail is readability-only* — OVP grounded-Units→cited-cards
> **won faithfulness** (0.919 vs 0.910, + provenance KMEM lacks) and **won coverage
> decisively** (0.878 vs 0.670), and lost ONLY on blind readability (KMEM 11/11).
> So the experiment **validates the truth-layer + card-view architecture on
> correctness/coverage/provenance**, isolates the problem to **card-synthesis
> presentation** (a prompt/format issue, NOT the truth layer and NOT a missing
> Referent), and finds the **Referent/Resolver layer is not needed in the main
> path** (only a narrow identity-resolution helper). Decision: keep the simplified
> trunk; demote Referent to an optional helper; fix card-view readability next.

## Method (as pre-registered)

Per `docs/stage-m15-methodology-audit.md` + `docs/stage-m15-knowledge-mem-evidence-audit.md`.
- Sample: N=12 deterministic draw (seed 20260603) from the 943-article pool, tuned-3
  excluded (`docs/m15/sample-manifest.md`). **11 scored** — case `s03` dropped (a
  judge failed to emit structured output); the 11-0 readability sweep and the
  faithfulness/coverage aggregates are unaffected by the one drop.
- KMEM arm: local Nowledge Mem 0.8.6 service — ingest → `/extract` → poll → read
  memories; **full content re-fetched** via `GET /memories/{id}` (the
  `/sources/{id}` projection truncates to 200 chars) for a fair comparison.
- OVP arm: per article, frozen **v5 base extract (live)** → **critic-repair (live,
  M14a.8)** → **frozen `card_synth/v1`** (`docs/m15/card-synthesis-prompt.v1.md`)
  compiling repaired Units into cited cards. Grounding held: `accepted_without_quote=0`,
  quote_found 91–100%, 0 cards dropped for missing citations.
- Judging (split-blind, per the P2 fix): unblinded faithfulness/coverage auditor
  (span-support oracle, symmetric over KMEM memories *and* OVP card sentences) +
  **blind stripped-view** readability judge (A/B anonymized, citations removed,
  arm-balanced 6/6). Thresholds frozen before the run; applied as-is.

## Aggregates (11 cases; 67 KMEM memories vs 74 OVP cards; 115 central points)

| axis | KMEM | OVP | frozen rule | verdict |
|---|---|---|---|---|
| **Faithfulness** supported-rate | 0.910 | **0.919** | floor: OVP bad ≤5% (=0.014 ✓); attr OVP≤KMEM (0=0 ✓); A/B/C: both ≥90%, OVP≥KMEM, OVP cited (✓ route B) | **PASS** |
| **Coverage** of central points | 0.670 | **0.878** | OVP ≥ KMEM − 10pts | **PASS** (+21pts) |
| **Readability** (blind pairwise) | **11 wins** | 0 wins | KMEM wins ≤4 pass / ≥7 fail | **FAIL** |
| **H1** | | | faith ∧ read ∧ cov | **FAIL** (readability only) |
| **H2** (Referent needed?) | | | object/nav gap severity | **INCONCLUSIVE** |

Per-case readability was a clean sweep for KMEM — **including 5 cases where OVP
also carried more correct + more complete content** (e.g. s05, s09, s10: OVP covered
11/11, 10/10, 12/12 central points yet still lost the blind read). The card view is
paying a readability tax for structure the reader isn't compensated for in prose.

## The six questions

1. **Does OVP match KMEM readability?** **No** — it lost the blind comparison 11/11.
   This is the sole H1 failure and it is a **card-synthesis prompt/format** problem,
   not a truth-layer one (the underlying Units are fine; the prose compiled from them
   reads denser/less punchy than KMEM's memory cards).
2. **Faithfulness / provenance vs KMEM?** **OVP wins** — supported-rate 0.919 vs
   0.910 (within judge noise → read as "at least as faithful"), bad-rate 0.014 vs 0,
   and OVP is the **only** arm with claim→source provenance (every card sentence →
   cited unit → verbatim quote; verified traceable). KMEM memories carry no
   per-item source span by design.
3. **Coverage loss?** **No — OVP gains ~21 points** (0.878 vs 0.670). The simplified
   pipeline captures more of each article's central points than the memory baseline.
4. **Does lack of Referent/Resolver hurt downstream?** **Mostly no.** Object/nav gap
   is a *real* downstream issue in only **2/11** sources (s04 entity-dense — and it
   enabled a real misattribution, "John G. Fischer"; s06 code-symbol-dense), mild in
   5, irrelevant in 4 (essays/conceptual). And on the OVP arm the referent material
   already exists latent at the **unit layer** (arguments: surface/role/locatable);
   the deficiency is under-surfacing in the card view, not a missing model.
5. **Referent: paused / minimal / main-path?** **Minimal optional helper** — do NOT
   return it to the main path. Only 2/11 sources need object navigation; the median
   source is fine on theme grouping. The one real Referent-shaped defect is
   **identity resolution** (s04 mis-binding), addressable cheaply via the existing
   `BL-113` lightweight embedding-identity backlog — NOT a new trunk subsystem. A
   thin optional "object-index view" rendering the already-populated unit surfaces,
   gated on entity-density, would cover s04/s06 at near-zero trunk cost.
6. **Real blockers?** Two: **(1) readability regression** (HARD blocker for shipping
   the card view as the reader surface; 11-0; a card-synthesis prompt/format fix,
   orthogonal to Referent). **(2) entity-identity resolution** (the only
   Referent-shaped defect; narrow, s04). NOT blockers: faithfulness (pass), coverage
   (pass, OVP ahead), claim→source provenance (working, OVP-only), general navigation
   (theme grouping adequate for the median source).

## Decision

**KEEP the simplified truth-layer + card-view pipeline as the trunk; DEMOTE
Referent/Resolver to an optional helper.** The structured path already wins where a
knowledge compiler must (faithfulness, coverage, provenance); the registered H1 fail
is entirely readability and entirely in the card-synthesis presentation. Next, in
order: (1) **fix card-view readability** — per the frozen-prompt rule this means a
NEW versioned card-synthesis prompt + a fresh registered run, NOT tuning
`card_synth/v1` against these results; (2) add a thin **optional object-index view**
over existing unit surfaces, entity-density-gated; (3) route the **s04 identity
defect** to `BL-113`. Do NOT re-open the eager Referent ontology in the main path.

## Honest caveats

- **Small N (11)** + **single live recording per arm** — arm-internal variance
  unmeasured; the 11-0 readability sweep could partly reflect one card-format config.
- **KMEM content was re-fetched full** for fairness, which likely *flatters* KMEM on
  both readability and coverage — so OVP's coverage win is probably conservative and
  the readability loss possibly overstated.
- Faithfulness margin (0.919 vs 0.910) is within judge noise → "at least not worse,"
  not a confirmed win. H2 rests heavily on **one** source (s04). Thresholds were
  frozen and not stress-tested (correct for reproducibility).
- `s03` excluded (judge structured-output failure). Raw artifacts under `.run/m15/`
  (gitignored; KMEM dumps not committed per protocol).
