/** Today `/` — day browser: calendar + multi-dimension view of a chosen day
 * (captures, reads/packs, crystal claims with date-bearing runs, run rows).
 *
 * Default day is the projection's `model.date`. `?day=YYYY-MM-DD` selects a
 * past day (bookmarkable). Attention stays on the projection day only —
 * blocked/needs-content is a live operator queue, not a historical ledger.
 */
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import AttentionCard from '../components/AttentionCard';
import DayCalendar from '../components/DayCalendar';
import {
  AgeLabel,
  ClaimPill,
  EmptyState,
  ModelGate,
  PageHelp,
} from '../components/ui';
import { useI18n } from '../i18n';
import {
  attentionSources,
  dayView,
  isIsoDay,
  isMiscTheme,
  monthHeat,
  monthStart,
  type DayView,
} from '../lib/derive';
import type { IndexModel, RunRow } from '../lib/types';
import { useModel } from '../model';

const LIST_CAP = 12;

function DayStats({ view }: { view: DayView }) {
  const { t } = useI18n();
  return (
    <div className="grid stats">
      <div className="card">
        <div className="metric-label">{t('today.captured')}</div>
        <div className="metric-num">{view.captured}</div>
        <div className="metric-sub">
          {view.runs.length === 0 && view.captured === 0
            ? t('today.capturedEmpty')
            : `${t('today.pinboard')} ${view.capturedPinboard}`}
        </div>
      </div>
      <div className="card">
        <div className="metric-label">{t('today.read')}</div>
        <div className="metric-num">{view.read}</div>
        <div className="metric-sub">
          {t('today.unitsCards', {
            units: view.readUnits,
            cards: view.readCards,
          })}
        </div>
      </div>
      <div className="card">
        <div className="metric-label">{t('today.dayClaims')}</div>
        <div className="metric-num">{view.claims.length}</div>
        <div className="metric-sub">
          {t('today.durableCaveated', {
            durable: view.claimsDurable,
            caveated: view.claimsCaveated,
          })}
        </div>
      </div>
      <div className="card">
        <div className="metric-label">{t('today.dayPacks')}</div>
        <div className="metric-num">{view.packs.length}</div>
        <div className="metric-sub">
          {t('today.daySourcesDated', { n: view.sourcesDated.length })}
        </div>
      </div>
    </div>
  );
}

function RunsThatDay({ runs }: { runs: RunRow[] }) {
  const { t } = useI18n();
  if (runs.length === 0) return null;
  return (
    <div className="section">
      <h2>{t('today.runsTitle')}</h2>
      <div className="row-list">
        {runs.map((r, i) => (
          <div className="row" key={`${r.run_id}-${i}`}>
            <span className="mono">{r.run_id}</span>
            <span className="meta">
              {t('today.runLine', {
                ok: r.succeeded,
                fail: r.failed,
                ingested: r.ingested,
                blocked: r.blocked,
              })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SourceList({
  title,
  empty,
  items,
}: {
  title: string;
  empty: string;
  items: { sha: string; title: string; meta?: string }[];
}) {
  const shown = items.slice(0, LIST_CAP);
  const more = items.length - shown.length;
  return (
    <div className="section">
      <h2>{title}</h2>
      {items.length === 0 ? (
        <EmptyState>
          <p>{empty}</p>
        </EmptyState>
      ) : (
        <div className="row-list">
          {shown.map((it) => (
            <div className="row" key={it.sha}>
              <Link to={`/library/${it.sha}`}>{it.title}</Link>
              {it.meta && <span className="meta">{it.meta}</span>}
            </div>
          ))}
          {more > 0 && (
            <p className="tiny muted">
              +{more}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ClaimsThatDay({ view }: { view: DayView }) {
  const { t } = useI18n();
  const shown = view.claims.slice(0, LIST_CAP);
  const more = view.claims.length - shown.length;
  return (
    <div className="section">
      <h2>{t('today.crystalTitle')}</h2>
      <p className="muted tiny">{t('today.crystalNote')}</p>
      {view.claims.length === 0 ? (
        <EmptyState>
          <p>{t('today.crystalEmpty')}</p>
        </EmptyState>
      ) : (
        <>
          {shown.map((c) => (
            <div className="card" key={c.claim_id}>
              <div className="claim-top">
                {(c.status === 'durable' || c.status === 'caveated') && (
                  <ClaimPill status={c.status} />
                )}
                {c.strength && (
                  <span className="claim-meta">
                    {t('today.strength')}: {c.strength}
                  </span>
                )}
                {c.run_id && (
                  <span className="claim-meta mono tiny">{c.run_id}</span>
                )}
              </div>
              <p className="claim-text">{c.claim}</p>
              {c.theme && (
                <div className="claim-meta">
                  {isMiscTheme(c.theme) ? t('theme.unclassified') : c.theme}
                </div>
              )}
            </div>
          ))}
          {more > 0 && <p className="tiny muted">+{more}</p>}
        </>
      )}
    </div>
  );
}

function Attention({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const sources = attentionSources(model);
  const staleThemes = model.themes_stale_packs ?? 0;
  if (sources.length === 0 && staleThemes === 0) return null;
  return (
    <div className="section">
      <h2>{t('today.attentionTitle')}</h2>
      {staleThemes > 0 && (
        <div className="card attention-card sm">
          <p style={{ margin: 0 }}>{t('today.themesStale', { n: staleThemes })}</p>
        </div>
      )}
      {sources.map((s) => (
        <AttentionCard source={s} key={s.sha256} />
      ))}
    </div>
  );
}

function DayBody({
  model,
  view,
}: {
  model: IndexModel;
  view: DayView;
}) {
  const { t } = useI18n();
  const readItems = view.sourcesRead.map(({ source, pack }) => ({
    sha: source.sha256,
    title: source.title ?? source.sha256,
    meta: pack
      ? t('today.unitsCards', { units: pack.units, cards: pack.cards })
      : undefined,
  }));
  const datedItems = view.sourcesDated.map((s) => ({
    sha: s.sha256,
    title: s.title ?? s.sha256,
    meta: s.status,
  }));
  const packItems = view.packs.map((p) => ({
    sha: p.source_sha256 ?? p.pack_dir,
    title: p.title,
    meta: t('today.unitsCards', { units: p.units, cards: p.cards }),
    href: p.source_sha256 ? `/library/${p.source_sha256}` : null,
  }));

  return (
    <>
      <DayStats view={view} />
      {view.isProjectionDay && <Attention model={model} />}
      <RunsThatDay runs={view.runs} />
      <SourceList
        title={t('today.readTitle')}
        empty={t('today.readEmpty')}
        items={readItems}
      />
      <div className="section">
        <h2>{t('today.packsTitle')}</h2>
        {packItems.length === 0 ? (
          <EmptyState>
            <p>{t('today.packsEmpty')}</p>
          </EmptyState>
        ) : (
          <div className="row-list">
            {packItems.slice(0, LIST_CAP).map((it) => (
              <div className="row" key={it.sha}>
                {it.href ? (
                  <Link to={it.href}>{it.title}</Link>
                ) : (
                  <span>{it.title}</span>
                )}
                {it.meta && <span className="meta">{it.meta}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
      <SourceList
        title={t('today.sourcesDatedTitle')}
        empty={t('today.sourcesDatedEmpty')}
        items={datedItems}
      />
      <ClaimsThatDay view={view} />
    </>
  );
}

export default function TodayPage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  const [params, setParams] = useSearchParams();
  const paramDay = params.get('day');

  // Month cursor for the calendar (independent of selection while browsing).
  const [monthCursor, setMonthCursor] = useState<string | null>(null);

  const selected = useMemo(() => {
    if (!model) return null;
    if (isIsoDay(paramDay)) return paramDay;
    return model.date;
  }, [model, paramDay]);

  const view = useMemo(
    () => (model && selected ? dayView(model, selected) : null),
    [model, selected],
  );

  const heat = useMemo(() => {
    if (!model || !selected) return new Map<string, 0 | 1 | 2 | 3>();
    const cursor = monthCursor ?? monthStart(selected);
    return monthHeat(model, cursor);
  }, [model, selected, monthCursor]);

  const setDay = (day: string) => {
    const next = new URLSearchParams(params);
    if (model && day === model.date) next.delete('day');
    else next.set('day', day);
    setParams(next, { replace: true });
    setMonthCursor(monthStart(day));
  };

  return (
    <ModelGate loading={loading} error={error}>
      {model && selected && view && (
        <>
          <div className="today-layout">
            <div className="today-main">
              <h1 style={{ marginTop: '1rem' }}>
                {view.isProjectionDay
                  ? t('today.title')
                  : t('today.dayTitle', { day: selected })}
              </h1>
              <p className="muted sm" style={{ marginTop: '-2px' }}>
                <span className="mono">{selected}</span>
                {view.isProjectionDay && (
                  <>
                    {' · '}
                    <AgeLabel builtAt={model.built_at} />
                  </>
                )}
                {!view.isProjectionDay && (
                  <>
                    {' · '}
                    <button
                      type="button"
                      className="tiny linkish"
                      onClick={() => setDay(model.date)}
                    >
                      {t('today.calJumpToday')}
                    </button>
                  </>
                )}
              </p>
              <PageHelp>{t('today.help')}</PageHelp>
              {view.runs.length === 0 && view.heat === 0 && (
                <p className="muted tiny" style={{ marginTop: '-0.5rem' }}>
                  {t('today.noActivityDay')}
                </p>
              )}
              <DayBody model={model} view={view} />
            </div>
            <aside className="today-aside">
              <DayCalendar
                selected={selected}
                monthCursor={monthCursor ?? monthStart(selected)}
                heat={heat}
                projectionDay={model.date}
                onSelect={setDay}
                onMonthChange={setMonthCursor}
              />
              <p className="tiny muted day-cal-legend">{t('today.calLegend')}</p>
            </aside>
          </div>
        </>
      )}
    </ModelGate>
  );
}
