/** AttentionCard — one blocked / needs-content source with the
 * why-it-matters line, the action link into its detail page (design §7),
 * and an acknowledge action that hides this (source, status) pair until the
 * status changes. Shared by Today (US1/US6) and System (B5 §b). */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n';
import { ackAttention } from '../lib/api';
import type { SourceRow } from '../lib/types';
import { StatusPill } from './ui';

export default function AttentionCard({ source }: { source: SourceRow }) {
  const { t } = useI18n();
  // Optimistic hide: the ack persists server-side and the model overlay
  // filters it on every later load; locally the card just disappears.
  const [acked, setAcked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (acked) return null;

  const onAck = () => {
    setError(null);
    ackAttention(source.sha256, source.status)
      .then(() => setAcked(true))
      .catch((e: Error) => setError(e.message));
  };

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
      <div className="attention-actions">
        <Link className="sm" to={`/library/${source.sha256}`}>
          {t('today.attentionAction')} →
        </Link>
        <button
          type="button"
          className="attention-ack"
          title={t('attention.ackHint')}
          onClick={onAck}
        >
          {t('attention.ack')}
        </button>
      </div>
      {error && <p className="sm warn">{error}</p>}
    </div>
  );
}
