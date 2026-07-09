/** Today `/` — answers US1/US6 (design §3.1): what came in, what was read,
 * what crystallized, what needs me. All numbers derive from /api/model for
 * model.date.
 *
 * B1 deviation (documented): per-day claim attribution is not derivable
 * from ClaimRow (no date; run_id namespace differs from RunRow), so the
 * crystallized section renders as "Recent claims" — see lib/derive.ts. */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import AttentionCard from '../components/AttentionCard';
import { EmptyState, ModelGate, PageHelp } from '../components/ui';
import { useI18n } from '../i18n';
import {
  attentionSources,
  readToday,
  claimsSample,
  timeline,
  todayStats,
} from '../lib/derive';
import type { TodayStats } from '../lib/derive';
import type { IndexModel } from '../lib/types';
import { useModel } from '../model';

const RECENT_CLAIMS = 3;
const TIMELINE_DAYS = 7;

function Stats({ model, stats: s }: { model: IndexModel; stats: TodayStats }) {
  const { t } = useI18n();
  const { totals } = model;
  return (
    <div className="grid stats">
      <div className="card">
        <div className="metric-label">{t('today.captured')}</div>
        <div className="metric-num">{s.captured}</div>
        <div className="metric-sub">
          {s.todayRuns.length === 0
            ? t('today.capturedEmpty')
            : `${t('today.pinboard')} ${s.capturedPinboard}`}
        </div>
      </div>
      <div className="card">
        <div className="metric-label">{t('today.read')}</div>
        <div className="metric-num">{s.read}</div>
        <div className="metric-sub">
          {t('today.unitsCards', { units: s.readUnits, cards: s.readCards })}
        </div>
      </div>
      <div className="card">
        <div className="metric-label">{t('today.claims')}</div>
        <div className="metric-num">
          {totals.claims_durable + totals.claims_caveated}
        </div>
        <div className="metric-sub">
          {t('today.durableCaveated', {
            durable: totals.claims_durable,
            caveated: totals.claims_caveated,
          })}
        </div>
      </div>
      <div className="card">
        <div className="metric-label">{t('today.attention')}</div>
        <div className={s.attention > 0 ? 'metric-num warn' : 'metric-num'}>
          {s.attention}
        </div>
        <div className="metric-sub">
          {t('today.blockedNeeds', {
            blocked: totals.blocked,
            needs: totals.needs_content,
          })}
        </div>
      </div>
    </div>
  );
}

function Attention({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const sources = attentionSources(model);
  if (sources.length === 0) return null;
  return (
    <div className="section">
      <h2>{t('today.attentionTitle')}</h2>
      {sources.map((s) => (
        <AttentionCard source={s} key={s.sha256} />
      ))}
    </div>
  );
}

function RecentClaims({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const claims = claimsSample(model, RECENT_CLAIMS);
  if (claims.length === 0) return null;
  return (
    <div className="section">
      <h2>{t('today.claimsSample')}</h2>
      <p className="muted tiny">{t('today.claimsSampleNote')}</p>
      {claims.map((c) => (
        <div className="card" key={c.claim_id}>
          <div className="claim-top">
            <span className={`pill ${c.status}`}>{c.status}</span>
            {c.strength && (
              <span className="claim-meta">
                {t('today.strength')}: {c.strength}
              </span>
            )}
          </div>
          <p className="claim-text">{c.claim}</p>
          {c.theme && <div className="claim-meta">{c.theme}</div>}
        </div>
      ))}
    </div>
  );
}

function ReadToday({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const reads = readToday(model);
  return (
    <div className="section">
      <h2>{t('today.readToday')}</h2>
      {reads.length === 0 ? (
        <EmptyState>
          <p>{t('today.readEmpty')}</p>
        </EmptyState>
      ) : (
        <div className="row-list">
          {reads.map(({ source, pack }) => (
            <div className="row" key={source.sha256}>
              <Link to={`/library/${source.sha256}`}>
                {source.title ?? source.sha256}
              </Link>
              {pack && (
                <span className="meta">
                  {t('today.unitsCards', {
                    units: pack.units,
                    cards: pack.cards,
                  })}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Timeline({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const days = timeline(model, TIMELINE_DAYS);
  if (days.length === 0) return null;
  return (
    <div className="foot">
      {t('today.timeline')}:{' '}
      {days.map((d) => (
        <span key={d.date}>
          <span className="mono">{d.date.slice(5)}</span>{' '}
          {t('today.timelineRead', { n: d.read })}
          {d.captured > 0 && (
            <> · {t('today.timelineCaptured', { n: d.captured })}</>
          )}
          {' · '}
        </span>
      ))}
      <Link to="/system">{t('today.timelineAll')}</Link>
    </div>
  );
}

export default function TodayPage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  // todayStats walks every run/pack row — compute once per model, not on
  // every render and not once per consumer below.
  const stats = useMemo(() => (model ? todayStats(model) : null), [model]);
  return (
    <ModelGate loading={loading} error={error}>
      {model && stats && (
        <>
          <h1 style={{ marginTop: '1rem' }}>{t('today.title')}</h1>
          <p className="muted sm" style={{ marginTop: '-2px' }}>
            <span className="mono">{model.date}</span>
            {stats.dogfoodDay > 0 && (
              <> · {t('common.day')} {stats.dogfoodDay}</>
            )}
          </p>
          <PageHelp>{t('today.help')}</PageHelp>
          {stats.todayRuns.length === 0 && (
            <p className="muted tiny" style={{ marginTop: '-0.5rem' }}>
              {t('today.noRunsToday')}
            </p>
          )}
          <Stats model={model} stats={stats} />
          <Attention model={model} />
          <RecentClaims model={model} />
          <ReadToday model={model} />
          <Timeline model={model} />
        </>
      )}
    </ModelGate>
  );
}
