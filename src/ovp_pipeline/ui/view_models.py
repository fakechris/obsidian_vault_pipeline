from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


# ---- /map AtlasGraph kit-shape projection helpers ---------------
# Mapping from internal object_kind to the kit's four type
# buckets.  Anything outside the keys lands in ``evergreen`` so the
# type filter stays well-defined.  ``open-question`` is the
# renderable affordance for ``contradiction_crystal``.
_ATLAS_TYPE_BY_OBJECT_KIND: dict[str, str] = {
    "evergreen": "evergreen",
    "interpretation": "deepdive",
    "deep_dive": "deepdive",
    "community_crystal": "topic",
    "contradiction_crystal": "open-question",
}

# Edge-kind reduction. The kit only colors three semantic
# categories: ``cite`` (with directional particles), ``contradict``
# (warn-amber, the only thicker stroke), and ``ref`` (muted, the
# default).  Registry edge kinds outside these collapse to ``ref``.
_ATLAS_LINK_KINDS: dict[str, str] = {
    "cite": "cite",
    "cites": "cite",
    "citation": "cite",
    "contradict": "contradict",
    "contradicts": "contradict",
    "contradiction": "contradict",
    "ref": "ref",
    "reference": "ref",
    "references": "ref",
}


def _atlas_community_slug(name: str) -> str:
    """Lower-kebab slug, used for legend stable ids in the kit."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "community"

from ..assembly_recipe_registry import describe_assembly_recipe_contract
from ..governance_registry import describe_governance_contract
from ..observation_surface_registry import describe_observation_surface_contract
from ..pack_resolution import iter_compatible_packs
from ..packs.loader import PRIMARY_PACK_NAME
from ..projection_labels import projection_label
from ..reuse_emitter import collect_object_ids, emit_reuse_events_for_object_ids
from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_store import CONTRADICTION_HEURISTIC_NOTE
from ..truth_api import (
    CONTRADICTION_STATUS_EXPLANATIONS,
    MAX_PAGE_SIZE,
    SIGNAL_TYPE_EXPLANATIONS,
    _batch_object_rows,
    count_action_queue_by_status,
    count_contradictions_by_status,
    count_graph_clusters,
    count_objects,
    get_briefing_snapshot,
    get_graph_cluster_detail,
    get_object_detail,
    get_object_source_chain,
    get_object_traceability,
    get_note_inbound_capture_summary,
    get_note_provenance,
    get_note_traceability,
    get_operational_runtime_state,
    get_object_provenance_map,
    get_review_context,
    get_runtime_status,
    get_topic_neighborhood,
    list_candidate_concepts,
    list_evolution_candidates,
    list_evolution_links,
    list_review_actions,
    list_atlas_memberships,
    list_action_queue,
    list_contradictions,
    list_graph_clusters,
    list_graph_edges_for_object_scope,
    list_objects,
    list_mention_kind_stats,
    list_object_kind_stats,
    list_relation_kind_stats,
    list_production_gaps,
    list_production_chains,
    list_signals,
    list_stale_summaries,
    list_timeline_events,
    search_vault_surface,
)

DEFAULT_CANDIDATE_BROWSER_LIMIT = 25
DEFAULT_EVENT_DOSSIER_LIMIT = 25
DEFAULT_TIMELINE_DAYS = 14
DEFAULT_TIMELINE_SAMPLE_SIZE = 6
# Audit event types that the operator-facing timeline highlights
# above the long-tail ``by_type`` histogram.  Order matters: the
# renderer prints these blocks top-to-bottom so a quick "new
# evergreens / errors / sources processed" scan reads naturally.
TIMELINE_HIGHLIGHTED_TYPES = (
    "evergreen_auto_promoted",
    "github_intake_completed",
    "article_processed",
    "absorb_skipped_source",
    "absorb_parse_error",
    "absorb_schema_drift",
    "github_intake_error",
    "broken_link",
    "community_crystal_synthesized",
    "contradiction_crystal_synthesized",
)
# Subset of the highlighted types that the renderer pulls into the
# per-day "errors / skips" sample list.  These are the failure modes
# the maintainer most often opens the dashboard to chase down.
TIMELINE_ERROR_EVENT_TYPES = (
    "absorb_parse_error",
    "absorb_schema_drift",
    "absorb_skipped_source",
    "broken_link",
    "github_intake_error",
)
# Trim error-row payload snippets so the dashboard doesn't dump
# multi-KB JSON dumps onto the page.  200 chars is enough to see the
# error class + filename without burying the rest of the day card.
TIMELINE_SNIPPET_CHARS = 200
# Cap on sibling-evergreen count returned by ``_compute_v2_lineage``
# so a popular raw source (one that produced 20+ units) doesn't
# render a wall of links on every per-evergreen lineage card.
LINEAGE_SIBLING_EVERGREEN_LIMIT = 50
DEFAULT_TRACEABILITY_BROWSER_LIMIT = 15
DEFAULT_GRAPH_MAP_LIMIT = 24
# Cap each cluster's contribution to the default atlas view.  A cap
# of 12 with 24 clusters gives up to ~288 nodes — dense enough that
# the user sees a meaningful graph at first paint, but bounded so
# the WebGL force simulation stays responsive on a typical laptop.
# ``?show_all=1`` lifts the cap entirely for power users.
DEFAULT_GRAPH_MAP_MEMBER_CAP = 12
GRAPH_MAP_WIDTH = 960
GRAPH_MAP_HEIGHT = 640
GRAPH_MAP_CLUSTER_ORBIT_X_FACTOR = 0.26
GRAPH_MAP_CLUSTER_ORBIT_Y_FACTOR = 0.22
GRAPH_MAP_MARGIN = 42.0
GRAPH_MAP_LOCAL_RADIUS_BASE = 42.0
GRAPH_MAP_LOCAL_RADIUS_PER_MEMBER = 12.0
GRAPH_MAP_LOCAL_RADIUS_MAX = 128.0
GRAPH_MAP_NODE_BASE_RADIUS = 7
GRAPH_MAP_NODE_RADIUS_PER_DEGREE = 2
GRAPH_MAP_NODE_RADIUS_BONUS_MAX = 10


def _scoped_path(path: str, *, pack_name: str | None = None) -> str:
    if not pack_name:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}pack={quote(pack_name, safe='')}"


def _build_note_jump_path(path: object, *, pack_name: str | None = None) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return ""
    return _scoped_path(f"/note?path={quote(normalized, safe='')}", pack_name=pack_name)


def _emit_briefing_reuse(
    vault_dir: Path | str,
    payload: dict[str, Any],
    *,
    pack: str,
    consumer_ref: str,
) -> None:
    """Emit one ``briefing``-surface reuse event per canonical object in payload.

    Reuse-event emission is best-effort instrumentation — a JSONL append
    failure must never block the view-builder from returning a payload.
    """
    if not pack:
        return
    try:
        object_ids = collect_object_ids(payload)
        if not object_ids:
            return
        emit_reuse_events_for_object_ids(
            vault_dir,
            pack=pack,
            object_ids=object_ids,
            surface="briefing",
            consumer_ref=consumer_ref,
        )
    except Exception:  # noqa: BLE001 — best-effort instrumentation
        return


def _supports_research_shell(pack_name: str | None = None) -> bool:
    try:
        return any(pack.name == PRIMARY_PACK_NAME for pack in iter_compatible_packs(pack_name))
    except ValueError:
        return False


def _assembly_contract(recipe_name: str, *, pack_name: str | None = None) -> dict[str, str]:
    return describe_assembly_recipe_contract(pack_name=pack_name, recipe_name=recipe_name)


def _access_projection_label(
    *,
    surface: str,
    pack_name: str | None,
    generated_by: str,
    derived_from: tuple[str, ...] = ("knowledge.db",),
    rebuild_policy: str = "read_time",
) -> dict[str, object]:
    return projection_label(
        surface=surface,
        projection_kind="access_surface",
        layer="Layer 3",
        owner_pack=pack_name or PRIMARY_PACK_NAME,
        generated_by=generated_by,
        derived_from=derived_from,
        rebuild_policy=rebuild_policy,
    )


from ..object_kinds import OBJECT_KIND_LABELS as _OBJECT_KIND_LABELS
_OBJECT_KIND_READER_PROFILES = {
    "person": {
        "layout": "person_profile",
        "title": "Person Profile",
        "primary_question": (
            "Who is this person, what are they known for, and how do they connect to this library?"
        ),
        "prompts": (
            ("Role", "Use the summary and claims to understand this person's role in the library."),
            ("Ideas", "Look for repeated concepts, claims, and source notes tied to this person."),
            ("Connections", "Follow relations and backlinks to nearby people, projects, and concepts."),
        ),
        "section_labels": {
            "current_state": "Profile",
            "why_it_matters": "Why They Matter",
            "evidence_traceability": "Sources About This Person",
        },
    },
    "concept": {
        "layout": "concept_brief",
        "title": "Concept Brief",
        "primary_question": "What does this idea mean, where is it used, and what supports it?",
        "prompts": (
            ("Definition", "Start with the compiled summary and strongest claims."),
            ("Use", "Follow relations to see where the idea appears in the library."),
            ("Evidence", "Check source notes and quoted evidence before reusing the concept."),
        ),
        "section_labels": {
            "current_state": "Definition",
            "why_it_matters": "Where It Matters",
            "evidence_traceability": "Evidence For This Concept",
        },
    },
    "company": {
        "layout": "entity_brief",
        "title": "Company Brief",
        "primary_question": "What is this organization, why is it relevant, and what is it connected to?",
        "prompts": (
            ("Identity", "Start with what the company is and how it is described."),
            ("Relevance", "Look for why it appears in this library."),
            ("Connections", "Follow related tools, people, projects, and claims."),
        ),
        "section_labels": {
            "current_state": "Company Snapshot",
            "why_it_matters": "Why It Matters",
            "evidence_traceability": "Sources About This Company",
        },
    },
    "tool": {
        "layout": "entity_brief",
        "title": "Tool Brief",
        "primary_question": "What does this tool do, when is it useful, and what is the evidence?",
        "prompts": (
            ("Use", "Start with the job this tool is used for."),
            ("Fit", "Look for projects, workflows, or claims that explain when it matters."),
            ("Evidence", "Check source notes before treating capabilities as facts."),
        ),
        "section_labels": {
            "current_state": "Tool Snapshot",
            "why_it_matters": "When It Matters",
            "evidence_traceability": "Sources About This Tool",
        },
    },
    "project": {
        "layout": "entity_brief",
        "title": "Project Brief",
        "primary_question": "What is this project trying to do, what changed, and what evidence supports it?",
        "prompts": (
            ("Goal", "Start with the compiled goal or current state."),
            ("Progress", "Look for events, claims, and production traces."),
            ("Connections", "Follow related people, tools, and concepts."),
        ),
        "section_labels": {
            "current_state": "Project Snapshot",
            "why_it_matters": "Why It Matters",
            "evidence_traceability": "Project Sources",
        },
    },
    "event": {
        "layout": "event_dossier",
        "title": "Event Dossier",
        "primary_question": "What happened, why does it matter, and what changed afterward?",
        "prompts": (
            ("What Happened", "Start with the compiled event summary."),
            ("Impact", "Look for claims and relations that changed because of this event."),
            ("Evidence", "Use source notes to verify timing and attribution."),
        ),
        "section_labels": {
            "current_state": "What Happened",
            "why_it_matters": "Impact",
            "evidence_traceability": "Event Sources",
        },
    },
    "claim": {
        "layout": "claim_review",
        "title": "Claim Review",
        "primary_question": "What assertion is being made, how strong is the evidence, and can it be reused?",
        "prompts": (
            ("Assertion", "Read the claim in plain language first."),
            ("Support", "Check evidence rows and source spans before trusting it."),
            ("Risk", "Look for contradictions or stale summaries before reuse."),
        ),
        "section_labels": {
            "current_state": "Claim",
            "why_it_matters": "Confidence And Risk",
            "evidence_traceability": "Evidence For This Claim",
        },
    },
    "paper": {
        "layout": "entity_brief",
        "title": "Paper Brief",
        "primary_question": "What does this paper contribute, who wrote it, and what does it build on?",
        "prompts": (
            ("Contribution", "Start with the core finding or contribution."),
            ("Context", "Look for related work, prior papers, and concepts."),
            ("Evidence", "Check source notes and quoted claims for accuracy."),
        ),
        "section_labels": {
            "current_state": "Paper Snapshot",
            "why_it_matters": "Why It Matters",
            "evidence_traceability": "Sources About This Paper",
        },
    },
    "framework": {
        "layout": "concept_brief",
        "title": "Framework Brief",
        "primary_question": "What is this framework, how is it applied, and what supports it?",
        "prompts": (
            ("Definition", "Start with what the framework is and its core structure."),
            ("Application", "Look for where and how this framework is used in practice."),
            ("Evidence", "Check source notes for grounding before adopting the framework."),
        ),
        "section_labels": {
            "current_state": "Framework Overview",
            "why_it_matters": "Application Context",
            "evidence_traceability": "Framework Sources",
        },
    },
    "method": {
        "layout": "concept_brief",
        "title": "Method Brief",
        "primary_question": "What is this method, when is it used, and what results does it produce?",
        "prompts": (
            ("Definition", "Start with a clear description of the method."),
            ("Usage", "Look for when and where this method applies."),
            ("Evidence", "Check evidence and results before recommending this method."),
        ),
        "section_labels": {
            "current_state": "Method Description",
            "why_it_matters": "When To Use",
            "evidence_traceability": "Method Sources",
        },
    },
}
_LIST_MARKER_RE = re.compile(r"^([-*•]|\d+\.)\s+")
OBJECT_SOURCE_RAIL_RELATED_LIMIT = 8


def _object_kind_label(object_kind: str) -> str:
    from ..object_kinds import display_label, normalize_kind

    raw = (object_kind or "").strip().lower()
    if not raw:
        return "Object"
    return display_label(normalize_kind(raw))


def _object_reader_profile(detail: dict[str, Any], *, relation_count: int) -> dict[str, object]:
    summary_text = str((detail.get("summary") or {}).get("summary_text") or "").strip()
    source_note_count = len(detail["provenance"]["source_notes"])
    atlas_count = len(detail["provenance"]["mocs"])
    return {
        "kind_label": _object_kind_label(str(detail["object"].get("object_kind") or "")),
        "headline": detail["object"]["title"],
        "dek": summary_text or "No compiled summary yet.",
        "supporting_line": (
            f"{len(detail['claims'])} claims · {relation_count} relations · "
            f"{source_note_count} source notes · {atlas_count} atlas pages"
        ),
        "empty_summary": not bool(summary_text),
    }


def _object_kind_profile(detail: dict[str, Any], *, relation_count: int) -> dict[str, object]:
    from ..object_kinds import normalize_kind

    object_row = detail["object"]
    object_kind = normalize_kind(str(object_row.get("object_kind") or "").strip())
    spec = _OBJECT_KIND_READER_PROFILES.get(object_kind)
    if spec is None:
        return {
            "kind": object_kind or "object",
            "layout": "object_brief",
            "title": f"{_object_kind_label(object_kind)} Brief",
            "primary_question": "What is this object, what supports it, and where should I read next?",
            "reading_prompts": [
                {
                    "label": "Summary",
                    "detail": "Start with the compiled summary and claims.",
                },
                {
                    "label": "Evidence",
                    "detail": "Check source notes and evidence rows before reuse.",
                },
                {
                    "label": "Connections",
                    "detail": f"Follow {relation_count} relation(s) and backlinks for surrounding context.",
                },
            ],
            "section_labels": {},
        }
    return {
        "kind": object_kind,
        "layout": spec["layout"],
        "title": spec["title"],
        "primary_question": spec["primary_question"],
        "reading_prompts": [
            {"label": label, "detail": detail_text}
            for label, detail_text in spec["prompts"]
        ],
        "section_labels": dict(spec["section_labels"]),
    }


def _clean_excerpt_line(line: str) -> str:
    return _LIST_MARKER_RE.sub("", line.strip()).strip()


def _source_excerpt_for_object(
    vault_dir: Path | str,
    *,
    note_path: str,
    object_id: str,
    title: str,
) -> str:
    if not note_path:
        return ""
    path = resolve_vault_dir(vault_dir) / note_path
    if not path.exists() or not path.is_file():
        return ""
    needles = [
        f"[[{object_id}]]",
        f"[[{title}]]",
        object_id,
        title,
    ]
    try:
        in_frontmatter = False
        with path.open(encoding="utf-8") as handle:
            for index, raw_line in enumerate(handle):
                raw_line = raw_line.rstrip("\n")
                if raw_line.strip() == "---":
                    if index == 0:
                        in_frontmatter = True
                        continue
                    if in_frontmatter:
                        in_frontmatter = False
                        continue
                if in_frontmatter:
                    continue
                line = _clean_excerpt_line(raw_line)
                if not line or line.startswith("---") or line.startswith("#"):
                    continue
                lowered = line.lower()
                if any(needle and needle.lower() in lowered for needle in needles):
                    return line[:240]
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _build_source_backlink_rail(
    vault_dir: Path | str,
    *,
    detail: dict[str, Any],
    relations: list[dict[str, Any]],
    requested_pack: str,
) -> dict[str, object]:
    object_id = str(detail["object"]["object_id"])
    title = str(detail["object"]["title"])
    evergreen_path = str(detail["provenance"].get("evergreen_path") or "")
    source_notes = [
        {
            **item,
            "excerpt": _source_excerpt_for_object(
                vault_dir,
                note_path=str(item.get("path") or ""),
                object_id=object_id,
                title=title,
            ),
            "jump_path": _build_note_jump_path(item.get("path"), pack_name=requested_pack),
        }
        for item in detail["provenance"]["source_notes"]
    ]
    atlas_pages = [
        {
            **item,
            "jump_path": _build_note_jump_path(item.get("path"), pack_name=requested_pack),
        }
        for item in detail["provenance"]["mocs"]
    ]
    related_objects = []
    for item in relations:
        if len(related_objects) >= OBJECT_SOURCE_RAIL_RELATED_LIMIT:
            break
        related_objects.append(
            {
                "object_id": item["target_object_id"],
                "title": item.get("target_title", item["target_object_id"]),
                "relation_type": item["relation_type"],
                "path": item.get("target_path", ""),
            }
        )
    return {
        "summary": (
            f"{len(source_notes)} source notes, {len(atlas_pages)} atlas pages, "
            f"{len(related_objects)} related objects"
        ),
        "evergreen": {
            "title": title,
            "path": evergreen_path,
            "jump_path": _build_note_jump_path(evergreen_path, pack_name=requested_pack),
        },
        "source_notes": source_notes,
        "atlas_pages": atlas_pages,
        "related_objects": related_objects,
    }


def _compiled_section(
    section_id: str,
    label: str,
    *,
    summary: str,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_items = list(items or [])
    return {
        "id": section_id,
        "label": label,
        "anchor": section_id.replace("_", "-"),
        "summary": summary,
        "item_count": len(normalized_items),
        "items": normalized_items,
    }


def _workflow_group(
    group_id: str,
    title: str,
    summary: str,
    items: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": group_id,
        "title": title,
        "summary": summary,
        "items": items,
    }


def _briefing_value_evidence_count(item: dict[str, Any]) -> int:
    evidence: set[str] = set()
    for key in ("source_paths", "note_paths", "object_ids"):
        value = item.get(key)
        if isinstance(value, list):
            evidence.update(str(entry) for entry in value if str(entry or "").strip())
    for key in ("signal_id", "path"):
        value = str(item.get(key) or "").strip()
        if value:
            evidence.add(value)
    return len(evidence)


def _briefing_value_actionability(item: dict[str, Any]) -> str:
    recommended_action = item.get("recommended_action")
    if not isinstance(recommended_action, dict):
        return "review"
    queue_status = str(recommended_action.get("queue_status") or "").strip().lower()
    if queue_status in {"queued", "running", "pending", "scheduled", "in_progress"}:
        return "queued"
    if bool(recommended_action.get("executable")):
        return "executable"
    return "review"


def _briefing_value_check(first_useful_sign: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(first_useful_sign, dict):
        return {
            "status": "empty",
            "kind": "",
            "reason": "No background insight or priority item has enough current evidence to surface.",
            "evidence_count": 0,
            "actionability": "review",
        }
    raw_evidence = first_useful_sign.get("evidence_count")
    try:
        evidence_count = int(raw_evidence)
    except (TypeError, ValueError):
        evidence_count = _briefing_value_evidence_count(first_useful_sign)
    raw_actionability = first_useful_sign.get("actionability")
    actionability = (
        str(raw_actionability).strip()
        if str(raw_actionability or "").strip()
        else _briefing_value_actionability(first_useful_sign)
    )
    kind = str(first_useful_sign.get("kind") or "")
    title = str(first_useful_sign.get("title") or "")
    return {
        "status": "useful",
        "kind": kind,
        "reason": (
            f"{title or kind} surfaced with {evidence_count} evidence reference(s) "
            f"and {actionability} follow-up."
        ),
        "evidence_count": evidence_count,
        "actionability": actionability,
    }


def _build_dashboard_workflow_groups(
    *,
    requested_pack: str,
    research_overview_supported: bool,
) -> list[dict[str, Any]]:
    return [
        _workflow_group(
            "orient",
            "Orient",
            "Start with the compiled entry products before diving into individual queues.",
            [
                {
                    "label": "Orientation Brief",
                    "path": _scoped_path("/ops/briefing", pack_name=requested_pack),
                    "detail": "Read the current entry product.",
                },
                {
                    "label": "Workbench Home",
                    "path": _scoped_path("/", pack_name=requested_pack),
                    "detail": "Return to the current shell overview.",
                },
            ],
        ),
        _workflow_group(
            "inspect",
            "Inspect",
            "Read the current knowledge state directly from compiled browsing surfaces.",
            [
                {
                    "label": "Objects",
                    "path": _scoped_path("/ops/objects", pack_name=requested_pack),
                    "detail": "Browse indexed evergreen objects.",
                },
                {
                    "label": "Search",
                    "path": _scoped_path("/search", pack_name=requested_pack),
                    "detail": "Search notes and objects across the shell.",
                },
            ],
        ),
        _workflow_group(
            "review",
            "Review",
            "Open the highest-signal maintenance surfaces for contradictions, summaries, and signals.",
            [
                {
                    "label": "Signals",
                    "path": _scoped_path("/ops/signals", pack_name=requested_pack),
                    "detail": "Review current active signals.",
                },
                {
                    "label": "Contradictions" if research_overview_supported else "Actions",
                    "path": _scoped_path(
                        "/ops/contradictions" if research_overview_supported else "/ops/actions",
                        pack_name=requested_pack,
                    ),
                    "detail": (
                        "Inspect open semantic tensions."
                        if research_overview_supported
                        else "Review queued execution actions."
                    ),
                },
            ],
        ),
        _workflow_group(
            "trace",
            "Trace",
            "Follow provenance and downstream production chains before editing or reviewing.",
            [
                {
                    "label": "Production",
                    "path": _scoped_path("/ops/production", pack_name=requested_pack),
                    "detail": "Inspect production weak points and chain state.",
                },
                {
                    "label": "Notes",
                    "path": _scoped_path(
                        "/ops/objects",
                        pack_name=requested_pack,
                    ),
                    "detail": "Use object pages as the primary trace surface.",
                },
            ],
        ),
        _workflow_group(
            "explore",
            "Explore",
            "Move through topic, graph, and timeline surfaces once the shell has oriented you.",
            [
                {
                    "label": "Events" if research_overview_supported else "Objects",
                    "path": _scoped_path(
                        "/ops/events" if research_overview_supported else "/ops/objects",
                        pack_name=requested_pack,
                    ),
                    "detail": (
                        "Explore timeline and dossier surfaces."
                        if research_overview_supported
                        else "Explore the shared-shell object browser."
                    ),
                },
                {
                    "label": "Clusters" if research_overview_supported else "Search",
                    "path": _scoped_path(
                        "/ops/clusters" if research_overview_supported else "/search",
                        pack_name=requested_pack,
                    ),
                    "detail": (
                        "Explore graph clusters and higher-order structure."
                        if research_overview_supported
                        else "Use search to move laterally through the vault."
                    ),
                },
            ],
        ),
    ]


def _operator_action(label: str, path: str, detail: str) -> dict[str, str]:
    return {
        "label": label,
        "path": path,
        "detail": detail,
    }


def _impact_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(
            str((item.get("impact_summary") or {}).get("impact_status") or "unknown")
            for item in items
        )
    )


def _capture_status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(
            str((item.get("capture_summary") or {}).get("status") or "missing")
            for item in items
        )
    )


def _section_nav_from_compiled_sections(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "href": f"#{str(section.get('anchor') or str(section.get('id') or '').replace('_', '-'))}",
            "label": str(section.get("label") or section.get("id") or ""),
        }
        for section in sections
    ]


def _db_path(vault_dir: Path | str) -> Path:
    resolved = resolve_vault_dir(vault_dir)
    return VaultLayout.from_vault(resolved).knowledge_db


def _existing_object_rows(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, str]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT object_id, title
            FROM objects
            WHERE object_id IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchall()
    return {str(object_id): str(title) for object_id, title in rows}


def _object_scope_paths(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, str]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {}
    rows = _batch_object_rows(vault_dir, normalized_object_ids, pack_name=pack_name)
    return {
        str(object_id): str(rows.get(object_id, {}).get("canonical_path") or "")
        for object_id in normalized_object_ids
    }


def _object_ids_from_claim_ids(*claim_id_lists: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for claim_ids in claim_id_lists:
        for claim_id in claim_ids:
            object_id = claim_id.split("::", 1)[0]
            if object_id and object_id not in seen:
                seen.add(object_id)
                ordered.append(object_id)
    return ordered


def _edge_kind_parts(edge_kind: str) -> tuple[str, str]:
    family, sep, subtype = str(edge_kind).partition(":")
    if not sep:
        return (family, "")
    return (family, subtype)


def _derive_cluster_structural_label(
    *,
    center_title: str,
    edge_summary_items: list[dict[str, Any]],
) -> dict[str, str]:
    contradiction_item = next((item for item in edge_summary_items if item["edge_family"] == "contradiction"), None)
    if contradiction_item is not None:
        return {
            "kind": "contradiction_cluster",
            "title": f"Contradiction cluster around {center_title}",
            "reason": f"{contradiction_item['count']} contradiction edges are present in the local graph.",
        }
    dominant = edge_summary_items[0] if edge_summary_items else None
    if dominant is None:
        return {
            "kind": "reference_cluster",
            "title": f"Reference cluster around {center_title}",
            "reason": "No internal edge structure has been materialized yet.",
        }
    if dominant["edge_family"] == "relation":
        return {
            "kind": "relation_cluster",
            "title": f"Relation cluster around {center_title}",
            "reason": f"{dominant['count']} {dominant['display_name']} dominate the local graph.",
        }
    return {
        "kind": "mixed_cluster",
        "title": f"Mixed graph cluster around {center_title}",
        "reason": f"Dominant edge family is {dominant['edge_family']}.",
    }


def _build_relation_pattern_items(edge_summary_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "edge_kind": item["edge_kind"],
            "subtype": item["edge_subtype"],
            "display_name": item["display_name"],
            "count": item["count"],
        }
        for item in edge_summary_items
        if item["edge_family"] == "relation"
    ]


def _relation_pattern_preview(relation_pattern_items: list[dict[str, Any]]) -> str:
    if not relation_pattern_items:
        return ""
    preview_items = relation_pattern_items[:2]
    preview = ", ".join(f"{item['display_name']} ({item['count']})" for item in preview_items)
    if len(relation_pattern_items) > 2:
        return f"{preview}, +{len(relation_pattern_items) - 2} more"
    return preview


def _top_counter_items(
    counts: Counter[str],
    item_map: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return [
        {**item_map[key], "object_count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if key in item_map
    ][:limit]


def _collect_cluster_provenance(
    vault_dir: Path | str,
    member_object_ids: list[str],
) -> dict[str, Any]:
    provenance_map = get_object_provenance_map(vault_dir, member_object_ids)
    source_note_counts: Counter[str] = Counter()
    source_note_items: dict[str, dict[str, Any]] = {}
    moc_counts: Counter[str] = Counter()
    moc_items: dict[str, dict[str, Any]] = {}
    for provenance in provenance_map.values():
        for note in provenance["source_notes"]:
            slug = str(note["slug"])
            source_note_items.setdefault(slug, note)
            source_note_counts[slug] += 1
        for moc in provenance["mocs"]:
            slug = str(moc["slug"])
            moc_items.setdefault(slug, moc)
            moc_counts[slug] += 1
    return {
        "source_note_counts": source_note_counts,
        "source_note_items": source_note_items,
        "moc_counts": moc_counts,
        "moc_items": moc_items,
    }


def _build_cluster_provenance_index(
    vault_dir: Path | str,
    cluster_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(row["cluster_id"]): _collect_cluster_provenance(
            vault_dir,
            [str(member["object_id"]) for member in row["members"]],
        )
        for row in cluster_rows
    }


def _build_related_cluster_items(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    requested_pack: str,
    current_source_note_items: dict[str, dict[str, Any]],
    current_moc_items: dict[str, dict[str, Any]],
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    current_source_slugs = set(current_source_note_items)
    current_moc_slugs = set(current_moc_items)
    if not current_source_slugs and not current_moc_slugs:
        return []
    related_items: list[dict[str, Any]] = []
    rows = cluster_rows if cluster_rows is not None else list_graph_clusters(vault_dir, pack_name=requested_pack, limit=200)
    for row in rows:
        if str(row["cluster_id"]) == cluster_id:
            continue
        provenance = None
        if cluster_provenance_index is not None:
            provenance = cluster_provenance_index.get(str(row["cluster_id"]))
        if provenance is None:
            member_object_ids = [str(member["object_id"]) for member in row["members"]]
            provenance = _collect_cluster_provenance(vault_dir, member_object_ids)
        shared_source_slugs = sorted(current_source_slugs & set(provenance["source_note_items"]))
        shared_moc_slugs = sorted(current_moc_slugs & set(provenance["moc_items"]))
        if not shared_source_slugs and not shared_moc_slugs:
            continue
        reason_parts: list[str] = []
        if shared_source_slugs:
            reason_parts.append(f"{len(shared_source_slugs)} shared source notes")
        if shared_moc_slugs:
            reason_parts.append(f"{len(shared_moc_slugs)} shared atlas pages")
        score = len(shared_source_slugs) * 10 + len(shared_moc_slugs) * 5 + int(row["member_count"])
        if shared_source_slugs and shared_moc_slugs:
            bridge_kind = "source_and_atlas_overlap"
        elif shared_source_slugs:
            bridge_kind = "source_overlap"
        else:
            bridge_kind = "atlas_overlap"
        if len(shared_source_slugs) >= 1 and len(shared_moc_slugs) >= 1:
            bridge_band = "strong"
        elif len(shared_source_slugs) >= 1 or len(shared_moc_slugs) >= 2:
            bridge_band = "medium"
        else:
            bridge_band = "light"
        related_items.append(
            {
                "cluster_id": str(row["cluster_id"]),
                "pack": requested_pack,
                "label": str(row["label"]),
                "display_title": f"Cluster around {row['center_title']}",
                "detail_path": (
                    f"/ops/cluster?id={quote(str(row['cluster_id']), safe='')}"
                    f"&pack={quote(requested_pack, safe='')}"
                ),
                "member_count": int(row["member_count"]),
                "shared_source_count": len(shared_source_slugs),
                "shared_moc_count": len(shared_moc_slugs),
                "shared_source_titles": [
                    str(current_source_note_items.get(slug, provenance["source_note_items"].get(slug, {})).get("title", slug))
                    for slug in shared_source_slugs
                ][:3],
                "shared_moc_titles": [
                    str(current_moc_items.get(slug, provenance["moc_items"].get(slug, {})).get("title", slug))
                    for slug in shared_moc_slugs
                ][:3],
                "bridge_kind": bridge_kind,
                "bridge_band": bridge_band,
                "reason": ", ".join(reason_parts),
                "score": score,
            }
        )
    related_items.sort(key=lambda item: (-item["score"], item["label"].lower(), item["cluster_id"]))
    return related_items[:5]


def _bridge_kind_display_name(bridge_kind: str) -> str:
    if bridge_kind == "source_and_atlas_overlap":
        return "Source + Atlas Overlap"
    if bridge_kind == "source_overlap":
        return "Source Overlap"
    if bridge_kind == "atlas_overlap":
        return "Atlas Overlap"
    return bridge_kind.replace("_", " ").title()


def _build_related_cluster_groups(related_clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in related_clusters:
        group = grouped.setdefault(
            str(item["bridge_kind"]),
            {
                "bridge_kind": str(item["bridge_kind"]),
                "display_name": _bridge_kind_display_name(str(item["bridge_kind"])),
                "count": 0,
                "cluster_titles": [],
            },
        )
        group["count"] += 1
        if item["display_title"] not in group["cluster_titles"]:
            group["cluster_titles"].append(item["display_title"])
    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["count"]), str(item["bridge_kind"])),
    )


def _plural_reader_label(label: str) -> str:
    if len(label) >= 2 and label.endswith("y") and label[-2].lower() not in "aeiou":
        return f"{label[:-1]}ies"
    if label.endswith("s"):
        return label
    return f"{label}s"


def _search_match_reason(
    *,
    query: str,
    title: str,
    summary: str,
    evidence_count: int,
) -> str:
    normalized_query = query.strip().lower()
    title_match = bool(normalized_query and normalized_query in title.lower())
    summary_match = bool(normalized_query and normalized_query in summary.lower())
    if title_match and summary_match and evidence_count > 0:
        return "Matched title, summary, and evidence-backed claims."
    if title_match and evidence_count > 0:
        return "Matched title and evidence-backed claims."
    if summary_match and evidence_count > 0:
        return "Matched summary and evidence-backed claims."
    if title_match:
        return "Matched title."
    if summary_match:
        return "Matched summary."
    if evidence_count > 0:
        return "Matched evidence-backed claims."
    return "Matched object text."


def _search_note_type_label(note_type: str) -> str:
    normalized = (note_type or "note").replace("_", " ").strip().title()
    if not normalized:
        normalized = "Note"
    if normalized.endswith("Note") or normalized.endswith("Notes"):
        return _plural_reader_label(normalized)
    return f"{normalized} Notes"


def _reader_summary(
    reader_groups: list[dict[str, Any]],
    source_groups: list[dict[str, Any]],
) -> str:
    object_parts = [
        f"{group['result_count']} {str(group['kind']).replace('_', ' ')}"
        + ("" if int(group["result_count"]) == 1 else "s")
        for group in reader_groups
    ]
    note_count = sum(int(group["result_count"]) for group in source_groups)
    if note_count:
        object_parts.append(f"{note_count} note" + ("" if note_count == 1 else "s"))
    return ", ".join(object_parts) if object_parts else "No reader results"


def _build_reader_search_projection(
    vault_dir: Path | str,
    *,
    query: str,
    objects: list[dict[str, Any]],
    notes: list[dict[str, Any]],
) -> dict[str, Any]:
    object_pack_pairs = sorted(
        {
            (str(item["object_id"]), str(item.get("row_pack") or item.get("pack") or ""))
            for item in objects
        }
    )
    summary_by_object: dict[tuple[str, str], str] = {}
    evidence_count_by_object: dict[tuple[str, str], int] = {}
    if object_pack_pairs:
        db_path = _db_path(vault_dir)
        pair_clause = " OR ".join("(object_id = ? AND pack = ?)" for _ in object_pack_pairs)
        claim_pair_clause = " OR ".join(
            "(claims.object_id = ? AND claims.pack = ?)" for _ in object_pack_pairs
        )
        pair_params = [value for pair in object_pack_pairs for value in pair]
        with sqlite3.connect(db_path) as conn:
            summary_by_object = {
                (str(object_id), str(pack)): str(summary_text or "")
                for object_id, pack, summary_text in conn.execute(
                    f"""
                    SELECT object_id, pack, summary_text
                    FROM compiled_summaries
                    WHERE {pair_clause}
                    """,
                    tuple(pair_params),
                ).fetchall()
            }
            evidence_count_by_object = {
                (str(object_id), str(pack)): int(count)
                for object_id, pack, count in conn.execute(
                    f"""
                    SELECT claims.object_id, claims.pack, COUNT(claim_evidence.claim_id)
                    FROM claims
                    LEFT JOIN claim_evidence
                      ON claim_evidence.pack = claims.pack
                     AND claim_evidence.claim_id = claims.claim_id
                    WHERE {claim_pair_clause}
                    GROUP BY claims.object_id, claims.pack
                    """,
                    tuple(pair_params),
                ).fetchall()
            }

    grouped_objects: dict[str, dict[str, Any]] = {}
    for item in objects:
        object_kind = str(item.get("object_kind") or "object").strip().lower() or "object"
        group = grouped_objects.setdefault(
            object_kind,
            {
                "kind": object_kind,
                "label": _plural_reader_label(_object_kind_label(object_kind)),
                "items": [],
                "result_count": 0,
            },
        )
        object_id = str(item["object_id"])
        row_pack = str(item.get("row_pack") or item.get("pack") or "")
        summary = summary_by_object.get((object_id, row_pack), "")
        evidence_count = evidence_count_by_object.get((object_id, row_pack), 0)
        group["items"].append(
            {
                **item,
                "summary": summary or "No compiled summary yet.",
                "evidence_count": evidence_count,
                "reason": _search_match_reason(
                    query=query,
                    title=str(item.get("title") or ""),
                    summary=summary,
                    evidence_count=evidence_count,
                ),
            }
        )
        group["result_count"] += 1

    source_groups_by_type: dict[str, dict[str, Any]] = {}
    for item in notes:
        note_type = str(item.get("note_type") or "note").strip().lower() or "note"
        group = source_groups_by_type.setdefault(
            note_type,
            {
                "kind": note_type,
                "label": _search_note_type_label(note_type),
                "items": [],
                "result_count": 0,
            },
        )
        group["items"].append(
            {
                **item,
                "reason": "Matched note title or body.",
            }
        )
        group["result_count"] += 1

    reader_groups = list(grouped_objects.values())
    source_groups = list(source_groups_by_type.values())
    return {
        "reader_groups": reader_groups,
        "source_groups": source_groups,
        "reader_summary": _reader_summary(reader_groups, source_groups),
    }


def _build_reading_routes(related_clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route_specs = [
        (
            "full_context_route",
            "Full Context Route",
            {"source_and_atlas_overlap"},
        ),
        (
            "source_continuity_route",
            "Source Continuity Route",
            {"source_and_atlas_overlap", "source_overlap"},
        ),
        (
            "atlas_continuity_route",
            "Atlas Continuity Route",
            {"source_and_atlas_overlap", "atlas_overlap"},
        ),
    ]
    routes: list[dict[str, Any]] = []
    for index, (route_kind, display_name, allowed_bridge_kinds) in enumerate(route_specs, start=1):
        candidate = next(
            (item for item in related_clusters if str(item["bridge_kind"]) in allowed_bridge_kinds),
            None,
        )
        if candidate is None:
            continue
        if route_kind == "full_context_route":
            route_reason = (
                "Best first if you want both evidence continuity and atlas continuity across clusters."
            )
            route_score = int(candidate["score"]) + 30
        elif route_kind == "source_continuity_route":
            route_reason = "Best if you want to keep reading along shared source-note coverage."
            route_score = int(candidate["score"]) + 20
        else:
            route_reason = "Best if you want to keep reading along shared atlas-page coverage."
            route_score = int(candidate["score"]) + 10
        routes.append(
            {
                "route_kind": route_kind,
                "route_rank": index,
                "route_score": route_score,
                "display_name": display_name,
                "cluster_id": candidate["cluster_id"],
                "display_title": candidate["display_title"],
                "detail_path": candidate["detail_path"],
                "bridge_kind": candidate["bridge_kind"],
                "bridge_band": candidate["bridge_band"],
                "reason": candidate["reason"],
                "route_reason": route_reason,
            }
        )
    return routes


def _build_cluster_surface_sections(
    vault_dir: Path | str,
    *,
    cluster: dict[str, Any],
    edges: list[dict[str, Any]],
    requested_pack: str,
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    member_object_ids = [str(member["object_id"]) for member in cluster["members"]]
    edge_kind_counts = Counter(edge["edge_kind"] for edge in edges)
    edge_summary_items = [
        {
            "edge_kind": edge_kind,
            "edge_family": _edge_kind_parts(edge_kind)[0],
            "edge_subtype": _edge_kind_parts(edge_kind)[1],
            "display_name": (
                "contradiction links"
                if _edge_kind_parts(edge_kind)[0] == "contradiction"
                else (
                    f"{_edge_kind_parts(edge_kind)[1].replace('_', ' ')} links"
                    if _edge_kind_parts(edge_kind)[0] == "relation" and _edge_kind_parts(edge_kind)[1]
                    else edge_kind.replace(":", " ")
                )
            ),
            "count": count,
        }
        for edge_kind, count in sorted(edge_kind_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    object_kind_counts = Counter(
        str(member["object_kind"])
        for member in cluster["members"]
        if member.get("object_kind")
    )
    review_context = get_review_context(vault_dir, member_object_ids, pack_name=requested_pack)
    open_contradictions = [
        {
            "contradiction_id": item["contradiction_id"],
            "subject_key": item["subject_key"],
            "object_ids": _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"]),
            "path": f"/ops/contradictions?q={quote(str(item['subject_key']), safe='')}",
        }
        for item in list_contradictions(
            vault_dir,
            pack_name=requested_pack,
            status="open",
            limit=MAX_PAGE_SIZE,
        )
        if set(_object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])) & set(member_object_ids)
    ][:5]
    stale_summaries = list_stale_summaries(
        vault_dir,
        pack_name=requested_pack,
        object_ids=member_object_ids,
        limit=5,
    )
    provenance = (
        cluster_provenance_index.get(str(cluster["cluster_id"]))
        if cluster_provenance_index is not None and str(cluster["cluster_id"]) in cluster_provenance_index
        else _collect_cluster_provenance(vault_dir, member_object_ids)
    )
    source_note_counts = provenance["source_note_counts"]
    source_note_items = provenance["source_note_items"]
    moc_counts = provenance["moc_counts"]
    moc_items = provenance["moc_items"]

    top_edge_kind = next(iter(sorted(edge_kind_counts.items(), key=lambda item: (-item[1], item[0]))), None)
    kind_summary = ", ".join(
        f"{kind} {count}"
        for kind, count in sorted(object_kind_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    summary_bullets = [
        f"{cluster['member_count']} objects in a {cluster['cluster_kind']} cluster centered on {cluster['center_title']}.",
    ]
    if top_edge_kind:
        summary_bullets.append(
            f"{len(edges)} internal edges across {len(edge_kind_counts)} edge kinds; dominant edge kind is {top_edge_kind[0]} ({top_edge_kind[1]})."
        )
    if kind_summary:
        summary_bullets.append(f"Object kinds in scope: {kind_summary}.")
    if review_context["source_note_count"] or review_context["moc_count"]:
        summary_bullets.append(
            f"Coverage currently includes {review_context['source_note_count']} source/deep-dive notes and {review_context['moc_count']} atlas pages."
        )
    if review_context["open_contradiction_count"] or review_context["stale_summary_count"]:
        summary_bullets.append(
            f"Review pressure: {review_context['open_contradiction_count']} open contradictions and {review_context['stale_summary_count']} stale summaries in this cluster scope."
        )
    structural_label = _derive_cluster_structural_label(
        center_title=str(cluster["center_title"]),
        edge_summary_items=edge_summary_items,
    )
    relation_pattern_items = _build_relation_pattern_items(edge_summary_items)
    relation_pattern_preview = _relation_pattern_preview(relation_pattern_items)
    related_clusters = _build_related_cluster_items(
        vault_dir,
        cluster_id=str(cluster["cluster_id"]),
        requested_pack=requested_pack,
        current_source_note_items=source_note_items,
        current_moc_items=moc_items,
        cluster_rows=cluster_rows,
        cluster_provenance_index=cluster_provenance_index,
    )
    related_cluster_groups = _build_related_cluster_groups(related_clusters)
    reading_routes = _build_reading_routes(related_clusters)
    next_read_cluster = related_clusters[0] if related_clusters else None

    return {
        "display_title": structural_label["title"],
        "edge_count": len(edges),
        "edge_kind_counts": dict(edge_kind_counts),
        "edge_summary_items": edge_summary_items,
        "relation_pattern_items": relation_pattern_items,
        "relation_pattern_preview": relation_pattern_preview,
        "object_kind_counts": dict(object_kind_counts),
        "structural_label": structural_label,
        "review_context": review_context,
        "open_contradictions": open_contradictions,
        "stale_summaries": stale_summaries,
        "related_clusters": related_clusters,
        "related_cluster_groups": related_cluster_groups,
        "reading_routes": reading_routes,
        "next_read_cluster": next_read_cluster,
        "top_source_notes": _top_counter_items(source_note_counts, source_note_items),
        "top_mocs": _top_counter_items(moc_counts, moc_items),
        "summary_bullets": summary_bullets,
    }


def build_cluster_summary_payload(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    pack_name: str | None = None,
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    detail = get_graph_cluster_detail(vault_dir, cluster_id, pack_name=pack_name)
    cluster = detail["cluster"]
    requested_pack = pack_name or str(cluster["pack"])
    member_index = {str(member["object_id"]): member for member in cluster["members"]}
    detail_path = (
        f"/ops/cluster?id={quote(str(cluster['cluster_id']), safe='')}"
        f"&pack={quote(requested_pack, safe='')}"
    )
    enriched_cluster = {
        **cluster,
        "detail_path": detail_path,
        "center_object_path": _scoped_path(
            f"/object?id={quote(str(cluster['center_object_id']), safe='')}",
            pack_name=requested_pack,
        ),
        "member_links": [
            {
                **member,
                "path": _scoped_path(
                    f"/object?id={quote(str(member['object_id']), safe='')}",
                    pack_name=requested_pack,
                ),
            }
            for member in cluster["members"]
        ],
    }
    enriched_edges = [
        {
            **edge,
            "source_title": member_index.get(str(edge["source_object_id"]), {}).get(
                "title",
                str(edge["source_object_id"]),
            ),
            "target_title": member_index.get(str(edge["target_object_id"]), {}).get(
                "title",
                str(edge["target_object_id"]),
            ),
            "source_path": _scoped_path(
                f"/object?id={quote(str(edge['source_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "target_path": _scoped_path(
                f"/object?id={quote(str(edge['target_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for edge in detail["edges"]
    ]
    sections = _build_cluster_surface_sections(
        vault_dir,
        cluster=enriched_cluster,
        edges=enriched_edges,
        requested_pack=requested_pack,
        cluster_rows=cluster_rows,
        cluster_provenance_index=cluster_provenance_index,
    )
    return {
        "requested_pack": requested_pack,
        "cluster": enriched_cluster,
        "edges": enriched_edges,
        **sections,
    }


def _build_production_summary(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    object_traceability = [
        get_object_traceability(vault_dir, object_id, pack_name=pack_name)
        for object_id in normalized_object_ids
    ]
    source_note_counts: Counter[str] = Counter()
    atlas_page_counts: Counter[str] = Counter()
    source_note_items: dict[str, dict[str, str]] = {}
    atlas_page_items: dict[str, dict[str, str]] = {}
    missing_source_object_ids: list[str] = []
    missing_atlas_object_ids: list[str] = []

    for traceability in object_traceability:
        object_id = traceability["object"]["object_id"]
        if not traceability["source_notes"]:
            missing_source_object_ids.append(object_id)
        if not traceability["atlas_pages"]:
            missing_atlas_object_ids.append(object_id)
        for item in traceability["source_notes"]:
            source_note_items.setdefault(item["path"], item)
            source_note_counts[item["path"]] += 1
        for item in traceability["atlas_pages"]:
            atlas_page_items.setdefault(item["slug"], item)
            atlas_page_counts[item["slug"]] += 1

    def _top_items(
        counts: Counter[str],
        item_map: dict[str, dict[str, str]],
    ) -> list[dict[str, Any]]:
        ordered = sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        return [
            {
                **item_map[key],
                "object_count": count,
            }
            for key, count in ordered
            if key in item_map
        ][:5]

    signals: list[dict[str, Any]] = []
    if missing_source_object_ids:
        signals.append(
            {
                "code": "missing_source_notes",
                "count": len(missing_source_object_ids),
                "label": "Missing source notes",
                "object_ids": missing_source_object_ids,
            }
        )
    if missing_atlas_object_ids:
        signals.append(
            {
                "code": "missing_atlas_reach",
                "count": len(missing_atlas_object_ids),
                "label": "Missing Atlas / MOC reach",
                "object_ids": missing_atlas_object_ids,
            }
        )

    return {
        "object_count": len(normalized_object_ids),
        "counts": {
            "source_notes": len(source_note_items),
            "atlas_pages": len(atlas_page_items),
        },
        "top_source_notes": _top_items(source_note_counts, source_note_items),
        "top_atlas_pages": _top_items(atlas_page_counts, atlas_page_items),
        "signals": signals,
    }


def _build_production_weak_points(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    return list_production_gaps(vault_dir, pack_name=pack_name, query=query, limit=limit)


def _build_evolution_section(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    link_type: str | None = None,
    status: str = "candidate",
    scoped_object_ids: list[str] | None = None,
) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in (scoped_object_ids or []) if object_id))
    canonical_paths = {
        path
        for path in _object_scope_paths(
            vault_dir,
            normalized_object_ids,
            pack_name=pack_name,
        ).values()
        if path
    }
    reviewed_links = list_evolution_links(
        vault_dir,
        object_ids=normalized_object_ids or None,
        pack_name=pack_name,
        query=query,
        link_type=link_type,
    )
    reviewed_evolution_ids = {str(item["evolution_id"]) for item in reviewed_links}
    accepted_links = [item for item in reviewed_links if item["status"] == "accepted"]
    rejected_links = [item for item in reviewed_links if item["status"] == "rejected"]
    candidate_items = [
        item
        for item in list_evolution_candidates(
            vault_dir,
            object_ids=normalized_object_ids or None,
            pack_name=pack_name,
            query=query,
            link_type=link_type,
            status="candidate",
        )
        if item["evolution_id"] not in reviewed_evolution_ids
    ]
    if normalized_object_ids:
        filtered_items: list[dict[str, Any]] = []
        for item in candidate_items:
            refs = (str(item["earlier_ref"]), str(item["later_ref"]))
            if item["subject_kind"] == "object" and item["subject_id"] in normalized_object_ids:
                filtered_items.append(item)
                continue
            if any(
                ref.startswith(f"claim://{object_id}::") or ref == f"object://{object_id}"
                for object_id in normalized_object_ids
                for ref in refs
            ):
                filtered_items.append(item)
                continue
            if any(path in canonical_paths for path in item["source_paths"]):
                filtered_items.append(item)
        candidate_items = filtered_items
        accepted_links = [
            item for item in accepted_links
            if set(item.get("object_ids", [])).intersection(normalized_object_ids)
        ]
        rejected_links = [
            item for item in rejected_links
            if set(item.get("object_ids", [])).intersection(normalized_object_ids)
        ]
    if status == "accepted":
        candidate_items = []
    elif status == "rejected":
        candidate_items = []
        accepted_links = []
    elif status == "candidate":
        pass
    else:
        # keep all sections visible on the default "all" view
        status = "all"
    return {
        "accepted_links": accepted_links,
        "rejected_links": rejected_links,
        "candidate_items": candidate_items,
        "candidate_count": len(candidate_items),
        "accepted_count": len(accepted_links),
        "rejected_count": len(rejected_links),
        "link_types": sorted(
            {
                *(item["link_type"] for item in candidate_items),
                *(str(item.get("link_type") or "") for item in accepted_links),
                *(str(item.get("link_type") or "") for item in rejected_links),
            }
        ),
        "status": status,
    }


def build_signal_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    signal_type: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    governance_contract = describe_governance_contract(pack_name=pack_name)
    surface_contract = describe_observation_surface_contract(
        pack_name=pack_name,
        surface_kind="signals",
    )
    if surface_contract["status"] == "missing":
        return {
            "screen": "signals/browser",
            "requested_pack": requested_pack,
            "surface_contract": surface_contract,
            "governance_contract": governance_contract,
            "operator_rail": [],
            "surface_error": (
                f"Pack '{surface_contract['requested_pack']}' does not expose a shared shell "
                f"'signals' surface."
            ),
            "items": [],
            "count": 0,
            "query": query or "",
            "signal_type": signal_type or "",
            "type_counts": {},
            "impact_counts": {},
            "signal_type_explanations": SIGNAL_TYPE_EXPLANATIONS,
        }
    items = list_signals(vault_dir, pack_name=pack_name, signal_type=signal_type, query=query)
    return {
        "screen": "signals/browser",
        "requested_pack": requested_pack,
        "surface_contract": surface_contract,
        "governance_contract": governance_contract,
        "operator_rail": [
            _operator_action(
                "Action Queue",
                _scoped_path("/ops/actions", pack_name=requested_pack),
                "Run or inspect queued actions.",
            ),
            _operator_action(
                "Production Browser",
                _scoped_path("/ops/production", pack_name=requested_pack),
                "Trace current production weak points.",
            ),
            _operator_action(
                "Contradictions",
                _scoped_path(
                    "/ops/contradictions" if _supports_research_shell(pack_name) else "/search",
                    pack_name=requested_pack,
                ),
                (
                    "Review semantic tensions."
                    if _supports_research_shell(pack_name)
                    else "Shared-shell search fallback."
                ),
            ),
            _operator_action(
                "Orientation Brief",
                _scoped_path("/ops/briefing", pack_name=requested_pack),
                "Return to the current entry product.",
            ),
        ],
        "items": items,
        "count": len(items),
        "query": query or "",
        "signal_type": signal_type or "",
        "type_counts": dict(Counter(item["signal_type"] for item in items)),
        "impact_counts": _impact_counts(items),
        "capture_status_counts": _capture_status_counts(items),
        "signal_type_explanations": SIGNAL_TYPE_EXPLANATIONS,
    }


def build_action_queue_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    items = list_action_queue(vault_dir, pack_name=pack_name, status=status, query=query)
    return {
        "screen": "actions/browser",
        "requested_pack": requested_pack,
        "governance_contract": describe_governance_contract(pack_name=pack_name),
        "items": items,
        "count": len(items),
        "query": query or "",
        "status": status or "",
        "status_counts": dict(Counter(str(item["status"]) for item in items)),
        "impact_counts": _impact_counts(items),
        "queued_safe_count": sum(1 for item in items if item.get("status") == "queued" and item.get("safe_to_run")),
        "failed_count": sum(1 for item in items if item.get("status") == "failed"),
        "failure_buckets": dict(
            Counter(
                str(item.get("failure_bucket") or "")
                for item in items
                if item.get("status") == "failed" and str(item.get("failure_bucket") or "")
            )
        ),
    }


def build_queue_overview_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Counts + oldest-row hints across the four maintainer queues.

    The four queues — concept candidates, contradictions, signals
    waiting for action, action-worker tasks — historically lived on
    four different ``/ops/*`` pages with no top-level summary, so
    the operator could not tell whether the day's triage was done
    without visiting each page.  This payload powers ``/ops/queue``,
    a single landing page that answers "is there anything to do?"
    in one screen.

    The implementation is intentionally cheap: it reuses each
    queue's existing ``list_*`` function with a small ``limit`` to
    sample the oldest pending item, then counts items in
    interpretable buckets.  Healthy state (productive signals,
    completed actions, evergreens already in the truth store) is
    surfaced separately so the page makes the "no action needed"
    case visible too.
    """
    requested_pack = pack_name or ""

    # Candidates: registry load is the cost; capping at 1 is enough
    # for the oldest-pending hint — the registry's own ``count``
    # field carries the total without iterating the list.
    #
    # The candidate registry is intentionally vault-global, not pack-
    # scoped — ``ConceptRegistry`` lives at one path per vault and
    # is keyed by slug across the whole vault.  ``list_candidate_concepts``
    # therefore takes no ``pack_name`` argument; passing one would
    # raise ``TypeError``.  The other queues' pack scoping still
    # holds because contradictions / actions / signals all live in
    # per-pack tables or ledgers.
    candidate_payload = list_candidate_concepts(vault_dir, limit=1)
    candidates_first = (candidate_payload.get("candidates") or [None])[0]
    candidates_pending = int(candidate_payload.get("count") or 0)
    candidates_oldest = candidates_first

    # Contradictions: lightweight ``GROUP BY status`` + LIMIT 1
    # probe instead of fetching up to 500 rows just to count.
    contradiction_overview = count_contradictions_by_status(
        vault_dir, pack_name=pack_name
    )
    contradictions_pending = int(
        contradiction_overview.get("by_status", {}).get("open") or 0
    )
    contradictions_oldest = contradiction_overview.get("oldest_open")

    # Signals come from a JSONL ledger so we still scan once, but
    # bound the cost: read just enough to find the oldest waiting
    # row, and use the same pass for the productive count.  500
    # rows already covers any realistic active signals window.
    signals = list_signals(vault_dir, pack_name=pack_name, limit=500)
    signals_waiting = [s for s in signals if s.get("capture_status") == "waiting"]
    signals_productive = [s for s in signals if s.get("capture_status") == "productive"]
    signals_pending = len(signals_waiting)
    signals_oldest = signals_waiting[0] if signals_waiting else None

    # Action queue: lightweight pass that skips the per-row
    # resolver-metadata + contract-metadata enrichment that
    # ``list_action_queue`` runs on every row.
    action_overview = count_action_queue_by_status(vault_dir, pack_name=pack_name)
    action_by_status = action_overview.get("by_status", {})
    actions_pending = int(
        (action_by_status.get("failed") or 0) + (action_by_status.get("blocked") or 0)
    )
    actions_succeeded_count = int(action_by_status.get("succeeded") or 0)
    actions_oldest = action_overview.get("oldest_failed")

    # Evergreen/object total — informational, surfaces "you have a
    # vault" so the healthy-state line carries weight.
    try:
        evergreen_total = count_objects(vault_dir, pack_name=pack_name)
    except Exception:
        evergreen_total = 0

    queues = [
        {
            "id": "concepts",
            "label": "concept candidate" + ("s" if candidates_pending != 1 else ""),
            "count": candidates_pending,
            "browse_path": _scoped_path(
                "/ops/queue/concepts", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(candidates_oldest.get("title") or candidates_oldest.get("slug") or "")
                if candidates_oldest
                else ""
            ),
            "oldest_at": (
                str(candidates_oldest.get("last_seen_at") or "")
                if candidates_oldest
                else ""
            ),
        },
        {
            "id": "contradictions",
            "label": "contradiction" + ("s" if contradictions_pending != 1 else "") + " open",
            "count": contradictions_pending,
            "browse_path": _scoped_path(
                "/ops/queue/contradictions", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(contradictions_oldest.get("subject_key") or "")
                if contradictions_oldest
                else ""
            ),
            "oldest_at": "",
        },
        {
            "id": "signals",
            "label": "signal" + ("s" if signals_pending != 1 else "") + " waiting",
            "count": signals_pending,
            "browse_path": _scoped_path(
                "/ops/queue/signals?status=waiting", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(signals_oldest.get("title") or signals_oldest.get("signal_type") or "")
                if signals_oldest
                else ""
            ),
            "oldest_at": (
                str(signals_oldest.get("detected_at") or "")
                if signals_oldest
                else ""
            ),
        },
        {
            "id": "actions",
            "label": "action" + ("s" if actions_pending != 1 else "") + " failed/blocked",
            "count": actions_pending,
            "browse_path": _scoped_path(
                "/ops/queue/actions?status=failed", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(actions_oldest.get("title") or actions_oldest.get("action_id") or "")
                if actions_oldest
                else ""
            ),
            "oldest_at": (
                str(actions_oldest.get("created_at") or "")
                if actions_oldest
                else ""
            ),
        },
    ]

    healthy = {
        "productive_signals": len(signals_productive),
        "succeeded_actions": actions_succeeded_count,
        "evergreen_total": evergreen_total,
    }

    return {
        "screen": "ops/queue",
        "requested_pack": requested_pack,
        "queues": queues,
        "pending_total": sum(q["count"] for q in queues),
        "healthy": healthy,
    }


def build_candidate_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_CANDIDATE_BROWSER_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    payload = list_candidate_concepts(vault_dir, query=query, limit=limit, offset=offset)
    payload["requested_pack"] = requested_pack
    payload["operator_rail"] = [
        _operator_action(
            "Orientation Brief",
            _scoped_path("/ops/briefing", pack_name=requested_pack),
            "Read the compiled context before changing canonical concepts.",
        ),
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Check whether this candidate is attached to active production signals.",
        ),
        _operator_action(
            "Actions",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Inspect queued work that may depend on candidate canonicalization.",
        ),
        _operator_action(
            "Objects",
            _scoped_path("/ops/objects", pack_name=requested_pack),
            "Compare candidates against active Evergreen objects.",
        ),
    ]
    payload["query"] = query or ""
    return payload


def build_briefing_payload(vault_dir: Path | str, *, pack_name: str | None = None) -> dict[str, Any]:
    requested_pack = pack_name or ""
    surface_contract = describe_observation_surface_contract(
        pack_name=pack_name,
        surface_kind="briefing",
    )
    assembly_contract = _assembly_contract("orientation_brief", pack_name=pack_name)
    governance_contract = describe_governance_contract(pack_name=pack_name)
    if surface_contract["status"] == "missing":
        return {
            "screen": "briefing/intelligence",
            "requested_pack": requested_pack,
            "projection_label": _access_projection_label(
                surface="briefing",
                pack_name=pack_name,
                generated_by="build_briefing_payload",
                derived_from=("knowledge.db", "signals ledger", "actions ledger"),
            ),
            "surface_contract": surface_contract,
            "assembly_contract": assembly_contract,
            "governance_contract": governance_contract,
            "surface_error": (
                f"Pack '{surface_contract['requested_pack']}' does not expose a shared shell "
                f"'briefing' surface."
            ),
            "generated_at": "",
            "recent_signal_count": 0,
            "unresolved_issue_count": 0,
            "changed_object_count": 0,
            "active_topic_count": 0,
            "recent_signals": [],
            "unresolved_issues": [],
            "changed_objects": [],
            "active_topics": [],
            "insight_count": 0,
            "priority_item_count": 0,
            "insights": [],
            "priority_items": [],
            "compiled_sections": [],
            "section_nav": [],
            "first_useful_sign": None,
            "first_useful_sign_check": {
                "status": "empty",
                "kind": "",
                "reason": "No briefing surface is available for this pack.",
                "evidence_count": 0,
                "actionability": "review",
            },
            "background_policy": {
                "governed_signal_types": [],
                "auto_queue_enabled_signal_types": [],
                "review_only_signal_types": [],
                "active_auto_queue_signal_count": 0,
                "active_review_only_signal_count": 0,
                "skipped_signal_count": 0,
                "skipped_reasons": {},
                "signal_type_decisions": {},
            },
            "loop_summary": {
                "productive_count": 0,
                "waiting_count": 0,
                "running_count": 0,
                "ready_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "stalled_count": 0,
                "review_only_count": 0,
            },
            "queue_summary": {
                "queued_count": 0,
                "safe_queued_count": 0,
                "running_count": 0,
                "failed_count": 0,
                "failure_buckets": {},
            },
        }
    snapshot = get_briefing_snapshot(vault_dir, pack_name=pack_name)
    changed_items = [
        {
            "kind": "changed_object",
            "label": str(item["title"]),
            "path": str(item["path"]),
            "detail": f"Changed object · {item['object_id']}",
        }
        for item in snapshot.get("changed_objects", [])[:5]
    ]
    what_matters_items = [
        {
            "kind": "active_topic",
            "label": str(item["title"]),
            "path": str(item["path"]),
            "detail": f"{int(item['signal_count'])} signals in scope",
        }
        for item in snapshot.get("active_topics", [])[:5]
    ]
    needs_review_items = [
        {
            "kind": str(item["signal_type"]),
            "label": str(item["title"]),
            "path": str(item["source_path"]),
            "detail": str(item["detail"]),
        }
        for item in snapshot.get("unresolved_issues", [])[:5]
    ]
    next_read_items = [
        {
            "kind": str(item["kind"]),
            "label": str(item["title"]),
            "path": str(item["path"]),
            "detail": str(item["detail"]),
        }
        for item in snapshot.get("insights", [])[:5]
    ]
    next_action_items = [
        {
            "kind": str(item["kind"]),
            "label": str(item["title"]),
            "path": str(((item.get("recommended_action") or {}).get("path")) or item.get("path") or ""),
            "detail": str(((item.get("recommended_action") or {}).get("label")) or item.get("detail") or ""),
        }
        for item in snapshot.get("priority_items", [])[:5]
    ]
    impact_counts = _impact_counts(snapshot.get("recent_signals", []))
    loop_summary = {
        "productive_count": impact_counts.get("productive", 0),
        "waiting_count": impact_counts.get("waiting", 0),
        "running_count": impact_counts.get("running", 0),
        "ready_count": impact_counts.get("ready", 0),
        "completed_count": impact_counts.get("completed", 0),
        "failed_count": impact_counts.get("failed", 0),
        "stalled_count": impact_counts.get("stalled", 0),
        "review_only_count": impact_counts.get("review_only", 0),
    }
    signal_loop_items = [
        {
            "kind": "productive",
            "label": "Productive",
            "path": _scoped_path("/ops/signals", pack_name=requested_pack),
            "detail": f"{loop_summary['productive_count']} signals produced visible downstream change.",
        },
        {
            "kind": "waiting",
            "label": "Waiting",
            "path": _scoped_path("/ops/actions", pack_name=requested_pack),
            "detail": f"{loop_summary['waiting_count']} signals currently have queued execution waiting.",
        },
        {
            "kind": "running",
            "label": "Running",
            "path": _scoped_path("/ops/actions", pack_name=requested_pack),
            "detail": f"{loop_summary['running_count']} signals are currently executing.",
        },
        {
            "kind": "blocked",
            "label": "Blocked",
            "path": _scoped_path("/ops/actions", pack_name=requested_pack),
            "detail": (
                f"{loop_summary['failed_count'] + loop_summary['stalled_count']} signals are failed or stalled."
            ),
        },
        {
            "kind": "review_only",
            "label": "Review Only",
            "path": _scoped_path("/ops/signals", pack_name=requested_pack),
            "detail": f"{loop_summary['review_only_count']} signals currently route to review rather than queued execution.",
        },
    ]
    productive_signal = next(
        (
            item
            for item in snapshot.get("recent_signals", [])
            if str((item.get("impact_summary") or {}).get("impact_status") or "") == "productive"
        ),
        None,
    )
    first_useful_sign = (
        {
            "signal_id": str(productive_signal.get("signal_id") or ""),
            "kind": str(productive_signal.get("signal_type") or ""),
            "title": str(productive_signal.get("title") or ""),
            "detail": str((productive_signal.get("impact_summary") or {}).get("impact_detail") or ""),
            "path": str(
                ((productive_signal.get("recommended_action") or {}).get("queue_path"))
                or ((productive_signal.get("recommended_action") or {}).get("path"))
                or productive_signal.get("source_path")
                or ""
            ),
            "source_paths": list(productive_signal.get("note_paths", [])),
            "object_ids": list(productive_signal.get("object_ids", [])),
            "recommended_action": productive_signal.get("recommended_action"),
        }
        if productive_signal is not None
        else snapshot.get("first_useful_sign")
    )
    first_useful_sign_check = _briefing_value_check(first_useful_sign)
    inbound_capture_items = [
        {
            "kind": "capture_signal",
            "label": str(item.get("title") or ""),
            "path": str(item.get("source_path") or ""),
            "detail": str((item.get("capture_summary") or {}).get("summary") or ""),
        }
        for item in snapshot.get("recent_signals", [])
        if str((item.get("capture_summary") or {}).get("status") or "") != "missing"
    ]
    compiled_sections = [
        _compiled_section(
            "signal_loop",
            "Signal Loop",
            summary=(
                f"{loop_summary['productive_count']} productive, "
                f"{loop_summary['waiting_count']} waiting, "
                f"{loop_summary['failed_count'] + loop_summary['stalled_count']} blocked/stalled."
            ),
            items=signal_loop_items,
        ),
        _compiled_section(
            "inbound_capture",
            "Inbound Capture",
            summary=(
                f"{len(inbound_capture_items)} recent signals currently expose deterministic inbound capture audit."
                if inbound_capture_items
                else "No recent signals currently carry inbound capture audit."
            ),
            items=inbound_capture_items,
        ),
        _compiled_section(
            "what_changed",
            "What Changed",
            summary=f"{len(changed_items)} changed objects surfaced recently.",
            items=changed_items,
        ),
        _compiled_section(
            "what_matters",
            "What Matters",
            summary=f"{len(what_matters_items)} active topics currently dominate the signal surface.",
            items=what_matters_items,
        ),
        _compiled_section(
            "needs_review",
            "Needs Review",
            summary=f"{len(needs_review_items)} unresolved issues currently deserve attention.",
            items=needs_review_items,
        ),
        _compiled_section(
            "next_reads",
            "Next Reads",
            summary=f"{len(next_read_items)} compiled next-read routes were surfaced from current evidence.",
            items=next_read_items,
        ),
        _compiled_section(
            "next_actions",
            "Next Actions",
            summary=f"{len(next_action_items)} next actions are currently available from the queue and briefing logic.",
            items=next_action_items,
        ),
    ]
    operator_rail = [
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Open active signal review from the current shell.",
        ),
        _operator_action(
            "Action Queue",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Run or inspect queued actions.",
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect production-chain weak points and reach.",
        ),
        _operator_action(
            "Search",
            _scoped_path("/search", pack_name=requested_pack),
            "Jump into freeform search from the current orientation pass.",
        ),
    ]
    payload: dict[str, Any] = {
        "screen": "briefing/intelligence",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="briefing",
            pack_name=pack_name,
            generated_by="build_briefing_payload",
            derived_from=("knowledge.db", "signals ledger", "actions ledger"),
        ),
        "surface_contract": surface_contract,
        "assembly_contract": assembly_contract,
        "governance_contract": governance_contract,
        **snapshot,
        "first_useful_sign": first_useful_sign,
        "first_useful_sign_check": first_useful_sign_check,
        "loop_summary": loop_summary,
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }
    _emit_briefing_reuse(
        vault_dir,
        payload,
        pack=requested_pack or PRIMARY_PACK_NAME,
        consumer_ref="view:briefing",
    )
    return payload


def build_object_page_payload(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    research_shell_enabled = _supports_research_shell(pack_name)
    detail = get_object_detail(vault_dir, object_id, pack_name=pack_name)
    neighborhood = get_topic_neighborhood(vault_dir, object_id, pack_name=pack_name)
    review_context = get_review_context(vault_dir, [object_id], pack_name=pack_name) if research_shell_enabled else {}
    neighbor_titles = {item["object_id"]: item["title"] for item in neighborhood["neighbors"]}
    relations = [
        {
            **item,
            "target_title": neighbor_titles.get(item["target_object_id"], item["target_object_id"]),
            "target_path": _scoped_path(
                f"/object?id={quote(str(item['target_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in detail["relations"]
    ]
    research_links = {
        "events_path": _scoped_path(f"/ops/events?q={quote(object_id, safe='')}", pack_name=requested_pack),
        "contradictions_path": _scoped_path(
            f"/ops/contradictions?q={quote(object_id, safe='')}",
            pack_name=requested_pack,
        ),
        "summaries_path": _scoped_path(
            f"/ops/summaries?q={quote(object_id, safe='')}",
            pack_name=requested_pack,
        ),
        "atlas_path": _scoped_path(f"/atlas?q={quote(object_id, safe='')}", pack_name=requested_pack),
    } if research_shell_enabled else {
        "events_path": "",
        "contradictions_path": "",
        "summaries_path": "",
        "atlas_path": "",
    }
    evolution_section = (
        _build_evolution_section(
            vault_dir,
            pack_name=pack_name,
            status="all",
            scoped_object_ids=[object_id],
        )
        if research_shell_enabled
        else {
            "accepted_links": [],
            "rejected_links": [],
            "candidate_items": [],
            "candidate_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "link_types": [],
            "status": "all",
        }
    )
    summary_text = detail["summary"]["summary_text"] if detail["summary"] else ""
    reader_profile = _object_reader_profile(detail, relation_count=len(relations))
    kind_profile = _object_kind_profile(detail, relation_count=len(relations))
    section_labels = kind_profile["section_labels"]
    source_backlink_rail = _build_source_backlink_rail(
        vault_dir,
        detail=detail,
        relations=relations,
        requested_pack=requested_pack,
    )
    stale_summary_details = (
        list_stale_summaries(
            vault_dir,
            pack_name=pack_name,
            object_ids=[object_id],
            limit=10,
        )
        if research_shell_enabled
        else []
    )
    production_chain = get_object_traceability(vault_dir, object_id, pack_name=pack_name)
    source_chain = get_object_source_chain(vault_dir, object_id, pack_name=pack_name)
    compiled_sections = [
        _compiled_section(
            "current_state",
            str(section_labels.get("current_state") or "Current State"),
            summary=summary_text or "No compiled summary yet.",
            items=[
                {
                    "kind": "summary",
                    "label": detail["object"]["title"],
                    "path": "",
                    "detail": summary_text or "No compiled summary yet.",
                },
                {"kind": "claims", "label": "Claims", "path": "", "detail": f"{len(detail['claims'])} claims"},
                {"kind": "relations", "label": "Relations", "path": "", "detail": f"{len(relations)} relations"},
            ],
        ),
        _compiled_section(
            "why_it_matters",
            str(section_labels.get("why_it_matters") or "Why It Matters"),
            summary=f"{len(detail['contradictions']) if research_shell_enabled else 0} contradictions and {review_context.get('stale_summary_count', 0)} stale summaries shape current maintenance urgency.",
            items=[
                {
                    "kind": "topic",
                    "label": "Explore topic",
                    "path": _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": "Open the surrounding topic neighborhood.",
                },
                *(
                    [
                        {
                            "kind": "events",
                            "label": "Related events",
                            "path": research_links["events_path"],
                            "detail": "See timeline context for this object.",
                        }
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            str(section_labels.get("evidence_traceability") or "Evidence Traceability"),
            summary=f"{len(detail['evidence'])} evidence rows, {len(detail['provenance']['source_notes'])} source notes, {len(detail['provenance']['mocs'])} atlas pages.",
            items=[
                {
                    "kind": "evergreen",
                    "label": "Evergreen note",
                    "path": _scoped_path(
                        f"/note?path={quote(detail['provenance']['evergreen_path'], safe='')}",
                        pack_name=requested_pack,
                    )
                    if detail["provenance"]["evergreen_path"]
                    else "",
                    "detail": detail["provenance"]["evergreen_path"] or "No evergreen markdown path",
                },
                *[
                    {
                        "kind": "source_note",
                        "label": item["title"],
                        "path": _scoped_path(f"/note?path={quote(item['path'], safe='')}", pack_name=requested_pack),
                        "detail": item["note_type"],
                    }
                    for item in detail["provenance"]["source_notes"][:3]
                ],
            ],
        ),
        _compiled_section(
            "production_chain",
            "Production Chain",
            summary=str(production_chain.get("chain_summary") or ""),
            items=[
                {
                    "kind": "chain_status",
                    "label": "Chain status",
                    "path": "",
                    "detail": str(production_chain.get("chain_status") or ""),
                },
                {
                    "kind": "missing_stages",
                    "label": "Missing stages",
                    "path": "",
                    "detail": ", ".join(
                        str(item).replace("_", " ")
                        for item in production_chain.get("missing_stages", [])
                    )
                    or "None",
                },
                *[
                    {
                        "kind": "atlas_page",
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(item['path'], safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": "Atlas / MOC reach",
                    }
                    for item in production_chain["atlas_pages"][:2]
                ],
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{len(detail['contradictions']) if research_shell_enabled else 0} contradictions and {len(stale_summary_details) if research_shell_enabled else 0} stale-summary signals remain.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": research_links["contradictions_path"],
                        "detail": item["status"],
                    }
                    for item in detail["contradictions"][:3]
                ],
                *[
                    {
                        "kind": "stale_summary",
                        "label": item["title"],
                        "path": research_links["summaries_path"],
                        "detail": ", ".join(item["reason_texts"]),
                    }
                    for item in stale_summary_details[:2]
                ],
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Use the surrounding compiled products to continue reading or review.",
            items=[
                {
                    "kind": "topic",
                    "label": "Topic overview",
                    "path": _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": "Open the surrounding topic page.",
                },
                *(
                    [
                        {
                            "kind": "events",
                            "label": "Event dossier",
                            "path": research_links["events_path"],
                            "detail": "See event and time context.",
                        },
                        {
                            "kind": "contradictions",
                            "label": "Contradiction review",
                            "path": research_links["contradictions_path"],
                            "detail": "Inspect open conflicts.",
                        },
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Topic overview",
            _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
            "Open the surrounding topic page.",
        ),
        _operator_action(
            "Event dossier" if research_shell_enabled else "Signals",
            research_links["events_path"] if research_shell_enabled else _scoped_path("/ops/signals", pack_name=requested_pack),
            (
                "See timeline context for this object."
                if research_shell_enabled
                else "Open active signal review."
            ),
        ),
        _operator_action(
            "Contradiction review" if research_shell_enabled else "Search",
            research_links["contradictions_path"] if research_shell_enabled else _scoped_path("/search", pack_name=requested_pack),
            (
                "Inspect open contradictions for this object."
                if research_shell_enabled
                else "Search laterally from this object."
            ),
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect downstream production chain state.",
        ),
    ]
    payload: dict[str, Any] = {
        "screen": "object/page",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="object_page",
            pack_name=pack_name,
            generated_by="build_object_page_payload",
            derived_from=("knowledge.db", "review audit"),
        ),
        "assembly_contract": _assembly_contract("object_brief", pack_name=pack_name),
        "research_shell_enabled": research_shell_enabled,
        **detail,
        "reader_profile": reader_profile,
        "kind_profile": kind_profile,
        "source_backlink_rail": source_backlink_rail,
        "source_chain": source_chain,
        "production_chain": production_chain,
        "relations": relations,
        "claim_count": len(detail["claims"]),
        "relation_count": len(relations),
        "contradiction_count": len(detail["contradictions"]) if research_shell_enabled else 0,
        "evidence_count": len(detail["evidence"]),
        "context": {
            "object_kind": detail["object"]["object_kind"],
            "source_slug": detail["object"]["source_slug"],
            "canonical_path": detail["object"]["canonical_path"],
        },
        "provenance": detail["provenance"],
        "mention_kind_stats": list_mention_kind_stats(vault_dir, object_id, pack_name=pack_name),
        "relation_kind_stats": list_relation_kind_stats(vault_dir, object_id, pack_name=pack_name),
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=[object_id], limit=8) if research_shell_enabled else [],
        "evolution": evolution_section,
        "stale_summary_details": stale_summary_details,
        "open_contradiction_ids": (
            [item["contradiction_id"] for item in detail["contradictions"] if item["status"] == "open"]
            if research_shell_enabled
            else []
        ),
        "links": {
            "topic_path": _scoped_path(f"/topic?id={quote(object_id, safe='')}", pack_name=requested_pack),
            **research_links,
        },
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": [
            {"href": "#summary", "label": "Summary"},
            {"href": "#sources", "label": "Sources"},
            *_section_nav_from_compiled_sections(compiled_sections),
            {"href": "#claims", "label": "Claims"},
            {"href": "#relations", "label": "Relations"},
            *(
                [{"href": "#contradictions", "label": "Contradictions"}]
                if research_shell_enabled
                else []
            ),
        ],
    }
    _emit_briefing_reuse(
        vault_dir,
        payload,
        pack=str((detail.get("object") or {}).get("pack") or requested_pack),
        consumer_ref=f"view:object_page:{object_id}",
    )
    return payload


def build_topic_overview_payload(
    vault_dir: Path | str,
    object_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    research_shell_enabled = _supports_research_shell(pack_name)
    neighborhood = get_topic_neighborhood(vault_dir, object_id, pack_name=pack_name)
    detail = get_object_detail(vault_dir, object_id, pack_name=pack_name)
    scoped_object_ids = [object_id, *[item["object_id"] for item in neighborhood["neighbors"]]]
    review_context = (
        get_review_context(
            vault_dir,
            scoped_object_ids,
            pack_name=pack_name,
        )
        if research_shell_enabled
        else {}
    )
    scoped_stale_summaries = (
        list_stale_summaries(
            vault_dir,
            pack_name=pack_name,
            object_ids=scoped_object_ids,
            limit=50,
        )
        if research_shell_enabled
        else []
    )
    scoped_contradictions = (
        [
            item
            for item in list_contradictions(vault_dir, pack_name=pack_name, limit=100)
            if set(item["positive_claim_ids"] + item["negative_claim_ids"])
            and any(claim_id.split("::", 1)[0] in set(scoped_object_ids) for claim_id in item["positive_claim_ids"] + item["negative_claim_ids"])
            and item["status"] == "open"
        ]
        if research_shell_enabled
        else []
    )
    neighbors = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in neighborhood["neighbors"]
    ]
    production_summary = _build_production_summary(
        vault_dir,
        scoped_object_ids,
        pack_name=pack_name,
    )
    research_links = {
        "events_path": _scoped_path(f"/ops/events?q={quote(object_id, safe='')}", pack_name=requested_pack),
        "contradictions_path": _scoped_path(
            f"/ops/contradictions?q={quote(object_id, safe='')}",
            pack_name=requested_pack,
        ),
        "summaries_path": _scoped_path(
            f"/ops/summaries?q={quote(object_id, safe='')}",
            pack_name=requested_pack,
        ),
        "atlas_path": _scoped_path(f"/atlas?q={quote(object_id, safe='')}", pack_name=requested_pack),
    } if research_shell_enabled else {
        "events_path": "",
        "contradictions_path": "",
        "summaries_path": "",
        "atlas_path": "",
    }
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=f"{len(neighbors)} neighbors and {len(neighborhood['edges'])} edges define this topic view.",
            items=[
                {
                    "kind": "center_object",
                    "label": detail["object"]["title"],
                    "path": _scoped_path(f"/object?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": detail["summary"]["summary_text"] if detail["summary"] else "No compiled summary yet.",
                },
                *[
                    {
                        "kind": "neighbor",
                        "label": item["title"],
                        "path": item["object_path"],
                        "detail": "Neighbor in current topic scope",
                    }
                    for item in neighbors[:3]
                ],
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=f"{review_context.get('open_contradiction_count', 0)} contradictions and {review_context.get('stale_summary_count', 0)} stale summaries currently shape this topic.",
            items=[
                *(
                    [
                        {
                            "kind": "events",
                            "label": "Event dossier",
                            "path": research_links["events_path"],
                            "detail": "See time-bounded activity around this topic.",
                        },
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary=f"{len(detail['provenance']['source_notes'])} source notes and {len(detail['provenance']['mocs'])} atlas pages anchor this topic.",
            items=[
                *[
                    {
                        "kind": "source_note",
                        "label": item["title"],
                        "path": _scoped_path(f"/note?path={quote(item['path'], safe='')}", pack_name=requested_pack),
                        "detail": item["note_type"],
                    }
                    for item in detail["provenance"]["source_notes"][:3]
                ],
                *[
                    {
                        "kind": "atlas_page",
                        "label": item["title"],
                        "path": _scoped_path(f"/note?path={quote(item['path'], safe='')}", pack_name=requested_pack),
                        "detail": "Atlas / MOC",
                    }
                    for item in detail["provenance"]["mocs"][:3]
                ],
            ],
        ),
        _compiled_section(
            "production_chain",
            "Production Chain",
            summary=(
                f"{production_summary['object_count']} objects in scope currently resolve to "
                f"{production_summary['counts']['source_notes']} source notes and "
                f"{production_summary['counts']['atlas_pages']} atlas pages."
            ),
            items=[
                *[
                    {
                        "kind": "top_atlas_page",
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(item['path'], safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"Reaches {item['object_count']} objects in this topic scope.",
                    }
                    for item in production_summary["top_atlas_pages"][:2]
                ],
                *[
                    {
                        "kind": "gap_signal",
                        "label": item["label"],
                        "path": _scoped_path(
                            f"/ops/production?q={quote(object_id, safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"{item['count']} objects in this topic scope.",
                    }
                    for item in production_summary["signals"][:3]
                ],
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{len(scoped_contradictions)} open contradictions and {len(scoped_stale_summaries)} stale summaries remain in this topic scope.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": research_links["contradictions_path"],
                        "detail": item["status"],
                    }
                    for item in scoped_contradictions[:3]
                ],
                *[
                    {
                        "kind": "stale_summary",
                        "label": item["title"],
                        "path": research_links["summaries_path"],
                        "detail": ", ".join(item["reason_texts"]),
                    }
                    for item in scoped_stale_summaries[:2]
                ],
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Jump from the topic hub into the most useful next compiled products.",
            items=[
                {
                    "kind": "center_object",
                    "label": "Center object",
                    "path": _scoped_path(f"/object?id={quote(object_id, safe='')}", pack_name=requested_pack),
                    "detail": "Open the canonical object page.",
                },
                *(
                    [
                        {
                            "kind": "contradictions",
                            "label": "Contradictions",
                            "path": research_links["contradictions_path"],
                            "detail": "Review open tensions.",
                        },
                        {
                            "kind": "atlas",
                            "label": "Atlas / MOC",
                            "path": research_links["atlas_path"],
                            "detail": "Open atlas reach.",
                        },
                    ]
                    if research_shell_enabled
                    else []
                ),
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Center object",
            _scoped_path(f"/object?id={quote(object_id, safe='')}", pack_name=requested_pack),
            "Open the canonical object page.",
        ),
        _operator_action(
            "Event dossier" if research_shell_enabled else "Signals",
            research_links["events_path"] if research_shell_enabled else _scoped_path("/ops/signals", pack_name=requested_pack),
            (
                "See time-bounded activity around this topic."
                if research_shell_enabled
                else "Review active shell signals."
            ),
        ),
        _operator_action(
            "Contradictions" if research_shell_enabled else "Search",
            research_links["contradictions_path"] if research_shell_enabled else _scoped_path("/search", pack_name=requested_pack),
            (
                "Review open tensions in topic scope."
                if research_shell_enabled
                else "Search laterally from this topic."
            ),
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect production-chain weak points in the current shell.",
        ),
    ]
    payload: dict[str, Any] = {
        "screen": "overview/topic",
        "requested_pack": requested_pack,
        "assembly_contract": _assembly_contract("topic_overview", pack_name=pack_name),
        "research_shell_enabled": research_shell_enabled,
        **neighborhood,
        "neighbors": neighbors,
        "edge_count": len(neighborhood["edges"]),
        "neighbor_count": len(neighbors),
        "center_summary": detail["summary"]["summary_text"] if detail["summary"] else "",
        "provenance": detail["provenance"],
        "production_summary": production_summary,
        "review_context": review_context,
        "review_history": (
            list_review_actions(
                vault_dir,
                object_ids=scoped_object_ids,
                limit=8,
            )
            if research_shell_enabled
            else []
        ),
        "evolution": (
            _build_evolution_section(
                vault_dir,
                pack_name=pack_name,
                status="all",
                scoped_object_ids=scoped_object_ids,
            )
            if research_shell_enabled
            else {
                "accepted_links": [],
                "rejected_links": [],
                "candidate_items": [],
                "candidate_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "link_types": [],
                "status": "all",
            }
        ),
        "scoped_object_ids": scoped_object_ids,
        "scoped_stale_summary_ids": [item["object_id"] for item in scoped_stale_summaries],
        "scoped_open_contradiction_ids": [item["contradiction_id"] for item in scoped_contradictions],
        "links": {
            "center_object_path": _scoped_path(
                f"/object?id={quote(object_id, safe='')}",
                pack_name=requested_pack,
            ),
            **research_links,
        },
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }
    _emit_briefing_reuse(
        vault_dir,
        payload,
        pack=str((neighborhood.get("center") or {}).get("row_pack") or requested_pack),
        consumer_ref=f"view:topic_overview:{object_id}",
    )
    return payload


def build_timeline_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Daily digest of ``audit_events`` for the maintainer dashboard.

    Pre-fix the maintainer side had ``/ops/pulse`` (live tail) and
    ``/ops/events`` (object-keyed dossier) but no "what got created
    today / yesterday / last week" view.  This payload groups the
    last ``days`` days of audit events by date, surfaces the highest-
    signal event types per day (new evergreens, github intake,
    absorb errors, crystal synthesis), and samples a handful of
    affected slugs so the user can click straight through to a
    specific note from the dashboard.

    Returns ``{"days": [{date, total, by_type: {...},
    samples: [{slug, title, path}], errors: [{type, slug, snippet}]}]}``
    in reverse-chronological order.
    """
    from datetime import datetime, timedelta, timezone
    requested_pack = pack_name or ""
    window = max(1, days if days is not None else DEFAULT_TIMELINE_DAYS)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).strftime("%Y-%m-%d")

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/timeline",
            "requested_pack": requested_pack,
            "window_days": window,
            "days": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    days_map: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as conn:
        # Per-day, per-type counts.  ``date()`` rolls a UTC ISO
        # timestamp into ``YYYY-MM-DD`` — the same key the renderer
        # uses to header each section.
        rows = conn.execute(
            """
            SELECT date(timestamp) AS day, event_type, COUNT(*) AS n
              FROM audit_events
             WHERE date(timestamp) >= ?
             GROUP BY day, event_type
            """,
            (cutoff,),
        ).fetchall()
        for day, event_type, count in rows:
            if not day:
                continue
            bucket = days_map.setdefault(day, {
                "date": day, "total": 0, "by_type": {},
                "samples": [], "errors": [],
            })
            bucket["by_type"][event_type] = int(count)
            bucket["total"] += int(count)

        # Sample evergreens promoted today/yesterday/etc — give the
        # user a clickable list rather than a bare count.
        sample_rows = conn.execute(
            """
            SELECT date(timestamp) AS day,
                   json_extract(payload_json, '$.slug') AS slug,
                   json_extract(payload_json, '$.title') AS title
              FROM audit_events
             WHERE event_type = 'evergreen_auto_promoted'
               AND date(timestamp) >= ?
             ORDER BY timestamp DESC
            """,
            (cutoff,),
        ).fetchall()
        for day, slug, title in sample_rows:
            if not day or not slug:
                continue
            bucket = days_map.get(day)
            if bucket is None:
                continue
            if len(bucket["samples"]) >= DEFAULT_TIMELINE_SAMPLE_SIZE:
                continue
            note_path = f"10-Knowledge/Evergreen/{slug}.md"
            bucket["samples"].append({
                "slug": str(slug),
                "title": str(title or slug),
                "note_href": _scoped_path(
                    f"/note?path={quote(note_path, safe='')}",
                    pack_name=requested_pack,
                ),
            })

        # Error / skip events get their own short list — these are
        # the things the maintainer most often opens the dashboard
        # to chase down.  Types live in ``TIMELINE_ERROR_EVENT_TYPES``
        # so the SQL ``IN`` clause stays in sync with downstream
        # consumers (e.g. the renderer's "error" pill colouring).
        placeholders = ",".join("?" for _ in TIMELINE_ERROR_EVENT_TYPES)
        error_rows = conn.execute(
            f"""
            SELECT date(timestamp) AS day, event_type,
                   COALESCE(json_extract(payload_json, '$.source'),
                            json_extract(payload_json, '$.slug'),
                            slug) AS subject,
                   substr(payload_json, 1, {TIMELINE_SNIPPET_CHARS}) AS snippet
              FROM audit_events
             WHERE event_type IN ({placeholders})
               AND date(timestamp) >= ?
             ORDER BY timestamp DESC
            """,
            (*TIMELINE_ERROR_EVENT_TYPES, cutoff),
        ).fetchall()
        for day, event_type, subject, snippet in error_rows:
            if not day:
                continue
            bucket = days_map.get(day)
            if bucket is None:
                continue
            if len(bucket["errors"]) >= DEFAULT_TIMELINE_SAMPLE_SIZE:
                continue
            bucket["errors"].append({
                "event_type": str(event_type),
                "subject": str(subject or "(unspecified)"),
                "snippet": str(snippet or ""),
            })

    days_sorted = sorted(
        days_map.values(), key=lambda d: d["date"], reverse=True,
    )
    return {
        "screen": "ops/timeline",
        "requested_pack": requested_pack,
        "window_days": window,
        "days": days_sorted,
        "available": True,
        "highlighted_types": list(TIMELINE_HIGHLIGHTED_TYPES),
    }


# ---------------------------------------------------------------------------
# BL-053: Ops workbench IA — by-day "today" digest + by-run "runs" index
# ---------------------------------------------------------------------------

# Per-card event-type buckets for ``/ops/today``.
#
# M24.0 stop-gap (2026-05-14): card event_types are now sourced from
# ``event_evidence_registry`` so the same lists drive the calendar
# on ``/digests``, Layer 0 of the M23 digest, and these cards.
# Previously three independent allowlists disagreed and the operator
# saw 27 / 7 / many different counts for the same day.  The registry
# excludes legacy / debug-only event types from the primary count by
# default so high-volume forensic rows (``atlas_updated_from_registry``,
# ``quality_checked``, etc.) don't drown the cards.
from ..audit_identity import (
    audit_cluster_ids,
    audit_object_ids,
    audit_slug_for_column,
)
from ..audit_time import local_day as _audit_local_day
from ..event_evidence_registry import (
    CATEGORIES as _EVT_CATEGORIES,
    event_types_for_category as _evt_for_category,
)

_TODAY_CARD_LABELS: dict[str, str] = {
    "intake": "Intake",
    "absorb": "Absorb",
    "synthesis": "Synthesis",
    "governance": "Governance",
    "failures": "Failures",
}

# Legacy: kept for back-compat with anything that imported the
# old card list shape.  Same content the previous TODAY_DIGEST_CARDS
# carried; the M25.3 view model below uses ``M25_LIFECYCLE_CARDS``.
TODAY_DIGEST_CARDS: tuple[tuple[str, str, tuple[str, ...]], ...] = tuple(
    (cat, _TODAY_CARD_LABELS[cat], _evt_for_category(cat))
    for cat in _EVT_CATEGORIES
)


# M25.3: hybrid cards keyed on lifecycle state.  Each card carries
# both the primary (state count) and secondary (events-in-window
# count) numbers per the M25 plan §M25.3.  The event-category
# mapping below decides which event_types feed each card's
# secondary number.  ``governance`` events live on both Accepted
# (promote_concept) and NeedsAction (open contradictions); we
# split that category at the event-type level so the secondary
# counts don't double-count.
M25_LIFECYCLE_CARD_DEFS: tuple[dict[str, Any], ...] = (
    {
        "id": "Received",
        "label": "Received",
        "explainer": (
            "Items where evidence shows intake but no extraction "
            "yet."
        ),
        "categories": ("intake",),
        "secondary_verb": "arrived today",
    },
    {
        "id": "Extracted",
        "label": "Extracted",
        "explainer": (
            "Items where the absorber ran, producing candidates "
            "waiting for promotion."
        ),
        "categories": ("absorb",),
        # ``evergreen_auto_promoted`` and ``promote_concept`` move
        # items to Accepted, not Extracted — exclude from secondary.
        "exclude_event_types": (
            "evergreen_auto_promoted",
            "evergreen_created",
        ),
        "secondary_verb": "extracted today",
    },
    {
        "id": "Accepted",
        "label": "Accepted",
        "explainer": (
            "Items with a canonical artifact in the vault."
        ),
        # Accepted is the promote-event signal: auto-promote
        # (absorb-cat) + operator promote (governance-cat).
        #
        # Codex review on PR #237 caught two double-counting bugs
        # that landed on main inside the M25.3 squash before the
        # fix could ship.  This PR re-applies the fix:
        #
        #   * ``ovp-promote run`` emits BOTH ``promote_concept``
        #     and ``promotion`` (M24.2 wired them as a pair for
        #     different consumers — kernel reads promote_concept,
        #     doctor mtime check reads promotion).  Counting both
        #     turns one operator action into "2 accepted today".
        #     Drop ``promotion``; ``promote_concept`` is the
        #     canonical card-counting row.
        #   * ``source_archived_to_processed`` is in the intake
        #     category and counts on the Received card.  Per the
        #     M24.1 doc fix (PR #230) it's a Received signal —
        #     03-Processed is the absorber's INPUT, not its output.
        #     Drop here so a single archival doesn't increment
        #     both cards.
        "categories": (),
        "include_event_types": (
            "evergreen_auto_promoted",
            "promote_concept",
            "evergreen_created",
        ),
        "secondary_verb": "accepted today",
    },
    {
        "id": "Synthesized",
        "label": "Synthesized",
        "explainer": (
            "Items in clusters with a fresh synthesis crystal."
        ),
        "categories": ("synthesis",),
        "secondary_verb": "synthesized today",
    },
    {
        "id": "NeedsAction",
        "label": "Needs Action",
        "explainer": (
            "Items blocked or waiting on operator action — "
            "failures, open contradictions, stale review queues."
        ),
        "categories": ("failures",),
        "secondary_verb": "new blockers today",
    },
)


def _event_types_for_card(card: dict[str, Any]) -> tuple[str, ...]:
    """Compose the event_type list for a hybrid card's secondary
    count.  Starts from the card's declared categories, adds any
    ``include_event_types``, removes any ``exclude_event_types``.
    """
    result: list[str] = []
    seen: set[str] = set()
    for cat in card.get("categories", ()):
        for et in _evt_for_category(cat):
            if et not in seen:
                seen.add(et)
                result.append(et)
    for et in card.get("include_event_types", ()):
        if et not in seen:
            seen.add(et)
            result.append(et)
    excluded = set(card.get("exclude_event_types", ()))
    return tuple(et for et in result if et not in excluded)


# BL-101: the Activity card secondary number is a DISTINCT ITEM
# count, not a raw event-row count.  One source emits several intake
# rows; one promote run emits one row per candidate.  The identity
# kind depends on the lifecycle state the card represents.
_ACTIVITY_IDENTITY_KIND: dict[str, str] = {
    "Received": "source",
    "Extracted": "source",
    "Accepted": "object",
    "Synthesized": "cluster",
    "NeedsAction": "source",
}


def _activity_item_identity(
    state: str, slug: str, payload: dict[str, Any]
) -> str | None:
    """Stable distinct-count identity for one audit row under a
    given Activity card, or None when the row carries no usable
    identity (then it is shown in the drilldown but counted by
    neither side — so card count == drilldown distinct count holds
    by construction).

    Identity kind per state (BL-101): source slug for
    Received/Extracted/NeedsAction, object id for Accepted, cluster
    id for Synthesized.  ``min()`` picks a deterministic
    representative when a payload carries several.
    """
    kind = _ACTIVITY_IDENTITY_KIND.get(state, "source")
    if kind == "object":
        ids = audit_object_ids(payload)
        if ids:
            return min(ids)
        # promote rows that only carry a source fall back to the
        # source identity so they still count once.
        return _source_identity(slug, payload)
    if kind == "cluster":
        ids = audit_cluster_ids(payload)
        return min(ids) if ids else None
    return _source_identity(slug, payload)


def _source_identity(slug: str, payload: dict[str, Any]) -> str | None:
    """Source-class distinct identity: the populated ``slug`` column
    if present, else derived from the payload exactly as ingest's
    ``_infer_audit_slug`` would (``file`` / ``source`` / ``path``
    basename).  The ``slug`` column is only ~60% backfilled on the
    live vault (M24 PR-B), so relying on it alone would silently
    drop ~40% of source rows from the count."""
    s = (slug or "").strip()
    if s:
        return s
    derived = audit_slug_for_column(payload)
    return derived or None


def _audit_row_pack(payload: dict[str, Any]) -> str | None:
    """Pack recorded in the audit payload, or None for legacy rows
    that predate pack stamping."""
    pack = payload.get("pack")
    return str(pack) if pack else None


def _fetch_activity_rows(
    conn: sqlite3.Connection,
    event_types: tuple[str, ...],
    date_key: str,
    effective_pack: str,
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Rows for an Activity card / its drilldown, scoped by
    operator-local day (BL-102) and pack.

    Day bucketing is done in Python via the shared
    ``audit_time.local_day`` so UTC-``Z`` and naive-local rows fall
    on the same operator day — SQLite ``date(timestamp)`` mixed the
    two clocks.  A coarse ``substr`` prefilter (±1 day) keeps the
    Python scan bounded; a tz shift moves a row at most one calendar
    day.  Pack scoping: matching pack included, different pack
    excluded, legacy pack-less rows only under the default pack.
    Both the card count and the drilldown call THIS, so they cannot
    disagree.
    """
    if not event_types:
        return []
    try:
        anchor = _dt.datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return []
    day_prefixes = [
        (anchor + _dt.timedelta(days=d)).strftime("%Y-%m-%d")
        for d in (-1, 0, 1)
    ]
    et_ph = ",".join("?" for _ in event_types)
    pre_ph = ",".join("?" for _ in day_prefixes)
    raw = conn.execute(
        f"""
        SELECT timestamp, event_type, slug, payload_json
          FROM audit_events
         WHERE event_type IN ({et_ph})
           AND substr(timestamp, 1, 10) IN ({pre_ph})
        """,
        (*event_types, *day_prefixes),
    ).fetchall()
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for ts, et, slug, pj in raw:
        if _audit_local_day(str(ts or "")) != date_key:
            continue
        try:
            payload = json.loads(pj or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        row_pack = _audit_row_pack(payload)
        if row_pack is None:
            if effective_pack != PRIMARY_PACK_NAME:
                continue
        elif row_pack != effective_pack:
            continue
        out.append(
            (str(ts or ""), str(et or ""), str(slug or ""), payload)
        )
    return out


def _state_for_event_types(event_types: tuple[str, ...]) -> str:
    """Infer the lifecycle state a card-drilldown belongs to from its
    event_types set.  Card links carry exactly a card's composed
    event_types, so an exact set match resolves the state without an
    extra URL param.  Empty string when it can't be resolved (the
    drilldown then shows rows but no distinct-item reconciliation)."""
    want = set(event_types)
    if not want:
        return ""
    for card_def in M25_LIFECYCLE_CARD_DEFS:
        if set(_event_types_for_card(card_def)) == want:
            return str(card_def["id"])
    return ""


def _build_m25_hybrid_cards(
    db_path: Path,
    *,
    date_key: str,
    requested_pack: str,
    effective_pack: str,
) -> list[dict[str, Any]]:
    """Build the five M25 hybrid cards.

    See ``build_today_digest_payload`` docstring for the shape /
    contract.  Single sqlite connection so we don't reopen per
    card.
    """
    cards: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        has_ops_state = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='ops_state'"
        ).fetchone() is not None

        for card_def in M25_LIFECYCLE_CARD_DEFS:
            state = str(card_def["id"])
            event_types = _event_types_for_card(card_def)

            # ── Primary number + samples ──────────────────────
            primary_count = 0
            samples: list[dict[str, str]] = []
            if has_ops_state:
                primary_row = conn.execute(
                    "SELECT COUNT(*) FROM ops_state "
                    " WHERE pack = ? AND state = ?",
                    (effective_pack, state),
                ).fetchone()
                primary_count = int(primary_row[0] or 0) if primary_row else 0

                # Samples: 3 newest items per card, sourced from
                # ``ops_state`` (M25 plan §M25.3 lock — samples
                # come from items, not events).  NeedsAction is
                # the one exception: oldest first so the operator
                # sees the most-aged blockers.
                order_dir = (
                    "ASC" if state == "NeedsAction" else "DESC"
                )
                sample_rows = conn.execute(
                    f"""
                    SELECT item_kind, item_id, last_evidence_at
                      FROM ops_state
                     WHERE pack = ? AND state = ?
                     ORDER BY last_evidence_at {order_dir}
                     LIMIT ?
                    """,
                    (effective_pack, state, TODAY_CARD_SAMPLE_SIZE),
                ).fetchall()

                # Resolve source slugs to vault paths so samples link
                # to real routes (mirrors the M25.2 lookup).
                source_slugs = [
                    str(r[1]) for r in sample_rows
                    if r and r[0] == "source" and r[1]
                ]
                slug_to_path: dict[str, str] = {}
                if source_slugs:
                    has_pages = conn.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pages_index'"
                    ).fetchone()
                    if has_pages is not None:
                        placeholders = ",".join("?" * len(source_slugs))
                        for slug, path in conn.execute(
                            f"SELECT slug, path FROM pages_index "
                            f" WHERE slug IN ({placeholders})",
                            source_slugs,
                        ).fetchall():
                            if slug and path:
                                slug_to_path[str(slug)] = str(path)

                for kind, item_id, last_ts in sample_rows:
                    kind_str = str(kind or "")
                    item_id_str = str(item_id or "")
                    source_path = (
                        slug_to_path.get(item_id_str)
                        if kind_str == "source"
                        else ""
                    )
                    href = _items_primary_href(
                        kind_str, item_id_str, effective_pack,
                        source_path=source_path or "",
                    )
                    samples.append({
                        "item_kind": kind_str,
                        "item_id": item_id_str,
                        "last_evidence_at": str(last_ts or ""),
                        "path": href,
                    })

            # ── Secondary number (distinct items on this day) ──
            # BL-101/BL-102: count DISTINCT items (not raw event
            # rows), bucketed by operator-local day with pack
            # scoping.  ``by_type`` stays a raw-row breakdown so the
            # drilldown evidence still reconciles per event_type.
            event_count = 0
            by_type: dict[str, int] = {}
            if event_types:
                rows = _fetch_activity_rows(
                    conn, event_types, date_key, effective_pack
                )
                identities: set[str] = set()
                for _ts, et, slug, payload in rows:
                    by_type[et] = by_type.get(et, 0) + 1
                    ident = _activity_item_identity(state, slug, payload)
                    if ident is not None:
                        identities.add(ident)
                by_type = dict(
                    sorted(
                        by_type.items(),
                        key=lambda kv: kv[1],
                        reverse=True,
                    )
                )
                event_count = len(identities)

            # ── Hrefs ─────────────────────────────────────────
            # Primary CTA → /ops/items.  Critically NO date param:
            # the primary number is "all current items in this
            # state", not date-windowed (M25 plan §M25.2/3 lock).
            primary_href_parts = [f"state={quote(state, safe='')}"]
            if requested_pack:
                primary_href_parts.append(
                    f"pack={quote(requested_pack, safe='')}"
                )
            primary_href = (
                f"/ops/items?{'&'.join(primary_href_parts)}"
            )

            # Secondary CTA → /ops/events/audit (M25.4).  This is
            # the raw-audit-evidence view that reads the same SQL
            # the card secondary count used, so card N === page N
            # by construction.  The legacy /ops/events (timeline
            # projection) remains accessible from the audit page's
            # role banner.
            #
            # M25.4 (codex review on PR #239): set the URL limit to
            # at LEAST the raw evidence-row volume (NOT the distinct
            # item count) so the drilldown isn't silently truncated
            # — one item can carry many rows.  Clamp to the audit
            # view's hard MAX so the URL stays bounded.
            secondary_href = ""
            if event_types:
                raw_row_total = sum(by_type.values())
                target_limit = max(EVENTS_AUDIT_DEFAULT_LIMIT, raw_row_total)
                target_limit = min(target_limit, EVENTS_AUDIT_MAX_LIMIT)
                see_all_qs_parts = [
                    f"date={quote(date_key, safe='')}",
                    f"limit={target_limit}",
                    "event_types=" + quote(",".join(event_types), safe=""),
                ]
                secondary_href = _scoped_path(
                    f"/ops/events/audit?{'&'.join(see_all_qs_parts)}",
                    pack_name=requested_pack,
                )

            # Per-state secondary label.  Fall back to the
            # conservative "N evidence events today" when the
            # default verb would be misleading.
            secondary_verb = str(card_def.get("secondary_verb", ""))
            secondary_label = (
                f"{event_count} {secondary_verb}"
                if secondary_verb
                else f"{event_count} items today"
            )

            cards.append({
                "id": state,
                "label": str(card_def["label"]),
                "explainer": str(card_def.get("explainer", "")),
                "primary_count": primary_count,
                "primary_href": primary_href,
                "event_count": event_count,
                "event_label": secondary_label,
                "event_href": secondary_href,
                "event_by_type": by_type,
                "event_types": list(event_types),
                "samples": samples,
            })
    return cards

# Cap on how many sample rows each ``/ops/today`` card surfaces.
# Cards are skim-mode — operators click through to the per-stage page
# for the long tail.
TODAY_CARD_SAMPLE_SIZE = 5

# Default lookback window for ``/ops/runs``: how many recent
# transactions to show.  30 covers ~1 week of normal cadence (~3-5
# runs/day across full+incremental) and keeps the page small.
DEFAULT_RUNS_INDEX_LIMIT = 30

# A transaction with no ``transaction_completed`` row is treated as
# ``running`` for this many hours after start; older than that and
# we surface it as ``stale`` (probably crashed mid-run with no
# completion event ever written).  6h covers the longest legitimate
# full run on the live vault (~3h) with comfortable headroom.
RUNS_STALE_AFTER_HOURS = 6


def build_today_digest_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    """M25.3 hybrid cards for ``/ops/today``.

    Five cards keyed on the lifecycle vocabulary
    (Received / Extracted / Accepted / Synthesized / NeedsAction).
    Each card carries two parallel numbers per the M25 plan
    §M25.3:

    * **Primary** — items currently in this state, read from
      ``ops_state``.  Primary CTA targets ``/ops/items?state=…``
      with NO date param (cards count "current items", not
      date-windowed; adding date would break card-N === page-N).
    * **Secondary** — evidence events for this state in the
      operator's date window, read from ``audit_events``.
      Secondary CTA targets ``/ops/events?event_types=…&date=…``
      (M25.4 will move this to ``/ops/events/audit`` to honor
      raw-audit semantics).

    Samples come from ``ops_state`` rows, not event rows — the
    plan locks this so the visible items match what the primary
    number counted.

    ``target_date`` accepts ``YYYY-MM-DD`` for back-dated views
    (defaults to today UTC).  The date affects the SECONDARY
    number only; the primary number is "right now", not historic.
    """
    from datetime import datetime, timezone
    requested_pack = pack_name or ""
    if target_date:
        date_key = target_date.strip()
    else:
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/today",
            "requested_pack": requested_pack,
            "date": date_key,
            "cards": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    effective_pack = requested_pack or PRIMARY_PACK_NAME
    cards: list[dict[str, Any]] = _build_m25_hybrid_cards(
        db_path,
        date_key=date_key,
        requested_pack=requested_pack,
        effective_pack=effective_pack,
    )

    # Prev/next date pivots so the operator can step through history
    # without crafting query strings.  Always populated (the dossier
    # may be empty for the target date — that is itself useful info).
    from datetime import datetime, timedelta
    try:
        anchor = datetime.strptime(date_key, "%Y-%m-%d")
        prev_date = (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (anchor + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        prev_date = ""
        next_date = ""

    def _date_path(d: str) -> str:
        if not d:
            return ""
        return _scoped_path(f"/ops/today?date={quote(d, safe='')}", pack_name=requested_pack)

    # M25.3: the M24.4 standalone lifecycle backlog strip is now
    # collapsed INTO the cards above (primary number per card).
    # We keep the ``lifecycle_summary`` payload field so the
    # renderer can detect "projection not built yet" and surface
    # an explicit reason banner — same honest-zero rule that
    # already governs every other M24/M25 surface.
    lifecycle_summary = _read_lifecycle_summary(
        vault_dir, pack=requested_pack
    )

    return {
        "screen": "ops/today",
        "requested_pack": requested_pack,
        "date": date_key,
        "prev_date": prev_date,
        "next_date": next_date,
        "prev_date_path": _date_path(prev_date),
        "next_date_path": _date_path(next_date),
        "cards": cards,
        "lifecycle_summary": lifecycle_summary,
        "available": True,
    }


def _read_lifecycle_summary(
    vault_dir: Path | str,
    *,
    pack: str,
) -> dict[str, Any]:
    """Read the five-state lifecycle distribution from ``ops_state``.

    Returns ``{"available": False, "reason": ...}`` when the
    projection table doesn't exist yet (e.g. the ``ops_state`` DAG
    step hasn't run).  Returns ``{"available": True, "counts": {…}}``
    otherwise.

    Keeping this in ``view_models`` rather than calling
    ``ops_state.counts_from_projection`` directly avoids ``view_models``
    accidentally creating the table on a vault that hasn't run the
    DAG yet — we read what's there; we don't write.
    """
    from ..ops_lifecycle import ALL_STATES

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {"available": False, "reason": "knowledge_index has not been built yet"}

    effective_pack = pack or PRIMARY_PACK_NAME
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='ops_state'"
            ).fetchone()
            if row is None:
                return {
                    "available": False,
                    "reason": "ops_state projection not built yet — run `ovp-ops-state --rebuild`",
                }
            rows = conn.execute(
                "SELECT state, COUNT(*) FROM ops_state "
                " WHERE pack = ? GROUP BY state",
                (effective_pack,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        return {"available": False, "reason": f"ops_state read failed: {exc}"}

    counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    for state, count in rows:
        if state in counts:
            counts[state] = int(count)
    return {
        "available": True,
        "pack": effective_pack,
        "counts": counts,
        "total": sum(counts.values()),
    }


# M25.2: /ops/items default page size.  Cards drill into this view
# carrying state= and optional pack=; the page paginates the rest.
ITEMS_LIST_DEFAULT_LIMIT = 50
ITEMS_LIST_MAX_LIMIT = 500


def build_items_list_payload(
    vault_dir: Path | str,
    *,
    state: str,
    pack_name: str | None = None,
    offset: int = 0,
    limit: int = ITEMS_LIST_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """M25.2: ``/ops/items?state=<state>`` payload.

    Reads ``ops_state`` (built by M24.1's ``ovp-ops-state``) and
    returns the items currently in ``state``.  This is the route
    the M25 hybrid card primary CTA targets, so card N === page N
    is a hard contract: both numbers come from the same projection
    table with the same pack filter.

    No ``date=`` filter — the primary card number is "all current
    items in this state", not date-windowed.  The plan doc locks
    this in §M25.2 / M25.3 acceptance.
    """
    from ..ops_lifecycle import ALL_STATES

    requested_pack = pack_name or ""
    state = state.strip()
    if state not in ALL_STATES:
        return {
            "screen": "ops/items",
            "available": False,
            "reason": (
                f"unknown state {state!r}; expected one of "
                f"{ALL_STATES}"
            ),
            "state": state,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
        }

    safe_limit = max(1, min(int(limit or ITEMS_LIST_DEFAULT_LIMIT), ITEMS_LIST_MAX_LIMIT))
    safe_offset = max(0, int(offset or 0))

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/items",
            "available": False,
            "reason": "knowledge_index has not been built yet",
            "state": state,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    effective_pack = requested_pack or PRIMARY_PACK_NAME
    try:
        with sqlite3.connect(db_path) as conn:
            # Guard: ops_state may not exist yet (M24.1 DAG step
            # hasn't run).  Surface explicitly rather than crash.
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='ops_state'"
            ).fetchone()
            if row is None:
                return {
                    "screen": "ops/items",
                    "available": False,
                    "reason": (
                        "ops_state projection not built yet — run "
                        "`ovp-ops-state --rebuild`"
                    ),
                    "state": state,
                    "requested_pack": requested_pack,
                    "rows": [],
                    "total": 0,
                    "offset": safe_offset,
                    "limit": safe_limit,
                }

            total_row = conn.execute(
                "SELECT COUNT(*) FROM ops_state "
                " WHERE pack = ? AND state = ?",
                (effective_pack, state),
            ).fetchone()
            total = int(total_row[0] or 0) if total_row else 0

            # M25.2: NeedsAction surfaces oldest-first so the
            # operator can attack the most-aged blockers first.
            # Every other state surfaces newest-first.
            order_dir = (
                "ASC" if state == "NeedsAction" else "DESC"
            )
            rows = conn.execute(
                f"""
                SELECT item_kind, item_id, sub_state,
                       last_evidence_at, evidence_event_types_json,
                       needs_action_reason
                  FROM ops_state
                 WHERE pack = ? AND state = ?
                 ORDER BY last_evidence_at {order_dir}
                 LIMIT ? OFFSET ?
                """,
                (effective_pack, state, safe_limit, safe_offset),
            ).fetchall()

            # M25.2 (codex review on PR #236): source-kind items
            # don't have a known canonical drilldown route yet
            # (the M25.4 ``/ops/events/audit`` view doesn't exist
            # until that PR lands).  Resolve source slugs to their
            # real file paths via ``pages_index`` so the primary
            # link points at ``/note?path=…`` — a route that
            # exists.  Sources we can't resolve fall through to an
            # unlinked cell.
            source_slugs = [
                str(r[1]) for r in rows
                if r and r[0] == "source" and r[1]
            ]
            slug_to_path: dict[str, str] = {}
            if source_slugs:
                page_row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='pages_index'"
                ).fetchone()
                if page_row is not None:
                    placeholders = ",".join("?" * len(source_slugs))
                    page_rows = conn.execute(
                        f"SELECT slug, path FROM pages_index "
                        f" WHERE slug IN ({placeholders})",
                        source_slugs,
                    ).fetchall()
                    slug_to_path = {
                        str(s): str(p) for s, p in page_rows if s and p
                    }
    except sqlite3.OperationalError as exc:
        return {
            "screen": "ops/items",
            "available": False,
            "reason": f"ops_state read failed: {exc}",
            "state": state,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    items: list[dict[str, Any]] = []
    for kind, item_id, sub_state, last_evidence_at, evt_json, na_reason in rows:
        try:
            evt_types = json.loads(evt_json) if evt_json else []
        except (TypeError, ValueError):
            evt_types = []
        # Top-3 evidence types for the row preview; rest are
        # available on the item's drilldown (out of scope for v1).
        evt_preview = list(evt_types)[:3] if isinstance(evt_types, list) else []
        kind_str = str(kind or "")
        item_id_str = str(item_id or "")
        resolved_source_path = (
            slug_to_path.get(item_id_str) if kind_str == "source" else ""
        )
        items.append({
            "item_kind": kind_str,
            "item_id": item_id_str,
            "sub_state": str(sub_state) if sub_state else "",
            "last_evidence_at": str(last_evidence_at or ""),
            "evidence_types": evt_preview,
            "needs_action_reason": str(na_reason) if na_reason else "",
            "primary_href": _items_primary_href(
                kind_str, item_id_str, effective_pack,
                source_path=resolved_source_path,
            ),
        })

    has_more = safe_offset + len(items) < total
    next_offset = safe_offset + safe_limit if has_more else None
    prev_offset = max(0, safe_offset - safe_limit) if safe_offset > 0 else None

    return {
        "screen": "ops/items",
        "available": True,
        "state": state,
        "pack": effective_pack,
        "requested_pack": requested_pack,
        "rows": items,
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "next_offset": next_offset,
        "prev_offset": prev_offset,
    }


def _items_primary_href(
    item_kind: str | None,
    item_id: str | None,
    pack: str,
    *,
    source_path: str = "",
) -> str:
    """Map (kind, id) → the canonical drilldown URL.

    * ``source``  → ``/note?path=<vault-relative-path>`` when the
      caller resolved the path via ``pages_index``; otherwise
      empty string (renderer falls back to a plain non-link cell).
      We DON'T link to the future M25.4 ``/ops/events/audit``
      route because that doesn't exist yet — clicking would 404
      (codex review on PR #236 flagged this).
    * ``object``  → ``/object?id=…``  (existing route).
    * ``cluster`` → ``/ops/cluster?id=…`` (existing route).
    """
    if not item_id:
        return ""
    kind = (item_kind or "").lower()
    pack_qs = f"&pack={quote(pack, safe='')}" if pack else ""
    if kind == "object":
        return f"/object?id={quote(str(item_id), safe='')}{pack_qs}"
    if kind == "cluster":
        return f"/ops/cluster?id={quote(str(item_id), safe='')}{pack_qs}"
    if kind == "source" and source_path:
        return f"/note?path={quote(str(source_path), safe='')}"
    # No known drilldown — return empty so the renderer surfaces
    # the item as plain text rather than a broken link.  M25.4
    # adds the raw-audit-evidence view that will pick this up.
    return ""


# M25.4: /ops/events/audit page size.  Slightly larger than the
# items list because raw audit rows are noisier; operators tend to
# scan rather than click.
EVENTS_AUDIT_DEFAULT_LIMIT = 200
EVENTS_AUDIT_MAX_LIMIT = 2000


def build_events_audit_payload(
    vault_dir: Path | str,
    *,
    event_types: tuple[str, ...] | list[str] | None = None,
    date_key: str = "",
    pack_name: str | None = None,
    limit: int = EVENTS_AUDIT_DEFAULT_LIMIT,
    state: str = "",
) -> dict[str, Any]:
    """M25.4: ``/ops/events/audit`` raw-audit-evidence view.

    The M25 cards' SECONDARY count comes from a query against the
    raw ``audit_events`` table.  ``/ops/events`` today renders
    **timeline projections** (``list_timeline_events`` over dated
    notes + contradictions) — a different ledger.  Pointing the
    card's secondary CTA at that page resurrects the M24.0
    two-ledger problem (card N != page N).

    This view fixes the contract: it reads ``audit_events``
    directly using the same SQL the card uses, so card N === page
    N by construction.  Flat table, no timeline grouping.

    Plan contract (locked in M25 §M25.4, tightened by M26 BL-102):
    * ``event_types`` is the card's event_types list — required
      so the page rows match what the card counted.
    * ``date_key`` filters to that day, bucketed by operator-local
      day via the shared ``audit_time`` parser (NOT SQLite
      ``date(timestamp)``) so it matches the card exactly.
    * Pack scoping is applied to rows (BL-102): matching payload
      pack included, different excluded, legacy pack-less rows only
      under the default pack — identical to the card.
    * ``state`` (optional) lets the page report the distinct-item
      count; inferred from ``event_types`` when omitted.  The card
      count equals ``distinct_item_count`` here by construction —
      both go through ``_fetch_activity_rows`` +
      ``_activity_item_identity``.
    """
    requested_pack = pack_name or ""
    event_types_tup = tuple(event_types or ())

    safe_limit = max(1, min(int(limit or EVENTS_AUDIT_DEFAULT_LIMIT), EVENTS_AUDIT_MAX_LIMIT))

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/events/audit",
            "available": False,
            "reason": "knowledge_index has not been built yet",
            "state": state or _state_for_event_types(event_types_tup),
            "distinct_item_count": 0,
            "event_types": list(event_types_tup),
            "date": date_key,
            "requested_pack": requested_pack,
            "rows": [],
            "total": 0,
            "limit": safe_limit,
        }

    resolved_state = state or _state_for_event_types(event_types_tup)
    effective_pack = requested_pack or PRIMARY_PACK_NAME

    audit_rows: list[dict[str, Any]] = []
    distinct_item_count = 0

    def _row(ts: str, et: str, slug: str, payload_str: str, src: str) -> dict[str, Any]:
        snippet = (
            payload_str[:117] + "…" if len(payload_str) > 120 else payload_str
        )
        return {
            "timestamp": ts,
            "event_type": et,
            "slug": slug,
            "payload_snippet": snippet,
            "payload_full": payload_str,
            "source_log": src,
        }

    if event_types_tup and date_key:
        # Card-drilldown path: identical scoping to the card
        # (operator-local day + pack) so card N === page N by
        # construction.  source_log isn't returned by the shared
        # fetch; re-read it here keyed on the same scoped rows.
        with sqlite3.connect(db_path) as conn:
            scoped = _fetch_activity_rows(
                conn, event_types_tup, date_key, effective_pack
            )
        identities: set[str] = set()
        for ts, et, slug, payload in scoped:
            if resolved_state:
                ident = _activity_item_identity(resolved_state, slug, payload)
                if ident is not None:
                    identities.add(ident)
            audit_rows.append(
                _row(
                    ts,
                    et,
                    slug,
                    json.dumps(payload, ensure_ascii=False)
                    if payload
                    else "",
                    "",
                )
            )
        distinct_item_count = len(identities)
        total = len(audit_rows)
        audit_rows.sort(key=lambda r: r["timestamp"], reverse=True)
        audit_rows = audit_rows[:safe_limit]
    else:
        # Legacy landing (no scope): N most-recent rows across all
        # event_types so the page isn't empty when the operator
        # arrives from the timeline-projection role banner.
        where: list[str] = []
        params: list[object] = []
        if event_types_tup:
            placeholders = ",".join("?" for _ in event_types_tup)
            where.append(f"event_type IN ({placeholders})")
            params.extend(event_types_tup)
        if date_key:
            where.append("date(timestamp) = ?")
            params.append(date_key)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with sqlite3.connect(db_path) as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) FROM audit_events {where_sql}",
                params,
            ).fetchone()
            total = int(total_row[0] or 0) if total_row else 0
            rows = conn.execute(
                f"""
                SELECT timestamp, event_type, slug, payload_json, source_log
                  FROM audit_events
                 {where_sql}
                 ORDER BY timestamp DESC
                 LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
        for ts, event_type, slug, payload_json, source_log in rows:
            audit_rows.append(
                _row(
                    str(ts or ""),
                    str(event_type or ""),
                    str(slug or ""),
                    str(payload_json or ""),
                    str(source_log or ""),
                )
            )

    return {
        "screen": "ops/events/audit",
        "available": True,
        "reason": "",
        "state": resolved_state,
        "distinct_item_count": distinct_item_count,
        "event_types": list(event_types_tup),
        "date": date_key,
        "requested_pack": requested_pack,
        "rows": audit_rows,
        "total": total,
        "limit": safe_limit,
    }


def build_digest_health_payload(vault_dir: Path | str) -> dict[str, Any]:
    """``/ops/digest-health`` payload — three metric panels (M23 / BL-097).

    Reads only ``audit_events``; no new schema.  Returns the data
    shape the renderer consumes — empty / "no data" states are
    explicit so the page doesn't render bogus zeros as authoritative.
    """
    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/digest-health",
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    with sqlite3.connect(db_path) as conn:
        # Skip rate
        try:
            generated = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='digest_generated'"
            ).fetchone()[0]
            skipped = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='digest_skipped_no_change'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            generated = 0
            skipped = 0
        total_attempts = generated + skipped
        skip_rate = (skipped / total_attempts) if total_attempts else None

        # Intake reflection — per generated digest, did Layer 0
        # surface intake when the day had ≥ 3 article_processed events?
        intake_rows: list[dict[str, Any]] = []
        try:
            digests = conn.execute(
                """
                SELECT payload_json, timestamp FROM audit_events
                 WHERE event_type='digest_generated'
                 ORDER BY timestamp DESC LIMIT 60
                """,
            ).fetchall()
            for payload_json, ts in digests:
                try:
                    payload = json.loads(payload_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    continue
                day_key = (payload.get("window_end") or ts or "")[:10]
                if not day_key:
                    continue
                layer0 = int(payload.get("layer0_events") or 0)
                # Same-day article_processed count from audit_events.
                article_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM audit_events
                     WHERE event_type IN (
                       'article_processed', 'source_archived_to_processed'
                     ) AND timestamp LIKE ?
                    """,
                    (day_key + "%",),
                ).fetchone()[0]
                intake_rows.append({
                    "day": day_key,
                    "layer0_events": layer0,
                    "article_count_for_day": article_count,
                    "active_day": article_count >= 3,
                    "reflected": layer0 > 0,
                })
        except sqlite3.OperationalError:
            intake_rows = []

        active_days = [r for r in intake_rows if r["active_day"]]
        reflected_active = [r for r in active_days if r["reflected"]]
        intake_reflection_rate = (
            len(reflected_active) / len(active_days) if active_days else None
        )

        # Click-through breakdown
        click_breakdown: dict[str, int] = {}
        try:
            rows = conn.execute(
                """
                SELECT payload_json FROM audit_events
                 WHERE event_type='digest_clicked_through'
                """,
            ).fetchall()
            for (payload_json,) in rows:
                try:
                    payload = json.loads(payload_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                action = str(payload.get("action") or "other") if isinstance(payload, dict) else "other"
                click_breakdown[action] = click_breakdown.get(action, 0) + 1
        except sqlite3.OperationalError:
            click_breakdown = {}

    return {
        "screen": "ops/digest-health",
        "available": True,
        "generated_count": generated,
        "skipped_count": skipped,
        "total_attempts": total_attempts,
        "skip_rate": skip_rate,
        "intake_rows": intake_rows,
        "intake_reflection_rate": intake_reflection_rate,
        "click_breakdown": click_breakdown,
    }


def build_runs_index_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Index of recent transactions for ``/ops/runs``.

    Lists ``transaction_started`` rows in reverse-chronological order
    with status (completed / failed / running), workflow type, start
    + end timestamps, and event count.  Status comes from a
    ``transaction_completed`` event for the same ``txn_id`` if any —
    rows with no matching completion are flagged as ``running`` (or
    ``stale`` if older than 6 hours and still no completion).
    """
    from datetime import datetime, timedelta, timezone
    requested_pack = pack_name or ""
    cap = max(1, limit if limit is not None else DEFAULT_RUNS_INDEX_LIMIT)

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/runs",
            "requested_pack": requested_pack,
            "runs": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    runs: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        # ``transaction_started`` carries ``txn_id`` and the workflow
        # ``type`` in its payload.  We pair each with an optional
        # matching ``transaction_completed`` row by ``txn_id``.
        started_rows = conn.execute(
            """
            SELECT json_extract(payload_json, '$.txn_id') AS txn_id,
                   json_extract(payload_json, '$.type')   AS workflow_type,
                   timestamp
              FROM audit_events
             WHERE event_type = 'transaction_started'
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (cap,),
        ).fetchall()

        if not started_rows:
            return {
                "screen": "ops/runs",
                "requested_pack": requested_pack,
                "runs": [],
                "available": True,
            }

        txn_ids = tuple(row[0] for row in started_rows if row[0])
        completed_lookup: dict[str, str] = {}
        if txn_ids:
            placeholders = ",".join("?" for _ in txn_ids)
            for tid, ts in conn.execute(
                f"""
                SELECT json_extract(payload_json, '$.txn_id') AS txn_id,
                       timestamp
                  FROM audit_events
                 WHERE event_type = 'transaction_completed'
                   AND json_extract(payload_json, '$.txn_id') IN ({placeholders})
                """,
                txn_ids,
            ).fetchall():
                if tid:
                    completed_lookup[str(tid)] = str(ts)

            # Per-txn event counts so the index page shows magnitude.
            count_lookup: dict[str, int] = {}
            for tid, n in conn.execute(
                f"""
                SELECT json_extract(payload_json, '$.txn_id') AS txn_id,
                       COUNT(*) AS n
                  FROM audit_events
                 WHERE json_extract(payload_json, '$.txn_id') IN ({placeholders})
                 GROUP BY txn_id
                """,
                txn_ids,
            ).fetchall():
                if tid:
                    count_lookup[str(tid)] = int(n)
        else:
            count_lookup = {}

    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=RUNS_STALE_AFTER_HOURS)
    for txn_id, workflow_type, started_at in started_rows:
        if not txn_id:
            continue
        completed_at = completed_lookup.get(str(txn_id), "")
        if completed_at:
            status = "completed"
        else:
            try:
                started_dt = datetime.fromisoformat(
                    str(started_at).replace("Z", "+00:00")
                )
                # PipelineLogger writes naive UTC timestamps for some
                # events (no trailing Z, no offset).  Treat them as
                # UTC so the < comparison doesn't crash.
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                status = "stale" if started_dt < stale_cutoff else "running"
            except ValueError:
                status = "running"
        runs.append({
            "txn_id": str(txn_id),
            "workflow_type": str(workflow_type or "(unknown)"),
            "started_at": str(started_at or ""),
            "completed_at": completed_at,
            "status": status,
            "event_count": count_lookup.get(str(txn_id), 0),
            "detail_href": _scoped_path(
                f"/ops/runs/{quote(str(txn_id), safe='')}",
                pack_name=requested_pack,
            ),
        })

    # Day grouping — build ``[(day, [run, ...])]`` so the renderer can
    # emit one section per calendar day in chronological order, with
    # explicit ``Idle`` markers for days that contained no runs.  The
    # operator's mental model is "what did the pipeline do this week";
    # day-grouped output makes weekend gaps and broken-cron days
    # immediately obvious.
    from datetime import timedelta as _timedelta
    runs_by_day: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        ts = str(run.get("started_at", ""))[:10]
        if not ts:
            continue
        runs_by_day.setdefault(ts, []).append(run)

    day_groups: list[dict[str, Any]] = []
    if runs_by_day:
        sorted_days = sorted(runs_by_day.keys(), reverse=True)
        try:
            newest = datetime.strptime(sorted_days[0], "%Y-%m-%d").date()
            oldest = datetime.strptime(sorted_days[-1], "%Y-%m-%d").date()
        except ValueError:
            newest = oldest = None
        if newest and oldest:
            cur = newest
            while cur >= oldest:
                key = cur.strftime("%Y-%m-%d")
                day_runs = runs_by_day.get(key, [])
                day_groups.append({
                    "date": key,
                    "runs": day_runs,
                    "count": len(day_runs),
                    "idle": not day_runs,
                })
                cur -= _timedelta(days=1)
        else:
            for key in sorted_days:
                day_groups.append({
                    "date": key,
                    "runs": runs_by_day[key],
                    "count": len(runs_by_day[key]),
                    "idle": False,
                })

    # Window summary — surface the implicit time range the limit
    # imposes so the operator knows whether the page reflects "today"
    # or "the last fortnight".
    if runs:
        oldest_ts = str(runs[-1].get("started_at", ""))
        try:
            oldest_dt = datetime.fromisoformat(oldest_ts.replace("Z", "+00:00"))
            if oldest_dt.tzinfo is None:
                oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
            window_days = max(0, (datetime.now(timezone.utc) - oldest_dt).days)
        except ValueError:
            window_days = None
    else:
        window_days = None

    return {
        "screen": "ops/runs",
        "requested_pack": requested_pack,
        "runs": runs,
        "day_groups": day_groups,
        "limit": cap,
        "window_days": window_days,
        "available": True,
    }


def build_run_detail_payload(
    vault_dir: Path | str,
    txn_id: str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Per-transaction event timeline for ``/ops/runs/<txn_id>``.

    Joins via ``session_id`` because most pipeline events don't
    carry ``txn_id`` directly — they're emitted by the same
    PipelineLogger process whose ``session_id`` is the column-
    level grouping key.  Approach:

    1. Find every ``transaction_started`` row matching ``txn_id`` —
       collect all ``session_id`` values that participated in the
       run.  Spawned subprocesses (``pinboard_process`` step etc.)
       have their own session_id but they all bracket themselves
       with ``transaction_started`` rows that share the parent
       ``txn_id`` chain — for the pipeline's current shape, the
       parent's session_id covers the whole run.
    2. SELECT every audit row whose ``session_id`` is in that set
       OR whose ``payload_json.txn_id`` matches.  This is the
       union of "events the bracketing logger wrote" + "events
       that explicitly tagged themselves with the txn".
    3. Order by timestamp, return with subject + snippet.
    """
    requested_pack = pack_name or ""
    cleaned_txn = (txn_id or "").strip()
    if not cleaned_txn:
        return {
            "screen": "ops/runs/detail",
            "requested_pack": requested_pack,
            "txn_id": "",
            "events": [],
            "available": False,
            "reason": "txn_id required",
        }

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/runs/detail",
            "requested_pack": requested_pack,
            "txn_id": cleaned_txn,
            "events": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    with sqlite3.connect(db_path) as conn:
        # Step 1: collect session_ids for this txn's bracketing rows.
        # ``$.type`` is pulled via SQLite's ``json_extract`` (proper
        # JSON parser) rather than substring-regex over the snippet —
        # the snippet was truncating long payloads and a JSON
        # formatter change could trivially break a regex match.
        bracket_rows = conn.execute(
            """
            SELECT session_id, timestamp, event_type,
                   json_extract(payload_json, '$.type') AS workflow_type
              FROM audit_events
             WHERE event_type IN ('transaction_started', 'transaction_completed')
               AND json_extract(payload_json, '$.txn_id') = ?
            """,
            (cleaned_txn,),
        ).fetchall()
        session_ids = {row[0] for row in bracket_rows if row[0]}

        if not session_ids and not bracket_rows:
            return {
                "screen": "ops/runs/detail",
                "requested_pack": requested_pack,
                "txn_id": cleaned_txn,
                "events": [],
                "available": False,
                "reason": f"no events found for txn_id {cleaned_txn}",
            }

        # Step 2: fetch every event in those sessions OR tagged
        # with the txn directly.  ``OR`` instead of ``UNION`` so
        # the SQLite optimiser can use both indexes.
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            rows = conn.execute(
                f"""
                SELECT timestamp, event_type, session_id,
                       COALESCE(json_extract(payload_json, '$.slug'),
                                json_extract(payload_json, '$.source'),
                                json_extract(payload_json, '$.file'),
                                json_extract(payload_json, '$.url'),
                                slug) AS subject,
                       substr(payload_json, 1, {TIMELINE_SNIPPET_CHARS}) AS snippet
                  FROM audit_events
                 WHERE session_id IN ({placeholders})
                    OR json_extract(payload_json, '$.txn_id') = ?
                 ORDER BY timestamp ASC
                """,
                (*session_ids, cleaned_txn),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT timestamp, event_type, session_id,
                       COALESCE(json_extract(payload_json, '$.slug'),
                                json_extract(payload_json, '$.source'),
                                json_extract(payload_json, '$.file'),
                                json_extract(payload_json, '$.url'),
                                slug) AS subject,
                       substr(payload_json, 1, {TIMELINE_SNIPPET_CHARS}) AS snippet
                  FROM audit_events
                 WHERE json_extract(payload_json, '$.txn_id') = ?
                 ORDER BY timestamp ASC
                """,
                (cleaned_txn,),
            ).fetchall()

    events = [
        {
            "timestamp": str(ts or ""),
            "event_type": str(event_type or ""),
            "session_id": str(session_id or ""),
            "subject": str(subject or ""),
            "snippet": str(snippet or ""),
        }
        for ts, event_type, session_id, subject, snippet in rows
    ]

    # Header data: pull from the bracketing rows we already fetched.
    # ``event_type`` is a column, not a payload field — keying off the
    # column lets us populate workflow_type from the payload's ``type``
    # without re-querying.
    started_at = ""
    completed_at = ""
    workflow_type = ""
    for _session_id, ts, event_type, payload_workflow_type in bracket_rows:
        if event_type == "transaction_started":
            started_at = str(ts or "")
            if payload_workflow_type:
                workflow_type = str(payload_workflow_type)
        elif event_type == "transaction_completed":
            completed_at = str(ts or "")

    return {
        "screen": "ops/runs/detail",
        "requested_pack": requested_pack,
        "txn_id": cleaned_txn,
        "workflow_type": workflow_type,
        "started_at": started_at,
        "completed_at": completed_at,
        "session_ids": sorted(session_ids),
        "events": events,
        "event_count": len(events),
        "available": True,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_event_dossier_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    event_types_filter: tuple[str, ...] = (),
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    research_shell_enabled = _supports_research_shell(pack_name)
    effective_limit = DEFAULT_EVENT_DOSSIER_LIMIT if limit is None else limit
    normalized_from = (from_date or "").strip() or None
    normalized_to = (to_date or "").strip() or None
    # M24.0 stop-gap: when an event_types filter is set, over-fetch
    # so post-filter trim still has matches.  Filtering after the
    # pre-limited fetch (CodeRabbit Major) would drop legitimately
    # matching rows that happened to sit past the first
    # ``effective_limit`` rows in the timeline.
    query_limit = effective_limit or DEFAULT_EVENT_DOSSIER_LIMIT
    if event_types_filter:
        query_limit = max(query_limit, 1000)
    events = [
        _build_timeline_event_item(row)
        for row in list_timeline_events(
            vault_dir,
            pack_name=pack_name,
            query=query,
            limit=query_limit,
            from_date=normalized_from,
            to_date=normalized_to,
        )
    ]
    if event_types_filter:
        allowed = frozenset(event_types_filter)
        events = [e for e in events if e.get("event_type") in allowed]
        # Trim back to caller's effective_limit after filtering.
        if effective_limit and effective_limit > 0:
            events = events[:effective_limit]
    provenance_map = get_object_provenance_map(
        vault_dir,
        [event["object_id"] for event in events],
        pack_name=pack_name,
    )
    scoped_object_ids = [event["object_id"] for event in events]
    review_context = get_review_context(vault_dir, scoped_object_ids, pack_name=pack_name)
    scoped_stale_summaries = list_stale_summaries(
        vault_dir,
        pack_name=pack_name,
        object_ids=scoped_object_ids,
        limit=100,
    )
    scoped_contradictions = [
        item
        for item in list_contradictions(vault_dir, pack_name=pack_name, limit=200)
        if any(claim_id.split("::", 1)[0] in set(scoped_object_ids) for claim_id in item["positive_claim_ids"] + item["negative_claim_ids"])
        and item["status"] == "open"
    ]
    for event in events:
        event["object_path"] = _scoped_path(
            f"/object?id={quote(str(event['object_id']), safe='')}",
            pack_name=requested_pack,
        )
        event["review_links"] = {
            "object_path": event["object_path"],
            "topic_path": _scoped_path(
                f"/topic?id={quote(str(event['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "contradictions_path": _scoped_path(
                f"/ops/contradictions?q={quote(str(event['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "summaries_path": _scoped_path(
                f"/ops/summaries?q={quote(str(event['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        event["provenance"] = provenance_map.get(
            event["object_id"],
            {"evergreen_path": "", "source_notes": [], "mocs": []},
        )
    dates = sorted({event["event_date"] for event in events}, reverse=True)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["event_date"], []).append(event)
    cluster_sections = [
        {
            "date": date,
            "clusters": _cluster_timeline_events(grouped[date]),
        }
        for date in dates
    ]
    event_type_counts = Counter(event["event_kind"] for event in events)
    row_type_counts = Counter(event["row_type"] for event in events)
    anchor_kind_counts = Counter(event["timeline_anchor_kind"] for event in events)
    semantic_roles = Counter(event["semantic_role"] for event in events)
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=f"{len(events)} timeline rows grouped into {sum(len(section['clusters']) for section in cluster_sections)} visible event clusters.",
            items=[
                *[
                    {
                        "kind": "event_cluster",
                        "label": item["title"],
                        "path": item["review_links"]["topic_path"],
                        "detail": f"{item['row_count']} timeline rows",
                    }
                    for section in cluster_sections[:2]
                    for item in section["clusters"][:2]
                ]
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=f"{review_context.get('open_contradiction_count', 0)} contradictions and {review_context.get('stale_summary_count', 0)} stale summaries appear in the visible event scope.",
            items=[
                {"kind": "query", "label": query or "All events", "path": "", "detail": "Current dossier filter scope."},
                {
                    "kind": "contradictions",
                    "label": "Contradiction review",
                    "path": _scoped_path(f"/ops/contradictions?q={quote(query or '', safe='')}", pack_name=requested_pack) if query else _scoped_path("/ops/contradictions", pack_name=requested_pack),
                    "detail": "Inspect tensions in the visible event scope.",
                },
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary=f"{len({note['slug'] for event in events for note in event['provenance']['source_notes']})} source notes and {len({moc['slug'] for event in events for moc in event['provenance']['mocs']})} atlas pages anchor the visible event scope.",
            items=[
                *[
                    {
                        "kind": "source_note",
                        "label": note["title"],
                        "path": _scoped_path(f"/note?path={quote(note['path'], safe='')}", pack_name=requested_pack),
                        "detail": note["note_type"],
                    }
                    for event in events[:3]
                    for note in event["provenance"]["source_notes"][:1]
                ]
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{len(scoped_contradictions)} contradictions and {len(scoped_stale_summaries)} stale summaries remain visible in this dossier.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(f"/ops/contradictions?q={quote(item['subject_key'], safe='')}", pack_name=requested_pack),
                        "detail": item["status"],
                    }
                    for item in scoped_contradictions[:3]
                ],
                *[
                    {
                        "kind": "stale_summary",
                        "label": item["title"],
                        "path": item["object_path"],
                        "detail": ", ".join(item["reason_texts"]),
                    }
                    for item in scoped_stale_summaries[:2]
                ],
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Continue from the timeline into object, contradiction, and summary review surfaces.",
            items=[
                *[
                    {
                        "kind": "topic",
                        "label": item["title"],
                        "path": item["review_links"]["topic_path"],
                        "detail": "Open topic context for this event cluster.",
                    }
                    for item in events[:3]
                ]
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect production chains behind the visible timeline scope.",
        ),
        _operator_action(
            "Contradictions",
            _scoped_path(
                f"/ops/contradictions?q={quote(query or '', safe='')}",
                pack_name=requested_pack,
            )
            if query
            else _scoped_path("/ops/contradictions", pack_name=requested_pack),
            "Review contradiction rows for the current dossier scope.",
        ),
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Open the active signal queue.",
        ),
        _operator_action(
            "Clusters" if research_shell_enabled else "Search",
            _scoped_path("/ops/clusters" if research_shell_enabled else "/search", pack_name=requested_pack),
            (
                "Explore graph clusters connected to current work."
                if research_shell_enabled
                else "Search laterally from the current shell."
            ),
        ),
    ]
    return {
        "screen": "event/dossier",
        "requested_pack": requested_pack,
        "assembly_contract": _assembly_contract("event_dossier", pack_name=pack_name),
        "events": events,
        "event_count": len(events),
        "cluster_count": sum(len(section["clusters"]) for section in cluster_sections),
        "dates": dates,
        "cluster_sections": cluster_sections,
        "event_type_counts": dict(event_type_counts),
        # M24.0 stop-gap: surface the filter so the renderer can warn
        # when an incoming ``event_types=`` filter returns 0 rows
        # because this page is a *timeline projection*, not a raw
        # audit-event browser.  The ``/ops/today`` cards count raw
        # audit_events; the timeline only contains dated note /
        # heading / contradiction projections.  Without the warning,
        # an operator clicks "See all 27 →" and sees 0 rows and
        # thinks the data is wrong — actually the data sources just
        # differ.  M25's ``/ops/items`` unifies them.
        "event_types_filter": list(event_types_filter),
        "limit": effective_limit,
        "is_limited": effective_limit is not None,
        "from_date": normalized_from or "",
        "to_date": normalized_to or "",
        "timeline_contract": {
            "timeline_kind": "dated_note_projection",
            "grouping_kind": "object_date_rollup",
            "row_type_counts": dict(row_type_counts),
            "anchor_kind_counts": dict(anchor_kind_counts),
            "semantic_roles": dict(semantic_roles),
            "event_vs_note_explanation": (
                "Event Dossier groups dated note and heading projections by object and date; "
                "it is not a canonical event entity store."
            ),
        },
        "production_summary": _build_production_summary(
            vault_dir,
            scoped_object_ids,
            pack_name=pack_name,
        ),
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=scoped_object_ids, limit=8),
        "scoped_object_ids": list(dict.fromkeys(scoped_object_ids)),
        "scoped_stale_summary_ids": [item["object_id"] for item in scoped_stale_summaries],
        "scoped_open_contradiction_ids": [item["contradiction_id"] for item in scoped_contradictions],
        "model_notes": [
            "Event Dossier is a timeline over dated notes projected from indexed pages, not a separate event entity system.",
            "page_date rows come from note-level dates; heading_date rows come from dated section headings.",
        ],
        "operator_rail": operator_rail,
        "query": query or "",
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }


def build_evolution_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    status: str = "all",
    link_type: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    evolution = _build_evolution_section(
        vault_dir,
        pack_name=pack_name,
        query=query,
        link_type=link_type,
        status=status,
    )
    type_counts = Counter(
        item["link_type"]
        for item in [
            *evolution["candidate_items"],
            *evolution["accepted_links"],
            *evolution["rejected_links"],
        ]
    )
    return {
        "screen": "evolution/browser",
        "requested_pack": requested_pack,
        "query": query or "",
        "status": status,
        "link_type": link_type or "",
        "items": evolution["candidate_items"],
        "candidate_items": evolution["candidate_items"],
        "accepted_links": evolution["accepted_links"],
        "rejected_links": evolution["rejected_links"],
        "candidate_count": evolution["candidate_count"],
        "accepted_count": evolution["accepted_count"],
        "rejected_count": evolution["rejected_count"],
        "count": evolution["candidate_count"] + evolution["accepted_count"] + evolution["rejected_count"],
        "type_counts": dict(type_counts),
        "link_types": evolution["link_types"],
    }


_CLUSTER_BROWSER_PAGE_SIZES = (15, 50, 200)


def build_cluster_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_TRACEABILITY_BROWSER_LIMIT,
    show_all: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    # ``show_all`` lifts the display cap so the operator can audit the
    # full cluster set when they need to.  We still cap at MAX_PAGE_SIZE
    # to protect renderer cost; a vault with 10k+ clusters would still
    # be unworkable, so warn at the call site rather than render forever.
    #
    # ``show_all`` and ``offset`` are mutually exclusive — Show all
    # always starts from cluster #0; an explicit offset only paginates
    # within the per-page limit.
    effective_limit = MAX_PAGE_SIZE if show_all else limit
    effective_offset = 0 if show_all else max(0, int(offset or 0))
    total_count = count_graph_clusters(vault_dir, pack_name=pack_name, query=query)
    items = list_graph_clusters(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=effective_limit,
        offset=effective_offset,
    )
    cluster_provenance_index = _build_cluster_provenance_index(vault_dir, items)
    cluster_kind_counts = Counter(item["cluster_kind"] for item in items)
    largest_cluster_size = max((int(item["member_count"]) for item in items), default=0)
    enriched_items = []
    for item in items:
        requested_pack = pack_name or str(item["pack"])
        summary = build_cluster_summary_payload(
            vault_dir,
            cluster_id=str(item["cluster_id"]),
            pack_name=requested_pack,
            cluster_rows=items,
            cluster_provenance_index=cluster_provenance_index,
        )
        review_context = summary["review_context"]
        dominant_edge_kind = next(
            iter(
                sorted(
                    summary["edge_kind_counts"].items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ),
            None,
        )
        priority_score = (
            review_context["open_contradiction_count"] * 100
            + review_context["stale_summary_count"] * 40
            + int(item["member_count"]) * 10
            + int(summary["edge_count"]) * 3
            + review_context["source_note_count"]
            + review_context["moc_count"]
        )
        if summary["reading_routes"]:
            priority_score += 15
        if review_context["open_contradiction_count"] > 0 or review_context["stale_summary_count"] > 0:
            priority_band = "attention"
            priority_reason = (
                f"{review_context['open_contradiction_count']} open contradictions, "
                f"{review_context['stale_summary_count']} stale summaries"
            )
        elif dominant_edge_kind is not None:
            priority_band = "active"
            priority_reason = f"dominant edge kind {dominant_edge_kind[0]} ({dominant_edge_kind[1]})"
        else:
            priority_band = "reference"
            priority_reason = f"{review_context['source_note_count']} source notes in scope"
        strongest_related = summary["related_clusters"][0] if summary["related_clusters"] else None
        top_reading_route = summary["reading_routes"][0] if summary["reading_routes"] else None
        enriched_items.append(
            {
                **item,
                "row_pack": str(item.get("row_pack") or item["pack"]),
                "pack": requested_pack,
                "detail_path": summary["cluster"]["detail_path"],
                "center_object_path": summary["cluster"]["center_object_path"],
                "member_links": summary["cluster"]["member_links"],
                "display_title": summary["display_title"],
                "relation_pattern_preview": summary["relation_pattern_preview"],
                "related_cluster_count": len(summary["related_clusters"]),
                "related_cluster_preview": ", ".join(
                    related["display_title"] for related in summary["related_clusters"][:2]
                ),
                "neighborhood_score": strongest_related["score"] if strongest_related else 0,
                "neighborhood_reason": strongest_related["reason"] if strongest_related else "",
                "neighborhood_band": strongest_related["bridge_band"] if strongest_related else "",
                "neighborhood_bridge_kind": strongest_related["bridge_kind"] if strongest_related else "",
                "next_read_title": strongest_related["display_title"] if strongest_related else "",
                "next_read_path": strongest_related["detail_path"] if strongest_related else "",
                "next_read_reason": strongest_related["reason"] if strongest_related else "",
                "top_reading_route_kind": top_reading_route["route_kind"] if top_reading_route else "",
                "top_reading_route_title": top_reading_route["display_title"] if top_reading_route else "",
                "top_reading_route_reason": top_reading_route["route_reason"] if top_reading_route else "",
                "has_reading_route": bool(top_reading_route),
                "reading_intent_count": len(summary["reading_routes"]),
                "reading_intent_preview": ", ".join(
                    route["display_name"] for route in summary["reading_routes"]
                ),
                "summary_bullets": summary["summary_bullets"],
                "structural_label": summary["structural_label"],
                "edge_kind_counts": summary["edge_kind_counts"],
                "edge_summary_items": summary["edge_summary_items"],
                "edge_count": summary["edge_count"],
                "relation_pattern_items": summary["relation_pattern_items"],
                "review_context": summary["review_context"],
                "open_contradictions": summary["open_contradictions"],
                "stale_summaries": summary["stale_summaries"],
                "related_clusters": summary["related_clusters"],
                "related_cluster_groups": summary["related_cluster_groups"],
                "reading_routes": summary["reading_routes"],
                "next_read_cluster": summary["next_read_cluster"],
                "top_source_notes": summary["top_source_notes"],
                "top_mocs": summary["top_mocs"],
                "object_kind_counts": summary["object_kind_counts"],
                "priority_score": priority_score,
                "priority_band": priority_band,
                "priority_reason": priority_reason,
                "top_summary_bullet": summary["summary_bullets"][0] if summary["summary_bullets"] else "",
                "dominant_edge_kind": dominant_edge_kind[0] if dominant_edge_kind is not None else "",
            }
        )
    enriched_items.sort(
        key=lambda item: (
            -int(item["priority_score"]),
            str(item["label"]).lower(),
            str(item["cluster_id"]),
        )
    )
    return {
        "screen": "graph/clusters",
        "requested_pack": pack_name or "",
        "projection_label": _access_projection_label(
            surface="graph_clusters",
            pack_name=pack_name,
            generated_by="build_cluster_browser_payload",
            derived_from=("knowledge.db.graph_clusters", "knowledge.db.graph_edges"),
        ),
        "query": query or "",
        "limit": effective_limit,
        "default_limit": limit,
        "offset": effective_offset,
        "show_all": bool(show_all),
        "total_count": total_count,
        # Compute truncation from actual counts so show_all=True
        # doesn't silently report "complete" while still capped at
        # MAX_PAGE_SIZE on a vault with > MAX_PAGE_SIZE clusters.
        "is_limited": total_count > len(enriched_items),
        "items": enriched_items,
        "count": len(enriched_items),
        "cluster_kind_counts": dict(cluster_kind_counts),
        "largest_cluster_size": largest_cluster_size,
        "model_notes": [
            "Graph clusters currently come from pack-owned graph seed projections, not from a final semantic clustering model.",
            "Current research-tech clusters are relation/contradiction connected components over pack-scoped truth rows.",
        ],
    }


def _clamp_graph_coordinate(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def build_graph_map_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_GRAPH_MAP_LIMIT,
    show_all: bool = False,
    member_cap: int = DEFAULT_GRAPH_MAP_MEMBER_CAP,
) -> dict[str, Any]:
    cluster_payload = build_cluster_browser_payload(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=limit,
    )
    clusters = cluster_payload["items"]
    # BL-051: cap each cluster's members in the visual map (full
    # list still reachable via ``/ops/clusters`` and ``/ops/cluster``).
    # ``show_all`` lifts the cap.
    if not show_all and member_cap > 0:
        for cluster in clusters:
            members = cluster.get("members") or []
            if len(members) > member_cap:
                cluster["members"] = members[:member_cap]
                cluster["truncated_member_count"] = len(members) - member_cap
    requested_pack = pack_name or cluster_payload.get("requested_pack", "")
    center_x = GRAPH_MAP_WIDTH / 2
    center_y = GRAPH_MAP_HEIGHT / 2
    cluster_orbit_x = GRAPH_MAP_WIDTH * GRAPH_MAP_CLUSTER_ORBIT_X_FACTOR
    cluster_orbit_y = GRAPH_MAP_HEIGHT * GRAPH_MAP_CLUSTER_ORBIT_Y_FACTOR
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    all_member_ids = sorted(
        {
            str(member["object_id"])
            for cluster in clusters
            for member in cluster.get("members", [])
            if member.get("object_id")
        }
    )
    cluster_packs = sorted(
        {
            str(cluster.get("row_pack") or cluster.get("pack") or requested_pack)
            for cluster in clusters
            if cluster.get("row_pack") or cluster.get("pack") or requested_pack
        }
    )
    scoped_edges = list_graph_edges_for_object_scope(
        vault_dir,
        object_ids=all_member_ids,
        pack_names=cluster_packs,
        pack_name=pack_name,
    )
    edges_by_pack: dict[str, list[dict[str, Any]]] = {}
    for edge in scoped_edges:
        edges_by_pack.setdefault(str(edge.get("pack") or ""), []).append(edge)

    for cluster_index, cluster in enumerate(clusters):
        display_pack = str(cluster.get("pack") or requested_pack)
        cluster_count = max(1, len(clusters))
        cluster_angle = (2 * math.pi * cluster_index / cluster_count) if cluster_count > 1 else 0
        cluster_x = center_x + (math.cos(cluster_angle) * cluster_orbit_x if cluster_count > 1 else 0)
        cluster_y = center_y + (math.sin(cluster_angle) * cluster_orbit_y if cluster_count > 1 else 0)
        members = cluster.get("members", [])
        member_count = max(1, len(members))
        local_radius = min(
            GRAPH_MAP_LOCAL_RADIUS_MAX,
            GRAPH_MAP_LOCAL_RADIUS_BASE + member_count * GRAPH_MAP_LOCAL_RADIUS_PER_MEMBER,
        )
        for member_index, member in enumerate(members):
            object_id = str(member["object_id"])
            member_angle = (2 * math.pi * member_index / member_count) - (math.pi / 2)
            x = _clamp_graph_coordinate(
                cluster_x + math.cos(member_angle) * local_radius,
                GRAPH_MAP_MARGIN,
                GRAPH_MAP_WIDTH - GRAPH_MAP_MARGIN,
            )
            y = _clamp_graph_coordinate(
                cluster_y + math.sin(member_angle) * local_radius,
                GRAPH_MAP_MARGIN,
                GRAPH_MAP_HEIGHT - GRAPH_MAP_MARGIN,
            )
            node = nodes.setdefault(
                object_id,
                {
                    "object_id": object_id,
                    "title": str(member.get("title") or object_id),
                    "object_kind": str(member.get("object_kind") or "object"),
                    "kind_label": _object_kind_label(str(member.get("object_kind") or "")),
                    "path": _scoped_path(
                        f"/object?id={quote(object_id, safe='')}",
                        pack_name=display_pack,
                    ),
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "degree": 0,
                    "cluster_ids": [],
                    "cluster_titles": [],
                },
            )
            if cluster["cluster_id"] not in node["cluster_ids"]:
                node["cluster_ids"].append(cluster["cluster_id"])
                node["cluster_titles"].append(cluster.get("display_title") or cluster["label"])

    # Edge collection runs once after every cluster has populated
    # ``nodes`` so cross-community edges (source in cluster A, target
    # in cluster B) survive — the previous per-cluster filter dropped
    # any edge whose endpoints didn't both sit inside the same
    # cluster's member list, leaving the atlas almost edge-less.
    for edges_list in edges_by_pack.values():
        for edge in edges_list:
            source_id = str(edge["source_object_id"])
            target_id = str(edge["target_object_id"])
            if source_id not in nodes or target_id not in nodes:
                continue
            key = (source_id, target_id, str(edge["edge_kind"]))
            edge_weight = float(edge.get("weight") or 0.0)
            if key in edges:
                edges[key]["weight"] += edge_weight
            else:
                edges[key] = {
                    "source_object_id": source_id,
                    "target_object_id": target_id,
                    "edge_kind": str(edge["edge_kind"]),
                    "weight": edge_weight,
                    "source_title": nodes[source_id]["title"],
                    "target_title": nodes[target_id]["title"],
                }

    for edge in edges.values():
        nodes[edge["source_object_id"]]["degree"] += 1
        nodes[edge["target_object_id"]]["degree"] += 1

    node_items = sorted(nodes.values(), key=lambda item: (-int(item["degree"]), str(item["title"]).lower()))
    for node in node_items:
        node["radius"] = GRAPH_MAP_NODE_BASE_RADIUS + min(
            GRAPH_MAP_NODE_RADIUS_BONUS_MAX,
            int(node["degree"]) * GRAPH_MAP_NODE_RADIUS_PER_DEGREE,
        )
    edge_items = sorted(
        edges.values(),
        key=lambda item: (
            str(item["source_title"]).lower(),
            str(item["target_title"]).lower(),
            str(item["edge_kind"]),
        ),
    )

    # AtlasGraph kit-shape projection — the dark 3D view at /map
    # consumes ``atlas`` directly via ``window.OVP_GRAPH``. The
    # existing ``nodes``/``edges``/``clusters`` keys above are kept
    # for backward-compat with payload consumers (tests, /api, the
    # 2D inspector still on /ops/cluster).  See
    # docs/design-system/ui_kits/ovp/graph-data.js for the
    # canonical kit shape this mirrors.
    backlinks_in: dict[str, int] = {}
    for edge in edge_items:
        target = str(edge["target_object_id"])
        backlinks_in[target] = backlinks_in.get(target, 0) + 1
    atlas_communities = [
        {
            "id": str(cluster["cluster_id"]),
            "name": str(cluster.get("display_title") or cluster["label"]),
            "slug": _atlas_community_slug(
                str(cluster.get("display_title") or cluster["label"])
            ),
            # The kit reads the trailing digit off ``var(--c-N)`` and
            # uses the runtime-computed token, so the swatch follows
            # the active theme.  Cycle through 1..8 by index.
            "color": f"var(--c-{(idx % 8) + 1})",
            "count": int(cluster.get("member_count") or 0),
        }
        for idx, cluster in enumerate(clusters)
    ]
    # ``absorbedAt`` is required by the kit's timeline scrubber.
    # We don't yet surface a per-object absorbed-at on the cluster
    # member dict; for v1 every node carries today's date so the
    # timeline degenerates to a single bucket and the "Play history"
    # affordance is harmless.  Stage 4 will surface real timestamps.
    # Use UTC so day boundaries match the rest of this module — local
    # timezone would shift fallback nodes between timeline buckets
    # around UTC midnight.
    absorbed_default = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    atlas_nodes = [
        {
            "id": str(node["object_id"]),
            "label": str(node["title"]),
            "type": _ATLAS_TYPE_BY_OBJECT_KIND.get(
                str(node.get("object_kind") or ""),
                "evergreen",
            ),
            "community": (
                str(node["cluster_ids"][0])
                if node.get("cluster_ids")
                else ""
            ),
            "quality": None,
            "backlinks": int(backlinks_in.get(str(node["object_id"]), 0)),
            "openQuestion": str(node.get("object_kind") or "")
                == "contradiction_crystal",
            "source": "manual",
            "absorbedAt": absorbed_default,
            "path": str(node.get("path") or ""),
        }
        for node in node_items
    ]
    atlas_links = [
        {
            "source": str(edge["source_object_id"]),
            "target": str(edge["target_object_id"]),
            "kind": _ATLAS_LINK_KINDS.get(
                str(edge["edge_kind"]).lower(),
                "ref",
            ),
        }
        for edge in edge_items
    ]
    atlas_payload = {
        "communities": atlas_communities,
        "nodes": atlas_nodes,
        "links": atlas_links,
    }
    return {
        "screen": "graph/map",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="graph_map",
            pack_name=pack_name,
            generated_by="build_graph_map_payload",
            derived_from=("knowledge.db.graph_clusters", "knowledge.db.graph_edges"),
        ),
        "query": query or "",
        "limit": limit,
        # The graph map intentionally caps how many neighborhoods it
        # paints; treat that cap as "limited" even when the underlying
        # cluster set is small — the banner explains the display intent
        # rather than reporting on pagination.
        "is_limited": True,
        "layout": {"width": GRAPH_MAP_WIDTH, "height": GRAPH_MAP_HEIGHT},
        "nodes": node_items,
        "edges": edge_items,
        "clusters": [
            {
                "cluster_id": str(cluster["cluster_id"]),
                "title": str(cluster.get("display_title") or cluster["label"]),
                "detail_path": str(cluster["detail_path"]),
                "member_count": int(cluster["member_count"]),
                "priority_band": str(cluster["priority_band"]),
                "summary": str(cluster.get("top_summary_bullet") or cluster["priority_reason"]),
            }
            for cluster in clusters
        ],
        "map_summary": {
            "node_count": len(node_items),
            "edge_count": len(edge_items),
            "cluster_count": len(clusters),
            "largest_cluster_size": cluster_payload["largest_cluster_size"],
            # The graph map intentionally caps how many neighborhoods it
        # paints; treat that cap as "limited" even when the underlying
        # cluster set is small — the banner explains the display intent
        # rather than reporting on pagination.
        "is_limited": True,
            "limit": limit,
            # BL-051 visibility caps — surface to the renderer so the
            # page can show the right banner + ``Show all`` toggle.
            "show_all": show_all,
            "member_cap": member_cap if not show_all else 0,
            "truncated_clusters": sum(
                1 for c in clusters if c.get("truncated_member_count")
            ),
        },
        "model_notes": [
            "This spatial map is a reader projection over graph clusters and edges.",
            "Use it to see nearby ideas first; use the cluster browser for analytical/debug detail.",
        ],
        "atlas": atlas_payload,
    }


def build_cluster_detail_payload(
    vault_dir: Path | str,
    *,
    cluster_id: str,
    pack_name: str | None = None,
    cluster_rows: list[dict[str, Any]] | None = None,
    cluster_provenance_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    detail = get_graph_cluster_detail(vault_dir, cluster_id, pack_name=pack_name)
    cluster = detail["cluster"]
    requested_pack = pack_name or str(cluster["pack"])
    member_index = {str(member["object_id"]): member for member in cluster["members"]}
    detail_path = (
        f"/ops/cluster?id={quote(str(cluster['cluster_id']), safe='')}"
        f"&pack={quote(requested_pack, safe='')}"
    )
    enriched_cluster = {
        **cluster,
        "detail_path": detail_path,
        "center_object_path": _scoped_path(
            f"/object?id={quote(str(cluster['center_object_id']), safe='')}",
            pack_name=requested_pack,
        ),
        "member_links": [
            {
                **member,
                "path": _scoped_path(
                    f"/object?id={quote(str(member['object_id']), safe='')}",
                    pack_name=requested_pack,
                ),
            }
            for member in cluster["members"]
        ],
    }
    enriched_edges = [
        {
            **edge,
            "source_title": member_index.get(str(edge["source_object_id"]), {}).get(
                "title",
                str(edge["source_object_id"]),
            ),
            "target_title": member_index.get(str(edge["target_object_id"]), {}).get(
                "title",
                str(edge["target_object_id"]),
            ),
            "source_path": _scoped_path(
                f"/object?id={quote(str(edge['source_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "target_path": _scoped_path(
                f"/object?id={quote(str(edge['target_object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for edge in detail["edges"]
    ]
    sections = _build_cluster_surface_sections(
        vault_dir,
        cluster=enriched_cluster,
        edges=enriched_edges,
        requested_pack=requested_pack,
        cluster_rows=cluster_rows,
        cluster_provenance_index=cluster_provenance_index,
    )

    return {
        "screen": "graph/cluster-detail",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="graph_cluster_detail",
            pack_name=pack_name,
            generated_by="build_cluster_detail_payload",
            derived_from=("knowledge.db.graph_clusters", "knowledge.db.graph_edges"),
        ),
        "cluster": enriched_cluster,
        "browser_path": f"/ops/clusters?pack={quote(requested_pack, safe='')}",
        "edges": enriched_edges,
        **sections,
        "model_notes": [
            "Cluster detail currently reflects pack-owned graph seed structure, not a final semantic subgraph model.",
            "Edges are filtered to the cluster's own member set inside the requested pack projection.",
        ],
    }


def build_contradiction_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    raw_items = list_contradictions(vault_dir, pack_name=pack_name, status=status, query=query)
    provenance_map = get_object_provenance_map(
        vault_dir,
        _object_ids_from_claim_ids(
            *(
                item["positive_claim_ids"] + item["negative_claim_ids"]
                for item in raw_items
            )
        ),
        pack_name=pack_name,
    )
    items = []
    for item in raw_items:
        object_ids = _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])
        source_notes: dict[str, dict[str, Any]] = {}
        mocs: dict[str, dict[str, Any]] = {}
        object_titles: dict[str, str] = {}
        for object_id in object_ids:
            provenance = provenance_map.get(
                object_id,
                {"title": object_id, "evergreen_path": "", "source_notes": [], "mocs": []},
            )
            object_titles[object_id] = provenance["title"]
            for note in provenance["source_notes"]:
                source_notes.setdefault(note["slug"], note)
            for moc in provenance["mocs"]:
                mocs.setdefault(moc["slug"], moc)
        items.append(
            {
                **item,
                "object_ids": object_ids,
                "object_titles": object_titles,
                "object_links": [
                    {
                        "object_id": object_id,
                        "path": _scoped_path(
                            f"/object?id={quote(object_id, safe='')}",
                            pack_name=requested_pack,
                        ),
                    }
                    for object_id in object_ids
                ],
                "provenance": {
                    "source_notes": list(source_notes.values()),
                    "mocs": list(mocs.values()),
                },
            }
    )
    status_counts = Counter(item["status"] for item in items)
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=f"{len(items)} contradiction rows are currently visible, with {status_counts.get('open', 0)} still open.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(f"/ops/contradictions?q={quote(item['subject_key'], safe='')}", pack_name=requested_pack),
                        "detail": item["status"],
                    }
                    for item in items[:4]
                ]
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=f"{len({object_id for item in items for object_id in item['object_ids']})} objects and {len({note['slug'] for item in items for note in item['provenance']['source_notes']})} source notes are affected by the visible contradiction scope.",
            items=[
                {"kind": "filter", "label": status or "all", "path": "", "detail": "Current contradiction filter."},
                {"kind": "query", "label": query or "all", "path": "", "detail": "Current query scope."},
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary="Contradictions are anchored by ranked evidence and provenance across source notes and atlas pages.",
            items=[
                *[
                    {
                        "kind": "source_note",
                        "label": note["title"],
                        "path": _scoped_path(f"/note?path={quote(note['path'], safe='')}", pack_name=requested_pack),
                        "detail": note["note_type"],
                    }
                    for item in items[:3]
                    for note in item["provenance"]["source_notes"][:1]
                ]
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{status_counts.get('open', 0)} open rows still require review or dismissal.",
            items=[
                *[
                    {
                        "kind": "open_contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(f"/ops/contradictions?q={quote(item['subject_key'], safe='')}", pack_name=requested_pack),
                        "detail": item["status_explanation"],
                    }
                    for item in items[:4]
                    if item["status"] == "open"
                ]
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Route from contradiction review into object pages and downstream maintenance.",
            items=[
                *[
                    {
                        "kind": "object",
                        "label": item["object_titles"].get(link["object_id"], link["object_id"]),
                        "path": link["path"],
                        "detail": "Open affected object page.",
                    }
                    for item in items[:2]
                    for link in item["object_links"][:2]
                ]
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Open active signals for related maintenance entry points.",
        ),
        _operator_action(
            "Action Queue",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Inspect queued or failed execution work.",
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Trace production gaps behind the visible contradictions.",
        ),
        _operator_action(
            "Events",
            _scoped_path("/ops/events", pack_name=requested_pack),
            "Compare contradiction scope against the timeline surface.",
        ),
    ]
    return {
        "screen": "truth/contradictions",
        "requested_pack": requested_pack,
        "assembly_contract": _assembly_contract("contradiction_view", pack_name=pack_name),
        "items": items,
        "count": len(items),
        "open_count": status_counts.get("open", 0),
        "resolved_count": sum(count for status, count in status_counts.items() if status != "open"),
        "scope_summary": {
            "item_count": len(items),
            "object_count": len({object_id for item in items for object_id in item["object_ids"]}),
            "source_note_count": len(
                {
                    note["slug"]
                    for item in items
                    for note in item["provenance"]["source_notes"]
                }
            ),
        },
        "detection_contract": {
            "model": "page_summary_polarity",
            "confidence": "heuristic",
            "polarity_semantics": "Positive and negative claim sets are compared within the same contradiction subject scope.",
            "evidence_semantics": "Ranked evidence is assembled from claim_evidence rows attached to both polarity sides.",
            "status_buckets": {
                "open": status_counts.get("open", 0),
                "reviewed": sum(count for row_status, count in status_counts.items() if row_status != "open"),
            },
            "status_explanations": CONTRADICTION_STATUS_EXPLANATIONS,
        },
        "detection_notes": [
            "Contradictions are currently detected from page_summary claim polarity, not from full semantic contradiction analysis.",
            "Zero results do not prove consistency; they usually mean the current heuristic did not detect a conflict.",
            CONTRADICTION_HEURISTIC_NOTE,
        ],
        "empty_state": "Zero results usually means the current heuristic did not detect a conflict, not that the vault is globally contradiction-free.",
        "operator_rail": operator_rail,
        "status": status or "",
        "query": query or "",
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }


def _build_timeline_event_item(row: tuple[Any, ...] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        payload = json.loads(str(row.get("payload_json") or "{}"))
        event_date = str(row.get("event_date") or "")
        event_type = str(row.get("event_type") or "")
        heading = str(row.get("heading") or "").strip()
        object_id = str(row.get("object_id") or "")
        title = str(row.get("title") or object_id)
        summary_text = str(row.get("summary_text") or "")
        row_pack = str(row.get("row_pack") or "")
    else:
        payload = json.loads(row[3] or "{}")
        event_date = str(row[0] or "")
        event_type = str(row[1])
        heading = str(row[2] or "").strip()
        object_id = str(row[4])
        title = str(row[5])
        summary_text = str(row[6] or "")
        row_pack = ""
    if event_type == "page_date":
        timeline_anchor_kind = "note"
        timeline_anchor_label = str(payload.get("title") or title)
        semantic_role = "note_date_projection"
        event_kind = "dated_note"
        event_label = "Dated Note"
    else:
        timeline_anchor_kind = "heading"
        timeline_anchor_label = heading or str(payload.get("title") or title)
        semantic_role = "heading_date_projection"
        event_kind = "dated_heading"
        event_label = "Dated Heading"
    return {
        "event_date": event_date,
        "event_type": event_type,
        "row_type": event_type,
        "event_kind": event_kind,
        "event_label": event_label,
        "semantic_role": semantic_role,
        "timeline_anchor_kind": timeline_anchor_kind,
        "timeline_anchor_label": timeline_anchor_label,
        "object_id": object_id,
        "title": title,
        "summary_text": summary_text,
        "row_pack": row_pack,
    }


def _cluster_timeline_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (str(event["event_date"]), str(event["object_id"]))
        cluster = clusters.setdefault(
            key,
            {
                "event_date": event["event_date"],
                "object_id": event["object_id"],
                "title": event["title"],
                "object_path": event["object_path"],
                "summary_text": event["summary_text"],
                "review_links": event["review_links"],
                "provenance": event["provenance"],
                "row_count": 0,
                "row_types": [],
                "event_labels": [],
                "semantic_roles": [],
                "timeline_anchor_labels": [],
                "grouping_kind": "object_date_rollup",
                "event_vs_note_explanation": (
                    "This cluster groups timeline rows for the same object and date; "
                    "it is a dossier rollup, not a canonical event entity."
                ),
            },
        )
        cluster["row_count"] += 1
        for field, value in (
            ("row_types", event["row_type"]),
            ("event_labels", event["event_label"]),
            ("semantic_roles", event["semantic_role"]),
            ("timeline_anchor_labels", event["timeline_anchor_label"]),
        ):
            if value not in cluster[field]:
                cluster[field].append(value)
    for cluster in clusters.values():
        cluster["row_types"] = sorted(cluster["row_types"])
        cluster["semantic_roles"] = sorted(cluster["semantic_roles"])
    return sorted(clusters.values(), key=lambda item: (str(item["event_date"]), str(item["object_id"])))


def build_truth_dashboard_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    runtime = get_runtime_status(vault_dir)
    operational_runtime_state = get_operational_runtime_state(vault_dir)
    objects = build_objects_index_payload(vault_dir, limit=12, offset=0, pack_name=pack_name)
    signals = build_signal_browser_payload(vault_dir, pack_name=pack_name)
    production = build_production_browser_payload(vault_dir, pack_name=pack_name)
    production_weak_points = production["weak_points"]
    research_overview_supported = _supports_research_shell(pack_name)
    if research_overview_supported:
        contradictions = build_contradiction_browser_payload(vault_dir, pack_name=pack_name)
        events = build_event_dossier_payload(vault_dir, pack_name=pack_name, limit=8)
        stale_summaries = build_stale_summary_browser_payload(vault_dir, pack_name=pack_name)
        evolution = build_evolution_browser_payload(vault_dir, pack_name=pack_name, status="all")
    else:
        contradictions = {
            "count": 0,
            "open_count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/contradictions", pack_name=requested_pack),
        }
        events = {
            "count": 0,
            "items": [],
            "dates": [],
            "browser_path": _scoped_path("/ops/events", pack_name=requested_pack),
        }
        stale_summaries = {
            "count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/summaries", pack_name=requested_pack),
        }
        evolution = {
            "candidate_count": 0,
            "accepted_count": 0,
            "items": [],
        }
    priorities: list[dict[str, Any]] = []
    if research_overview_supported:
        for item in contradictions["items"][:4]:
            priorities.append(
                {
                    "kind": "contradiction",
                    "label": item["subject_key"],
                    "path": _scoped_path(
                        f"/ops/contradictions?q={quote(str(item['subject_key']), safe='')}",
                        pack_name=requested_pack,
                    ),
                    "detail": f"{len(item['object_ids'])} objects in scope",
                }
            )
        for item in stale_summaries["items"][:4]:
            priorities.append(
                {
                    "kind": "stale_summary",
                    "label": item["title"],
                    "path": item["object_path"],
                    "detail": ", ".join(item["reason_codes"]),
                }
            )
    else:
        for item in signals["items"][:4]:
            priorities.append(
                {
                    "kind": item["signal_type"],
                    "label": item["title"],
                    "path": item["source_path"],
                    "detail": item["detail"],
                }
            )
    for item in production_weak_points[:4]:
        priorities.append(
            {
                "kind": "production_gap",
                "label": item["title"],
                "path": _scoped_path(
                    f"/note?path={quote(item['note_path'], safe='')}",
                    pack_name=requested_pack,
                ),
                "detail": item["detail"],
            }
        )
    orientation = build_briefing_payload(vault_dir, pack_name=pack_name)
    entry_sections = [
        _compiled_section(
            "what_changed_recently",
            "What Changed Recently",
            summary=f"{orientation.get('changed_object_count', 0)} changed objects and {orientation.get('recent_signal_count', 0)} recent signals surfaced.",
            items=[
                *[
                    {
                        "kind": "changed_object",
                        "label": item["title"],
                        "path": item["path"],
                        "detail": f"Changed object · {item['object_id']}",
                    }
                    for item in orientation.get("changed_objects", [])[:4]
                ]
            ],
        ),
        _compiled_section(
            "important_right_now",
            "Important Right Now",
            summary=f"{len(orientation.get('priority_items', []))} priority items are currently surfaced.",
            items=[
                *[
                    {
                        "kind": str(item["kind"]),
                        "label": str(item["title"]),
                        "path": str(item["path"]),
                        "detail": str(item["detail"]),
                    }
                    for item in orientation.get("priority_items", [])[:4]
                ]
            ],
        ),
        _compiled_section(
            "deserves_review",
            "Deserves Review",
            summary=f"{contradictions['open_count'] if research_overview_supported else signals['count']} review-oriented items are currently in scope.",
            items=(
                [
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(
                            f"/ops/contradictions?q={quote(str(item['subject_key']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"{len(item['object_ids'])} objects in scope",
                    }
                    for item in contradictions["items"][:4]
                ]
                if research_overview_supported
                else [
                    {
                        "kind": str(item["signal_type"]),
                        "label": str(item["title"]),
                        "path": str(item["source_path"]),
                        "detail": str(item["detail"]),
                    }
                    for item in signals["items"][:4]
                ]
            ),
        ),
        _compiled_section(
            "recommended_next_steps",
            "Recommended Next Steps",
            summary="Start with the orientation brief, then move into the highest-signal compiled surfaces.",
            items=[
                {
                    "kind": "orientation",
                    "label": "Orientation Brief",
                    "path": _scoped_path("/ops/briefing", pack_name=requested_pack),
                    "detail": "Open the current knowledge entry product.",
                },
                {
                    "kind": "signals",
                    "label": "Signals",
                    "path": _scoped_path("/ops/signals", pack_name=requested_pack),
                    "detail": "Review current active signals.",
                },
                {
                    "kind": "production",
                    "label": "Production",
                    "path": _scoped_path("/ops/production", pack_name=requested_pack),
                    "detail": "Inspect production weak points.",
                },
                *(
                    [
                        {
                            "kind": "graph",
                            "label": "Clusters",
                            "path": _scoped_path("/ops/clusters", pack_name=requested_pack),
                            "detail": "Explore graph clusters.",
                        }
                    ]
                    if research_overview_supported
                    else []
                ),
            ],
        ),
    ]
    workflow_groups = _build_dashboard_workflow_groups(
        requested_pack=requested_pack,
        research_overview_supported=research_overview_supported,
    )
    return {
        "screen": "truth/dashboard",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="ops_dashboard",
            pack_name=pack_name,
            generated_by="build_truth_dashboard_payload",
            derived_from=("knowledge.db", "runtime ledgers", "review audit"),
        ),
        "research_overview": {
            "status": "supported" if research_overview_supported else "shared_shell_only",
            "reason": (
                "Research-specific overview surfaces are available because this pack resolves through research-tech."
                if research_overview_supported
                else "This pack currently gets the shared home shell only; research-specific overview panels stay hidden until the pack defines its own equivalents."
            ),
        },
        "objects": {
            "count": objects["total_count"],
            "items": objects["items"],
        },
        "contradictions": {
            "count": contradictions["count"],
            "open_count": contradictions["open_count"],
            "items": contradictions["items"][:8],
            "browser_path": _scoped_path("/ops/contradictions", pack_name=requested_pack),
        },
        "events": {
            "count": events["event_count"],
            "items": events["events"][:8],
            "dates": events["dates"],
            "browser_path": _scoped_path("/ops/events", pack_name=requested_pack),
        },
        "stale_summaries": {
            "count": stale_summaries["count"],
            "items": stale_summaries["items"][:8],
            "browser_path": _scoped_path("/ops/summaries", pack_name=requested_pack),
        },
        "evolution": {
            "candidate_count": evolution["candidate_count"],
            "accepted_count": evolution["accepted_count"],
            "items": evolution["candidate_items"][:6],
        },
        "production": {
            **production,
            "browser_path": _scoped_path("/ops/production", pack_name=requested_pack),
            "weak_point_count": len(production_weak_points),
        },
        "signals": {
            **signals,
            "items": signals["items"][:8],
            "browser_path": _scoped_path("/ops/signals", pack_name=requested_pack),
        },
        "runtime": runtime,
        "runtime_state": operational_runtime_state,
        "orientation": orientation,
        "workflow_groups": workflow_groups,
        "entry_sections": entry_sections,
        "recent_review_actions": list_review_actions(vault_dir, limit=8),
        "priorities": priorities[:8],
    }


def build_runtime_home_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    runtime = get_runtime_status(vault_dir)
    operational_runtime_state = get_operational_runtime_state(vault_dir)
    research_overview_supported = _supports_research_shell(pack_name)
    try:
        objects = build_objects_index_payload(vault_dir, limit=8, offset=0, pack_name=pack_name)
    except (OSError, sqlite3.Error):
        objects = {
            "total_count": 0,
            "items": [],
            "error": "object_index_unavailable",
        }
    # BL-053 Phase 2 foyer block: three-section "what's the state of
    # the world" header rendered at the top of /ops.  Each block re-
    # uses an existing builder so the foyer never gets out of sync
    # with the source-of-truth pages it links to.
    foyer: dict[str, Any] = {
        "today_summary": "",
        "today_path": _scoped_path("/ops/today", pack_name=requested_pack),
        "queue_summary": "",
        "queue_path": _scoped_path("/ops/queue", pack_name=requested_pack),
        "last_run": None,
        "runs_path": _scoped_path("/ops/runs", pack_name=requested_pack),
    }
    try:
        today = build_today_digest_payload(vault_dir, pack_name=pack_name)
        if today.get("available"):
            cards = today.get("cards") or []
            ingested = sum(
                int(card.get("total") or 0)
                for card in cards
                if card.get("id") in ("intake", "absorb")
            )
            failures = sum(
                int(card.get("total") or 0)
                for card in cards
                if card.get("id") == "failures"
            )
            foyer["today_summary"] = (
                f"{ingested} ingested · {failures} failure"
                f"{'s' if failures != 1 else ''} · {today.get('date', '')}"
            )
    except (OSError, sqlite3.Error):
        pass
    try:
        queue = build_queue_overview_payload(vault_dir, pack_name=pack_name)
        pending_chunks = [
            f"{int(q.get('count') or 0)} {q.get('label')}"
            for q in queue.get("queues", [])
            if int(q.get("count") or 0) > 0
        ]
        if pending_chunks:
            foyer["queue_summary"] = " · ".join(pending_chunks)
        else:
            foyer["queue_summary"] = "no pending review items"
    except (OSError, sqlite3.Error):
        pass
    try:
        runs = build_runs_index_payload(vault_dir, pack_name=pack_name, limit=1)
        if runs.get("runs"):
            last = runs["runs"][0]
            foyer["last_run"] = {
                "txn_id": str(last.get("txn_id", "")),
                "workflow_type": str(last.get("workflow_type", "")),
                "status": str(last.get("status", "")),
                "started_at": str(last.get("started_at", "")),
                "detail_href": str(last.get("detail_href", "")),
            }
    except (OSError, sqlite3.Error):
        pass

    entry_sections: list[dict[str, Any]] = []
    return {
        "screen": "truth/runtime-home",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="reader_home",
            pack_name=pack_name,
            generated_by="build_runtime_home_payload",
            derived_from=("knowledge.db", "runtime ledgers"),
        ),
        "foyer": foyer,
        "runtime": runtime,
        "runtime_state": operational_runtime_state,
        "research_overview": {
            "status": "supported" if research_overview_supported else "shared_shell_only",
            "reason": (
                "Research-specific overview surfaces are available because this pack resolves through research-tech."
                if research_overview_supported
                else "This pack currently gets the shared home shell only; research-specific overview panels stay hidden until the pack defines its own equivalents."
            ),
        },
        "workflow_groups": _build_dashboard_workflow_groups(
            requested_pack=requested_pack,
            research_overview_supported=research_overview_supported,
        ),
        "entry_sections": entry_sections,
        "objects": {
            "count": objects["total_count"],
            "total_count": objects["total_count"],
            "items": objects["items"],
            **({"error": objects["error"]} if objects.get("error") else {}),
        },
        "orientation": {
            "assembly_contract": _assembly_contract("orientation_brief", pack_name=pack_name),
            "governance_contract": describe_governance_contract(pack_name=pack_name),
        },
        "signals": {
            "count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/signals", pack_name=requested_pack),
            "surface_contract": describe_observation_surface_contract(pack_name=pack_name, surface_kind="signals"),
        },
        "production": {
            "weak_points": [],
            "weak_point_count": 0,
            "browser_path": _scoped_path("/ops/production", pack_name=requested_pack),
            "surface_contract": describe_observation_surface_contract(
                pack_name=pack_name,
                surface_kind="production_chains",
            ),
        },
        "contradictions": {
            "count": 0,
            "open_count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/contradictions", pack_name=requested_pack),
        },
        "events": {
            "count": 0,
            "items": [],
            "dates": [],
            "browser_path": _scoped_path("/ops/events", pack_name=requested_pack),
        },
        "stale_summaries": {
            "count": 0,
            "items": [],
            "browser_path": _scoped_path("/ops/summaries", pack_name=requested_pack),
        },
        "evolution": {
            "candidate_count": 0,
            "accepted_count": 0,
            "items": [],
        },
        "recent_review_actions": [],
        "priorities": [],
        "mode": "runtime_first",
    }


_OBJECTS_INDEX_VALID_SORTS = ("alpha", "most_linked")


def build_objects_index_payload(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    object_kind: str | None = None,
    pack_name: str | None = None,
    sort: str = "alpha",
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    if sort not in _OBJECTS_INDEX_VALID_SORTS:
        sort = "alpha"
    items = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in list_objects(
            vault_dir,
            limit=limit,
            offset=offset,
            query=query,
            object_kind=object_kind,
            pack_name=pack_name,
            sort=sort,
        )
    ]
    total_count = count_objects(vault_dir, query=query, object_kind=object_kind, pack_name=pack_name)

    kind_stats: list[dict[str, Any]] = []
    try:
        kind_stats = list_object_kind_stats(vault_dir, pack_name=pack_name)
    except Exception:
        pass

    return {
        "screen": "objects/index",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="objects_index",
            pack_name=pack_name,
            generated_by="build_objects_index_payload",
        ),
        "items": items,
        "count": len(items),
        "total_count": total_count,
        "kind_stats": kind_stats,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "query": query or "",
        "object_kind": object_kind or "",
    }


def build_atlas_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    items = list_atlas_memberships(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT,
    )
    # Atlas membership browser: source-note coverage comes from
    # ``get_note_traceability`` per atlas page.  Pre-BL-029 a parallel
    # query joined the deep-dive index too — the chain has no
    # deep-dive intermediate stage now, so just gather source notes.
    object_to_source_notes: dict[str, dict[str, dict[str, str]]] = {}
    for atlas_item in items:
        for member in atlas_item["members"]:
            member_id = str(member.get("object_id") or "")
            if not member_id:
                continue
            traceability = get_object_traceability(
                vault_dir, member_id, pack_name=pack_name,
            )
            object_to_source_notes.setdefault(member_id, {})
            for source in traceability["source_notes"]:
                object_to_source_notes[member_id][source["path"]] = source
    enriched_items = []
    for item in items:
        enriched_members = [
            {
                **member,
                "object_path": _scoped_path(
                    f"/object?id={quote(str(member['object_id']), safe='')}",
                    pack_name=requested_pack,
                ),
            }
            for member in item["members"]
        ]
        preview_titles = [member["title"] for member in enriched_members[:5]]
        member_object_ids = [member["object_id"] for member in enriched_members]
        source_note_map: dict[str, dict[str, str]] = {}
        for member_object_id in member_object_ids:
            for source in object_to_source_notes.get(member_object_id, {}).values():
                source_note_map.setdefault(source["path"], source)
        enriched_items.append(
            {
                **item,
                "members": enriched_members,
                "member_count": len(enriched_members),
                "preview_titles": preview_titles,
                "source_notes": list(source_note_map.values()),
            }
        )
    return {
        "screen": "atlas/browser",
        "requested_pack": requested_pack,
        "items": enriched_items,
        "count": len(enriched_items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
    }


CURATED_ATLAS_DEFAULT_TOP_N = 30
CURATED_ATLAS_MAX_TOP_N = 100

# BL-050: Reader home pulls from the M14 substrate (community
# crystals + curated atlas + scoring).  Tunables live next to the
# atlas defaults so they share one mental model.
READER_HOME_TOP_TOPICS_LIMIT = 5
READER_HOME_RECENT_CRYSTALS_LIMIT = 8
READER_HOME_RECENT_DAYS = 7


def build_reader_home_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Reader-shell home payload.  No DB stat counts, no pipeline
    state — just reading entry points sourced from the synthesis
    substrate.

    Sections:

    * **top_topics** — top-N rows from ``crystal_scores`` for the
      pack, joined with body + label so the home can render a teaser
      without re-fetching markdown.
    * **curated_atlas** — total chain count + top-N constant so the
      home can headline "30 most reusable ideas in your vault".
    * **recent_crystals** — community crystals synthesized in the
      last ``READER_HOME_RECENT_DAYS`` days, capped at
      ``READER_HOME_RECENT_CRYSTALS_LIMIT``.
    * **map_supported** — whether the active pack supports a
      research-style graph nav (drives the Map card visibility).
    """
    from ..synthesis.curated_atlas import build_curated_atlas, _extract_teaser
    from ..synthesis._shared import CRYSTAL_DIR_REL
    from datetime import datetime, timedelta, timezone

    requested_pack = pack_name or ""
    pack = pack_name or PRIMARY_PACK_NAME

    db_path = _db_path(vault_dir)
    # Reader home must not crash on a fresh vault that hasn't run
    # ``ovp-knowledge-index`` yet — show the empty-state hint instead.
    atlas = None
    recent_rows: list[tuple] = []
    recent_total_active = 0
    recent_newest_at = ""
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                atlas = build_curated_atlas(
                    conn, pack=pack, top_n=READER_HOME_TOP_TOPICS_LIMIT,
                )
                cutoff = (datetime.now(timezone.utc)
                          - timedelta(days=READER_HOME_RECENT_DAYS)).isoformat(timespec="seconds")
                recent_rows = conn.execute(
                    """
                    SELECT cc.cluster_id, cc.synthesized_at, cc.body_md, gc.label
                      FROM community_crystals cc
                      JOIN graph_clusters gc
                        ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
                     WHERE cc.pack = ?
                       AND cc.superseded_by_synthesized_at = ''
                       AND cc.synthesized_at > ?
                     ORDER BY cc.synthesized_at DESC
                     LIMIT ?
                    """,
                    (pack, cutoff, READER_HOME_RECENT_CRYSTALS_LIMIT),
                ).fetchall()
                # M25.7 honest-zero: when the 7-day window is empty,
                # the operator needs to know WHY — a bare "no topics"
                # reads as broken when in fact there are hundreds of
                # crystals that are simply older than the window.
                # Pull the active-crystal total + newest synthesized
                # date so the renderer can explain instead of going
                # silent (M25.6 dogfood finding: home looked broken
                # on a vault whose newest crystal was 10 days old).
                ctx_row = conn.execute(
                    """
                    SELECT COUNT(*), MAX(synthesized_at)
                      FROM community_crystals
                     WHERE pack = ?
                       AND superseded_by_synthesized_at = ''
                    """,
                    (pack,),
                ).fetchone()
                recent_total_active = int(ctx_row[0] or 0) if ctx_row else 0
                recent_newest_at = (
                    str(ctx_row[1]) if ctx_row and ctx_row[1] else ""
                )
        except sqlite3.DatabaseError:
            atlas = None
            recent_rows = []
            recent_total_active = 0
            recent_newest_at = ""
    if atlas is None:
        # Empty placeholder so downstream rendering shows the
        # ``run ovp-knowledge-index`` hint without special-casing.
        from ..synthesis.curated_atlas import CuratedAtlas
        atlas = CuratedAtlas(
            pack=pack, top_n=READER_HOME_TOP_TOPICS_LIMIT,
            total_chains=0, entries=(),
            generated_at="",
        )

    # Single source of truth for crystal_id → on-disk safe-id is
    # ``synthesis._shared.crystal_safe_id``.  Imported lazily here to
    # avoid pulling synthesis dependencies into ``view_models``'s
    # module-load path.
    from ..synthesis._shared import crystal_safe_id

    top_topics = []
    for entry in atlas.entries:
        safe_id = crystal_safe_id(entry.crystal_kind, entry.crystal_id)
        note_rel = str(CRYSTAL_DIR_REL / f"{safe_id}.md")
        top_topics.append({
            "rank": entry.rank,
            "label": entry.label,
            "teaser": entry.teaser,
            "score": round(entry.score, 3),
            "note_href": _scoped_path(
                f"/note?path={quote(note_rel, safe='')}",
                pack_name=requested_pack,
            ),
        })

    recent_crystals = []
    for cluster_id, synthesized_at, body_md, label in recent_rows:
        # Recent-crystals query is community-only by design (it joins
        # community_crystals + graph_clusters), so the kind is fixed.
        safe_id = crystal_safe_id("community", str(cluster_id))
        note_rel = str(CRYSTAL_DIR_REL / f"{safe_id}.md")
        recent_crystals.append({
            "label": str(label or "(untitled)"),
            "synthesized_at": str(synthesized_at or ""),
            "teaser": _extract_teaser(str(body_md or ""), max_chars=140),
            "note_href": _scoped_path(
                f"/note?path={quote(note_rel, safe='')}",
                pack_name=requested_pack,
            ),
        })

    return {
        "screen": "reader/home",
        "requested_pack": requested_pack,
        "pack": atlas.pack,
        "top_topics": top_topics,
        # ``curated_atlas`` payload: ``available`` flips off when the
        # corpus is empty so the renderer can suppress the card
        # instead of headlining "30 ideas... ranked from 0 chains".
        # ``effective_top_n`` is ``min(default, total)`` so the body
        # copy never claims more than actually shipped.
        "curated_atlas": {
            "available": atlas.total_chains > 0,
            "total_chains": atlas.total_chains,
            "top_n": CURATED_ATLAS_DEFAULT_TOP_N,
            "effective_top_n": min(CURATED_ATLAS_DEFAULT_TOP_N, atlas.total_chains),
            "atlas_href": _scoped_path("/topics", pack_name=requested_pack),
        },
        "recent_crystals": recent_crystals,
        "recent_days": READER_HOME_RECENT_DAYS,
        # M25.7 honest-zero context for the empty Recent Topics
        # state.  ``total_active`` = crystals that exist regardless
        # of age; ``newest_at`` = when the most recent one was
        # synthesized.  The renderer uses these to explain an
        # empty 7-day window instead of going silent.
        "recent_context": {
            "total_active": recent_total_active,
            "newest_at": recent_newest_at,
            "topics_href": _scoped_path(
                "/topics", pack_name=requested_pack
            ),
        },
        "map_supported": _supports_research_shell(pack_name),
        "search_href": _scoped_path("/search", pack_name=requested_pack),
        "map_href": _scoped_path("/map", pack_name=requested_pack),
        # M20 / BL-077: latest digest summary for the Reader home
        # banner card.  Empty dict when no digest has been generated.
        "digest": _build_latest_digest_info(
            Path(vault_dir), requested_pack=requested_pack,
        ),
    }


def _build_latest_digest_info(
    vault_dir: Path, *, requested_pack: str
) -> dict[str, str]:
    """Look up the most recent file under
    ``40-Resources/Generated/digests/`` and return a small dict the
    Reader home banner card consumes.  Empty dict when the folder
    is missing or empty."""
    folder = Path(vault_dir) / "40-Resources" / "Generated" / "digests"
    if not folder.exists():
        return {}
    candidates = sorted(folder.glob("*.md"))
    if not candidates:
        return {}
    latest = candidates[-1]
    # The task dispatcher writes ``YYYY-MM-DD-<prefix>-<slug>.md``
    # (e.g. ``2026-05-11-digest-daily.md``).  Use the first 10
    # characters of the filename to recover the date label; the
    # earlier ``latest.stem`` extraction shipped the whole name into
    # the home banner (rev-bot 206.1).
    date_str = latest.name[:10]
    # Teaser: skip the YAML frontmatter block and the H1 heading,
    # then return the first non-blank paragraph of the digest body.
    # Earlier this loop skipped any line starting with ``---`` or
    # ``#`` individually, which kept it inside the frontmatter and
    # returned ``type: digest`` as the teaser for every newly
    # generated digest (Codex P2 / rev-bot 206 follow-up).
    teaser = ""
    try:
        body = latest.read_text(encoding="utf-8")
        lines = body.splitlines()
        # Strip the leading frontmatter block (--- ... ---) if present.
        if lines and lines[0].strip() == "---":
            try:
                close_idx = next(
                    i for i, line in enumerate(lines[1:], start=1)
                    if line.strip() == "---"
                )
                lines = lines[close_idx + 1:]
            except StopIteration:
                # Malformed frontmatter — bail to empty teaser.
                lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            teaser = stripped
            break
        if len(teaser) > 220:
            teaser = teaser[:217].rstrip() + "…"
    except OSError:
        teaser = ""
    rel = str(latest.relative_to(vault_dir))
    href = _scoped_path(
        f"/note?path={quote(rel, safe='')}",
        pack_name=requested_pack,
    )
    return {"date": date_str, "href": href, "teaser": teaser}


def build_curated_atlas_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    from ..synthesis.curated_atlas import build_curated_atlas
    from ..synthesis._shared import CRYSTAL_DIR_REL

    requested_pack = pack_name or ""
    pack = pack_name or PRIMARY_PACK_NAME
    requested_top_n = top_n if top_n is not None else CURATED_ATLAS_DEFAULT_TOP_N
    requested_top_n = max(1, min(requested_top_n, CURATED_ATLAS_MAX_TOP_N))

    db_path = _db_path(vault_dir)
    with sqlite3.connect(db_path) as conn:
        atlas = build_curated_atlas(conn, pack=pack, top_n=requested_top_n)

    # Lazy import — same pattern as the adjacent
    # ``build_reader_home_payload`` helper.
    from ..synthesis._shared import crystal_safe_id

    entries: list[dict[str, Any]] = []
    for entry in atlas.entries:
        safe_id = crystal_safe_id(entry.crystal_kind, entry.crystal_id)
        note_rel = str(CRYSTAL_DIR_REL / f"{safe_id}.md")
        entries.append(
            {
                "rank": entry.rank,
                "crystal_kind": entry.crystal_kind,
                "crystal_id": entry.crystal_id,
                "safe_id": safe_id,
                "label": entry.label,
                "score": round(entry.score, 4),
                "size_norm": round(entry.size_norm, 3),
                "credibility_norm": round(entry.credibility_norm, 3),
                "source_diversity_norm": round(entry.source_diversity_norm, 3),
                "contradiction_norm": round(entry.contradiction_norm, 3),
                "reuse_recency_norm": round(entry.reuse_recency_norm, 3),
                "evergreen_recency_norm": round(entry.evergreen_recency_norm, 3),
                "teaser": entry.teaser,
                "source_slugs": list(entry.source_slugs),
                "note_path": note_rel,
                "note_href": _scoped_path(
                    f"/note?path={quote(note_rel, safe='')}",
                    pack_name=requested_pack,
                ),
            }
        )

    # Emit one reuse event per displayed crystal so the
    # ``reuse_recency_norm`` signal in ``crystal_scoring`` actually
    # has a producer.  Pre-fix the signal stayed cold-zero because no
    # surface ever wrote ``reuse_events`` rows with
    # ``object_kind in ('community_crystal', 'contradiction_crystal')``.
    # Best-effort — a JSONL-append failure must not block the
    # /topics page from rendering.
    if entries:
        try:
            from ..reuse_emitter import emit_crystal_reuse_events
            emit_crystal_reuse_events(
                vault_dir,
                pack=pack,
                crystals=[
                    (
                        f"{entry['crystal_kind']}_crystal",
                        str(entry["crystal_id"]),
                    )
                    for entry in entries
                ],
                surface="atlas",
                consumer_ref=f"top_n={atlas.top_n}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort instrumentation
            logger.warning(
                "crystal reuse-event emission failed for /topics: %s", exc,
            )

    return {
        "screen": "atlas/curated",
        "requested_pack": requested_pack,
        "pack": atlas.pack,
        "top_n": atlas.top_n,
        "total_chains": atlas.total_chains,
        "entries": entries,
        "count": len(entries),
        "generated_at": atlas.generated_at,
        "default_top_n": CURATED_ATLAS_DEFAULT_TOP_N,
        "max_top_n": CURATED_ATLAS_MAX_TOP_N,
    }


def build_production_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    surface_contract = describe_observation_surface_contract(
        pack_name=pack_name,
        surface_kind="production_chains",
    )
    if surface_contract["status"] == "missing":
        return {
            "screen": "production/browser",
            "requested_pack": requested_pack,
            "surface_contract": surface_contract,
            "surface_error": (
                f"Pack '{surface_contract['requested_pack']}' does not expose a shared shell "
                f"'production_chains' surface."
            ),
            "items": [],
            "source_items": [],
            "weak_points": [],
            "count": 0,
            "query": query or "",
            "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
            "is_limited": True,
            "counts": {
                "source_notes": 0,
            },
            "operator_rail": [],
            "compiled_sections": [],
            "section_nav": [],
        }
    items = list_production_chains(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT,
    )
    source_items = [item for item in items if item["stage_label"] == "source_note"]
    weak_points = _build_production_weak_points(vault_dir, pack_name=pack_name, query=query)
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=(
                f"{len(items)} production-chain entries are currently visible, spanning "
                f"{len(source_items)} source notes."
            ),
            items=[
                {
                    "kind": "source_notes",
                    "label": "Source notes",
                    "path": "",
                    "detail": f"{len(source_items)} source-note chain entries in scope.",
                },
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=(
                f"{len(weak_points)} chain weak points currently block full source-to-object-to-atlas legibility."
            ),
            items=[
                *[
                    {
                        "kind": "weak_point",
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(str(item['note_path']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"Missing {', '.join(item['missing'])}",
                    }
                    for item in weak_points[:3]
                ]
            ],
        ),
        _compiled_section(
            "chain_gaps",
            "Chain Gaps",
            summary="Weak points highlight where the current production chain stops short of a complete downstream path.",
            items=[
                *[
                    {
                        "kind": item["stage_label"],
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(str(item['note_path']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": ", ".join(item["missing"]),
                    }
                    for item in weak_points[:5]
                ]
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Use the visible source and deep-dive entries to continue into note, object, and atlas-level traceability.",
            items=[
                *[
                    {
                        "kind": item["stage_label"],
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(str(item['path']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": str(item["traceability"].get("chain_summary") or ""),
                    }
                    for item in items[:4]
                ]
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Orientation Brief",
            _scoped_path("/ops/briefing", pack_name=requested_pack),
            "Return to the current entry product.",
        ),
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Review active signals related to chain maintenance.",
        ),
        _operator_action(
            "Action Queue",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Run or inspect queued execution work.",
        ),
        _operator_action(
            "Search",
            _scoped_path("/search", pack_name=requested_pack),
            "Search laterally from the current production scope.",
        ),
    ]
    return {
        "screen": "production/browser",
        "requested_pack": requested_pack,
        "surface_contract": surface_contract,
        "items": items,
        "source_items": source_items,
        "weak_points": weak_points,
        "count": len(items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
        "counts": {
            "source_notes": len(source_items),
        },
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }


def build_stale_summary_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    items = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in list_stale_summaries(vault_dir, pack_name=pack_name, query=query)
    ]
    review_context = get_review_context(vault_dir, [item["object_id"] for item in items], pack_name=pack_name)
    return {
        "screen": "truth/stale-summaries",
        "requested_pack": requested_pack,
        "items": items,
        "count": len(items),
        "query": query or "",
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=[item["object_id"] for item in items], limit=8),
        "detection_notes": [
            "Stale summary review flags compiled summaries that are weak and have no outgoing supporting relations.",
            "This queue is deterministic and favors false negatives over false positives.",
        ],
    }


def build_search_payload(
    vault_dir: Path | str,
    *,
    query: str,
    pack_name: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    offset = (page - 1) * page_size
    results = search_vault_surface(
        vault_dir,
        query=query,
        pack_name=pack_name,
        object_limit=page_size,
        note_limit=page_size,
        object_offset=offset,
        note_offset=offset,
    )
    objects = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in results["objects"]
    ]
    notes = [
        {
            **item,
            "note_path": _scoped_path(
                f"/note?path={quote(str(item['path']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in results["notes"]
    ]
    reader_projection = _build_reader_search_projection(
        vault_dir,
        query=query,
        objects=objects,
        notes=notes,
    )
    return {
        "screen": "search/results",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="search_results",
            pack_name=pack_name,
            generated_by="build_search_payload",
            derived_from=("knowledge.db.objects", "knowledge.db.pages_index"),
        ),
        **results,
        "objects": objects,
        "notes": notes,
        **reader_projection,
        "object_count": len(results["objects"]),
        "note_count": len(results["notes"]),
        "page": page,
        "page_size": page_size,
    }


def _compute_v2_lineage(
    vault_dir: Path | str,
    note_path: str,
    requested_pack: str,
) -> dict[str, Any] | None:
    """Compute the BL-058 raw-source ↔ evergreens ↔ crystals chain
    for the note at ``note_path``.

    Pre-fix the only lineage signal was ``production_chain`` (the
    legacy deep-dive era data flow).  v2 evergreens come from raw
    GitHub/article sources in ``50-Inbox/03-Processed`` rather than
    deep-dives, so the operator had no UI to answer "which raw
    source did this evergreen come from?" or "which evergreens
    came from this raw source?".  This payload fills that gap.

    Returns ``None`` when the note isn't an evergreen or a raw
    intake source — the caller suppresses the card in that case
    so non-applicable notes (MOCs, atlas pages, …) don't render
    an empty section.

    Shape::

        {
          "kind": "evergreen" | "raw_source",
          "raw_source": {"slug", "path", "note_href"} | None,
          "evergreens": [{slug, title, note_href}, ...],
          "clusters": [{cluster_id, label, member_count, cluster_href}, ...],
          "crystals": [{kind, crystal_id, label, note_href}, ...],
        }
    """
    rel = str(note_path).replace("\\", "/").lstrip("./")
    is_evergreen = rel.startswith("10-Knowledge/Evergreen/") and rel.endswith(".md")
    is_raw_source = rel.startswith("50-Inbox/03-Processed/") and rel.endswith(".md")
    if not (is_evergreen or is_raw_source):
        return None

    vault_root = Path(vault_dir).resolve()
    abs_path = vault_root / rel
    if not abs_path.exists():
        return None

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return None

    raw_stem: str | None = None
    target_evergreen_slugs: list[str] = []

    if is_evergreen:
        # Parse the body's ``## Source`` block to find the raw source
        # wikilink.  Frontmatter doesn't yet carry a ``source_path``
        # field for v2 evergreens (BL-058 deferred that to BL-058b),
        # so the wikilink in the rendered body is the only durable
        # back-reference we can rely on without a schema migration.
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        m = re.search(
            r"##\s*Source\s*\n+\s*-\s*\[\[([^\]]+)\]\]",
            text, flags=re.MULTILINE,
        )
        if m:
            raw_stem = m.group(1).strip()
        else:
            # Older v1 evergreens: scan body for any wikilink targeting
            # the 03-Processed area (less reliable but keeps the lineage
            # card useful for legacy notes too).
            m = re.search(r"\[\[([^\]]*?(?:_深度解读|github|article))\]\]", text)
            if m:
                raw_stem = m.group(1).strip()
        own_slug = abs_path.stem
        if own_slug:
            target_evergreen_slugs.append(own_slug)
    else:
        # Raw source — ``raw_stem`` is the file's basename without ``.md``.
        raw_stem = abs_path.stem

    sibling_evergreens: list[dict[str, str]] = []
    clusters: list[dict[str, Any]] = []
    crystals: list[dict[str, Any]] = []

    with sqlite3.connect(db_path) as conn:
        if raw_stem:
            # First-choice strategy: query the indexed ``page_links``
            # table where each row is one resolved wikilink.  Cheap
            # JOIN, scales linearly with the in-degree of the target.
            try:
                rows = conn.execute(
                    """
                    SELECT pi.slug, pi.title
                      FROM page_links pl
                      JOIN pages_index pi ON pi.slug = pl.source_slug
                     WHERE pi.note_type = 'evergreen'
                       AND (pl.target_slug = ? OR pl.target_raw = ?)
                     ORDER BY pi.slug
                     LIMIT ?
                    """,
                    (raw_stem, raw_stem, LINEAGE_SIBLING_EVERGREEN_LIMIT),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            # Fallback: ``page_links`` only stores rows whose target
            # was resolvable to a slug already in ``pages_index``.  The
            # file scanner (knowledge_index.py:1062) drops unresolved
            # wikilinks entirely — and raw intake sources in
            # ``50-Inbox/03-Processed`` are NOT scanned into
            # ``pages_index`` (only Evergreen / Atlas / 20-Areas are),
            # so the ``## Source`` link to a raw-source basename like
            # ``2026-04-28_neuphonic_neutts`` produces zero
            # ``page_links`` rows.  The body-LIKE scan below recovers
            # those cases.  Once BL-058b adds a typed ``source_stem``
            # column to ``pages_index`` (or a typed ``source_path``
            # field to evergreen frontmatter that knowledge_index
            # surfaces), this fallback can be removed.
            if not rows:
                # ``ESCAPE`` lets the LIKE pattern carry literal ``%``
                # / ``_`` / ``\`` characters in raw stems without false
                # positives.  Trigram-FTS would be faster but
                # ``page_fts`` strips brackets when tokenising so
                # phrase-matching ``[[<stem>]]`` doesn't beat LIKE.
                escaped = (
                    raw_stem.replace("\\", "\\\\")
                            .replace("%", "\\%")
                            .replace("_", "\\_")
                )
                try:
                    rows = conn.execute(
                        """
                        SELECT slug, title FROM pages_index
                         WHERE note_type = 'evergreen'
                           AND body LIKE ? ESCAPE '\\'
                         ORDER BY slug
                         LIMIT ?
                        """,
                        (f"%[[{escaped}]]%", LINEAGE_SIBLING_EVERGREEN_LIMIT),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            for slug, title in rows:
                sibling_evergreens.append({
                    "slug": str(slug),
                    "title": str(title or slug),
                    "note_href": _scoped_path(
                        f"/note?path={quote(f'10-Knowledge/Evergreen/{slug}.md', safe='')}",
                        pack_name=requested_pack,
                    ),
                })
                if slug not in target_evergreen_slugs:
                    target_evergreen_slugs.append(str(slug))

        # Forward chain: clusters that contain any of our evergreen
        # slugs.  ``member_object_ids_json`` is a JSON array of object
        # ids that match the evergreen slug.
        if target_evergreen_slugs:
            try:
                cluster_rows = conn.execute(
                    """
                    SELECT cluster_id, label, member_object_ids_json
                      FROM graph_clusters
                     WHERE cluster_kind = 'louvain_community'
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                cluster_rows = []
            slug_set = set(target_evergreen_slugs)
            for cluster_id, label, members_json in cluster_rows:
                try:
                    members = set(json.loads(members_json or "[]"))
                except json.JSONDecodeError:
                    continue
                hit = members & slug_set
                if not hit:
                    continue
                from ..synthesis._shared import crystal_safe_id
                safe_id = crystal_safe_id("community", str(cluster_id))
                clusters.append({
                    "cluster_id": str(cluster_id),
                    "label": str(label or "(untitled)"),
                    "member_count": len(members),
                    "matched": sorted(hit),
                    "cluster_href": _scoped_path(
                        f"/ops/cluster?id={quote(str(cluster_id), safe='')}",
                        pack_name=requested_pack,
                    ),
                    "crystal_note_href": _scoped_path(
                        f"/note?path=40-Resources/Crystals/{safe_id}.md",
                        pack_name=requested_pack,
                    ),
                })

            # Crystals — community first, contradictions second.
            try:
                crystal_rows = conn.execute(
                    """
                    SELECT cluster_id, source_evergreen_slugs_json
                      FROM community_crystals
                     WHERE superseded_by_synthesized_at = ''
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                crystal_rows = []
            for cluster_id, slugs_json in crystal_rows:
                try:
                    slugs = set(json.loads(slugs_json or "[]"))
                except json.JSONDecodeError:
                    continue
                if not (slugs & slug_set):
                    continue
                from ..synthesis._shared import crystal_safe_id
                safe_id = crystal_safe_id("community", str(cluster_id))
                crystals.append({
                    "kind": "community_crystal",
                    "crystal_id": str(cluster_id),
                    "label": str(cluster_id),
                    "note_href": _scoped_path(
                        f"/note?path=40-Resources/Crystals/{safe_id}.md",
                        pack_name=requested_pack,
                    ),
                })
            try:
                contra_rows = conn.execute(
                    """
                    SELECT contradiction_id, subject_key, source_object_ids_json
                      FROM contradiction_crystals
                     WHERE superseded_by_synthesized_at = ''
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                contra_rows = []
            for contradiction_id, subject_key, source_ids_json in contra_rows:
                try:
                    sources = set(json.loads(source_ids_json or "[]"))
                except json.JSONDecodeError:
                    continue
                if not (sources & slug_set):
                    continue
                from ..synthesis._shared import crystal_safe_id
                safe_id = crystal_safe_id("contradiction", str(contradiction_id))
                crystals.append({
                    "kind": "contradiction_crystal",
                    "crystal_id": str(contradiction_id),
                    "label": str(subject_key or contradiction_id),
                    "note_href": _scoped_path(
                        f"/note?path=40-Resources/Crystals/{safe_id}.md",
                        pack_name=requested_pack,
                    ),
                })

    raw_source_info: dict[str, str] | None = None
    if raw_stem:
        # Locate the raw source file under 03-Processed.  The basename
        # → path mapping is unambiguous because Phase A's output filenames
        # are already disambiguated by ``<date>_<owner>_<repo>``.
        candidates = list(
            (vault_root / "50-Inbox" / "03-Processed").rglob(f"{raw_stem}.md")
        )
        if candidates:
            rel_target = candidates[0].relative_to(vault_root)
            raw_source_info = {
                "slug": raw_stem,
                "path": str(rel_target),
                "note_href": _scoped_path(
                    f"/note?path={quote(str(rel_target), safe='')}",
                    pack_name=requested_pack,
                ),
            }
        else:
            # File may have been archived already (Phase A re-process
            # moves the legacy deep-dive into 70-Archive).  Surface the
            # stem so the user can grep for it.
            raw_source_info = {
                "slug": raw_stem,
                "path": "",
                "note_href": "",
            }

    return {
        "kind": "evergreen" if is_evergreen else "raw_source",
        "raw_source": raw_source_info,
        "evergreens": sibling_evergreens,
        "clusters": sorted(clusters, key=lambda c: -int(c.get("member_count", 0))),
        "crystals": crystals,
    }


def build_note_page_payload(
    vault_dir: Path | str,
    *,
    note_path: str,
    pack_name: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    provenance = get_note_provenance(vault_dir, note_path=note_path)
    production_chain = get_note_traceability(vault_dir, note_path=note_path, pack_name=pack_name)
    inbound_capture = get_note_inbound_capture_summary(vault_dir, note_path=note_path)
    production_chain["source_notes"] = [
        {
            **item,
            "note_path": _scoped_path(
                f"/note?path={quote(str(item['path']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in production_chain["source_notes"]
    ]
    production_chain["objects"] = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in production_chain["objects"]
    ]
    production_chain["atlas_pages"] = [
        {
            **item,
            "note_path": _scoped_path(
                f"/note?path={quote(str(item['path']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in production_chain["atlas_pages"]
    ]
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=(
                f"{production_chain['note']['title']} currently resolves as a "
                f"{production_chain.get('stage_label', '').replace('_', ' ')} with "
                f"{production_chain['counts']['objects']} objects and "
                f"{production_chain['counts']['atlas_pages']} atlas pages downstream."
            ),
            items=[
                {
                    "kind": "stage",
                    "label": str(production_chain.get("stage_label") or "").replace("_", " "),
                    "path": "",
                    "detail": str(production_chain.get("chain_status") or ""),
                }
            ],
        ),
        _compiled_section(
            "inbound_capture",
            "Inbound Capture",
            summary=str(inbound_capture.get("summary") or ""),
            items=[
                {
                    "kind": str(item.get("kind") or ""),
                    "label": str(item.get("label") or ""),
                    "path": (
                        _scoped_path(f"/note?path={quote(str(item['path']), safe='')}", pack_name=requested_pack)
                        if item.get("path")
                        else ""
                    ),
                    "detail": str(item.get("detail") or ""),
                }
                for item in inbound_capture.get("items", [])
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary="The note traceability chain shows which objects and atlas pages this note currently anchors.",
            items=[
                {
                    "kind": "object",
                    "label": item["title"],
                    "path": item["object_path"],
                    "detail": "Derived evergreen object",
                }
                for item in production_chain["objects"][:3]
            ],
        ),
        _compiled_section(
            "production_chain",
            "Production Chain",
            summary=str(production_chain.get("chain_summary") or ""),
            items=[
                {
                    "kind": "chain_status",
                    "label": "Chain status",
                    "path": "",
                    "detail": str(production_chain.get("chain_status") or ""),
                },
                {
                    "kind": "missing_stages",
                    "label": "Missing stages",
                    "path": "",
                    "detail": ", ".join(str(item).replace("_", " ") for item in production_chain.get("missing_stages", [])) or "None",
                },
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Continue into derived objects or atlas reach from this note.",
            items=[
                {
                    "kind": "object",
                    "label": item["title"],
                    "path": item["object_path"],
                    "detail": "Open derived object page.",
                }
                for item in production_chain["objects"][:2]
            ],
        ),
    ]
    fallback_object_path = (
        production_chain["objects"][0]["object_path"]
        if production_chain["objects"]
        else _scoped_path("/ops/objects", pack_name=requested_pack)
    )
    fallback_object_label = "Open derived object" if production_chain["objects"] else "Objects"
    return {
        "screen": "note/page",
        "requested_pack": requested_pack,
        "note_path": note_path,
        "provenance": provenance,
        "inbound_capture": inbound_capture,
        "production_chain": production_chain,
        # BL-058 follow-up — raw source ↔ evergreens ↔ clusters ↔ crystals
        # chain.  ``None`` for notes that aren't an evergreen or
        # 03-Processed source so the renderer can suppress the card.
        "lineage": _compute_v2_lineage(
            vault_dir, note_path, requested_pack,
        ),
        "operator_rail": [
            _operator_action(
                "Production Browser",
                _scoped_path("/ops/production", pack_name=requested_pack),
                "Inspect broader production-chain weak points.",
            ),
            _operator_action(
                "Signals",
                _scoped_path("/ops/signals", pack_name=requested_pack),
                "Open active signals for this shell scope.",
            ),
            _operator_action(
                fallback_object_label,
                fallback_object_path,
                "Jump into the most relevant derived object surface.",
            ),
        ],
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }
