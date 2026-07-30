/** Renderer tests for the react-markdown engine (vitest, node env) via
 * renderToStaticMarkup. Covers the syntax real clippings carry and the
 * invariants the v2 mini renderer established: citations never nest inside
 * links, scripts never become markup, line anchors survive. */
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  MarkdownView,
  sourceImageCandidates,
  splitFrontmatter,
  trimAtMermaidErrorLine,
  type MarkdownViewProps,
} from './markdown';

const render = (md: string, extra?: Partial<MarkdownViewProps>) =>
  renderToStaticMarkup(<MarkdownView markdown={md} gutter={false} {...extra} />);

const CITE_RE = /\[\s*((?:claim|card|unit):[^\]\n]+?)\s*\]/g;

describe('trimAtMermaidErrorLine (truncated-ingest healing)', () => {
  // The 2026-07-13 TencentDB-Agent-Memory note: README cut at 8000 bytes
  // mid-```mermaid block; the unclosed fence swallowed "*(README
  // truncated)*" into the code and mermaid failed with "Syntax error".
  const TRUNCATED =
    'graph LR\n' +
    '  Log["Verbose Logs"] --> FS[("External FS")]\n' +
    '  style Log fill:#f1f5f9\n' +
    '\n' +
    '*(README truncated)*';

  it('cuts the garbage tail at the parse-error line', () => {
    const err = new Error(
      'Parse error on line 5:\n*(README truncated)*\n———^\nExpecting ...',
    );
    expect(trimAtMermaidErrorLine(TRUNCATED, err)).toBe(
      'graph LR\n' +
        '  Log["Verbose Logs"] --> FS[("External FS")]\n' +
        '  style Log fill:#f1f5f9\n',
    );
  });

  it('accepts lexical-error wording too', () => {
    const cut = trimAtMermaidErrorLine(
      TRUNCATED,
      new Error('Lexical error on line 3.'),
    );
    expect(cut).toBe('graph LR\n  Log["Verbose Logs"] --> FS[("External FS")]');
  });

  it('returns null when the error has no usable line number', () => {
    expect(trimAtMermaidErrorLine(TRUNCATED, new Error('boom'))).toBeNull();
    expect(trimAtMermaidErrorLine(TRUNCATED, 'nope')).toBeNull();
    // Line 1 failures leave nothing renderable — no retry.
    expect(
      trimAtMermaidErrorLine(TRUNCATED, new Error('Parse error on line 1: x')),
    ).toBeNull();
  });
});

describe('sourceImageCandidates (repo vs vault image chains)', () => {
  const GH = sourceImageCandidates(
    'https://github.com/TencentCloud/TencentDB-Agent-Memory',
    '50-Inbox/03-Processed/2026-06/note.md',
  );

  it('GitHub notes try raw.githubusercontent.com/HEAD first, vault second', () => {
    expect(GH('./assets/images/logo.png')).toEqual([
      'https://raw.githubusercontent.com/TencentCloud/TencentDB-Agent-Memory/HEAD/assets/images/logo.png',
      '/api/file/assets/images/logo.png?note=50-Inbox%2F03-Processed%2F2026-06%2Fnote.md',
    ]);
  });

  it('strips a .git suffix from the repo segment', () => {
    const r = sourceImageCandidates('https://github.com/o/r.git');
    expect(r('./x.png')[0]).toBe('https://raw.githubusercontent.com/o/r/HEAD/x.png');
  });

  it('passes absolute/data srcs through as a single candidate', () => {
    expect(GH('https://img.shields.io/b.svg')).toEqual(['https://img.shields.io/b.svg']);
    expect(GH('data:image/png;base64,xx')).toEqual(['data:image/png;base64,xx']);
  });

  it('clippings try the vault attachment endpoint first, then URL-joined remote', () => {
    const r = sourceImageCandidates(
      'https://example.com/blog/post',
      '50-Inbox/03-Processed/2026-05/note.md',
    );
    expect(r('50-Inbox/01-Raw/attachments/2026-05/img-1.png')).toEqual([
      '/api/file/50-Inbox/01-Raw/attachments/2026-05/img-1.png?note=50-Inbox%2F03-Processed%2F2026-05%2Fnote.md',
      'https://example.com/blog/50-Inbox/01-Raw/attachments/2026-05/img-1.png',
    ]);
  });

  it('percent-encodes path segments (Chinese dir names, spaces)', () => {
    const r = sourceImageCandidates(undefined, undefined);
    expect(r('50-Inbox/附件 图/img x.png')).toEqual([
      `/api/file/50-Inbox/${encodeURIComponent('附件 图')}/${encodeURIComponent('img x.png')}`,
    ]);
  });

  it('still yields the vault candidate without any source URL', () => {
    expect(sourceImageCandidates()('a/b.png')).toEqual(['/api/file/a/b.png']);
    expect(sourceImageCandidates('')('a/b.png')).toEqual(['/api/file/a/b.png']);
  });
});

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
