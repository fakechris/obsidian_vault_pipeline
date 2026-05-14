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
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from .handler_registry import execute_profile_stage_handler
    from .runtime import VaultLayout, looks_like_vault_dir, resolve_vault_dir, vault_workflow_lock
    from .packs.loader import DEFAULT_PACK_NAME, DEFAULT_WORKFLOW_PACK_NAME, PRIMARY_PACK_NAME, resolve_workflow_profile
    from .batch_quality_checker import collect_quality_files
    from .auto_evergreen_extractor import run_absorb_workflow
    from .stage_artifacts import StageArtifactStore, build_file_records, build_stage_fingerprint, hash_file_set, hash_json_payload
    from .step_contracts import (
        STEP_CONTRACTS,
        AbsorbStepResult,
        ArticlesStepResult,
        ClippingsStepResult,
        DedupStepResult,
        EntityExtractStepResult,
        FixLinksStepResult,
        KnowledgeIndexStepResult,
        MocStepResult,
        NoteTypeNormalizeStepResult,
        PinboardProcessStepResult,
        PinboardStepResult,
        QualityStepResult,
        RefineStepResult,
        RegistrySyncStepResult,
        StepContractError,
        StepResult,
        coerce_step_result,
        to_absorb_result as _to_absorb_result,
        to_typed_step_result as _to_typed_step_result,
    )
    from .txn import (
        build_transaction_payload,
        heartbeat_transaction,
        mark_transaction_completed,
        mark_transaction_failed,
        update_transaction_step,
    )
except ImportError:  # pragma: no cover - script mode fallback
    from handler_registry import execute_profile_stage_handler
    from runtime import VaultLayout, looks_like_vault_dir, resolve_vault_dir, vault_workflow_lock
    from packs.loader import DEFAULT_PACK_NAME, DEFAULT_WORKFLOW_PACK_NAME, PRIMARY_PACK_NAME, resolve_workflow_profile
    from batch_quality_checker import collect_quality_files
    from auto_evergreen_extractor import run_absorb_workflow
    from step_contracts import (
        STEP_CONTRACTS,
        AbsorbStepResult,
        ArticlesStepResult,
        ClippingsStepResult,
        DedupStepResult,
        EntityExtractStepResult,
        FixLinksStepResult,
        KnowledgeIndexStepResult,
        MocStepResult,
        NoteTypeNormalizeStepResult,
        PinboardProcessStepResult,
        PinboardStepResult,
        QualityStepResult,
        RefineStepResult,
        RegistrySyncStepResult,
        StepContractError,
        StepResult,
        coerce_step_result,
        to_absorb_result as _to_absorb_result,
        to_typed_step_result as _to_typed_step_result,
    )
    from stage_artifacts import StageArtifactStore, build_file_records, build_stage_fingerprint, hash_file_set, hash_json_payload
    from txn import (
        build_transaction_payload,
        heartbeat_transaction,
        mark_transaction_completed,
        mark_transaction_failed,
        update_transaction_step,
    )

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


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
    """Read the installed package version, falling back to repository metadata."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("obsidian-vault-pipeline")
    except (ImportError, PackageNotFoundError):
        pass

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            tomllib = None

    if tomllib is not None:
        for parent in Path(__file__).resolve().parents:
            pyproject = parent / "pyproject.toml"
            if not pyproject.exists():
                continue
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError, UnicodeError):
                continue
            project_version = data.get("project", {}).get("version")
            if project_version:
                return str(project_version)
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


def init_env_file(vault_dir: Path | str | None = None) -> int:
    """初始化 .env 文件(交互式)"""
    resolved_vault = resolve_vault_dir(vault_dir)
    env_file = resolved_vault / ".env"
    env_example = resolved_vault / ".env.example"

    print("="*60)
    print("Obsidian Vault Pipeline - 环境初始化")
    print("="*60)

    # 检查是否已有 .env
    if env_file.exists():
        print(f"\n✓ 发现已有配置文件: {env_file}")
        content = env_file.read_text(encoding="utf-8")
        if "AUTO_VAULT_API_KEY=" in content and "your_key" not in content:
            print("  看起来已经配置好了。如需重新配置, 请先删除该文件。")
            return 0
        print("  但可能未正确配置, 继续引导设置...\n")

    # 创建 .env.example 如果不存在
    if not env_example.exists():
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
        env_example.write_text(example_content, encoding="utf-8")
        print(f"✓ 创建模板文件: {env_example}")

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
        print(f"\n你可以稍后手动创建 {env_file}:")
        print(f"  cp {env_example} {env_file}")
        print("  然后编辑填入你的 Key")
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

    env_file.write_text(env_content, encoding="utf-8")
    os.chmod(env_file, 0o600)  # 设置权限为仅用户可读

    print(f"\n✓ 配置文件已创建: {env_file}")
    print(f"  Provider: {base_url}")
    print(f"  Model: {model}")
    print("\n现在可以运行: ovp --full")
    return 0


def check_environment(vault_dir: Path | None = None) -> tuple[bool, list[str]]:
    """检查环境配置，返回(是否就绪, 问题列表)"""
    issues = []
    resolved_vault = resolve_vault_dir(vault_dir)

    # 加载环境变量（尝试多个位置）
    _load_env(resolved_vault)

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
    env_paths.append(resolved_vault / ".env")
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
        issues.append(".env file: NOT FOUND")

    vault_ok = looks_like_vault_dir(resolved_vault)
    if vault_ok:
        issues.append(f"Vault root: OK ({resolved_vault})")
    else:
        issues.append(
            "Vault root: not a vault "
            f"({resolved_vault}; expected 10-Knowledge, 20-Areas, 50-Inbox plus .obsidian or root Index.md/Log.md)"
        )

    return key_ok and vault_ok, issues


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
    "entity_extract", # 7b. Entity NER 提取 (从深度解读中提取命名实体)
    "dedup",          # 7c. 去重刚吸收的 Evergreen（scope 限于本轮 absorb 产出）
    "note_type_normalize",  # 8. 规范化 note_type 元数据
    "registry_sync",  # 9. 同步Registry与文件系统
    "moc",            # 10. 更新MOC
    "knowledge_index",  # 11. 刷新派生 knowledge.db
    "ops_state",      # 12. M24.1: rebuild lifecycle projection
]

OPTIONAL_PIPELINE_STEPS = ["refine"]  # cleanup + breakdown 的批处理重构
STEP_ALIASES = {"evergreen": "absorb"}
PIPELINE_STEP_CHOICES = [*BASE_PIPELINE_STEPS, *OPTIONAL_PIPELINE_STEPS, *STEP_ALIASES.keys()]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SRC = PROJECT_ROOT / "src"
QUALITY_STAGE_ALGORITHM_VERSION = "quality:v1"
STAGE_CACHE_CHECKOUT = "checkout"
STAGE_CACHE_RECORD_ONLY = "record_only"
STAGE_CACHE_DISABLED = "disabled"
STAGE_CACHE_POLICIES = {
    "pinboard": STAGE_CACHE_RECORD_ONLY,
    "pinboard_process": STAGE_CACHE_RECORD_ONLY,
    "clippings": STAGE_CACHE_RECORD_ONLY,
    "articles": STAGE_CACHE_RECORD_ONLY,
    "quality": STAGE_CACHE_CHECKOUT,
    "fix_links": STAGE_CACHE_CHECKOUT,
    "absorb": STAGE_CACHE_CHECKOUT,
    "entity_extract": STAGE_CACHE_RECORD_ONLY,
    "note_type_normalize": STAGE_CACHE_RECORD_ONLY,
    "registry_sync": STAGE_CACHE_CHECKOUT,
    "moc": STAGE_CACHE_CHECKOUT,
    "knowledge_index": STAGE_CACHE_CHECKOUT,
    "ops_state": STAGE_CACHE_RECORD_ONLY,
    "refine": STAGE_CACHE_RECORD_ONLY,
}
STAGE_ALGORITHM_VERSIONS = {
    "quality": QUALITY_STAGE_ALGORITHM_VERSION,
    "fix_links": "fix_links:exact-only:v1",
    "absorb": "absorb:qualified-files:item-ledger:v2",
    "note_type_normalize": "note_type_normalize:canonical-note-types:v1",
    "registry_sync": "registry_sync:rebuild-registry:v1",
    "moc": "moc:auto-moc-scan:v1",
    "knowledge_index": "knowledge_index:truth-projection:v1",
    "ops_state": "ops_state:lifecycle-projection:v1",
    "pinboard": "pinboard:fetch:v1",
    "pinboard_process": "pinboard_process:route-and-process:v1",
    "clippings": "clippings:process-inbox:v1",
    "articles": "articles:auto-article-processor:v1",
    "refine": "refine:cleanup-breakdown:v1",
}


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
    incremental = bool(getattr(args, "incremental", False))
    pack_name = getattr(args, "pack", None)
    profile_name = getattr(args, "profile", None)
    pack, profile = resolve_workflow_profile(
        pack_name=pack_name,
        profile_name=profile_name,
        default_profile="full",
        runtime_adapter="pipeline_step",
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

    if incremental:
        requested_steps = slice_from_step(selected_steps)
        description = (
            f"Incremental pipeline from {normalized_from_step} ({pack.name}/{profile.name})"
            if normalized_from_step
            else f"Incremental pipeline ({pack.name}/{profile.name})"
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

    def _txn_file(self, txn_id: str) -> Path:
        return self.txn_dir / f"{txn_id}.json"

    def _read(self, txn_id: str) -> dict[str, Any] | None:
        txn_file = self._txn_file(txn_id)
        if not txn_file.exists():
            return None
        with open(txn_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, txn_id: str, txn_data: dict[str, Any]) -> None:
        txn_file = self._txn_file(txn_id)
        txn_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=txn_file.parent,
            prefix=f".{txn_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_file = Path(f.name)
            try:
                json.dump(txn_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
                raise
        try:
            os.replace(tmp_file, txn_file)
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    def start(
        self,
        workflow_type: str,
        description: str,
        *,
        pack_name: str | None = None,
        workflow_profile: str | None = None,
        planned_steps: list[str] | None = None,
    ) -> str:
        txn_id = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()[:8]}"
        txn_data = build_transaction_payload(
            txn_id,
            workflow_type,
            description,
            pack_name=pack_name,
            workflow_profile=workflow_profile,
            planned_steps=planned_steps,
        )
        self._write(txn_id, txn_data)
        return txn_id

    def step(self, txn_id: str, step_name: str, status: str, output: str = "", **progress_kwargs: Any):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        update_transaction_step(txn_data, step_name, status, output=output, **progress_kwargs)
        self._write(txn_id, txn_data)

    def heartbeat(self, txn_id: str, *, step_name: str | None = None, **kwargs: Any):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        heartbeat_transaction(txn_data, step_name=step_name, **kwargs)
        self._write(txn_id, txn_data)

    def complete(self, txn_id: str):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        mark_transaction_completed(txn_data)
        self._write(txn_id, txn_data)

    def fail(self, txn_id: str, reason: str):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        mark_transaction_failed(txn_data, reason)
        self._write(txn_id, txn_data)


class EnhancedPipeline:
    """增强版Pipeline调度器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger, txn: TransactionManager):
        self.layout = VaultLayout.from_vault(vault_dir)
        self.vault_dir = self.layout.vault_dir
        self.scripts_dir = self.vault_dir / "60-Logs" / "scripts"
        self.logger = logger
        self.txn = txn
        self.step_results: dict[str, StepResult] = {}
        self.txn_id = None
        self.workflow_pack_name = DEFAULT_WORKFLOW_PACK_NAME
        self.workflow_profile_name = "full"
        self.run_mode = "custom"
        # Contract mode: "strict" (default) raises on unknown fields,
        # "warn" emits StepContractWarning, "off" stores raw dicts.
        self.step_contract_mode: str = "strict"

    def _record_step_result(
        self,
        step: str,
        raw: dict[str, Any] | StepResult,
    ) -> StepResult:
        """Coerce a step's raw return value through its declared contract
        and write it to ``self.step_results[step]``.

        In ``warn`` mode (default): unknown fields emit a StepContractWarning
        and are dropped.  In ``strict`` mode: unknown fields raise
        StepContractError.  In ``off`` mode: the raw value is stored as-is
        (legacy fallback for steps that have not been registered yet — only
        permitted when ``step`` is missing from STEP_CONTRACTS).
        """
        if self.step_contract_mode == "off" or step not in STEP_CONTRACTS:
            self.step_results[step] = raw  # type: ignore[assignment]
            return raw  # type: ignore[return-value]
        strict = self.step_contract_mode == "strict"
        typed = coerce_step_result(step, raw, strict=strict)
        self.step_results[step] = typed
        return typed

    def _normalize_quality_target_files(self, target_files: list[str | Path] | None) -> list[Path]:
        if target_files is None:
            return collect_quality_files(self.layout, all_areas=True)

        normalized: list[Path] = []
        seen: set[Path] = set()
        for raw_path in target_files:
            resolved = self._resolve_existing_vault_file(raw_path)
            if resolved is None:
                continue
            path = Path(resolved)
            if path in seen:
                continue
            seen.add(path)
            normalized.append(path)
        return sorted(normalized)

    def _recent_quality_files(self, *, days: int = 1) -> list[Path]:
        cutoff = time.time() - (days * 24 * 60 * 60)
        recent: list[Path] = []
        for path in collect_quality_files(self.layout, all_areas=True):
            try:
                if path.stat().st_mtime >= cutoff:
                    recent.append(path)
            except OSError:
                continue
        return sorted(recent)

    def _incremental_quality_target_files(self, results: dict[str, Any] | None) -> list[str | Path] | None:
        if self.run_mode != "incremental":
            return None
        # ``results["articles"]`` is an ArticlesStepResult or dict; both
        # expose ``.get()`` so no isinstance check is needed.
        article_result = (results or {}).get("articles")
        if article_result is not None and hasattr(article_result, "get"):
            produced_files = article_result.get("produced_files")
            if produced_files is not None:
                return produced_files if isinstance(produced_files, list) else []
        return self._recent_quality_files(days=1)

    def _quality_stage_inputs(self, target_files: list[str | Path] | None = None) -> tuple[list[Path], str, str, str]:
        files = self._normalize_quality_target_files(target_files)
        input_digest = hash_file_set(self.vault_dir, files)
        algorithm_digest = hash_json_payload(
            {
                "stage": "quality",
                "algorithm_version": QUALITY_STAGE_ALGORITHM_VERSION,
            }
        )
        fingerprint = build_stage_fingerprint(
            stage="quality",
            input_digest=input_digest,
            algorithm_digest=algorithm_digest,
            pack_name=self.workflow_pack_name,
            workflow_profile=self.workflow_profile_name,
        )
        return files, input_digest, algorithm_digest, fingerprint

    def _stage_artifact_store(self) -> StageArtifactStore:
        return StageArtifactStore(self.layout.stage_artifacts_dir)

    def _relative_artifact_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.vault_dir).as_posix()
        except ValueError:
            return str(resolved)

    def _existing_files(self, files: list[Path]) -> list[Path]:
        return sorted((path.resolve() for path in files if path.exists()), key=lambda item: self._relative_artifact_path(item))

    def _markdown_files_under(self, directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return self._existing_files(list(directory.rglob("*.md")))

    def _concept_registry_file(self) -> Path:
        return self.layout.atlas_dir / "concept-registry.jsonl"

    def _evergreen_source_files(self) -> list[Path]:
        return self._existing_files(
            [
                path
                for path in self.layout.evergreen_dir.rglob("*.md")
                if "_Candidates" not in path.parts
            ]
        )

    def _moc_output_files(self) -> list[Path]:
        candidates: list[Path] = []
        if self.layout.atlas_dir.exists():
            candidates.extend(self.layout.atlas_dir.glob("*.md"))
        candidates.extend(self.vault_dir.glob("20-Areas/**/MOC*.md"))
        candidates.extend(self.vault_dir.glob("20-Areas/**/Topics/*MOC.md"))
        return self._existing_files(candidates)

    def _knowledge_index_source_files(self) -> list[Path]:
        files: list[Path] = []
        files.extend(self._markdown_files_under(self.layout.evergreen_dir))
        files.extend(self._markdown_files_under(self.layout.atlas_dir))
        files.extend(self._markdown_files_under(self.vault_dir / "20-Areas"))
        registry = self._concept_registry_file()
        if registry.exists():
            files.append(registry)
        entity_dir = self.vault_dir / "10-Knowledge" / "Entity"
        if entity_dir.is_dir():
            files.extend(self._markdown_files_under(entity_dir))
        entity_registry_file = entity_dir / "entity-registry.jsonl"
        if entity_registry_file.exists():
            files.append(entity_registry_file)
        extraction_log = self.vault_dir / "60-Logs" / "entity-extractions.jsonl"
        if extraction_log.exists():
            files.append(extraction_log)
        return self._existing_files([path for path in files if "_Candidates" not in path.parts])

    def _stage_input_files(self, stage: str) -> list[Path]:
        if stage in {"quality", "fix_links"}:
            return self._existing_files(collect_quality_files(self.layout, all_areas=True))
        if stage == "pinboard":
            return self._markdown_files_under(self.layout.pinboard_dir)
        if stage == "pinboard_process":
            return self._markdown_files_under(self.layout.pinboard_dir)
        if stage == "clippings":
            files = []
            files.extend(self._markdown_files_under(self.layout.raw_dir))
            files.extend(self._markdown_files_under(self.layout.clippings_dir))
            return self._existing_files(files)
        if stage == "articles":
            files = []
            files.extend(self._markdown_files_under(self.layout.processing_dir))
            files.extend(self._markdown_files_under(self.layout.raw_dir))
            return self._existing_files(files)
        if stage == "note_type_normalize":
            return self._knowledge_index_source_files()
        if stage == "registry_sync":
            return self._evergreen_source_files()
        if stage == "moc":
            files = self._evergreen_source_files()
            registry = self._concept_registry_file()
            if registry.exists():
                files.append(registry)
            return self._existing_files(files)
        if stage == "knowledge_index":
            return self._knowledge_index_source_files()
        if stage == "absorb":
            # Absorb's real input is whatever quality has marked qualified.
            # We read that from the most-recent quality artifact when
            # available so the artifact's input_digest matches what absorb
            # actually consumed.  Falls through to [] when no quality
            # artifact exists yet — that's strictly worse observability,
            # not a runtime error.
            artifact = self._load_quality_stage_artifact()
            if artifact:
                qualified = artifact.get("outputs", {}).get("qualified_files") or []
                return self._existing_files(
                    [Path(p) for p in qualified if isinstance(p, str)]
                )
            return []
        if stage == "entity_extract":
            return self._stage_input_files("absorb")
        if stage == "refine":
            return self._evergreen_source_files()
        return []

    def _stage_output_files(self, stage: str) -> list[Path]:
        if stage == "registry_sync":
            return self._existing_files([self._concept_registry_file()])
        if stage == "moc":
            return self._moc_output_files()
        if stage == "knowledge_index":
            return self._existing_files([self.layout.knowledge_db])
        if stage == "absorb":
            return self._evergreen_source_files()
        return []

    def _stage_algorithm_digest(self, stage: str) -> str:
        return hash_json_payload(
            {
                "stage": stage,
                "algorithm_version": STAGE_ALGORITHM_VERSIONS.get(stage, f"{stage}:v1"),
            }
        )

    def _absorb_item_ledger_path(self) -> Path:
        return self.layout.logs_dir / "item-ledgers" / "absorb.jsonl"

    def _absorb_item_context(self, source_path: str | Path) -> dict[str, Any]:
        path = Path(source_path).resolve()
        file_records = build_file_records(self.vault_dir, [path])
        file_record = file_records[0]
        input_digest = hash_json_payload(file_record)
        algorithm_digest = self._stage_algorithm_digest("absorb")
        fingerprint = build_stage_fingerprint(
            stage="absorb_item",
            input_digest=input_digest,
            algorithm_digest=algorithm_digest,
            pack_name=self.workflow_pack_name,
            workflow_profile=self.workflow_profile_name,
        )
        return {
            "fingerprint": fingerprint,
            "input_digest": input_digest,
            "algorithm_digest": algorithm_digest,
            "source_path": str(path),
            "source_relpath": file_record["path"],
            "source_sha256": file_record["sha256"],
            "source_size": file_record["size"],
        }

    def _load_absorb_item_statuses(self) -> dict[str, dict[str, Any]]:
        path = self._absorb_item_ledger_path()
        if not path.exists():
            return {}
        statuses: dict[str, dict[str, Any]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return statuses
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("stage") != "absorb":
                continue
            fingerprint = record.get("fingerprint")
            if isinstance(fingerprint, str) and fingerprint:
                statuses[fingerprint] = record
        return statuses

    def _absorb_item_succeeded(self, source_path: str | Path, statuses: dict[str, dict[str, Any]]) -> bool:
        context = self._absorb_item_context(source_path)
        record = statuses.get(context["fingerprint"])
        return bool(record and record.get("status") == "succeeded")

    def _append_absorb_item_record(self, source_path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        error = result.get("error") or event.get("error") or ""
        context = self._absorb_item_context(source_path)
        record = {
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stage": "absorb",
            "status": "failed" if error else "succeeded",
            "run_id": self.txn_id or "",
            "pack_name": self.workflow_pack_name,
            "workflow_profile": self.workflow_profile_name,
            "current_item": str(event.get("current_item") or event.get("file") or ""),
            "error": str(error) if error else "",
            **context,
            "metrics": {
                "concepts_extracted": _safe_int(result.get("concepts_extracted")),
                "concepts_created": _safe_int(result.get("concepts_created")),
                "concepts_promoted": _safe_int(result.get("concepts_promoted")),
                "concepts_skipped": _safe_int(result.get("concepts_skipped")),
                "candidates_added": _safe_int(result.get("candidates_added")),
            },
        }
        path = self._absorb_item_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def _build_stage_artifact_context(
        self,
        stage: str,
        *,
        results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        algorithm_digest = self._stage_algorithm_digest(stage)
        if stage == "absorb":
            quality_result = (results or {}).get("quality", {})
            quality_fingerprint = quality_result.get("quality_stage_fingerprint")
            qualified_files = quality_result.get("quality_qualified_files")
            if not quality_fingerprint:
                artifact = self._load_quality_stage_artifact()
                if artifact:
                    quality_fingerprint = artifact.get("fingerprint")
                    qualified_files = artifact.get("outputs", {}).get("qualified_files")
            input_payload = {
                "quality_stage_fingerprint": quality_fingerprint or "",
                "qualified_files": sorted(str(Path(path).resolve()) for path in (qualified_files or []) if Path(path).exists()),
            }
            input_digest = hash_json_payload(input_payload)
            inputs = {
                **input_payload,
                "qualified_file_count": len(input_payload["qualified_files"]),
            }
        elif stage == "quality":
            files, input_digest, algorithm_digest, fingerprint = self._quality_stage_inputs(
                self._incremental_quality_target_files(results)
            )
            inputs = {
                "files": [self._relative_artifact_path(path) for path in files],
                "file_count": len(files),
            }
        else:
            files = self._stage_input_files(stage)
            input_digest = hash_file_set(self.vault_dir, files)
            inputs = {
                "files": [self._relative_artifact_path(path) for path in files],
                "file_count": len(files),
            }
        if stage != "quality":
            fingerprint = build_stage_fingerprint(
                stage=stage,
                input_digest=input_digest,
                algorithm_digest=algorithm_digest,
                pack_name=self.workflow_pack_name,
                workflow_profile=self.workflow_profile_name,
            )
        output_files = self._stage_output_files(stage)
        outputs = {
            "paths": [self._relative_artifact_path(path) for path in output_files],
            "files": build_file_records(self.vault_dir, output_files),
        }
        return {
            "stage": stage,
            "input_digest": input_digest,
            "algorithm_digest": algorithm_digest,
            "fingerprint": fingerprint,
            "inputs": inputs,
            "outputs": outputs,
        }

    def _checkout_stage_artifact(self, stage: str, *, results: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if STAGE_CACHE_POLICIES.get(stage, STAGE_CACHE_DISABLED) != STAGE_CACHE_CHECKOUT:
            return None
        context = self._build_stage_artifact_context(stage, results=results)
        manifest = self._stage_artifact_store().load(
            stage,
            context["fingerprint"],
            validate_outputs_under=self.vault_dir,
        )
        if not manifest:
            return None
        artifact_path = self._stage_artifact_store().path_for(stage, context["fingerprint"])
        result: dict[str, Any] = {
            "success": True,
            "skipped": True,
            "cache_hit": True,
            "stage_fingerprint": context["fingerprint"],
            "stage_artifact": str(artifact_path),
            "input_digest": context["input_digest"],
            "algorithm_digest": context["algorithm_digest"],
            "produced": 0,
            "output": f"Cache hit: {stage} {context['fingerprint'][:12]}",
        }
        if stage == "quality":
            outputs = manifest.get("outputs", {})
            metrics = manifest.get("metrics", {})
            result.update(
                {
                    "quality_checked": metrics.get("quality_checked", 0),
                    "quality_qualified": metrics.get("quality_qualified", 0),
                    "quality_failed": metrics.get("quality_failed", 0),
                    "quality_score": metrics.get("quality_score", 0.0),
                    "quality_qualified_files": outputs.get("qualified_files", []),
                    "quality_results_json": outputs.get("results_json"),
                    "quality_stage_fingerprint": context["fingerprint"],
                    "quality_stage_artifact": str(artifact_path),
                }
            )
        return result

    def _stage_artifact_metrics(self, result: dict[str, Any]) -> dict[str, Any]:
        keys = {
            "produced",
            "processed",
            "skipped",
            "failed",
            "updated",
            "migrated",
            "remaining",
            "total_evergreen",
            "total_interpretations",
            "new_bookmarks",
        }
        return {key: result[key] for key in keys if key in result}

    def _write_stage_artifact(self, stage: str, result: dict[str, Any], *, results: dict[str, Any] | None = None) -> None:
        if result.get("success") is not True:
            return
        if STAGE_CACHE_POLICIES.get(stage, STAGE_CACHE_DISABLED) not in {STAGE_CACHE_CHECKOUT, STAGE_CACHE_RECORD_ONLY}:
            return
        if stage == "quality":
            return
        context = self._build_stage_artifact_context(stage, results=results)
        manifest = self._stage_artifact_store().write_completed(
            stage=stage,
            fingerprint=context["fingerprint"],
            input_digest=context["input_digest"],
            algorithm_digest=context["algorithm_digest"],
            run_id=self.txn_id,
            pack_name=self.workflow_pack_name,
            workflow_profile=self.workflow_profile_name,
            inputs=context["inputs"],
            outputs=context["outputs"],
            metrics=self._stage_artifact_metrics(result),
        )
        result["stage_fingerprint"] = manifest["fingerprint"]
        result["stage_artifact"] = str(self._stage_artifact_store().path_for(stage, manifest["fingerprint"]))

    def _load_quality_stage_artifact(self) -> dict[str, Any] | None:
        candidate_targets: list[list[str | Path] | None] = []
        if self.run_mode == "incremental":
            candidate_targets.append(self._incremental_quality_target_files(None))
        candidate_targets.append(None)

        artifact = None
        seen_fingerprints: set[str] = set()
        for target_files in candidate_targets:
            _, _, _, fingerprint = self._quality_stage_inputs(target_files)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            artifact = self._stage_artifact_store().load(
                "quality",
                fingerprint,
                validate_outputs_under=self.vault_dir,
            )
            if artifact is not None:
                break
        if artifact is None:
            return None
        artifact_files = artifact.get("outputs", {}).get("qualified_files")
        if isinstance(artifact_files, list):
            for raw_path in artifact_files:
                if not isinstance(raw_path, str) or self._resolve_existing_vault_file(raw_path) is None:
                    return None
        return artifact

    def _resolve_existing_vault_file(self, raw_path: str | Path) -> str | None:
        path = Path(raw_path).resolve()
        try:
            path.relative_to(self.vault_dir.resolve())
        except ValueError:
            return None
        if not path.exists():
            return None
        return str(path)

    def _write_quality_stage_artifact(
        self,
        result: "QualityStepResult | dict[str, Any]",
        files: list[Path],
        input_digest: str,
        algorithm_digest: str,
        fingerprint: str,
    ) -> tuple[str, str] | tuple[None, None]:
        """Write the quality stage artifact and return (fingerprint, artifact_path).

        Returns ``(None, None)`` when ``result.success is not True``.  Callers
        feed the returned tuple back into the QualityStepResult's
        ``quality_stage_fingerprint`` / ``quality_stage_artifact`` fields.
        """
        if (result.get("success") if hasattr(result, "get") else result.get("success")) is not True:
            return (None, None)
        normalized_files = [
            normalized
            for raw_path in (result.get("quality_qualified_files") or [])
            if isinstance(raw_path, (str, Path)) and (normalized := self._resolve_existing_vault_file(raw_path)) is not None
        ]
        base_vault = self.vault_dir.resolve()
        manifest = self._stage_artifact_store().write_completed(
            stage="quality",
            fingerprint=fingerprint,
            input_digest=input_digest,
            algorithm_digest=algorithm_digest,
            run_id=self.txn_id,
            pack_name=self.workflow_pack_name,
            workflow_profile=self.workflow_profile_name,
            inputs={
                "files": [path.resolve().relative_to(base_vault).as_posix() for path in files],
                "file_count": len(files),
            },
            outputs={
                "qualified_files": normalized_files,
                "results_json": result.get("quality_results_json"),
            },
            metrics={
                "quality_checked": result.get("quality_checked", 0) or 0,
                "quality_qualified": result.get("quality_qualified", 0) or 0,
                "quality_failed": result.get("quality_failed", 0) or 0,
                "quality_score": result.get("quality_score", 0.0) or 0.0,
            },
        )
        artifact_path = str(self._stage_artifact_store().path_for("quality", fingerprint))
        return (manifest["fingerprint"], artifact_path)

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
        interpretation_files: list[str] = []
        for directory in topics_dirs:
            if directory.exists():
                interpretation_files.extend(str(path.resolve()) for path in directory.glob("*_深度解读.md"))
        counts["interpretation_files"] = sorted(interpretation_files)

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
        # Surface skip-reason from typed step results so the pipeline
        # report can render ⚠ instead of ✅ for steps that bailed early
        # (e.g. entity_extract returning skipped=True, reason='no_llm_client').
        if cmd_result.get("skipped"):
            results["skipped"] = True
            if cmd_result.get("reason"):
                results["reason"] = cmd_result.get("reason")

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
            results["files_processed"] = cmd_result.get("files_processed", cmd_result.get("processed", results["produced"]))
            results["files_skipped"] = cmd_result.get("files_skipped", cmd_result.get("skipped", 0))
            results["files_failed"] = cmd_result.get("files_failed", cmd_result.get("failed", 0))

        elif step == "articles":
            # 检查生成的深度解读数量
            topics_dirs = [
                self.layout.month_topics_dir("AI-Research"),
                self.layout.month_topics_dir("Investing"),
                self.layout.month_topics_dir("Programming"),
                self.layout.month_topics_dir("Tools"),
            ]
            current_files: list[Path] = []
            for directory in topics_dirs:
                if directory.exists():
                    current_files.extend(sorted(directory.glob("*_深度解读.md")))
            current_count = len(current_files)
            before_files = set(before_counts.get("interpretation_files", []))
            produced_files = [
                resolved
                for path in current_files
                if (resolved := str(path.resolve())) not in before_files
            ]
            results["produced"] = len(produced_files)
            results["total_interpretations"] = current_count
            results["produced_files"] = sorted(produced_files)

        elif step == "absorb":
            # 检查 absorb 阶段新增的 Evergreen 数量
            evergreen_dir = self.layout.evergreen_dir
            current_count = len(list(evergreen_dir.glob("*.md"))) if evergreen_dir.exists() else 0
            results["produced"] = current_count - before_counts.get("evergreen", 0)
            results["total_evergreen"] = current_count

        elif step == "entity_extract":
            results["produced"] = cmd_result.get("produced", 0)
            results["total_entities"] = cmd_result.get("total_entities", 0)
            results["mentions_extracted"] = cmd_result.get("mentions_extracted", 0)

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
            # Use the qualified contract field names; ``quality_checked`` etc.
            # are already populated by step_quality, this is just the
            # produced-count derivation.
            checked = cmd_result.get("quality_checked", 0)
            results["produced"] = checked

        elif step == "note_type_normalize":
            results["produced"] = int(cmd_result.get("note_type_changed", 0) or 0)
            # note_type_changed / note_type_skipped are the contract fields;
            # the producer (run_command stdout parser) already sets them.

        elif step == "knowledge_index":
            current_mtime = self.layout.knowledge_db.stat().st_mtime if self.layout.knowledge_db.exists() else 0.0
            before_mtime = before_counts.get("knowledge_db_mtime", 0.0)
            results["produced"] = 1 if current_mtime and current_mtime != before_mtime else 0
            results["db_path"] = str(self.layout.knowledge_db)
            results["updated"] = bool(results["produced"])
        elif step == "ops_state":
            # M24.1: projection rebuild emits a counts dict in stdout
            # JSON; surface the total for the run summary.
            results["produced"] = int(cmd_result.get("total", 0) or 0)
            results["counts"] = cmd_result.get("counts", {})
            results["pack"] = cmd_result.get("pack", "")
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

        elif step == "note_type_normalize":
            return 300

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

        elif step == "ops_state":
            # Pure sqlite rebuild over the knowledge.db that
            # ``knowledge_index`` just wrote.  Bounded by audit-row
            # count, not file count; 5 minutes is generous for a
            # vault with ~100k audit rows.
            return 300

        return 1800  # 默认30分钟

    def run_command(self, cmd: list[str], step_name: str, timeout: int | None = None) -> dict:
        """运行命令并记录"""
        if timeout is None:
            timeout = self._calculate_timeout(step_name)

        self.logger.log("command_started", {"step": step_name, "cmd": " ".join(cmd), "timeout": timeout})

        try:
            with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as stdout_file, tempfile.NamedTemporaryFile(
                mode="w+",
                encoding="utf-8",
            ) as stderr_file:
                process = subprocess.Popen(
                    cmd,
                    cwd=self.vault_dir,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    env=self._subprocess_env(),
                )
                started = time.monotonic()
                timeout_seconds = float(timeout)
                poll_interval = min(1.0, max(0.1, timeout_seconds / 10.0))
                while True:
                    returncode = process.poll()
                    if returncode is not None:
                        break
                    elapsed = time.monotonic() - started
                    if elapsed > timeout_seconds:
                        process.kill()
                        process.wait()
                        self.logger.log("command_timeout", {"step": step_name, "timeout": timeout})
                        return {"success": False, "timeout": True, "error": f"Timeout after {timeout}s"}
                    if self.txn_id:
                        progress = self._parse_command_progress(self._read_command_output_snapshot(stdout_file.name))
                        self.txn.heartbeat(self.txn_id, step_name=step_name, **progress)
                    time.sleep(poll_interval)

                stdout_file.flush()
                stderr_file.flush()
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read()
                stderr = stderr_file.read()

            success = returncode == 0

            self.logger.log("command_completed", {
                "step": step_name,
                "success": success,
                "returncode": returncode,
                "timeout": timeout,
                "stdout": stdout[-1000:] if stdout else "",
                "stderr": stderr[-500:] if stderr else ""
            })

            return {
                "success": success,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr
            }

        except Exception as e:
            self.logger.log("command_error", {"step": step_name, "error": str(e)})
            return {"success": False, "error": str(e)}

    @staticmethod
    def _read_command_output_snapshot(path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _parse_command_progress(stdout: str) -> dict[str, Any]:
        resolved_matches = list(re.finditer(r"Resolved\s+(\d+)/(\d+)", stdout))
        if resolved_matches:
            match = resolved_matches[-1]
            done = int(match.group(1))
            total = int(match.group(2))
            return {
                "progress_mode": "counted",
                "work_units_total": total,
                "work_units_done": done,
                "current_item": f"Resolved {done}/{total}",
                "progress_summary": f"Resolved {done} of {total} items.",
                "last_meaningful_event": {
                    "event_type": "command_progress",
                    "work_units_done": done,
                    "work_units_total": total,
                    "line": match.group(0),
                },
            }

        found_match = re.search(r"Found\s+(\d+)\s+unique broken mentions", stdout)
        if found_match:
            total = int(found_match.group(1))
            return {
                "progress_mode": "counted",
                "work_units_total": total,
                "work_units_done": 0,
                "current_item": "resolve broken wikilinks",
                "progress_summary": f"Found {total} broken wikilink mentions to resolve.",
                "last_meaningful_event": {
                    "event_type": "command_progress",
                    "work_units_done": 0,
                    "work_units_total": total,
                    "line": found_match.group(0),
                },
            }

        return {}

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        project_src = str(PROJECT_SRC)
        current_pythonpath = env.get("PYTHONPATH", "")
        segments = [segment for segment in current_pythonpath.split(os.pathsep) if segment]
        if project_src not in segments:
            segments.insert(0, project_src)
        env["PYTHONPATH"] = os.pathsep.join(segments)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def step_pinboard(
        self,
        days: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        dry_run: bool = False
    ) -> "PinboardStepResult":
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
                return PinboardStepResult(success=False, error=error)

            if start_day > end_day:
                error = f"Invalid pinboard date range: {start_date} is after {end_date}"
                print(f"✗ Pinboard processing failed: {error}")
                return PinboardStepResult(success=False, error=error)

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

        return _to_typed_step_result("pinboard", result)

    def _step_pinboard_by_day(self, start_day, end_day, dry_run: bool = False) -> "PinboardStepResult":
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
                return _to_typed_step_result("pinboard", {
                    "success": False,
                    "error": error,
                    "stdout": "\n".join(combined_stdout),
                    "stderr": "\n".join(combined_stderr),
                    "days_processed": day_count,
                })

            day_count += 1
            current += timedelta(days=1)

        print(f"✓ Pinboard processed successfully across {day_count} daily request(s)")
        return _to_typed_step_result("pinboard", {
            "success": True,
            "stdout": "\n".join(combined_stdout),
            "stderr": "\n".join(combined_stderr),
            "days_processed": day_count,
        })

    def step_pinboard_process(self, dry_run: bool = False) -> "PinboardProcessStepResult":
        """处理 02-Pinboard/ 中的书签文件，路由到对应处理器"""
        print("\n" + "="*60)
        print("STEP 2: Processing Pinboard Files")
        print("="*60)

        pinboard_dir = self.layout.pinboard_dir
        archive_dir = self.layout.pinboard_archive_dir

        if not pinboard_dir.exists():
            print("  02-Pinboard/ 目录不存在，跳过")
            return PinboardProcessStepResult(success=True, skipped=True, reason="pinboard_dir_missing")

        files = list(pinboard_dir.glob("*.md"))
        if not files:
            print("  没有待处理的 Pinboard 文件")
            return PinboardProcessStepResult(success=True, skipped=True, reason="no_files")

        print(f"  找到 {len(files)} 个 Pinboard 文件")

        results = {"processed": 0, "skipped": 0, "failed": 0}
        total_files = len(files)

        if self.txn_id:
            self.txn.heartbeat(
                self.txn_id,
                step_name="pinboard_process",
                progress_mode="counted",
                work_units_total=total_files,
                work_units_done=0,
                work_units_failed=0,
                progress_summary=f"0/{total_files} files processed",
            )

        for index, f in enumerate(files, start=1):
            try:
                self.logger.log(
                    "pinboard_process_file_started",
                    {"file": f.name, "index": index, "total": total_files},
                )
                if self.txn_id:
                    self.txn.heartbeat(
                        self.txn_id,
                        step_name="pinboard_process",
                        progress_mode="counted",
                        work_units_total=total_files,
                        work_units_done=index - 1,
                        work_units_failed=results["failed"],
                        current_item=f.name,
                        progress_summary=f"{index - 1}/{total_files} files processed",
                        last_meaningful_event={
                            "event_type": "pinboard_process_file_started",
                            "file": f.name,
                        },
                    )
                content = f.read_text(encoding="utf-8")
                url_type = detect_pinboard_processor(content)
                if not url_type:
                    print(f"  ⚠️  无法识别类型: {f.name}")
                    results["skipped"] += 1
                    self.logger.log("pinboard_process_file_skipped", {"file": f.name, "reason": "unknown_type"})
                    if self.txn_id:
                        self.txn.heartbeat(
                            self.txn_id,
                            step_name="pinboard_process",
                            progress_mode="counted",
                            work_units_total=total_files,
                            work_units_done=index,
                            work_units_failed=results["failed"],
                            current_item=f.name,
                            progress_summary=f"{index}/{total_files} files processed",
                            last_meaningful_event={
                                "event_type": "pinboard_process_file_skipped",
                                "file": f.name,
                            },
                        )
                    continue

                if url_type == "social":
                    print(f"  ⏭️  跳过 social: {f.name}")
                    results["skipped"] += 1
                    self.logger.log("pinboard_process_file_skipped", {"file": f.name, "reason": "social"})
                    if self.txn_id:
                        self.txn.heartbeat(
                            self.txn_id,
                            step_name="pinboard_process",
                            progress_mode="counted",
                            work_units_total=total_files,
                            work_units_done=index,
                            work_units_failed=results["failed"],
                            current_item=f.name,
                            progress_summary=f"{index}/{total_files} files processed",
                            last_meaningful_event={
                                "event_type": "pinboard_process_file_skipped",
                                "file": f.name,
                            },
                        )
                    continue

                # 构建命令
                if url_type == "github":
                    cmd = [
                        sys.executable, "-m", "ovp_pipeline.auto_github_processor",
                        "--process-single", str(f),
                        "--vault-dir", str(self.vault_dir),
                    ]
                elif url_type == "paper":
                    cmd = [
                        sys.executable, "-m", "ovp_pipeline.auto_paper_processor",
                        "--process-single", str(f),
                        "--vault-dir", str(self.vault_dir),
                    ]
                elif url_type in ("article", "website"):
                    cmd = [
                        sys.executable, "-m", "ovp_pipeline.auto_article_processor",
                        "--process-single", str(f),
                        "--vault-dir", str(self.vault_dir),
                    ]
                else:
                    print(f"  ⏭️  跳过未知类型 {url_type}: {f.name}")
                    results["skipped"] += 1
                    self.logger.log("pinboard_process_file_skipped", {"file": f.name, "reason": f"unsupported:{url_type}"})
                    if self.txn_id:
                        self.txn.heartbeat(
                            self.txn_id,
                            step_name="pinboard_process",
                            progress_mode="counted",
                            work_units_total=total_files,
                            work_units_done=index,
                            work_units_failed=results["failed"],
                            current_item=f.name,
                            progress_summary=f"{index}/{total_files} files processed",
                            last_meaningful_event={
                                "event_type": "pinboard_process_file_skipped",
                                "file": f.name,
                            },
                        )
                    continue

                if dry_run:
                    print(f"  🔍 [DRY RUN] 路由 {url_type}: {f.name}")
                    results["processed"] += 1
                    self.logger.log("pinboard_process_file_completed", {"file": f.name, "processor": url_type, "dry_run": True})
                    if self.txn_id:
                        self.txn.heartbeat(
                            self.txn_id,
                            step_name="pinboard_process",
                            progress_mode="counted",
                            work_units_total=total_files,
                            work_units_done=index,
                            work_units_failed=results["failed"],
                            current_item=f.name,
                            progress_summary=f"{index}/{total_files} files processed",
                            last_meaningful_event={
                                "event_type": "pinboard_process_file_completed",
                                "file": f.name,
                            },
                        )
                    continue

                # 执行处理器
                result = self.run_command(cmd, "pinboard_process", timeout=600)

                if result.get("success"):
                    print(f"  ✅ {url_type}: {f.name}")
                    results["processed"] += 1

                    # 移动到 archive
                    if f.exists():
                        month_dir = archive_dir / datetime.now().strftime("%Y-%m")
                        month_dir.mkdir(parents=True, exist_ok=True)
                        archive_file = month_dir / f.name
                        f.rename(archive_file)
                        archived_to = str(archive_file)
                    else:
                        archived_to = "processor-managed"
                    self.logger.log("pinboard_process_file_completed", {"file": f.name, "processor": url_type, "archived_to": archived_to})
                    if self.txn_id:
                        self.txn.heartbeat(
                            self.txn_id,
                            step_name="pinboard_process",
                            progress_mode="counted",
                            work_units_total=total_files,
                            work_units_done=index,
                            work_units_failed=results["failed"],
                            current_item=f.name,
                            progress_summary=f"{index}/{total_files} files processed",
                            last_meaningful_event={
                                "event_type": "pinboard_process_file_completed",
                                "file": f.name,
                            },
                        )
                else:
                    print(f"  ❌ {url_type} 处理失败: {f.name}")
                    stderr = str(result.get("stderr") or result.get("error") or "")
                    print(f"     {stderr[:100]}")
                    results["failed"] += 1
                    self.logger.log("pinboard_process_file_failed", {"file": f.name, "processor": url_type, "error": stderr[:500]})
                    if self.txn_id:
                        self.txn.heartbeat(
                            self.txn_id,
                            step_name="pinboard_process",
                            progress_mode="counted",
                            work_units_total=total_files,
                            work_units_done=index,
                            work_units_failed=results["failed"],
                            current_item=f.name,
                            progress_summary=f"{index}/{total_files} files processed",
                            last_meaningful_event={
                                "event_type": "pinboard_process_file_failed",
                                "file": f.name,
                            },
                        )

            except Exception as e:
                print(f"  ❌ 处理异常 {f.name}: {e}")
                results["failed"] += 1
                self.logger.log("pinboard_process_file_failed", {"file": f.name, "error": str(e)})
                if self.txn_id:
                    self.txn.heartbeat(
                        self.txn_id,
                        step_name="pinboard_process",
                        progress_mode="counted",
                        work_units_total=total_files,
                        work_units_done=index,
                        work_units_failed=results["failed"],
                        current_item=f.name,
                        progress_summary=f"{index}/{total_files} files processed",
                        last_meaningful_event={
                            "event_type": "pinboard_process_file_failed",
                            "file": f.name,
                        },
                    )

        print(f"\n  汇总: 处理 {results['processed']}, 跳过 {results['skipped']}, 失败 {results['failed']}")
        return PinboardProcessStepResult(
            success=results["failed"] == 0,
            files_processed=results["processed"],
            files_skipped=results["skipped"],
            files_failed=results["failed"],
        )

    def step_clippings(self, batch_size: int | None = None, dry_run: bool = False) -> "ClippingsStepResult":
        """执行Clippings处理步骤"""
        print("\n" + "="*60)
        print("STEP 3: Processing Clippings")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.clippings_processor",
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

        return _to_typed_step_result("clippings", result)

    def step_articles(self, batch_size: int | None = None, dry_run: bool = False) -> "ArticlesStepResult":
        """执行文章深度解读步骤"""
        print("\n" + "="*60)
        print("STEP 4: Generating Article Interpretations")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.auto_article_processor",
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

        return _to_typed_step_result("articles", result)

    def step_quality(
        self,
        batch_size: int | None = None,
        dry_run: bool = False,
        target_files: list[str | Path] | None = None,
    ) -> "QualityStepResult":
        """执行质量检查步骤"""
        print("\n" + "="*60)
        print("STEP 5: Quality Check")
        print("="*60)

        if batch_size is not None and batch_size <= 0:
            return QualityStepResult(
                success=False,
                error=f"invalid_batch_size ({batch_size} <= 0)",
            )

        quality_files, input_digest, algorithm_digest, fingerprint = self._quality_stage_inputs(target_files)
        total_files = len(quality_files)
        effective_batch_size = batch_size or total_files

        if total_files == 0:
            print("  没有待质检的深度解读文件")
            empty = QualityStepResult(success=True)
            if not dry_run:
                stage_fp, artifact_path = self._write_quality_stage_artifact(
                    empty.to_dict(),
                    quality_files,
                    input_digest,
                    algorithm_digest,
                    fingerprint,
                )
                if stage_fp is not None:
                    empty = replace(
                        empty,
                        quality_stage_fingerprint=stage_fp,
                        quality_stage_artifact=artifact_path,
                    )
            return empty

        aggregated = {
            "success": True,
            "quality_checked": 0,
            "quality_qualified": 0,
            "quality_failed": 0,
            "quality_qualified_files": [],
            "quality_results_json": None,
            "quality_score": 0.0,
        }

        temp_quality_dir = None
        quality_target_dir: Path | None = None
        quality_original_by_name: dict[str, str] = {}
        try:
            if target_files is not None:
                self.layout.logs_dir.mkdir(parents=True, exist_ok=True)
                temp_quality_dir = tempfile.TemporaryDirectory(
                    prefix="quality-targets-",
                    dir=str(self.layout.logs_dir),
                )
                quality_target_dir = Path(temp_quality_dir.name)
                for index, path in enumerate(quality_files):
                    link_name = path.name
                    if (quality_target_dir / link_name).exists():
                        link_name = f"{index:04d}-{path.name}"
                    link = quality_target_dir / link_name
                    try:
                        link.symlink_to(path)
                    except OSError:
                        link.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                    quality_original_by_name[link.name] = str(path.resolve())

            total_batches = (total_files + effective_batch_size - 1) // effective_batch_size
            for batch_index, start_index in enumerate(range(0, total_files, effective_batch_size), start=1):
                current_batch = min(effective_batch_size, total_files - start_index)
                cmd = [
                    sys.executable, "-m", "ovp_pipeline.batch_quality_checker",
                    "--vault-dir", str(self.vault_dir),
                    "--start-index", str(start_index),
                    "--batch-size", str(current_batch),
                ]
                if quality_target_dir is not None:
                    cmd.extend(["--dir", str(quality_target_dir)])
                else:
                    cmd.append("--all")
                if dry_run:
                    cmd.append("--dry-run")

                if self.txn_id:
                    self.txn.heartbeat(
                        self.txn_id,
                        step_name="quality",
                        progress_mode="counted",
                        work_units_total=total_files,
                        work_units_done=aggregated["quality_checked"],
                        work_units_failed=aggregated["quality_failed"],
                        current_item=f"quality batch {batch_index}/{total_batches}",
                        progress_summary=f"{aggregated['quality_checked']}/{total_files} files checked",
                        last_meaningful_event={
                            "event_type": "quality_batch_started",
                            "start_index": start_index,
                            "batch_size": current_batch,
                        },
                    )

                result = self.run_command(
                    cmd,
                    "quality",
                    timeout=self._calculate_timeout("quality", batch_size=current_batch),
                )

                if not result["success"]:
                    print(f"✗ Quality check failed: {result.get('error', 'Unknown error')}")
                    return _to_typed_step_result("quality", {
                        **aggregated, "success": False,
                        "error": str(result.get("error", "")),
                        "stdout": str(result.get("stdout", "")),
                        "stderr": str(result.get("stderr", "")),
                        "returncode": int(result.get("returncode", 0) or 0),
                    })

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
                    return _to_typed_step_result("quality", {
                        **aggregated, "success": False, "error": "missing_quality_payload",
                    })

                aggregated["quality_checked"] += batch_payload.get("checked", 0)
                aggregated["quality_qualified"] += batch_payload.get("qualified", 0)
                aggregated["quality_failed"] += batch_payload.get("failed", 0)
                qualified_files = []
                for raw_path in batch_payload.get("qualified_files", []):
                    original = quality_original_by_name.get(Path(raw_path).name)
                    qualified_files.append(original or raw_path)
                aggregated["quality_qualified_files"].extend(qualified_files)
                aggregated["quality_results_json"] = batch_payload.get("results_json")
                if self.txn_id:
                    self.txn.heartbeat(
                        self.txn_id,
                        step_name="quality",
                        progress_mode="counted",
                        work_units_total=total_files,
                        work_units_done=aggregated["quality_checked"],
                        work_units_failed=aggregated["quality_failed"],
                        current_item=f"quality batch {batch_index}/{total_batches}",
                        progress_summary=f"{aggregated['quality_checked']}/{total_files} files checked",
                        last_meaningful_event={
                            "event_type": "quality_batch_completed",
                            "checked": batch_payload.get("checked", 0),
                            "qualified": batch_payload.get("qualified", 0),
                            "failed": batch_payload.get("failed", 0),
                        },
                    )
        finally:
            if temp_quality_dir is not None:
                temp_quality_dir.cleanup()

        if aggregated["quality_checked"] > 0:
            aggregated["quality_score"] = (
                aggregated["quality_qualified"] / aggregated["quality_checked"] * 5.0
            )

        print("✓ Quality check completed")
        stage_fp = None
        artifact_path = None
        if not dry_run:
            stage_fp, artifact_path = self._write_quality_stage_artifact(
                aggregated,
                quality_files,
                input_digest,
                algorithm_digest,
                fingerprint,
            )
        return QualityStepResult(
            success=True,
            quality_checked=aggregated["quality_checked"],
            quality_qualified=aggregated["quality_qualified"],
            quality_failed=aggregated["quality_failed"],
            quality_qualified_files=list(aggregated["quality_qualified_files"]),
            quality_results_json=aggregated["quality_results_json"],
            quality_score=aggregated["quality_score"],
            quality_stage_fingerprint=stage_fp,
            quality_stage_artifact=artifact_path,
        )

    def step_fix_links(self, dry_run: bool = False) -> "FixLinksStepResult":
        """执行断裂链接修复步骤"""
        print("\n" + "="*60)
        print("STEP 6: Fixing Broken Links")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.migrate_broken_links",
            "--write" if not dry_run else "--dry-run",
            "--exact-only",
            "--vault-dir", str(self.vault_dir),
        ]

        target_files = len(collect_quality_files(self.layout, all_areas=True))
        if self.txn_id:
            self.txn.heartbeat(
                self.txn_id,
                step_name="fix_links",
                progress_mode="counted" if target_files else "indeterminate",
                work_units_total=target_files or None,
                work_units_done=0,
                work_units_failed=0,
                current_item="migrate broken wikilinks",
                progress_summary=(
                    f"Scanning and migrating wikilinks across {target_files} files"
                    if target_files
                    else "Scanning and migrating broken wikilinks"
                ),
                last_meaningful_event={
                    "event_type": "fix_links_started",
                    "target_files": target_files,
                },
            )

        result = self.run_command(cmd, "fix_links", timeout=self._calculate_timeout("fix_links"))

        if result["success"]:
            print("✓ Broken links fixed")
        else:
            print(f"✗ Fix links failed: {result.get('error', 'Unknown error')}")

        return _to_typed_step_result("fix_links", result)

    def step_registry_sync(self, dry_run: bool = False) -> "RegistrySyncStepResult":
        """执行Registry同步步骤"""
        print("\n" + "="*60)
        print("STEP 8: Syncing Registry with Filesystem")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.rebuild_registry",
            "--write" if not dry_run else "--dry-run",
            "--vault-dir", str(self.vault_dir),
        ]

        result = self.run_command(cmd, "registry_sync", timeout=120)

        if result["success"]:
            print("✓ Registry sync completed")
        else:
            print(f"✗ Registry sync failed: {result.get('error', 'Unknown error')}")

        return _to_typed_step_result("registry_sync", result)

    def _build_absorb_progress_callback(
        self,
        *,
        total_files: int | None,
        completed_before: int = 0,
        failed_before: int = 0,
        staged_sources: dict[str, str] | None = None,
        staging_dir: Path | None = None,
        record_item_ledger: bool = False,
    ):
        def _callback(event: dict[str, Any]) -> None:
            effective_total = total_files or int(event.get("files_total") or 0) or None
            batch_done = int(event.get("files_done") or 0)
            batch_failed = int(event.get("files_failed") or 0)
            current_item = str(event.get("current_item") or event.get("file") or "").strip() or None
            # ``staged_sources`` is keyed by absolute staging path so two
            # batch inputs with the same basename (e.g. two README.md
            # files in different vault directories) don't collide.
            # ``current_item`` from the absorb worker is a basename, so
            # rejoin against the staging dir to get the lookup key.
            staged_path_key: str | None = None
            if current_item and staging_dir is not None:
                staged_path_key = str(staging_dir / current_item)
            if (
                record_item_ledger and staged_path_key and staged_sources
                and staged_path_key in staged_sources
            ):
                self._append_absorb_item_record(staged_sources[staged_path_key], event)
            work_units_done = completed_before + batch_done
            work_units_failed = failed_before + batch_failed
            progress_summary = (
                f"{work_units_done}/{effective_total} files processed"
                if effective_total is not None
                else "Progress is currently indeterminate."
            )
            if self.txn_id:
                self.txn.heartbeat(
                    self.txn_id,
                    step_name="absorb",
                    progress_mode="counted" if effective_total is not None else "indeterminate",
                    work_units_total=effective_total,
                    work_units_done=work_units_done,
                    work_units_failed=work_units_failed,
                    current_item=current_item,
                    progress_summary=progress_summary,
                    last_meaningful_event={
                        "event_type": str(event.get("event_type") or "absorb_file_processed"),
                        "file": current_item or "",
                    },
                )

        return _callback

    def _run_absorb_workflow_direct(
        self,
        *,
        dry_run: bool,
        total_files: int | None = None,
        completed_before: int = 0,
        failed_before: int = 0,
        directory: Path | None = None,
        recent: int | None = None,
        staged_sources: dict[str, str] | None = None,
        record_item_ledger: bool = False,
    ) -> dict[str, Any]:
        self.logger.log(
            "command_started",
            {
                "step": "absorb",
                "mode": "direct_workflow",
                "directory": str(directory) if directory else "",
                "recent": recent,
                "total_files": total_files,
            },
        )
        if self.txn_id and total_files is not None:
            self.txn.heartbeat(
                self.txn_id,
                step_name="absorb",
                progress_mode="counted",
                work_units_total=total_files,
                work_units_done=completed_before,
                work_units_failed=failed_before,
                progress_summary=f"{completed_before}/{total_files} files processed",
            )
        try:
            payload = run_absorb_workflow(
                self.vault_dir,
                directory=directory,
                recent=recent,
                dry_run=dry_run,
                auto_promote=True,
                promote_threshold=1,
                progress_callback=self._build_absorb_progress_callback(
                    total_files=total_files,
                    completed_before=completed_before,
                    failed_before=failed_before,
                    staged_sources=staged_sources,
                    staging_dir=directory if staged_sources else None,
                    record_item_ledger=record_item_ledger,
                ),
            )
        except Exception as exc:
            self.logger.log("command_error", {"step": "absorb", "error": str(exc)})
            return {"success": False, "error": str(exc)}

        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        error_count = _safe_int(summary.get("errors"))
        error_count += _safe_int(summary.get("failed"))
        error_count += _safe_int(summary.get("files_failed"))
        if error_count == 0:
            error_count = sum(1 for item in results if isinstance(item, dict) and item.get("error"))
        success = error_count == 0
        stdout = json.dumps(payload, ensure_ascii=False)
        stderr = "" if success else f"{error_count} absorb file(s) failed"
        self.logger.log(
            "command_completed",
            {
                "step": "absorb",
                "success": success,
                "mode": "direct_workflow",
                "directory": str(directory) if directory else "",
                "recent": recent,
                "total_files": total_files,
                "stdout": stdout[-1000:],
                "stderr": stderr,
            },
        )
        # Rewrite each item["file"] from staging-dir path back to its
        # original vault path BEFORE any consumer reads it.  The previous
        # code emitted staging paths into processed_files; once
        # ``step_absorb`` exited the ``with TemporaryDirectory`` block the
        # paths were dead, and ``step_entity_extract`` silently skipped
        # every file via its ``if not fpath.exists(): continue`` guard,
        # producing 0 mentions on every incremental run since PR #99
        # introduced staging.  See test_e2e_acceptance:
        # test_absorb_processed_files_are_vault_paths.
        #
        # ``staged_sources`` is keyed by absolute staging path (not
        # basename) so two batch inputs sharing a basename — two
        # ``README.md`` files in different vault directories, for
        # example — round-trip back to the right vault path.
        if staged_sources:
            for item in results:
                if isinstance(item, dict) and item.get("file"):
                    staged = str(item["file"])
                    if staged in staged_sources:
                        item["file"] = staged_sources[staged]

        promoted_slugs: list[str] = []
        processed_files: list[str] = []
        for item in results:
            if isinstance(item, dict) and item.get("file"):
                processed_files.append(str(item["file"]))
            for concept in (item.get("concepts") or []):
                if concept.get("status") == "promoted_created" and concept.get("slug"):
                    promoted_slugs.append(concept["slug"])
        result = {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "promoted_slugs": promoted_slugs,
            "processed_files": processed_files,
            **payload,
        }
        if not success:
            result["error"] = stderr
        return _to_absorb_result(result)

    def step_absorb(
        self,
        recent_days: int = 7,
        dry_run: bool = False,
        quality_score: float = -1.0,
        qualified_files: list[str] | None = None,
        batch_size: int | None = None,
        require_quality_artifact: bool = False,
    ) -> "AbsorbStepResult":
        """执行 Absorb 步骤

        Args:
            recent_days: 处理最近N天的深度解读
            dry_run: 预览模式
            quality_score: 质量分数。只有在 normalized_files is None 时，>= 0 且 < 3.0 才阻断执行；
                < 0 表示未执行质量检查（不阻断）。
            qualified_files: 通过质检的深度解读文件列表。提供时会先解析成 normalized_files，
                并过滤为当前仍存在的文件。
                只要 normalized_files 非空，就按文件级白名单执行 absorb，不再使用 quality_score
                做整批阻断。
            require_quality_artifact: 在 pipeline 中要求从 quality stage artifact checkout 输入；
                找不到 artifact 时停线，避免扫历史 quality reports。
        """
        if batch_size is not None and batch_size <= 0:
            return _to_absorb_result({
                "success": False,
                "error": f"invalid_batch_size ({batch_size} <= 0)",
            })

        normalized_files = None
        input_artifact: dict[str, Any] | None = None
        if qualified_files is None:
            if require_quality_artifact:
                input_artifact = self._load_quality_stage_artifact()
                if not input_artifact:
                    return _to_absorb_result({
                        "success": False,
                        "blocked": True,
                        "reason": "missing_quality_stage_artifact",
                        "error": "Absorb requires a matching quality stage artifact; refusing to scan historical quality reports.",
                    })
                artifact_files = input_artifact.get("outputs", {}).get("qualified_files")
                if not isinstance(artifact_files, list):
                    return _to_absorb_result({
                        "success": False,
                        "blocked": True,
                        "reason": "invalid_quality_stage_artifact",
                        "error": "Quality stage artifact is missing outputs.qualified_files.",
                    })
                normalized_files = [
                    normalized
                    for path in artifact_files
                    if isinstance(path, str) and (normalized := self._resolve_existing_vault_file(path)) is not None
                ]
        else:
            normalized_files = [
                normalized
                for path in qualified_files
                if isinstance(path, str) and (normalized := self._resolve_existing_vault_file(path)) is not None
            ]

        if normalized_files is not None and not normalized_files:
            print("\n⚠️  No qualified deep-dive files to absorb; skipping absorb stage")
            result = {
                "success": True,
                "skipped": True,
                "reason": "no_qualified_files",
                "output": "No qualified files to absorb",
                "produced": 0,
                "promoted_slugs": [],
                "processed_files": [],
            }
            if input_artifact is not None:
                result["input_artifact"] = {
                    "stage": input_artifact.get("stage"),
                    "fingerprint": input_artifact.get("fingerprint"),
                    "run_id": input_artifact.get("run_id"),
                }
            return _to_absorb_result(result)

        # Quality gate: 仅在没有文件级质检结果时才阻断
        if normalized_files is None and 0 <= quality_score < 3.0:
            print(f"\n⚠️  Quality score ({quality_score:.1f}) < 3.0, blocking absorb stage")
            return _to_absorb_result({
                "success": False,
                "blocked": True,
                "reason": f"quality_score_too_low ({quality_score:.1f} < 3.0)",
                "error": "Absorb stage blocked due to low quality score",
                "promoted_slugs": [],
                "processed_files": [],
            })

        print("\n" + "="*60)
        print("STEP 7: Absorbing Knowledge Into Evergreen Layer")
        print("="*60)

        if normalized_files is not None:
            qualified_input_files = list(normalized_files)
            item_cache_hit_files: list[str] = []
            item_statuses = self._load_absorb_item_statuses()
            pending_files: list[str] = []
            for path in normalized_files:
                if self._absorb_item_succeeded(path, item_statuses):
                    item_cache_hit_files.append(path)
                else:
                    pending_files.append(path)
            normalized_files = pending_files

            total_files = len(qualified_input_files)
            if not normalized_files:
                print("✓ Absorb stage completed from item cache")
                result = {
                    "success": True,
                    "skipped": True,
                    "reason": "all_absorb_items_cached",
                    "qualified_files": qualified_input_files,
                    "pending_qualified_files": [],
                    "item_cache_hits": len(item_cache_hit_files),
                    "item_cache_hit_files": item_cache_hit_files,
                    "summary": {
                        "files_processed": 0,
                        "concepts_extracted": 0,
                        "candidates_added": 0,
                        "concepts_created": 0,
                        "concepts_promoted": 0,
                        "concepts_skipped": 0,
                        "errors": 0,
                    },
                    "results": [],
                    "promoted_slugs": [],
                    "processed_files": [],
                    "stdout": "All qualified files already absorbed",
                    "stderr": "",
                    "produced": 0,
                }
                if input_artifact is not None:
                    result["input_artifact"] = {
                        "stage": input_artifact.get("stage"),
                        "fingerprint": input_artifact.get("fingerprint"),
                        "run_id": input_artifact.get("run_id"),
                    }
                return _to_absorb_result(result)

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
                    staged_sources: dict[str, str] = {}
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
                        # Key by absolute staging path so two inputs with
                        # the same basename don't overwrite each other in
                        # the dict.  The collision guard above already
                        # gives them distinct ``-N`` filenames in staging,
                        # but keying by full path makes the contract
                        # explicit and survives any future change to
                        # the rename logic.
                        staged_sources[str(target)] = str(source)
                        try:
                            target.symlink_to(source)
                        except OSError:
                            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

                    result = self._run_absorb_workflow_direct(
                        directory=staging_path,
                        dry_run=dry_run,
                        total_files=total_files,
                        completed_before=len(item_cache_hit_files) + aggregated_summary["files_processed"],
                        failed_before=aggregated_summary["errors"],
                        staged_sources=staged_sources,
                        record_item_ledger=not dry_run,
                    )
                    # result is AbsorbStepResult from _run_absorb_workflow_direct
                    if not result["success"]:
                        print(f"✗ Absorb stage failed: {result.get('error', 'Unknown error')}")
                        batch_summary = result.get("summary") or {}
                        if isinstance(batch_summary, dict):
                            for key in aggregated_summary:
                                aggregated_summary[key] += _safe_int(batch_summary.get(key))
                        batch_results_list = result.get("results") or []
                        if isinstance(batch_results_list, list):
                            aggregated_results.extend(batch_results_list)
                        return _to_absorb_result({
                            "success": False,
                            "error": result.get("error"),
                            "stdout": result.get("stdout", ""),
                            "stderr": result.get("stderr", ""),
                            "qualified_files": qualified_input_files,
                            "pending_qualified_files": normalized_files,
                            "item_cache_hits": len(item_cache_hit_files),
                            "item_cache_hit_files": item_cache_hit_files,
                            "summary": aggregated_summary,
                            "results": aggregated_results,
                            "promoted_slugs": [
                                concept["slug"]
                                for item in aggregated_results
                                if isinstance(item, dict)
                                for concept in (item.get("concepts") or [])
                                if concept.get("status") == "promoted_created" and concept.get("slug")
                            ],
                            "processed_files": [
                                str(item["file"])
                                for item in aggregated_results
                                if isinstance(item, dict) and item.get("file")
                            ],
                        })

                    batch_summary = result.get("summary") or {}
                    for key in aggregated_summary:
                        aggregated_summary[key] += int(batch_summary.get(key, 0) or 0)
                    aggregated_results.extend(result.get("results") or [])

            aggregated_promoted_slugs = [
                concept["slug"]
                for item in aggregated_results
                if isinstance(item, dict)
                for concept in (item.get("concepts") or [])
                if concept.get("status") == "promoted_created" and concept.get("slug")
            ]
            aggregated_processed_files = [
                str(item["file"])
                for item in aggregated_results
                if isinstance(item, dict) and item.get("file")
            ]
            payload = {
                "success": True,
                "qualified_files": qualified_input_files,
                "pending_qualified_files": normalized_files,
                "item_cache_hits": len(item_cache_hit_files),
                "item_cache_hit_files": item_cache_hit_files,
                "summary": aggregated_summary,
                "results": aggregated_results,
                "promoted_slugs": aggregated_promoted_slugs,
                "processed_files": aggregated_processed_files,
                "stdout": json.dumps({"summary": aggregated_summary, "results": aggregated_results}, ensure_ascii=False),
                "stderr": "",
            }
            if input_artifact is not None:
                payload["input_artifact"] = {k: input_artifact.get(k) for k in ("stage", "fingerprint", "run_id")}
            result = _to_absorb_result(payload)
        else:
            # _run_absorb_workflow_direct already returns AbsorbStepResult
            result = self._run_absorb_workflow_direct(dry_run=dry_run, recent=recent_days)

        if result["success"]:
            print("✓ Absorb stage completed")
        else:
            print(f"✗ Absorb stage failed: {result.get('error', 'Unknown error')}")

        return result

    def step_evergreen(self, recent_days: int = 7, dry_run: bool = False, quality_score: float = -1.0) -> dict:
        """兼容旧调用，内部转到 Absorb。"""
        return self.step_absorb(recent_days=recent_days, dry_run=dry_run, quality_score=quality_score)

    def step_entity_extract(self, dry_run: bool = False) -> "EntityExtractStepResult":
        """Extract named entities from recent deep dives using LLM NER."""
        print("\n" + "=" * 60)
        print("ENTITY EXTRACT — 命名实体提取")
        print("=" * 60)

        from .entity_extractor import make_extractor
        from .entity_registry import EntityRegistry

        try:
            registry = EntityRegistry(self.vault_dir).load()
            before_count = len(registry)

            if dry_run:
                print("  [DRY RUN] Entity extraction skipped")
                return EntityExtractStepResult(
                    success=True, skipped=True, reason="dry_run",
                    total_entities=before_count,
                )

            # Don't silently swallow ImportError here — this is exactly
            # how the missing-llm_client.py bug hid for two months.
            # Catch only the documented "no API key" path (None return).
            from .llm_client import get_litellm_client
            llm_call = None
            client = get_litellm_client(vault_dir=self.vault_dir)
            if client:
                llm_call = client.call

            if llm_call is None:
                print("  ⚠ No LLM client available — entity extraction skipped")
                return EntityExtractStepResult(
                    success=True, skipped=True, reason="no_llm_client",
                    total_entities=before_count,
                )

            extractor = make_extractor(
                self.vault_dir, llm_call=llm_call
            )

            # Read absorb's typed contract — the silent-fallback bug we fixed
            # in PATCH-1 is now structurally impossible because absorb_result
            # always exposes processed_files (empty list if nothing absorbed).
            absorb_result = self.step_results.get("absorb")
            absorb_files: list[str] = []
            if absorb_result is not None:
                absorb_files = list(absorb_result.get("processed_files", []))

            if not absorb_files:
                areas_dir = self.vault_dir / "20-Areas"
                if areas_dir.exists():
                    from datetime import datetime, timedelta

                    cutoff = datetime.now() - timedelta(days=7)
                    absorb_files = [
                        str(f)
                        for f in areas_dir.rglob("*_深度解读.md")
                        if f.stat().st_mtime >= cutoff.timestamp()
                    ]

            total_mentions = 0
            extraction_log = self.vault_dir / "60-Logs" / "entity-extractions.jsonl"
            extraction_log.parent.mkdir(parents=True, exist_ok=True)

            already_extracted: set[str] = set()
            if extraction_log.exists():
                import json as _json_reader
                for line in extraction_log.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = _json_reader.loads(line)
                        sf = obj.get("source_file", "")
                        if sf:
                            already_extracted.add(sf)
                    except Exception:
                        pass

            log_lines: list[str] = []
            for fpath_str in absorb_files:
                fpath = Path(fpath_str)
                if not fpath.exists():
                    continue
                if str(fpath) in already_extracted:
                    continue
                extraction = extractor.extract_entities_from_file(fpath)
                total_mentions += len(extraction.mentions)
                if extraction.mentions:
                    from .identity import canonicalize_note_id as _cni
                    import json as _json
                    log_lines.append(_json.dumps({
                        "source_slug": _cni(fpath.stem),
                        "source_file": str(fpath),
                        "mentions": [m.to_dict() for m in extraction.mentions],
                    }, ensure_ascii=False))

            if log_lines:
                with open(extraction_log, "a", encoding="utf-8") as f:
                    f.write("\n".join(log_lines) + "\n")

            extractor.registry.save()
            after_count = len(extractor.registry)
            produced = after_count - before_count

            print(f"  新增 Entity 候选: {produced}")
            print(f"  累计 Entity: {after_count}")
            print(f"  提取到 Mentions: {total_mentions}")

            return EntityExtractStepResult(
                success=True,
                produced=produced,
                total_entities=after_count,
                mentions_extracted=total_mentions,
            )

        except Exception as exc:
            print(f"  ✗ Entity extraction failed: {exc}")
            return EntityExtractStepResult(success=False, error=str(exc))

    def step_dedup(self, dry_run: bool = False) -> "DedupStepResult":
        """Post-absorb deduplication scoped to recently absorbed slugs."""
        print("\n" + "=" * 60)
        print("STEP 7a: Dedup Evergreen (scoped to absorbed)")
        print("=" * 60)

        from .concept_dedup import (
            DEFAULT_THRESHOLD,
            apply_proposal,
            find_clusters,
            write_proposal,
        )

        # Read absorb's typed contract.  promoted_slugs is now guaranteed
        # to exist on every absorb return path (PATCH-1 + commit 2 of the
        # contract refactor); empty list means "no scope, fall back to
        # full vault".
        scope_slugs: set[str] | None = None
        absorb_result = self.step_results.get("absorb")
        if absorb_result is not None:
            promoted = list(absorb_result.get("promoted_slugs", []))
            if promoted:
                scope_slugs = set(promoted)

        threshold = DEFAULT_THRESHOLD
        clusters = find_clusters(self.vault_dir, threshold=threshold, scope_slugs=scope_slugs)

        if not clusters:
            scope_desc = f"scope={len(scope_slugs)} slugs" if scope_slugs else "full vault"
            print(f"  No duplicates found ({scope_desc}, threshold={threshold}).")
            return DedupStepResult(success=True)

        prop_path, proposal = write_proposal(self.vault_dir, clusters, threshold=threshold)
        print(f"  Proposal: {prop_path.name} ({len(clusters)} cluster(s))")

        if dry_run:
            for cl in clusters:
                dups = ", ".join(d.slug for d in cl.duplicates)
                print(f"    [DRY] {cl.canonical.slug} ← {dups}")
            return DedupStepResult(
                success=True,
                clusters=len(clusters),
                dry_run=True,
                proposal_id=proposal.proposal_id,
            )

        results = apply_proposal(
            self.vault_dir, proposal, dry_run=False, pack=self.workflow_pack_name
        )
        from .concept_dedup import archive_applied_proposal
        archive_applied_proposal(self.vault_dir, prop_path)

        total_archived = sum(len(r.archived) for r in results)
        total_rewrites = sum(r.wikilink_rewrites for r in results)
        errors = [e for r in results for e in r.errors]

        for r in results:
            dups = ", ".join(str(p.name) for p in r.archived) if r.archived else "(none)"
            print(f"    {r.canonical_slug}: archived={len(r.archived)} rewrites={r.wikilink_rewrites}")

        if errors:
            for e in errors:
                print(f"    [WARN] {e}")

        print(f"  ✓ Dedup complete: {len(clusters)} clusters, {total_archived} archived, {total_rewrites} rewrites")
        return DedupStepResult(
            success=not errors,
            clusters=len(clusters),
            archived=total_archived,
            rewrites=total_rewrites,
            errors=errors,
        )

    def step_moc(self, dry_run: bool = False) -> "MocStepResult":
        """执行MOC更新步骤"""
        print("\n" + "="*60)
        print("STEP 9: Updating MOC Indexes")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.auto_moc_updater",
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

        return _to_typed_step_result("moc", result)

    def step_refine(self, dry_run: bool = False) -> "RefineStepResult":
        """执行 Refine 批处理步骤（cleanup + breakdown）。"""
        print("\n" + "="*60)
        print("STEP 10: Refining Existing Evergreen Notes")
        print("="*60)

        cleanup_cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.cleanup",
            "--vault-dir", str(self.vault_dir),
            "--all",
            "--json",
        ]
        breakdown_cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.breakdown",
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
            return RefineStepResult(
                success=False,
                error=str(cleanup_result.get("error", "")),
                stdout=str(cleanup_result.get("stdout", "")),
                stderr=str(cleanup_result.get("stderr", "")),
                cleanup=cleanup_result,
            )

        breakdown_result = self.run_command(breakdown_cmd, "refine_breakdown", timeout=300)
        if not breakdown_result["success"]:
            print(f"✗ Breakdown refine failed: {breakdown_result.get('error', 'Unknown error')}")
            return RefineStepResult(
                success=False,
                error=str(breakdown_result.get("error", "")),
                stdout=str(breakdown_result.get("stdout", "")),
                stderr=str(breakdown_result.get("stderr", "")),
                cleanup=cleanup_result,
                breakdown=breakdown_result,
            )

        print("✓ Refine batch completed")
        return RefineStepResult(
            success=True,
            cleanup=cleanup_result,
            breakdown=breakdown_result,
        )

    def step_note_type_normalize(self, dry_run: bool = False) -> "NoteTypeNormalizeStepResult":
        """Normalize note_type frontmatter before derived indexes are rebuilt."""
        print("\n" + "="*60)
        print("Normalizing note_type Metadata")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.note_type_normalize",
            "--vault-dir", str(self.vault_dir),
        ]
        if dry_run:
            cmd.append("--dry-run")

        result = self.run_command(
            cmd,
            "note_type_normalize",
            timeout=self._calculate_timeout("note_type_normalize"),
        )
        stdout = str(result.get("stdout") or "")
        changed_match = re.search(r"changed:\s+(\d+)", stdout)
        skipped_match = re.search(r"skipped:\s+(\d+)", stdout)
        result["note_type_changed"] = int(changed_match.group(1)) if changed_match else 0
        result["note_type_skipped"] = int(skipped_match.group(1)) if skipped_match else 0
        if result.get("success"):
            result["output"] = (
                f"Normalized {result['note_type_changed']} note_type values; "
                f"skipped {result['note_type_skipped']}"
            )
        return _to_typed_step_result("note_type_normalize", result)

    def step_knowledge_index(self, dry_run: bool = False) -> "KnowledgeIndexStepResult":
        """刷新派生 knowledge.db。"""
        print("\n" + "="*60)
        print("STEP 10: Refreshing Knowledge Index")
        print("="*60)

        cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.knowledge_index",
            "--vault-dir", str(self.vault_dir),
            "--pack", self.workflow_pack_name,
            "--json",
        ]

        evergreen_files = [
            path
            for path in self.layout.evergreen_dir.rglob("*.md")
            if "_Candidates" not in path.parts
        ]
        total_files = len(evergreen_files)
        if self.txn_id:
            self.txn.heartbeat(
                self.txn_id,
                step_name="knowledge_index",
                progress_mode="counted" if total_files else "indeterminate",
                work_units_total=total_files or None,
                work_units_done=0,
                work_units_failed=0,
                current_item="rebuild knowledge index",
                progress_summary=(
                    f"Rebuilding knowledge index from {total_files} Evergreen files"
                    if total_files
                    else "Rebuilding knowledge index"
                ),
                last_meaningful_event={
                    "event_type": "knowledge_index_started",
                    "target_files": total_files,
                },
            )

        result = self.run_command(cmd, "knowledge_index", timeout=self._calculate_timeout("knowledge_index"))

        if result["success"]:
            print("✓ Knowledge index refresh completed")
            # Phase 38: piggyback on the knowledge_index step to materialize
            # Crystals + the daily Working Memory file. Both are idempotent
            # (Crystal collapses on identical content; Working Memory
            # overwrites today's file), so cycling them per refresh is safe.
            crystals_cmd = [
                sys.executable, "-m", "ovp_pipeline.commands.build_crystals",
                "--vault-dir", str(self.vault_dir),
                "--pack", self.workflow_pack_name,
                "--json",
            ]
            self.run_command(crystals_cmd, "knowledge_index", timeout=120)
            working_memory_cmd = [
                sys.executable, "-m", "ovp_pipeline.commands.working_memory",
                "--vault-dir", str(self.vault_dir),
                "--json",
            ]
            self.run_command(working_memory_cmd, "knowledge_index", timeout=60)
        else:
            print(f"✗ Knowledge index refresh failed: {result.get('error', 'Unknown error')}")

        return _to_typed_step_result("knowledge_index", result)

    def step_ops_state(self, dry_run: bool = False) -> dict[str, Any]:
        """M24.1: rebuild the lifecycle ``ops_state`` projection.

        Reads ``audit_events`` + truth-projection tables from
        ``knowledge.db`` (so this step depends on a successful
        ``knowledge_index`` run earlier in the DAG) and writes a
        per-pack snapshot of the five-state lifecycle counts.

        Pure projection rebuild — no LLM calls, no markdown reads.
        Idempotent; safe to re-run on the same evidence.
        """
        print("\n" + "=" * 60)
        print("STEP 12: Rebuilding ops_state lifecycle projection")
        print("=" * 60)

        if dry_run:
            print("✓ ops_state rebuild (dry-run skipped)")
            return {
                "success": True,
                "skipped": True,
                "dry_run": True,
                "pack": self.workflow_pack_name,
                "produced": 0,
            }

        cmd = [
            sys.executable, "-m", "ovp_pipeline.commands.ops_state_cli",
            "--vault-dir", str(self.vault_dir),
            "--pack", self.workflow_pack_name,
            "--rebuild",
            "--json",
        ]
        result = self.run_command(
            cmd, "ops_state",
            timeout=self._calculate_timeout("ops_state"),
        )

        # ``ovp-ops-state --rebuild --json`` prints a single JSON
        # object {counts, total, pack, vault_dir}.  Surface it on
        # the result dict so the run-summary aggregator can render
        # the bucket distribution without re-querying the projection.
        stdout = str(result.get("stdout") or "").strip()
        if stdout:
            try:
                parsed = json.loads(stdout)
            except (ValueError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, dict):
                result["counts"] = parsed.get("counts", {})
                result["total"] = int(parsed.get("total", 0) or 0)
                result["pack"] = parsed.get("pack", "")

        if result.get("success"):
            total = result.get("total", 0)
            print(
                f"✓ ops_state lifecycle projection rebuilt "
                f"({total} items)"
            )
        else:
            print(
                f"✗ ops_state rebuild failed: "
                f"{result.get('error', 'Unknown error')}"
            )
        return result

    def run_pipeline(
        self,
        steps: list[str] | None = None,
        pinboard_days: int | None = None,
        pinboard_start: str | None = None,
        pinboard_end: str | None = None,
        batch_size: int | None = None,
        dry_run: bool = False,
        from_step: str | None = None,
        pack_name: str | None = None,
        profile_name: str | None = None,
    ) -> dict:
        """Run the pipeline under the vault-wide workflow lock."""
        with vault_workflow_lock(self.vault_dir):
            return self._run_pipeline_locked(
                steps=steps,
                pinboard_days=pinboard_days,
                pinboard_start=pinboard_start,
                pinboard_end=pinboard_end,
                batch_size=batch_size,
                dry_run=dry_run,
                from_step=from_step,
                pack_name=pack_name,
                profile_name=profile_name,
            )

    def _run_pipeline_locked(
        self,
        steps: list[str] | None = None,
        pinboard_days: int | None = None,
        pinboard_start: str | None = None,
        pinboard_end: str | None = None,
        batch_size: int | None = None,
        dry_run: bool = False,
        from_step: str | None = None,
        pack_name: str | None = None,
        profile_name: str | None = None,
    ) -> dict:
        """运行Pipeline（基于实际产出检测状态）"""
        results = {}
        original_pack_name = self.workflow_pack_name
        original_profile_name = self.workflow_profile_name
        resolved_pack, resolved_profile = resolve_workflow_profile(
            pack_name=pack_name or self.workflow_pack_name,
            profile_name=profile_name or self.workflow_profile_name,
            default_profile=self.workflow_profile_name or "full",
            runtime_adapter="pipeline_step",
        )

        # 确定要运行的步骤
        if steps:
            steps_to_run = steps
        else:
            available_steps = pipeline_steps(base_steps=resolved_profile.stages)
            start_idx = 0
            normalized_from_step = normalize_step_name(from_step)
            if normalized_from_step and normalized_from_step in available_steps:
                start_idx = available_steps.index(normalized_from_step)
            steps_to_run = available_steps[start_idx:]

        print(f"\nPipeline steps to run: {', '.join(steps_to_run)}")

        self.workflow_pack_name = resolved_pack.name
        self.workflow_profile_name = resolved_profile.name

        try:
            # 获取执行前的文件计数
            before_counts = self._get_before_counts()

            for step in steps_to_run:
                self.txn.step(self.txn_id, step, "in_progress")

                if not dry_run:
                    cache_result = self._checkout_stage_artifact(step, results=results)
                    if cache_result:
                        typed_cache = self._record_step_result(step, cache_result)
                        results[step] = typed_cache
                        self.txn.step(
                            self.txn_id,
                            step,
                            "completed",
                            typed_cache.get("output", ""),
                            cache_hit=typed_cache.get("cache_hit"),
                            skipped=typed_cache.get("skipped"),
                            stage_fingerprint=typed_cache.get("stage_fingerprint"),
                            stage_artifact=typed_cache.get("stage_artifact"),
                        )
                        continue

                try:
                    cmd_result = execute_profile_stage_handler(
                        self,
                        step,
                        pack_name=self.workflow_pack_name,
                        batch_size=batch_size,
                        dry_run=dry_run,
                        pinboard_days=pinboard_days,
                        pinboard_start=pinboard_start,
                        pinboard_end=pinboard_end,
                        results=results,
                    )
                except ValueError as exc:
                    if "Unknown stage handler" not in str(exc):
                        raise
                    cmd_result = {"success": False, "error": f"Unknown step: {step}"}

                # cmd_result is a frozen StepResult (or a dict from the
                # "Unknown step" fallback); accumulate dispatcher additions
                # on a mutable copy, re-coerce to typed at the end.
                payload = cmd_result.to_dict() if isinstance(cmd_result, StepResult) else dict(cmd_result)

                # 基于实际产出判断状态（非dry_run模式）
                if not dry_run and payload.get("success"):
                    output_check = self._count_output_files(step, before_counts, payload)
                    produced = output_check.get("produced", 0)

                    payload.update(output_check)
                    payload["output"] = f"Produced {produced} items"

                    # 有产出即视为成功（不再依赖超时导致的退出码）
                    if produced > 0:
                        payload["success"] = True
                        # 更新 before_counts 为下次检查做准备
                        if step == "clippings":
                            before_counts["processed"] += produced
                        elif step == "pinboard":
                            before_counts["pinboard"] += produced
                        elif step == "pinboard_process":
                            before_counts["pinboard_archived"] += produced
                        elif step == "articles":
                            before_counts["interpretations"] = before_counts.get("interpretations", 0) + produced
                            before_counts["interpretation_files"] = sorted(
                                set(before_counts.get("interpretation_files", []))
                                | set(payload.get("produced_files", []))
                            )
                        elif step == "absorb":
                            before_counts["evergreen"] += produced
                        elif step == "refine":
                            before_counts["refine_log_mtime"] = (self.layout.logs_dir / "refine-mutations.jsonl").stat().st_mtime if (self.layout.logs_dir / "refine-mutations.jsonl").exists() else before_counts.get("refine_log_mtime", 0.0)

                    self._write_stage_artifact(step, payload, results={**results, step: payload})

                typed_result = self._record_step_result(step, payload)
                results[step] = typed_result

                if typed_result["success"]:
                    self.txn.step(
                        self.txn_id,
                        step,
                        "completed",
                        typed_result.get("output", ""),
                        cache_hit=typed_result.get("cache_hit"),
                        skipped=typed_result.get("skipped"),
                        stage_fingerprint=typed_result.get("stage_fingerprint"),
                        stage_artifact=typed_result.get("stage_artifact"),
                    )
                else:
                    blocked_reason = str(typed_result.get("reason") or typed_result.get("error") or "").strip()
                    step_status = "blocked" if typed_result.get("blocked") else "failed"
                    self.txn.step(
                        self.txn_id,
                        step,
                        step_status,
                        typed_result.get("error", ""),
                        skipped=typed_result.get("skipped"),
                        blocked_reason=blocked_reason if typed_result.get("blocked") else None,
                    )
                    print(f"\nPipeline stopped at step: {step}")
                    if typed_result.get("blocked"):
                        self.txn.fail(self.txn_id, f"Blocked at step: {step} ({blocked_reason})")
                    else:
                        self.txn.fail(self.txn_id, f"Failed at step: {step}")
                    break

            return results
        finally:
            self.workflow_pack_name = original_pack_name
            self.workflow_profile_name = original_profile_name

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
            # Distinguish "ran and produced 0" from "skipped without
            # running" — both used to render as ✅ 成功 with no detail,
            # which masked entity_extract silently skipping every file
            # for ~3 days starting 2026-05-02.  Skipped runs surface ⚠
            # plus the reason from the typed step contract.
            if result.get("success") and result.get("skipped"):
                status = "⚠ 跳过"
            elif result.get("success"):
                status = "✅ 成功"
            else:
                status = "❌ 失败"
            if result.get("success") and result.get("skipped"):
                reason = result.get("reason") or result.get("skip_reason") or "(no reason)"
                detail = f"未执行 — {reason}"
            elif result.get("success"):
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
                elif step == "entity_extract":
                    produced = result.get("produced", 0)
                    total = result.get("total_entities", 0)
                    mentions = result.get("mentions_extracted", 0)
                    detail = f"新增Entity: {produced}, 累计: {total}, Mentions: {mentions}"
                elif step == "moc":
                    detail = "已更新"
                elif step == "refine":
                    detail = "cleanup + breakdown 已执行"
                elif step == "knowledge_index":
                    detail = "knowledge.db 已刷新"
                elif step == "ops_state":
                    total = result.get("produced", 0)
                    counts = result.get("counts", {})
                    if counts:
                        bucket_str = ", ".join(
                            f"{state}: {counts.get(state, 0)}"
                            for state in (
                                "Received", "Extracted", "Accepted",
                                "Synthesized", "NeedsAction",
                            )
                        )
                        detail = f"lifecycle 项目: {total} ({bucket_str})"
                    else:
                        detail = f"lifecycle 项目: {total}"
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
        lines.append("\n## 总体状态")
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
    parser.add_argument("--incremental", action="store_true",
                       help="日常增量流水线（默认包含最近7天 Pinboard + Clippings + 后续步骤）")
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
    parser.add_argument(
        "--pack",
        default=None,
        help=(
            f"Domain pack 名称 (默认 workflow pack: {DEFAULT_WORKFLOW_PACK_NAME}; "
            f"兼容 pack: {DEFAULT_PACK_NAME}; 第一标准 pack: {PRIMARY_PACK_NAME})"
        ),
    )
    parser.add_argument("--profile", default=None, help="Workflow profile 名称（默认: full）")
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault根目录 (默认: OVP_VAULT_DIR, VAULT_DIR, 或当前目录)",
    )

    args = parser.parse_args()
    vault_dir = resolve_vault_dir(args.vault_dir)

    # 处理 init 命令
    if args.init:
        return init_env_file(vault_dir)

    # 处理 check 命令
    if args.check:
        print("\n" + "="*60)
        print("环境检查")
        print("="*60)
        ok, issues = check_environment(vault_dir)
        for issue in issues:
            print(f"  {'✓' if 'OK' in issue or 'Found' in issue else '✗'} {issue}")
        if ok:
            print("\n✓ 环境就绪，可以运行 pipeline")
            key = os.environ.get("AUTO_VAULT_API_KEY", "")[:20]
            print(f"  API Key: {key}...")
            print(f"  API Base: {os.environ.get('AUTO_VAULT_API_BASE', 'N/A')}")
        else:
            print("\n✗ 环境未就绪, 请确认 --vault-dir/OVP_VAULT_DIR/VAULT_DIR 指向 vault, 并检查 .env")
        return 0 if ok else 1

    # 检查环境(运行前)
    ok, issues = check_environment(vault_dir)
    if not ok:
        print("\n" + "="*60)
        print("环境错误")
        print("="*60)
        for issue in issues:
            print(f"  ✗ {issue}")
        print("\n请确认 --vault-dir/OVP_VAULT_DIR/VAULT_DIR 指向 vault, 并检查 .env")
        return 1

    execution_plan = build_execution_plan(args)
    if not execution_plan:
        parser.print_help()
        sys.exit(1)

    layout = VaultLayout.from_vault(vault_dir)

    # 初始化
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    pipeline = EnhancedPipeline(layout.vault_dir, logger, txn)
    pipeline.run_mode = "full" if args.full else ("incremental" if args.incremental else "custom")

    steps = execution_plan["steps"]
    pinboard_days = execution_plan["pinboard_days"]
    pinboard_start = execution_plan["pinboard_start"]
    pinboard_end = execution_plan["pinboard_end"]
    description = execution_plan["description"]

    # 创建事务
    pipeline.txn_id = txn.start(
        "enhanced-pipeline",
        description,
        pack_name=execution_plan["pack"],
        workflow_profile=execution_plan["profile"],
        planned_steps=steps,
    )
    logger.log("pipeline_started", {
        "txn_id": pipeline.txn_id,
        "mode": pipeline.run_mode,
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
        from_step=args.from_step,
        pack_name=execution_plan["pack"],
        profile_name=execution_plan["profile"],
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
