/** Renderer unit tests (vitest, node env): assertions walk the ReactNode
 * tree directly — no DOM. Focus is the InlineMarker hook contract, and
 * especially that a marker can NEVER nest inside a link (<a><button/></a>
 * is invalid markup with two competing navigations). */
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { parseMarkdown, renderInline, type InlineMarker } from './markdown';

/** Mirror of the Ask page tokenizer (requires the literal brackets). */
const CITE_RE = /\[\s*((?:claim|card|unit):[^\]\n]+?)\s*\]/g;

const citeMarker: InlineMarker = {
  pattern: CITE_RE,
  render: (m, key) => (
    <button key={key} data-cite={m[1]}>
      [1]
    </button>
  ),
};

/** Depth-first flatten of a ReactNode tree into elements and strings. */
function flatten(node: ReactNode): (ReactElement | string)[] {
  if (node == null || typeof node === 'boolean') return [];
  if (Array.isArray(node)) return node.flatMap(flatten);
  if (typeof node === 'string' || typeof node === 'number') {
    return [String(node)];
  }
  if (isValidElement(node)) {
    const { children } = node.props as { children?: ReactNode };
    return [node, ...flatten(children)];
  }
  return [];
}

const tags = (nodes: ReactNode): unknown[] =>
  flatten(nodes)
    .filter((n): n is ReactElement => typeof n !== 'string')
    .map((el) => el.type);

const text = (nodes: ReactNode): string =>
  flatten(nodes)
    .filter((n): n is string => typeof n === 'string')
    .join('');

describe('renderInline marker hook', () => {
  it('replaces citation tokens in plain text with marker nodes', () => {
    const out = renderInline('grounded [claim:c01] here', 'k', citeMarker);
    expect(tags(out)).toContain('button');
    // Token replaced; the "[1]" seen here is the marker button's own label.
    expect(text(out)).toBe('grounded [1] here');
  });

  it('never nests a marker inside a link label', () => {
    // A bracket-less pattern CAN match inside a link label — the renderer
    // must suppress it there so the anchor keeps single navigation.
    const bare: InlineMarker = {
      pattern: /claim:\w+/g,
      render: (m, key) => <button key={key}>{m[0]}</button>,
    };
    const out = renderInline(
      'see [claim:c01](/knowledge#c01) and claim:c02',
      'k',
      bare,
    );
    const anchors = flatten(out).filter(
      (n): n is ReactElement => isValidElement(n) && n.type === 'a',
    );
    expect(anchors).toHaveLength(1);
    const a = anchors[0] as ReactElement<{ href: string; children: ReactNode }>;
    expect(a.props.href).toBe('/knowledge#c01');
    // Inside the anchor: plain text only, no interactive marker.
    expect(tags(a.props.children)).toHaveLength(0);
    expect(text(a.props.children)).toBe('claim:c01');
    // Outside the anchor the same pattern still fires.
    const buttons = flatten(out).filter(
      (n): n is ReactElement => isValidElement(n) && n.type === 'button',
    );
    expect(buttons).toHaveLength(1);
  });

  it('renders a citation-shaped link as a plain anchor (link wins)', () => {
    // The Ask tokenizer needs brackets, and a link label can never contain
    // `]` — so `[claim:c01](url)` is a LINK, not a marker. Documented
    // precedence: the anchor renders, no button anywhere.
    const out = renderInline(
      'see [claim:c01](/knowledge#c01) for detail',
      'k',
      citeMarker,
    );
    expect(tags(out)).toContain('a');
    expect(tags(out)).not.toContain('button');
  });

  it('still fires inside emphasis (valid nesting)', () => {
    const out = renderInline('**bold [unit:u-1] evidence**', 'k', citeMarker);
    const kinds = tags(out);
    expect(kinds).toContain('strong');
    expect(kinds).toContain('button');
  });

  it('leaves tokens as plain text when render returns null', () => {
    const nullMarker: InlineMarker = { pattern: CITE_RE, render: () => null };
    const out = renderInline('keep [claim:c01] literal', 'k', nullMarker);
    expect(tags(out)).toHaveLength(0);
    expect(text(out)).toBe('keep [claim:c01] literal');
  });

  it('renders unchanged when no marker is supplied', () => {
    const out = renderInline('plain [claim:c01] text', 'k');
    expect(text(out)).toBe('plain [claim:c01] text');
  });
});

/** Clipping-body rendering (Source tab): badge links, escapes, wiki-links,
 * highlights — the mini renderer must not leak raw syntax to the page. */
describe('renderInline clipping syntax', () => {
  it('renders a badge [![alt](img)](href) as ONE anchor, no dangling parens', () => {
    const out = renderInline(
      '[![CI](https://img.shields.io/ci.svg)](https://example.com/ci)',
      'k',
    );
    const anchors = flatten(out).filter(
      (n): n is ReactElement => isValidElement(n) && n.type === 'a',
    );
    expect(anchors).toHaveLength(1);
    expect((anchors[0] as ReactElement<{ href: string }>).props.href).toBe(
      'https://example.com/ci',
    );
    expect(text(out)).toBe('[image: CI]');
  });

  it('makes a bare image a clickable chip without remote loading', () => {
    const out = renderInline('![alt](https://img.example/x.png)', 'k');
    const anchors = flatten(out).filter(
      (n): n is ReactElement => isValidElement(n) && n.type === 'a',
    );
    expect(anchors).toHaveLength(1);
    expect(tags(out)).not.toContain('img');
    expect(text(out)).toBe('[image: alt]');
  });

  it('unescapes clipper backslashes ("1\\. Codex")', () => {
    const out = renderInline('1\\. Codex \\| a\\_b', 'k');
    expect(text(out)).toBe('1. Codex | a_b');
  });

  it('renders wiki-links as their display text', () => {
    expect(text(renderInline('see [[Ray Dalio]] here', 'k'))).toBe(
      'see Ray Dalio here',
    );
    expect(text(renderInline('[[Note|Alias]]', 'k'))).toBe('Alias');
  });

  it('renders ==highlights== as mark', () => {
    const out = renderInline('a ==key phrase== b', 'k');
    expect(tags(out)).toContain('mark');
    expect(text(out)).toBe('a key phrase b');
  });
});

describe('parseMarkdown tables', () => {
  it('parses a GFM table with alignment', () => {
    const blocks = parseMarkdown(
      ['| Name | Cost |', '| :--- | ---: |', '| a | 1 |', '| b | 2 |', '', 'after'].join('\n'),
    );
    const table = blocks.find((b) => b.kind === 'table');
    expect(table).toBeDefined();
    if (table?.kind !== 'table') return;
    expect(table.header).toEqual(['Name', 'Cost']);
    expect(table.aligns).toEqual(['left', 'right']);
    expect(table.rows.map((r) => r.cells)).toEqual([
      ['a', '1'],
      ['b', '2'],
    ]);
    // The trailing paragraph is its own block, not absorbed into the table.
    expect(blocks[blocks.length - 1]).toMatchObject({ kind: 'para' });
  });

  it('does not treat a pipe line without a separator as a table', () => {
    const blocks = parseMarkdown('a | b\nno separator here\n');
    expect(blocks.every((b) => b.kind !== 'table')).toBe(true);
  });

  it('keeps escaped pipes inside cells', () => {
    const blocks = parseMarkdown('| a \\| b | c |\n| --- | --- |\n| d | e |\n');
    const table = blocks[0];
    if (table.kind !== 'table') throw new Error('expected table');
    expect(table.header).toEqual(['a | b', 'c']);
  });
});
