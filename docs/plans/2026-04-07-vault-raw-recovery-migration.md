# Vault Raw Recovery Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 先从脏旧 vault 中恢复可追溯的 RAW corpus，再做目录重置和最新框架下的全链路重跑。

**Architecture:** 本次迁移分三层执行。第一层只做全库扫描、去重和资产清单生成，不触碰现有内容。第二层在打 Git 标签后，重建目标目录结构，把筛出的原始语料回收到新的 `50-Inbox/01-Raw`。第三层使用当前 `ovp` 主链和 MiniMax 模型从头重跑，并单独盯住解读质量与 canonical/derived 一致性。

**Tech Stack:** Python 3, Obsidian Vault Pipeline CLI, Markdown frontmatter scanning, CSV/TXT recovery inventories, Git tags.

### Task 1: 固化 RAW 恢复基线

**Files:**
- Create: `../ovp-vault/RAW_RECOVERY_INVENTORY_2026-04-07.csv`
- Create: `../ovp-vault/RAW_RECOVERY_INVENTORY_2026-04-07.md`
- Modify: `../ovp-vault/VAULT_ASSET_INVENTORY_2026-04-07.md`

**Step 1: 扫描全库原文候选**

信号：
- `source`
- `original_url`
- `type: raw-article`
- 文件名含 `原文`
- 正文含 `原文内容`
- 正文含 `> [!quote] 原文`

**Step 2: 生成候选 CSV 和摘要**

输出：
- 全量候选 CSV
- bucket 统计摘要

**Step 3: 人工确认明显误报**

排除：
- `.skill/*`
- 模板文件
- 本次迁移文档自身

### Task 2: 生成可执行回收清单

**Files:**
- Create: `../ovp-vault/raw_recovery_primary.txt`
- Create: `../ovp-vault/raw_recovery_salvage.txt`
- Create: `../ovp-vault/raw_needs_api_refetch.txt`
- Create: `../ovp-vault/archive_raw_salvage.txt`
- Create: `../ovp-vault/private_separate.txt`

**Step 1: 按 bucket 分流**

- `raw_primary`
- `raw_salvage`
- `raw_needs_api_refetch`
- `archive_raw_salvage`
- `private_separate`

**Step 2: 做第一轮去重**

键：
- `source`
- `original_url`
- `title`
- `date`

**Step 3: 保留冲突清单**

对去重冲突不直接覆盖，输出到单独 review 列表。

### Task 3: 冻结旧 vault 并准备目录重置

**Files:**
- Create: `../ovp-vault/MIGRATION_EXECUTION_CHECKLIST_2026-04-07.md`

**Step 1: 先打 Git 标签**

命令：
```bash
git -C ../ovp-vault tag vault-legacy-pre-migration-2026-04-07
```

**Step 2: 列出要保留和要清空的目录**

保留：
- Git 历史
- RAW 回收清单
- private 拆分清单

重置：
- `50-Inbox/*`
- `20-Areas/*`
- `10-Knowledge/*`
- 大部分 `60-Logs/*`

**Step 3: 明确不能整体删除的目录**

- `70-Archive` 只能按 `archive_raw_salvage.txt` 先抽取，再清理剩余子集

### Task 4: 恢复新的 `01-Raw`

**Files:**
- Modify: `../ovp-vault/50-Inbox/01-Raw/*`

**Step 1: 重建目标目录结构**

至少确保：
- `50-Inbox/01-Raw`
- `50-Inbox/02-Pinboard`
- `50-Inbox/03-Processed`
- `10-Knowledge/Atlas`
- `20-Areas`
- `60-Logs`

**Step 2: 按清单回填 RAW**

顺序：
1. `raw_recovery_primary.txt`
2. `raw_recovery_salvage.txt`
3. `archive_raw_salvage.txt`

**Step 3: Pinboard 单独回拉**

- 用 API 重拉，不直接依赖旧本地 pinboard markdown

### Task 5: 从头重跑 pipeline

**Files:**
- Modify: `../ovp-vault/.env`（仅在缺失时）
- Generate: 最新 canonical / derived 产物

**Step 1: 检查模型配置**

目标：
- 解读仍使用 MiniMax
- vault 根目录 `.env` 中配置正确

**Step 2: 跑主链**

建议顺序：
```bash
ovp --vault-dir ../ovp-vault --check
ovp --vault-dir ../ovp-vault --full
ovp-knowledge-index --vault-dir ../ovp-vault --stats --json
ovp-rebuild-registry --vault-dir ../ovp-vault --dry-run --json
```

**Step 3: 视情况跑 `--with-refine`**

只在首轮输出稳定后再开 refine，不作为第一轮默认动作。

### Task 6: 盯解读质量

**Files:**
- Generate: `../ovp-vault/60-Logs/migration-quality-report-*.md`

**Step 1: 抽样检查 MiniMax 生成的深度解读**

关注：
- 是否像原文摘抄
- 是否结构空洞
- 是否错误抽象
- 是否 source / title / 主结论错位

**Step 2: 质量不过关时先停**

如果解读质量明显不稳：
- 不继续 absorb 全量
- 先修 prompt / model config / batch strategy

### Task 7: 验收

**Step 1: 验证 RAW 回收结果**

指标：
- 新 `01-Raw` 文件数
- 去重后 source 数
- Pinboard API 缺项数

**Step 2: 验证 canonical 层**

命令：
```bash
ovp-rebuild-registry --vault-dir ../ovp-vault --dry-run --json
```

**Step 3: 验证 derived 层**

命令：
```bash
ovp-knowledge-index --vault-dir ../ovp-vault --stats --json
ovp-lint --vault-dir ../ovp-vault --check --json ../ovp-vault/60-Logs/migration-lint.json
```

**Step 4: 只在验证通过后再删旧中间层**

删除对象：
- 剩余 archive drop 子集
- 旧 processed/processing 遗留
- 旧 derived 痕迹
