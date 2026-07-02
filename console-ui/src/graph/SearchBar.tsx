import { useEffect, useState } from 'react';
import { useGraphStore } from '../store/graphStore';
import { fetchThemes } from '../lib/api';
import type { ThemeCount } from '../lib/types';

/** Top-center controls: search (→ tight hit subgraph) + theme filter
 * (server-side filtered overview). */
export default function SearchBar() {
  const viewMode = useGraphStore((s) => s.viewMode);
  const searchQuery = useGraphStore((s) => s.searchQuery);
  const themeFilter = useGraphStore((s) => s.themeFilter);
  const loadSearch = useGraphStore((s) => s.loadSearch);
  const backToOverview = useGraphStore((s) => s.backToOverview);
  const setThemeFilter = useGraphStore((s) => s.setThemeFilter);

  const [text, setText] = useState('');
  const [themes, setThemes] = useState<ThemeCount[]>([]);

  useEffect(() => {
    fetchThemes().then(setThemes, () => {});
  }, []);

  const submit = () => {
    const q = text.trim();
    if (q) void loadSearch(q);
  };

  const clear = () => {
    setText('');
    if (viewMode === 'search') void backToOverview();
  };

  return (
    <div className="pointer-events-auto absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2">
      <div className="flex items-center rounded-lg border border-border-soft bg-panel shadow-lg backdrop-blur-xl">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
            if (e.key === 'Escape') clear();
          }}
          placeholder="Search claims 搜索论断…"
          className="w-64 bg-transparent px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none"
        />
        {(text || viewMode === 'search') && (
          <button
            onClick={clear}
            className="px-2 text-slate-500 transition-colors hover:text-slate-200"
            aria-label="Clear search"
          >
            ✕
          </button>
        )}
      </div>

      {viewMode === 'search' && searchQuery && (
        <span className="rounded-md border border-highlight/30 bg-highlight/10 px-2 py-1 text-xs text-highlight">
          “{searchQuery}”
        </span>
      )}

      {viewMode === 'overview' && themes.length > 0 && (
        <select
          value={themeFilter ?? ''}
          onChange={(e) => setThemeFilter(e.target.value || null)}
          className="rounded-lg border border-border-soft bg-panel px-2 py-1.5 text-sm text-slate-300 shadow-lg backdrop-blur-xl focus:outline-none"
        >
          <option value="">All themes 全部主题</option>
          {themes.map((t) => (
            <option key={t.theme} value={t.theme}>
              {t.theme} ({t.count})
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
