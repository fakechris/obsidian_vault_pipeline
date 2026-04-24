"""Phase 38 Stage C — graph operators backed by ``knowledge.db``.

These operators give the MCP server and the ``/explore`` UI a single,
budget-aware view over the same ``pages_index`` + ``page_links`` rows that
``ovp-graph`` already consumes. The loader is deliberately a duplicate of
``graph_cli._load_graph_from_index`` minus the audit-event provenance pass —
operators only need the canonical wikilink graph; the provenance shim is a
visualization concern that bloats the in-memory graph for shortest-path /
betweenness queries.

All operators return JSON-serializable dicts so they round-trip cleanly
through the MCP envelope. Any operator that walks the graph respects the
``max_nodes`` budget so a runaway BFS never melts the daemon.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import networkx as nx

from ..runtime import VaultLayout, resolve_vault_dir


def load_graph(vault_dir: Path | str | None = None) -> nx.MultiDiGraph:
    """Build a ``MultiDiGraph`` from ``pages_index`` + ``page_links``.

    Returns an empty graph when ``knowledge.db`` is missing — operators then
    short-circuit to empty results rather than raising. Each node carries
    its title and ``note_type`` as attributes; edges carry ``link_type``.
    """
    layout = VaultLayout(resolve_vault_dir(vault_dir))
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    if not layout.knowledge_db.exists():
        return graph
    with sqlite3.connect(layout.knowledge_db) as conn:
        for slug, title, note_type, path in conn.execute(
            "SELECT slug, title, note_type, path FROM pages_index"
        ):
            graph.add_node(
                slug,
                title=title or slug,
                note_type=note_type or "unknown",
                path=path or "",
            )
        for source_slug, target_slug, link_type in conn.execute(
            "SELECT source_slug, target_slug, link_type FROM page_links"
        ):
            if not source_slug or not target_slug:
                continue
            if target_slug not in graph:
                graph.add_node(target_slug, title=target_slug, note_type="unknown", path="")
            if source_slug not in graph:
                graph.add_node(source_slug, title=source_slug, note_type="unknown", path="")
            graph.add_edge(source_slug, target_slug, link_type=link_type or "wikilink")
    return graph


def _node_payload(graph: nx.MultiDiGraph, node_id: str) -> dict[str, Any]:
    data = graph.nodes[node_id]
    return {
        "object_id": node_id,
        "title": data.get("title", node_id),
        "note_type": data.get("note_type", "unknown"),
        "path": data.get("path", ""),
    }


def node_details(graph: nx.MultiDiGraph, node_id: str) -> dict[str, Any]:
    """Return node metadata, in/out neighbors, degree, and approximate
    betweenness. ``betweenness`` is computed on a sampled subgraph capped at
    200 nodes so a vault-scale graph stays under one second per call.
    """
    if node_id not in graph:
        return {"object_id": node_id, "exists": False}

    in_neighbors = [_node_payload(graph, src) for src in graph.predecessors(node_id)]
    out_neighbors = [_node_payload(graph, tgt) for tgt in graph.successors(node_id)]

    sample_nodes = list(graph.nodes)
    if len(sample_nodes) > 200:
        # Always keep the requested node + its 1-hop frontier so the
        # estimate is meaningful for *this* node rather than a random slice.
        keep = {node_id}
        keep.update(graph.predecessors(node_id))
        keep.update(graph.successors(node_id))
        for nid in sample_nodes:
            if len(keep) >= 200:
                break
            keep.add(nid)
        sample_nodes = list(keep)
    sub = graph.subgraph(sample_nodes)
    try:
        bc = nx.betweenness_centrality(nx.DiGraph(sub), normalized=True)
    except Exception:
        bc = {}

    return {
        "object_id": node_id,
        "exists": True,
        "node": _node_payload(graph, node_id),
        "in_neighbors": in_neighbors,
        "out_neighbors": out_neighbors,
        "degree": graph.in_degree(node_id) + graph.out_degree(node_id),
        "in_degree": graph.in_degree(node_id),
        "out_degree": graph.out_degree(node_id),
        "betweenness": float(bc.get(node_id, 0.0)),
    }


def neighborhood(
    graph: nx.MultiDiGraph,
    node_id: str,
    *,
    hop: int = 1,
    max_nodes: int = 50,
) -> dict[str, Any]:
    """BFS the ``hop``-neighborhood of ``node_id`` undirected, capped at
    ``max_nodes``. Edges between nodes inside the captured set are returned
    so the caller can render a coherent sub-graph.
    """
    if node_id not in graph or hop <= 0 or max_nodes <= 0:
        return {"object_id": node_id, "nodes": [], "edges": [], "truncated": False}

    visited: set[str] = {node_id}
    frontier: list[str] = [node_id]
    truncated = False

    for _ in range(hop):
        next_frontier: list[str] = []
        for nid in frontier:
            for neighbor in list(graph.predecessors(nid)) + list(graph.successors(nid)):
                if neighbor in visited:
                    continue
                if len(visited) >= max_nodes:
                    truncated = True
                    break
                visited.add(neighbor)
                next_frontier.append(neighbor)
            if truncated:
                break
        if truncated or not next_frontier:
            break
        frontier = next_frontier

    nodes_payload = [_node_payload(graph, nid) for nid in visited]
    edges_payload: list[dict[str, Any]] = []
    seen_edge: set[tuple[str, str, str]] = set()
    for src, tgt, data in graph.edges(data=True):
        if src not in visited or tgt not in visited:
            continue
        link_type = str(data.get("link_type") or "wikilink")
        key = (src, tgt, link_type)
        if key in seen_edge:
            continue
        seen_edge.add(key)
        edges_payload.append({"source": src, "target": tgt, "link_type": link_type})

    return {
        "object_id": node_id,
        "hop": hop,
        "nodes": nodes_payload,
        "edges": edges_payload,
        "truncated": truncated,
    }


def shortest_path(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
) -> dict[str, Any] | None:
    """Find the shortest *undirected* path between ``source`` and
    ``target``. Wikilinks are inherently directional but reviewers think in
    "is this concept reachable from that one" terms — bidirectional search
    matches that mental model.
    """
    if source not in graph or target not in graph:
        return None
    undirected = graph.to_undirected(as_view=True)
    try:
        nodes = nx.shortest_path(undirected, source=source, target=target)
    except nx.NetworkXNoPath:
        return None
    except nx.NodeNotFound:
        return None
    edges: list[dict[str, str]] = []
    for src, tgt in zip(nodes, nodes[1:]):
        if graph.has_edge(src, tgt):
            link_type = next(iter(graph.get_edge_data(src, tgt).values())).get(
                "link_type", "wikilink"
            )
            edges.append({"source": src, "target": tgt, "link_type": link_type})
        elif graph.has_edge(tgt, src):
            link_type = next(iter(graph.get_edge_data(tgt, src).values())).get(
                "link_type", "wikilink"
            )
            edges.append({"source": tgt, "target": src, "link_type": link_type})
    return {
        "source": source,
        "target": target,
        "nodes": [_node_payload(graph, nid) for nid in nodes],
        "edges": edges,
        "length": len(nodes) - 1,
    }


def bridge_nodes(
    graph: nx.MultiDiGraph,
    *,
    limit: int = 20,
    sample_size: int = 200,
) -> list[dict[str, Any]]:
    """Top-``limit`` nodes by approximate betweenness centrality.

    Betweenness on a vault-scale graph is O(V*E) — too slow for an
    interactive call. We compute on a sampled subgraph (``sample_size``
    nodes) which keeps it well under a second while still surfacing the
    same kind of bridge nodes a full computation would.
    """
    if graph.number_of_nodes() == 0:
        return []
    sample_nodes = list(graph.nodes)
    if len(sample_nodes) > sample_size:
        sample_nodes = sample_nodes[:sample_size]
    sub = nx.DiGraph(graph.subgraph(sample_nodes))
    try:
        bc = nx.betweenness_centrality(sub, normalized=True)
    except Exception:
        return []
    ranked = sorted(bc.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [
        {**_node_payload(graph, nid), "betweenness": float(score)}
        for nid, score in ranked
        if score > 0
    ]


def communities(
    graph: nx.MultiDiGraph,
    *,
    algorithm: str = "label_prop",
) -> dict[str, Any]:
    """Cluster nodes into communities. ``label_prop`` is asynchronous label
    propagation — fast, deterministic given the same graph + seed, and
    unsupervised so we don't need to pick a cluster count up front.
    """
    if graph.number_of_nodes() == 0:
        return {"algorithm": algorithm, "clusters": {}}
    undirected = graph.to_undirected(as_view=True)
    if algorithm == "label_prop":
        from networkx.algorithms.community import asyn_lpa_communities

        groups = list(asyn_lpa_communities(undirected, seed=42))
    elif algorithm == "greedy_modularity":
        from networkx.algorithms.community import greedy_modularity_communities

        groups = list(greedy_modularity_communities(undirected))
    else:
        raise ValueError(f"Unknown community algorithm: {algorithm}")
    clusters: dict[str, list[str]] = {}
    for idx, group in enumerate(groups):
        clusters[f"c{idx}"] = sorted(group)
    return {"algorithm": algorithm, "clusters": clusters}
