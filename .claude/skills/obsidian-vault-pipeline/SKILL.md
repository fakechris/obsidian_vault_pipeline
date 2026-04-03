---
name: obsidian-vault-pipeline
description: |
  使用 Obsidian Vault Pipeline 自动化整理知识库。

  **触发场景：**
  - 用户说 "运行 WIGS 流程"、"整理 Obsidian Vault"、"处理知识库"
  - 用户说 "提取 Evergreen"、"更新 MOC"、"运行 Pipeline"
  - 用户提到 "整理笔记"、"知识管理"、"处理书签"
  - 用户说 "质检"、"质量检查"、"检查一致性"

  **Vault 位置设置：**
  默认使用当前工作目录作为 vault 根目录，或通过 `--vault-dir` 参数指定。

  只要用户提到 Obsidian、知识管理、WIGS、Pipeline、Evergreen、MOC 等关键词，就立即使用此 skill。
---

# Obsidian Vault Pipeline Skill

## 概述

此 skill 用于帮助用户运行 Obsidian Vault Pipeline 自动化知识管理流程。

## 安装

```bash
pip install obsidian-vault-pipeline
```

## Vault 位置设置

Pipeline 自动检测 vault 位置（按优先级）：

1. **当前工作目录** - 默认使用 `cwd`
2. ** `--vault-dir` 参数** - 显式指定
3. **环境变量** - `VAULT_DIR`

**最佳实践：**
```bash
cd /path/to/my-vault  # 进入 vault 目录
ovp --check           # 检查环境
ovp --full            # 运行完整 pipeline
```

## 可用命令

| 命令 | 说明 |
|------|------|
| `ovp --check` | 检查环境配置 |
| `ovp --init` | 初始化配置（交互式） |
| `ovp --full` | 运行完整 pipeline |
| `ovp-article --process-inbox` | 处理 50-Inbox/01-Raw/ 中的文章 |
| `ovp-evergreen --recent 7` | 提取最近7天的 Evergreen 笔记 |
| `ovp-moc --scan` | 扫描并更新 MOC 索引 |
| `ovp-quality --recent 7` | 质量检查 |

## 标准操作流程

### 1. 首次使用

```bash
# 进入 vault 目录
cd my-vault

# 检查环境
ovp --check

# 如果提示未配置，运行初始化
ovp --init
```

### 2. 日常处理

```bash
# 放入新文章到 50-Inbox/01-Raw/
cp article.md my-vault/50-Inbox/01-Raw/

# 运行 pipeline
ovp --full
```

### 3. WIGS 完整性检查

```bash
# 5层一致性检查
./60-Logs/scripts/check-consistency.sh

# 自动修复低风险问题
./60-Logs/scripts/repair.sh --auto
```

## 配置文件

在 vault 根目录创建 `.env`：

```bash
AUTO_VAULT_API_KEY=your_api_key
AUTO_VAULT_API_BASE=https://api.minimaxi.com/anthropic
AUTO_VAULT_MODEL=minimax/MiniMax-M2.5
```

## 触发词映射

| 用户说 | 执行命令 |
|--------|----------|
| "运行 WIGS 流程" | `./60-Logs/scripts/check-consistency.sh` |
| "整理 Obsidian" | `ovp --full` |
| "处理文章" | `ovp-article --process-inbox` |
| "提取 Evergreen" | `ovp-evergreen --recent 7` |
| "更新 MOC" | `ovp-moc --scan` |
| "质量检查" | `ovp-quality --recent 7` |
| "检查一致性" | `./60-Logs/scripts/check-consistency.sh` |

## 处理流程

```
50-Inbox/01-Raw/     →  ovp-article    →  20-Areas/深度解读
20-Areas/             →  ovp-evergreen  →  10-Knowledge/Evergreen/
20-Areas/             →  ovp-moc        →  10-Knowledge/Atlas/MOC-*.md
```

## 错误处理

| 错误 | 解决方案 |
|------|----------|
| API Key 未配置 | `ovp --init` |
| .env 未找到 | 在 vault 目录创建 .env |
| 找不到 vault | 确保在 vault 目录运行，或用 `--vault-dir` 指定 |
| 文章未处理 | 检查 50-Inbox/01-Raw/ 是否存在 .md 文件 |

## 相关仓库

- **Template**: https://github.com/fakechris/obsidian_vault_pipeline
- **Showcase**: https://github.com/fakechris/obsidian_vault_showcase
- **PyPI**: https://pypi.org/project/obsidian-vault-pipeline/
