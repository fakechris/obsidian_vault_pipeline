# OVP Architecture

> Architecture index: [README](./README.md) | **ARCHITECTURE** | [RUNTIME](./RUNTIME.md) | [PACKS](./PACKS.md) | [PRODUCT_SURFACES](./PRODUCT_SURFACES.md) | [GLOSSARY](./GLOSSARY.md)
>
> Language: English | [简体中文](./ARCHITECTURE.zh-CN.md)
>
> **This file explains:** where durable knowledge lives, what is rebuildable from it, and what the Governance Control Plane controls.
> **This file does not explain:** product roadmap, UI details, pack authoring, command execution, or backlog status. Each lives in the doc named at the top.

---

## One sentence

OVP turns external **Sources** into reviewable **Candidates**, promotes accepted knowledge into **Canonical State**, builds rebuildable **Projections** from it, and exposes those projections through **Access Surfaces**. The **Governance Control Plane** controls every write, promotion, repair, and audit boundary.

## The six core terms

The architecture has exactly six first-class words. Every other concept (Crystal, Atlas, KSR, Capture/Compile/Reuse, runtime stages, etc.) is either a kind of one of these or lives in the [glossary](./GLOSSARY.md).

| Term | Origin | Mutability | Defines truth? |
| --- | --- | --- | --- |
| **Source** | external (web, paper, repo, clip) | original immutable; metadata appendable | no |
| **Candidate** | system-proposed (LLM, parser, agent) | mutable, rejectable, mergeable | no |
| **Canonical State** | accepted via promotion | revisable through Governance + audit | **yes** |
| **Projection** | derived from Canonical State | freely rebuildable | no |
| **Access Surface** | UI / search / graph / briefing / export / MCP | read-mostly; writes go via Governance | no |
| **Governance** | control plane (cross-cutting) | configures rules, never holds knowledge | controls |

## Flow

```text
Inputs / Sources                          (external, immutable)
        |
        v
Candidates                                (system-proposed, awaiting review)
        |        ^
        |        |  promotion, review, verification, audit
        v        |
Canonical State                           (accepted, evidence-backed, long-term)
        |
        v
Projections                               (knowledge.db, graph, search, crystals)
        |
        v
Access Surfaces                           (reader, ops, search, briefing, MCP, export)


Governance Control Plane (cross-cutting)
        promotion · review · verification · repair · permissions · audit
```

Governance is **not** a fourth step in the flow. It sits across all four states, controlling every write that promotes a Candidate, repairs a Projection, or modifies a Surface that touches Canonical State.

---

## Term: Source

**Meaning:** External, user-attributable raw material — a clipped article, a paper PDF, a GitHub repo snapshot, a Pinboard entry, a hand-written note.

**Stored at:** `50-Inbox/03-Processed/<YYYY-MM>/`, `60-Logs/raw_data/`, the original `aliases.json` and content-hash records.

**Produced by:** `ovp-article` / `ovp-paper` / `ovp-github` / `ovp-clippings` and the user manually pasting markdown.

**Can be deleted?** The processed copy can be archived; the raw record on filesystem is the source of `Inputs`.

**Can it define truth?** No. A Source is *raw input*. Truth requires evidence + acceptance.

**Failure mode:** Lost original; renamed; unparseable encoding.

**Repair:** Re-ingest from origin; recompute content hash; hand re-attach metadata.

**Test:** A Source's content hash + ingestion timestamp uniquely identify it; re-ingesting an unchanged URL must be idempotent.

### Active staging set (URL uniqueness)

A Source's `source_url` is its global identity in the active intake chain. The chain consists of five directories — collectively the **active staging set**:

| Stage | Directory | Role |
|---|---|---|
| L0 | `Clippings/` (incl. subdirs) | user-side capture (Reader / web clipper) |
| L1 | `50-Inbox/02-Pinboard/` | Pinboard fetch landing |
| L2 | `50-Inbox/01-Raw/` | post-clipping / post-pinboard raw |
| L3 | `50-Inbox/02-Processing/` | actively being processed |
| L4 | `50-Inbox/03-Processed/<YYYY-MM>/` | intake done; absorb-eligible |

A `source_url` appearing **anywhere** in this set claims its slot. Any second arrival of the same URL — at any layer — is rejected with a `source_dedup_skipped` audit event. `70-Archive/` is **excluded by design**: a user who archived a prior copy and wants a fresh take is supported.

The dedup gate fires at every intake site (`ovp-clippings`, `ovp-pinboard-process`, `ovp-article --process-inbox`, `ovp-article --process-single`) using the global `source_dedup.build_active_url_index` index. See `src/ovp_pipeline/source_dedup.py` and `tests/test_intake_dedup_and_url.py` for the full contract.

## Term: Candidate

**Meaning:** System-proposed internal state that has *not yet been accepted*. Includes proposed objects, claims, relations, and entity merges.

**Stored at:** `60-Logs/knowledge.db` candidate tables; pre-promotion frontmatter on `_Candidates/` evergreen drafts.

**Produced by:** `auto_evergreen_extractor`, semantic relation extractor, LLM identity-merge proposer, ambiguity router. **Always derived from a Source or another Candidate, never invented.**

**Can be deleted?** Yes. Discarding a rejected candidate is normal flow.

**Can it define truth?** No. A Candidate must pass promotion to enter Canonical State.

**Can it write Canonical State?** No, except through Governance promotion.

**Failure mode:** Stale (no longer matches its source); orphaned (source deleted); duplicate; rejected without audit trail.

**Repair:** Re-extract from current Source; mark as superseded; apply review.

**Test:** Every Canonical State row must trace back to one or more accepted Candidates via the audit trail.

## Term: Canonical State

**Meaning:** Evidence-backed, user-owned, accepted long-term knowledge. The OVP trust boundary.

**Stored at:** vault Markdown (`10-Knowledge/Evergreen/**`, `10-Knowledge/Entity/**`, `40-Resources/`), concept and alias registries, evidence chains, audit log.

**Produced by:** Governance promotion of Candidates; user direct edits to vault markdown (with audit hooks).

**Can be deleted?** Only through Governance with explicit audit; deletion never happens silently.

**Can it define truth?** **Yes.** This is the architectural definition of truth in OVP.

**Failure mode:** Conflicting claims without resolution; missing evidence; orphaned identity merge; corrupted markdown.

**Repair:** Review queue, contradiction resolution, evidence re-attachment, hand correction with audit.

**Test:** Delete every Projection → rebuild from Canonical State → all Projections reconstructable. **If a Projection cannot be rebuilt, the Projection layer carried truth that should have been in Canonical State — that's an architectural bug.**

## Term: Projection

**Meaning:** Derived state computed from Canonical State. Indexes, graphs, search tables, synthesized crystals, view-model JSON.

**Stored at:** `60-Logs/knowledge.db`, `40-Resources/Crystals/`, runtime-state JSON, materialized views in `compiled_views/`.

**Produced by:** `ovp-knowledge-index`, `ovp-build-views`, `ovp-synthesize-community-crystals`, runtime projectors.

**Can be deleted?** Yes. Deleting a projection is a normal operation; rebuild is the answer.

**Can it define truth?** No. A Projection is a derived view, never authoritative.

**Can it write Canonical State?** No, except through Governance (e.g., a Projection-discovered contradiction enters the Review queue, not Canonical State directly).

**Failure mode:** Stale (Canonical State changed and the Projection lags), schema mismatch, missing.

**Repair:** Rebuild from Canonical State. Every Projection has a deterministic rebuild path; if it doesn't, that's a bug.

**Test:** `rm -rf` the Projection store → run `ovp-knowledge-index` → audit and reuse state preserved → no truth lost.

## Term: Access Surface

**Meaning:** What a human or agent sees and can act through. UI routes, MCP tools, CLI commands that read, search results, briefings, exported context packs.

**Stored at:** `commands/ui_server.py` (HTTP routes), `commands/mcp.py`, `40-Resources/` exports, briefing JSON.

**Produced by:** Read-mostly composition over Projections.

**Can it write Canonical State?** **No.** A Surface that wants to change Canonical State must go through Governance — for example, an MCP tool that "approves" a candidate emits a Governance promotion event, not a direct write.

**Failure mode:** Surface displays stale Projection; Surface skips Governance; Surface presents a Candidate as if it were Canonical.

**Repair:** Rebuild Projection; rewrite Surface to route writes through Governance; mark Candidates explicitly in UI.

**Test:** Disable every Access Surface → Canonical State unchanged. (Surfaces are pure consumers; their absence cannot lose truth.)

## Term: Governance

**Meaning:** The control plane that owns every transition into or modification of Canonical State.

**Stored at:** `promotion_policy.py`, `relation_promotion.py`, `commands/promote*`, audit JSONL log, review queues, projection-lifecycle markers.

**Subaxes:** Policy · Promotion · Review · Verification · Routing · Repair · Audit. Each subaxis is a separate concern; do not collapse them under "the Governance layer".

**Can it hold knowledge?** No. Governance configures rules, runs gates, and emits audit events. The knowledge being gated lives in Candidates / Canonical State.

**Failure mode:** Silent promotion (no audit), missing review queue, repair without rebuild record, ambiguous routing.

**Repair:** Replay audit log against Canonical State; reconcile review queue; ensure every promotion has an audit event.

**Test:** Pick any row in Canonical State → audit log answers *who promoted it, when, from which Candidate, against which evidence*. If the answer is missing, Governance has a hole.

---

## Migration note

Older OVP docs and code use these legacy names; the new architecture vocabulary supersedes them.

| Legacy term | Replacement |
| --- | --- |
| Authority (architectural sense) | Canonical State | <!-- lint-allow: migration table -->
| Layer 1 / Layer 1 Canonical Knowledge | Canonical State | <!-- lint-allow: migration table -->
| Layer 2 / Derived Indexes / Derived state | Projections | <!-- lint-allow: migration table -->
| Layer 3 / Context Assembly / Access | Access Surfaces | <!-- lint-allow: migration table -->
| Layer 4 | Governance Control Plane (subaxes named explicitly) | <!-- lint-allow: migration table -->
| Runtime stage `Canonical` | Runtime stage `Normalize` (see [RUNTIME](./RUNTIME.md)) |
| `source_authority` table (code) | Stays for now; migration to `source_credibility_score` deferred. **`source_authority` measures source trustworthiness — it is unrelated to Canonical State.** |

`Authority` no longer means a state in this architecture; the table named `source_authority` retains its original meaning ("how trustworthy is this source") and does not refer to the architecture's truth boundary.

---

## What is *not* in this file

- **Pipeline stages** (Ingest / Interpret / Absorb / Refine / Normalize / Derive) — see [RUNTIME](./RUNTIME.md).
- **Pack model** (Core / Domain Pack / Workflow Profile, pack discovery, schema) — see [PACKS](./PACKS.md).
- **UI / MCP / CLI surfaces** — see [PRODUCT_SURFACES](./PRODUCT_SURFACES.md).
- **Vocabulary collected from older docs** (Capture/Compile/Reuse, KSR, Crystal, Atlas, Briefing, Working Memory, Context Pack, Runtime State, Synthesis Layer) — see [GLOSSARY](./GLOSSARY.md). They are kinds of the six core terms above; they are not parallel architecture concepts.

## How to add a new term

A new word belongs in this file *only* if you can fill the mechanical template above. If you can't answer:

- **Where is it stored?**
- **Who produces it?**
- **Can it be deleted?**
- **Can it define truth?**
- **What is the failure mode?**
- **How is it repaired?**
- **What test enforces the boundary?**

… then it is not an architecture term. It belongs in `GLOSSARY.md`, `PRODUCT_SURFACES.md`, or `RUNTIME.md`. The first-page word budget is locked at six.
