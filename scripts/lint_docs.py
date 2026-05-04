#!/usr/bin/env python3
"""Documentation language + structure lint.

Three modes:

1. **warn** (default) — print findings to stdout, always exit 0.
   Use during the M15 rollout window so docs don't fail CI while
   the cleanup is in flight.

2. **fail-changed** — exit 1 only if a violation appears on files
   listed by git as modified vs ``--base-ref`` (default ``main``).
   Use after the warn-mode cleanup is done so new commits can't
   reintroduce legacy vocabulary.

3. **fail** — exit 1 on any violation, including pre-existing ones.
   Final state once every doc has been cleaned up.

The list of banned legacy terms is the M15 architecture-language
contract; if you really need a forbidden word in a doc, add an
explicit ``<!-- lint-allow: <term> -->`` marker on the same line
or convert the term to its canonical replacement.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The 6 architecture root terms — these are NEVER banned anywhere.
# Listed so operators can grep for them when extending the lint.
CORE_TERMS = {
    "Source", "Candidate", "Canonical State",
    "Projection", "Access Surface", "Governance",
}

# Files that must obey the architecture-language contract.
GOVERNED_FILES = [
    "README.md",
    "README.zh-CN.md",
    "ARCHITECTURE.md",
    "ARCHITECTURE.zh-CN.md",
    "RUNTIME.md",
    "PACKS.md",
    "PRODUCT_SURFACES.md",
    "GLOSSARY.md",
    "MILESTONE.md",
    "MILESTONE.zh-CN.md",
    "BACKLOG.md",
]

# Terms that the M15 cleanup explicitly retires from architecture
# prose.  Each rule is (pattern, message).  Patterns are case-
# sensitive substrings unless the value is a compiled regex.
BANNED_TERMS: list[tuple[str | re.Pattern, str]] = [
    (re.compile(r"\bLayer [1-4]\b(?! entity_type| entity layer)"),
     "Layer 1/2/3/4 framing is retired — use Canonical State / "
     "Projections / Access Surfaces (Governance is a control plane, "
     "not Layer 4).  See ARCHITECTURE.md migration note."),
    ("OpenClaw",
     "Project name is OVP, not OpenClaw."),
    ("openclaw_pipeline",
     "Module is `ovp_pipeline`."),
    ("OPENCLAW_PACK_MANIFESTS",
     "Env var is `OVP_PACK_MANIFESTS`."),
    ("derived state",
     "Use 'Projection' (capitalized) instead of 'derived state'.  "
     "If unavoidable, add <!-- lint-allow: derived state --> on this line."),
    ("Authority Boundary",
     "Use 'Canonical State Boundary' to match ARCHITECTURE.md vocabulary."),
]

# Some legacy mentions are unavoidable in migration notes.  Skip
# any line containing this marker.
LINT_ALLOW_RE = re.compile(r"<!--\s*lint-allow:")

# Per-file size caps (lines).  ARCHITECTURE.md has the tightest cap
# because the main architecture doc must stay scannable on one screen.
SIZE_CAPS = {
    "ARCHITECTURE.md": 250,
    "ARCHITECTURE.zh-CN.md": 250,
    "PACKS.md": 200,
    "RUNTIME.md": 200,
    "PRODUCT_SURFACES.md": 200,
    "GLOSSARY.md": 400,  # glossary is allowed to grow
}


# ---------------------------------------------------------------------------
# Lint engine
# ---------------------------------------------------------------------------


def _lint_file(path: Path) -> list[tuple[int, str, str]]:
    """Return a list of ``(line_number, term, message)`` violations."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if LINT_ALLOW_RE.search(line):
            continue
        for pattern, message in BANNED_TERMS:
            if isinstance(pattern, re.Pattern):
                if pattern.search(line):
                    findings.append((lineno, pattern.pattern, message))
            else:
                if pattern in line:
                    findings.append((lineno, pattern, message))
    return findings


def _check_size(path: Path) -> tuple[int, int] | None:
    """Return ``(lines, cap)`` if the file exceeds its cap, else None."""
    cap = SIZE_CAPS.get(path.name)
    if cap is None:
        return None
    if not path.exists():
        return None
    n = len(path.read_text(encoding="utf-8").splitlines())
    if n > cap:
        return (n, cap)
    return None


def _changed_files(repo_root: Path, base_ref: str) -> set[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=repo_root, text=True,
        )
    except subprocess.CalledProcessError:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--mode", choices=["warn", "fail-changed", "fail"],
        default="warn",
        help="Enforcement mode (default: warn).",
    )
    parser.add_argument(
        "--base-ref", default="main",
        help="Compare against this ref when --mode=fail-changed.",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(),
        help="Repository root (default: cwd).",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    changed = _changed_files(repo_root, args.base_ref) if args.mode == "fail-changed" else None

    total_findings = 0
    failable_findings = 0
    for rel in GOVERNED_FILES:
        path = repo_root / rel
        # Term lint
        findings = _lint_file(path)
        for lineno, term, message in findings:
            print(f"{rel}:{lineno}: BANNED({term!r}): {message}")
            total_findings += 1
            if args.mode == "fail":
                failable_findings += 1
            elif args.mode == "fail-changed" and rel in (changed or set()):
                failable_findings += 1
        # Size lint
        size_violation = _check_size(path)
        if size_violation:
            n, cap = size_violation
            print(f"{rel}:1: SIZE: {n} lines exceeds cap of {cap}")
            total_findings += 1
            if args.mode == "fail":
                failable_findings += 1
            elif args.mode == "fail-changed" and rel in (changed or set()):
                failable_findings += 1

    print()
    print("=== summary ===")
    print(f"  files governed:   {len(GOVERNED_FILES)}")
    print(f"  total findings:   {total_findings}")
    print(f"  mode:             {args.mode}")
    if args.mode == "warn":
        print("  warn-only — exit 0 regardless of findings")
        return 0
    if args.mode == "fail-changed":
        print(f"  fail-changed — {failable_findings} on files modified vs {args.base_ref}")
        return 1 if failable_findings else 0
    print(f"  fail — {failable_findings} would block")
    return 1 if failable_findings else 0


if __name__ == "__main__":
    sys.exit(main())
