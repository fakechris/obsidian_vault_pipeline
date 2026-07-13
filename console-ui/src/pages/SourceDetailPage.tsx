/** Source detail `/library/:sha` — the three-layer drill-down (design §3.2):
 * header meta, [Memory | Source md] tabs, grounded units with `L<n> →`
 * anchors into the markdown reading view, and a right rail with the
 * neighborhood KnowledgeGraph and citing crystal claims. Data comes from
 * /api/source/:sha; the markdown is rendered client-side by the escape-first
 * renderer in lib/markdown.tsx (raw text never becomes HTML). */
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import KnowledgeGraph from '../components/KnowledgeGraph';
import { ClaimPill, EmptyState, StatusPill } from '../components/ui';
import { useI18n } from '../i18n';
import { fetchSourceDetail } from '../lib/api';
import { collectionOf } from '../lib/derive';
import { MarkdownView } from '../lib/markdown';
import type { ClaimRow, SourceDetail } from '../lib/types';

type Tab = 'memory' | 'source';

interface DetailState {
  detail: SourceDetail | null;
  status: 'loading' | 'ready' | 'notFound' | 'error';
}

function useSourceDetail(sha: string | undefined): DetailState {
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
  }, [sha]);

  return state;
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
  const { detail, status } = useSourceDetail(sha);
  // Tab is URL-parameterized (?tab=source) — shareable deep links, same
  // rule as the Library facets (design §5).
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get('tab') === 'source' ? 'source' : 'memory';
  const setTab = (next: Tab) => {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next === 'source') p.set('tab', 'source');
        else p.delete('tab');
        return p;
      },
      { replace: true },
    );
  };
  const [highlightLine, setHighlightLine] = useState<number | null>(null);

  const anchoredLines = useMemo(
    () =>
      new Set(
        (detail?.memory.units ?? [])
          .map((u) => u.line)
          .filter((l): l is number => l != null),
      ),
    [detail],
  );

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
        {source.date && (
          <>
            <dt>{t('source.date')}</dt>
            <dd className="mono tiny">{source.date}</dd>
          </>
        )}
        <dt>{t('source.origin')}</dt>
        <dd className="tiny">{t(`library.${collectionOf(source)}`)}</dd>
        {source.rel_path && (
          <>
            <dt>{t('source.location')}</dt>
            <dd className="mono tiny">{source.rel_path}</dd>
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

      <div className="grid two-col">
        {/* main column: Memory | Source tabs */}
        <div>
          <div className="tab-row">
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
            <button
              type="button"
              className={tab === 'source' ? 'active' : ''}
              onClick={() => setTab('source')}
            >
              {t('source.tabSource')} <span className="mono muted tiny">md</span>
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
        </div>

        {/* right rail: neighborhood graph + citing claims */}
        <div>
          <div className="card">
            <h3 style={{ marginBottom: '0.6rem' }}>{t('source.neighborhood')}</h3>
            <KnowledgeGraph scope="neighborhood" id={source.sha256} height={360} />
            <div className="graph-caption">{t('source.neighborhoodCaption')}</div>
          </div>
          <div className="card">
            <h3 style={{ marginBottom: '0.6rem' }}>{t('source.citingClaims')}</h3>
            <CitingClaims claims={citing} />
          </div>
        </div>
      </div>
    </>
  );
}
