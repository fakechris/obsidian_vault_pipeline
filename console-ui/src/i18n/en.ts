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

  // run-liveness banner (fixed top strip, every page)
  'banner.none': 'No runs yet',
  'banner.completed': 'Last run: completed {ago}',
  'banner.completedCounts': 'Last run: completed {ago} · {read} read · {queued} queued',
  'banner.running': 'Run in progress · started {ago}',
  'banner.stale': 'Last run: {ago} — the daily loop may be stalled',
  'banner.failed': 'Last run: FAILED {ago}{error}',
  'banner.aborted': 'Last run: ABORTED {ago}{error}',
  'banner.agoJustNow': 'just now',
  'banner.agoMinutes': '{n}m ago',
  'banner.agoHours': '{n}h ago',
  'banner.agoDays': '{n}d ago',
  'banner.viewSystem': 'View system status',

  // shared
  'common.loading': 'Loading…',
  'common.error': 'Could not load the index model — is the server running against a vault?',
  'common.whatIsThisPage': 'What is this page?',
  'common.day': 'dogfood day',

  // data-freshness label (P1): "as of <instant> · N min ago". Every surface
  // that shows counts stamps the projection's build instant so a stale number
  // never reads like a fresh one.
  'age.asOf': 'as of {instant}',
  'age.now': 'just now',
  'age.minutes': '{n} min ago',
  'age.hours': '{n} hr ago',
  'age.days': '{n} d ago',
  'age.unknown': 'unknown age',
  'age.stamp': 'as of {instant} · {rel}',

  // concept tooltips — plain-language one-liners for the pipeline vocabulary
  // (operator finding: durable/caveated and unit/card need explaining
  // wherever the pills render).
  'concept.durableTip':
    'Verified: every quoted citation was checked against the source text',
  'concept.caveatedTip':
    "Unverified: promising but the evidence didn't fully check out — treat with skepticism",
  'concept.claimTip':
    'Claim: a cross-source conclusion — durable (verified) or caveated (unverified)',
  'concept.cardTip':
    'Card: a readable distillation — every statement traceable to a grounded unit',
  'concept.unitTip':
    'Unit: a verbatim excerpt with line numbers — the evidence itself',

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
  'source.cardsTitle': 'Cards',
  'source.cardsHint':
    'Readable distillations of this source — every statement traceable to a grounded unit below.',
  'source.groundedUnits': 'Grounded units',
  'source.unitsHint':
    'Verbatim excerpts with line numbers — the evidence itself.',
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
    'This source → its memory cards → citing claims → sibling sources. Click a node for a summary, double-click to open it.',
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
  'graph.kindCard': 'card',
  'graph.openHint': 'Double-click to open.',
  'graph.noPage': 'Legacy source — no detail page in this vault.',
  'graph.cardHint':
    "This source's memory — the full card is in the Memory tab.",

  // knowledge home
  'knowledge.title': 'Knowledge',
  'knowledge.help':
    'What the knowledge base currently believes, grouped by theme. Durable claims passed every evidence gate; caveated claims carry a known weakness and await review.',
  'knowledge.helpLayers':
    'Three layers ground every claim: the source (the original markdown), its memory (cards and quoted units with line anchors), and the crystal (cross-source claims citing those units). Click through any claim to verify the chain.',
  'knowledge.helpLadder':
    'The ladder in plain language: source text → unit (a verifiable excerpt) → card (a readable understanding) → claim (a cross-source conclusion, always either durable or caveated).',
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

  // theme naming — the synthesizer's 'misc' fallback bucket is displayed
  // honestly (display layer ONLY: keys, URLs and data stay 'misc').
  'theme.unclassified': 'Unclassified',
  'theme.unclassifiedNote':
    "Sources that didn't match any keyword bucket — automatic clustering is a planned improvement (M34).",

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

  // ask page
  'ask.title': 'Ask',
  'ask.help':
    'Ask questions in natural language; answers are grounded in your evidence index — crystal claims, reader cards and quoted units — with numbered citations you can verify. Every answer is saved to the history on the left. Unverified citations are flagged.',
  'ask.historyTitle': 'History',
  'ask.historyEmpty': 'No saved chats yet — every answer is saved here automatically.',
  'ask.savedChat': 'Saved chat',
  'ask.closeChat': 'Back to conversation',
  'ask.chatLoadError': 'Could not load this chat — is the server running?',
  'ask.citationsTitle': 'Citations',
  'ask.citationsEmpty':
    'Citations for the latest answer land here — hover a [1] marker in the answer to highlight its evidence.',
  'ask.unverified': 'unverified',
  'ask.openCitation': 'Open',
  'ask.noLink': 'No detail page in this vault.',
  'ask.verifiedLine': 'verified citations {verified}/{cited}',
  'ask.contextHits': '{n} context hits',
  'ask.placeholder': 'Ask about your knowledge base…',
  'ask.hint': 'Enter to send · Shift+Enter for a new line',
  'ask.send': 'Send',
  'ask.pending': 'Thinking…',
  'ask.emptyTitle': 'Ask your knowledge base',
  'ask.emptyBody':
    'Answers come only from what you have read and crystallized — no outside knowledge, no invented citations. Try one of these:',
  'ask.example1': 'What does my knowledge base believe about agent memory?',
  'ask.example2': 'Which sources discuss context engineering, and what do they claim?',
  'ask.example3': 'What is the strongest evidence about retrieval quality?',
  'ask.errNotConfigured':
    'The server has no LLM configured — restart `ovp2 serve` with ANTHROPIC_API_KEY set (build with --features anthropic).',
  'ask.errIndexUnavailable':
    'The index is not available — run `ovp2 index` against this vault, and check the server was started with the right --vault-root.',
  'ask.errBusy':
    'Ask is busy — the in-flight answer limit is reached. Wait for the current answers and retry shortly.',
  'ask.errTimeout':
    'No answer within the time limit. The request was not cancelled — if the model finishes, the saved transcript still appears in History.',
  'ask.errGeneric': 'Ask failed — is the server running against a vault?',

  // system page (B5)
  'system.help':
    'The engine room: every recorded run, sources waiting on you, the pipeline admin views, what the three layers mean, and the server configuration (read-only).',
  'system.runs': 'Runs',
  'system.runsEmpty':
    'No runs recorded yet — run `ovp2 daily` against this vault.',
  'system.runDate': 'date',
  'system.runOk': 'ok',
  'system.runFailed': 'failed',
  'system.runBlocked': 'blocked',
  'system.runIngested': 'ingested',
  'system.runReport': 'report',
  'system.attentionTitle': 'Attention',
  'system.attentionEmpty':
    'Nothing needs you — no blocked or needs-content sources.',
  'system.doctorHint': 'For a deeper diagnosis, run in a terminal:',
  'system.surfaces': 'Pipeline surfaces',
  'system.surfacesNote':
    'Admin views onto the plumbing — useful when something is stuck, not part of the daily product surface.',
  'system.flowLink': 'Flow (pipeline Sankey)',
  'system.monitorLink': 'Monitor (run feed)',
  'system.adminPagesNote':
    'Legacy generated console pages (present when this vault has a generated console):',
  'system.concepts': 'Concepts',
  'system.conceptLayers':
    'Three layers, always linked: the SOURCE is the original markdown you captured; its MEMORY is the reader pack — readable cards plus quoted units anchored to source lines; the CRYSTAL is cross-source claims that cite those units.',
  'system.conceptDurable':
    'DURABLE claims passed every evidence gate; CAVEATED claims carry a known weakness and wait for review — they are labeled, never hidden.',
  'system.conceptGate':
    'THE GATE is a mechanical check before anything is written to the crystal ledger: every citation must resolve to a real quoted unit, and claim strength is scored — human decisions go through it too, never around it.',
  'system.settings': 'Settings',
  'system.settingsReadonly':
    'Read-only in v1 — changes happen at the CLI, this panel shows what the server is running with.',
  'system.settingsError': 'Could not load settings — is the server running?',
  'system.vaultRoot': 'vault',
  'system.schema': 'index schema',
  'system.indexDate': 'index date',
  'system.builtAt': 'built',
  'system.runId': 'run id',
  'system.counts': 'counts',
  'system.countsLine': '{sources} sources · {packs} packs · {claims} claims',
  'system.noIndex': 'no index built — run `ovp2 index`',
  'system.llm': 'LLM (Ask)',
  'system.llmOn': 'configured — POST /api/ask is live',
  'system.llmOff':
    'not configured — Ask answers 503. Set ANTHROPIC_API_KEY and restart `ovp2 serve` (built with --features anthropic).',
  'system.askTimeout': 'ask timeout',
  'system.askTimeoutValue': '{secs}s per question · up to {cap} concurrent',
  'system.version': 'server version',
  'system.togglesNote':
    'Theme and language switch in the top bar (LIGHT/DARK · EN/中) — persisted per browser, on every page.',
} as const;
