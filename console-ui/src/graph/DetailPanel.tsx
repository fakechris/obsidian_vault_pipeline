import { useState } from 'react';
import { useGraphStore } from '../store/graphStore';
import { nodeColor } from '../lib/palette';
import type { CitationDetail } from '../lib/types';

/** Right-side inspection panel — the full-density tier: complete citation
 * chain claim → quote → unit text → source, straight from /api/claim/:id. */
export default function DetailPanel() {
  const selection = useGraphStore((s) => s.selection);
  const detail = useGraphStore((s) => s.detail);
  const detailLoading = useGraphStore((s) => s.detailLoading);
  const viewMode = useGraphStore((s) => s.viewMode);
  const data = useGraphStore((s) => s.data);
  const loadFocus = useGraphStore((s) => s.loadFocus);
  const select = useGraphStore((s) => s.select);

  if (!selection) return null;
  const node = data?.nodes.find((n) => n.id === selection);

  return (
    <aside className="absolute bottom-4 right-4 top-4 z-40 flex w-96 flex-col overflow-hidden rounded-lg border border-border-soft bg-panel shadow-2xl backdrop-blur-xl">
      <header className="flex items-center gap-2 border-b border-border-soft px-4 py-2.5">
        <span
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: nodeColor(node?.type ?? 'claim') }}
        >
          {node?.type ?? 'node'}
        </span>
        {selection.startsWith('claim:') && viewMode === 'overview' && (
          <button
            onClick={() => void loadFocus(selection)}
            className="rounded-md bg-white/10 px-2 py-0.5 text-xs text-slate-200 transition-colors hover:bg-white/20"
          >
            Focus 聚焦 ⤢
          </button>
        )}
        <button
          onClick={() => select(null)}
          className="ml-auto rounded-md px-2 py-0.5 text-slate-500 transition-colors hover:bg-white/10 hover:text-slate-200"
          aria-label="Close"
        >
          ✕
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {detailLoading && (
          <p className="text-sm text-slate-500">Loading 加载中…</p>
        )}

        {detail ? (
          <>
            <h2 className="text-sm font-semibold leading-snug text-claim">
              {detail.claim}
            </h2>
            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
              <span className="rounded bg-white/10 px-1.5 py-0.5 text-slate-300">
                {detail.theme}
              </span>
              <span
                className={
                  detail.strength === 'supported'
                    ? 'rounded bg-green-500/15 px-1.5 py-0.5 text-green-400'
                    : 'rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-400'
                }
              >
                {detail.strength}
              </span>
            </div>

            <h3 className="mt-5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Citation chain 引用链 ({detail.citations.length})
            </h3>
            <div className="mt-2 space-y-2.5">
              {detail.citations.map((c) => (
                <Citation key={`${c.case_id}:${c.unit_id}`} citation={c} />
              ))}
            </div>
          </>
        ) : (
          !detailLoading && (
            <div className="text-sm text-slate-400">
              <p className="leading-snug text-slate-200">{node?.label}</p>
              {node?.url && (
                <a
                  href={node.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-3 block truncate text-xs text-source underline-offset-2 hover:underline"
                >
                  {node.url}
                </a>
              )}
              {node?.type === 'unit' && (
                <p className="mt-3 text-xs text-slate-500">
                  Select a citing claim for the full chain
                  选择引用它的 claim 查看完整链路。
                </p>
              )}
            </div>
          )
        )}
      </div>
    </aside>
  );
}

function Citation({ citation: c }: { citation: CitationDetail }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-md border border-border-soft bg-surface/60 p-3">
      <blockquote className="border-l-2 border-claim-deep pl-2.5 text-xs italic leading-snug text-slate-300">
        “{c.quote}”
      </blockquote>
      {c.unit_text && c.unit_text !== c.quote && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 text-[11px] text-slate-500 transition-colors hover:text-slate-300"
        >
          {expanded ? '▾ Unit text 证据段' : '▸ Unit text 证据段'}
        </button>
      )}
      {expanded && (
        <p className="mt-1 whitespace-pre-wrap text-[11px] leading-snug text-slate-400">
          {c.unit_text}
        </p>
      )}
      <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-500">
        <span>📄</span>
        {c.source_url ? (
          <a
            href={c.source_url}
            target="_blank"
            rel="noreferrer"
            className="truncate text-source underline-offset-2 hover:underline"
          >
            {c.source_title}
          </a>
        ) : (
          <span className="truncate">{c.source_title}</span>
        )}
        {c.resolved_line != null && (
          <span className="ml-auto shrink-0">L{c.resolved_line}</span>
        )}
      </div>
    </div>
  );
}
