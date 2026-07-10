# Obsidian Vault Pipeline (OVP2)

面向 Obsidian vault 的本地优先知识运行时。OVP2 把你捕获的文章、剪藏和书签
编译成一个可核查、有据可依的知识库——它保留的每一句结论都能追溯到原文的
逐字引文和行号。

[English](README.md)

## 这是什么

OVP2 把一个 vault 组织成三层：

| 层 | English | 内容 |
|---|---|---|
| 原文 | Source | 捕获的资料本身：网页剪藏、Pinboard 书签、手动投放的文件。永不改写。 |
| 记忆 | Memory | 每个源的接地 **Unit**（带行号的逐字引文）和只由这些 Unit 构成的可读 **Card**。 |
| 结晶 | Knowledge | 跨源 **Claim**，每条标注 **durable** 或 **caveated**，每个引用都能解析到 Unit、引文和原文行号。 |

真相层就是产品。不能引用逐字证据的主张不会被持久化：机械化的 gate 在写入前
把每个引用核对到已接受的 Unit；所有持久状态都存放在 vault 内的 append-only
账本（`.ovp/`）中。其余一切——搜索索引、web 门户、主题视图——都是投影，
随时可以删除并从账本完整重建。

## 从 OVP 到 OVP2

OVP2 是 Python 版 OVP（六阶段管线、`knowledge.db`、Evergreen/MOC 笔记、
`ovp`/`ovp-autopilot`/`ovp-ui`）的 Rust 全量重写。重写改变的不只是语言，
更是方向：急切的概念图谱与 canonical 本体抽取在真实数据上验证失败，系统围绕
接地阅读主干（grounded reader trunk）和带 gate 的结晶真相层重建。完整的
决策叙事、命令映射和现有 vault 的迁移说明见
[`docs/ovp-to-ovp2.zh-CN.md`](docs/ovp-to-ovp2.zh-CN.md)。

## 安装

macOS（arm64/x64）和 Linux（x64）的预编译二进制；不需要 Rust 工具链。
当前版本：**v0.23.0**。两个渠道都经过端到端验证。

```sh
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/fakechris/obsidian_vault_pipeline/releases/latest/download/ovp-cli-installer.sh | sh
```

或使用 Homebrew：

```sh
brew install fakechris/ovp2/ovp2
```

用 `ovp2 --version` 确认安装成功。细节、发布流程以及 `brew` 的代理注意事项
见 [`docs/install.md`](docs/install.md)。

## 快速开始

1. **配置 LLM**（live 运行需要）——写在 shell profile 或私有 `.env` 中
   （永远不要放进仓库或 vault）：

   ```sh
   export ANTHROPIC_API_KEY=sk-ant-...
   export OVP_LLM_TIMEOUT_SECS=480   # live 运行必需；默认 180s 会误杀慢响应
   # 可选：ANTHROPIC_BASE_URL、OVP_LLM_MODEL、OVP_LLM_MAX_TOKENS、OVP_LLM_NO_PROXY=1
   ```

2. **对你的 vault 跑每日循环**（先用 `--dry-run` 查看计划，不写任何东西）：

   ```sh
   ovp2 daily --vault-root ~/Documents/my-vault --client live
   ```

   一次运行会把捕获物归一化进队列（URL + 内容去重）、让每个新源走一遍接地
   阅读主干、把 reader pack 写进 vault、把每次尝试记录进 append-only 账本，
   并重建读模型。

3. **打开门户**：

   ```sh
   ovp2 serve --vault-root ~/Documents/my-vault
   ```

   然后在浏览器打开打印出的 URL（默认 `http://127.0.0.1:3141`）。

4. **可选——Pinboard 捕获**：

   ```sh
   ovp2 pinboard-sync --vault-root ~/Documents/my-vault --live --max 200
   ```

   需要 `PINBOARD_TOKEN`（`username:TOKEN`；不落盘、不进日志）。Pinboard API
   会返回账户的全部历史，因此首次同步有防洪保护：不带 `--since`/`--max` 时，
   任何将创建超过 500 条新笔记的运行都会在写入前中止。`--max 200` 只取最新
   200 条，更早的书签在后续运行中逐步补齐。

## 门户

`ovp2 serve` 在 vault 的读模型之上提供一个单页门户，共六个一级页面：

| 页面 | 回答的问题 |
|---|---|
| 今天 Today | 今天进来了什么、读完了什么、结晶了什么、有什么需要处理 |
| 资料 Library | 按集合、月份、状态浏览全部源；源详情页三层钻取（记忆 / 原文 / 主张） |
| 搜索 Search | 一个输入框搜源、卡片、单元、主张、主题（任何页面 `⌘K` 唤起） |
| 知识 Knowledge | 主题与主张、durable/caveated 状态、证据钻取、带作用域的图谱视图 |
| 对话 Ask | 基于 vault 证据的带引用问答；引用经过索引校验 |
| 系统 System | 运行记录、阻塞的源、`doctor` 结果、设置、概念说明 |

两套平权主题——浅色 "Atelier"（暖羊皮纸 + 赤陶土）与深色 "Vault"（近黑 +
深蓝 + 青）——界面默认英文，内置完整简体中文翻译，均可在 UI 中切换。

## 核心命令

每个 CLI 动词都在 `--help` 里标注 PRODUCT / DIAGNOSTIC / DEMOTED。产品面：

| 命令 | 作用 |
|---|---|
| `ovp2 daily` | 每日主循环：捕获清扫 → 每个新源走接地阅读主干 → 生命周期流转 → 账本 + 报告 → 读模型与控制台刷新 |
| `ovp2 serve` | 启动本机门户服务：`.ovp/console/` 页面 + JSON API（`/api/find`、`/api/search`、`/api/ask` 等） |
| `ovp2 ask` | 对产品状态做检索增强问答；输出带引用的回答，并做确定性引用校验 |
| `ovp2 pinboard-sync` | 把 Pinboard 书签物化为收件箱笔记，URL 去重，带首次同步防洪保护 |
| `ovp2 crystal-synth` | 一键 Crystal 合成：reader pack → 跨源主张 → 接地过滤 → 强度 gate → durable 写入 |
| `ovp2 crystal-review-session` | 为 caveated 主张准备一个有界的人工复核会话（复核单 + 决策模板） |
| `ovp2 index` | 重建读模型（`.ovp/index/index.json`）；永远是全量确定性重建 |
| `ovp2 find` | 查询读模型：源、pack、主张、运行、卡片、单元——按关键词、类型、状态、日期 |
| `ovp2 doctor` | vault 状态健康检查；`--fix` 只做安全修复，永不删除 |
| `ovp2 digest` | 每日摘要（`.ovp/digests/<date>.md`）；短暂复用面，不进账本 |
| `ovp2 project` | Projection Lanes：按 lane 查看主张，或把 durable 主张写成 vault 笔记（`--write` / `--rebuild`） |
| `ovp2 mcp` | MCP stdio 服务，向 MCP 兼容编辑器暴露 find/search/status/doctor 工具 |

## 隐私与信任

OVP2 是本地优先的。它知道的一切都以纯文件形式存放在你的 vault 内（`.ovp/`
账本与投影，加上笔记本身）；没有云端组件、没有账号，也**没有任何遥测**。
只有以下三类数据会离开你的机器，且每一类都由你显式配置：

- **LLM 调用** —— 在 `daily`、`ask`、`crystal-synth`（以及门户的 Ask 页面）
  期间，文章/书签文本会发送给**你自己**通过环境变量配置的 LLM 服务商
  （`ANTHROPIC_API_KEY`，可选 `ANTHROPIC_BASE_URL`）。不配置 key 就没有任何
  调用：默认运行是离线/回放模式。
- **Pinboard 同步** —— `pinboard-sync --live` 使用你的 `PINBOARD_TOKEN`
  与 pinboard.in 通信（token 不落盘、不进日志）。
- **Web/GitHub 补全** —— 补全功能会抓取你收藏的 URL 本身（GitHub 仓库链接
  还会请求 GitHub API 元数据）以获得正文内容。

除此之外，不传输任何东西。

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/ovp-to-ovp2.zh-CN.md`](docs/ovp-to-ovp2.zh-CN.md) | OVP → OVP2 的完整故事：改了什么、为什么、如何迁移（[English](docs/ovp-to-ovp2.md)） |
| [`docs/install.md`](docs/install.md) | 安装渠道与维护者发布流程 |
| [`docs/operator-runbook.md`](docs/operator-runbook.md) | 真实 vault 的日常操作：每日循环、故障处理、复核会话、恢复 |
| [`docs/architecture.md`](docs/architecture.md) | crate 地图、数据流、不变量、门户与演化内核 |
| [`docs/product-state-layout.md`](docs/product-state-layout.md) | 产品状态的存放位置；权威态 vs 派生态 |
| [`docs/invariants.md`](docs/invariants.md) | 架构不变量，尽可能由 CI 把关 |

## 状态

工作区共 22 个 Rust crate；780 个测试通过（1 个忽略），另有二进制级端到端
覆盖。每日循环、门户、Crystal 合成与复核流程已在真实 vault 上运行。发布
v0.23.0 延续仓库的发布谱系（v0.22.0 是最后一个 Python 时代版本）；v2.0.0
保留给合并主干 / Python 退役里程碑。进行中：真实 vault 的持续 dogfood 与
语义主题投影。历史阶段记录见 `docs/stage-*.md`；版本历史见
[`CHANGELOG.md`](CHANGELOG.md)。

## 许可证

本项目采用双许可证，二选一：

- MIT 许可证（[LICENSE-MIT](LICENSE-MIT)）
- Apache 许可证 2.0 版（[LICENSE-APACHE](LICENSE-APACHE)）

除非你另有明确声明，你有意提交并纳入本项目的任何贡献（按 Apache-2.0 许可证
的定义），都将按上述双许可证授权，不附加任何额外条款。

例外：随仓库分发的 IBM Plex 网页字体（`console-ui/src/design/fonts/`）
仍遵循 SIL Open Font License 1.1 ——见
[`console-ui/src/design/fonts/LICENSE.txt`](console-ui/src/design/fonts/LICENSE.txt)。
