# 60-Logs: System Documentation

自动化Pipeline和日志系统文档。

---

## 目录结构

```
60-Logs/
├── scripts/              # Pipeline脚本
│   ├── unified_pipeline_enhanced.py
│   ├── clippings_processor.py
│   ├── auto_article_processor.py
│   ├── batch_quality_checker.py
│   ├── auto_evergreen_extractor.py
│   ├── auto_moc_updater.py
│   ├── txn.sh
│   └── check-consistency.sh
├── transactions/       # 事务记录 (JSON)
├── quality-reports/    # 质检报告
├── pipeline-reports/   # Pipeline执行报告
├── Daily/             # 每日笔记
├── Weekly/            # 周回顾
└── Sessions/          # 会话记录
```

## 核心脚本

### 统一调度器

```bash
# 完整Pipeline
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full

# 历史Pinboard处理
python3 60-Logs/scripts/unified_pipeline_enhanced.py \
  --pinboard-history 2026-02-01 2026-02-28
```

### 事务管理

```bash
# 列出未完成事务
./60-Logs/scripts/txn.sh list

# 查看事务详情
./60-Logs/scripts/txn.sh show <txn-id>
```

### 一致性检查

```bash
# 运行5层检查
./60-Logs/scripts/check-consistency.sh

# 自动修复
./60-Logs/scripts/repair.sh --auto
```

## 日志格式

统一日志: `60-Logs/pipeline.jsonl`

```json
{
  "timestamp": "2026-04-02T12:00:00",
  "session_id": "20260402-120000-abc123",
  "event_type": "pipeline_started",
  ...
}
```

---

*参考完整文档: 60-Logs/scripts/PIPELINE_SYSTEM.md*