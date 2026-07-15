/** Library `/library` — answers US2/US6 (design §3.2). In-page secondary
 * navigation over three dimensions (collection × month × status), month-
 * grouped rows, all state URL-parameterized: /library?c=&m=&status=. */
import { Link, useSearchParams } from 'react-router-dom';
import { STATIC_MODE } from '../lib/api';
import { AgeLabel, EmptyState, ModelGate, PageHelp, StatusPill } from '../components/ui';
import { useI18n, type MsgKey } from '../i18n';
import {
  collectionOf,
  countBy,
  filterSources,
  groupByMonth,
  monthOf,
  type Collection,
} from '../lib/derive';
import type { IndexModel, SourceRow } from '../lib/types';
import { useModel } from '../model';

const COLLECTIONS: Collection[] = ['clippings', 'pinboard', 'capture'];
// The four operator-facing statuses (design §3.2); other statuses
// (duplicate/failed/unparseable) still appear under "All".
const STATUS_FILTERS = ['queued', 'processed', 'blocked', 'needs_content'];

function collectionLabel(c: Collection): MsgKey {
  return `library.${c}` as MsgKey;
}

function LibraryBody({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();

  const collection = params.get('c') as Collection | null;
  const month = params.get('m');
  const status = params.get('status');

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const byCollection = countBy(model.sources, collectionOf);
  const byMonth = countBy(model.sources, monthOf);
  const months = [...byMonth.keys()].filter((m) => m !== '').sort().reverse();
  const byStatus = countBy(model.sources, (s: SourceRow) => s.status as string);

  // The QUEUED facet shows the LIVE 01-Raw backlog (`queued_live`, ticks
  // down per source during a run) — operators watch this number, and a
  // frozen projection value here reads as "stuck". Every other status stays
  // projection-derived. When the live count differs from the projection's
  // queued row count (mid-run, between refreshes), the pill shows both so
  // the number is live AND honest about the row list it filters:
  // "queued 53 · 175 snapshot".
  // The queued facet shows the LIVE 01-Raw backlog (ticks down per source);
  // every other status is projection-derived. One number, like every other
  // facet — the projection-vs-live nuance is an internal detail, not a second
  // figure on the chip.
  const statusCount = (st: string): number =>
    st === 'queued' ? (model.queued_live ?? (byStatus.get('queued') ?? 0)) : (byStatus.get(st) ?? 0);

  const filtered = filterSources(model.sources, {
    collection: collection && COLLECTIONS.includes(collection) ? collection : null,
    month,
    status,
  });
  const groups = groupByMonth(filtered);

  return (
    <div className="grid library">
      {/* facet rail */}
      <div>
        {/* Collection (clippings/pinboard/capture) is derived from the intake
            path, which is redacted on the published static site — hide the
            facet there rather than mislabel every source as clippings. */}
        {!STATIC_MODE && (
          <div className="facet-group">
            <h3>{t('library.collections')}</h3>
            <ul className="facet-list">
              <li>
                <button
                  type="button"
                  className={collection === null ? 'active' : ''}
                  onClick={() => setParam('c', null)}
                >
                  <span>{t('library.all')}</span>
                  <span className="count">{model.sources.length}</span>
                </button>
              </li>
              {COLLECTIONS.map((c) => (
                <li key={c}>
                  <button
                    type="button"
                    className={collection === c ? 'active' : ''}
                    onClick={() => setParam('c', collection === c ? null : c)}
                  >
                    <span>{t(collectionLabel(c))}</span>
                    <span className="count">{byCollection.get(c) ?? 0}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="facet-group">
          <h3>{t('library.byMonth')}</h3>
          <ul className="facet-list">
            {months.map((m) => (
              <li key={m}>
                <button
                  type="button"
                  className={month === m ? 'active' : ''}
                  onClick={() => setParam('m', month === m ? null : m)}
                >
                  <span className="mono">{m}</span>
                  <span className="count">{byMonth.get(m) ?? 0}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* list column */}
      <div>
        <div className="filter-row">
          <button
            type="button"
            className={status === null ? 'active' : ''}
            onClick={() => setParam('status', null)}
          >
            {t('library.statusAll')} ({model.sources.length})
          </button>
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              type="button"
              className={status === s ? 'active' : ''}
              onClick={() => setParam('status', status === s ? null : s)}
            >
              {t(`sourceStatus.${s}` as MsgKey)} ({statusCount(s)})
            </button>
          ))}
        </div>

        {groups.length === 0 && (
          <EmptyState>
            <p>{t('library.empty')}</p>
          </EmptyState>
        )}
        {groups.map((group) => (
          <div className="month-group" key={group.month || 'no-date'}>
            <div className="month-head">
              {group.month || t('library.noDate')}
            </div>
            <div className="row-list">
              {group.sources.map((s) => (
                <div className="row" key={s.sha256}>
                  <span className="row-main">
                    <StatusPill status={s.status} />
                    <Link to={`/library/${s.sha256}`}>
                      {s.title ?? s.sha256}
                    </Link>
                    {(s.status === 'blocked' || s.status === 'failed') &&
                      s.last_reason && (
                        <span className="fail-note">{s.last_reason}</span>
                      )}
                  </span>
                  <span className="meta">
                    {s.date ?? ''} · {t(collectionLabel(collectionOf(s)))}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function LibraryPage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  return (
    <ModelGate loading={loading} error={error}>
      {model && (
        <>
          <h1 style={{ marginTop: '1rem' }}>{t('library.title')}</h1>
          <p className="muted sm" style={{ marginTop: '-2px' }}>
            <AgeLabel builtAt={model.built_at} />
          </p>
          <PageHelp>{t('library.help')}</PageHelp>
          <LibraryBody model={model} />
        </>
      )}
    </ModelGate>
  );
}
