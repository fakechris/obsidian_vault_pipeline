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
  'today.claimsSample': 'From the crystal store',
  'today.claimsSampleNote':
    'A durable-first sample — the crystal ledger records no dates, so per-day attribution is not derivable yet.',
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

  // source detail
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
  'source.loadError': 'Could not load the source detail — is the server running?',
  'source.tabMemory': 'Memory',
  'source.tabMemoryCounts': '{cards} cards · {units} units',
  'source.tabSource': 'Source',
  'source.groundedUnits': 'Grounded units',
  'source.unitNoLine': 'no line anchor',
  'source.noMemory':
    'No memory yet — this source has no cards or grounded units in its reader pack.',
  'source.evidenceMissing':
    'Evidence index not built — run `ovp2 index` against this vault to load cards and units.',
  'source.docEmpty': 'No markdown file on disk for this source.',
  'source.docError': 'Could not read the source file: {error}',
  'source.docTruncated':
    'Preview truncated at 200 KB — open the file in the vault for the full text.',
  'source.neighborhood': 'Neighborhood',
  'source.neighborhoodCaption':
    'This source → citing claims → sibling sources. Click a node for a summary, double-click to open it.',
  'source.citingClaims': 'Citing claims',
  'source.citingEmpty': 'No crystal claims cite this source yet.',
  'source.citingEmptyHint': '→ Knowledge: how claims crystallize',

  // knowledge graph component
  'graph.loading': 'Loading graph…',
  'graph.error': 'Could not load the graph.',
  'graph.empty': 'No neighborhood yet — nothing cites this source.',
  'graph.emptyGlobal': 'No claims in the crystal store yet — run crystallization first.',
  'graph.emptyTheme': 'No claims carry this theme yet.',
  'graph.fullscreen': 'EXPAND',
  'graph.exitFullscreen': 'CLOSE',
  'graph.truncated': 'Truncated — showing the strongest claims.',
  'graph.kindClaim': 'claim',
  'graph.kindSource': 'source',
  'graph.kindUnit': 'unit',
  'graph.openHint': 'Double-click to open.',
  'graph.noPage': 'Legacy source — no detail page in this vault.',

  // knowledge home
  'knowledge.title': 'Knowledge',
  'knowledge.help':
    'What the knowledge base currently believes, grouped by theme. Durable claims passed every evidence gate; caveated claims carry a known weakness and await review.',
  'knowledge.helpLayers':
    'Three layers ground every claim: the source (the original markdown), its memory (cards and quoted units with line anchors), and the crystal (cross-source claims citing those units). Click through any claim to verify the chain.',
  'knowledge.viewList': 'List',
  'knowledge.viewGraph': 'Graph',
  'knowledge.empty':
    'No claims in the crystal store yet — crystallize sources to build the knowledge layer.',
  'knowledge.untitledTheme': '(no theme)',
  'knowledge.claimCount': '{n} claims',
  'knowledge.ratioLine': 'durable {durable} · caveated {caveated}',
  'knowledge.graphCaption':
    'All claims, colored by community. Click a node for a summary, double-click to open its theme.',
  'knowledge.unknownClaim':
    'No active claim "{id}" — it may have been superseded or retracted.',

  // theme detail
  'theme.counts': 'durable {durable} · caveated {caveated}',
  'theme.citedSources': 'Sources:',
  'theme.legacySource': 'Legacy source — no detail page in this vault.',
  'theme.strength': 'strength',
  'theme.empty': 'No active claims carry this theme.',
  'theme.backToKnowledge': 'All themes',
  'theme.graph': 'Theme graph',
  'theme.graphCaption':
    'This theme’s claims and the sources they cite. Click a node for a summary, double-click to open.',

  // search page + ⌘K overlay
  'search.title': 'Search',
  'search.help':
    'One box across everything: sources, reader packs, crystal claims and themes. Results link straight to the entity — press ⌘K (Ctrl+K) anywhere to search without leaving the page.',
  'search.placeholder': 'Search sources, claims, themes…',
  'search.keys': '↑↓ navigate · Enter open · Esc close',
  'search.error': 'Search failed — is the server running against a vault?',
  'search.empty': 'No matches. Try a shorter term — search is substring-based.',
  'search.noPage': 'No detail page for this entry in this vault.',
  'search.open': 'Search (⌘K)',
  'search.group.claim': 'Claims',
  'search.group.source': 'Sources',
  'search.group.pack': 'Reader packs',
  'search.group.theme': 'Themes',

  // placeholders
  'placeholder.ask': 'Cited answers over your knowledge base land in B4.',
  'placeholder.system': 'Runs, flow, doctor and settings land in B5.',
  'placeholder.systemFlow': 'Legacy flow view',
  'placeholder.systemMonitor': 'Legacy monitor view',
} as const;
