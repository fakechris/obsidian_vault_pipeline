/** Source-work queue manager — per-article translate/summarize jobs.
 * Serial across articles; parallel tasks within one article. */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageHelp } from '../components/ui';
import { useI18n } from '../i18n';
import { useSourceWorkQueue } from '../lib/sourceWorkQueue';
import type { SourceWorkQueueItem, WorkTaskState } from '../lib/api';
import {
  computeWorkQueueEta,
  formatClock,
  formatDurationSec,
} from '../lib/workQueueEta';

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
  nowSec,
}: {
  item: SourceWorkQueueItem;
  onUp: () => void;
  onDown: () => void;
  onCancel: () => void;
  onRemove: () => void;
  canUp: boolean;
  canDown: boolean;
  nowSec: number;
}) {
  const { t } = useI18n();
  const title = item.title?.trim() || item.sha256.slice(0, 12) + '…';
  const isQueued = item.status === 'queued';
  const isRunning = item.status === 'running';
  const isTerminal =
    item.status === 'done' ||
    item.status === 'failed' ||
    item.status === 'cancelled';

  let timing: string | null = null;
  if (isRunning && item.started_at != null) {
    timing = t('workq.itemRunningFor', {
      elapsed: formatDurationSec(Math.max(0, nowSec - item.started_at)),
    });
  } else if (isTerminal && item.finished_at != null) {
    const when = formatClock(item.finished_at);
    if (item.started_at != null && item.finished_at > item.started_at) {
      const dur = formatDurationSec(item.finished_at - item.started_at);
      timing = `${t('workq.itemFinished', { when })} · ${t('workq.itemDuration', { dur })}`;
    } else {
      timing = t('workq.itemFinished', { when });
    }
  }

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
        {timing && (
          <>
            <span>·</span>
            <span className="workq-timing">{timing}</span>
          </>
        )}
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

function EtaPanel({
  items,
  workerActive,
  nowSec,
}: {
  items: SourceWorkQueueItem[];
  workerActive: boolean | null;
  nowSec: number;
}) {
  const { t } = useI18n();
  const eta = useMemo(() => computeWorkQueueEta(items, nowSec), [items, nowSec]);
  const remaining = items.filter(
    (i) => i.status === 'queued' || i.status === 'running',
  ).length;

  return (
    <section className="workq-eta" aria-live="polite">
      <h3 className="workq-eta-title">{t('workq.etaTitle')}</h3>
      <div className="workq-eta-grid">
        {eta.sampleCount > 0 ? (
          <p className="workq-eta-line">
            <strong>
              {t('workq.etaAvg', { avg: formatDurationSec(eta.avgSec) })}
            </strong>
            <span className="muted">
              {' '}
              · {t('workq.etaMedian', { median: formatDurationSec(eta.medianSec) })}
              {' · '}
              {t('workq.etaSamples', { n: eta.sampleCount })}
            </span>
          </p>
        ) : (
          <p className="workq-eta-line muted">{t('workq.etaWarmup')}</p>
        )}

        <p className="workq-eta-line">
          {t('workq.etaThroughput', {
            n15: eta.doneLast15m,
            n60: eta.doneLastHour,
          })}
        </p>

        {eta.lastFinishedAt != null && (
          <p className="workq-eta-line">
            {eta.lastStatus === 'failed'
              ? t('workq.etaLastFailed', {
                  when: formatClock(eta.lastFinishedAt),
                  dur: formatDurationSec(eta.lastDurationSec ?? 0),
                })
              : t('workq.etaLast', {
                  when: formatClock(eta.lastFinishedAt),
                  dur: formatDurationSec(eta.lastDurationSec ?? 0),
                })}
            {eta.lastTitle && (
              <span className="muted">
                {' '}
                {t('workq.etaLastTitle', {
                  title:
                    eta.lastTitle.length > 48
                      ? eta.lastTitle.slice(0, 48) + '…'
                      : eta.lastTitle,
                })}
              </span>
            )}
          </p>
        )}

        {eta.runningElapsedSec != null && (
          <p className="workq-eta-line">
            {t('workq.etaRunning', {
              elapsed: formatDurationSec(eta.runningElapsedSec),
            })}
          </p>
        )}

        {workerActive === false && remaining > 0 && (
          <p className="workq-eta-line workq-eta-warn">{t('workq.etaNoWorker')}</p>
        )}

        {remaining === 0 ? (
          <p className="workq-eta-line workq-eta-emph">{t('workq.etaIdle')}</p>
        ) : eta.reliable && eta.etaSec != null && eta.etaAt != null ? (
          <p className="workq-eta-line workq-eta-emph">
            {t('workq.etaRemaining', {
              left: formatDurationSec(eta.etaSec),
              eta: formatClock(eta.etaAt),
            })}
          </p>
        ) : eta.sampleCount > 0 && eta.avgSec > 0 ? (
          <p className="workq-eta-line workq-eta-emph">
            {t('workq.etaRemainingShort', {
              left: formatDurationSec(eta.avgSec * Math.max(1, remaining)),
            })}
            <span className="muted"> · {t('workq.etaWarmup')}</span>
          </p>
        ) : (
          <p className="workq-eta-line muted">{t('workq.etaWarmup')}</p>
        )}
      </div>
    </section>
  );
}

export default function WorkQueuePage() {
  const { t } = useI18n();
  const { items, worker, reorder, cancel, remove, refresh } = useSourceWorkQueue();
  const [polledAt, setPolledAt] = useState(() => Date.now());
  // Tick so running elapsed / ETA countdown refresh without waiting for poll.
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));

  const queued = items.filter((i) => i.status === 'queued');
  const running = items.filter((i) => i.status === 'running');
  const history = items.filter(
    (i) =>
      i.status === 'done' || i.status === 'failed' || i.status === 'cancelled',
  );

  useEffect(() => {
    setPolledAt(Date.now());
  }, [items]);

  useEffect(() => {
    const id = window.setInterval(() => {
      setNowSec(Math.floor(Date.now() / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  const moveQueued = async (id: string, dir: -1 | 1) => {
    const ids = queued.map((i) => i.id);
    const idx = ids.indexOf(id);
    const j = idx + dir;
    if (idx < 0 || j < 0 || j >= ids.length) return;
    const next = [...ids];
    [next[idx], next[j]] = [next[j], next[idx]];
    await reorder(next);
  };

  const workerActive =
    worker == null ? null : worker.active_here === true;

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
      {worker && !worker.active_here && (
        <p className="tiny muted workq-worker-banner" role="status">
          {t('workq.workerElsewhere', {
            pid: worker.owner_pid ?? '—',
            here: worker.this_pid ?? '—',
          })}
        </p>
      )}
      {worker?.active_here && (
        <p className="tiny muted workq-worker-banner" role="status">
          {t('workq.workerHere', { pid: worker.this_pid ?? '—' })}
        </p>
      )}

      <EtaPanel items={items} workerActive={workerActive} nowSec={nowSec} />

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
                nowSec={nowSec}
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
                nowSec={nowSec}
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
                nowSec={nowSec}
              />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
