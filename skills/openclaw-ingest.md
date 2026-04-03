# OpenClaw Ingest Skill

Ingest external content into the vault's Inbox system.

## Ingestion Workflow

### 1. URL → Raw Note

When processing a URL:
1. Fetch content
2. Extract: title, author, source URL, publish date
3. Create note in 50-Inbox/01-Raw/

### 2. Post-Ingestion Actions

After creating raw note:
1. Add entry to 50-Inbox/Processing-Queue.md
2. Suggest classification (AI/工具/投资/编程)
3. Estimate priority

## File Naming Convention

Raw notes: `YYYY-MM-DD_{sanitized-title}.md`

## Frontmatter Template

```yaml
---
title: "{extracted title}"
source: "{original URL}"
author: "{extracted author or unknown}"
published: "{YYYY-MM-DD}"
added: "{YYYY-MM-DD}"
type: raw-article
tags: []
status: raw
---
```

## Directory Structure

```
50-Inbox/
├── 00-Capture/        # Quick captures
├── 01-Raw/           # Original articles (destination)
│   └── YYYY-MM-DD_*.md
├── 02-Processing/    # Being analyzed
└── 03-Processed/     # Done processing
```
