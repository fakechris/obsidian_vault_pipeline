# OpenClaw Vault Workflow Skill

Complete workflow for maintaining the vault's PARA structure.

## Vault Structure Overview

```
00-Polaris/        → Session Memory (Top of Mind)
10-Knowledge/      → Knowledge Graph (Atlas, Evergreen, Literature)
20-Areas/          → Areas of Responsibility
├── AI-Research/Topics/YYYY-MM/
├── Tools/Topics/YYYY-MM/
├── Investing/Topics/YYYY-MM/
└── Programming/Topics/YYYY-MM/
30-Projects/       → Active Projects
40-Resources/      → Templates, Snippets
50-Inbox/          → Ingestion Pipeline
60-Logs/           → Daily/Weekly/Sessions
70-Archive/        → Archive
```

## Safe File Operations (Critical)

### ⚠️ Use obsidian move ONLY

**NEVER use `mv` or filesystem move for markdown files**

Why: `mv` breaks wiki-links (`[[...]]`). Obsidian move updates all internal links.

**Correct syntax**:
```bash
obsidian move file="path/to/file.md" to="path/to/directory/"
```

## Article Interpretation Standards

### 6 Dimensions

Every 深度解读 file MUST contain:

```markdown
# 一句话定义
[One-sentence definition]

# 详细解释
[Core concept explanation]

# 重要细节
[At least 3 detailed sections]

# 架构图 / 流程图
[ASCII diagrams]

# 行动建议
[At least 2 actionable recommendations]

# 关联知识
[List of related wiki links]
```

## Quality Checklist
- [ ] Has one-sentence definition
- [ ] Has detailed explanation
- [ ] Has ≥3 important details
- [ ] Has architecture/process diagrams
- [ ] Has ≥2 action recommendations
- [ ] Has related knowledge links
