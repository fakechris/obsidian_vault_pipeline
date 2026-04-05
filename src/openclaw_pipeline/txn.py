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
from typing import Literal


# =============================================================================
# Core Transaction Operations
# =============================================================================

def _get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
