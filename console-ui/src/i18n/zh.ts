import type { en } from './en';

/** 简体中文 UI 文案 — 与 en.ts 同键；缺键会编译报错。 */
export const zh: Record<keyof typeof en, string> = {
  // nav
  'nav.today': '今天',
  'nav.library': '资料',
  'nav.search': '搜索',
  'nav.knowledge': '知识',
  'nav.ask': '对话',
  'nav.system': '系统',

  // status light
  'status.ok': '正常',
  'status.attention': '需处理',
  'status.failed': '最近运行失败',

  // run-liveness banner (fixed top strip, every page)
  'banner.none': '尚无运行记录',
  'banner.completed': '最近运行：已完成 {ago}',
  'banner.completedCounts': '最近运行：已完成 {ago} · 阅读 {read} · 队列 {queued}',
  'banner.running': '运行进行中 · 开始于 {ago}',
  'banner.stale': '最近运行：{ago} —— 每日流程可能已停滞',
  'banner.failed': '最近运行：失败 {ago}{error}',
  'banner.aborted': '最近运行：中断 {ago}{error}',
  'banner.agoJustNow': '刚刚',
  'banner.agoMinutes': '{n} 分钟前',
  'banner.agoHours': '{n} 小时前',
  'banner.agoDays': '{n} 天前',
  'banner.viewSystem': '查看系统状态',

  // shared
  'common.loading': '加载中…',
  'common.error': '无法加载索引模型——服务是否已连接 vault？',
  'common.whatIsThisPage': '这是什么页？',
  'common.day': '试用第',

  // 数据新鲜度标签（P1）：“截至 <时刻> · N 分钟前”。凡展示计数的界面都标注
  // 构建时刻，陈旧数字不再伪装成最新。
  'age.asOf': '截至 {instant}',
  'age.now': '刚刚',
  'age.minutes': '{n} 分钟前',
  'age.hours': '{n} 小时前',
  'age.days': '{n} 天前',
  'age.unknown': '时间未知',
  'age.stamp': '截至 {instant} · {rel}',

  // concept tooltips
  'concept.durableTip': '已验证：每条引文均逐字核对过原文',
  'concept.caveatedTip': '未验证：有价值但证据未完全过关，需带着怀疑使用',
  'concept.claimTip': '主张：跨源结论——durable（已验证）或 caveated（未验证）',
  'concept.cardTip': '卡片：可读的提炼——每句话都可追溯到接地单元',
  'concept.unitTip': '单元：带行号的逐字摘录——证据本身',

  // source statuses
  'sourceStatus.processed': '已处理',
  'sourceStatus.queued': '待读',
  'sourceStatus.blocked': '阻塞',
  'sourceStatus.needs_content': '缺内容',
  'sourceStatus.failed': '失败',
  'sourceStatus.unparseable': '无法解析',
  'sourceStatus.duplicate': '重复',

  // today page
  'today.title': '今天',
  'today.help':
    'OVP2 每天捕获你的剪藏与书签，读成有据记忆，并结晶跨源知识。本页展示今天的变化。',
  'today.captured': '进来',
  'today.read': '读完',
  'today.claims': '结晶',
  'today.attention': '待处理',
  'today.pinboard': '书签',
  'today.unitsCards': '{units} 单元 · {cards} 卡片',
  'today.durableCaveated': '持久 {durable} · 存疑 {caveated}',
  'today.blockedNeeds': '阻塞 {blocked} · 缺内容 {needs}',
  'today.attentionTitle': '需要你',
  'today.whyItMatters': '为什么',
  'today.whyBlocked':
    '该源已捕获但没有有据记忆——重跑前不会出现在搜索、卡片与结晶中。',
  'today.whyNeedsContent':
    '这条捕获内容太薄无法阅读——补充正文后才能进入有据记忆。',
  'today.attentionAction': '打开资料详情',
  'today.claimsSample': '来自结晶库',
  'today.claimsSampleNote':
    'durable 优先的样本——结晶账本尚未记录日期，暂无法按日归因。',
  'today.claimSources': '来源',
  'today.strength': '强度',
  'today.readToday': '今日读完',
  'today.readEmpty': '今天还没有读完的内容——日常运行尚未产出新的阅读包。',
  'today.capturedEmpty': '今天没有捕获运行',
  'today.timeline': '时间线',
  'today.timelineRead': '读完 {n}',
  'today.timelineCaptured': '进来 {n}',
  'today.timelineAll': '→ 系统：查看全部运行',
  'today.noRunsToday': '今天还没有运行记录——日常运行完成前统计为 0。',

  // library page
  'library.title': '资料',
  'library.help':
    '你捕获的全部资产：剪藏、Pinboard 书签与手动捕获。按集合、月份和状态筛选；点击行进入详情。',
  'library.collections': '集合',
  'library.all': '全部',
  'library.clippings': '剪藏',
  'library.pinboard': '书签',
  'library.capture': '捕获',
  'library.byMonth': '按月',
  'library.statusAll': '全部',
  'library.empty': '当前筛选下没有匹配的源。',
  'library.noDate': '无日期',

  // source detail
  'source.title': '资料',
  'source.url': '链接',
  'source.date': '日期',
  'source.origin': '来源',
  'source.location': '位置',
  'source.lastRun': '最近运行',
  'source.failCount': '失败次数',
  'source.lastReason': '最近错误',
  'source.notFound': '索引中没有这个 id 对应的源。',
  'source.backToLibrary': '资料',
  'source.loadError': '无法加载资料详情——服务是否在运行？',
  'source.tabMemory': '记忆',
  'source.tabMemoryCounts': '{cards} 卡片 · {units} 单元',
  'source.tabSource': '原文',
  'source.cardsTitle': '卡片',
  'source.cardsHint': '本源的可读提炼——每句话都可追溯到下方的接地单元。',
  'source.groundedUnits': '接地单元',
  'source.unitsHint': '带行号的逐字摘录——证据本身。',
  'source.unitNoLine': '无行号锚点',
  'source.noMemory': '还没有记忆——该源的阅读包中没有卡片或接地单元。',
  'source.evidenceMissing':
    '证据索引尚未构建——对该 vault 运行 `ovp2 index` 以加载卡片与单元。',
  'source.docEmpty': '磁盘上没有该源的 markdown 文件。',
  'source.docError': '无法读取原文文件：{error}',
  'source.docTruncated': '预览在 200 KB 处截断——完整内容请在 vault 中打开原文件。',
  'source.neighborhood': '关联图谱 · 邻域',
  'source.neighborhoodCaption':
    '本源 → 它的记忆卡片 → 引用它的主张 → 兄弟源。单击节点看摘要，双击打开详情。',
  'source.citingClaims': '结晶引用',
  'source.citingEmpty': '暂无结晶主张引用本源。',
  'source.citingEmptyHint': '→ 知识：了解结晶如何产生',

  // knowledge graph component
  'graph.loading': '图谱加载中…',
  'graph.error': '无法加载图谱。',
  'graph.empty': '暂无邻域——还没有内容引用本源。',
  'graph.emptyGlobal': '结晶库还没有主张——先运行结晶流程。',
  'graph.emptyTheme': '还没有主张属于这个主题。',
  'graph.fullscreen': '全屏',
  'graph.exitFullscreen': '关闭',
  'graph.truncated': '已截断——仅显示最强的主张。',
  'graph.kindClaim': '主张',
  'graph.kindSource': '源',
  'graph.kindUnit': '单元',
  'graph.kindCard': '卡片',
  'graph.openHint': '双击打开详情。',
  'graph.noPage': '旧源——该 vault 中没有对应的详情页。',
  'graph.cardHint': '这是本源的记忆——完整卡片在"记忆"标签页。',

  // knowledge home
  'knowledge.title': '知识',
  'knowledge.help':
    '知识库当前"相信"的内容，按主题分组。durable（持久）主张通过了全部证据门；caveated（存疑）主张带有已知弱点，等待复核。',
  'knowledge.helpLayers':
    '每条主张由三层支撑：原文（markdown 源文件）、记忆（卡片与带行号锚点的引文单元）、结晶（引用这些单元的跨源主张）。点进任意主张即可核查全链。',
  'knowledge.helpLadder':
    '一句话的阶梯：原文 → unit（可验证摘录）→ card（可读理解）→ claim（跨源结论，durable/caveated 二态）。',
  'knowledge.viewList': '列表',
  'knowledge.viewGraph': '图谱',
  'knowledge.empty': '结晶库还没有主张——对源运行结晶流程以构建知识层。',
  'knowledge.untitledTheme': '（无主题）',
  'knowledge.claimCount': '{n} 条主张',
  'knowledge.ratioLine': '持久 {durable} · 存疑 {caveated}',
  'knowledge.graphCaption': '全部主张，按社区着色。单击节点看摘要，双击进入其主题。',
  'knowledge.unknownClaim': '没有活跃主张 "{id}"——它可能已被取代或撤回。',

  // theme naming
  'theme.unclassified': '未分类',
  'theme.unclassifiedNote':
    '未命中任何关键词分组的来源——自动聚类是计划中的改进（M34）。',

  // theme detail
  'theme.counts': '持久 {durable} · 存疑 {caveated}',
  'theme.citedSources': '来源：',
  'theme.legacySource': '旧源——该 vault 中没有对应的详情页。',
  'theme.strength': '强度',
  'theme.empty': '没有活跃主张属于这个主题。',
  'theme.backToKnowledge': '全部主题',
  'theme.graph': '主题图谱',
  'theme.graphCaption': '本主题的主张与其引用的源。单击节点看摘要，双击打开详情。',

  // search page + ⌘K overlay
  'search.title': '搜索',
  'search.help':
    '一个输入框搜全部实体：源、阅读包、结晶主张与主题。结果直达对应详情——在任何页面按 ⌘K（Ctrl+K）即可随处唤起搜索。',
  'search.placeholder': '搜索源、主张、主题…',
  'search.keys': '↑↓ 选择 · Enter 打开 · Esc 关闭',
  'search.error': '搜索失败——服务是否已连接 vault？',
  'search.empty': '没有匹配结果。试试更短的词——搜索按子串匹配。',
  'search.noPage': '该条目在此 vault 中没有详情页。',
  'search.open': '搜索（⌘K）',
  'search.group.claim': '主张',
  'search.group.source': '源',
  'search.group.pack': '阅读包',
  'search.group.theme': '主题',

  // ask page
  'ask.title': '对话',
  'ask.help':
    '用自然语言提问；回答只依据你的证据索引——结晶主张、阅读卡片与带引文的单元——并附可核查的编号引用。每次回答都会保存到左侧历史。未通过校验的引用会被标注。',
  'ask.historyTitle': '历史会话',
  'ask.historyEmpty': '还没有保存的会话——每次回答都会自动保存到这里。',
  'ask.savedChat': '已保存会话',
  'ask.closeChat': '返回对话',
  'ask.chatLoadError': '无法加载该会话——服务是否在运行？',
  'ask.citationsTitle': '引用',
  'ask.citationsEmpty':
    '最新回答的引用会显示在这里——悬停回答中的 [1] 标记可高亮对应证据。',
  'ask.unverified': '未核实',
  'ask.openCitation': '打开',
  'ask.noLink': '该条目在此 vault 中没有详情页。',
  'ask.verifiedLine': '引用校验 {verified}/{cited}',
  'ask.contextHits': '{n} 条上下文',
  'ask.placeholder': '向你的知识库提问…',
  'ask.hint': 'Enter 发送 · Shift+Enter 换行',
  'ask.send': '发送',
  'ask.pending': '思考中…',
  'ask.emptyTitle': '向你的知识库提问',
  'ask.emptyBody':
    '回答只来自你读过并结晶的内容——不引入外部知识，不编造引用。试试这些问题：',
  'ask.example1': '我的知识库对 agent 记忆有什么看法？',
  'ask.example2': '哪些来源讨论了上下文工程？它们的主张是什么？',
  'ask.example3': '关于检索质量，最有力的证据是什么？',
  'ask.errNotConfigured':
    '服务端未配置 LLM——请设置 ANTHROPIC_API_KEY 并重启 `ovp2 serve`（构建时加 --features anthropic）。',
  'ask.errIndexUnavailable':
    '索引不可用——请对该 vault 运行 `ovp2 index`，并确认服务启动时的 --vault-root 指向正确。',
  'ask.errBusy': '对话繁忙——同时进行的回答已达上限，请稍候再试。',
  'ask.errTimeout':
    '在时限内没有等到回答。请求并未被取消——如果模型最终完成，保存的会话仍会出现在历史中。',
  'ask.errGeneric': '提问失败——服务是否已连接 vault？',

  // system page (B5)
  'system.help':
    '机房页：全部运行记录、需要你处理的源、管线管理视图、三层模型说明，以及服务端配置（只读）。',
  'system.runs': '运行记录',
  'system.runsEmpty': '还没有运行记录——对该 vault 运行 `ovp2 daily`。',
  'system.runDate': '日期',
  'system.runOk': '成功',
  'system.runFailed': '失败',
  'system.runBlocked': '阻塞',
  'system.runIngested': '摄入',
  'system.runReport': '报告',
  'system.attentionTitle': '需要你',
  'system.attentionEmpty': '没有需要处理的源——无阻塞、无缺内容。',
  'system.doctorHint': '更深入的诊断，在终端运行：',
  'system.surfaces': '管线入口',
  'system.surfacesNote':
    '面向管理的水管视图——排查卡点时有用，不属于日常产品面。',
  'system.flowLink': '流程 Flow（管线桑基图）',
  'system.monitorLink': '监控 Monitor（运行流水）',
  'system.adminPagesNote':
    '旧版生成的控制台页面（该 vault 生成过控制台时可用）：',
  'system.concepts': '概念说明',
  'system.conceptLayers':
    '三层内容，处处互链：原文 = 你捕获的 markdown 源文件；记忆 = 它的阅读包——可读卡片 + 锚定到原文行号的引文单元；结晶 = 引用这些单元的跨源主张。',
  'system.conceptDurable':
    'durable（持久）主张通过了全部证据门；caveated（存疑）主张带有已知弱点、等待复核——只会被标注，不会被隐藏。',
  'system.conceptGate':
    '"门"（gate）是写入结晶账本前的机械校验：每条引用必须对应真实的引文单元，主张强度会被评分——人的裁决也要过门，从不绕过。',
  'system.settings': '设置',
  'system.settingsReadonly':
    'v1 只读——修改在 CLI 完成，这里展示服务端当前运行的配置。',
  'system.settingsError': '无法加载设置——服务是否在运行？',
  'system.vaultRoot': 'vault 路径',
  'system.schema': '索引 schema',
  'system.indexDate': '索引日期',
  'system.builtAt': '构建时刻',
  'system.runId': '运行 id',
  'system.counts': '统计',
  'system.countsLine': '{sources} 源 · {packs} 阅读包 · {claims} 主张',
  'system.noIndex': '索引未构建——运行 `ovp2 index`',
  'system.llm': 'LLM（对话）',
  'system.llmOn': '已配置——POST /api/ask 可用',
  'system.llmOff':
    '未配置——对话返回 503。设置 ANTHROPIC_API_KEY 后重启 `ovp2 serve`（构建时加 --features anthropic）。',
  'system.askTimeout': '对话超时',
  'system.askTimeoutValue': '每问 {secs} 秒 · 并发上限 {cap}',
  'system.version': '服务端版本',
  'system.togglesNote':
    '主题与语言在顶栏切换（LIGHT/DARK · EN/中）——按浏览器持久化，每页可用。',
};
