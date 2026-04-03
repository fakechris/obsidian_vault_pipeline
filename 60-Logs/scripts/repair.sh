#!/bin/bash
#
# repair.sh - 自动修复 WIGS 检测到的问题
# 用法: ./repair.sh [--dry-run] [--auto]
#

# 尝试自动检测Vault目录
if [[ -z "$WIGS_VAULT_DIR" ]]; then
    if git rev-parse --show-toplevel &>/dev/null; then
        VAULT_DIR=$(git rev-parse --show-toplevel)
    else
        VAULT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
    fi
else
    VAULT_DIR="$WIGS_VAULT_DIR"
fi

TRANSACTIONS_DIR="${VAULT_DIR}/60-Logs/transactions"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

DRY_RUN=false
AUTO_MODE=false
FIXED=0
SKIPPED=0
ERRORS=0

# 日志函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }

# 显示帮助
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --dry-run    预览模式，显示会做什么但不执行"
    echo "  --auto       自动修复低风险问题（无需确认）"
    echo "  -h, --help   显示帮助"
}

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --auto)
            AUTO_MODE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# 执行命令（支持dry-run）
run_cmd() {
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}[DRY-RUN] Would execute: $*${NC}"
        return 0
    else
        eval "$@"
    fi
}

# 修复 L1: 未完成事务
fix_layer1_transactions() {
    log_info "Fixing Layer 1: Incomplete Transactions"

    if [[ ! -d "$TRANSACTIONS_DIR" ]]; then
        log_warn "Transactions directory not found"
        return
    fi

    local fixed_count=0

    if command -v jq &> /dev/null; then
        for txn in "${TRANSACTIONS_DIR}"/*.json; do
            [[ -f "$txn" ]] || continue
            local status=$(jq -r '.status // "unknown"' "$txn")
            if [[ "$status" == "in_progress" ]]; then
                local id=$(jq -r '.id' "$txn")
                local checkpoint=$(jq -r '.checkpoint // "unknown"' "$txn")

                if [[ "$AUTO_MODE" == "true" ]]; then
                    # 自动完成已停滞的事务（超过24小时）
                    local start_time=$(jq -r '.start_time' "$txn")
                    if [[ -n "$start_time" ]]; then
                        # 简化：如果用户要求自动，标记为失败需要手动处理
                        log_warn "Transaction $id 需要手动处理 (checkpoint: $checkpoint)"
                        ((SKIPPED++))
                    fi
                else
                    echo ""
                    echo -e "${YELLOW}发现未完成事务: $id${NC}"
                    echo "  当前步骤: $checkpoint"
                    echo ""
                    read -p "选择操作: [c]完成 / [f]标记失败 / [s]跳过 / [q]退出: " choice
                    case "$choice" in
                        c|C)
                            run_cmd "jq '.status = \"completed\"' \"$txn\" > \"${txn}.tmp\" && mv \"${txn}.tmp\" \"$txn\""
                            log_success "已标记完成: $id"
                            ((FIXED++))
                            ;;
                        f|F)
                            read -p "输入失败原因: " reason
                            run_cmd "jq '.status = \"failed\" | .failure_reason = \"$reason\"' \"$txn\" > \"${txn}.tmp\" && mv \"${txn}.tmp\" \"$txn\""
                            log_info "已标记失败: $id"
                            ((FIXED++))
                            ;;
                        s|S)
                            log_info "跳过: $id"
                            ((SKIPPED++))
                            ;;
                        q|Q)
                            exit 0
                            ;;
                    esac
                fi
            fi
        done
    else
        log_warn "jq not available, cannot fix transactions"
    fi
}

# 修复 L3: 重复文件
fix_layer3_duplicates() {
    log_info "Fixing Layer 3: Duplicate Files"

    local raw_dir="${VAULT_DIR}/50-Inbox/01-Raw"
    local dup_count=0

    if [[ -d "$raw_dir" ]]; then
        for file in "$raw_dir"/*.md; do
            [[ -f "$file" ]] || continue
            local basename=$(basename "$file")
            local pattern="[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_${basename}"

            if find "$raw_dir" -name "$pattern" -type f | grep -q .; then
                ((dup_count++))
                local dated_file=$(find "$raw_dir" -name "$pattern" -type f | head -1)

                echo ""
                log_warn "发现重复文件:"
                echo "  旧文件: $basename"
                echo "  新文件: $(basename "$dated_file")"

                if [[ "$AUTO_MODE" == "true" ]]; then
                    # 自动删除无日期前缀的旧版本
                    run_cmd "rm \"$file\""
                    log_success "自动删除旧版本: $basename"
                    ((FIXED++))
                else
                    read -p "选择操作: [k]保留有日期的 / [d]删除有日期的 / [s]跳过: " choice
                    case "$choice" in
                        k|K)
                            run_cmd "rm \"$file\""
                            log_success "已删除: $basename"
                            ((FIXED++))
                            ;;
                        d|D)
                            run_cmd "rm \"$dated_file\""
                            log_success "已删除: $(basename "$dated_file")"
                            ((FIXED++))
                            ;;
                        s|S)
                            log_info "跳过"
                            ((SKIPPED++))
                            ;;
                    esac
                fi
            fi
        done
    fi

    if [[ $dup_count -eq 0 ]]; then
        log_success "未发现重复文件"
    fi
}

# 修复 L4: 未索引文件（通过运行 MOC updater）
fix_layer4_unindexed() {
    log_info "Fixing Layer 4: Unindexed Files"

    local updater="${VAULT_DIR}/60-Logs/scripts/auto_moc_updater.py"

    if [[ -f "$updater" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "[DRY-RUN] 会运行: python3 $updater --scan"
        else
            log_info "运行 MOC 更新器..."
            python3 "$updater" --scan
            log_success "MOC 更新完成"
            ((FIXED++))
        fi
    else
        log_warn "MOC updater not found: $updater"
        log_info "手动修复: 将新文件添加到对应的 MOC.md"
    fi
}

# 生成修复报告
generate_report() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                     修复报告 (Repair Report)                    ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "已修复: ${GREEN}$FIXED${NC}"
    echo -e "已跳过: ${YELLOW}$SKIPPED${NC}"
    echo -e "错误: ${RED}$ERRORS${NC}"
    echo ""

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}这是预览模式，未执行实际修复${NC}"
        echo "要执行修复，请运行: $0"
        if [[ "$AUTO_MODE" == "false" ]]; then
            echo "自动修复低风险问题: $0 --auto"
        fi
    fi
}

# 主流程
main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║           WIGS: Workflow Integrity Repair Tool               ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Vault: $VAULT_DIR"
    echo "Mode: $([[ $DRY_RUN == true ]] && echo 'DRY-RUN' || echo 'LIVE')"
    echo "Auto: $([[ $AUTO_MODE == true ]] && echo 'YES' || echo 'NO')"
    echo ""

    # 先运行一致性检查获取问题列表
    log_info "Step 1: 运行一致性检查..."
    if ! "${VAULT_DIR}/60-Logs/scripts/check-consistency.sh" &>/dev/null; then
        log_warn "检测到问题，开始修复流程"
    else
        log_success "未检测到问题，退出"
        exit 0
    fi

    echo ""
    log_info "Step 2: 执行修复..."

    fix_layer1_transactions
    echo ""

    fix_layer3_duplicates
    echo ""

    fix_layer4_unindexed
    echo ""

    generate_report
}

# 运行主函数
main "$@"
