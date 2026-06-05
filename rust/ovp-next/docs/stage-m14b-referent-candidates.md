# Stage M14b — local ReferentCandidates from accepted Units

> **Status: PARTIAL PASS / conditional GO to ReferentResolver.** The
> `Source → Unit → Referent` direction is validated: every live referent is
> grounded in an accepted unit (`referents_ungrounded = 0` on all 3 cases,
> deterministic), v2's "everything is a concept" pathology is NOT recreated, and
> the output is a materially better foundation than v2 `concepts[]`. It is not a
> clean pass — three tracked follow-ups (below), none of which are canonicalization
> blockers. Canonical concepts remain review-gated; M14b mints nothing canonical.

## What this is

An experimental `referents` module (parallel, deletable; NOT a `DomainBody`, no
manifest / GraphAssembler / RunCycle, no vault/canonical write, no evergreen, no
slugs, no alias-merge, no RAG/KnowledgeMEM). It consumes the M14a.8
`units.accepted.json` and classifies the OBJECTS the units talk about into LOCAL
`ReferentCandidate{ surface_names, kind ∈ entity|concept|ambiguous|local_phrase|
noise, support_unit_ids, evidence_refs, boundary?, rationale, confidence }`.

**Protocol (mirrors the M14a hand-harness discipline — the LLM proposes, the
deterministic validator disposes):**
- HYBRID harvest: a deterministic seed list from `Unit.arguments` (dropping
  non-locatable `role=topic` directive handles) + the LLM may ADD object surfaces
  found literally in unit `text`/`quote`. Forbidden sources: title, metadata,
  keywords, v2 `concepts[]`, MOC, KnowledgeMEM, world knowledge.
- GROUNDING GATE (the hard invariant, M14b's `accepted_without_quote=0`): a live
  referent's surface must render-normalize-substring-match a supporting accepted
  unit's `text+quote` — reusing the EXACT M14a `render_norm` (hoisted to a shared
  module; a test asserts byte-identical normalization). No fuzzy match. Ungrounded
  → rejected. A surface < 3 chars (ASCII) / < 2 (CJK) cannot ground.
- STRUCTURAL rules: support ids resolved against the accepted set (tolerant of the
  model truncating `u-NNN-hash` → `u-NNN` by unique prefix); evidence_refs DERIVED
  from real support units (never trusted from the model); concept REQUIRES a real
  boundary; **BOUNDARY-PROVENANCE**: a concept's boundary content tokens must trace
  to its support units (≥50%), else downgrade to ambiguous; directive-topic handle
  → forced local_phrase; dedup by canonical surface / shared support; confidence
  computed deterministically in Rust.

## Results (3 cases, live-recorded then deterministic on replay)

| case | live | entity | concept | ambiguous | local | noise | concept_rate |
|---|---|---|---|---|---|---|---|
| rag_wrong | 12 | 9 | 2 | 0 | 0 | 1 | **17%** |
| eval_ai_agents | 12 | 1 | 5 | 2 | 3 | 1 | **42%** |
| agent_memory_zh | 8 | 5 | 2 | 0 | 0 | 1 | **25%** |

`referents_ungrounded = 0` and `accepted_without_quote`-analogue holds on all
three; replay byte-identical. Per case:
- **rag**: entities IdeaBlock, Question-Answer Packet, chunk, naive chunks, RAG
  pipeline, distillation layer …; concepts **semantic deduplication**, **typed
  metadata** (both genuine reusable abstractions with grounded boundaries); the
  chunk-as-unit *claim* → noise; 3 rejected ungrounded.
- **eval**: concepts floor raising, benchmark maxxing, golden cases (genuine) +
  synthetic evals, end-to-end evaluation (residual — see below); `Agents`
  (article thesis, boundary lifted from a *rejected* unit) and `Issues`
  (boundary cited a non-support sibling) → **ambiguous via boundary-provenance**;
  directive handles → local_phrase; 7 rejected ungrounded.
- **zh**: the Google 3-type memory taxonomy (情景/语义/程序性记忆) minted as ONE
  taxonomy concept and the extract/update/retrieve operations as ONE concept —
  both correctly GROUPED (fixing v2's over-split); entities 上下文压缩, 长期记忆系统,
  memory.md …; 7 rejected ungrounded (the recall gap, below).

## The deterministic-gate ceiling (an honest, reusable finding)

The qualitative review flagged eval's over-mint (originally 58%, with `Agents`,
`synthetic evals`, `end-to-end evaluation` as claim/thesis-as-concept). I added
two deterministic backstops and measured:
- **BOUNDARY-PROVENANCE kept** (concept's boundary must come from its support):
  correctly demoted `Agents` and `Issues` to ambiguous with **no false positives**
  — this closes a real blind spot the review found (`referents_ungrounded=0`
  grounds the *surface* but not the *boundary*).
- **single-support "predicate-restatement" → ambiguous: REMOVED.** It
  false-downgraded rag's `semantic deduplication` (a *genuine* single-support
  concept whose boundary legitimately comes from its one defining unit) while
  failing to catch eval's surviving claim-concepts. A genuine single-support
  concept is **deterministically indistinguishable** from a claim-as-concept (both
  have a boundary highly contained in their support). This is the SAME wall as
  M14a's faithfulness gate: concept-vs-claim is irreducibly semantic. So that call
  stays a prompt/review judgment, not a gate — and eval's 2 residual
  claim-concepts are a known limit, not silently forced.

Net: provenance lowered eval 58% → **42%** and exercised the ambiguous lane,
without losing a genuine concept anywhere.

## The seven questions

1. **Referents per case?** rag 12, eval 12, zh 8 live (+ 3/7/7 rejected ungrounded).
2. **Kind breakdown?** rag 9e/2c; eval 1e/5c/2amb/3lp; zh 5e/2c. concept_rate
   17/42/25%.
3. **Which supported by which units?** All — `referents_ungrounded=0`, every
   surface render-normalized-traced to a support unit, evidence_refs derived from
   real units (see `referents.by-unit.md` per case).
4. **Avoided claim/action-as-concept?** rag ✅, zh ✅ (theses/recommendations →
   noise/entity/not-minted; chunk-as-unit claim → noise). eval ⚠ — 2 of 5 concepts
   (synthetic evals, end-to-end evaluation) are single-support claims that survived
   (the semantic ceiling above); the 2 clearest (Agents, Issues) were demoted.
   Directive gerund handles ("evaluating agents", "massive eval sets") → local_phrase.
5. **Avoided synonym over-mint?** ✅ — `synonym_over_mint=[]` on rag and eval; the
   model GROUPED co-referents (IdeaBlock/IdeaBlocks; floor raising ×4; Issues/Signals
   taxonomy; the zh memory taxonomy) into single candidates. One zh scope-conflation
   (语义记忆 spanning Google/OpenClaw/EverOS scopes) should have been ambiguous.
6. **Better than v2 `concepts[]`?** **Yes, materially, on all three.** v2 minted
   10/10/15 mostly-claims/theses with classic taxonomy over-split (zh: 4 slugs for
   one 3-type taxonomy; eval: Stumbles/Issues/Signals as separate concepts). M14b
   avoided/demoted/regrouped ~6/10 (rag), ~6/10 (eval), ~9/15 (zh) and collapsed the
   taxonomy over-splits into single grouped concepts. It does NOT recreate
   everything-is-concept (concept_rate 17–42% vs v2's ~100%).
7. **Ready for ReferentResolver / promotion gate?** **Partial — conditional GO.**
   The substrate is trustworthy (grounded, conservative, better than v2) enough to
   build a resolver on, with the three follow-ups tracked and canonical promotion
   staying review-gated.

## Failure classification + follow-ups (none block a review-gated resolver)

- **eval = class B (classifier prompt), residual + semantically bounded.** 2 of 5
  concepts are claim-as-concept that the deterministic gate cannot separate from
  genuine single-support concepts. Lever: ONE bounded classifier-prompt iteration
  (or accept as a known eval-case weakness); NOT another deterministic rule (proven
  to over-correct). Tracked, not gate-blocking for a review-gated resolver.
- **zh = class E (gate granularity / upstream extraction).** Genuine EverOS/OpenClaw
  mechanisms (`语义巩固`/Semantic Consolidation, `EverOS检索方式`, `EverOS抽取`,
  `OpenClaw检索`, `Skill自进化`) were rejected ungrounded because the model emitted
  glued surfaces ("EverOS语义巩固") that don't substring-match, or the supporting
  unit's subject is a paraphrase. This is precision-over-recall, correctly logged in
  `referents.rejected.json` (not silently lost) — but a real recall gap. Lever: a
  unit-level locatable fallback in M14a.8, or the classifier splitting glued
  surfaces. Must NOT be chased with classifier prompt tuning. **Do not trust this
  substrate for lossless mechanism capture until fixed.**
- **rag = pass, with an entity-granularity follow-up.** Concept lane clean; but the
  model heterogeneously LUMPED distinct entities (Blockify + naive chunks + cosine
  distance into one; SharePoint/Confluence/Git + "enterprise corpora"), burying
  Blockify (the source's central named product). The inverse of synonym over-mint.
  Lever: a grouping-discipline prompt note; not gate-blocking.

Cross-cutting: `ambiguous_rate` was 0 until provenance (now eval 17%); the model
biases toward resolving uncertainty UP into concept/entity — the provenance gate +
the prompt's GATE-4 are the counterweights, and the residual bias is a review-time
watch item.

## Artifacts
- Committed: `crates/ovp-domain/src/referents/{mod,parser,validator,prompt,harness,
  review_pack}.rs`, `prompts/referent_classify.md`, `units/normalize.rs` (hoisted
  shared render-norm), `extract-referents` CLI, this doc. Validator accept rules
  for M14a unchanged; M14b adds only stricter, explainable structural rules.
- NOT committed (`.run/m14b/`): per-case packs (REVIEW.md, referents.all.json,
  referents.by-unit.md, referents.by-kind.md, rejected-or-noise.md,
  unresolved-ambiguous.md, report.json) + the `referent_classify/v1` cassettes.
