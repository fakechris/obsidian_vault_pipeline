"""
ovp-repair - WIGS 自动修复工具

自动/半自动修复断裂点：

Usage:
    ovp-repair --dry-run           # 预览修复
    ovp-repair --auto             # 自动修复低风险问题
    ovp-repair                    # 交互式修复
"""

import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


class RepairTool:
    """WIGS 修复工具"""

    def __init__(self, vault_dir: Path, dry_run: bool = False, auto: bool = False):
        self.vault_dir = vault_dir
        self.dry_run = dry_run
        self.auto = auto
        self.transactions_dir = vault_dir / "60-Logs" / "transactions"
        self.fixed_count = 0
        self.skipped_count = 0

    def log(self, level: str, message: str):
        """日志输出"""
        symbols = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅", "dry": "🔍"}
        print(f"{symbols.get(level, '•')} {message}")

    def confirm(self, prompt: str) -> bool:
        """确认提示"""
        if self.auto:
            return True
        response = input(f"{prompt} [y/N]: ").strip().lower()
        return response in ('y', 'yes')

    # =============================================================================
    # Fix 1: 未完成事务
    # =============================================================================

    def fix_incomplete_transactions(self):
        """修复未完成的事务"""
        self.log("info", "修复: 未完成事务")

        if not self.transactions_dir.exists():
            self.log("warn", "事务目录不存在")
            return

        for txn_file in sorted(self.transactions_dir.glob("*.json")):
            if txn_file.stem == "archive":
                continue
            try:
                txn_data = json.loads(txn_file.read_text())
            except json.JSONDecodeError:
                continue

            if txn_data.get("status") != "in_progress":
                continue

            txn_id = txn_data.get("id")
            self.log("warn", f"发现未完成事务: {txn_id}")

            if self.dry_run:
                self.log("dry", f"  Would mark as completed/aborted")
                continue

            if self.confirm(f"  处理事务 {txn_id}?"):
                # 简单标记为 aborted
                txn_data["status"] = "aborted"
                txn_data["abort_reason"] = "Auto-aborted by repair script"
                txn_data["last_updated"] = datetime.now().isoformat()
                txn_file.write_text(json.dumps(txn_data, indent=2))
                self.log("success", f"  已标记为 aborted: {txn_id}")
                self.fixed_count += 1

    # =============================================================================
    # Fix 2: MOC 索引
    # =============================================================================

    def fix_moc_index(self):
        """修复 MOC 索引"""
        self.log("info", "修复: 更新 MOC 索引")

        areas = ["AI-Research", "Tools", "Investing", "Programming"]
        total_fixed = 0

        for area in areas:
            moc_file = None
            topics_dir = self.vault_dir / "20-Areas" / area / "Topics"

            if topics_dir.exists():
                moc_candidates = list(topics_dir.glob("*MOC*.md")) + list(topics_dir.glob("MOC.md"))
                if moc_candidates:
                    moc_file = moc_candidates[0]

            main_moc = self.vault_dir / "20-Areas" / area / "MOC.md"

            if not topics_dir.exists():
                continue

            unindexed = []
            for di_file in topics_dir.glob("*_深度解读.md"):
                stem = di_file.stem
                is_indexed = False

                if moc_file and moc_file.exists():
                    content = moc_file.read_text()
                    if f"[[{stem}]]" in content or f"[[{stem}.md]]" in content:
                        is_indexed = True

                if main_moc.exists():
                    content = main_moc.read_text()
                    if f"[[{stem}]]" in content or f"[[{stem}.md]]" in content:
                        is_indexed = True

                if not is_indexed:
                    unindexed.append((di_file, stem))

            if not unindexed:
                continue

            self.log("warn", f"{area}: 发现 {len(unindexed)} 个未索引文件")

            for di_file, stem in unindexed:
                if self.dry_run:
                    self.log("dry", f"  Would add to MOC: {stem}")
                    total_fixed += 1
                    continue

                if self.confirm(f"  添加 {stem} 到 MOC?"):
                    target = main_moc if main_moc.exists() else (moc_file if moc_file else None)
                    if target:
                        target.append_text(f"\n- [[{stem}]]")
                        self.log("success", f"  已添加: {stem}")
                        total_fixed += 1
                    else:
                        self.log("error", f"  无法找到目标 MOC")
                else:
                    self.skipped_count += 1

        if total_fixed > 0:
            self.log("success", f"共修复 {total_fixed} 个未索引文件")
        else:
            self.log("success", "所有 MOC 索引已是最新")

    # =============================================================================
    # Fix 3: 重复文件
    # =============================================================================

    def fix_duplicate_files(self):
        """修复重复文件"""
        self.log("info", "修复: 重复文件")

        raw_dir = self.vault_dir / "50-Inbox" / "01-Raw"
        if not raw_dir.exists():
            self.log("error", "Raw 目录不存在")
            return

        quarantine_dir = self.vault_dir / "70-Archive" / ".staged-for-deletion" / datetime.now().strftime("%Y-%m-%d")
        duplicates_found = False

        for dated_file in raw_dir.glob("2026-*.md"):
            without_date = dated_file.name[11:]  # Remove YYYY-MM-DD_
            older_file = raw_dir / without_date

            if older_file.exists():
                duplicates_found = True
                self.log("warn", f"发现重复:")
                self.log("warn", f"  新版本: {dated_file.name}")
                self.log("warn", f"  旧版本: {without_date}")

                if self.dry_run:
                    self.log("dry", f"  Would move {without_date} to quarantine")
                    continue

                if self.confirm(f"  将旧版本移至隔离区?"):
                    quarantine_dir.mkdir(parents=True, exist_ok=True)
                    older_file.rename(quarantine_dir / without_date)
                    self.log("success", f"  已移至: {quarantine_dir / without_date}")
                    self.fixed_count += 1

        if not duplicates_found:
            self.log("success", "未发现重复文件")

    # =============================================================================
    # Fix 4: 初始化 manifest
    # =============================================================================

    def fix_manifest_init(self):
        """初始化 manifest"""
        self.log("info", "修复: Manifest 初始化")

        manifest_file = self.vault_dir / "50-Inbox" / ".manifest.json"

        if manifest_file.exists():
            self.log("success", "Manifest 已存在")
            return

        self.log("warn", "Manifest 不存在")

        if self.dry_run:
            self.log("dry", f"  Would create: {manifest_file}")
            return

        if self.confirm(f"  创建 manifest?"):
            manifest_data = {
                "version": "1.0",
                "last_updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "files": []
            }
            manifest_file.write_text(json.dumps(manifest_data, indent=2))
            self.log("success", f"  已创建: {manifest_file}")
            self.fixed_count += 1

    # =============================================================================
    # 主流程
    # =============================================================================

    def run(self):
        """运行所有修复"""
        print("")
        print("=" * 60)
        print("WIGS Repair Tool")
        print("=" * 60)
        print("")

        if self.dry_run:
            print("🔍 DRY RUN 模式 - 不会执行任何修改")
        elif self.auto:
            print("⚡ AUTO 模式 - 将自动修复低风险问题")
        else:
            print("🔧 INTERACTIVE 模式 - 需要确认")
        print("")

        self.fix_incomplete_transactions()
        print("")

        self.fix_manifest_init()
        print("")

        self.fix_moc_index()
        print("")

        self.fix_duplicate_files()
        print("")

        print("=" * 60)
        print("修复完成")
        print(f"  修复: {self.fixed_count}")
        print(f"  跳过: {self.skipped_count}")
        print("=" * 60)
        print("")

        if not self.dry_run:
            print("验证命令:")
            print("  ovp-lint --check")


def get_vault_dir() -> Path:
    """获取 vault 目录"""
    try:
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True
        ).strip()
        return Path(git_root)
    except subprocess.CalledProcessError:
        return Path.cwd()


def main():
    parser = argparse.ArgumentParser(description="WIGS Repair Tool - 自动修复断裂点")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--auto", action="store_true", help="自动修复低风险问题")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault 目录")

    args = parser.parse_args()

    vault_dir = args.vault_dir or get_vault_dir()

    tool = RepairTool(vault_dir, dry_run=args.dry_run, auto=args.auto)
    tool.run()


if __name__ == "__main__":
    main()
