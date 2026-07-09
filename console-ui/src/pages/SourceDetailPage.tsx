/** Source detail `/library/:sha` — B1 stub: meta card from SourceRow plus a
 * guided empty state. The real three-layer drill-down (memory cards,
 * grounded units, markdown reading view, neighborhood graph) is phase B2. */
import { Link, useParams } from 'react-router-dom';
import { EmptyState, ModelGate, StatusPill } from '../components/ui';
import { useI18n } from '../i18n';
import { collectionOf } from '../lib/derive';
import { useModel } from '../model';

export default function SourceDetailPage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  const { sha } = useParams<{ sha: string }>();

  const source = model?.sources.find((s) => s.sha256 === sha);

  return (
    <ModelGate loading={loading} error={error}>
      {model && (
        <>
          <div className="crumbs">
            <Link to="/library">{t('source.backToLibrary')}</Link> /{' '}
            {source?.title ?? sha}
          </div>
          {!source ? (
            <EmptyState>
              <p>{t('source.notFound')}</p>
            </EmptyState>
          ) : (
            <>
              <div className="src-head">
                <h1 style={{ marginBottom: '0.25rem' }}>
                  {source.title ?? source.sha256}
                </h1>
                <StatusPill status={source.status} />
              </div>
              <dl className="meta-rows">
                {source.url && (
                  <>
                    <dt>{t('source.url')}</dt>
                    <dd>
                      <a
                        className="mono tiny"
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                      >
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
                <dd className="tiny">
                  {t(`library.${collectionOf(source)}`)}
                </dd>
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
                {source.last_reason && (
                  <>
                    <dt>{t('source.lastReason')}</dt>
                    <dd className="mono tiny">{source.last_reason}</dd>
                  </>
                )}
              </dl>
              <div className="section">
                <div className="card">
                  <EmptyState>
                    <p>
                      <strong>{t('source.b2Empty')}</strong>
                    </p>
                    <p>{t('source.b2EmptyDetail')}</p>
                  </EmptyState>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </ModelGate>
  );
}
