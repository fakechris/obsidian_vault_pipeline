import { describe, expect, it } from 'vitest';
import { buildProcessGraph, preservePhysics, type ProcessNode } from './AskProcessGraph';

describe('buildProcessGraph', () => {
  it('replays a dense history trail (many sources + memory cards)', () => {
    // Mirrors a real session tool_trace: fulltext + evidence hits, no live
    // progress events — History must still build a non-empty graph.
    const sources = Array.from({ length: 5 }, (_, i) => ({
      kind: 'source',
      id: `sha-${i}`,
      label: `Source ${i}`,
      source_id: `sha-${i}`,
    }));
    const cards = Array.from({ length: 3 }, (_, i) => ({
      kind: 'card',
      id: `card:sha-${i}:Card ${i}`,
      label: `Card ${i}`,
      source_id: `sha-${i}`,
    }));
    const { nodes, edges } = buildProcessGraph({
      toolTrace: [
        { tool: 'search_fulltext', summary: 'ok', ok: true, hits: sources },
        { tool: 'search_evidence', summary: 'ok', ok: true, hits: cards },
      ],
    });
    expect(nodes.filter((n) => n.kind === 'source')).toHaveLength(5);
    expect(nodes.filter((n) => n.kind === 'card')).toHaveLength(3);
    expect(edges.length).toBeGreaterThan(0);
  });

  it('accumulates claim/source/card hits and cites edges', () => {
    const { nodes, edges } = buildProcessGraph({
      events: [
        {
          event: 'tool_finished',
          tool: 'search_claims',
          hits: [
            {
              kind: 'claim',
              id: 'ck-1',
              label: 'Governed forgetting',
              source_id: 'sha-a',
            },
          ],
        },
        {
          event: 'tool_finished',
          tool: 'search_sources',
          hits: [
            {
              kind: 'source',
              id: 'sha-a',
              label: 'Agent Memory Systems',
              source_id: 'sha-a',
            },
          ],
        },
      ],
      citations: [
        {
          id: 'claim:ck-1',
          kind: 'claim',
          title: 'Governed forgetting',
          snippet: null,
          link_target: '/knowledge#ck-1',
          verified: true,
        },
      ],
    });

    expect(nodes.some((n) => n.kind === 'claim' && n.cited)).toBe(true);
    expect(nodes.some((n) => n.id === 'source:sha-a' && n.hit)).toBe(true);
    expect(edges.some((e) => e.type === 'cites')).toBe(true);
  });

  it('builds from tool_trace alone (history replay)', () => {
    const { nodes } = buildProcessGraph({
      toolTrace: [
        {
          tool: 'search_evidence',
          summary: 'ok',
          ok: true,
          hits: [
            {
              kind: 'card',
              id: 'card:s1:Memory',
              label: 'Memory as state',
              source_id: 's1',
            },
          ],
        },
      ],
    });
    expect(nodes.some((n) => n.kind === 'card')).toBe(true);
    expect(nodes.some((n) => n.id === 'source:s1')).toBe(true);
  });
});

describe('preservePhysics', () => {
  const settled: ProcessNode[] = [
    { id: 'source:a', kind: 'source', label: 'A', x: 10, y: 20, vx: 0.1, vy: -0.2 },
    { id: 'claim:c1', kind: 'claim', label: 'C1', x: -5, y: 8, vx: 0, vy: 0 },
  ];

  it('carries settled positions onto refreshed nodes matched by id', () => {
    const fresh: ProcessNode[] = [
      { id: 'source:a', kind: 'source', label: 'A better label' },
      { id: 'claim:c1', kind: 'claim', label: 'C1' },
    ];
    const out = preservePhysics(fresh, settled);
    expect(out[0]).toMatchObject({ x: 10, y: 20, vx: 0.1, vy: -0.2, label: 'A better label' });
    expect(out[1]).toMatchObject({ x: -5, y: 8 });
    // Engine owns the copies it mutates — our inputs stay untouched.
    expect(fresh[0].x).toBeUndefined();
  });

  it('leaves genuinely new nodes positionless so only they pop in', () => {
    const fresh: ProcessNode[] = [
      { id: 'source:a', kind: 'source', label: 'A' },
      { id: 'card:new', kind: 'card', label: 'New' },
    ];
    const out = preservePhysics(fresh, settled);
    expect(out[0].x).toBe(10);
    expect(out[1].x).toBeUndefined();
  });

  it('first render (no previous nodes) starts everyone unsettled', () => {
    const out = preservePhysics([{ id: 'source:a', kind: 'source', label: 'A' }], []);
    expect(out[0].x).toBeUndefined();
  });
});
