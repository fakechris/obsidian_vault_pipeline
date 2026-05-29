# BL-112: leaf-extracted from unified_pipeline_enhanced.py — verbatim move, no logic change.
from pathlib import Path



# ========== 环境初始化 ==========
# 加载 .env 文件（从 Vault 根目录或 auto_vault 目录）
SCRIPTS_DIR = Path(__file__).parent

VAULT_DIR = SCRIPTS_DIR.parent.parent

ENV_FILE = VAULT_DIR / ".env"

ENV_FILE_ALT = SCRIPTS_DIR / "auto_vault" / ".env"

ENV_EXAMPLE = VAULT_DIR / ".env.example"



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
    "synthesize",     # 11b. BL-117: 预算化重合成 stale community crystals
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


__all__ = [
    'SCRIPTS_DIR',
    'VAULT_DIR',
    'ENV_FILE',
    'ENV_FILE_ALT',
    'ENV_EXAMPLE',
    'BASE_PIPELINE_STEPS',
    'OPTIONAL_PIPELINE_STEPS',
    'STEP_ALIASES',
    'PIPELINE_STEP_CHOICES',
    'PROJECT_ROOT',
    'PROJECT_SRC',
    'QUALITY_STAGE_ALGORITHM_VERSION',
    'STAGE_CACHE_CHECKOUT',
    'STAGE_CACHE_RECORD_ONLY',
    'STAGE_CACHE_DISABLED',
    'STAGE_CACHE_POLICIES',
    'STAGE_ALGORITHM_VERSIONS'
]
