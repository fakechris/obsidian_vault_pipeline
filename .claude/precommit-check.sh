#!/bin/bash
# precommit-check.sh - Obsidian Vault Pipeline 内容质量检查脚本
# 用法: ./precommit-check.sh [文件列表]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 默认值
MIN_LINES=150
MAX_FILES=10
CHECK_PLACEHOLDERS=true
CHECK_FRONTMATTER=true
MODE="all"

# 禁止的占位符模式（中英文）
FORBIDDEN_PATTERNS=(
    # 英文占位符
    "Please refer to the project's documentation"
    "Please check the examples directory or README"
    "This project may be related to other"
    "This project contributes to the AI agent ecosystem"
    "This project contributes to the AI ecosystem"
    "Configuration details depend on the specific project"
    "For more details, visit the project repository"
    "Review the GitHub Issues page for known issues"
    "This project is related to"
    "Useful for"
    "More information can be found"
    "For more information"
    # 中文占位符
    "详见官方文档"
    "请参考项目主页"
    "更多信息请查看"
    "详情请参考"
    "请参阅文档"
)

# 帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS] [FILES]"
    echo ""
    echo "Options:"
    echo "  -h, --help           显示帮助"
    echo "  -l, --lines-only     只检查行数"
    echo "  -p, --placeholders   只检查占位符"
    echo "  -f, --frontmatter    只检查 frontmatter"
    echo "  -m, --min-lines N    设置最低行数要求 (默认: $MIN_LINES)"
    echo "  --max-files N        设置单次提交最大文件数 (默认: $MAX_FILES)"
    echo ""
    echo "示例:"
    echo "  $0                          # 检查所有暂存文件"
    echo "  $0 file1.md file2.md       # 检查指定文件"
    echo "  $0 --lines-only             # 只检查行数"
    echo "  $0 --min-lines 200          # 设置最低200行"
}

# 解析参数
FILES=()
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -l|--lines-only)
            MODE="lines"
            shift
            ;;
        -p|--placeholders)
            MODE="placeholders"
            shift
            ;;
        -f|--frontmatter)
            MODE="frontmatter"
            shift
            ;;
        -m|--min-lines)
            MIN_LINES="$2"
            shift 2
            ;;
        --max-files)
            MAX_FILES="$2"
            shift 2
            ;;
        -*)
            echo -e "${RED}未知选项: $1${NC}"
            show_help
            exit 1
            ;;
        *)
            FILES+=("$1")
            shift
            ;;
    esac
done

# 尝试自动检测Vault目录
if [[ -z "$WIGS_VAULT_DIR" ]]; then
    if git rev-parse --show-toplevel &>/dev/null; then
        VAULT_DIR=$(git rev-parse --show-toplevel)
    else
        VAULT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    fi
else
    VAULT_DIR="$WIGS_VAULT_DIR"
fi

# 如果没有指定文件，获取git暂存的.md文件
if [ ${#FILES[@]} -eq 0 ]; then
    # 检查暂存区是否有文件
    STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep '\.md$' || true)
    if [ -z "$STAGED_FILES" ]; then
        echo -e "${YELLOW}没有暂存的Markdown文件，跳过检查${NC}"
        exit 0
    fi
    # 兼容macOS的bash（不支持mapfile）
    while IFS= read -r line; do
        [ -n "$line" ] && FILES+=("$line")
    done <<< "$STAGED_FILES"
fi

# 检查文件数量
FILE_COUNT=${#FILES[@]}
if [ $FILE_COUNT -gt $MAX_FILES ]; then
    echo -e "${RED}错误: 暂存文件数量 ($FILE_COUNT) 超过上限 ($MAX_FILES)${NC}"
    echo "请分批提交，每次不超过 $MAX_FILES 个文件"
    echo "提示: 可以使用 --max-files 参数调整限制"
    exit 1
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Obsidian Vault Pipeline 质量检查${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}检查 $FILE_COUNT 个文件...${NC}"
echo -e "${YELLOW}Vault目录: $VAULT_DIR${NC}"
echo ""

# 统计
PASSED=0
FAILED=0
SKIPPED=0
WARNINGS=0

# 检查frontmatter
check_frontmatter() {
    local file="$1"
    local content
    content=$(cat "$file" 2>/dev/null)
    local result=0

    # 检查是否有frontmatter
    if ! echo "$content" | head -5 | grep -q "^---"; then
        echo -e "${YELLOW}  [WARN] $file (缺少 frontmatter)${NC}"
        ((WARNINGS++))
        result=1
    fi

    # 检查frontmatter完整性
    local fm_end
    fm_end=$(echo "$content" | grep -n "^---" | head -2 | tail -1 | cut -d: -f1)
    if [ -z "$fm_end" ] || [ "$fm_end" -lt 2 ]; then
        echo -e "${YELLOW}  [WARN] $file (frontmatter 格式可能不完整)${NC}"
        ((WARNINGS++))
        result=1
    fi

    return $result
}

# 检查函数
check_file() {
    local file="$1"
    local result=0
    local issues=()

    # 检查文件是否存在
    if [ ! -f "$file" ]; then
        echo -e "${RED}  [SKIP] $file (不存在)${NC}"
        ((SKIPPED++))
        return 1
    fi

    # 检查是否是Markdown文件
    if [[ ! "$file" =~ \.md$ ]]; then
        echo -e "${YELLOW}  [SKIP] $file (非Markdown文件)${NC}"
        ((SKIPPED++))
        return 0
    fi

    # 检查行数
    if [ "$MODE" = "all" ] || [ "$MODE" = "lines" ]; then
        lines=$(wc -l < "$file" | tr -d ' ')
        if [ "$lines" -lt $MIN_LINES ]; then
            issues+=("${lines}行 < ${MIN_LINES}行")
            result=1
        fi
    fi

    # 检查占位符
    if [ "$MODE" = "all" ] || [ "$MODE" = "placeholders" ]; then
        for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
            if grep -q "$pattern" "$file" 2>/dev/null; then
                issues+=("占位符: $pattern")
                result=1
                break
            fi
        done
    fi

    # 检查frontmatter
    if [ "$MODE" = "all" ] || [ "$MODE" = "frontmatter" ]; then
        if ! check_frontmatter "$file"; then
            result=1
        fi
    fi

    # 输出结果
    if [ $result -eq 0 ]; then
        if [ "$MODE" = "all" ] || [ "$MODE" = "lines" ]; then
            echo -e "${GREEN}  [OK]   $file (${lines}行)${NC}"
        else
            echo -e "${GREEN}  [OK]   $file${NC}"
        fi
        ((PASSED++))
    else
        echo -e "${RED}  [FAIL] $file${NC}"
        for issue in "${issues[@]}"; do
            echo -e "${RED}         - $issue${NC}"
        done
        ((FAILED++))
    fi

    return $result
}

# 遍历文件检查
for file in "${FILES[@]}"; do
    check_file "$file"
done

# 输出总结
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  检查总结${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}通过: $PASSED${NC} | ${RED}失败: $FAILED${NC} | ${YELLOW}警告: $WARNINGS${NC} | ${YELLOW}跳过: $SKIPPED${NC}"
echo ""

# 如果有失败的文件，输出检查清单
if [ $FAILED -gt 0 ]; then
    echo -e "${RED}检查清单:${NC}"
    echo "1. 文件行数是否达到 ${MIN_LINES} 行？"
    echo "2. 是否有禁止的占位符文本？"
    echo "3. 章节结构是否完整？"
    echo "4. Frontmatter 格式是否正确？"
    echo ""
    echo -e "${YELLOW}参考文档: .claude/QUALITY_STANDARDS.md${NC}"
    echo ""
    exit 1
fi

if [ $WARNINGS -gt 0 ]; then
    echo -e "${YELLOW}有警告但不阻止提交，建议修复${NC}"
    echo ""
fi

echo -e "${GREEN}所有检查通过！${NC}"
exit 0
