"""Guardrail 1: prevent regression of the silent-ImportError-fallback pattern.

The May 2026 ``llm_client.py`` outage was a textbook silent ImportError
fallback: a module didn't exist on disk, ``try: from .llm_client import
get_litellm_client; except Exception: pass`` swallowed the error, and
the calling code defaulted to ``None`` for two months without anyone
noticing entity_extract had stopped calling the LLM.

This fitness test scans the ``ovp_pipeline`` source tree and asserts
that no new ``try: <import>; ...; except (Exception | ImportError):
pass`` patterns appear.  The legacy violations that exist today are
explicitly listed so they don't grow.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# -- Pre-existing violations.  This list MUST shrink, never grow.
# Each entry is (relative_path_under_src/ovp_pipeline, line_number).
# Most of these are optional ``from dotenv import load_dotenv`` style
# fallbacks where dotenv isn't a hard dependency.
LEGACY_SILENT_IMPORTS = {
    ("auto_evergreen_extractor.py", 153),      # dotenv optional (BL-054 added helpers above it)
    ("auto_article_processor.py", 113),         # dotenv optional
    ("auto_github_processor.py", 82),           # dotenv optional
    ("auto_moc_updater.py", 51),                # dotenv optional
    ("auto_paper_processor.py", 86),            # dotenv optional
    ("clippings_processor.py", 51),             # dotenv optional
    ("batch_quality_checker.py", 64),           # dotenv optional
    ("unified_pipeline_enhanced.py", 244),      # importlib.metadata version probe
    # General except-Exception-pass after import (broader, more dangerous).
    # We may tighten these in follow-up PRs but keep them here so the
    # ratchet test doesn't break.
    ("image_downloader.py", 213),
    ("promote_candidates.py", 332),
    ("query_tool.py", 157),
    ("commands/backfill_entities.py", 214),     # pipeline.jsonl logger optional
    ("autopilot/daemon.py", 382),
}


class _Finder(ast.NodeVisitor):
    """Locate ``try: <import>; ...; except: pass`` patterns."""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Try(self, node: ast.Try) -> None:
        # Body contains an import?
        has_import = any(
            isinstance(s, (ast.Import, ast.ImportFrom)) for s in node.body
        )
        if has_import:
            for handler in node.handlers:
                if (
                    handler.body
                    and len(handler.body) == 1
                    and isinstance(handler.body[0], ast.Pass)
                ):
                    self.hits.append(node.lineno)
        self.generic_visit(node)


def _scan_silent_imports(src_dir: Path) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for f in src_dir.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        finder = _Finder()
        finder.visit(tree)
        if finder.hits:
            rel = str(f.relative_to(src_dir))
            for line in finder.hits:
                found.add((rel, line))
    return found


def test_no_new_silent_import_fallbacks(repo_root):
    """Adding a new ``try: import; except: pass`` that isn't on the
    legacy allow-list will fail this test.

    To get rid of one of these, change the handler from ``pass`` to
    something that surfaces the error (re-raise, log+fallback, or
    explicit no-op stub with logging) — then remove the entry from
    ``LEGACY_SILENT_IMPORTS``.
    """
    src = repo_root / "src" / "ovp_pipeline"
    found = _scan_silent_imports(src)

    new_violations = found - LEGACY_SILENT_IMPORTS
    if new_violations:
        formatted = "\n  ".join(f"{path}:{line}" for path, line in sorted(new_violations))
        pytest.fail(
            "New silent ImportError/Exception fallback(s) detected. "
            "Replace ``except: pass`` with a logged fallback or explicit re-raise:\n  "
            + formatted
        )


def test_legacy_list_only_shrinks(repo_root):
    """If a previously-listed legacy site has been cleaned up, drop it
    from ``LEGACY_SILENT_IMPORTS`` so the ratchet stays tight.

    This guards against the list staying stale after refactors that
    remove the offending pattern.
    """
    src = repo_root / "src" / "ovp_pipeline"
    found = _scan_silent_imports(src)

    stale = LEGACY_SILENT_IMPORTS - found
    if stale:
        formatted = "\n  ".join(f"{path}:{line}" for path, line in sorted(stale))
        pytest.fail(
            "Legacy silent-import entries no longer match the source. "
            "Remove these from LEGACY_SILENT_IMPORTS in tests/test_no_silent_imports.py:\n  "
            + formatted
        )
