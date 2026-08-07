/** Source detail `/library/:sha` — the three-layer drill-down (design §3.2):
 * header meta, [Memory | Source md] tabs, grounded units with `L<n> →`
 * anchors into the markdown reading view, and a right rail with the
 * neighborhood KnowledgeGraph and citing crystal claims. Data comes from
 * /api/source/:sha; the markdown is rendered client-side by the escape-first
 * renderer in lib/markdown.tsx (raw text never becomes HTML). */
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import KnowledgeGraph from '../components/KnowledgeGraph';
import SourceChatPanel from '../components/SourceChatPanel';
import { ClaimPill, EmptyState, StatusPill } from '../components/ui';
import { useI18n } from '../i18n';
import {
  entityUrl,
  fetchSourceDetail,
  fetchSourceWork,
  fetchTags,
  postSourceTags,
  STATIC_MODE,
  type SourceWorkPayload,
} from '../lib/api';
import { useSourceWorkQueueOptional } from '../lib/sourceWorkQueue';
import {
  collectionOf,
  isMiscTheme,
  libraryBrowseOrder,
  libraryFilterActive,
  libraryFilterFromSearch,
  librarySourcePath,
  loadLibraryNavSnapshot,
  sourceDisplayTitle,
  sourceThemes,
  themeRoute,
  type LibraryFilter,
} from '../lib/derive';
import { isReactImeComposing } from '../lib/ime';
import { MarkdownView, sourceImageCandidates } from '../lib/markdown';
import { companionLinks, isPrimarilyEnglish } from '../lib/sourceLinks';
import type { ClaimRow, SourceDetail, SourceRow } from '../lib/types';
import { useModel } from '../model';

type Tab = 'memory' | 'source' | 'zh' | 'summary';

interface DetailState {
  detail: SourceDetail | null;
  status: 'loading' | 'ready' | 'notFound' | 'error';
}

/** meta.translated_at / summarized_at — used to detect force re-runs finishing. */
function workStamp(
  w: SourceWorkPayload | null | undefined,
  key: 'translated_at' | 'summarized_at',
): string | null {
  const m = w?.meta;
  if (!m || typeof m !== 'object') return null;
  const v = (m as Record<string, unknown>)[key];
  return typeof v === 'string' && v.length > 0 ? v : null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms));
}

/**
 * Long translate/summarize POSTs often outlive desktop WebView request
 * tolerance while the server still finishes. Poll GET /work until the
 * predicate holds so the 中文/摘要 tab appears without leaving the page.
 */
async function pollSourceWork(
  sha: string,
  done: (w: SourceWorkPayload) => boolean,
  opts: {
    timeoutMs?: number;
    intervalMs?: number;
    /** Set `.current = true` to stop early (POST already applied UI). */
    stop?: { current: boolean };
  } = {},
): Promise<SourceWorkPayload | null> {
  const timeoutMs = opts.timeoutMs ?? 12 * 60 * 1000;
  const intervalMs = opts.intervalMs ?? 2000;
  const deadline = Date.now() + timeoutMs;
  let last: SourceWorkPayload | null = null;
  while (Date.now() < deadline) {
    if (opts.stop?.current) return last && done(last) ? last : null;
    try {
      last = await fetchSourceWork(sha);
      if (done(last)) return last;
    } catch {
      /* transient — keep polling */
    }
    await sleep(intervalMs);
  }
  return last && done(last) ? last : null;
}

function useSourceDetail(sha: string | undefined, version: number): DetailState {
  const [state, setState] = useState<DetailState>({
    detail: null,
    status: 'loading',
  });

  useEffect(() => {
    if (!sha) {
      setState({ detail: null, status: 'notFound' });
      return;
    }
    let cancelled = false;
    setState({ detail: null, status: 'loading' });
    fetchSourceDetail(sha)
      .then((detail) => {
        if (!cancelled) setState({ detail, status: 'ready' });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            detail: null,
            status: String(err).includes(': 404') ? 'notFound' : 'error',
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sha, version]);

  return state;
}

/** Tag chips + the sanctioned edit affordances (live portal only): accept an
 * inferred tag (writes it into the note's frontmatter — the user editing
 * their vault through the product) or add a tag with vocabulary
 * autocomplete, reuse-first. */
function SourceTags({
  source,
  onChanged,
}: {
  source: SourceRow;
  onChanged: (newSha?: string) => void;
}) {
  const { t } = useI18n();
  const [vocab, setVocab] = useState<string[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!STATIC_MODE) {
      fetchTags()
        .then((d) => setVocab(d.tags.map((r) => r.tag)))
        .catch(() => setVocab([]));
    }
  }, []);

  const write = async (tags: string[]) => {
    setBusy(true);
    setError(null);
    try {
      const res = await postSourceTags(source.sha256, tags);
      setDraft('');
      onChanged(res.sha);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <dd>
      {(source.tags ?? []).map((tg) => (
        <Link key={tg} className="tag-chip" to={`/library?tag=${encodeURIComponent(tg)}`}>
          #{tg}
        </Link>
      ))}
      {(source.tags_inferred ?? []).map((tg) => (
        <span key={`~${tg}`} className="tag-chip inferred" title="inferred (tags-suggest)">
          ~#{tg}
          {!STATIC_MODE && (
            <button
              type="button"
              className="tag-chip-accept"
              disabled={busy}
              title={t('tags.acceptInferred')}
              onClick={() => write([tg])}
            >
              ✓
            </button>
          )}
        </span>
      ))}
      {(source.tags_implied ?? []).map((tg) => (
        <Link
          key={`>${tg}`}
          className="tag-chip implied"
          to={`/library?tag=${encodeURIComponent(tg)}`}
          title="rolled up via implications"
        >
          &gt;#{tg}
        </Link>
      ))}
      {!STATIC_MODE && (
        <>
          <input
            className="tag-add"
            list="tag-vocabulary"
            value={draft}
            disabled={busy}
            placeholder={t('tags.addPlaceholder')}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // IME: Enter confirms a candidate, not "add tag" (lib/ime.ts).
              if (isReactImeComposing(e)) return;
              if (e.key === 'Enter' && draft.trim()) write([draft.trim()]);
            }}
          />
          <datalist id="tag-vocabulary">
            {vocab.map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
        </>
      )}
      {error && <span className="fail-note">{error}</span>}
    </dd>
  );
}

/** Which crystal knowledge this source supports: the distinct themes its
 * citing claims land in, linking into /knowledge/theme/:theme. Hidden when
 * no active claim cites the source (the claims card already shows empty). */
function SupportedThemes({ citing }: { citing: ClaimRow[] }) {
  const { t } = useI18n();
  const themes = sourceThemes(citing);
  if (themes.length === 0) return null;
  return (
    <div className="card">
      <h3 style={{ marginBottom: '0.6rem' }}>{t('source.supportedThemes')}</h3>
      <ul className="citing-list">
        {themes.map(({ theme, count }) => (
          <li key={theme || '(unclassified)'}>
            <Link to={themeRoute(theme)}>
              {isMiscTheme(theme)
                ? t('theme.unclassified')
                : theme || t('knowledge.untitledTheme')}
            </Link>{' '}
            <span className="tiny muted mono">
              {t('source.supportedThemesCount', { n: count })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CitingClaims({ claims }: { claims: ClaimRow[] }) {
  const { t, lang } = useI18n();
  if (claims.length === 0) {
    return (
      <EmptyState>
        <p>{t('source.citingEmpty')}</p>
        <Link className="tiny" to="/knowledge">
          {t('source.citingEmptyHint')}
        </Link>
      </EmptyState>
    );
  }
  return (
    <ul className="citing-list">
      {claims.map((c) => {
        const text =
          lang === 'zh' && c.claim_zh?.trim() ? c.claim_zh : c.claim;
        return (
          <li key={c.claim_id}>
            {(c.status === 'durable' || c.status === 'caveated') && (
              <ClaimPill status={c.status} />
            )}{' '}
            <Link to={`/knowledge#${c.claim_id}`}>{text}</Link>
          </li>
        );
      })}
    </ul>
  );
}

function filterLabel(f: LibraryFilter): string {
  const bits: string[] = [];
  if (f.collection) bits.push(f.collection);
  if (f.month) bits.push(f.month);
  if (f.status) bits.push(f.status);
  if (f.tag) bits.push(`#${f.tag}`);
  return bits.join(' · ');
}

function libraryListHref(f: LibraryFilter): string {
  const q = new URLSearchParams();
  if (f.collection) q.set('c', f.collection);
  if (f.month) q.set('m', f.month);
  if (f.status) q.set('status', f.status);
  if (f.tag) q.set('tag', f.tag);
  const s = q.toString();
  return s ? `/library?${s}` : '/library';
}

export default function SourceDetailPage() {
  const { t, lang } = useI18n();
  const { sha } = useParams<{ sha: string }>();
  const navigate = useNavigate();
  const { model } = useModel();
  const workQueue = useSourceWorkQueueOptional();
  // Bumped after a tag write so the detail (and its rebuilt tags) reloads.
  const [version, setVersion] = useState(0);
  const { detail, status } = useSourceDetail(sha, version);
  // Tab is URL-parameterized (?tab=memory) — shareable deep links, same
  // rule as the Library facets (design §5). Default is the full SOURCE
  // rendering (operator finding: the memory tab alone reads as excerpts).
  // Facet params (c/m/status/tag) are preserved for in-set prev/next.
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const tab: Tab =
    tabParam === 'memory' || tabParam === 'zh' || tabParam === 'summary'
      ? tabParam
      : 'source';
  const setTab = (next: Tab) => {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next === 'source') p.delete('tab');
        else p.set('tab', next);
        return p;
      },
      { replace: true },
    );
  };

  /** Continuous browse within the Library filter set the user entered with. */
  const browse = useMemo(() => {
    const empty: LibraryFilter = {
      collection: null,
      month: null,
      status: null,
      tag: null,
    };
    if (!sha) {
      return { order: [] as string[], idx: -1, filter: empty, label: '' };
    }
    const fromUrl = libraryFilterFromSearch(searchParams);
    let order: string[] = [];
    let filter = fromUrl;
    let label = '';
    if (model && libraryFilterActive(fromUrl)) {
      order = libraryBrowseOrder(model.sources, fromUrl);
      label = filterLabel(fromUrl);
    } else {
      const snap = loadLibraryNavSnapshot();
      if (snap?.order?.includes(sha)) {
        order = snap.order;
        filter = snap.filter ?? fromUrl;
        label = snap.label ?? filterLabel(filter);
      } else if (model) {
        order = libraryBrowseOrder(model.sources, empty);
      }
    }
    return {
      order,
      idx: order.indexOf(sha),
      filter,
      label,
    };
  }, [sha, searchParams, model]);

  const prevSha =
    browse.idx > 0 ? browse.order[browse.idx - 1] : null;
  const nextSha =
    browse.idx >= 0 && browse.idx < browse.order.length - 1
      ? browse.order[browse.idx + 1]
      : null;
  // Carry the active content tab across prev/next so reading summary/zh
  // stays on that tab when the neighbor has it; missing tabs fall back to
  // source once work status is known (see effect below).
  const browseTabExtra =
    tab === 'source' ? undefined : ({ tab } as Record<string, string>);
  const prevHref = prevSha
    ? librarySourcePath(prevSha, browse.filter, browseTabExtra)
    : null;
  const nextHref = nextSha
    ? librarySourcePath(nextSha, browse.filter, browseTabExtra)
    : null;

  // [ ] / ArrowLeft/Right — sequential browse when not typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el) {
        const tag = el.tagName;
        if (
          tag === 'INPUT' ||
          tag === 'TEXTAREA' ||
          tag === 'SELECT' ||
          el.isContentEditable
        ) {
          return;
        }
        if (el.closest('[data-omnibox-suppress]')) return;
      }
      if ((e.key === 'ArrowLeft' || e.key === '[') && prevHref) {
        e.preventDefault();
        navigate(prevHref);
      } else if ((e.key === 'ArrowRight' || e.key === ']') && nextHref) {
        e.preventDefault();
        navigate(nextHref);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [prevHref, nextHref, navigate]);
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  const [work, setWork] = useState<SourceWorkPayload | null>(null);
  /** Local enqueue-in-flight (not server job running — that lives in the queue). */
  const [busyTranslate, setBusyTranslate] = useState(false);
  const [busySummarize, setBusySummarize] = useState(false);
  const [workErrors, setWorkErrors] = useState<{
    translate?: string;
    summarize?: string;
  }>({});
  const [queueNote, setQueueNote] = useState<string | null>(null);
  // Source-grounded chat dock (not a jump to Ask). URL: ?chat=1 opens empty;
  // ?chat=<stem> resumes that session in-context.
  const chatParam = searchParams.get('chat');
  const chatOpen = chatParam != null && chatParam !== '';
  const resumeChat =
    chatParam && chatParam !== '1' && chatParam !== 'true' ? chatParam : null;
  const setChatOpen = (open: boolean, stem?: string | null) => {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (!open) p.delete('chat');
        else p.set('chat', stem && stem !== '1' ? stem : '1');
        return p;
      },
      { replace: true },
    );
  };

  const anchoredLines = useMemo(
    () =>
      new Set(
        (detail?.memory.units ?? [])
          .map((u) => u.line)
          .filter((l): l is number => l != null),
      ),
    [detail],
  );

  // Notes reference image paths relative to the REPO (READMEs) or the
  // VAULT (clipped attachments) — resolve through an ordered candidate
  // chain built from the note's source URL + vault path; the <img> error
  // handler walks it.
  // HOOKS RULE: this useMemo must stay ABOVE the status early-returns below
  // — a hook that only runs once `detail` is ready changes the hook count
  // between renders and React unmounts the whole app (the 2026-07-29
  // OpenChatCut white screen).
  const sourceUrl = detail?.source.url ?? undefined;
  const noteRelPath = detail?.source.rel_path ?? undefined;
  const imageSrcCandidates = useMemo(
    () => sourceImageCandidates(sourceUrl, noteRelPath),
    [sourceUrl, noteRelPath],
  );

  // Companion chips + English-ness only need URL/entities/body — safe hooks
  // above early returns (same rules as imageSrcCandidates).
  const companions = useMemo(
    () =>
      companionLinks(
        detail?.source.url,
        detail?.source.entities ?? [],
      ),
    [detail?.source.url, detail?.source.entities],
  );
  const bodyMarkdown = detail?.doc.markdown ?? '';
  const offerTranslate =
    !STATIC_MODE &&
    (work?.primarily_english ??
      (bodyMarkdown ? isPrimarilyEnglish(bodyMarkdown) : false));

  /** True once source-work for *this* sha has settled (ok or missing). */
  const [workReady, setWorkReady] = useState(false);

  useEffect(() => {
    // Drop prior article's artifacts so tab availability is not stale while
    // the next fetch is in flight (otherwise zh/summary may look present).
    setWork(null);
    setWorkReady(false);
    if (STATIC_MODE || !sha || status !== 'ready') {
      if (status === 'ready') setWorkReady(true);
      return;
    }
    let cancelled = false;
    fetchSourceWork(sha)
      .then((w) => {
        if (!cancelled) {
          setWork(w);
          setWorkReady(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setWork(null);
          setWorkReady(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sha, status, version]);

  // Prefer sticking to the URL tab (carried by prev/next). If this article
  // has no zh/summary artifact, fall back to the default source tab.
  useEffect(() => {
    if (!workReady) return;
    if (tab === 'zh' && !work?.has_zh) setTab('source');
    else if (tab === 'summary' && !work?.has_summary) setTab('source');
    // memory + source always exist as tabs
  }, [workReady, work?.has_zh, work?.has_summary, tab]);

  /** Merge so a finishing translate never wipes summary body (and vice versa). */
  const applyWork = (w: SourceWorkPayload, preferTab: 'zh' | 'summary' | null) => {
    setWork((prev) => ({
      work_rel: w.work_rel || prev?.work_rel || '',
      has_original: w.has_original ?? prev?.has_original ?? true,
      has_zh: Boolean(w.has_zh || prev?.has_zh),
      has_summary: Boolean(w.has_summary || prev?.has_summary),
      primarily_english: w.primarily_english ?? prev?.primarily_english ?? true,
      meta: w.meta ?? prev?.meta ?? null,
      zh: w.zh ?? prev?.zh ?? null,
      summary: w.summary ?? prev?.summary ?? null,
    }));
    if (preferTab === 'zh' && w.has_zh) setTab('zh');
    if (preferTab === 'summary' && w.has_summary) setTab('summary');
  };

  // While this article has a queue job running, poll work artifacts so tabs appear
  // even after navigating away and back.
  useEffect(() => {
    if (!sha || !workQueue) return;
    const mine = workQueue.items.filter(
      (i) =>
        i.sha256 === sha &&
        (i.status === 'queued' || i.status === 'running'),
    );
    if (mine.length === 0) return;
    let stop = false;
    const tick = () => {
      if (stop) return;
      fetchSourceWork(sha)
        .then((w) => {
          if (stop) return;
          applyWork(w, null);
        })
        .catch(() => {});
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [sha, workQueue?.items]);

  const runTranslate = async (force = false) => {
    if (!sha || busyTranslate) return;
    setBusyTranslate(true);
    setWorkErrors((e) => ({ ...e, translate: undefined }));
    setQueueNote(null);
    try {
      if (!workQueue) throw new Error('work queue unavailable');
      const titleHint =
        detail != null
          ? sourceDisplayTitle(detail.source)
          : sha;
      await workQueue.enqueue({
        sha256: sha,
        title: titleHint,
        translate: true,
        force,
      });
      setQueueNote(t('source.queuedOk'));
    } catch (e) {
      setWorkErrors((err) => ({
        ...err,
        translate: (e as Error).message,
      }));
    } finally {
      setBusyTranslate(false);
    }
  };

  const runSummarize = async (force = false) => {
    if (!sha || busySummarize) return;
    setBusySummarize(true);
    setWorkErrors((e) => ({ ...e, summarize: undefined }));
    setQueueNote(null);
    try {
      if (!workQueue) throw new Error('work queue unavailable');
      const titleHint =
        detail != null
          ? sourceDisplayTitle(detail.source)
          : sha;
      await workQueue.enqueue({
        sha256: sha,
        title: titleHint,
        summarize: true,
        force,
      });
      setQueueNote(t('source.queuedOk'));
    } catch (e) {
      setWorkErrors((err) => ({
        ...err,
        summarize: (e as Error).message,
      }));
    } finally {
      setBusySummarize(false);
    }
  };

  const jumpToLine = (line: number) => {
    setTab('source');
    setHighlightLine(line);
  };

  if (status === 'loading') {
    return <div className="portal-note">{t('common.loading')}</div>;
  }
  if (status === 'error') {
    return <div className="portal-note">{t('source.loadError')}</div>;
  }
  if (status === 'notFound' || !detail) {
    return (
      <>
        <div className="crumbs">
          <Link to={libraryListHref(browse.filter)}>
            {t('source.backToLibrary')}
          </Link>{' '}
          / {sha}
        </div>
        <EmptyState>
          <p>{t('source.notFound')}</p>
        </EmptyState>
      </>
    );
  }

  const { source, memory, citing_claims: citing, doc } = detail;
  const title = sourceDisplayTitle(source);

  return (
    <>
      {/* Single compact chrome row: prev | Library / title… | i/n | next */}
      <nav className="source-top-bar" aria-label={t('source.browseNav')}>
        {browse.order.length > 1 && browse.idx >= 0 ? (
          prevHref ? (
            <Link
              className="browse-step"
              to={prevHref}
              title={t('source.browsePrevHint')}
              aria-label={t('source.browsePrev')}
            >
              ←
            </Link>
          ) : (
            <span className="browse-step is-disabled" aria-hidden>
              ←
            </span>
          )
        ) : (
          <span className="browse-step is-spacer" aria-hidden />
        )}
        <div className="source-top-trail">
          <Link className="source-top-lib" to={libraryListHref(browse.filter)}>
            {t('source.backToLibrary')}
          </Link>
          <span className="source-top-sep" aria-hidden>
            /
          </span>
          <span className="source-top-title" title={title}>
            {title}
          </span>
        </div>
        {browse.order.length > 1 && browse.idx >= 0 ? (
          <>
            <span
              className="source-top-pos mono tiny muted"
              title={
                browse.label
                  ? `${browse.label} · [ ] / ← →`
                  : '[ ] / ← →'
              }
            >
              {t('source.browsePosition', {
                i: browse.idx + 1,
                n: browse.order.length,
              })}
            </span>
            {nextHref ? (
              <Link
                className="browse-step"
                to={nextHref}
                title={t('source.browseNextHint')}
                aria-label={t('source.browseNext')}
              >
                →
              </Link>
            ) : (
              <span className="browse-step is-disabled" aria-hidden>
                →
              </span>
            )}
          </>
        ) : null}
      </nav>

      <div className="src-head">
        <h1 title={title}>{title}</h1>
        <StatusPill status={source.status} />
      </div>

      {queueNote && (
        <div className="source-work-busy" role="status">
          {queueNote}{' '}
          <Link to="/work-queue" className="tiny">
            {t('source.openQueue')} →
          </Link>
        </div>
      )}

      {(source.status === 'failed' || source.status === 'blocked') && (
        <div className="card warn source-failed">
          <p className="sm">
            <strong>{t('source.failedTitle')}</strong>{' '}
            {t(
              source.status === 'blocked'
                ? 'source.failedBlockedBody'
                : 'source.failedBody',
              { attempts: source.fail_count },
            )}
          </p>
          {source.last_reason && (
            <p className="tiny muted" style={{ marginBottom: 0 }}>
              {t('source.failedReason')}{' '}
              <span className="mono">{source.last_reason}</span>
            </p>
          )}
        </div>
      )}

      <dl className="meta-rows">
        {source.url && (
          <>
            <dt>{t('source.url')}</dt>
            <dd>
              <a className="mono tiny" href={source.url} target="_blank" rel="noreferrer">
                {source.url}
              </a>
            </dd>
          </>
        )}
        {companions.length > 0 && (
          <>
            <dt>{t('source.companions')}</dt>
            <dd className="companion-row">
              {companions.map((c) => (
                <a
                  key={c.href}
                  className="companion-chip"
                  href={c.href}
                  target="_blank"
                  rel="noreferrer"
                  title={c.title}
                >
                  {c.label} ↗
                </a>
              ))}
            </dd>
          </>
        )}
        {source.date && (
          <>
            <dt>{t('source.date')}</dt>
            <dd className="mono tiny">{source.date}</dd>
          </>
        )}
        {/* Origin (collection) is derived from the intake path, which is
            redacted on the static site — hide it rather than mislabel. */}
        {!STATIC_MODE && (
          <>
            <dt>{t('source.origin')}</dt>
            <dd className="tiny">{t(`library.${collectionOf(source)}`)}</dd>
          </>
        )}
        {source.rel_path && (
          <>
            <dt>{t('source.location')}</dt>
            <dd className="mono tiny">{source.rel_path}</dd>
          </>
        )}
        {(!STATIC_MODE ||
          (source.tags ?? []).length +
            (source.tags_inferred ?? []).length +
            (source.tags_implied ?? []).length >
            0) && (
          <>
            <dt>{t('tags.title')}</dt>
            <SourceTags
              source={source}
              onChanged={(newSha) => {
                // A queued note's content hash changes on edit — follow the
                // row to its new sha instead of 404ing on the old route.
                if (newSha && newSha !== sha) {
                  navigate(`/library/${newSha}`, { replace: true });
                } else {
                  setVersion((v) => v + 1);
                }
              }}
            />
          </>
        )}
        {(source.entities ?? []).length > 0 && (
          <>
            <dt>{t('entities.title')}</dt>
            <dd>
              {(source.entities ?? []).map((id) => (
                <span key={id} className="entity-chip">
                  <Link to={`/entity/${encodeURIComponent(id)}`}>@{id}</Link>
                  {entityUrl(id) && (
                    <a
                      href={entityUrl(id)!}
                      target="_blank"
                      rel="noreferrer"
                      title={entityUrl(id)!}
                      className="entity-out"
                    >
                      ↗
                    </a>
                  )}
                </span>
              ))}
            </dd>
          </>
        )}
        {source.last_run_id && (
          <>
            <dt>{t('source.lastRun')}</dt>
            <dd className="mono tiny">{source.last_run_id}</dd>
          </>
        )}
        {source.fail_count > 0 && (
          <>
            <dt>{t('source.failCount')}</dt>
            <dd className="mono tiny">{source.fail_count}</dd>
          </>
        )}
      </dl>

      {!STATIC_MODE && (
        <div className="source-actions">
          {offerTranslate && (
            <button
              type="button"
              className="action-btn"
              disabled={busyTranslate}
              onClick={() => runTranslate(!!work?.has_zh)}
            >
              {busyTranslate
                ? t('source.translating')
                : work?.has_zh
                  ? t('source.retranslate')
                  : t('source.translate')}
            </button>
          )}
          <button
            type="button"
            className="action-btn"
            disabled={busySummarize}
            onClick={() => runSummarize(!!work?.has_summary)}
          >
            {busySummarize
              ? t('source.summarizing')
              : work?.has_summary
                ? t('source.resummarize')
                : t('source.summarize')}
          </button>
          <button
            type="button"
            className={`action-btn${chatOpen ? ' active' : ''}`}
            onClick={() => setChatOpen(!chatOpen)}
          >
            {t('source.chatOnThis')}
          </button>
          {work?.work_rel && (
            <span className="tiny muted mono" title={work.work_rel}>
              {t('source.workDir')}: {work.work_rel}
            </span>
          )}
          {workErrors.translate && (
            <span className="fail-note">
              {t('source.translate')}: {workErrors.translate}
            </span>
          )}
          {workErrors.summarize && (
            <span className="fail-note">
              {t('source.summarize')}: {workErrors.summarize}
            </span>
          )}
        </div>
      )}

      <div className="grid two-col">
        {/* main column: Memory | Source tabs. On the published static site the
            evidence layer + full markdown aren't shipped (copyright), so show a
            read-the-original note instead of the tabs' empty/remediation UI. */}
        {STATIC_MODE ? (
          <div>
            <EmptyState>
              <p>{source.url ? t('source.staticLite') : t('source.staticLiteNoUrl')}</p>
            </EmptyState>
          </div>
        ) : (
        <div>
          <div className="tab-row">
            <button
              type="button"
              className={tab === 'source' ? 'active' : ''}
              onClick={() => setTab('source')}
            >
              {t('source.tabSource')} <span className="mono muted tiny">md</span>
            </button>
            {work?.has_zh && (
              <button
                type="button"
                className={tab === 'zh' ? 'active' : ''}
                onClick={() => setTab('zh')}
              >
                {t('source.tabZh')}
              </button>
            )}
            {work?.has_summary && (
              <button
                type="button"
                className={tab === 'summary' ? 'active' : ''}
                onClick={() => setTab('summary')}
              >
                {t('source.tabSummary')}
              </button>
            )}
            <button
              type="button"
              className={tab === 'memory' ? 'active' : ''}
              onClick={() => setTab('memory')}
            >
              {t('source.tabMemory')}{' '}
              <span className="muted">
                (
                {t('source.tabMemoryCounts', {
                  cards: memory.cards.length,
                  units: memory.units.length,
                })}
                )
              </span>
            </button>
          </div>

          {tab === 'memory' && (
            <>
              {memory.cards.length > 0 && (
                <>
                  <h3>{t('source.cardsTitle')}</h3>
                  <p className="tiny muted">{t('source.cardsHint')}</p>
                </>
              )}
              {memory.cards.map((card, i) => {
                const title =
                  lang === 'zh' && card.title_zh?.trim()
                    ? card.title_zh
                    : card.title;
                const content =
                  lang === 'zh' && card.content_zh?.trim()
                    ? card.content_zh
                    : card.content;
                return (
                  <div className="card mem-card" key={card.id ?? `c${i}`}>
                    <div className="mem-title">{title}</div>
                    <p>{content}</p>
                  </div>
                );
              })}
              {memory.cards.length === 0 && memory.units.length === 0 && (
                <EmptyState>
                  <p>
                    {memory.evidence_available
                      ? t('source.noMemory')
                      : t('source.evidenceMissing')}
                  </p>
                </EmptyState>
              )}
              {memory.units.length > 0 && (
                <div className="section">
                  <h3>{t('source.groundedUnits')}</h3>
                  <p className="tiny muted">{t('source.unitsHint')}</p>
                  {memory.units.map((unit) => (
                    <div className="unit-row" key={unit.unit_id}>
                      <blockquote>“{unit.quote}”</blockquote>
                      {unit.line != null ? (
                        <button
                          type="button"
                          className="line-anchor"
                          onClick={() => jumpToLine(unit.line as number)}
                        >
                          L{unit.line} →
                        </button>
                      ) : (
                        <span className="tiny muted">
                          {t('source.unitNoLine')}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'source' && (
            <>
              {doc.error && (
                <div className="doc-note mono tiny">
                  {t('source.docError', { error: doc.error })}
                </div>
              )}
              {!doc.error && doc.markdown == null && (
                <EmptyState>
                  <p>{t('source.docEmpty')}</p>
                </EmptyState>
              )}
              {doc.markdown != null && (
                <>
                  <MarkdownView
                    markdown={doc.markdown}
                    anchoredLines={anchoredLines}
                    highlightLine={highlightLine}
                    frontmatterLabel={t('source.frontmatter')}
                    imageSrcCandidates={imageSrcCandidates}
                  />
                  {doc.truncated && (
                    <div className="doc-note tiny muted">
                      {t('source.docTruncated')}
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {tab === 'zh' && (
            <>
              {work?.zh ? (
                <MarkdownView
                  markdown={work.zh}
                  gutter={false}
                  frontmatterLabel={t('source.frontmatter')}
                />
              ) : (
                <EmptyState>
                  <p>{t('source.zhEmpty')}</p>
                </EmptyState>
              )}
            </>
          )}

          {tab === 'summary' && (
            <>
              {work?.summary ? (
                <MarkdownView
                  markdown={work.summary}
                  gutter={false}
                  frontmatterLabel={t('source.frontmatter')}
                />
              ) : (
                <EmptyState>
                  <p>{t('source.summaryEmpty')}</p>
                </EmptyState>
              )}
            </>
          )}
        </div>
        )}

        {/* right rail: neighborhood graph + citing claims. The neighborhood
            subgraph isn't pre-baked for the static site — hide it there. */}
        <div>
          {!STATIC_MODE && (
            <div className="card">
              <h3 style={{ marginBottom: '0.6rem' }}>{t('source.neighborhood')}</h3>
              <KnowledgeGraph scope="neighborhood" id={source.sha256} height={360} />
              <div className="graph-caption">{t('source.neighborhoodCaption')}</div>
            </div>
          )}
          <SupportedThemes citing={citing} />
          <div className="card">
            <h3 style={{ marginBottom: '0.6rem' }}>{t('source.citingClaims')}</h3>
            <CitingClaims claims={citing} />
          </div>
        </div>
      </div>

      {!STATIC_MODE && (
        <SourceChatPanel
          sha={source.sha256}
          title={title}
          cardCount={memory.cards.length}
          unitCount={memory.units.length}
          claimCount={citing.length}
          open={chatOpen}
          resumeChat={resumeChat}
          onClose={() => setChatOpen(false)}
        />
      )}
    </>
  );
}
