"""Pins the cytoscape-fcose / bubblesets / hover / animation additions.

These tests assert on the rendered HTML so accidental regressions in the
template — broken CDN refs, missing handlers, missing toggles — fail fast.
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


def _payload(node_count: int = 10, *, hop2_count: int = 0) -> dict:
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


def test_fcose_layout_script_loaded_with_peers():
    """fcose is the primary layout — it and its three peers must ship."""
    html = GraphVisualizer(_payload()).html()

    assert "cytoscape-fcose@2.2.0" in html
    assert "layout-base@2.0.1" in html
    assert "cose-base@2.2.0" in html
    assert "numeric-1.2.6" in html


def test_cose_bilkent_kept_as_fallback():
    """If the fcose CDN fails or the registration check misses, the page
    should still lay out — cose-bilkent stays loaded as the fallback."""
    html = GraphVisualizer(_payload()).html()

    assert "cytoscape-cose-bilkent@4.1.0" in html
    # And the runtime selector that prefers fcose, falls back to cose-bilkent.
    assert "preferredLayoutName" in html
    assert "'cose-bilkent'" in html


def test_bubblesets_script_loaded():
    """BubbleSets paints the cluster envelopes — without the script the
    overlay system silently no-ops, which is hard to spot in QA."""
    html = GraphVisualizer(_payload()).html()

    assert "cytoscape-bubblesets@4.0.5" in html


def test_hover_highlight_handlers_wired():
    """Hovering a node should highlight it + its incident edges. The two
    handler pairs must be present and use the .hover-hl class."""
    html = GraphVisualizer(_payload()).html()

    assert "cy.on('mouseover', 'node'" in html
    assert "cy.on('mouseout', 'node'" in html
    assert "addClass('hover-hl')" in html
    assert "removeClass('hover-hl')" in html
    # Style rules for the highlight class.
    assert ".hover-hl" in html


def test_cluster_toggle_and_legend_in_sidebar():
    """The Clusters block in the sidebar pins the toggle + legend mount."""
    html = GraphVisualizer(_payload()).html()

    assert 'id="cluster-toggle"' in html
    assert 'id="cluster-legend"' in html
    assert "Show connected-component hulls" in html


def test_redraw_clusters_function_present():
    """redrawClusters() is the only path that paints hulls — without it the
    canvas overlay never updates."""
    html = GraphVisualizer(_payload()).html()

    assert "function redrawClusters" in html
    # And it must be hooked into the layout + filter lifecycle.
    assert "cy.on('layoutstop', redrawClusters)" in html


def test_animated_collapse_uses_opacity_not_display_none():
    """The Phase 38.E hop2 collapse needs to fade, not pop. display:none
    breaks the transition; opacity:0 + events:no preserves layout
    positions and animates cleanly."""
    html = GraphVisualizer(_payload()).html()

    # The .collapsed-hop2 selector must drive opacity, not display.
    assert ".collapsed-hop2" in html
    assert "'opacity': 0" in html or '"opacity": 0' in html
    assert "ANIM_MS" in html


def test_unbundled_bezier_edge_curves():
    """Edges should curve like the reference visualization — straight lines
    look industrial, curves keep the cluster shapes legible."""
    html = GraphVisualizer(_payload()).html()

    assert "'curve-style': 'unbundled-bezier'" in html
