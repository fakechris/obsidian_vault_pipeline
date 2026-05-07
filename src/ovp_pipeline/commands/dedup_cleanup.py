"""ovp-dedup-cleanup — find duplicate raw sources by URL and archive
all but the canonical copy.

Why this CLI exists
-------------------
``source_dedup`` shuts the door at intake going forward, but the
2026-05-06 census found 8 URLs already present in 2-3 raw files
each (Reader re-clipping artefact).  Those duplicates each got a
full absorb pass, producing redundant evergreens with the same
body but different ``absorbed_at`` timestamps.

This CLI surfaces the dups, picks one to keep (the largest by
body size — most likely the most-fully-rendered copy), and moves
the others into ``70-Archive/<date>_dedup-cleanup/`` so the next
``ovp-knowledge-index`` rebuild drops them out of the active
index.

Operator workflow:

    ovp-dedup-cleanup --vault-dir <vault>             # dry-run summary
    ovp-dedup-cleanup --vault-dir <vault> --apply     # archive losers
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir
from ..source_dedup import find_duplicate_groups


def _archive_path(vault_dir: Path) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return vault_dir / "70-Archive" / f"{today}_dedup-cleanup"


def _pick_canonical(paths: list[Path]) -> tuple[Path, list[Path]]:
    """Choose which copy to keep.  Largest body size wins —
    rationale: a partial Reader fetch typically truncates body, so
    a 22 KB clip beats a 4 KB stub of the same URL.  Ties broken
    alphabetically for determinism (so re-running the CLI on the
    same vault picks the same winner)."""
    sized = sorted(paths, key=lambda p: (-p.stat().st_size, str(p)))
    return sized[0], sized[1:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-dedup-cleanup",
        description=(
            "Find raws in 50-Inbox/03-Processed sharing a source URL "
            "and archive all but the largest copy.  Dry-run by default; "
            "pass --apply to actually move files."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true",
                        help="Actually move the duplicates (default: dry-run)")
    args = parser.parse_args(argv)

    vault = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault)

    groups = find_duplicate_groups(vault)
    if not groups:
        print("No duplicate URLs found in 50-Inbox/03-Processed.")
        return 0

    archive_dir = _archive_path(vault)
    n_groups = len(groups)
    n_to_archive = sum(len(paths) - 1 for paths in groups.values())
    n_to_keep = n_groups
    print(f"Found {n_groups} URL group(s) with duplicates "
          f"({n_to_archive} extra files to archive, "
          f"{n_to_keep} canonical files to keep)")
    print()

    # Initialize the audit logger up-front so a partially-completed
    # move loop can't leave us re-stat()-ing files that already
    # moved.  Each archive call writes its own pipeline.jsonl event,
    # captured at move-time.
    if args.apply:
        from ..auto_evergreen_extractor import PipelineLogger
        logger = PipelineLogger(layout.pipeline_log)
    else:
        logger = None

    moved = 0
    for url, paths in sorted(groups.items()):
        canonical, losers = _pick_canonical(paths)
        print(f"  URL: {url[:90]}")
        try:
            keep_size = canonical.stat().st_size
        except OSError:
            keep_size = 0
        print(f"    KEEP   {canonical.relative_to(vault)} "
              f"({keep_size} bytes)")
        for loser in losers:
            target = archive_dir / loser.relative_to(vault / "50-Inbox")
            # Snapshot the size BEFORE the move so the audit event
            # never re-stat()s a freshly-moved file.
            try:
                size = loser.stat().st_size
            except OSError:
                size = 0
            verb = "ARCHIVE" if args.apply else "WOULD ARCHIVE"
            print(f"    {verb}{' ':3s}{loser.relative_to(vault)} "
                  f"({size} bytes) → {target.relative_to(vault)}")
            if args.apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    print("             ⊘ target already exists, deleting source")
                    loser.unlink()
                else:
                    shutil.move(str(loser), str(target))
                moved += 1
                if logger is not None:
                    logger.log("dedup_cleanup_archived", {
                        "url": url,
                        "archived_path": str(target.relative_to(vault)),
                        "kept": str(canonical.relative_to(vault)),
                        "size_bytes": size,
                        "trigger": "ovp-dedup-cleanup",
                    })
        print()

    if args.apply:
        print(f"Moved {moved} duplicate(s) into {archive_dir.relative_to(vault)}")
        return 0
    print("Dry-run only — re-run with --apply to actually move files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
