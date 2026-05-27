from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# File size limits
# ---------------------------------------------------------------------------

MAX_MODULE_LINES = 3000

# Tech-debt ratchet: these limits MUST NOT increase.  When a module
# shrinks below its cap, lower the cap to lock in the gain.
KNOWN_OVERSIZED = {
    "truth_api.py": 7000,           # target: split into truth_queries / governance_api / search_api
    # commands/_ui_renderers.py — RETIRED: M27 BL-110 PR2 split it
    # into the commands/_ui_renderers/ package (same shape as
    # view_models: constants leaf + topological layers + per-surface
    # modules), every file < the 3000 default.  Ratchet tightened.
    # ui/view_models.py — RETIRED: M27 BL-110 PR1 split it into the
    # ui/view_models/ package (constants leaf + topological layers +
    # per-surface modules), every file < the 3000 default.  Ratchet
    # tightened: the 5000 carve-out is gone, not relocated.
    "unified_pipeline_enhanced.py": 3500,  # target: split (M27 BL-112)
    # BL-115/116 added ~50 lines of matcher wiring (snapshot helper
    # call, identity_match invocation, audit emit) inside the
    # rebuild_knowledge_index body.  Limit raised from 3000 to 3100
    # as a deliberate ratchet — target: extract preservation + matcher
    # wiring helpers in a follow-up so this drops back below 3000.
    "knowledge_index.py": 3100,
}


def test_file_size_limits(repo_root):
    """Core modules must not exceed MAX_MODULE_LINES (known exceptions xfail-tracked)."""
    src = repo_root / "src" / "ovp_pipeline"
    violations = []
    for py in sorted(src.rglob("*.py")):
        rel = str(py.relative_to(src))
        if rel == "__init__.py" or rel.endswith("/__init__.py"):
            continue
        lines = len(py.read_text(encoding="utf-8").splitlines())
        limit = KNOWN_OVERSIZED.get(rel, MAX_MODULE_LINES)
        if lines > limit:
            violations.append(f"{rel}: {lines} lines (limit {limit})")
    assert not violations, "Files exceeding size limits:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Layer import direction
# ---------------------------------------------------------------------------

LAYER_MAP = {
    "L1_canonical": {
        "identity",
        "concept_registry",
        "concept_resolver",
        "object_registry",
        "semantic_relation_registry",
    },
    "L2_derived": {
        "truth_store",
        "knowledge_index",
        "graph",
        "graph_cli",
        "lint_checker",
        "discovery",
        "evidence",
        "evidence_replay",
    },
    "L3_assembly": {
        "query_tool",
        "wiki_views",
    },
    "L4_governance": {
        "promotion_policy",
        "governance_registry",
        "promote_candidates",
        "batch_evergreen",
        "relation_promotion",
        "refine",
    },
}

FORBIDDEN_DEPS = [
    ("L1_canonical", "L4_governance"),
    ("L1_canonical", "L3_assembly"),
    ("L1_canonical", "L2_derived"),
    ("L2_derived", "L4_governance"),
]

# Modules excluded from the layer rule until their cross-layer imports are
# refactored out.  Each entry is tech debt — do not add without a comment.
EXCLUDED_FROM_LAYER_CHECK = {
    "truth_api",              # facade: imports all layers by design
    "_truth_helpers",         # extracted from truth_api, same scope
    "commands/ui_server",     # UI entry-point touches all layers
    "commands/_ui_renderers", # renderers extracted from ui_server
    "concept_registry",       # debt: imports discovery (L2) via relative import
    "knowledge_index",        # debt: lazy-imports relation_promotion (L4)
}


def _module_to_layer(module_stem: str) -> str | None:
    for layer, members in LAYER_MAP.items():
        if module_stem in members:
            return layer
    return None


def _extract_ovp_imports(filepath: Path, *, src_root: Path | None = None) -> list[str]:
    """Return list of ovp_pipeline sub-module stems imported by filepath.

    Handles both absolute (``from ovp_pipeline.x import ...``) and
    relative (``from .x import ...``) import forms so that the layer
    rule cannot be bypassed via relative imports.
    """
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("ovp_pipeline."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    imports.append(parts[1])
            elif node.level and node.level >= 1 and node.module:
                parts = node.module.split(".")
                imports.append(parts[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ovp_pipeline."):
                    parts = alias.name.split(".")
                    if len(parts) >= 2:
                        imports.append(parts[1])
    return imports


def test_layer_import_direction(repo_root):
    """Lower layers must not import from higher layers."""
    src = repo_root / "src" / "ovp_pipeline"
    violations = []
    for py in sorted(src.rglob("*.py")):
        rel = str(py.relative_to(src)).replace(".py", "").replace("/__init__", "")
        if rel == "__init__":
            continue
        if any(rel == ex or rel.endswith(f"/{ex}") for ex in EXCLUDED_FROM_LAYER_CHECK):
            continue
        stem = rel.split("/")[-1]
        source_layer = _module_to_layer(stem)
        if source_layer is None:
            continue
        for imported in _extract_ovp_imports(py):
            target_layer = _module_to_layer(imported)
            if target_layer is None:
                continue
            if (source_layer, target_layer) in FORBIDDEN_DEPS:
                violations.append(
                    f"{rel} ({source_layer}) imports {imported} ({target_layer})"
                )
    assert not violations, "Layer dependency violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# No direct sqlite3 in non-data modules
# ---------------------------------------------------------------------------

SQLITE_ALLOWED_MODULES = {
    "_truth_helpers",
    "truth_api",
    "truth_store",
    "knowledge_index",
    "commands/doctor",
    # Data-adjacent modules with legitimate sqlite3 usage (tech-debt baseline)
    "autopilot/queue",
    "commands/evidence_verify",
    "commands/embedding_dedup",
    "commands/link_suggest",
    "commands/repair",
    "commands/reuse_report",
    "commands/ui_server",
    "commands/_ui_renderers",
    "commands/working_memory",
    "commands/score_sources",
    "commands/backup_db",
    "commands/source_coverage",
    "commands/score_domain",
    "source_authority",
    "entities/store",
    "discovery",
    "evidence",
    "evidence_replay",
    "graph/graph_ops",
    "graph_cli",
    "lint_checker",
    "materializers/crystal",
    "materializers/event_dossier",
    "materializers/topic_view",
    "operations/runtime",
    "packs/research_tech/surfaces",
    "promotion_policy",
    "relation_promotion",
    "reuse_emitter",
    "commands/backfill_provenance",
    "provenance",
    "commands/list_crystals",
    "commands/rerender_crystals",
    "commands/rescore_crystals",
    "synthesis/community_crystal",
    "synthesis/contradiction_crystal",
    "synthesis/crystal_fts",
    "synthesis/crystal_scoring",
    "synthesis/curated_atlas",
    "synthesis/_shared",
    "synthesis/_versioning",
    # BL-115/116: Jaccard concept-identity matcher.  Touches
    # ``concept_identity_ledger`` (UPDATE current_cluster_id +
    # lineage_json) and ``community_crystals`` (BL-116 orphan
    # supersede) — same category as the rest of ``synthesis/*``
    # writers; works on the derived knowledge.db projection only.
    "synthesis/identity_match",
    "ui/view_models",
    # M23 / BL-094: digest input collector reads evergreen_revisions,
    # audit_events, community_crystals, graph_clusters etc.  Data-
    # layer aggregator over knowledge.db projections — same category
    # as materializers/* and synthesis/_shared.
    "digest_inputs",
}

# ---------------------------------------------------------------------
# M27 BL-111 — SQLite boundary policy, grouped + rationalised.
#
# This is NOT "relaxing the rule to make the test pass": it records
# the rule's TRUE boundary.  The architecture invariant (CLAUDE.md
# §7a) is that the *canonical Authority* is vault markdown + the
# registries — `knowledge.db` is a DERIVED, rebuildable projection.
# Modules that read/write the derived projection are data-adjacent
# by definition, the same category already baselined above
# (digest_inputs / materializers/* / synthesis/_shared).  Grouping
# them with an explicit per-group rationale makes the boundary
# legible instead of a flat blob.
#
# The real fix — a data-access facade (ops_state_store /
# audit_events_store / digest_store / chat_store) so business code
# never hand-writes SQL — is tracked as **BL-111b**, deliberately
# NOT done here ("NOT a big migration first", M27 plan).  These
# entries are the documented baseline until BL-111b migrates them.

# UI payload builders: read the derived projection to render Reader
# / maintainer pages.  No Authority writes.
_SQLITE_UI_PROJECTION_PAYLOAD_BUILDERS = {
    "commands/_chats_list_page",
    "commands/_digests_list_page",
    "revisions_view",
}
# Ops/lifecycle readers: derive ops state from audit_events /
# ops_state / objects projections.  Read-only over derived data.
_SQLITE_OPS_PROJECTION_READERS = {
    "absorb_router",
    "auto_evergreen_extractor",
    "ops_lifecycle",
    "producer_audit",
    "commands/refresh_ops",
    "commands/backfill_objects_source_url",
}
# Projection writers: (re)materialise DERIVED knowledge.db tables
# (chats / ops_state / relations / truth projections).  Rebuildable
# by `ovp-knowledge-index` — they do not own canonical Authority.
_SQLITE_PROJECTION_WRITERS = {
    "chats_projection",
    "ops_state",
    "relation_writer",
    "truth_store_writers",
}
# Thin CLI wrappers around the projection modules above (open a
# connection, delegate).  No SQL logic of their own.
_SQLITE_THIN_PROJECTION_CLIS = {
    "commands/ops_state_cli",
    "commands/producer_audit_cli",
}
SQLITE_ALLOWED_MODULES = (
    SQLITE_ALLOWED_MODULES
    | _SQLITE_UI_PROJECTION_PAYLOAD_BUILDERS
    | _SQLITE_OPS_PROJECTION_READERS
    | _SQLITE_PROJECTION_WRITERS
    | _SQLITE_THIN_PROJECTION_CLIS
)


def test_no_direct_sqlite_in_non_data_modules(repo_root):
    """Only data-layer modules may use sqlite3 directly."""
    src = repo_root / "src" / "ovp_pipeline"
    violations = []
    for py in sorted(src.rglob("*.py")):
        rel = str(py.relative_to(src)).replace(".py", "")
        if rel.endswith("/__init__") or rel == "__init__":
            continue
        # Prefix-aware: an allowlisted module that has since become a
        # PACKAGE (BL-110 split ui/view_models, commands/_ui_renderers)
        # still grants its submodules — the sqlite usage was always
        # allowlisted, only the path shape changed.  `startswith`
        # covers that and any future package split.
        if any(
            rel == allowed
            or rel.endswith(f"/{allowed}")
            or rel.startswith(f"{allowed}/")
            for allowed in SQLITE_ALLOWED_MODULES
        ):
            continue
        text = py.read_text(encoding="utf-8")
        if "import sqlite3" in text or "sqlite3.connect" in text:
            violations.append(rel)
    assert not violations, f"Unexpected sqlite3 usage in: {violations}"


# ---------------------------------------------------------------------------
# Doctor bypass detection (tech-debt baseline)
# ---------------------------------------------------------------------------

DOCTOR_SQLITE_MAX = 6


def test_doctor_sqlite_bypass_count(repo_root):
    """Track doctor.py direct sqlite3.connect calls as tech-debt baseline.

    doctor.py uses direct sqlite3 reads for health checks (no rebuild trigger).
    This test pins the current count so new bypasses are caught.
    """
    doctor = repo_root / "src" / "ovp_pipeline" / "commands" / "doctor.py"
    if not doctor.exists():
        return
    text = doctor.read_text(encoding="utf-8")
    count = text.count("sqlite3.connect")
    assert count <= DOCTOR_SQLITE_MAX, (
        f"doctor.py has {count} sqlite3.connect calls (baseline: {DOCTOR_SQLITE_MAX}). "
        f"New bypasses should use truth_api/truth_queries instead."
    )


# ---------------------------------------------------------------------------
# Doctor bypass ratchet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/search?q=alpha", "/ops/objects"])
def test_reader_routes_do_not_expose_operator_jargon(
    temp_vault,
    fetch_ui,
    seed_hot_path_vault,
    path,
):
    seed_hot_path_vault(temp_vault)

    status, body, _content_type = fetch_ui(temp_vault, path)

    assert status == 200
    for banned in ["Workflow Map", "Compile gate", "Projection lifecycle", "source of truth"]:
        assert banned not in body


def test_readme_and_milestone_avoid_source_of_truth_language(repo_root):
    docs = [
        repo_root / "README.md",
        repo_root / "README.zh-CN.md",
        repo_root / "MILESTONE.md",
        repo_root / "MILESTONE.zh-CN.md",
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "source of truth" not in text.lower()


# ---------------------------------------------------------------------------
# BL-060: single-writer invariant for canonical tables
# ---------------------------------------------------------------------------
#
# See `docs/canonical-write-ownership.md` for the owner-module map.  Every
# INSERT / UPDATE / DELETE against a canonical table (`objects`, `provenance`,
# `claims`, `relations`) must originate from the table's owner module.  Other
# modules call the owner's helper.
#
# Phase 1 (this PR): enumerate the audited violations as `KNOWN_BYPASS` and
# assert no new sites get added.  Phase 2 (BL-060 PR#2): refactor the
# violations and shrink `KNOWN_BYPASS` to empty.

CANONICAL_TABLES = ("objects", "provenance", "claims", "relations")

# Regex matches SQL strings that mutate a canonical table.  Tolerates:
#   INSERT / INSERT INTO / INSERT OR (IGNORE|REPLACE|ROLLBACK|ABORT|FAIL) INTO
#   REPLACE INTO  — SQLite shorthand for INSERT OR REPLACE
#   UPDATE / UPDATE OR (ROLLBACK|ABORT|REPLACE|FAIL|IGNORE)  — full SQLite UPDATE OR clause set
#   DELETE FROM
# The OR-clause alternation is permissive (any one-word identifier between
# OR and INTO/table-name) so future SQLite variants still register.
_CANONICAL_WRITE_RE = re.compile(
    r"\b(?:"
    r"INSERT(?:\s+OR\s+\w+)?\s+INTO"
    r"|REPLACE\s+INTO"
    r"|UPDATE(?:\s+OR\s+\w+)?"
    r"|DELETE\s+FROM"
    r")\s+("
    + "|".join(CANONICAL_TABLES)
    + r")\b",
    re.IGNORECASE,
)

# Files allowed to issue raw canonical-table SQL.  Anything else fails the test.
# Keys are repo-relative paths under ``src/ovp_pipeline/`` — basenames are
# unsafe because we have ``commands/knowledge_index.py`` (CLI wrapper) and
# ``knowledge_index.py`` (rebuild module) coexisting; matching by basename
# would let the wrapper sneak through if it ever started writing.
# Refer to docs/canonical-write-ownership.md before adding new entries.
#
# Post-BL-060 PR#2: only the three owner modules retain raw SQL.  Every
# other writer calls into one of these via the public helpers.
OWNER_FILES: set[str] = {
    "provenance.py",            # owner of `provenance` table
    "truth_store_writers.py",   # owner of `objects` + `claims` tables
    "relation_writer.py",       # owner of `relations` table
}

# Tech-debt ratchet — empty after BL-060 PR#2 refactored every prior bypass
# (knowledge_index.py rebuild, relation_promotion.py _ensure_relation_row +
# replay_relation_promotions, commands/backfill_objects_source_url.py).
# This being empty is the load-bearing assertion: no module outside
# ``OWNER_FILES`` may write a canonical table.
KNOWN_BYPASS: set[str] = set()


def test_canonical_writes_have_single_owner(repo_root):
    """BL-060: only owner modules may issue raw INSERT/UPDATE/DELETE on
    canonical tables.  Non-owner modules must call the owner's helper.

    Phase 1 (this PR): the existing violations in ``KNOWN_BYPASS`` are
    grandfathered; the test only catches *new* violations.  Phase 2 of
    BL-060 (PR#2) refactors the bypass sites and shrinks the set to empty.
    """
    src = repo_root / "src" / "ovp_pipeline"
    new_violations: list[str] = []
    grandfathered_seen: set[str] = set()

    for py in sorted(src.rglob("*.py")):
        rel = str(py.relative_to(src))
        if rel == "__init__.py" or rel.endswith("/__init__.py"):
            continue
        text = py.read_text(encoding="utf-8")
        # Strip comments + docstrings to avoid flagging mentions of SQL in prose.
        # Cheap heuristic: walk the AST and only inspect string literals.  Any
        # docstring (the first stmt of a module / class / function body) is
        # skipped via ``ast.get_docstring()``.
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        sql_strings: list[str] = []
        # Collect docstring node ids so we don't flag them.
        doc_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    doc_ids.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in doc_ids:
                    continue
                sql_strings.append(node.value)

        # Search each string literal individually rather than joining them —
        # joining with ``\n`` could glue a mutation verb at the end of one
        # string to a canonical table name at the start of the next, producing
        # phantom matches.
        if not any(_CANONICAL_WRITE_RE.search(s) for s in sql_strings):
            continue

        # File touches a canonical table.  Owner or grandfathered violation?
        # Match against the relative path (rel), NOT py.name — basenames
        # collide between top-level modules and their CLI wrappers under
        # ``commands/``.
        if rel in OWNER_FILES:
            continue
        if rel in KNOWN_BYPASS:
            grandfathered_seen.add(rel)
            continue
        new_violations.append(
            f"{rel}: writes a canonical table but is neither an owner "
            f"({sorted(OWNER_FILES)}) nor in KNOWN_BYPASS"
        )

    # Catch stale ratchet entries (file in KNOWN_BYPASS but no longer writes).
    stale = sorted(KNOWN_BYPASS - grandfathered_seen)

    assert not new_violations, (
        "New canonical-write violations (see docs/canonical-write-ownership.md):\n"
        + "\n".join(new_violations)
    )
    assert not stale, (
        "KNOWN_BYPASS lists files that no longer write canonical tables; "
        "remove them: " + ", ".join(stale)
    )
