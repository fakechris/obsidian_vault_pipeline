import { clusterColor } from '../lib/palette';
import type { Community } from '../lib/types';

export default function Legend({ communities }: { communities: Community[] }) {
  if (communities.length === 0) return null;
  return (
    <div className="pointer-events-auto absolute bottom-4 left-4 max-h-64 w-64 overflow-y-auto rounded-lg border border-border-soft bg-panel p-3 shadow-2xl backdrop-blur-xl">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Communities 社区
      </div>
      <ul className="space-y-1.5">
        {communities.slice(0, 12).map((c) => (
          <li key={c.id} className="flex items-center gap-2 text-xs">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: clusterColor(c.id) }}
            />
            <span className="truncate text-slate-300">{c.label}</span>
            <span className="ml-auto shrink-0 text-slate-500">{c.size}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
