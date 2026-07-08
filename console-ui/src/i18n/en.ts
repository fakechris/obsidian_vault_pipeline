/** English UI strings — the default locale. Keys are shared with zh.ts;
 * the Dict type in index.tsx is derived from this file, so a key missing
 * in zh.ts is a compile error. */
export const en = {
  // nav
  'nav.today': 'Today',
  'nav.library': 'Library',
  'nav.search': 'Search',
  'nav.knowledge': 'Knowledge',
  'nav.ask': 'Ask',
  'nav.system': 'System',

  // status light
  'status.ok': 'ok',
  'status.attention': 'attention',
  'status.failed': 'last run failed',

  // shared
  'common.loading': 'Loading…',
  'common.error': 'Could not load the index model — is the server running against a vault?',
  'common.whatIsThisPage': 'What is this page?',
  'common.day': 'dogfood day',

  // source statuses
  'sourceStatus.processed': 'processed',
  'sourceStatus.queued': 'queued',
  'sourceStatus.blocked': 'blocked',
  'sourceStatus.needs_content': 'needs content',
  'sourceStatus.failed': 'failed',
  'sourceStatus.unparseable': 'unparseable',
  'sourceStatus.duplicate': 'duplicate',

  // today page
  'today.title': 'Today',
  'today.help':
    'Every day OVP2 captures your clippings and bookmarks, reads them into grounded memory, and crystallizes cross-source knowledge. This page shows what changed today.',
  'today.captured': 'Captured',
  'today.read': 'Read',
  'today.claims': 'Claims',
  'today.attention': 'Attention',
  'today.pinboard': 'pinboard',
  'today.unitsCards': '{units} units · {cards} cards',
  'today.durableCaveated': 'durable {durable} · caveated {caveated}',
  'today.blockedNeeds': 'blocked {blocked} · needs-content {needs}',
  'today.attentionTitle': 'Attention',
  'today.whyItMatters': 'Why it matters',
  'today.whyBlocked':
    'this source is captured but has no grounded memory — it stays invisible to search, cards, and crystal claims until reprocessed.',
  'today.whyNeedsContent':
    'this capture is too thin to read — enrich it with real content so it can enter grounded memory.',
  'today.attentionAction': 'Open source detail',
  'today.recentClaims': 'Recent claims',
  'today.recentClaimsNote':
    'Latest crystallized claims (per-day attribution lands in B2).',
  'today.claimSources': 'Sources',
  'today.strength': 'strength',
  'today.readToday': 'Read today',
  'today.readEmpty': 'Nothing read yet today — the daily run has not produced new packs.',
  'today.capturedEmpty': 'no capture runs today',
  'today.timeline': 'Timeline',
  'today.timelineRead': 'read {n}',
  'today.timelineCaptured': 'captured {n}',
  'today.timelineAll': '→ System: all runs',
  'today.noRunsToday':
    'No runs recorded for today yet — stats show 0 until the daily run lands.',

  // library page
  'library.title': 'Library',
  'library.help':
    'Everything you have captured: clippings, pinboard bookmarks, and manual captures. Filter by collection, month, and status; click a row for detail.',
  'library.collections': 'Collections',
  'library.all': 'All',
  'library.clippings': 'Clippings',
  'library.pinboard': 'Pinboard',
  'library.capture': 'Capture',
  'library.byMonth': 'By month',
  'library.statusAll': 'All',
  'library.empty': 'No sources match the current filters.',
  'library.noDate': 'no date',

  // source detail (B1 stub)
  'source.title': 'Source',
  'source.url': 'url',
  'source.date': 'date',
  'source.origin': 'origin',
  'source.location': 'location',
  'source.lastRun': 'last run',
  'source.failCount': 'failures',
  'source.lastReason': 'last error',
  'source.notFound': 'No source with this id in the index.',
  'source.backToLibrary': 'Library',
  'source.b2Empty': 'Memory & source view coming in B2.',
  'source.b2EmptyDetail':
    'The three-layer drill-down (memory cards, grounded units, original markdown, neighborhood graph) ships in phase B2.',

  // placeholders
  'placeholder.search': 'Search across sources, cards, units, claims and themes lands in B3.',
  'placeholder.knowledge': 'Theme walls, claim detail and the scoped knowledge graph land in B3.',
  'placeholder.knowledgeInterim': 'Interim graph view',
  'placeholder.ask': 'Cited answers over your knowledge base land in B4.',
  'placeholder.system': 'Runs, flow, doctor and settings land in B5.',
} as const;
