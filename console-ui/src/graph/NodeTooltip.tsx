import { useGraphStore } from '../store/graphStore';
import { clusterColor, nodeColor } from '../lib/palette';
import type { Community } from '../lib/types';

const TYPE_LABEL: Record<string, string> = {
  claim: 'Claim 论断',
  unit: 'Unit 证据段',
  source: 'Source 来源',
};

function Bar({ value, color }: { value: number; color: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${Math.round(value * 100)}%`, backgroundColor: color }}
      />
    </div>
  );
}

/** Hover card (after a short delay) — the mid-density information layer:
 * everything worth knowing about a node before committing to a click. */
export default function NodeTooltip({
  communities,
}: {
  communities: Community[];
}) {
  const hover = useGraphStore((s) => s.hover);
  if (!hover) return null;

  const { node, x, y } = hover;
  const communityLabel = communities.find((c) => c.id === node.cluster)?.label;
  // Keep the card inside the viewport.
  const flipX = x > window.innerWidth - 360;
  const flipY = y > window.innerHeight - 260;

  return (
    <div
      className="pointer-events-none fixed z-50 w-80 rounded-lg border border-border-soft bg-panel p-3 shadow-2xl backdrop-blur-xl"
      style={{
        left: flipX ? x - 336 : x + 16,
        top: flipY ? y - 220 : y + 14,
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
          style={{
            backgroundColor: `${nodeColor(node.type)}22`,
            color: nodeColor(node.type),
          }}
        >
          {TYPE_LABEL[node.type] ?? node.type}
        </span>
        {node.strength && (
          <span
            className={
              node.strength === 'supported'
                ? 'rounded bg-green-500/15 px-1.5 py-0.5 text-[10px] font-medium text-green-400'
                : 'rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-400'
            }
          >
            {node.strength}
          </span>
        )}
      </div>

      <p className="mt-2 line-clamp-3 text-sm leading-snug text-slate-200">
        {node.label}
      </p>

      {node.type === 'claim' && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-2 text-[11px] text-slate-400">
            <span className="w-24 shrink-0">Importance 重要度</span>
            <Bar value={node.importance ?? 0} color="#fbbf24" />
            <span className="w-8 text-right tabular-nums">
              {Math.round((node.importance ?? 0) * 100)}
            </span>
          </div>
          {node.provenance != null && (
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <span className="w-24 shrink-0">Provenance 溯源</span>
              <Bar value={node.provenance} color="#8b5cf6" />
              <span className="w-8 text-right tabular-nums">
                {Math.round(node.provenance * 100)}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
        {node.theme && <span>Theme: {node.theme}</span>}
        <span>Degree 连接: {node.degree}</span>
        {communityLabel && node.cluster > 0 && (
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: clusterColor(node.cluster) }}
            />
            {communityLabel}
          </span>
        )}
        {node.url && <span className="truncate">{node.url}</span>}
      </div>

      {node.type === 'claim' && (
        <div className="mt-2 border-t border-border-soft pt-2 text-[10px] text-slate-600">
          Click for provenance 点击查看溯源 · Double-click to focus 双击聚焦
        </div>
      )}
    </div>
  );
}
