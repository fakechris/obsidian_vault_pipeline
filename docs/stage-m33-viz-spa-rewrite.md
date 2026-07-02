# Stage M33 — Console Viz Rewrite: React SPA + G6 v5 渐进式信息披露

**动机**：对比 Nowledge Mem 后确认旧可视化（Cytoscape/Cosmos/Three.js 三引擎
+ 手写 DOM）产品化差、美观差；差距不在渲染性能，而在**信息模型太薄 + 无渐进
披露 + 无组件体系**。详细架构见 `docs/design/console-visualization.md`。

## Stage 1: 密度感知后端 + SPA 骨架 + 星系图全景
**Goal**: 服务端信息密度管理（overview/neighborhood 模式、importance、社区
元数据）+ React/G6 脚手架渲染带包络的全景
**Success Criteria**: cargo test/clippy 绿；`/viz/graph` 渲染 overview
（10k fixture ≤2000 节点 <2s）；hull + 社区着色可见；SPA fallback 生效
**Status**: Complete（commits `95e843d6` 后端 / `f02bc007` 前端）

关键决策（实现中发现，偏离原计划的部分）：
- G6 5.1.1 `hull` 插件在 viewport 变换时抛错并破坏全图交互 → 换 `bubble-sets`
- 全景弃用 d3-force（毛球问题）→ 确定性星系图布局（圆盘打包 + 葵花籽）
- 社区检测从 union-find 连通分量升级为带权标签传播（hub source 会把连通分量
  并成单一巨块）；overview 截断后 related 边在幸存集合上重建（防断链）
- Explore/Flow/Monitor 在 Stage 1 一并移植 React（emptyOutDir 会清旧页，
  避免功能倒退）

## Stage 2: 渐进披露 — tier、tooltip、DetailPanel、focus
**Goal**: zoom→标签 LOD、悬停卡、点击→邻域展开、引用链面板、深链
**Success Criteria**: 全景 ≤30 标签；zoom 时标签渐现（屏幕密度恒定）；双击
claim <500ms 展开邻域；DetailPanel 带原文 quote；`?focus=` 深链可分享
**Status**: Complete（commit `73629815`）

- 社区标签为屏幕空间 React overlay（世界坐标 canvas 文字无法同时服务
  0.08 与 0.9 zoom），贪心防碰撞 + 圆盘可见性门槛 + 点击飞行
- focus 模式 = 替换数据为邻域子图（非就地 dim——更快、匹配 API 语义），
  返回全景走缓存瞬时恢复

## Stage 3: 搜索/过滤场景 + Explore
**Goal**: 命中子图搜索、主题过滤、Explore 页结构化检索
**Success Criteria**: 搜索收敛为紧凑子图（hits amber 环/context 淡化）；
清除恢复全景；主题过滤走服务端；explore→graph 深链聚焦
**Status**: Complete（commit `fbceaf02`）

- `/api/search?subgraph=1` 直接在 ledger 上匹配——index 的 `/api/find` 命中
  是展示字符串无结构化 id（旧 explore 页因此一直是坏的）
- `/api/themes` 主题直方图；ExplorePage 对 index 缺失优雅降级

## Stage 4: 清理 + 文档
**Goal**: 删除全部遗留代码/依赖/接口，bundle 审计，重写文档
**Success Criteria**: console-ui 内 grep cytoscape/cosmos/three 为空；
`mode=full` 与 `/api/sse` 删除；graph chunk <1.2MB gz、非图路由 <200KB gz
**Status**: Complete

- 删除：5 个旧 HTML 页、7 个旧 TS 文件、shared/、global.css、graph.css、
  `mode=full`、`/api/sse`、cytoscape/cosmos/three/d3-selection/d3-scale 依赖
- Bundle：graph chunk 419KB gz ✓；index 85KB gz + 页面 chunk 各 <5KB ✓

## 已知后续（非本阶段范围）
- 静态 console（`crates/ovp-console`）的 Attention feed 可加
  `/viz/graph?focus=claim:<key>` 深链入口
- viz 构建产物进真实 vault 目前靠手动复制到 `<vault>/.ovp/console/viz/`；
  可考虑 `ovp-next console --with-viz` 或 serve 时兜底读 repo 产物
- 真实 vault 语料还小（M32 全量跑完后再看社区/importance 分布是否需要调权）
- G6 程序化 `zoomTo` 在 bubble-sets 挂载时偶发 landmark 报错（用户滚轮/拖拽
  不受影响）；升级 G6 时复查
