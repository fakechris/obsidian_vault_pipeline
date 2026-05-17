"""``ovp-concept-dedup`` — Phase 38.A canonical Evergreen file dedup CLI.

Three subcommands:

  propose [--threshold 0.82] [--write]
      Scan ``10-Knowledge/Evergreen/`` for near-duplicate clusters and print
      the report. With ``--write`` (or no ``--dry-run``) save the proposal as
      JSON to ``60-Logs/dedup-proposals/`` for later application.

  list
      Show all pending proposals.

  apply <proposal-id-or-path> [--dry-run] [--only <slug,slug,...>]
      Execute the proposal: archive duplicates, rewrite wikilinks, update
      canonical aliases, update concept registry (best effort), emit
      ``concept_merged`` audit events.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..concept_dedup import (
    DEFAULT_THRESHOLD,
    apply_proposal,
    archive_applied_proposal,
    find_clusters,
    list_proposals,
    load_proposal,
    write_proposal,
)
from ..runtime import resolve_vault_dir, vault_workflow_lock


def _resolve_proposal_path(vault_dir: Path, ident: str) -> Path:
    candidate = Path(ident)
    if candidate.is_file():
        return candidate
    direct = vault_dir / "60-Logs" / "dedup-proposals" / ident
    if direct.is_file():
        return direct
    if not direct.suffix:
        with_suffix = direct.with_suffix(".json")
        if with_suffix.is_file():
            return with_suffix
    matches = [p for p in list_proposals(vault_dir) if ident in p.name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(
            f"Ambiguous proposal '{ident}'; matches: {', '.join(m.name for m in matches)}"
        )
    raise SystemExit(f"Proposal not found: {ident}")


def _cmd_propose(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    with vault_workflow_lock(vault_dir):
        # Mirror _scan_evergreen exactly (same path, same _/.
        # stem skip) so the printed pair-comparison estimate equals
        # the candidate set find_clusters actually scans.
        eg_dir = vault_dir / "10-Knowledge" / "Evergreen"
        n = (
            sum(
                1
                for p in eg_dir.glob("*.md")
                if not p.stem.startswith(("_", "."))
            )
            if eg_dir.is_dir()
            else 0
        )
        if n:
            print(
                f"Full-vault scan: {n} Evergreen files, "
                f"~{n * (n - 1) // 2:,} pair comparisons (explicit "
                f"maintenance op; pipeline/autopilot never run this)."
            )
        # Explicit operator opt-in to the O(N²) full-vault scan; the
        # pipeline / autopilot paths are fail-closed and never pass this.
        clusters = find_clusters(
            vault_dir, threshold=args.threshold, allow_full_scan=True
        )
        if not clusters:
            print(f"No duplicate clusters found at threshold {args.threshold}.")
            return 0

        total_dups = sum(len(c.duplicates) for c in clusters)
        print(
            f"Found {len(clusters)} cluster(s) covering {total_dups} duplicate file(s) "
            f"(threshold={args.threshold})."
        )
        for i, cluster in enumerate(clusters, 1):
            print(
                f"\n[{i}] canonical={cluster.canonical.slug} "
                f"({cluster.canonical.size_bytes}B, similarity≥{cluster.min_similarity:.2f})"
            )
            for dup in cluster.duplicates:
                print(f"      → {dup.slug} ({dup.size_bytes}B)")

        if not args.write:
            print("\n(dry run — re-run with --write to save a proposal file)")
            return 0

        path, proposal = write_proposal(vault_dir, clusters, threshold=args.threshold)
        print(f"\nProposal written: {path}")
        print(f"Apply with: ovp-concept-dedup apply {proposal.proposal_id} --dry-run")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    proposals = list_proposals(vault_dir)
    if not proposals:
        print("No pending proposals.")
        return 0
    print(f"{len(proposals)} proposal(s):")
    for path in proposals:
        try:
            proposal = load_proposal(path)
            n = len(proposal.clusters)
            d = sum(len(c.duplicates) for c in proposal.clusters)
            print(f"  {proposal.proposal_id}  ({n} clusters, {d} duplicates)  {path}")
        except Exception as exc:
            print(f"  ! {path.name}  unreadable: {exc}")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    path = _resolve_proposal_path(vault_dir, args.proposal)
    proposal = load_proposal(path)

    only = None
    if args.only:
        only = {s.strip() for s in args.only.split(",") if s.strip()}

    archived_path = None
    with vault_workflow_lock(vault_dir):
        results = apply_proposal(
            vault_dir,
            proposal,
            dry_run=args.dry_run,
            pack=args.pack or "",
            only_canonicals=only,
        )
        if results:
            errors = sum(len(r.errors) for r in results)
            if not args.dry_run and errors == 0:
                archived_path = archive_applied_proposal(vault_dir, path)

    if not results:
        print("No clusters matched filter.")
        return 0

    archived = sum(len(r.archived) for r in results)
    rewrites = sum(r.wikilink_rewrites for r in results)
    errors = sum(len(r.errors) for r in results)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] {len(results)} cluster(s): {archived} archived, {rewrites} wikilinks rewritten, {errors} error(s)")
    for r in results:
        line = f"  {r.canonical_slug}: archived={len(r.archived)} rewrites={r.wikilink_rewrites} aliases={len(r.aliases_added)} registry={r.registry_updated}"
        if r.errors:
            line += f" errors={r.errors}"
        print(line)
    if archived_path is not None:
        print(f"Proposal archived: {archived_path}")
    return 0 if errors == 0 else 1


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault-dir", type=Path, default=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ovp-concept-dedup")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_propose = sub.add_parser("propose", help="Scan vault and print/save a dedup proposal")
    _add_common(p_propose)
    p_propose.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_propose.add_argument(
        "--write",
        action="store_true",
        help="Save the proposal to 60-Logs/dedup-proposals/ (otherwise dry-print only)",
    )
    p_propose.set_defaults(func=_cmd_propose)

    p_list = sub.add_parser("list", help="List pending proposals")
    _add_common(p_list)
    p_list.set_defaults(func=_cmd_list)

    p_apply = sub.add_parser("apply", help="Apply a proposal")
    _add_common(p_apply)
    p_apply.add_argument("proposal", help="Proposal id or path to JSON file")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--pack", default="")
    p_apply.add_argument(
        "--only",
        help="Comma-separated canonical slugs to apply (skip other clusters in the proposal)",
    )
    p_apply.set_defaults(func=_cmd_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
