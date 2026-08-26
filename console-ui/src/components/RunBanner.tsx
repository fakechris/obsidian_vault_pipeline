/** Fixed top strip surfacing run-liveness (OVP2 observability P0). Rendered by
 * the Shell above every page. It reflects `.ovp/last-run.json`:
 *   green  — completed recently ("Last run: completed 2h ago · 8 read · 180 queued")
 *   amber  — stale (older than the schedule interval) or no runs yet
 *   red    — the last run FAILED / ABORTED (with the short error)
 * Clicking navigates to the System page.
 *
 * Age is computed client-side from started_at/ended_at + Date.now and ticks on
 * an interval, so it stays honest without refetching the model. It renders even
 * when the model is null/empty — a stalled vault is exactly when the operator
 * most needs to see it — so it never sits behind the model's loading gate. */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import {
  RETRY_WATCHDOG_MS,
  formatRunWhen,
  isRunningWithProgress,
  lastRunBanner,
  runActivity,
  runSignature,
  shouldClearRetry,
  type BannerLevel,
  type RetryPending,
  failingJobs,
  type FailingJob,
} from '../lib/derive';
import { STATIC_MODE, fetchSchedule, startRunNow, type ScheduleJob } from '../lib/api';
import { useModel } from '../model';
import RunActivity from './RunActivity';

/** Re-render tick so the age string advances. A minute is granular enough for
 * a wall-clock banner; the interval is cleared on unmount. */
export function useNowTick(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Banner level → the status-light color class the CSS already defines. */
const LEVEL_CLASS: Record<BannerLevel, string> = {
  ok: 'ok',
  stale: 'attention',
  failed: 'failed',
  none: 'muted',
};

export default function RunBanner() {
  const { t } = useI18n();
  const { model } = useModel();
  const navigate = useNavigate();
  const now = useNowTick();
  const [expanded, setExpanded] = useState(false);
  const [retryPending, setRetryPending] = useState<RetryPending | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [dailyJob, setDailyJob] = useState<ScheduleJob | null>(null);
  // The banner already fetched every job and threw all but `daily` away, so a
  // crystallize failing for days was visible ONLY on the hidden System page.
  const [failing, setFailing] = useState<FailingJob[]>([]);

  const banner = lastRunBanner(model, now);
  // Retry stays pending until the heartbeat actually moves the banner off
  // `failed` — the 202 alone doesn't mean the model caught up yet. Keyed on the
  // run SIGNATURE, not just the level: a retry that fails again leaves the level
  // at `failed` (same value → a level-keyed effect never re-fires), which used
  // to strand the button on "Starting…" forever with no error shown.
  const bannerLevel = banner.level;
  const signature = runSignature(banner);
  const retrying = retryPending !== null;
  useEffect(() => {
    if (!retryPending) return;
    if (shouldClearRetry(retryPending, banner, Date.now())) {
      setRetryPending(null);
      return;
    }
    // Re-arm on a schedule of its own: a child that dies before writing any
    // heartbeat moves neither the level nor the signature, and the operator
    // must never be left without a working control.
    const left = Math.max(0, RETRY_WATCHDOG_MS - (Date.now() - retryPending.atMs));
    const id = window.setTimeout(() => setRetryPending(null), left);
    return () => window.clearTimeout(id);
    // `banner` is re-derived every render; the signature + level are the parts
    // that actually decide, so they are the deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryPending, signature, bannerLevel]);

  // Schedule context for hover: next due / whether currently due. Soft-fail if
  // the endpoint is down so the banner still works offline/static.
  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    fetchSchedule()
      .then((s) => {
        if (cancelled) return;
        setDailyJob(s.jobs.find((j) => j.id === 'daily') ?? null);
        setFailing(failingJobs(s.jobs));
      })
      .catch(() => {
        if (cancelled) return;
        setDailyJob(null);
        setFailing([]);
      });
    return () => {
      cancelled = true;
    };
  }, [bannerLevel, banner.runId]);

  // The activity feed is worth expanding when there IS a run to show (a live
  // run, or a just-finished one whose feed is still on the heartbeat).
  const hasActivity = runActivity(model).status !== null;

  const ago = (): string => {
    const m = banner.ageMinutes;
    if (m == null) return '';
    if (m < 1) return t('banner.agoJustNow');
    if (m < 60) return t('banner.agoMinutes', { n: m });
    if (m < 60 * 24) return t('banner.agoHours', { n: Math.floor(m / 60) });
    return t('banner.agoDays', { n: Math.floor(m / (60 * 24)) });
  };

  const when = (): string => {
    // Prefer end clock for terminal runs; start for in-flight. Always show
    // absolute local wall time first (relative age is secondary in the string).
    const raw = banner.endedAt ?? banner.startedAt;
    return formatRunWhen(raw, { withSeconds: true }) || '—';
  };

  const shortError = (): string => {
    if (!banner.error) return '';
    const e = banner.error.length > 120
      ? `${banner.error.slice(0, 117)}…`
      : banner.error;
    return ` — ${e}`;
  };

  const hoverTitle = (): string => {
    const base = t('banner.hover.base', {
      runId: banner.runId ?? '—',
      status: banner.status ?? 'none',
      started: formatRunWhen(banner.startedAt) || '—',
      ended: formatRunWhen(banner.endedAt) || '—',
      error: banner.error ?? t('banner.hover.noError'),
    });
    if (!dailyJob) return base;
    const schedule = t('banner.hover.schedule', {
      lastRun: formatRunWhen(dailyJob.last_run) || t('auto.never'),
      lastStatus: dailyJob.last_status || '—',
      nextRun: dailyJob.enabled
        ? formatRunWhen(dailyJob.next_run) || '—'
        : t('auto.paused'),
      dueLine: dailyJob.due ? t('banner.hover.dueYes') : t('banner.hover.dueNo'),
    });
    return `${base}\n\n${schedule}`;
  };

  // A live run WITH a progress fraction (heartbeat wrote at least one
  // per-source update): show "18/90 (current…)" and a subtle bar instead of the
  // frozen "started 12m ago". A stale "running" (long past the interval) still
  // takes the stale branch above — a stuck run must not masquerade as progress.
  const withProgress = isRunningWithProgress(banner) && banner.level !== 'stale';
  const progressPct =
    withProgress && banner.totalPlanned! > 0
      ? Math.min(100, Math.round((banner.processedSoFar! / banner.totalPlanned!) * 100))
      : 0;

  let text: string;
  if (banner.level === 'none') {
    text = t('banner.none');
  } else if (banner.status === 'failed') {
    text = t('banner.failed', { ago: ago(), when: when(), error: shortError() });
  } else if (banner.status === 'aborted') {
    text = t('banner.aborted', { ago: ago(), when: when(), error: shortError() });
  } else if (banner.level === 'stale') {
    text = t('banner.stale', { ago: ago(), when: when() });
  } else if (withProgress) {
    const params = {
      done: banner.processedSoFar!,
      total: banner.totalPlanned!,
      ago: ago(),
      when: when(),
    };
    text = banner.current
      ? t('banner.runningProgress', { ...params, current: banner.current })
      : t('banner.runningProgressNoCurrent', params);
  } else if (banner.status === 'running') {
    text = t('banner.running', { ago: ago(), when: when() });
  } else if (banner.processed != null && banner.queuedAfter != null) {
    text = t('banner.completedCounts', {
      ago: ago(),
      when: when(),
      read: banner.processed,
      queued: banner.queuedAfter,
    });
  } else {
    text = t('banner.completed', { ago: ago(), when: when() });
  }

  const level = LEVEL_CLASS[banner.level];

  return (
    <div className={`run-banner-wrap ${level}`}>
      {failing.length > 0 && (
        <div className="run-banner-failing">
          {failing.map((j) => (
            <button
              key={j.id}
              type="button"
              className="run-banner-failing-item"
              onClick={() => navigate('/system')}
              title={j.reason ?? undefined}
            >
              {t('banner.jobFailing', {
                id: j.id,
                streak: j.streak,
                when: formatRunWhen(j.lastRun) || '—',
              })}
              {j.reason ? ` — ${j.reason}` : ''}
            </button>
          ))}
        </div>
      )}
      <div className="run-banner-bar">
        <button
          type="button"
          className={`run-banner ${level}`}
          onClick={() => navigate('/system')}
          title={hoverTitle()}
          aria-label={text}
        >
          <span className="run-banner-dot" />
          <span className="run-banner-text">{text}</span>
          {withProgress && (
            <span
              className="run-banner-progress"
              role="progressbar"
              aria-valuenow={progressPct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span
                className="run-banner-progress-fill"
                style={{ width: `${progressPct}%` }}
              />
            </span>
          )}
        </button>
        {/* Failed run → retry on the spot: the manual-run endpoint reruns
            today's job under the full overlap protection (heartbeat + slot +
            dispatch lock). The banner flips to "running" via the heartbeat
            as soon as the child starts. */}
        {banner.level === 'failed' && (
          <button
            type="button"
            className="run-banner-retry"
            disabled={retrying}
            onClick={() => {
              setRetryError(null);
              setRetryPending({ from: signature, atMs: Date.now() });
              startRunNow('daily')
                .then(() => {
                  // Revalidate soon so the banner flips to "running" via the
                  // heartbeat instead of sitting on the stale failed state
                  // for the idle poll interval. `retrying` stays true until
                  // the banner actually leaves `failed` (effect below).
                  window.setTimeout(
                    () => window.dispatchEvent(new Event('ovp:model-refresh')),
                    1500,
                  );
                  window.setTimeout(
                    () => window.dispatchEvent(new Event('ovp:model-refresh')),
                    5000,
                  );
                })
                .catch((e: unknown) => {
                  // A rejected start (409 overlap, 500, offline) used to be
                  // swallowed — the operator saw the button flicker and nothing
                  // else. Surface it next to the control that caused it.
                  setRetryPending(null);
                  setRetryError(e instanceof Error ? e.message : String(e));
                });
            }}
          >
            {retrying ? t('banner.retrying') : t('banner.retry')}
          </button>
        )}
        {banner.level === 'failed' && retryError && (
          <span className="run-banner-retry-error" title={retryError}>
            {t('banner.retryFailed', { error: retryError })}
          </span>
        )}
        {/* Expand the live per-source activity feed inline, without leaving the
            current page — the operator's tail -f, one click away everywhere. */}
        {hasActivity && (
          <button
            type="button"
            className="run-banner-activity-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            title={t('banner.activityToggle')}
          >
            {t('banner.activityToggle')} {expanded ? '▾' : '▸'}
          </button>
        )}
      </div>
      {expanded && hasActivity && (
        <div className="run-banner-activity">
          <RunActivity />
        </div>
      )}
    </div>
  );
}
