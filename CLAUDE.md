# CLAUDE.md - Obsidian Vault Pipeline Schema

> 这是 Obsidian Vault Pipeline 的核心配置文件。它定义了知识库的架构、工作流程和质量标准。
> 核心理念：[Karpathy's LLM Wiki Pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
> - Obsidian 是 IDE —— 你查看、导航、思考的地方
> - LLM 是程序员 —— AutoPilot 自动完成编译、链接、维护
> - Wiki 是代码库 —— 持续编译、自动维护的结构化知识

## 当前平台边界

- 当前内置标准 pack 是 `default-knowledge`
- 主流程可显式选择 `--pack default-knowledge --profile full`
- AutoPilot 可显式选择 `--pack default-knowledge --profile autopilot`
- 第三方领域包通过 Pack API 接入，文档见 `docs/pack-api/`

---
schema_version: "1.0.0"
note_id: claude-0db92ed6

## 1. 目录结构 (PARA Method)

```
my-vault/
├── 00-Polaris/          # 北极星：当前聚焦领域
│   ├── README.md        # Top of Mind（每周手动更新）
│   └── Home.md          # Obsidian 首页入口
├── 10-Knowledge/        # 知识库：LLM 维护的原子知识
│   ├── Evergreen/       # 永恒笔记（原子概念）
│   └── Atlas/           # 知识地图 (MOC)
│       ├── MOC-Index.md         # 全局索引
│       ├── MOC-AI-Research.md   # AI 研究领域
│       ├── MOC-Tools.md       # 工具领域
│       ├── MOC-Programming.md # 编程领域
│       └── MOC-Queries.md     # 查询归档 (新增)
├── 20-Areas/            # 领域：深度解读输出
│   ├── AI-Research/
│   │   └── Topics/
│   │       └── 2026-04/       # YYYY-MM 子目录
│   ├── Tools/
│   ├── Investing/
│   └── Programming/
├── 30-Projects/         # 项目：有截止日的具体项目
├── 40-Resources/        # 资源：参考资料库
├── 50-Inbox/            # 收件箱：原始输入
│   ├── 01-Raw/         # 原始文章（AutoPilot 监控目录）
│   └── Processing-Queue.md
├── 60-Logs/             # 日志：审计与调试
│   ├── scripts/         # 维护脚本
│   ├── pipeline.jsonl   # 结构化日志
│   └── transactions/    # 事务状态
├── 70-Archive/          # 归档：已完成/过时内容
├── 80-Views/            # 视图：数据展示
├── 90-Templates/        # 模板：可复用模板
└── CLAUDE.md           # 本文件：Schema 定义
```

### 层级职责

| 层级 | 内容类型 | 维护者 | 更新频率 |
|-----|---------|-------|---------|
| 00-Polaris | Top of Mind, 导航 | 人 | 每周 |
| 10-Knowledge | 原子知识, 概念 | LLM | 自动 |
| 20-Areas | 深度解读, 分析 | LLM | 自动 |
| 30-Projects | 具体项目 | 人 | 手动 |
| 40-Resources | 参考资料 | 人 | 手动 |
| 50-Inbox | 原始输入 | 人/Auto | 实时 |
| 60-Logs | 审计日志 | 系统 | 实时 |
| 70-Archive | 归档内容 | 人 | 每月 |

---

## 2. 深度解读 6 维度质量标准

每篇深度解读必须满足以下 6 个维度，每项 0-5 分，总分 3.0 为及格线。

### 2.1 一句话定义 (Definition)
- 5 分：准确、简洁、无歧义的一句话概括核心概念
- 3 分：基本准确但略有冗余或歧义
- 1 分：模糊或错误

**模板**:
```markdown
> **一句话定义**: [核心概念] 是 [关键特征] 的 [类别/定位]，用于 [主要用途/解决的问题]。
```

### 2.2 解释深度 (Explanation)
- 5 分：What/Why/How 完整，逻辑清晰
- 3 分：覆盖了 2/3 维度
- 1 分：仅表面描述

**模板**:
```markdown
## 📝 详细解释

### 是什么？
[客观定义和核心特征]

### 为什么重要？
[价值和意义]

### 如何运作？
[机制/流程/原理]
```

### 2.3 细节丰富度 (Details)
- 5 分：≥3 个具体、可验证的技术点
- 3 分：2 个技术点
- 1 分：泛泛而谈

**要求**:
- 包含具体数字、版本号、时间节点
- 引用原始来源
- 技术细节可验证

### 2.4 结构化程度 (Structure)
- 5 分：清晰的架构图/流程图（ASCII 或 Mermaid）
- 3 分：有表格或列表组织信息
- 1 分：纯文本描述

**模板**:
```markdown
## 🏗️ 架构图

```
┌─────────┐    ┌─────────┐    ┌─────────┐
│ 输入层   │ → │ 处理层   │ → │ 输出层   │
└─────────┘    └─────────┘    └─────────┘
```
```

### 2.5 可执行性 (Actionable)
- 5 分：≥2 条具体、可落地的行动建议
- 3 分：1 条建议
- 1 分：无建议或建议模糊

**模板**:
```markdown
## 💡 行动建议

1. **短期**: [本周可以做的具体行动]
2. **长期**: [需要规划的中期行动]
```

### 2.6 知识网络 (Linking)
- 5 分：合理的双向链接，有助于知识导航
- 3 分：有链接但不够丰富
- 1 分：无链接或全是外部链接

**要求**:
- 链接到现有 Evergreen 概念
- 新概念提取为独立 Evergreen 页面
- 更新相关 MOC

---

## 3. Ingest 流程 (数据摄入 → 知识编译)

### 3.1 手动流程

```bash
# Step 1: 放置原始文件
mv article.md 50-Inbox/01-Raw/

# Step 2: 运行 Pipeline
ovp --full

# 或分步执行
ovp --step articles       # L1 → L2: 生成深度解读
ovp-absorb --recent 7     # L2 → L3: 提取/吸收 Evergreen 生命周期动作
ovp-moc --scan           # 更新 MOC 索引
```

### 3.2 AutoPilot 自动流程

```bash
# 启动守护进程
ovp-autopilot --watch=inbox --parallel=1

# 之后只需丢文件
mv article.md 50-Inbox/01-Raw/

# 全自动发生:
# 1. 检测新文件 → 加入队列
# 2. 生成深度解读 (6维度质量评分)
# 3. 质量达标 → absorb
# 4. 更新 MOC
# 5. 刷新 knowledge.db
# 6. 自动 git commit
```

### 3.3 完整数据流

```
50-Inbox/01-Raw/           # L0: 原始输入
    ↓
ovp-article --process     # L1 → L2
    ↓
20-Areas/AI-Research/     # L2: 深度解读
Topics/2026-04/
    ↓
ovp-absorb --recent 7     # L2 → L3
    ↓
10-Knowledge/Evergreen/   # L3: 原子概念
    ↓
ovp-moc --scan           # L3 → L4
    ↓
10-Knowledge/Atlas/       # L4: 知识地图
MOC-*.md
```

---

## 4. 命名约定

### 4.1 文件命名

**深度解读**:
```
{原始文件名}_深度解读.md
例如: ai-agent-architecture_深度解读.md
```

**Evergreen 原子笔记**:
```
{概念名}.md
例如: AI Agent.md, RAG.md, 注意力机制.md
```

**MOC 文件**:
```
MOC-{领域}.md
例如: MOC-AI-Research.md, MOC-Tools.md
```

### 4.2 目录结构

**时间子目录**:
```
20-Areas/AI-Research/Topics/2026-04/
20-Areas/AI-Research/Topics/2026-03/
格式: YYYY-MM
```

**日志文件**:
```
pipeline.YYYY-MM-DD.jsonl
transaction_{timestamp}.json
```

### 4.3 Frontmatter 模板

**深度解读**:
```yaml
---
title: "{原标题}"
description: "{一句话摘要}"
date: 2026-04-03
type: interpretation
source: "{原始 URL}"
tags: [tag1, tag2]
aliases: ["{别名}"]
---
```

**Evergreen**:
```yaml
---
title: "{概念名}"
type: evergreen
date: 2026-04-03
tags: [evergreen, {领域}]
aliases: ["{英文名}"]
---
```

---

## 5. Lint 规则 (知识健康检查)

### 5.1 WIGS 5层架构检查

```bash
ovp-lint  # 运行 WIGS 5层架构检查（默认启用）
ovp-lint --no-wigs  # 禁用 WIGS 检查，仅运行基础检查
```

**WIGS (Workflow Integrity Guarantee System) 5层架构**:

| 层级 | 检查项 | 说明 |
|------|--------|------|
| L1 | 事务状态 | 检查未完成的工作流事务 |
| L2 | 孤儿Evergreen | 未链接到MOC的Evergreen页面 |
| L2 | 断裂链接 | 无效的 `[[...]]` 链接 |
| L3 | Ingestion Pipeline | Clippings未迁移、Pinboard待处理、重复文件 |
| L4 | Areas完整性 | 未在MOC中索引的深度解读 |
| L4 | Git完整性 | 未提交的修改 |
| L5 | Archive层 | 超过1年未访问的归档文件 |

### 5.2 基础检查项

**孤儿页面检测**
- 扫描无入链的 Evergreen 页面
- 原因：未被任何深度解读或 MOC 引用
- 处理：补充引用或考虑删除

**缺失概念检测**
- 扫描深度解读中 `[[概念]]` 链接
- 检查目标页面是否存在
- 原因：提到了但未创建独立页面
- 处理：自动提取为 Evergreen

**过时页面检测**
- 标记超过 90 天未更新的技术内容
- 提示需要重新验证或更新

**断裂链接检测**
- 检查所有 `[[...]]` 链接是否有效
- 列出无效链接和来源页面

### 5.3 修复流程

```bash
# 检查并报告
ovp-lint --check

# 自动修复低风险问题
ovp-lint --fix

# 交互式修复（推荐）
ovp-lint --interactive
```

---

## 5.4 事务系统 (Transaction Manager)

用于跟踪长时间运行的工作流任务。

```bash
# 创建新事务
python3 -m openclaw_pipeline.txn start <type> <description>

# 更新步骤
python3 -m openclaw_pipeline.txn step <txn_id> <step_name> <status> [output]

# 完成事务
python3 -m openclaw_pipeline.txn complete <txn_id>

# 失败事务
python3 -m openclaw_pipeline.txn fail <txn_id> <reason>

# 列出未完成
python3 -m openclaw_pipeline.txn list

# 显示详情
python3 -m openclaw_pipeline.txn show <txn_id>

# 归档旧事务
python3 -m openclaw_pipeline.txn archive [days]
```

**事务JSON结构**:
```json
{
  "id": "txn-YYYYMMDD-HHMMSS-uuid",
  "type": "workflow_type",
  "description": "description",
  "status": "in_progress|completed|failed",
  "steps": {
    "step_name": {
      "status": "pending|processing|completed|failed",
      "output": "step output",
      "updated_at": "ISO timestamp"
    }
  },
  "checkpoint": "current step"
}
```

---

## 6. Query → 回写流程 (知识复利闭环)

### 6.1 查询并归档

```bash
# 查询并保存结果到 wiki
ovp-query "对比 AI Agent 和 RAG 的架构差异" --save-to "20-Areas/Queries/"

# 自动生成:
# - 20-Areas/Queries/2026-04/对比 AI Agent 和 RAG 的架构差异.md
# - 链接相关 Evergreen (AI Agent, RAG)
# - 更新 MOC-Queries.md
```

### 6.2 查询结果结构

```markdown
---
title: "对比 AI Agent 和 RAG 的架构差异"
date: 2026-04-03
type: query
query: "对比 AI Agent 和 RAG 的架构差异"
sources: ["[[AI Agent]]", "[[RAG]]", "..."]
---

# 对比 AI Agent 和 RAG 的架构差异

> **一句话总结**: ...

## 核心差异

| 维度 | AI Agent | RAG |
|-----|----------|-----|
| ... | ... | ... |

## 详细分析
...

## 🔗 相关概念
- [[AI Agent]]
- [[RAG]]
- [[架构设计]]
```

### 6.3 知识复利循环

```
Ingest → Query → Output → 回写 wiki → 下次 Query 可用
    ↑___________________________________|
               （复利循环）
```

---

## 7. Image 处理流程

### 7.1 自动下载

```bash
# AutoPilot 处理时自动检测
# 发现 md 中的图片 URL → 下载到本地

原始: ![图](https://example.com/img.png)
处理后: ![图](attachments/2026-04/img-hash.png)
```

### 7.2 可选：QMD 外部发现适配器

当前默认 discovery 已经统一到 `knowledge.db`：
- `ovp-query` 默认走 `knowledge.db`
- 关键词检索使用 FTS5 BM25
- 语义检索使用本地 deterministic embeddings

QMD 现在只是**显式可选**的外部发现适配器，不再是默认 runtime 依赖，也不会参与自动链接或 canonical identity 决策。

```bash
# 默认：knowledge.db
ovp-query "AI Agent 架构"

# 显式改用 qmd
ovp-query --engine qmd "AI Agent 架构"
```

如果需要保留 QMD 做人工探索或对照检索，可以单独安装并维护它，但不要把 QMD 结果当成自动 link resolution 的依据。

```bash
# .env
VAULT_ATTACHMENTS_PATH="50-Inbox/01-Raw/attachments/"
AUTO_DOWNLOAD_IMAGES=true
```

### 7.3 LLM 查看图片

处理流程中增加步骤：
1. 读取 markdown 文本
2. 提取图片路径
3. LLM 单独查看图片获取视觉信息
4. 综合文本和图片生成解读

---

## 8. 版本与更新

- **Schema 版本**: v0.2.0
- **最后更新**: 2026-04-04
- **更新记录**:
  - v0.2.0: 增加 AutoPilot、CLAUDE.md schema、Query 回写、Image 处理
  - v0.1.8: 基础 Pipeline 功能

---

## 9. 参考资料

- [Karpathy's LLM Wiki Pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [PARA Method](https://fortelabs.com/blog/para/)
- [Evergreen Notes](https://notes.andymatuschak.org/Evergreen_notes)
- [Zettelkasten](https://zettelkasten.de/)

---

*本文件由 LLM 维护，人类负责阅读和思考。*
