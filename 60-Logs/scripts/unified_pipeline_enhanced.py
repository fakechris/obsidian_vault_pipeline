#!/usr/bin/env python3
"""
Enhanced Unified Pipeline - 增强版统一自动化调度器
支持Pinboard+Clippings双输入源，支持历史日期处理

Usage:
    # 完整Pipeline（当前新内容）
    python3 unified_pipeline_enhanced.py --full

    # 处理历史Pinboard（指定日期范围）
    python3 unified_pipeline_enhanced.py --pinboard-history 2026-02-01 2026-02-28
    python3 unified_pipeline_enhanced.py --pinboard-days 30

    # 处理历史+当前
    python3 unified_pipeline_enhanced.py --full --pinboard-days 7

    # 仅处理新Pinboard书签
    python3 unified_pipeline_enhanced.py --pinboard-new

    # 单步执行
    python3 unified_pipeline_enhanced.py --step pinboard --pinboard-days 14

Features:
    - Pinboard+Clippings双输入
    - 历史日期范围处理
    - 增量模式（只处理新书签）
    - 全自动深度解读→质检→Evergreen→MOC
    - 统一日志和报告
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ========== 配置 ==========
VAULT_DIR = Path(__file__).parent.parent.parent
SCRIPTS_DIR = Path(__file__).parent
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"
TXN_DIR = VAULT_DIR / "60-Logs" / "transactions"
REPORT_DIR = VAULT_DIR / "60-Logs" / "pipeline-reports"

# Pipeline步骤定义（含Pinboard）
PIPELINE_STEPS = [
    "pinboard",     # 1. 获取Pinboard书签
    "clippings",    # 2. 扫描并迁移Clippings
    "articles",     # 3. 生成深度解读
    "quality",      # 4. 质量检查
    "evergreen",    # 5. 提取Evergreen
    "moc",          # 6. 更新MOC
]


class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            **data
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TransactionManager:
    """事务管理器"""

    def __init__(self, txn_dir: Path):
        self.txn_dir = txn_dir

    def start(self, workflow_type: str, description: str) -> str:
        txn_id = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()[:8]}"
        txn_file = self.txn_dir / f"{txn_id}.json"

        txn_data = {
            "id": txn_id,
            "type": workflow_type,
            "description": description,
            "start_time": datetime.now().isoformat(),
            "status": "in_progress",
            "steps": {},
            "checkpoint": "initialized",
            "last_updated": datetime.now().isoformat()
        }

        txn_file.parent.mkdir(parents=True, exist_ok=True)
        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

        return txn_id

    def step(self, txn_id: str, step_name: str, status: str, output: str = ""):
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["steps"][step_name] = {
            "status": status,
            "output": output,
            "updated_at": datetime.now().isoformat()
        }
        txn_data["checkpoint"] = step_name
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

    def complete(self, txn_id: str):
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["status"] = "completed"
        txn_data["completed_at"] = datetime.now().isoformat()
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

    def fail(self, txn_id: str, reason: str):
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["status"] = "failed"
        txn_data["failure_reason"] = reason
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)


class EnhancedPipeline:
    """增强版Pipeline调度器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger, txn: TransactionManager):
        self.vault_dir = vault_dir
        self.scripts_dir = vault_dir / "60-Logs" / "scripts"
        self.logger = logger
        self.txn = txn
        self.step_results = {}
        self.txn_id = None

    def run_command(self, cmd: list[str], step_name: str) -> dict:
        """运行命令并记录"""
        self.logger.log("command_started", {"step": step_name, "cmd": " ".join(cmd)})

        try:
            result = subprocess.run(
                cmd,
                cwd=self.vault_dir,
                capture_output=True,
                text=True,
                timeout=1800  # 30分钟超时（Pinboard可能需要更长时间）
            )

            success = result.returncode == 0

            self.logger.log("command_completed", {
                "step": step_name,
                "success": success,
                "returncode": result.returncode,
                "stdout": result.stdout[-1000:] if result.stdout else "",
                "stderr": result.stderr[-500:] if result.stderr else ""
            })

            return {
                "success": success,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }

        except subprocess.TimeoutExpired:
            self.logger.log("command_timeout", {"step": step_name})
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            self.logger.log("command_error", {"step": step_name, "error": str(e)})
            return {"success": False, "error": str(e)}

    def step_pinboard(
        self,
        days: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        dry_run: bool = False
    ) -> dict:
        """执行Pinboard处理步骤"""
        print("\n" + "="*60)
        print("STEP 1: Processing Pinboard Bookmarks")
        print("="*60)

        cmd = [
            sys.executable,
            str(self.vault_dir / "pinboard-processor.py"),
        ]

        if start_date and end_date:
            # 指定日期范围
            print(f"  Date range: {start_date} to {end_date}")
            cmd.extend(["--start-date", start_date, "--end-date", end_date])
        elif days:
            # 最近N天
            print(f"  Last {days} days")
            cmd.append(str(days))
        else:
            # 默认最近7天
            cmd.append("7")

        if dry_run:
            cmd.append("--dry-run")
        else:
            cmd.append("--dry-run=false")

        result = self.run_command(cmd, "pinboard")

        if result["success"]:
            print("✓ Pinboard processed successfully")
            # 解析输出中的统计信息
            stdout = result.get("stdout", "")
            if "GitHub:" in stdout:
                for line in stdout.split("\n"):
                    if "GitHub:" in line or "Articles:" in line or "Websites:" in line:
                        print(f"  {line.strip()}")
        else:
            print(f"✗ Pinboard processing failed: {result.get('error', 'Unknown error')}")

        return result

    def step_clippings(self, batch_size: int | None = None, dry_run: bool = False) -> dict:
        """执行Clippings处理步骤"""
        print("\n" + "="*60)
        print("STEP 2: Processing Clippings")
        print("="*60)

        cmd = [
            sys.executable,
            str(self.scripts_dir / "clippings_processor.py"),
        ]
        if dry_run:
            cmd.append("--dry-run")
        if batch_size:
            cmd.extend(["--batch-size", str(batch_size)])

        result = self.run_command(cmd, "clippings")

        if result["success"]:
            print("✓ Clippings processed successfully")
        else:
            print(f"✗ Clippings processing failed: {result.get('error', 'Unknown error')}")

        return result

    def step_articles(self, batch_size: int | None = None, dry_run: bool = False) -> dict:
        """执行文章深度解读步骤"""
        print("\n" + "="*60)
        print("STEP 3: Generating Article Interpretations")
        print("="*60)

        cmd = [
            sys.executable,
            str(self.scripts_dir / "auto_article_processor.py"),
            "--process-inbox"
        ]
        if dry_run:
            cmd.append("--dry-run")
        if batch_size:
            cmd.extend(["--batch-size", str(batch_size)])

        result = self.run_command(cmd, "articles")

        if result["success"]:
            print("✓ Articles processed successfully")
        else:
            print(f"✗ Article processing failed: {result.get('error', 'Unknown error')}")

        return result

    def step_quality(self, dry_run: bool = False) -> dict:
        """执行质量检查步骤"""
        print("\n" + "="*60)
        print("STEP 4: Quality Check")
        print("="*60)

        cmd = [
            sys.executable,
            str(self.scripts_dir / "batch_quality_checker.py"),
            "--all"
        ]
        if dry_run:
            cmd.append("--dry-run")

        result = self.run_command(cmd, "quality")

        if result["success"]:
            print("✓ Quality check completed")
        else:
            print(f"✗ Quality check failed: {result.get('error', 'Unknown error')}")

        return result

    def step_evergreen(self, recent_days: int = 7, dry_run: bool = False) -> dict:
        """执行Evergreen提取步骤"""
        print("\n" + "="*60)
        print("STEP 5: Extracting Evergreen Notes")
        print("="*60)

        cmd = [
            sys.executable,
            str(self.scripts_dir / "auto_evergreen_extractor.py"),
            "--recent", str(recent_days)
        ]
        if dry_run:
            cmd.append("--dry-run")

        result = self.run_command(cmd, "evergreen")

        if result["success"]:
            print("✓ Evergreen extraction completed")
        else:
            print(f"✗ Evergreen extraction failed: {result.get('error', 'Unknown error')}")

        return result

    def step_moc(self, dry_run: bool = False) -> dict:
        """执行MOC更新步骤"""
        print("\n" + "="*60)
        print("STEP 6: Updating MOC Indexes")
        print("="*60)

        cmd = [
            sys.executable,
            str(self.scripts_dir / "auto_moc_updater.py"),
            "--scan"
        ]
        if dry_run:
            cmd.append("--dry-run")

        result = self.run_command(cmd, "moc")

        if result["success"]:
            print("✓ MOC update completed")
        else:
            print(f"✗ MOC update failed: {result.get('error', 'Unknown error')}")

        return result

    def run_pipeline(
        self,
        steps: list[str] | None = None,
        pinboard_days: int | None = None,
        pinboard_start: str | None = None,
        pinboard_end: str | None = None,
        batch_size: int | None = None,
        dry_run: bool = False,
        from_step: str | None = None
    ) -> dict:
        """运行Pipeline"""
        results = {}

        # 确定要运行的步骤
        if steps:
            steps_to_run = steps
        else:
            start_idx = 0
            if from_step and from_step in PIPELINE_STEPS:
                start_idx = PIPELINE_STEPS.index(from_step)
            steps_to_run = PIPELINE_STEPS[start_idx:]

        print(f"\nPipeline steps to run: {', '.join(steps_to_run)}")

        for step in steps_to_run:
            self.txn.step(self.txn_id, step, "in_progress")

            if step == "pinboard":
                result = self.step_pinboard(
                    days=pinboard_days,
                    start_date=pinboard_start,
                    end_date=pinboard_end,
                    dry_run=dry_run
                )
            elif step == "clippings":
                result = self.step_clippings(batch_size, dry_run)
            elif step == "articles":
                result = self.step_articles(batch_size, dry_run)
            elif step == "quality":
                result = self.step_quality(dry_run)
            elif step == "evergreen":
                result = self.step_evergreen(7, dry_run)
            elif step == "moc":
                result = self.step_moc(dry_run)
            else:
                result = {"success": False, "error": f"Unknown step: {step}"}

            results[step] = result
            self.step_results[step] = result

            if result["success"]:
                self.txn.step(self.txn_id, step, "completed")
            else:
                self.txn.step(self.txn_id, step, "failed", result.get("error", ""))
                print(f"\nPipeline stopped at step: {step}")
                self.txn.fail(self.txn_id, f"Failed at step: {step}")
                break

        return results

    def generate_report(self, results: dict) -> str:
        """生成Pipeline报告"""
        lines = []
        lines.append("# Pipeline执行报告")
        lines.append(f"\n生成时间: {datetime.now().isoformat()}")
        lines.append(f"事务ID: {self.txn_id}")

        lines.append("\n## 执行步骤")
        lines.append("\n| 步骤 | 状态 | 详情 |")
        lines.append("|------|------|------|")

        for step, result in results.items():
            status = "✅ 成功" if result.get("success") else "❌ 失败"
            detail = result.get("error", "") if not result.get("success") else "完成"
            lines.append(f"| {step} | {status} | {detail} |")

        all_success = all(r.get("success") for r in results.values())
        lines.append(f"\n## 总体状态")
        lines.append(f"\n**{'全部成功' if all_success else '部分失败'}**")
        lines.append(f"\n完成步骤: {sum(1 for r in results.values() if r.get('success'))}/{len(results)}")

        return "\n".join(lines)

    def save_report(self, report: str) -> Path:
        """保存报告"""
        report_dir = self.vault_dir / "60-Logs" / "pipeline-reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        report_file = report_dir / f"pipeline-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)

        return report_file


def main():
    parser = argparse.ArgumentParser(
        description="增强版统一自动化Pipeline（支持Pinboard+Clippings双输入）"
    )

    # 运行模式
    parser.add_argument("--full", action="store_true",
                       help="完整Pipeline（Pinboard+Clippings+Articles+Quality+Evergreen+MOC）")
    parser.add_argument("--step", choices=PIPELINE_STEPS,
                       help="运行指定步骤")
    parser.add_argument("--from-step", choices=PIPELINE_STEPS,
                       help="从指定步骤开始")

    # Pinboard参数
    pinboard_group = parser.add_argument_group("Pinboard Options")
    pinboard_group.add_argument("--pinboard-new", action="store_true",
                               help="处理新Pinboard书签（增量）")
    pinboard_group.add_argument("--pinboard-days", type=int,
                               help="处理最近N天的Pinboard书签")
    pinboard_group.add_argument("--pinboard-history", nargs=2, metavar=("START", "END"),
                               help="处理历史Pinboard书签（格式: YYYY-MM-DD YYYY-MM-DD）")

    # 其他参数
    parser.add_argument("--batch-size", type=int, help="批次大小（用于articles/clippings）")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--vault-dir", type=Path, default=VAULT_DIR, help="Vault根目录")

    args = parser.parse_args()

    # 初始化
    logger = PipelineLogger(LOG_FILE)
    txn = TransactionManager(TXN_DIR)
    pipeline = EnhancedPipeline(args.vault_dir, logger, txn)

    # 确定运行模式
    if args.full:
        # 完整模式
        steps = None  # 运行所有步骤
        pinboard_days = args.pinboard_days or 7
        pinboard_start = None
        pinboard_end = None
        description = "Full pipeline (Pinboard+Clippings+All)"
    elif args.pinboard_new:
        # 仅处理新Pinboard
        steps = ["pinboard"]
        pinboard_days = 7
        pinboard_start = None
        pinboard_end = None
        description = "New Pinboard bookmarks only"
    elif args.pinboard_history:
        # 历史Pinboard模式
        steps = ["pinboard", "articles", "quality", "evergreen", "moc"]
        pinboard_days = None
        pinboard_start, pinboard_end = args.pinboard_history
        description = f"Historical Pinboard {pinboard_start} to {pinboard_end}"
    elif args.pinboard_days:
        # 最近N天Pinboard（包含后续处理）
        steps = ["pinboard", "articles", "quality", "evergreen", "moc"]
        pinboard_days = args.pinboard_days
        pinboard_start = None
        pinboard_end = None
        description = f"Pinboard last {args.pinboard_days} days + full pipeline"
    elif args.step:
        # 单步模式
        steps = [args.step]
        pinboard_days = args.pinboard_days
        pinboard_start = None
        pinboard_end = None
        description = f"Single step: {args.step}"
    elif args.from_step:
        # 从指定步骤开始
        steps = None
        pinboard_days = args.pinboard_days or 7
        pinboard_start = None
        pinboard_end = None
        description = f"From step: {args.from_step}"
    else:
        parser.print_help()
        sys.exit(1)

    # 创建事务
    pipeline.txn_id = txn.start("enhanced-pipeline", description)
    logger.log("pipeline_started", {
        "txn_id": pipeline.txn_id,
        "mode": "full" if args.full else "custom",
        "steps": steps or "all"
    })

    print("\n" + "="*60)
    print("ENHANCED UNIFIED PIPELINE")
    print(f"Transaction: {pipeline.txn_id}")
    print(f"Description: {description}")
    print("="*60)

    # 执行Pipeline
    results = pipeline.run_pipeline(
        steps=steps,
        pinboard_days=pinboard_days,
        pinboard_start=pinboard_start,
        pinboard_end=pinboard_end,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        from_step=args.from_step
    )

    # 生成和保存报告
    report = pipeline.generate_report(results)
    report_file = pipeline.save_report(report)

    # 完成事务
    all_success = all(r.get("success") for r in results.values())
    if all_success:
        txn.complete(pipeline.txn_id)
        logger.log("pipeline_completed", {"txn_id": pipeline.txn_id})
    else:
        logger.log("pipeline_partial_failure", {
            "txn_id": pipeline.txn_id,
            "failed_steps": [s for s, r in results.items() if not r.get("success")]
        })

    # 输出汇总
    print("\n" + "="*60)
    print("PIPELINE COMPLETE")
    print("="*60)
    print(f"Steps run: {len(results)}")
    print(f"Successful: {sum(1 for r in results.values() if r.get('success'))}")
    print(f"Failed: {sum(1 for r in results.values() if not r.get('success'))}")
    print(f"Report saved: {report_file}")

    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
