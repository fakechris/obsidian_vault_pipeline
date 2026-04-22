---
tags:
  - knowledge-compilation
  - obsidian-vault
  - semantic-relations
  - evidence-tracking
  - policy-promotion
tools:
  - Obsidian
  - SQLite
projects:
  - OVP
  - research-tech
  - OpenClaw
---
# Vision & Roadmap: The Auditable Knowledge Compiler

**Date:** 2026-04-22
**Status:** Proposed
**Supersedes (in narrative scope):** the six-layer pipeline framing in `README.md`. Engineering layers remain; the **product narrative** collapses to Capture → Compile → Reuse.

---

## 1. Vision

> **OVP is an auditable knowledge compiler for Obsidian: it turns external material into evidence-backed, review-gated, reusable long-term knowledge — without polluting the human vault.**

This is narrower than "agent memory layer" and more defensible than "second brain." It commits to four things competitors do not jointly hold:

1. **Vault markdown + concept registry are source of truth.** `knowledge.db` is forever derived. (Unlike GBrain's self-wiring graph.)
2. **AI never becomes truth directly.** It generates candidates, claims, relations, contradictions. Promotion is policy-gated. (Unlike OpenKL's auto-substrate.)
3. **Evidence is first-class, not a citation afterthought.** Every promoted claim must be re-locatable and re-verifiable.
4. **Pack-owned semantics, core-owned discipline.** Domain meaning lives in packs; canonical, audit, evidence, review live in core. (Unlike Foundry's prompt-maintained meta files.)

## 2. Product Narrative: Capture → Compile → Reuse

Internal engineering keeps the six-layer model (Ingest, Interpret, Absorb, Refine, Canonical, Derived). External narrative is three verbs:

- **Capture** — turn articles, papers, meetings, web pages into traceable source + deep dive.
- **Compile** — generate candidates, concepts, relations from multiple sources, gated by evidence and policy.
- **Reuse** — pull the compiled knowledge back into queries, briefings, writing prompts, workbench surfaces — and let that consumption produce new candidates and writing directions.

`knowledge.db`, graph, truth API, UI, lint are infrastructure for these three verbs, not the product.

## 3. The LLM ↔ OVP Division of Labor

The single sentence to remember:

> **The model is responsible for "thinking of"; OVP is responsible for "remembering why it's trustworthy."**

| | LLM weights | OVP absorbed knowledge |
|---|---|---|
| Form | Statistical compression, not addressable | Atomic notes + evidence chain |
| Ownership | Public average | What **you** read, thought, filtered |
| Time | Frozen at training cutoff | Continuously updated |
| Auditable | No | Yes |
| Correctable | No (only prompt patches) | Yes (edit source + recompile) |
| Hallucination risk | High | Low (quote required) |

**The complementary relationship is not "LLM knowledge + your knowledge = more knowledge."** It is:

- LLM provides reasoning substrate, language, analogy, compression
- OVP provides factual substrate that is yours, with provenance
- LLM proposes; OVP decides whether the proposal can join long-term truth

Concretely the LLM helps OVP by:
- Suggesting that two concepts may be related (→ `semantic_relation_candidate`)
- Suggesting that a new claim may contradict an existing one (→ `contradiction` candidate)
- Suggesting that a term resembles an existing canonical object (→ alias merge candidate)
- Suggesting a new source belongs to a pack schema (→ extraction routing)

In none of these cases does the LLM's output become truth without policy-gated review.

## 4. North Star + Guardrails

**North Star — Trusted Reuse Events per Week:**

> A `trusted_reuse_event` is logged when a canonical concept, accepted relation, resolved contradiction, or cited query report is consumed by a downstream surface (query, briefing, writing prompt, compiled view) **and** the consumed object has source evidence **and** has no broken backlink or unresolved provenance.

This is the only metric that ties knowledge accumulation to actual thinking and writing value. It cannot be gamed by ingest volume, concept count, or graph edges.

**Guardrails (must trend in the right direction or alert):**

| Metric | Definition | Why |
|---|---|---|
| Promotion precision | Fraction of reviewed candidates accepted | Catches noisy extractors |
| Evidence coverage | Fraction of canonical claims with quote + source_slug | Catches "quiet truth" drift |
| Noise rate | Fraction of objects flagged orphan / stale / unsupported by `ovp-lint` / `ovp-doctor` | Catches absorb-without-prune |
| Time to reuse | Days between source ingest and first reuse event | Catches dead-archive growth |
| Unreviewed canonical mutation | Agent writes to **accepted-state** files (project plan, roadmap, decisions, canonical concept pages) without explicit promotion command + diff + audit event | **Must be 0** |

## 5. Operating Principle: Policy-Gated Auto Review

The user's constraint is *"as little human intervention as possible while still absorbing knowledge well, and supporting domain packs."* The reconciliation:

> **Reviewable ≠ heavy human review.**
>
> Review gate := **policy-gated auto promotion + audit trail + human escalation only for risky cases.**

**Lane assignment** (declared by pack policy, applied by core promotion engine):

- **Auto-promote lane** — independent multi-source + complete evidence + no contradiction with existing canonical. Promoted, audited, sampled later.
- **Workbench lane** — single-source-but-high-impact, low-confidence, identity-merge ambiguity, contradiction with existing canonical, schema gap. Goes to review queue.
- **Reject lane** — fails minimum evidence floor or policy block. Logged, not promoted.

Goal: 30 minutes/week of human review, not 30 minutes/source.

## 6. Pack Contract Extension (the minimal-intervention enabler)

Packs are not vocabularies; packs are **contracts of autonomy**. To support both "minimal human intervention" and "domain pack pluggability," every pack must declare:

```yaml
# pack manifest additions
promotion_policy:
  auto_promote:
    require_independent_sources: 2     # not just source_count
    require_evidence_kinds: [quote, paraphrase]
    require_no_open_contradiction: true
  escalate_to_workbench:
    single_source_high_impact: true
    identity_merge_ambiguity: true
    contradicts_existing_canonical: true

evidence_requirements:
  claim:
    must_have: [source_slug, quote_text, locator]
    should_have: [content_hash, retrieval_context]
  relation:
    must_have: [source_object_id, target_object_id, relation_type, evidence_source_slug, quote_text]

reuse_signals:
  counts_as_reuse:
    - query_cited_canonical_object
    - briefing_consumed_object
    - writing_prompt_grounded_in_object
```

`research-tech` is the reference implementation. New packs (medical, media, investment) reuse Capture → Compile → Reuse and only swap `object kinds`, `relation vocabulary`, and `promotion_policy`.

## 7. What We Are Not Building

Hard anti-scope. If a future PR drifts toward any of these, it should be rejected on sight:

- ❌ Kùzu / new graph backend / new memory backend (SQLite `knowledge.db` is enough)
- ❌ A separate citation system parallel to `claim_evidence`
- ❌ Auto-promotion without policy + audit trail
- ❌ **Agents mutating accepted-state files** (project plan, roadmap, decisions, canonical concept pages, README) without an explicit promotion command, diff, and audit event
- ❌ More layer names, phase names, or command names to express complexity
- ❌ Treating "more automation" as progress when it bypasses reviewable reuse
- ❌ Letting `knowledge.db` or semantic retrieval upgrade into canonical identity
- ❌ A general-purpose knowledge browser UI (UI must be Review Workbench + Reuse Surface)

**Explicitly in-scope** (correcting a frequent misread of the above):

- ✅ Agents writing freely into declared **agent-owned zones** — project inboxes, briefings, drafts, append-only suggestion files — as long as outputs are marked as generated/candidate and carry provenance
- ✅ OVP being the primary author of project briefings, research plans, decision drafts, next-action suggestions — these are the high-value reuse surfaces

The boundary is **state**, not authorship. See §7a.

---

## 7a. Workspace Zoning

OVP is agent-driven by design. The risk is not "agent writes too much" — it is "agent output silently becomes accepted truth." Workspace zoning makes the candidate↔accepted boundary explicit at the **filesystem level**, the same way §5 makes it explicit at the candidate↔canonical-concept level.

### Zone taxonomy

Every project (and analogously every pack-managed area) declares two file zones:

**Agent-owned zone** — agents may write freely; outputs must carry provenance and a `state: candidate|draft|suggested` frontmatter field:

```
30-Projects/<project>/
├── _OVP-Inbox/              # agent ingest staging
├── Briefings/YYYY-MM-DD.md  # generated briefings
├── Drafts/*.md              # writing drafts
└── OVP-Suggestions.md       # append-only suggestion log
```

**Accepted zone** — represents current project commitment; no silent agent mutation:

```
30-Projects/<project>/
├── README.md         # what this project is
├── Plan.md           # current plan
├── Roadmap.md        # committed roadmap
└── Decisions.md      # accepted decisions log
```

The same shape applies elsewhere:
- `00-Polaris/` — accepted (Top of Mind is a personal commitment)
- `10-Knowledge/Evergreen/` — accepted (canonical concepts; mutation goes through registry promotion)
- `20-Areas/.../Topics/YYYY-MM/*_深度解读.md` — agent-owned (deep dives are generated artifacts)
- `50-Inbox/01-Raw/` — agent-owned (ingest)

### Promotion path: draft → accepted

To move content from agent-owned to accepted requires the same machinery as Phase 34's policy promotion, applied to file state instead of concept state:

1. Explicit command: `ovp-promote workspace --from <draft> --to <accepted>`
2. Diff presented to operator (or auto-approved per pack policy if change is additive + provenance complete)
3. Audit event written to `60-Logs/pipeline.jsonl` with `from_path`, `to_path`, `provenance_chain`, `approver`
4. Source draft retained in agent-owned zone (history) or archived per policy

### Provenance fields (required on agent-owned writes)

```yaml
---
state: candidate         # candidate | draft | suggested | derived
generated_by: ovp-query  # which command/agent produced this
generated_at: 2026-04-22T14:32:00Z
sources:                 # what canonical objects this leaned on
  - "[[AI Agent]]"
  - "[[RAG]]"
reuse_event_ids:         # links to Phase 32 reuse events
  - "rev-20260422-..."
promotion_target: 30-Projects/foo/Plan.md  # optional: where it would land
---
```

### Pack contract addition (extends §6)

```yaml
workspace_zones:
  agent_owned:
    - "30-Projects/*/_OVP-Inbox/**"
    - "30-Projects/*/Briefings/**"
    - "30-Projects/*/Drafts/**"
    - "30-Projects/*/OVP-Suggestions.md"  # append-only
    - "20-Areas/**/Topics/**/*_深度解读.md"
    - "50-Inbox/**"
  accepted:
    - "30-Projects/*/README.md"
    - "30-Projects/*/Plan.md"
    - "30-Projects/*/Roadmap.md"
    - "30-Projects/*/Decisions.md"
    - "00-Polaris/**"
    - "10-Knowledge/Evergreen/**"
  append_only:
    - "30-Projects/*/OVP-Suggestions.md"
    - "60-Logs/**"
  promotion_rules:
    draft_to_accepted:
      require_human_approval: true
      require_provenance_complete: true
      require_diff_review: true
```

Core enforces the zone boundary; packs declare the file globs and promotion rules.

## 8. Roadmap: Phase 32 → 36

Five phases form one closed loop. Order matters: each phase strictly enables the next.

```
Phase 32  Trusted Reuse Loop      ─┐
Phase 33  Evidence v2              ├─ Closed end-to-end
Phase 34  Policy Promotion         │  trusted-reuse loop
Phase 35  Reviewed Semantic Extr.  │
Phase 36  Query Feedback          ─┘
```

---

### Phase 32 — Trusted Reuse Loop

**Goal.** Make the North Star measurable. Every consumption of a canonical object by a downstream surface emits a `trusted_reuse_event`. Without this, every later phase's value is invisible.

**Why first.** "Reviewable reuse" is the product thesis. If we can't see reuse, we can't tell whether Phase 33–36 are working.

**Deliverables.**
- New `reuse_events` table in `knowledge.db` (event stream, derived, rebuildable):
  - `event_id`, `ts`, `pack`, `object_id`, `surface` (`query|briefing|writing_prompt|compiled_view|export`), `consumer_ref`, `evidence_present` (bool), `provenance_clean` (bool), `trusted` (bool)
- Instrument the consumers:
  - `ovp-query` (`query_tool.py:524` evidence payload site) emits one event per cited canonical object
  - `ovp-export` (compiled views: `object-page`, `topic-overview`, `event-dossier`) emits one event per object materialized
  - `ovp-truth` reads emit events when called with object slugs
  - `prompt assembler` (new thin module) emits events when canonical objects are dropped into a prompt
- New CLI: `ovp-reuse weekly --json` — reuse events grouped by week / pack / surface
- New UI panel: **"Reused this week"** — top objects, time-to-first-reuse, never-reused-after-30-days list

**Code touchpoints.**
- `src/ovp_pipeline/truth_store.py` — extend schema (`reuse_events` table)
- `src/ovp_pipeline/query_tool.py:524` — emit on each evidence payload
- `src/ovp_pipeline/commands/export_artifact.py` — emit on materialization
- `src/ovp_pipeline/commands/truth_api.py` — emit on object reads
- `src/ovp_pipeline/commands/ui_server.py` — new dashboard panel
- New: `src/ovp_pipeline/commands/reuse_report.py`

**Success criteria.**
- After one week of normal use, `ovp-reuse weekly` returns ≥1 event for ≥80% of objects promoted in the same week's normal workflow
- Dashboard shows weekly trend line, no manual instrumentation needed
- All four guardrail metrics computable from existing tables + `reuse_events`

**Anti-scope.**
- No new ranking, no new retrieval algorithm, no graph re-walk
- No "smart" reuse detection — explicit consumer instrumentation only

---

### Phase 33 — Evidence v2

**Goal.** Make `claim_evidence` strong enough that any promoted claim can be re-located and re-verified six months later.

**Why now.** Phase 32 measures reuse trust; Phase 33 makes "trust" defensible. Without this, `evidence_present` and `provenance_clean` flags in Phase 32 are weak.

**Current state.** `truth_store.py:32` — `claim_evidence(pack, claim_id, source_slug, evidence_kind, quote_text)`. Five fields, no locator, no hash, no status.

**Deliverables.**
- Extend `claim_evidence` schema (additive — old rows remain valid):
  - `locator TEXT` — `section#heading`, `paragraph_index`, page range, line range
  - `content_hash TEXT` — SHA-256 of source file at evidence-creation time
  - `retrieval_context TEXT` — short surrounding snippet for disambiguation
  - `status TEXT` — `verified | stale | broken | unverified` (default `unverified`)
  - `verified_at TEXT`
- Backfill migration: existing rows get `status='unverified'` and best-effort `locator` from quote search
- New verifier: `ovp-evidence verify --recent 30 --json` — re-locates each evidence row in current source, marks `verified` / `stale` / `broken`
- `ovp-doctor` adds an Evidence Health section reporting per-pack verified / stale / broken counts
- `ovp-lint` blocks promotion of claims whose required evidence is missing locator or hash (per pack policy)

**Code touchpoints.**
- `src/ovp_pipeline/truth_store.py` — schema additions + migration
- `src/ovp_pipeline/evidence.py` — verifier logic, hash computation, locator search
- `src/ovp_pipeline/commands/doctor.py` — Evidence Health section
- `src/ovp_pipeline/lint_checker.py` — evidence-completeness rule

**Success criteria.**
- 100% of new evidence rows after Phase 33 land has `locator` + `content_hash`
- `ovp-evidence verify` correctly classifies stale/broken on a synthetic source-change test
- After backfill, ≥70% of legacy rows reach `verified`; remainder explicitly `unverified` (not silently passing)

**Anti-scope.**
- No new evidence storage backend
- No external citation database or DOI integration
- No "smart" evidence ranking — verification is binary

---

### Phase 34 — Policy Promotion (concept + workspace)

**Goal.** Replace `source_count >= 2 or evidence_count >= 3` with a real policy engine driven by pack-declared rules. Make §5 (policy-gated auto review) executable for **two kinds of promotion**:

1. **Concept promotion** — candidate concept → canonical concept (existing surface, currently broken)
2. **Workspace promotion** — agent-owned draft → accepted-state file (new surface, enables §7a)

Both use the same lane logic (auto / escalate / reject), the same audit trail, the same pack-declared policy. Different inputs, identical machinery.

**Current state.** `promote_candidates.py:397` — single OR rule, applied identically across packs, no concept of independent sources, no escalation lane.

**Deliverables.**
- New module: `src/ovp_pipeline/promotion_policy.py`
  - Loads pack manifest's `promotion_policy` block
  - Computes **independent source count** (sources with different `source_slug` *and* different originating domain/feed)
  - Evaluates each candidate against three lanes: `auto_promote | escalate | reject`
  - Returns structured decision with reason codes
  - Two evaluation entry points: `evaluate_concept(candidate)` and `evaluate_workspace(draft_path, target_path)`
- Refactor `promote_candidates.py:review_candidates`:
  - Becomes a thin caller of `promotion_policy.evaluate_concept()`
  - Auto-promote lane writes to canonical + emits audit event
  - Escalate lane writes to review workbench queue
  - Reject lane logs reason, leaves as candidate
- New module: `src/ovp_pipeline/workspace_promotion.py`
  - Enforces zone boundaries declared in pack `workspace_zones` (§7a)
  - Validates required provenance frontmatter on agent-owned writes
  - Refuses any write to accepted-zone paths that does not come through the promotion command
  - Exposes `ovp-promote workspace --from <draft> --to <accepted> [--diff] [--auto]`
- `research-tech` pack manifest gains both `promotion_policy` (§6) and `workspace_zones` (§7a) blocks as reference implementation
- `default-knowledge` pack gets a permissive policy preserving current behavior (compatibility)
- New CLI: `ovp-promote run --pack <name> --json` — runs concept policy
- New CLI: `ovp-promote workspace ...` — workspace promotion as above
- `ovp-doctor` reports: concept lane rates, workspace lane rates, **unreviewed canonical mutation count** (must be 0)

**Code touchpoints.**
- `src/ovp_pipeline/promote_candidates.py:397` — replace OR rule
- `src/ovp_pipeline/packs/base.py` — add `promotion_policy` and `workspace_zones` to pack contract
- `src/ovp_pipeline/packs/research_tech/` — declare reference policy + zones
- `src/ovp_pipeline/packs/default_knowledge/` — declare permissive policy + minimal zones
- New: `src/ovp_pipeline/promotion_policy.py`
- New: `src/ovp_pipeline/workspace_promotion.py`
- `src/ovp_pipeline/lint_checker.py` — add zone-boundary lint: any write to an accepted-zone path without a matching audit event is a violation

**Success criteria.**
- Same vault, same candidates: `default-knowledge` permissive policy reproduces current behavior bit-for-bit
- `research-tech` strict policy correctly assigns lanes on a fixture set with known answers
- Audit trail records lane + reason for every decision (concept and workspace)
- Auto-promote rate × promotion precision (from Phase 32 guardrails) ≥ baseline
- Zone-boundary lint catches a synthetic violation (agent write to accepted-zone path bypassing promotion command)
- After Phase 34 lands, `ovp-doctor`'s **unreviewed canonical mutation** count is 0 across the dogfooding vault

**Anti-scope.**
- No machine-learned promotion scoring
- No global cross-pack policy
- No promotion of relations (that's Phase 35)
- No banning agent writes — only routing them through state + provenance

---

### Phase 35 — Reviewed Semantic Extractor

**Goal.** Add the actual semantic relation extractor that Phase 31 prepared the contract for. Output is **only** `semantic_relation_candidate`. Promotion to `relations` / `graph_edges` flows through Phase 34's policy engine.

**Why this order.** Without Phase 34, the extractor would have no safe promotion path. Without Phase 33, accepted relations would lack verifiable evidence. Phase 35 must wait.

**Deliverables.**
- `src/ovp_pipeline/extraction/semantic_relations.py` — extractor that, given a deep-dive document, proposes relations across canonical objects:
  - LLM call constrained to pack-declared relation vocabulary
  - Each proposal must carry `source_object_id`, `target_object_id`, `relation_type`, `quote_text`, `locator`, `content_hash` (Phase 33 evidence)
  - Outputs `semantic_relation_candidate` artifacts (Phase 31 spec)
- Wired into `ovp --full` after `absorb`, before `moc`, behind `--with-relations` flag (opt-in initially)
- `ovp-promote run` (Phase 34) handles relation candidates with the same lane logic
- Promoted relations write to `relations` and `graph_edges` tables; rejected stay in candidates folder
- `ovp-doctor` reports relation extraction rate, promotion rate, contradiction rate

**Code touchpoints.**
- New: `src/ovp_pipeline/extraction/semantic_relations.py`
- `src/ovp_pipeline/unified_pipeline_enhanced.py` — register `relations` step (opt-in)
- `src/ovp_pipeline/promotion_policy.py` — relation-shaped candidates
- `src/ovp_pipeline/truth_store.py` — relation-with-evidence write path
- `src/ovp_pipeline/packs/research_tech/` — extractor prompt + vocabulary binding

**Success criteria.**
- On fixture deep dives with known relations, extractor proposes them with valid evidence
- Zero relations land in `relations` / `graph_edges` without going through Phase 34 policy
- Phase 32 reuse events for promoted relations are non-zero (they're being consumed by queries / views)

**Anti-scope.**
- No relation inference across packs
- No "graph-aware" retrieval yet (that comes after this loop closes, as a separate effort)
- No promotion bypass for "obvious" relations

---

### Phase 36 — Query Feedback Loop

**Goal.** Close the loop. `ovp-query` stops being a one-shot answer; it becomes a producer of new candidates, claims, open questions, writing prompts. **This is the compounding mechanism.**

**Why last.** It depends on Phase 32 (reuse events), 33 (verifiable evidence), 34 (policy promotion), 35 (relation candidates) all working.

**Deliverables.**
- `ovp-query` outputs a structured artifact, not just a markdown answer:
  - `cited_reusable_claims[]` — canonical claim IDs the answer leaned on (emit reuse events — Phase 32)
  - `candidate_concepts[]` — terms used in the answer that don't resolve to canonical objects (route to `concept_registry` candidate intake)
  - `open_questions[]` — questions the LLM flagged as unresolved (route to `60-Logs/open-questions.jsonl`)
  - `writing_prompts[]` — angles for follow-up writing (route to `00-Polaris/Writing-Prompts.md`, **append-only, never overwrite human content**)
  - `proposed_relations[]` — relation candidates implied by the answer (route to Phase 35 candidate stream)
- New CLI: `ovp-query --feedback` produces all four streams
- `ovp-query --save-to` continues to work; feedback streams are an additional output, not a replacement
- UI gains an **"Open Questions"** + **"Writing Prompts"** panel sourced from this stream
- `ovp-doctor` reports query→candidate yield, query→reuse ratio

**Code touchpoints.**
- `src/ovp_pipeline/query_tool.py:524` — already builds evidence payload; extend to emit feedback artifacts
- `src/ovp_pipeline/query_to_wiki.py` — write-back path for the four streams
- `src/ovp_pipeline/commands/ui_server.py` — Open Questions panel
- New: `src/ovp_pipeline/feedback_router.py` — routes the four streams to their destinations

**Success criteria.**
- After two weeks of querying, candidate concepts produced by `ovp-query` reach Phase 34 promotion at ≥10% rate
- Open questions backlog visible in UI, draining over time
- Writing prompts file grows append-only, no overwrites of human edits
- Reuse events from query consumption visible in Phase 32 dashboard

**Anti-scope.**
- Query does not write to canonical objects directly; only to candidate streams
- No agent loop ("query → answer → query") — single-turn output of structured feedback
- No automatic publishing of writing prompts as drafts

---

## 9. Concrete User-Facing Scenarios After Phase 36

These are the moments where a user feels the difference between OVP and either ChatGPT or a normal Obsidian vault:

1. **"What do I actually know about agent memory?"** → returns canonical concepts + accepted relations + open contradictions + 5 quote-backed claims. Not chunks. Not a chat answer.
2. **Writing cold start.** → ask for a topic; receive concept skeleton + your accepted positions + unresolved questions + reusable quotes. Write on a scaffold of your own thinking, not on a blank page filled by a generic model.
3. **Decision audit.** → "why did I choose X six months ago?" returns the evidence chain and the rejected alternatives, with provenance.
4. **Contradiction surfacing.** → new source contradicts an accepted claim → workbench flags it before it silently rewrites your view.
5. **Weekly briefing.** → "this week 12 reuse events, 3 new candidates promoted, 1 contradiction resolved, 2 writing prompts opened" — value visible, not just volume.
6. **New pack onboarding.** → drop in a `medical` or `investing` pack; same Capture → Compile → Reuse loop; only `object_kinds`, `relation_vocabulary`, `promotion_policy` change.
7. **Personal corpus as MCP/API.** → eventual: any agent can use your vault as auditable long-term memory — but truth still lives in markdown.

## 10. Sequencing Summary

| Phase | Unblocks | Closes loop on |
|---|---|---|
| 32 Trusted Reuse Loop | Visibility | Measurement |
| 33 Evidence v2 | Verifiability | Trust |
| 34 Policy Promotion | Autonomy with safety (concept **and** workspace) | Minimal-intervention promotion + state-not-authorship boundary |
| 35 Reviewed Semantic Extractor | Pack-owned relations | Phase 31 contract executed |
| 36 Query Feedback | Compounding | Capture → Compile → **Reuse → Capture** |

After Phase 36, the system has a closed compounding loop. Subsequent work (graph-aware retrieval consuming existing truth, MCP/API surface, additional packs) is incremental on top of the loop, not a new layer.

---

## 11. Tagline

> **"The reviewable knowledge compiler for people who write and think in Obsidian."**
>
> Not second brain. Not RAG. Not memory DB.
> AI's speed inside an auditable pipeline that does not pollute the human vault and compounds over time.
