/** Markdown reading view on top of react-markdown (mature engine) with a
 * lazy mermaid renderer for ```mermaid fences. Replaces the v1/v2 escape-
 * first mini renderer: README bodies are full of raw HTML, GFM tables,
 * images and diagrams — a hand-rolled parser could never keep up.
 *
 * Security model (unchanged in spirit):
 * - No dangerouslySetInnerHTML for NOTE CONTENT: react-markdown emits React
 *   elements only; embedded HTML is parsed by rehype-raw and whitelisted by
 *   rehype-sanitize (GitHub-derived schema + align). Scripts, handlers and
 *   unknown tags never become markup; URLs are scheme-filtered.
 * - Images render as real <img> in the live app (the operator's own local
 *   vault, Obsidian parity). The published static site (VITE_OVP_STATIC)
 *   keeps alt-text chips — the B2 no-remote-loading decision is scoped to
 *   where bodies actually ship.
 * - Mermaid SVG is the one innerHTML sink: mermaid runs with
 *   securityLevel 'strict' (labels sanitized) and the input is the local
 *   vault — documented trust boundary.
 * - Line anchors: a rehype plugin wraps top-level blocks in .md-row divs
 *   carrying data-ls/data-le from remark position info, so grounded-unit
 *   L<n> anchors and scroll-to-line survive the engine swap.
 */
import {
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PluggableList } from 'unified';
import { STATIC_MODE } from './api';

/** Citation interactivity for Ask answers: the vault-text plugin wraps
 * `[kind:id]` tokens in span.md-cite[data-cite]; the span component calls
 * render(citeId) — null keeps the literal marker text. Tokens inside links
 * and code are never marked (single-navigation invariant from v2). */
export interface CiteMarks {
  pattern: RegExp;
  render: (citeId: string) => ReactNode | null;
}

/** Sanitizer schema: GitHub defaults plus the few extras real clippings
 * need (align on blocks — README centering, language-* on code so mermaid
 * fences survive, mark/u/s/sub/sup, details/summary already included). */
const MD_SCHEMA = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames ?? []),
    'mark',
    'u',
    's',
    'details',
    'summary',
    'sub',
    'sup',
  ],
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code ?? []),
      ['className', /^language-[\w-]+$/],
    ],
    img: [...(defaultSchema.attributes?.img ?? []), 'align', 'width', 'height'],
    p: [...(defaultSchema.attributes?.p ?? []), 'align'],
    h1: [...(defaultSchema.attributes?.h1 ?? []), 'align'],
    h2: [...(defaultSchema.attributes?.h2 ?? []), 'align'],
    h3: [...(defaultSchema.attributes?.h3 ?? []), 'align'],
    h4: [...(defaultSchema.attributes?.h4 ?? []), 'align'],
    h5: [...(defaultSchema.attributes?.h5 ?? []), 'align'],
    h6: [...(defaultSchema.attributes?.h6 ?? []), 'align'],
    div: [...(defaultSchema.attributes?.div ?? []), 'align'],
    table: [...(defaultSchema.attributes?.table ?? []), 'align'],
    td: [...(defaultSchema.attributes?.td ?? []), 'align'],
    th: [...(defaultSchema.attributes?.th ?? []), 'align'],
  },
} as unknown as typeof defaultSchema;

// ---------------------------------------------------------------- hast bits

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  children?: HastNode[];
  properties?: Record<string, unknown>;
  position?: { start: { line: number }; end: { line: number } };
}

/** Wrap every TOP-LEVEL element in <div class="md-row" data-ls data-le> so
 * the gutter/anchor layer can map rendered blocks back to source lines.
 * Runs AFTER sanitize (data-* and class would otherwise be stripped). */
function rehypeLineRows() {
  return (tree: HastNode) => {
    if (!tree.children) return;
    tree.children = tree.children.map((child) => {
      if (child.type !== 'element') return child;
      return {
        type: 'element',
        tagName: 'div',
        properties: {
          className: ['md-row'],
          dataLs: child.position?.start.line ?? 0,
          dataLe: child.position?.end.line ?? 0,
        },
        children: [
          {
            type: 'element',
            tagName: 'div',
            properties: { className: ['md-block'] },
            children: [child],
          },
        ],
      };
    });
  };
}

/** Text-node transforms the engine doesn't know: citation markers (Ask),
 * Obsidian [[wiki-links]] (display text, portal can't resolve them) and
 * ==highlights==. Never fires inside a/code/pre — a marked token nested in
 * an anchor would be invalid markup with two competing navigations. */
function rehypeVaultText(citePattern: RegExp | null) {
  const SKIP = new Set(['a', 'code', 'pre', 'script', 'style']);
  const citeRx = citePattern
    ? new RegExp(
        citePattern.source,
        citePattern.flags.includes('g') ? citePattern.flags : `${citePattern.flags}g`,
      )
    : null;
  const WIKI = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;
  const MARK = /==([^=\n]+)==/g;

  const applyRx = (
    node: HastNode,
    rx: RegExp,
    make: (m: RegExpMatchArray) => HastNode,
  ): HastNode[] => {
    if (node.type !== 'text' || node.value == null) return [node];
    const out: HastNode[] = [];
    let last = 0;
    for (const m of node.value.matchAll(rx)) {
      const at = m.index ?? 0;
      if (at > last) out.push({ type: 'text', value: node.value.slice(last, at) });
      out.push(make(m));
      last = at + m[0].length;
    }
    if (out.length === 0) return [node];
    if (last < node.value.length) {
      out.push({ type: 'text', value: node.value.slice(last) });
    }
    return out;
  };

  const splitText = (value: string): HastNode[] => {
    let nodes: HastNode[] = [{ type: 'text', value }];
    if (citeRx) {
      nodes = nodes.flatMap((n) =>
        applyRx(n, citeRx, (m) => ({
          type: 'element',
          tagName: 'span',
          properties: { className: ['md-cite'], dataCite: m[1] ?? m[0] },
          children: [{ type: 'text', value: m[0] }],
        })),
      );
    }
    nodes = nodes.flatMap((n) =>
      applyRx(n, WIKI, (m) => ({
        type: 'element',
        tagName: 'span',
        properties: { className: ['md-wikilink'] },
        children: [{ type: 'text', value: (m[2] ?? m[1]).trim() }],
      })),
    );
    nodes = nodes.flatMap((n) =>
      applyRx(n, MARK, (m) => ({
        type: 'element',
        tagName: 'mark',
        properties: {},
        children: [{ type: 'text', value: m[1] }],
      })),
    );
    return nodes;
  };

  const walk = (node: HastNode, blocked: boolean) => {
    if (!node.children) return;
    const b = blocked || (node.tagName != null && SKIP.has(node.tagName));
    node.children = node.children.flatMap((c) => {
      if (c.type === 'text' && !b) return splitText(c.value ?? '');
      walk(c, b);
      return [c];
    });
  };

  return (tree: HastNode) => walk(tree, false);
}

/** Frontmatter occupies lines 1..close; those lines carry unit anchors, so
 * the body keeps its line count — frontmatter lines are BLANKED, not cut,
 * and rendered separately as a collapsed details block. */
export function splitFrontmatter(md: string): {
  fmText: string | null;
  body: string;
} {
  const lines = md.split('\n');
  if (lines[0]?.trim() !== '---') return { fmText: null, body: md };
  for (let j = 1; j < lines.length; j += 1) {
    if (lines[j].trim() === '---') {
      return {
        fmText: lines.slice(1, j).join('\n'),
        body: lines.map((l, i) => (i <= j ? '' : l)).join('\n'),
      };
    }
  }
  return { fmText: null, body: md };
}

// ---------------------------------------------------------------- context

interface MdCtxValue {
  anchored?: ReadonlySet<number>;
  highlight?: number | null;
  gutter: boolean;
  register: (key: string, el: HTMLElement | null, ls: number, le: number) => void;
  cite?: CiteMarks;
  resolveImageSrc?: (src: string) => string;
}
const MdCtx = createContext<MdCtxValue>({ gutter: true, register: () => {} });

/** One top-level source block: gutter tick for anchored lines, highlight
 * flash for the jump target. Structure matches the v2 CSS grid. */
function Row({
  ls,
  le,
  children,
}: {
  ls: number;
  le: number;
  children: ReactNode;
}) {
  const ctx = useContext(MdCtx);
  const anchor = ctx.anchored
    ? [...ctx.anchored].find((l) => ls <= l && l <= le)
    : undefined;
  const hit = ctx.highlight != null && ls <= ctx.highlight && ctx.highlight <= le;
  return (
    <div
      ref={(el) => ctx.register(`${ls}:${le}`, el, ls, le)}
      id={anchor != null ? `L${anchor}` : undefined}
      className={`md-row${hit ? ' md-hit' : ''}`}
      data-ls={ls}
      data-le={le}
    >
      {ctx.gutter && (
        <span className="gut">{anchor != null ? `L${anchor}` : ''}</span>
      )}
      {children}
    </div>
  );
}

let mmdSeq = 0;

/** ```mermaid fence → SVG via the mermaid engine, loaded on demand so the
 * main bundle stays lean. Falls back to the raw code block on error. */
function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { default: mermaid } = await import('mermaid');
        const dark = document.documentElement.dataset.theme === 'dark';
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: dark ? 'dark' : 'neutral',
        });
        const { svg } = await mermaid.render(`ovp-mmd-${(mmdSeq += 1)}`, code);
        if (!cancelled && ref.current) {
          // Documented trust boundary (module header): strict mode + own
          // vault. Never point this at untrusted markdown.
          ref.current.innerHTML = svg;
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code]);
  if (failed) {
    return (
      <pre>
        <code>{code}</code>
      </pre>
    );
  }
  return <div className="md-mermaid" ref={ref} />;
}

// ------------------------------------------------------------- components

/** README centering uses the deprecated align attribute — map it to a real
 * style (browsers ignore bare align on modern elements). */
function alignOf(props: Record<string, unknown>): CSSProperties | undefined {
  const a = props.align;
  return typeof a === 'string' && ['center', 'right', 'left'].includes(a)
    ? { textAlign: a as 'center' | 'right' | 'left' }
    : undefined;
}

type AnyProps = Record<string, unknown> & { children?: ReactNode };

const components: Components = {
  div(props) {
    const p = props as unknown as AnyProps;
    if (p.className === 'md-row') {
      const ls = Number(p['data-ls'] ?? p.dataLs ?? 0);
      const le = Number(p['data-le'] ?? p.dataLe ?? 0);
      return (
        <Row ls={ls} le={le}>
          {p.children}
        </Row>
      );
    }
    return (
      <div className={typeof p.className === 'string' ? p.className : undefined} style={alignOf(p)}>
        {p.children}
      </div>
    );
  },
  // Page structure owns h1 — source headings shift down (v1 parity).
  h1(props) {
    const p = props as unknown as AnyProps;
    return <h2 style={alignOf(p)}>{p.children}</h2>;
  },
  h2(props) {
    const p = props as unknown as AnyProps;
    return <h3 style={alignOf(p)}>{p.children}</h3>;
  },
  h3(props) {
    const p = props as unknown as AnyProps;
    return <h4 style={alignOf(p)}>{p.children}</h4>;
  },
  h4(props) {
    const p = props as unknown as AnyProps;
    return <h4 style={alignOf(p)}>{p.children}</h4>;
  },
  h5(props) {
    const p = props as unknown as AnyProps;
    return <h4 style={alignOf(p)}>{p.children}</h4>;
  },
  h6(props) {
    const p = props as unknown as AnyProps;
    return <h4 style={alignOf(p)}>{p.children}</h4>;
  },
  p(props) {
    const p2 = props as unknown as AnyProps;
    return <p style={alignOf(p2)}>{p2.children}</p>;
  },
  a(props) {
    const p = props as unknown as AnyProps & { href?: unknown };
    const href = typeof p.href === 'string' ? p.href : undefined;
    const external = href != null && /^https?:/i.test(href);
    return (
      <a
        href={href}
        {...(external ? { target: '_blank', rel: 'noreferrer' } : {})}
      >
        {p.children}
      </a>
    );
  },
  img(props) {
    const ctx = useContext(MdCtx);
    const p = props as unknown as { src?: unknown; alt?: unknown };
    const raw = typeof p.src === 'string' ? p.src : '';
    const resolved = raw && ctx.resolveImageSrc ? ctx.resolveImageSrc(raw) : raw;
    const altText = typeof p.alt === 'string' ? p.alt : '';
    if (STATIC_MODE || !resolved) {
      // B2: the published static site never hot-loads remote imagery.
      return (
        <span className="md-img-placeholder">
          [image{altText ? `: ${altText}` : ''}]
        </span>
      );
    }
    return <img className="md-img" src={resolved} alt={altText} loading="lazy" />;
  },
  pre(props) {
    const p = props as unknown as AnyProps;
    if (isValidElement(p.children)) {
      const cp = p.children.props as { className?: string; children?: ReactNode };
      if (/\blanguage-mermaid\b/.test(cp.className ?? '')) {
        return (
          <MermaidBlock code={String(cp.children ?? '').replace(/\n$/, '')} />
        );
      }
    }
    return <pre>{p.children}</pre>;
  },
  span(props) {
    const ctx = useContext(MdCtx);
    const p = props as unknown as AnyProps;
    if (p.className === 'md-cite' && ctx.cite) {
      const id = p['data-cite'] ?? p.dataCite;
      const rendered =
        typeof id === 'string' ? ctx.cite.render(id) : null;
      return <>{rendered ?? p.children}</>;
    }
    return (
      <span className={typeof p.className === 'string' ? p.className : undefined}>
        {p.children}
      </span>
    );
  },
};

// ---------------------------------------------------------------- view

export interface MarkdownViewProps {
  markdown: string;
  /** Source lines that grounded units anchor to — get a gutter `L<n>` mark. */
  anchoredLines?: ReadonlySet<number>;
  /** Line to scroll to and highlight (set when a unit anchor is clicked). */
  highlightLine?: number | null;
  /** False hides the line-number gutter column (chat answers). */
  gutter?: boolean;
  /** Summary label for the collapsed frontmatter block (localized). */
  frontmatterLabel?: string;
  /** Citation interactivity for Ask answers — see CiteMarks. */
  citeMarks?: CiteMarks;
  /** Resolve relative image srcs (READMEs reference repo-relative paths) —
   * SourceDetailPage rewrites them against the note's origin URL. */
  resolveImageSrc?: (src: string) => string;
}

/** The ~720px-measure reading view with a line-number gutter. */
export function MarkdownView({
  markdown,
  anchoredLines,
  highlightLine,
  gutter = true,
  frontmatterLabel = 'metadata',
  citeMarks,
  resolveImageSrc,
}: MarkdownViewProps) {
  const fm = useMemo(() => splitFrontmatter(markdown), [markdown]);
  const rowsRef = useRef(new Map<string, { ls: number; le: number; el: HTMLElement }>());

  const register = useCallback(
    (key: string, el: HTMLElement | null, ls: number, le: number) => {
      if (el) rowsRef.current.set(key, { ls, le, el });
      else rowsRef.current.delete(key);
    },
    [],
  );

  useEffect(() => {
    if (highlightLine == null) return;
    for (const { ls, le, el } of rowsRef.current.values()) {
      if (ls <= highlightLine && highlightLine <= le) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        break;
      }
    }
  }, [highlightLine, markdown]);

  const ctx = useMemo<MdCtxValue>(
    () => ({
      anchored: anchoredLines,
      highlight: highlightLine,
      gutter,
      register,
      cite: citeMarks,
      resolveImageSrc,
    }),
    [anchoredLines, highlightLine, gutter, register, citeMarks, resolveImageSrc],
  );

  const rehypePlugins = useMemo(
    () =>
      [
        rehypeRaw,
        [rehypeSanitize, MD_SCHEMA],
        [rehypeVaultText, citeMarks?.pattern ?? null],
        rehypeLineRows,
      ] as unknown as PluggableList,
    [citeMarks?.pattern],
  );

  return (
    <MdCtx.Provider value={ctx}>
      <div className={`md-preview${gutter ? '' : ' no-gut'}`}>
        {fm.fmText != null && (
          <details className="md-frontmatter">
            <summary>{frontmatterLabel}</summary>
            <pre>
              <code>{fm.fmText}</code>
            </pre>
          </details>
        )}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          remarkRehypeOptions={{ allowDangerousHtml: true }}
          rehypePlugins={rehypePlugins}
          components={components}
        >
          {fm.body}
        </ReactMarkdown>
      </div>
    </MdCtx.Provider>
  );
}
