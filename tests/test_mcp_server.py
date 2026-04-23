"""Phase 37 — tests for the MCP stdio JSON-RPC server."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from ovp_pipeline.mcp_server import MCPServer
from ovp_pipeline.pulse import DEFAULT_LOGS  # noqa: F401  (sanity import)


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


def test_tools_list_returns_three_descriptors(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    reply = server.handle_line(request)
    assert reply is not None
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 1
    tools = reply["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    assert names == ["assemble_prompt", "evaluate_promotion", "route_feedback"]
    # Every descriptor must declare its side effects so MCP clients can gate
    # writes when running a dry-run preview.
    for tool in tools:
        assert "side_effects" in tool
        assert "inputSchema" in tool


def test_initialize_returns_capability_block(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps({"jsonrpc": "2.0", "id": "boot", "method": "initialize"})
    reply = server.handle_line(request)
    assert reply is not None
    result = reply["result"]
    assert "protocolVersion" in result
    assert result["serverInfo"]["name"] == "ovp-mcp"
    assert "tools" in result["capabilities"]


# ---------------------------------------------------------------------------
# tools/call: evaluate_promotion
# ---------------------------------------------------------------------------


def test_evaluate_promotion_concept_legacy_or_rule(temp_vault: Path) -> None:
    """default-knowledge is the permissive pack: source_count>=2 OR
    evidence_count>=3 promotes. We pass evidence_count=4 and expect LANE_AUTO."""
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "evaluate_promotion",
                "arguments": {
                    "candidate_kind": "concept",
                    "pack": "default-knowledge",
                    "payload": {
                        "slug": "alpha",
                        "title": "Alpha",
                        "source_count": 1,
                        "evidence_count": 4,
                    },
                },
            },
        }
    )
    reply = server.handle_line(request)
    assert reply is not None
    decision = reply["result"]["result"]
    assert decision["lane"] == "auto"
    assert decision["reason_code"] == "legacy_or_rule"
    assert decision["payload"]["slug"] == "alpha"


def test_evaluate_promotion_concept_below_threshold_holds(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "evaluate_promotion",
                "arguments": {
                    "candidate_kind": "concept",
                    "pack": "default-knowledge",
                    "payload": {
                        "slug": "weak",
                        "source_count": 1,
                        "evidence_count": 1,
                    },
                },
            },
        }
    )
    reply = server.handle_line(request)
    decision = reply["result"]["result"]
    assert decision["lane"] == "hold"
    assert decision["blocking_facts"]


def test_evaluate_promotion_workspace_permissive(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "evaluate_promotion",
                "arguments": {
                    "candidate_kind": "workspace",
                    "pack": "default-knowledge",
                    "payload": {
                        "draft": "10-Knowledge/Evergreen/_Candidates/x.md",
                        "target": "10-Knowledge/Evergreen/x.md",
                    },
                },
            },
        }
    )
    reply = server.handle_line(request)
    assert reply["result"]["result"]["lane"] == "auto"


# ---------------------------------------------------------------------------
# tools/call: assemble_prompt
# ---------------------------------------------------------------------------


def test_assemble_prompt_joins_slots_and_returns_object_ids(temp_vault: Path) -> None:
    """``assemble_prompt`` joins the slot strings and echoes the object_ids
    it attempted to record reuse events for. Emission of ``trusted_reuse_event``
    rows is gated on the canonical objects existing in ``knowledge.db``; that
    path is exercised exhaustively by ``test_reuse_emitter`` and we don't
    re-prove it here. We just want to confirm the MCP wire shape works
    end-to-end without raising."""
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "assemble_prompt",
                "arguments": {
                    "slot_specs": ["alpha context", "beta context"],
                    "object_ids": ["alpha", "beta"],
                    "pack": "default-knowledge",
                    "consumer_ref": "test://mcp",
                    "separator": " | ",
                },
            },
        }
    )
    reply = server.handle_line(request)
    payload = reply["result"]["result"]
    assert payload["text"] == "alpha context | beta context"
    assert payload["object_ids"] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# tools/call: route_feedback
# ---------------------------------------------------------------------------


def test_route_feedback_open_question_writes_jsonl(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "route_feedback",
                "arguments": {
                    "stream": "open_question",
                    "pack": "default-knowledge",
                    "items": [
                        {"question": "what is X?", "consumer_ref": "ref-1"},
                        {"question": "why is Y?", "consumer_ref": "ref-2"},
                    ],
                },
            },
        }
    )
    reply = server.handle_line(request)
    assert reply["result"]["result"]["written"] == 2

    log = temp_vault / "60-Logs" / "open-questions.jsonl"
    assert log.exists()
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {row["question"] for row in lines} == {"what is X?", "why is Y?"}


def test_route_feedback_candidate_concept_upserts_registry(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "route_feedback",
                "arguments": {
                    "stream": "candidate_concept",
                    "pack": "default-knowledge",
                    "items": [
                        {"term": "Quantum Slime", "definition": "a thing", "area": "ai"},
                    ],
                },
            },
        }
    )
    reply = server.handle_line(request)
    assert reply["result"]["result"]["written"] == 1

    from ovp_pipeline.concept_registry import ConceptRegistry

    registry = ConceptRegistry(temp_vault).load()
    assert registry.find_by_slug("quantum-slime") is not None


# ---------------------------------------------------------------------------
# Error envelopes — server stays alive
# ---------------------------------------------------------------------------


def test_unknown_method_returns_method_not_found(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    reply = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/read"}))
    assert reply is not None
    assert reply["error"]["code"] == -32601


def test_unknown_tool_returns_invalid_params(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "frobnicate", "arguments": {}},
        }
    )
    reply = server.handle_line(request)
    assert reply["error"]["code"] == -32602


def test_malformed_json_returns_parse_error(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    reply = server.handle_line("{not json")
    assert reply is not None
    assert reply["error"]["code"] == -32700


def test_notification_returns_no_reply(temp_vault: Path) -> None:
    """JSON-RPC requests without ``id`` are notifications — no response."""
    server = MCPServer(temp_vault)
    reply = server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "initialize"}))
    assert reply is None


def test_serve_loop_processes_multiple_requests(temp_vault: Path) -> None:
    """The serve() loop must keep going after an error envelope. We feed two
    lines: a bogus method, then a real tools/list. Both should reply, and the
    second reply must still be the well-formed tools/list result — proving the
    error didn't kill the loop."""
    server = MCPServer(temp_vault)
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/read"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    server.serve(stdin=stdin, stdout=stdout)

    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(replies) == 2
    assert replies[0]["error"]["code"] == -32601
    assert "tools" in replies[1]["result"]


# ---------------------------------------------------------------------------
# Direct call_tool / list_tools API (used by the CLI's --call / --tools-list)
# ---------------------------------------------------------------------------


def test_list_tools_direct(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    tools = server.list_tools()
    assert {t["name"] for t in tools} == {
        "evaluate_promotion",
        "assemble_prompt",
        "route_feedback",
    }


def test_call_tool_direct_evaluate_promotion(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    result = server.call_tool(
        "evaluate_promotion",
        {
            "candidate_kind": "concept",
            "pack": "default-knowledge",
            "payload": {"slug": "x", "source_count": 5, "evidence_count": 5},
        },
    )
    assert result["lane"] == "auto"


def test_call_tool_unknown_raises_keyerror(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    with pytest.raises(KeyError):
        server.call_tool("nope", {})


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 §4.1: notifications never receive any reply, even on error.
# ---------------------------------------------------------------------------


def test_notification_unknown_method_returns_no_reply(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    reply = server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "resources/read"}))
    assert reply is None


def test_notification_invalid_params_returns_no_reply(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    reply = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "frobnicate", "arguments": {}},
            }
        )
    )
    assert reply is None


def test_notification_internal_error_returns_no_reply(temp_vault: Path) -> None:
    """A notification that triggers a generic exception inside a tool must
    still get no reply."""
    server = MCPServer(temp_vault)
    reply = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "evaluate_promotion",
                    "arguments": {
                        "candidate_kind": "concept",
                        "pack": "default-knowledge",
                        # Missing required 'slug' raises _InvalidParams inside
                        # the tool body.
                        "payload": {"source_count": 1, "evidence_count": 1},
                    },
                },
            }
        )
    )
    assert reply is None


# ---------------------------------------------------------------------------
# Workspace candidate kind validation
# ---------------------------------------------------------------------------


def test_evaluate_promotion_workspace_empty_paths_invalid(temp_vault: Path) -> None:
    server = MCPServer(temp_vault)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "evaluate_promotion",
                "arguments": {
                    "candidate_kind": "workspace",
                    "pack": "default-knowledge",
                    "payload": {"draft": "", "target": ""},
                },
            },
        }
    )
    reply = server.handle_line(request)
    assert reply is not None
    assert reply["error"]["code"] == -32602
    assert "non-empty" in reply["error"]["message"]


# ---------------------------------------------------------------------------
# tools/call result follows the MCP shape
# ---------------------------------------------------------------------------


def test_tools_call_result_has_mcp_content_array(temp_vault: Path) -> None:
    """``tools/call`` must return MCP-shaped content + isError so a generic
    MCP client can render the reply without knowing per-tool schemas."""
    server = MCPServer(temp_vault)
    reply = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "evaluate_promotion",
                    "arguments": {
                        "candidate_kind": "concept",
                        "pack": "default-knowledge",
                        "payload": {"slug": "x", "source_count": 5, "evidence_count": 5},
                    },
                },
            }
        )
    )
    assert reply is not None
    result = reply["result"]
    assert result["isError"] is False
    assert isinstance(result["content"], list)
    assert result["content"][0]["type"] == "text"
    parsed = json.loads(result["content"][0]["text"])
    assert parsed["lane"] == "auto"
