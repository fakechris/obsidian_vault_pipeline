# Research-Tech Skillpack

`research-tech` is the primary built-in pack for technical research workflows.

## What It Covers

- source ingestion from pinboard, clippings, repos, papers, and articles
- interpretation into deep-dive notes
- truth-aware indexing and compiled summaries
- materialized views:
  - object pages
  - topic overviews
  - event dossiers
  - contradiction views
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
- `ovp-export --pack research-tech --target topic-overview --output-path out/topic.md`
- `ovp-doctor --pack research-tech --json`
- `ovp-truth objects --vault-dir /path/to/vault`
- `ovp-ui --vault-dir /path/to/vault --port 8787`

## What This Pack Is Not

- not the compatibility layer
- not media/editorial specific
- not a generic demo pack

`default-knowledge` remains available only for compatibility.
