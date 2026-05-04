"""Tests for the Louvain community detector that replaced the
connected-component clustering in M13 BL-041.

Connected components emit one cluster per disconnected island,
ignoring internal density.  Louvain maximises modularity, so a
tightly-knit subgroup INSIDE a single connected component becomes
its own community — which is the structure users actually mean
when they ask "what topics is the knowledge base organised around?".
The barbell test below pins the behavioural difference.
"""

from __future__ import annotations

from ovp_pipeline.packs.research_tech.truth_projection import (
    _build_graph_seeds,
    _detect_communities,
)
from ovp_pipeline.truth_store import (
    GraphEdgeRow,
    ObjectRow,
    RelationRow,
)


def _edge(src: str, tgt: str, *, weight: float = 1.0) -> GraphEdgeRow:
    return GraphEdgeRow(
        pack="t", edge_id=f"{src}->{tgt}",
        source_object_id=src, target_object_id=tgt,
        edge_kind="relation:t", weight=weight, evidence_source_slug="",
    )


class TestDetectCommunities:
    def test_empty_graph_returns_no_communities(self):
        # Edgeless graph would crash Louvain (ZeroDivisionError on
        # deg_sum²).  Short-circuit returns [].
        assert _detect_communities({}, ["a", "b", "c"]) == []

    def test_isolated_node_filtered(self):
        # ``c`` has no edge — the (a, b) pair becomes one community,
        # ``c`` gets its own size-1 community which we drop.
        edges = {"e1": _edge("a", "b")}
        out = _detect_communities(edges, ["a", "b", "c"])
        assert len(out) == 1
        assert out[0] == ["a", "b"]

    def test_barbell_splits_into_two_communities(self):
        # Two cliques of 3 connected by a single bridge edge.
        # Connected-component would emit ONE cluster of 6 nodes.
        # Louvain maximises modularity and finds two communities.
        edges: dict[str, GraphEdgeRow] = {}
        # Left clique: a-b-c fully connected.
        for src, tgt in [("a", "b"), ("b", "c"), ("a", "c")]:
            edges[f"{src}-{tgt}"] = _edge(src, tgt)
        # Right clique: d-e-f fully connected.
        for src, tgt in [("d", "e"), ("e", "f"), ("d", "f")]:
            edges[f"{src}-{tgt}"] = _edge(src, tgt)
        # Single bridge edge between the cliques.
        edges["c-d"] = _edge("c", "d")
        out = _detect_communities(edges, ["a", "b", "c", "d", "e", "f"])
        # Two distinct communities — exactly the structural insight
        # connected components misses.
        assert len(out) == 2
        # Membership: {a,b,c} and {d,e,f}.
        as_sets = sorted([frozenset(c) for c in out], key=lambda s: min(s))
        assert as_sets[0] == frozenset({"a", "b", "c"})
        assert as_sets[1] == frozenset({"d", "e", "f"})

    def test_deterministic_with_seed(self):
        # Louvain is order-sensitive.  The fixed seed in the
        # production helper means the same edge set yields the same
        # partition across runs — graph_cluster_id stays stable.
        edges = {
            "e1": _edge("a", "b"),
            "e2": _edge("b", "c"),
            "e3": _edge("c", "a"),
            "e4": _edge("d", "e"),
            "e5": _edge("e", "f"),
            "e6": _edge("d", "f"),
        }
        ids = ["a", "b", "c", "d", "e", "f"]
        first = _detect_communities(edges, ids)
        second = _detect_communities(edges, ids)
        assert first == second


class TestBuildGraphSeedsEmitsLouvainKind:
    """End-to-end through ``_build_graph_seeds`` — the cluster_kind
    column is the internal label users won't see, but downstream
    consumers (truth_api, view_models) read it.  Pre-fix this was
    ``relation_component``."""

    def test_cluster_kind_is_louvain_community(self):
        objects = [
            ObjectRow(
                pack="t", object_id=f"obj{i}", object_kind="evergreen",
                title=f"Object {i}", canonical_path=f"obj{i}.md",
                source_slug="",
            )
            for i in range(4)
        ]
        relations = [
            RelationRow(
                pack="t", source_object_id="obj0", target_object_id="obj1",
                relation_type="references", evidence_source_slug="src1",
            ),
            RelationRow(
                pack="t", source_object_id="obj2", target_object_id="obj3",
                relation_type="references", evidence_source_slug="src2",
            ),
        ]
        edges, clusters = _build_graph_seeds(
            "t", objects=objects, relations=relations, contradictions=[],
        )
        assert len(edges) == 2
        # Two disconnected pairs → two communities.
        assert len(clusters) == 2
        assert all(c.cluster_kind == "louvain_community" for c in clusters)
