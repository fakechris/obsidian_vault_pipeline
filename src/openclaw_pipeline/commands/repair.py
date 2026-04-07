#!/usr/bin/env python3
"""
repair - 修复卡住的事务和系统状态

清理卡在 in_progress 状态超过 24 小时的事务，
并修复常见的系统问题。

Usage:
    ovp-repair --transactions
    ovp-repair --autopilot
    ovp-repair --all
    ovp-repair --vault-dir /path/to/vault
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_vault_dir() -> Path:
    """获取 Vault 目录"""
    try:
        import subprocess
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True
        ).strip()
        return Path(git_root)
    except subprocess.CalledProcessError:
        return Path.cwd()


def repair_transactions(vault_dir: Path, dry_run: bool = True) -> dict:
    """修复卡住的事务"""
    transactions_dir = vault_dir / "60-Logs" / "transactions"

    if not transactions_dir.exists():
        return {"error": "Transactions directory not found", "fixed": 0}

    stuck_transactions = []
    cutoff_time = datetime.now() - timedelta(hours=24)

    # 查找卡住的事务
    for txn_file in transactions_dir.glob("*.json"):
        try:
            txn_data = json.loads(txn_file.read_text())
            if txn_data.get("status") != "in_progress":
                continue

            # 检查是否超时
            last_updated = txn_data.get("last_updated", "")
            if last_updated:
                try:
                    last_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    if last_dt < cutoff_time:
                        stuck_transactions.append({
                            "id": txn_data.get("id"),
                            "type": txn_data.get("type"),
                            "description": txn_data.get("description"),
                            "last_updated": last_updated,
                            "checkpoint": txn_data.get("checkpoint"),
                        })
                except ValueError:
                    # 无法解析时间，当作超时处理
                    stuck_transactions.append({
                        "id": txn_data.get("id"),
                        "type": txn_data.get("type"),
                        "description": txn_data.get("description"),
                        "last_updated": last_updated,
                        "reason": "unparseable_timestamp",
                    })
        except (json.JSONDecodeError, KeyError):
            continue

    result = {
        "total_stuck": len(stuck_transactions),
        "fixed": 0,
        "transactions": stuck_transactions,
    }

    if not stuck_transactions:
        print("✅ No stuck transactions found")
        return result

    print(f"\nFound {len(stuck_transactions)} stuck transactions:")
    for txn in stuck_transactions:
        print(f"  • {txn['id']} | {txn.get('type', 'unknown')} | "
              f"checkpoint: {txn.get('checkpoint', '?')} | "
              f"last_updated: {txn.get('last_updated', '?')}")

    if dry_run:
        print(f"\n🔍 [DRY RUN] Would mark {len(stuck_transactions)} transactions as 'aborted'")
        return result

    # 修复：标记为 aborted
    for txn in stuck_transactions:
        txn_file = transactions_dir / f"{txn['id']}.json"
        if txn_file.exists():
            try:
                txn_data = json.loads(txn_file.read_text())
                txn_data["status"] = "aborted"
                txn_data["aborted_at"] = datetime.now().isoformat()
                txn_data["aborted_reason"] = "stuck_over_24h"
                txn_file.write_text(json.dumps(txn_data, indent=2))
                result["fixed"] += 1
                print(f"  ✅ Aborted: {txn['id']}")
            except Exception as e:
                print(f"  ❌ Error aborting {txn['id']}: {e}")

    return result


def repair_autopilot(vault_dir: Path, dry_run: bool = True) -> dict:
    """修复 Autopilot DB"""
    autopilot_db = vault_dir / "60-Logs" / "autopilot.db"

    if not autopilot_db.exists():
        return {"error": "Autopilot DB not found", "fixed": 0}

    result = {
        "db_path": str(autopilot_db),
        "fixed": 0,
    }

    try:
        conn = sqlite3.connect(autopilot_db)
        cursor = conn.cursor()

        # 检查表结构
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            print("⚠️  Autopilot DB is empty (no tables)")
            conn.close()
            return result

        print(f"\nAutopilot DB tables: {', '.join(tables)}")

        # 检查 tasks 表
        if "tasks" in tables:
            cursor.execute("SELECT COUNT(*) FROM tasks")
            task_count = cursor.fetchone()[0]
            print(f"  Tasks in queue: {task_count}")

            # 检查卡住的任务
            cursor.execute("""
                SELECT id, status, created_at, updated_at
                FROM tasks
                WHERE status IN ('pending', 'running')
            """)
            stuck_tasks = cursor.fetchall()

            if stuck_tasks:
                print(f"\n  Found {len(stuck_tasks)} stuck tasks:")
                for task in stuck_tasks[:5]:
                    print(f"    • {task[0]} | status={task[1]} | created={task[2]}")
                if len(stuck_tasks) > 5:
                    print(f"    ... and {len(stuck_tasks) - 5} more")

                if not dry_run:
                    # 清理卡住的任务
                    cursor.execute("""
                        UPDATE tasks
                        SET status = 'cancelled'
                        WHERE status IN ('pending', 'running')
                        AND datetime(updated_at) < datetime('now', '-1 day')
                    """)
                    result["fixed"] = cursor.rowcount
                    conn.commit()
                    print(f"\n  ✅ Cancelled {result['fixed']} stuck tasks")
            else:
                print("  ✅ No stuck tasks")

        conn.close()

    except sqlite3.Error as e:
        print(f"❌ SQLite error: {e}")
        result["error"] = str(e)

    return result


def repair_registry(vault_dir: Path, dry_run: bool = True) -> dict:
    """检查 Registry 状态"""
    atlas_dir = vault_dir / "10-Knowledge" / "Atlas"
    registry_file = atlas_dir / "concept-registry.jsonl"

    result = {
        "registry_exists": registry_file.exists(),
        "fixed": 0,
    }

    if not registry_file.exists():
        print("⚠️  Registry file not found")
        return result

    # 统计
    entry_count = 0
    with open(registry_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entry_count += 1

    print(f"\nRegistry entries: {entry_count}")

    # 检查 Evergreen 文件数量
    evergreen_dir = vault_dir / "10-Knowledge" / "Evergreen"
    if evergreen_dir.exists():
        file_count = len(list(evergreen_dir.glob("*.md")))
        print(f"Evergreen files: {file_count}")

        if file_count != entry_count:
            diff = abs(file_count - entry_count)
            print(f"⚠️  Mismatch: {diff} difference between registry and filesystem")
            print(f"   Run 'ovp-rebuild-registry --write' to sync")
        else:
            print("✅ Registry and filesystem are in sync")

    return result


def print_report(results: dict):
    """打印综合报告"""
    print("\n" + "=" * 60)
    print("Repair Report")
    print("=" * 60)

    for key, result in results.items():
        print(f"\n--- {key} ---")
        if "error" in result:
            print(f"  ❌ {result['error']}")
        elif "fixed" in result:
            print(f"  Fixed: {result['fixed']}")


def main():
    parser = argparse.ArgumentParser(description="Repair stuck transactions and system state")
    parser.add_argument("--transactions", action="store_true",
                        help="修复卡住的事务")
    parser.add_argument("--autopilot", action="store_true",
                        help="修复 Autopilot DB")
    parser.add_argument("--registry", action="store_true",
                        help="检查 Registry 状态")
    parser.add_argument("--all", action="store_true",
                        help="修复所有问题")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry run mode (default)")
    parser.add_argument("--write", action="store_true",
                        help="Actually apply fixes")
    parser.add_argument("--vault-dir", type=Path, default=None,
                        help="Vault directory")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    vault_dir = args.vault_dir or get_vault_dir()

    # --write implies not dry_run
    dry_run = not args.write

    print(f"Repairing vault: {vault_dir}")
    print(f"Mode: {'DRY RUN' if dry_run else 'WRITE'}")

    # 确定要执行哪些修复
    run_transactions = args.transactions or args.all
    run_autopilot = args.autopilot or args.all
    run_registry = args.registry or args.all

    results = {}

    if run_transactions:
        results["transactions"] = repair_transactions(vault_dir, dry_run=dry_run)

    if run_autopilot:
        results["autopilot"] = repair_autopilot(vault_dir, dry_run=dry_run)

    if run_registry:
        results["registry"] = repair_registry(vault_dir, dry_run=dry_run)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)

        if not dry_run:
            total_fixed = sum(r.get("fixed", 0) for r in results.values())
            if total_fixed > 0:
                print(f"\n✅ Total fixes applied: {total_fixed}")
            else:
                print("\n✅ No fixes needed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
