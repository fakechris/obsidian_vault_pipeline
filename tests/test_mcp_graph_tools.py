"""Phase 38 Stage C — MCP server graph tools.

Coverage:
* ``tools/list`` exposes the 5 new graph tool descriptors
* ``graph_node_details`` round-trips through the JSON-RPC envelope
* ``graph_neighborhood`` with ``render="html"`` returns ``_html_fragment`` containing an ``<svg>``
* ``graph_neighborhood`` with default ``render="json"`` omits ``_html_fragment``
* ``graph_shortest_path`` returns ``found=False`` when nodes are disconnected
* ``graph_communities`` returns a non-empty cluster map for a multi-component graph
"""

from __future__ import annotations

import json
import sqlite3

from ovp_pipeline.mcp_server import MCPServer
from ovp_pipeline.runtime import VaultLayout


def _seed_three_node_graph(vault_dir):
    layout = VaultLayout.from_vault(vault_dir)
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
                ("orphan", "Orphan", "evergreen", "10-Knowledge/Evergreen/Orphan.md", ""),
            ],
        )
        conn.execute(
            "INSERT INTO page_links (source_slug, target_slug, target_raw, link_type, line_number)"
            " VALUES ('agent', 'rag', 'RAG', 'wikilink', 1)"
        )
        conn.commit()


def test_tools_list_includes_graph_tools(temp_vault):
    server = MCPServer(temp_vault)
    names = {t["name"] for t in server.list_tools()}
    assert {
        "graph_node_details",
        "graph_neighborhood",
        "graph_shortest_path",
        "graph_bridge_nodes",
        "graph_communities",
    } <= names


def test_graph_node_details_round_trips_jsonrpc(temp_vault):
    _seed_three_node_graph(temp_vault)
    server = MCPServer(temp_vault)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "graph_node_details", "arguments": {"object_id": "rag"}},
    }
    reply = server.handle_line(json.dumps(request))
    assert reply is not None
    result = reply["result"]["result"]
    assert result["exists"] is True
    assert result["in_degree"] == 1
    assert {n["object_id"] for n in result["in_neighbors"]} == {"agent"}


def test_graph_neighborhood_html_render_returns_svg_fragment(temp_vault):
    _seed_three_node_graph(temp_vault)
    server = MCPServer(temp_vault)
    result = server.call_tool(
        "graph_neighborhood",
        {"object_id": "rag", "hop": 1, "render": "html"},
    )
    assert "_html_fragment" in result
    fragment = result["_html_fragment"]
    assert "<svg" in fragment
    assert 'id="cy-neighborhood"' in fragment


def test_graph_neighborhood_json_render_omits_html_fragment(temp_vault):
    _seed_three_node_graph(temp_vault)
    server = MCPServer(temp_vault)
    result = server.call_tool(
        "graph_neighborhood",
        {"object_id": "rag", "hop": 1},  # render defaults to "json"
    )
    assert "_html_fragment" not in result
    assert {n["object_id"] for n in result["nodes"]} == {"rag", "agent"}


def test_graph_shortest_path_found_false_when_disconnected(temp_vault):
    _seed_three_node_graph(temp_vault)
    server = MCPServer(temp_vault)
    result = server.call_tool("graph_shortest_path", {"source": "rag", "target": "orphan"})
    assert result == {"source": "rag", "target": "orphan", "found": False}


def test_graph_communities_returns_clusters(temp_vault):
    _seed_three_node_graph(temp_vault)
    server = MCPServer(temp_vault)
    result = server.call_tool("graph_communities", {})
    assert result["algorithm"] == "label_prop"
    assert len(result["clusters"]) >= 1
