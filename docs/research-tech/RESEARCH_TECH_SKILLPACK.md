# Research-Tech Skillpack

`research-tech` is the primary built-in pack for technical research workflows.

## What It Covers

- source ingestion from pinboard, clippings, repos, papers, and articles
- interpretation into deep-dive notes
- truth-aware indexing and compiled summaries
- explicit artifact families for:
  - canonical objects
  - claims
  - evidence
  - compiled overviews
  - review items
  - semantic relation candidates
- explicit assembly recipes for:
  - orientation brief
  - topic overview
  - object brief
  - event dossier
  - contradiction view
- explicit governance contracts for:
  - review queues
  - signal rules
  - resolver rules
- explicit semantic relation contracts for:
  - relation vocabulary
  - evidence requirements
  - review-gated promotion policy
- materialized views:
  - workbench home / entry surface
  - orientation brief
  - object pages
  - topic overviews
  - event dossiers
  - contradiction views
- compiled page sections for:
  - current state
  - why it matters
  - evidence traceability
  - open tensions
  - where to go next
- review and maintenance loops:
  - contradiction review / resolution
  - stale summary review / rebuild

## Operator Commands

- `ovp --full --pack research-tech`
- `ovp-autopilot --pack research-tech --profile autopilot`
- `ovp-extract --pack research-tech --profile tech/doc_structure`
- `ovp-extract-preview --pack research-tech --profile tech/doc_structure`
- `ovp-extraction-dashboard --pack research-tech`
- `ovp-ops --pack research-tech --profile vault/review_queue`
- `ovp-build-views --pack research-tech --view overview/topic`
- `ovp-export --pack research-tech --target orientation-brief --output-path out/orientation.json`
- `ovp-export --pack research-tech --target topic-overview --output-path out/topic.md`
- `ovp-doctor --pack research-tech --json`
- `ovp-truth objects --vault-dir /path/to/vault`
- `ovp-ui --vault-dir /path/to/vault --port 8787`

## How To Inspect Contracts

- `ovp-doctor --pack research-tech --json`
  - 看 `declared` / `effective` contract families
  - 看 shared shell 解析到的 `governance_contract`
  - 看 `semantic_relation_contracts` 是否仍然是 pack-owned、evidence-gated、review-gated
- `ovp-ui --vault-dir /path/to/vault --port 8787`
  - 看 `/` workbench home 的 entry sections
  - 看 `/briefing` 的 orientation sections
  - 看页面级 `Assembly Contract` / `Governance Contract`
  - 看 signals / actions / briefing 上的 item-level provenance
- `ovp-export --pack research-tech --target orientation-brief --output-path out/orientation.json`
  - 看 orientation product 走到的 `assembly recipe -> source contract -> source provider` 链路
- `ovp-export --pack research-tech --target topic-overview --output-path out/topic.md`
  - 看 export target 走到的 `assembly recipe -> source contract -> source provider` 链路

## Semantic Relation Boundary

`research-tech` can declare relation vocabulary such as `supports`, `challenges`, `extends`, `replaces`, and `uses`, but this pack does not treat extraction output as canonical graph truth by default.

The safe path is:

1. extractor creates `semantic_relation_candidate`,
2. candidate carries source slug and quote evidence,
3. candidate enters `semantic-relations`,
4. a later review action promotes or rejects it.

## What This Pack Is Not

- not the compatibility layer
- not media/editorial specific
- not a generic demo pack

`default-knowledge` remains available only for compatibility.
