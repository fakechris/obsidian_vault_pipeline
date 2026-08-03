/** English UI strings — the default locale. Keys are shared with zh.ts;
 * the Dict type in index.tsx is derived from this file, so a key missing
 * in zh.ts is a compile error. */
export const en = {
  // nav
  'nav.today': 'Today',
  'nav.library': 'Library',
  'nav.workQueue': 'Work queue',
  'nav.tags': 'Tags',
  'nav.entities': 'Entities',

  // entities (Tier-0 URL entities)
  'entities.title': 'Entities',
  'entities.help':
    'Machine-verified referents pulled from links in your sources — GitHub repos, arXiv papers, packages. Ranked by how many sources mention each. Deterministic, no LLM.',
  'entities.all': 'All',
  'entities.filter': 'Filter entities…',
  'entities.empty': 'No URL entities found in your sources yet.',
  'entities.sources': 'sources',
  'entities.url': 'URL',
  'entities.kind': 'Kind',
  'entities.mentionedIn': 'Mentioned in',
  'entities.citingClaims': 'Claims from these sources',
  'entities.noClaims': 'No durable claims cite these sources yet.',
  'entities.notFound': 'Entity not found.',
  'nav.search': 'Search',
  'nav.knowledge': 'Knowledge',
  'nav.ask': 'Ask',
  'nav.system': 'System',
  'nav.back': 'Back',
  'nav.forward': 'Forward',
  'nav.openInBrowser': 'Open this page in your browser',

  // status light
  'status.ok': 'ok',
  'status.attention': 'attention',
  'status.failed': 'last run failed',

  // run-liveness banner (fixed top strip, every page)
  'banner.none': 'No runs yet',
  'banner.completed': 'Last run: completed {when} ({ago})',
  'banner.completedCounts': 'Last run: completed {when} ({ago}) · {read} read · {queued} queued',
  'banner.running': 'Run in progress · started {when} ({ago})',
  'banner.runningProgress': 'Run in progress · {done}/{total} ({current}) · started {when} ({ago})',
  'banner.runningProgressNoCurrent': 'Run in progress · {done}/{total} · started {when} ({ago})',
  'banner.stale': 'Last run: {when} ({ago}) — daily loop may be stalled',
  'banner.failed': 'Last run: FAILED · {when} ({ago}){error}',
  'banner.aborted': 'Last run: ABORTED · {when} ({ago}){error}',
  'banner.agoJustNow': 'just now',
  'banner.agoMinutes': '{n}m ago',
  'banner.agoHours': '{n}h ago',
  'banner.agoDays': '{n}d ago',
  'banner.viewSystem': 'View system status',
  'banner.activityToggle': 'activity',
  'banner.hover.base':
    'Heartbeat (.ovp/last-run.json)\nrun: {runId}\nstatus: {status}\nstarted: {started}\nended: {ended}\n{error}',
  'banner.hover.schedule':
    'Schedule (daily)\nlast schedule stamp: {lastRun} ({lastStatus})\nnext due: {nextRun}\n{dueLine}\nClock: desktop app ticks ~every 10m while open; OS launchd/cron if installed. A failed capture phase no longer blocks intake+reader.',
  'banner.hover.dueYes': 'due now: YES — waiting for the next tick / Run now',
  'banner.hover.dueNo': 'due now: no',
  'banner.hover.noError': '(no error body)',

  // live run activity feed (the portal's tail -f)
  'activity.title': 'Run activity',
  'activity.running': 'In progress · {done}/{total} · {pct}% · started {when} ({ago})',
  'activity.current': 'Now: {current}',
  'activity.idle': 'No run in progress. Showing the last run’s timeline.',
  'activity.empty': 'No per-source rows (run never reached the reader phase, or planned 0 sources).',
  'activity.emptyAfterFail':
    'No per-source rows — the run died before processing any source (see timeline above). Intake/reader did not start.',
  'activity.emptyRunning': 'Waiting for the first source outcome… (timeline updates as phases complete)',
  'activity.feedTitle': 'Per-source feed (newest first)',
  'activity.ok': '{title} — {units} units · {cards} cards',
  'activity.failed': '{title} — {reason}',
  'activity.failedNoReason': '{title} — failed',
  'activity.finished': 'Finished: {ok} read · {failed} failed',
  'activity.finishedOk': 'Completed {started} → {ended} · {ok} read · {failed} failed',
  'activity.finishedFail': 'Failed {started} → {ended} · {ok} read · {failed} failed before exit',
  'timeline.title': 'Timeline',
  'timeline.noClock': '—',
  'timeline.stillRunning': '(still running)',
  'timeline.clockLine':
    '{runId} · {status} · start {started} · end {ended} · ({ago})',
  'timeline.schedStamp': 'Schedule stamp written ({status})',
  'timeline.started': 'Daily process started ({runId})',
  'timeline.completed': 'Completed · {ok} read · {failed} failed · queue left {queued}',
  'timeline.running': 'Still running (capture / plan phase — no source counts yet)',
  'timeline.runningProgress': 'Reading sources · {done}/{total}{current}',
  'timeline.failPinboard':
    'FAILED at Pinboard capture — intake + reader never started. {error}',
  'timeline.failIntake': 'FAILED at intake sweep — reader never started. {error}',
  'timeline.failEnrich': 'FAILED during enrich (web/github). {error}',
  'timeline.failReader': 'FAILED during reader / LLM. {error}',
  'timeline.failIndex': 'FAILED during index/console refresh. {error}',
  'timeline.failUnknown': 'FAILED. {error}',
  'timeline.skippedReader': 'Skipped: intake → reader → index (not reached)',
  'timeline.noSourceFeed': 'No per-source activity — failure was before any source was claimed',
  'timeline.nextDue': 'Next schedule window ({cadence})',
  'timeline.nextDueNow': 'Next schedule window ({cadence}) — DUE NOW (waiting for tick / Run now)',

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
  'today.dayTitle': 'Day {day}',
  'today.help':
    'Pick a day to see two timelines: content day (when a note was published/bookmarked) vs pipeline day (when daily/reader ran). Subject period (what the article is about) is not stored yet. Attention is the live operator queue.',
  'today.captured': 'Intake that day',
  'today.read': 'Read (pipeline)',
  'today.claims': 'Claims',
  'today.dayClaims': 'Crystal',
  'today.dayPacks': 'Packs',
  'today.daySourcesDated': '{n} content-day',
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
  'today.crystalTitle': 'Crystal that day',
  'today.crystalNote':
    'Claims whose run id embeds this calendar day (for example a daily or full-crystal run). Claims without a date-bearing run id are omitted.',
  'today.crystalEmpty': 'No date-linked claims for this day.',
  'today.claimSources': 'Sources',
  'today.strength': 'strength',
  'today.readToday': 'Read today',
  'today.readTitle': 'Processed that day (pipeline)',
  'today.readEmpty':
    'Nothing finished on this pipeline day — no successful daily sources for its run(s).',
  'today.packsTitle': 'Reader packs (pipeline day)',
  'today.packsEmpty': 'No reader packs written on this pipeline day.',
  'today.sourcesDatedTitle': 'Content day (capture / publish)',
  'today.sourcesDatedEmpty':
    'No sources have a content day (published / bookmark / filename) on this date.',
  'today.runsTitle': 'Pipeline runs',
  'today.runLine': 'ok {ok} · fail {fail} · in {ingested} · blocked {blocked}',
  'today.capturedEmpty': 'no intake that day',
  'today.timeline': 'Timeline',
  'today.timelineRead': 'read {n}',
  'today.timelineCaptured': 'captured {n}',
  'today.timelineAll': '→ System: all runs',
  'today.noRunsToday':
    'No runs recorded for today yet — stats show 0 until the daily run lands.',
  'today.noActivityDay':
    'No runs, packs, or date-linked claims recorded for this day in the current index.',
  'today.calPrevMonth': 'Previous month',
  'today.calNextMonth': 'Next month',
  'today.calJumpToday': 'Jump to projection day',
  'today.calLegend':
    'Dots mark days with vault activity (runs, dated sources/packs, or date-linked claims).',

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
  'library.byTag': 'By tag',
  'library.manageTags': 'Manage tags →',
  'library.moreTags': 'more tags',

  // tags curation page
  'tags.title': 'Tags',
  'tags.help':
    'Your tag vocabulary and its curation inbox. Accepting a merge records it in the decisions file (your hand-edited aliases.toml is never touched) and rebuilds the index; rejecting remembers the pair so it never resurfaces.',
  'tags.inbox': 'Merge proposals',
  'tags.nameCos': 'name',
  'tags.contextCos': 'context',
  'tags.acceptHint': 'Merge the lower-count tag into the higher-count one',
  'tags.reverse': 'Reverse',
  'tags.reverseHint': 'Merge the other direction (make the second tag the alias)',
  'tags.showWeak': 'Show weaker candidates',
  'tags.hideWeak': 'Hide weaker candidates',
  'tags.accept': 'Accept',
  'tags.reject': 'Reject',
  'tags.vocabulary': 'Vocabulary',
  'tags.filter': 'Filter tags…',
  'tags.empty': 'No tags match.',
  'tags.addPlaceholder': 'Add tag…',
  'tags.acceptInferred': 'accept',
  'library.statusAll': 'All',
  'library.empty': 'No sources match the current filters.',
  'library.noDate': 'no date',

  // source detail
  'source.title': 'Source',
  'source.browseNav': 'Browse within Library filter',
  'source.browsePrev': 'Previous',
  'source.browseNext': 'Next',
  'source.browsePrevHint': 'Previous source in the current Library filter ([ or ←)',
  'source.browseNextHint': 'Next source in the current Library filter (] or →)',
  'source.browsePosition': '{i} / {n}',
  'source.workBusyTranslate':
    'Translating… the 中文 tab will appear here when ready — no need to leave this page.',
  'source.workBusySummarize':
    'Summarizing… the Summary tab will appear here when ready — no need to leave this page.',
  'source.workBusyBoth':
    'Translating + summarizing in parallel… tabs appear here when each finishes.',
  'source.queuedOk': 'Queued — you can leave; a notification will fire when done.',
  'source.queuedTranslate': 'Translation queued',
  'source.queuedSummarize': 'Summary queued',
  'source.openQueue': 'Open work queue',

  // work queue page
  'workq.title': 'Work queue',
  'workq.help':
    'Translate and deep-summary jobs run one article at a time. Within an article, both tasks can run in parallel. UI clicks are high priority and jump ahead of bulk backfill. Drag order with ↑↓ within the same priority, cancel queued items, get a notification when each article finishes.',
  'workq.refresh': 'Refresh',
  'workq.counts': 'running {running} · queued {queued} · history {history}',
  'workq.polled': 'updated {time}',
  'workq.workerHere': 'Queue worker is this portal (pid {pid}). Safe to open other windows — only one worker runs per vault.',
  'workq.workerElsewhere':
    'Queue worker is another process (pid {pid}); this portal (pid {here}) only enqueues. Jobs still run — no need to close either.',
  'workq.running': 'Running',
  'workq.queued': 'Queued',
  'workq.queuedEmpty': 'Nothing waiting — start Translate or Summary from a source page.',
  'workq.history': 'Recent',
  'workq.cancel': 'Cancel',
  'workq.remove': 'Remove',
  'workq.taskTranslate': 'Translate',
  'workq.taskSummarize': 'Summary',
  'workq.prioInteractive': 'UI',
  'workq.prioBackfill': 'bf',
  'workq.prioHint': 'priority {n} — higher runs first (UI 100 · backfill 0)',
  'workq.etaTitle': 'Pace & ETA',
  'workq.etaAvg': '≈ {avg} / article',
  'workq.etaMedian': 'median {median}',
  'workq.etaSamples': 'from {n} recent finishes',
  'workq.etaThroughput': '{n15} done in 15m · {n60} in the last hour',
  'workq.etaLast': 'Last finished {when} · took {dur}',
  'workq.etaLastTitle': '“{title}”',
  'workq.etaLastFailed': 'Last finished {when} · failed after {dur}',
  'workq.etaRunning': 'Current article running {elapsed}',
  'workq.etaRemaining': '~{left} left · ETA {eta}',
  'workq.etaRemainingShort': '~{left} remaining',
  'workq.etaIdle': 'queue empty',
  'workq.etaWarmup': 'Need a few finished jobs before ETA is reliable',
  'workq.etaNoWorker': 'No active worker — ETA pauses until a worker is elected',
  'workq.etaHoverHint': 'details',
  'workq.compactPace': '≈{avg}/article',
  'workq.compactThru': '{n15}/15m · {n60}/h',
  'workq.compactEta': '~{left} · ETA {eta}',
  'workq.compactRunning': 'run {elapsed}',
  'workq.compactNoWorker': 'no worker',
  'workq.itemDuration': 'took {dur}',
  'workq.itemFinished': 'finished {when}',
  'workq.itemRunningFor': 'running {elapsed}',
  'source.url': 'url',
  'source.companions': 'open with',
  'source.staticLite':
    'The full text and evidence layer are not published here. Follow the url above to read the original; the durable claims this source supports are listed on the right.',
  'source.staticLiteNoUrl':
    'The full text and evidence layer are not published here. The durable claims this source supports are listed on the right.',
  'source.date': 'date',
  'source.origin': 'origin',
  'source.location': 'location',
  'source.lastRun': 'last run',
  'source.failCount': 'failures',
  'source.lastReason': 'last error',
  'source.failedTitle': 'This source has not been processed yet.',
  'source.failedBody': 'The reader pipeline failed on it ({attempts} attempt(s)) — so it has no memory or claims below. It retries automatically on the next daily run; after 3 failures it is set aside until you review it.',
  'source.failedBlockedBody': 'It failed 3 times and is set aside pending review — no memory or claims below. Fix the cause and rerun `ovp2 daily --retry-blocked`, or waive it.',
  'source.failedReason': 'What went wrong:',
  'source.notFound': 'No source with this id in the index.',
  'source.backToLibrary': 'Library',
  'source.loadError': 'Could not load the source detail — is the server running?',
  'error.pageTitle': 'Something went wrong rendering this page.',
  'error.pageHint':
    'Use the navigation above to keep browsing — reopening the page retries.',
  'source.tabMemory': 'Memory',
  'source.tabMemoryCounts': '{cards} cards · {units} units',
  'source.tabSource': 'Source',
  'source.tabZh': '中文',
  'source.tabSummary': 'Summary',
  'source.translate': 'Translate to 中文',
  'source.retranslate': 'Re-translate',
  'source.translating': 'Translating…',
  'source.summarize': 'Deep summary',
  'source.resummarize': 'Re-summarize',
  'source.summarizing': 'Summarizing…',
  'source.chatOnThis': 'Chat on this',
  'source.chatPanelTitle': 'Chat on this source',
  'source.chatGroundedIn': 'Grounded in',
  'source.chatChipBody': 'Body',
  'source.chatChipMemory': 'Memory · {n}',
  'source.chatChipCrystal': 'Crystal · {n}',
  'source.chatMetaLine':
    'Context · body + {cards} cards · {units} units · {claims} crystals',
  'source.chatPackSummary':
    '{cards} cards · {units} units · {claims} citing claims — auto-injected each turn (not shown raw)',
  'source.chatPackHint': 'Injected each turn · not shown as text',
  'source.chatEmpty':
    'Ask about this article. Body, memory cards/units, and citing crystals are already in context — no need to re-select.',
  'source.chatPlaceholder': 'Ask about this source…',
  'source.chatSeed1': 'What is the core thesis of this piece?',
  'source.chatSeed2': 'How do the memory cards relate to the original text?',
  'source.chatSeed3': 'Which crystal claims cite this source, and why?',
  'source.chatRecents': 'Past chats on this source',
  'source.chatNewOnSource': 'New chat',
  'source.chatOpenInAsk': 'Open in Ask',
  'source.chatClose': 'Close',
  'source.chatWorking': 'Working… ({n} steps)',
  'source.workDir': 'archive',
  'source.zhEmpty': 'No Chinese translation yet — use Translate above.',
  'source.summaryEmpty': 'No deep summary yet — use Deep summary above.',
  'source.frontmatter': 'Properties (frontmatter)',
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
    'This source → its memory cards → citing claims → sibling sources. Click a node for a summary and an open link; hover to highlight its neighborhood.',
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
  'graph.openHint': 'Click for a summary, then Open.',
  'graph.open': 'Open',
  'graph.evidenceTitle': 'Evidence chain',
  'graph.lineageTitle': 'Lineage',
  'graph.lineageStatus': 'status: {status}',
  'graph.lineageSupersedes': 'supersedes {key}',
  'graph.lineageSupersededBy': 'superseded by {key}',
  'graph.evidenceLine': 'line {n}',
  'graph.no3d': '3D needs WebGL, which is unavailable in this browser.',
  'graph.controls3d': 'Drag to rotate · scroll to zoom · right-drag to pan',
  'graph.focusCommunity': 'Focus this community',
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
  'knowledge.viewTerrain': 'Terrain',
  'knowledge.perspClaim': 'Claims',
  'knowledge.perspSource': 'Sources',
  'knowledge.perspNodeClaim':
    'Each point is a claim — a cross-source conclusion. Click to open its theme.',
  'knowledge.perspNodeSource':
    'Each point is a source — an original document. Click to open it.',
  'knowledge.terrainCaption':
    'A themescape of the corpus: peaks are dense clusters (labelled by theme), each point a source. Hover a point to see the note. Built from the same embeddings as the themes.',
  'knowledge.terrainHud': '{notes} notes · {themes} themes · drag to orbit, scroll to zoom',
  'knowledge.terrainLoading': 'loading…',
  'knowledge.terrainAllTime': 'all time',
  'knowledge.terrainPlay': 'play',
  'knowledge.terrainPause': 'pause',
  'knowledge.terrainNotBuilt':
    'Terrain not built yet — run `ovp2 crystal-terrain --vault-root <vault>`.',
  'knowledge.terrainNoWebgl':
    'This 3D view needs WebGL, which is unavailable in this browser. Try the List or Graph view.',
  'knowledge.terrainUnclassified': 'Unclassified',
  'knowledge.terrainFocusTheme': 'Filter to this theme and fly to it (click again to clear)',
  'knowledge.terrainTagFilter': 'Filter by tag (click again to clear)',
  'knowledge.terrainCrystalEvidence': '{n} sources · spans {m} clusters',
  'knowledge.terrainCrystalTag': 'Crystal',
  'knowledge.terrainHudClaims': '◆ {crystals} crystals over the source land · {themes} themes',
  'knowledge.terrainHeightBy': 'What the mountain height encodes',
  'knowledge.terrainHeightDensity': 'Height: density',
  'knowledge.terrainHeightRecency': 'Height: recency',
  'knowledge.terrainHeightInfluence': 'Height: influence',
  'knowledge.empty':
    'No claims in the crystal store yet — crystallize sources to build the knowledge layer.',
  'knowledge.untitledTheme': '(no theme)',
  'knowledge.claimCount': '{n} claims',
  'knowledge.ratioLine': 'durable {durable} · caveated {caveated}',
  'knowledge.graphCaption':
    'All claims, colored by community. Zoom in to reveal labels, hover to highlight a neighborhood, click for a summary. Toggle 3D to rotate.',
  'knowledge.unknownClaim':
    'No active claim "{id}" — it may have been superseded or retracted.',

  // theme naming — the synthesizer's 'misc' fallback bucket is displayed
  // honestly (display layer ONLY: keys, URLs and data stay 'misc').
  'theme.unclassified': 'Unclassified',
  'theme.unclassifiedNote':
    "Sources that didn't match any keyword bucket — automatic clustering is a planned improvement (M34).",

  // theme detail
  'theme.topicOverview': 'Topic overview',
  'theme.topicOverviewCaption': 'woven from {n} claims',
  'theme.counts': 'durable {durable} · caveated {caveated}',
  'theme.contentLang': 'content {lang} · zh ready {zh}/{total}',
  'theme.claimEnOnly': 'EN only',
  'theme.claimZhMissingTip':
    'No Chinese projection for this claim — ledger text is English authority.',
  'theme.zhMissingBody':
    'UI is Chinese, but claim translations are missing for all {n} claims on this theme. Theme labels may still localize; body text falls back to English.',
  'theme.zhMissingHint':
    'Generate rebuildable projections: `ovp2 source-work claims-zh --vault-root … --client live` and `ovp2 source-work memory-zh --vault-root … --theme-pages --client live`. Files land under `.ovp/crystal/claims_zh.json` / `theme_pages_zh.json` (ledger stays English).',
  'theme.zhPartialOverview':
    'Claim zh is partial; topic-overview sections are still English (no theme_pages_zh yet).',
  'theme.switchToEn': 'Switch UI to EN',
  'theme.citedSources': 'Sources:',
  'theme.legacySource': 'Legacy source — no detail page in this vault.',
  'theme.strength': 'strength',
  'theme.empty': 'No active claims carry this theme.',
  'theme.backToKnowledge': 'All themes',
  'theme.graph': 'Theme graph',
  'theme.graphCaption':
    'This theme’s claims and the sources they cite. Click a node for a summary and an open link; hover to highlight its neighborhood.',

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
    'Vault assistant: finds articles in your library, answers knowledge questions with checkable citations, or chats more openly. Follow-ups keep context in one session. Ask “what can you do?” for capability limits.',
  'ask.focusBanner':
    'Source-grounded mode — answers prefer the focused article (body + memory + crystal). Prefer chatting from the source page:',
  'ask.focusOpenOnSource': 'Open chat on source',
  'ask.historyTitle': 'History',
  'ask.historyEmpty':
    'No saved chats yet — each conversation is saved here as one session.',
  'ask.historyFilterAll': 'All',
  'ask.historyFilterSource': 'On sources',
  'ask.historyFilterVault': 'Vault-wide',
  'ask.historyFilterEmpty': 'No chats in this filter.',
  'ask.historyOnSource': 'Source',
  'ask.historyVault': 'Vault',
  'ask.savedChat': 'Saved chat',
  'ask.closeChat': 'Back to conversation',
  'ask.newConversation': 'New conversation',
  'ask.chatLoadError': 'Could not load this chat — is the server running?',
  'ask.chatParseEmpty':
    'This saved transcript has no readable Q/A turns — the file may be corrupt or empty.',
  'ask.citationsTitle': 'Citations',
  'ask.citationsEmpty':
    'Citations for the latest answer land here — hover a [1] marker in the answer to highlight its evidence.',
  'ask.processTitle': 'Process',
  'ask.processHelp':
    'Entities the agent touched while searching — claims, sources, and memory cards grow into the graph live.',
  'ask.processEmpty':
    'Nodes appear here as the agent searches claims, sources, and evidence.',
  'ask.processClaims': '{n} claims',
  'ask.processSources': '{n} sources',
  'ask.processMemory': '{n} memory',
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
    'No LLM configured — open System → LLM Provider, save an API key (and endpoint/model if needed), then ask again. No restart required.',
  'ask.errIndexUnavailable':
    'The index is not available — run `ovp2 index` against this vault, and check the server was started with the right --vault-root.',
  'ask.errBusy':
    'Ask is busy — the in-flight answer limit is reached. Wait for the current answers and retry shortly.',
  'ask.errTimeout':
    'No answer within the time limit. The request was not cancelled — if the model finishes, the saved transcript still appears in History.',
  'ask.errGeneric': 'Ask failed — is the server running against a vault?',
  // agent live trail + receipts (A3c)
  'ask.trailConnecting': 'Connecting to the vault agent…',
  'ask.trailThinking': 'Thinking…',
  'ask.trailComposing': 'Writing the answer…',
  'ask.trailSearching': 'Searching',
  'ask.trailReading': 'Reading',
  'ask.trailListing': 'Listing',
  'ask.trailRunning': 'Running',
  'ask.trailTitle': 'Agent steps',
  'ask.trailFailedStep': 'failed',
  'ask.coverageTitle': 'Searched',
  'ask.covClaims': 'conclusions',
  'ask.covSources': 'sources',
  'ask.covBody': 'article reads',
  'ask.covEvidence': 'evidence cards',
  'ask.covFulltext': 'full-text scan',
  'ask.covComplete': 'complete',
  'ask.covPartial': 'partial',
  'ask.covNotQueried': 'not searched',
  'ask.covUnavailable': 'unavailable',
  'ask.covFailed': 'failed',
  'ask.stopTimeout':
    'The agent ran out of time this turn — below is what it completed before stopping.',
  'ask.stopToolError': 'A vault tool kept failing — the answer may be incomplete.',
  'ask.stopModelError': 'The model call failed mid-turn — the answer may be incomplete.',
  'ask.stopMaxRounds':
    'The agent reached its round limit and answered with what it had.',

  // automation / schedule explainer (System)
  'auto.title': 'Automation',
  'auto.help':
    'A map of what the timer runs — pick a job, follow the nodes left to right. Dimmed nodes are off; click a node for a short explanation.',
  'auto.detailHint': 'Click a node on the left to see what that stage does.',
  'auto.jobsAria': 'Scheduled jobs',
  'auto.toggle.pinboard': 'Pinboard live sync',
  'auto.toggle.pinboardHint':
    'Toggle on the Pinboard node (or here). Needs PINBOARD_TOKEN in .ovp/daily.env.',
  'auto.clock':
    'Clock: the desktop app ticks every ~10 minutes while it is open. Closing the app pauses automatic runs (unless you also installed an OS schedule with `ovp2 schedule install`).',
  'auto.loading': 'Loading schedule…',
  'auto.error': 'Could not load schedule',
  'auto.missing':
    'No schedule registry yet ({path}). Opening the desktop app seeds defaults (daily + weekly crystallize), or run `ovp2 schedule init`.',
  'auto.empty': 'Schedule registry is empty — no jobs configured.',
  'auto.configHint': 'Source of truth: vault {path}. Historical volume:',
  'auto.flowLink': 'Pipeline flow →',
  'auto.cadence': 'When',
  'auto.lastRun': 'Last run',
  'auto.nextRun': 'Next due',
  'auto.never': 'Never',
  'auto.paused': 'Paused (disabled)',
  'auto.lastRunLine': 'Last: {when} ({status})',
  'auto.dueExplainYes':
    'Due: the most recent schedule window has passed since the last stamp, but nothing new finished. The desktop app must be open (or launchd installed) to actually exec — or use Run now below.',
  'auto.dueExplainNo': 'Not due: still inside this cycle, or the job is disabled.',
  'auto.jobTooltip':
    '{title}\ncadence: {cadence}\nstatus: {status}\nlast: {lastRun}\nnext: {nextRun}\n{dueExplain}',
  'auto.pipelineAria': 'Pipeline stages for this job',
  'auto.stageOn': 'On',
  'auto.stageOff': 'Off',
  'auto.stageAlways': 'Always',
  'auto.status.ok': 'OK',
  'auto.status.error': 'Error',
  'auto.status.seeded': 'Waiting first run',
  'auto.status.due': 'Due now',
  'auto.status.disabled': 'Disabled',
  'auto.status.idle': 'Idle',
  'auto.job.daily': 'Daily intake + reader',
  'auto.job.crystallize': 'Weekly crystallize',
  'auto.job.other': 'Job: {id}',
  'auto.job.noDesc': 'No description.',
  'auto.stage.pinboard': 'Pinboard capture',
  'auto.stage.pinboard.body':
    'Pull new bookmarks from Pinboard into 50-Inbox/02-Pinboard. Off by default — enable only when you want live Pinboard sync in the daily job.',
  'auto.stage.intake': 'Intake sweep',
  'auto.stage.intake.body':
    'Collect notes from Clippings/, 00-Capture/, and 02-Pinboard/ into 50-Inbox/01-Raw (dedupe + normalize).',
  'auto.stage.web': 'Web enrich',
  'auto.stage.web.body':
    'Fetch full article text for needs-content URLs so the reader has a body to work on.',
  'auto.stage.github': 'GitHub enrich',
  'auto.stage.github.body':
    'Fill in metadata for GitHub repository URLs among needs-content sources.',
  'auto.stage.reader': 'Plan + reader',
  'auto.stage.reader.body':
    'For each new source: dedupe by content hash, run the reader trunk (cards + quoted units), write a Reader pack.',
  'auto.stage.reader.bodyMax':
    'For each new source (up to {n} per run): dedupe by content hash, run the reader trunk (cards + quoted units), write a Reader pack.',
  'auto.stage.lifecycle': 'Archive + report',
  'auto.stage.lifecycle.body':
    'Move succeeded sources to 03-Processed/YYYY-MM/ and write the durable run report under .ovp/reports/.',
  'auto.stage.index': 'Index refresh',
  'auto.stage.index.body':
    'Rebuild the read model so Today, Ask, and search see what this run produced.',
  'auto.stage.crystal': 'Cross-source crystallize',
  'auto.stage.crystal.body':
    'Synthesize durable crystal claims across sources (expensive; weekly by default).',
  'auto.stage.custom': 'Command: {id}',
  'auto.stage.custom.body': 'Custom scheduled argv — see registry for flags.',

  // system page (B5)
  'system.help':
    'The engine room: scheduled automation (what the timer runs), every recorded run, sources waiting on you, pipeline admin views, the three layers, and server configuration (read-only).',
  'system.runs': 'Runs',
  'system.runsEmpty':
    'No runs recorded yet — run `ovp2 daily` against this vault.',
  'system.runsShowingRecent':
    'Showing the newest {shown} of {total} runs (reports are kept forever under .ovp/reports/).',
  'system.runsShowingAll':
    'Showing all {n} runs (reports are kept forever under .ovp/reports/).',
  'system.runsExpand': 'Show {n} older run(s)…',
  'system.runsCollapse': 'Show only the newest {n}',
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
  'attention.ack': 'Acknowledge',
  'attention.ackHint': 'Hide this item until its status changes',
  'banner.retry': 'Retry',
  'banner.retrying': 'Starting…',
  'run.title': 'Pipeline run',
  'run.help':
    'Force today\'s daily job to run right now. Protected: a second click while any run is in progress is rejected, and a completed manual run counts for the automatic schedule (no double run).',
  'run.runNow': 'Run today\'s job now',
  'run.runAgain': 'Run again',
  'run.running': 'Running…',
  'run.confirmAgain': 'Today\'s job already ran. Run it again?',
  'run.lastRun': 'Last scheduled run: {when} ({status})',
  'run.lastRunHint':
    'Local stamp from .ovp/schedule-state.json. May differ by seconds from the top banner heartbeat (.ovp/last-run.json); status=error means the child exited non-zero.',
  'run.lastOk': 'Manual run completed.',
  'run.lastFailed': 'Manual run failed',
  'providers.title': 'LLM Provider',
  'providers.help':
    'Which model endpoint the pipeline uses (stored in .ovp/providers.toml; keys are masked once saved).',
  'providers.preset': 'Provider',
  'providers.baseUrl': 'Endpoint',
  'providers.baseUrlHint': 'empty = official Anthropic API',
  'providers.model': 'Model',
  'providers.apiKey': 'API key',
  'providers.apiKeyHint': 'leave masked value to keep the current key',
  'providers.noProxy': 'Bypass system proxy for LLM calls',
  'providers.save': 'Save',
  'providers.saved':
    'Saved. Ask and scheduled runs pick this up immediately — no restart needed.',
  'providers.protocolNote':
    'All presets use Anthropic-Messages-compatible endpoints. OpenAI-compatible and Gemini native protocols are not supported yet.',
  'system.publish': 'Publish',
  'system.publishHelp':
    'Snapshot the public-safe knowledge site and deploy it as configured in .ovp/publish.toml.',
  'system.publishNotConfigured':
    'Not configured — set `out` (and optionally `repo`/`branch`/`spa_dir`) in .ovp/publish.toml.',
  'system.publishNow': 'Publish now',
  'system.publishRunning': 'Publishing…',
  'system.publishLastOk': 'Last publish: {files} files, {claims} durable claims',
  'system.publishPushed': 'pushed',
  'system.publishNoChange': 'no change to deploy',
  'system.publishLastFailed': 'Last publish failed',
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
  'system.queued': 'queued',
  'system.queuedLiveOnly': '{live} · live',
  'system.queuedLiveVsBuild': '{live} · live (projection {build} @ {date})',
  'system.noIndex': 'no index built — run `ovp2 index`',
  'system.llm': 'LLM (Ask)',
  'system.llmOn': 'configured — Ask is live',
  'system.llmOff':
    'not configured — set an API key under LLM Provider above. Ask picks it up as soon as you save.',
  'system.askTimeout': 'ask timeout',
  'system.askTimeoutValue': '{secs}s per question · up to {cap} concurrent',
  'system.uiBuild': 'UI build',
  'system.version': 'server version',
  'system.togglesNote':
    'Theme and language switch in the top bar (LIGHT/DARK · EN/中) — persisted per browser, on every page.',
} as const;
