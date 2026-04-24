"""Phase 37 — hand-rolled JSON-RPC 2.0 over stdio MCP server.

Exposes three compiler primitives that Phases 32–36 already shipped as
JSON-serializable Python functions:

* ``evaluate_promotion`` — wraps :mod:`promotion_policy.evaluate_concept`,
  ``evaluate_relation``, and ``evaluate_workspace``. Each returns a
  :class:`PolicyDecision` frozen dataclass; we ``asdict`` it for the wire.
* ``assemble_prompt`` — wraps :func:`prompt_assembler.assemble`. Side effect:
  emits one ``trusted_reuse_event`` per resolved object_id.
* ``route_feedback`` — wraps :mod:`feedback_router`. Writes to the candidate
  registry, ``open-questions.jsonl``, ``Writing-Prompts.md``, or the relation
  review queue depending on the ``stream`` discriminator.

Why hand-rolled rather than the official ``mcp`` SDK: the rest of this
codebase has zero web dependencies (stdlib ``http.server`` for UI, sqlite3
for storage). MCP-over-stdio is a line-delimited JSON-RPC 2.0 protocol; the
minimal verb set Claude Desktop actually invokes (``initialize``,
``tools/list``, ``tools/call``) fits in ~250 LOC of stdlib.

Forward-compat: any deferred verb (``resources/*``, ``prompts/*``,
``sampling/*``) returns the JSON-RPC ``method-not-found`` error envelope, not
a Python traceback.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Any, Callable, ClassVar, IO

from .concept_registry import ConceptEntry
from .feedback_router import (
    CandidateConcept,
    OpenQuestion,
    WritingPrompt,
    route_candidate_concepts,
    route_open_questions,
    route_proposed_relations,
    route_writing_prompts,
)
from .extraction.semantic_relations import SemanticRelationCandidate
from .graph import graph_ops
from .pack_resolution import coerce_pack
from .prompt_assembler import assemble as _assemble_prompt
from .promotion_policy import (
    evaluate_concept,
    evaluate_relation,
    evaluate_workspace,
)

# JSON-RPC 2.0 error codes (subset we actually emit).
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Tool descriptors (returned by tools/list verbatim)
# ---------------------------------------------------------------------------


_TOOLS_DESCRIPTORS: tuple[dict[str, Any], ...] = (
    {
        "name": "evaluate_promotion",
        "description": (
            "Decide the promotion lane for a concept, semantic relation, or "
            "workspace draft. Returns a PolicyDecision dict with lane, "
            "reason_code, blocking_facts, payload."
        ),
        "side_effects": "none (pure function)",
        "inputSchema": {
            "type": "object",
            "required": ["candidate_kind", "payload", "pack"],
            "properties": {
                "candidate_kind": {
                    "type": "string",
                    "enum": ["concept", "relation", "workspace"],
                },
                "payload": {"type": "object"},
                "pack": {"type": "string"},
                "has_open_contradiction": {"type": "boolean"},
                "evidence_kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    },
    {
        "name": "assemble_prompt",
        "description": (
            "Join slot_specs into a single prompt string and emit one "
            "trusted_reuse_event per resolved canonical object_id."
        ),
        "side_effects": "writes 60-Logs/reuse-events.jsonl",
        "inputSchema": {
            "type": "object",
            "required": ["slot_specs", "object_ids", "pack"],
            "properties": {
                "slot_specs": {"type": "array", "items": {"type": "string"}},
                "object_ids": {"type": "array", "items": {"type": "string"}},
                "pack": {"type": "string"},
                "consumer_ref": {"type": "string"},
                "separator": {"type": "string"},
                "session_id": {"type": "string"},
            },
        },
    },
    {
        "name": "route_feedback",
        "description": (
            "Route a batch of feedback items into the matching stream. "
            "stream ∈ {candidate_concept, open_question, writing_prompt, "
            "proposed_relation}."
        ),
        "side_effects": (
            "writes one of: concept registry, open-questions.jsonl, "
            "Writing-Prompts.md, or the semantic-relations review queue"
        ),
        "inputSchema": {
            "type": "object",
            "required": ["stream", "items", "pack"],
            "properties": {
                "stream": {
                    "type": "string",
                    "enum": [
                        "candidate_concept",
                        "open_question",
                        "writing_prompt",
                        "proposed_relation",
                    ],
                },
                "items": {"type": "array", "items": {"type": "object"}},
                "pack": {"type": "string"},
            },
        },
    },
    {
        "name": "graph_node_details",
        "description": (
            "Return metadata, in/out neighbors, degree, and approximate "
            "betweenness for a node in the wikilink graph."
        ),
        "side_effects": "none (read-only over knowledge.db)",
        "inputSchema": {
            "type": "object",
            "required": ["object_id"],
            "properties": {"object_id": {"type": "string"}},
        },
    },
    {
        "name": "graph_neighborhood",
        "description": (
            "BFS the hop-neighborhood of a node, capped at max_nodes. "
            "render='html' returns an SVG fragment in `_html_fragment` for "
            "MCP Apps clients; render='json' (default) returns nodes/edges."
        ),
        "side_effects": "none (read-only over knowledge.db)",
        "inputSchema": {
            "type": "object",
            "required": ["object_id"],
            "properties": {
                "object_id": {"type": "string"},
                "hop": {"type": "integer", "minimum": 1, "maximum": 4},
                "max_nodes": {"type": "integer", "minimum": 1, "maximum": 200},
                "render": {"type": "string", "enum": ["json", "html"]},
            },
        },
    },
    {
        "name": "graph_shortest_path",
        "description": (
            "Find the shortest undirected path between two nodes. Returns "
            "{nodes, edges, length} or null when disconnected."
        ),
        "side_effects": "none (read-only over knowledge.db)",
        "inputSchema": {
            "type": "object",
            "required": ["source", "target"],
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
            },
        },
    },
    {
        "name": "graph_bridge_nodes",
        "description": (
            "Top-N nodes by approximate betweenness centrality — surfaces "
            "the high-leverage bridges between communities."
        ),
        "side_effects": "none (read-only over knowledge.db)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "sample_size": {"type": "integer", "minimum": 10, "maximum": 1000},
            },
        },
    },
    {
        "name": "graph_communities",
        "description": (
            "Cluster the wikilink graph. algorithm ∈ {label_prop, "
            "greedy_modularity}. Returns {algorithm, clusters: {cid: [ids]}}."
        ),
        "side_effects": "none (read-only over knowledge.db)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "algorithm": {
                    "type": "string",
                    "enum": ["label_prop", "greedy_modularity"],
                }
            },
        },
    },
)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class MCPServer:
    """JSON-RPC 2.0 dispatcher for the three compiler primitives.

    A single instance is bound to a vault directory. Holds no per-call state
    beyond what each tool already persists through its existing emit paths.
    """

    PROTOCOL_VERSION: ClassVar[str] = "2026-04-22"
    SERVER_INFO: ClassVar[dict[str, str]] = {"name": "ovp-mcp", "version": "0.1.0"}

    def __init__(self, vault_dir: Path | str) -> None:
        self.vault_dir = Path(vault_dir)
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {
            "evaluate_promotion": self._tool_evaluate_promotion,
            "assemble_prompt": self._tool_assemble_prompt,
            "route_feedback": self._tool_route_feedback,
            "graph_node_details": self._tool_graph_node_details,
            "graph_neighborhood": self._tool_graph_neighborhood,
            "graph_shortest_path": self._tool_graph_shortest_path,
            "graph_bridge_nodes": self._tool_graph_bridge_nodes,
            "graph_communities": self._tool_graph_communities,
        }

    # -- Public surface -----------------------------------------------------

    def serve(
        self,
        *,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        """Read JSON-RPC requests line-by-line from stdin, write replies to stdout.

        One request per line, one reply per line — newline-delimited so a
        single OS pipe can carry both directions without framing headers.
        Loop exits when stdin reaches EOF.
        """
        in_stream = stdin or sys.stdin
        out_stream = stdout or sys.stdout
        for raw_line in in_stream:
            line = raw_line.strip()
            if not line:
                continue
            response = self.handle_line(line)
            if response is None:
                continue
            out_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            out_stream.flush()

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the public tool descriptors as a fresh list."""
        return [dict(d) for d in _TOOLS_DESCRIPTORS]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool by name and return its result dict.

        Raises ``KeyError`` for unknown tools and ``TypeError`` /
        ``ValueError`` for malformed arguments — the JSON-RPC layer turns
        those into the appropriate error envelope.
        """
        impl = self._tools.get(name)
        if impl is None:
            raise KeyError(name)
        return impl(**arguments)

    # -- JSON-RPC plumbing --------------------------------------------------

    def handle_line(self, line: str) -> dict[str, Any] | None:
        """Parse a single JSON-RPC line and return the reply dict (or None
        for notifications, which have no ``id``)."""
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return _error_envelope(None, _PARSE_ERROR, f"Invalid JSON: {exc}")
        if not isinstance(request, dict):
            return _error_envelope(None, _INVALID_REQUEST, "Request must be an object")

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not method:
            return _error_envelope(request_id, _INVALID_REQUEST, "Missing method")
        if not isinstance(params, dict):
            return _error_envelope(request_id, _INVALID_PARAMS, "params must be an object")

        try:
            result = self._dispatch(method, params)
        except _MethodNotFound:
            # JSON-RPC 2.0 §4.1: notifications MUST NOT receive any reply,
            # not even an error envelope.
            if request_id is None:
                return None
            return _error_envelope(
                request_id,
                _METHOD_NOT_FOUND,
                f"Method not implemented: {method}",
            )
        except _InvalidParams as exc:
            if request_id is None:
                return None
            return _error_envelope(request_id, _INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 — guard the loop
            if request_id is None:
                return None
            return _error_envelope(request_id, _INTERNAL_ERROR, str(exc))

        # Notifications (no id) get no reply.
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": self.SERVER_INFO,
            }
        if method == "tools/list":
            return {"tools": self.list_tools()}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not name:
                raise _InvalidParams("tools/call requires a string 'name'")
            if not isinstance(arguments, dict):
                raise _InvalidParams("tools/call 'arguments' must be an object")
            try:
                result = self.call_tool(name, arguments)
            except KeyError as exc:
                raise _InvalidParams(f"Unknown tool: {name}") from exc
            except TypeError as exc:
                raise _InvalidParams(str(exc)) from exc
            # MCP tools/call result shape: a content array of typed parts
            # plus an isError flag. We serialize the tool's dict as JSON text
            # so MCP clients can both display it and parse it back. The raw
            # dict is also exposed under "result" for callers that want to
            # skip the JSON round-trip.
            return {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
                "result": result,
            }
        raise _MethodNotFound(method)

    # -- Tool implementations ----------------------------------------------

    def _tool_evaluate_promotion(
        self,
        *,
        candidate_kind: str,
        payload: dict[str, Any],
        pack: str,
        has_open_contradiction: bool = False,
        evidence_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        pack_obj = coerce_pack(pack)
        if candidate_kind == "concept":
            entry = _concept_entry_from_payload(payload)
            decision = evaluate_concept(
                entry,
                pack=pack_obj,
                has_open_contradiction=has_open_contradiction,
                evidence_kinds=frozenset(evidence_kinds or ()),
            )
        elif candidate_kind == "relation":
            candidate = SemanticRelationCandidate.from_dict(payload)
            decision = evaluate_relation(
                candidate,
                pack=pack_obj,
                has_open_contradiction=has_open_contradiction,
            )
        elif candidate_kind == "workspace":
            draft_str = str(payload.get("draft", ""))
            target_str = str(payload.get("target", ""))
            if not draft_str or not target_str:
                raise _InvalidParams("workspace payload requires non-empty 'draft' and 'target'")
            decision = evaluate_workspace(Path(draft_str), Path(target_str), pack=pack_obj)
        else:
            raise _InvalidParams(f"Unknown candidate_kind: {candidate_kind}")
        result = asdict(decision)
        result["blocking_facts"] = list(decision.blocking_facts)
        return result

    def _tool_assemble_prompt(
        self,
        *,
        slot_specs: list[str],
        object_ids: list[str],
        pack: str,
        consumer_ref: str = "",
        separator: str = "\n\n",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        text = _assemble_prompt(
            vault_dir=self.vault_dir,
            pack=pack,
            slot_specs=list(slot_specs),
            object_ids=list(object_ids),
            consumer_ref=consumer_ref,
            separator=separator,
            session_id=session_id,
        )
        return {"text": text, "object_ids": list(object_ids)}

    def _tool_route_feedback(
        self,
        *,
        stream: str,
        items: list[dict[str, Any]],
        pack: str,
    ) -> dict[str, Any]:
        pack_obj = coerce_pack(pack)
        if stream == "candidate_concept":
            decoded = [
                CandidateConcept(
                    term=str(item.get("term") or ""),
                    definition=str(item.get("definition") or ""),
                    area=str(item.get("area") or ""),
                )
                for item in items
            ]
            written = route_candidate_concepts(decoded, vault_dir=self.vault_dir, pack=pack_obj)
            return {"stream": stream, "written": written}
        if stream == "open_question":
            decoded_q = [
                OpenQuestion(
                    question=str(item.get("question") or ""),
                    consumer_ref=str(item.get("consumer_ref") or ""),
                )
                for item in items
            ]
            written = route_open_questions(decoded_q, vault_dir=self.vault_dir, pack=pack_obj)
            return {"stream": stream, "written": written}
        if stream == "writing_prompt":
            decoded_p = [
                WritingPrompt(
                    prompt=str(item.get("prompt") or ""),
                    rationale=str(item.get("rationale") or ""),
                )
                for item in items
            ]
            written = route_writing_prompts(decoded_p, vault_dir=self.vault_dir, pack=pack_obj)
            return {"stream": stream, "written": written}
        if stream == "proposed_relation":
            decoded_r = [SemanticRelationCandidate.from_dict(item) for item in items]
            paths = route_proposed_relations(decoded_r, vault_dir=self.vault_dir, pack=pack_obj)
            return {"stream": stream, "written": len(paths), "paths": [str(p) for p in paths]}
        raise _InvalidParams(f"Unknown stream: {stream}")

    # -- Graph operators (Phase 38 Stage C) --------------------------------

    def _tool_graph_node_details(self, *, object_id: str) -> dict[str, Any]:
        graph = graph_ops.load_graph(self.vault_dir)
        return graph_ops.node_details(graph, object_id)

    def _tool_graph_neighborhood(
        self,
        *,
        object_id: str,
        hop: int = 1,
        max_nodes: int = 50,
        render: str = "json",
    ) -> dict[str, Any]:
        graph = graph_ops.load_graph(self.vault_dir)
        result = graph_ops.neighborhood(graph, object_id, hop=hop, max_nodes=max_nodes)
        if render == "html":
            result["_html_fragment"] = _neighborhood_svg_fragment(result)
        return result

    def _tool_graph_shortest_path(self, *, source: str, target: str) -> dict[str, Any]:
        graph = graph_ops.load_graph(self.vault_dir)
        result = graph_ops.shortest_path(graph, source, target)
        return (
            result if result is not None else {"source": source, "target": target, "found": False}
        )

    def _tool_graph_bridge_nodes(
        self, *, limit: int = 20, sample_size: int = 200
    ) -> dict[str, Any]:
        graph = graph_ops.load_graph(self.vault_dir)
        return {"bridges": graph_ops.bridge_nodes(graph, limit=limit, sample_size=sample_size)}

    def _tool_graph_communities(self, *, algorithm: str = "label_prop") -> dict[str, Any]:
        graph = graph_ops.load_graph(self.vault_dir)
        return graph_ops.communities(graph, algorithm=algorithm)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MethodNotFound(Exception):
    """Sentinel for the JSON-RPC method_not_found envelope."""


class _InvalidParams(Exception):
    """Sentinel for the JSON-RPC invalid_params envelope."""


def _error_envelope(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _neighborhood_svg_fragment(result: dict[str, Any]) -> str:
    """Render a self-contained SVG card for a ``neighborhood`` result.

    Uses a deterministic circular layout — small, no JS, and safe to drop
    into an iframe. The center node is the queried ``object_id`` so reviewers
    can orient at a glance. This is the experimental MCP Apps payload —
    Claude Desktop ignores ``_html_fragment`` today, but a client that honors
    the convention can render the card inline. All user-supplied strings are
    HTML-escaped before interpolation so titles like ``"A & B"`` round-trip
    safely through XML.
    """
    import math

    nodes = list(result.get("nodes") or [])
    edges = list(result.get("edges") or [])
    if not nodes:
        return '<div id="cy-neighborhood" data-empty="true">no nodes</div>'

    width = 400
    height = 300
    cx, cy = width / 2, height / 2
    radius = 110

    center_id = str(result.get("object_id") or nodes[0]["object_id"])
    others = [n for n in nodes if n["object_id"] != center_id]
    positions: dict[str, tuple[float, float]] = {center_id: (cx, cy)}
    n_others = max(len(others), 1)
    for idx, node in enumerate(others):
        theta = 2 * math.pi * idx / n_others
        positions[node["object_id"]] = (
            cx + radius * math.cos(theta),
            cy + radius * math.sin(theta),
        )

    edge_svgs: list[str] = []
    for edge in edges:
        src = positions.get(str(edge.get("source")))
        tgt = positions.get(str(edge.get("target")))
        if not src or not tgt:
            continue
        edge_svgs.append(
            f'<line x1="{src[0]:.1f}" y1="{src[1]:.1f}" '
            f'x2="{tgt[0]:.1f}" y2="{tgt[1]:.1f}" '
            f'stroke="#9ca3af" stroke-width="1" />'
        )

    node_svgs: list[str] = []
    for node in nodes:
        nid = str(node["object_id"])
        x, y = positions[nid]
        is_center = nid == center_id
        fill = "#2563eb" if is_center else "#e5e7eb"
        text_fill = "#ffffff" if is_center else "#111827"
        title = escape(str(node.get("title") or nid)[:20])
        node_svgs.append(
            f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="14" fill="{fill}" '
            f'stroke="#374151" stroke-width="1" />'
            f'<text x="{x:.1f}" y="{y + 4:.1f}" font-size="9" '
            f'text-anchor="middle" fill="{text_fill}">{title}</text></g>'
        )

    return (
        f'<div id="cy-neighborhood" data-object-id="{escape(center_id, quote=True)}">'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(edge_svgs)
        + "".join(node_svgs)
        + "</svg></div>"
    )


def _concept_entry_from_payload(payload: dict[str, Any]) -> ConceptEntry:
    """Build a ``ConceptEntry`` from the wire payload.

    Only ``slug`` is required; everything else has a sensible default. This
    is a pure transport adapter — no validation beyond the dataclass's own
    ``__post_init__`` checks.
    """
    slug = str(payload.get("slug") or "")
    if not slug:
        raise _InvalidParams("concept payload requires 'slug'")
    return ConceptEntry(
        slug=slug,
        title=str(payload.get("title") or slug),
        aliases=list(payload.get("aliases") or []),
        definition=str(payload.get("definition") or ""),
        area=str(payload.get("area") or ""),
        source_count=int(payload.get("source_count") or 0),
        evidence_count=int(payload.get("evidence_count") or 0),
    )
