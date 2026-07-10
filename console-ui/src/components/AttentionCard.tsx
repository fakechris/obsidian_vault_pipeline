/** AttentionCard — one blocked / needs-content source with the
 * why-it-matters line and the action link into its detail page (design §7).
 * Shared by Today (US1/US6) and System (B5 §b). */
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n';
import type { SourceRow } from '../lib/types';
import { StatusPill } from './ui';

export default function AttentionCard({ source }: { source: SourceRow }) {
  const { t } = useI18n();
  return (
    <div className="card warning">
      <div className="attention-title">
        <StatusPill status={source.status} />
        <strong>
          <Link to={`/library/${source.sha256}`}>
            {source.title ?? source.sha256}
          </Link>
        </strong>
      </div>
      {source.last_reason && (
        <div className="attention-reason">{source.last_reason}</div>
      )}
      <p className="sm" style={{ marginBottom: '0.5rem' }}>
        {t('today.whyItMatters')}:{' '}
        {source.status === 'blocked'
          ? t('today.whyBlocked')
          : t('today.whyNeedsContent')}
      </p>
      <Link className="sm" to={`/library/${source.sha256}`}>
        {t('today.attentionAction')} →
      </Link>
    </div>
  );
}
