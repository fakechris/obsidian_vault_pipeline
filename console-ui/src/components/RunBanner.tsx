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
import { isRunningWithProgress, lastRunBanner, type BannerLevel } from '../lib/derive';
import { useModel } from '../model';

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

  const banner = lastRunBanner(model, now);

  const ago = (): string => {
    const m = banner.ageMinutes;
    if (m == null) return '';
    if (m < 1) return t('banner.agoJustNow');
    if (m < 60) return t('banner.agoMinutes', { n: m });
    if (m < 60 * 24) return t('banner.agoHours', { n: Math.floor(m / 60) });
    return t('banner.agoDays', { n: Math.floor(m / (60 * 24)) });
  };

  const shortError = (): string => {
    if (!banner.error) return '';
    const e = banner.error.length > 120
      ? `${banner.error.slice(0, 117)}…`
      : banner.error;
    return ` — ${e}`;
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
    text = t('banner.failed', { ago: ago(), error: shortError() });
  } else if (banner.status === 'aborted') {
    text = t('banner.aborted', { ago: ago(), error: shortError() });
  } else if (banner.level === 'stale') {
    text = t('banner.stale', { ago: ago() });
  } else if (withProgress) {
    const params = {
      done: banner.processedSoFar!,
      total: banner.totalPlanned!,
      ago: ago(),
    };
    text = banner.current
      ? t('banner.runningProgress', { ...params, current: banner.current })
      : t('banner.runningProgressNoCurrent', params);
  } else if (banner.status === 'running') {
    text = t('banner.running', { ago: ago() });
  } else if (banner.processed != null && banner.queuedAfter != null) {
    text = t('banner.completedCounts', {
      ago: ago(),
      read: banner.processed,
      queued: banner.queuedAfter,
    });
  } else {
    text = t('banner.completed', { ago: ago() });
  }

  const level = LEVEL_CLASS[banner.level];

  return (
    <button
      type="button"
      className={`run-banner ${level}`}
      onClick={() => navigate('/system')}
      title={t('banner.viewSystem')}
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
  );
}
