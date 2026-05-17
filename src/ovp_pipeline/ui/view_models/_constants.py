# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
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

from ovp_pipeline.assembly_recipe_registry import describe_assembly_recipe_contract
from ovp_pipeline.governance_registry import describe_governance_contract
from ovp_pipeline.observation_surface_registry import describe_observation_surface_contract
from ovp_pipeline.pack_resolution import iter_compatible_packs
from ovp_pipeline.packs.loader import PRIMARY_PACK_NAME
from ovp_pipeline.projection_labels import projection_label
from ovp_pipeline.reuse_emitter import collect_object_ids, emit_reuse_events_for_object_ids
from ovp_pipeline.runtime import VaultLayout, resolve_vault_dir
from ovp_pipeline.truth_store import CONTRADICTION_HEURISTIC_NOTE
from ovp_pipeline.truth_api import (
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


from ovp_pipeline.object_kinds import OBJECT_KIND_LABELS as _OBJECT_KIND_LABELS


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
from ovp_pipeline.audit_identity import (
    audit_cluster_ids,
    audit_object_ids,
    audit_slug_for_column,
)
from ovp_pipeline.audit_time import local_day as _audit_local_day
from ovp_pipeline.audit_time import parse_audit_ts as _parse_audit_ts
from ovp_pipeline.event_evidence_registry import (
    CATEGORIES as _EVT_CATEGORIES,
    event_types_for_category as _evt_for_category,
)



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


# Clock-skew slack when comparing timestamps from different
# producers (event_emitter UTC vs PipelineLogger naive-local, plus
# ops_state.refreshed_at).  Below this delta we call it "current"
# rather than flag a spurious staleness.
_STALENESS_SLACK_SECONDS = 120



# A cohort source still sitting in Received/Extracted older than
# this is "stalled" — intake happened but the pipeline never moved
# it forward.  One week covers the normal manual-absorb cadence.
_INTAKE_STALL_DAYS = 7


# BL-103b: DAG-boundary stage telemetry event types + which DAG
# stages feed each lifecycle card.  Synthesis crystals are a
# separate command (not a DAG stage) so a 0 there legitimately maps
# to "not_run"/"unknown", which is itself the honest answer.
_STAGE_EVENT_TYPES = (
    "stage_started",
    "stage_completed",
    "stage_failed",
    "stage_skipped",
)

_STATE_FEEDING_STAGES: dict[str, frozenset[str]] = {
    "Received": frozenset(
        {"pinboard", "pinboard_process", "clippings", "articles"}
    ),
    "Extracted": frozenset({"absorb"}),
    "Accepted": frozenset({"absorb"}),
    "Synthesized": frozenset({"moc"}),
    "NeedsAction": frozenset(),
}



# M25.2: /ops/items default page size.  Cards drill into this view
# carrying state= and optional pack=; the page paginates the rest.
ITEMS_LIST_DEFAULT_LIMIT = 50

ITEMS_LIST_MAX_LIMIT = 500



# M25.4: /ops/events/audit page size.  Slightly larger than the
# items list because raw audit rows are noisier; operators tend to
# scan rather than click.
EVENTS_AUDIT_DEFAULT_LIMIT = 200

EVENTS_AUDIT_MAX_LIMIT = 2000



_CLUSTER_BROWSER_PAGE_SIZES = (15, 50, 200)



_OBJECTS_INDEX_VALID_SORTS = ("alpha", "most_linked")



CURATED_ATLAS_DEFAULT_TOP_N = 30

CURATED_ATLAS_MAX_TOP_N = 100


# BL-050: Reader home pulls from the M14 substrate (community
# crystals + curated atlas + scoring).  Tunables live next to the
# atlas defaults so they share one mental model.
READER_HOME_TOP_TOPICS_LIMIT = 5

READER_HOME_RECENT_CRYSTALS_LIMIT = 8

READER_HOME_RECENT_DAYS = 7


__all__ = [
    'annotations',
    '_dt',
    'json',
    'logging',
    'math',
    're',
    'sqlite3',
    'Counter',
    'Path',
    'Any',
    'quote',
    'describe_assembly_recipe_contract',
    'describe_governance_contract',
    'describe_observation_surface_contract',
    'iter_compatible_packs',
    'PRIMARY_PACK_NAME',
    'projection_label',
    'collect_object_ids',
    'emit_reuse_events_for_object_ids',
    'VaultLayout',
    'resolve_vault_dir',
    'CONTRADICTION_HEURISTIC_NOTE',
    'CONTRADICTION_STATUS_EXPLANATIONS',
    'MAX_PAGE_SIZE',
    'SIGNAL_TYPE_EXPLANATIONS',
    '_batch_object_rows',
    'count_action_queue_by_status',
    'count_contradictions_by_status',
    'count_graph_clusters',
    'count_objects',
    'get_briefing_snapshot',
    'get_graph_cluster_detail',
    'get_object_detail',
    'get_object_source_chain',
    'get_object_traceability',
    'get_note_inbound_capture_summary',
    'get_note_provenance',
    'get_note_traceability',
    'get_operational_runtime_state',
    'get_object_provenance_map',
    'get_review_context',
    'get_runtime_status',
    'get_topic_neighborhood',
    'list_candidate_concepts',
    'list_evolution_candidates',
    'list_evolution_links',
    'list_review_actions',
    'list_atlas_memberships',
    'list_action_queue',
    'list_contradictions',
    'list_graph_clusters',
    'list_graph_edges_for_object_scope',
    'list_objects',
    'list_mention_kind_stats',
    'list_object_kind_stats',
    'list_relation_kind_stats',
    'list_production_gaps',
    'list_production_chains',
    'list_signals',
    'list_stale_summaries',
    'list_timeline_events',
    'search_vault_surface',
    '_OBJECT_KIND_LABELS',
    'audit_cluster_ids',
    'audit_object_ids',
    'audit_slug_for_column',
    '_audit_local_day',
    '_parse_audit_ts',
    '_EVT_CATEGORIES',
    '_evt_for_category',
    'logger',
    '_ATLAS_TYPE_BY_OBJECT_KIND',
    '_ATLAS_LINK_KINDS',
    'DEFAULT_CANDIDATE_BROWSER_LIMIT',
    'DEFAULT_EVENT_DOSSIER_LIMIT',
    'DEFAULT_TIMELINE_DAYS',
    'DEFAULT_TIMELINE_SAMPLE_SIZE',
    'TIMELINE_HIGHLIGHTED_TYPES',
    'TIMELINE_ERROR_EVENT_TYPES',
    'TIMELINE_SNIPPET_CHARS',
    'LINEAGE_SIBLING_EVERGREEN_LIMIT',
    'DEFAULT_TRACEABILITY_BROWSER_LIMIT',
    'DEFAULT_GRAPH_MAP_LIMIT',
    'DEFAULT_GRAPH_MAP_MEMBER_CAP',
    'GRAPH_MAP_WIDTH',
    'GRAPH_MAP_HEIGHT',
    'GRAPH_MAP_CLUSTER_ORBIT_X_FACTOR',
    'GRAPH_MAP_CLUSTER_ORBIT_Y_FACTOR',
    'GRAPH_MAP_MARGIN',
    'GRAPH_MAP_LOCAL_RADIUS_BASE',
    'GRAPH_MAP_LOCAL_RADIUS_PER_MEMBER',
    'GRAPH_MAP_LOCAL_RADIUS_MAX',
    'GRAPH_MAP_NODE_BASE_RADIUS',
    'GRAPH_MAP_NODE_RADIUS_PER_DEGREE',
    'GRAPH_MAP_NODE_RADIUS_BONUS_MAX',
    '_OBJECT_KIND_READER_PROFILES',
    '_LIST_MARKER_RE',
    'OBJECT_SOURCE_RAIL_RELATED_LIMIT',
    '_TODAY_CARD_LABELS',
    'TODAY_DIGEST_CARDS',
    'M25_LIFECYCLE_CARD_DEFS',
    '_ACTIVITY_IDENTITY_KIND',
    'TODAY_CARD_SAMPLE_SIZE',
    'DEFAULT_RUNS_INDEX_LIMIT',
    'RUNS_STALE_AFTER_HOURS',
    '_STALENESS_SLACK_SECONDS',
    '_INTAKE_STALL_DAYS',
    '_STAGE_EVENT_TYPES',
    '_STATE_FEEDING_STAGES',
    'ITEMS_LIST_DEFAULT_LIMIT',
    'ITEMS_LIST_MAX_LIMIT',
    'EVENTS_AUDIT_DEFAULT_LIMIT',
    'EVENTS_AUDIT_MAX_LIMIT',
    '_CLUSTER_BROWSER_PAGE_SIZES',
    '_OBJECTS_INDEX_VALID_SORTS',
    'CURATED_ATLAS_DEFAULT_TOP_N',
    'CURATED_ATLAS_MAX_TOP_N',
    'READER_HOME_TOP_TOPICS_LIMIT',
    'READER_HOME_RECENT_CRYSTALS_LIMIT',
    'READER_HOME_RECENT_DAYS'
]
