from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..runtime import resolve_vault_dir
from ..truth_api import run_next_action_queue_item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run queued action workers")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--once", action="store_true", help="Run at most one queued action")
    args = parser.parse_args(argv)

    resolved_vault = resolve_vault_dir(args.vault_dir)
    payload = run_next_action_queue_item(resolved_vault)
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
