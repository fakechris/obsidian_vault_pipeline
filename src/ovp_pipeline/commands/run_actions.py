from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from ..runtime import VaultLayout, advisory_file_lock, resolve_vault_dir
from ..truth_api import list_action_queue, record_action_worker_state, run_next_action_queue_item


def _worker_candidate(vault_dir: Path | str, *, safe_only: bool) -> dict[str, object]:
    actions = list_action_queue(vault_dir, status="queued", limit=500)
    if safe_only:
        actions = [action for action in actions if bool(action.get("safe_to_run"))]
    actions.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("action_id", ""))))
    if not actions:
        return {}
    action = actions[0]
    return {
        "action_id": str(action.get("action_id") or ""),
        "action_kind": str(action.get("action_kind") or ""),
        "source_signal_id": str(action.get("source_signal_id") or ""),
        "target_ref": str(action.get("target_ref") or ""),
    }


def run_action_worker_loop(
    vault_dir: Path | str,
    *,
    interval_seconds: float = 2.0,
    max_runs: int | None = None,
    safe_only: bool = False,
) -> dict[str, object]:
    resolved_vault = resolve_vault_dir(vault_dir)
    iterations = 0
    last_result: dict[str, object] = {"ran": False, "reason": "not_started"}
    worker_id = f"action-worker-{os.getpid()}"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    while max_runs is None or iterations < max_runs:
        current_action = _worker_candidate(resolved_vault, safe_only=safe_only)
        record_action_worker_state(
            resolved_vault,
            worker_id=worker_id,
            pid=os.getpid(),
            state="running" if current_action else "idle",
            mode="loop" if max_runs is None else "loop_limited",
            safe_only=safe_only,
            current_action=current_action,
            last_result=last_result,
            interval_seconds=interval_seconds,
            max_runs=max_runs,
            started_at=started_at,
        )
        last_result = run_next_action_queue_item(resolved_vault, safe_only=safe_only)
        iterations += 1
        record_action_worker_state(
            resolved_vault,
            worker_id=worker_id,
            pid=os.getpid(),
            state="idle",
            mode="loop" if max_runs is None else "loop_limited",
            safe_only=safe_only,
            current_action={},
            last_result=last_result,
            interval_seconds=interval_seconds,
            max_runs=max_runs,
            started_at=started_at,
        )
        if max_runs is not None and iterations >= max_runs:
            break
        time.sleep(max(0.0, interval_seconds))

    return {
        "loop": True,
        "iterations": iterations,
        "interval_seconds": max(0.0, interval_seconds),
        "safe_only": safe_only,
        "last_result": last_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run queued action workers")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run at most one queued action")
    mode.add_argument("--loop", action="store_true", help="Run as a dedicated action worker loop")
    parser.add_argument(
        "--interval", type=float, default=2.0, help="Polling interval for loop mode"
    )
    parser.add_argument(
        "--max-runs", type=int, default=None, help="Maximum iterations in loop mode"
    )
    parser.add_argument(
        "--safe-only", action="store_true", help="Only run actions marked safe_to_run"
    )
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
                    safe_only=args.safe_only,
                )
        except TimeoutError:
            payload = {"loop": True, "started": False, "reason": "worker_already_running"}
    else:
        worker_id = f"action-worker-{os.getpid()}"
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record_action_worker_state(
            resolved_vault,
            worker_id=worker_id,
            pid=os.getpid(),
            state="running",
            mode="one_shot",
            safe_only=args.safe_only,
            current_action=_worker_candidate(resolved_vault, safe_only=args.safe_only),
            started_at=started_at,
        )
        payload = run_next_action_queue_item(resolved_vault, safe_only=args.safe_only)
        record_action_worker_state(
            resolved_vault,
            worker_id=worker_id,
            pid=os.getpid(),
            state="stopped",
            mode="one_shot",
            safe_only=args.safe_only,
            current_action={},
            last_result=payload,
            started_at=started_at,
        )
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
