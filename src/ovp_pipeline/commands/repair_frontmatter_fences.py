"""ovp-repair-frontmatter-fences — Strip ``` ```yaml ``` wrapping
around YAML frontmatter on markdown files.

Some deep-dive generators emit frontmatter inside a fenced code block::

    ```yaml
    ---
    title: ...
    ---
    ```

    body...

Obsidian and the OVP knowledge_index parser both require the file to
start with ``---``, so the fenced version silently loses every frontmatter
field except those filename-derived (title falls back to filename, but
tags/source/author/date/aliases all become empty).

This command walks ``--vault-dir`` and rewrites every affected file so
its frontmatter is at the very start of the file, then a blank line,
then the body.  Idempotent: files without the fence wrap are untouched.

Default is ``--dry-run`` — pass ``--write`` to apply.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Match a leading "```<lang>?\n---\n...---\n```\n" block where <lang>
# is optional and typically ``yaml`` / ``markdown`` / empty.  DOTALL on
# the first capture lets ``...`` span multiple frontmatter lines; the
# lazy match bounded by ``\n---\n```\n`` ensures we only consume the
# wrapper, not any other code blocks in the body.
_WRAP_RE = re.compile(
    r"\A```[a-zA-Z]*\s*\n(---\s*\n.*?\n---\s*\n)```\s*\n",
    re.DOTALL,
)
_LEADING_FENCE_RE = re.compile(r"\A```[a-zA-Z]*\s*\n")


@dataclass
class RepairReport:
    files_scanned: int = 0
    files_repaired: int = 0
    files_skipped_no_wrap: int = 0
    files_skipped_unsafe: int = 0
    repaired_paths: list[Path] = field(default_factory=list)
    unsafe_paths: list[Path] = field(default_factory=list)


def detect_and_strip(content: str) -> tuple[str, bool]:
    """Return (new_content, was_repaired).

    Idempotent: if the file does not start with ``\\`\\`\\`yaml`` the original
    content is returned with ``was_repaired=False``.
    """
    m = _WRAP_RE.match(content)
    if not m:
        return content, False
    inner = m.group(1)  # the "---\n...\n---\n" frontmatter without fences
    rest = content[m.end():]
    return inner + rest, True


_LEADING_FENCE_THEN_FM_RE = re.compile(
    r"\A```[a-zA-Z]*\s*\n(---\s*\n.*?\n---\s*\n)",
    re.DOTALL,
)


def detect_and_strip_aggressive(content: str) -> tuple[str, bool]:
    """Aggressive variant for the open-fence cases.

    If the file starts with ``\\`\\`\\`<lang>\\n---\\n...\\n---\\n`` but the
    closing ``\\`\\`\\`\\n`` is missing or far away (the LLM wrote an open
    fence and forgot to close it), strip only the leading fence line.
    The frontmatter then starts at byte 0 and Obsidian / KG parse it.

    A stray closing ``\\`\\`\\`\\n`` later in the body becomes an orphan
    fence; Obsidian renders that as plain text, so we don't damage the
    document.

    Only safe to use on LLM-generated files that we know shouldn't
    legitimately start with a code block (e.g. ``*_深度解读.md``).
    """
    m = _LEADING_FENCE_THEN_FM_RE.match(content)
    if not m:
        return content, False
    # Strip just the leading "```<lang>\n" line, keep everything else.
    leading_fence_line_end = content.index("\n") + 1
    return content[leading_fence_line_end:], True


def is_safe_to_repair(content: str) -> bool:
    """A file is safe to auto-repair only if the wrap match is unambiguous.

    Specifically the file must:
      * start with a leading ``\\`\\`\\`<lang>?`` fence (already confirmed)
      * contain a balanced ``---\\n...\\n---\\n\\`\\`\\`\\n`` inside the wrap
    Files with an open fence but no immediate close after the frontmatter
    block are flagged unsafe (the frontmatter would otherwise leak into a
    code block in the body).
    """
    if not _LEADING_FENCE_RE.match(content):
        return True  # not our pattern, treated as no-op
    return bool(_WRAP_RE.match(content))


def repair(
    vault_dir: Path,
    *,
    glob_patterns: tuple[str, ...] = (
        "20-Areas/**/Topics/**/*_深度解读.md",
        "10-Knowledge/Evergreen/*.md",
        "50-Inbox/03-Processed/**/*.md",
    ),
    write: bool = False,
    aggressive: bool = False,
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

            if not _LEADING_FENCE_RE.match(content):
                report.files_skipped_no_wrap += 1
                continue

            if is_safe_to_repair(content):
                new_content, repaired = detect_and_strip(content)
            elif aggressive:
                # Only attempt for files matching deep-dive pattern
                if "_深度解读.md" not in f.name:
                    report.files_skipped_unsafe += 1
                    report.unsafe_paths.append(f)
                    continue
                new_content, repaired = detect_and_strip_aggressive(content)
                if not repaired:
                    report.files_skipped_unsafe += 1
                    report.unsafe_paths.append(f)
                    continue
            else:
                report.files_skipped_unsafe += 1
                report.unsafe_paths.append(f)
                continue

            if repaired:
                report.files_repaired += 1
                report.repaired_paths.append(f)
                if write:
                    f.write_text(new_content, encoding="utf-8")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strip ```yaml fence wrapping around frontmatter",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--write", action="store_true",
        help="Apply repairs in place (default: dry-run)",
    )
    parser.add_argument(
        "--show", type=int, default=20,
        help="Show first N repaired paths in summary (default 20)",
    )
    parser.add_argument(
        "--aggressive", action="store_true",
        help="Also repair open-fence cases (no closing ``` after frontmatter) "
             "by stripping just the leading fence line.  Only applies to "
             "*_深度解读.md files; other patterns stay safe-only.",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    report = repair(vault, write=args.write, aggressive=args.aggressive)
    print(f"Scanned: {report.files_scanned}")
    print(f"To repair: {report.files_repaired}")
    print(f"Skipped (no wrap): {report.files_skipped_no_wrap}")
    print(f"Skipped (unsafe / ambiguous): {report.files_skipped_unsafe}")

    if report.repaired_paths:
        print(f"\nFirst {min(args.show, len(report.repaired_paths))} repair targets:")
        for p in report.repaired_paths[:args.show]:
            try:
                rel = p.relative_to(vault)
            except ValueError:
                rel = p
            print(f"  {rel}")

    if report.unsafe_paths:
        print(f"\n⚠ Unsafe (need manual review): {len(report.unsafe_paths)}")
        for p in report.unsafe_paths[:args.show]:
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
