---
title: "内容质量标准"
description: "Obsidian Vault Pipeline 内容质量标准文档"
date: 2026-04-03
type: meta
aliases: [质量标准, Quality Standards]
---

# Obsidian Vault Pipeline 内容质量标准

## 1. 目的

确保每次提交的内容达到统一的高质量标准，避免批量生成导致的系统性质量问题。

> ⚠️ **适用范围**：本标准适用于 **GitHub 项目分析**（20-Areas/Tools/）和 **网站/工具资源**（40-Resources/）。
> 文章深度解读（20-Areas/AI-Research/Topics/）的质量标准请参见 `README.md` 的 6 维度体系
> （一句话定义、详细解释、重要细节、架构图、行动建议、关联知识）。

---

## 2. 最低质量要求

### 2.1 文件长度

| 类型 | 最低行数 | 推荐行数 |
|------|----------|----------|
| Projects（深度分析） | 300行 | 500行以上 |
| Resources（网站/工具分析） | 300行 | 400行以上 |
| Papers（论文分析） | 300行 | 500行以上 |
| 普通文章深度解读 | 150行 | 300行以上 |

### 2.2 禁止的占位符模式

以下模式出现任何一个，该文件判定为**不合格**：

> **示例格式**：（以下为禁用模式示例，用 `*` 代替实际字符以避免误检测）
> - `Please refer to the *project's* documentation`
> - `Please check the *examples* directory or README`
> - `This *project* may be related to other...`
> - `This *project* contributes to the AI ecosystem`
> - `Configuration details depend on the specific *project*`
> - `For more details, visit the *project* repository`
> - `Review the GitHub Issues *page* for known issues`
> - `This *project* is related to`
> - `*Useful* for`
> - `More *information* can be found`
> - `详*见*官方文档`（敷衍中文占位符）
> - `请*参*考项目主页`（敷衍中文占位符）

### 2.3 章节结构要求

**GitHub Projects 必须包含以下章节：**

```
1. 项目概述（中英文）
2. 核心技术栈
3. 架构分析（含架构图）
4. 功能模块详解
5. 使用场景
6. 技术细节
7. 安装配置
8. API参考
9. 代码示例
10. 依赖关系
11. 许可证
12. Star历史
13. 总结评价
```

**Resources 必须包含以下章节：**

```
1. 项目概述（中英文）
2. 核心功能详解
3. 技术架构
4. 使用场景
5. 竞品对比
6. 定价模式
7. 优劣势分析
8. 适用人群
```

**普通文章深度解读 必须包含（6维度）：**

```
1. 一句话定义
2. 详细解释
3. 重要细节（≥3个技术点）
4. 架构图（可选，但推荐）
5. 行动建议（≥2条可落地）
6. 关联知识（有[[wikilink]]）
```

---

## 3. 语言规范

- **主要内容**：全中文撰写
- **专有名词**：保持英文（GitHub、Claude Code、API 等）
- **人名/地名**：保持英文
- **代码块内**：保持原始语言
- **Frontmatter**：使用标准 YAML 格式

---

## 4. 内容质量标准

### 4.1 原创性

- 禁止直接复制 README 的 HTML/Markdown 格式作为内容
- 禁止使用规则判断内容质量，必须用 LLM 深度分析
- 内容应该是基于源码/文档的深度理解，而非表面描述

### 4.2 实用性

- 包含具体的命令、代码示例
- 包含真实的竞品对比数据
- 包含实际的使用场景说明

### 4.3 可操作性

- 安装配置步骤清晰可执行
- API 参数说明完整
- 示例代码可直接使用

---

## 5. 提交流程

### 5.1 提交前检查

**每次提交前必须运行检查脚本：**

```bash
./.claude/precommit-check.sh
```

**检查内容：**
1. 文件行数 ≥ 最低要求
2. 无禁止的占位符模式
3. 文件数量 ≤ 10 个（单次提交）
4. Frontmatter 格式正确

### 5.2 提交步骤

```
1. 运行 precommit-check.sh 检查
2. 创建新分支
3. 提交文件
4. 推送并创建 PR
5. PR 内自己 Merge
6. 关闭 PR
7. 下次从 main 创建新分支
```

### 5.3 PR 自动合并规则

由于文档性质特殊，**提交 PR 后自行 merge 并关闭**：

```bash
# 创建 PR 后执行
gh pr merge --squash --delete-branch
```

**每次提交必须从 main 创建新分支**，禁止在旧分支上追加文件。

### 5.4 禁止的行为

- **禁止在已合并的分支上继续提交** — 每次从 main 新建
- **禁止单个 PR 超过 10 个文件** — 分批提交
- **禁止跳过 precommit-check.sh** — 必须检查通过才能提交
- **禁止提交不合格文件** — 发现质量问题立即修复

### 5.5 PR 描述要求

每个 PR 必须包含：

```markdown
## Quality Checklist

- [ ] 所有文件行数 ≥ 最低要求
- [ ] 无占位符文本
- [ ] 章节结构完整
- [ ] Frontmatter 格式正确
```

---

## 6. 问题处理

### 6.1 发现不合格文件

- **立即停止当前批次处理**
- **只提交已验证合格的文件**
- 不合格的单独重做

### 6.2 修复流程

```
发现问题 → 标记问题文件 → 单独重做 → 验证合格 → 补提
```

---

## 7. 自动化质量检查

### 7.1 AI 自动评分

Pipeline 集成 6 维度质量评分：

| 维度 | 权重 | 合格标准 | 检查方式 |
|------|------|----------|----------|
| 一句话定义 | 5分 | 存在且清晰 | LLM评估 |
| 详细解释 | 5分 | What/Why/How完整 | LLM评估 |
| 重要细节 | 5分 | ≥3个技术点 | LLM统计 |
| 架构图 | 5分 | 有ASCII图 | LLM检测 |
| 行动建议 | 5分 | ≥2条可落地 | LLM评估 |
| 关联知识 | 5分 | 有[[wikilink]] | 正则匹配 |

**总分≥18为合格**（平均3+）

### 7.2 批量质量检查

```bash
# 检查最近7天的文章
python3 60-Logs/scripts/batch_quality_checker.py --recent 7

# 检查所有文章
python3 60-Logs/scripts/batch_quality_checker.py --all

# 仅检查 AI-Research 领域
python3 60-Logs/scripts/batch_quality_checker.py --area ai-research
```

---

## 8. 附录

### 8.1 检查脚本使用说明

#### 安装 pre-commit hook（可选）

```bash
# 在仓库根目录执行
ln -sf .claude/precommit-check.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

#### 手动运行检查

```bash
# 检查所有暂存文件
./.claude/precommit-check.sh

# 检查指定文件
./.claude/precommit-check.sh file1.md file2.md

# 只检查行数
./.claude/precommit-check.sh --lines-only

# 只检查占位符
./.claude/precommit-check.sh --placeholders

# 设置最低行数要求
./.claude/precommit-check.sh --min-lines 200
```

### 8.2 GitHub Actions 自动检查

仓库已配置 `.github/workflows/daily-pipeline.yml`，每次推送自动运行质量检查。

---

**版本**：1.0
**创建日期**：2026-04-03
**最后更新**：2026-04-03
**适用于**：Obsidian Vault Pipeline
