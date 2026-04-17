# Phase 17: Research Graph Visualization And Exploration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the existing research graph into a first-class visual exploration surface with an infinite canvas, progressive drill-down, and usable graph navigation that does not collapse under data volume.

**Architecture:** `Phase 15` already built the graph substrate, cluster detail, crystal views, and research navigation hints. `Phase 16` made pack/runtime ownership explicit. `Phase 17` stays inside `research-tech` and spends that foundation on a graph visualization product layer: cluster-first entry points, bounded subgraph loading, canvas-native navigation, and rich side-panel detail instead of dumping the full graph at once.

**Tech Stack:** Existing `truth_api.py` graph endpoints, `ui/view_models.py`, `ui_server.py`, pack-owned `research-tech` truth projection and surfaces, compiled cluster artifacts, local UI shell.

## Why This Is The Right Next Phase

The platform is no longer the blocker.

What users still do **not** have is a strong visual graph experience:

- they can browse clusters and crystal pages, but not really explore the graph spatially
- they can inspect relationships, but not move through the graph the way a human research user expects
- the graph is rich enough now that a naive “show everything” visualization would become unreadable and slow

So `Phase 17` should not add Media Pack work and should not reopen multi-pack runtime hardening.
It should focus on one thing:

- make `research-tech` graph intelligence visually explorable, performant, and legible

## Product Direction

The right product shape is **not** “open a giant graph with every node visible.”

The right product shape is:

1. **cluster-first entry**
   - users start from ranked clusters, briefing, signals, or object pages
   - every entry opens a bounded graph context, not the whole vault

2. **infinite canvas as workspace**
   - the canvas should pan and zoom freely
   - the user can expand neighborhoods incrementally
   - the canvas is for structure and traversal, not for showing every field inline

3. **progressive disclosure**
   - zoomed out: show cluster/group shapes, labels, pressure badges
   - mid zoom: show objects and dominant relation structure
   - focused selection: show detailed metadata in a side panel, not inside every node

4. **text + graph together**
   - cluster crystal, detail panels, and source-backed summaries remain important
   - graph exploration should deepen reading, not replace it

## Exit Condition

`Phase 17` is complete when all of the following are true:

- users can open a bounded graph canvas from cluster/object/research entry points
- the canvas supports real exploration:
  - pan
  - zoom
  - select
  - expand
- the default graph view stays performant on real vault data
- the graph uses progressive disclosure instead of flooding the screen
- graph exploration and crystal/detail reading reinforce each other
