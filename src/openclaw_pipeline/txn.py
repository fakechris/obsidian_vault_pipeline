"""
Workflow Transaction Manager

Manages workflow transactions with state tracking:
- start: Create new transaction
- step: Update step status
- complete: Mark as completed
- fail: Mark as failed
- list: Show incomplete transactions
- show: Display transaction details
- archive: Archive old transactions

Transaction JSON structure:
{
    "id": "txn-YYYYMMDD-HHMMSS-uuid",
    "type": "workflow_type",
    "description": "description",
    "start_time": "ISO timestamp",
    "status": "in_progress|completed|failed",
    "steps": {
        "step_name": {
            "status": "pending|processing|completed|failed",
            "output": "step output/result",
            "updated_at": "ISO timestamp"
        }
    },
    "checkpoint": "current step name",
    "last_updated": "ISO timestamp",
    "completed_at": "ISO timestamp (when completed)",
    "failure_reason": "reason (when failed)"
}
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


RUN_STALE_AFTER_SECONDS = 30 * 60


# =============================================================================
# Core Transaction Operations
# =============================================================================

def _get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_step_state(status: str) -> str:
    return {
        "pending": "pending",
        "processing": "running",
        "in_progress": "running",
        "completed": "completed",
        "failed": "failed",
        "blocked": "blocked",
    }.get(status, status)


def _derive_progress_percent(work_units_done: int | None, work_units_total: int | None) -> float | None:
    if work_units_total is None or work_units_total <= 0 or work_units_done is None:
        return None
    return round((float(work_units_done) / float(work_units_total)) * 100.0, 1)


def _derive_progress_summary(
    *,
    progress_mode: str,
    work_units_done: int | None,
    work_units_total: int | None,
    work_units_failed: int | None,
) -> str:
    if progress_mode != "counted" or work_units_total is None:
        return "Progress is currently indeterminate."
    failed = work_units_failed or 0
    if failed:
        return f"{work_units_done or 0}/{work_units_total} work units completed, {failed} failed"
    return f"{work_units_done or 0}/{work_units_total} work units completed"


def build_transaction_payload(
    txn_id: str,
    workflow_type: str,
    description: str,
    *,
    timestamp: str | None = None,
    pack_name: str | None = None,
    workflow_profile: str | None = None,
    planned_steps: list[str] | None = None,
) -> dict[str, Any]:
    ts = timestamp or _get_timestamp()
    return {
        "id": txn_id,
        "type": workflow_type,
        "description": description,
        "start_time": ts,
        "status": "in_progress",
        "steps": {},
        "checkpoint": "initialized",
        "last_updated": ts,
        "run_ledger": {
            "run_id": txn_id,
            "run_state": "running",
            "workflow_profile": workflow_profile or "",
            "pack_name": pack_name or "",
            "planned_steps": planned_steps or [],
            "started_at": ts,
            "updated_at": ts,
            "heartbeat_at": ts,
            "current_step_name": "initialized",
            "current_step": {
                "step_name": "initialized",
                "step_state": "pending",
                "step_started_at": ts,
                "step_heartbeat_at": ts,
                "progress_mode": "indeterminate",
                "work_units_total": None,
                "work_units_done": 0,
                "work_units_failed": 0,
                "current_item": None,
                "progress_percent": None,
                "progress_summary": "Waiting to start.",
            },
            "last_meaningful_event": None,
            "stale": False,
            "blocked_reason": None,
            "error_summary": None,
        },
    }


def ensure_run_ledger(payload: dict[str, Any]) -> dict[str, Any]:
    if "run_ledger" in payload:
        return payload
    ts = payload.get("start_time") or payload.get("last_updated") or _get_timestamp()
    payload["run_ledger"] = {
        "run_id": payload.get("id", ""),
        "run_state": "completed" if payload.get("status") == "completed" else ("failed" if payload.get("status") == "failed" else "running"),
        "workflow_profile": "",
        "pack_name": "",
        "planned_steps": list(payload.get("steps", {}).keys()),
        "started_at": ts,
        "updated_at": payload.get("last_updated", ts),
        "heartbeat_at": payload.get("last_updated", ts),
        "current_step_name": payload.get("checkpoint", "initialized"),
        "current_step": {
            "step_name": payload.get("checkpoint", "initialized"),
            "step_state": _normalize_step_state(payload.get("status", "pending")),
            "step_started_at": ts,
            "step_heartbeat_at": payload.get("last_updated", ts),
            "progress_mode": "indeterminate",
            "work_units_total": None,
            "work_units_done": 0,
            "work_units_failed": 0,
            "current_item": None,
            "progress_percent": None,
            "progress_summary": "Progress is currently indeterminate.",
        },
        "last_meaningful_event": None,
        "stale": False,
        "blocked_reason": None,
        "error_summary": payload.get("failure_reason"),
    }
    return payload


def update_transaction_step(
    payload: dict[str, Any],
    step_name: str,
    status: str,
    *,
    output: str = "",
    timestamp: str | None = None,
    progress_mode: str | None = None,
    work_units_total: int | None = None,
    work_units_done: int | None = None,
    work_units_failed: int | None = None,
    current_item: str | None = None,
    progress_summary: str | None = None,
    last_meaningful_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_run_ledger(payload)
    ts = timestamp or _get_timestamp()

    payload["steps"][step_name] = {
        "status": status,
        "output": output,
        "updated_at": ts,
    }
    payload["checkpoint"] = step_name
    payload["last_updated"] = ts

    run_ledger = payload["run_ledger"]
    current = dict(run_ledger.get("current_step") or {})
    previous_step = current.get("step_name")
    if previous_step != step_name:
        current = {"step_started_at": ts}
    current["step_name"] = step_name
    current["step_state"] = _normalize_step_state(status)
    current["step_heartbeat_at"] = ts
    current["progress_mode"] = progress_mode or current.get("progress_mode") or "indeterminate"
    current["work_units_total"] = work_units_total if work_units_total is not None else current.get("work_units_total")
    current["work_units_done"] = work_units_done if work_units_done is not None else current.get("work_units_done", 0)
    current["work_units_failed"] = work_units_failed if work_units_failed is not None else current.get("work_units_failed", 0)
    current["current_item"] = current_item if current_item is not None else current.get("current_item")
    current["progress_percent"] = _derive_progress_percent(current.get("work_units_done"), current.get("work_units_total"))
    current["progress_summary"] = progress_summary or _derive_progress_summary(
        progress_mode=current.get("progress_mode") or "indeterminate",
        work_units_done=current.get("work_units_done"),
        work_units_total=current.get("work_units_total"),
        work_units_failed=current.get("work_units_failed"),
    )

    run_ledger["current_step_name"] = step_name
    run_ledger["current_step"] = current
    run_ledger["updated_at"] = ts
    run_ledger["heartbeat_at"] = ts
    run_ledger["run_state"] = "failed" if status == "failed" else ("completed" if status == "completed" and payload.get("status") == "completed" else "running")
    run_ledger["stale"] = False
    run_ledger["error_summary"] = output if status == "failed" and output else run_ledger.get("error_summary")
    if last_meaningful_event is not None:
        run_ledger["last_meaningful_event"] = last_meaningful_event
    return payload


def mark_transaction_completed(payload: dict[str, Any], *, timestamp: str | None = None) -> dict[str, Any]:
    ensure_run_ledger(payload)
    ts = timestamp or _get_timestamp()
    payload["status"] = "completed"
    payload["completed_at"] = ts
    payload["last_updated"] = ts
    payload["run_ledger"]["run_state"] = "completed"
    payload["run_ledger"]["updated_at"] = ts
    payload["run_ledger"]["heartbeat_at"] = ts
    current = payload["run_ledger"].get("current_step") or {}
    if current:
        current["step_state"] = "completed"
        current["step_heartbeat_at"] = ts
        if current.get("progress_mode") == "counted" and current.get("work_units_total") is not None:
            current["work_units_done"] = current.get("work_units_total")
            current["progress_percent"] = 100.0
            current["progress_summary"] = _derive_progress_summary(
                progress_mode="counted",
                work_units_done=current.get("work_units_done"),
                work_units_total=current.get("work_units_total"),
                work_units_failed=current.get("work_units_failed"),
            )
    return payload


def mark_transaction_failed(payload: dict[str, Any], reason: str, *, timestamp: str | None = None) -> dict[str, Any]:
    ensure_run_ledger(payload)
    ts = timestamp or _get_timestamp()
    payload["status"] = "failed"
    payload["failure_reason"] = reason
    payload["last_updated"] = ts
    payload["run_ledger"]["run_state"] = "failed"
    payload["run_ledger"]["error_summary"] = reason
    payload["run_ledger"]["updated_at"] = ts
    payload["run_ledger"]["heartbeat_at"] = ts
    current = payload["run_ledger"].get("current_step") or {}
    if current:
        current["step_state"] = "failed"
        current["step_heartbeat_at"] = ts
    return payload


def heartbeat_transaction(
    payload: dict[str, Any],
    *,
    step_name: str | None = None,
    timestamp: str | None = None,
    current_item: str | None = None,
    work_units_done: int | None = None,
    work_units_total: int | None = None,
    work_units_failed: int | None = None,
    progress_mode: str | None = None,
    progress_summary: str | None = None,
    last_meaningful_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_run_ledger(payload)
    ts = timestamp or _get_timestamp()
    run_ledger = payload["run_ledger"]
    current = dict(run_ledger.get("current_step") or {})
    effective_step = step_name or current.get("step_name") or payload.get("checkpoint") or "initialized"
    previous_step = current.get("step_name")
    if previous_step != effective_step:
        current = {"step_started_at": ts}
    current["step_name"] = effective_step
    current["step_state"] = current.get("step_state") or "running"
    current["step_started_at"] = current.get("step_started_at") or ts
    current["step_heartbeat_at"] = ts
    current["progress_mode"] = progress_mode or current.get("progress_mode") or "indeterminate"
    current["work_units_total"] = work_units_total if work_units_total is not None else current.get("work_units_total")
    current["work_units_done"] = work_units_done if work_units_done is not None else current.get("work_units_done", 0)
    current["work_units_failed"] = work_units_failed if work_units_failed is not None else current.get("work_units_failed", 0)
    current["current_item"] = current_item if current_item is not None else current.get("current_item")
    current["progress_percent"] = _derive_progress_percent(current.get("work_units_done"), current.get("work_units_total"))
    current["progress_summary"] = progress_summary or current.get("progress_summary") or _derive_progress_summary(
        progress_mode=current.get("progress_mode") or "indeterminate",
        work_units_done=current.get("work_units_done"),
        work_units_total=current.get("work_units_total"),
        work_units_failed=current.get("work_units_failed"),
    )
    run_ledger["current_step_name"] = effective_step
    run_ledger["current_step"] = current
    run_ledger["updated_at"] = ts
    run_ledger["heartbeat_at"] = ts
    run_ledger["run_state"] = "running"
    run_ledger["stale"] = False
    payload["last_updated"] = ts
    if last_meaningful_event is not None:
        run_ledger["last_meaningful_event"] = last_meaningful_event
    return payload


def classify_run_ledgers(
    transactions_dir: Path,
    *,
    now_iso: str | None = None,
    stale_after_seconds: int = RUN_STALE_AFTER_SECONDS,
) -> dict[str, list[dict[str, Any]]]:
    now = (_parse_timestamp(now_iso) if now_iso else None) or datetime.now(timezone.utc)
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []

    if not transactions_dir.exists():
        return {"active": active, "stale": stale}

    for txn_file in sorted(transactions_dir.glob("*.json")):
        try:
            payload = json.loads(txn_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") != "in_progress":
            continue
        ensure_run_ledger(payload)
        ledger = payload["run_ledger"]
        heartbeat = _parse_timestamp(ledger.get("heartbeat_at")) or _parse_timestamp(payload.get("last_updated"))
        is_stale = False
        if heartbeat is not None:
            is_stale = (now - heartbeat).total_seconds() > stale_after_seconds
        ledger["stale"] = is_stale
        (stale if is_stale else active).append(payload)

    def key_fn(item: dict[str, Any]) -> str:
        return (
            item.get("run_ledger", {}).get("heartbeat_at")
            or item.get("last_updated")
            or ""
        )

    active.sort(key=key_fn, reverse=True)
    stale.sort(key=key_fn, reverse=True)
    return {"active": active, "stale": stale}


def _generate_txn_id() -> str:
    """Generate a unique transaction ID."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    uid = str(uuid.uuid4()).split("-")[0]
    return f"txn-{ts}-{uid}"


def start_transaction(
    transactions_dir: Path,
    workflow_type: str,
    description: str,
) -> str:
    """
    Create a new transaction.

    Args:
        transactions_dir: Directory to store transaction JSON files
        workflow_type: Type of workflow (e.g., "pinboard-processing", "wigs")
        description: Human-readable description

    Returns:
        Transaction ID
    """
    transactions_dir.mkdir(parents=True, exist_ok=True)

    txn_id = _generate_txn_id()
    timestamp = _get_timestamp()

    txn_data = {
        "id": txn_id,
        "type": workflow_type,
        "description": description,
        "start_time": timestamp,
        "status": "in_progress",
        "steps": {},
        "checkpoint": "initialized",
        "last_updated": timestamp,
    }

    txn_file = transactions_dir / f"{txn_id}.json"
    txn_file.write_text(json.dumps(txn_data, indent=2))

    return txn_id


def update_step(
    transactions_dir: Path,
    txn_id: str,
    step_name: str,
    status: Literal["pending", "processing", "completed", "failed"],
    output: str = "",
) -> bool:
    """
    Update a step within a transaction.

    Args:
        transactions_dir: Directory containing transaction files
        txn_id: Transaction ID
        step_name: Name of the step
        status: Step status
        output: Optional output/result from the step

    Returns:
        True if updated successfully, False if transaction not found
    """
    txn_file = transactions_dir / f"{txn_id}.json"
    if not txn_file.exists():
        print(f"Error: Transaction {txn_id} not found")
        return False

    txn_data = json.loads(txn_file.read_text())
    timestamp = _get_timestamp()

    txn_data["steps"][step_name] = {
        "status": status,
        "output": output,
        "updated_at": timestamp,
    }
    txn_data["last_updated"] = timestamp
    txn_data["checkpoint"] = step_name

    txn_file.write_text(json.dumps(txn_data, indent=2))
    print(f"Updated {txn_id}: {step_name} -> {status}")
    return True


def complete_transaction(transactions_dir: Path, txn_id: str) -> bool:
    """
    Mark a transaction as completed.

    Args:
        transactions_dir: Directory containing transaction files
        txn_id: Transaction ID

    Returns:
        True if completed successfully, False if not found
    """
    txn_file = transactions_dir / f"{txn_id}.json"
    if not txn_file.exists():
        print(f"Transaction not found: {txn_id}")
        return False

    txn_data = json.loads(txn_file.read_text())
    timestamp = _get_timestamp()

    txn_data["status"] = "completed"
    txn_data["completed_at"] = timestamp
    txn_data["last_updated"] = timestamp

    txn_file.write_text(json.dumps(txn_data, indent=2))
    print(f"Transaction {txn_id} completed")
    return True


def fail_transaction(transactions_dir: Path, txn_id: str, reason: str) -> bool:
    """
    Mark a transaction as failed.

    Args:
        transactions_dir: Directory containing transaction files
        txn_id: Transaction ID
        reason: Failure reason

    Returns:
        True if marked successfully, False if not found
    """
    txn_file = transactions_dir / f"{txn_id}.json"
    if not txn_file.exists():
        print(f"Transaction not found: {txn_id}")
        return False

    txn_data = json.loads(txn_file.read_text())
    timestamp = _get_timestamp()

    txn_data["status"] = "failed"
    txn_data["failure_reason"] = reason
    txn_data["last_updated"] = timestamp

    txn_file.write_text(json.dumps(txn_data, indent=2))
    print(f"Transaction {txn_id} failed: {reason}")
    return True


# =============================================================================
# Query Operations
# =============================================================================

def list_incomplete(transactions_dir: Path) -> list[dict]:
    """
    List all incomplete transactions.

    Args:
        transactions_dir: Directory containing transaction files

    Returns:
        List of incomplete transaction summaries
    """
    incomplete = []

    if not transactions_dir.exists():
        return incomplete

    for txn_file in sorted(transactions_dir.glob("*.json")):
        try:
            txn_data = json.loads(txn_file.read_text())
            if txn_data.get("status") not in ("completed", "failed"):
                incomplete.append({
                    "id": txn_data.get("id"),
                    "type": txn_data.get("type"),
                    "description": txn_data.get("description"),
                    "start_time": txn_data.get("start_time"),
                    "checkpoint": txn_data.get("checkpoint"),
                })
        except (json.JSONDecodeError, KeyError):
            continue

    return incomplete


def show_transaction(transactions_dir: Path, txn_id: str) -> dict | None:
    """
    Get full transaction details.

    Args:
        transactions_dir: Directory containing transaction files
        txn_id: Transaction ID

    Returns:
        Transaction data dict or None if not found
    """
    txn_file = transactions_dir / f"{txn_id}.json"
    if not txn_file.exists():
        print(f"Transaction not found: {txn_id}")
        return None

    return json.loads(txn_file.read_text())


def archive_old_transactions(transactions_dir: Path, days: int = 30) -> int:
    """
    Archive transactions older than specified days.

    Args:
        transactions_dir: Directory containing transaction files
        days: Age threshold in days (default: 30)

    Returns:
        Number of transactions archived
    """
    from time import time

    if not transactions_dir.exists():
        return 0

    archive_dir = transactions_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    cutoff = time() - (days * 86400)
    archived = 0

    for txn_file in transactions_dir.glob("*.json"):
        if txn_file.stat().st_mtime < cutoff:
            txn_file.rename(archive_dir / txn_file.name)
            archived += 1

    print(f"Archived {archived} transaction(s) older than {days} days")
    return archived


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Workflow Transaction Manager")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # start
    subparsers.add_parser("start", help="Start a new transaction")
    subparsers.add_parser("list", help="List incomplete transactions")
    subparsers.add_parser("archive", help="Archive old transactions")

    # start with args
    start_parser = subparsers.add_parser("start", help="Start new transaction")
    start_parser.add_argument("type", help="Workflow type")
    start_parser.add_argument("description", help="Description")

    # step with args
    step_parser = subparsers.add_parser("step", help="Update step")
    step_parser.add_argument("txn_id", help="Transaction ID")
    step_parser.add_argument("step_name", help="Step name")
    step_parser.add_argument("status", choices=["pending", "processing", "completed", "failed"])
    step_parser.add_argument("output", nargs="?", default="", help="Step output")

    # complete with args
    complete_parser = subparsers.add_parser("complete", help="Complete transaction")
    complete_parser.add_argument("txn_id", help="Transaction ID")

    # fail with args
    fail_parser = subparsers.add_parser("fail", help="Fail transaction")
    fail_parser.add_argument("txn_id", help="Transaction ID")
    fail_parser.add_argument("reason", help="Failure reason")

    # show with args
    show_parser = subparsers.add_parser("show", help="Show transaction")
    show_parser.add_argument("txn_id", help="Transaction ID")

    # archive with args
    archive_parser = subparsers.add_parser("archive", help="Archive old transactions")
    archive_parser.add_argument("days", nargs="?", type=int, default=30, help="Days threshold")

    args = parser.parse_args()

    # Detect vault directory
    from pathlib import Path
    import os

    vault_dir = os.environ.get("WIGS_VAULT_DIR")
    if not vault_dir:
        try:
            import subprocess
            vault_dir = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True
            ).strip()
        except subprocess.CalledProcessError:
            # Fallback: use script's parent directory
            vault_dir = Path(__file__).parent.parent.parent

    transactions_dir = Path(os.environ.get(
        "WIGS_TRANSACTIONS_DIR",
        f"{vault_dir}/60-Logs/transactions"
    ))

    if args.command == "start":
        txn_id = start_transaction(transactions_dir, args.type, args.description)
        print(f"Created: {txn_id}")

    elif args.command == "step":
        update_step(transactions_dir, args.txn_id, args.step_name, args.status, args.output)

    elif args.command == "complete":
        complete_transaction(transactions_dir, args.txn_id)

    elif args.command == "fail":
        fail_transaction(transactions_dir, args.txn_id, args.reason)

    elif args.command == "list":
        incomplete = list_incomplete(transactions_dir)
        if not incomplete:
            print("No incomplete transactions")
        else:
            print(f"Incomplete transactions: {len(incomplete)}")
            for txn in incomplete:
                print(f"  • {txn['id']} | {txn['type']} | {txn['description']} | started: {txn['start_time']}")

    elif args.command == "show":
        txn = show_transaction(transactions_dir, args.txn_id)
        if txn:
            print(json.dumps(txn, indent=2))

    elif args.command == "archive":
        archive_old_transactions(transactions_dir, args.days)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
