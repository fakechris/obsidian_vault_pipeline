import { useEffect, useRef, useState } from 'react';
import { AgeLabel } from '../components/ui';
import { fetchModel } from '../lib/api';
import type { IndexModel } from '../lib/types';

const POLL_MS = 2000;
const MAX_RECENT_RUNS = 15;

// The old monitor listened to /api/sse, which was always a stub (tiny_http
// is a sequential loop; it cannot stream). Polling /api/model and diffing
// runs gives an honest event feed instead.
export default function MonitorPage() {
  const [model, setModel] = useState<IndexModel | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [live, setLive] = useState(false);
  const knownRuns = useRef<Set<string> | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const m = await fetchModel();
        if (cancelled) return;
        setModel(m);
        setLive(true);
        const seen = knownRuns.current;
        const ids = new Set(m.runs.map((r) => r.run_id));
        if (seen) {
          for (const run of m.runs) {
            if (!seen.has(run.run_id)) {
              setEvents((prev) =>
                [
                  `${new Date().toLocaleTimeString()} ✓ run ${run.run_id.slice(0, 12)}… (${run.date}): ${run.succeeded} ok, ${run.failed} failed`,
                  ...prev,
                ].slice(0, 200),
              );
            }
          }
        }
        knownRuns.current = ids;
      } catch {
        if (!cancelled) setLive(false);
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const totals = model?.totals;

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-center gap-3">
        <h1 className="text-lg font-semibold text-slate-200">
          Pipeline Monitor 监控
        </h1>
        <span
          className={
            live
              ? 'rounded-full bg-green-500/15 px-2.5 py-0.5 text-xs font-medium text-green-400'
              : 'rounded-full bg-slate-500/15 px-2.5 py-0.5 text-xs font-medium text-slate-400'
          }
        >
          {live ? '● CONNECTED' : '○ OFFLINE'}
        </span>
        {model && (
          <span className="flex items-center gap-2 text-xs text-slate-500">
            <span>
              index {model.date}
              {model.run_id && ` · ${model.run_id.slice(0, 12)}…`}
            </span>
            {/* "connected" ≠ "fresh": the socket may be live while the model on
                disk is hours old. Show the model AGE next to the dot so the two
                signals can't be conflated (P1). */}
            <AgeLabel builtAt={model.built_at} />
          </span>
        )}
      </div>

      {totals && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {(
            [
              ['Sources 来源', totals.sources],
              ['Processed 已处理', totals.processed],
              ['Queued 待处理', totals.queued],
              ['Blocked 阻塞', totals.blocked],
              ['Durable claims 可靠', totals.claims_durable],
              ['Caveated 存疑', totals.claims_caveated],
            ] as const
          ).map(([label, value]) => (
            <div
              key={label}
              className="rounded-lg border border-border-soft bg-surface p-3"
            >
              <div className="text-2xl font-semibold text-slate-100">
                {value}
              </div>
              <div className="mt-1 text-xs text-slate-500">{label}</div>
            </div>
          ))}
        </div>
      )}

      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
        Recent runs 最近运行
      </h2>
      <div className="mb-6 overflow-x-auto rounded-lg border border-border-soft">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border-soft text-left text-xs text-slate-500">
              <th className="px-3 py-2 font-medium">Run</th>
              <th className="px-3 py-2 font-medium">Date</th>
              <th className="px-3 py-2 font-medium">OK</th>
              <th className="px-3 py-2 font-medium">Failed</th>
              <th className="px-3 py-2 font-medium">Skipped</th>
              <th className="px-3 py-2 font-medium">Blocked</th>
            </tr>
          </thead>
          <tbody>
            {(model?.runs ?? [])
              .slice(-MAX_RECENT_RUNS)
              .reverse()
              .map((r) => (
                <tr
                  key={r.run_id}
                  className="border-b border-border-soft/50 text-slate-300 last:border-0"
                >
                  <td className="px-3 py-2 font-mono text-xs">
                    {r.run_id.slice(0, 14)}…
                  </td>
                  <td className="px-3 py-2">{r.date}</td>
                  <td className="px-3 py-2 text-green-400">{r.succeeded}</td>
                  <td className="px-3 py-2 text-red-400">{r.failed}</td>
                  <td className="px-3 py-2">{r.skipped}</td>
                  <td className="px-3 py-2">{r.blocked}</td>
                </tr>
              ))}
            {model && model.runs.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-sm text-slate-500">
                  No runs yet 暂无运行记录
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
        Events 事件
      </h2>
      <div className="max-h-64 overflow-y-auto rounded-lg border border-border-soft bg-surface p-3 font-mono text-xs text-slate-400">
        {events.length === 0 ? (
          <div>Watching for new runs 等待新运行…</div>
        ) : (
          events.map((e, i) => <div key={i}>{e}</div>)
        )}
      </div>
    </div>
  );
}
