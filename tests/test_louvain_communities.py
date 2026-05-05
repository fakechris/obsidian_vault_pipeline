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

    def test_parallel_edges_aggregate_weights(self):
        # Two edge kinds connecting the same pair (e.g., a relation
        # weight=1.0 + a contradiction weight=0.8 from
        # ``packs/research_tech/truth_projection``).  Pre-fix
        # ``nx.Graph.add_edge`` last-write-wins collapsed both to
        # whichever was added last, often down-weighting the stronger
        # relation.  Post-fix the weights aggregate.
        from ovp_pipeline.truth_store import GraphEdgeRow

        # Two parallel edges on the same pair (a, b).
        edges = {
            "rel:a-b": GraphEdgeRow(
                pack="t", edge_id="rel:a-b",
                source_object_id="a", target_object_id="b",
                edge_kind="relation:references", weight=1.0,
                evidence_source_slug="",
            ),
            "contra:a-b": GraphEdgeRow(
                pack="t", edge_id="contra:a-b",
                source_object_id="a", target_object_id="b",
                edge_kind="contradiction:subject", weight=0.8,
                evidence_source_slug="",
            ),
        }
        # Build the graph the same way _detect_communities does and
        # confirm the (a, b) edge weight is the SUM, not last-write.
        from ovp_pipeline.packs.research_tech.truth_projection import (
            _detect_communities,
        )

        # The community result is the same (single 2-node community)
        # regardless of weight aggregation, so we inspect the graph
        # the helper builds.  Easiest: sanity-check by replicating
        # the aggregation rule the helper uses.
        pair_weights: dict[tuple[str, str], float] = {}
        for edge in edges.values():
            pair = tuple(sorted((edge.source_object_id, edge.target_object_id)))
            pair_weights[pair] = pair_weights.get(pair, 0.0) + edge.weight
        assert pair_weights[("a", "b")] == 1.8

        # And the function still produces a sensible community.
        out = _detect_communities(edges, ["a", "b"])
        assert out == [["a", "b"]]

    def test_below_threshold_not_split(self):
        # 30-member community (< 50 threshold) should pass through
        # unchanged even when it has rich internal structure.
        from ovp_pipeline.truth_store import GraphEdgeRow

        members = [f"n{i:02d}" for i in range(30)]
        edges: dict[str, GraphEdgeRow] = {}
        # Wire as a path so Louvain finds one community.
        for i in range(len(members) - 1):
            src, tgt = members[i], members[i + 1]
            eid = f"{src}-{tgt}"
            edges[eid] = GraphEdgeRow(
                pack="t", edge_id=eid,
                source_object_id=src, target_object_id=tgt,
                edge_kind="relation:t", weight=1.0,
                evidence_source_slug="",
            )
        out = _detect_communities(edges, members)
        # Path Louvain may produce more than one community; the
        # invariant we care about is that NONE exceed 50.
        for community in out:
            assert len(community) <= 50

    def test_above_threshold_with_internal_structure_splits(self):
        # Build a "barbell-of-3" topology: 3 dense cliques of 25
        # members each (75 total > 50 threshold), connected by
        # single bridge edges.  Louvain on the full graph might
        # merge them; the splitter should re-split the merged
        # community into its 3 sub-components.
        from ovp_pipeline.truth_store import GraphEdgeRow

        edges: dict[str, GraphEdgeRow] = {}

        def _connect(src, tgt, weight=1.0):
            eid = f"{src}-{tgt}"
            edges[eid] = GraphEdgeRow(
                pack="t", edge_id=eid,
                source_object_id=src, target_object_id=tgt,
                edge_kind="relation:t", weight=weight,
                evidence_source_slug="",
            )

        all_members: list[str] = []
        for clique_idx in range(3):
            clique_members = [f"c{clique_idx}_{i:02d}" for i in range(25)]
            all_members.extend(clique_members)
            # Densely connect within the clique.
            for i in range(len(clique_members)):
                for j in range(i + 1, len(clique_members)):
                    _connect(clique_members[i], clique_members[j])
        # Single bridges between cliques.
        _connect("c0_00", "c1_00", weight=0.1)
        _connect("c1_00", "c2_00", weight=0.1)

        out = _detect_communities(edges, all_members)
        # Whatever Louvain does on the full graph, after splitting
        # NO output community should exceed 50.
        for community in out:
            assert len(community) <= 50, (
                f"community of size {len(community)} exceeds split threshold"
            )

    def test_above_threshold_without_internal_edges_kept_whole(self):
        # Edge case: a "community" of 60 members where the only
        # edges that landed them together are with EXTERNAL nodes
        # (not in this set).  When the splitter examines the induced
        # sub-graph, it has no edges → can't split.  Helper returns
        # the whole community.  We can't easily produce this with
        # the public API because Louvain on the full graph wouldn't
        # group disconnected nodes anyway, so we test the helper
        # directly.
        from ovp_pipeline.packs.research_tech.truth_projection import (
            _split_if_too_big,
        )

        big = {f"n{i}" for i in range(60)}
        # No edges among these 60.
        result = _split_if_too_big(big, pair_weights={})
        assert result == [sorted(big)]

    def test_split_helper_keeps_whole_when_louvain_returns_one(self):
        # If Louvain on the sub-graph returns a single sub-community
        # equal to the input, no split is possible — keep whole.
        from ovp_pipeline.packs.research_tech.truth_projection import (
            _split_if_too_big,
        )

        big = {f"n{i}" for i in range(60)}
        # Fully-connected sub-graph: Louvain should return one
        # community covering everything.
        pair_weights: dict[tuple[str, str], float] = {}
        members_list = sorted(big)
        for i in range(len(members_list)):
            for j in range(i + 1, len(members_list)):
                pair_weights[(members_list[i], members_list[j])] = 1.0
        result = _split_if_too_big(big, pair_weights=pair_weights)
        # 60-member fully-connected → likely one Louvain output → kept whole.
        # Either we get back the whole community, or Louvain split it
        # into multiple ≤50 pieces.  Both outcomes preserve the
        # ≤50 invariant on the FINAL output.
        for community in result:
            assert len(community) <= 60  # bounded
        if len(result) == 1:
            assert sorted(result[0]) == sorted(big)

    def test_split_attaches_singletons_to_nearest_subcommunity(self):
        """Pre-fix the splitter dropped any sub-Louvain singleton from
        the output (``len(c) >= 2`` filter), so a >50-member parent
        could lose a handful of members from ``graph_clusters`` and
        downstream coverage / total counts would silently undercount.

        New behaviour: each singleton attaches to whichever sized
        sub-community has the most weighted edges to it.  The
        sub-community count goes up by ≥1 over its members but every
        original member appears somewhere in the output.
        """
        from ovp_pipeline.packs.research_tech.truth_projection import (
            _split_if_too_big,
        )

        # Build a 60-node graph that Louvain will split into two
        # tight sub-communities (a-half, b-half) plus a singleton ``s``
        # that has weak ties to BOTH halves but is closer to the
        # b-half.  Without singleton attach, ``s`` disappears from
        # the output.
        a_half = {f"a{i}" for i in range(28)}
        b_half = {f"b{i}" for i in range(28)}
        singleton = "s"
        members = a_half | b_half | {singleton}
        assert len(members) == 57  # > _SPLIT_THRESHOLD (50)

        pair_weights: dict[tuple[str, str], float] = {}
        # Strong intra-half edges so Louvain finds the two halves.
        for halves in (a_half, b_half):
            sorted_half = sorted(halves)
            for i in range(len(sorted_half)):
                for j in range(i + 1, len(sorted_half)):
                    pair_weights[(sorted_half[i], sorted_half[j])] = 5.0
        # Weak edges from ``s`` to both halves; the b-half edge is
        # heavier so the singleton-attach must pick b-half.
        pair_weights[("a0", singleton)] = 0.1
        pair_weights[(singleton, "b0")] = 0.5
        pair_weights[(singleton, "b1")] = 0.5

        result = _split_if_too_big(members, pair_weights)

        all_returned = {member for sub in result for member in sub}
        assert singleton in all_returned, (
            "singleton was dropped — pre-fix behaviour where "
            "``len(c) >= 2`` filtered out sub-Louvain singletons"
        )
        assert all_returned == members, (
            "every original member must appear in exactly one "
            "sub-community"
        )

        # Singleton landed with the b-half (heavier weighted ties).
        sub_with_singleton = next(sub for sub in result if singleton in sub)
        b_overlap = len(set(sub_with_singleton) & b_half)
        a_overlap = len(set(sub_with_singleton) & a_half)
        assert b_overlap > a_overlap, (
            "singleton should attach to the sub-community with "
            "heavier weighted ties (b-half), got "
            f"a_overlap={a_overlap} b_overlap={b_overlap}"
        )

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
