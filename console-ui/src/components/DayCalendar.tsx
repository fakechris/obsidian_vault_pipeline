/** Month calendar for the Today page day-browser.
 *
 * Pure presentational: parent owns selected day + month cursor. Days with
 * vault activity render a heat mark so past runs are scannable at a glance.
 */
import { useMemo } from 'react';
import { useI18n } from '../i18n';
import { isIsoDay, monthStart } from '../lib/derive';

const WEEKDAYS_EN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WEEKDAYS_ZH = ['一', '二', '三', '四', '五', '六', '日'];

export interface DayCalendarProps {
  /** Currently selected day (YYYY-MM-DD). */
  selected: string;
  /** Month being browsed (YYYY-MM-01 or any day in that month). */
  monthCursor: string;
  /** Heat 0–3 per ISO day. */
  heat: Map<string, 0 | 1 | 2 | 3>;
  /** Projection "today" — highlighted as the build day. */
  projectionDay: string;
  onSelect: (day: string) => void;
  onMonthChange: (monthCursor: string) => void;
}

function daysInMonth(year: number, month0: number): number {
  return new Date(Date.UTC(year, month0 + 1, 0)).getUTCDate();
}

/** Monday=0 … Sunday=6 for a UTC calendar day. */
function mondayIndex(year: number, month0: number, day: number): number {
  const dow = new Date(Date.UTC(year, month0, day)).getUTCDay(); // 0=Sun
  return (dow + 6) % 7;
}

export default function DayCalendar({
  selected,
  monthCursor,
  heat,
  projectionDay,
  onSelect,
  onMonthChange,
}: DayCalendarProps) {
  const { t, lang } = useI18n();
  const weekdays = lang === 'zh' ? WEEKDAYS_ZH : WEEKDAYS_EN;

  const { year, month0, cells, label } = useMemo(() => {
    const base = isIsoDay(monthCursor) ? monthCursor : monthStart(selected);
    const y = Number(base.slice(0, 4));
    const m0 = Number(base.slice(5, 7)) - 1;
    const dim = daysInMonth(y, m0);
    const lead = mondayIndex(y, m0, 1);
    const cells: Array<{ day: string | null; n: number | null }> = [];
    for (let i = 0; i < lead; i += 1) cells.push({ day: null, n: null });
    for (let d = 1; d <= dim; d += 1) {
      const dd = String(d).padStart(2, '0');
      const mm = String(m0 + 1).padStart(2, '0');
      cells.push({ day: `${y}-${mm}-${dd}`, n: d });
    }
    while (cells.length % 7 !== 0) cells.push({ day: null, n: null });
    const label =
      lang === 'zh'
        ? `${y}年${m0 + 1}月`
        : new Date(Date.UTC(y, m0, 1)).toLocaleString('en-US', {
            month: 'long',
            year: 'numeric',
            timeZone: 'UTC',
          });
    return { year: y, month0: m0, cells, label };
  }, [monthCursor, selected, lang]);

  const goMonth = (delta: number) => {
    const dt = new Date(Date.UTC(year, month0 + delta, 1));
    const yy = dt.getUTCFullYear();
    const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
    onMonthChange(`${yy}-${mm}-01`);
  };

  return (
    <div className="day-cal card">
      <div className="day-cal-head">
        <button
          type="button"
          className="day-cal-nav"
          onClick={() => goMonth(-1)}
          aria-label={t('today.calPrevMonth')}
        >
          ‹
        </button>
        <span className="day-cal-label">{label}</span>
        <button
          type="button"
          className="day-cal-nav"
          onClick={() => goMonth(1)}
          aria-label={t('today.calNextMonth')}
        >
          ›
        </button>
      </div>
      <div className="day-cal-weekdays">
        {weekdays.map((w) => (
          <span key={w} className="day-cal-wd">
            {w}
          </span>
        ))}
      </div>
      <div className="day-cal-grid">
        {cells.map((c, i) => {
          if (!c.day || c.n == null) {
            return <span key={`e${i}`} className="day-cal-cell empty" />;
          }
          const h = heat.get(c.day) ?? 0;
          const cls = [
            'day-cal-cell',
            h > 0 ? `heat-${h}` : '',
            c.day === selected ? 'selected' : '',
            c.day === projectionDay ? 'is-today' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <button
              key={c.day}
              type="button"
              className={cls}
              onClick={() => onSelect(c.day!)}
              title={c.day}
            >
              <span className="day-cal-n">{c.n}</span>
              {h > 0 && <span className="day-cal-dot" aria-hidden />}
            </button>
          );
        })}
      </div>
      <div className="day-cal-foot">
        <button
          type="button"
          className="tiny day-cal-jump"
          onClick={() => {
            onMonthChange(monthStart(projectionDay));
            onSelect(projectionDay);
          }}
        >
          {t('today.calJumpToday')}
        </button>
      </div>
    </div>
  );
}
