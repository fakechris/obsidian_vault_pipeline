"""Phase 38 Stage C — graph_ops operators.

Coverage:
* ``load_graph`` returns an empty graph for a vault without ``knowledge.db``
* ``load_graph`` reads ``pages_index`` + ``page_links`` after a rebuild
* ``node_details`` reports degree + neighbors for a synthetic 5-node graph
* ``neighborhood`` respects ``hop`` and ``max_nodes`` budgets
* ``shortest_path`` finds the obvious path; returns None when disconnected
* ``bridge_nodes`` ranks the central node above leaves
* ``communities`` returns a non-empty cluster map for a multi-component graph
"""

from __future__ import annotations

import sqlite3

import networkx as nx
import pytest

from ovp_pipeline.graph import graph_ops
from ovp_pipeline.runtime import VaultLayout


def _star_graph() -> nx.MultiDiGraph:
    """5-node star: hub <-> {a, b, c, d}. Hub is the obvious bridge."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for nid in ("hub", "a", "b", "c", "d"):
        g.add_node(nid, title=nid.upper(), note_type="evergreen", path=f"{nid}.md")
    for leaf in ("a", "b", "c", "d"):
        g.add_edge("hub", leaf, link_type="wikilink")
        g.add_edge(leaf, "hub", link_type="wikilink")
    return g


def _two_component_graph() -> nx.MultiDiGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for nid in ("a1", "a2", "a3", "b1", "b2"):
        g.add_node(nid, title=nid, note_type="evergreen", path=f"{nid}.md")
    g.add_edge("a1", "a2", link_type="wikilink")
    g.add_edge("a2", "a3", link_type="wikilink")
    g.add_edge("b1", "b2", link_type="wikilink")
    return g


def test_load_graph_empty_when_no_knowledge_db(temp_vault):
    g = graph_ops.load_graph(temp_vault)
    assert g.number_of_nodes() == 0
    assert g.number_of_edges() == 0


def test_load_graph_reads_pages_index_and_page_links(temp_vault):
    layout = VaultLayout.from_vault(temp_vault)
    layout.knowledge_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.executescript("""
            CREATE TABLE pages_index (
                slug TEXT PRIMARY KEY, title TEXT, note_type TEXT, path TEXT, day_id TEXT
            );
            CREATE TABLE page_links (
                source_slug TEXT, target_slug TEXT, target_raw TEXT,
                link_type TEXT, line_number INTEGER
            );
            """)
        conn.executemany(
            "INSERT INTO pages_index (slug, title, note_type, path, day_id) VALUES (?, ?, ?, ?, ?)",
            [
                ("rag", "RAG", "evergreen", "10-Knowledge/Evergreen/RAG.md", ""),
                ("agent", "Agent", "evergreen", "10-Knowledge/Evergreen/Agent.md", ""),
            ],
        )
        conn.execute(
            "INSERT INTO page_links (source_slug, target_slug, target_raw, link_type, line_number)"
            " VALUES ('agent', 'rag', 'RAG', 'wikilink', 1)"
        )
        conn.commit()

    g = graph_ops.load_graph(temp_vault)
    assert g.number_of_nodes() == 2
    assert g.has_edge("agent", "rag")
    assert g.nodes["rag"]["title"] == "RAG"


def test_node_details_reports_neighbors_and_degree():
    g = _star_graph()
    details = graph_ops.node_details(g, "hub")
    assert details["exists"] is True
    assert details["degree"] == 8  # 4 in + 4 out
    assert {n["object_id"] for n in details["in_neighbors"]} == {"a", "b", "c", "d"}
    assert {n["object_id"] for n in details["out_neighbors"]} == {"a", "b", "c", "d"}
    assert details["betweenness"] > 0.0


def test_node_details_missing_node_returns_exists_false():
    g = _star_graph()
    assert graph_ops.node_details(g, "missing")["exists"] is False


def test_neighborhood_respects_hop_and_max_nodes_budget():
    g = _star_graph()
    one_hop = graph_ops.neighborhood(g, "hub", hop=1, max_nodes=50)
    assert {n["object_id"] for n in one_hop["nodes"]} == {"hub", "a", "b", "c", "d"}
    assert one_hop["truncated"] is False

    capped = graph_ops.neighborhood(g, "hub", hop=1, max_nodes=2)
    # hub itself + at most one more
    assert len(capped["nodes"]) <= 2
    assert capped["truncated"] is True


def test_shortest_path_finds_undirected_path():
    g = _star_graph()
    path = graph_ops.shortest_path(g, "a", "b")
    assert path is not None
    assert path["length"] == 2  # a -> hub -> b
    assert [n["object_id"] for n in path["nodes"]] == ["a", "hub", "b"]


def test_shortest_path_returns_none_when_disconnected():
    g = _two_component_graph()
    assert graph_ops.shortest_path(g, "a1", "b1") is None


def test_shortest_path_returns_none_for_unknown_node():
    g = _star_graph()
    assert graph_ops.shortest_path(g, "hub", "ghost") is None


def test_bridge_nodes_ranks_hub_first():
    g = _star_graph()
    bridges = graph_ops.bridge_nodes(g, limit=5)
    assert bridges, "expected at least one bridge node"
    assert bridges[0]["object_id"] == "hub"


def test_bridge_nodes_empty_graph():
    assert graph_ops.bridge_nodes(nx.MultiDiGraph()) == []


def test_communities_returns_non_empty_clusters():
    g = _two_component_graph()
    out = graph_ops.communities(g, algorithm="label_prop")
    assert out["algorithm"] == "label_prop"
    assert len(out["clusters"]) >= 2
    flat = [nid for ids in out["clusters"].values() for nid in ids]
    assert set(flat) == {"a1", "a2", "a3", "b1", "b2"}


def test_communities_unknown_algorithm_raises():
    g = _star_graph()
    with pytest.raises(ValueError):
        graph_ops.communities(g, algorithm="unknown")
