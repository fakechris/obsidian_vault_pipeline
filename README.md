# PARA + Zettelkasten Knowledge Management System

一个基于PARA方法和卡片盒笔记法（Zettelkasten）的Obsidian知识管理系统模板。

## 核心理念

### PARA方法
- **Projects**: 有明确目标和截止时间的项目
- **Areas**: 持续维护的责任领域
- **Resources**: 参考资料
- **Archive**: 归档

### 三层笔记架构
1. **Session Memory**: 当前关注重点（00-Polaris）
2. **Knowledge Graph**: 可搜索、可关联的知识网络（10-Knowledge/20-Areas）
3. **Ingestion Pipeline**: 内容摄入和处理（50-Inbox/60-Logs）

## 目录结构

```
📁 00-Polaris/               # 北极星层 - 当前关注重点
📁 10-Knowledge/             # 知识层
   ├── Atlas/               # 知识地图和MOC
   ├── Evergreen/             # 常青笔记（原子化概念）
   ├── Literature/            # 文献笔记
   └── Sources/               # 原文存储
📁 20-Areas/                 # 责任领域
   ├── AI-Research/         # AI研究
   ├── Tools/                 # 工具评测
   ├── Investing/             # 投资思考
   └── Programming/           # 编程技术
📁 30-Projects/              # 活跃项目
📁 40-Resources/             # 资源
📁 50-Inbox/                 # 收集箱（三层捕获）
📁 60-Logs/                  # 日志和自动化脚本
📁 70-Archive/               # 归档
```

## 自动化Pipeline

### 6步完整流程

```bash
# 处理Pinboard书签 + 本地剪藏 + 完整流程
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

1. **Pinboard** - 从Pinboard API获取书签
2. **Clippings** - 处理Obsidian Web Clipper内容
3. **Articles** - LLM生成6维度深度解读
4. **Quality** - 自动质量检查
5. **Evergreen** - 提取核心概念
6. **MOC** - 更新知识地图

## 快速开始

### 1. 克隆并初始化

```bash
git clone https://github.com/yourusername/para-zettelkasten-template.git my-vault
cd my-vault
```

### 2. 配置环境

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 填入你的API密钥
# - Pinboard API Token (可选)
# - LLM API Key (MiniMax/OpenAI/Anthropic)
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 使用Pipeline

```bash
# 处理最近7天的内容
python3 60-Logs/scripts/unified_pipeline_enhanced.py --pinboard-days 7

# 或仅处理本地Clippings
python3 60-Logs/scripts/unified_pipeline.py --full
```

## 核心文件说明

### 00-Polaris/README.md
定义你的当前关注重点。每次会话从这里开始。

### 50-Inbox/Processing-Queue.md
显式跟踪待处理队列，替代隐藏状态文件。

### 60-Logs/scripts/
完整自动化脚本集合：

| 脚本 | 功能 |
|------|------|
| `unified_pipeline_enhanced.py` | 统一调度器（含Pinboard） |
| `clippings_processor.py` | Clippings处理 |
| `auto_article_processor.py` | 文章深度解读 |
| `batch_quality_checker.py` | 批量质检 |
| `auto_evergreen_extractor.py` | Evergreen提取 |
| `auto_moc_updater.py` | MOC更新 |
| `check-consistency.sh` | 一致性检查 |
| `txn.sh` | 事务管理 |

## 质量保证

### 6维度深度解读标准

每篇深度解读必须包含：
1. 一句话定义
2. 详细解释（what/why/how）
3. 重要细节（≥3）
4. 架构图/流程图
5. 行动建议（≥2）
6. 关联知识（[[...]]格式）

### WIGS原则

- **W**orkflow **I**ntegrity **G**uarantee **S**ystem
- 强制使用 `obsidian move`（非mv）保护wiki-links
- 显式状态文件（Processing-Queue.md）
- 事务完整性（txn.sh）
- 幂等处理（manifest检查）

## 依赖

- **Obsidian** - 知识库软件
- **obsidian-cli** - 命令行工具（`obsidian move`必需）
- **Python 3.10+** - Pipeline脚本
- **jq** - JSON处理（可选但推荐）

## License

MIT License

## 致谢

- PARA方法: Tiago Forte
- Zettelkasten: Niklas Luhmann
- Obsidian: Dynalist团队
