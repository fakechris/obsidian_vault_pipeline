/** Renderer tests for the react-markdown engine (vitest, node env) via
 * renderToStaticMarkup. Covers the syntax real clippings carry and the
 * invariants the v2 mini renderer established: citations never nest inside
 * links, scripts never become markup, line anchors survive. */
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  MarkdownView,
  splitFrontmatter,
  type MarkdownViewProps,
} from './markdown';

const render = (md: string, extra?: Partial<MarkdownViewProps>) =>
  renderToStaticMarkup(<MarkdownView markdown={md} gutter={false} {...extra} />);

const CITE_RE = /\[\s*((?:claim|card|unit):[^\]\n]+?)\s*\]/g;

describe('mature-engine coverage', () => {
  it('renders a GFM table as a real table', () => {
    const html = render('| a | b |\n| --- | --- |\n| 1 | 2 |\n');
    expect(html).toContain('<table');
    expect(html).toContain('<td>1</td>');
  });

  it('renders a badge [![alt](img)](href) as one anchor holding the image', () => {
    const html = render(
      '[![CI](https://img.shields.io/ci.svg)](https://example.com/ci)',
    );
    expect(html).toContain('href="https://example.com/ci"');
    expect(html).toContain('src="https://img.shields.io/ci.svg"');
    // No dangling "(url)" text after the image (v2's broken output).
    expect(html).not.toContain('](https://example.com/ci)');
  });

  it('renders raw HTML blocks (README style) with align mapped to style', () => {
    const html = render('<p align="center">Hi <strong>there</strong></p>');
    expect(html).toContain('text-align:center');
    expect(html).toContain('<strong>there</strong>');
  });

  it('drops script tags — they never become markup', () => {
    const html = render('<script>alert(1)</script>\n\nok');
    expect(html).not.toContain('<script');
    expect(html).toContain('ok');
  });

  it('unescapes clipper backslashes natively ("1\\. Codex")', () => {
    expect(render('1\\. Codex')).toContain('1. Codex');
  });

  it('renders wiki-links as display text', () => {
    const html = render('see [[Ray Dalio]] and [[Note|Alias]]');
    expect(html).toContain('md-wikilink');
    expect(html).toContain('Ray Dalio');
    expect(html).toContain('Alias');
    expect(html).not.toContain('[[');
  });

  it('renders ==highlights== as mark', () => {
    expect(render('a ==key phrase== b')).toContain('<mark>key phrase</mark>');
  });

  it('routes ```mermaid fences to the diagram container, not a code dump', () => {
    const html = render('```mermaid\ngraph TD; A-->B\n```');
    expect(html).toContain('md-mermaid');
    expect(html).not.toContain('A--&gt;B');
  });
});

describe('anchors + frontmatter', () => {
  it('wraps top-level blocks with source-line data', () => {
    const html = render('one\n\ntwo');
    expect(html).toContain('data-ls="1"');
    expect(html).toContain('data-ls="3"');
  });

  it('collapses frontmatter and preserves body line numbers', () => {
    const fm = splitFrontmatter('---\ntitle: x\n---\n# Hi');
    expect(fm.fmText).toBe('title: x');
    expect(fm.body.split('\n')).toHaveLength(4); // blanked, not cut
    const html = render('---\ntitle: x\n---\n\nafter', {
      frontmatterLabel: 'Props',
    });
    expect(html).toContain('md-frontmatter');
    expect(html).toContain('Props');
    expect(html).toContain('data-ls="5"');
  });
});

describe('citation marks (Ask)', () => {
  const cite = {
    pattern: CITE_RE,
    render: (id: string) => <button data-c={id}>[1]</button>,
  };

  it('replaces citation tokens with the marker element', () => {
    const html = render('grounded [claim:c01] here', { citeMarks: cite });
    expect(html).toContain('data-c="claim:c01"');
  });

  it('never fires inside a link label (single navigation)', () => {
    const html = render('see [claim:c01](https://example.com)', {
      citeMarks: cite,
    });
    expect(html).toContain('<a ');
    expect(html).not.toContain('<button');
  });

  it('keeps the literal text when render returns null', () => {
    const html = render('keep [claim:c01] literal', {
      citeMarks: { pattern: CITE_RE, render: () => null },
    });
    expect(html).toContain('[claim:c01]');
    expect(html).not.toContain('<button');
  });
});
