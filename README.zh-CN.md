# OVP2 — 为 Obsidian vault 做的「有据可查」知识库

**把你收藏的文章变成可以提问的知识库——答案里的每一条引用，都能点回原文的真实行号。**

OVP2 是面向 Obsidian vault 的本地优先应用：读取你捕获的内容、提取接地记忆、结晶可核查的主张，并提供门户用于浏览、搜索与对话——不编造引用。

[English](README.md) · [安装说明](docs/install.md) · [运维手册](docs/operator-runbook.md)

<p align="center">
  <img src="docs/images/05-knowledge-graph.png" alt="OVP2 知识图谱" width="900" />
</p>

<p align="center"><em>知识图谱：真实 dogfood vault 中的主题与主张。</em></p>

---

## 为什么是 OVP2

多数笔记工具只存文本。OVP2 维护一层 **真相层**：

| 层 | 你看到什么 | 规则 |
|---|---|---|
| **原文 Source** | 剪藏、书签、投放的文件 | 永不改写 |
| **记忆 Memory** | 每篇文章的卡片与单元 | 单元必须对应逐字引文 + 行号 |
| **知识 Knowledge** | 跨源主张（claim） | 没有接地引用，就不能成为 durable |

不能指向 vault 内证据的句子，不会变成持久知识。搜索、图谱与对话都是账本之上的投影——随时可删、可重建。

---

## 门户界面

`ovp2 serve`（或 **OVP2 桌面应用**）在本地打开 vault 的读模型门户。

### 今天 Today — 发生了什么

晨间看板：捕获、阅读、新增主张，以及需要处理的事项。

<p align="center">
  <img src="docs/images/01-today.png" alt="Today 页面：统计与 Attention 列表" width="900" />
</p>

### 资料 Library — 全部收藏

按集合与月份浏览。打开任一来源，可见 **记忆卡片**、原文 markdown、引用该源的主张，以及邻域图谱。

<p align="center">
  <img src="docs/images/02-library.png" alt="Library 来源列表" width="900" />
</p>

<p align="center">
  <img src="docs/images/03-source-detail.png" alt="来源详情：记忆卡片与单元" width="900" />
</p>

### 知识 Knowledge — 主题与主张

durable / caveated 主张按主题分组。需要结构时切换 **列表 / 图谱 / 地形**。

<p align="center">
  <img src="docs/images/04-knowledge.png" alt="Knowledge 主题卡片" width="900" />
</p>

### 对话 Ask — 带回执的回答

自然语言提问。Agent 检索主张、来源与证据卡片；右侧 **过程图** 展示触及了什么；答案带 **可点开的编号引用**。

<p align="center">
  <img src="docs/images/06-ask.png" alt="Ask 空态与示例问题" width="900" />
</p>

<p align="center">
  <img src="docs/images/07-ask-history.png" alt="Ask 历史：带引用的回答与过程图" width="900" />
</p>

### 搜索 Search — 一个输入框

源、主张、pack、主题——任意页面 `⌘K` / `Ctrl+K`。

<p align="center">
  <img src="docs/images/08-search.png" alt="搜索 agent memory 的主张结果" width="900" />
</p>

另有 **标签 Tags**、**实体 Entities**、**系统 System**（运行记录、doctor、LLM 设置、日程）。浅色 Atelier / 深色 Vault 两套主题；界面支持 English 与简体中文。

---

## 日常怎么用

| 想做什么 | 怎么做 |
|---|---|
| 定时消化书签 / 剪藏 | `ovp2 schedule install`，再填写 `<vault>/.ovp/daily.env` |
| 跑一遍日循环 | `ovp2 daily --vault-root ~/path/to/vault --client live` |
| 打开界面 | `ovp2 serve --vault-root ~/path/to/vault` → 打开打印的 URL |
| 桌面端 | [OVP2.app 发布页](https://github.com/fakechris/obsidian_vault_pipeline/releases)（macOS） |
| 命令行提问 | `ovp2 ask --vault-root … "你的问题"` |
| 编辑器里当工具 | `ovp2 mcp`（stdio MCP） |

日循环：捕获清扫 → 新源接地阅读 → 账本 → 重建读模型。结晶合成把 reader pack 变成跨源主张，并由机械 gate 把关。

---

## 安装

macOS arm64 与 Linux x64 的预编译 **CLI**（当前线：**v2.0.1**），无需 Rust 工具链。

```sh
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/fakechris/obsidian_vault_pipeline/releases/latest/download/ovp-cli-installer.sh | sh
```

或：

```sh
brew install fakechris/ovp2/ovp2
```

```sh
ovp2 --version
```

渠道、桌面 DMG、回滚：[`docs/install.md`](docs/install.md)。

### 快速开始

1. **LLM 凭证**（live 阅读 / 对话需要）——放在私有 shell 或 vault 侧配置，不要提交进仓库：

   ```sh
   export ANTHROPIC_API_KEY=sk-ant-...
   export OVP_LLM_TIMEOUT_SECS=480
   ```

2. **跑一次日循环**（可先 `--dry-run`）：

   ```sh
   ovp2 daily --vault-root ~/Documents/my-vault --client live
   ```

3. **装日程**（可选）：

   ```sh
   ovp2 schedule install --vault-root ~/Documents/my-vault
   ```

4. **打开门户**：

   ```sh
   ovp2 serve --vault-root ~/Documents/my-vault
   ```

5. **Pinboard**（可选）：`PINBOARD_TOKEN=user:TOKEN`，再  
   `ovp2 pinboard-sync --vault-root … --live --max 200`

---

## 隐私与信任

本地优先：产品状态是 vault 内的普通文件（`.ovp/` 账本 + 笔记）。无账号，**无遥测**。

只有你**主动配置**时才会出网：

- **LLM 调用** — 处理的文本发往你配置的 API Key / 本地端点。无 Key 则仅离线 / 回放。
- **Pinboard** — 仅 `--live` + 你的 token（不写日志）。
- **网页 / GitHub  enrichment** — 在启用时抓取你书签的 URL（及 GitHub 元数据）。
- **手动诊断对比** — 仅在你主动跑 compare 命令、并指向自选外部服务时；不是 `daily` 的一部分。

---

## 更多文档

| 文档 | 用途 |
|---|---|
| [`docs/install.md`](docs/install.md) | 安装、桌面端、版本 |
| [`docs/operator-runbook.md`](docs/operator-runbook.md) | 真实 vault 运维与恢复 |
| [`docs/ovp-to-ovp2.zh-CN.md`](docs/ovp-to-ovp2.zh-CN.md) | 重写叙事与迁移（[EN](docs/ovp-to-ovp2.md)） |
| [`docs/architecture.md`](docs/architecture.md) | 架构（给工程师） |
| [`docs/product-state-layout.md`](docs/product-state-layout.md) | 磁盘上的产品状态 |
| [`CHANGELOG.md`](CHANGELOG.md) | 发布历史 |

[`docs/images/`](docs/images/) 中的截图来自本地 dogfood vault（公开技术剪藏）。门户运行时即可重拍；发布前请再扫一眼是否含密钥或私密内容。

---

## 状态

Rust 工作区（CLI + 门户 + 可选桌面端）。日循环、结晶合成、Ask agent 与门户已在真实 vault 上使用。产物见 [releases](https://github.com/fakechris/obsidian_vault_pipeline/releases)。

---

## 许可证

双许可，任选其一：

- MIT（[LICENSE-MIT](LICENSE-MIT)）
- Apache License 2.0（[LICENSE-APACHE](LICENSE-APACHE)）

除非另有明确说明，你有意提交纳入本作品的贡献（定义见 Apache-2.0）将按上述双许可授权。

例外：随附的 IBM Plex 网页字体（`console-ui/src/design/fonts/`）仍为 SIL Open Font License 1.1 —— 见 [`console-ui/src/design/fonts/LICENSE.txt`](console-ui/src/design/fonts/LICENSE.txt)。
