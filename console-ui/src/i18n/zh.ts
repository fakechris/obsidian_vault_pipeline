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

  // shared
  'common.loading': '加载中…',
  'common.error': '无法加载索引模型——服务是否已连接 vault？',
  'common.whatIsThisPage': '这是什么页？',
  'common.day': '试用第',

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
  'today.recentClaims': '最新结晶',
  'today.recentClaimsNote': '最近结晶的主张（按日归因将在 B2 落地）。',
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

  // source detail (B1 stub)
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
  'source.b2Empty': '记忆与原文视图将在 B2 上线。',
  'source.b2EmptyDetail':
    '三层钻取（记忆卡片、接地单元、原文 markdown、邻域图谱）在 B2 期交付。',

  // placeholders
  'placeholder.search': '跨源/卡片/单元/主张/主题的搜索将在 B3 上线。',
  'placeholder.knowledge': '主题墙、主张详情与带作用域的知识图谱将在 B3 上线。',
  'placeholder.knowledgeInterim': '临时图谱视图',
  'placeholder.ask': '带引用的问答将在 B4 上线。',
  'placeholder.system': '运行记录、流程、诊断与设置将在 B5 上线。',
};
