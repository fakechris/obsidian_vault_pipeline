# Pack API v1

面向第三方开发者的 Domain Pack 开发文档。

当前状态：

- 这不是纯设计稿了，core 已经实现了最小 pack runtime
- 内置 `default-knowledge` 已经按 pack 运行
- `ovp` / `ovp-autopilot` 已经支持 `--pack` / `--profile`
- core 已支持 entry point 和 manifest 两种 pack 发现路径

这套 API 的目标不是让外部开发者“改 core”，而是让他们在 **不破坏 core 运行时契约** 的前提下，开发自己的领域包：

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
- schema
- discovery 规则
- absorb / refine 规则
- lint 规则
- prompts / templates
- workflow profiles

### Workflow Profile 负责什么

Profile 是某个 pack 下的一条可执行 DAG。

例如：

- `default-knowledge/full`
- `default-knowledge/autopilot`
- `media-editorial/daily-desk`
- `media-editorial/weibo-fastlane`

当前 core 已落地的是：

- `default-knowledge/full`
- `default-knowledge/autopilot`

---

## 2. 第一个标准 Pack

平台的第一个标准 pack 是：

```text
default-knowledge
```

它就是当前仓库现有的偏技术/知识管理工作流，被正式包装成默认领域包。

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
ovp --pack default-knowledge --profile full
ovp --pack media-editorial --profile daily-desk
ovp-autopilot --pack default-knowledge --profile autopilot
```

当前 core 已支持两种发现方式：

- Python entry point 组：`openclaw_pipeline.packs`
- 显式 manifest 路径：环境变量 `OPENCLAW_PACK_MANIFESTS=/path/a.yaml:/path/b.yaml`

对第三方 pack 来说，推荐优先提供 entry point；manifest 适合开发期和未安装场景。

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

也就是说：

> 这不是写给“未来某个抽象第三方”的空文档。
> 这是我们自己要先拿来做媒体 pack、再给别人用的开发文档。
