# OpenClaw Evergreen Skill

Create and maintain evergreen (atomic, permanent) notes in the vault.

## Evergreen Note Principles

1. **Atomic**: One concept per note
2. **Permanent**: Timeless value, not news
3. **Linked**: Connected to other notes
4. **Own Words**: Not copy-paste
5. **Discoverable**: Named as claims/statements

## Note Structure

```markdown
---
title: "Concept Name"
type: evergreen
date: YYYY-MM-DD
tags: [evergreen, domain-tag]
aliases: [alternative names]
---

# Concept Name

> **一句话定义**: Clear, concise definition

## 📝 详细解释

### 是什么？
Detailed explanation in your own words.

### 为什么重要？
Significance and applications.

## 🔗 关联概念
- [[Related Concept 1]] - relationship
- [[Related Concept 2]] - relationship

## 📚 来源与扩展阅读
- [[Source Literature Note]]
- External: [Title](URL)
```

## Naming Convention

Name notes as claims, not categories:
- ✅ "AI agents require persistent memory"
- ❌ "AI Memory"

## Directory Structure

```
10-Knowledge/
├── Evergreen/           # Destination
│   ├── AI-Agent.md
│   ├── Concept-Name.md
│   └── ...
└── Atlas/              # Update MOCs here
    └── MOC-Index.md
```
