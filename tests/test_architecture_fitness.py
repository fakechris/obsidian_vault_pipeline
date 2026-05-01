from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# File size limits
# ---------------------------------------------------------------------------

MAX_MODULE_LINES = 3000
KNOWN_OVERSIZED = {
    "truth_api.py": 7000,
    "commands/_ui_renderers.py": 5000,
    "ui/view_models.py": 5000,
    "unified_pipeline_enhanced.py": 3500,
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
    },
    "L2_derived": {
        "truth_store",
        "knowledge_index",
        "graph",
        "graph_cli",
        "lint_checker",
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
    },
}

FORBIDDEN_DEPS = [
    ("L1_canonical", "L4_governance"),
    ("L1_canonical", "L3_assembly"),
    ("L1_canonical", "L2_derived"),
]

EXCLUDED_FROM_LAYER_CHECK = {
    "truth_api",
    "commands/ui_server",
}


def _module_to_layer(module_stem: str) -> str | None:
    for layer, members in LAYER_MAP.items():
        if module_stem in members:
            return layer
    return None


def _extract_ovp_imports(filepath: Path) -> list[str]:
    """Return list of ovp_pipeline sub-module stems imported by filepath."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("ovp_pipeline."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    imports.append(parts[1])
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
    "commands/link_suggest",
    "commands/repair",
    "commands/reuse_report",
    "commands/ui_server",
    "commands/_ui_renderers",
    "commands/working_memory",
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
    "ui/view_models",
}


def test_no_direct_sqlite_in_non_data_modules(repo_root):
    """Only data-layer modules may use sqlite3 directly."""
    src = repo_root / "src" / "ovp_pipeline"
    violations = []
    for py in sorted(src.rglob("*.py")):
        rel = str(py.relative_to(src)).replace(".py", "")
        if rel.endswith("/__init__") or rel == "__init__":
            continue
        if any(rel == allowed or rel.endswith(f"/{allowed}") for allowed in SQLITE_ALLOWED_MODULES):
            continue
        text = py.read_text(encoding="utf-8")
        if "import sqlite3" in text or "sqlite3.connect" in text:
            violations.append(rel)
    assert not violations, f"Unexpected sqlite3 usage in: {violations}"


@pytest.mark.parametrize("path", ["/", "/search?q=alpha", "/objects"])
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
