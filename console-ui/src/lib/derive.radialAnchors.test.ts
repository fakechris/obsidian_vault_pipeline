/** Geometry of the knowledge-graph packed-cloud layout (radialAnchors) —
 * pure, no DOM. The component only seeds positions and applies pull forces
 * toward these points. */
import { describe, expect, it } from 'vitest';
import { radialAnchors } from './derive';

const sizes = (pairs: [number, number][]) => new Map(pairs);
const norm = (p: { x: number; y: number }) => Math.hypot(p.x, p.y);
// Mirror of the packer's blob-footprint formula (nodeRadius default 7).
const blobR = (n: number) => 7 * Math.sqrt(n) * 1.45 + 10;

describe('radialAnchors', () => {
  it('packs the largest community at the origin, disks never overlapping', () => {
    const input: [number, number][] = [[1, 50], [2, 20], [3, 80], [4, 5], [5, 12]];
    const a = radialAnchors(sizes(input), []);
    expect(a.byCluster.size).toBe(5);
    expect(a.byCluster.get(3)).toEqual({ x: 0, y: 0 });
    const bySize = new Map(input);
    const placed = [...a.byCluster.entries()];
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const [ca, pa] = placed[i];
        const [cb, pb] = placed[j];
        const d = Math.hypot(pa.x - pb.x, pa.y - pb.y);
        expect(d).toBeGreaterThanOrEqual(blobR(bySize.get(ca)!) + blobR(bySize.get(cb)!) - 1e-6);
      }
    }
    // The reported radius covers every disk.
    for (const [c, p] of placed) {
      expect(norm(p) + blobR(bySize.get(c)!)).toBeLessThanOrEqual(a.radius + 1e-6);
    }
  });

  it('affinity pulls a connected community earlier (closer to the center)', () => {
    // A is largest; B and C are equal-size, but only B is wired to A —
    // B must be packed before C, hence nearer the origin.
    const s = sizes([[1, 100], [2, 30], [3, 30]]);
    const affinity = new Map([[2, new Map([[1, 9]])], [1, new Map([[2, 9]])]]);
    const a = radialAnchors(s, [], affinity);
    expect(norm(a.byCluster.get(2)!)).toBeLessThan(norm(a.byCluster.get(3)!));
  });

  it('is deterministic without affinity data (size desc, id asc ties)', () => {
    const a = radialAnchors(sizes([[7, 10], [2, 10], [5, 40]]), []);
    const b = radialAnchors(sizes([[5, 40], [7, 10], [2, 10]]), []);
    for (const c of [2, 5, 7]) expect(a.byCluster.get(c)).toEqual(b.byCluster.get(c));
  });

  it('unclustered nodes get deterministic seats on a ring outside the cloud', () => {
    const a = radialAnchors(sizes([[1, 30]]), ['b', 'a', 'c']);
    expect(a.byId.size).toBe(3);
    for (const p of a.byId.values()) {
      expect(norm(p)).toBeCloseTo(a.radius + 26, 6);
    }
    const b = radialAnchors(sizes([[1, 30]]), ['c', 'a', 'b']);
    for (const id of ['a', 'b', 'c']) expect(a.byId.get(id)).toEqual(b.byId.get(id));
  });

  it('zero-size or empty inputs stay safe', () => {
    const a = radialAnchors(sizes([[1, 0]]), []);
    expect(a.byCluster.size).toBe(0);
    expect(a.byId.size).toBe(0);
    expect(radialAnchors(new Map(), ['x']).byId.size).toBe(1);
  });
});
