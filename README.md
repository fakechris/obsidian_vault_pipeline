# Obsidian Vault Pipeline - 全自动知识管理流水线

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Obsidian](https://img.shields.io/badge/Obsidian-Plugin-7C3AED?logo=obsidian)](https://obsidian.md)

**一套基于 LLM 的全自动化 Obsidian 知识管理流水线**

[核心特性](#核心特性) • [快速开始](#快速开始) • [配置详解](#配置详解) • [使用指南](#使用指南) • [自动化原理](#自动化原理)

</div>

---

## 🎯 核心特性

### 全自动知识处理流水线

```
输入源 → 深度解读 → 质量质检 → 知识提炼 → 索引更新 → 日志记录
   ↓         ↓           ↓           ↓           ↓           ↓
Pinboard   LLM 6维度    自动评分   Evergreen   自动MOC    JSONL
Clippings   分析        1-5分      原子笔记    反向链接    结构化日志
```

### 四大自动维护系统

| 系统 | 功能 | 自动化程度 | 价值 |
|------|------|-----------|------|
| **反向链接维护** | 自动检测断裂链接，更新MOC索引 | 95% | 知识图谱永不断裂 |
| **Evergreen维护** | LLM自动提取核心概念，创建原子笔记 | 90% | 知识复利增长 |
| **MOC维护** | 自动扫描新文件，更新知识地图 | 95% | 导航永远最新 |
| **运行质检** | 每步操作JSONL记录，可追溯可审计 | 100% | 完全透明可控 |

---

## 🚀 快速开始

### 第一步：克隆并进入目录

```bash
git clone https://github.com/fakechris/obsidian_vault_pipeline.git my-vault
cd my-vault
```

### 第二步：配置 API Keys（关键步骤）

```bash
# 复制模板
cp .env.example .env

# 编辑配置文件
nano .env  # 或 vim .env
```

**`.env` 文件配置示例：**

```bash
# ================================
# 必需：LLM API（深度解读生成）
# ================================

# 选项1：MiniMax（推荐，成本低，中文好）
AUTO_VAULT_API_KEY=your_minimax_key_here
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
AUTO_VAULT_MODEL=minimax/MiniMax-M2.5

# 选项2：Anthropic Claude（质量最高）
# AUTO_VAULT_API_KEY=sk-ant-api-xxxxx
# AUTO_VAULT_API_BASE=https://api.anthropic.com
# AUTO_VAULT_MODEL=anthropic/claude-3-5-sonnet-20241022

# 选项3：OpenAI（备选）
# AUTO_VAULT_API_KEY=sk-xxxxx
# AUTO_VAULT_API_BASE=https://api.openai.com/v1

# ================================
# 可选：Pinboard（自动书签导入）
# ================================
# 从 https://pinboard.in/settings/password 获取
PINBOARD_TOKEN=your_username:your_api_token

# ================================
# 可选：代理配置
# ================================
HTTP_PROXY=http://127.0.0.1:7897
```

**获取 API Key：**

- **MiniMax**: 访问 https://api.minimaxi.com 注册获取
- **Anthropic**: 访问 https://console.anthropic.com 获取
- **Pinboard**: 访问 https://pinboard.in/settings/password

### 第三步：安装依赖

```bash
pip install -r requirements.txt
```

### 第四步：运行完整流水线

```bash
# 处理最近7天（Pinboard + Clippings + 全流程）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

---

## 📖 使用指南

### 日常使用方法

```bash
# 1. 每日自动处理（添加到 crontab）
0 9 * * * cd /path/to/my-vault && python3 60-Logs/scripts/unified_pipeline_enhanced.py --full

# 2. 处理最近30天（批量历史处理）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --pinboard-days 30

# 3. 处理指定日期范围（如2026年2月）
python3 60-Logs/scripts/unified_pipeline_enhanced.py \
  --pinboard-history 2026-02-01 2026-02-28

# 4. 仅处理本地 Clippings（无 Pinboard）
python3 60-Logs/scripts/unified_pipeline.py --full

# 5. 仅更新 MOC 索引（快速维护）
python3 60-Logs/scripts/unified_pipeline.py --step moc

# 6. 预览模式（不实际执行，查看会处理什么）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full --dry-run
```

### 单步调试使用

```bash
# Step 1: 仅获取 Pinboard 书签
python3 60-Logs/scripts/unified_pipeline_enhanced.py --step pinboard --pinboard-days 7

# Step 2: 仅处理 Clippings 迁移
python3 60-Logs/scripts/unified_pipeline.py --step clippings

# Step 3: 仅生成深度解读
python3 60-Logs/scripts/unified_pipeline.py --step articles

# Step 4: 仅质量检查
python3 60-Logs/scripts/batch_quality_checker.py --all

# Step 5: 仅提取 Evergreen
python3 60-Logs/scripts/auto_evergreen_extractor.py --recent 7

# Step 6: 仅更新 MOC
python3 60-Logs/scripts/auto_moc_updater.py --scan
```

---

## ⚙️ 配置详解

### 目录结构说明

```
my-vault/
├── 00-Polaris/               # 【手动维护】当前关注重点
│   └── README.md            # Top of Mind - 每周回顾更新
├── 10-Knowledge/
│   ├── Evergreen/            # 【自动维护】常青笔记（LLM自动提取）
│   └── Atlas/               # 【自动维护】MOC索引（脚本自动更新）
├── 20-Areas/                 # 【自动+手动】深度解读输出
│   └── AI-Research/Topics/   # YYYY-MM/ 子目录
├── 50-Inbox/
│   ├── 01-Raw/             # 【自动填充】原始文章
│   └── Processing-Queue.md # 【建议手动】待处理队列跟踪
└── 60-Logs/
    ├── scripts/              # 【直接使用】8个核心脚本
    ├── pipeline.jsonl       # 【自动生成】统一结构化日志
    └── transactions/         # 【自动生成】事务状态记录
```

### API 配置对比

| 提供商 | 注册地址 | 成本 | 中文支持 | 推荐场景 |
|--------|----------|------|----------|----------|
| **MiniMax** | api.minimaxi.com | ¥0.01/1K tokens | 优秀 | 日常批量处理 |
| **Anthropic** | console.anthropic.com | $0.03/1K tokens | 良好 | 高质量深度解读 |
| **OpenAI** | platform.openai.com | $0.01-0.03/1K tokens | 良好 | 备选方案 |

**成本估算：**
- 处理10篇文章（每篇5000 tokens）：约 ¥1-3 元
- 处理100篇GitHub项目（13节深度解读）：约 ¥10-30 元

---

## 🤖 自动化原理详解

### 1. 反向链接维护（WIGS核心）

**问题：** 传统 `mv` 命令会断裂 Obsidian 的 `[[wikilink]]`

**解决方案：**
```bash
# ✅ Pipeline强制使用 obsidian move
obsidian move file="source.md" to="dest/"
# 自动更新所有反向链接

# ❌ 传统方式（Pipeline禁止）
mv source.md dest/  # 会破坏链接
```

**自动化流程：**
1. 文件名清理（移除特殊字符 `"'《》` 等）
2. obsidian CLI 迁移（自动维护链接）
3. MOC 自动更新（重新索引）

**质量保证：**
- 一致性检查脚本 `check-consistency.sh` 每日扫描断裂链接
- 发现孤儿笔记自动报告

### 2. Evergreen 自动维护

**传统方式：**
- 手动阅读 → 理解 → 提炼 → 创建笔记 → 添加链接（耗时30分钟/篇）

**Pipeline自动化：**
```bash
# LLM自动执行（耗时30秒/篇，成本¥0.1）
python3 60-Logs/scripts/auto_evergreen_extractor.py --recent 7
```

**提取流程：**
1. 读取深度解读文件
2. LLM识别核心概念（3-5个）
3. 生成原子化笔记：
   ```markdown
   ---
   title: "AI-Agent-Memory-Architecture"
   type: evergreen
   ---
   > 一句话定义：AI Agent使用分层记忆架构...
   ## 详细解释...
   ## 关联概念：[[LLM]] [[Context-Window]]
   ```
4. 自动添加到 `10-Knowledge/Evergreen/`
5. 更新 Atlas MOC

**幂等保证：**
- 检查同名笔记是否存在
- 存在则跳过，避免重复

### 3. MOC 自动维护

**自动化流程：**
```bash
python3 60-Logs/scripts/auto_moc_updater.py --scan
```

**执行逻辑：**
1. 扫描4个Areas：
   - `20-Areas/AI-Research/Topics/YYYY-MM/`
   - `20-Areas/Tools/Topics/YYYY-MM/`
   - `20-Areas/Investing/Topics/YYYY-MM/`
   - `20-Areas/Programming/Topics/YYYY-MM/`

2. 检测未索引文件：
   - 检查文件名是否出现在对应 MOC.md
   - 检查 `[[filename]]` 是否存在

3. 自动添加索引：
   ```markdown
   ## 2026-04
   - [[2026-04-02_Article_深度解读]]
   - [[2026-04-03_Another_深度解读]]
   ```

4. Atlas全局索引：
   - 更新 `10-Knowledge/Atlas/MOC-Index.md`
   - 添加新 Evergreen 链接

**月度自动分组：**
- 新文件自动归入 `## YYYY-MM` 章节
- 历史文件保持不动

### 4. 运行记录质检（100%透明）

**统一日志系统：**

位置：`60-Logs/pipeline.jsonl`（结构化JSON Lines）

```json
{"timestamp":"2026-04-02T09:00:00","session_id":"abc123","event_type":"pipeline_started","txn_id":"txn-20260402-090000"}
{"timestamp":"2026-04-02T09:00:05","session_id":"abc123","event_type":"pinboard_fetched","count":15,"github":8,"articles":4,"websites":3}
{"timestamp":"2026-04-02T09:00:30","session_id":"abc123","event_type":"clippings_migrated","count":3,"files":["file1.md","file2.md"]}
{"timestamp":"2026-04-02T09:02:00","session_id":"abc123","event_type":"article_processed","file":"article.md","classification":"ai","tokens":1534,"model":"minimax/MiniMax-M2.5"}
{"timestamp":"2026-04-02T09:05:00","session_id":"abc123","event_type":"quality_checked","file":"article.md","score":26,"qualified":true}
{"timestamp":"2026-04-02T09:10:00","session_id":"abc123","event_type":"evergreen_created","concept":"AI-Agent-Memory","path":"10-Knowledge/Evergreen/AI-Agent-Memory.md"}
{"timestamp":"2026-04-02T09:15:00","session_id":"abc123","event_type":"moc_updated","area":"AI-Research","files_added":2}
{"timestamp":"2026-04-02T09:15:30","session_id":"abc123","event_type":"pipeline_completed","duration_s":930,"total_cost_¥":2.5}
```

**事务管理系统：**

```bash
# 查看最近事务
./60-Logs/scripts/txn.sh list

# 输出示例：
# Incomplete transactions:
#   • txn-20260402-090000-abc123 | enhanced-pipeline | Process last 7 days | started: 2026-04-02T09:00:00

# 查看详细状态
./60-Logs/scripts/txn.sh show txn-20260402-090000-abc123

# 输出示例（JSON格式）：
# {
#   "id": "txn-20260402-090000-abc123",
#   "type": "enhanced-pipeline",
#   "status": "completed",
#   "steps": {
#     "pinboard": {"status": "completed", "output": "15 bookmarks"},
#     "articles": {"status": "completed", "output": "8 interpretations"},
#     ...
#   }
# }
```

**质检报告：**

每次运行自动生成：`60-Logs/pipeline-reports/pipeline-report-YYYYMMDD-HHMMSS.md`

```markdown
# Pipeline执行报告
生成时间: 2026-04-02T09:15:30
事务ID: txn-20260402-090000-abc123

## 执行步骤
| 步骤 | 状态 | 详情 |
|------|------|------|
| pinboard | ✅ 成功 | 15 bookmarks |
| clippings | ✅ 成功 | 3 files |
| articles | ✅ 成功 | 8 interpretations |
| quality | ✅ 成功 | 7/8 qualified |
| evergreen | ✅ 成功 | 12 concepts |
| moc | ✅ 成功 | 8 files indexed |

## 总体状态
**全部成功**
完成步骤: 6/6
总成本: ¥2.5
总耗时: 930s
```

**审计追溯：**
```bash
# 查询某天的处理记录
cat 60-Logs/pipeline.jsonl | jq 'select(.timestamp | startswith("2026-04-02"))'

# 统计本月成本
cat 60-Logs/pipeline.jsonl | jq -s '[.[] | select(.event_type=="pipeline_completed").duration_s] | add'

# 查找失败记录
cat 60-Logs/pipeline.jsonl | jq 'select(.event_type | contains("error"))'
```

---

## 📊 质量保证机制

### 5层一致性检查

```bash
# 运行完整检查
./60-Logs/scripts/check-consistency.sh
```

| 层级 | 检查内容 | 自动修复 |
|------|----------|----------|
| L1 | 未完成事务 | 可恢复 |
| L2 | 孤儿Evergreen/断裂链接 | 报告提示 |
| L3 | Ingestion一致性 | 部分自动 |
| L4 | Areas完整性/Git提交 | 需手动 |
| L5 | Archive层 | 需手动 |

### 6维度质量评分

自动质检标准：

| 维度 | 权重 | 合格标准 | 检查方式 |
|------|------|----------|----------|
| 一句话定义 | 5分 | 存在且清晰 | LLM评估 |
| 详细解释 | 5分 | What/Why/How完整 | LLM评估 |
| 重要细节 | 5分 | ≥3个技术点 | LLM统计 |
| 架构图 | 5分 | 有ASCII图 | LLM检测 |
| 行动建议 | 5分 | ≥2条可落地 | LLM评估 |
| 关联知识 | 5分 | 有[[wikilink]] | 正则匹配 |

**总分≥18为合格**（平均3+）

不合格文件自动生成修复建议报告。

---

## 🔧 故障排查

### 常见问题

**Q: API Key 无效？**
```bash
# 检查环境变量
env | grep AUTO_VAULT

# 重新加载 .env
source .env
```

**Q: obsidian move 命令找不到？**
```bash
# 安装 Obsidian CLI
npm install -g obsidian-cli

# 或手动下载
# https://github.com/obsidianmd/obsidian-api
```

**Q: Pipeline 中断如何恢复？**
```bash
# 查看未完成事务
./60-Logs/scripts/txn.sh list

# 从中断步骤恢复（例如从 articles 开始）
python3 60-Logs/scripts/unified_pipeline_enhanced.py --from-step articles
```

**Q: 如何查看处理历史？**
```bash
# 最近10条日志
cat 60-Logs/pipeline.jsonl | tail -10

# 查找特定文件的处理记录
cat 60-Logs/pipeline.jsonl | grep "filename"
```

---

## 📝 手动维护清单

尽管 Pipeline 自动化程度达95%，以下仍需手动维护：

| 频率 | 任务 | 命令/文件 |
|------|------|----------|
| 每日 | 运行 Pipeline | `python3 60-Logs/scripts/unified_pipeline_enhanced.py --full` |
| 每周 | 更新 Top of Mind | 编辑 `00-Polaris/README.md` |
| 每周 | 审查质检报告 | 查看 `60-Logs/quality-reports/*.md` |
| 每月 | 归档旧文件 | 使用 `obsidian move` 移动到 `70-Archive/` |
| 每季 | 优化 Evergreen 链接 | 手动添加跨领域关联 |

---

## 🎓 进阶使用

### GitHub Actions 自动运行

已配置 `.github/workflows/daily-pipeline.yml`：

```yaml
name: Daily Knowledge Pipeline
on:
  schedule:
    - cron: '0 9 * * *'  # 每天上午9点
  workflow_dispatch:       # 支持手动触发
```

配置 Secrets：
1. 仓库 Settings → Secrets and variables → Actions
2. 添加 `PINBOARD_TOKEN`
3. 添加 `AUTO_VAULT_API_KEY`
4. 添加 `AUTO_VAULT_API_BASE`

### 自定义 Pipeline

编辑 `60-Logs/scripts/unified_pipeline_enhanced.py`：

```python
# 修改默认参数
PINBOARD_DAYS = 7  # 改为14天
BATCH_SIZE = 10  # 改为5
EVERGREEN_RECENT_DAYS = 7  # 改为14
```

---

## 📜 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

**开始使用：**
```bash
git clone https://github.com/fakechris/obsidian_vault_pipeline.git
cd obsidian_vault_pipeline
cp .env.example .env
# 编辑 .env 填入 API Key
pip install -r requirements.txt
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```
