# Task Plan: External Project Discovery for OVP

## Goal
Build a running comparative map of external memory, context, runtime, and governance systems; classify what each project is actually doing; and extract only the durable ideas that matter for Obsidian Vault Pipeline.

## Current Phase
Phase 4

## Phases

### Phase 1: Local Project Context
- [x] Understand current project goals and structure
- [x] Identify where note-taking, article generation, or knowledge workflows already exist
- [x] Capture findings in findings.md
- **Status:** complete

### Phase 2: arscontexta Discovery
- [x] Read repository docs and structure
- [x] Inspect core implementation files and architecture
- [x] Capture findings in findings.md
- **Status:** complete

### Phase 3: Comparative Analysis
- [x] Compare arscontexta assumptions against OpenClaw's current direction
- [x] Separate strong ideas from superficial similarities
- [x] Record decisions and rationale
- **Status:** complete

### Phase 4: Synthesis for Articles
- [x] Extract 3-5 research/article directions
- [x] Explain significance and tradeoffs
- [x] Prepare concise delivery
- **Status:** complete

### Phase 5: Delivery
- [x] Review findings for accuracy
- [x] Deliver conclusions with concrete recommendations
- [x] Include source references
- **Status:** complete

## Key Questions
1. What problem is arscontexta actually solving beyond "better note taking"?
2. Which parts of its system are durable ideas versus implementation-specific choices?
3. What is genuinely useful for OpenClaw now, and what is likely premature?
4. Which external patterns should OVP explicitly reject rather than absorb?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use file-based planning for this task | The work spans local context, external repo inspection, and synthesis across multiple tool calls |
| Evaluate external repos by implemented runtime shape, not README positioning alone | This prevents marketing language from being mistaken for architectural reality |
| Keep the survey centered on OVP rather than drifting into generic “agent platform” analysis | The user explicitly corrected this, and it materially changes what counts as relevant |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes
- Re-read this plan before major synthesis decisions
- Distinguish repository claims from implemented reality
- Prefer primary sources: repo docs, code, and official project pages
- Keep updating `docs/plans/2026-04-17-external-project-discovery-log.md` as the canonical survey artifact
- Keep `docs/plans/2026-04-17-ovp-architecture-mapping.md` and `docs/plans/2026-04-17-ovp-interface-contract-mapping.md` as the current architecture interpretation layer for future design work
- Use `docs/plans/2026-04-17-ovp-contract-evolution-design.md` as the current proposal for how new pack-side contracts should be introduced without rewriting the runtime
- Treat `docs/plans/2026-04-17-phase18-knowledge-compiler-contract-consolidation-plan.md` as the current roadmap placement for this work: after `Phase 17`, before deeper temporal-truth/memory/evaluation expansions
- Current implementation slices landed: `ArtifactSpec`, `AssemblyRecipeSpec`, `GovernanceSpec`; contract-consumption is now live in `ovp-export`, `truth_api`, the shared UI shell, and `ovp-doctor`, including recipe-provider vs source-provider resolution for access artifacts
- Current contract-consumption depth:
  - access surfaces now explain `recipe provider -> source provider`
  - runtime/operator surfaces now explain `governance provider -> review/signal/resolver rules`
  - `truth_api` resolver metadata and shared UI governance cards now read from the same governance registry
  - item-level runtime outputs now explain `resolver rule -> governance provider`
  - `ovp-doctor` shell payload now explains which governance contract the current pack scope actually resolves to
- `Phase 18` closeout work now also includes:
  - registry-backed effective artifact enumeration via `artifact_registry`
  - registry-backed effective assembly/governance enumeration in `ovp-doctor`
  - explicit `/api/briefing`, `/api/signals`, and `/api/actions` endpoint assertions for contract provenance
  - closeout docs marking `Phase 18` complete / ready to close
