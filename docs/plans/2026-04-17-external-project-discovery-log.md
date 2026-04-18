# External Project Discovery Log

**Purpose:** Capture what external projects are actually doing, what they are actually good at, and whether they meaningfully help `Obsidian Vault Pipeline` rather than merely sounding adjacent.

**Status:** Living document. New projects should be added as new rounds under the same evaluation frame.

---

## Evaluation Frame

For each external project, answer five questions:

1. What problem is it really solving?
2. Where is its actual leverage: runtime, methodology, onboarding, product surface, or implementation detail?
3. Which parts are durable ideas versus local stylistic choices?
4. Does it help OVP directly, indirectly, or not at all?
5. If it helps, which OVP layer should absorb the lesson?

The default rule is conservative:

- similarity in vocabulary is not evidence of relevance
- PKM aesthetics are not architecture
- prompt-heavy systems should not be mistaken for strong runtimes
- OVP should only absorb ideas that clarify product semantics, runtime contracts, or user value

---

## OVP Baseline

Before comparing any outside project, the current project should be described correctly.

`Obsidian Vault Pipeline` is not a generic note app and not a conversational memory product.

At this stage it is best understood as:

**a local-first knowledge compilation pipeline for Obsidian vaults**

It ingests source material, interprets it into structured knowledge artifacts, absorbs that output into canonical vault state, and materializes derived views and query surfaces over that state.

### Current OVP strengths

- explicit pipeline layers instead of one undifferentiated AI pass
- pack/profile architecture for domain-specific workflows
- derived `knowledge.db` and truth-aware query surfaces
- persistent queueing and operator loops
- materialized views such as object pages, topic overviews, event dossiers, contradiction views
- local UI and CLI inspection surfaces

### Current OVP weakness relative to its internal power

The system already has substantial backend/runtime strength, but its product semantics are still under-explained.

In plain terms:

- the system knows more clearly what it is doing than the user does
- the user can run commands, but may not understand the runtime contract
- the value proposition can still collapse into "a pipeline that generates many markdown files" if not explained properly

That is the main reason the `arscontexta` comparison mattered.

---

## Round 1: `agenticnotetaking/arscontexta`

### What it actually is

`arscontexta` is not primarily a note-taking system.

It is a **derivation and operating-contract product** for Claude Code. It tries to generate a complete agent-usable local knowledge system from a short onboarding conversation.

Its center of gravity is not a compiled runtime. Its center of gravity is:

- derivation logic in setup flows
- explicit kernel primitives
- three-space separation
- generated skills
- generated hooks
- methodology documentation

Relevant sources inspected in this round:

- `README.md`
- `reference/kernel.yaml`
- `reference/three-spaces.md`
- `skills/setup/SKILL.md`
- `hooks/hooks.json`
- `hooks/scripts/session-orient.sh`
- `hooks/scripts/write-validate.sh`

### What problem it is really solving

The core problem is:

**how an agent enters a local knowledge system and works inside it consistently across sessions without drifting**

That is more specific than "better note taking" and more useful than "second brain".

Its real contribution is not better storage. It is:

- startup protocol
- role and state separation
- write-time behavioral discipline
- session continuity
- methodology as runtime-visible artifact

### What is genuinely strong in it

#### 1. It treats operating methodology as a first-class product artifact

Most knowledge tools expose folders and commands.

`arscontexta` tries to expose:

- why the system is shaped this way
- what belongs where
- what to read first in a new session
- what quality constraints every write must satisfy
- how friction and contradictions are fed back into the system

This is not cosmetic. It is the part most systems leave implicit.

#### 2. It separates three kinds of state clearly

Its `self/`, `notes/`, and `ops/` split is a serious idea.

The point is not the folder names. The point is that these three things should not be conflated:

- agent identity and orientation state
- durable domain knowledge
- temporary operational coordination state

The repo is right that mixing them causes search pollution, drift, and maintenance confusion.

#### 3. It understands that discoverability is a hard requirement

Its strongest invariant is not "capture everything."

It is closer to:

**if a future session cannot find and reuse this content, the content has failed**

That is a strong design rule and a useful one.

#### 4. It productizes setup instead of assuming users can infer architecture

This is probably its highest leverage move.

It does not assume the user can translate their needs into:

- folder structure
- note schema
- command vocabulary
- maintenance policy
- automation level

It tries to derive that contract for them.

### What is not actually strong in it

#### 1. It is not a strong truth runtime

Compared with OVP, `arscontexta` is weak in:

- canonical vs derived boundaries
- explicit truth projection
- structured evidence and contradiction models
- compiled derived views
- operator-grade query surfaces

Much of its power is encoded in instructions, conventions, and hooks rather than in a more explicit runtime model.

#### 2. It is not a backend architecture reference for OVP

OVP already has stronger machinery in this area:

- `src/openclaw_pipeline/truth_store.py`
- `src/openclaw_pipeline/autopilot/queue.py`
- `src/openclaw_pipeline/commands/ui_server.py`
- pack/profile contracts documented in `README.md` and `docs/pack-api/README.md`

So `arscontexta` should not be used as a model for OVP's backend direction.

#### 3. Its large generated command surface is not inherently desirable

Generating many commands, hooks, and skills from day one may be appropriate for its product shape.

That does not automatically mean OVP should do the same.

For OVP, this could easily create noise, maintenance burden, and conceptual inflation.

---

## What `arscontexta` Can Actually Help OVP With

Only two areas appear meaningfully relevant.

### 1. Derivation / onboarding layer

OVP currently has strong internal architecture, but weak front-door semantics.

Users should not have to infer:

- what kind of system this is
- what a pack/profile means in practice
- what canonical vs derived means
- what happens when a source enters the pipeline
- where trust boundaries are

The lesson from `arscontexta` is not "generate a vault."

The lesson is:

**compile an operating contract for the user before expecting them to use the runtime correctly**

For OVP, that could mean a lightweight onboarding or operator briefing layer that states:

- what kind of source is entering
- which workflow will process it
- what the runtime is allowed to infer
- what can abstain
- what becomes canonical
- what remains derived
- where the user should go next to inspect results

### 2. Runtime methodology layer

OVP needs a clearer explanation of how the runtime transforms knowledge.

This is not code documentation.

It is the user-facing statement of:

- what each pipeline layer is for
- which layers are interpretive
- which layers are deterministic
- which outputs are authoritative
- which outputs are rebuildable
- how review and correction flow back into the system

This is the strongest transferable idea from `arscontexta`.

Not its implementation.

Its insistence that a system should reveal its own method of operation.

---

## Why Users Should Care About OVP Runtime

Users do **not** need to care about internal implementation details such as SQLite, process orchestration, or internal Python modules.

Users **do** need to care about what the runtime is allowed to do on their behalf.

That includes:

- why a given source produced a particular deep dive
- whether the system abstained or inferred
- whether an artifact is canonical or derived
- where a contradiction came from
- where to correct the system when it is wrong
- which views are explainers versus which views are authoritative state

So the user-facing runtime question is not:

> how is the runtime implemented?

It is:

> what judgments does the runtime make, under what rules, and how can I inspect or correct them?

That is the product-level importance of runtime.

Without this, OVP risks being perceived as a black-box markdown generator.

---

## What OVP Runtime Methodology Is

The cleanest definition from this round:

**Runtime methodology is the constitution for how source material becomes knowledge inside OVP.**

It defines:

- what enters the system
- what interpretive transformation is allowed
- what must remain deterministic
- what may abstain
- what counts as canonical state
- what counts as derived state
- how maintenance and review feed back into the system

For OVP, the current six-layer framing is already close to the right answer:

- Ingest
- Interpret
- Absorb
- Refine / review operations
- Canonical maintenance
- Derived views and indexes

The missing piece is not architecture. The missing piece is product articulation.

Users should be able to understand, in one pass:

- what each layer does
- what it does not do
- why that separation exists

---

## Draft Product Semantics For OVP

This is the first draft of the product definition that emerged from this round.

### What goes in

The user gives OVP source material:

- web articles
- GitHub repositories or documentation
- papers and PDFs
- clippings
- raw markdown source notes

### What OVP does

OVP does **not** merely summarize.

It compiles source material through a staged knowledge process:

1. normalize and classify source input
2. interpret source into structured deep-dive artifacts when evidence supports doing so
3. absorb durable findings into canonical vault state
4. maintain registries, aliases, Atlas/MOC relationships, and object identity
5. build derived query surfaces and review surfaces over the resulting state

### What OVP will not do

OVP should be explicit that it does not:

- treat every generated artifact as source of truth
- silently upgrade weak interpretation into canonical knowledge
- treat `knowledge.db` as canonical truth
- hide contradictions behind fake consensus
- require the user to inspect raw implementation state to trust outputs

### What comes out

The user does not merely get files.

The user gets a local knowledge space with multiple consumption surfaces:

- deep-dive notes
- evergreen/canonical notes
- topic overviews
- object pages
- event dossiers
- contradiction views
- search/truth query surfaces
- review queues and maintenance signals

### What the user ultimately gets

The core user benefit is:

**a continuously compiled research memory**

This means the user can ask:

- what do we currently know about this topic?
- what objects, events, or themes matter here?
- where do sources agree or conflict?
- what changed recently?
- what should be reviewed next?
- where did a given conclusion come from?

That is much stronger than "automatic note organization."

The product is closer to:

**a local-first knowledge compiler and inspection workbench for serious knowledge work**

---

## Working Conclusion From Round 1

`arscontexta` is relevant to OVP, but only at the front-door and contract-explanation layer.

It is **not** a reference backend.

It is useful because it forces a sharper question:

> can a user and a future agent understand how this system should be operated without reverse-engineering the codebase?

OVP does not need to copy its file layout, prompt structure, or command sprawl.

OVP should only consider absorbing the following principles:

- make the runtime contract visible
- explain what state is canonical versus derived
- separate durable knowledge from operational state
- make discoverability and inspectability hard product requirements
- give users a clear statement of what the system is compiling and why

---

## Next Rounds

When additional external projects are added, each round should include:

### Snapshot

- project name
- what it claims to be
- what it actually is

### Real leverage

- onboarding
- runtime
- methodology
- data model
- review workflow
- UI/product surface

### Relevance to OVP

- direct help
- indirect help
- no help

### Absorption boundary

- should absorb
- should reinterpret
- should explicitly reject

---

## Round 2: “横纵分析法” / HV Analysis

### What it actually is

This is not a software architecture and not a knowledge runtime.

It is a **research framing and report-generation method** for rapidly orienting around an unfamiliar object with AI assistance.

The article's core claim is simple:

- use a **vertical axis** to reconstruct the object's evolution over time
- use a **horizontal axis** to compare its present position against adjacent alternatives
- intersect the two to form an initial judgment

The surrounding packaging matters too:

- AI is used to gather, compress, and draft quickly
- the output is intentionally long-form and readable
- the report is treated as a starting map, not a final conclusion
- the framework is designed to adapt to different object types: product, company, concept, person, event

This round includes both:

- the public article framing
- the actual open-sourced repository implementation in `KKKKhazix/khazix-skills`

Relevant sources inspected in this round:

- repository `README.md`
- `prompts/横纵分析法.md`
- `hv-analysis/SKILL.md`
- `hv-analysis/scripts/md_to_pdf.py`

### What problem it is really solving

The real problem is not "how to know everything."

It is:

**how to get from zero familiarity to a coherent first-pass mental model fast enough that curiosity can keep moving**

That is a real problem.

Most people fail before deep research begins because:

- the search space is too wide
- they do not know which questions to ask first
- they drown in raw sources before a map exists

The method's value is that it supplies a reusable first-pass question scaffold.

### What is genuinely strong in it

#### 1. It separates orientation from deep research

This is the most valuable idea in the piece.

The report is not presented as final truth. It is presented as:

- a quick orientation artifact
- a cognitive map
- a frame for selecting what deserves deeper manual follow-up

That is the right product promise.

#### 2. It gives users a stable interrogative scaffold

The practical value is not "纵向 + 横向" as terminology.

The practical value is:

- when faced with something unfamiliar, the user no longer starts with a blank page
- the first set of questions is already structured
- AI can then execute inside that frame

This reduces activation energy substantially.

#### 3. It understands that object type should change the template

The article correctly notes that products, companies, concepts, and people should not be researched in exactly the same way.

That is important.

A useful research framework is not one universal prompt with different nouns pasted into it.

It is a stable meta-structure with type-sensitive emphasis.

#### 4. It optimizes for readable synthesis, not just source scraping

This also matters more than it sounds.

Many AI research outputs fail not because they lack information, but because they fail to produce a report a human can actually consume quickly.

The article is right to care about:

- legibility
- narrative continuity
- easy entry into the topic

#### 5. The skill operationalizes the method into an actual workflow

This is the most important upgrade over the article alone.

The repository's `hv-analysis` skill does not just describe the method.
It turns it into a reproducible agent workflow:

- mandatory web research rather than relying on model memory
- explicit split between longitudinal and cross-sectional collection tasks
- optional parallel subagent collection
- arXiv API guidance for academic/technical topics
- type-aware adaptation by object class
- strong writing-style instructions for report readability
- Markdown-to-PDF conversion via a real script (`md_to_pdf.py`)

That means the project is not only a "prompt idea." It is a packaged research pipeline for agent environments.

### What is weaker than it appears

#### 1. The underlying theory is older and simpler than the branding suggests

The article is honest about this, which is good.

The method is basically a repackaging of:

- longitudinal / diachronic analysis
- cross-sectional / synchronic analysis
- strategic comparison

That is not a criticism.

But it means the durable value is not novel theory. It is productization of a simple, portable frame.

#### 2. Horizontal comparison can become shallow if left unchecked

The "horizontal axis" is useful, but it can easily degrade into:

- competitor grids
- concept adjacency lists
- superficial feature comparisons

Without stronger evidence and claim discipline, the horizontal section can become plausible-but-thin synthesis.

#### 3. It does not itself solve verification, contradiction handling, or truth ownership

The method is optimized for fast orientation, not for canonical knowledge maintenance.

It does not tell you:

- which claims become authoritative
- how contradictions are managed
- how provenance is preserved at system scale
- how reports evolve when later evidence conflicts with early framing

That is the main reason it is relevant to OVP only indirectly.

#### 4. It produces a report, not a durable knowledge structure

Its output format is a readable narrative report.

That is useful.

But it is still different from a system that maintains:

- object-level truth
- source-grounded claims
- evolving contradiction state
- stable derived views

So its best use is as a front-end orientation layer, not as a replacement for a deeper knowledge system.

#### 5. The skill is operationally heavier than the article suggests

The actual `hv-analysis` skill assumes quite a lot from the host environment:

- web search and page fetch capabilities
- optional subagent support
- external `curl` access to arXiv
- Python environment with `weasyprint` and `markdown`

So while the method is conceptually light, the packaged skill is fairly opinionated and environment-dependent.

This is not a flaw by itself, but it means:

- the portability of the idea is higher than the portability of the exact skill
- the workflow should be separated mentally from the specific implementation wrapper

---

## What HV Analysis Can Actually Help OVP With

This round is relevant, but only in a narrow and useful way.

### 1. It sharpens the idea of an orientation artifact

OVP already has machinery for:

- ingesting source
- interpreting it
- absorbing it
- deriving views

What this method adds is a clearer product idea for a specific early output:

**a first-pass orientation report**

This is a report whose job is not to finalize truth, but to help the user quickly understand:

- what this thing is
- how it got here
- what it is adjacent to
- where its present position comes from
- what deserves deeper inspection next

That is a distinct output category and a useful one.

### 2. It suggests a stronger "map first, deepen second" workflow

The article explicitly recommends:

1. generate a broad, structured map
2. read it quickly
3. identify doubts and areas of interest
4. dig deeper only where the map indicates value

OVP can use this pattern.

That does not require changing core architecture.

It only requires naming a workflow clearly:

- orientation pass
- verification/deepening pass
- absorption/review pass

### 3. It reinforces type-sensitive research profiles

This part aligns well with OVP's pack/profile direction.

A product, company, concept, event, or person should expose different briefing shapes.

That suggests OVP could benefit from lightweight "orientation profiles" that determine:

- which timeline signals matter
- what counts as horizontal comparison
- what adjacent entities to include
- which open questions should end the report

### 4. It clarifies that reports are for cognition, not just storage

This is the strongest philosophical overlap with OVP.

The article's real promise is:

**give me something I can read once and become meaningfully less confused**

That is a valid product surface.

OVP should care about this because many strong knowledge systems fail to provide an easy entry artifact for a human operator.

### 5. It shows how a research recipe can become a user-facing product surface

The open-sourced skill makes one more useful point:

users often do not want a "framework."
they want a command that produces a useful artifact.

That matters for OVP.

If OVP adopts any version of this lesson, it should not stop at abstract method language.
It should define:

- what the user invokes
- what artifact is generated
- what sources it uses
- what confidence/limitations are attached
- what the next action is after reading it

---

## What OVP Should Not Absorb From It

### 1. Do not treat HV analysis as a new core ontology

This is a research lens, not a canonical data model.

OVP should not reorganize its runtime around "horizontal" and "vertical" as first-class ontology primitives.

That would be overfitting a report style into the engine.

### 2. Do not mistake a readable long report for authoritative knowledge

A polished narrative can be psychologically stronger than its evidence base.

OVP should keep the distinction sharp:

- orientation report: useful, readable, exploratory
- canonical knowledge: stricter, slower, source-grounded

### 3. Do not absorb its optimism about AI research quality without stronger guards

The article includes its own caution, correctly.

OVP should preserve that caution and go further:

- reports can orient
- reports should not silently promote themselves into canonical truth
- any later absorption must remain source-grounded and reviewable

---

## Recommended Absorption Boundary For OVP

The cleanest place to absorb this round's lesson is:

**not in core runtime**

but in:

- operator-facing research recipes
- orientation report exports
- type-sensitive deep-dive templates
- product semantics for "what the first useful output is"
- possibly a small exported report format, but only if it is grounded in OVP's existing provenance and truth boundaries

### Recommended interpretation

OVP can reinterpret HV analysis into a more precise internal form:

- **time axis** -> historical evolution and key transitions
- **landscape axis** -> present adjacency, alternatives, and position
- **evidence axis** -> supporting sources, provenance density, confidence
- **tension axis** -> contradictions, ambiguities, missing information

This would be a stronger OVP-native version than simply copying the article's framing.

In other words:

OVP should borrow the user-facing entry shape,
but enrich it with the truth/review discipline the article does not provide.

---

## Provisional Product Opportunity For OVP

This round suggests a concrete surface OVP could plausibly own in the future:

### "Orientation Brief"

A compiled local-first report that answers:

- what is this object?
- how did it develop over time?
- what is it adjacent to right now?
- what are the key differentiators or tensions?
- what evidence supports that view?
- what needs deeper verification next?

This would be especially useful for:

- products
- companies
- protocols
- concepts
- current events

Its role would be:

- fast comprehension
- source-guided curiosity expansion
- entry into deeper OVP surfaces

Its role would **not** be:

- final truth
- automatic canonical promotion
- contradiction resolution

That separation is important.

---

## Working Conclusion From Round 2

HV analysis is useful because it productizes a good first-pass research habit:

**build a map quickly, then decide where depth is worth paying for**

For OVP, this is relevant as a report/workflow/product-surface lesson.

It is not a runtime lesson and not a knowledge-model lesson.

The most useful absorption is:

- make orientation outputs explicit
- keep them distinct from canonical knowledge
- support object-type-sensitive briefing shapes
- let those briefings lead users into deeper source-grounded OVP surfaces

The repository implementation strengthens this conclusion:

- the method is real enough to package
- the packaging is useful
- but the artifact is still best understood as a high-quality orientation workflow, not a knowledge runtime

---

## Cross-Round Synthesis So Far

After Round 1 (`arscontexta`) and Round 2 (`HV Analysis` / `khazix-skills`), the pattern is clearer.

Two kinds of external inspiration matter to OVP:

### Type A: Operating-contract systems

Example:

- `arscontexta`

These systems matter because they clarify:

- how a user or agent enters the system
- what belongs where
- what the runtime is allowed to do
- how session continuity and maintenance are handled

Their main value to OVP is:

- onboarding semantics
- runtime methodology explanation
- state-boundary clarity

They are useful at the **front-door contract layer**, not as backend/runtime architecture references.

### Type B: Orientation-report systems

Example:

- `HV Analysis`
- `khazix-skills/hv-analysis`

These systems matter because they clarify:

- how to get from zero familiarity to a usable first map quickly
- how to turn a research recipe into a user-facing artifact
- how to separate orientation from deeper verification and knowledge maintenance

Their main value to OVP is:

- report/export patterns
- operator-facing research recipes
- object-type-sensitive briefing formats

They are useful at the **entry artifact / workflow surface layer**, not as canonical knowledge models.

### What still appears missing in OVP itself

Across both rounds, the same OVP gap keeps showing up:

OVP is already strong at:

- knowledge compilation
- truth/derived separation
- operator surfaces
- pack/profile architecture

But it is still weaker than it should be at:

- explaining itself to new users
- making the first useful output obvious
- telling the user what to read first
- distinguishing clearly between "orientation output" and "canonical knowledge"

### Provisional product direction implied by both rounds

If OVP absorbs anything from these discoveries, the likely additions are:

1. **A clearer operator/runtime contract**
   This explains what the pipeline does, what it does not do, and how to inspect or correct it.

2. **An explicit Orientation Brief surface**
   This gives users a fast, readable, source-aware first-pass map of an object or topic before they move into deeper OVP surfaces.

3. **A stronger handoff between orientation and truth**
   Orientation outputs should not silently become canonical, but should point cleanly into:
   - deep dives
   - object pages
   - topic overviews
   - contradiction/review surfaces

### Current rejection rules

Based on the first two rounds, OVP should continue rejecting these temptations:

- copying prompt-heavy systems as if they were runtime architecture
- mistaking good writing for truth management
- promoting orientation artifacts into canonical knowledge without stricter grounding
- importing external vocabulary directly into core ontology

### Current working thesis

The most promising external ideas for OVP are not "better note-taking" ideas.

They are ideas that improve one of two things:

- **how the system explains and constrains itself**
- **how the system gives users a first high-value artifact quickly**

That is the filter future rounds should continue using.

---

## Round 3: “Creating a Second Brain with Claude Code”

### What it actually is

This is not a new research method in the same sense as Round 2, and it is not a generic operating-contract system in the same sense as Round 1.

It is best understood as a **personal deployment blueprint for a local AI-assisted work memory system**.

Its main components are:

- a large raw corpus of personal work history
- local indexing via QMD
- a personal profile document (`me.md`)
- a distilled summary layer between self and raw corpus
- tool connectors for the operator's stack
- automatic prompt-time context injection via hooks
- a multi-timescale learning loop

It is much more concrete than a philosophy post.

It is essentially saying:

**here is how to operationalize a personal "second brain" around Claude Code using local retrieval, lightweight memory files, and recurring summarization**

Relevant material inspected in this round:

- local clipping note: [Creating a Second Brain with Claude Code.md](/Users/chris/Documents/openclaw-vault/Clippings/Creating%20a%20Second%20Brain%20with%20Claude%20Code.md)

### What problem it is really solving

The core problem is not note-taking.

It is:

**how a knowledge worker with too much historical context and too many live tools can recover recall, continuity, and follow-through without relying on biological memory alone**

This is a narrower and more practical problem than the broad "second brain" label suggests.

More concretely, the system is meant to reduce:

- recall latency
- meeting-prep overhead
- missed follow-ups
- tool/context fragmentation
- repetition of known mistakes

That is why it feels more like a work augmentation system than a knowledge ideology.

### What is genuinely strong in it

#### 1. It is staged and test-driven

This is one of the most useful properties in the piece.

The workflow is explicitly broken into phases:

- create self profile
- gather and index raw corpus
- distill summaries
- wire context injection
- create learning loops

And it insists on testing each phase before moving on.

That sounds simple, but it is a real strength.

Many "second brain" writeups jump from aspiration to magic. This one actually names an incremental build path.

#### 2. It inserts a distilled layer between the self and the raw corpus

This is probably the most important architectural idea in the post.

The author does not rely only on:

- raw documents
- direct retrieval at prompt time

He also creates an intermediate summary/context layer from the corpus, shaped by a `me.md` profile.

That is useful because raw corpus retrieval alone is often too noisy, while hand-written summaries alone go stale.

This middle layer is effectively a compiled operator briefing layer.

#### 3. It uses hybrid retrieval instead of one search mode

The explicit split between:

- semantic/vector search
- keyword/BM25 search

is a strong practical insight.

This is not novel in IR terms, but it is important in real workflows.

Acronyms, proper nouns, and exact metrics often require lexical matching.
Broader topical questions often require semantic retrieval.

Treating both as complementary is correct.

#### 4. It makes context injection a default behavior, not a manual task

This is where the system becomes operational rather than aspirational.

The hook model means retrieval does not depend on the user remembering to ask for context.

That is a real shift:

- the memory system becomes ambient
- the default prompt becomes richer
- "lazy jargon" from the human can still map into useful context

This is one of the clearest practical bridges between retrieval and day-to-day agent use.

#### 5. It defines learning loops at multiple time scales

The per-session / per-day / per-month split is strong.

It recognizes that not all learning belongs in the same cadence:

- session-level: tool errors, workflow corrections, one-off learnings
- daily/weekly: briefs, activity tracking, active context refresh
- monthly: pattern review, retrospective, strategic correction

This is a useful decomposition and more concrete than generic "the system learns over time."

### What is weaker than it appears

#### 1. The truth boundaries are weak

This is the main structural weakness.

The article combines:

- raw documents
- self-description
- derived summaries
- injected retrieval snippets
- learned memory updates

but does not define a strong contract for:

- what is authoritative
- what is inferred
- what is temporary
- what can be safely rewritten
- how conflicting summaries are resolved

That is acceptable for a personal productivity system.

It is not sufficient as a more formal knowledge runtime.

#### 2. The system can easily reinforce operator bias

The post acknowledges that the system is both highly knowledgeable and highly biased by the operator's own history.

That is not a side note. It is central.

A system built from:

- your documents
- your reviews
- your summaries
- your goals

can become an amplifier of your existing worldview unless it has explicit tension and contradiction handling.

This is where OVP's review/contradiction surfaces are structurally stronger.

#### 3. The "learn" loop risks becoming prompt sediment

The session learning mechanism sounds useful, but without harder constraints it can drift into:

- stale heuristics
- duplicated lessons
- overfit tool workarounds
- long-lived accidental preferences

This is a common failure mode in memory-file systems.

It is not fatal, but it means the memory layer itself needs maintenance.

#### 4. It is a deployment recipe, not a portable architecture

Like Round 2, the exact implementation assumes a specific environment:

- Claude Code
- hooks
- QMD
- connector or API access to external tools
- local scripts and scheduled jobs

So again:

- the idea is more portable than the exact implementation
- the pattern matters more than the specific prompt

---

## What This Can Actually Help OVP With

This round is more relevant than it first appears, but again not at the core truth-model layer.

### 1. It offers a concrete user archetype for OVP

This is valuable.

The article describes a real high-context operator:

- many documents
- many tools
- many meetings
- lots of latent historical knowledge
- poor recall under load

That is exactly the kind of user for whom a local-first knowledge compilation system is compelling.

So even if OVP does not copy the architecture, the post helps define a user story:

**OVP is useful when the user's historical context exceeds their working-memory budget**

That is a much sharper framing than generic PKM language.

### 2. It validates the importance of a compiled context layer

The post's strongest architectural move is the "distill" layer between `me.md` and the raw corpus.

This maps well onto OVP thinking.

OVP already believes in:

- canonical state
- derived state
- materialized views

This post reinforces that a user-facing system often needs:

**a compiled context layer that sits between raw source and direct prompt-time retrieval**

That does not mean copying the author's folder names.

It means taking seriously the idea that retrieval alone is not enough.

### 3. It shows a practical ambient-entry pattern: retrieval via hooks

This is probably the most directly reusable product pattern in the article.

Users often will not:

- search manually
- open the right view first
- name the right entities precisely

So ambient context injection, when done carefully, can improve the usefulness of the system dramatically.

OVP does not need to copy the exact hook implementation, but it should pay attention to the interaction model:

- user writes normal prompt
- system enriches with relevant local context
- richer response becomes the default experience

### 4. It suggests that "memory" should be multi-speed

The session / day / month cadence split is useful for OVP as a design thought.

OVP already has operator loops, but this article sharpens a more human-facing framing:

- fast loop: what just happened and what should change now
- medium loop: what is active and what deserves today's attention
- slow loop: what patterns are emerging and what should be reoriented

This is more a product/ops pattern than a data-model pattern, but it is useful.

---

## What OVP Should Not Absorb From It

### 1. Do not collapse OVP into a personal memory file system

The article is deeply personal by design.

OVP should not overfit around:

- one user's self-profile
- mutable personal memory files as the primary system backbone
- tool-specific glue as the product center

Those are appropriate for this workflow and still too narrow for OVP's broader knowledge pipeline identity.

### 2. Do not weaken canonical/derived discipline in the name of convenience

The article's system gets value from fluidity.

OVP gets value from clearer boundaries.

That distinction should be preserved.

### 3. Do not confuse "recall augmentation" with "knowledge compilation"

This post is strongest as a recall and productivity augmentation system.

OVP's opportunity is larger:

- not only remembering
- but compiling, reviewing, tracing, and surfacing knowledge

The overlap is real, but the scopes are different.

---

## Recommended Absorption Boundary For OVP

This round's useful absorption boundary is:

- user archetypes
- context-layer design
- ambient retrieval patterns
- multi-timescale operator loops

Not:

- core ontology
- truth ownership model
- personal-memory-first architecture

### Best OVP-native reinterpretation

The cleanest reinterpretation would be:

- **profile/context overlay** rather than a universal `me.md` assumption
- **compiled briefings** rather than loose mutable memory summaries
- **source-aware ambient enrichment** rather than opaque memory injection
- **reviewable learning loops** rather than free-form memory accretion

This would preserve the post's practical strengths while avoiding its weaker truth guarantees.

---

## Working Conclusion From Round 3

This article is useful because it shows how local retrieval, context distillation, and recurring learning loops can become a real daily work system rather than a toy demo.

Its strongest ideas are:

- staged bootstrap
- distilled context layer
- hybrid retrieval
- ambient context injection
- multi-speed learning loops

For OVP, it is most valuable as:

- a user-story reference
- a context-layer/product-surface reference
- a reminder that the best system is often the one that quietly helps on every prompt

It is not a substitute for stronger truth/derived boundaries, and it should not be treated as a core knowledge architecture model.

---

## Round 4: `alivecontext/alive`

### Short answer

Yes.

`alivecontext/alive` is meaningfully a continuation of the ideas in `Creating a Second Brain with Claude Code`, but it is not just a rhetorical supplement.

It is a more explicit **context runtime** with:

- a defined world model
- stricter read/write contracts
- a save/load protocol
- projection discipline
- multi-session coordination hooks

The article describes a useful personal deployment pattern.

`alive` turns that pattern into a portable Claude Code plugin runtime.

### What it actually is

`alive` presents itself as a **Personal Context Manager for Claude Code**, but the important part is not the marketing phrase.

Its real shape is:

**a file-backed context operating system for repeated AI sessions**

The core model is unusually explicit:

- the root is a `world`, identified by `.alive/`
- each meaningful context object is a `walnut`
- each walnut has a `_kernel/`
- each deliverable or scoped workstream is a `bundle`
- the agent runtime is a `squirrel`

The repo is not vague about this. It says directly in `world.md`:

**the file system is the methodology**

That is the right sentence to take seriously.

### What problem it is really solving

The problem is not merely "remember more things."

It is:

**how an agent repeatedly enters a personal context world, loads the right amount of state, works without losing transient discoveries, and saves back into a stable structure without corrupting that structure**

That is a stronger and more operational problem statement than the earlier article.

The article mostly explained:

- how to bootstrap a useful local AI work-memory system
- how to distill context
- how to inject context ambiently
- how to maintain learning loops

`alive` adds the missing runtime rigor:

- what is loaded first
- what should not be loaded yet
- what gets written first
- what the agent may never write directly
- how projections are recomputed
- how concurrent sessions are handled

### What is genuinely strong in it

#### 1. It has a real projection discipline

This is the best idea in the repo.

The walnut model splits source files from computed state:

- `_kernel/key.md` for identity
- `_kernel/log.md` for signed prepend-only history
- `_kernel/insights.md` for standing knowledge
- `_kernel/tasks.json` for queue state
- `_kernel/now.json` for computed current state

Crucially, `project.py` computes `now.json`, and the skill instructions explicitly say the agent must never write `now.json` directly.

That is important because it keeps:

- authored judgment
- operational queue state
- generated snapshot state

separate from each other.

This is one of the cleanest ideas we have seen across all rounds.

#### 2. It treats save/load as protocol, not convenience

The `alive:load-context` and `alive:save` skills are not just helper commands.

They define a concrete runtime contract:

- load a brief pack first
- resolve people context automatically
- offer deeper bundle loading only when needed
- write the log first on save
- route tasks through scripts
- update bundle manifests separately
- compute projections after writes

This is much stronger than the article's more free-form "assemble your second brain" posture.

#### 3. It has a good distinction between unit of context and unit of work

This is another strong idea.

- `walnut` = unit of context
- `bundle` = unit of work

That separation matters.

Many systems mix "the thing itself" with "the work currently being done about that thing." `alive` keeps them distinct.

That gives it a cleaner model for:

- ongoing entities
- scoped deliverables
- evergreen accumulation
- graduation and archive

#### 4. It handles session drift and multi-session interference explicitly

The `alive-context-watch.sh` hook does more than reminder text.

It monitors:

- context-window thresholds and reinjects rules/context
- unsaved active squirrel stashes from other sessions
- walnut file changes made by another session

This is one of the first systems in this comparison set that treats concurrent agent activity as a first-class problem instead of pretending one agent/session exists at a time.

#### 5. It is more operationally honest than the article

The article is good at user empathy and staged adoption.

`alive` is better at saying:

- here is the shape of the world
- here is the protocol
- here is what the agent must read
- here is what the agent must not touch

That honesty is useful.

### What is weaker in it

#### 1. It is still a context runtime, not a truth system

Even though `alive` is stricter than the article, it still does not solve the same class of problem OVP solves.

It is weaker than OVP in:

- canonical truth modeling
- evidence/claim structure
- contradiction handling
- rebuildable knowledge views
- compiled query surfaces over a more explicit knowledge graph

It manages context state well.

It does not offer a stronger knowledge compilation model than OVP.

#### 2. Its ontology is highly local and metaphor-driven

`world`, `walnut`, and `squirrel` are coherent inside the product, but they are also a branded local ontology.

Some of that is good product design.

Some of it is irreducibly product-specific and should not be mistaken for a durable conceptual primitive.

#### 3. It is optimized for one person's AI operating environment

This is not a flaw for the product, but it matters for transfer.

The repo assumes:

- a personal world
- repeated sessions with one human
- personal projects and people as core units
- a strong Claude Code plugin/hook environment

OVP has overlap with that, but it should not inherit the whole worldview.

### Is it a supplement to the previous article?

Yes, and in a specific way.

The previous article gave a **deployment blueprint** for a local work-memory system.

`alive` gives a **runtime specification** for one version of that blueprint.

The clean comparison is:

- article: why this style of system is useful in daily knowledge work
- `alive`: how to operationalize that style into files, hooks, save/load contracts, and session state

So the relationship is not "same thing twice."

It is:

**the article supplies the human-facing rationale, while `alive` supplies the runtime mechanics**

### What can actually help OVP

#### 1. Projection discipline

This is the most transferable idea.

OVP already has strong canonical/derived thinking. `alive` reinforces one useful pattern:

- source-of-truth files are authored
- runtime snapshot files are generated
- agents do not edit projections directly

That principle is fully compatible with OVP.

#### 2. A more explicit load/save protocol for agent-facing work

OVP currently has pipeline power but still under-specifies how an agent or operator should:

- enter a context
- see the minimum sufficient state
- defer deeper loading until needed
- checkpoint changes back into the system

`alive` is good evidence that this layer can be made explicit and useful.

#### 3. Separation between durable context object and scoped work package

OVP already has objects, dossiers, and views, but `alive` sharpens an operational distinction:

- the durable thing
- the active work package around that thing

This may map well to OVP if expressed in OVP-native terms, not copied literally.

#### 4. Runtime protection against drift

The hook system around stale context, external changes, and reinjection is more mature than the earlier article and points to a real design question for OVP:

**how does the system keep agents from working on stale or over-assumed context?**

That question matters.

### What OVP should not absorb

#### 1. Do not import the branded ontology whole

OVP does not need walnuts and squirrels.

It needs the underlying contract, not the metaphor layer.

#### 2. Do not collapse OVP into a personal context manager

`alive` is strongest when used as a persistent personal AI operating environment.

OVP should stay centered on:

- source ingestion
- interpretation
- absorption
- canonical/derived boundaries
- queryable knowledge products

not just personal recall augmentation.

#### 3. Do not mistake session state for canonical knowledge

`alive` is careful about this inside its own runtime, but because it is context-heavy, there is always a risk that operational state starts to feel like durable truth.

OVP should remain stricter here.

### Working conclusion from Round 4

`alivecontext/alive` is the strongest follow-on we have seen to the "Second Brain with Claude Code" article because it turns the article's intuitions into explicit runtime machinery.

Its most valuable lessons for OVP are:

- projection discipline
- save/load as protocol
- context object vs work package separation
- stale-context and multi-session safeguards

It is not a replacement for OVP's truth model.

It is better understood as:

**a well-specified personal context runtime that can inform OVP's agent-facing operating contract**

## Round 5: `getzep/zep`

### Short answer

`Zep` 值得看，但它和前面几轮不是同一类东西。

它不是本地知识系统，不是 Obsidian 工作流，也不是研究型知识编译器。

它更准确地说是：

**一个面向 agent 的 context engineering / memory service**

而且需要先看清一个现实：

**今天这个 GitHub 仓库本身并不是 Zep 完整产品源码。**

当前仓库主要是：

- examples
- integrations
- SDK usage patterns
- MCP server
- 已废弃的 Community Edition 放在 `legacy/`

官方已经在 2025-04-02 公告里说明，停止维护 Zep Community Edition，开放源码重心转向 `Graphiti`。因此研究 `getzep/zep`，本质上是在研究：

- Zep Cloud 的产品/接口形态
- Graphiti 驱动的 context graph 思路
- 以及它给开发者提供的 agent context assembly surface

### What it actually is

官方首页和文档的表述已经很一致：

- ingest chat history, business data, documents, app events
- build a temporal context graph
- retrieve and assemble prompt-ready context

这点在官网的三段式里非常明确：

- ingest
- graph
- assemble

它交付给开发者的核心不是“一个长期可维护的知识库”，而是：

**在 agent 响应前，快速给出一段已经组装好的、和当前 query 高度相关的上下文块**

这跟 OVP 的重心很不一样。

### What problem it is really solving

`Zep` 真正在解决的是：

**生产环境 agent 的上下文供给问题**

更细一点，是这几个痛点：

- 仅靠 chat memory 不够
- static RAG 太慢也太 stale
- tool calling 不稳定，LLM 经常不知道该查什么
- 业务数据、用户历史、线程消息散在不同系统里

所以 Zep 的核心产品承诺不是“建立更好的知识”，而是：

**在低延迟下，把跨来源的用户/业务/对话信息组装成 agent 此刻该看的上下文**

这就是它的本质。

### What is genuinely strong in it

#### 1. 它把 “context assembly” 当成独立产品层

这一点很重要。

很多系统只做到：

- 存一点 memory
- 提供一个 search API
- 剩下 prompt 拼接交给开发者自己做

`Zep` 不是这样。

它明确把产品层定义成：

- 你先把数据灌进来
- 系统自动形成 temporal context graph
- 再返回一个 pre-formatted context block

文档里甚至把三种装配方式写得很清楚：

- default context block
- context templates
- advanced construction

这说明它卖的不是底层存储，而是 **context delivery surface**。

#### 2. 它对 “temporal facts” 的处理是认真的

无论是官网、迁移文档还是 legacy/Graphiti 接口，都反复强调：

- facts 有有效时间和失效时间
- 新数据会让旧事实 invalidated
- retrieval 不只是“找到相关片段”，而是“理解事实随时间如何变化”

这和普通 memory 产品差异很大。

它不是简单的“记住用户喜欢 Adidas”，而是能表示：

- 以前喜欢 Adidas
- 后来鞋坏了
- 现在打算改穿 Nike

这个 temporal state model 是它的关键价值之一。

#### 3. 它对开发者非常重视默认路径

`thread.get_user_context()` 这条路径说明它很清楚主流开发者要什么：

- 不想自己查图
- 不想自己拼 prompt
- 希望“加消息，然后直接拿上下文”

`return_context=True`、context templates、自动 relevance detection，这些都在服务一个目标：

**让 context engineering 成为一个开发者能在几行代码里接上的产品能力**

#### 4. 它把 user graph 和 domain graph 的架构模式讲清楚了

`architecture-patterns` 文档这一点做得不错。

它没有只讲一个场景，而是明确区分：

- conversation + user context
- standalone domain graph
- 两者组合
- tool-call retrieval vs per-turn retrieval

这说明它对“persistent context architecture”是有明确方法论的，而不是只卖一个 API。

### What is weaker in it

#### 1. 它不是 knowledge compilation system

这点必须说清楚。

`Zep` 的核心输出是：

- context block
- graph search result
- user summary
- facts / entities / episodes

它没有像 OVP 那样强调：

- canonical vault state
- materialized knowledge artifacts
- object pages / topic overviews / event dossiers
- operator-facing review loops
- contradiction surfaces as first-class products

所以它更像 **serving layer**，不是 **knowledge compilation layer**。

#### 2. 它的仓库已经不再代表完整开源内核

这不是小事。

当前 `getzep/zep` 这个仓库主要反映：

- 云产品的接入方式
- 示例工程
- 旧社区版的遗留代码

而不是一个你可以完整研究并继承的开源主干。

如果要研究更底层的图谱机制，应该看 `Graphiti`，不是把 `getzep/zep` 本身误读成核心 runtime 仓库。

#### 3. 它天然偏向在线 agent serving，不偏向本地知识工作

`Zep` 最强的场景是：

- customer support agents
- sales agents
- assistants with user history
- app/event/CRM + chat 的联动场景

这和 OVP 面向的本地知识处理、文章/资料吸收、常青知识组织，重心并不相同。

### What it can actually help OVP with

#### 1. Clarify the difference between “knowledge compilation” and “context serving”

这轮最大的启发不是具体实现，而是概念边界。

`Zep` 很清楚自己在做 serving：

- retrieve
- format
- deliver

OVP 则更偏 compile：

- ingest
- interpret
- absorb
- derive
- review

把这两个层次分清很重要。

这会帮助 OVP 避免把自己误包装成另一个 agent memory API。

#### 2. Pre-formatted context block 作为产品面是值得借的

虽然 OVP 不该变成 Zep，但 Zep 有一个明确长处：

**它知道开发者不想自己拼上下文。**

所以它把“context block”做成正式产物。

对 OVP 来说，这启发的不是底层图，而是上层 artifact：

- briefing block
- object brief
- topic brief
- agent-ready context export

也就是：

**把 OVP 的一部分输出做成更直接可消费的 prompt-ready surface**

#### 3. Temporal invalidation 是值得继续关注的

OVP 已经有 truth / evolution / contradiction 的方向，但 `Zep` 再次提醒了一件事：

**上下文不是只会累积，也会失效。**

这件事如果做得好，会帮助 OVP 更明确地区分：

- 当前成立
- 曾经成立
- 已被后续材料推翻
- 仍待确认

这个点是有共鸣的。

### What OVP should not absorb

#### 1. 不要把 OVP 收缩成 agent memory vendor 叙事

`Zep` 的产品叙事很适合在线 agent 平台。

OVP 如果照着讲，很容易把自己从知识编译系统降格成“另一种 memory layer”。

这不是正确方向。

#### 2. 不要把 serving latency 当成主导问题

Zep 非常强调 `<200ms` retrieval 和 per-turn context retrieval。

这对它合理。

但 OVP 不该因此把自己的架构目标从：

- knowledge quality
- interpretive rigor
- artifact quality
- reviewability

转成“每轮极低延迟喂 prompt”。

#### 3. 不要误以为 graph = architecture equivalence

两边都讲 graph，不代表是同类系统。

Zep 的 graph 更偏 retrieval substrate。

OVP 的 graph / truth / views 更偏 compiled knowledge state。

这两个不能混。

### Working conclusion from Round 5

`Zep` 是一个做得很清楚的 **agent context serving platform**。

它的长处是：

- 把多源上下文统一进 temporal graph
- 提供默认可用的 context assembly surface
- 让开发者少做 prompt 拼装
- 明确区分 retrieval patterns

它对 OVP 最有价值的不是“图谱”本身，而是：

- context-serving layer 的产品化方式
- prompt-ready artifact 的设计思路
- temporal invalidation 对上下文质量的重要性

它不该被当成 OVP 的整体架构参考。

更准确的说法是：

**Zep 是 OVP 旁边的一层，不是 OVP 本身。**

如果 OVP 未来要做 agent-facing serving/export surface，Zep 很值得继续看。
如果讨论的是本地知识吸收、规范化、编译和审阅，它的帮助就有限得多。

## File References For This Round

### OVP

- [README.md](/Users/chris/Documents/openclaw-template/README.md)
- [docs/pack-api/README.md](/Users/chris/Documents/openclaw-template/docs/pack-api/README.md)
- [src/openclaw_pipeline/truth_store.py](/Users/chris/Documents/openclaw-template/src/openclaw_pipeline/truth_store.py)
- [src/openclaw_pipeline/autopilot/queue.py](/Users/chris/Documents/openclaw-template/src/openclaw_pipeline/autopilot/queue.py)
- [src/openclaw_pipeline/commands/ui_server.py](/Users/chris/Documents/openclaw-template/src/openclaw_pipeline/commands/ui_server.py)

### arscontexta

- `https://github.com/agenticnotetaking/arscontexta/blob/main/README.md`
- `https://github.com/agenticnotetaking/arscontexta/blob/main/reference/kernel.yaml`
- `https://github.com/agenticnotetaking/arscontexta/blob/main/reference/three-spaces.md`
- `https://github.com/agenticnotetaking/arscontexta/blob/main/skills/setup/SKILL.md`
- `https://github.com/agenticnotetaking/arscontexta/blob/main/hooks/hooks.json`
- `https://github.com/agenticnotetaking/arscontexta/blob/main/hooks/scripts/session-orient.sh`
- `https://github.com/agenticnotetaking/arscontexta/blob/main/hooks/scripts/write-validate.sh`

### HV Analysis / Khazix Skills

- `https://github.com/KKKKhazix/khazix-skills/blob/main/README.md`
- `https://github.com/KKKKhazix/khazix-skills/blob/main/prompts/%E6%A8%AA%E7%BA%B5%E5%88%86%E6%9E%90%E6%B3%95.md`
- `https://github.com/KKKKhazix/khazix-skills/blob/main/hv-analysis/SKILL.md`
- `https://github.com/KKKKhazix/khazix-skills/blob/main/hv-analysis/scripts/md_to_pdf.py`

### Creating a Second Brain with Claude Code

- [Creating a Second Brain with Claude Code.md](/Users/chris/Documents/openclaw-vault/Clippings/Creating%20a%20Second%20Brain%20with%20Claude%20Code.md)

### ALIVE

- `https://github.com/alivecontext/alive`
- `/tmp/alivecontext-alive/README.md`
- `/tmp/alivecontext-alive/plugins/alive/.claude-plugin/plugin.json`
- `/tmp/alivecontext-alive/plugins/alive/CLAUDE.md`
- `/tmp/alivecontext-alive/plugins/alive/rules/world.md`
- `/tmp/alivecontext-alive/plugins/alive/skills/load-context/SKILL.md`
- `/tmp/alivecontext-alive/plugins/alive/skills/save/SKILL.md`
- `/tmp/alivecontext-alive/plugins/alive/hooks/hooks.json`
- `/tmp/alivecontext-alive/plugins/alive/hooks/scripts/alive-context-watch.sh`
- `/tmp/alivecontext-alive/plugins/alive/scripts/project.py`

### Zep

- `https://github.com/getzep/zep`
- `/tmp/getzep-zep/README.md`
- `/tmp/getzep-zep/examples/python/agent-memory-full-example/README.md`
- `/tmp/getzep-zep/examples/python/context-templates-example/README.md`
- `/tmp/getzep-zep/examples/python/openai-agents-sdk/README.md`
- `/tmp/getzep-zep/mcp/zep-mcp-server/README.md`
- `/tmp/getzep-zep/legacy/src/api/routes.go`
- `/tmp/getzep-zep/legacy/src/api/apihandlers/memory_handlers_common.go`
- `/tmp/getzep-zep/legacy/src/lib/graphiti/service_ce.go`
- `https://www.getzep.com/`
- `https://help.getzep.com/assembling-context`
- `https://help.getzep.com/retrieving-context`
- `https://help.getzep.com/context-templates`
- `https://help.getzep.com/architecture-patterns`
- `https://help.getzep.com/mem0-to-zep`
- `https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/`

## Round 6: `getzep/graphiti`

### Short answer

`Graphiti` 有意义，而且比 `Zep` 本身更值得研究。

原因很直接：

- `Zep` 更像 managed serving surface
- `Graphiti` 才是时序 context graph 的真实引擎

如果说上一轮研究 `Zep` 主要是在理解一个产品层，那这一轮研究 `Graphiti` 才是在看：

**“temporal context graph” 这个说法到底有没有工程含量**

结论是：有，而且不低。

### What it actually is

`Graphiti` 是一个开源 temporal context graph engine。

它不是单纯的 graph wrapper，也不是一套 GraphRAG prompt 技巧，而是一个明确的图谱框架，包含：

- core graph engine
- episode/entity/edge models
- temporal validity fields
- provenance structure
- hybrid search and rerank recipes
- multiple graph backends
- MCP server

这点从 repo 结构就能看出来：

- `graphiti_core/`
- `graphiti_core/driver/`
- `graphiti_core/search/`
- `graphiti_core/models/`
- `mcp_server/`
- examples 与 tests 也比较完整

### What problem it is really solving

它解决的问题不是“让 agent 有点记忆”，而是：

**如何让 agent 面对持续变化的数据时，还能保留结构化事实、历史真值、来源追踪和可检索性**

这和普通 memory 产品不一样。

普通 memory 系统很多只在做：

- 追加片段
- 向量检索
- 生成摘要

`Graphiti` 在做的是：

- 以 `episode` 为原始输入单位
- 从 episode 中抽取 entities 和 edges/facts
- 给 facts 加上 `valid_at` / `invalid_at`
- 处理新事实对旧事实的 invalidation
- 保留 fact 到 episode 的 provenance
- 以 hybrid retrieval 把相关 edges / nodes / episodes 取回来

这是一个更硬的状态模型。

### What is genuinely strong in it

#### 1. Episode / Entity / Edge 三层模型是清楚的

这是它最核心的结构。

README 里定义得很明确：

- `Episode` 是 ingest 进来的原始数据与来源
- `Entity` 是节点
- `Fact / Relationship` 是带时间窗口的边

代码里也对得上：

- `graphiti_core/nodes.py` 里有 `EpisodeType`
- `graphiti_core/edges.py` 里 `EntityEdge` 明确带 `valid_at` / `invalid_at`
- `graphiti_core/graphiti.py` 的 `add_episode()` 是主入口

这个分层是有实质的，不是命名游戏。

#### 2. 它认真处理事实失效，而不是覆盖

这一点非常关键。

在 `EntityEdge` 模型里，事实边有：

- `valid_at`
- `invalid_at`
- `expired_at`
- `reference_time`

README 也强调：

- old facts are invalidated, not deleted
- query what is true now, or what was true at any point in time

这说明它真正关心的是：

**事实演化**

不是“把最新说法覆盖掉旧说法”。

这点对任何关心知识演进的系统都重要。

#### 3. 它的 ingest 不是 batch rebuild，而是 incremental integration

`add_episode()` 的设计说明得很清楚：

- 添加新 episode
- 抽取 nodes
- 抽取 edges
- resolve / deduplicate
- invalidate conflicting edges
- 更新图

而且文档明确强调：

- 不需要 complete graph recomputation
- 推荐顺序处理 episode
- 适合后台队列

这不是静态 GraphRAG 的思路，而是流式演化图谱。

#### 4. 它把 retrieval 做成了可配置层，而不是一个黑盒 search

`search()` 只是默认路径。

更有价值的是 `search_()` 和 `search_config_recipes`：

- hybrid semantic + keyword
- graph traversal
- node distance rerank
- cross-encoder rerank
- edge/node/episode 多层检索配置

这说明它不是“有图就行”，而是认真在做 retrieval engineering。

#### 5. 它把 provenance 当成第一等概念

README 里明确说 every derived fact traces back to episodes。

而代码层也有：

- `HasEpisodeEdge`
- `NextEpisodeEdge`
- `get_nodes_and_edges_by_episode()`

这意味着它并不只想“查出结果”，而是保留来源链条。

这一点对 OVP 是重要共鸣。

### What is weaker in it

#### 1. 它仍然主要服务 agent context，不是完整知识工作系统

虽然 `Graphiti` 比 `Zep` 更底层、更有架构价值，但它依然主要服务于：

- agent memory
- dynamic context retrieval
- production retrieval workloads

它并不直接解决：

- 人类可读知识产物
- 研究报告式 materialization
- long-form interpretation artifacts
- operator review workflows

所以它比 `Zep` 更接近 OVP 的核心问题，但仍然不是 OVP 这一类系统。

#### 2. 它对 LLM extraction 依赖不轻

README 和实现都明显建立在：

- LLM 做抽取
- 结构化输出要可靠
- ingestion concurrency 要考虑 rate limits

这意味着它的事实层虽比普通 memory 更强，但仍然受 extraction quality 影响。

它不是 deterministic truth compiler。

#### 3. 它的运行复杂度不低

需要：

- graph database
- embedding provider
- LLM provider
- reranker / search config

这对构建平台合理，但对很多本地知识工作流来说成本较高。

### What can actually help OVP

#### 1. “事实失效而非覆盖” 是最有价值的启发

这可能是这一轮最值得吸收的点。

OVP 如果只是不断累积新结论，而没有更显式地区分：

- 当前有效
- 历史有效
- 已被推翻

那知识系统最终会变得模糊。

`Graphiti` 在这里给了一个很强的提醒：

**知识不是只有新增，也有 invalidation。**

#### 2. Episode provenance 的设计值得借

OVP 也有 source / deep-dive / absorb 链条，但 `Graphiti` 的 episode-first 设计很值得参考：

- 原始输入是 episode
- 事实永远可回到 episode
- retrieval 可以按 episode、edge、node 视角进行

对 OVP 来说，这提示的是：

**源材料、解释结果、知识状态之间的 lineage 应该更显式。**

#### 3. Search recipes / retrieval recipes 很值得借鉴

OVP 现在更强在 compile，但如果未来做更强的 agent-facing retrieval 或 API surface，`Graphiti` 的做法有参考价值：

- 不只有一个 search
- 而是不同 recipe
- 针对 node / edge / episode / rerank 组合
- 明确暴露 retrieval strategy

这比“一个万能 search”要成熟。

#### 4. 它帮助 OVP把 “graph” 这件事说清楚

Graphiti 让我更确定一点：

OVP 如果以后也讲 graph，不能只讲“我们也有关系图”。

必须讲清：

- 图里的原子对象是什么
- 事实有没有时间窗口
- 冲突如何进入图
- provenance 如何回溯
- graph 是 retrieval substrate 还是 compiled knowledge state

`Graphiti` 在这件事上是一个好参照。

### What OVP should not absorb

#### 1. 不要把 OVP 退化成 context graph engine

这点很关键。

`Graphiti` 是 engine。

OVP 的价值不只在 graph engine，而在：

- ingest and interpretation
- canonical absorb
- materialized artifacts
- review and operator workflow

所以不能因为 `Graphiti` 很强，就把 OVP 的目标收窄成“做个更好的 temporal graph”。

#### 2. 不要过度依赖 runtime extraction 作为知识真值本身

`Graphiti` 的架构适合动态 context，但 OVP 仍然需要更强的：

- reviewability
- artifact inspection
- human-readable outputs
- selective canonicalization

这类东西不能被纯 runtime graph 吞掉。

### Working conclusion from Round 6

`Graphiti` 是到目前为止，外部项目里在 **temporal knowledge / context evolution** 这个问题上最有工程含量的一个。

它对 OVP 真正有价值的地方不是“也有图”，而是：

- edge invalidation / temporal truth windows
- episode-based provenance
- incremental graph evolution
- retrieval recipe design

如果只从“agent context serving”角度看，`Zep` 更像产品层。

如果从“持续变化的事实如何被组织和检索”看，`Graphiti` 才是值得认真吸收的一层。

一句话压缩：

**Zep 值得看它的 serving surface，Graphiti 值得看它的 time-aware fact model。**

## File References For This Round

### Graphiti

- `https://github.com/getzep/graphiti`
- `/tmp/getzep-graphiti/README.md`
- `/tmp/getzep-graphiti/examples/quickstart/README.md`
- `/tmp/getzep-graphiti/mcp_server/README.md`
- `/tmp/getzep-graphiti/graphiti_core/graphiti.py`
- `/tmp/getzep-graphiti/graphiti_core/edges.py`
- `/tmp/getzep-graphiti/graphiti_core/search/search_config_recipes.py`
- `/tmp/getzep-graphiti/graphiti_core/search/search.py`
- `/tmp/getzep-graphiti/graphiti_core/nodes.py`

## Round 7: `trustgraph-ai/trustgraph`

### Short answer

`trustgraph` 值得研究。

它不是一个轻量 memory repo，也不是单点 GraphRAG demo，而是一个更重的：

**context infrastructure platform**

如果要压缩成一句话，它更像：

**“面向 context graphs 的 Supabase + flow orchestration + GraphRAG/OntoRAG/agent platform”**

这意味着它和 OVP 不是同类产品，但它提出了几件 OVP 应该认真看的事情：

- context core 作为可移植产物
- ontology-driven extraction / query
- explainability 作为结构性能力
- flow / queue / orchestration 作为系统级能力

### What it actually is

README 已经说得很直：

- context development platform
- graph-native infrastructure
- multi-model storage
- semantic retrieval pipelines
- context cores
- fully agentic system
- MCP integration

这个仓库的代码结构也和这个定位一致：

- `trustgraph-base/`
- `trustgraph-flow/`
- `trustgraph-mcp/`
- `trustgraph-cli/`
- 多个 provider / OCR / embedding 模块
- 大量 `docs/tech-specs/`

这说明它不是“一个 feature”，而是一个平台化系统。

### What problem it is really solving

`trustgraph` 试图解决的是：

**如果你把 context 当成一等基础设施，而不只是一个向量库或知识图谱，你需要一整套什么系统能力？**

它给出的答案是：

- 图与向量并存
- ontology 与 extraction 并存
- retrieval 与 explainability 并存
- agent orchestration 与 MCP tool access 并存
- queue-driven flows 负责把这一切串起来

这已经不是单纯的“RAG 更强一点”，而是：

**上下文工程平台化**

### What is genuinely strong in it

#### 1. 它把 “context” 当成可部署、可编排、可版本化的基础设施

这是它和前面几轮最大的区别。

`context core` 这个概念很重要：

- portable
- versioned
- reusable
- can be promoted / rolled back

README 里对 context core 的定义相当清楚：

- ontology
- context graph
- embeddings / vector index
- source manifests + provenance
- retrieval policies

这不是单个知识库目录，而是：

**可运输的 context artifact**

这个思路比“做一个 vault”更平台化。

#### 2. 它的 ontology 方向是认真的，不是装饰

`ontology.md` 和 `ontorag.md` 说明它不是简单说“支持 ontology”。

它实际在做：

- formal ontology loading
- ontology subset selection by embeddings
- ontology-conformant extraction
- ontology-aware query generation

也就是说，它想把：

- GraphRAG 的开放性
- 和 formal ontology 的约束力

结合起来。

这点不是所有 graph 项目都有。

#### 3. 它把 explainability 设计成了结构能力

`query-time-explainability.md` 和 agent/orchestration specs 很值得注意。

它不是事后附一段 rationale，而是把 explainability 做成：

- session
- retrieval
- selection
- answer

这些实体之间的 provenance graph。

并且明确区分：

- extraction-time provenance
- query-time explainability

这比很多“可解释 AI”口号要硬得多。

#### 4. 它对 orchestration 的系统思维是成熟的

`agent-orchestration.md` 和 `flow-service-queue-lifecycle.md` 说明它不是把 agent 当成单次调用，而是：

- pattern-aware orchestration
- graph-constrained routing
- reentrant pub-sub
- flow blueprint
- explicit queue lifecycle ownership

这是一种很基础设施化的思路。

它不是把 agent 当成 UI feature，而是把 agent execution 当成消息驱动工作流。

#### 5. 它认真处理 provenance / trust / veracity 这类元语义

`graph-contexts.md` 很关键。

它明确把目标设成：

- temporal metadata
- provenance
- trust / veracity
- statements about statements

甚至引入 RDF-star / quoted triples / named graphs 这些机制去支撑“对事实本身再做陈述”。

这比普通 graph 项目更接近知识系统真正难的地方。

### What is weaker in it

#### 1. 它非常重

这不是批评，是事实。

`trustgraph` 基本是在做整套平台：

- Cassandra
- Qdrant
- Garage
- Pulsar
- 多种 inference / OCR / embedding / MCP
- flow service / config service / workbench

这个重量决定了它对 OVP 的借鉴必须是概念和边界层面的，不能轻易照搬实现。

#### 2. 它更偏 infra platform，不偏 human-readable knowledge product

虽然它很重视 provenance、ontology、GraphRAG，但它的核心仍然是 platform / infra。

它不像 OVP 那样天然强调：

- object page
- topic overview
- dossier
- deep-dive
- operator-facing knowledge products

所以它的强项在“系统能力”，不在“认知产物”。

#### 3. 它有平台过度扩张的风险

README 覆盖的范围非常大：

- database
- vector
- graph
- agents
- MCP
- prompts
- flows
- schemas
- ontologies
- workbench

这种项目很容易出现“每一块都沾一点，但用户难以理解最核心价值”的风险。

这点对 OVP 是反向提醒。

### What can actually help OVP

#### 1. Context Core 是最值得吸收的概念之一

这可能是这轮最重要的收获。

OVP 现在已经有：

- pack
- profile
- derived artifacts
- truth / absorb / view

但 `trustgraph` 提醒了一个很值得进一步抽象的问题：

**OVP 的“可交付上下文单元”到底是什么？**

如果未来 OVP 要支持：

- 不同项目之间搬运 context
- 给 agent / workflow 分发 context
- 版本化知识包
- staging / promotion / rollback

那 `context core` 是非常值得参考的概念层。

#### 2. Explainability 应该被当成系统产物，不是日志

OVP 目前已经有 trace / truth / contradictions 的方向，但 `trustgraph` 明确展示了更强的一种姿态：

**把 explainability 建成 queryable provenance graph**

这意味着：

- 为什么得出这个 answer
- 选了哪些 edges
- 来源文档是什么
- 哪一步做了选择

都应该是结构化可追踪的。

这一点对 OVP 很有价值。

#### 3. Ontology-constrained extraction 值得认真关注

这一点和 `Graphiti` 不一样。

`Graphiti` 更强在时序事实演化。

`trustgraph` 更强在：

- 用 ontology 来约束 extraction
- 用 ontology 子集来限制 query
- 保证知识图里的 triples 更符合语义边界

对 OVP 来说，这可能启发的是：

**某些 pack 是否应该支持更明确的 ontology-guided extraction，而不只是自由抽取。**

#### 4. Queue / flow ownership 的系统纪律有参考价值

如果 OVP 以后继续增强 agentic runtime 或 background pipelines，`flow-service-queue-lifecycle.md` 这种文档里的纪律很有参考价值：

- 谁拥有 queue 生命周期
- flow stop/start 的边界是什么
- consumer 不应该偷偷创建 queue
- orchestration state 应该在哪里

这是比较成熟的 infra 思维。

### What OVP should not absorb

#### 1. 不要把 OVP 演化成一整套 context infra platform

这是最重要的边界。

`trustgraph` 之所以重，是因为它目标就是平台。

OVP 不应该因为看到这些能力，就开始复制：

- 全套 infra
- 全套 workbench
- 全套 queue fabric
- 全套 agent platform

那会让 OVP失焦。

#### 2. 不要牺牲认知产物来追求平台完整性

OVP 的优势之一，是它更容易落在：

- 文章
-对象页
- 综述
- 研究输出

如果被平台化冲动带走，很容易把人类可消费产物放到次要位置。

这不是好方向。

#### 3. 不要把 ontology 变成无门槛默认要求

`trustgraph` 的 ontology 路线很强，但也很重。

对 OVP 来说，更合理的吸收方式可能是：

- 在高价值 pack 中启用
- 在明确领域中使用
- 作为 precision mode

而不是让所有知识处理都依赖 formal ontology。

### Working conclusion from Round 7

`trustgraph` 是目前这批外部项目里最“平台野心型”的一个。

它最值得 OVP 认真看的不是它的“全家桶”，而是三件事：

- `context core` 作为可版本化、可搬运的上下文产物
- explainability / provenance 作为结构化系统能力
- ontology-constrained extraction/query 作为高精度模式

如果说：

- `arscontexta` 提醒我们 operating contract
- `HV Analysis` 提醒我们 orientation artifact
- `Second Brain` / `alive` 提醒我们 context runtime
- `Zep` 提醒我们 context serving
- `Graphiti` 提醒我们 time-aware fact model

那 `trustgraph` 提醒的就是：

**当 context 被当成基础设施时，它如何被打包、编排、解释和治理。**

它对 OVP 的帮助是高层概念和系统边界上的，不是实现层面的照抄。

## File References For This Round

### TrustGraph

- `https://github.com/trustgraph-ai/trustgraph`
- `/tmp/trustgraph-ai-trustgraph/README.md`
- `/tmp/trustgraph-ai-trustgraph/trustgraph/README.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/architecture-principles.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/graph-contexts.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/ontology.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/ontorag.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/agent-orchestration.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/flow-service-queue-lifecycle.md`
- `/tmp/trustgraph-ai-trustgraph/docs/tech-specs/query-time-explainability.md`
- `/tmp/trustgraph-ai-trustgraph/trustgraph-flow/trustgraph/flow/service/flow.py`
- `/tmp/trustgraph-ai-trustgraph/trustgraph-mcp/trustgraph/mcp_server/mcp.py`

## Round 8: `topoteretes/cognee`

### First judgment

`cognee` 值得研究，而且它和前面几轮都不完全一样。

如果压成一句话：

**它是一个把 “agent memory” 做成产品级操作语义的 knowledge engine。**

不是纯 GraphRAG。
不是单纯的 context serving。
也不只是个人工作记忆的文件协议。

它的真正特点是：

- 对外暴露一组很强的 memory verbs
  - `remember`
  - `recall`
  - `improve`
  - `forget`
  - `serve`
- 对内又确实把这些 verbs 接到了：
  - session cache
  - permanent graph
  - vector / graph retrieval
  - feedback-driven enrichment
  - multi-tenant / permission-aware dataset model

这使它比很多 “memory 系统” 更像一个完整产品，而不是一堆 retrieval primitives。

### What it is actually doing

读完 repo 之后，更准确的描述是：

**`cognee` 在做一个 session memory 和 permanent knowledge graph 双向耦合的 agent memory engine。**

#### 1. 它有一套明确的 V2 memory-oriented API

`cognee/__init__.py` 里已经把产品表面收敛成一组 memory verbs：

- `remember`
- `recall`
- `improve`
- `forget`
- `serve`
- `disconnect`

这不是文案层包装，是真的 API 主面。

这点和很多“底层很复杂、用户接口很乱”的图/检索系统不一样。

#### 2. `remember` 不是单一动作，而是统一入口

`remember()` 做了两种完全不同的事：

- 没有 `session_id` 时，走 permanent path，把内容 ingest + cognify 到知识图里
- 有 `session_id` 时，只写 session cache，把内容作为当前会话记忆保存

也就是说，它把“长期记忆”和“会话记忆”用同一个词统一了，但保留了底层路径分叉。

这是一个非常产品化的选择。

#### 3. `recall` 不是只查图，而是 session-first / graph-fallback

`recall()` 的实现非常值得注意：

- 当只给 `session_id` 且没有显式 datasets / query_type 时，会先搜 session cache
- session 没命中，再 fall through 到 permanent graph search
- 当带 `session_id` 做图搜索时，session history 和 graph context 会一起进入 completion

也就是说，它不是简单的 “向量库 + 图库” 拼接，而是：

**把 short-term memory 和 durable memory 放在同一个 recall surface 下。**

#### 4. `improve` 是真正的桥接层

这是 `cognee` 最强、也最有辨识度的部分。

`improve(session_ids=...)` 在实现上做了四件事：

1. 把 session 里的反馈分数映射回被使用过的 graph nodes / edges，更新 `feedback_weight`
2. 把 session Q&A 持久化到 permanent graph
3. 对图做默认 enrichment / embedding 等处理
4. 再把新图谱里的 edges 增量同步回 session cache，作为 graph knowledge snapshot

这意味着它不是单向管道，而是三条回路都真的存在：

- session -> graph
- feedback -> graph weights
- graph -> session

这一点比前面很多项目都更完整。

#### 5. `memify` 是它的 enrichment engine

`memify()` 不是 marketing 词，它是实际存在的 enrichment pipeline 入口。

它的角色是：

- 从已有 graph 或指定 data 中抽取 memory fragment
- 跑 extraction tasks
- 跑 enrichment tasks
- 形成图谱增强、embedding、权重更新、session persistence 之类的操作

所以 `cognee` 的核心结构不是“add + search”，而更像：

- ingest / cognify
- remember / recall
- memify / improve
- forget / serve

#### 6. 它认真处理了 session graph snapshot

`sync_graph_to_session.py` 很关键。

它不是把图直接暴露给 session，而是：

- 只增量抓取新 edges
- 结构化渲染为 JSON lines
- 独立存到 session 的 graph knowledge context
- 用 checkpoint 避免重复同步
- 用 max lines 控制上下文膨胀

这说明它不是“session 历史越来越长”的无纪律模式，而是在做：

**session-local compiled knowledge snapshot**

这点很值得记。

### What is actually strong here

#### 1. 它把 memory product surface 设计得很清楚

这是 `cognee` 最强的地方之一。

很多系统的真实问题不是后端弱，而是用户根本不知道应该做什么。

`cognee` 反过来做了一件正确的事：

- 不先暴露 schema、pipeline、retriever、index
- 先暴露人类能理解的 memory 动词

这对产品语义很重要。

#### 2. 它真的建立了多速度记忆结构

`cognee` 的结构可以粗略看成三层：

- session cache
- permanent graph
- enriched / weighted / embedded graph state

而 `improve()` 正是在这些层之间做编译与桥接。

这种多速度记忆结构，比“只有聊天历史”或者“只有 graph DB”成熟得多。

#### 3. feedback -> graph 这条回路很值得注意

前面几轮项目里，很少有项目把“回答后的反馈”正式变成知识系统的更新信号。

`cognee` 做了，而且是结构化地做：

- session answer 记录哪些 graph elements 被用过
- feedback score 映射到这些 elements
- 更新 `feedback_weight`

它不是完美真理系统，但它是一个明确的 learning loop。

#### 4. 它的 packaging 很完整

repo 里不只是 SDK，还有：

- CLI
- MCP server
- frontend
- examples
- coding agent / summarization / web scraper task
- multi-tenant / permission tests

所以它不是研究原型，而是明确在做产品分发和工作流接入。

### What OVP can learn from it

#### 1. 学它的 product verbs，不要学它的全部 runtime

对 OVP 来说，这轮最强的启发不是某个数据库或某个 retriever，而是：

**用户到底应该对系统说什么动词。**

`remember / recall / improve / forget` 这类动词级产品语义很强。

OVP 不一定照抄这些词，但应该认真思考：

- ingest 之后，用户心智里发生了什么
- review / enrich / absorb 应该怎么被说出来
- orientation artifact 和 durable knowledge 应该如何用简单动作表达

#### 2. session <-> canonical / derived 的桥接值得借鉴

OVP 目前更强在 canonical / derived 边界。

但 `cognee` 提醒了一件事：

**用户当下会话里产生的临时认知，什么时候进入长期知识，什么时候只停留在短期层，需要一条明确桥接路径。**

这不一定要按 `improve()` 的样子做，但这层产品语义和 runtime 边界是值得吸收的。

#### 3. feedback loop 是值得考虑的

如果 OVP 以后强化 operator review 或 agent-assisted refinement，那么：

- 什么被用户确认
- 什么被否定
- 什么长期被引用
- 什么经常被修正

这些都可能是有价值的系统信号。

`cognee` 至少证明了一件事：

**反馈不一定只是 UI 点赞，它可以进入知识系统。**

#### 4. graph snapshot into session 这个思路有价值

OVP 如果以后有更强的 agent-facing runtime，可以认真考虑：

- 不要每次都实时全图查询
- 可以有编译过的 session-local knowledge slice
- 这份 slice 可以有预算、增量同步和独立上下文位

这一点和之前 `alive` 的 save/load discipline、`Zep` 的 assembled context、`Graphiti` 的事实演化，都能接起来。

### What OVP should not absorb

#### 1. 不要把 OVP 改造成通用 memory engine

这是最重要的边界。

`cognee` 的产品中心是 agent memory。

OVP 的重心还是：

- knowledge compilation
- materialized research artifacts
- canonical / derived discipline
- human-consumable outputs

不能因为 `cognee` 这套 verbs 很顺，就把整个系统目标改掉。

#### 2. 不要把 feedback weight 当 truth

`feedback_weight` 这种东西适合做 ranking / enrichment / prioritization。

但它不等于：

- evidence strength
- claim validity
- canonical truth

这一点 OVP 必须守住。

#### 3. 不要把 session persistence 混同于知识吸收

`persist_sessions_in_knowledge_graph` 对 `cognee` 合理，因为它本来就是 memory engine。

对 OVP 来说，这条边界要更谨慎：

- 会话里说过，不代表应该成为 canonical knowledge
- operator feedback、临时推断、草稿性理解，可能只适合进入 review / staging layer

这点不能偷懒。

### Working conclusion from Round 8

`cognee` 是目前这些项目里，**最像“agent memory 产品”** 的一个。

它比 `alive` 更产品化，比 `Zep` 更有会话桥接感，比 `Graphiti` 更重产品语义而不是事实时序建模。

如果压成一个分类：

- `alive` = context runtime
- `Zep` = context serving surface
- `Graphiti` = temporal fact engine
- `cognee` = memory-product knowledge engine

对 OVP 来说，它最有价值的不是图谱或 embedding，而是：

- 简单而强的 memory verbs
- session / permanent / enriched 三层桥接
- feedback 进入知识引擎的学习回路
- session-local compiled knowledge snapshot

一句话总结：

**`cognee` 不是 OVP 的内核参考物，但它是目前最值得参考的“agent memory 产品语义”样板之一。**

## File References For This Round

### Cognee

- `https://github.com/topoteretes/cognee`
- `/tmp/topoteretes-cognee/README.md`
- `/tmp/topoteretes-cognee/cognee/__init__.py`
- `/tmp/topoteretes-cognee/cognee/api/v1/remember/remember.py`
- `/tmp/topoteretes-cognee/cognee/api/v1/recall/recall.py`
- `/tmp/topoteretes-cognee/cognee/api/v1/improve/improve.py`
- `/tmp/topoteretes-cognee/cognee/api/v1/forget/forget.py`
- `/tmp/topoteretes-cognee/cognee/api/v1/cognify/cognify.py`
- `/tmp/topoteretes-cognee/cognee/api/v1/search/search.py`
- `/tmp/topoteretes-cognee/cognee/modules/memify/memify.py`
- `/tmp/topoteretes-cognee/cognee/memify_pipelines/persist_sessions_in_knowledge_graph.py`
- `/tmp/topoteretes-cognee/cognee/memify_pipelines/apply_feedback_weights.py`
- `/tmp/topoteretes-cognee/cognee/tasks/memify/sync_graph_to_session.py`
- `/tmp/topoteretes-cognee/cognee/infrastructure/session/session_manager.py`
- `/tmp/topoteretes-cognee/examples/demos/remember_recall_improve_example.py`

## Round 9: Witcheer “Two Camps” Meta-Synthesis

### First judgment

这篇综述**有解释力，而且比大多数“memory tool 大盘点”更有价值**。

它真正做对的事不是列工具清单，而是抓到了一个前面几轮调研里已经反复出现、但很少被明确说破的分野：

- 一类系统在解决 **recall**
- 一类系统在解决 **compounding**

它把这条线命名成：

- `memory backends`
- `context substrates`

这个命名未必最终会赢，但问题意识是对的。

### Why this article matters

#### 1. 它第一次把“memory != context”说成了一级区别

这是这篇最重要的价值。

很多项目都叫 memory，但它们其实在解决不同问题：

- “记住一个事实，然后以后找回来”
- “让 agent 长期工作在一套逐步变厚的上下文里”

这两者不是一个问题。

前者关心：

- recall rate
- retrieval latency
- extraction quality

后者关心：

- context continuity
- state accumulation
- human inspectability / editability
- multi-session work stability

前面几轮看下来，这个区分是成立的。

#### 2. 它给前几轮调研提供了一个更清楚的总框架

用这篇的语言回看前面的项目，会更清楚：

- `arscontexta` 不属于典型 memory backend，它更像 operating contract + context substrate bootstrap
- `alive` 明显是 context substrate / context runtime
- `Zep` 在 camp 1 与 camp 2 之间，更像 context serving bridge
- `Graphiti` 不是 substrate，本质上还是 temporal memory / fact engine
- `TrustGraph` 不是单纯 substrate，它更像 context infrastructure / packaging layer
- `cognee` 明显更偏 memory backend / knowledge engine，但已经有 bridge-to-substrate 的味道

也就是说，这篇的二分法不是完整地图，但它是一个比“都叫 memory”更好的起点。

#### 3. 它把“文件是上下文，不是存储细节”这个点强调出来了

这点和前面 `alive`、`MemSearch`、还有 OVP 自身的方向都高度相关。

当系统是 file-native / human-readable / rebuildable 的时候，用户得到的不是一个隐藏数据库，而是一套可检查、可纠正、可复用的工作表面。

这对长期 agent 系统非常关键。

### Where the article is right

#### 1. 它对 Camp 1 的抽象基本是对的

这类系统的共同 loop 确实是：

- 发生对话或输入
- 抽取事实或保存内容
- 写入向量库 / 图库 / 其他索引
- 下次按相关性取回并注入

这类系统最擅长的是：

- preference recall
- user profile
- fact recall
- episodic retrieval

前面看 `Zep`、`Graphiti`、`cognee` 时，虽然它们 sophistication 不同，但都能落到这条线上。

#### 2. 它对 Camp 2 的抽象也大体成立

这类系统的核心不是“背后帮你存东西”，而是：

- agent 进入上下文
- 在上下文中工作
- 把新的状态写回上下文
- 下一轮从更厚的上下文继续工作

这个 loop 和传统 memory backend 的确不一样。

`alive` 是这个 loop 的强例子。

OVP 如果继续往 agent-native knowledge runtime 走，也更接近这条线，而不是经典 memory backend。

#### 3. 它把“compounding”提成主目标是对的

这是这篇里最重要的产品判断之一。

很多 memory 产品在 benchmark 上看起来很强，但它们只回答：

- 你还能不能找回那条信息

而 continuous agent 真正关心的是：

- 这套上下文会不会越跑越有用
- 会不会漂移
- 会不会越来越难维护
- 人还能不能读懂、改得动、接得上

这就是 compounding 问题。

### Where the article overstates or blurs

#### 1. 二分法有解释力，但不是严格边界

这是我最想修正的一点。

现实里很多系统是混合体，不是纯 camp 1 或纯 camp 2。

例如：

- `Zep` 仍然是 retrieval / assembly system，但它确实在往 context engineering 语言迁移
- `cognee` 是 memory engine，但已经开始做 session <-> graph 双向桥接
- `TrustGraph` 既不是文件型 substrate，也不是普通 memory backend，它是更高一层的 context packaging / governance infra

所以这篇更像在给出两个引力中心，而不是两个互斥箱子。

#### 2. 它把 “nothing gets extracted” 说得太绝对

对很多 substrate-style system 来说，这句话不完全成立。

即使 source of truth 是文件或结构化上下文，系统里仍然可能有：

- consolidation
- summarization
- promotion
- graph derivation
- shadow indexing

区别不在于“有没有 extraction”，而在于：

**extraction 是不是 source of truth。**

这才是关键。

#### 3. 它把 some tools 讲得过于整齐

这篇作为立场文没问题，但如果拿来当严格 taxonomy，会有点过度扁平化。

比如：

- `Graphiti` 更像 temporal fact engine，不该直接当 context substrate
- `TrustGraph` 更像 context infrastructure，不只是 substrate exemplar
- `cognee` 的产品语义比文中一句 “vector + graph reasoning” 复杂得多

所以它更适合当 framing，不适合当最终精确分类表。

#### 4. 它在悄悄把“continuous personal agent setup”当成默认场景

这点要意识到。

如果目标真的是：

- 24/7 personal agent
- multi-session continuity
- long-horizon autonomous work

那 context substrate 的优势会非常明显。

但如果目标只是：

- chatbot personalization
- assistant memory
- enterprise recall

memory backend 依然是更合理的解。

也就是说，它的 thesis 很强，但适用场景不是全部 agent 场景。

### The most useful refinement

这篇文章帮我把前面几轮判断进一步压缩成了三层，而不是两层。

#### Layer 1: Memory Backends

解决 recall 和 preference / fact persistence。

代表：

- `mem0`
- `Supermemory`
- `cognee`（更强版本）
- `Graphiti`（temporal end）

#### Layer 2: Context Substrates / Context Runtimes

解决多 session、可读可写、可累积的 working context。

代表：

- `alive`
- file-native personal context systems
- 一部分 `MemSearch` 风格系统

#### Layer 3: Context Packaging / Serving / Governance

解决 context 如何被装配、交付、版本化、解释、推广。

代表：

- `Zep`（serving / assembly）
- `TrustGraph`（packaging / governance）
- `arscontexta`（bootstrap / operating contract）

我认为这比原文二分法更贴近我们前面调研的真实地形。

### What this changes for OVP

#### 1. 它强化了一个判断：OVP 不是 memory backend

这点现在可以说得更明确。

OVP 就算借鉴 recall、graph、feedback、retrieval，也不应该把自己定义成：

- fact memory
- user preference memory
- hidden memory layer

它更接近：

**knowledge compilation + context substrate + human-readable outputs**

#### 2. 它也提醒 OVP：要把 “context” 讲清楚

如果外部语言正在从 memory 向 context engineering 迁移，那 OVP 自己也要想清楚：

- 我们交付的是 memory 吗
- 是 compiled knowledge 吗
- 是 working context 吗
- 是 artifact pipeline 吗

现在更像是：

**我们交付的是可累积、可检查、可派生的知识上下文。**

这比“笔记自动化”或“memory system”更准确。

#### 3. 它帮助 OVP 避免误吸收

以后再看新项目时，可以先问：

- 它主要解决 recall，还是 compounding？
- source of truth 在哪？
- human-readable context 是第一性，还是附属物？
- retrieval 是 access layer，还是核心产品本体？

这能让筛选快很多。

### Working conclusion from Round 9

这篇综述的最大价值，不是它列了哪些项目，而是它给出了一个很强的行业判断：

**memory tooling 里真正重要的断裂，不是向量 vs 图，而是 recall systems vs compounding systems。**

我认同这个判断的大方向。

但我会把它从二分法修正成三层：

- memory backends
- context substrates / runtimes
- context packaging / serving / governance

如果压成一句话：

**这篇文章是目前为止最有用的“行业地图草图”之一，但它还不是最终地图。**

对 OVP 来说，它最重要的作用是帮助我们更坚定地把自己放在：

**不是 hidden memory，不是 chatbot recall，而是可累积知识上下文的编译与运行。**

## File References For This Round

### Article / Thread Text

- User-provided text in conversation: “I Went Through Every AI Memory Tool I Could Find. There Are Two Camps.”

## Round 10: `zilliztech/memsearch`

### First judgment

`memsearch` 值得看，而且它恰好是前一轮那篇 “Two Camps” 文章里最需要实证检查的一个样本。

我的结论是：

**它不是纯粹的 context substrate，也不是普通 memory backend，而是一个很清楚的 “markdown-canonical semantic memory engine”。**

如果压成一句话：

**source of truth 在 markdown，memory access layer 在 Milvus。**

这使它在我们现在的地图里更接近：

- substrate-friendly
- retrieval-heavy
- packaging-complete

而不是极端 camp 1 或极端 camp 2。

### What it is actually doing

读完 repo 和实现之后，可以更准确地说：

`memsearch` 做的是一个面向 coding agents 的跨平台 memory system，其核心结构是：

- `.md` memory files 作为 canonical store
- Milvus 作为 derived shadow index
- hybrid search 作为 primary recall mechanism
- plugin hooks 在 Claude Code / OpenClaw / OpenCode / Codex CLI 上自动捕获与召回

#### 1. 它明确把 markdown 设成 source of truth

这不是文案层说说而已。

README、`docs/design-philosophy.md`、`docs/architecture.md` 全都在重复一个判断：

- markdown files are canonical
- Milvus is derived
- index can be dropped and rebuilt

而 `core.py` 里的实现也对得上：

- 扫描 markdown
- chunking
- 计算 chunk ID
- 只把 chunks upsert 到 Milvus
- 删除 stale chunks
- 没有额外 sidecar state 去当“真源”

也就是说，这不是“数据库为真，顺手导出 markdown”，而是反过来。

#### 2. 它的 retrieval layer 很强，而且是核心产品能力

`store.py` 里很清楚：

- dense vector
- BM25 sparse
- Milvus hybrid search
- RRF reranking

这说明它不是 file browser，而是一个很认真在做 recall quality 的系统。

换句话说：

**它虽然 source-of-truth 很 substrate，但主要用户价值仍然通过 retrieval surface 交付。**

这点很重要。

#### 3. 它的 progressive disclosure 设计很成熟

这轮最值得记的一点，是它把 recall 明确做成了三层：

- L1: `search` → snippets
- L2: `expand` → full markdown section
- L3: `transcript` → raw conversation

这套设计非常好，因为它没有把 recall 简化成“一次搜一大段文本扔给模型”，而是在：

- context cost
- provenance depth
- exactness

之间做了分层。

这比很多 memory 工具成熟得多。

#### 4. 它的写入路径非常保守

这点也值得注意。

`memsearch` 默认不是在写入时做 aggressive fact extraction，而是：

- 插件在每轮结束后做 bullet-point summary
- append 到 daily markdown
- 然后再索引

也就是说，它的 write path 更接近：

**append-only working memory**

而不是：

**LLM 决定该保留哪些结构化事实**

这让它更可控，也更接近 file-native context systems。

#### 5. `compact` 让它开始有 substrate 味道

`compact.py` 和 `core.py` 里的 compact flow 说明了一件事：

- chunks 可以被 LLM 压缩总结
- 总结写回 daily markdown
- watcher 再把新 markdown 重新索引

这构成了一个非常轻量的 consolidation loop。

它没有 `alive` 那么强的 runtime protocol，也没有 OVP 那么强的 canonical/derived discipline，但它已经不是“只会存和搜”的系统了。

### What is actually strong here

#### 1. 它把 “markdown-canonical + vector access” 这件事做得很干净

很多项目嘴上说 files-first，实际实现里还是数据库中心。

`memsearch` 这点做得很干净：

- 文件可读、可改、可 git
- 向量层可丢弃、可重建
- 不需要神秘同步机制

这点非常对。

#### 2. 它是目前看过的最完整的 cross-platform packaging 之一

这点比它底层架构更有现实意义。

repo 直接提供：

- Claude Code plugin
- OpenClaw plugin
- OpenCode plugin
- Codex CLI plugin
- CLI / Python API

所以它不是“一个好想法”，而是明确在解决：

**同一套 memory 如何在多 agent / 多 CLI 之间共享。**

这件事非常现实，也很难。

#### 3. 它很清楚地把 retrieval 当 access layer，而不是全部系统

这是它和纯 camp 1 工具不一样的地方。

Camp 1 常见问题是：

- memory 只存在于数据库里
- 人无法直接编辑
- retrieval 成了唯一入口

`memsearch` 至少把这件事改对了一半：

- retrieval 是主入口
- 但不是 source of truth

这是一个更健康的结构。

#### 4. 它非常重视 operational pragmatism

这一点从 docs 和代码里都能看出来：

- dedup 用 content-addressable chunk IDs
- deleted-file cleanup
- watcher debounce
- ONNX 本地 embedding 默认
- per-project collection derivation
- 多平台 transcript drill-down

这套东西不是论文式架构，而是很工程地在解决“这个系统能不能真的每天跑”。

### Where it sits on the map

这轮最重要的问题就是：它到底在三层地图上放哪？

我的判断是：

#### 1. 它不是纯 memory backend

因为：

- source of truth 不是数据库
- write path 不是主要靠 fact extraction
- human-readable files 是一等公民

所以它不能被简单归到 `mem0` / 传统 recall memory 那一边。

#### 2. 它也不是纯 context substrate / runtime

因为：

- 它最强的用户价值还是 recall
- 工作方式仍然是 search / expand / transcript
- 没有强 operating contract
- 没有真正的 agent-work-inside-files runtime discipline

所以它也不像 `alive`。

#### 3. 它更像 “context-friendly memory engine”

这是我觉得最准确的位置。

也就是：

- memory engine 的交付方式
- substrate-style 的 source-of-truth discipline
- packaging-heavy 的 plugin distribution

换句话说，它站在 memory backend 向 context substrate 迁移的中间地带，而且站得比大多数项目都稳。

### What OVP can learn from it

#### 1. `files are canonical, indexes are derived` 这个边界值得吸收

这点和 OVP 的方向天然一致。

如果 OVP 以后有更多 graph / vector / retrieval / DB surfaces，这条边界必须讲清楚：

- 真源在哪
- 哪些是 rebuildable
- 哪些只是 access layer

`memsearch` 在这件事上表达得非常清楚。

#### 2. progressive disclosure 非常值得借

这轮我认为最值得 OVP 借的是这套三层 recall：

- 搜索结果
- 扩展上下文
- 原始来源

OVP 未来如果做 agent-facing 或 user-facing 的 recall/query surface，这个分层会非常有用。

#### 3. cross-platform memory packaging 有启发

OVP 现在更偏 pipeline / knowledge system。

`memsearch` 提醒的一点是：

**如果要让外部 agent 真正消费你的知识系统，接入层和插件层不能太弱。**

这不是 core truth model，但是真实 adoption 很重要。

#### 4. compact loop 值得关注，但不能直接照抄

它的 compact 很像轻量 consolidation。

但 OVP 不该满足于：

- summarize
- append
- reindex

OVP 更需要：

- explicit absorb rules
- canonical / staging separation
- derived artifact regeneration

所以它只能借“节律”和“入口”，不能借全部机制。

### What OVP should not absorb

#### 1. 不要把 OVP 退化成 search-over-markdown

这是最大边界。

`memsearch` 很强，但它核心还是：

- semantic memory
- cross-session recall
- cross-platform access

OVP 的价值更高，不能退回成一个 recall engine。

#### 2. 不要把 append-only summary 当成充分知识建模

对 `memsearch` 来说，这很合理。

对 OVP 来说不够。

因为 OVP 要解决的不只是：

- “之前讨论过什么”

还包括：

- 什么进入 evergreen / canonical
- 什么只是临时观察
- 什么产生 contradiction
- 什么需要 atlas / object / topic level 重组

#### 3. 不要把 Milvus shadow index 的存在误当成 architecture completeness

`memsearch` 的影子索引设计是对的，但它仍然主要服务 recall。

OVP 如果以后做 shadow index，也要服务：

- richer artifact access
- truth projections
- structured review

而不是只有 semantic search。

### Working conclusion from Round 10

`memsearch` 是目前看到的一个非常重要的中间态项目。

它证明了三件事：

1. 文件为真源和向量检索并不冲突  
2. memory 系统可以是 markdown-canonical  
3. 真正的产品落地，离不开 plugin / packaging / retrieval UX

如果用我们现在的三层地图来放：

- 它不像 `mem0` 那样是纯 memory backend
- 它也不像 `alive` 那样是纯 context runtime
- 它更像 **markdown-canonical memory engine with semantic access**

一句话总结：

**`memsearch` 不是 OVP 的内核模板，但它是目前最好的“文件真源 + 检索访问层”参考样板之一。**

## File References For This Round

### MemSearch

- `https://github.com/zilliztech/memsearch`
- `/tmp/zilliztech-memsearch/README.md`
- `/tmp/zilliztech-memsearch/docs/design-philosophy.md`
- `/tmp/zilliztech-memsearch/docs/architecture.md`
- `/tmp/zilliztech-memsearch/docs/home/comparison.md`
- `/tmp/zilliztech-memsearch/docs/platforms/openclaw/how-it-works.md`
- `/tmp/zilliztech-memsearch/evaluation/README.md`
- `/tmp/zilliztech-memsearch/src/memsearch/core.py`
- `/tmp/zilliztech-memsearch/src/memsearch/store.py`
- `/tmp/zilliztech-memsearch/src/memsearch/compact.py`
- `/tmp/zilliztech-memsearch/plugins/openclaw/README.md`
- `/tmp/zilliztech-memsearch/plugins/openclaw/index.ts`
- `/tmp/zilliztech-memsearch/plugins/openclaw/scripts/derive-collection.sh`

## Round 11: Garry Tan “Resolvers” Meta-Synthesis

### First judgment

这篇文章非常重要。

如果前面那篇 “Two Camps” 帮忙把 memory / context 这条横向分野说清了，那么这篇文章做的是另一件同样重要的事：

**它把 agent system 里长期被混在 prompt、skill description、目录约定、人工记忆里的那一层，明确命名成了 `resolver`。**

一句话概括：

**resolver 不是知识本身，也不是能力本身，而是“能力、上下文、落点、子流程”之间的路由治理层。**

这件事比很多人以为的重要得多。

### What the article is really saying

表面上这篇在讲：

- 一个 20,000 行 `CLAUDE.md` 缩成 200 行决策树
- 技能误触发
- 文章误归档
- `check-resolvable`
- trigger evals

但本质上它在讲的是：

**agent system 的失败，很多时候不是因为模型不够强，也不是因为 skill 不够多，而是因为 routing / governance 缺位。**

#### 1. 不要把所有知识塞进固定上下文

这是最直接的一层。

20,000 行 instructions 不会让 agent 更聪明，只会让它更模糊、更慢、更不稳。

resolver 的价值在于：

- 不把所有知识一直放在眼前
- 而是在任务出现时加载正确的那一小部分

也就是：

**knowledge should be addressable, not omnipresent.**

这点和前面 `arscontexta` 的 operating contract、`memsearch` 的 progressive disclosure，本质是一条线。

#### 2. skill existence 不等于 skill reachability

这是文章里最重要的工程洞见之一。

一个 skill 存在，并不代表系统能用到它。

如果 resolver 没有：

- 触发语义
- 优先级
- 调用路径
- 升级/回退路径

那这个 skill 只是“存在于仓库里”，不是存在于系统里。

这比 skill 缺失更危险，因为它制造了 capability illusion。

#### 3. 误归档不是小错误，而是知识层失真起点

Manidis 那个误归档例子本质不是“路径写错了”，而是：

- skill 自带了自己的分类假设
- skill 没有 consult resolver
- 系统的知识拓扑开始 silently drift

这跟前面我们反复说的 canonical / staging / derived 边界是一回事。

一旦 filing logic 不集中，系统就不是“知识库”，而是 slowly degrading junk drawer。

#### 4. 测 output 不够，必须测 routing

这也是非常关键的一点。

大多数人会测：

- prompt 输出质量
- tool 正确性
- end-to-end 成功率

但 resolver 告诉你还要测另一类东西：

- 这个输入有没有触发正确技能
- 两个技能会不会冲突触发
- 某个技能是不是永远不可达
- 某个文件分类是不是被错误 fallback 吞掉了

这类 eval 是组织级 eval，不是单点模型 eval。

### Why this matters more than it looks

#### 1. 它补上了我们当前地图里一个“纵向切面”

前面我们的三层图是横向结构：

- memory backends
- context substrates / runtimes
- context packaging / serving / governance

resolver 不是这个图里单独的一类产品。

它更像一个纵向原语，会出现在每一层里：

- memory layer 里决定 recall 走哪种路径
- substrate layer 里决定内容写到哪、读哪份 context
- packaging / governance layer 里决定哪个 skill、哪个 artifact、哪个 policy 生效

也就是文章里说的：

**resolvers are fractal.**

这判断我认同。

#### 2. 它把“agent 组织管理”说清楚了

文章后半段那个组织比喻其实是对的，不是修辞。

在一个 40+ skills、几万文件、多个子系统的 agent 环境里：

- skills 像员工
- filing rules 像流程制度
- resolvers 像 org chart + routing rules
- trigger evals 像 performance reviews
- check-resolvable 像 audit/compliance

这不是夸张，这是很准确的工程抽象。

一旦你接受这个视角，很多问题就不再是“prompt engineering 问题”，而是：

**coordination architecture 问题。**

#### 3. 它比很多 skill 文章更接近真正的 scaling pain

大多数“fat skill”讨论停在：

- 怎么写 skill
- 怎么触发 skill
- 怎么让 skill 更强

这篇更进一步，讲的是：

- skill 多起来之后怎么不失控
- 新 skill 怎么不变成 dark capability
- 目录体系怎么不漂移
- 用户自然语言怎么持续映射到正确 action

这些才是系统规模起来以后最真实的问题。

### Where it fits relative to earlier rounds

#### 1. 它和 `arscontexta` 是近亲，但更强调治理

`arscontexta` 强在：

- derivation
- setup
- operating contract
- space separation

这篇 resolver 文强在：

- routing discipline
- trigger coverage
- reachability auditing
- self-healing governance

前者更像 system constitution。
后者更像 runtime org chart。

#### 2. 它和 `alive` 不冲突，反而互补

`alive` 解决的是：

- save/load protocol
- projection discipline
- authored vs generated state
- multi-session runtime hygiene

resolver 解决的是：

- 什么时候该读哪份 state
- 什么时候该调哪个 skill
- 什么时候该写到哪个空间

前者是 storage/runtime discipline。
后者是 routing/governance discipline。

#### 3. 它比 `memsearch` 更接近管理层，而不是 access layer

`memsearch` 是：

- source-of-truth / shadow-index 分离
- recall UX
- cross-platform packaging

resolver 不是 access layer。

它是 access layer 上面那层：

- 谁该调 recall
- 什么时候该调 expand/transcript
- 哪种输入该去哪个分类体系

#### 4. 它和 `TrustGraph` 的治理感觉很接近，但更轻

`TrustGraph` 是重型 infra / governance。

resolver 文章给的是一个极轻的实现方式：

- markdown decision tree
- trigger evals
- reachability audit

这点很重要，因为它说明：

**治理层不一定要重平台化，先从 documents + tests 就能成立。**

### What OVP can learn from it

#### 1. OVP 需要显式 resolver，不要把 routing 隐含在 skill 里

这是这轮最重要的启发。

如果 OVP 以后有：

- 多种 ingest 入口
- 多类知识对象
- 多种 artifact 生成路径
- 多类 review / absorb / derive 行为

那就不能让每个 skill / command / pack 自带自己的半隐式 routing。

必须有一层显式 resolver，回答：

- 这类输入先走哪个 recipe
- 该落在哪个空间
- 哪些 pack / object / topic / review 流程优先
- 出现冲突时谁覆盖谁

#### 2. OVP 应该测 routing，而不是只测产物

除了内容质量 eval 之外，OVP 以后很应该有：

- 输入句子 -> 预期 recipe / skill
- 内容类型 -> 预期 vault 落点
- 对象类型 -> 预期 canonical target
- 任务意图 -> 预期 review / derive path

也就是 trigger evals / routing evals。

这会比单纯继续堆 prompt 价值更高。

#### 3. OVP 需要某种 `check-resolvable`

这篇里最“立刻可做”的 idea 就是这个。

OVP 未来如果存在多个 skills / packs / generators / commands，那么应该有个静态或半静态检查器，回答：

- 这个能力能不能被用户自然触达
- 这个命令有没有 resolver 路径
- 这个 pack 有没有被任何 routing 规则指向
- 有没有 dark capability

这是纯治理增益。

#### 4. filing rules 应该被提升成系统契约

这和 OVP 当前方向直接相关。

以前我们一直在说：

- canonical vs derived
- staging vs durable
- object / topic / atlas / evergreen 的边界

resolver 文章提醒的一点是：

**这些边界如果不变成所有写入路径都必须 consult 的显式规则，就会慢慢失效。**

也就是说，classification / filing contract 不应该只是文档说明，而应该是 resolver-governed contract。

### What OVP should not absorb

#### 1. 不要把 resolver 变成另一份巨大的 prompt

这是文章本身已经反复警告的。

resolver 的价值是：

- routing
- narrowing
- selection

不是把它再写成另一份 20,000 行总说明。

#### 2. 不要把所有治理都交给自然语言“约定”

文章里 trigger evals 和 `check-resolvable` 最重要的点就是：

光有约定不够，必须有测试。

OVP 如果吸收 resolver 思想，不能只写一份 `RESOLVER.md` 然后自我感动。

需要：

- evals
- lints
- reachability checks
- drift audits

#### 3. 不要忽视 resolver 的衰减

这篇另一个很对的判断是：

resolver 会腐烂。

所以它不是“写一次”的文档，而是：

- 随 skill 增长而维护
- 随用户语义变化而修
- 随 traffic 数据更新

这个 maintenance reality 必须一开始就承认。

### Working conclusion from Round 11

这篇文章给出的最重要启发是：

**agent system 里还缺一个经常被忽视的一等原语：resolver。**

它既不是 memory，也不是 skill，也不是 context 本身。

它是：

- capability routing
- context routing
- filing routing
- governance and reachability

如果把前面几轮的收获压成一句更完整的话：

- `alive` 教我们 runtime discipline
- `memsearch` 教我们 canonical-vs-index discipline
- `arscontexta` 教我们 operating contract
- 这篇 resolver 文教我们 **routing governance**

对 OVP 来说，这篇最大的价值不是“要不要也做 GBrain”，而是：

**以后如果 OVP 有多 skill、多 pack、多落点、多 artifact，就必须有显式 resolver、trigger evals 和 reachability audit，否则系统规模一大就会静默腐烂。**

## File References For This Round

### Article / Thread Text

- User-provided text in conversation: Garry Tan, “Resolvers: The Routing Table for Intelligence”

## Round 12: `MemPalace/mempalace`

### First judgment

`mempalace` 值得看，但需要纠偏。

那篇 landscape 把它讲成：

- local-first
- verbatim memory
- ChromaDB search
- 最适合 “找回三周前说过的话”

这不是错，但**不完整**。

更准确地说：

**`mempalace` 是一个以 verbatim recall 为主轴、但已经长出 layered wake-up、closet indexing、temporal KG、hooks、MCP 的本地 memory system。**

所以它不是单纯的 transcript bucket。
但它也仍然不是 context substrate。

### What it is actually doing

#### 1. 主路径仍然是 verbatim drawer retrieval

这一点是成立的，而且 repo 自己也说得很直：

- 不 summarize
- 不 paraphrase
- 不做写时抽取作为主路径
- 原始内容作为 drawers 存进去
- 搜索时主要回 drawers

`miner.py` 的注释非常明确：

> Stores verbatim chunks as drawers. No summaries. Ever.

`searcher.py` 也明确说：

- drawer query 是 floor
- closet 只是 ranking signal，不是 gate

也就是说，系统的真正 retrieval backbone 还是 verbatim drawer。

#### 2. 但它并不是“只有 raw recall”

这轮最需要修正的就是这一点。

`mempalace` 已经明显长出了几层结构：

- `closets`：对 drawers 的紧凑索引层 / pointer layer
- `Layer0-3` memory stack：identity / essential story / on-demand / deep search
- temporal knowledge graph：本地 SQLite，带 validity window
- hooks：定期保存与 precompact 紧急保存
- MCP / agents surface：不仅能搜，还能 traverse / timeline / query KG

所以它不是一句“存原文然后语义搜索”能讲完的。

#### 3. source of truth 仍然更偏数据库，不是文件

这是和 `memsearch` / `alive` 最关键的区别。

虽然它会处理 conversation files / source files，但真正的常驻 memory surface 主要是：

- ChromaDB drawers
- ChromaDB closets
- SQLite KG
- `~/.mempalace/identity.txt`

也就是说：

**它是 local-first，但不是 file-canonical。**

这点很重要。

#### 4. `closet` 不是 canonical memory，而是索引化摘要指针层

`palace.py` 和 `closet_llm.py` 说明：

- closets 默认由 regex / heuristics 生成 topic pointer lines
- 可选用 LLM 重建 richer closets
- 但 closets 只是 searchable index layer
- drawers 才是 primary retrieval layer

这让它和 `memsearch` 的 “files canonical, vectors derived” 非常不同。

`mempalace` 更像：

- raw content in drawers
- pointer/signal layer in closets
- search over both

#### 5. 它已经在认真做 compounding，但主要在 retrieval side

`layers.py` 很关键。

这里能看出它不是只有 flat search，而是试图做：

- L0 identity
- L1 essential story（从高 importance drawers 自动生成）
- L2 scoped recall
- L3 full search

这说明它已经意识到：

**长期系统不能每次都只做一次裸 semantic search。**

但这套 compounding 仍然主要服务：

- 更好的 recall
- 更便宜的 wake-up
- 更有层次的 context loading

而不是像 `alive` 或 OVP 那样做 explicit durable knowledge compilation。

#### 6. temporal KG 是真实存在的，不是 marketing add-on

`knowledge_graph.py` 不是空壳。

它确实提供：

- entity nodes
- typed triples
- `valid_from` / `valid_to`
- invalidate
- timeline
- source_closet / source_file provenance

所以 `mempalace` 现在已经不只是 retrieval system，也在向 time-aware memory 演化。

只是这条线还不是它的 primary value prop。

### What is actually strong here

#### 1. 它把 verbatim recall 做到了非常强

这一点没必要拐弯。

从 repo 的 benchmark 和实现来看，它在“找回原话 / 原 session / 原细节”这条线上非常认真：

- raw semantic retrieval
- hybrid keyword boost
- temporal boost
- assistant-reference two-pass retrieval
- LLM rerank

它不是 generic vector search demo，而是把 recall benchmark 当作核心工程目标。

#### 2. layered wake-up 设计很值得注意

很多 recall 系统停在 search API。

`mempalace` 则已经在做：

- bounded startup context
- top moments selection
- room-scoped retrieval
- full deep search

这让它比普通 memory backend 更接近一个 agent memory runtime。

#### 3. 它的 “closet as signal, not gate” 很成熟

`searcher.py` 里这一句我认为很重要：

> Closets are a ranking signal, never a gate.

这是成熟的系统意识。

弱质量的抽象层只应该帮助排名，不应该决定可见性。

这点对所有有 heuristic / extracted layer 的系统都适用。

#### 4. 它是很现实的 local-first engineering

这个 repo 不是在做空洞哲学。

它处理了很多真实问题：

- incremental mine
- normalize_version 重建
- 0-chunk 文件注册
- hook 自动保存
- MCP surface
- i18n entity detection
- benchmark reproducibility

这是比较硬的工程。

### Where it sits on the map

#### 1. 它仍然主要属于 memory backend

这是结论。

虽然它已经长出了很多层，但它的 primary center 仍然是：

- retrieval recall
- session memory
- verbatim content access

而不是：

- file-canonical context
- agent work-inside-context
- compiled durable knowledge

所以在我们的三层地图上，它仍然更接近 Layer 1。

#### 2. 但它是 Layer 1 里更厚的一类

如果简单对比：

- `mem0` 更像 flat fact memory
- `cognee` 更像 productized memory engine
- `Graphiti` 更像 temporal fact engine
- `mempalace` 更像 verbatim recall engine with layered memory surfaces

它的特色不在 fact extraction，也不在 file-canonical substrate，而在：

**verbatim-first recall + layered access + local temporal extensions**

#### 3. 它不是 `alive` 那类 context runtime

这一点要明确。

因为：

- 不以人类可维护文件体系为主真源
- 不强调 authored context protocol
- 不把 agent 工作面建立在持续读写同一套上下文文件上

所以尽管它有 hooks、有 layers、有 local state，它也不该被放进纯 context substrate 那一边。

### What OVP can learn from it

#### 1. 高质量 recall 仍然是独立价值，不该低估

前面几轮容易让人往 “context substrate 才高级” 这边倾斜。

`mempalace` 提醒的一点是：

**raw recall 本身仍然是很硬的系统能力，而且做得好很难。**

OVP 如果以后有 recall/query surface，这条线不能随便糊弄。

#### 2. layer 化的 wake-up 很值得借鉴

`Layer0-3` 这套思路对 OVP 很有价值。

不是照抄实现，而是借这个问题意识：

- always-loaded 的是什么
- scoped recall 的是什么
- deep search 的是什么
- 哪一层是冷启动成本最优的

这点和 `memsearch` 的 progressive disclosure 形成互补。

#### 3. “ranking signal, not gate” 是很好的原则

以后 OVP 如果也有：

- extracted labels
- graph links
- heuristic topic grouping
- lightweight summary layers

那这些层更适合作为 ranking / routing signal，而不是唯一真相入口。

`mempalace` 在这点上做得比很多系统稳。

#### 4. temporal KG 可以是附加层，不必一开始就吞掉主系统

`mempalace` 证明了一种比较轻的做法：

- 主系统继续做自己的 retrieval
- 时间知识图作为补充层接进去

这对 OVP 是个提醒：

有些高级结构不一定非要一开始就变成系统主干，也可以先做附加能力。

### What OVP should not absorb

#### 1. 不要把 OVP 退回数据库中心 memory

这是最大边界。

`mempalace` 的 source-of-truth 更偏 Chroma/SQLite，本质仍然是 DB-centered local memory。

OVP 不应该往这个方向退。

#### 2. 不要把“无摘要、无改写”当成原则

对 `mempalace` 来说，这是一种 deliberate choice，因为它押注 recall fidelity。

对 OVP 来说不成立。

OVP 的核心价值本来就包括：

- interpretation
- absorb
- canonicalization
- artifact generation

所以不能因为 `mempalace` 在 verbatim 上强，就误以为 synthesis 不重要。

#### 3. 不要把 benchmark recall 当成系统全部价值

`mempalace` 的 benchmark-driven discipline 很好。

但 OVP 的目标不是只拿 recall benchmark。

OVP 还要解决：

- durable knowledge quality
- contradiction handling
- explainability
- operator review
- artifact usefulness

这不是一个评价轴。

### Working conclusion from Round 12

`mempalace` 是一个比外部综述里描述得更厚、更完整的系统，但它的主心骨没有变：

**它仍然主要是一个本地、verbatim-first、retrieval-centered memory backend。**

只是它已经从“纯 transcript 搜索”进化成了：

- drawers + closets
- L0-L3 stack
- local temporal KG
- hooks + MCP + agent surfaces

所以如果放进我们的地图：

- 它不属于 context substrate
- 它也不是 flat memory
- 它是 **Layer-1 memory backend 里非常成熟、非常 retrieval-optimized 的一支**

一句话总结：

**`mempalace` 最值得尊重的地方，是它把“找回原话和原上下文”这件事做成了一个厚系统；但它对 OVP 的借鉴主要在 recall layering，不在知识系统内核。**

## File References For This Round

### MemPalace

- `https://github.com/MemPalace/mempalace`
- `/tmp/mempalace/README.md`
- `/tmp/mempalace/website/concepts/the-palace.md`
- `/tmp/mempalace/website/concepts/memory-stack.md`
- `/tmp/mempalace/website/concepts/knowledge-graph.md`
- `/tmp/mempalace/benchmarks/HYBRID_MODE.md`
- `/tmp/mempalace/hooks/README.md`
- `/tmp/mempalace/mempalace/palace.py`
- `/tmp/mempalace/mempalace/searcher.py`
- `/tmp/mempalace/mempalace/miner.py`
- `/tmp/mempalace/mempalace/convo_miner.py`
- `/tmp/mempalace/mempalace/layers.py`
- `/tmp/mempalace/mempalace/closet_llm.py`
- `/tmp/mempalace/mempalace/knowledge_graph.py`

## Round 13: `SuperagenticAI/metaharness`

### First judgment

`metaharness` 有意义，但它和前面大多数项目不是同一层的问题。

它不是：

- memory system
- context substrate
- resolver runtime
- knowledge compilation engine

它本质上是：

**一个 outer-loop harness optimizer。**

也就是把：

- `AGENTS.md`
- `GEMINI.md`
- bootstrap scripts
- validation scripts
- routing glue code
- benchmark harness

这些“围绕 agent 的可执行支撑层”当成优化对象，用另一个 agent 去反复改、测、比、保留。

所以它值得看，但它对 OVP 的启发主要在 **优化方法**，不在系统内核。

### What it is actually doing

#### 1. 它优化的不是模型本身，而是 harness

README 和 `docs/architecture.md` 都很清楚：

- baseline workspace
- proposer backend 修改 workspace
- validator / evaluator 判定结果
- keep best candidate
- 所有 artifacts 存盘

也就是说它不是在做：

- online agent orchestration
- runtime routing
- memory accumulation

它做的是：

**把 prompt 外围的文件和脚本，变成一个可实验、可迭代、可比较的搜索空间。**

#### 2. 它的 source of truth 是 filesystem run store

这点和很多调度系统不太一样。

`FilesystemRunStore` 做的事情很明确：

- materialize baseline
- clone candidate workspace
- 写 `.metaharness/bootstrap`
- 写 prompt / instructions bundle
- 捕获 diff
- 保存 validation / evaluation / manifest / leaderboard

所以它是一个非常 inspectable 的 outer-loop system，而不是“黑箱自动调优”。

#### 3. 它更像 benchmark/optimization infrastructure

`coding_tool` integration 说明它现在最强的 domain 是：

- instruction files
- helper scripts
- deterministic checks
- coding-tool workflows

task 类型目前也比较直接：

- `file_phrase`
- `command`

这就说明它当前的 sweet spot 不是通用智能系统，而是：

**对 coding-agent harness 做可重复的 deterministic optimization。**

#### 4. 它和 resolver 的关系是“可优化对象”，不是“实现对象”

这轮最关键的判断是这个。

Garry Tan 那篇 resolver 文讲的是：

- routing governance
- trigger evals
- check-resolvable

`metaharness` 本身并不提供这些 runtime 原语。

它提供的是另一层能力：

**如果你已经有 resolver / AGENTS.md / scripts / evaluations，能不能把它们放进一个外循环里持续改进。**

所以它不是 resolver system，而是 resolver optimizer 的潜在底座。

### What is actually strong here

#### 1. 它把 “harness is code” 这件事做实了

很多人会说：

- prompt 很重要
- instructions 很重要
- bootstrap 很重要

但真正把这些东西当成：

- candidate workspace
- scored artifact
- diffable optimization target

来做的项目不多。

`metaharness` 在这件事上是认真的。

#### 2. 它很适合优化外围治理文件

这也是它和我们当前调研最相关的地方。

resolver 文章讲了一堆东西：

- `AGENTS.md`
- `RESOLVER.md`
- filing rules
- trigger descriptions
- check-resolvable

这些东西大部分本质上都是：

- 文档
- 规则
- 脚本
- 评测

而这些恰好是 `metaharness` 擅长的优化对象。

#### 3. 它的 artifacts discipline 很好

对于任何 serious optimization system，这些都很重要：

- 候选版本可回看
- diff 可检查
- keep/discard 有证据
- run 可比较
- 失败类型显式分类

`metaharness` 在这点上很成熟。

### Where it sits on the map

这轮最重要的结论是：

#### 1. 它不在 memory/context 这张地图里

至少不直接在。

前面的三层地图是：

- memory backends
- context substrates / runtimes
- context packaging / serving / governance

`metaharness` 本身不属于其中任何一层本体。

它更像一条垂直能力：

**harness optimization / evaluation infrastructure**

也就是给其他层做“外循环改良”。

#### 2. 它最接近 governance tooling

如果硬要放到我们前面的讨论脉络里，它更接近：

- resolver governance
- eval discipline
- artifact-driven optimization

而不是 runtime。

所以它最好被看成：

**meta-layer tooling for improving governance artifacts**

### What OVP can learn from it

#### 1. OVP 以后可以把 resolver / recipe / instructions 当优化对象

这是它对 OVP 最现实的价值。

如果 OVP 后面真的形成了：

- `RESOLVER.md`
- ingest recipes
- filing rules
- artifact generation prompts
- review / absorb scripts

那这些东西不该永远靠手工拍脑袋维护。

理论上完全可以像 `metaharness` 这样：

- baseline
- mutate
- run deterministic evals
- keep/discard

把治理层也纳入可优化闭环。

#### 2. routing evals 和 harness optimization 可以结合

前一轮 resolver 文提出：

- trigger evals
- check-resolvable

`metaharness` 则给出一个潜在方法：

- 把这些 evals 变成 objective
- 让 proposer 去改 routing docs / trigger descriptions / support scripts
- 用外循环找到更好的 harness

这一点非常有启发。

#### 3. 它提醒 OVP：外围系统也值得工程化

OVP 很容易把注意力都放在：

- truth model
- pipeline
- artifact generation

但 `metaharness` 提醒的一点是：

**外围的 instructions、bootstrap、validation、workflow glue 也值得成为一等工程对象。**

这对长期系统质量很重要。

### What OVP should not absorb

#### 1. 不要把它误当成 OVP 产品内核方向

这是最大边界。

`metaharness` 不是在回答：

- knowledge should be modeled how
- context should accumulate how
- artifacts should be compiled how

它回答的是：

- 你怎么系统化地改进 agent harness

这不是同一个问题。

#### 2. 不要过早把 OVP 变成 benchmark farm

`metaharness` 的方法很强，但也有前提：

- 目标函数清楚
- validation 足够 deterministic
- task 能被结构化评估

OVP 现在如果还在定义核心语义，过早搞一整套 meta-optimization 可能太早。

#### 3. 不要把 deterministic score 误当成全部质量

对 coding-tool benchmark 这很合理。

对 OVP 来说，很多核心价值还是：

-知识正确性
- 解释质量
- artifact usefulness
- operator trust

这些未必能被简单 file_phrase / command score 覆盖。

### Working conclusion from Round 13

`metaharness` 不是 resolver 系统，也不是 memory/context 系统。

它真正有价值的地方是：

**把 agent harness 本身变成可优化、可评分、可存档的工程对象。**

如果压成一句话：

**它不是 OVP 的内核参考物，但它可能是 OVP 未来优化 resolver、recipes、instructions、validation harness 的方法论参考物。**

也就是说：

- 对当前产品定义：帮助有限
- 对未来治理层优化：很有价值

## File References For This Round

### MetaHarness

- `https://github.com/SuperagenticAI/metaharness`
- `/tmp/metaharness/README.md`
- `/tmp/metaharness/docs/architecture.md`
- `/tmp/metaharness/docs/official-comparison.md`
- `/tmp/metaharness/docs/alignment.md`
- `/tmp/metaharness/src/metaharness/core/engine.py`
- `/tmp/metaharness/src/metaharness/store/filesystem.py`
- `/tmp/metaharness/src/metaharness/integrations/coding_tool/runtime.py`
- `/tmp/metaharness/examples/ticket_router/README.md`

## Round 14: `Dynamis-Labs/no-escape`

### First judgment

`no-escape` 不是又一个 memory / context 工具。

它本质上是：

**一个对 memory systems 提出理论约束的研究仓库。**

更准确地说，它试图证明：

**只要一个记忆系统按“语义相近应该表示更相近”这条原则组织信息，就必然会出现遗忘、干扰、错误召回或 partial retrieval。**

所以这轮的价值不是“学它怎么做产品”，而是：

**它给前面所有产品讨论加了一个上界条件。**

### What it is actually doing

README、GitHub 页面、`docs/PROJECT_CONTEXT.md` 和 `docs/EXPERIMENT_LOG.md` 说得很一致。

它测试了五类 memory architecture：

1. vector DB  
2. attention-based context memory  
3. filesystem agent memory  
4. graph-based memory  
5. parametric memory  

然后试图证明一个中心结论：

如果系统满足他们定义的 `Semantic Proximity Property`，那么以下现象不可避免：

- forgetting
- false recall
- tip-of-the-tongue / partial retrieval

这不是产品工程 repo，而是：

- theorem verification
- architecture comparison
- experiment reproduction
- negative result / impossibility framing

### Why this matters for our survey

#### 1. 它直接修正了“memory tools 只是在工程上好坏不同”这种直觉

前面很多项目的讨论，很自然会落到：

- retrieval 好不好
- context 组织对不对
- file/native vs db/native
- temporal invalidation 有没有做

这些当然都重要。

但 `no-escape` 提醒的一点是：

**某些 failure mode 可能不是“这个团队做得不够好”，而是“你一旦按语义组织，就会付出代价”。**

这个视角非常重要。

#### 2. 它让 memory backend / context substrate 的分野更细了

前面的 “Two Camps” 那篇文章，把世界切成：

- recall systems
- compounding systems

这很有用。

`no-escape` 则补了一条新的轴：

- 你是否依赖 semantic proximity 作为核心组织原则

如果依赖，就会有 interference cost。

这意味着：

- memory backends 会中招
- graph memory 会中招
- attention / parametric memory 只是换一种方式中招
- 连所谓 filesystem memory，只要最终还是通过 semantic relevance 来取，也仍然要面对这个问题

所以这篇不是推翻前面的地图，而是给地图加了一个更深的物理约束。

#### 3. 它最有意思的不是攻击 vector DB，而是攻击“meaningful memory”本身

这点值得注意。

仓库不是在说：

- vector DB 不行，换 graph 就好
- graph 不行，换 files 就好
- RAG 不行，换 parametric 就好

它在说：

**只要你试图让系统按 meaning 组织，你就会带来竞争、混淆、替代、遗忘。**

这个野心比普通 memory critique 大得多。

### What the code and experiments suggest

#### 1. 它不是空口理论，确实做了多架构实验

本地代码里能看到五个 architecture 实现：

- `vector_db.py`
- `attention_memory.py`
- `filesystem_memory.py`
- `graph_memory.py`
- `parametric_memory.py`

每种都跑：

- Ebbinghaus forgetting
- DRM false recall
- spacing
- TOT

而 `docs/EXPERIMENT_LOG.md` 里也明确区分了：

- 几何层面的普适性
- 行为层面的架构差异

这个区分其实很重要，也比单纯“都一样糟”更成熟。

#### 2. 它对 filesystem memory 的定义很窄

这是一个必须注意的限制。

它的 “filesystem memory” 在代码里并不是 `alive` / `arscontexta` / OVP 这类 file-native context runtime。

`filesystem_memory.py` 做的是：

- JSON records
- BM25 keyword retrieval
- Qwen rerank

这更像：

**keyword-indexed local memory**

而不是：

**agent 在显式上下文文件里工作、写回、累积**

所以不能简单把它的 filesystem 结果外推到所有 file-native context systems。

#### 3. 它对语义系统的 critique 更像“interference theorem”

我会把它理解成：

**任何 semantic retrieval / semantic organization 都存在 unavoidable interference tradeoff。**

但这不等于：

**所有 context substrate 都失败。**

因为 context substrate 的价值不只来自 retrieval。

它还来自：

- human inspectability
- explicit authored state
- layering
- governance
- resolvers
- review loops

这些维度，`no-escape` 这个 repo没有真正覆盖。

### What OVP can learn from it

#### 1. 不要幻想“完美语义记忆”

这是最直接的启发。

无论 OVP 将来做：

- semantic search
- graph-based recall
- topic clustering
- object linking
- automated suggestion

都不应该以为只要 embedding / graph / rerank 更强，就能消灭干扰。

更现实的思路应该是：

- 承认 interference
- 设计 surfacing / review / provenance
- 设计 correction loops

#### 2. OVP 应该把 explainability 和 source trace 做得更强

如果 semantic confusion 是系统性风险，那么最重要的不是假装不会错，而是：

- 错了能看见
- 为什么召回这个能解释
- 来源能追
- operator 能纠正

这和前面 `TrustGraph`、resolver 那篇、还有 OVP 自己的 canonical/derived 思路是同方向的。

#### 3. keyword / exact-match / symbolic constraints 仍然有价值

`no-escape` 的 filesystem 结果虽然定义比较窄，但它至少说明一件事：

**你越靠 exact match / explicit symbolic constraints，semantic interference 越低。**

这意味着 OVP 在一些高精度路径上，应该考虑：

- exact ID lookup
- explicit object matching
- schema-constrained retrieval
- resolver-routed narrowing

而不是所有东西都交给 semantic similarity。

#### 4. OVP 的价值可能恰好在于“不要把所有事情都变成语义近邻搜索”

这是我认为和 OVP 最相关的一点。

OVP 之所以值得做，不是因为它能做更好的 embedding recall。

而是因为它可能通过：

- canonical objects
- explicit claims / evidence
- contradiction tracking
- staged absorb
- human-readable artifacts
- resolver-governed routing

把一部分问题从“纯语义空间竞争”里拿出来。

这不意味着完全逃脱 no-escape。
但意味着可以不把系统全部押在 semantic retrieval 上。

### What OVP should not absorb

#### 1. 不要把这篇论文误解成“所以 context substrate 没意义”

这是最容易犯的错误。

这个 repo 的结论更像是：

**semantic organization 有成本。**

不是：

**所有长期上下文系统都没意义。**

#### 2. 不要把它的 filesystem baseline 误当成 file-native runtime 的代表

这点必须强调。

它测的 filesystem memory 更像 BM25 + LLM rerank 的 local memory。

跟：

- `alive`
- `arscontexta`
- `memsearch`
- OVP

这类以文件为长期工作表面的系统，不是同一个对象。

#### 3. 不要被“理论不可能”吓到放弃工程治理

相反，越是有 no-escape 这种约束，越说明：

- resolver
- provenance
- review loop
- contradiction surface
- explicit contracts

这些治理层越重要。

### Working conclusion from Round 14

`no-escape` 对这轮调研最重要的意义，不是给了一个新产品方向，而是给了一个新的约束：

**任何依赖 semantic proximity 的 memory system，都有不可消除的 interference tax。**

这对 memory backend 是直接警告。
对 context substrate 不是直接否定，而是提醒：

- 不要把系统全押在语义检索上
- 要靠显式结构、治理、审查和可追溯性来管理 inevitability

一句话总结：

**`no-escape` 不是 OVP 的设计参考物，而是 OVP 的“物理定律参考物”。它告诉我们哪些问题不可能靠更强 embedding 单独解决。**

## File References For This Round

### No Escape

- `https://github.com/Dynamis-Labs/no-escape`
- GitHub page: `https://github.com/Dynamis-Labs/no-escape`
- `/tmp/no-escape/README.md`
- `/tmp/no-escape/docs/PROJECT_CONTEXT.md`
- `/tmp/no-escape/docs/EXPERIMENT_LOG.md`
- `/tmp/no-escape/noescape/architectures/vector_db.py`
- `/tmp/no-escape/noescape/architectures/filesystem_memory.py`
- `/tmp/no-escape/noescape/architectures/graph_memory.py`

## Round 15: `campfirein/byterover-cli`

### First judgment

`byterover-cli` 不是一个普通 memory plugin。

它更像是：

**一个把 context tree、agentic curation、review queue、dream consolidation、version control 和 multi-source query 打包在一起的 context curation product。**

跟前面这些项目相比，它最接近的不是：

- `mem0`
- `mempalace`
- `supermemory`

而更接近：

- `alive` 的 runtime surface
- `memsearch` 的 file-canonical boundary
- `TrustGraph` 的 context artifact / governance 感
- 以及 Garry Tan 那篇里 resolver/governance 的产品化倾向

但它本身比这些都更“产品壳重”。

### What it is actually doing

README、paper README 和 CLI 命令结构说明得很一致。

它试图把 agent memory / context 系统压成一套完整产品动作：

- `search`：无 LLM、纯 BM25 的 deterministic search
- `query`：自然语言查询，基于 provider 做综合回答
- `curate`：把 context 写进 context tree
- `review`：对高影响 curation 做 pending / approve / reject
- `dream`：对 context tree 做 consolidate / synthesize / prune
- `vc`：对 context tree 做 init / add / commit / branch / merge / push / pull
- `worktree`：多工作树管理
- `swarm`：跨知识源查询与 provider routing

所以它的中心不是“记住聊天内容”，而是：

**经营一棵可写、可审、可整理、可版本化的 context tree。**

### What the code suggests

#### 1. 它的长期真源是 file-based context tree，不是 hidden DB

`file-context-tree-service.ts` 很直接：核心目录就是 `.brv/context-tree/`。

`file-context-tree-snapshot-service.ts` 说明它会：

- 扫描 `.md` 文件
- 做 hash snapshot
- 比较 added / modified / deleted
- 把 snapshot 存回文件

`file-context-tree-writer-service.ts` 说明 sync/pull 也是直接对树上的文件做增删改。

这说明它的主语义面仍然是：

**文件树是工作表面。**

这点非常重要。

#### 2. 但它不是纯 file-native runtime，而是“file tree + product daemon + orchestration shell”

虽然真源是 files，但它明显不是像 `alive` 那样尽量轻薄。

它有：

- daemon transport
- provider layer
- TUI
- review API / UI
- worktree / VC / remote
- swarm query graph

所以更准确地说，它不是朴素 file runtime，而是：

**围绕 file-canonical context tree 建了一整套 orchestration product shell。**

#### 3. 它把 search、query、curate 明确拆开了

这是它最成熟的地方之一。

从 CLI 和 prompt 资源能看出三条边界：

- `search`：README 和 `search.ts` 都强调 `Pure BM25 retrieval — no LLM`
- `query`：允许 provider 参与综合回答
- `curate`：通过 agent / tools.curate 写入 context tree

这相当于把三个经常被混在一起的动作拆开了：

- 找
- 答
- 写回

这对 OVP 很有借鉴意义。

#### 4. 它把 review 作为一等流程，而不是附属日志

`curate/index.ts` 和 `review/pending.ts` 很清楚：

- curate 可以产生 `needsReview`
- review 有 pending list
- 可 approve / reject
- 高影响 operation 会单独 surfaced

这说明它不是“LLM 写了就算”，而是：

**curation 是有治理面的。**

这点比很多 memory 产品都成熟。

#### 5. 它的 dream 不是 marketing 概念，是真实后台整理链路

`consolidate.ts`、`synthesize.ts`、`prune.ts` 都是实实现。

这条链大致是：

- consolidate：按 domain 聚合 changed files，找 related files，LLM 判断 MERGE / TEMPORAL_UPDATE / CROSS_REFERENCE / SKIP
- synthesize：基于 domain `_index.md` 做 cross-domain synthesis
- prune：基于 importance decay + staleness 找候选，再 archive / keep / merge

这比“梦境”这种叙事更具体：

**它其实是在做树上的持续整理与层级维护。**

#### 6. 它有明显的 derived-artifact discipline

`derived-artifact.ts` 明确区分：

- derived artifacts
- archive stubs
- excluded-from-sync content

比如：

- `_index.md`
- `_manifest.json`
- `_archived/*.full.md`

这些不进入 snapshot/sync 主路径。

这说明它已经开始认真处理：

**canonical tree 和 derived artifacts 的边界。**

这也是 OVP 一直要守住的东西。

### Why this matters for our survey

#### 1. 它补上了“context substrate 走向产品化之后会长什么样”

前面：

- `alive` 更像 runtime spec
- `memsearch` 更像 semantic access layer
- `TrustGraph` 更像 infrastructure packaging

`byterover-cli` 则更像：

**当你真想把这些东西做成一套 agent-facing 产品，它大概会长成什么样。**

#### 2. 它把“context engineering”做成了操作面，而不是只做概念

它不是只说：

- context 很重要
- memory 不够

而是给出了实际操作层：

- curate
- review
- dream
- vc
- swarm

这让它比很多“概念正确”的项目更像真实工作系统。

#### 3. 它说明 context substrate 也可以有很强 governance，不必等于松散 markdown

很多 file-native 系统容易被误解成：

- 只是几堆 markdown
- 靠 agent 自觉读写
- 结构靠约定维持

`byterover-cli` 给出的另一种方向是：

**文件真源仍然成立，但治理层可以做得很厚。**

### What OVP can learn from it

#### 1. OVP 值得认真考虑“curation as a first-class operation”

这轮里最值得吸收的不是 UI，而是动词设计。

OVP 现在更像 pipeline 语义：

- ingest
- absorb
- derive
- review

`byterover-cli` 提醒的一点是：

**“curate” 也许应该成为用户和 agent 都能直接感知的一等动作。**

因为它准确表达了：

- 不是原样保存
- 不是最终 truth
- 而是把材料整理进上下文结构

#### 2. OVP 可以把 deterministic search 和 synthesized query 分得更清楚

`byterover-cli` 在这点上是对的：

- search 是 search
- query 是 query

OVP 也应尽量避免把：

- exact lookup
- semantic retrieval
- synthesized answer

混成一个入口。

#### 3. Review queue 非常值得借

这可能是这轮最具体、最可落地的借鉴点。

OVP 后面如果有：

- auto-generated object updates
- contradiction merges
- MOC rewrites
- atlas / overview refresh

其中一部分完全可以走：

**pending review queue**

而不是“全自动直接落地”或者“全人工手工改”这两头。

#### 4. Dream 更值得借的是“后台整理职责分解”，不是名字

它把后台整理拆成：

- consolidate
- synthesize
- prune

这个分解很有价值。

对 OVP 来说，这比笼统说“后台 consolidation”更清楚，也更容易做 artifact 级治理。

#### 5. Derived-artifact exclusion discipline 值得直接参考

`_index.md`、`_manifest.json`、archive full files 这些被排除出 sync/snapshot 的做法很稳。

OVP 自己也应该继续把：

- canonical notes
- derived views
- archive stubs
- generated summaries

区分得更硬。

### What OVP should not absorb

#### 1. 不要把 OVP 带偏成又一个 provider-heavy memory product

`byterover-cli` 明显带着产品壳：

- provider config
- daemon
- cloud sync
- remote
- TUI
- swarm providers

这对它自己的产品路线合理，但不应该成为 OVP 核心方向。

#### 2. 不要把“context tree”当成唯一正确的知识形状

tree 是一种有用的治理表面，但 OVP 的价值未必在于把知识都压成树。

OVP 仍然更偏：

- canonical object / claim / evidence
- atlas / topic / overview
- contradiction / review
- derived exports

所以借它的动作和治理层，比借它的抽象名字更重要。

#### 3. 不要把 query UX 误当成知识内核

它的 query / swarm 很强，但这更像 access layer。

OVP 不该因此把重点从：

- absorb correctness
- canonical discipline
- traceability

转移到“更酷的问答入口”上。

### Working conclusion from Round 15

`byterover-cli` 不是简单的 memory backend，也不是纯 context substrate。

我会把它归类为：

**一个 file-canonical、review-governed、dream-maintained、versioned context curation platform。**

它对 OVP 最有价值的不是“又一个 context tree”，而是这几个更硬的工程点：

- curate 作为一等动作
- search / query / write-back 边界分离
- review queue
- background consolidation responsibilities
- canonical vs derived exclusion discipline
- context artifact 的 versioning

一句话总结：

**`byterover-cli` 是目前这轮里最像“把 context substrate 产品化之后会长什么样”的样本之一。对 OVP 最值得借的是治理和动作分层，不是它整套产品壳。**

## File References For This Round

### ByteRover CLI

- `https://github.com/campfirein/byterover-cli`
- `/tmp/byterover-cli/README.md`
- `/tmp/byterover-cli/paper/README.md`
- `/tmp/byterover-cli/src/oclif/commands/search.ts`
- `/tmp/byterover-cli/src/oclif/commands/query.ts`
- `/tmp/byterover-cli/src/oclif/commands/curate/index.ts`
- `/tmp/byterover-cli/src/oclif/commands/review/pending.ts`
- `/tmp/byterover-cli/src/oclif/commands/dream.ts`
- `/tmp/byterover-cli/src/oclif/commands/vc/commit.ts`
- `/tmp/byterover-cli/src/server/infra/context-tree/file-context-tree-service.ts`
- `/tmp/byterover-cli/src/server/infra/context-tree/file-context-tree-snapshot-service.ts`
- `/tmp/byterover-cli/src/server/infra/context-tree/file-context-tree-writer-service.ts`
- `/tmp/byterover-cli/src/server/infra/context-tree/derived-artifact.ts`
- `/tmp/byterover-cli/src/server/infra/dream/operations/consolidate.ts`
- `/tmp/byterover-cli/src/server/infra/dream/operations/synthesize.ts`
- `/tmp/byterover-cli/src/server/infra/dream/operations/prune.ts`
- `/tmp/byterover-cli/src/agent/resources/prompts/system-prompt.yml`

## Round 16: `vercel-labs/open-agents`

### First judgment

`open-agents` 对 OVP **帮助不大**。

不是因为它差，而是因为它解决的问题基本不是 OVP 在解决的问题。

更准确地说，它是：

**一个 cloud coding agent reference app / runtime template。**

不是：

- memory system
- context substrate
- knowledge compilation system
- vault workflow

### What it is actually doing

README 和架构文档都很直白。

它的核心结构是：

```text
Web -> Agent workflow -> Sandbox VM
```

也就是三层：

- web app：auth、session、chat UI、streaming
- agent workflow：durable workflow run
- sandbox VM：filesystem、shell、git、preview ports

它最强调的架构点是：

**agent 不在 sandbox 里运行。**

agent 在外面跑，通过 tools 操作 VM。

所以它真正关心的是：

- durable execution
- cloud sandbox lifecycle
- GitHub integration
- repo clone / branch / PR
- hosted app auth / deployment

### What the code suggests

#### 1. 它本质上是 open-harness，不是 knowledge system

`package.json` 名字就是 `open-harness`。

`docs/agents/architecture.md` 也直接写了：

- Web
- Agent
- Sandbox

这说明 repo 的真正中心是：

**agent runtime + cloud execution harness**

而不是任何长期知识层。

#### 2. 它的“context management”是 token/context-window 管理，不是知识沉淀

`packages/agent/context-management/` 这块不是 vault/runtime memory。

它更偏：

- cache control
- compaction
- message context handling

也就是：

**对当前 agent run 的上下文预算管理。**

这和 OVP 的长期知识编译，不是一回事。

#### 3. 它真正有工程含量的是 sandbox 抽象

`packages/sandbox/vercel/sandbox.ts` 很清楚：

- 用 `@vercel/sandbox`
- 持久 sandbox session
- timeout / hibernation / reconnect
- GitHub credential brokering
- snapshot-based resume

这部分对“云上 coding agent 怎么跑”很有参考价值。

但对 OVP 的核心问题：

- knowledge absorb
- canonical/derived boundary
- reviewable knowledge artifacts
- vault-native compounding

帮助很有限。

### Why it has limited relevance for OVP

#### 1. 它主要解决 compute/runtime，不解决 knowledge/runtime

这是最关键的边界。

OVP 更关心：

- source 怎么变成 knowledge
- knowledge 怎么进入 canonical layer
- derived views 怎么生成
- operator 怎么 review / correct

`open-agents` 更关心：

- agent 怎么持续跑
- VM 怎么挂起恢复
- GitHub PR 怎么自动化
- UI 和 workflow 怎么对接

两者的重心差很多。

#### 2. 它没有给 OVP 想要的长期上下文答案

它没有认真回答：

- durable context objects 长什么样
- truth / evidence / contradiction 怎么维护
- file-canonical knowledge 如何积累
- review queue 如何作用于知识层

所以不能把它当作 OVP 的 memory/context 参考物。

#### 3. 它更像 deployment/runtime shell 样本

如果以后 OVP 想做 hosted agent 或 remote execution，
那它有价值。

但如果当前问题还是：

- OVP 的知识系统怎么长
- artifact 怎么分层
- runtime methodology 怎么定义

那它基本不在关键路径上。

### What OVP can still learn from it

#### 1. Agent 和 execution environment 解耦

这是它最硬的架构点。

如果 OVP 将来要接：

- remote workers
- background jobs
- hosted ingestion / review runs

那么：

**control plane 不要塞进执行环境里**

这点是值得记住的。

#### 2. Durable workflow / resumability

它对长任务的处理方式是成熟的：

- workflow-backed execution
- resume
- cancellation
- hibernating sandbox

如果 OVP 后面出现长时间 ingest / compile / review 流程，
这类 runtime pattern 有借鉴价值。

#### 3. Hosted product shell 的完整度

它让人看清一件事：

**一旦你把 agent 系统产品化，auth、session、repo access、sandbox lifecycle、streaming、PR integration 会迅速吃掉大量复杂度。**

这对 OVP 是一个范围提醒：

不要轻易把自己带进 hosted coding-agent product 赛道。

### What OVP should not absorb

#### 1. 不要把它误判成 memory/context 项目

它不是。

#### 2. 不要为了“看起来完整”把 OVP 带到 web app + cloud sandbox + GitHub automation 的方向

这会直接改变产品性质。

#### 3. 不要把 context-window compaction 当成长期知识架构

这是 run-time prompt management，不是 vault-native accumulation。

### Working conclusion from Round 16

`open-agents` 是一个不错的：

**cloud coding agent runtime reference**

但不是 OVP 现在最相关的样本。

一句话总结：

**对 OVP 来说，它最多提供一些 remote execution / durable workflow 的 runtime 启发；在知识系统、context substrate、artifact governance 这些核心问题上，帮助很有限。**

## File References For This Round

### Open Agents

- `https://github.com/vercel-labs/open-agents`
- `/tmp/open-agents/README.md`
- `/tmp/open-agents/docs/agents/architecture.md`
- `/tmp/open-agents/packages/sandbox/vercel/sandbox.ts`
- `/tmp/open-agents/packages/agent/context-management/index.ts`
- `/tmp/open-agents/package.json`

## Round 17: “Open Harnesses, Open Memory” Framing Piece

### First judgment

看过了。

这篇是有价值的，但它的价值不在实现细节，而在于：

**它把“memory ownership”明确提升成了 harness choice 的后果。**

也就是说，它不是在讨论一个普通产品 feature，而是在说：

**你选什么 harness，基本就决定了你是否真正拥有自己的 agent memory。**

这个判断对。

### What it is actually arguing

这篇文章的核心链条很简单：

1. agent harness 会长期存在，不会被模型吃掉  
2. harness 本质上负责 context/state 的管理  
3. memory 不是外挂插件，而是 harness 的内在职责  
4. 所以 closed harness，尤其 API-only harness，会把 memory ownership 一起锁走  
5. memory 是 sticky agent experience 的关键，因此这会形成极强 lock-in  

它最值得注意的地方，不是某个例子，而是这个结构本身。

### Why this matters for our survey

#### 1. 它把前面几轮零散的观察压成了一个更高层的战略命题

前面我们已经分别看到：

- `alive` 强调 runtime discipline
- resolver 那篇强调 routing/governance
- `byterover-cli` 强调 reviewed curation + versioned context artifacts
- Witcheer 那篇强调 memory backend 和 context substrate 的分野

这篇文章则把这些东西向上收束成一句话：

**memory 不只是数据层，memory 是 harness governance 的一部分。**

这点非常重要。

#### 2. 它纠正了“memory service 可随便插拔”的乐观叙事

这篇最有力的句子其实是：

> memory isn’t a plugin, it’s the harness

这个表述虽然有点绝对，但方向是对的。

原因很简单：

- 记忆怎么进 prompt
- 哪些东西 survive compaction
- skill metadata 怎么显示
- AGENTS.md / CLAUDE.md 怎么加载
- filesystem 状态怎么表示
- 跨 session state 怎么读写

这些都不是一个独立 memory DB 自己能决定的。

它们是 harness contract。

#### 3. 它把“open vs closed”问题从 ideology 拉回到了 portability 和 artifact ownership

这篇最强的一点，不是骂闭源，而是指出：

**只要 state/memory 被 provider 私有化，你就失去了模型可切换性、线程可迁移性、memory 可解释性。**

这比“开源更自由”那种泛泛论述要扎实得多。

### What I think it gets right

#### 1. Harness 不会消失

这一点我认同，而且和我们前面看 `open-agents`、resolver、`metaharness` 的判断一致。

模型可能吃掉一部分低层编排，但不会吃掉：

- tool mediation
- context routing
- state management
- approval / review
- persistence contracts
- artifact surfacing

所以 harness 不是过渡阶段产物，而是长期层。

#### 2. Memory 和 harness 确实强耦合

这点也对。

至少在今天这个阶段，memory 还远不是一个标准化得足以彻底插拔的层。

因为真正决定 memory 行为的，不只是“存在哪里”，而是：

- 什么时候写
- 写成什么形状
- 什么时候读
- 读到哪里
- 何时压缩
- 何时遗忘
- 何时变成长期状态

这些都属于 harness。

#### 3. Provider-managed state 会制造 lock-in

这一点几乎是事实判断，不是观点。

只要：

- thread state
- compaction summary
- long-term memory
- interaction history

被 provider 私有协议包住，迁移成本就会上升。

这正是 OVP 这种 file-native / artifact-native 路线的战略意义之一。

### Where I would narrow it

#### 1. “memory 不是 plugin” 方向对，但不该被理解成 memory system 毫无独立价值

更准确地说应该是：

**memory system 可以独立存在，但只有进入 harness contract 之后才真正生效。**

也就是说：

- memory store 可以独立
- retrieval layer 可以独立
- provenance layer 可以独立

但它们不能脱离 harness 的 context/state rules 被理解。

#### 2. 这篇更像战略/治理文章，不是架构蓝图

它讲清楚了为什么要 open harness。

但它没有细讲：

- artifact boundary 怎么定
- review loop 怎么跑
- canonical vs derived 怎么分
- resolver 怎么设计
- contradiction 怎么处理

所以它对 OVP 的价值是 framing，不是 implementation recipe。

### What OVP can learn from it

#### 1. OVP 应该明确把“memory ownership / artifact ownership”当成产品原则

这篇最适合 OVP 吸收的不是某个技术点，而是一条原则：

**用户必须拥有长期状态。**

对 OVP 来说，这意味着：

- canonical knowledge 要是用户可读、可改、可迁移的
- derived artifacts 要可重建
- state 不能只活在 provider API thread 里
- 关键 compaction / summary / review 结果不能是私有黑盒格式

#### 2. OVP 应该继续避免 provider-locked state

这和前面的很多轮是一致的。

如果 OVP 想保住自己的意义，就不该把核心状态绑到：

- provider thread ids
- 私有 compaction blobs
- 不可解释的 server-side memory

上面。

#### 3. OVP 可以更明确地区分“harness concerns”和“knowledge concerns”

这篇虽然强调 harness 和 memory 强耦合，但对 OVP 反而有个反向启发：

**OVP 不一定要自己成为完整 harness，但必须能服务开放 harness。**

也就是说，OVP 更像：

- user-owned knowledge substrate
- artifact compiler
- reviewable memory surface

而不是必须自己包掉全部 agent runtime。

### What OVP should not absorb

#### 1. 不要把这篇变成简单的“开源口号”

真正重要的不是喊 open，而是把：

- portability
- inspectability
- rebuildability
- explicit contracts

做出来。

#### 2. 不要把所有 memory 问题都归结成 harness ownership

ownership 很重要，但：

- truth modeling
- contradiction handling
- evidence discipline
- retrieval precision

这些问题不会因为 harness 开源就自动解决。

### Working conclusion from Round 17

这篇最重要的价值，是给整个调研加了一条战略层判断：

**memory ownership is harness ownership.**

对 OVP 来说，它最值得吸收的不是某个实现，而是一条更明确的定位原则：

**OVP 应该站在 user-owned, portable, inspectable, rebuildable memory artifacts 这一边，而不是 provider-managed black-box state 这一边。**

一句话总结：

**这篇不是新的实现参考物，而是把 OVP 这条路的战略正当性讲清楚了。**

## Round 18: `rohitg00/agentmemory`

### First judgment

`agentmemory` 值得看，而且定位很清楚：

**它是一个跨 harness、跨 agent 共享的 persistent memory engine / memory server。**

它不是：

- context substrate
- file-canonical runtime
- knowledge compilation system

它更接近：

**“可插到很多 agent 上的长期记忆后端，外加一层 hooks + MCP + viewer + coordination surface。”**

### What it is actually doing

README、OpenClaw integration 文档和源码都指向同一个中心：

- 任何支持 hooks / MCP / REST 的 agent 都能接它
- 所有 agent 共享同一 memory server
- 自动捕获 tool use / prompts / session lifecycle
- 压缩成结构化 memory
- 用 BM25 + vector + graph 做检索
- 在新 session 或 LLM 调用前注入 top-K context

所以它本质上在做的是：

**一个 self-hosted memory engine，试图把 memory 从单一 harness 里剥离出来。**

### What the code suggests

#### 1. 它的真源不是 files，而是 state scopes / indexed state

`state/schema.ts` 很清楚，所有核心东西都在 `KV.*` 里：

- sessions
- observations
- memories
- summaries
- semantic / procedural
- graph nodes / edges
- access log
- leases / signals / checkpoints

`state/kv.ts` 则说明这一切是通过 `iii-sdk` 的 state primitives 读写。

这说明它的长期状态面不是 markdown/files，而是：

**KV-scoped memory state。**

#### 2. 它是典型 retrieval-centered memory engine

`functions/search.ts` 和 `state/hybrid-search.ts` 非常清楚：

- BM25 index
- optional vector index
- graph retrieval
- RRF fusion
- token budget
- session diversification
- optional rerank

这是一个非常标准、而且做得挺厚的 retrieval pipeline。

所以它的中心仍然是：

**高质量 recall / injection。**

#### 3. 它的 consolidation 做得比普通 memory backend 更重

`functions/consolidation-pipeline.ts` 不是玩具。

它有：

- semantic consolidation
- procedural extraction
- reflect tier
- decay tier
- auto export to Obsidian

再加上 README 里的 working / episodic / semantic / procedural 四层，
说明它比 mem0 这种“抽 facts 存起来”的系统要厚很多。

但即便如此，它还是 memory engine，不是 substrate。

因为这些层最终都服务于：

- retrieval
- recall
- injection

而不是让 agent 工作在一套人类可直接审阅的长期上下文表面里。

#### 4. 它靠 hooks 把 memory 变成“自动背景行为”

`hooks/session-start.ts`、README 的 hook 表和 OpenClaw integration 说明：

- SessionStart 注册 session，必要时注入 context
- PostToolUse 自动记录 observation
- SessionEnd 完成 session，供后续 consolidation
- PreToolUse / PreCompact 还能做 enrich / reinject

所以它不是要求用户手动 `memory.add()`。

这点是它产品化最强的部分。

#### 5. 它有 coordination surface，但那仍然是 memory-adjacent

它不只有 recall，还有：

- leases
- signals
- actions / routines
- mesh sync
- snapshots

这让它比普通 memory backend 更像一个 shared agent state engine。

但这并没有改变它的核心定位：

**这些功能仍然围绕 memory serving 和 multi-agent coordination，而不是 canonical knowledge compilation。**

### Why this matters for our survey

#### 1. 它是目前这轮里最明确的“open harness-compatible memory engine”样本之一

前一轮 framing piece 讲的是：

**memory ownership is harness ownership**

`agentmemory` 则提供了一个现实世界的回答：

**至少可以先把一部分长期记忆从单一 harness 里抽出来，做成共享的 self-hosted server。**

这点很重要。

#### 2. 它证明了 memory backend 也可以很厚

很多人会把 memory backend 想成：

- add / search / delete
- 一点 embedding
- 一点 extraction

`agentmemory` 不是这样。

它已经长出：

- hook capture
- multi-stream retrieval
- graph expansion
- procedural extraction
- decay / eviction
- real-time viewer
- audit / governance

也就是说：

**memory backend 这条线也能做成厚系统。**

#### 3. 但它依然没有跨到 context substrate 那一边

这是这轮最需要说清楚的边界。

即使它有：

- Obsidian export
- MEMORY.md bridge
- hooks
- MCP

它的世界观依然是：

**agent 在别处工作，memory server 负责捕获、压缩、检索、注入。**

而不是：

**agent 在长期上下文文件/artifact 里面工作并写回。**

所以它不是 `alive`、不是 `arscontexta`、不是 OVP 这条线。

### What OVP can learn from it

#### 1. Capture automation 很值得重视

它最强的现实价值，是把 memory capture 变成背景行为。

OVP 不一定要照抄 hooks 形态，但应该认真思考：

- 哪些信号能自动收集
- 哪些 artifacts 能自动生成候选
- 哪些地方该进入 review queue

而不是把所有写入都设计成手工行为。

#### 2. Search / recall / injection 的 access layer 可以做得很强

即使 OVP 不是 retrieval-centered，它仍然需要好的 access layer。

`agentmemory` 说明：

- BM25
- vector
- graph expansion
- token budget
- session diversification

这些 access engineering 是有产品价值的。

#### 3. Multi-agent coordination signals 值得单独记住

leases / signals / checkpoints / routines 这些东西，对 OVP 核心不是第一优先级，
但如果以后 OVP 接多 agent 协作，这些会比“纯 memory API”更重要。

### What OVP should not absorb

#### 1. 不要把 OVP 退化成 retrieval-and-injection engine

这是最关键的边界。

OVP 的价值不在于：

- 更好地注入 past context
- 更快地 recall past tool use

而在于：

- canonical objects
- evidence / claims
- reviewable artifacts
- durable, human-readable knowledge space

#### 2. 不要把 KV/index state 当成最终知识表面

`agentmemory` 这样做合理，因为它解决的是 memory serving。

但 OVP 不能把自己的长期知识层也埋到这种 internal state 里。

#### 3. 不要把 benchmark-heavy recall 误当成系统全部价值

它的 retrieval engineering 做得不错，但这仍然只是系统价值的一部分。

对 OVP 来说，truth modeling 和 artifact governance 更重要。

### Working conclusion from Round 18

`agentmemory` 是一个做得很完整的：

**open-harness-compatible, self-hosted memory engine**

它最值得 OVP 借的是：

- 自动 capture
- 多流 recall / injection engineering
- memory lifecycle 厚度
- multi-agent coordination primitives

它最不该让 OVP 借偏的是：

- 把长期知识层退化成 KV-backed memory serving
- 把产品重心转成 recall benchmark

一句话总结：

**`agentmemory` 是“跨 harness 共享记忆后端”这条路上的强样本，但它仍然是 memory engine，不是 OVP 这条 file-canonical knowledge substrate 路线的替代物。**

## File References For This Round

### agentmemory

- `https://github.com/rohitg00/agentmemory`
- `/tmp/agentmemory/README.md`
- `/tmp/agentmemory/package.json`
- `/tmp/agentmemory/src/state/schema.ts`
- `/tmp/agentmemory/src/state/kv.ts`
- `/tmp/agentmemory/src/functions/search.ts`
- `/tmp/agentmemory/src/state/hybrid-search.ts`
- `/tmp/agentmemory/src/functions/graph-retrieval.ts`
- `/tmp/agentmemory/src/functions/consolidation-pipeline.ts`
- `/tmp/agentmemory/src/hooks/session-start.ts`
- `/tmp/agentmemory/integrations/openclaw/README.md`
- `/tmp/agentmemory/benchmark/COMPARISON.md`

## Round 19: `EverMind-AI/EverOS`

### First judgment

`EverOS` 值得研究，但要先拆开看。

这个 repo 不是一个单一产品，而是：

**methods + benchmarks + use-cases 的打包仓库。**

对 OVP 真正相关的，不是整个 monorepo，而是其中的：

- `methods/evermemos`（README 里叫 EverCore / EverMemOS）
- `methods/HyperMem`
- `benchmarks/*`
- `examples/openclaw-plugin`

如果只看 OVP 相关性，我会把这轮的核心对象定为：

**EverMemOS / EverCore：一个偏重 extraction、structured memory construction 和 multi-strategy retrieval 的 memory operating system。**

### What it is actually doing

根 README 的定位已经很明确：

EverOS 想做的是一整套长期记忆生态：

- 方法：EverCore、HyperMem
- benchmark：EverMemBench、EvoAgentBench
- use case：Claude Code plugin、OpenClaw plugin、demo apps

所以它和前面很多单 repo 项目不一样。

它不是一个具体 runtime shell，而更像：

**一个长期记忆方法论与验证体系仓库。**

### What the code suggests

#### 1. EverMemOS 的核心不是 file-native substrate，而是 database/search-native memory OS

`docs/ARCHITECTURE.md` 讲得非常直接：

- MongoDB
- Elasticsearch (BM25)
- Milvus (vector)
- Redis

`search_mem_service.py` 也印证了这一点：

- keyword: ES
- vector: Milvus
- hybrid
- rrf
- agentic multi-round retrieval

这说明它的主语义面不是文件，而是：

**数据库 + 检索系统上的结构化 memory OS。**

#### 2. 它把 memory construction 做得非常显式

这是这轮最值得注意的点。

`memory_manager.py`、架构文档和 overview 都围绕同一个中心：

- MemCell 作为 atomic memory unit
- 从 conversation 中抽取 MemCell
- 再往上构造 episode / profile / atomic fact / foresight / agent skill 等 memory types

也就是说，它比很多 memory backend 更强调：

**memory 不是直接存 conversation，而是先做 structured construction。**

这点对 OVP 是相关的。

#### 3. 它是 retrieval-heavy，但不是只有 retrieval

它当然很重视 recall：

- BM25
- vector
- rerank
- RRF
- agentic multi-round recall

但它和纯 recall 系统不同的地方在于：

**它前面先做了 memory construction / classification / typing。**

所以更准确地说，它是：

**construction-first, retrieval-heavy 的 memory OS。**

#### 4. 它有强 benchmark 意识

这轮和其他 repo 不同的一点是：

它不只是给方法，还给 benchmark：

- EverMemBench：memory quality
- EvoAgentBench：agent self-evolution

这说明他们在推动的不只是实现，而是一套 evaluation worldview。

这点对 OVP 很有启发，因为前面很多项目都是“有实现，缺统一尺子”。

#### 5. OpenClaw 插件位置说明了它的接入方式

`examples/openclaw-plugin/README.md` 很关键：

- 它不是 memory slot plugin
- 它是 `context-engine` plugin
- `assemble()` 前检索记忆
- `afterTurn()` 后写回记忆
- `plugins.slots.memory = "none"`

这说明他们自己也意识到：

**memory 不只是一个独立 memory tool，更像要接到 context assembly 上。**

这点和前面那篇 “memory isn’t a plugin, it’s the harness” 是呼应的。

### Why this matters for our survey

#### 1. 它把“memory system”推进到了 method / benchmark / integration 三位一体

前面很多项目只占其中一部分：

- 有的偏 runtime
- 有的偏 product
- 有的偏 benchmark

`EverOS` 是少数把三件事都放进一个 repo 的。

这让它不只是一个实现样本，而是一个：

**memory ecosystem proposal。**

#### 2. 它让 construction vs retrieval 这条线更清楚了

前面我们看：

- `agentmemory` 偏 retrieval/injection
- `mempalace` 偏 recall
- `Graphiti` 偏 temporal fact graph

`EverMemOS` 则提醒了一点：

**memory system 的核心竞争点之一，不只是 recall，还包括 construction discipline。**

也就是：

- 你抽什么
- 你怎么分型
- 你怎么形成 episode / profile / fact / skill

这些都比单纯“搜得准不准”更上游。

#### 3. 它和 OVP 的距离比 `agentmemory` 更近，但仍然不是同一路

它和 OVP 共享的地方在于：

- 重视 structured memory construction
- 不满足于 conversation transcript
- 不满足于单纯 vector recall
- 有 memory type / typed artifact 意识

但它和 OVP 仍然有明显差异：

- 它是 DB/search-native
- 它偏 API/service architecture
- 它的 artifact 更多是 internal structured memory，而不是 user-facing canonical knowledge space

### What OVP can learn from it

#### 1. Memory construction discipline 很值得借

这是这轮最值得吸收的地方。

OVP 已经不想停在“把原文存下来”这个层面。

`EverMemOS` 提醒的一点是：

**上游 construction schema 很重要。**

比如：

- atomic unit 是什么
- episode 怎么定义
- profile / preference / skill / fact 怎么分
- 哪些类型该进入长期层

这和 OVP 后面的：

- object
- claim
- evidence
- overview
- topic page

这些 artifact 设计有直接关系。

#### 2. Typed memory / typed artifact 的思路值得继续强化

OVP 不一定照抄它的 memory types，但这个方向是对的：

**不是所有长期知识都应该进同一种桶。**

#### 3. Benchmark worldview 值得认真记住

EverMemBench / EvoAgentBench 说明：

**memory system 不该只用 retrieval recall 一个指标评估。**

还应该看：

- reasoning quality
- personalization
- longitudinal improvement
- evolution impact

这对 OVP 很重要。

#### 4. OpenClaw 接入点很有参考价值

他们把 memory 接在 `context-engine`，而不是独立 memory slot。

这点值得记住，因为它说明：

**长期记忆对 agent 的真正影响点，是 context assembly。**

### What OVP should not absorb

#### 1. 不要把 OVP 带进重数据库依赖的 memory OS 路线

`EverMemOS` 走 Mongo + ES + Milvus + Redis 这条路，对它合理。

但 OVP 当前价值不在这里。

OVP 更该保住：

- human-readable artifacts
- file-native inspectability
- rebuildable derived layers

#### 2. 不要把 memory type taxonomy 误当成最终答案

它的 types 有启发，但 OVP 不应该直接照搬：

- episodic_memory
- profile
- agent_case
- agent_skill

因为 OVP 的知识对象语义不完全一样。

#### 3. 不要把 benchmark repo 的 worldview 全盘当成产品路线

benchmark 很重要，但 benchmark 优化不等于产品价值本身。

### Working conclusion from Round 19

`EverOS` 这轮最值得看的，不是 repo 名字，而是其中的 `EverMemOS / EverCore`。

我会把它归类为：

**一个 construction-first、retrieval-heavy、DB/search-native memory operating system；外加一套 benchmark 和 integration 生态。**

它对 OVP 最有价值的是：

- memory construction discipline
- typed memory thinking
- benchmark worldview
- context-engine 级接入点

它对 OVP 最不该带偏的是：

- 重基础设施依赖
- 把长期知识层完全服务化 / 内部化
- 放弃 file-native artifact ownership

一句话总结：

**`EverOS` 比普通 memory backend 更成熟，也比纯 runtime 项目更有方法论厚度；但它仍然更像“memory OS”而不是 OVP 这条 user-owned knowledge substrate 路线。**

## File References For This Round

### EverOS

- `https://github.com/EverMind-AI/EverOS`
- `/tmp/EverOS/README.md`
- `/tmp/EverOS/methods/evermemos/docs/OVERVIEW.md`
- `/tmp/EverOS/methods/evermemos/docs/ARCHITECTURE.md`
- `/tmp/EverOS/methods/evermemos/src/agentic_layer/search_mem_service.py`
- `/tmp/EverOS/methods/evermemos/src/agentic_layer/get_mem_service.py`
- `/tmp/EverOS/methods/evermemos/src/memory_layer/memory_manager.py`
- `/tmp/EverOS/methods/evermemos/examples/openclaw-plugin/README.md`

## Actionable Synthesis: What OVP Should Do

### The shortest answer

OVP 不该去做另一个通用 memory engine，也不该去做另一个 hosted coding-agent harness。

OVP 最应该做的，是成为：

**一个 user-owned、file-native、reviewable 的 knowledge substrate，外加一个清晰的 context assembly / access layer。**

也就是说，OVP 的重心应该放在：

1. **canonical knowledge artifacts**
2. **context assembly / retrieval access**
3. **governance / resolver / review**

而不是把主要精力放在：

- provider-managed thread state
- cloud runtime / sandbox
- benchmark-first recall product
- 黑盒 memory server

### What OVP should explicitly not become

#### 1. 不要变成 mem0 / agentmemory 那类通用 memory backend

这些系统擅长：

- 自动 capture
- recall
- injection
- multi-agent coordination

但 OVP 如果走这条路，会损失自己最独特的东西：

- file-native inspectability
- canonical artifact ownership
- reviewable knowledge compilation

#### 2. 不要变成 open-agents / hosted harness 产品

那会把精力拖向：

- auth
- sandbox
- GitHub automation
- remote execution infra

这不是当前 OVP 的主问题。

#### 3. 不要把一切问题都押在 semantic retrieval 上

`no-escape` 已经给了很强的约束：

**semantic proximity 有不可消除的 interference tax。**

所以 OVP 不该把核心建在“更强 embedding + 更强 rerank”上。

### What OVP should become more clearly

#### 1. A canonical artifact system

这是 OVP 的根。

核心不是“把记忆存起来”，而是把 source 编译成一组长期、可读、可维护、可追溯的 artifacts。

建议把长期知识层明确切成几类：

- **Object**
  人、组织、产品、项目、概念、协议、论文、事件源对象
- **Claim**
  关于 object/topic 的可检验陈述
- **Evidence**
  支撑 claim 的 source-linked evidence
- **Overview**
  面向主题 / 对象 / 时间段的综合说明页
- **Review items**
  冲突、低置信度、待决策项

这不是照搬 EverMemOS 的 type taxonomy，而是为 OVP 自己的知识编译目标服务。

#### 2. A context assembly layer, not just a vault full of files

只做 artifacts 还不够。

OVP 需要一层清晰的 access/assembly：

- exact lookup
- keyword search
- constrained semantic retrieval
- object/topic overview assembly
- session brief / orientation brief

这里可以借：

- `memsearch` 的 progressive disclosure
- `agentmemory` 的 retrieval engineering
- `byterover-cli` 的 `search / query / curate` 边界

但要注意：

**assembly layer 服务 canonical artifacts，而不是取代 canonical artifacts。**

#### 3. A governance layer

这是目前 OVP 最明显的缺口之一。

建议明确做三样东西：

- **Resolver**
  定义“什么输入 / 什么任务 / 什么 artifact / 什么 recipe 应该走哪条路”
- **Review queue**
  让自动生成的 object update、claim merge、overview rewrite、contradiction resolution 先进入待审，而不是直接落地
- **Reachability / routing eval**
  检查系统宣称的能力到底有没有可走通的路径

这里直接吸收：

- resolver 那篇
- `byterover-cli` 的 pending review
- `metaharness` 的“未来可优化治理对象”视角

### Recommended architecture direction

#### Layer 1: Canonical knowledge

持久真源，最好是文件优先、可读、可改。

要求：

- 人能读
- agent 能读
- 能追溯到 source
- derived state 可重建

#### Layer 2: Derived indexes and views

允许有：

- SQLite / graph / vector / BM25 indexes
- object registry
- topic graph
- contradiction index
- session snapshots

但这些必须是 **derived**，不是唯一真源。

#### Layer 3: Context assembly

把 Layer 1 + Layer 2 组装成：

- session brief
- object brief
- topic overview
- contradiction view
- delta digest

#### Layer 4: Governance

- resolver
- review queue
- audit trail
- change attribution
- reachability checks

### The first concrete wedge

如果现在要收敛成一个最小可做方向，我建议不是“先做通用 memory”。

我建议做：

**OVP Orientation + Canonical Artifact Loop**

具体就是四件事：

1. **定义 artifact schema v1**
   先只做少数几类：Object / Claim / Evidence / Overview / ReviewItem

2. **做一个 orientation brief**
   输入一批 source 后，先产出一份高质量“地图”
   这个是 Round 2 和 Round 3 给的启发

3. **做一个 reviewed absorb loop**
   自动把信息编成 object/claim/evidence 候选，但重要变更先进 review queue

4. **做一个 basic resolver**
   明确什么时候：
   - 生成 orientation brief
   - 更新 object page
   - 合并 claim
   - 标记 contradiction
   - 重建 overview

这四件事加在一起，才是一个最像 OVP 的最小闭环。

### The product promise OVP should make

不要承诺：

- “你的 agent 什么都记得”
- “我们有更高 recall”
- “自动帮你管理所有 memory”

应该承诺：

**把来源复杂、会变化、容易遗忘的信息，持续编译成你自己拥有的、可读可追溯的知识状态。**

用户最终买单的不是 memory feature。

用户买单的是：

- 我现在对这个主题到底知道什么
- 哪些结论有依据
- 哪些地方互相矛盾
- 最近更新了什么
- agent 下次进来时能不能立刻接上上下文

### Recommended next milestones

#### Milestone 1: Product semantics

- 定义 OVP 的输入 / 输出 / 不做什么
- 定义 artifact taxonomy v1
- 定义 canonical vs derived 边界

#### Milestone 2: Governance

- 定义 resolver v1
- 定义 review queue item schema
- 定义哪些自动变更必须审

#### Milestone 3: Access layer

- orientation brief
- object/topic overview assembly
- exact + keyword + constrained semantic retrieval

#### Milestone 4: Evaluation

- retrieval 只是其中一项
- 还要测：
  - artifact quality
  - contradiction surfacing
  - source traceability
  - session re-entry quality

### Final recommendation

如果只压成一句话：

**OVP 最该做的，不是“更会记”，而是“更会把记忆编译成你拥有的知识 artifacts，并且让 agent 能在这套 artifacts 上稳定工作”。**
