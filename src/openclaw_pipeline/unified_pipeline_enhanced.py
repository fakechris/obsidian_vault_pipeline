#!/usr/bin/env python3
"""
Enhanced Unified Pipeline - 增强版统一自动化调度器
支持Pinboard+Clippings双输入源，支持历史日期处理

Usage:
    # 完整Pipeline（当前新内容）
    ovp --full

    # 处理历史Pinboard（指定日期范围）
    ovp --pinboard-history 2026-02-01 2026-02-28
    ovp --pinboard-days 30

    # 处理历史+当前
    ovp --full --pinboard-days 7

    # 仅处理新Pinboard书签
    ovp --pinboard-new

    # 单步执行
    ovp --step pinboard --pinboard-days 14

Features:
    - Pinboard+Clippings双输入
    - 历史日期范围处理
    - 增量模式（只处理新书签）
    - 全自动深度解读→质检→Absorb→MOC→knowledge.db
    - 统一日志和报告
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from .runtime import VaultLayout, resolve_vault_dir
    from .packs.loader import resolve_workflow_profile
    from .batch_quality_checker import collect_quality_files
except ImportError:  # pragma: no cover - script mode fallback
    from runtime import VaultLayout, resolve_vault_dir
    from packs.loader import resolve_workflow_profile
    from batch_quality_checker import collect_quality_files

# ========== 环境初始化 ==========
# 加载 .env 文件（从 Vault 根目录或 auto_vault 目录）
SCRIPTS_DIR = Path(__file__).parent
VAULT_DIR = SCRIPTS_DIR.parent.parent
ENV_FILE = VAULT_DIR / ".env"
ENV_FILE_ALT = SCRIPTS_DIR / "auto_vault" / ".env"
ENV_EXAMPLE = VAULT_DIR / ".env.example"


def parse_pinboard_frontmatter(content: str) -> dict[str, str]:
    """Parse lightweight frontmatter emitted by pinboard-processor."""
    metadata: dict[str, str] = {}
    if not content.startswith("---"):
        return metadata
    parts = content.split("---", 2)
    if len(parts) < 3:
        return metadata
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def detect_pinboard_processor(content: str) -> str | None:
    """Route stale Pinboard files by source/title/tags instead of trusting old type fields."""
    metadata = parse_pinboard_frontmatter(content)
    declared_type = metadata.get("type", "")
    if declared_type.startswith("pinboard-"):
        declared_type = declared_type.split("pinboard-", 1)[1]

    source = metadata.get("source", "").lower()
    title = metadata.get("title", "")
    tags = metadata.get("tags", "").lower()

    if "gist.github.com" in source:
        return "website"
    if "github.com" in source or declared_type == "github":
        return "github"
    if (
        "arxiv.org" in source
        or source.endswith(".pdf")
        or "paper" in tags
        or re.match(r"^\[\d{4}\.\d+\]", title)
        or declared_type == "paper"
    ):
        return "paper"
    if declared_type == "social":
        return "social"
    if declared_type in ("article", "website"):
        return declared_type
    if source:
        return "website"
    return None


def _extract_json_suffix(text: str) -> dict[str, Any] | None:
    """Extract a JSON object payload from stdout that may contain log prefixes."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            payload, end = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        if idx + end == len(raw) and isinstance(payload, dict):
            return payload
    return None


def _load_env(vault_dir: Path | None = None) -> bool:
    """加载 .env 文件，返回是否成功

    尝试顺序:
    1. 指定的 vault_dir
    2. 当前工作目录
    3. 脚本所在目录的 vault 结构
    4. auto_vault 子目录
    """
    try:
        from dotenv import load_dotenv

        # 尝试多个位置
        env_paths = []
        if vault_dir:
            env_paths.append(vault_dir / ".env")
        env_paths.append(Path.cwd() / ".env")  # 当前工作目录
        env_paths.append(ENV_FILE)  # 脚本相对路径
        env_paths.append(ENV_FILE_ALT)  # auto_vault 子目录

        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(dotenv_path=env_path, override=True)
                return True
        return False
    except ImportError:
        # dotenv 未安装，检查文件是否存在
        return any([
            (vault_dir / ".env").exists() if vault_dir else False,
            (Path.cwd() / ".env").exists(),
            ENV_FILE.exists(),
            ENV_FILE_ALT.exists()
        ])


def _get_version() -> str:
    """从pyproject.toml读取版本"""
    try:
        import tomllib
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return data.get("project", {}).get("version", "0.3.2")
    except Exception:
        pass
    return "0.3.2"


def _check_api_key() -> tuple[bool, str]:
    """检查API Key是否配置，返回(是否有效, 提示信息)"""
    # API密钥回退链
    key_fallbacks = (
        "AUTO_VAULT_API_KEY",
        "SPEC_ORCH_LLM_API_KEY",
        "MINIMAX_API_KEY",
        "MINIMAX_CN_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    )
    key = None
    for env_name in key_fallbacks:
        value = os.environ.get(env_name, "")
        if value and value not in ("", "your_key_here", "test_key_for_testing_only"):
            key = value
            break
    if not key:
        return False, "No valid API key found in environment"
    if len(key) < 10:  # 基本验证
        return False, "API key looks invalid (too short)"
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
AUTO_VAULT_MODEL=anthropic/MiniMax-M2.7-highspeed

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
        model = "anthropic/MiniMax-M2.7-highspeed"
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
    print("\n现在可以运行: ovp --full")
    return 0


def check_environment(vault_dir: Path | None = None) -> tuple[bool, list[str]]:
    """检查环境配置，返回(是否就绪, 问题列表)"""
    issues = []

    # 加载环境变量（尝试多个位置）
    _load_env(vault_dir)

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

    # 检查 .env 文件（多个位置）
    env_paths = []
    if vault_dir:
        env_paths.append(vault_dir / ".env")
    env_paths.append(Path.cwd() / ".env")
    env_paths.append(ENV_FILE)
    env_paths.append(ENV_FILE_ALT)

    found_env = None
    for env_path in env_paths:
        if env_path.exists():
            found_env = env_path
            break

    if found_env:
        issues.append(f".env file: Found at {found_env}")
    else:
        issues.append(f".env file: NOT FOUND")

    return key_ok, issues


# ========== 配置 ==========
# Pipeline步骤定义（含Pinboard）
BASE_PIPELINE_STEPS = [
    "pinboard",       # 1. 获取Pinboard书签到 02-Pinboard/
    "pinboard_process", # 2. 处理 02-Pinboard/ 文件到对应处理器
    "clippings",      # 3. 扫描并迁移Clippings到 01-Raw/
    "articles",       # 4. 生成深度解读
    "quality",        # 5. 质量检查
    "fix_links",      # 6. 修复断裂链接
    "absorb",         # 7. 吸收 Evergreen 生命周期动作（quality >= 3.0 才能执行）
    "registry_sync",  # 8. 同步Registry与文件系统
    "moc",            # 9. 更新MOC
    "knowledge_index",  # 10. 刷新派生 knowledge.db
]

OPTIONAL_PIPELINE_STEPS = ["refine"]  # cleanup + breakdown 的批处理重构
STEP_ALIASES = {"evergreen": "absorb"}
PIPELINE_STEP_CHOICES = [*BASE_PIPELINE_STEPS, *OPTIONAL_PIPELINE_STEPS, *STEP_ALIASES.keys()]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SRC = PROJECT_ROOT / "src"


def normalize_step_name(step: str | None) -> str | None:
    if step is None:
        return None
    return STEP_ALIASES.get(step, step)


def pipeline_steps(
    include_refine: bool = False,
    base_steps: list[str] | None = None,
) -> list[str]:
    steps = list(base_steps or BASE_PIPELINE_STEPS)
    if include_refine and "refine" not in steps:
        steps.insert(-1, "refine")
    return steps


def build_execution_plan(args: argparse.Namespace) -> dict[str, Any]:
    """Build the requested execution plan from CLI args."""
    include_refine = bool(getattr(args, "with_refine", False))
    pack_name = getattr(args, "pack", None)
    profile_name = getattr(args, "profile", None)
    pack, profile = resolve_workflow_profile(
        pack_name=pack_name,
        profile_name=profile_name,
        default_profile="full",
    )
    selected_steps = pipeline_steps(include_refine=include_refine, base_steps=profile.stages)
    pinboard_selected_steps = [step for step in selected_steps if step != "clippings"]
    normalized_from_step = (
        normalize_step_name(args.from_step)
        if getattr(args, "from_step", None)
        else None
    )

    def plan_dict(steps: list[str], description: str, pinboard_days: int | None, pinboard_start: str | None, pinboard_end: str | None) -> dict[str, Any]:
        return {
            "pack": pack.name,
            "profile": profile.name,
            "steps": steps,
            "pinboard_days": pinboard_days,
            "pinboard_start": pinboard_start,
            "pinboard_end": pinboard_end,
            "description": description,
        }

    def slice_from_step(steps: list[str]) -> list[str]:
        if normalized_from_step and normalized_from_step in steps:
            return steps[steps.index(normalized_from_step):]
        return steps

    if args.full:
        requested_steps = slice_from_step(selected_steps)
        description = (
            f"Full pipeline from {normalized_from_step} ({pack.name}/{profile.name})"
            if normalized_from_step
            else f"Full pipeline ({pack.name}/{profile.name})"
        )
        return plan_dict(
            requested_steps,
            description,
            args.pinboard_days or 7,
            None,
            None,
        )

    if args.pinboard_new:
        return plan_dict(["pinboard", "pinboard_process"], "New Pinboard bookmarks only", 7, None, None)

    if args.pinboard_history:
        pinboard_start, pinboard_end = args.pinboard_history
        return plan_dict(
            pinboard_selected_steps,
            f"Historical Pinboard {pinboard_start} to {pinboard_end}",
            None,
            pinboard_start,
            pinboard_end,
        )

    if args.pinboard_days:
        return plan_dict(
            pinboard_selected_steps,
            f"Pinboard last {args.pinboard_days} days + full pipeline",
            args.pinboard_days,
            None,
            None,
        )

    if args.step:
        return plan_dict(
            [normalize_step_name(args.step)],
            f"Single step: {normalize_step_name(args.step)}",
            args.pinboard_days,
            None,
            None,
        )

    if args.from_step:
        return plan_dict(
            slice_from_step(selected_steps),
            f"From step: {normalized_from_step}",
            args.pinboard_days or 7,
            None,
            None,
        )

    return {}


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
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
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
        self.layout = VaultLayout.from_vault(vault_dir)
        self.vault_dir = self.layout.vault_dir
        self.scripts_dir = self.vault_dir / "60-Logs" / "scripts"
        self.logger = logger
        self.txn = txn
        self.step_results = {}
        self.txn_id = None

    def _get_before_counts(self) -> dict:
        """获取执行前的文件计数（用于基于产出的检测）"""
        counts = {}

        # Raw目录文件数
        raw_dir = self.layout.raw_dir
        counts["raw"] = len(list(raw_dir.glob("*.md"))) if raw_dir.exists() else 0

        # Processed目录文件数
        processed_dir = self.layout.processed_dir
        counts["processed"] = len(list(processed_dir.rglob("*.md"))) if processed_dir.exists() else 0

        pinboard_dir = self.layout.pinboard_dir
        counts["pinboard"] = len(list(pinboard_dir.glob("*.md"))) if pinboard_dir.exists() else 0

        archive_month_dir = self.layout.pinboard_archive_dir / datetime.now().strftime("%Y-%m")
        counts["pinboard_archived"] = len(list(archive_month_dir.glob("*.md"))) if archive_month_dir.exists() else 0

        # 深度解读数量（当前月份）
        topics_dirs = [
            self.layout.month_topics_dir("AI-Research"),
            self.layout.month_topics_dir("Investing"),
            self.layout.month_topics_dir("Programming"),
            self.layout.month_topics_dir("Tools"),
        ]
        counts["interpretations"] = sum(
            len(list(d.glob("*_深度解读.md"))) for d in topics_dirs if d.exists()
        )

        # Evergreen 数量（供 absorb 产出统计使用）
        evergreen_dir = self.layout.evergreen_dir
        counts["evergreen"] = len(list(evergreen_dir.glob("*.md"))) if evergreen_dir.exists() else 0

        moc_state: dict[str, float] = {}
        for moc_file in self.layout.atlas_dir.glob("*.md"):
            moc_state[str(moc_file)] = moc_file.stat().st_mtime
        for moc_file in self.vault_dir.glob("20-Areas/**/MOC*.md"):
            moc_state[str(moc_file)] = moc_file.stat().st_mtime
        for moc_file in self.vault_dir.glob("20-Areas/**/Topics/*MOC.md"):
            moc_state[str(moc_file)] = moc_file.stat().st_mtime
        counts["moc_state"] = moc_state
        counts["knowledge_db_mtime"] = self.layout.knowledge_db.stat().st_mtime if self.layout.knowledge_db.exists() else 0.0
        refine_log = self.layout.logs_dir / "refine-mutations.jsonl"
        counts["refine_log_mtime"] = refine_log.stat().st_mtime if refine_log.exists() else 0.0

        return counts

    def _count_output_files(self, step: str, before_counts: dict, cmd_result: dict) -> dict:
        """基于实际产出检测结果（替代依赖退出码）"""
        results = {"success": True, "produced": 0, "method": "filesystem"}

        if step == "clippings":
            # 检查迁移的文件数
            raw_count = len(list(self.layout.raw_dir.glob("*.md")))
            processed_count = len(list(self.layout.processed_dir.rglob("*.md")))
            results["produced"] = processed_count - before_counts.get("processed", 0)
            results["migrated"] = results["produced"]
            results["remaining"] = raw_count

        elif step == "pinboard":
            pinboard_count = len(list(self.layout.pinboard_dir.glob("*.md")))
            results["produced"] = pinboard_count - before_counts.get("pinboard", 0)
            results["new_bookmarks"] = results["produced"]

        elif step == "pinboard_process":
            archived_dir = self.layout.pinboard_archive_dir / datetime.now().strftime("%Y-%m")
            archived_count = len(list(archived_dir.glob("*.md"))) if archived_dir.exists() else 0
            results["produced"] = archived_count - before_counts.get("pinboard_archived", 0)
            results["processed"] = cmd_result.get("processed", results["produced"])
            results["skipped"] = cmd_result.get("skipped", 0)
            results["failed"] = cmd_result.get("failed", 0)

        elif step == "articles":
            # 检查生成的深度解读数量
            topics_dirs = [
                self.layout.month_topics_dir("AI-Research"),
                self.layout.month_topics_dir("Investing"),
                self.layout.month_topics_dir("Programming"),
                self.layout.month_topics_dir("Tools"),
            ]
            current_count = sum(len(list(d.glob("*_深度解读.md"))) for d in topics_dirs if d.exists())
            results["produced"] = current_count - before_counts.get("interpretations", 0)
            results["total_interpretations"] = current_count

        elif step == "absorb":
            # 检查 absorb 阶段新增的 Evergreen 数量
            evergreen_dir = self.layout.evergreen_dir
            current_count = len(list(evergreen_dir.glob("*.md"))) if evergreen_dir.exists() else 0
            results["produced"] = current_count - before_counts.get("evergreen", 0)
            results["total_evergreen"] = current_count

        elif step == "moc":
            current_state = {}
            for moc_file in self.layout.atlas_dir.glob("*.md"):
                current_state[str(moc_file)] = moc_file.stat().st_mtime
            for moc_file in self.vault_dir.glob("20-Areas/**/MOC*.md"):
                current_state[str(moc_file)] = moc_file.stat().st_mtime
            for moc_file in self.vault_dir.glob("20-Areas/**/Topics/*MOC.md"):
                current_state[str(moc_file)] = moc_file.stat().st_mtime
            before_state = before_counts.get("moc_state", {})
            changed = {
                path for path, mtime in current_state.items()
                if path not in before_state or before_state[path] != mtime
            }
            results["produced"] = len(changed)
            results["updated"] = bool(changed)
            results["changed_files"] = sorted(changed)

        elif step == "quality":
            checked = cmd_result.get("quality_checked", 0)
            results["produced"] = checked
            results["checked"] = checked > 0
            results["qualified"] = cmd_result.get("quality_qualified", 0)
            results["failed"] = cmd_result.get("quality_failed", 0)

        elif step == "knowledge_index":
            current_mtime = self.layout.knowledge_db.stat().st_mtime if self.layout.knowledge_db.exists() else 0.0
            before_mtime = before_counts.get("knowledge_db_mtime", 0.0)
            results["produced"] = 1 if current_mtime and current_mtime != before_mtime else 0
            results["db_path"] = str(self.layout.knowledge_db)
            results["updated"] = bool(results["produced"])
        elif step == "refine":
            refine_log = self.layout.logs_dir / "refine-mutations.jsonl"
            current_mtime = refine_log.stat().st_mtime if refine_log.exists() else 0.0
            before_mtime = before_counts.get("refine_log_mtime", 0.0)
            results["produced"] = 1 if current_mtime and current_mtime != before_mtime else 0
            results["updated"] = bool(results["produced"])
            results["refine_log"] = str(refine_log)

        return results

    def _calculate_timeout(self, step: str, batch_size: int | None = None) -> int:
        """计算动态超时（根据步骤和文件大小）"""
        if step == "articles":
            # 基于 Raw + Processing 队列体量计算超时
            queue_files: list[Path] = []
            if self.layout.processing_dir.exists():
                queue_files.extend(sorted(self.layout.processing_dir.glob("*.md")))
            if self.layout.raw_dir.exists():
                queue_files.extend(sorted(self.layout.raw_dir.glob("*.md")))

            if batch_size:
                queue_files = queue_files[:batch_size]

            file_count = len(queue_files)
            total_chars = 0
            for f in queue_files:
                try:
                    total_chars += f.stat().st_size
                except OSError:
                    pass

            per_file_budget = max(180, (total_chars // max(file_count, 1)) // 20)
            estimated_timeout = max(300, per_file_budget * max(file_count, 1))
            return min(43200, estimated_timeout)

        elif step == "pinboard":
            # Pinboard 可能需要更长时间（网络请求）
            return 300  # 5分钟

        elif step == "pinboard_process":
            # 处理 pinboard 文件，每个文件最多5分钟
            pinboard_dir = self.vault_dir / "50-Inbox" / "02-Pinboard"
            if pinboard_dir.exists():
                file_count = len(list(pinboard_dir.glob("*.md")))
                return max(60, min(1800, file_count * 300))  # 每人5分钟
            return 300

        elif step == "clippings":
            return 180  # 3分钟

        elif step == "absorb":
            file_count = batch_size or 0
            if file_count <= 0:
                return 300
            return min(14400, max(600, file_count * 120))

        elif step == "quality":
            file_count = batch_size or len(collect_quality_files(self.layout, all_areas=True))
            if file_count <= 0:
                return 300
            return min(14400, max(600, file_count * 90))

        elif step == "fix_links":
            file_count = len(collect_quality_files(self.layout, all_areas=True))
            if file_count <= 0:
                return 300
            return min(10800, max(300, file_count * 40))

        elif step == "moc":
            return 120  # 2分钟
        elif step == "refine":
            return 600  # cleanup + breakdown 可能扫描全库
        elif step == "knowledge_index":
            file_count = len(
                [
                    path
                    for path in self.layout.evergreen_dir.rglob("*.md")
                    if "_Candidates" not in path.parts
                ]
            )
            if file_count <= 0:
                return 300
            return min(14400, max(600, file_count * 2))

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
                timeout=timeout,
                env=self._subprocess_env(),
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
            return {"success": False, "timeout": True, "error": f"Timeout after {timeout}s"}
        except Exception as e:
            self.logger.log("command_error", {"step": step_name, "error": str(e)})
            return {"success": False, "error": str(e)}

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        project_src = str(PROJECT_SRC)
        current_pythonpath = env.get("PYTHONPATH", "")
        segments = [segment for segment in current_pythonpath.split(os.pathsep) if segment]
        if project_src not in segments:
            segments.insert(0, project_src)
        env["PYTHONPATH"] = os.pathsep.join(segments)
        return env

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
            try:
                start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                error = f"Invalid pinboard date format: {start_date} ~ {end_date}. Expected YYYY-MM-DD."
                print(f"✗ Pinboard processing failed: {error}")
                return {"success": False, "error": error}

            if start_day > end_day:
                error = f"Invalid pinboard date range: {start_date} is after {end_date}"
                print(f"✗ Pinboard processing failed: {error}")
                return {"success": False, "error": error}

            if start_day != end_day:
                print(f"  Date range: {start_date} to {end_date}")
                print("  Pinboard only accepts same-day low-level queries; decomposing into daily requests.")
                return self._step_pinboard_by_day(start_day, end_day, dry_run=dry_run)

            print(f"  Single day: {start_date}")
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

    def _step_pinboard_by_day(self, start_day, end_day, dry_run: bool = False) -> dict:
        combined_stdout: list[str] = []
        combined_stderr: list[str] = []
        current = start_day
        day_count = 0

        while current <= end_day:
            day_str = current.isoformat()
            cmd = [
                sys.executable,
                str(self.vault_dir / "pinboard-processor.py"),
                "--start-date",
                day_str,
                "--end-date",
                day_str,
            ]
            if dry_run:
                cmd.append("--dry-run")
            else:
                cmd.append("--dry-run=false")

            result = self.run_command(cmd, "pinboard")
            if result.get("stdout"):
                combined_stdout.append(result["stdout"])
            if result.get("stderr"):
                combined_stderr.append(result["stderr"])

            if not result.get("success"):
                error = (
                    f"Pinboard day {day_str} failed. "
                    "Pinboard low-level queries must stay within one day; "
                    "see that day's stdout/stderr for details."
                )
                print(f"✗ Pinboard processing failed: {error}")
                return {
                    "success": False,
                    "error": error,
                    "stdout": "\n".join(combined_stdout),
                    "stderr": "\n".join(combined_stderr),
                    "days_processed": day_count,
                    "failed_day": day_str,
                }

            day_count += 1
            current += timedelta(days=1)

        print(f"✓ Pinboard processed successfully across {day_count} daily request(s)")
        return {
            "success": True,
            "stdout": "\n".join(combined_stdout),
            "stderr": "\n".join(combined_stderr),
            "days_processed": day_count,
        }

    def step_pinboard_process(self, dry_run: bool = False) -> dict:
        """处理 02-Pinboard/ 中的书签文件，路由到对应处理器"""
        print("\n" + "="*60)
        print("STEP 2: Processing Pinboard Files")
        print("="*60)

        pinboard_dir = self.layout.pinboard_dir
        archive_dir = self.layout.pinboard_archive_dir

        if not pinboard_dir.exists():
            print("  02-Pinboard/ 目录不存在，跳过")
            return {"success": True, "processed": 0, "skipped": 0}

        files = list(pinboard_dir.glob("*.md"))
        if not files:
            print("  没有待处理的 Pinboard 文件")
            return {"success": True, "processed": 0, "skipped": 0}

        print(f"  找到 {len(files)} 个 Pinboard 文件")

        results = {"processed": 0, "skipped": 0, "failed": 0}

        for f in files:
            try:
                content = f.read_text(encoding="utf-8")
                url_type = detect_pinboard_processor(content)
                if not url_type:
                    print(f"  ⚠️  无法识别类型: {f.name}")
                    results["skipped"] += 1
                    continue

                if url_type == "social":
                    print(f"  ⏭️  跳过 social: {f.name}")
                    results["skipped"] += 1
                    continue

                # 构建命令
                if url_type == "github":
                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.auto_github_processor",
                        "--process-single", str(f),
                        "--vault-dir", str(self.vault_dir),
                    ]
                elif url_type == "paper":
                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.auto_paper_processor",
                        "--process-single", str(f),
                        "--vault-dir", str(self.vault_dir),
                    ]
                elif url_type in ("article", "website"):
                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.auto_article_processor",
                        "--process-single", str(f),
                        "--vault-dir", str(self.vault_dir),
                    ]
                else:
                    print(f"  ⏭️  跳过未知类型 {url_type}: {f.name}")
                    results["skipped"] += 1
                    continue

                if dry_run:
                    print(f"  🔍 [DRY RUN] 路由 {url_type}: {f.name}")
                    results["processed"] += 1
                    continue

                # 执行处理器
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(self.vault_dir),
                    timeout=600,
                    env=self._subprocess_env(),
                )

                if result.returncode == 0:
                    print(f"  ✅ {url_type}: {f.name}")
                    results["processed"] += 1

                    # 移动到 archive
                    month_dir = archive_dir / datetime.now().strftime("%Y-%m")
                    month_dir.mkdir(parents=True, exist_ok=True)
                    archive_file = month_dir / f.name
                    f.rename(archive_file)
                else:
                    print(f"  ❌ {url_type} 处理失败: {f.name}")
                    print(f"     {result.stderr[:100]}")
                    results["failed"] += 1

            except Exception as e:
                print(f"  ❌ 处理异常 {f.name}: {e}")
                results["failed"] += 1

        print(f"\n  汇总: 处理 {results['processed']}, 跳过 {results['skipped']}, 失败 {results['failed']}")
        return {"success": results["failed"] == 0, **results}

    def step_clippings(self, batch_size: int | None = None, dry_run: bool = False) -> dict:
        """执行Clippings处理步骤"""
        print("\n" + "="*60)
        print("STEP 3: Processing Clippings")
        print("="*60)

        cmd = [
            sys.executable, "-m", "openclaw_pipeline.clippings_processor",
            "--vault-dir", str(self.vault_dir)
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
        print("STEP 4: Generating Article Interpretations")
        print("="*60)

        cmd = [
            sys.executable, "-m", "openclaw_pipeline.auto_article_processor",
            "--vault-dir", str(self.vault_dir),
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

    def step_quality(self, batch_size: int | None = None, dry_run: bool = False) -> dict:
        """执行质量检查步骤"""
        print("\n" + "="*60)
        print("STEP 5: Quality Check")
        print("="*60)

        target_files = collect_quality_files(self.layout, all_areas=True)
        total_files = len(target_files)
        effective_batch_size = batch_size or total_files

        if total_files == 0:
            print("  没有待质检的深度解读文件")
            return {
                "success": True,
                "quality_checked": 0,
                "quality_qualified": 0,
                "quality_failed": 0,
                "quality_qualified_files": [],
                "quality_results_json": None,
                "quality_score": 0.0,
            }

        aggregated = {
            "success": True,
            "quality_checked": 0,
            "quality_qualified": 0,
            "quality_failed": 0,
            "quality_qualified_files": [],
            "quality_results_json": None,
            "quality_score": 0.0,
        }

        for start_index in range(0, total_files, effective_batch_size):
            current_batch = min(effective_batch_size, total_files - start_index)
            cmd = [
                sys.executable, "-m", "openclaw_pipeline.batch_quality_checker",
                "--all",
                "--vault-dir", str(self.vault_dir),
                "--start-index", str(start_index),
                "--batch-size", str(current_batch),
            ]
            if dry_run:
                cmd.append("--dry-run")

            result = self.run_command(
                cmd,
                "quality",
                timeout=self._calculate_timeout("quality", batch_size=current_batch),
            )

            if not result["success"]:
                print(f"✗ Quality check failed: {result.get('error', 'Unknown error')}")
                result.update(aggregated)
                return result

            batch_payload = None
            stdout = result.get("stdout", "")
            for line in stdout.split("\n"):
                if line.startswith("__QC_JSON__:"):
                    try:
                        batch_payload = json.loads(line.split("__QC_JSON__:", 1)[1].strip())
                    except json.JSONDecodeError:
                        batch_payload = None
                    break

            if batch_payload is None:
                print("✗ Quality check failed: missing __QC_JSON__ payload")
                return {
                    "success": False,
                    "error": "missing_quality_payload",
                    **aggregated,
                }

            aggregated["quality_checked"] += batch_payload.get("checked", 0)
            aggregated["quality_qualified"] += batch_payload.get("qualified", 0)
            aggregated["quality_failed"] += batch_payload.get("failed", 0)
            aggregated["quality_qualified_files"].extend(batch_payload.get("qualified_files", []))
            aggregated["quality_results_json"] = batch_payload.get("results_json")

        if aggregated["quality_checked"] > 0:
            aggregated["quality_score"] = (
                aggregated["quality_qualified"] / aggregated["quality_checked"] * 5.0
            )

        print("✓ Quality check completed")
        return aggregated

    def step_fix_links(self, dry_run: bool = False) -> dict:
        """执行断裂链接修复步骤"""
        print("\n" + "="*60)
        print("STEP 6: Fixing Broken Links")
        print("="*60)

        cmd = [
            sys.executable, "-m", "openclaw_pipeline.commands.migrate_broken_links",
            "--write" if not dry_run else "--dry-run",
            "--vault-dir", str(self.vault_dir),
        ]

        result = self.run_command(cmd, "fix_links", timeout=self._calculate_timeout("fix_links"))

        if result["success"]:
            print("✓ Broken links fixed")
        else:
            print(f"✗ Fix links failed: {result.get('error', 'Unknown error')}")

        return result

    def step_registry_sync(self, dry_run: bool = False) -> dict:
        """执行Registry同步步骤"""
        print("\n" + "="*60)
        print("STEP 8: Syncing Registry with Filesystem")
        print("="*60)

        cmd = [
            sys.executable, "-m", "openclaw_pipeline.commands.rebuild_registry",
            "--write" if not dry_run else "--dry-run",
            "--vault-dir", str(self.vault_dir),
        ]

        result = self.run_command(cmd, "registry_sync", timeout=120)

        if result["success"]:
            print("✓ Registry sync completed")
        else:
            print(f"✗ Registry sync failed: {result.get('error', 'Unknown error')}")

        return result

    def _load_latest_qualified_files(self) -> list[str]:
        results_dir = self.layout.quality_reports_dir
        if not results_dir.exists():
            return []

        candidates = sorted(
            results_dir.glob("quality-results-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for results_file in candidates:
            try:
                payload = json.loads(results_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            qualified_files = payload.get("qualified_files")
            if isinstance(qualified_files, list):
                return [str(Path(path).resolve()) for path in qualified_files if Path(path).exists()]
        return []

    def step_absorb(
        self,
        recent_days: int = 7,
        dry_run: bool = False,
        quality_score: float = -1.0,
        qualified_files: list[str] | None = None,
        batch_size: int | None = None,
    ) -> dict:
        """执行 Absorb 步骤

        Args:
            recent_days: 处理最近N天的深度解读
            dry_run: 预览模式
            quality_score: 质量分数。只有在 normalized_files is None 时，>= 0 且 < 3.0 才阻断执行；
                < 0 表示未执行质量检查（不阻断）。
            qualified_files: 通过质检的深度解读文件列表。提供时会先解析成 normalized_files，
                并过滤为当前仍存在的文件；若为 None，则回退到 _load_latest_qualified_files()。
                只要 normalized_files 非空，就按文件级白名单执行 absorb，不再使用 quality_score
                做整批阻断。
        """
        normalized_files = None
        if qualified_files is None:
            fallback_files = self._load_latest_qualified_files()
            if fallback_files:
                normalized_files = fallback_files
        else:
            normalized_files = [
                str(Path(path).resolve())
                for path in qualified_files
                if Path(path).exists()
            ]
            if not normalized_files:
                print("\n⚠️  No qualified deep-dive files to absorb; skipping absorb stage")
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "no_qualified_files",
                    "output": "No qualified files to absorb",
                    "produced": 0,
                }

        # Quality gate: 仅在没有文件级质检结果时才阻断
        if normalized_files is None and 0 <= quality_score < 3.0:
            print(f"\n⚠️  Quality score ({quality_score:.1f}) < 3.0, blocking absorb stage")
            return {
                "success": False,
                "blocked": True,
                "reason": f"quality_score_too_low ({quality_score:.1f} < 3.0)",
                "error": "Absorb stage blocked due to low quality score",
            }

        print("\n" + "="*60)
        print("STEP 7: Absorbing Knowledge Into Evergreen Layer")
        print("="*60)

        if normalized_files is not None:
            effective_batch_size = batch_size or len(normalized_files)
            aggregated_summary = {
                "files_processed": 0,
                "concepts_extracted": 0,
                "candidates_added": 0,
                "concepts_created": 0,
                "concepts_promoted": 0,
                "concepts_skipped": 0,
                "errors": 0,
            }
            aggregated_results: list[dict[str, Any]] = []
            self.layout.logs_dir.mkdir(parents=True, exist_ok=True)

            for start_index in range(0, len(normalized_files), effective_batch_size):
                batch_files = normalized_files[start_index:start_index + effective_batch_size]
                with tempfile.TemporaryDirectory(prefix="absorb-qualified-", dir=str(self.layout.logs_dir)) as staging_dir:
                    staging_path = Path(staging_dir)
                    used_targets: set[Path] = set()
                    for path in batch_files:
                        source = Path(path)
                        target = staging_path / source.name
                        if target.exists() or target in used_targets:
                            stem = source.stem
                            suffix = source.suffix
                            counter = 2
                            while True:
                                candidate = staging_path / f"{stem}-{counter}{suffix}"
                                if not candidate.exists() and candidate not in used_targets:
                                    target = candidate
                                    break
                                counter += 1
                        used_targets.add(target)
                        try:
                            target.symlink_to(source)
                        except OSError:
                            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

                    cmd = [
                        sys.executable, "-m", "openclaw_pipeline.commands.absorb",
                        "--vault-dir", str(self.vault_dir),
                        "--dir", str(staging_path),
                        "--auto-promote",
                        "--promote-threshold", "1",
                        "--json",
                    ]
                    if dry_run:
                        cmd.append("--dry-run")

                    result = self.run_command(
                        cmd,
                        "absorb",
                        timeout=self._calculate_timeout("absorb", batch_size=len(batch_files)),
                    )
                    if not result["success"]:
                        print(f"✗ Absorb stage failed: {result.get('error', 'Unknown error')}")
                        result["qualified_files"] = normalized_files
                        result["summary"] = aggregated_summary
                        result["results"] = aggregated_results
                        return result

                    payload = _extract_json_suffix(result.get("stdout", ""))
                    if payload is None:
                        print("✗ Absorb stage failed: missing absorb JSON payload")
                        return {
                            "success": False,
                            "error": "missing_absorb_payload",
                            "qualified_files": normalized_files,
                            "summary": aggregated_summary,
                            "results": aggregated_results,
                        }

                    summary = payload.get("summary", {})
                    for key in aggregated_summary:
                        aggregated_summary[key] += int(summary.get(key, 0) or 0)
                    aggregated_results.extend(payload.get("results", []))

            result = {
                "success": True,
                "qualified_files": normalized_files,
                "summary": aggregated_summary,
                "results": aggregated_results,
                "stdout": json.dumps(
                    {
                        "summary": aggregated_summary,
                        "results": aggregated_results,
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        else:
            cmd = [
                sys.executable, "-m", "openclaw_pipeline.commands.absorb",
                "--vault-dir", str(self.vault_dir),
                "--recent", str(recent_days),
                "--json",
            ]
            if dry_run:
                cmd.append("--dry-run")

            result = self.run_command(cmd, "absorb")

        if result["success"]:
            print("✓ Absorb stage completed")
        else:
            print(f"✗ Absorb stage failed: {result.get('error', 'Unknown error')}")

        return result

    def step_evergreen(self, recent_days: int = 7, dry_run: bool = False, quality_score: float = -1.0) -> dict:
        """兼容旧调用，内部转到 Absorb。"""
        return self.step_absorb(recent_days=recent_days, dry_run=dry_run, quality_score=quality_score)

    def step_moc(self, dry_run: bool = False) -> dict:
        """执行MOC更新步骤"""
        print("\n" + "="*60)
        print("STEP 9: Updating MOC Indexes")
        print("="*60)

        cmd = [
            sys.executable, "-m", "openclaw_pipeline.auto_moc_updater",
            "--vault-dir", str(self.vault_dir),
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

    def step_refine(self, dry_run: bool = False) -> dict:
        """执行 Refine 批处理步骤（cleanup + breakdown）。"""
        print("\n" + "="*60)
        print("STEP 10: Refining Existing Evergreen Notes")
        print("="*60)

        cleanup_cmd = [
            sys.executable, "-m", "openclaw_pipeline.commands.cleanup",
            "--vault-dir", str(self.vault_dir),
            "--all",
            "--json",
        ]
        breakdown_cmd = [
            sys.executable, "-m", "openclaw_pipeline.commands.breakdown",
            "--vault-dir", str(self.vault_dir),
            "--all",
            "--json",
        ]
        if not dry_run:
            cleanup_cmd.append("--write")
            breakdown_cmd.append("--write")

        cleanup_result = self.run_command(cleanup_cmd, "refine_cleanup", timeout=300)
        if not cleanup_result["success"]:
            print(f"✗ Cleanup refine failed: {cleanup_result.get('error', 'Unknown error')}")
            return cleanup_result

        breakdown_result = self.run_command(breakdown_cmd, "refine_breakdown", timeout=300)
        if not breakdown_result["success"]:
            print(f"✗ Breakdown refine failed: {breakdown_result.get('error', 'Unknown error')}")
            return breakdown_result

        print("✓ Refine batch completed")
        return {
            "success": True,
            "cleanup": cleanup_result,
            "breakdown": breakdown_result,
        }

    def step_knowledge_index(self, dry_run: bool = False) -> dict:
        """刷新派生 knowledge.db。"""
        print("\n" + "="*60)
        print("STEP 10: Refreshing Knowledge Index")
        print("="*60)

        cmd = [
            sys.executable, "-m", "openclaw_pipeline.commands.knowledge_index",
            "--vault-dir", str(self.vault_dir),
            "--json",
        ]

        result = self.run_command(cmd, "knowledge_index", timeout=self._calculate_timeout("knowledge_index"))

        if result["success"]:
            print("✓ Knowledge index refresh completed")
        else:
            print(f"✗ Knowledge index refresh failed: {result.get('error', 'Unknown error')}")

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
            if from_step and normalize_step_name(from_step) in pipeline_steps():
                start_idx = pipeline_steps().index(normalize_step_name(from_step))
            steps_to_run = pipeline_steps()[start_idx:]

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
            elif step == "pinboard_process":
                cmd_result = self.step_pinboard_process(dry_run)
            elif step == "clippings":
                cmd_result = self.step_clippings(batch_size, dry_run)
            elif step == "articles":
                cmd_result = self.step_articles(batch_size, dry_run)
            elif step == "quality":
                cmd_result = self.step_quality(batch_size=batch_size, dry_run=dry_run)
            elif step == "fix_links":
                cmd_result = self.step_fix_links(dry_run)
            elif step == "absorb":
                # 从 quality 步骤获取质量分数
                quality_score = results.get("quality", {}).get("quality_score", -1.0)
                qualified_files = results.get("quality", {}).get("quality_qualified_files")
                cmd_result = self.step_absorb(
                    7,
                    dry_run,
                    quality_score=quality_score,
                    qualified_files=qualified_files,
                    batch_size=batch_size,
                )
            elif step == "registry_sync":
                cmd_result = self.step_registry_sync(dry_run)
            elif step == "moc":
                cmd_result = self.step_moc(dry_run)
            elif step == "refine":
                cmd_result = self.step_refine(dry_run)
            elif step == "knowledge_index":
                cmd_result = self.step_knowledge_index(dry_run)
            else:
                cmd_result = {"success": False, "error": f"Unknown step: {step}"}

            # 基于实际产出判断状态（非dry_run模式）
            if not dry_run and cmd_result.get("success"):
                output_check = self._count_output_files(step, before_counts, cmd_result)
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
                    elif step == "pinboard":
                        before_counts["pinboard"] += produced
                    elif step == "pinboard_process":
                        before_counts["pinboard_archived"] += produced
                    elif step == "articles":
                        before_counts["interpretations"] += produced
                    elif step == "absorb":
                        before_counts["evergreen"] += produced
                    elif step == "refine":
                        before_counts["refine_log_mtime"] = (self.layout.logs_dir / "refine-mutations.jsonl").stat().st_mtime if (self.layout.logs_dir / "refine-mutations.jsonl").exists() else before_counts.get("refine_log_mtime", 0.0)

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
            if result.get("success"):
                # 显示实际产出数字
                output = result.get("output", "完成")
                # 尝试显示更详细的数字
                if step == "pinboard":
                    new_bm = result.get("new_bookmarks", 0)
                    detail = f"新增书签: {new_bm}"
                elif step == "clippings":
                    migrated = result.get("migrated", 0)
                    remaining = result.get("remaining", 0)
                    detail = f"迁移: {migrated}, 待处理: {remaining}"
                elif step == "articles":
                    produced = result.get("produced", 0)
                    total = result.get("total_interpretations", 0)
                    detail = f"新增: {produced}, 累计: {total}"
                elif step == "absorb":
                    produced = result.get("produced", 0)
                    total = result.get("total_evergreen", 0)
                    detail = f"新增: {produced}, 累计: {total}"
                elif step == "moc":
                    detail = "已更新"
                elif step == "refine":
                    detail = "cleanup + breakdown 已执行"
                elif step == "knowledge_index":
                    detail = "knowledge.db 已刷新"
                elif step == "quality":
                    checked = result.get("quality_checked", 0)
                    qualified = result.get("quality_qualified", 0)
                    failed = result.get("quality_failed", 0)
                    if checked > 0:
                        detail = f"检查: {checked}, 合格: {qualified}, 不合格: {failed}"
                    else:
                        detail = "检查完成"
                else:
                    detail = output
            else:
                detail = result.get("error", "未知错误")
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
        description="增强版统一自动化Pipeline（支持 Pinboard/Clippings、Absorb、Refine、knowledge.db）"
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")

    # 运行模式
    parser.add_argument("--full", action="store_true",
                       help="完整Pipeline（Pinboard+Clippings+Articles+Quality+Absorb+MOC+knowledge.db）")
    parser.add_argument("--step", choices=PIPELINE_STEP_CHOICES,
                       help="运行指定步骤")
    parser.add_argument("--from-step", choices=PIPELINE_STEP_CHOICES,
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
    parser.add_argument("--with-refine", action="store_true", help="在 absorb/moc 之后执行 cleanup + breakdown 批处理")
    parser.add_argument("--pack", default=None, help="Domain pack 名称（默认: default-knowledge）")
    parser.add_argument("--profile", default=None, help="Workflow profile 名称（默认: full）")
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
        ok, issues = check_environment(args.vault_dir)
        for issue in issues:
            print(f"  {'✓' if 'OK' in issue or 'Found' in issue else '✗'} {issue}")
        if ok:
            print("\n✓ 环境就绪，可以运行 pipeline")
            key = os.environ.get("AUTO_VAULT_API_KEY", "")[:20]
            print(f"  API Key: {key}...")
            print(f"  API Base: {os.environ.get('AUTO_VAULT_API_BASE', 'N/A')}")
        else:
            print("\n✗ 环境未就绪，请运行: ovp --init")
        return 0 if ok else 1

    # 检查环境（运行前）
    ok, issues = check_environment(args.vault_dir)
    if not ok:
        print("\n" + "="*60)
        print("环境错误")
        print("="*60)
        for issue in issues:
            print(f"  ✗ {issue}")
        print("\n请先运行: ovp --init")
        return 1

    execution_plan = build_execution_plan(args)
    if not execution_plan:
        parser.print_help()
        sys.exit(1)

    layout = VaultLayout.from_vault(args.vault_dir or VAULT_DIR)

    # 初始化
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    pipeline = EnhancedPipeline(layout.vault_dir, logger, txn)

    steps = execution_plan["steps"]
    pinboard_days = execution_plan["pinboard_days"]
    pinboard_start = execution_plan["pinboard_start"]
    pinboard_end = execution_plan["pinboard_end"]
    description = execution_plan["description"]

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
