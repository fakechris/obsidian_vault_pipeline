# Findings & Decisions

## Requirements
- Explain what `agenticnotetaking/arscontexta` does in detail
- Evaluate why it matters conceptually and architecturally
- Judge what is relevant to the current OpenClaw project
- Surface several article/research directions based on the comparison

## Research Findings
- Local project is centered on `src/openclaw_pipeline`, with commands for `absorb`, `build_views`, `truth_api`, `knowledge_index`, `run_operations`, and registry maintenance.
- Repository structure mixes a vault-like content tree (`10-Knowledge`, `20-Areas`, `50-Inbox`, `80-Views`) with a Python pipeline that materializes knowledge artifacts and views.
- Existing docs already frame the system around packs, extraction profiles, truth projections, observation surfaces, and materialized views, which means the current project emphasis is knowledge processing and presentation rather than a lightweight interactive note editor.
- There are existing article/recipe surfaces under `docs/recipes/research-tech/*.md`, suggesting article-worthy workflow narratives already exist around ingesting clippings, GitHub repos, PDFs, and web articles.
- `arscontexta` positions itself as a Claude Code plugin that derives a complete knowledge system from conversation, not as a fixed vault template.
- The generated system centers on three spaces: `self/`, `notes/`, and `ops/`, combining persistent agent identity, a markdown knowledge graph, and operational coordination state.
- Its central promise is "derivation, not templating": it claims architecture choices are traced to a research graph of 249 methodology claims rather than chosen from static presets alone.
- The plugin surface is split between plugin-level commands (`/arscontexta:setup`, `/ask`, `/architect`, `/reseed`, `/upgrade`) and generated workflow commands (`/reduce`, `/reflect`, `/reweave`, `/verify`, `/pipeline`, `/ralph`).
- The processing model is the "6 Rs": Record, Reduce, Reflect, Reweave, Verify, Rethink, with explicit emphasis on refreshing context per phase via subagent spawning.
- The hook layer enforces operational discipline: session orientation, write-time schema validation, asynchronous auto-commit, and session-state capture.
- `arscontexta`'s `reference/kernel.yaml` is the real conceptual center: 15 primitives define invariant behaviors such as markdown+YAML notes, wiki-link graph edges, MOC hierarchy, discovery-first design, operational learning loop, task stack, methodology folder, and session capture.
- The setup flow is not a thin wizard. `skills/setup/SKILL.md` encodes a serious derivation process: platform detection, conversational signal extraction, vocabulary remapping, dimension resolution with confidence scores, coherence validation, failure-mode checks, then generation of hooks, templates, skills, queue structures, and validation.
- The implementation is mostly "productized prompts + shell runtime" rather than a compiled application. The heavy logic lives in setup/generation instructions, reference docs, generated skill templates, and shell hooks.
- The hook implementations are concrete, not fictional: `session-orient.sh` injects workspace state and maintenance signals, while `write-validate.sh` enforces note schema presence on write events.
- The three-space architecture is a strong design rule rather than simple folder preference: `self/` is agent identity/orientation, `notes/` is durable knowledge, and `ops/` is temporal coordination. The repo explicitly models failure modes caused by mixing those spaces.
- A notable product choice is "full automation by default, opt down later." The generated system installs a large command surface immediately and expects the user to restart Claude Code to activate skills and hooks.
- Local implementation confirms OpenClaw already embodies several "hard system" ideas that arscontexta mostly expresses via generated conventions:
  - `truth_store.py` defines an explicit canonical/derived projection schema with objects, claims, evidence, relations, contradictions, graph edges, and graph clusters.
  - `autopilot/queue.py` implements a persistent SQLite task queue with claim/complete/fail lifecycle, deduped active identities, and retry semantics.
  - `commands/ui_server.py` exposes a read-oriented shell over objects, signals, actions, contradictions, evolution, clusters, atlas, and briefing views.
- This reinforces a core comparison: arscontexta is stronger at front-door onboarding and agent-operating conventions; OpenClaw is stronger at domain truth modeling, derived-state boundaries, and operator surfaces.
- Created a durable discovery document at `docs/plans/2026-04-17-external-project-discovery-log.md` to hold this round's conclusions and future external-project comparisons.
- Round 2 added the "横纵分析法 / HV Analysis" article as a research-methodology comparison. The key conclusion is that it is valuable as an orientation-report pattern and question scaffold, but not as a canonical knowledge/runtime model.
- Repository inspection of `KKKKhazix/khazix-skills` confirmed that the method has been operationalized into both a reusable prompt and a heavier `hv-analysis` skill with mandatory web research, optional parallel-subagent collection, arXiv guidance, writing-style constraints, and Markdown-to-PDF conversion.
- Round 3 added the local article `Creating a Second Brain with Claude Code` as a personal deployment blueprint for local work-memory systems. The key conclusion is that its strongest lessons are staged bootstrap, a distilled context layer, hybrid retrieval, ambient hook-based context injection, and multi-timescale learning loops.
- Round 4 added `alivecontext/alive` as the strongest operational follow-on to the "Second Brain with Claude Code" article. The key conclusion is that it turns that article's intuitions into a stricter context runtime with an explicit world model, save/load protocol, projection discipline, and multi-session coordination hooks.
- `alive` is best understood as a file-backed context operating system for repeated AI sessions, not just a memory plugin. Its strongest concepts are:
  - authored source files vs generated snapshot state (`_kernel/key.md`, `log.md`, `insights.md` vs computed `now.json`)
  - save/load treated as protocol rather than convenience
  - separation between unit of context (`walnut`) and unit of work (`bundle`)
  - stale-context and cross-session interference detection via hooks
- The most relevant transferable lesson from `alive` to OVP is projection discipline plus a clearer agent-facing load/save contract. It is not a replacement for OVP's stronger truth model, evidence model, contradiction handling, or derived knowledge views.
- Round 5 added `getzep/zep`. The key conclusion is that Zep is not a local knowledge system but an agent context engineering / memory serving platform centered on ingesting chat + business data, building a temporal context graph, and returning prompt-ready context blocks.
- The current `getzep/zep` repository is not the full maintained product core. It now mainly contains examples, integrations, an MCP server, and a deprecated Community Edition under `legacy/`; the company’s active open-source focus moved to `Graphiti` in April 2025.
- Zep’s strongest ideas are:
  - context assembly as a first-class product surface
  - temporal fact invalidation and time-aware retrieval
  - prompt-ready context blocks and templates
  - clear architecture patterns for user graphs, domain graphs, and mixed retrieval
- The most relevant lesson from Zep for OVP is not “use a graph” but “distinguish knowledge compilation from context serving.” Zep is a serving-layer reference for agent-ready brief/context artifacts, not a reference for OVP’s canonical knowledge architecture.
- Round 6 added `getzep/graphiti`. The key conclusion is that Graphiti is the first external project in this survey with real engineering depth around temporal fact modeling rather than just productized memory/retrieval rhetoric.
- Graphiti’s strongest ideas are:
  - an episode/entity/edge model with explicit provenance
  - `valid_at` / `invalid_at` windows on facts instead of overwrite-only memory
  - incremental ingestion via `add_episode()` rather than batch graph recomputation
  - configurable hybrid retrieval and reranking recipes across nodes, edges, and episodes
- The most relevant lesson from Graphiti for OVP is not “use Neo4j/graph DB” but “treat fact invalidation, time windows, and source lineage as first-class if you care about evolving truth.”
- Round 7 added `trustgraph-ai/trustgraph`. The key conclusion is that TrustGraph is a platform-scale “context infrastructure” system rather than a narrow knowledge/memory tool, with meaningful ideas around context cores, ontology-constrained extraction, explainability graphs, and flow orchestration.
- TrustGraph’s strongest ideas are:
  - `context core` as a portable, versioned, promotable context artifact
  - explainability and provenance modeled as queryable graph entities rather than plain logs
  - ontology-driven extraction/query (`OntoRAG`) as a precision-oriented mode
  - explicit flow/queue ownership and orchestration discipline for agentic pipelines
- The most relevant lesson from TrustGraph for OVP is not to become a full infra platform, but to think more clearly about the unit of packaged context, structural explainability, and when ontology-constrained extraction is worth the added weight.
- Round 8 added `topoteretes/cognee`. The key conclusion is that Cognee is not mainly a graph/retrieval engine but a productized agent-memory knowledge engine with a strong verb surface: `remember`, `recall`, `improve`, `forget`, `serve`.
- Cognee’s strongest ideas are:
  - one unified memory surface spanning session cache and permanent graph
  - `improve()` as a real bridge layer for session -> graph, feedback -> graph weights, and graph -> session sync
  - session-local compiled graph snapshots with checkpoints and size budgets
  - a clear product semantics layer built on memory verbs rather than raw pipeline terminology
- The most relevant lesson from Cognee for OVP is not to become a generic memory engine, but to think harder about user-facing verbs, session-to-durable knowledge bridges, and whether operator feedback should become a structured refinement signal.
- Round 9 added the Witcheer “Two Camps” article/thread as a meta-synthesis artifact rather than a software project. The key conclusion is that its core split is directionally right: the meaningful industry divide is recall systems vs compounding systems.
- The article’s strongest idea is the distinction between:
  - memory backends, which optimize fact recall
  - context substrates, which optimize cumulative working context across sessions
- The article is still too binary for the full landscape. The current survey supports a three-layer map instead:
  - memory backends
  - context substrates / runtimes
  - context packaging / serving / governance
- The most relevant lesson from this article for OVP is that OVP should not describe itself as a hidden memory layer. It is closer to compiled, human-readable, accumulating knowledge context.
- Round 10 added `zilliztech/memsearch`. The key conclusion is that MemSearch is not a pure context substrate and not a classic memory backend either; it is best understood as a markdown-canonical semantic memory engine.
- MemSearch’s strongest ideas are:
  - markdown files as canonical state with Milvus as a rebuildable shadow index
  - progressive disclosure (`search -> expand -> transcript`) as a disciplined recall UX
  - pragmatic cross-platform packaging across Claude Code, OpenClaw, OpenCode, and Codex CLI
  - a lightweight consolidation loop via `compact` that writes summaries back into the markdown memory store
- The most relevant lesson from MemSearch for OVP is not to become “search over markdown,” but to preserve the source-of-truth / derived-index boundary and think seriously about progressive disclosure and agent-facing access layers.
- Round 11 added the Garry Tan “Resolvers” article/thread as a governance-pattern artifact rather than a repo-first project. The key conclusion is that resolver is a missing first-class primitive: a routing/governance layer for skills, context, and filing decisions.
- The resolver article’s strongest ideas are:
  - capability existence is different from capability reachability
  - routing must be explicit instead of being smuggled into each skill’s private logic
  - trigger evals and reachability audits are as important as output evals
  - resolvers decay over time and need maintenance, tests, and eventually self-healing loops
- The most relevant lesson from the resolver article for OVP is that any future multi-skill / multi-pack / multi-artifact system needs an explicit resolver layer, plus routing evals and a `check-resolvable`-style audit, or it will silently drift.
- Round 12 added `MemPalace/mempalace`. The key conclusion is that MemPalace is thicker than the common summary suggests, but it is still fundamentally a retrieval-centered, verbatim-first local memory backend rather than a context substrate.
- MemPalace’s strongest ideas are:
  - very strong verbatim recall engineering, including hybrid retrieval and benchmark discipline
  - a layered memory stack (`L0-L3`) for bounded wake-up and scoped recall
  - closet pointers as ranking signals rather than hard gates
  - a lightweight local temporal knowledge graph layered beside the main recall path
- The most relevant lesson from MemPalace for OVP is not to become DB-centered local memory, but to take recall quality, layered wake-up design, and ranking-signal discipline more seriously.
- Round 13 added `SuperagenticAI/metaharness`. The key conclusion is that MetaHarness is not a memory/context/runtime system at all; it is an outer-loop optimization system for harness artifacts such as instructions, bootstrap scripts, validation scripts, and routing glue.
- MetaHarness’s strongest ideas are:
  - treating the harness around an agent as executable, optimizable code
  - filesystem-first candidate runs with explicit keep/discard evidence
  - deterministic evaluation of instruction and script changes
  - a practical path for optimizing governance artifacts rather than only prompts
- The most relevant lesson from MetaHarness for OVP is future-facing: if OVP later formalizes resolver docs, filing rules, recipes, and harness scripts, those can become optimization targets under an outer loop like this.
- Round 14 added `Dynamis-Labs/no-escape`. The key conclusion is that this is not a product-system reference but a theory/experiment constraint on memory systems: semantic organization appears to carry unavoidable interference, forgetting, or false-recall costs.
- No Escape’s strongest contribution to this survey is:
  - a constraint perspective on semantic memory rather than another implementation pattern
  - a useful reminder that different architectures may shift the behavioural manifestation, but not necessarily remove the geometric vulnerability
  - a warning against assuming that better embeddings alone will solve memory-system failure modes
- The most relevant lesson from No Escape for OVP is to avoid overcommitting to pure semantic retrieval and instead lean harder on explicit structure, provenance, canonical objects, review loops, and resolver-guided narrowing.
- Round 15 added `campfirein/byterover-cli`. The key conclusion is that ByteRover CLI is best understood not as a simple memory backend, but as a file-canonical, review-governed, dream-maintained, versioned context-curation platform.
- ByteRover CLI’s strongest ideas are:
  - a writable `.brv/context-tree/` as the main working surface, with snapshot/sync logic treating files as canonical state
  - a clean split between deterministic search, synthesized query, and curation/write-back
  - a real review queue for pending high-impact curation operations
  - a concrete dream pipeline split into consolidate, synthesize, and prune rather than vague “memory consolidation”
  - explicit canonical-vs-derived exclusion rules for `_index.md`, manifests, archive artifacts, and searchable stubs
  - version-control semantics for context artifacts rather than only implicit filesystem drift
- The most relevant lesson from ByteRover CLI for OVP is not to copy its product shell, but to think harder about curation as a first-class action, review queues for machine-generated knowledge changes, and stricter operational boundaries between search, query, canonical notes, and derived artifacts.
- Round 16 added `vercel-labs/open-agents`. The key conclusion is that Open Agents is a cloud coding-agent runtime reference app, not a memory/context system.
- Open Agents’ strongest ideas are:
  - explicit separation between the agent control plane and the sandbox execution environment
  - durable workflow-backed runs with resume/cancel semantics
  - a serious cloud sandbox abstraction with hibernation, reconnect, and GitHub credential brokering
  - a clear example of how much product complexity appears once you package a hosted coding agent
- The most relevant lesson from Open Agents for OVP is mostly negative or future-facing: it is useful only if OVP later needs remote execution or durable workflow infrastructure, but it does not provide much guidance for OVP’s core knowledge, context, or artifact-governance questions.
- Round 17 added an “Open Harnesses, Open Memory” framing piece. The key conclusion is that its most useful idea is strategic rather than architectural: memory ownership is downstream of harness ownership.
- This article’s strongest ideas are:
  - harnesses are persistent infrastructure, not temporary scaffolding
  - memory is tightly coupled to context/state management inside the harness
  - provider-managed state creates real switching costs and memory lock-in
  - open, portable, inspectable artifacts are a strategic defense against that lock-in
- The most relevant lesson from this article for OVP is to treat user-owned, portable, rebuildable memory artifacts as a product principle, while avoiding provider-locked thread state or opaque compaction formats as core dependencies.
- Round 18 added `rohitg00/agentmemory`. The key conclusion is that agentmemory is a strong sample of an open-harness-compatible, self-hosted memory engine, not a context substrate or file-canonical knowledge runtime.
- agentmemory’s strongest ideas are:
  - automatic hook-driven capture with near-zero manual effort
  - a thick retrieval stack: BM25 + vector + graph + RRF + token-budgeted injection
  - a more mature memory lifecycle than most backends, including semantic/procedural consolidation, decay, and auto-eviction
  - multi-agent coordination primitives such as leases, signals, routines, checkpoints, and mesh sync
  - practical cross-agent packaging via MCP, REST, hooks, and viewer tooling
- The most relevant lesson from agentmemory for OVP is to take automatic capture, access-layer retrieval engineering, and coordination signals seriously, while avoiding the trap of collapsing OVP into a KV-backed recall-and-injection engine.
- Round 19 added `EverMind-AI/EverOS`. The key conclusion is that the relevant part of EverOS is its EverMemOS/EverCore method stack: a construction-first, retrieval-heavy, DB/search-native memory operating system with attached benchmarks and integrations.
- EverOS’s strongest ideas are:
  - a strong emphasis on memory construction discipline, especially MemCell extraction and typed memory construction
  - an explicit multi-strategy retrieval layer spanning BM25, vector, hybrid, RRF, and agentic multi-round recall
  - a benchmark worldview that treats memory quality and agent evolution as first-class evaluation targets
  - an integration pattern that connects memory at the context-engine layer rather than treating it as a loose memory tool
- The most relevant lesson from EverOS for OVP is to strengthen construction schemas, typed artifact thinking, and benchmark rigor, while avoiding a drift into heavy service/database-native architecture at the expense of file-native inspectability and user-owned artifacts.
- Current actionable synthesis across all rounds:
  - OVP should not become a generic memory backend or a hosted coding-agent runtime
  - OVP should lean into user-owned, file-native, reviewable canonical knowledge artifacts
  - OVP needs a clearer architecture split between canonical artifacts, derived indexes/views, context assembly, and governance
  - The most important missing pieces now are artifact taxonomy, resolver/governance, review queues, and a strong orientation/access layer
  - The recommended near-term wedge is an Orientation + Canonical Artifact Loop: artifact schema v1, orientation brief, reviewed absorb loop, and a basic resolver
- Architectural mapping conclusion:
  - the six-layer OVP pipeline remains the execution DAG
  - `Core Platform / Domain Pack / Workflow Profile` remains the ownership and extension model
  - the proposed four-layer model should be treated as a persistent architecture contract over the existing system, not as a replacement
  - the weakest current layer is not “truth storage” but the explicitness of context assembly and governance
- Interface-contract mapping conclusion:
  - current OVP already has a real interface stack: entry surfaces, pack declarations, execution contracts, truth/access surfaces, and governance operations
  - the six-layer runtime flow is currently packaged through concrete stage names that resolve into `ExecutionContractSpec = StageHandlerSpec + ProcessorContractSpec`
  - `WorkflowProfile` is intentionally small and should remain a routing object rather than becoming the home of all semantics
  - `BaseDomainPack` already carries much more architecture than earlier docs imply: object kinds, workflow profiles, extraction/operation/wiki views, stage handlers, truth projection, observation surfaces, and processor contracts
  - `default-knowledge` demonstrates the intended inheritance model: compatibility packs can override selected semantics while inheriting runtime contracts from a base pack
  - the most meaningful missing explicit contracts are still pack-side canonical artifact contracts, context-assembly recipe contracts, and governance/resolver contracts
- Contract-evolution design conclusion:
  - the right next step is parallel contract expansion, not a runtime rewrite
  - the recommended new pack-side spec families are `ArtifactSpec`, `AssemblyRecipeSpec`, and `GovernanceSpec`
  - `ArtifactSpec` should land first because Layer 1 is still the most implicit architectural layer
  - `AssemblyRecipeSpec` should come second to unify wiki views, observation surfaces, exports, and briefing-like products under one access-layer language
  - `GovernanceSpec` should come third to make queues, signals, resolver rules, and action routing explicit without replacing the existing operation/action runtime
  - the external-module survey should not be read as ten equally necessary roadmap items; several are constraints or later-stage refinements rather than immediate build tracks
  - the current must-have set is smaller: file-canonical access, reviewed curation, basic governance/routing, typed construction, lightweight bootstrap/operating contract, and lightweight runtime discipline
  - `orientation artifact` and `benchmark worldview` are useful later, but not required to make the core architecture coherent
  - `temporal truth` and `capture/access-layer memory` are real future directions, but would be premature before canonical artifacts and governance are explicit
  - in the active roadmap, this work should sit *after* `Phase 17` rather than inside it: `Phase 17` remains graph-product work, while the new `Phase 18` should be a contract-consolidation phase
  - the recommended Phase 18 scope is: `ArtifactSpec` -> `AssemblyRecipeSpec` -> `GovernanceSpec`, plus doctor/docs surfacing and `research-tech` dogfooding
  - there is no single best external reference overall; OVP should borrow by module:
    - `alive` for runtime discipline
    - `Graphiti` for temporal truth evolution
    - `MemSearch` for file-canonical access-layer boundaries
    - `ByteRover CLI` for reviewed curation and background maintenance
    - Garry Tan resolver system for routing/reachability governance
    - `hv-analysis` for orientation artifacts
    - `arscontexta` for operating-contract/bootstrap framing
    - `agentmemory` for capture/access-layer memory ideas
    - `EverOS` for typed construction and evaluation worldview
    - `no-escape` as the key constraint reference

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use repo docs plus code inspection, not README alone | Prevents over-reading marketing language into the system |
| Treat `arscontexta` as a methodology product more than a software library | Most of the value is encoded in derivation/generation rules and operating conventions rather than runtime code volume |
| Treat `alive` as a context-runtime reference rather than a knowledge-model reference | Its strongest ideas are operational contracts and projection discipline, not canonical truth compilation |
| Treat `Zep` as a context-serving reference rather than a vault/runtime reference | Its product center is retrieval and assembly of agent context, not local knowledge compilation |
| Treat `Graphiti` as a temporal fact-model reference rather than a full product reference | Its lasting value for OVP is time-aware truth evolution and provenance, not the surrounding agent-memory product framing |
| Treat `TrustGraph` as a context-infrastructure reference rather than a direct product reference | Its value to OVP lies in packaging/governance/explainability concepts, not in copying its full platform scope |
| Treat `Cognee` as a memory-product semantics reference rather than a truth-model reference | Its strongest transferable value for OVP is user-facing memory verbs and session/graph bridging, not canonical truth architecture |
| Treat the Witcheer “Two Camps” piece as a framing artifact, not a final taxonomy | Its recall-vs-compounding split is useful, but several surveyed systems live in a third packaging/serving/governance layer |
| Treat `MemSearch` as a markdown-canonical memory-engine reference rather than a pure substrate reference | Its most valuable transferable ideas are canonical-file boundaries, progressive disclosure, and plugin packaging, not a full knowledge-runtime model |
| Treat the Garry Tan “Resolvers” piece as a governance primitive, not just prompt advice | Its core value is making routing, reachability, and filing discipline explicit and testable across the system |
| Treat `MemPalace` as a mature Layer-1 recall system rather than a substrate exemplar | Its center of gravity remains verbatim retrieval, even though it now includes layered wake-up, closets, hooks, and a local temporal KG |
| Treat `MetaHarness` as meta-layer optimization infrastructure rather than a runtime architecture reference | Its value is in optimizing instructions, scripts, and governance artifacts, not in providing a memory/context substrate itself |
| Treat `No Escape` as a theoretical constraint reference rather than a product reference | Its main value is setting limits on semantic-memory expectations, not providing a reusable runtime architecture |
| Treat `ByteRover CLI` as a context-curation platform rather than a plain memory tool | Its strongest transferable value is in reviewed curation workflows, dream maintenance, and versioned context artifacts, not its full provider/cloud product shell |
| Treat `Open Agents` as a cloud runtime reference rather than a knowledge-system reference | Its value lies in agent/sandbox separation and durable execution, not in memory, context accumulation, or vault-native knowledge architecture |
| Treat the “Open Harnesses, Open Memory” piece as a strategic framing artifact rather than an implementation guide | Its value is in clarifying ownership, portability, and lock-in, not in specifying concrete runtime or artifact architecture |
| Treat `agentmemory` as a memory-engine reference rather than a substrate reference | Its strengths are in automated capture, retrieval, and injection across open harnesses, not in human-readable canonical knowledge compilation |
| Treat `EverOS` mainly through its EverMemOS/EverCore method stack, not as one monolithic product | Its strongest transferable value is in memory construction and evaluation worldview, not in its full infrastructure stack or repository packaging |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| GitHub skill alias not loadable via superpowers CLI | Continued with direct repo inspection and GitHub/web tools |

## Resources
- Local project root: `/Users/chris/Documents/openclaw-template`
- Target repo: `https://github.com/agenticnotetaking/arscontexta`
- Local docs to inspect next: `README.md`, `README_EN.md`, `docs/pack-api/README.md`, `docs/research-tech/RESEARCH_TECH_SKILLPACK.md`
- External docs to inspect next: `reference/kernel.yaml`, `skills/setup/SKILL.md`, `hooks/hooks.json`, selected setup/generator assets from `arscontexta`
- Local comparison targets to inspect next: `src/openclaw_pipeline/truth_store.py`, `src/openclaw_pipeline/autopilot/queue.py`, `src/openclaw_pipeline/commands/ui_server.py`
- Inspected local implementation: `src/openclaw_pipeline/truth_store.py`, `src/openclaw_pipeline/autopilot/queue.py`, `src/openclaw_pipeline/commands/ui_server.py`

## Visual/Browser Findings
- Pending

*Update this file after every 2 view/browser/search operations*
