---
title: "Home"
description: "Obsidian Vault Pipeline 的入口导航"
date: 2026-04-03
type: meta
aliases: [首页, 入口, Vault Home]
---

# 🏠 Home

> **Obsidian Vault Pipeline** - 全自动知识管理系统的入口

---

## 🧭 快速导航

### 核心层级 (PARA)

| 层级 | 用途 | 位置 |
|------|------|------|
| 🔭 **[[00-Polaris/README\|Polaris]]** | 当前关注重点、人生北极星 | `00-Polaris/` |
| 📚 **[[10-Knowledge/README\|Knowledge]]** | 常青笔记、知识地图 (MOC) | `10-Knowledge/` |
| 🎯 **[[20-Areas/README\|Areas]]** | 深度解读、持续学习领域 | `20-Areas/` |
| 📂 **[[30-Projects/README\|Projects]]** | 有截止日的具体项目 | `30-Projects/` |
| 🔧 **[[40-Resources/README\|Resources]]** | 工具、参考资料库 | `40-Resources/` |
| 📥 **[[50-Inbox/README\|Inbox]]** | 待处理输入、原始素材 | `50-Inbox/` |
| 📝 **[[60-Logs/README\|Logs]]** | 日志、回顾、会话记录 | `60-Logs/` |
| 🗃️ **[[70-Archive/README\|Archive]]** | 归档完成项目 | `70-Archive/` |

---

## 🔄 日常工作流

### 自动化流水线
```
Pinboard → Clippings → 深度解读 → 质检 → Evergreen → MOC更新
   ↓          ↓           ↓         ↓        ↓           ↓
获取书签   迁移文件    LLM分析   6维度    概念提取    反向链接
                                              评分                    维护
```

**运行命令**: `python3 60-Logs/scripts/unified_pipeline_enhanced.py --full`

### 每日检查清单

- [ ] 运行 Pipeline 处理新内容
- [ ] 检查 [[50-Inbox/Processing-Queue\|处理队列]]
- [ ] 查看生成的深度解读
- [ ] 确认质量检查通过

### 每周回顾

- [ ] 更新 [[00-Polaris/README\|Top of Mind]]
- [ ] 审查 [[60-Logs/Weekly/\|周回顾]]
- [ ] 检查 Evergreen 索引完整性
- [ ] 归档完成项目到 `70-Archive/`

---

## 📊 系统状态

### 质量门禁
- **precommit-check**: `./.claude/precommit-check.sh`
- **一致性检查**: `./60-Logs/scripts/check-consistency.sh`
- **质量标准**: `.claude/QUALITY_STANDARDS.md`

### 最近处理
- 查看 [[60-Logs/pipeline-reports/\|Pipeline 报告]]
- 检查 [[60-Logs/transactions/\|事务状态]]

---

## 🔗 重要链接

### 知识地图 (MOC)
- **[[10-Knowledge/Atlas/MOC-AI-Research\|AI Research]]** - 人工智能研究
- **[[10-Knowledge/Atlas/MOC-Tools\|Tools]]** - 开发工具
- **[[10-Knowledge/Atlas/MOC-Investing\|Investing]]** - 投资研究
- **[[10-Knowledge/Atlas/MOC-Programming\|Programming]]** - 编程技术

### 常青笔记索引
- **[[10-Knowledge/Evergreen/README\|Evergreen Notes]]** - 原子化知识
- **[[10-Knowledge/Atlas/MOC-Index\|全局 MOC]]** - 所有索引入口

### 工具脚本
- **Pipeline 统一脚本**: `60-Logs/scripts/unified_pipeline_enhanced.py`
- **事务管理**: `60-Logs/scripts/txn.sh`
- **一致性检查**: `60-Logs/scripts/check-consistency.sh`

---

## 🆘 故障排查

### 常见问题

**Q: Pipeline 中断如何恢复？**
```bash
# 查看未完成事务
./60-Logs/scripts/txn.sh list

# 从指定步骤恢复
python3 60-Logs/scripts/unified_pipeline_enhanced.py --from-step articles
```

**Q: 如何检查断裂链接？**
```bash
./60-Logs/scripts/check-consistency.sh
```

**Q: 质量检查失败怎么办？**
```bash
# 检查具体文件
./.claude/precommit-check.sh path/to/file.md

# 查看质量标准
cat .claude/QUALITY_STANDARDS.md
```

---

## 📝 最近更新

- 2026-04-03: 添加质量门禁系统
- 2026-04-03: 创建导航系统
- 初始化: 建立 PARA 结构

---

## 📈 使用统计

### 内容生成统计

| 类型 | 统计方式 |
|------|----------|
| 深度解读 | `find 20-Areas -name "*.md" | wc -l` |
| 常青笔记 | `find 10-Knowledge/Evergreen -name "*.md" | wc -l` |
| Pipeline运行 | `cat 60-Logs/pipeline.jsonl | wc -l` |

---

## 🎯 本月目标

- [ ] 建立稳定的日常Pipeline流程
- [ ] 积累100个高质量Evergreen笔记
- [ ] 完善4个核心MOC索引

---

*这个页面是 Obsidian Vault 的入口。建议将其设置为默认打开页面。*

---
