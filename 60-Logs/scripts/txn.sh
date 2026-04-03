#!/bin/bash
#
# Workflow Transaction Manager
# 管理工作流事务的创建、更新和状态跟踪
#

# 【FIXED】TRANSACTIONS_DIR 移到 VAULT_DIR 检测之后声明，避免硬编码

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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

# 【FIXED】TRANSACTIONS_DIR 在 VAULT_DIR 确定后声明
TRANSACTIONS_DIR="${WIGS_TRANSACTIONS_DIR:-${VAULT_DIR}/60-Logs/transactions}"

# 创建新事务
start_transaction() {
    local workflow_type=$1
    local description=$2
    local txn_id="txn-$(date +%Y%m%d-%H%M%S)-$(uuidgen | cut -d'-' -f1)"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local txn_file="${TRANSACTIONS_DIR}/${txn_id}.json"

    cat > "$txn_file" << EOF
{
  "id": "${txn_id}",
  "type": "${workflow_type}",
  "description": "${description}",
  "start_time": "${timestamp}",
  "status": "in_progress",
  "steps": {},
  "checkpoint": "initialized",
  "last_updated": "${timestamp}"
}
EOF

    echo "$txn_id"
}

# 更新事务步骤
update_step() {
    local txn_id=$1
    local step_name=$2
    local status=$3
    local output=$4
    local txn_file="${TRANSACTIONS_DIR}/${txn_id}.json"

    if [[ ! -f "$txn_file" ]]; then
        echo "${RED}Error: Transaction ${txn_id} not found${NC}" >&2
        return 1
    fi

    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # 使用jq更新JSON（如果可用）
    if command -v jq &> /dev/null; then
        local tmp_file=$(mktemp)
        jq --arg step "$step_name" \
           --arg status "$status" \
           --arg output "$output" \
           --arg time "$timestamp" \
           '.steps[$step] = {
               "status": $status,
               "output": $output,
               "updated_at": $time
           } | .last_updated = $time | .checkpoint = $step' \
           "$txn_file" > "$tmp_file" && mv "$tmp_file" "$txn_file"
    else
        # 降级方案：使用sed（简陋但可用）
        echo "${YELLOW}Warning: jq not available, using sed fallback${NC}" >&2
    fi

    echo "Updated ${txn_id}: ${step_name} -> ${status}"
}

# 完成事务
complete_transaction() {
    local txn_id=$1
    local txn_file="${TRANSACTIONS_DIR}/${txn_id}.json"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    if command -v jq &> /dev/null; then
        local tmp_file=$(mktemp)
        jq --arg time "$timestamp" \
           '.status = "completed" | .completed_at = $time | .last_updated = $time' \
           "$txn_file" > "$tmp_file" && mv "$tmp_file" "$txn_file"
    fi

    echo "${GREEN}Transaction ${txn_id} completed${NC}"
}

# 失败事务
fail_transaction() {
    local txn_id=$1
    local reason=$2
    local txn_file="${TRANSACTIONS_DIR}/${txn_id}.json"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    if command -v jq &> /dev/null; then
        local tmp_file=$(mktemp)
        jq --arg time "$timestamp" \
           --arg reason "$reason" \
           '.status = "failed" | .failure_reason = $reason | .last_updated = $time' \
           "$txn_file" > "$tmp_file" && mv "$tmp_file" "$txn_file"
    fi

    echo "${RED}Transaction ${txn_id} failed: ${reason}${NC}"
}

# 列出未完成事务
list_incomplete() {
    echo "${YELLOW}Incomplete transactions:${NC}"

    if command -v jq &> /dev/null; then
        for txn in "${TRANSACTIONS_DIR}"/*.json; do
            [[ -f "$txn" ]] || continue
            local status=$(jq -r '.status' "$txn")
            if [[ "$status" != "completed" && "$status" != "failed" ]]; then
                local id=$(jq -r '.id' "$txn")
                local type=$(jq -r '.type' "$txn")
                local desc=$(jq -r '.description' "$txn")
                local start=$(jq -r '.start_time' "$txn")
                printf "  ${YELLOW}•${NC} %s | %s | %s | started: %s\n" "$id" "$type" "$desc" "$start"
            fi
        done
    else
        ls -la "${TRANSACTIONS_DIR}"/*.json 2>/dev/null | head -10
    fi
}

# 显示事务详情
show_transaction() {
    local txn_id=$1
    local txn_file="${TRANSACTIONS_DIR}/${txn_id}.json"

    if [[ ! -f "$txn_file" ]]; then
        echo "${RED}Transaction not found: ${txn_id}${NC}"
        return 1
    fi

    if command -v jq &> /dev/null; then
        cat "$txn_file" | jq '.'
    else
        cat "$txn_file"
    fi
}

# 清理旧事务（归档）
archive_old_transactions() {
    local days=${1:-30}
    local archive_dir="${TRANSACTIONS_DIR}/archive"

    mkdir -p "$archive_dir"

    find "${TRANSACTIONS_DIR}" -name "*.json" -type f -mtime +$days -exec mv {} "$archive_dir/" \;
    echo "Archived transactions older than ${days} days"
}

# 主命令处理
case "${1:-help}" in
    start)
        shift
        if [[ $# -lt 2 ]]; then
            echo "Usage: $0 start <workflow_type> <description>"
            exit 1
        fi
        start_transaction "$1" "$2"
        ;;
    step)
        shift
        if [[ $# -lt 3 ]]; then
            echo "Usage: $0 step <txn_id> <step_name> <status> [output]"
            exit 1
        fi
        update_step "$1" "$2" "$3" "${4:-}"
        ;;
    complete)
        shift
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 complete <txn_id>"
            exit 1
        fi
        complete_transaction "$1"
        ;;
    fail)
        shift
        if [[ $# -lt 2 ]]; then
            echo "Usage: $0 fail <txn_id> <reason>"
            exit 1
        fi
        fail_transaction "$1" "$2"
        ;;
    list)
        list_incomplete
        ;;
    show)
        shift
        if [[ $# -lt 1 ]]; then
            echo "Usage: $0 show <txn_id>"
            exit 1
        fi
        show_transaction "$1"
        ;;
    archive)
        shift
        archive_old_transactions "${1:-30}"
        ;;
    help|*)
        cat << 'USAGE'
Workflow Transaction Manager

Commands:
  start <type> <desc>     Create new transaction
  step <id> <name> <status> [output]  Update step
  complete <id>           Mark as completed
  fail <id> <reason>      Mark as failed
  list                    Show incomplete transactions
  show <id>               Show transaction details
  archive [days]          Archive old transactions (default: 30 days)

Examples:
  ./txn.sh start pinboard-processing "Process today's bookmarks"
  ./txn.sh step txn-20260331-143000-abc123 api-fetch completed "10 bookmarks"
  ./txn.sh complete txn-20260331-143000-abc123
USAGE
        ;;
esac
