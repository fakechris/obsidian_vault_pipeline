"""Unit tests for ``_prune_hop1`` in graph_cli.

The helper trims hop1 nodes whose seed-degree is below a threshold, and
optionally caps each seed's hop1 fan-out by top-K seed-degree. Hop2 nodes
that lose all their bridges to surviving hop1/seed nodes are also dropped.
"""

from __future__ import annotations

from ovp_pipeline.graph_cli import _prune_hop1


def _edge(src: str, tgt: str) -> dict:
    return {
        "edge_id": f"{src}-{tgt}",
        "source": src,
        "target": tgt,
        "edge_type": "wikilink",
        "weight": 1.0,
        "is_new_today": False,
        "anchor_text": tgt,
        "evidence_line": 0,
    }


def test_no_op_when_both_flags_default():
    """min_seed_degree=1 and top_k_per_seed=None must short-circuit unchanged."""
    seeds = {"S1"}
    expanded = {"S1", "H1"}
    distance = {"S1": 0, "H1": 1}
    edges = [_edge("S1", "H1")]

    new_expanded, new_distance, new_edges, summary = _prune_hop1(
        seeds, expanded, distance, edges
    )

    assert new_expanded == expanded
    assert new_distance == distance
    assert new_edges == edges
    assert summary == {"hop1_dropped": 0, "hop2_dropped": 0}


def test_drops_hop1_below_min_seed_degree():
    """A hop1 connected to only 1 seed is dropped when min_seed_degree=2."""
    seeds = {"S1", "S2"}
    # H_shared bridges both seeds; H_only bridges S1 alone.
    expanded = {"S1", "S2", "H_shared", "H_only"}
    distance = {"S1": 0, "S2": 0, "H_shared": 1, "H_only": 1}
    edges = [
        _edge("S1", "H_shared"),
        _edge("S2", "H_shared"),
        _edge("S1", "H_only"),
    ]

    new_expanded, _, new_edges, summary = _prune_hop1(
        seeds, expanded, distance, edges, min_seed_degree=2
    )

    assert new_expanded == {"S1", "S2", "H_shared"}
    assert summary["hop1_dropped"] == 1
    # The S1↔H_only edge must vanish from the edge list.
    assert all("H_only" not in (e["source"], e["target"]) for e in new_edges)


def test_top_k_per_seed_caps_fanout():
    """Per-seed cap keeps the K hop1s with highest seed-degree."""
    seeds = {"S1"}
    # Three hop1s, all touching only S1, ranked by tie-break (alphabetical).
    expanded = {"S1", "Ha", "Hb", "Hc"}
    distance = {"S1": 0, "Ha": 1, "Hb": 1, "Hc": 1}
    edges = [_edge("S1", "Ha"), _edge("S1", "Hb"), _edge("S1", "Hc")]

    new_expanded, _, _, summary = _prune_hop1(
        seeds, expanded, distance, edges, top_k_per_seed=2
    )

    # All three have seed-degree=1 → tie-broken alphabetically → keep Ha, Hb.
    assert new_expanded == {"S1", "Ha", "Hb"}
    assert summary["hop1_dropped"] == 1


def test_top_k_prefers_higher_seed_degree():
    """When K=1 and one hop1 bridges more seeds, that one survives."""
    seeds = {"S1", "S2"}
    expanded = {"S1", "S2", "H_shared", "H_solo"}
    distance = {"S1": 0, "S2": 0, "H_shared": 1, "H_solo": 1}
    edges = [
        _edge("S1", "H_shared"),
        _edge("S2", "H_shared"),
        _edge("S1", "H_solo"),
    ]

    new_expanded, _, _, summary = _prune_hop1(
        seeds, expanded, distance, edges, top_k_per_seed=1
    )

    # S1's top-1: H_shared (degree 2) > H_solo (degree 1).
    # S2's top-1: H_shared. Union → {H_shared}.
    assert new_expanded == {"S1", "S2", "H_shared"}
    assert "H_solo" not in new_expanded
    assert summary["hop1_dropped"] == 1


def test_orphaned_hop2_dropped_after_hop1_prune():
    """Hop2 source-md nodes lose their hop1 bridge → must be removed."""
    seeds = {"S1", "S2"}
    expanded = {"S1", "S2", "H_shared", "H_only", "Src_orphan", "Src_kept"}
    distance = {
        "S1": 0,
        "S2": 0,
        "H_shared": 1,
        "H_only": 1,
        "Src_orphan": 2,
        "Src_kept": 2,
    }
    edges = [
        _edge("S1", "H_shared"),
        _edge("S2", "H_shared"),
        _edge("S1", "H_only"),
        # Src_orphan only reachable via H_only (which gets pruned).
        _edge("H_only", "Src_orphan"),
        # Src_kept reachable via H_shared (survives).
        _edge("H_shared", "Src_kept"),
    ]

    new_expanded, _, new_edges, summary = _prune_hop1(
        seeds, expanded, distance, edges, min_seed_degree=2
    )

    assert "H_only" not in new_expanded
    assert "Src_orphan" not in new_expanded
    assert "Src_kept" in new_expanded
    assert summary["hop1_dropped"] == 1
    assert summary["hop2_dropped"] == 1
    # Edges referencing dropped nodes are gone.
    for edge in new_edges:
        assert edge["source"] in new_expanded
        assert edge["target"] in new_expanded


def test_hop2_kept_when_directly_bridged_by_seed():
    """A hop2 also reachable directly from a seed survives hop1 pruning."""
    seeds = {"S1", "S2"}
    expanded = {"S1", "S2", "H_only", "Src_dual"}
    distance = {"S1": 0, "S2": 0, "H_only": 1, "Src_dual": 2}
    edges = [
        _edge("S1", "H_only"),
        _edge("H_only", "Src_dual"),
        # Direct seed→Src_dual bridge.
        _edge("S2", "Src_dual"),
    ]

    new_expanded, _, _, summary = _prune_hop1(
        seeds, expanded, distance, edges, min_seed_degree=2
    )

    assert "H_only" not in new_expanded
    # Src_dual survives via the S2 bridge even after H_only drops.
    assert "Src_dual" in new_expanded
    assert summary["hop2_dropped"] == 0
