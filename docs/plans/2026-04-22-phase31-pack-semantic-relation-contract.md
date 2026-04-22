# Phase 31: Pack-Level Semantic Relation Contract

## Goal

Introduce semantic relation extraction as a pack-owned contract, not as a hidden global memory backend.

This phase deliberately stops at the contract boundary:

- declare relation vocabulary,
- declare the candidate artifact shape,
- declare evidence and review gates,
- expose the contract through `ovp-doctor`,
- keep canonical graph truth unchanged until reviewed promotion exists.

## Why This Shape

The GBrain-style relationship layer is useful, but only if it remains governed by the same OVP architecture:

- files and pack contracts stay legible,
- derived indexes remain access layers,
- candidates do not silently become truth,
- every relation proposal carries source evidence,
- review queues own promotion decisions.

So Phase 31 adds a semantic relation contract family before adding any extractor.

## Implemented Scope

- `research-tech` declares `research_semantic_relations`.
- The relation vocabulary includes:
  - `supports`
  - `challenges`
  - `extends`
  - `replaces`
  - `uses`
- Relation candidates use the `semantic_relation_candidate` artifact spec.
- Candidate artifacts require:
  - relation type,
  - source object id,
  - target object id,
  - source slug,
  - evidence quote,
  - confidence.
- Candidates write only to the `semantic-relations` review queue.
- The write policy is `review_required`.
- `ovp-doctor --pack research-tech --json` exposes declared and effective semantic relation contracts.

## Non-Goals

- No semantic relation extractor.
- No automatic promotion to canonical graph truth.
- No Graphiti-style temporal validity model.
- No UI review workflow beyond declaring the review queue contract.
- No change to existing wikilink/backlink semantics.

## Next Phase Candidate

The next safe step is a reviewed extractor slice:

1. read relation contract vocabulary from the pack,
2. produce `semantic_relation_candidate` artifacts only,
3. persist candidates with evidence and source provenance,
4. surface candidates through the existing review queue/product shell,
5. promote accepted candidates into graph truth only through an explicit review action.
