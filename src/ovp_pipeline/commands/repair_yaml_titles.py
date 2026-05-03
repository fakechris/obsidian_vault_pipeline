"""ovp-repair-yaml-titles — Quote unquoted YAML title/source values.

When LLM-generated frontmatter contains unquoted strings with colons or
unescaped quotes, ``yaml.safe_load`` rejects the whole frontmatter::

    title: Context Engineering: Lessons from Manus       # ← colon in unquoted scalar
    title: "Agentic-AI实现从"AI回答"到"AI执行"的核心范式转变"   # ← unescaped " inside ""

Both cases produce ``YAMLError: mapping values are not allowed here`` /
``while parsing a quoted scalar``.  The fix is mechanical: rewrite the
problematic lines to a safely-quoted form.

The repair is line-based (does not roundtrip through a YAML loader)
because the loader has already failed by definition.  Idempotent: lines
that already parse stay untouched.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Frontmatter regex with capture groups split — the shared
# ``layer_schemas.FRONTMATTER_BLOCK_RE`` collapses the delimiters into
# the boundary; here we need the opener / body / closer separately so
# we can splice repaired body back into the exact original framing.
# Tolerates a closing ``---`` at end-of-file (no trailing newline).
_FRONTMATTER_RE = re.compile(r"\A(---\s*\n)(.*?)(\n---\s*(?:\n|\Z))", re.DOTALL)
# Lines like "title: <some value>" where <some value> isn't already quoted.
_KEY_VALUE_RE = re.compile(r"^([a-zA-Z_][\w\-]*):\s*(.*?)\s*$")


def _yaml_safely_quote(value: str) -> str:
    """Wrap ``value`` in double quotes, escaping any ``"`` inside."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _frontmatter_parses(block: str) -> bool:
    try:
        yaml.safe_load(block)
        return True
    except yaml.YAMLError:
        return False


def _try_repair_block(block: str) -> tuple[str, bool]:
    """Try to make ``block`` (without --- delimiters) parseable.

    Strategy: re-quote any top-level scalar value that isn't already
    quoted.  If the result parses, return it; otherwise give up.
    """
    if _frontmatter_parses(block):
        return block, False

    out_lines: list[str] = []
    changed = False
    for line in block.split("\n"):
        m = _KEY_VALUE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        key, val = m.group(1), m.group(2)
        if not val:
            out_lines.append(line)
            continue
        # If the value already passes a single-line YAML parse, it's fine.
        if _frontmatter_parses(f"{key}: {val}"):
            out_lines.append(line)
            continue
        # Otherwise re-quote.  For values that already start+end with "
        # (the L3 nested-quote case), strip the outer quotes first so
        # _yaml_safely_quote produces a clean ``"...escaped...inside..."``.
        normalized = val
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            normalized = val[1:-1]
        out_lines.append(f"{key}: {_yaml_safely_quote(normalized)}")
        changed = True

    if not changed:
        return block, False

    candidate = "\n".join(out_lines)
    if _frontmatter_parses(candidate):
        return candidate, True
    return block, False


@dataclass
class RepairReport:
    files_scanned: int = 0
    files_already_ok: int = 0
    files_repaired: int = 0
    files_unfixable: int = 0
    repaired_paths: list[Path] = field(default_factory=list)
    unfixable_paths: list[Path] = field(default_factory=list)


def repair(
    vault_dir: Path,
    *,
    glob_patterns: tuple[str, ...] = (
        "20-Areas/**/Topics/**/*_深度解读.md",
        "10-Knowledge/Evergreen/*.md",
        "10-Knowledge/Entity/*.md",
        "50-Inbox/03-Processed/**/*.md",
    ),
    write: bool = False,
) -> RepairReport:
    report = RepairReport()
    seen: set[Path] = set()
    for pat in glob_patterns:
        for f in vault_dir.glob(pat):
            if not f.is_file():
                continue
            resolved = f.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            report.files_scanned += 1

            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            m = _FRONTMATTER_RE.match(content)
            if m is None:
                # No frontmatter → not our concern (handled by repair_frontmatter_fences).
                continue

            opener, body, closer = m.group(1), m.group(2), m.group(3)
            if _frontmatter_parses(body):
                report.files_already_ok += 1
                continue

            new_body, repaired = _try_repair_block(body)
            if repaired:
                report.files_repaired += 1
                report.repaired_paths.append(f)
                if write:
                    rest = content[m.end():]
                    f.write_text(opener + new_body + closer + rest, encoding="utf-8")
            else:
                report.files_unfixable += 1
                report.unfixable_paths.append(f)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Quote unquoted YAML title/source values to make frontmatter parse",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true",
                        help="Apply repairs in place (default: dry-run)")
    parser.add_argument("--show", type=int, default=20)
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    report = repair(vault, write=args.write)
    print(f"Scanned: {report.files_scanned}")
    print(f"Already OK: {report.files_already_ok}")
    print(f"To repair: {report.files_repaired}")
    print(f"Unfixable (need manual): {report.files_unfixable}")

    if report.repaired_paths:
        print(f"\nFirst {min(args.show, len(report.repaired_paths))} repair targets:")
        for p in report.repaired_paths[:args.show]:
            try:
                rel = p.relative_to(vault)
            except ValueError:
                rel = p
            print(f"  {rel}")

    if report.unfixable_paths:
        print(f"\nUnfixable: {len(report.unfixable_paths)}")
        for p in report.unfixable_paths[:args.show]:
            try:
                rel = p.relative_to(vault)
            except ValueError:
                rel = p
            print(f"  {rel}")

    if not args.write:
        print("\n[dry-run] no files written. Pass --write to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
