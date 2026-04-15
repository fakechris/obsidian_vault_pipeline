from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..runtime import VaultLayout, advisory_file_lock, resolve_vault_dir
from ..truth_api import run_next_action_queue_item


def run_action_worker_loop(
    vault_dir: Path | str,
    *,
    interval_seconds: float = 2.0,
    max_runs: int | None = None,
) -> dict[str, object]:
    resolved_vault = resolve_vault_dir(vault_dir)
    iterations = 0
    last_result: dict[str, object] = {"ran": False, "reason": "not_started"}

    while max_runs is None or iterations < max_runs:
        last_result = run_next_action_queue_item(resolved_vault)
        iterations += 1
        if max_runs is not None and iterations >= max_runs:
            break
        time.sleep(max(0.0, interval_seconds))

    return {
        "loop": True,
        "iterations": iterations,
        "interval_seconds": max(0.0, interval_seconds),
        "last_result": last_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run queued action workers")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run at most one queued action")
    mode.add_argument("--loop", action="store_true", help="Run as a dedicated action worker loop")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval for loop mode")
    parser.add_argument("--max-runs", type=int, default=None, help="Maximum iterations in loop mode")
    args = parser.parse_args(argv)

    resolved_vault = resolve_vault_dir(args.vault_dir)
    if args.loop:
        layout = VaultLayout.from_vault(resolved_vault)
        try:
            with advisory_file_lock(layout.action_worker_lock, timeout_seconds=0.0):
                payload = run_action_worker_loop(
                    resolved_vault,
                    interval_seconds=args.interval,
                    max_runs=args.max_runs,
                )
        except TimeoutError:
            payload = {"loop": True, "started": False, "reason": "worker_already_running"}
    else:
        payload = run_next_action_queue_item(resolved_vault)
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
