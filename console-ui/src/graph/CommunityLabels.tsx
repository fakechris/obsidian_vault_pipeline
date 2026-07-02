import { useEffect, useMemo, useState } from 'react';
import { flyToNodes, getGraph, worldToScreen } from './controller';
import { clusterColor } from '../lib/palette';
import type { Community, GraphNode } from '../lib/types';
import { MAX_HULLS, MIN_HULL_MEMBERS } from './g6/hulls';

/** Top-N communities always get a label even when their disc is small on
 * screen — the overview needs landmarks. */
const LANDMARK_RANK_THRESHOLD = 6;
/** Minimum on-screen disc radius (px) for a community to own a label. */
const MIN_LABELED_DISC_RADIUS = 26;

interface Anchor {
  community: Community;
  world: [number, number];
}

interface Props {
  communities: Community[];
  nodes: GraphNode[];
  /** Bumped by GraphCanvas on viewport transforms and after render. */
  transformTick: number;
}

/**
 * Screen-space community labels — the tier-0 navigation layer. Rendered as
 * DOM (fixed pixel size, backdrop blur) instead of canvas text so they stay
 * readable at any zoom; they fade out as node labels take over. Clicking a
 * label flies to that community.
 */
export default function CommunityLabels({
  communities,
  nodes,
  transformTick,
}: Props) {
  // World anchors: centroid of member positions. Overview positions are
  // static (deterministic seeding), so compute once per data set.
  const [anchors, setAnchors] = useState<Anchor[]>([]);

  const memberIds = useMemo(() => {
    const byCluster = new Map<number, string[]>();
    for (const n of nodes) {
      if (n.cluster > 0) {
        const list = byCluster.get(n.cluster) ?? [];
        list.push(n.id);
        byCluster.set(n.cluster, list);
      }
    }
    return byCluster;
  }, [nodes]);

  useEffect(() => {
    const graph = getGraph();
    if (!graph) return;
    const next: Anchor[] = [];
    for (const c of communities.slice(0, MAX_HULLS)) {
      const members = memberIds.get(c.id) ?? [];
      if (members.length < MIN_HULL_MEMBERS) continue;
      let sx = 0;
      let count = 0;
      let minY = Infinity;
      for (const id of members) {
        try {
          const p = graph.getElementPosition(id);
          sx += p[0];
          minY = Math.min(minY, p[1]);
          count++;
        } catch {
          // node not rendered yet
        }
      }
      if (count > 0) {
        // Label sits above the disc, not on top of its nodes.
        next.push({ community: c, world: [sx / count, minY - 40] });
      }
    }
    setAnchors(next);
    // transformTick re-runs this after the first render completes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [communities, memberIds, transformTick === 0]);

  const graph = getGraph();
  const zoom = graph ? graph.getZoom() : 1;
  // Fade out as node labels become readable (density.ts takes over).
  const opacity = zoom < 0.45 ? 1 : Math.max(0, 1 - (zoom - 0.45) / 0.3);
  if (!graph || opacity <= 0.02) return null;

  // transformTick dependency: recompute screen positions each viewport move.
  void transformTick;

  // Screen-space label LOD: a label only shows when its community disc is
  // big enough on screen to own it (top-6 landmarks always qualify), and a
  // greedy collision pass drops overlapping labels — bigger communities win
  // (anchors arrive size-desc). Zooming in spreads discs → more labels.
  const placed: { x: number; y: number; w: number }[] = [];
  const visible: { community: Community; x: number; y: number }[] = [];
  anchors.forEach(({ community, world }, rank) => {
    const discScreenR = (20 * Math.sqrt(community.size) + 34) * zoom;
    if (
      discScreenR < MIN_LABELED_DISC_RADIUS &&
      rank >= LANDMARK_RANK_THRESHOLD
    )
      return;
    const screen = worldToScreen(world);
    if (!screen) return;
    const [x, y] = screen;
    if (x < -200 || y < -60) return;
    const w = community.label.length * 7 + 70;
    const collides = placed.some(
      (p) => Math.abs(p.x - x) < (p.w + w) / 2 + 8 && Math.abs(p.y - y) < 34,
    );
    if (collides) return;
    placed.push({ x, y, w });
    visible.push({ community, x, y });
  });

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {visible.map(({ community, x, y }) => {
        return (
          <button
            key={community.id}
            onClick={() => flyToNodes(memberIds.get(community.id) ?? [])}
            className="pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2 cursor-pointer whitespace-nowrap rounded-md border border-border-soft bg-panel px-2.5 py-1 text-xs font-semibold text-slate-200 shadow-lg backdrop-blur-md transition-transform hover:scale-105"
            style={{ left: x, top: y, opacity }}
          >
            <span
              className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle"
              style={{ backgroundColor: clusterColor(community.id) }}
            />
            {community.label}
            <span className="ml-1.5 font-normal text-slate-500">
              {community.size}
            </span>
          </button>
        );
      })}
    </div>
  );
}

