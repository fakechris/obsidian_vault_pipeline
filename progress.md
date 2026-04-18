# Progress Log

## Session: 2026-04-18

### Phase 23: Inbound Capture Audit Visibility
- **Status:** complete
- Actions taken:
  - Defined `Phase 23` as the second active execution slice for `Milestone 7`, scoped narrowly to inbound-capture legibility rather than hooks or background automation
  - Added deterministic `get_note_inbound_capture_summary(...)` in `truth_api.py`, derived from existing `pipeline.jsonl` and `refine-mutations.jsonl`
  - Kept the v1 event set intentionally narrow and explicit:
    - `source_staged_for_processing`
    - `source_archived_to_processed`
    - `source_restored_to_raw`
    - `article_processed`
    - `article_abstained`
    - `article_error`
    - `candidates_upserted`
    - `candidate_upsert_error`
    - `evergreen_auto_promoted`
    - `evergreen_created`
    - `evergreen_error`
    - `refine_mutation_applied`
  - Attached `capture_summary` to signal rows based on their backing `note_paths`
  - Upgraded note pages with a first-class `Inbound Capture` compiled section
  - Upgraded `/briefing` with an `Inbound Capture` compiled section derived from recent signals carrying note-level capture audit
  - Upgraded `/signals` to render item-level inbound capture summaries directly
  - Closed out `Phase 23` docs and moved the roadmap to the next Milestone 7 slice:
    - brain-first lookup + backlink legibility
- Files created/modified:
  - docs/plans/2026-04-18-phase23-inbound-capture-audit-visibility.md
  - docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
  - docs/research-tech/RESEARCH_TECH_VERIFY.md
  - src/openclaw_pipeline/truth_api.py
  - src/openclaw_pipeline/ui/view_models.py
  - src/openclaw_pipeline/commands/ui_server.py
  - tests/test_truth_api.py
  - tests/test_ui_view_models.py
  - tests/test_ui_server.py
  - progress.md
  - task_plan.md

## Session: 2026-04-17

### Phase 22: Active Signal Impact Accounting
- **Status:** complete
- Actions taken:
  - Defined `Phase 22` as the first active execution slice for `Milestone 7`, scoped narrowly to signal impact accounting rather than inbound hooks or background intelligence
  - Wrote a dedicated phase plan focused on:
    - deterministic signal/action impact summaries
    - signal/browser and action/browser lifecycle legibility
    - briefing-level loop productivity visibility
  - Added deterministic `impact_summary` contracts in `truth_api.py` for:
    - signal rows
    - action queue rows
  - Introduced stable lifecycle/impact buckets:
    - `review_only`
    - `ready`
    - `waiting`
    - `running`
    - `productive`
    - `completed`
    - `failed`
    - `stalled`
  - Kept the implementation grounded in existing runtime state only:
    - signal ledger
    - action queue state
    - focused-action result payloads
    - processor output metadata
  - Upgraded `build_signal_browser_payload` and `build_action_queue_payload` with `impact_counts`
  - Upgraded `build_briefing_payload` with:
    - `loop_summary`
    - leading `signal_loop` compiled section
    - more useful `first_useful_sign` selection when a productive signal exists
  - Updated the UI shell so `/signals`, `/actions`, and `/briefing` render the new impact/lifecycle language directly instead of only queue badges
  - Closed out `Phase 22` docs and moved the roadmap to the next Milestone 7 slice:
    - selected inbound capture + audit visibility
- Files created/modified:
  - docs/plans/2026-04-17-phase22-active-signal-impact-accounting.md
  - docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
  - docs/research-tech/RESEARCH_TECH_VERIFY.md
  - src/openclaw_pipeline/truth_api.py
  - src/openclaw_pipeline/ui/view_models.py
  - src/openclaw_pipeline/commands/ui_server.py
  - tests/test_truth_api.py
  - tests/test_ui_view_models.py
  - tests/test_ui_server.py
  - progress.md
  - task_plan.md

### Phase 21: Product Shell And Operator UX
- **Status:** complete
- Actions taken:
  - Defined `Phase 21` as the active successor to `Phase 20`, scoped narrowly to shell IA and operator affordances rather than signal automation or new intelligence layers
  - Added a dedicated implementation plan for `Phase 21` focused on:
    - dashboard workflow IA
    - cross-surface operator rails
    - page-shell ordering and affordance density
  - Updated the main milestone roadmap so `Phase 21` is now the active next reference for `Milestone 6`
  - Started `Task 1` with red-first tests for dashboard workflow groups and root-shell rendering
  - Added stable dashboard `workflow_groups` with five workflow buckets:
    - orient
    - inspect
    - review
    - trace
    - explore
  - Updated the root UI shell to render a visible `Workflow Map` block above `Where To Start`
  - Kept all workflow links pack-scoped through the existing shared-shell path helpers
  - Started `Task 2` with red-first payload and UI assertions for a shared `operator_rail` / `Next Actions` affordance across:
    - note pages
    - object pages
    - topic pages
    - event dossier
    - contradictions browser
    - signals browser
    - production browser
  - Added stable `operator_rail` payloads to the page builders that need cross-surface operator affordances
  - Added a shared UI renderer for `Next Actions` so those rails now render as a consistent card instead of ad hoc link rows
  - Fixed two real regressions caught by the wider suite while landing the rail:
    - restored a missing `operator_rail` declaration in `build_object_page_payload`
    - replaced an accidental object-scoped rail copy in `build_briefing_payload` with a briefing-scoped rail
  - Re-ran the full `ui_view_models` and `ui_server` suites to green after the focused red/green cycle passed
  - Completed `Task 3` by reordering shell pages so they now lead with:
    - a lead compiled section
    - then `Next Actions`
    - then contract/status and deeper evidence/review blocks
  - Closed out `Phase 21` docs:
    - marked the phase plan complete
    - marked `Milestone 6` complete
    - updated verify guidance for workflow IA and operator rails
    - pointed the roadmap at `Milestone 7: Active Signal Loop` as the next real gap
- Files created/modified:
  - docs/plans/2026-04-17-phase21-product-shell-and-operator-ux.md
  - docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
  - docs/research-tech/RESEARCH_TECH_VERIFY.md
  - progress.md
  - task_plan.md
  - src/openclaw_pipeline/ui/view_models.py
  - src/openclaw_pipeline/commands/ui_server.py
  - tests/test_ui_view_models.py
  - tests/test_ui_server.py

### Phase 20: Semantic Trust And Production Traceability
- **Status:** complete
- Actions taken:
  - Started the first execution batch for `Phase 20`, focused on event semantics, contradiction semantics, and production-chain traceability contracts
  - Hardened `Event Dossier` semantics with explicit:
    - `grouping_kind`
    - `anchor_kind_counts`
    - `event_vs_note_explanation`
  - Hardened contradiction rows with explicit:
    - `polarity_summary`
    - `evidence_summary`
    - `tension_summary`
  - Hardened contradiction browser contract with explicit:
    - `polarity_semantics`
    - `evidence_semantics`
  - Upgraded note/object traceability payloads with:
    - `stage_label`
    - `stage_presence`
    - `missing_stages`
    - `chain_status`
    - `chain_summary`
  - Spent the new trust/traceability fields on the current UI surfaces:
    - `/events`
    - `/contradictions`
    - `/note`
    - `/object`
    - `/production`
  - Added and ran red-first tests covering the new contract fields and page rendering
  - Completed a second execution batch for `Phase 20`, focused on spending those contracts on compiled trust sections across the production-chain surfaces
  - Upgraded `/note` and `/production` payloads/pages so their compiled sections now make chain state explicit via:
    - `current_state`
    - `evidence_traceability`
    - `production_chain`
    - `chain_gaps`
    - `where_to_go_next`
  - Added explicit `Production Chain` compiled sections to:
    - object pages
    - topic pages
  - Topic pages now spend `production_summary` on a stable compiled section instead of only a side-card summary, including:
    - top deep-dive contribution
    - Atlas/MOC reach
    - gap signals
  - Re-ran the trust/UI suite to green after locking the new note/object/topic/production expectations with focused tests first
  - Closed out `Phase 20` in the phase plan and the main milestone roadmap after the new trust/traceability contracts and compiled sections satisfied the phase exit conditions
- Files created/modified:
  - docs/plans/2026-04-17-phase20-semantic-trust-and-production-traceability.md
  - docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
  - docs/research-tech/RESEARCH_TECH_VERIFY.md
  - src/openclaw_pipeline/truth_api.py
  - src/openclaw_pipeline/ui/view_models.py
  - src/openclaw_pipeline/commands/ui_server.py
  - tests/test_truth_api.py
  - tests/test_ui_view_models.py
  - tests/test_ui_server.py
  - progress.md

### Phase 20: Semantic Trust And Production Traceability Planning
- **Status:** planned
- Actions taken:
  - Reviewed the post-`Phase 19` roadmap state in the main milestone doc
  - Re-read the archived `Phase 10` and `Phase 11` plans to avoid duplicating or reopening stale execution slices
  - Defined a new `Phase 20` as the active successor plan for the remaining Milestone 4 + 5 work
  - Scoped `Phase 20` around two product gaps only:
    - semantic trust for events and contradictions
    - production-chain legibility across notes, deep dives, objects, and Atlas/topic pages
  - Explicitly deferred temporal truth, harness/session memory, graph workspaces, and benchmark-platform work
- Files created/modified:
  - docs/plans/2026-04-17-phase20-semantic-trust-and-production-traceability.md
  - docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
  - task_plan.md
  - progress.md

### Phase 19: Orientation And Compiled Knowledge Products
- **Status:** complete
- Actions taken:
  - Added a first-class `orientation_brief` assembly recipe to `research-tech`
  - Extended `ovp-export` with an `orientation-brief` target that emits a compiled JSON entry product
  - Turned `/briefing` into an orientation page with stable compiled sections and section navigation
  - Added stable compiled-page sections to object/topic/event/contradiction payload builders
  - Upgraded the workbench home `/` into an entry surface with `Where To Start`, `Orientation Brief`, and explicit entry sections
  - Updated the shared UI shell to render compiled sections and section navigation across the new entry products
  - Closed out `Phase 19` docs and verify checklists for orientation and compiled-page contracts
- Files created/modified:
  - docs/plans/2026-04-17-phase19-orientation-and-compiled-knowledge-products.md
  - docs/plans/2026-04-14-local-knowledge-workbench-milestone.md
  - docs/pack-api/README.md
  - docs/research-tech/RESEARCH_TECH_SKILLPACK.md
  - docs/research-tech/RESEARCH_TECH_VERIFY.md
  - progress.md
  - task_plan.md
  - src/openclaw_pipeline/packs/research_tech/assembly_recipes.py
  - src/openclaw_pipeline/commands/export_artifact.py
  - src/openclaw_pipeline/ui/view_models.py
  - src/openclaw_pipeline/commands/ui_server.py
  - tests/test_export_command.py
  - tests/test_ui_view_models.py
  - tests/test_ui_server.py

### Phase 1: Local Project Context
- **Status:** complete
- **Started:** 2026-04-17 America/Los_Angeles
- Actions taken:
  - Ran superpowers bootstrap as required by the project instructions
  - Loaded `superpowers:using-superpowers`
  - Loaded `planning-with-files`
  - Initialized persistent planning files for this research task
  - Inspected repository layout, tracked current git state, and identified the main Python pipeline and vault/content directories
  - Read high-level local docs and key implementation files for truth projection, task queueing, and UI shell
- Files created/modified:
  - task_plan.md (created)
  - findings.md (created)
  - progress.md (created)

### Phase 2: arscontexta Discovery
- **Status:** complete
- Actions taken:
  - Read repository README and key reference files defining kernel primitives, three-space architecture, setup derivation flow, hooks, and generated reduce skill behavior
  - Verified that the runtime is implemented primarily as Claude Code plugin metadata, shell hooks, reference documents, and generated skill templates
- Files created/modified:
  - findings.md

### Phase 3: Comparative Analysis
- **Status:** in_progress
- Actions taken:
  - Compared arscontexta's derived operating system model against OpenClaw's existing pack-based orchestration and truth projection architecture
- Files created/modified:
  - findings.md

### Phase 4: Synthesis for Articles
- **Status:** complete
- Actions taken:
  - Wrote a reusable external-project discovery log with Round 1 focused on arscontexta and OVP product semantics
  - Captured the key conclusion that arscontexta is relevant mainly as a derivation/onboarding and runtime-contract reference, not as a backend model
  - Added Round 2 for the "横纵分析法 / HV Analysis" methodology article, positioning it as a front-end orientation/reporting pattern rather than a runtime architecture reference
  - Inspected `KKKKhazix/khazix-skills` to verify the prompt and `hv-analysis` skill implementation, then updated Round 2 to cover the real packaged workflow instead of only the article framing
  - Read the local clipping `Creating a Second Brain with Claude Code` and added Round 3, treating it as a concrete personal deployment blueprint rather than a core knowledge architecture reference
  - Inspected `alivecontext/alive` and added Round 4, classifying it as a stronger runtime-spec follow-on to the "Second Brain with Claude Code" article rather than a direct truth-model reference
  - Researched `getzep/zep` and added Round 5, classifying it as a context-serving / context-assembly platform rather than a local knowledge-system reference
  - Quickly screened `getzep/graphiti`, found it materially stronger than `getzep/zep` at the engine layer, and added Round 6 focused on temporal fact modeling, provenance, and retrieval recipes
  - Researched `trustgraph-ai/trustgraph` and added Round 7, classifying it as a context-infrastructure platform with useful ideas around context cores, ontology-constrained extraction, explainability, and orchestration
  - Researched `topoteretes/cognee` and added Round 8, classifying it as a productized agent-memory knowledge engine with especially strong ideas around memory verbs, session/permanent graph bridging, feedback-weight loops, and session-local graph snapshots
  - Added Round 9 for the Witcheer “Two Camps” article/thread, treating it as a meta-synthesis of the landscape and refining its binary framing into a three-layer map: memory backends, context substrates/runtimes, and context packaging/serving/governance
  - Researched `zilliztech/memsearch` and added Round 10, classifying it as a markdown-canonical semantic memory engine with a rebuildable Milvus shadow index, progressive disclosure recall model, and strong cross-platform plugin packaging
  - Added Round 11 for the Garry Tan “Resolvers” article/thread, treating it as a governance-pattern artifact and extracting resolver, trigger-eval, and reachability-audit concepts as a missing systems layer
  - Researched `MemPalace/mempalace` and added Round 12, classifying it as a mature retrieval-centered local memory backend with strong verbatim recall engineering, layered wake-up surfaces, closet indexing, and a local temporal knowledge graph
  - Researched `SuperagenticAI/metaharness` and added Round 13, classifying it as outer-loop harness-optimization infrastructure rather than a memory/context/runtime system, with possible future relevance for optimizing resolver docs, recipes, and validation harnesses
  - Researched `Dynamis-Labs/no-escape` and added Round 14, classifying it as a theory/constraint artifact on semantic memory systems rather than a product architecture reference
  - Researched `campfirein/byterover-cli` and added Round 15, classifying it as a file-canonical, review-governed, dream-maintained, versioned context-curation platform rather than a simple memory backend
  - Verified in code that ByteRover’s center of gravity is a writable `.brv/context-tree/` with snapshot/sync discipline, explicit derived-artifact exclusions, a real review queue, a dream pipeline split into consolidate/synthesize/prune, and version-control commands over the context artifact itself
  - Researched `vercel-labs/open-agents` and added Round 16, classifying it as a cloud coding-agent runtime reference app rather than a memory/context system
  - Verified in code that Open Agents centers on `Web -> Agent workflow -> Sandbox VM`, with durable workflow execution and Vercel sandbox lifecycle management rather than long-term knowledge accumulation
  - Added Round 17 for an “Open Harnesses, Open Memory” framing piece, treating it as a strategic ownership/lock-in artifact rather than a software project
  - Captured the main conclusion that memory ownership is effectively downstream of harness ownership, which strengthens OVP’s rationale for user-owned, portable, inspectable artifacts
  - Researched `rohitg00/agentmemory` and added Round 18, classifying it as an open-harness-compatible, self-hosted memory engine rather than a context substrate
  - Verified in code that agentmemory centers on KV-scoped memory state, hook-driven capture, BM25/vector/graph retrieval with token-budgeted injection, and a thick consolidation pipeline rather than file-canonical knowledge artifacts
  - Researched `EverMind-AI/EverOS` and added Round 19, focusing on the EverMemOS/EverCore method stack rather than the whole monorepo label
  - Verified in docs and code that EverMemOS is a construction-first, retrieval-heavy, DB/search-native memory operating system with typed memory extraction, multi-strategy retrieval, benchmark infrastructure, and a context-engine style OpenClaw integration
  - Added an actionable cross-round synthesis section translating the survey into recommended OVP direction, anti-goals, architecture layers, and near-term milestones
  - Wrote a dedicated architecture mapping doc clarifying how the proposed four-layer model stacks with the existing six-layer OVP pipeline and the current `Core Platform / Domain Pack / Workflow Profile` model
  - Inspected the current runtime interface layer in code: pack base types, workflow profile resolution, execution-contract resolution, handler registry, processor registry, observation surfaces, truth projection registry, and pack compatibility inheritance
  - Verified that the six-layer runtime flow is currently packaged through concrete stage names resolved into `ExecutionContractSpec = StageHandlerSpec + ProcessorContractSpec`, rather than through a single monolithic runtime-layer enum
  - Verified that `BaseDomainPack` already acts as a much richer architecture bundle than earlier docs suggest, carrying object kinds, workflow profiles, extraction/operation/wiki view declarations, stage handlers, truth projection, observation surfaces, and processor contracts
  - Wrote a dedicated interface contract mapping doc explaining how the current interfaces package the six-layer flow, `Core / Pack / Profile`, and the proposed four-layer persistent architecture view without replacing the current system
  - Inspected the current `ExtractionProfileSpec`, `OperationProfileSpec`, `WikiViewSpec`, `truth_store`, runtime queue/lock, action-worker, and signal/action code paths to determine how a next-step contract layer should fit the current implementation style
  - Designed a contract-evolution proposal that keeps the existing runtime intact and adds three new pack-side spec families: `ArtifactSpec`, `AssemblyRecipeSpec`, and `GovernanceSpec`
  - Wrote a dedicated contract-evolution design doc with three architecture options, a recommended path, draft dataclass shapes, phased rollout order, and a module-by-module borrowing map from the external project survey
  - Added a necessity ranking to the contract-evolution design so the external-module survey is not misread as ten equally necessary build tracks
  - Explicitly split the surveyed ideas into must-have-now, should-come-later, advanced/defer, and constraint-only buckets
  - Added a new `Phase 18` roadmap document positioning the contract-consolidation work as the architecture follow-up *after* `Phase 17`, not as extra scope stuffed into the graph-visualization phase
  - Updated the master local-knowledge-workbench milestone doc so the active next-step sequence is now `Phase 17` followed by `Phase 18`, with explicit sequencing rules and deferrals
  - Updated the `Phase 17` plan so its “What Comes After” section now points first to `Phase 18` contract consolidation before richer graph actions or cross-pack product work
  - Started implementation of `Phase 18` with the first smallest closed loop: `ArtifactSpec`
  - Added pack-base artifact contract dataclasses and `BaseDomainPack.artifact_specs()`
  - Declared the first five `research-tech` artifact families:
    - object
    - claim
    - evidence
    - overview
    - review_item
  - Extended `ovp-doctor` to expose declared and effective artifact specs, including compatibility-pack inheritance behavior
  - Updated Pack API and `research-tech` skillpack docs to mention explicit artifact-family contracts
  - Continued `Phase 18` with the second smallest closed loop: `AssemblyRecipeSpec`
  - Added pack-base assembly recipe contract dataclasses and `BaseDomainPack.assembly_recipes()`
  - Declared the first five `research-tech` assembly recipes:
    - operator_briefing
    - topic_overview
    - object_brief
    - event_dossier
    - contradiction_view
  - Extended `ovp-doctor` to expose declared and effective assembly recipes, including compatibility-pack inheritance behavior
  - Updated Pack API and `research-tech` skillpack docs to mention explicit assembly/access contracts
  - Continued `Phase 18` with the third smallest closed loop: `GovernanceSpec`
  - Added pack-base governance contract dataclasses and `BaseDomainPack.governance_specs()`
  - Declared the first `research-tech` governance bundle with:
    - review queues
    - signal rules
    - resolver rules
  - Extended `ovp-doctor` to expose declared and effective governance specs, including compatibility-pack inheritance behavior
  - Updated Pack API and `research-tech` skillpack docs to mention explicit governance/routing contracts
  - Started the first contract-consumption step after declaration work by wiring `ovp-export` through `AssemblyRecipeSpec` instead of a pure hardcoded target-to-view map
  - Kept the existing CLI targets stable while changing the internal resolution path to:
    - export target -> assembly recipe
    - assembly recipe -> source contract
    - source contract -> resolved wiki view
  - Added export-command coverage proving compatibility packs can inherit an assembly recipe from `research-tech` while still using their own declared wiki-view spec
  - Updated Pack API and `research-tech` verify docs to reflect that export now consumes assembly contracts directly
  - Started the first runtime contract-consumption step by wiring truth-api auto-queue behavior through `GovernanceSpec.signal_rules`
  - Replaced the hardcoded auto-queue signal lookup in `truth_api` with a governance-backed resolver that walks the compatibility chain
  - Added truth-api coverage proving the auto-queue signal set comes from governance contracts and that `default-knowledge` inherits the same auto-queue signals through compatibility resolution
  - Continued runtime contract consumption by enriching `list_signals()` output from `GovernanceSpec.resolver_rules`
  - Attached resolver metadata like `resolution_kind`, `dispatch_mode`, and governance-backed `safe_to_run` to recommended actions at ledger-read time instead of requiring each signal producer to duplicate that policy
  - Added truth-api coverage proving contradiction-review signals and focused-action signals both surface governance-backed resolver metadata
  - Extended runtime contract consumption to the action queue by backfilling `resolution_kind` and `dispatch_mode` from `GovernanceSpec.resolver_rules` in `list_action_queue()`
  - Updated the UI shell so `/signals` and `/actions` now render governance-backed resolver metadata instead of only showing executable/manual state
  - Added UI coverage proving resolver metadata is visible in both the signal browser and action queue browser
  - Brought `/briefing` into the same contract-consumption path so priority items now render governance-backed resolver metadata alongside recommended actions
  - Continued `Phase 18` by extracting a shared `assembly_recipe_registry`
  - Replaced the local export-command recipe resolver with the shared registry so `ovp-export` and UI access surfaces now read from the same assembly contract source
  - Extended UI payload builders to expose `assembly_contract` on:
    - `object/page`
    - `overview/topic`
    - `event/dossier`
    - `truth/contradictions`
    - `briefing/intelligence`
  - Updated the shared UI shell to render an explicit Assembly Contract card showing:
    - recipe provider/inheritance
    - source contract kind/name
    - output mode and publish target
  - Added view-model and UI coverage proving compatibility-pack pages inherit assembly recipes from `research-tech` while preserving pack-scoped links
  - Refined assembly contract resolution so access artifacts now distinguish:
    - recipe provider
    - source contract provider
  - This means compatibility-pack pages can now show the real chain, e.g.:
    - recipe inherited from `research-tech`
    - wiki view still served by `default-knowledge`
  - Extended `ovp-doctor` so declared/effective assembly recipe payloads now expose the same source-provider chain as export and UI
  - This closes the operator-tooling loop: doctor, export, payload builders, and shared shell now all describe access contracts with the same fields
  - Continued `Phase 18` by extracting a shared `governance_registry`
  - Added governance-contract payloads to `signals/browser`, `actions/browser`, and `briefing/intelligence`
  - Shared UI shell pages now render a `Governance Contract` card showing provider/inheritance plus review queue, signal rule, and resolver rule counts/previews
  - Replaced the local truth-api resolver/governance pack walk with governance-registry lookups so runtime metadata and UI contract explanations come from the same source
  - Extended runtime item metadata so recommended actions and queued actions now expose:
    - `resolver_rule_name`
    - `governance_provider_name`
    - `governance_provider_pack`
  - Updated the shared shell so `/signals`, `/actions`, and `/briefing` render that rule/provider provenance alongside `resolution_kind` and `dispatch_mode`
  - Added coverage proving both payload builders and rendered pages expose governance-contract metadata across declared and inherited pack scopes
  - Brought `ovp-doctor` shell payload onto the same governance explanation path by exposing a resolved `governance_contract` summary beside shared/research route availability
  - Added doctor coverage proving the shell-level governance contract resolves as `declared` for `research-tech` and `inherited` for `default-knowledge`
  - Added a shared `artifact_registry` so artifact-family resolution now has the same registry shape as assembly/governance resolution
  - Switched `ovp-doctor` effective artifact/assembly/governance enumeration to registry-backed resolution instead of bespoke compatibility-chain loops
  - Finished `Phase 18` operator docs by adding:
    - minimal manifest-and-hooks examples for `ArtifactSpec`, `AssemblyRecipeSpec`, and `GovernanceSpec`
    - explicit `declared / inherited / missing` semantics
    - concrete `ovp-doctor / ovp-ui / ovp-export` verification guidance
  - Extended `research-tech` verify docs with explicit API checks for `/api/briefing`, `/api/signals`, and `/api/actions`
  - Added explicit UI-server endpoint assertions proving the JSON APIs preserve:
    - `assembly_contract`
    - `governance_contract`
    - item-level resolver/governance provenance
  - Marked `Phase 18` as complete / ready to close in the phase plan and the milestone sequencing doc
  - Researched `HKUDS/DeepTutor` and added Round 20, classifying it as a tutoring-oriented context-assembly and compiled-learning-product system rather than a knowledge compiler
  - Verified in code that DeepTutor’s KB path is conventional llamaindex indexing:
    - `raw/` staging
    - PDF/text parsing
    - fixed chunking
    - embeddings
    - persisted vector index in `llamaindex_storage/`
  - Verified that the older “numbered item” extraction path for definitions/theorems/equations is now explicitly deprecated/no-op in the active llamaindex-only route
  - Identified the strongest transferable idea in `services/session/turn_runtime.py`: one turn composes notebook context, history context, lightweight memory, conversation history, attachments, and selected KBs into a single `UnifiedContext`
  - Verified that notebook references are not injected raw; they are first compressed by `NotebookAnalysisAgent` into a question-targeted observation note
  - Verified that notebook records and deep research outputs are treated as reusable work artifacts rather than canonical truth objects
- Files created/modified:
  - docs/plans/2026-04-17-external-project-discovery-log.md
  - docs/plans/2026-04-17-ovp-architecture-mapping.md
  - docs/plans/2026-04-17-ovp-interface-contract-mapping.md
  - docs/plans/2026-04-17-ovp-contract-evolution-design.md
  - docs/plans/2026-04-17-phase18-knowledge-compiler-contract-consolidation-plan.md
  - docs/pack-api/README.md
  - docs/research-tech/RESEARCH_TECH_SKILLPACK.md
  - src/openclaw_pipeline/packs/base.py
  - src/openclaw_pipeline/packs/research_tech/assembly_recipes.py
  - src/openclaw_pipeline/packs/research_tech/artifacts.py
  - src/openclaw_pipeline/packs/research_tech/governance.py
  - src/openclaw_pipeline/packs/research_tech/pack.py
  - src/openclaw_pipeline/commands/export_artifact.py
  - src/openclaw_pipeline/assembly_recipe_registry.py
  - src/openclaw_pipeline/artifact_registry.py
  - src/openclaw_pipeline/governance_registry.py
  - src/openclaw_pipeline/ui/view_models.py
  - src/openclaw_pipeline/commands/ui_server.py
  - src/openclaw_pipeline/commands/doctor.py
  - src/openclaw_pipeline/truth_api.py
  - tests/test_export_command.py
  - tests/test_ui_view_models.py
  - tests/test_ui_server.py
  - tests/test_truth_api.py
  - tests/test_doctor_command.py
  - tests/test_artifact_registry.py
  - findings.md
  - progress.md

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Skill bootstrap | `superpowers-codex bootstrap` | Load workflow guidance | Completed successfully | ✓ |
| Skill load | `superpowers-codex use-skill planning-with-files` | Load persistent research workflow | Completed successfully | ✓ |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-04-17 | `github:github` not found in `superpowers-codex use-skill` | 1 | Continued with direct inspection and available GitHub/web tools |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 4: ongoing external-project discovery and synthesis for OVP |
| Where am I going? | Continue classifying external projects and fold durable lessons into the survey map |
| What's the goal? | Build a stable comparative map of external memory/context systems and extract what matters for OVP |
| What have I learned? | See findings.md |
| What have I done? | Initialized workflow and planning files |

*Update after completing each phase or encountering errors*
