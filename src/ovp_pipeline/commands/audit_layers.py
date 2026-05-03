"""ovp-audit-layers — Audit every markdown layer's frontmatter against
its declared schema in ``ovp_pipeline.layer_schemas``.

Usage::

    ovp-audit-layers --vault-dir ~/Documents/ovp-vault                 # human report
    ovp-audit-layers --vault-dir ~/Documents/ovp-vault --json          # machine report
    ovp-audit-layers --vault-dir ~/Documents/ovp-vault --severity HIGH  # only HIGH
    ovp-audit-layers --vault-dir ~/Documents/ovp-vault --layer "L3 Evergreen"

Exit code is non-zero if any HIGH violation is found, so this can gate
CI / pre-commit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..layer_schemas import audit_all_layers, summarize_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit markdown layer frontmatter against schemas",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path.cwd(),
        help="Vault root directory (default: cwd)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report instead of human summary",
    )
    parser.add_argument(
        "--severity",
        choices=("HIGH", "MEDIUM", "LOW"),
        help="Only emit violations at or above this severity",
    )
    parser.add_argument(
        "--layer",
        help="Only emit violations from a specific layer (e.g. 'L3 Evergreen')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N violations per layer (0 = no limit)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Only scan first N files per layer (0 = all)",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault dir not found: {vault}", file=sys.stderr)
        return 2

    sample = args.sample if args.sample > 0 else None
    report = audit_all_layers(vault, sample_size=sample)

    severity_floor = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    floor = severity_floor[args.severity] if args.severity else 2

    def keep(v_sev: str) -> bool:
        return severity_floor[v_sev] <= floor

    # Filter layers + violations per filters
    for layer in report["layers"]:
        if args.layer and layer["name"] != args.layer:
            layer["violations"] = []
            continue
        layer["violations"] = [v for v in layer["violations"] if keep(v["severity"])]
        if args.limit > 0:
            layer["violations"] = layer["violations"][:args.limit]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(summarize_report(report))
        print()
        for layer in report["layers"]:
            if not layer["violations"]:
                continue
            print(f"=== {layer['name']} ===")
            for v in layer["violations"]:
                print(f"  [{v['severity']}] {v['rule']:18s} {Path(v['file']).name}: {v['message']}")
            print()

    high_count = sum(
        1
        for layer in report["layers"]
        for v in layer["violations"]
        if v["severity"] == "HIGH"
    )
    return 1 if high_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
