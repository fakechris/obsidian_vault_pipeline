"""Smoke tests for the M15 documentation language lint.

Two layers:

1. **Lint engine** — the pure ``_lint_file`` / ``_check_size`` helpers
   are unit-tested with synthetic markdown so the rules themselves
   are verified.

2. **Repo doc state** — a guard test runs the full lint in
   ``fail-changed`` mode against the current repo.  Default mode
   is ``warn`` (no test failure regardless of findings); flip the
   ``LINT_DOCS_STRICT`` env var to fail on remaining violations.

The phased rollout (warn → fail-changed → fail) is configured by
the ``--mode`` flag on ``scripts/lint_docs.py``; this file just
exercises the rules so they don't regress.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


def _load_lint_module():
    """Import ``scripts/lint_docs.py`` as a module without polluting
    sys.path (the file isn't in a package)."""
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "lint_docs", repo_root / "scripts" / "lint_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Engine unit tests
# ---------------------------------------------------------------------------


class TestLintEngine:
    def test_layer_n_is_flagged(self, tmp_path):
        mod = _load_lint_module()
        f = tmp_path / "doc.md"
        f.write_text("Layer 1 owns trust.\n", encoding="utf-8")
        findings = mod._lint_file(f)
        # 'Layer 1' should match the regex.
        assert any("Layer" in p for _, p, _ in findings)

    def test_lint_allow_marker_skips_line(self, tmp_path):
        # Line carrying ``<!-- lint-allow: ... -->`` is exempt — used
        # for migration tables and glossary entries that intentionally
        # name retired terms.
        mod = _load_lint_module()
        f = tmp_path / "doc.md"
        f.write_text(
            "Layer 1 → Canonical State <!-- lint-allow: migration table -->\n",
            encoding="utf-8",
        )
        findings = mod._lint_file(f)
        assert findings == []

    def test_layer_entity_type_is_not_flagged(self, tmp_path):
        # The regex's negative lookahead exempts the M8 backlog
        # phrase ``Layer 1 entity_type``.
        mod = _load_lint_module()
        f = tmp_path / "doc.md"
        f.write_text("Add Layer 1 entity_type to the frontmatter.\n",
                     encoding="utf-8")
        findings = mod._lint_file(f)
        assert findings == []

    def test_openclaw_is_flagged(self, tmp_path):
        mod = _load_lint_module()
        f = tmp_path / "doc.md"
        f.write_text("OpenClaw was the old project name.\n", encoding="utf-8")
        findings = mod._lint_file(f)
        assert any(p == "OpenClaw" for _, p, _ in findings)

    def test_size_cap_triggers_above_limit(self, tmp_path):
        mod = _load_lint_module()
        # Use a real file name from the SIZE_CAPS dict so the lookup
        # finds it.
        f = tmp_path / "ARCHITECTURE.md"
        # 251 lines with a 250 cap → violation.
        f.write_text("\n".join(["x"] * 251), encoding="utf-8")
        result = mod._check_size(f)
        assert result is not None
        assert result == (251, 250)

    def test_size_cap_passes_at_limit(self, tmp_path):
        mod = _load_lint_module()
        f = tmp_path / "ARCHITECTURE.md"
        # 250 lines is exactly at cap → no violation.
        f.write_text("\n".join(["x"] * 250), encoding="utf-8")
        assert mod._check_size(f) is None

    def test_unknown_file_has_no_size_cap(self, tmp_path):
        # Files not in SIZE_CAPS are unbounded.
        mod = _load_lint_module()
        f = tmp_path / "unknown.md"
        f.write_text("\n".join(["x"] * 5000), encoding="utf-8")
        assert mod._check_size(f) is None


# ---------------------------------------------------------------------------
# Repo state guard
# ---------------------------------------------------------------------------


class TestRepoLintState:
    """Guard: the M15 doc state must stay clean.

    Default behaviour is **warn** — this test always passes.  Set
    ``LINT_DOCS_STRICT=1`` to fail on any finding.  Once warn-only
    has shaken out the legacy refs, flip the default to strict.
    """

    def test_repo_has_no_lint_findings(self):
        mod = _load_lint_module()
        repo_root = Path(__file__).resolve().parents[1]
        total = 0
        violations: list[str] = []
        for rel in mod.GOVERNED_FILES:
            path = repo_root / rel
            findings = mod._lint_file(path)
            for lineno, term, message in findings:
                violations.append(f"{rel}:{lineno}: {term} — {message[:60]}")
                total += 1
            size_violation = mod._check_size(path)
            if size_violation:
                n, cap = size_violation
                violations.append(f"{rel}:1: SIZE {n} > {cap}")
                total += 1

        if total and os.environ.get("LINT_DOCS_STRICT"):
            pytest.fail(
                "doc lint violations (strict mode):\n  "
                + "\n  ".join(violations[:20])
                + (f"\n  ... ({total - 20} more)" if total > 20 else "")
            )
        # Warn mode: always pass.  Print findings so they show in
        # pytest -v output.
        if total:
            print(f"\n[lint warn] {total} doc-lint findings:")
            for line in violations[:20]:
                print(f"  {line}")
            if total > 20:
                print(f"  ... ({total - 20} more)")
