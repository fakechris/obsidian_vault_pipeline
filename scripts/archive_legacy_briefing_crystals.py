#!/usr/bin/env python3
"""One-shot archiver for the legacy briefing-crystal files that
predate the BL-042 ``community_crystals`` table.

Usage::

    python scripts/archive_legacy_briefing_crystals.py [--vault-dir PATH]

Background:
    Before M13, ``materializers/crystal.py`` produced "briefing
    crystals" (operator briefing snapshots) into
    ``40-Resources/Crystals/crystal-<YYYY-MM-DD>-<sha>.md``.  M13's
    BL-042 introduced a different surface — LLM-synthesized
    *community crystals* — using the same directory but a different
    filename pattern (``<sha>.md``, no date prefix).  The two
    surfaces are conceptually distinct; mixing them in the live
    directory pollutes UI counts and operator understanding.

    This script moves every ``crystal-<YYYY-...>-<sha>.md`` legacy
    file to ``70-Archive/Crystals/Legacy/`` so the live directory
    holds only M13 substrate output.  Idempotent: re-running with
    no remaining legacy files is a no-op.

The script is committed (rather than left as a shell ``mv``) so the
migration is reproducible and auditable in git history.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--vault-dir", type=Path, default=Path.cwd(),
        help="Vault root (default: cwd).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would move; touch nothing.",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    live = vault / "40-Resources" / "Crystals"
    archive = vault / "70-Archive" / "Crystals" / "Legacy"

    candidates = sorted(live.glob("crystal-2026-*-*.md"))
    if not candidates:
        print("no legacy briefing crystals to archive (no-op)")
        return 0

    if not args.dry_run:
        archive.mkdir(parents=True, exist_ok=True)
    moved = 0
    for src in candidates:
        dst = archive / src.name
        if dst.exists():
            print(f"  skip (already archived): {src.name}")
            continue
        if args.dry_run:
            print(f"  would move: {src.name} -> 70-Archive/Crystals/Legacy/")
        else:
            shutil.move(str(src), str(dst))
            print(f"  moved: {src.name} -> 70-Archive/Crystals/Legacy/")
        moved += 1
    verb = "would archive" if args.dry_run else "archived"
    print(f"\n{verb} {moved} legacy briefing crystals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
