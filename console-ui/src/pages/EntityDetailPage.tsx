/** Entity detail `/entity/:id` — one URL entity: its external link, the
 * sources that mention it, and the durable/caveated claims those sources
 * cite. The reverse of a SourceDetail entity chip. */
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { fetchEntity, type EntityDetail } from '../lib/api';
import { ClaimPill, EmptyState, ModelGate } from '../components/ui';
import { useI18n } from '../i18n';

export default function EntityDetailPage() {
  const { t } = useI18n();
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');

  useEffect(() => {
    if (!id) return;
    setStatus('loading');
    fetchEntity(id)
      .then((d) => {
        setDetail(d);
        setStatus('ready');
      })
      .catch(() => setStatus('error'));
  }, [id]);

  return (
    <ModelGate loading={status === 'loading'} error={status === 'error' ? t('entities.notFound') : null}>
      {detail && (
        <>
          <div className="crumbs">
            <Link to="/entities">{t('entities.title')}</Link> / @{detail.id}
          </div>
          <div className="src-head">
            <h1 style={{ marginBottom: '0.25rem' }}>@{detail.id}</h1>
          </div>
          <dl className="meta-rows">
            {detail.url && (
              <>
                <dt>{t('entities.url')}</dt>
                <dd>
                  <a className="mono tiny" href={detail.url} target="_blank" rel="noreferrer">
                    {detail.url}
                  </a>
                </dd>
              </>
            )}
            {detail.kind && (
              <>
                <dt>{t('entities.kind')}</dt>
                <dd className="tiny">{detail.kind}</dd>
              </>
            )}
          </dl>

          <div className="section">
            <h2>
              {t('entities.mentionedIn')} ({detail.sources.length})
            </h2>
            <div className="row-list">
              {detail.sources.map((s) => (
                <div className="row" key={s.sha256}>
                  <span className="row-main">
                    <Link to={`/library/${s.sha256}`}>{s.title ?? s.sha256}</Link>
                  </span>
                  <span className="meta">{s.date ?? ''}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="section">
            <h2>
              {t('entities.citingClaims')} ({detail.citing_claims.length})
            </h2>
            {detail.citing_claims.length === 0 ? (
              <EmptyState>
                <p>{t('entities.noClaims')}</p>
              </EmptyState>
            ) : (
              <ul className="citing-list">
                {detail.citing_claims.map((c) => (
                  <li key={c.claim_id}>
                    {(c.status === 'durable' || c.status === 'caveated') && (
                      <ClaimPill status={c.status} />
                    )}{' '}
                    <Link to={`/knowledge#${c.claim_id}`}>{c.claim}</Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </ModelGate>
  );
}
