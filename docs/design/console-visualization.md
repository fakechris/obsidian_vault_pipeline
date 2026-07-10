# OVP Console Visualization Architecture (M33)

> Rewritten in M33. The previous 3D-first / multi-engine design (Three.js +
> Cytoscape + Cosmos, M31–M32) is retired — see git history of this file.

## 核心理念：信息密度管理，不是渲染力

对比 Nowledge Mem 0.9.1（逆向其构建产物）得出的关键结论：好的知识图谱前端
不是"画得多"，而是"画得少、每个节点信息给得多、不同缩放/场景披露不同内容"。
M33 把信息密度管理放进**服务端**（`/api/graph` 不再全量倾倒），前端单引擎
（AntV G6 v5 canvas）+ React 组件体系负责披露层级。

## 技术栈

| 层 | 选型 | 说明 |
|---|------|------|
| 图引擎 | `@antv/g6` v5 (canvas) | 与 Nowledge 同款；d3-force 布局内置；bubble-sets 社区包络（注意：5.1.1 的 `hull` 插件在 viewport 变换时抛错且 destroy 路径损坏，会静默杀死 zoom/drag——用 bubble-sets） |
| UI | React 19 + Tailwind v4 + zustand | 单 SPA，react-router basename `/viz`，graph 路由 lazy-load（G6 ~419KB gz） |
| 后端 | `ovp-server`（tiny_http 同步） | `graph.rs` 负责图装配；静态 console（`ovp-console` crate）不变 |
| 构建 | Vite 6 → `.ovp/console/viz/` | 服务端对无扩展名的 `/viz/*` 路径回落 `viz/index.html`（SPA fallback） |

## 信息密度层级（不同密度展示不同内容）

| 层 | 触发 | 内容 |
|---|------|------|
| **Tier 0 全景** | 初始加载 | 仅 claims（服务端 cap top-2000 by importance）；**星系图布局**（贪心圆盘打包 + 葵花籽排布，确定性、秒开）；bubble-sets 包络；屏幕空间 React 社区标签（防碰撞、点击飞行）；仅社区内部边 |
| **Tier 1 缩放** | zoom 变化 | 标签预算 ∝ zoom²（30→500，屏幕密度恒定），世界坐标字号折算 ~12px 屏幕恒定；社区标签淡出、节点标签接管 |
| **Hover** | 悬停 600ms | React tooltip：类型/强度徽章、importance/provenance 进度条、theme/degree/社区、URL |
| **Tier 2 聚焦** | 双击 claim / DetailPanel"聚焦" / `?focus=` 深链 | `mode=neighborhood` 2-hop 子图（units/sources 展开），d3-force 有机布局，按类型着色，焦点 amber 环；DetailPanel 完整引用链 claim→quote→unit 全文→source 链接 |
| **搜索场景** | 顶栏搜索 | `subgraph=1` 命中子图（≤40 hits amber 环 + ≤80 一跳关联 45% 透明度），紧凑 d3-force 参数（Nowledge 派生） |
| **主题过滤** | 下拉 | `/api/themes` 直方图 → 服务端过滤的 overview |

### 为什么全景不用力导向

2000 个跨社区连边的节点在 d3-force 下必然塌缩成毛球（试过：就地更新标签传播
也会被 hub source 连通成单一巨型社区）。全景用确定性布局（社区圆盘 + 重要度
从圆心向外的葵花籽排布），力导向只给 ≤300 节点的 focus/search 子图。

## 服务端 API（`crates/ovp-server`）

- `GET /api/graph?mode=overview&limit=2000&theme=<t>` —— claims-only，
  importance 排序截断，related 边**在幸存集合上重建**（直接过滤会在被裁剪的
  claim 处断链），`communities[]` 元数据（带权标签传播聚类 + dominant-theme
  标签），`total_nodes`/`truncated`。
- `GET /api/graph?mode=neighborhood&focus=<id>&hops≤2` —— BFS 邻域，
  cap 300（分层按 importance 取）。
- `GET /api/search?q=…&subgraph=1` —— 直接在 ledger 上匹配（index 的
  `/api/find` 命中是展示字符串、无结构化 id），hit 标记 + 一跳关联。
- `GET /api/themes` —— 主题直方图。
- `importance = 0.45·norm(ln(1+related_degree)) + 0.35·provenance_score
  + 0.20·strength_weight`（Supported 1.0 / OverSynthesized 0.5 /
  Overreach 0.4 / OpinionAsFact 0.3）。
- 社区检测 = **同步更新、黏性并列的带权标签传播**（连通分量会被 hub source
  并成一个巨块；就地更新会让单个标签一趟横扫整条链）。
- `/api/sse` 已删除（tiny_http 顺序处理无法流式；monitor 轮询 `/api/model`）。

## 前端结构（`console-ui/src`）

```
main.tsx / App.tsx            路由 shell（/graph /explore /flow /monitor）
store/graphStore.ts           zustand：viewMode(overview|focus|search)、selection、
                              hover、detail、transformTick；overview 响应缓存
graph/GraphCanvas.tsx         唯一接触 @antv/g6 的文件；事件→store
graph/controller.ts           模块级 Graph 句柄（worldToScreen/flyToNodes）——
                              Graph 实例永不进 store
graph/g6/{config,density,hulls}.ts  样式/布局预设、标签 LOD、bubble-sets
graph/{CommunityLabels,NodeTooltip,DetailPanel,SearchBar,Legend}.tsx
pages/{ExplorePage,FlowPage,MonitorPage}.tsx   flow=d3-sankey 纯布局+React SVG
```

## 验证

- 规模基准：`cargo run -p ovp-server --example gen_scale_vault --release --
  --claims 10000 --out /tmp/ovp-scale-vault`，然后
  `ovp-next serve --vault-root /tmp/ovp-scale-vault`。
  参考值：overview 2000 节点/40 社区 ~140ms；neighborhood ~110ms。
- 浏览器验证走 agent-browser；注意 headless Playwright 的鼠标定位不可靠，
  交互验证用合成 PointerEvent（`pointerType:'mouse'`）直接派发到
  `canvas[1]`（主交互层）。调试句柄：`window.__ovpGraph`。
