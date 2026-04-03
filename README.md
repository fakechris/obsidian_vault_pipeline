---
title: "Obsidian Vault Pipeline"
description: "全自动知识管理流水线"
date: 2026-04-03
type: meta
---

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

### 五大自动维护系统

| 系统 | 功能 | 自动化程度 | 价值 |
|------|------|-----------|------|
| **质量门禁** | 提交前自动检查行数/占位符/frontmatter | 100% | 确保内容质量 |
| **反向链接维护** | 自动检测断裂链接，更新MOC索引 | 95% | 知识图谱永不断裂 |
| **Evergreen维护** | LLM自动提取核心概念，创建原子笔记 | 90% | 知识复利增长 |
| **MOC维护** | 自动扫描新文件，更新知识地图 | 95% | 导航永远最新 |
| **运行质检** | 每步操作JSONL记录，可追溯可审计 | 100% | 完全透明可控 |

### 成熟度对标

本项目对标生产级 Obsidian Vault 管理方案，在以下方面达到同等或更高成熟度：

| 特性 | 本方案 | 备注 |
|------|--------|------|
| **质量门禁系统** | ✅ `.claude/precommit-check.sh` + `QUALITY_STANDARDS.md` | 提交前强制检查 |
| **Claude Code 集成** | ✅ `.claude/settings.local.json` | 完整权限配置 |
| **导航系统** | ✅ `Home.md` + `10-Knowledge/Atlas/` | 完整 MOC 体系 |
| **完整性检查** | ✅ `check-consistency.sh` + `repair.sh` | 5层检查+自动修复 |
| **事务管理** | ✅ `txn.sh` | 完整状态追踪 |
| **GitHub Actions** | ✅ `.github/workflows/` | 自动化CI/CD |

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

# ================================
# Vault配置 (通常自动检测)
# ================================
# WIGS_VAULT_DIR=/path/to/your/vault
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
│   ├── README.md            # Top of Mind - 每周回顾更新
│   └── Home.md              # 【入口导航】Obsidian首页
├── 10-Knowledge/
│   ├── Evergreen/            # 【自动维护】常青笔记（LLM自动提取）
│   └── Atlas/               # 【自动维护】MOC知识地图
│       ├── MOC-Index.md     # 全局索引
│       ├── MOC-AI-Research.md
│       ├── MOC-Tools.md
│       ├── MOC-Investing.md
│       └── MOC-Programming.md
├── 20-Areas/                 # 【自动+手动】深度解读输出
│   └── AI-Research/Topics/   # YYYY-MM/ 子目录
├── 50-Inbox/
│   ├── 01-Raw/             # 【自动填充】原始文章
│   └── Processing-Queue.md # 【建议手动】待处理队列跟踪
├── 60-Logs/
│   ├── scripts/              # 【直接使用】8个核心脚本
│   ├── pipeline.jsonl       # 【自动生成】统一结构化日志
│   └── transactions/         # 【自动生成】事务状态记录
└── .claude/                  # 【配置】Claude Code集成
    ├── QUALITY_STANDARDS.md # 内容质量标准
    ├── precommit-check.sh   # 提交前检查脚本
    └── settings.local.json  # 权限配置

### Obsidian 入口导航

在 Obsidian 中打开 Vault 后，建议将 `00-Polaris/Home.md` 设置为默认首页：

**Home.md 提供：**
- 📚 完整 PARA 层级导航
- 🔄 日常工作流快捷入口
- 📊 系统状态检查链接
- 🔗 所有 MOC 知识地图入口
- 🆘 故障排查快速链接

**设置默认页面：**
1. 安装 Homepage 插件（可选）
2. 或手动打开 `[[00-Polaris/Home|Home]]`

```

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

## 🔧 特殊内容处理（高价值）

### 1. GitHub 项目 13 节深度解读

**专用脚本**: `60-Logs/scripts/auto_github_processor.py`

**特殊之处**：
- 自动获取 README 和 stars 数
- 生成 **13 节结构化深度解读**（非普通文章的 6 维度）
- 包含 ASCII 架构图和能力置信度表格
- 自动输出到 `20-Areas/Tools/Topics/YYYY-MM/`

**使用方式**：
```bash
# 单个项目
python3 60-Logs/scripts/auto_github_processor.py \
  --single https://github.com/tw93/kaku

# 批量处理（urls.txt 每行一个 GitHub URL）
python3 60-Logs/scripts/auto_github_processor.py \
  --input github_urls.txt

# 预览模式
python3 60-Logs/scripts/auto_github_processor.py \
  --single https://github.com/microsoft/terminal --dry-run
```

**输出示例**：
```markdown
---
title: "Kaku - 跨平台悬浮视频播放器"
github: "https://github.com/tw93/kaku"
owner: "tw93"
repo: "kaku"
date: "2026-04-02"
type: github-project
tags: [tool, video-player, electron]
stars: 2847
---

# 一句话概述
Kaku 是一个基于 Electron 的跨平台悬浮视频播放器...

# 项目定位
在视频播放器生态中的位置...

# 核心能力
| 能力 | 说明 | 证据来源 | 置信度 |
|------|------|----------|--------|
| 悬浮播放 | 置顶窗口播放 | README | 5/5 |
| 跨平台 | Win/Mac/Linux | package.json | 5/5 |
...

# 技术架构
```
┌─────────────┐
│  UI Layer   │  (React + Electron)
├─────────────┤
│  Player Core│  (Video.js)
├─────────────┤
│  Platform   │  (Electron Main)
└─────────────┘
```
...
```

**与普通文章的区别**：
| 维度 | 普通文章 | GitHub项目 |
|------|----------|-----------|
| 结构 | 6维度 | 13节 |
| 输入 | 文本内容 | GitHub API + README |
| 输出位置 | 20-Areas/AI-Research/Topics/ | 20-Areas/Tools/Topics/ |
| 特殊字段 | 无 | stars, owner, repo |
| 架构图 | 可选 | 必须（ASCII） |
| 置信度 | 无 | 5分制表格 |

---

### 2. 学术论文深层解读

**专用脚本**: `60-Logs/scripts/auto_paper_processor.py`

**特殊之处**：
- 支持 arXiv 自动获取（标题、摘要、作者）
- 支持本地 PDF 文本提取
- **10 节学术结构**（方法复现指南是核心）
- 保留 LaTeX 公式和技术术语英文
- 自动输出到 `20-Areas/AI-Research/Papers/`

**使用方式**：
```bash
# arXiv 论文
python3 60-Logs/scripts/auto_paper_processor.py \
  --arxiv https://arxiv.org/abs/2401.12345

# 本地 PDF
python3 60-Logs/scripts/auto_paper_processor.py \
  --pdf ~/Downloads/paper.pdf \
  --title "Attention Is All You Need" \
  --authors "Vaswani et al."

# 批量处理
python3 60-Logs/scripts/auto_paper_processor.py \
  --input papers.txt
```

**输出示例**：
```markdown
---
title: "Attention Is All You Need"
source: "https://arxiv.org/abs/1706.03762"
authors: ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", ...]
date: "2026-04-02"
type: paper-analysis
arxiv_id: "1706.03762"
---

# 一句话核心贡献
提出了 Transformer 架构，完全基于注意力机制...

# 方法详解 ⭐
## 核心架构
```
Input → [Embedding + Positional Encoding]
      → [Encoder × N] → [Decoder × N]
      → [Linear + Softmax] → Output
```

## 关键创新：Multi-Head Attention
$$Attention(Q,K,V) = softmax(\frac{QK^T}{\sqrt{d_k}})V$$

# 方法复现指南 ⭐
## 伪代码
```python
# 1. 准备数据
src = embed(input) + positional_encode(input)

# 2. 编码器
for layer in encoder_layers:
    src = layer.norm(src + layer.self_attn(src))
    src = layer.norm(src + layer.ffn(src))

# 3. 解码器（带mask）
...
```

## 超参数
- d_model: 512
- n_layers: 6
- n_heads: 8
- d_k = d_v = 64

## 实现难点
1. 位置编码的sin/cos交替...
2. 解码器的look-ahead mask...

# 核心洞察
1. 注意力可以并行，比RNN快10倍
2. 长距离依赖直接建模...
...

# 关联研究
- [[RNN]] - 被取代的架构
- [[BERT]] - 基于Transformer的预训练
- [[GPT]] - 仅使用Decoder的变体
```

**与普通文章的区别**：
| 维度 | 普通文章 | 学术论文 |
|------|----------|----------|
| 结构 | 6维度 | 10节（学术向） |
| 输入 | URL/文本 | arXiv API / PDF |
| 输出位置 | Topics/ | Papers/ |
| 公式保留 | 无 | LaTeX |
| 术语处理 | 中英混合 | 保留英文 |
| 复现指南 | 无 | 详细（含伪代码） |
| 引用格式 | 无 | [作者, 年份] |

---

### 3. 三种处理流程对比

| 内容类型 | 脚本 | 输出结构 | 特殊能力 | 适用场景 |
|----------|------|----------|----------|----------|
| **普通文章** | `auto_article_processor.py` | 6维度 | 自动分类 | 博客、新闻、教程 |
| **GitHub项目** | `auto_github_processor.py` | 13节 | README解析+架构图 | 开源工具、框架 |
| **学术论文** | `auto_paper_processor.py` | 10节 | arXiv API+方法复现 | 研究论文、技术报告 |

---

## 🛡️ 质量门禁与一致性检查

### 提交前质量检查

**每次提交前必须运行：**

```bash
# 检查所有暂存文件
./.claude/precommit-check.sh

# 只检查行数
./.claude/precommit-check.sh --lines-only

# 只检查占位符
./.claude/precommit-check.sh --placeholders

# 设置最低行数（默认150）
./.claude/precommit-check.sh --min-lines 200
```

**检查内容：**
- ✅ 文件行数 ≥ 最低要求（默认150行）
- ✅ 无禁止的占位符文本（中英文）
- ✅ Frontmatter 格式正确
- ✅ 单次提交 ≤ 10 个文件

### WIGS 一致性检查系统

**Workflow Integrity Guarantee System** - 保证数据处理流程的完整性

```bash
# 运行5层一致性检查
./60-Logs/scripts/check-consistency.sh

# 预览修复方案（不执行）
./60-Logs/scripts/repair.sh --dry-run

# 自动修复低风险问题
./60-Logs/scripts/repair.sh --auto

# 交互式修复（推荐）
./60-Logs/scripts/repair.sh
```

**5层检查架构：**

| 层级 | 检查内容 | 自动修复 |
|------|----------|----------|
| **L1** | 未完成事务 | ❌ 需手动确认 |
| **L2** | 孤儿Evergreen/断裂链接 | ⚠️ 部分自动 |
| **L3** | Ingestion一致性 | ✅ 自动（重复文件） |
| **L4** | Areas完整性/Git提交 | ❌ 需手动 |
| **L5** | Archive层 | ❌ 需手动 |

### 事务管理系统

跟踪所有 Pipeline 执行的事务：

```bash
# 查看未完成事务
./60-Logs/scripts/txn.sh list

# 查看事务详情
./60-Logs/scripts/txn.sh show txn-20260403-120000-abc123

# 创建新事务（Pipeline自动调用）
./60-Logs/scripts/txn.sh start pipeline "处理最近7天"

# 更新事务步骤
./60-Logs/scripts/txn.sh step txn-xxx articles completed "8 interpretations"

# 完成事务
./60-Logs/scripts/txn.sh complete txn-xxx
```

### 质量评分系统

**6维度自动评分**（总分30分，≥18分合格）：

| 维度 | 权重 | 合格标准 |
|------|------|----------|
| 一句话定义 | 5分 | 存在且清晰 |
| 详细解释 | 5分 | What/Why/How完整 |
| 重要细节 | 5分 | ≥3个技术点 |
| 架构图 | 5分 | 有ASCII图 |
| 行动建议 | 5分 | ≥2条可落地 |
| 关联知识 | 5分 | 有[[wikilink]] |

```bash
# 批量质量检查
python3 60-Logs/scripts/batch_quality_checker.py --recent 7
python3 60-Logs/scripts/batch_quality_checker.py --all
```

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

**Q: 如何检查断裂链接？**
```bash
# 运行一致性检查
./60-Logs/scripts/check-consistency.sh

# 查看具体断裂的链接
./60-Logs/scripts/check-consistency.sh --verbose
```

**Q: 提交前质量检查失败怎么办？**
```bash
# 检查具体文件问题
./.claude/precommit-check.sh path/to/file.md

# 查看质量标准
cat .claude/QUALITY_STANDARDS.md

# 常见修复：
# 1. 文件行数不足 → 增加详细内容
# 2. 占位符文本 → 替换为实际内容
# 3. 缺少 frontmatter → 添加 YAML frontmatter
```

**Q: 如何修复检测到的问题？**
```bash
# 预览修复方案
./60-Logs/scripts/repair.sh --dry-run

# 交互式修复
./60-Logs/scripts/repair.sh

# 自动修复低风险问题
./60-Logs/scripts/repair.sh --auto
```

---

## 📝 手动维护清单

尽管 Pipeline 自动化程度达95%，以下仍需手动维护：

| 频率 | 任务 | 命令/文件 |
|------|------|----------|
| 每日 | 运行 Pipeline | `python3 60-Logs/scripts/unified_pipeline_enhanced.py --full` |
| 每日 | 检查系统状态 | `./60-Logs/scripts/check-consistency.sh` |
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
