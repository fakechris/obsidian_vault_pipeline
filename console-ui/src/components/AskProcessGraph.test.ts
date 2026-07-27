import { describe, expect, it } from 'vitest';
import { buildProcessGraph } from './AskProcessGraph';

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
