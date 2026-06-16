# OVP Console Visualization Architecture

## 现状

当前 console 是 Rust 预渲染的**纯静态 HTML**（无 JS），server 有 JSON API
但前端不消费。要实现图谱/溯源/实时监控，需要引入客户端渲染层。

## 设计原则

1. **3D-first** — 知识图谱用 Three.js/WebGL 3D 力导向，非 2D SVG
2. **渐进增强** — 现有静态页继续工作，可视化作为独立 SPA 页面叠加
3. **数据驱动** — 后端新增 API 供前端按需拉取，不在 HTML 里硬编码
4. **同步后端** — 保持 `tiny_http` 同步模型，SSE 通过 poll 模式实现
5. **构建隔离** — 前端用 Vite 打包为单一 bundle，Rust 端只 serve 静态产物

---

## 需求清单

| # | 功能 | 核心交互 |
|---|------|----------|
| 1 | Crystal 3D 知识图谱 | 节点=claims+units+sources，边=citations，3D 力导向 + 粒子边 |
| 2 | Pipeline 数据流瀑布图 | intake→reader→units→cards→crystal 流转 Sankey/waterfall |
| 3 | Claims 关系网络 | 按 theme/source 聚类，共享 source 的 claims 3D 聚团 |
| 4 | 实时 Pipeline 监控 | SSE 推送 daily run 进度（source by source） |
| 5 | Source→Units→Claims 溯源 | 点击 claim 展开 citation chain 直到原文 quote |
| 6 | 搜索/筛选 UI | 前端搜索框调用 /api/find 和 /api/search |

---

## 技术选型

### 核心方案：3d-force-graph

选用 [3d-force-graph](https://github.com/vasturiano/3d-force-graph)（6k stars），
同 Obsidian 3D Graph 插件底层一致。它是 Three.js + d3-force-3d 的高级封装。

**选择理由：**
- 开箱即用的 3D 力导向布局（无需手写 Three.js 场景管理）
- 内置粒子流动边（`linkDirectionalParticles`）
- 支持 DAG 模式（`dagMode: 'radialout'`）用于溯源树
- 节点自定义几何体（`nodeThreeObject`）
- 支持 5k+ 节点（OVP 数据量远小于此）
- 集成 orbit/trackball 相机控制
- 可直接访问底层 Three.js scene/renderer 做后处理

| 层 | 选择 | 理由 |
|----|------|------|
| 3D 图谱 | **3d-force-graph** | Three.js 高层封装，粒子边+DAG+自定义几何 |
| 后处理 | **Three.js UnrealBloomPass** | 通过 `graph.renderer()` 接入 EffectComposer |
| 2D 流程图 | **D3.js Sankey** | pipeline 阶段少（5 层），2D Sankey 天然适合 |
| 前端构建 | **Vite** | 打包 3d-force-graph + Three.js → 单一 ESM bundle |
| UI 层 | **vanilla TS + CSS** | 不引入 React，只用原生 DOM 做面板/搜索/筛选 |
| 搜索 | 前端 `<input>` + `fetch(/api/find)` | API 已存在 |
| SSE | `EventSource` + 后端 poll 模式 | tiny_http 不支持长连接，改为短 SSE burst |
| CSS | 暗色主题（深空黑底 + 发光节点） | 3D 图谱在暗底下效果最佳 |

**npm 依赖：**
- `3d-force-graph` — 核心 3D 图谱
- `three` — WebGL 渲染（3d-force-graph 的 peer dep）
- `d3-sankey` — 2D 瀑布图
- `d3-scale`, `d3-selection` — 辅助

**视觉特效清单（通过 3d-force-graph API 实现）：**
- 节点按 type 着色：claim=紫色发光球体, unit=蓝色, source=绿色
- 节点大小按 degree（被引用次数）缩放
- 边粒子流动（`linkDirectionalParticles: 4, linkDirectionalParticleSpeed: 0.005`）
- UnrealBloom 后处理（发光节点 + 边在暗底上格外突出）
- Hover 高亮连通子图（`onNodeHover` → 降低无关节点透明度）
- Click 聚焦（相机飞入目标节点 `graph.cameraPosition(...)`)
- Theme 聚类着色（同 theme 的 claims 共享色相）

### 构建流程

```
console-ui/              (新目录，独立于 Rust crates)
├── package.json
├── vite.config.ts
├── src/
│   ├── graph.ts         (3D 知识图谱页)
│   ├── flow.ts          (Sankey 瀑布)
│   ├── explore.ts       (溯源 + 搜索)
│   ├── monitor.ts       (SSE 实时)
│   └── shared/
│       ├── api.ts       (fetch wrapper)
│       ├── theme.ts     (暗色主题常量)
│       └── types.ts     (API response 类型)
├── index.html
├── graph.html
├── flow.html
├── explore.html
└── monitor.html

# 构建产物 → .ovp/console/ （Rust server serve 的目录）
vite build --outDir ../.ovp/console/
```

`scripts/build-console.sh`：
```bash
cd console-ui && npm ci && npx vite build --outDir ../.ovp/console/
```

Rust 的 `ovp-server` 照常 serve `.ovp/console/` 下的静态文件，无需改动。

---

## 后端新增 API

### `/api/graph` — Crystal 知识图谱数据

返回完整的 node/edge 图结构，供 3d-force-graph 渲染。

```json
{
  "nodes": [
    { "id": "claim:ck-abc123", "type": "claim", "label": "...", "theme": "..." },
    { "id": "unit:u-3-fa82b1c9", "type": "unit", "label": "...", "case_id": "..." },
    { "id": "source:sha256-...", "type": "source", "label": "...", "url": "..." }
  ],
  "edges": [
    { "source": "claim:ck-abc123", "target": "unit:u-3-fa82b1c9", "type": "cites" },
    { "source": "unit:u-3-fa82b1c9", "target": "source:sha256-...", "type": "extracted_from" }
  ]
}
```

**构建逻辑：**
1. 从 crystal `ledger.jsonl` 读 Active claims + citations
2. 从 index `PackRow` 反查 `source_sha256`
3. 从 pack 的 `units.accepted.json` 读 unit 明细（只取被引用的）
4. 组装 nodes + edges

### `/api/flow` — Pipeline 数据流统计

返回各阶段的流量统计，供 Sankey 渲染。

```json
{
  "stages": ["intake", "reader", "units", "cards", "crystal"],
  "flows": [
    { "from": "intake", "to": "reader", "value": 42, "label": "processed" },
    { "from": "intake", "to": "blocked", "value": 3, "label": "blocked" },
    { "from": "reader", "to": "units", "value": 185, "label": "accepted units" },
    { "from": "units", "to": "cards", "value": 92, "label": "cards kept" },
    { "from": "cards", "to": "crystal", "value": 28, "label": "durable claims" }
  ]
}
```

**构建逻辑：** 从 IndexModel 的 totals + packs + claims 聚合。

### `/api/claim/:id` — 单条 Claim 溯源详情

```json
{
  "claim_id": "ck-abc123",
  "claim": "...",
  "theme": "...",
  "strength": "strong",
  "citations": [
    {
      "unit_id": "u-3-fa82b1c9",
      "unit_text": "...",
      "quote": "exact text from source",
      "resolved_line": 42,
      "case_id": "some-article-2026-06-15",
      "source_title": "...",
      "source_url": "...",
      "source_sha256": "..."
    }
  ]
}
```

**构建逻辑：**
1. 从 `ledger.jsonl` 找到该 claim 的 `DurableRecord`
2. 对每条 citation：从对应 pack 的 `units.accepted.json` 读 unit 全文
3. 从 IndexModel 反查 source 元数据

### `/api/sse` — 实时进度推送（升级）

改为真实进度推送（daily run 期间）：

```
event: progress
data: {"source":"article.md","stage":"reader","status":"running","index":3,"total":10}

event: progress
data: {"source":"article.md","stage":"crystal","status":"done","units":8,"cards":4}

event: complete
data: {"run_id":"daily-2026-06-15","succeeded":8,"failed":2}
```

**实现方式：** daily command 写进度到 `.ovp/run-progress.json`（原子写），
SSE handler 轮询该文件（100ms interval），检测到变化就推送。
tiny_http 线程阻塞直到 run 结束或超时。

---

## 前端页面结构

```
console-ui/                       (独立前端项目，Vite 构建)
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html                    (导航入口)
├── graph.html                    (Crystal 3D 知识图谱)
├── flow.html                     (Pipeline 数据流)
├── explore.html                  (溯源导航 + 搜索)
├── monitor.html                  (实时监控)
└── src/
    ├── graph.ts                  (3d-force-graph + Bloom 后处理)
    ├── flow.ts                   (D3 Sankey)
    ├── explore.ts                (溯源交互)
    ├── monitor.ts                (SSE EventSource)
    └── shared/
        ├── api.ts                (fetch wrapper，type-safe)
        ├── theme.ts              (暗色主题：深空黑 #0a0a1a + 发光色)
        └── types.ts              (GraphData, FlowData, ClaimDetail 等)

# 构建产物目录（Rust server serve）
.ovp/console/
├── index.html          (Rust 生成的静态主页，不变)
├── ops.html            (Rust 生成的运维页，不变)
├── viz/                (Vite build 产物)
│   ├── index.html      (可视化导航)
│   ├── graph.html
│   ├── flow.html
│   ├── explore.html
│   ├── monitor.html
│   └── assets/
│       ├── graph-[hash].js
│       ├── flow-[hash].js
│       └── style-[hash].css
```

**构建命令：**
```bash
# 开发模式
cd console-ui && npm run dev   # Vite dev server, proxy /api → localhost:9990

# 生产构建
cd console-ui && npm run build  # → ../.ovp/console/viz/
```

Rust 的 `ovp-server` 在 serve `.ovp/console/` 时自动覆盖 `viz/` 子路径。
现有 `index.html` 顶部加一个 `[3D Graph]` 链接指向 `/viz/graph.html`。

---

## 页面设计

### graph.html — Crystal 3D 知识图谱

```
┌─────────────────────────────────────────────────────────────┐
│  [Theme ▼] [Type ▼] [Search: _________] [DAG/Force ⟳] [⚙]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│              (全屏 3D WebGL Canvas)                          │
│                                                             │
│     ● claim 紫色发光球体                                      │
│         ╲ 粒子流向 unit                                      │
│     ■ unit 蓝色球体                                          │
│         ╲ 粒子流向 source                                    │
│     ◆ source 绿色球体                                        │
│                                                             │
│     轨道相机：旋转/缩放/平移                                    │
│     UnrealBloom 发光后处理                                    │
│     深空黑背景                                                │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  [Detail Panel — 半透明叠加层，click 节点时展开]                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Claim: "LLMs benefit from structured prompts"       │    │
│  │ Theme: prompt-engineering │ Strength: strong         │    │
│  │ Citations:                                          │    │
│  │   → Unit u-3-fa82b1c9 "structured prompts improve" │    │
│  │     └─ Source: prompt-guide.md:42                   │    │
│  │   → Unit u-7-c92d1e03 "few-shot examples help"     │    │
│  │     └─ Source: research-notes.md:18                 │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**3d-force-graph 配置：**
```typescript
const graph = ForceGraph3D()(container)
  .graphData(data)
  .backgroundColor('#0a0a1a')
  .nodeAutoColorBy('type')
  .nodeVal(node => node.degree || 1)
  .nodeLabel(node => node.label)
  .nodeOpacity(0.9)
  .linkDirectionalParticles(4)
  .linkDirectionalParticleSpeed(0.005)
  .linkDirectionalParticleWidth(1.5)
  .linkOpacity(0.3)
  .linkWidth(0.5)
  .onNodeClick(node => showDetail(node))
  .onNodeHover(node => highlightNeighbors(node));

// UnrealBloom 后处理
const bloomPass = new UnrealBloomPass(
  new THREE.Vector2(window.innerWidth, window.innerHeight),
  1.5,  // strength
  0.4,  // radius
  0.85  // threshold
);
const composer = new EffectComposer(graph.renderer());
composer.addPass(new RenderPass(graph.scene(), graph.camera()));
composer.addPass(bloomPass);
```

**交互设计：**
- 轨道相机旋转/缩放/平移（trackball 模式）
- 节点按 type 着色（claim=`#a855f7` 紫, unit=`#3b82f6` 蓝, source=`#22c55e` 绿）
- 节点大小按 degree 缩放（被引用越多越大）
- 边上粒子流动方向 = 引用方向（claim → unit → source）
- Hover: 高亮节点 + 相邻连通分量，其余半透明
- Click: 相机飞入节点 + 展开 Detail Panel（调用 `/api/claim/:id`）
- 右上角 DAG/Force 切换：`dagMode('radialout')` vs 自由力导向
- Theme 筛选：只显示选中 theme 的 claims 子图
- UnrealBloom 让发光节点在深空黑背景下极具视觉冲击

### flow.html — Pipeline 数据流

```
┌─────────────────────────────────────────────────┐
│  Pipeline Data Flow   [Date: 2026-06-15 ▼]     │
├─────────────────────────────────────────────────┤
│                                                 │
│  Intake ─────42───→ Reader ───185──→ Units      │
│    │                                   │        │
│    ├──3──→ Blocked                     │        │
│    └──5──→ Needs-content        ───92──→ Cards  │
│                                          │      │
│                                   ───28──→ Crystal
│                                          │      │
│                                   ───12──→ Review
│                                                 │
├─────────────────────────────────────────────────┤
│  Totals: 42 sources │ 185 units │ 28 durable   │
└─────────────────────────────────────────────────┘
```

### explore.html — 溯源导航 + 搜索

```
┌─────────────────────────────────────────────────┐
│  [🔍 Search: ______________] [Kind ▼] [Status ▼]│
├───────────────────────┬─────────────────────────┤
│  Results List         │  Detail / Provenance    │
│  ─────────────────    │  ─────────────────────  │
│  ● claim: "LLMs..."  │  Claim: "LLMs can..."   │
│  ● claim: "Rust..."  │  ├─ Unit u-3 (quote)    │
│  ■ source: "art..."  │  │  └─ Source: art.md:42│
│  ■ source: "blog..." │  ├─ Unit u-7 (quote)    │
│                       │  │  └─ Source: blog:18  │
│                       │  └─ Strength: strong    │
└───────────────────────┴─────────────────────────┘
```

### monitor.html — 实时监控

```
┌─────────────────────────────────────────────────┐
│  Pipeline Monitor          [● LIVE] / [○ IDLE]  │
├─────────────────────────────────────────────────┤
│  Run: daily-2026-06-15   Progress: 7/10         │
│  ┌──────────────────────────────────────┐       │
│  │ ████████████████████░░░░░░░░ 70%     │       │
│  └──────────────────────────────────────┘       │
│                                                 │
│  Source          Stage      Status    Time      │
│  article-1.md   crystal    ✓ done    2.3s      │
│  article-2.md   reader     ✓ done    4.1s      │
│  article-3.md   units      ⟳ running 1.8s      │
│  article-4.md   intake     ◻ queued  —         │
│                                                 │
├─────────────────────────────────────────────────┤
│  Recent Events (SSE stream):                    │
│  09:15:03 article-3 → reader done (8 units)    │
│  09:15:05 article-3 → crystal running          │
└─────────────────────────────────────────────────┘
```

---

## 实现路线图

### Phase A（1 周）：前端脚手架 + 后端 API

- 初始化 `console-ui/` 项目（Vite + TypeScript）
- 安装 `3d-force-graph`, `three`, `d3-sankey`
- 后端实现 `/api/graph` 端点（从 ledger + packs 组装 nodes/edges）
- 后端实现 `/api/claim/:id` 端点
- 配置 Vite dev server proxy → `localhost:9990`（OVP server）
- 在现有 `index.html` 顶部加 `[3D Graph]` 导航链接

### Phase B（1-2 周）：3D 知识图谱核心

- `graph.ts`：3d-force-graph 初始化 + 数据加载
- 节点着色/大小逻辑（type → color, degree → size）
- 粒子边流动（citation 方向）
- UnrealBloom 后处理集成
- Hover 高亮 + Click 相机飞入 + Detail Panel
- Theme/Type 筛选器
- DAG 模式切换（`dagMode('radialout')`）

### Phase C（2-3 周）：溯源 + 数据流 + 搜索

- `explore.ts`：搜索框 → `/api/find` + 结果列表 + citation chain 详情
- `flow.ts`：后端实现 `/api/flow` + D3 Sankey 渲染
- `monitor.ts`：SSE 进度推送 + 实时 event stream 面板

### Phase D（3-4 周）：实时监控 + 打磨

- Daily command 写 `run-progress.json`
- SSE handler 从 poll stub 升级为文件 watch + 推送
- 响应式布局适配
- 生产构建 + `scripts/build-console.sh`
- Rust server 路由 `/viz/*` → `.ovp/console/viz/`

---

## 关键设计决策

1. **3d-force-graph 而非 D3 SVG** — 3D WebGL 力导向，带粒子边和 Bloom 后处理
2. **Vite 独立构建** — 前端 `console-ui/` 有自己的 `package.json`，产物部署到 `.ovp/console/viz/`
3. **不引入 React** — 3d-force-graph 是 vanilla JS API，不需要框架绑定
4. **API 路由在 ovp-server** — 新端点加入现有 `tiny_http` dispatch
5. **Graph 数据按需构建** — `/api/graph` 每次从 ledger+packs 实时组装，不持久化
6. **SSE 通过文件 poll** — 保持同步架构，daily 写进度文件，SSE handler 读
7. **Index 不扩展** — Unit 明细不进 IndexModel，按需从 pack JSON 读取（懒加载）
8. **暗色主题** — `#0a0a1a` 深空黑底 + 发光节点 + Bloom，视觉风格统一且冲击力强
