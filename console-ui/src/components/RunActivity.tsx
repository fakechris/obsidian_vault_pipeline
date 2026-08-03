/** Run activity — the portal's tail -f + an absolute-time timeline.
 *
 * Operators were stuck with "FAILED 1d ago" and "no per-source activity yet"
 * with no wall-clock or phase context. This panel always leads with a
 * timeline (start → fail/complete phase → what was skipped → next due), then
 * the live per-source feed when one exists. */
import { useEffect, useState } from 'react';
import { useI18n, type MsgKey } from '../i18n';
import {
  buildRunTimeline,
  formatRunWhen,
  runActivity,
  type TimelineStep,
} from '../lib/derive';
import { STATIC_MODE, fetchSchedule, type ScheduleJob } from '../lib/api';
import type { RecentSource } from '../lib/types';
import { useModel } from '../model';
import { useNowTick } from './RunBanner';

function FeedRow({ item }: { item: RecentSource }) {
  const { t } = useI18n();
  const ok = item.status === 'ok';
  const when = formatRunWhen(item.at, { withSeconds: true });
  const label = ok
    ? t('activity.ok', { title: item.title, units: item.units, cards: item.cards })
    : item.reason
      ? t('activity.failed', { title: item.title, reason: item.reason })
      : t('activity.failedNoReason', { title: item.title });
  return (
    <li className={`run-activity-row ${ok ? 'ok' : 'failed'}`}>
      <span className="run-activity-mark" aria-hidden="true">
        {ok ? '✓' : '✗'}
      </span>
      <span className="run-activity-when mono tiny muted">{when || '—'}</span>
      <span className="run-activity-label">{label}</span>
    </li>
  );
}

const KIND_MARK: Record<TimelineStep['kind'], string> = {
  done: '●',
  fail: '✗',
  skip: '○',
  pending: '▷',
  running: '▶',
};

function Timeline({
  steps,
  t,
}: {
  steps: TimelineStep[];
  t: (k: MsgKey, v?: Record<string, string | number>) => string;
}) {
  if (steps.length === 0) return null;
  return (
    <ol className="run-timeline" aria-label={t('timeline.title')}>
      {steps.map((s) => (
        <li key={s.id} className={`run-timeline-step run-timeline-step--${s.kind}`}>
          <span className="run-timeline-mark" aria-hidden="true">
            {KIND_MARK[s.kind]}
          </span>
          <span className="run-timeline-when mono">
            {s.when ?? t('timeline.noClock')}
          </span>
          <span className="run-timeline-label">
            {t(s.labelKey as MsgKey, s.vars)}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** The panel body. `useNowTick` keeps relative labels honest while running. */
export default function RunActivity() {
  const { t } = useI18n();
  const { model } = useModel();
  useNowTick();
  const act = runActivity(model);
  const lr = model?.ops?.last_run ?? null;
  const [dailyJob, setDailyJob] = useState<ScheduleJob | null>(null);

  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    fetchSchedule()
      .then((s) => {
        if (!cancelled) setDailyJob(s.jobs.find((j) => j.id === 'daily') ?? null);
      })
      .catch(() => {
        if (!cancelled) setDailyJob(null);
      });
    return () => {
      cancelled = true;
    };
  }, [lr?.run_id, lr?.status, lr?.ended_at]);

  // Nothing to show at all (fresh vault, no heartbeat).
  if (act.status === null) return null;

  const steps = buildRunTimeline(lr, dailyJob);
  const started = formatRunWhen(lr?.started_at, { withSeconds: true });
  const ended = formatRunWhen(lr?.ended_at, { withSeconds: true });
  const startedAgo = (() => {
    const raw = lr?.started_at;
    if (!raw) return '';
    const mins = Math.max(0, Math.floor((Date.now() - Date.parse(raw)) / 60000));
    if (Number.isNaN(mins)) return '';
    if (mins < 1) return t('banner.agoJustNow');
    if (mins < 60) return t('banner.agoMinutes', { n: mins });
    if (mins < 60 * 24) return t('banner.agoHours', { n: Math.floor(mins / 60) });
    return t('banner.agoDays', { n: Math.floor(mins / (60 * 24)) });
  })();

  return (
    <div className="run-activity">
      <div className="run-activity-summary">
        <p className="run-activity-clock mono sm">
          {t('timeline.clockLine', {
            runId: lr?.run_id ?? '—',
            status: act.status ?? '—',
            started: started || '—',
            ended: ended || (act.running ? t('timeline.stillRunning') : '—'),
            ago: startedAgo || '—',
          })}
        </p>
      </div>

      <h3 className="run-timeline-heading tiny muted">{t('timeline.title')}</h3>
      <Timeline steps={steps} t={t} />

      {act.running ? (
        <>
          <div className="run-activity-head">
            {act.processedSoFar != null && act.totalPlanned != null ? (
              <span className="run-activity-fraction">
                {t('activity.running', {
                  done: act.processedSoFar,
                  total: act.totalPlanned,
                  pct: act.pct ?? 0,
                  ago: startedAgo,
                  when: started || '—',
                })}
              </span>
            ) : (
              <span className="run-activity-fraction">
                {t('banner.running', { ago: startedAgo, when: started || '—' })}
              </span>
            )}
          </div>
          {act.pct != null && (
            <div
              className="run-activity-bar"
              role="progressbar"
              aria-valuenow={act.pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="run-activity-bar-fill" style={{ width: `${act.pct}%` }} />
            </div>
          )}
          {act.current && (
            <p className="run-activity-current sm muted">
              {t('activity.current', { current: act.current })}
            </p>
          )}
        </>
      ) : (
        <p className="sm run-activity-idle">
          {act.status === 'failed' || act.status === 'aborted'
            ? t('activity.finishedFail', {
                started: started || '—',
                ended: ended || '—',
                ok: act.processed ?? 0,
                failed: act.failed ?? 0,
              })
            : act.status === 'completed'
              ? t('activity.finishedOk', {
                  started: started || '—',
                  ended: ended || '—',
                  ok: act.processed ?? 0,
                  failed: act.failed ?? 0,
                })
              : t('activity.idle')}
          {act.error ? (
            <span className="run-activity-error"> — {act.error}</span>
          ) : null}
        </p>
      )}

      <h3 className="run-timeline-heading tiny muted">{t('activity.feedTitle')}</h3>
      {act.recent.length === 0 ? (
        <p className="sm muted">
          {act.status === 'failed' || act.status === 'aborted'
            ? t('activity.emptyAfterFail')
            : act.running
              ? t('activity.emptyRunning')
              : t('activity.empty')}
        </p>
      ) : (
        <ul className="run-activity-feed">
          {act.recent.map((item) => (
            <FeedRow item={item} key={`${item.seq}-${item.at}`} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** Section wrapper for the System page (a titled panel). */
export function RunActivitySection() {
  const { t } = useI18n();
  const { model } = useModel();
  if (runActivity(model).status === null) return null;
  return (
    <div className="section">
      <h2>{t('activity.title' as MsgKey)}</h2>
      <RunActivity />
    </div>
  );
}
