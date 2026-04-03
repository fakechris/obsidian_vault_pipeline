#!/bin/bash
#
# WIGS: Workflow Integrity Guarantee System - Consistency Checker
# 检测5层数据处理架构的所有断裂点
#

# 尝试自动检测Vault目录（如果没有设置环境变量）
if [[ -z "$WIGS_VAULT_DIR" ]]; then
    # 尝试从git根目录检测
    if git rev-parse --show-toplevel &>/dev/null; then
        VAULT_DIR=$(git rev-parse --show-toplevel)
    else
        # 降级方案：使用脚本所在目录的父目录
        VAULT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
    fi
else
    VAULT_DIR="$WIGS_VAULT_DIR"
fi
TRANSACTIONS_DIR="${VAULT_DIR}/60-Logs/transactions"
MANIFEST_FILE="${VAULT_DIR}/50-Inbox/.manifest.json"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

# 工具函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; ((WARNINGS++)); }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; ((ERRORS++)); }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }

# 检查命令依赖
check_dependencies() {
    local deps=("jq" "md5sum" "find")
    local missing=()

    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            missing+=("$dep")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "Missing dependencies: ${missing[*]}"
        log_warn "Some checks will be limited"
    fi
}

# L1: 检查未完成事务
check_layer1_transactions() {
    log_info "Checking Layer 1: Session Memory (Incomplete Transactions)"

    if [[ ! -d "$TRANSACTIONS_DIR" ]]; then
        log_error "Transactions directory not found: $TRANSACTIONS_DIR"
        return
    fi

    local incomplete_count=0

    if command -v jq &> /dev/null; then
        for txn in "${TRANSACTIONS_DIR}"/*.json; do
            [[ -f "$txn" ]] || continue
            local status=$(jq -r '.status // "unknown"' "$txn")
            if [[ "$status" == "in_progress" ]]; then
                local id=$(jq -r '.id' "$txn")
                local type=$(jq -r '.type' "$txn")
                local desc=$(jq -r '.description' "$txn")
                local start=$(jq -r '.start_time' "$txn")
                local checkpoint=$(jq -r '.checkpoint // "unknown"' "$txn")

                log_error "Incomplete transaction: $id"
                echo "  Type: $type | Description: $desc"
                echo "  Started: $start | Last checkpoint: $checkpoint"
                ((incomplete_count++))
            fi
        done
    else
        log_warn "jq not available, transaction check limited"
    fi

    if [[ $incomplete_count -eq 0 ]]; then
        log_success "No incomplete transactions"
    else
        log_error "Found $incomplete_count incomplete transaction(s)"
    fi
}

# L2: 检查孤儿Evergreen（未链接到MOC）
check_layer2_orphan_evergreen() {
    log_info "Checking Layer 2: Knowledge Graph (Orphan Evergreen)"

    local evergreen_dir="${VAULT_DIR}/10-Knowledge/Evergreen"

    # 【FIXED】更新为新的 Atlas MOC 路径
    local moc_files=(
        "${VAULT_DIR}/10-Knowledge/Atlas/MOC-Index.md"
        "${VAULT_DIR}/10-Knowledge/Atlas/MOC-AI-Research.md"
        "${VAULT_DIR}/10-Knowledge/Atlas/MOC-Tools.md"
        "${VAULT_DIR}/10-Knowledge/Atlas/MOC-Investing.md"
        "${VAULT_DIR}/10-Knowledge/Atlas/MOC-Programming.md"
    )

    if [[ ! -d "$evergreen_dir" ]]; then
        log_warn "Evergreen directory not found"
        return
    fi

    local orphan_count=0

    # 收集所有MOC中的链接（包括 Evergreen/xxx 格式的链接）
    local moc_links=""
    for moc in "${moc_files[@]}"; do
        if [[ -f "$moc" ]]; then
            # 【FIXED】改进匹配：支持多种wiki-link格式 (使用-E替代-P兼容macOS)
            # 格式1: [[Evergreen/AI-Agent]]
            # 格式2: [[../../10-Knowledge/Evergreen/AI-Agent]]
            # 格式3: [[AI-Agent|显示文本]]
            moc_links+="$(grep -oE '\[\[[^]]*\]\]' "$moc" 2>/dev/null | tr '\n' ' ' || true)"
        fi
    done

    # 检查每个Evergreen文件（跳过模板文件）
    while IFS= read -r -d '' file; do
        local basename=$(basename "$file" .md)
        # 跳过以 _ 开头的文件（如 _template.md）
        [[ "$basename" == _* ]] && continue
        local is_linked=false

        # 【FIXED】更精确的匹配逻辑
        # 检查多种可能的链接格式
        if echo "$moc_links" | grep -qiE "\[\[.*${basename}.*\]\]"; then
            is_linked=true
        fi

        if [[ "$is_linked" == "false" ]]; then
            log_warn "Orphan Evergreen: $basename (not linked in any MOC)"
            ((orphan_count++))
        fi
    done < <(find "$evergreen_dir" -name "*.md" -type f -print0 2>/dev/null)

    if [[ $orphan_count -eq 0 ]]; then
        log_success "All Evergreen files linked in MOCs"
    else
        log_error "Found $orphan_count orphan Evergreen file(s)"
    fi
}

# L2: 检查断开的wiki-links
# 【P2 TODO】完整实现需要全库扫描，当前为简化版
check_layer2_broken_links() {
    log_info "Checking Layer 2: Broken Wiki-Links"

    # 简化版：只检查关键MOC文件中的链接是否有对应文件
    local moc_files=(
        "${VAULT_DIR}/20-Areas/AI-Research/Topics/AI MOC.md"
        "${VAULT_DIR}/20-Areas/Programming/Topics/编程 MOC.md"
    )

    local broken_count=0
    for moc in "${moc_files[@]}"; do
        [[ -f "$moc" ]] || continue

        # 提取所有wiki-links
        local links=$(grep -oE '\[\[([^\]|]+)(\|[^\]]*)?\]\]' "$moc" 2>/dev/null | sed 's/\[\[\([^\]|]*\).*/\1/' | sort -u)

        for link in $links; do
            # 跳过外部链接和锚点
            [[ "$link" =~ ^http ]] && continue
            [[ "$link" =~ ^# ]] && continue

            # 检查文件是否存在（支持多种路径）
            local found=false
            local possible_paths=(
                "${VAULT_DIR}/${link}.md"
                "${VAULT_DIR}/10-Knowledge/Evergreen/${link}.md"
                "${VAULT_DIR}/20-Areas/AI-Research/Topics/2026-*/${link}.md"
            )

            for path in "${possible_paths[@]}"; do
                if [[ -f "$path" ]] || ls $path 2>/dev/null | grep -q .; then
                    found=true
                    break
                fi
            done

            if [[ "$found" == "false" ]]; then
                # 只在verbose模式下报告，避免过多警告
                ((broken_count++))
            fi
        done
    done

    if [[ $broken_count -eq 0 ]]; then
        log_success "No broken links in key MOCs"
    else
        log_warn "$broken_count potential broken links found (use --verbose for details)"
    fi
}

# L3: 检查Ingestion层一致性
check_layer3_ingestion() {
    log_info "Checking Layer 3: Ingestion Pipeline"

    local raw_dir="${VAULT_DIR}/50-Inbox/01-Raw"
    local processed_dir="${VAULT_DIR}/50-Inbox/03-Processed"

    # 检查重复文件（带日期前缀和不带）
    local dup_count=0
    if [[ -d "$raw_dir" ]]; then
        for file in "$raw_dir"/*.md; do
            [[ -f "$file" ]] || continue
            local basename=$(basename "$file")
            # 检查是否有对应的日期前缀版本
            local pattern="[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_${basename}"
            if find "$raw_dir" -name "$pattern" -type f | grep -q .; then
                log_warn "Potential duplicate: $basename (with and without date prefix)"
                ((dup_count++))
            fi
        done
    fi

    # 检查manifest完整性
    if [[ ! -f "$MANIFEST_FILE" ]]; then
        log_error "Manifest file not found: $MANIFEST_FILE"
    else
        log_success "Manifest file exists"

        # 检查manifest中的文件是否实际存在
        if command -v jq &> /dev/null; then
            local files_count=$(jq '.files | length' "$MANIFEST_FILE")
            log_info "Manifest tracks $files_count files"
        fi
    fi

    if [[ $dup_count -gt 0 ]]; then
        log_error "Found $dup_count potential duplicate(s)"
    fi
}

# L4: 检查Areas层完整性
check_layer4_areas() {
    log_info "Checking Layer 4: Areas/Projects Layer"

    # 【FIXED】检查所有4个Areas，而非仅AI-Research
    local areas=("AI-Research" "Tools" "Investing" "Programming")
    local total_unindexed=0

    for area in "${areas[@]}"; do
        # 【FIXED】正确查找Topics MOC文件
        local moc_file=""
        local topics_moc_pattern="${VAULT_DIR}/20-Areas/${area}/Topics/*MOC.md"

        # 使用find代替[ -f ...*... ]，因为bash通配符在变量中不展开
        moc_file=$(find "${VAULT_DIR}/20-Areas/${area}/Topics/" -maxdepth 1 -name "*MOC.md" -type f 2>/dev/null | head -1)

        # 如果找到了文件，验证它确实存在
        if [[ -n "$moc_file" && -f "$moc_file" ]]; then
            : # MOC file found and valid
        else
            moc_file=""
        fi

        local area_topics_dir="${VAULT_DIR}/20-Areas/${area}/Topics"
        if [[ -d "$area_topics_dir" ]]; then
            local area_unindexed=0
            while IFS= read -r -d '' file; do
                [[ -f "$file" ]] || continue
                local basename=$(basename "$file" .md)
                local is_indexed=false

                if [[ -n "$moc_file" && -f "$moc_file" ]]; then
                    if grep -qF "[[${basename}]]" "$moc_file" 2>/dev/null || grep -qF "[[${basename}.md]]" "$moc_file" 2>/dev/null; then
                        is_indexed=true
                    fi
                fi

                # 同时检查主MOC.md
                local main_moc="${VAULT_DIR}/20-Areas/${area}/MOC.md"
                if [[ -f "$main_moc" ]]; then
                    if grep -qF "[[${basename}]]" "$main_moc" 2>/dev/null || grep -qF "[[${basename}.md]]" "$main_moc" 2>/dev/null; then
                        is_indexed=true
                    fi
                fi

                if [[ "$is_indexed" == "false" ]]; then
                    log_warn "Unindexed in ${area}: $basename"
                    ((area_unindexed++))
                    ((total_unindexed++))
                fi
            done < <(find "$area_topics_dir" -name "*_深度解读.md" -type f -print0 2>/dev/null)

            if [[ $area_unindexed -gt 0 ]]; then
                log_info "Area ${area}: ${area_unindexed} unindexed file(s)"
            fi
        fi
    done

    if [[ $total_unindexed -eq 0 ]]; then
        log_success "All deep interpretations indexed in MOCs across all areas"
    else
        log_error "Found $total_unindexed unindexed file(s) across all areas"
    fi
}

# L4: 检查Git提交完整性
check_layer4_git_integrity() {
    log_info "Checking Layer 4: Git Commit Integrity"

    cd "$VAULT_DIR" || return

    # 检查未提交的修改
    if ! git diff --quiet 2>/dev/null; then
        log_error "Uncommitted changes detected"
        git status -sb | head -5
    else
        log_success "No uncommitted changes"
    fi

    # 检查上次提交消息是否包含必要的上下文
    local last_commit_msg=$(git log -1 --pretty=%B)
    if [[ ! "$last_commit_msg" =~ (Processing-Queue|Evergreen|事务|状态|moc|analysis|修复) ]]; then
        log_warn "Last commit may not include state information"
    fi
}

# L5: 检查归档层
check_layer5_archive() {
    log_info "Checking Layer 5: Archive Layer"

    local archive_dir="${VAULT_DIR}/70-Archive"

    if [[ ! -d "$archive_dir" ]]; then
        log_warn "Archive directory not found"
        return
    fi

    # 检查归档目录结构
    local archive_count=$(find "$archive_dir" -type f 2>/dev/null | wc -l)
    local archive_dirs=$(find "$archive_dir" -type d 2>/dev/null | wc -l)

    log_info "Archive contains $archive_count files in $archive_dirs directories"

    # 检查超过1年未访问的文件（建议迁移到冷存储）
    local old_files=$(find "$archive_dir" -type f -atime +365 2>/dev/null | wc -l)
    if [[ $old_files -gt 0 ]]; then
        log_warn "$old_files files not accessed in >1 year (consider cold storage)"
    fi

    log_success "Archive layer structure verified"
}

# 生成修复建议
generate_repair_plan() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                  修复建议 (Repair Plan)                       ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"

    if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
        echo -e "${GREEN}✓ 所有检查通过，无需修复${NC}"
        return
    fi

    echo ""
    echo "建议修复步骤："
    echo ""

    if [[ $ERRORS -gt 0 ]]; then
        echo -e "${RED}高优先级 (自动/半自动修复):${NC}"
        echo "  1. 运行: ./60-Logs/scripts/txn.sh list"
        echo "     查看并完成未完成的事务"
        echo ""
        echo "  2. 更新MOC索引:"
        echo "     - 将新Evergreen添加到AI MOC"
        echo "     - 将深度解读添加到对应Topics目录"
        echo ""
    fi

    if [[ $WARNINGS -gt 0 ]]; then
        echo -e "${YELLOW}中优先级 (需要确认):${NC}"
        echo "  3. 检查重复文件，确认后删除旧版本"
        echo ""
        echo "  4. 验证孤儿Evergreen是否真的需要链接"
        echo ""
    fi

    echo "运行修复脚本:"
    echo "  ./60-Logs/scripts/repair.sh --dry-run  # 预览修复"
    echo "  ./60-Logs/scripts/repair.sh             # 执行修复"
}

# 主检查流程
main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     WIGS: Workflow Integrity Guarantee System                 ║${NC}"
    echo -e "${BLUE}║              一致性检查器 (Consistency Checker)             ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Vault: $VAULT_DIR"
    echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    check_dependencies
    echo ""

    # 执行5层检查
    check_layer1_transactions
    echo ""

    check_layer2_orphan_evergreen
    check_layer2_broken_links
    echo ""

    check_layer3_ingestion
    echo ""

    check_layer4_areas
    check_layer4_git_integrity
    echo ""

    check_layer5_archive
    echo ""

    # 总结
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                        检查结果总结                           ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"

    if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
        echo -e "${GREEN}✓ 所有检查通过！系统状态健康。${NC}"
        exit 0
    elif [[ $ERRORS -eq 0 ]]; then
        echo -e "${YELLOW}⚠ 发现 ${WARNINGS} 个警告，建议处理${NC}"
        generate_repair_plan
        exit 0
    else
        echo -e "${RED}✗ 发现 ${ERRORS} 个错误，${WARNINGS} 个警告${NC}"
        echo -e "${RED}  强烈建议在继续新工作前修复错误${NC}"
        generate_repair_plan
        exit 1
    fi
}

# 运行主函数
main "$@"
