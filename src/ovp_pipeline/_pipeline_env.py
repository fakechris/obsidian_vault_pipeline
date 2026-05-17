# BL-112: leaf-extracted from unified_pipeline_enhanced.py — verbatim move, no logic change.
import os
import re
from datetime import datetime
from ovp_pipeline.runtime import looks_like_vault_dir, resolve_vault_dir
from pathlib import Path
from ._pipeline_constants import ENV_FILE, ENV_FILE_ALT




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


__all__ = [
    'parse_pinboard_frontmatter',
    'detect_pinboard_processor',
    '_load_env',
    '_check_api_key',
    'init_env_file',
    'check_environment'
]
