# Pack API v1

面向第三方开发者的 Domain Pack 开发文档。

当前状态：

- 这不是纯设计稿了，core 已经实现了最小 pack runtime
- 内置 `research-tech` 已经按 pack 运行
- `default-knowledge` 当前保留为默认兼容 pack
- `ovp` / `ovp-autopilot` 已经支持 `--pack` / `--profile`
- core 已支持 entry point 和 manifest 两种 pack 发现路径

这套 API 的目标不是让外部开发者“改 core”，而是让他们在 **不破坏 core 运行时契约** 的前提下，开发自己的领域包：

- `research-tech`
- `default-knowledge`
- `media-editorial`
- `medical-evidence`
- `engineering-research`
- 未来其他领域

## 1. 平台结构

OpenClaw 平台分成三层：

1. **Core Platform**
2. **Domain Pack**
3. **Workflow Profile**

### Core Platform 负责什么

Core 负责通用运行时，不负责某个领域的知识定义。

Core 拥有：

- runtime / vault layout
- pipeline orchestration
- autopilot / queue / watcher
- identity helpers
- registry framework
- derived `knowledge.db`
- graph / lint / audit 基础设施
- plugin loader
- evidence schema 基础契约

### Domain Pack 负责什么

Pack 负责领域语义。

Pack 定义：

- 对象类型
- artifact families
- assembly recipes
- governance contracts
- compiled page contracts and entry products
- schema
- discovery 规则
- absorb / refine 规则
- lint 规则
- prompts / templates
- workflow profiles

### Workflow Profile 负责什么

Profile 是某个 pack 下的一条可执行 DAG。

例如：

- `research-tech/full`
- `research-tech/autopilot`
- `media-editorial/daily-desk`
- `media-editorial/weibo-fastlane`
- `default-knowledge/full`
- `default-knowledge/autopilot`

当前 core 已落地的是：

- `research-tech/full`
- `research-tech/autopilot`
- `default-knowledge/full`
- `default-knowledge/autopilot`

当前默认 workflow 入口走的是：

- `ovp --full` -> `research-tech/full`
- `ovp-autopilot` -> `research-tech/autopilot`

---

## 2. 第一个标准 Pack

平台当前的第一套显式标准 pack 是：

```text
research-tech
```

它就是当前仓库现有的偏技术研究工作流，被正式包装成第一套标准领域包。

同时：

```text
default-knowledge
```

当前仍保留为默认兼容 pack，用来保证既有 CLI 和运行时默认值稳定。

这意味着：

- 媒体不是 core 的特例
- 媒体也不是 seed ontology
- 媒体、医疗等都应该作为独立 pack 来接入

这样 core 才能保持稳定。

---

## 3. Pack 的最小职责

一个可安装 pack 至少要提供：

1. manifest
2. pack entrypoint
3. object kind 定义
4. workflow profile 定义
5. schema / template / prompt 资源

推荐目录：

```text
openclaw-pack-<name>/
├── README.md
├── pyproject.toml
├── src/openclaw_pack_<name>/
│   ├── __init__.py
│   ├── plugin.py
│   ├── manifest.yaml
│   ├── schemas/
│   ├── templates/
│   ├── prompts/
│   ├── workflows.py
│   ├── discovery.py
│   ├── absorb.py
│   ├── refine.py
│   └── lint.py
└── tests/
```

---

## 4. Pack 生命周期

一个 pack 的接入流程应该是：

1. 开发者编写 pack
2. pack 暴露 manifest 和 Python entrypoint
3. core 通过 plugin loader 发现并加载它
4. 用户通过 `--pack` 和 `--profile` 选择运行

示例：

```bash
ovp-packs
ovp --pack research-tech --profile full
ovp-autopilot --pack research-tech --profile autopilot
ovp --pack default-knowledge --profile full
ovp-autopilot --pack default-knowledge --profile autopilot
ovp --pack media-editorial --profile daily-desk
```

当前 core 已支持两种发现方式：

- Python entry point 组：`openclaw_pipeline.packs`
- 显式 manifest 路径：环境变量 `OPENCLAW_PACK_MANIFESTS=/path/a.yaml:/path/b.yaml`

当前也可以直接通过：

```bash
ovp-packs --json
```

查看当前运行时可见的 builtin/external packs、角色、兼容基底和 profiles。

对于内置标准 pack 的运营验证，当前也已经有最小命令面：

```bash
ovp-doctor --pack research-tech --json
ovp-export --pack research-tech --target topic-overview --output-path /tmp/topic.md
ovp-export --pack research-tech --target orientation-brief --output-path /tmp/orientation.json
```

这些命令不是 Pack API 本身，但它们定义了 pack 在真实运行时应该具备的 operator surface：

- doctor / verify
- recipes
- exportable compiled artifacts
- inspectable artifact contracts
- inspectable assembly/access contracts
- inspectable governance/routing contracts

其中 `ovp-export` 当前已经开始消费 pack 声明：

- CLI target 先解析到 assembly recipe
- assembly recipe 再解析到 source contract（当前是 wiki view）
- compatibility pack 可以继承 recipe，但仍然使用自己声明的 view spec
- access contract 现在会同时暴露：
  - recipe provider
  - source contract provider

也就是说，compiled artifact export 不应该长期依赖 core 里的硬编码 target-to-view 表。

同一套 assembly contract 现在也开始进入 UI access layer：

- `/`
- `object/page`
- `overview/topic`
- `event/dossier`
- `truth/contradictions`
- `briefing/intelligence`

这些 payload 当前都会暴露 `assembly_contract`，shared shell 页面也会直接渲染 recipe provider、source contract、output mode。

从 `Phase 19` 开始，pack 还需要把 access contract 花在真正的 entry products 上，而不只是“能导出一个页面”：

- `/briefing` 现在应该被视为 orientation product，而不是 operator-only snapshot
- `/` workbench home 现在应该回答：
  - what changed recently
  - what is important right now
  - what deserves review
  - what the system recommends next
- object/topic/event/contradiction pages 现在应该暴露稳定的 `compiled_sections`
  - `current_state`
  - `why_it_matters`
  - `evidence_traceability`
  - `open_tensions`
  - `where_to_go_next`

`ovp-doctor` 现在也会展示同一条链：

- recipe provider
- source contract kind/name
- source contract provider

对第三方 pack 来说，推荐优先提供 entry point；manifest 适合开发期和未安装场景。

如果 pack 要支持 orientation-style entry products，推荐至少做到：

- 声明一个 `orientation_brief` assembly recipe
- 让 `ovp-export --target orientation-brief` 能导出编译后的 JSON 产物
- 让 shared shell 的 `/briefing` 和 `/` 使用同一套 contract 解释自己
- 让 compiled page payload 暴露稳定的 `compiled_sections` / `section_nav`

---

## 5. 重要边界

Pack 可以定义领域逻辑，但不能破坏 core 的硬边界。

### Pack 不能做的事

- 绕过 audit / pipeline logging
- 绕过 canonical identity framework
- 把 semantic retrieval 直接变成 canonical identity
- 直接把 `knowledge.db` 当成 source of truth
- 偷偷改 core runtime contract

### Pack 必须服从的事

- ID 必须 deterministic
- 写入必须经过 core 的可审计路径
- derived state 必须可重建
- workflow 的副作用必须可追踪
- abstain 必须是合法结果

---

## 6. Pack 开发顺序建议

不要一上来做全自动。

推荐顺序：

1. 定义对象模型
2. 定义 schema
3. 定义 workflow profiles
4. 定义 discovery / absorb / refine 规则
5. 定义 lint / evaluation
6. 最后再做 autopilot

这条顺序尤其适合媒体和医疗。

---

## 7. 文档组成

本目录当前包含：

- `README.md`
  面向 pack 作者的总览
- `manifest-and-hooks.md`
  pack manifest、hook、entrypoint 和运行时接口
- `dogfooding-with-media-pack.md`
  如何用媒体 pack 吃自己的狗粮

---

## 8. 设计原则

这套 Pack API 的目标有两件事：

1. 建立 OpenClaw 自己的领域扩展体系
2. 让我们自己的媒体项目先按这套体系落地，逼出真实接口

补充约束：

- `AssemblyRecipeSpec` 负责解释 access artifact 是怎么被编译出来的
- `GovernanceSpec` 负责解释 runtime/operator surface 为什么会出现某个 queue、signal、resolver path
- 共享 UI shell 里的 `/signals`、`/actions`、`/briefing` 现在会显式渲染 `Governance Contract`
- 具体的 recommended action / queued action 也会暴露它命中的 resolver rule 和 governance provider
- pack 不只是在声明“能做什么”，也在声明“这些运行时行为为什么存在”

也就是说：

> 这不是写给“未来某个抽象第三方”的空文档。
> 这是我们自己要先拿来做媒体 pack、再给别人用的开发文档。
