"""Interactive graph visualisations for the maintainer surface.

Hosts the D3-force layout used by both ``/ops/cluster?id=...``
(``render_cluster_force_graph``) and the reader-side ``/map``
(``render_graph_map_force_graph``).  The two call sites differ
only in the empty-state copy and the kind of payload they hand
in; the actual drawing / drag / zoom / legend behaviour is shared
through the private ``_render_force_graph_section`` helper.

This is the first external JS dependency in the codebase: D3 v7 is
loaded from ``unpkg.com``.  The tradeoff: building a smooth force-
layout + drag/zoom + edge-encoding solver inline would be 200+ lines
of vanilla JS and still lack D3-quality interactions.  D3 is ~280 KB,
cached after first hit and shared across the two pages that use it.

Data flow:
    payload (Python dict)
      → public renderer (cluster vs map) flattens to ``{nodes, edges}``
      → ``_render_force_graph_section`` builds SVG container plus a
        ``<script type="application/json">`` block carrying
        ``{nodes, edges, edge_kinds, object_kinds}``
      → bootstrap script (inline) parses the JSON and feeds D3-force
      → user sees nodes circles + edge lines, can drag / zoom /
        filter by edge kind / click through to /object?id=…
"""

from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import quote


# Visual encoding constants — exposed at module level so they can be
# tuned without touching the JS string.
NODE_RADIUS_MIN = 6.0
NODE_RADIUS_MAX = 18.0
EDGE_WIDTH_MIN = 1.0
EDGE_WIDTH_MAX = 6.0
GRAPH_VIEWPORT_HEIGHT = 520

# CDN script tag for D3 v7.  Pulled once per cluster page; the
# browser caches it across cluster page loads.
_D3_CDN = "https://unpkg.com/d3@7/dist/d3.min.js"


def _node_payload(member: dict[str, Any]) -> dict[str, Any]:
    """Normalise a cluster member into a graph node.

    Keys consumed by the bootstrap script: ``id``, ``title``,
    ``object_kind``, ``priority_band``, ``priority_score``,
    ``path`` (click-through), ``summary_excerpt``.  All optional
    except ``id`` + ``title``.
    """
    return {
        "id": str(member.get("object_id") or ""),
        "title": str(member.get("title") or member.get("object_id") or ""),
        "object_kind": str(member.get("object_kind") or ""),
        "priority_band": str(member.get("priority_band") or ""),
        "priority_score": float(member.get("priority_score") or 0.0),
        "path": str(member.get("path") or ""),
        "summary_excerpt": str(
            member.get("summary_excerpt") or member.get("excerpt") or ""
        ),
    }


def _edge_payload(edge: dict[str, Any]) -> dict[str, Any]:
    """Normalise a graph edge into the JSON the bootstrap consumes."""
    return {
        "source": str(edge.get("source_object_id") or ""),
        "target": str(edge.get("target_object_id") or ""),
        "edge_kind": str(edge.get("edge_kind") or "related"),
        "weight": float(edge.get("weight") or 1.0),
        "evidence": str(edge.get("evidence_source_slug") or ""),
    }


def _render_force_graph_section(
    nodes_data: list[dict[str, Any]],
    edges_data: list[dict[str, Any]],
    *,
    title: str,
    intro: str,
    empty_message: str,
    aria_label: str,
) -> str:
    """Inner helper — build the SVG mount + JSON data + bootstrap.

    Both ``render_cluster_force_graph`` (single cluster) and
    ``render_graph_map_force_graph`` (multi-cluster reader map)
    funnel through here.  DOM ids stay ``cluster-graph-*`` because
    they are internal CSS/JS handles — renaming would churn the
    bootstrap string for no user-visible benefit.
    """
    edge_kinds = sorted({e["edge_kind"] for e in edges_data})
    object_kinds = sorted({n["object_kind"] for n in nodes_data if n["object_kind"]})

    graph_payload = {
        "nodes": nodes_data,
        "edges": edges_data,
        "edge_kinds": edge_kinds,
        "object_kinds": object_kinds,
        "node_radius_min": NODE_RADIUS_MIN,
        "node_radius_max": NODE_RADIUS_MAX,
        "edge_width_min": EDGE_WIDTH_MIN,
        "edge_width_max": EDGE_WIDTH_MAX,
    }

    # JSON-in-HTML escape: ``</`` would close the surrounding script
    # block early when the data ever contained that substring.
    payload_json = json.dumps(graph_payload).replace("</", "<\\/")

    if not nodes_data:
        return (
            f"<section class='card'><h2>{escape(title)}</h2>"
            f"<p class='muted'>{escape(empty_message)}</p></section>"
        )

    return (
        "<section class='card cluster-graph'>"
        f"<h2>{escape(title)}</h2>"
        f"<p class='muted'>{escape(intro)}</p>"
        f"<style>{_FORCE_GRAPH_CSS}</style>"
        "<div class='cluster-graph-toolbar'>"
        "<span class='muted'>Filter edges:</span>"
        "<span id='cluster-graph-legend' class='cluster-graph-legend'></span>"
        "<button type='button' id='cluster-graph-reset' "
        "class='cluster-graph-reset'>Reset layout</button>"
        "</div>"
        "<svg id='cluster-graph-svg' "
        f"viewBox='0 0 800 {GRAPH_VIEWPORT_HEIGHT}' "
        "preserveAspectRatio='xMidYMid meet' role='img' "
        f"aria-label='{escape(aria_label)}'>"
        "<defs><marker id='cluster-arrow' viewBox='0 -5 10 10' "
        "refX='15' refY='0' markerWidth='7' markerHeight='7' "
        "orient='auto'><path d='M0,-5L10,0L0,5' fill='#9f9088'/>"
        "</marker></defs>"
        "<g id='cluster-graph-edges'></g>"
        "<g id='cluster-graph-nodes'></g>"
        "</svg>"
        "<div id='cluster-graph-tooltip' class='cluster-graph-tooltip' "
        "role='tooltip'></div>"
        f"<script type='application/json' id='cluster-graph-data'>{payload_json}</script>"
        f"<script src='{escape(_D3_CDN)}' defer></script>"
        f"<script>{_FORCE_GRAPH_BOOTSTRAP}</script>"
        "</section>"
    )


def render_cluster_force_graph(payload: dict[str, Any]) -> str:
    """Return an HTML section: SVG mount + JSON data + D3 bootstrap.

    The caller (``_render_cluster_detail_page``) injects this after
    the cluster header card.  The existing tabular ``Members`` and
    ``Internal Edges`` sections stay below as the printable /
    accessibility fallback.
    """
    cluster = payload.get("cluster") or {}
    member_links = cluster.get("member_links") or []
    edges = payload.get("edges") or []
    return _render_force_graph_section(
        nodes_data=[_node_payload(member) for member in member_links],
        edges_data=[_edge_payload(edge) for edge in edges],
        title="Force-Directed View",
        intro=(
            "Drag a node to pin it (double-click to release).  "
            "Wheel zooms; drag the background pans.  Click an "
            "edge-kind chip to fade non-matching edges.  Click a "
            "node to open its object detail."
        ),
        empty_message=(
            "No members in this cluster — the graph view is hidden "
            "until the cluster has at least one member."
        ),
        aria_label="Cluster force-directed graph",
    )


def render_graph_map_force_graph(payload: dict[str, Any]) -> str:
    """``/map`` reader-side force-directed view.

    Unlike ``render_cluster_force_graph``, the input is a flat
    ``{nodes, edges}`` covering every cluster the map is showing —
    cluster identity is not visually encoded (the force solver
    naturally pulls densely-connected nodes together; cluster-
    specific drill-in is on ``/ops/cluster?id=...``).

    Pre-computed orbital ``x`` / ``y`` on the ``/map`` payload are
    intentionally ignored — the D3 force solver re-lays the graph
    on every page load.  This is the whole point of the retrofit:
    static positions stop reflecting graph topology as the corpus
    grows.
    """
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    return _render_force_graph_section(
        nodes_data=[_node_payload(node) for node in nodes],
        edges_data=[_edge_payload(edge) for edge in edges],
        title="Knowledge Graph",
        intro=(
            "Drag a node to pin it (double-click to release).  "
            "Wheel zooms; drag the background pans.  Click an "
            "edge-kind chip to fade non-matching edges.  Click a "
            "node to open its object page."
        ),
        empty_message=(
            "No nodes in scope — try widening the search query, "
            "removing the per-cluster cap, or seeding the graph "
            "via ``ovp-knowledge-index``."
        ),
        aria_label="Knowledge graph force-directed view",
    )


_FORCE_GRAPH_CSS = """
.cluster-graph svg { width: 100%; height: auto; max-height: 560px;
  background: var(--graph-bg); border-radius: 12px; }
.cluster-graph-toolbar { display: flex; align-items: center;
  flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0 0.75rem; }
.cluster-graph-legend { display: inline-flex; flex-wrap: wrap;
  gap: 0.4rem; }
.cluster-graph-legend .chip { display: inline-flex; align-items: center;
  gap: 0.3rem; padding: 0.15rem 0.55rem; border-radius: 999px;
  border: 1px solid var(--border); background: var(--surface); cursor: pointer;
  appearance: none; font: inherit;
  font-size: 0.85rem; user-select: none; }
.cluster-graph-legend .chip.muted { opacity: 0.4; }
.cluster-graph-legend .chip-swatch { width: 10px; height: 10px;
  border-radius: 50%; display: inline-block; }
.cluster-graph-reset { font-size: 0.85rem; padding: 0.25rem 0.7rem;
  background: var(--surface); color: var(--text); border: 1px solid var(--border); }
.cluster-graph-tooltip { position: absolute; pointer-events: none;
  background: var(--text); color: var(--bg); padding: 0.5rem 0.7rem;
  border-radius: 6px; font-size: 0.85rem; max-width: 280px;
  line-height: 1.4; opacity: 0; transform: translate(-50%, -100%);
  transition: opacity 120ms; z-index: 1000;
  font-family: var(--ovp-font-sans); }
.cluster-graph-tooltip.visible { opacity: 1; }
.cluster-graph-tooltip strong { color: var(--accent-soft); }
.cluster-graph-edge { stroke-opacity: 0.55; }
.cluster-graph-edge.faded { stroke-opacity: 0.06; }
.cluster-graph-node { cursor: pointer; }
.cluster-graph-node circle { stroke-width: 2; stroke: var(--surface); }
.cluster-graph-node.attention circle { stroke: var(--graph-link-hi); }
.cluster-graph-node text { font-size: 11px; fill: var(--text);
  font-family: var(--ovp-font-sans);
  pointer-events: none; user-select: none; }
""".strip()


# The bootstrap script is intentionally written without ES modules so
# it loads from a single inline <script> tag.  It guards against D3
# not being ready by polling on DOMContentLoaded.  The polling loop is
# bounded — if D3 fails to load (CDN block, offline) it surfaces an
# inline notice so the operator knows to refresh or fall back to the
# tabular view below.
_FORCE_GRAPH_BOOTSTRAP = r"""
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState !== 'loading') { fn(); return; }
    document.addEventListener('DOMContentLoaded', fn);
  }

  function waitForD3(cb, attempt) {
    attempt = attempt || 0;
    if (window.d3) { cb(window.d3); return; }
    if (attempt > 80) {
      var svg = document.getElementById('cluster-graph-svg');
      if (svg) {
        svg.insertAdjacentHTML('afterend',
          "<p class='muted'>Force-directed view unavailable (D3 failed to load). " +
          "Tabular member + edge lists below still work.</p>");
      }
      return;
    }
    setTimeout(function () { waitForD3(cb, attempt + 1); }, 50);
  }

  ready(function () {
    var dataEl = document.getElementById('cluster-graph-data');
    if (!dataEl) return;
    var data;
    try { data = JSON.parse(dataEl.textContent || '{}'); }
    catch (e) { return; }
    if (!data.nodes || !data.nodes.length) return;
    waitForD3(function (d3) { mount(d3, data); });
  });

  function mount(d3, data) {
    var svgEl = document.getElementById('cluster-graph-svg');
    var tooltip = document.getElementById('cluster-graph-tooltip');
    var legendEl = document.getElementById('cluster-graph-legend');
    var resetBtn = document.getElementById('cluster-graph-reset');
    if (!svgEl || !tooltip || !legendEl) return;

    var svg = d3.select(svgEl);
    var width = 800;
    var height = parseInt(svgEl.getAttribute('viewBox').split(' ')[3], 10) || 520;

    // Node radius from priority_score (0..N) on a sqrt scale,
    // clamped to [min, max].  Falls back to a uniform radius when
    // no priority_score is supplied.
    var maxScore = d3.max(data.nodes, function (n) { return n.priority_score || 0; }) || 1;
    var radiusScale = d3.scaleSqrt()
      .domain([0, Math.max(1, maxScore)])
      .range([data.node_radius_min, data.node_radius_max])
      .clamp(true);

    // Edge thickness from weight.
    var maxWeight = d3.max(data.edges, function (e) { return e.weight || 1; }) || 1;
    var widthScale = d3.scaleLinear()
      .domain([0, Math.max(1, maxWeight)])
      .range([data.edge_width_min, data.edge_width_max])
      .clamp(true);

    // Edge colour from edge_kind (categorical, d3.schemeTableau10).
    var edgeColor = d3.scaleOrdinal()
      .domain(data.edge_kinds || [])
      .range(d3.schemeTableau10);

    // Node fill from object_kind.
    var nodeColor = d3.scaleOrdinal()
      .domain(data.object_kinds || [])
      .range(d3.schemeSet2);

    // Edge filter state — set of edge_kinds that are visible.
    var visibleKinds = new Set(data.edge_kinds);

    var simulation = d3.forceSimulation(data.nodes)
      .force('link', d3.forceLink(data.edges)
        .id(function (d) { return d.id; })
        .distance(110)
        .strength(0.6))
      .force('charge', d3.forceManyBody().strength(-220))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide().radius(function (d) { return radiusScale(d.priority_score || 0) + 4; }));

    var rootG = svg.append('g').attr('class', 'cluster-graph-root');
    var edgesGroup = rootG.append('g').attr('class', 'edges');
    var nodesGroup = rootG.append('g').attr('class', 'nodes');

    // Move pre-rendered <g> placeholders into the zoom group so the
    // server-rendered SVG structure stays valid for screen readers.
    var existingEdges = svgEl.querySelector('#cluster-graph-edges');
    var existingNodes = svgEl.querySelector('#cluster-graph-nodes');
    if (existingEdges) existingEdges.remove();
    if (existingNodes) existingNodes.remove();

    var edgeSel = edgesGroup.selectAll('line')
      .data(data.edges)
      .enter().append('line')
      .attr('class', 'cluster-graph-edge')
      .attr('stroke', function (d) { return edgeColor(d.edge_kind); })
      .attr('stroke-width', function (d) { return widthScale(d.weight || 1); })
      .attr('marker-end', 'url(#cluster-arrow)');

    var nodeSel = nodesGroup.selectAll('g')
      .data(data.nodes)
      .enter().append('g')
      .attr('class', function (d) {
        var cls = 'cluster-graph-node';
        if (d.priority_band === 'attention') cls += ' attention';
        return cls;
      })
      .on('click', function (event, d) {
        if (event.defaultPrevented) return;
        if (d.path) window.location.href = d.path;
      })
      .on('mouseenter', function (event, d) { showTooltip(event, d); })
      .on('mousemove', function (event, d) { positionTooltip(event); })
      .on('mouseleave', function () { hideTooltip(); });

    nodeSel.append('circle')
      .attr('r', function (d) { return radiusScale(d.priority_score || 0); })
      .attr('fill', function (d) { return nodeColor(d.object_kind || ''); });

    nodeSel.append('text')
      .attr('x', function (d) { return radiusScale(d.priority_score || 0) + 4; })
      .attr('y', 4)
      .text(function (d) { return d.title.length > 38 ? d.title.slice(0, 36) + '…' : d.title; });

    // Drag behaviour with double-click release for pinning.
    nodeSel.call(d3.drag()
      .on('start', function (event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', function (event, d) {
        d.fx = event.x; d.fy = event.y;
      })
      .on('end', function (event, d) {
        if (!event.active) simulation.alphaTarget(0);
        // Don't release on drag end — leave pinned at the operator's
        // chosen position.  Double-click releases.
      }));

    nodeSel.on('dblclick', function (event, d) {
      d.fx = null; d.fy = null;
      simulation.alphaTarget(0.3).restart();
      setTimeout(function () { simulation.alphaTarget(0); }, 600);
    });

    simulation.on('tick', function () {
      edgeSel
        .attr('x1', function (d) { return d.source.x; })
        .attr('y1', function (d) { return d.source.y; })
        .attr('x2', function (d) { return d.target.x; })
        .attr('y2', function (d) { return d.target.y; });
      nodeSel.attr('transform', function (d) {
        return 'translate(' + d.x + ',' + d.y + ')';
      });
    });

    // Zoom + pan on the whole rootG.
    svg.call(d3.zoom()
      .scaleExtent([0.3, 4])
      .on('zoom', function (event) { rootG.attr('transform', event.transform); }));

    // Legend chips — click toggles the corresponding edge_kind.
    // Use real <button> elements so keyboard users can tab to them
    // and activate with Enter/Space (a11y review fix).
    (data.edge_kinds || []).forEach(function (kind) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.dataset.kind = kind;
      chip.setAttribute('aria-pressed', 'true');
      chip.innerHTML = "<span class='chip-swatch' style='background:" +
        edgeColor(kind) + "'></span>" + escapeHtml(kind);
      chip.addEventListener('click', function () { toggleKind(kind, chip); });
      legendEl.appendChild(chip);
    });

    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        data.nodes.forEach(function (n) { n.fx = null; n.fy = null; });
        simulation.alpha(1).restart();
      });
    }

    function toggleKind(kind, chipEl) {
      if (visibleKinds.has(kind)) {
        visibleKinds.delete(kind);
        chipEl.classList.add('muted');
        chipEl.setAttribute('aria-pressed', 'false');
      } else {
        visibleKinds.add(kind);
        chipEl.classList.remove('muted');
        chipEl.setAttribute('aria-pressed', 'true');
      }
      edgeSel.classed('faded', function (d) { return !visibleKinds.has(d.edge_kind); });
    }

    function showTooltip(event, d) {
      var lines = ["<strong>" + escapeHtml(d.title) + "</strong>"];
      if (d.object_kind) lines.push(escapeHtml(d.object_kind));
      if (d.priority_band) lines.push("priority: " + escapeHtml(d.priority_band));
      if (d.summary_excerpt) lines.push(escapeHtml(d.summary_excerpt));
      tooltip.innerHTML = lines.join('<br>');
      tooltip.classList.add('visible');
      positionTooltip(event);
    }

    function positionTooltip(event) {
      tooltip.style.left = (event.clientX + window.scrollX) + 'px';
      tooltip.style.top = (event.clientY + window.scrollY - 12) + 'px';
    }

    function hideTooltip() {
      tooltip.classList.remove('visible');
    }

    function escapeHtml(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
      });
    }
  }
})();
""".strip()


# noqa: F401 — quote is imported for future cluster-side links from
# the visualisation module; keeping the import surface broad lets us
# build click-throughs without re-importing in each new helper.
_quote = quote
