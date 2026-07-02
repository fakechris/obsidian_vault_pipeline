import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchClaim, fetchFind, fetchSearchSubgraph } from '../lib/api';
import type { ClaimDetail, FindHit, GraphNode } from '../lib/types';

const MAX_OTHER_MATCHES = 20;

export default function ExplorePage() {
  const [term, setTerm] = useState('');
  const [claims, setClaims] = useState<GraphNode[]>([]);
  const [others, setOthers] = useState<FindHit[]>([]);
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    clearTimeout(debounce.current);
    const q = term.trim();
    if (!q) {
      setClaims([]);
      setOthers([]);
      return;
    }
    debounce.current = setTimeout(async () => {
      try {
        // Claims come from the graph search (structured ids + metadata);
        // /api/find hits are display lines for everything else — and need
        // the index, which a crystal-only vault may not have. Degrade.
        const [subgraph, hits] = await Promise.all([
          fetchSearchSubgraph(q),
          fetchFind(q).catch(() => [] as FindHit[]),
        ]);
        setClaims(subgraph.nodes.filter((n) => n.hit));
        setOthers(
          hits.filter((h) => h.kind !== 'claim').slice(0, MAX_OTHER_MATCHES),
        );
        setStatus(null);
      } catch {
        setStatus('Search failed 搜索失败');
      }
    }, 300);
    return () => clearTimeout(debounce.current);
  }, [term]);

  async function showDetail(node: GraphNode) {
    try {
      setDetail(await fetchClaim(node.id.replace(/^claim:/, '')));
      setStatus(null);
    } catch {
      setStatus('Failed to load claim detail 加载失败');
    }
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-96 shrink-0 flex-col border-r border-border-soft">
        <div className="border-b border-border-soft p-3">
          <input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Search claims, sources… 搜索"
            className="w-full rounded-lg border border-border-soft bg-surface px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-claim-deep focus:outline-none"
          />
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto p-2">
          {claims.map((n) => (
            <li key={n.id}>
              <button
                onClick={() => void showDetail(n)}
                className="w-full rounded-md px-3 py-2 text-left text-sm text-slate-300 transition-colors hover:bg-white/5"
              >
                <span className="mr-2 rounded bg-claim-deep/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-claim">
                  claim
                </span>
                {n.label}
                {n.theme && (
                  <span className="mt-0.5 block text-[11px] text-slate-500">
                    {n.theme} · {n.strength}
                  </span>
                )}
              </button>
            </li>
          ))}
          {others.length > 0 && (
            <li className="mt-2 border-t border-border-soft px-3 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Other matches 其他命中
            </li>
          )}
          {others.map((h, i) => (
            <li
              key={`${h.kind}-${i}`}
              className="px-3 py-1.5 text-xs text-slate-400"
            >
              <span className="mr-2 rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500">
                {h.kind}
              </span>
              {h.line}
            </li>
          ))}
          {term && claims.length === 0 && others.length === 0 && (
            <li className="px-3 py-2 text-sm text-slate-500">
              No results 无结果
            </li>
          )}
        </ul>
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto p-6">
        {status && <p className="mb-4 text-sm text-slate-500">{status}</p>}
        {detail ? (
          <>
            <h2 className="text-lg font-semibold text-claim">{detail.claim}</h2>
            <p className="mt-2 text-sm text-slate-400">
              Theme 主题: <strong className="text-slate-300">{detail.theme}</strong>{' '}
              · Strength 强度:{' '}
              <strong className="text-slate-300">{detail.strength}</strong> ·{' '}
              <Link
                to={`/graph?focus=${encodeURIComponent(`claim:${detail.claim_id}`)}`}
                className="text-unit underline-offset-2 hover:underline"
              >
                Open in graph 在图谱中查看
              </Link>
            </p>
            <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-slate-400">
              Citation chain 引用链 ({detail.citations.length})
            </h3>
            <div className="mt-3 space-y-3">
              {detail.citations.map((c) => (
                <div
                  key={`${c.case_id}:${c.unit_id}`}
                  className="rounded-lg border border-border-soft bg-surface p-4"
                >
                  <div className="text-xs text-slate-500">
                    Unit {c.unit_id.slice(0, 16)}…
                    {c.resolved_line != null && ` · line ${c.resolved_line}`}
                  </div>
                  <blockquote className="mt-2 border-l-2 border-claim-deep pl-3 text-sm italic text-slate-300">
                    “{c.quote}”
                  </blockquote>
                  <div className="mt-2 text-xs text-slate-400">
                    📄{' '}
                    {c.source_url ? (
                      <a
                        href={c.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-source underline-offset-2 hover:underline"
                      >
                        {c.source_title}
                      </a>
                    ) : (
                      c.source_title
                    )}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          !status && (
            <p className="text-sm text-slate-500">
              Search and pick a claim to trace its provenance
              搜索并选择一条 claim 以追溯其来源。
            </p>
          )
        )}
      </section>
    </div>
  );
}
