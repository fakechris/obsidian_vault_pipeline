/** Run activity — the portal's tail -f. Renders the live per-source feed from
 * the heartbeat `recent[]` ring plus the running fraction/percent bar and the
 * current source. Shown on the System page and expandable from the top
 * RunBanner. It reads the shared /api/model state (already polled every ~12s
 * while a run is `running`), so the feed refreshes at seconds-latency WITHOUT a
 * separate endpoint — the per-source heartbeat write is the mechanism, not the
 * coarse projection rebuild.
 *
 * On an idle/finished vault it shows the LAST run's feed until the next run, so
 * a completed/failed/aborted run's final outcomes stay diagnosable. */
import { useI18n, type MsgKey } from '../i18n';
import { runActivity } from '../lib/derive';
import type { RecentSource } from '../lib/types';
import { useModel } from '../model';
import { useNowTick } from './RunBanner';

function FeedRow({ item }: { item: RecentSource }) {
  const { t } = useI18n();
  const ok = item.status === 'ok';
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
      <span className="run-activity-label">{label}</span>
    </li>
  );
}

/** The panel body. `useNowTick` keeps the "started {ago}" string honest while
 * running without depending on a model refetch. */
export default function RunActivity() {
  const { t } = useI18n();
  const { model } = useModel();
  useNowTick(); // re-render so relative labels stay fresh
  const act = runActivity(model);

  // Nothing to show at all (fresh vault, no heartbeat).
  if (act.status === null) return null;

  const startedAgo = (() => {
    const started = model?.ops?.last_run?.started_at;
    if (!started) return '';
    const mins = Math.max(0, Math.floor((Date.now() - Date.parse(started)) / 60000));
    if (Number.isNaN(mins)) return '';
    if (mins < 1) return t('banner.agoJustNow');
    if (mins < 60) return t('banner.agoMinutes', { n: mins });
    if (mins < 60 * 24) return t('banner.agoHours', { n: Math.floor(mins / 60) });
    return t('banner.agoDays', { n: Math.floor(mins / (60 * 24)) });
  })();

  return (
    <div className="run-activity">
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
                })}
              </span>
            ) : (
              <span className="run-activity-fraction">{t('banner.running', { ago: startedAgo })}</span>
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
        <p className="sm muted run-activity-idle">
          {act.status === 'completed' || act.status === 'failed' || act.status === 'aborted'
            ? t('activity.finished', { ok: act.processed ?? 0, failed: act.failed ?? 0 })
            : t('activity.idle')}
          {act.error && ` — ${act.error}`}
        </p>
      )}

      {act.recent.length === 0 ? (
        <p className="sm muted">{t('activity.empty')}</p>
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
  // Hide the whole section only when there is genuinely no run to show.
  if (runActivity(model).status === null) return null;
  return (
    <div className="section">
      <h2>{t('activity.title' as MsgKey)}</h2>
      <RunActivity />
    </div>
  );
}
