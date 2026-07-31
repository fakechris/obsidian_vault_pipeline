/** Source detail `/library/:sha` — the three-layer drill-down (design §3.2):
 * header meta, [Memory | Source md] tabs, grounded units with `L<n> →`
 * anchors into the markdown reading view, and a right rail with the
 * neighborhood KnowledgeGraph and citing crystal claims. Data comes from
 * /api/source/:sha; the markdown is rendered client-side by the escape-first
 * renderer in lib/markdown.tsx (raw text never becomes HTML). */
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import KnowledgeGraph from '../components/KnowledgeGraph';
import { ClaimPill, EmptyState, StatusPill } from '../components/ui';
import { useI18n } from '../i18n';
import {
  entityUrl,
  fetchSourceDetail,
  fetchSourceWork,
  fetchTags,
  postSourceSummarize,
  postSourceTags,
  postSourceTranslate,
  STATIC_MODE,
  type SourceWorkPayload,
} from '../lib/api';
import { collectionOf } from '../lib/derive';
import { isReactImeComposing } from '../lib/ime';
import { MarkdownView, sourceImageCandidates } from '../lib/markdown';
import { companionLinks, isPrimarilyEnglish } from '../lib/sourceLinks';
import type { ClaimRow, SourceDetail, SourceRow } from '../lib/types';

type Tab = 'memory' | 'source' | 'zh' | 'summary';

interface DetailState {
  detail: SourceDetail | null;
  status: 'loading' | 'ready' | 'notFound' | 'error';
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

function CitingClaims({ claims }: { claims: ClaimRow[] }) {
  const { t } = useI18n();
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
      {claims.map((c) => (
        <li key={c.claim_id}>
          {(c.status === 'durable' || c.status === 'caveated') && (
            <ClaimPill status={c.status} />
          )}{' '}
          <Link to={`/knowledge#${c.claim_id}`}>{c.claim}</Link>
        </li>
      ))}
    </ul>
  );
}

export default function SourceDetailPage() {
  const { t } = useI18n();
  const { sha } = useParams<{ sha: string }>();
  const navigate = useNavigate();
  // Bumped after a tag write so the detail (and its rebuilt tags) reloads.
  const [version, setVersion] = useState(0);
  const { detail, status } = useSourceDetail(sha, version);
  // Tab is URL-parameterized (?tab=memory) — shareable deep links, same
  // rule as the Library facets (design §5). Default is the full SOURCE
  // rendering (operator finding: the memory tab alone reads as excerpts).
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
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  const [work, setWork] = useState<SourceWorkPayload | null>(null);
  const [workBusy, setWorkBusy] = useState<'translate' | 'summarize' | null>(null);
  const [workError, setWorkError] = useState<string | null>(null);

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

  useEffect(() => {
    if (STATIC_MODE || !sha || status !== 'ready') return;
    let cancelled = false;
    fetchSourceWork(sha)
      .then((w) => {
        if (!cancelled) setWork(w);
      })
      .catch(() => {
        if (!cancelled) setWork(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sha, status, version]);

  const runTranslate = async (force = false) => {
    if (!sha) return;
    setWorkBusy('translate');
    setWorkError(null);
    try {
      const w = await postSourceTranslate(sha, force);
      setWork((prev) => ({
        work_rel: w.work_rel ?? prev?.work_rel ?? '',
        has_original: true,
        has_zh: w.has_zh ?? true,
        has_summary: w.has_summary ?? prev?.has_summary ?? false,
        primarily_english: true,
        meta: w.meta ?? prev?.meta,
        zh: w.zh ?? null,
        summary: w.summary ?? prev?.summary,
      }));
      setTab('zh');
    } catch (e) {
      setWorkError((e as Error).message);
    } finally {
      setWorkBusy(null);
    }
  };

  const runSummarize = async (force = false) => {
    if (!sha) return;
    setWorkBusy('summarize');
    setWorkError(null);
    try {
      const w = await postSourceSummarize(sha, force);
      setWork((prev) => ({
        work_rel: w.work_rel ?? prev?.work_rel ?? '',
        has_original: true,
        has_zh: w.has_zh ?? prev?.has_zh ?? false,
        has_summary: w.has_summary ?? true,
        primarily_english: prev?.primarily_english ?? offerTranslate,
        meta: w.meta ?? prev?.meta,
        zh: w.zh ?? prev?.zh,
        summary: w.summary ?? null,
      }));
      setTab('summary');
    } catch (e) {
      setWorkError((e as Error).message);
    } finally {
      setWorkBusy(null);
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
          <Link to="/library">{t('source.backToLibrary')}</Link> / {sha}
        </div>
        <EmptyState>
          <p>{t('source.notFound')}</p>
        </EmptyState>
      </>
    );
  }

  const { source, memory, citing_claims: citing, doc } = detail;
  const title = source.title ?? source.sha256;

  return (
    <>
      <div className="crumbs">
        <Link to="/library">{t('source.backToLibrary')}</Link> / {title}
      </div>

      <div className="src-head">
        <h1 style={{ marginBottom: '0.25rem' }}>{title}</h1>
        <StatusPill status={source.status} />
      </div>

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
          (source.tags ?? []).length + (source.tags_inferred ?? []).length > 0) && (
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
              disabled={workBusy != null}
              onClick={() => runTranslate(!!work?.has_zh)}
            >
              {workBusy === 'translate'
                ? t('source.translating')
                : work?.has_zh
                  ? t('source.retranslate')
                  : t('source.translate')}
            </button>
          )}
          <button
            type="button"
            className="action-btn"
            disabled={workBusy != null}
            onClick={() => runSummarize(!!work?.has_summary)}
          >
            {workBusy === 'summarize'
              ? t('source.summarizing')
              : work?.has_summary
                ? t('source.resummarize')
                : t('source.summarize')}
          </button>
          <Link
            className="action-btn action-btn-link"
            to={`/ask?focus=${encodeURIComponent(source.sha256)}`}
          >
            {t('source.chatOnThis')}
          </Link>
          {work?.work_rel && (
            <span className="tiny muted mono" title={work.work_rel}>
              {t('source.workDir')}: {work.work_rel}
            </span>
          )}
          {workError && <span className="fail-note">{workError}</span>}
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
              {memory.cards.map((card, i) => (
                <div className="card mem-card" key={`c${i}`}>
                  <div className="mem-title">{card.title}</div>
                  <p>{card.content}</p>
                </div>
              ))}
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
          <div className="card">
            <h3 style={{ marginBottom: '0.6rem' }}>{t('source.citingClaims')}</h3>
            <CitingClaims claims={citing} />
          </div>
        </div>
      </div>
    </>
  );
}
