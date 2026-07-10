/** Markdown reading view for source bodies — a TS port (and extension) of
 * the v1 escape-first mini renderer (ovp-console/src/md.rs).
 *
 * XSS approach: there is NO html-string pathway at all. The parser produces
 * a block model, the renderer emits React elements, and every piece of
 * source text stays a React TEXT node — React escapes it on render. No
 * dangerouslySetInnerHTML anywhere, so a hostile clipping
 * (`<script>…</script>`, `<img onerror=…>`) can never become live markup.
 * Link URLs are additionally scheme-filtered (http/https/mailto/#) and
 * images render as an alt-text placeholder — no remote loading (B2
 * decision).
 *
 * Deliberately tiny: headings, paragraphs, fenced code, lists, blockquotes,
 * links/bold/italic/inline-code, hr, YAML frontmatter. The portal renders
 * vault notes for orientation, not fidelity — this keeps zero new
 * dependencies and gives exact source-line tracking for unit anchors, which
 * react-markdown would only approximate.
 */
import { useEffect, useMemo, useRef, type ReactNode } from 'react';

// ------------------------------------------------------------------ parser

/** One rendered block; `line`..`end` are 1-based source line numbers. */
export type MdBlock =
  | { kind: 'heading'; line: number; end: number; level: number; text: string }
  | { kind: 'para'; line: number; end: number; lines: string[] }
  | { kind: 'code'; line: number; end: number; lang: string; text: string }
  | { kind: 'quote'; line: number; end: number; lines: string[] }
  | {
      kind: 'list';
      line: number;
      end: number;
      ordered: boolean;
      items: { line: number; text: string }[];
    }
  | { kind: 'hr'; line: number; end: number }
  | { kind: 'frontmatter'; line: number; end: number; text: string };

const LIST_ITEM = /^\s{0,3}(?:([-*+])|(\d{1,9})[.)])\s+(.*)$/;
const HR = /^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;

/** `## Title` → level 2. ATX only, requires the space (v1 parity). */
function heading(line: string): { level: number; text: string } | null {
  const m = /^(#{1,6}) (.*)$/.exec(line);
  return m ? { level: m[1].length, text: m[2].trim() } : null;
}

export function parseMarkdown(md: string): MdBlock[] {
  // CRLF sources (Windows-authored clippings): strip the trailing \r so
  // trim-based matches (frontmatter fence, hr, headings) see clean lines.
  const lines = md
    .split('\n')
    .map((l) => (l.endsWith('\r') ? l.slice(0, -1) : l));
  const blocks: MdBlock[] = [];
  let i = 0;

  // YAML frontmatter: only when the document opens with `---`.
  if (lines[0]?.trim() === '---') {
    let close = -1;
    for (let j = 1; j < lines.length; j += 1) {
      if (lines[j].trim() === '---') {
        close = j;
        break;
      }
    }
    if (close > 0) {
      blocks.push({
        kind: 'frontmatter',
        line: 1,
        end: close + 1,
        text: lines.slice(1, close).join('\n'),
      });
      i = close + 1;
    }
  }

  while (i < lines.length) {
    const line = lines[i];
    const lineNo = i + 1;

    if (line.trim() === '') {
      i += 1;
      continue;
    }

    // Fenced code — runs to the closing fence or EOF (v1: an unterminated
    // fence is closed rather than swallowing the rest of the page).
    if (line.trimStart().startsWith('```')) {
      const lang = line.trimStart().slice(3).trim();
      const body: string[] = [];
      let j = i + 1;
      while (j < lines.length && !lines[j].trimStart().startsWith('```')) {
        body.push(lines[j]);
        j += 1;
      }
      blocks.push({
        kind: 'code',
        line: lineNo,
        end: Math.min(j + 1, lines.length),
        lang,
        text: body.join('\n'),
      });
      i = j + 1;
      continue;
    }

    const h = heading(line);
    if (h) {
      blocks.push({ kind: 'heading', line: lineNo, end: lineNo, ...h });
      i += 1;
      continue;
    }

    if (HR.test(line)) {
      blocks.push({ kind: 'hr', line: lineNo, end: lineNo });
      i += 1;
      continue;
    }

    if (line.startsWith('>')) {
      const body: string[] = [];
      let j = i;
      while (j < lines.length && lines[j].startsWith('>')) {
        body.push(lines[j].replace(/^> ?/, ''));
        j += 1;
      }
      blocks.push({ kind: 'quote', line: lineNo, end: j, lines: body });
      i = j;
      continue;
    }

    const li = LIST_ITEM.exec(line);
    if (li) {
      const ordered = li[2] !== undefined;
      const items: { line: number; text: string }[] = [];
      let j = i;
      while (j < lines.length) {
        const m = LIST_ITEM.exec(lines[j]);
        if (!m) break;
        items.push({ line: j + 1, text: m[3] });
        j += 1;
      }
      blocks.push({ kind: 'list', line: lineNo, end: j, ordered, items });
      i = j;
      continue;
    }

    // Paragraph: consecutive non-blank, non-structural lines.
    const body: string[] = [line];
    let j = i + 1;
    while (
      j < lines.length &&
      lines[j].trim() !== '' &&
      !lines[j].trimStart().startsWith('```') &&
      !heading(lines[j]) &&
      !lines[j].startsWith('>') &&
      !LIST_ITEM.test(lines[j]) &&
      !HR.test(lines[j])
    ) {
      body.push(lines[j]);
      j += 1;
    }
    blocks.push({ kind: 'para', line: lineNo, end: j, lines: body });
    i = j;
  }

  return blocks;
}

// ------------------------------------------------------------------ inline

/** Only these link targets become <a>; everything else renders as text.
 * Relative/anchor targets are fine — `javascript:` and friends are not. */
export function safeLinkHref(url: string): string | null {
  const trimmed = url.trim();
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  if (trimmed.startsWith('#') || trimmed.startsWith('/')) return trimmed;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(trimmed) && trimmed !== '') return trimmed;
  return null;
}

const INLINE =
  /(!\[[^\]]*\]\([^)]*\))|(\[[^\]]+\]\([^)]*\))|(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)/;

/** Marker hook for the inline pass: plain-text runs are additionally split
 * on `pattern` (which MUST carry the `g` flag) and each match is handed to
 * `render`. Returning null leaves the token as plain text. The hook only
 * ever produces React elements from the caller — matched source text still
 * never becomes markup, so the XSS story is unchanged. Suppressed inside
 * link labels: an interactive marker nested in an <a> would be invalid
 * markup with double navigation. Used by the Ask page to turn `[kind:id]`
 * citations into live markers inside markdown. */
export interface InlineMarker {
  pattern: RegExp;
  render: (match: RegExpMatchArray, key: string) => ReactNode | null;
}

/** Source text → React nodes. Text stays text nodes (React escapes it). */
export function renderInline(
  text: string,
  keyPrefix = 'i',
  marker?: InlineMarker,
): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let k = 0;
  const pushPlain = (chunk: string) => {
    if (chunk === '') return;
    if (!marker) {
      out.push(chunk);
      return;
    }
    let last = 0;
    // matchAll clones the regex — the shared pattern's lastIndex is safe.
    for (const m of chunk.matchAll(marker.pattern)) {
      const at = m.index ?? 0;
      const node = marker.render(m, `${keyPrefix}-mk${k}`);
      if (node == null) continue;
      if (at > last) out.push(chunk.slice(last, at));
      out.push(node);
      k += 1;
      last = at + m[0].length;
    }
    if (last < chunk.length) out.push(chunk.slice(last));
  };
  while (rest.length > 0) {
    const m = INLINE.exec(rest);
    if (!m || m.index === undefined) {
      pushPlain(rest);
      break;
    }
    if (m.index > 0) pushPlain(rest.slice(0, m.index));
    const token = m[0];
    const key = `${keyPrefix}-${k}`;
    k += 1;

    if (m[1]) {
      // Image → alt-text placeholder; no remote loading in B2.
      const alt = /^!\[([^\]]*)\]/.exec(token)?.[1] ?? '';
      out.push(
        <span key={key} className="md-img-placeholder">
          [image{alt ? `: ${alt}` : ''}]
        </span>,
      );
    } else if (m[2]) {
      const lm = /^\[([^\]]+)\]\(([^)]*)\)$/.exec(token);
      const label = lm?.[1] ?? token;
      const href = safeLinkHref(lm?.[2] ?? '');
      if (href) {
        // No marker hook inside a link label: an interactive marker nested
        // in an anchor would be invalid markup with two competing
        // navigations — a citation-shaped label renders as the link's plain
        // text and the <a> keeps single-navigation semantics.
        out.push(
          <a key={key} href={href} target="_blank" rel="noreferrer">
            {renderInline(label, key)}
          </a>,
        );
      } else {
        out.push(label);
      }
    } else if (m[3]) {
      out.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (m[4]) {
      out.push(
        <strong key={key}>{renderInline(token.slice(2, -2), key, marker)}</strong>,
      );
    } else {
      out.push(
        <em key={key}>{renderInline(token.slice(1, -1), key, marker)}</em>,
      );
    }
    rest = rest.slice(m.index + token.length);
  }
  return out;
}

// ---------------------------------------------------------------- renderer

function blockBody(block: MdBlock, key: string, marker?: InlineMarker): ReactNode {
  switch (block.kind) {
    case 'heading': {
      const inner = renderInline(block.text, key, marker);
      // Page structure owns h1; source headings start at h2 (v1 parity).
      if (block.level === 1) return <h2>{inner}</h2>;
      if (block.level === 2) return <h3>{inner}</h3>;
      return <h4>{inner}</h4>;
    }
    case 'para':
      return (
        <p>
          {block.lines.map((l, n) => (
            <span key={`${key}-l${n}`}>
              {n > 0 && <br />}
              {renderInline(l, `${key}-l${n}`, marker)}
            </span>
          ))}
        </p>
      );
    case 'code':
      return (
        <pre data-lang={block.lang || undefined}>
          <code>{block.text}</code>
        </pre>
      );
    case 'quote':
      return (
        <blockquote>
          {block.lines.map((l, n) => (
            <span key={`${key}-q${n}`}>
              {n > 0 && <br />}
              {renderInline(l, `${key}-q${n}`, marker)}
            </span>
          ))}
        </blockquote>
      );
    case 'list': {
      const items = block.items.map((it, n) => (
        <li key={`${key}-it${n}`}>
          {renderInline(it.text, `${key}-it${n}`, marker)}
        </li>
      ));
      return block.ordered ? <ol>{items}</ol> : <ul>{items}</ul>;
    }
    case 'hr':
      return <hr />;
    case 'frontmatter':
      return (
        <pre className="md-frontmatter">
          <code>{block.text}</code>
        </pre>
      );
  }
}

export interface MarkdownViewProps {
  markdown: string;
  /** Source lines that grounded units anchor to — get a gutter `L<n>` mark. */
  anchoredLines?: ReadonlySet<number>;
  /** Line to scroll to and highlight (set when a unit anchor is clicked). */
  highlightLine?: number | null;
  /** False hides the line-number gutter column (chat answers — no source
   * lines to anchor to). Default true: the source reading view. */
  gutter?: boolean;
  /** Inline marker hook — see InlineMarker (Ask citation markers). */
  marker?: InlineMarker;
}

/** The ~720px-measure reading view with a line-number gutter. Blocks whose
 * source range contains an anchored line get a gutter mark; the highlight
 * line scrolls into view and flashes the containing block. */
export function MarkdownView({
  markdown,
  anchoredLines,
  highlightLine,
  gutter = true,
  marker,
}: MarkdownViewProps) {
  // Parsing walks the whole document — memoize per markdown string so
  // unrelated re-renders (highlight changes, anchor sets) don't re-parse.
  const blocks = useMemo(() => parseMarkdown(markdown), [markdown]);
  const rowRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  useEffect(() => {
    if (highlightLine == null) return;
    const idx = blocks.findIndex(
      (b) => b.line <= highlightLine && highlightLine <= b.end,
    );
    if (idx >= 0) {
      rowRefs.current
        .get(idx)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    // blocks derive from markdown; the ref map is rebuilt on each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightLine, markdown]);

  return (
    <div className={`md-preview${gutter ? '' : ' no-gut'}`}>
      {blocks.map((b, idx) => {
        const anchor = anchoredLines
          ? [...anchoredLines].find((l) => b.line <= l && l <= b.end)
          : undefined;
        const hit =
          highlightLine != null &&
          b.line <= highlightLine &&
          highlightLine <= b.end;
        return (
          <div
            key={`b${b.line}`}
            className={`md-row${hit ? ' md-hit' : ''}`}
            id={anchor != null ? `L${anchor}` : undefined}
            ref={(el) => {
              if (el) rowRefs.current.set(idx, el);
              else rowRefs.current.delete(idx);
            }}
          >
            {gutter && (
              <span className="gut">{anchor != null ? `L${anchor}` : ''}</span>
            )}
            <div className="md-block">{blockBody(b, `b${b.line}`, marker)}</div>
          </div>
        );
      })}
    </div>
  );
}
