/** Source-work queue manager — per-article translate/summarize jobs.
 * Serial across articles; parallel tasks within one article. */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageHelp } from '../components/ui';
import { useI18n } from '../i18n';
import { useSourceWorkQueue } from '../lib/sourceWorkQueue';
import type { SourceWorkQueueItem, WorkTaskState } from '../lib/api';

function taskLabel(t: WorkTaskState, yes: string, no: string): string {
  if (!t.wanted) return no;
  return `${yes}: ${t.status}${t.error ? ` (${t.error})` : ''}`;
}

function ItemRow({
  item,
  onUp,
  onDown,
  onCancel,
  onRemove,
  canUp,
  canDown,
}: {
  item: SourceWorkQueueItem;
  onUp: () => void;
  onDown: () => void;
  onCancel: () => void;
  onRemove: () => void;
  canUp: boolean;
  canDown: boolean;
}) {
  const { t } = useI18n();
  const title = item.title?.trim() || item.sha256.slice(0, 12) + '…';
  const isQueued = item.status === 'queued';
  const isRunning = item.status === 'running';
  const isTerminal =
    item.status === 'done' ||
    item.status === 'failed' ||
    item.status === 'cancelled';

  return (
    <li className={`workq-item status-${item.status}`}>
      <div className="workq-main">
        <span className={`workq-pill status-${item.status}`}>{item.status}</span>
        <Link className="workq-title" to={`/library/${encodeURIComponent(item.sha256)}`}>
          {title}
        </Link>
        <span className="tiny muted mono">{item.sha256.slice(0, 10)}…</span>
      </div>
      <div className="workq-tasks tiny">
        <span>{taskLabel(item.translate, t('workq.taskTranslate'), '—')}</span>
        <span>·</span>
        <span>{taskLabel(item.summarize, t('workq.taskSummarize'), '—')}</span>
      </div>
      <div className="workq-actions">
        {isQueued && (
          <>
            <button type="button" className="tiny" disabled={!canUp} onClick={onUp}>
              ↑
            </button>
            <button type="button" className="tiny" disabled={!canDown} onClick={onDown}>
              ↓
            </button>
            <button type="button" className="tiny" onClick={onCancel}>
              {t('workq.cancel')}
            </button>
          </>
        )}
        {isRunning && (
          <button type="button" className="tiny" onClick={onCancel}>
            {t('workq.cancel')}
          </button>
        )}
        {isTerminal && (
          <button type="button" className="tiny" onClick={onRemove}>
            {t('workq.remove')}
          </button>
        )}
      </div>
    </li>
  );
}

export default function WorkQueuePage() {
  const { t } = useI18n();
  const { items, reorder, cancel, remove, refresh } = useSourceWorkQueue();
  const [polledAt, setPolledAt] = useState(() => Date.now());

  const queued = items.filter((i) => i.status === 'queued');
  const running = items.filter((i) => i.status === 'running');
  const history = items.filter(
    (i) =>
      i.status === 'done' || i.status === 'failed' || i.status === 'cancelled',
  );

  useEffect(() => {
    setPolledAt(Date.now());
  }, [items]);

  const moveQueued = async (id: string, dir: -1 | 1) => {
    const ids = queued.map((i) => i.id);
    const idx = ids.indexOf(id);
    const j = idx + dir;
    if (idx < 0 || j < 0 || j >= ids.length) return;
    const next = [...ids];
    [next[idx], next[j]] = [next[j], next[idx]];
    await reorder(next);
  };

  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('workq.title')}</h1>
      <PageHelp>{t('workq.help')}</PageHelp>
      <div className="workq-toolbar">
        <button type="button" className="action-btn" onClick={() => refresh()}>
          {t('workq.refresh')}
        </button>
        <span className="tiny muted">
          {t('workq.counts', {
            running: running.length,
            queued: queued.length,
            history: history.length,
          })}
          {' · '}
          {t('workq.polled', {
            time: new Date(polledAt).toLocaleTimeString(),
          })}
        </span>
      </div>

      {running.length > 0 && (
        <section className="workq-section">
          <h3>{t('workq.running')}</h3>
          <ul className="workq-list">
            {running.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                canUp={false}
                canDown={false}
                onUp={() => {}}
                onDown={() => {}}
                onCancel={() => void cancel(item.id)}
                onRemove={() => {}}
              />
            ))}
          </ul>
        </section>
      )}

      <section className="workq-section">
        <h3>{t('workq.queued')}</h3>
        {queued.length === 0 ? (
          <p className="tiny muted">{t('workq.queuedEmpty')}</p>
        ) : (
          <ul className="workq-list">
            {queued.map((item, i) => (
              <ItemRow
                key={item.id}
                item={item}
                canUp={i > 0}
                canDown={i < queued.length - 1}
                onUp={() => void moveQueued(item.id, -1)}
                onDown={() => void moveQueued(item.id, 1)}
                onCancel={() => void cancel(item.id)}
                onRemove={() => {}}
              />
            ))}
          </ul>
        )}
      </section>

      {history.length > 0 && (
        <section className="workq-section">
          <h3>{t('workq.history')}</h3>
          <ul className="workq-list">
            {history.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                canUp={false}
                canDown={false}
                onUp={() => {}}
                onDown={() => {}}
                onCancel={() => {}}
                onRemove={() => void remove(item.id)}
              />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
