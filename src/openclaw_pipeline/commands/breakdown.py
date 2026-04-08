from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..refine import (
    analyze_breakdown,
    attach_proposal_evidence,
    execute_breakdown,
    load_note_targets,
    record_refine_run,
    refresh_canonical_after_refine,
)
from ..runtime import resolve_vault_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate breakdown proposals for evergreen notes")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--slug", help="Single note slug to analyze")
    parser.add_argument("--all", action="store_true", help="Analyze all evergreen notes")
    parser.add_argument("--dry-run", action="store_true", help="Do not mutate files; emit proposals only")
    parser.add_argument("--write", action="store_true", help="Apply deterministic breakdown mutations")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    targets = load_note_targets(vault_dir, slug=args.slug, all_notes=args.all)
    proposals = [attach_proposal_evidence(vault_dir, analyze_breakdown(target)) for target in targets]
    mutations = []
    canonical_refresh = None
    if args.write:
        mutations = [
            execute_breakdown(vault_dir, target, proposal, write=True)
            for target, proposal in zip(targets, proposals)
        ]
        canonical_refresh = refresh_canonical_after_refine(vault_dir)
        record_refine_run(
            vault_dir,
            mode="breakdown",
            mutations=mutations,
            targets=[proposal["slug"] for proposal in proposals],
            write=True,
            canonical_refresh=canonical_refresh,
        )
    payload = {
        "mode": "breakdown",
        "vault_dir": str(vault_dir),
        "dry_run": not args.write,
        "write": args.write,
        "targets": [proposal["slug"] for proposal in proposals],
        "proposal_count": len(proposals),
        "proposals": proposals,
        "applied_count": sum(1 for mutation in mutations if mutation["status"] == "written"),
        "mutations": mutations,
        "canonical_refresh": canonical_refresh,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"breakdown proposals: {len(proposals)}")
        for proposal in proposals:
            print(f"- {proposal['slug']}: {proposal['action']}")
        if args.write:
            print(f"breakdown applied: {payload['applied_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
