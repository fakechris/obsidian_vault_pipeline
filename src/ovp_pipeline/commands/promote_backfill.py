"""``ovp-promote-backfill`` — Phase 38.C historical backfill.

Reads ``60-Logs/pipeline.jsonl`` for ``evergreen_auto_promoted`` and
``evergreen_created`` events and writes a promotion-backlink block into each
source MD that hasn't been touched yet.

Run once after deploying Phase 38.C; subsequent promotions write the block
automatically via the hook in ``auto_evergreen_extractor.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from ..promotion_backlinks import upsert_promotions_in_file
from ..runtime import VaultLayout, resolve_vault_dir


_EVENT_TYPES = {"evergreen_auto_promoted", "evergreen_created"}


def _iter_promotion_events(layout: VaultLayout):
    log_path = layout.logs_dir / "pipeline.jsonl"
    if not log_path.exists():
        return
    with log_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                evt = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            evt_type = evt.get("event") or evt.get("event_type")
            if evt_type not in _EVENT_TYPES:
                continue
            data = evt.get("data") if isinstance(evt.get("data"), dict) else evt
            source = data.get("source")
            concept = data.get("concept")
            if not source or not concept:
                continue
            yield str(source), str(concept)


def _resolve_source_path(vault_dir: Path, source_name: str) -> Path | None:
    """Source field is a basename; locate the actual MD file in the vault."""
    if "/" in source_name:
        candidate = vault_dir / source_name
        if candidate.is_file():
            return candidate
    matches = list(vault_dir.rglob(source_name))
    matches = [m for m in matches if m.is_file() and m.suffix == ".md"]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    # Prefer a path under 20-Areas / 50-Inbox over archive/templates.
    preferred = [
        m for m in matches if any(p in m.parts for p in ("20-Areas", "50-Inbox", "30-Projects"))
    ]
    if len(preferred) == 1:
        return preferred[0]
    if preferred:
        return preferred[0]
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ovp-promote-backfill")
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Print plan, do not write")
    parser.add_argument(
        "--verbose", action="store_true", help="Print every (source, concept) pair"
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)

    pairs: dict[str, set[str]] = defaultdict(set)
    for source_name, concept in _iter_promotion_events(layout):
        pairs[source_name].add(concept)

    if not pairs:
        print("No promotion events found in pipeline.jsonl.")
        return 0

    print(f"Found promotion events for {len(pairs)} source file(s).")

    written = 0
    skipped = 0
    missing = 0
    candidates = 0
    for source_name, slugs in sorted(pairs.items()):
        path = _resolve_source_path(vault_dir, source_name)
        if not path:
            missing += 1
            if args.verbose:
                print(f"  ! source not found: {source_name}")
            continue

        slugs_sorted = sorted(slugs)
        if args.dry_run:
            candidates += 1
            print(f"  [DRY] {path.relative_to(vault_dir)} ← {len(slugs_sorted)} slug(s)")
            continue

        try:
            changed = upsert_promotions_in_file(path, slugs_sorted)
        except Exception as exc:
            print(f"  ! {path}: {exc}", file=sys.stderr)
            continue

        if changed:
            written += 1
            if args.verbose:
                print(f"  + {path.relative_to(vault_dir)} ({len(slugs_sorted)} slug(s))")
        else:
            skipped += 1

    head = (
        f"Candidates: {candidates}"
        if args.dry_run
        else f"Wrote: {written}, already-current: {skipped}"
    )
    print(f"\n{head}, missing-source: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
