# OVP Glossary

> Architecture index: [README](./README.md) | [ARCHITECTURE](./ARCHITECTURE.md) | [RUNTIME](./RUNTIME.md) | [PACKS](./PACKS.md) | [PRODUCT_SURFACES](./PRODUCT_SURFACES.md) | **GLOSSARY**
>
> **This file explains:** every domain or product term that is *not* one of the six core architecture words, with a one-line definition, where it lives, and which doc covers it.
> **This file does not explain:** the architecture model itself (see [ARCHITECTURE](./ARCHITECTURE.md)) — only collects supporting vocabulary.

---

Terms are alphabetical. Each entry says **what kind it is** in the architecture model:

- *kind of Source / Candidate / Canonical State / Projection / Access Surface / Governance subaxis*

If a term cannot be classified as a kind of one of the six, it does not belong in OVP architecture documentation; it lives here only as legacy vocabulary.

---

## A — C

**Access Surface (root term)** — see [ARCHITECTURE](./ARCHITECTURE.md). UI / MCP / CLI / export.

**Atlas** — *kind of Access Surface.* Reader-first browse over the knowledge graph. Renders in `/atlas` route. See [PRODUCT_SURFACES](./PRODUCT_SURFACES.md).

**Audit (event log)** — *Governance subaxis.* Append-only record of every promotion, review decision, and Canonical State modification.

**Authority** — **legacy term.** Architectural sense: replaced by **Canonical State**. The code symbol `source_authority` (a table measuring source trustworthiness) is unrelated and retains its meaning.

**Briefing crystal** — *kind of Projection.* Snapshot of operator briefing assembled at request time. Materializer at `materializers/crystal.py`.

**Candidate (root term)** — see [ARCHITECTURE](./ARCHITECTURE.md).

**Canonical (legacy stage name)** — **legacy term.** The runtime stage previously called `Canonical` is now [`Normalize`](./RUNTIME.md). The architecture term `Canonical State` is distinct.

**Canonical handle** — *attribute of a Canonical State row.* Every entity carries one canonical handle (e.g., `karpathy`, `simonw`); see `entity_aliases` and the entity layer.

**Canonical State (root term)** — see [ARCHITECTURE](./ARCHITECTURE.md).

**Capture / Compile / Reuse** — **product narrative.** A reader-friendly description of what OVP does for users. Not an architecture concept; lives in [README](./README.md).

**Claim** — *kind of Canonical State.* A factual proposition about an object, with required evidence. Stored in `claims` table.

**Cluster** — *kind of Projection.* A Louvain community of evergreens. Stored in `graph_clusters` (cluster_kind = `louvain_community`).

**Community crystal** — *kind of Projection.* LLM-synthesized markdown derived from one Louvain community. Lives in `40-Resources/Crystals/<safe-id>.md` + the `community_crystals` table.

**Concept registry** — *kind of Canonical State storage.* Identity registry for concept slugs.

**Contradiction** — *kind of Canonical State signal.* A pair of opposing claims on the same subject; the `contradictions` table records open contradictions for the Governance review subaxis.

**Contradiction crystal** — *kind of Projection.* LLM-synthesized open-question markdown derived from one open contradiction. Lives in `40-Resources/Crystals/contradiction-<safe-id>.md` + the `contradiction_crystals` table.

**Context Pack** — *kind of Projection.* Selected-objects snapshot assembled at request time for an external agent. Same architecture status as a Briefing.

**Curated Atlas / Crystal Read Model** (planned M14) — *kind of Access Surface.* A ranked, top-N curation over the crystal corpus. Reads Projections; writes nothing.

## D — G

**Default-knowledge** — *kind of Pack.* Compatibility pack for vaults predating explicit standard packs. See [PACKS](./PACKS.md).

**Derive (stage)** — *runtime stage.* Final stage that produces Projections from Canonical State. See [RUNTIME](./RUNTIME.md).

**Derived state / Derived Indexes** — **legacy term.** Replaced by **Projections**.

**Domain Pack** — see *Pack* and [PACKS](./PACKS.md).

**Entity layer** — *kind of Canonical State.* Twitter authors, GitHub projects/users, persons, organizations. Resolves to canonical handles.

**Evergreen** — *kind of Canonical State.* An atomic concept note in `10-Knowledge/Evergreen/`.

**Evidence** — *kind of Canonical State attribute.* Quote / locator / content_hash supporting a Claim.

**Governance Control Plane (root term)** — see [ARCHITECTURE](./ARCHITECTURE.md). The control plane that gates every transition into Canonical State.

## I — M

**Identity merge** — *Governance Promotion subaxis.* Merging two source-platform identities into one canonical entity, with audit.

**Ingest (stage)** — see [RUNTIME](./RUNTIME.md).

**Interpret (stage)** — see [RUNTIME](./RUNTIME.md).

**KSR (Knowledge State Runtime)** — **legacy framing.** A vocabulary used in `30-Projects/Active/OVP-Knowledge-State-Runtime.md` describing source → observation → claim → evidence → validity → projection → permission. Useful as a thinking aid, but each concept maps onto one of the six architecture root terms; KSR itself is not a parallel architecture.

**Layer 1 / 2 / 3 / 4** — **legacy framing.** Replaced by Canonical State / Projections / Access Surfaces, with Governance Control Plane as a cross-cutting plane (not a fourth layer). <!-- lint-allow: glossary defines legacy term -->

**Louvain community** — see *Cluster.*

**MOC (Map of Content)** — *kind of Projection / Access Surface.* Index page over related notes. Built by `auto_moc_updater`.

## N — Z

**Normalize (stage)** — *runtime stage.* Identity resolution, alias resolution, contradiction detection. Renamed from `Canonical` stage. See [RUNTIME](./RUNTIME.md).

**Object** — *kind of Canonical State row.* The unifying record for any knowable thing — concept, entity, source, derivation. Stored in `objects` table.

**Pack** — see [PACKS](./PACKS.md).

**Profile (Workflow Profile)** — see [PACKS](./PACKS.md).

**Projection (root term)** — see [ARCHITECTURE](./ARCHITECTURE.md).

**Promotion** — *Governance subaxis.* The gate that turns a Candidate into a Canonical State row, paired with audit. See `promotion_policy.py`.

**Refine (stage)** — see [RUNTIME](./RUNTIME.md).

**Repair** — *Governance subaxis.* Projection-lifecycle markers for metadata repair, full rebuild, semantic reindex.

**research-tech** — *kind of Pack.* The first explicit standard built-in pack. See [PACKS](./PACKS.md).

**Resolver** — *Governance Routing subaxis component.* Identity / concept resolver code (e.g., `concept_resolver.py`). Do **not** use "Resolver" in architecture prose for unrelated routing concerns.

**Review** — *Governance subaxis.* Human queue for Candidate review, contradiction resolution, stale summary review.

**Runtime State** — *kind of Projection.* Live snapshot of in-flight runtime status (queues, action workers, schema version markers) used by `/ops` and `ovp-doctor`.

**Source (root term)** — see [ARCHITECTURE](./ARCHITECTURE.md).

**source_authority (table)** — Storage of source trustworthiness scores (e.g., `karpathy.com = 0.95`). Unrelated to the `Authority` legacy architecture term.

**Source authority subsystem** — *Governance Routing subaxis.* Computes per-source trust scores; feeds into promotion policy. See `source_authority.py`.

**Stale Summary** — *Governance Review subaxis.* Materialized summary that needs re-derivation because its source changed.

**Synthesis Layer (M13)** — *kind of Projection.* The community + contradiction crystals.

**Verification** — *Governance subaxis.* Evidence / hash / freshness / replay checks.

**Wikilink** — *kind of Canonical State formatting.* `[[handle]]` / `[[handle|alias]]` references inside an Evergreen body that resolve to other Canonical State rows.

**Working Memory** — *kind of Projection.* Per-session context state for the agent / operator workbench.

**Workspace promotion** — *Governance Promotion subaxis.* Promotion of items batched in a workspace into Canonical State.

---

## Adding a term here

Use the mechanical template from [ARCHITECTURE](./ARCHITECTURE.md) when you need to decide whether a new term belongs here. If the answer to *can it define truth?* / *what is its repair path?* / *what test enforces its boundary?* doesn't fit cleanly into one of the six core terms, the new word probably means we need to refine the boundary, not add another vocabulary.
