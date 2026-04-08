# Daily Ingestion Workflow

日常笔记增量处理工作流，整合 Clippings 和 Pinboard 书签的自动化处理。

> ⚠️ **目录保护规则（CRITICAL）**
> - **Clippings/** 目录必须保留在 Git 中，但内部的 .md 文件处理完成后可以删除
> - **绝不能删除、移动或修改**以 `.` 开头的目录（.skill/, .scripts/ 等）——这些是系统基础设施，不在本 Skill 管理范围内
> - 本 Skill 只操作常规内容目录（00-, 10-, 20-, 30-, 40-, 50-, 60-, 70- 前缀）

## 触发条件

当用户说"处理今天的书签"或"处理 Clippings"时激活此 skill。

> ⚠️ **必须同时遵守 [vault-conventions.md](vault-conventions.md) 中的所有规范**

---

## 前置准备

```bash
# 1. 拉取最新代码（必须！）
git checkout main && git pull origin main

# 2. 确认 git 状态
git status
```

## 检索配置

默认不需要 QMD。

当前日常流程的默认 discovery 已经统一到 `knowledge.db`：
- `ovp-query` 默认使用 `knowledge.db`
- 关键词检索使用 FTS5 BM25
- 语义检索使用本地 deterministic embeddings

只有在你需要额外的外部 discovery 对照时，才显式使用 QMD：

```bash
ovp-query "AI Agent 架构"
ovp-query --engine qmd "AI Agent 架构"
```

QMD 是可选 adapter，不参与自动链接和 canonical 概念决策。

---

## 工作流概览

```
Clippings/ (来源)
       ↓
   复制到 50-Inbox/01-Raw/
       ↓
   创建深度解读 → 20-Areas/AI-Research/ | 20-Areas/Tools/ | 20-Areas/Investing/ | 20-Areas/Programming/
       ↓
   更新 MOC
       ↓
Pinboard 增量书签
       ↓
   GitHub → 30-Projects/
       ↓
   Website → 10-Knowledge/Literature/
       ↓
   文章 → 50-Inbox/01-Raw/ + 深度解读
       ↓
   交叉引用更新
       ↓
   Git PR
```

---

## Step 1: 处理 Clippings

### 1.1 复制原文到 Inbox

```bash
# 复制所有 Clippings 到 50-Inbox/01-Raw/
for f in Clippings/*.md; do
  filename=$(basename "$f")
  date_str=$(date +%Y-%m-%d)
  cp "$f" "50-Inbox/01-Raw/${date_str}_${filename}"
done

# 或使用 rtk cp（注意编码问题）
```

### 1.2 创建深度解读

对每篇文章：
1. 根据内容判断分类（AI/工具/投资/编程）
2. 使用 `40-Resources/Templates/文章解读.md` 模板
3. Frontmatter 必须包含：
   - title（双引号）
   - date（YYYY-MM-DD）
   - type: analysis
   - tags（无空格，用 hyphens）
   - related（关联文章）

**分类规则**：
| 内容类型 | 存放目录 |
|---------|---------|
| AI/Agent/模型 | `20-Areas/AI-Research/Topics/YYYY-MM/` |
| 工具/产品 | `20-Areas/Tools/Topics/YYYY-MM/` |
| 编程技术 | `20-Areas/Programming/Topics/YYYY-MM/` |
| 投资思考/量化 | `20-Areas/Investing/Topics/YYYY-MM/` |

### 1.3 更新 MOC

在对应分类的 MOC 文件中添加链接：

```
## [分类名]（本期新增）

- [[YYYY-MM-DD_{文章名}_解读]]
```

---

## Step 2: 处理 Pinboard 增量书签

### 2.1 增量追踪

Pinboard 处理状态保存在 `50-Inbox/.pinboard_state.json`：

```json
{
  "last_processed_date": "2026-03-26",
  "first_processed_date": "2024-01-15",
  "last_processed_hash": "abc123..."
}
```

- `last_processed_date`: 正向处理的最晚日期
- `first_processed_date`: 反向处理的最早日期

### 2.2 执行增量处理

```bash
# 处理更新的书签（正向增量）
python3 pinboard-processor.py --incremental-forward

# 处理历史书签（反向增量）
python3 pinboard-processor.py --incremental-backward

# 双向都处理（从最早记录到最新）
python3 pinboard-processor.py --incremental-forward --incremental-backward

# 指定天数范围
python3 pinboard-processor.py 30
```

### 2.3 分类处理

#### 🔬 GitHub 项目 → `github-project-processor` skill
1. 探测 DeepWiki: `https://deepwiki.com/{owner}/{repo}`
2. 探测 Zread: `https://zread.ai/{owner}/{repo}`
3. 若都不存在，用 GitIngest
4. 归档到 `30-Projects/{date}_{owner}_{repo}.md`

#### 🌐 网站 → 10-Knowledge/Literature/
1. 抓取页面内容
2. 保存为 `10-Knowledge/Literature/YYYY-MM/{date}_{domain}.md`
3. frontmatter 标记 `type: literature`

#### 📝 文章 → 50-Inbox/01-Raw/ + 深度解读
1. 保存原文到 `50-Inbox/01-Raw/`
2. 创建深度解读到 `20-Areas/{area}/Topics/YYYY-MM/`

#### 📱 社交媒体 → 使用 `/browse` skill 抓取

---

## Step 3: 交叉引用更新

处理完所有文章后，更新 `related` 字段的交叉引用：

1. 扫描同分类下的所有文章
2. 识别相关主题的文章
3. 在 `related` 字段中添加双向链接

```python
# 伪代码：识别相关文章
def find_related(article, category_dir):
    keywords = extract_keywords(article.content)
    related = []
    for other in list_all_articles(category_dir):
        if other == article:
            continue
        if shared_keywords(keywords, other.keywords) > threshold:
            related.append(other.filename)
    return related
```

---

## Step 4: Git 提交

```bash
# 创建分支
git checkout -b bot/openclaw-$(date +%Y%m%d)

# 添加所有更改（显式指定文件，避免添加未跟踪文件）
git add 50-Inbox/ 20-Areas/ 30-Projects/ 10-Knowledge/
git add -u  # 更新已跟踪文件的删除/修改

# 提交（分类统计）
git commit -m "feat: 新增 N 篇 [分类] 文章深度解读 + M 条书签处理

- Clippings: N 篇
- Pinboard: M 条书签
  - GitHub: X 个
  - 网站: Y 个
  - 文章: Z 篇"

# 推送
git push -u origin bot/openclaw-$(date +%Y%m%d)
```

---

## Step 5: 创建 PR

```bash
gh pr create \
  --title "feat: 新增 N 篇 [分类] 文章深度解读" \
  --body "$(cat <<'EOF'
## Summary
- 新增 N 篇深度解读文章
- 处理 M 条 Pinboard 书签

## Test plan
- [ ] 确认所有文件 frontmatter 完整
- [ ] 确认 MOC 已更新
- [ ] 确认 related 交叉引用正确
EOF
)"
```

---

## 状态文件格式

### 50-Inbox/.clippings_state.json

```json
{
  "last_processed_date": "2026-03-26",
  "last_processed_hash": "f47ac10b58ccfef9",
  "processed_count": {
    "github": 45,
    "website": 12,
    "article": 8,
    "social": 3
  }
}
```

### 50-Inbox/.clippings_state.json

```json
{
  "last_processed_date": "2026-03-26",
  "processed_files": [
    "Clippings/xxx.md",
    "Clippings/yyy.md"
  ]
}
```

---

## 注意事项

1. **越长文章越有价值**：充分处理，不要吝惜 token
2. **中文文件名**：用 `cp` 可能有编码问题，可换用 `rtk cp`
3. **重复同名文章**：注意去重，检查 `50-Inbox/01-Raw/` 是否已存在
4. **Subagent 路由**：使用 `category="deep"` 会路由到 Sisyphus-Junior，可能不用配置的 MiniMax 模型。如果子任务失败，改为主会话直接处理。
5. **每次处理前**：必须 `git checkout main && git pull`
6. **PR 合并后**：更新状态文件的 `last_processed_date`

---

## 快捷命令

```bash
# 预览最近7天
python3 pinboard-processor.py 7

# 处理更新的书签（正向增量）
python3 pinboard-processor.py --incremental-forward

# 处理历史书签（反向增量）
python3 pinboard-processor.py --incremental-backward

# 实际处理书签
python3 pinboard-processor.py 7 --dry-run=false
```
