"""ovp-merge-identities — collapse twitter_author + github_user into person/organization.

Three modes:

  --dry-run            list every candidate (auto + review), no writes
  (default)            apply auto candidates only, print review queue
  --include-fuzzy      also apply fuzzy candidates (DANGEROUS — review
                       the dry-run output first; mostly here for tests)

The merge is read-only against twitter_author / github_user — they
stay untouched.  We only INSERT/UPDATE rows in the ``entities`` table
where ``entity_type='person'`` or ``'organization'`` (PR-F1).

Re-running is safe: each apply UPSERTs by canonical handle.  A first
``--migrate-existing`` pass also reclassifies pre-PR-F1 ``person``
rows whose linked github_user is actually an organization.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..entities.identity_merge import (
    ORGANIZATION_TYPE,
    PERSON_TYPE,
    apply_merge,
    find_merge_candidates,
    reclassify_persons_to_orgs,
)
from ..entities.store import EntityStore


# Cap on how many reclassification candidates we print inline.  The
# rest are summarized via "and N more"; full list lives in the JSON
# status file (PR-E5).
_MIGRATION_PREVIEW_LIMIT = 20
_FUZZY_PREVIEW_LIMIT = 20
_TOP_K = 10


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge twitter_author + github_user into person entities",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true",
                        help="List candidates without writing person entities")
    parser.add_argument("--include-fuzzy", action="store_true",
                        help="Also apply fuzzy (Levenshtein) candidates — "
                             "review the dry-run output first")
    parser.add_argument("--migrate-existing", action="store_true",
                        help="Before merging, reclassify pre-PR-F1 person "
                             "rows that should be organization (one-shot, "
                             "idempotent).  Default off so a routine refresh "
                             "doesn't surprise anyone.")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    db_path = vault / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        print(f"knowledge.db not found at {db_path}", file=sys.stderr)
        return 2

    store = EntityStore(db_path=db_path)

    if args.migrate_existing:
        # Single source of truth — both real and dry-run go through
        # reclassify_persons_to_orgs so the preview can never disagree
        # with the actual write.
        reclassified, kept, handles = reclassify_persons_to_orgs(
            store, dry_run=args.dry_run,
        )
        verb = "would be reclassified" if args.dry_run else "person → organization"
        print(f"PR-F1 migration: {reclassified} {verb} ({kept} unchanged)")
        for handle in handles[:_MIGRATION_PREVIEW_LIMIT]:
            print(f"  person → organization: {handle}")
        if len(handles) > _MIGRATION_PREVIEW_LIMIT:
            print(f"  ... and {len(handles) - _MIGRATION_PREVIEW_LIMIT} more")
        print()

    candidates = find_merge_candidates(store)

    by_method = {"self_reported": 0, "exact_handle": 0, "fuzzy": 0}
    for c in candidates:
        by_method[c.method] += 1

    print(f"vault: {vault}")
    print(f"db:    {db_path}")
    print(f"candidates discovered: {len(candidates)}")
    print(f"  self_reported (auto):  {by_method['self_reported']:>4}")
    print(f"  exact_handle (review): {by_method['exact_handle']:>4}")
    print(f"  fuzzy (review):        {by_method['fuzzy']:>4}")
    print()

    if args.dry_run:
        print("=== self_reported (would auto-apply) ===")
        for c in candidates:
            if c.method == "self_reported":
                print(f"  {c.confidence:.2f}  github:{c.github_login:<25} "
                      f"↔ twitter:@{c.twitter_handle}")
        print()
        print("=== exact_handle (review queue) ===")
        for c in candidates:
            if c.method == "exact_handle":
                print(f"  {c.confidence:.2f}  github:{c.github_login:<25} "
                      f"↔ twitter:@{c.twitter_handle}")
        print()
        print(f"=== fuzzy (review queue, top {_FUZZY_PREVIEW_LIMIT}) ===")
        fuzzy = [c for c in candidates if c.method == "fuzzy"]
        fuzzy.sort(key=lambda c: -c.confidence)
        for c in fuzzy[:_FUZZY_PREVIEW_LIMIT]:
            print(f"  {c.confidence:.2f}  github:{c.github_login:<25} "
                  f"↔ twitter:@{c.twitter_handle:<25}  ({c.rationale})")
        print()
        print("--dry-run set; not writing.  "
              "Re-run without --dry-run to apply self_reported merges.")
        return 0

    # Apply.  Fuzzy and exact_handle stay manual unless --include-fuzzy.
    applied = 0
    skipped = 0
    for c in candidates:
        if c.method == "self_reported" or (
            args.include_fuzzy and c.method in {"exact_handle", "fuzzy"}
        ):
            person = apply_merge(store, c)
            if person is not None:
                applied += 1
            else:
                skipped += 1
        else:
            skipped += 1

    print(f"applied: {applied}  (canonical entities inserted/updated)")
    print(f"skipped: {skipped}  (review queue + missing-side cases)")

    for canonical_type in (PERSON_TYPE, ORGANIZATION_TYPE):
        rows = store.list_by_type(canonical_type, limit=_TOP_K)
        if not rows:
            continue
        print()
        print(f"Top {_TOP_K} {canonical_type} entities by authority:")
        for e in rows:
            if e.derived_authority is None:
                continue
            links = e.signals.get("links", [])
            link_summary = " + ".join(
                f"{ln['entity_type'].split('_')[0]}:{ln['identity_key']}"
                for ln in links
            )
            print(f"  {e.derived_authority:.2f}  {e.identity_key:<25} "
                  f"({link_summary})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
