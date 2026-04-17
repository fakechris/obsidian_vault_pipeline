# Phase 17 Closeout: Research Graph Visualization And Exploration

**Status:** Complete

`Phase 17` turned the research graph from a textual cluster browser into a bounded visual exploration surface.

## What Shipped

- Added bounded graph API shaping for:
  - cluster overview graph
  - cluster scope graph
  - object neighborhood graph
- Added a research-only graph canvas route:
  - `/graph`
  - `/api/graph`
- Added infinite-canvas style interaction in the local UI:
  - pan
  - zoom
  - recenter
  - select
  - bounded expand
- Added progressive disclosure:
  - graph stage stays structural
  - dense metadata moves to the side rail
  - edge filtering keeps local views readable
- Added graph entry points from existing research flows:
  - `/clusters`
  - `/cluster`
  - `/object`
  - `/topic`
- Updated shell diagnostics so `doctor --pack --json` exposes `/graph` as a research route contract.

## User-Visible Result

Users can now:

- open a graph overview from ranked clusters
- drill from cluster detail into a scoped graph
- open an object-centered graph neighborhood
- move spatially through the graph without loading the full vault graph
- keep graph exploration tied to the existing textual detail surfaces

## Performance Boundary

`Phase 17` deliberately does **not** render the full graph by default.

Instead it ships:

- bounded node counts
- bounded edge counts
- expand-on-demand behavior
- scope-specific graph routes
- side-rail detail instead of node bloat

That keeps the experience legible and responsive on real vault data.

## Verification

- targeted graph API / payload / UI / doctor tests
- full `pytest` suite
- `python3.13 -m compileall src/openclaw_pipeline`

## Follow-Up

`Phase 17` establishes the first real graph product surface.

Likely next work after this:

- saved graph workspaces
- stronger route bookmarking
- richer synthesis overlays on top of the deterministic graph
- a future `Phase 18` plan once the next product loop is locked
