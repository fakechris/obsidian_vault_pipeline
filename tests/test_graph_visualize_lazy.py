"""Phase 38.E: Cytoscape lazy-loading of hop2 nodes.

Validates the server-side decisions that drive the JS:
  * graphs over the threshold mark hop2 nodes with ``collapsed: hop2`` and
    flip ``INITIAL_AUTO_COLLAPSE`` to ``true``;
  * graphs under the threshold leave the markers off and let the page boot
    fully expanded;
  * the sidebar surfaces the three required buttons.
"""

from __future__ import annotations


from ovp_pipeline.graph.visualize import GraphVisualizer


def _node(idx: int, role: str) -> dict:
    return {
        "note_id": f"n{idx}",
        "title": f"Node {idx}",
        "note_type": "evergreen",
        "seed_role": role,
        "distance_from_seed": 0 if role == "seed" else (1 if role == "neighbor_1hop" else 2),
    }


def _payload(node_count: int, *, hop2_count: int = 0) -> dict:
    nodes: list[dict] = [_node(0, "seed")]
    hop1_count = max(0, node_count - 1 - hop2_count)
    for i in range(hop1_count):
        nodes.append(_node(1 + i, "neighbor_1hop"))
    for i in range(hop2_count):
        nodes.append(_node(1 + hop1_count + i, "neighbor_2hop"))
    return {
        "day_id": "test",
        "generated_at": "2026-04-23",
        "nodes": nodes,
        "edges": [],
        "seed_note_ids": ["n0"],
    }


def test_large_graph_marks_hop2_nodes_with_collapsed_attribute():
    """A graph above the default threshold must annotate every hop2 node so
    the JS layer can hide them on initial render."""
    payload = _payload(node_count=320, hop2_count=120)

    html = GraphVisualizer(payload).html()

    assert '"collapsed": "hop2"' in html
    assert "INITIAL_AUTO_COLLAPSE = true" in html


def test_small_graph_does_not_auto_collapse():
    """Under the threshold, hop2 markers stay absent — small vaults render
    every node up-front."""
    payload = _payload(node_count=50, hop2_count=20)

    html = GraphVisualizer(payload).html()

    assert '"collapsed": "hop2"' not in html
    assert "INITIAL_AUTO_COLLAPSE = false" in html


def test_threshold_override_via_kwarg():
    """Lowering the threshold flips a small graph into auto-collapse mode —
    used by tests and by callers who want stricter defaults."""
    payload = _payload(node_count=50, hop2_count=20)

    html = GraphVisualizer(payload).html(collapse_hop2_threshold=10)

    assert '"collapsed": "hop2"' in html
    assert "INITIAL_AUTO_COLLAPSE = true" in html
    assert "COLLAPSE_THRESHOLD = 10" in html


def test_collapse_mode_query_param_handler_present():
    """The JS that parses ?collapse_hop2= must ship in every page so the
    URL-level override works without server changes."""
    payload = _payload(node_count=10)

    html = GraphVisualizer(payload).html()

    # Each branch of the override must be parsed and routed.
    assert "collapse_hop2=(auto|always|never)" in html
    assert "shouldCollapseInitially" in html


def test_sidebar_exposes_three_collapse_buttons():
    """Locks in the labels — the plan calls these out explicitly so a
    reviewer building muscle memory can spot UX regressions."""
    html = GraphVisualizer(_payload(node_count=10)).html()

    assert 'id="btn-expand-all"' in html
    assert "Expand all hop2" in html
    assert 'id="btn-collapse-all"' in html
    assert "Collapse all hop2" in html
    assert 'id="btn-expand-selected"' in html
    assert "Expand connected to selected" in html
    assert 'id="collapse-stat"' in html


def test_only_hop2_nodes_get_marked_not_seeds_or_hop1():
    """Marker discipline: seeds and hop1s must never carry ``collapsed`` —
    that would hide the very nodes the reviewer is trying to navigate."""
    payload = _payload(node_count=320, hop2_count=120)

    html = GraphVisualizer(payload).html(collapse_hop2_threshold=10)

    # Total marker count == hop2 count.
    assert html.count('"collapsed": "hop2"') == 120
