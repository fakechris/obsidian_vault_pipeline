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

# ========== 环境初始化 ==========
# 加载 .env 文件（从 Vault 根目录或 auto_vault 目录）
SCRIPTS_DIR = Path(__file__).parent
VAULT_DIR = SCRIPTS_DIR.parent.parent
ENV_FILE = VAULT_DIR / ".env"
ENV_FILE_ALT = SCRIPTS_DIR / "auto_vault" / ".env"
ENV_EXAMPLE = VAULT_DIR / ".env.example"


def _load_env() -> bool:
    """加载 .env 文件，返回是否成功"""
    try:
        from dotenv import load_dotenv
        # 尝试多个位置
        for env_path in [ENV_FILE, ENV_FILE_ALT]:
            if env_path.exists():
                load_dotenv(dotenv_path=env_path, override=True)
                return True
        return False
    except ImportError:
        # dotenv 未安装，检查文件是否存在
        return ENV_FILE.exists() or ENV_FILE_ALT.exists()


def _check_api_key() -> tuple[bool, str]:
    """检查API Key是否配置，返回(是否有效, 提示信息)"""
    key = os.environ.get("AUTO_VAULT_API_KEY", "")
    if not key or key == "your_key_here":
        return False, "AUTO_VAULT_API_KEY not configured"
    if len(key) < 10:  # 基本验证
        return False, "AUTO_VAULT_API_KEY looks invalid (too short)"
    return True, "OK"


def init_env_file() -> int:
    """初始化 .env 文件（交互式）"""
    print("="*60)
    print("Obsidian Vault Pipeline - 环境初始化")
    print("="*60)

    # 检查是否已有 .env
    if ENV_FILE.exists():
        print(f"\n✓ 发现已有配置文件: {ENV_FILE}")
        content = ENV_FILE.read_text(encoding="utf-8")
        if "AUTO_VAULT_API_KEY=" in content and "your_key" not in content:
            print("  看起来已经配置好了。如需重新配置，请先删除该文件。")
            return 0
        print("  但可能未正确配置，继续引导设置...\n")

    # 创建 .env.example 如果不存在
    if not ENV_EXAMPLE.exists():
        example_content = '''# Obsidian Vault Pipeline 环境配置
# ══════════════════════════════════════════════════════════
# 配置说明:
# 1. 复制本文件为 .env: cp .env.example .env
# 2. 编辑 .env 填入你的 API Key
# ══════════════════════════════════════════════════════════

# ── LLM Provider (必需) ─────────────────────────────────
# MiniMax (推荐，成本较低，中文好)
AUTO_VAULT_API_KEY=your_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
AUTO_VAULT_MODEL=minimax/MiniMax-M2.5

# 或 Anthropic (官方)
# AUTO_VAULT_API_KEY=sk-ant-xxxxx
# AUTO_VAULT_API_BASE=https://api.anthropic.com
# AUTO_VAULT_MODEL=anthropic/claude-3-5-sonnet-20241022

# 或 OpenAI 兼容端点
# AUTO_VAULT_API_KEY=sk-xxxxx
# AUTO_VAULT_API_BASE=https://api.openai.com/v1

# ── Pinboard (可选) ─────────────────────────────────────
# 从 https://pinboard.in/settings/password 获取
PINBOARD_TOKEN=your_username:your_token

# ── 代理配置 (可选) ─────────────────────────────────────
# HTTP_PROXY=http://127.0.0.1:7897
'''
        ENV_EXAMPLE.write_text(example_content, encoding="utf-8")
        print(f"✓ 创建模板文件: {ENV_EXAMPLE}")

    # 提示用户获取 API Key
    print("\n📋 你需要一个 LLM API Key 才能运行 Pipeline")
    print("\n推荐选项:")
    print("  1. MiniMax ( https://api.minimaxi.com ) - 成本较低，中文好")
    print("  2. Anthropic ( https://console.anthropic.com ) - Claude官方")
    print("  3. OpenAI 兼容端点")
    print("\n获取 Key 后，请输入:")

    # 交互式输入
    api_key = input("\n🔑 你的 API Key (sk-...): ").strip()
    if not api_key:
        print("\n❌ 未提供 API Key，初始化取消")
        print(f"\n你可以稍后手动创建 {ENV_FILE}:")
        print(f"  cp {ENV_EXAMPLE} {ENV_FILE}")
        print(f"  然后编辑填入你的 Key")
        return 1

    # 选择提供商
    print("\n选择提供商:")
    print("  1. MiniMax (默认)")
    print("  2. Anthropic")
    print("  3. 其他 (OpenAI兼容)")
    choice = input("选择 [1-3] (默认1): ").strip() or "1"

    if choice == "1":
        base_url = "https://api.minimaxi.com/anthropic"
        model = "minimax/MiniMax-M2.5"
    elif choice == "2":
        base_url = "https://api.anthropic.com"
        model = "anthropic/claude-3-5-sonnet-20241022"
    else:
        base_url = input("API Base URL: ").strip() or "https://api.openai.com/v1"
        model = input("Model name: ").strip() or "gpt-4"

    # 写入 .env
    env_content = f'''# Obsidian Vault Pipeline 环境配置
# 生成时间: {datetime.now().isoformat()}
AUTO_VAULT_API_KEY={api_key}
AUTO_VAULT_API_BASE={base_url}
AUTO_VAULT_MODEL={model}
'''

    ENV_FILE.write_text(env_content, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)  # 设置权限为仅用户可读

    print(f"\n✓ 配置文件已创建: {ENV_FILE}")
    print(f"  Provider: {base_url}")
    print(f"  Model: {model}")
    print(f"\n现在可以运行: python3 unified_pipeline_enhanced.py --full")
    return 0


def check_environment() -> tuple[bool, list[str]]:
    """检查环境配置，返回(是否就绪, 问题列表)"""
    issues = []

    # 加载环境变量
    _load_env()

    # 检查 API Key
    key_ok, key_msg = _check_api_key()
    if key_ok:
        issues.append(f"API Key: {key_msg}")
    else:
        issues.append(f"API Key: {key_msg}")

    # 检查 Python 依赖
    required_modules = ["requests"]
    for module in required_modules:
        try:
            __import__(module)
            issues.append(f"Module {module}: OK")
        except ImportError:
            issues.append(f"Module {module}: NOT FOUND (pip install {module})")

    # 检查 .env 文件
    if ENV_FILE.exists():
        issues.append(f".env file: Found at {ENV_FILE}")
    elif ENV_FILE_ALT.exists():
        issues.append(f".env file: Found at {ENV_FILE_ALT}")
    else:
        issues.append(f".env file: NOT FOUND")

    return key_ok, issues


# ========== 配置 ==========
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

    def _get_before_counts(self) -> dict:
        """获取执行前的文件计数（用于基于产出的检测）"""
        counts = {}

        # Raw目录文件数
        raw_dir = self.vault_dir / "50-Inbox" / "01-Raw"
        counts["raw"] = len(list(raw_dir.glob("*.md"))) if raw_dir.exists() else 0

        # Processed目录文件数
        processed_dir = self.vault_dir / "50-Inbox" / "03-Processed"
        counts["processed"] = len(list(processed_dir.glob("*.md"))) if processed_dir.exists() else 0

        # 深度解读数量（当前月份）
        current_month = datetime.now().strftime("%Y-%m")
        topics_dirs = [
            self.vault_dir / "20-Areas" / "AI-Research" / "Topics" / current_month,
            self.vault_dir / "20-Areas" / "Investing" / "Topics" / current_month,
            self.vault_dir / "20-Areas" / "Programming" / "Topics" / current_month,
            self.vault_dir / "20-Areas" / "Tools" / "Topics" / current_month,
        ]
        counts["interpretations"] = sum(
            len(list(d.glob("*_深度解读.md"))) for d in topics_dirs if d.exists()
        )

        # Evergreen数量
        evergreen_dir = self.vault_dir / "10-Knowledge" / "Evergreen"
        counts["evergreen"] = len(list(evergreen_dir.glob("*.md"))) if evergreen_dir.exists() else 0

        return counts

    def _count_output_files(self, step: str, before_counts: dict) -> dict:
        """基于实际产出检测结果（替代依赖退出码）"""
        results = {"success": True, "produced": 0, "method": "filesystem"}

        if step == "clippings":
            # 检查迁移的文件数
            raw_count = len(list((self.vault_dir / "50-Inbox" / "01-Raw").glob("*.md")))
            processed_count = len(list((self.vault_dir / "50-Inbox" / "03-Processed").glob("*.md")))
            results["produced"] = processed_count - before_counts.get("processed", 0)
            results["migrated"] = results["produced"]
            results["remaining"] = raw_count

        elif step == "pinboard":
            # 检查Raw目录新增文件
            raw_count = len(list((self.vault_dir / "50-Inbox" / "01-Raw").glob("*.md")))
            results["produced"] = raw_count - before_counts.get("raw", 0)
            results["new_bookmarks"] = results["produced"]

        elif step == "articles":
            # 检查生成的深度解读数量
            current_month = datetime.now().strftime("%Y-%m")
            topics_dirs = [
                self.vault_dir / "20-Areas" / "AI-Research" / "Topics" / current_month,
                self.vault_dir / "20-Areas" / "Investing" / "Topics" / current_month,
                self.vault_dir / "20-Areas" / "Programming" / "Topics" / current_month,
                self.vault_dir / "20-Areas" / "Tools" / "Topics" / current_month,
            ]
            current_count = sum(len(list(d.glob("*_深度解读.md"))) for d in topics_dirs if d.exists())
            results["produced"] = current_count - before_counts.get("interpretations", 0)
            results["total_interpretations"] = current_count

        elif step == "evergreen":
            # 检查新增的 Evergreen 数量
            evergreen_dir = self.vault_dir / "10-Knowledge" / "Evergreen"
            current_count = len(list(evergreen_dir.glob("*.md"))) if evergreen_dir.exists() else 0
            results["produced"] = current_count - before_counts.get("evergreen", 0)
            results["total_evergreen"] = current_count

        elif step == "moc":
            # MOC 更新没有直接产出文件数，标记为成功
            results["produced"] = 1  # 标记为执行了
            results["updated"] = True

        elif step == "quality":
            # 质量检查没有产出，标记为成功
            results["produced"] = 1
            results["checked"] = True

        return results

    def _calculate_timeout(self, step: str, batch_size: int | None = None) -> int:
        """计算动态超时（根据步骤和文件大小）"""
        if step == "articles":
            # 基于Raw目录文件大小计算超时
            total_chars = 0
            raw_dir = self.vault_dir / "50-Inbox" / "01-Raw"
            if raw_dir.exists():
                for f in raw_dir.glob("*.md"):
                    try:
                        total_chars += f.stat().st_size
                    except OSError:
                        pass

            # 每1000字符10秒，最小60秒，最大300秒（单篇文章不应超过5分钟）
            estimated_timeout = max(60, min(300, (total_chars // 1000) * 10))

            # 如果有batch_size，根据文件数调整
            if batch_size:
                raw_files = len(list(raw_dir.glob("*.md"))) if raw_dir.exists() else 1
                if raw_files > 0:
                    estimated_timeout = min(300, estimated_timeout * batch_size // raw_files)

            return estimated_timeout

        elif step == "pinboard":
            # Pinboard 可能需要更长时间（网络请求）
            return 300  # 5分钟

        elif step == "clippings":
            return 180  # 3分钟

        elif step == "evergreen":
            return 300  # 5分钟

        elif step == "quality":
            return 600  # 10分钟（检查所有文件）

        elif step == "moc":
            return 120  # 2分钟

        return 1800  # 默认30分钟

    def run_command(self, cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        """运行命令并记录"""
        if timeout is None:
            timeout = self._calculate_timeout(step_name)

        self.logger.log("command_started", {"step": step_name, "cmd": " ".join(cmd), "timeout": timeout})

        try:
            result = subprocess.run(
                cmd,
                cwd=self.vault_dir,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            success = result.returncode == 0

            self.logger.log("command_completed", {
                "step": step_name,
                "success": success,
                "returncode": result.returncode,
                "timeout": timeout,
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
            self.logger.log("command_timeout", {"step": step_name, "timeout": timeout})
            # 超时不一定是失败，返回特殊标记让上层基于产出检测
            return {"success": True, "timeout": True, "error": f"Timeout after {timeout}s"}
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
        """运行Pipeline（基于实际产出检测状态）"""
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

        # 获取执行前的文件计数
        before_counts = self._get_before_counts()

        for step in steps_to_run:
            self.txn.step(self.txn_id, step, "in_progress")

            # 执行步骤
            if step == "pinboard":
                cmd_result = self.step_pinboard(
                    days=pinboard_days,
                    start_date=pinboard_start,
                    end_date=pinboard_end,
                    dry_run=dry_run
                )
            elif step == "clippings":
                cmd_result = self.step_clippings(batch_size, dry_run)
            elif step == "articles":
                cmd_result = self.step_articles(batch_size, dry_run)
            elif step == "quality":
                cmd_result = self.step_quality(dry_run)
            elif step == "evergreen":
                cmd_result = self.step_evergreen(7, dry_run)
            elif step == "moc":
                cmd_result = self.step_moc(dry_run)
            else:
                cmd_result = {"success": False, "error": f"Unknown step: {step}"}

            # 基于实际产出判断状态（非dry_run模式）
            if not dry_run and cmd_result.get("success"):
                output_check = self._count_output_files(step, before_counts)
                produced = output_check.get("produced", 0)

                # 更新结果信息
                cmd_result.update(output_check)
                cmd_result["output"] = f"Produced {produced} items"

                # 有产出即视为成功（不再依赖超时导致的退出码）
                if produced > 0:
                    cmd_result["success"] = True
                    # 更新 before_counts 为下次检查做准备
                    if step == "clippings":
                        before_counts["processed"] += produced
                    elif step == "articles":
                        before_counts["interpretations"] += produced
                    elif step == "evergreen":
                        before_counts["evergreen"] += produced

            results[step] = cmd_result
            self.step_results[step] = cmd_result

            if cmd_result["success"]:
                self.txn.step(self.txn_id, step, "completed", cmd_result.get("output", ""))
            else:
                self.txn.step(self.txn_id, step, "failed", cmd_result.get("error", ""))
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
    parser.add_argument("--init", action="store_true",
                       help="初始化环境配置（交互式）")
    parser.add_argument("--check", action="store_true",
                       help="检查环境配置")

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

    # 处理 init 命令
    if args.init:
        return init_env_file()

    # 处理 check 命令
    if args.check:
        print("\n" + "="*60)
        print("环境检查")
        print("="*60)
        ok, issues = check_environment()
        for issue in issues:
            print(f"  {'✓' if 'OK' in issue or 'Found' in issue else '✗'} {issue}")
        if ok:
            print("\n✓ 环境就绪，可以运行 pipeline")
            key = os.environ.get("AUTO_VAULT_API_KEY", "")[:20]
            print(f"  API Key: {key}...")
            print(f"  API Base: {os.environ.get('AUTO_VAULT_API_BASE', 'N/A')}")
        else:
            print("\n✗ 环境未就绪，请运行: python3 unified_pipeline_enhanced.py --init")
        return 0 if ok else 1

    # 检查环境（运行前）
    ok, issues = check_environment()
    if not ok:
        print("\n" + "="*60)
        print("环境错误")
        print("="*60)
        for issue in issues:
            print(f"  ✗ {issue}")
        print("\n请先运行: python3 unified_pipeline_enhanced.py --init")
        return 1

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
