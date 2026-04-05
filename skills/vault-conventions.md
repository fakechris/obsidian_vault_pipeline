# Vault Conventions Skill

OpenClaw Vault 的写作与组织规范。所有 Bot 和人工编辑必须遵循。

## 触发条件

任何时候在 vault 中创建或编辑笔记时，必须遵循以下规范。

## 目录结构

> ⚠️ **目录保护规则（CRITICAL）**
>
> 1. **Clippings/** 目录必须保留在 Git 中，但内部的 .md 文件处理完成后可以删除
> 2. **以 . 开头的目录**（.skill/, .scripts/ 等）**绝不能删除** —— 这些是系统基础设施，不在 Skill 管理范围内
> 3. Skill 只操作常规内容目录（00-, 10-, 20-, 30-, 40-, 50-, 60-, 70- 前缀）

| 目录 | 用途 | 对应 type | 管理方式 |
|------|------|-----------|----------|
| `.skill/` | Skill 定义文件（本目录）| — | **保护：绝不能删除** |
| `.scripts/` | 自动化脚本 | — | **保护：绝不能删除** |
| `Clippings/` | Obsidian Web Clipper 来源 | raw-article | **保留目录，文件可删** |
| `00-Polaris/` | 北极星层 - 当前关注、指南针 | meta | Skill 管理 |
| `10-Knowledge/Evergreen/` | 常青笔记 - 原子化概念 | evergreen | Skill 管理 |
| `10-Knowledge/Literature/` | 文献笔记 - 原文+初步标记 | literature | Skill 管理 |
| `20-Areas/AI-Research/` | AI 研究、Agent、模型 | analysis, note | Skill 管理 |
| `20-Areas/Tools/` | 工具评测、AI 工具、配置指南 | tool-review, analysis | Skill 管理 |
| `20-Areas/Programming/` | 编程技术、开发工作流 | analysis, note, tips | Skill 管理 |
| `20-Areas/Investing/` | 投资思考、商业分析 | analysis, note | Skill 管理 |
| `30-Projects/Active/` | 活跃项目文档 | project | Skill 管理 |
| `30-Projects/Archive/` | 已完成项目 | project | Skill 管理 |
| `40-Resources/Templates/` | Obsidian 模板 | template | Skill 管理 |
| `40-Resources/Snippets/` | 代码片段 | snippet | Skill 管理 |
| `40-Resources/Checklists/` | 检查清单 | checklist | Skill 管理 |
| `40-Resources/References/` | 参考资料 | reference | Skill 管理 |
| `50-Inbox/00-Capture/` | 快速捕获 | capture | Skill 管理 |
| `50-Inbox/01-Raw/` | 原始文章 | raw-article | Skill 管理 |
| `50-Inbox/02-Processing/` | 待处理 | processing | Skill 管理 |
| `50-Inbox/03-Processed/` | 已处理 | processed | Skill 管理 |
| `60-Logs/Daily/` | 每日笔记 | daily | Skill 管理 |
| `60-Logs/Weekly/` | 周回顾 | weekly | Skill 管理 |
| `70-Archive/` | 归档 | archive | Skill 管理 |
| `60-Logs/Weekly/` | 周回顾 | weekly |
| `70-Archive/` | 归档 | archive |

## Frontmatter 规范

### 必填字段

```yaml
---
title: "标题"
date: YYYY-MM-DD
type: raw-article | analysis | note | project | tool-review | tips | daily | meta
tags: []
---
```

### YAML 转义规则（必须遵守）

> ⚠️ **含特殊字符的值必须用双引号包裹**，否则 Obsidian 无法解析 properties。

以下字符在 YAML 值中必须加双引号：
- `:` 冒号 — 如 `title: "Code Mode: give agents an API"`
- `,` 逗号 — 如 `title: "API in 1,000 tokens"`
- `#` 井号 — 如 `title: "C# 入门指南"`
- `%` 百分号 — 如 `title: "让 Agent 审查 100% 代码"`
- `[` `]` `{` `}` 括号 — 如 `title: "React [Hooks] 详解"`
- `&` `*` `!` `|` `>` `'` `` ` `` 等 YAML 保留字符

**错误示例：**
```yaml
title: Code Factory: 让 Agent 自动编写代码   # ❌ 冒号导致解析失败
title: WEB 4.0: The birth of superintelligent life  # ❌ 同上
```

**正确示例：**
```yaml
title: "Code Factory: 让 Agent 自动编写代码"   # ✅
title: "WEB 4.0: The birth of superintelligent life"  # ✅
```

**简单规则：`title` 字段一律使用双引号包裹。**

### 可选字段

```yaml
author: 作者名
source: 来源 URL
status: active | archived | review
github: GitHub 仓库 URL
related: ["[[相关笔记]]"]
```

## Tag 命名规则（必须遵守）

> ⚠️ Obsidian tag **不支持空格和点号**，违反会导致红叉解析错误。

- ✅ 允许：字母、数字、下划线 `_`、连字符 `-`、斜杠 `/`（嵌套）
- ❌ 禁止：空格、`.` 点号、`,` 逗号、`#`（自动加）
- 多词 tag 用连字符连接：`Open-Source`、`Claude-Code`、`Context-Engineering`
- 嵌套用斜杠：`AI/agent`、`coding/workflow`

**错误示例：**
```yaml
tags: [Claude Code, Open Source, AGENTS.md]   # ❌ 空格和点号
```

**正确示例：**
```yaml
tags: [Claude-Code, Open-Source, AGENTS-md]   # ✅
```

## Tag 层级

使用嵌套 tag 结构：

- `#AI`, `#AI/agent`, `#AI/prompt`, `#AI/training`, `#AI/infra`, `#AI/product`
- `#coding`, `#coding/workflow`, `#coding/architecture`, `#coding/devtools`
- `#invest`, `#invest/macro`, `#invest/stock`, `#invest/business`
- `#tool`, `#tool/AI`, `#tool/dev`, `#tool/productivity`
- `#status/active`, `#status/archived`, `#status/review`
- `#evergreen`, `#evergreen/agent`, `#evergreen/harness`
- `#polaris`, `#polaris/meta`

## 文件命名规范

- 原始文章：`{YYYY-MM-DD}_{标题摘要}.md`
- 深度解读：`{YYYY-MM-DD}_{标题摘要}_深度解读.md`
- 常青笔记：`{Concept-Name}.md`
- MOC 文件：`{Area} MOC.md` 或 `MOC.md`
- 项目笔记：直接用项目名

## 写作规范

1. **必须使用 YAML frontmatter**，不可省略
2. **原文必须完整保存**到 `50-Inbox/01-Raw/`，不能只存摘要
3. **解读要有深度**：核心观点 → 重要细节 → 关联知识 → 行动建议 → 反思
4. **使用 wikilinks**（`[[笔记名]]`）建立笔记间关联
5. **新笔记创建后**，在对应 MOC 文件中添加链接
6. **禁止在根目录放散落笔记**，必须归入对应分类目录

## 模板使用

创建新笔记时，优先使用 `40-Resources/Templates/` 下的模板：

| 场景 | 模板 |
|------|------|
| 保存原始文章 | `40-Resources/Templates/文章原文` |
| 文章深度解读 | `40-Resources/Templates/文章解读` |
| 新建项目文档 | `40-Resources/Templates/项目笔记` |
| 评测工具/产品 | `40-Resources/Templates/工具评测` |
| 快速记录 | `40-Resources/Templates/快速笔记` |
| 每日笔记 | `40-Resources/Templates/每日笔记` |

## MOC 维护

每个区域目录下有一个 `{区域名} MOC.md` 索引文件。新增笔记后必须在对应 MOC 中添加 wikilink。

Vault 首页为 `Home.md`，汇总所有 MOC 入口。
