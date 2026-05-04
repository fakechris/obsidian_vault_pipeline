"""ovp-link-entities — auto-wikilink for evergreen body prose (BL-040).

Walks ``10-Knowledge/Evergreen/**.md``, replaces canonical-entity
mentions with ``[[canonical_handle]]`` Obsidian wikilinks, and
generates ``10-Knowledge/Entity/<handle>.md`` stub pages for any
canonicals that don't have a markdown page yet.

Usage::

    ovp-link-entities --vault-dir ~/Documents/ovp-vault                # apply
    ovp-link-entities --vault-dir ~/Documents/ovp-vault --dry-run      # preview
    ovp-link-entities --vault-dir ~/Documents/ovp-vault --scan-dir 20-Areas
    ovp-link-entities --vault-dir ~/Documents/ovp-vault --no-stubs

Idempotent: re-running the command on the rewritten output is a
no-op because the new wikilinks land in the skip-regions list (the
matcher won't touch text inside ``[[...]]``).

Default scan target is ``10-Knowledge/Evergreen/``; pass
``--scan-dir`` to point at a different subtree (multiple flags
allowed).  Frontmatter, code blocks (fenced + inline), existing
wikilinks, and existing markdown links are never touched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..entities.aliases import (
    build_alias_index,
    collect_entity_aliases,
)
from ..entities.store import EntityStore
from ..entities.aliases import KIND_DISPLAY_NAME
from ..entities.wikilink import (
    DEFAULT_LINKABLE_KINDS,
    DEFAULT_MIN_ALIAS_LENGTH,
    apply_prepared_matcher,
    ensure_entity_stub_files,
    prepare_matcher,
)


_DEFAULT_SCAN_REL = Path("10-Knowledge") / "Evergreen"


def _iter_target_files(vault_dir: Path, scan_rel: list[Path]):
    """Yield every .md path under each ``scan_rel`` subtree, skipping
    backup / cache dirs.

    The skip-parts check runs against the path **relative to the
    vault root** — ``p.parts`` would otherwise compare against the
    absolute path, so a vault placed inside a directory called
    e.g. ``_backup`` would silently skip every file.
    """
    skip_parts = frozenset({"__pycache__", "_backup", ".git"})
    vault_root = vault_dir.resolve()
    for rel in scan_rel:
        root = vault_dir / rel
        if not root.is_dir():
            continue
        for p in root.rglob("*.md"):
            try:
                rel_p = p.resolve().relative_to(vault_root)
            except ValueError:
                # Symlink / mount oddity placed the file outside the
                # vault — be conservative and skip.
                continue
            if any(part in skip_parts for part in rel_p.parts):
                continue
            yield p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-wikilink canonical entities in evergreen prose",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--scan-dir", action="append", type=Path, default=None,
        help=f"Subtree under vault to scan (relative).  "
             f"Default: {_DEFAULT_SCAN_REL}.  Can be repeated.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview replacements + stub creations without writing",
    )
    parser.add_argument(
        "--no-stubs", action="store_true",
        help="Skip creation of 10-Knowledge/Entity/<handle>.md "
             "stub files (only do the in-place replacements)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print summary only, no per-file lines",
    )
    parser.add_argument(
        "--include-display-names", action="store_true",
        help="Also auto-link via display_name aliases (e.g., "
             "'Andrej Karpathy' → karpathy).  Off by default — "
             "display names are auto-derived from canonical_name "
             "and trip on common English words like 'image'.",
    )
    parser.add_argument(
        "--min-alias-length", type=int, default=DEFAULT_MIN_ALIAS_LENGTH,
        help=f"Skip aliases shorter than this many chars "
             f"(default {DEFAULT_MIN_ALIAS_LENGTH}). "
             "Lower at your own risk: short aliases like 'ai' / "
             "'ml' will linkify every occurrence.",
    )
    args = parser.parse_args(argv)

    kinds = set(DEFAULT_LINKABLE_KINDS)
    if args.include_display_names:
        kinds.add(KIND_DISPLAY_NAME)
    kinds = frozenset(kinds)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    scan_targets = args.scan_dir or [_DEFAULT_SCAN_REL]

    # Build the alias index ONCE.  ~929 canonicals on the OVP vault,
    # ~7000 evergreens to walk → 0.5s scan vs 0.5s × 7000 if rebuilt
    # per file.
    store = EntityStore(db_path=vault / "60-Logs" / "knowledge.db")
    aliases = collect_entity_aliases(vault_dir=vault, entity_store=store)
    alias_index = build_alias_index(aliases)

    if not alias_index:
        print("entity_aliases is empty — nothing to link.  "
              "Run ovp-backfill-twitter-authors / ovp-backfill-github "
              "first to populate the entity layer.", file=sys.stderr)
        return 0

    # ---- pass 1: rewrite evergreen bodies ----------------------------------

    # Pre-build the matcher ONCE outside the file loop.  Inside
    # ``apply_wikilinks`` the filter + regex compilation costs ~5ms;
    # at 7000 evergreens that's 35s of redundant work.  ``prepare_matcher``
    # does the work once.
    matcher = prepare_matcher(
        alias_index, kinds=kinds, min_length=args.min_alias_length,
    )

    files_changed = 0
    total_replacements = 0
    canonicals_used: set[str] = set()
    files_seen = 0
    for path in _iter_target_files(vault, scan_targets):
        files_seen += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError is NOT an OSError subclass — it
            # would crash the scan on a binary file or one with
            # non-UTF8 encoding.  Skip + continue so a single bad
            # file doesn't take down the whole vault walk.
            continue
        result = apply_prepared_matcher(text, matcher)
        if result.n_replaced == 0:
            continue
        files_changed += 1
        total_replacements += result.n_replaced
        canonicals_used.update(result.canonicals_used)
        if not args.quiet:
            print(f"  {result.n_replaced:>3} → {path.relative_to(vault)}  "
                  f"({len(result.canonicals_used)} canonicals)")
        if args.dry_run:
            continue
        path.write_text(result.text, encoding="utf-8")

    # ---- pass 2: stub creation ---------------------------------------------

    stubs_created: list[Path] = []
    if not args.no_stubs and canonicals_used:
        # Pick the highest-precedence alias per canonical so the stub
        # carries the right entity_type + authority.
        rep: dict = {}
        for alias_obj in alias_index.values():
            if alias_obj.canonical_handle not in canonicals_used:
                continue
            existing = rep.get(alias_obj.canonical_handle)
            if existing is None:
                rep[alias_obj.canonical_handle] = alias_obj
                continue
            # Keep the one with higher authority for the stub.
            existing_auth = existing.authority or 0.0
            new_auth = alias_obj.authority or 0.0
            if new_auth > existing_auth:
                rep[alias_obj.canonical_handle] = alias_obj
        stubs_created = ensure_entity_stub_files(
            vault, rep, dry_run=args.dry_run,
        )

    # ---- summary -----------------------------------------------------------

    print()
    verb = "would change" if args.dry_run else "changed"
    print(f"=== Summary ({verb}) ===")
    print(f"  files scanned:        {files_seen}")
    print(f"  files {verb}:         {files_changed}")
    print(f"  total wikilinks:      {total_replacements}")
    print(f"  unique canonicals:    {len(canonicals_used)}")
    if stubs_created:
        verb_stub = "would be created" if args.dry_run else "created"
        print(f"  entity stub pages {verb_stub}: {len(stubs_created)}")
        if not args.quiet:
            for p in stubs_created[:20]:
                print(f"    + {p.relative_to(vault)}")
            if len(stubs_created) > 20:
                print(f"    ... and {len(stubs_created) - 20} more")
    if args.dry_run:
        print()
        print("--dry-run set; nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
