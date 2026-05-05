# OVP Product Surfaces

> Architecture index: [README](./README.md) | [ARCHITECTURE](./ARCHITECTURE.md) | [RUNTIME](./RUNTIME.md) | [PACKS](./PACKS.md) | **PRODUCT_SURFACES** | [GLOSSARY](./GLOSSARY.md)
>
> **This file explains:** every concrete surface a user or agent interacts with — UI routes, MCP tools, CLI commands, exports — and what each reads/writes.
> **This file does not explain:** the durable state model (see [ARCHITECTURE](./ARCHITECTURE.md)) or how surfaces are computed (see [RUNTIME](./RUNTIME.md)).

---

## The boundary

Every Product Surface in OVP follows the same rule:

> **A surface reads Projections. A surface that wants to change Canonical State must go through Governance — never write Canonical State directly.**

This is the only constraint. UI / MCP / CLI / export are otherwise free to compose Projections however a domain prefers.

## Local web UI (`ovp-ui`)

The local HTTP server reads from `knowledge.db` Projections + materialized views.

**Two shells, one URL boundary** (BL-050 / M16):

* `path == "/" or not path.startswith("/ops")` ⇒ **Reader shell** — discovery, reading, search, atlas, crystals.  No DB stat counts.  No review forms.
* `path == "/ops" or path.startswith("/ops/")` ⇒ **Maintainer shell** — pipeline status, candidates, contradictions, signals, actions, runtime, audit.

Each shell renders its own nav.  The only cross-link is a single corner pointer (`→ Maintenance` / `← Back to Library`).  Old top-level maintainer paths (`/candidates`, `/signals`, …) emit `301` to their `/ops/*` equivalents.

### Vocabulary layering rule (BL-051)

The Reader shell uses one user-facing word — **Topic** — for what
internal storage calls a `community_crystal`.  Contradiction crystals
surface as **open question**.  The internal names (DB tables,
filesystem paths, CLI verbs, frontmatter `type:`) keep "crystal" for
schema stability.  Reader-facing text must say **Topic**.  Maintainer
docs that point at storage may say "crystal".  See [GLOSSARY](./GLOSSARY.md).

### Reader shell

| Route | What it shows | Reads |
| --- | --- | --- |
| `/` | Reader home — search box, Top Topics (`crystal_scores` top 5 + "See all N featured topics →"), Recent Topics (last 7 days) | Projection: `crystal_scores` + `community_crystals` |
| `/search` | FTS search across pages + topics + open questions | Projection: `page_fts` (titles prefixed `[topic]` / `[open question]`) |
| `/topics` | **Featured Topics** — top-N reading entry ranked by `crystal_scores` | Projection: `crystal_scores` + crystal bodies |
| `/atlas/curated` (legacy) | 301 → `/topics` | — |
| `/api/topics`, `/api/atlas/curated` | JSON twin of `/topics`; the legacy path 301s | — |
| `/atlas` | Legacy MOC browser over `graph_clusters` (power-user diagnostic; not in nav) | Projection: `graph_clusters` + label index |
| `/object?id=<obj>` | Object lens with source/backlink rail | Projection: object detail |
| `/note?path=<path>` | Note view (Evergreen, topic markdown, source) | Projection: note detail |
| `/topic?id=<obj>` | Topic overview around an anchor object (different surface from `/topics`) | Projection: topic neighborhood |
| `/map`, `/graph` | Visual knowledge map (capped 3 members per cluster; `?show_all=1` lifts cap) | Projection: `graph_edges` |
| `/explore?object_id=<obj>` | Three-pane reviewer surface (canvas + synth + agent timeline) | Projection: object pages + agent-decisions stream |

### Maintainer shell

| Route | What it shows | Reads / Writes |
| --- | --- | --- |
| `/ops` | Operator dashboard — runtime state, signal counts, where-to-start | Projection: runtime-state + signals + objects |
| `/ops/candidates` | Candidate concept queue with review controls | Projection: candidates; mutation via `/ops/candidates/review` (Governance promotion) |
| `/ops/contradictions` | Open contradictions queue | Projection: contradictions; mutation via `/ops/contradictions/resolve` |
| `/ops/signals` | Signal browser | Projection: signals |
| `/ops/actions` | Action queue + run/retry/dismiss controls | Projection: action_queue |
| `/ops/evolution` | Evolution candidates queue | Projection: evolution candidates |
| `/ops/production` | Production chains + gaps | Projection: production chains |
| `/ops/pulse`, `/ops/pulse/stream` | Live activity stream | Projection: pulse events |
| `/ops/events` | Audit / event log | Projection: audit_events |
| `/ops/clusters`, `/ops/cluster?id=<id>` | Louvain community diagnostics | Projection: `graph_clusters` |
| `/ops/objects` | Typed-object audit table | Projection: objects |
| `/ops/summaries`, `/ops/briefing`, `/ops/deep-dives` | Compiled-content review | Projection: compiled_summaries / briefing / deep dives |
| `/ops/reuse/fragment`, `/ops/open-questions/fragment`, `/ops/writing-prompts/fragment` | Embedded telemetry fragments for the dashboard | Projections |
| `/ops/workbench` | Reviewer / triage workbench | Projection: review actions |

Mutating routes (`*/review`, `*/resolve`, `*/rebuild`, `actions/*`) all flow through Governance and emit audit events; they never write Canonical State directly.

The UI is generated by `commands/ui_server.py` + `ui/view_models.py`. None of these write Canonical State; the few that mutate state (e.g., approving a candidate from `/ops/candidates/review`) emit a Governance promotion event.

## CLI surfaces

Read-mostly commands users type to navigate Projections without launching the UI:

| CLI | Reads | Writes |
| --- | --- | --- |
| `ovp-list-crystals` | community + contradiction crystal tables | nothing |
| `ovp-truth` | objects, claims, evidence, relations | nothing |
| `ovp-source-coverage` | source authority + entities | nothing |
| `ovp-doctor` | runtime state + projection markers | nothing |
| `ovp-export` | designated artifact | filesystem (export only) |

Compare with stage CLIs (those that DO write Canonical State or Projections — see [RUNTIME](./RUNTIME.md)):

| CLI | Writes through Governance? |
| --- | --- |
| `ovp-promote*` | ✓ promotion + audit |
| `ovp-merge-identities` | ✓ identity merge audit |
| `ovp-link-entities` | ✓ identity-aware writes; vault frontmatter audit |
| `ovp-knowledge-index` | rebuilds Projections only |
| `ovp-synthesize-community-crystals` | writes Projection rows + filesystem; never Canonical State |

## MCP

`commands/mcp.py` exposes a Model Context Protocol surface so external agents can call OVP tools. The same boundary applies: MCP tools read Projections and route any state-changing intent through Governance.

## Briefing & Context Pack

A briefing or context pack is a *snapshot Projection* assembled at request time from selected objects, claims, and evidence. It is a Projection — it is *never* Canonical State, even if the agent receiving it acts as if it is. Cite a briefing as evidence in a downstream Candidate; do not collapse the briefing into Canonical State.

## Synthesis Layer (Crystals)

Community crystals and contradiction crystals (M13) are a kind of Projection — LLM-synthesized markdown derived from `graph_clusters` + source evergreens. They render at `40-Resources/Crystals/<safe-id>.md`. Each crystal carries a frontmatter pointer back to its sources; **the crystal text itself is not Canonical State, even though it lives in the vault filesystem.** The architecture invariant is: deleting `40-Resources/Crystals/` and re-running `ovp-synthesize-community-crystals` reproduces equivalent crystals; no truth is lost.

The Curated Atlas / Crystal Read Model planned for M14 will be a Projection over the crystal corpus, not a new state layer.

## Surfaces that exist but are not yet in this doc

- Reading-progress / "today's queue" UI on the Reader home (planned, not BL-050 scope)
- Tag and entity facets for crystal search (BL-046b / BL-047b follow-ups)
- Surface-side `reuse_events` emission on Reader clicks (BL-049b follow-up)

These will be folded in as their boundaries stabilize. If you encounter a surface unsure of its Canonical State boundary, that's a Governance bug — please file.
