"""ovp-tag-legacy-evergreens — one-shot frontmatter migration (BL-058).

Walks every evergreen file in ``10-Knowledge/Evergreen/`` and tags
those that lack ``extraction_prompt_version`` with::

    extraction_prompt_version: v1
    legacy_unverified: true
    legacy_tagged_at: <iso>

The two markers are how downstream tools (reader UI badges, crystal
scoring weights, fidelity replay) tell apart "absorbed under the old
prompt that may have abstraction-inflated specifics" from "absorbed
under v2 with source_anchor + specificity guarantees".

Why a separate command, not part of the absorb refactor:

  * Touching ~7000 files writes a lot of audit log entries; we want to
    do that in one explicit step we can run + verify, not at every
    incremental run start.
  * Idempotent: re-running detects already-tagged files and skips them,
    so accidentally running twice is harmless.
  * Reversible: ``--untag`` mode removes the three added fields so we
    can roll back if the legacy classification turns out to be wrong
    (e.g. if some files were already v2 from a forgotten experiment).

Pre-flight check by default; pass ``--write`` to actually mutate files.

Output manifest at ``60-Logs/legacy-tag/<run-id>/manifest.json`` lists
every file the command touched (in either dry-run or write mode).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir


_MARKER_PROMPT_VERSION = "extraction_prompt_version"
_MARKER_LEGACY = "legacy_unverified"
_MARKER_TAGGED_AT = "legacy_tagged_at"

# Lines we INSERT for legacy classification.  Each one stays on its
# own line so the diff is readable.
_TAG_LINES_TEMPLATE = (
    f"{_MARKER_PROMPT_VERSION}: v1\n"
    f"{_MARKER_LEGACY}: true\n"
    f"{_MARKER_TAGGED_AT}: \"{{tagged_at}}\"\n"
)


@dataclass
class FileResult:
    path: Path
    action: str  # "tagged" | "skipped_already_tagged" | "skipped_v2" | "skipped_no_frontmatter" | "untagged"
    reason: str = ""


# ---------------------------------------------------------------------------
# Frontmatter helpers — same lightweight regex approach the rest of the
# codebase uses, no full YAML parser needed for these markers.
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str, str] | None:
    """Return ``(opening_fence, frontmatter_block, rest)`` or None.

    ``opening_fence`` is ``"---\\n"`` (we always re-emit that).
    ``frontmatter_block`` is everything between the fences, NOT
    including either fence.
    ``rest`` is everything after the closing fence, including the
    closing ``---\\n`` itself so the caller can emit it back unchanged.
    """
    if not text.startswith("---"):
        return None
    # Locate the closing fence — must be on its own line
    match = re.search(r"\n---\s*(?:\n|$)", text[3:])
    if not match:
        return None
    fm_start = text.find("\n", 0) + 1  # first char after opening fence's newline
    fm_end = 3 + match.start()           # position of "\n" before closing "---"
    closing_start = 3 + match.start() + 1  # start of "---" closing line
    frontmatter_block = text[fm_start:fm_end + 1]  # include trailing \n
    rest = text[closing_start:]
    return ("---\n", frontmatter_block, rest)


def _has_marker(frontmatter_block: str, key: str) -> bool:
    """True if frontmatter has a top-level key ``key``.

    We only check at line-start to avoid false-matching the marker
    inside a quoted value.
    """
    pattern = rf"(?m)^{re.escape(key)}\s*:"
    return bool(re.search(pattern, frontmatter_block))


def _strip_marker(frontmatter_block: str, key: str) -> str:
    """Remove every line that starts with ``<key>:``."""
    pattern = rf"(?m)^{re.escape(key)}\s*:.*\n?"
    return re.sub(pattern, "", frontmatter_block)


# ---------------------------------------------------------------------------
# Per-file classification + mutation
# ---------------------------------------------------------------------------


def _classify_and_tag(
    path: Path,
    *,
    tagged_at: str,
    write: bool,
) -> FileResult:
    """Decide what to do with one evergreen file.

    Returns a ``FileResult`` describing the action.  When ``write=True``
    the action is also applied to disk.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return FileResult(path, "skipped_no_frontmatter", reason=f"read error: {exc}")

    parts = _split_frontmatter(text)
    if parts is None:
        return FileResult(path, "skipped_no_frontmatter")
    opening, fm_block, rest = parts

    # Already tagged?
    if _has_marker(fm_block, _MARKER_LEGACY):
        return FileResult(path, "skipped_already_tagged")

    # Already v2?  Don't overwrite.
    version_match = re.search(
        rf"(?m)^{re.escape(_MARKER_PROMPT_VERSION)}\s*:\s*(.+)$",
        fm_block,
    )
    if version_match:
        existing = version_match.group(1).strip().strip('"\'')
        if existing == "v2":
            return FileResult(path, "skipped_v2", reason=f"version={existing}")

    if not write:
        return FileResult(path, "tagged", reason="dry_run")

    # Insert the three marker lines at the END of the frontmatter block
    # (before the closing fence).  Preserves all existing fields,
    # creates a minimal diff.
    new_lines = _TAG_LINES_TEMPLATE.format(tagged_at=tagged_at)
    if not fm_block.endswith("\n"):
        fm_block += "\n"
    new_fm = fm_block + new_lines
    new_text = opening + new_fm + rest
    path.write_text(new_text, encoding="utf-8")
    return FileResult(path, "tagged")


def _untag_file(path: Path, *, write: bool) -> FileResult:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return FileResult(path, "skipped_no_frontmatter", reason=f"read error: {exc}")
    parts = _split_frontmatter(text)
    if parts is None:
        return FileResult(path, "skipped_no_frontmatter")
    opening, fm_block, rest = parts
    if not _has_marker(fm_block, _MARKER_LEGACY):
        return FileResult(path, "skipped_already_tagged", reason="no legacy marker")
    if not write:
        return FileResult(path, "untagged", reason="dry_run")
    for key in (_MARKER_LEGACY, _MARKER_TAGGED_AT, _MARKER_PROMPT_VERSION):
        fm_block = _strip_marker(fm_block, key)
    new_text = opening + fm_block + rest
    path.write_text(new_text, encoding="utf-8")
    return FileResult(path, "untagged")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _walk_evergreens(layout: VaultLayout) -> list[Path]:
    """Every ``*.md`` under the Evergreen directory, excluding the
    ``_Candidates/`` subtree (those are pending notes that haven't been
    promoted yet — they don't need legacy tagging)."""
    if not layout.evergreen_dir.exists():
        return []
    return sorted(
        path
        for path in layout.evergreen_dir.rglob("*.md")
        if "_Candidates" not in path.parts
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-tag-legacy-evergreens",
        description=(
            "Tag every existing evergreen as legacy_unverified=true "
            "+ extraction_prompt_version=v1 (BL-058 migration). "
            "Idempotent and reversible."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument(
        "--write", action="store_true",
        help="Mutate files (default: dry-run).",
    )
    parser.add_argument(
        "--untag", action="store_true",
        help="Reverse mode — strip the three legacy markers.",
    )
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = layout.logs_dir / "legacy-tag" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = _walk_evergreens(layout)
    print(f"Scanning {len(paths)} evergreen files under {layout.evergreen_dir} …", file=sys.stderr)

    tagged_at = datetime.now(timezone.utc).isoformat()
    results: list[FileResult] = []
    for path in paths:
        if args.untag:
            result = _untag_file(path, write=args.write)
        else:
            result = _classify_and_tag(path, tagged_at=tagged_at, write=args.write)
        results.append(result)

    # Aggregate
    by_action: dict[str, int] = {}
    for r in results:
        by_action[r.action] = by_action.get(r.action, 0) + 1

    mode = "untag" if args.untag else "tag"
    write_mode = "WRITE" if args.write else "DRY-RUN"
    print(f"\n=== {mode} ({write_mode}) ===", file=sys.stderr)
    print(f"  total files: {len(results)}", file=sys.stderr)
    for action, count in sorted(by_action.items()):
        print(f"  {action:30s} {count}", file=sys.stderr)

    # Manifest
    manifest = {
        "run_id": run_id,
        "mode": mode,
        "write": args.write,
        "tagged_at": tagged_at,
        "vault_dir": str(vault_dir),
        "total_files": len(results),
        "by_action": by_action,
        "files": [
            {
                "path": str(r.path.relative_to(vault_dir)),
                "action": r.action,
                "reason": r.reason,
            }
            for r in results
        ],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nManifest: {manifest_path}", file=sys.stderr)
    if not args.write:
        print(
            f"\n[dry-run] Re-run with --write to apply changes.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
