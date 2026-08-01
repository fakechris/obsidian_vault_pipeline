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

/** One frontmatter field after light YAML-ish parse (vault notes, not full YAML). */
export interface FrontmatterField {
  key: string;
  /** Scalar string or list of scalars (author: - "[[x]]"). */
  values: string[];
}

/** Parse simple note frontmatter into structured fields for HTML property view.
 * Handles `key: value`, empty `key:` + `- list` items, and strips quotes. */
export function parseFrontmatterFields(fmText: string): FrontmatterField[] {
  const lines = fmText.split('\n');
  const out: FrontmatterField[] = [];
  let i = 0;
  const stripQ = (s: string) => {
    const t = s.trim();
    if (
      (t.startsWith('"') && t.endsWith('"')) ||
      (t.startsWith("'") && t.endsWith("'"))
    ) {
      return t.slice(1, -1);
    }
    return t;
  };
  while (i < lines.length) {
    const line = lines[i];
    const m = /^([A-Za-z0-9_/-]+):\s*(.*)$/.exec(line);
    if (!m) {
      i += 1;
      continue;
    }
    const key = m[1];
    const rest = m[2];
    if (rest === '' || rest === '|' || rest === '>') {
      const values: string[] = [];
      i += 1;
      while (i < lines.length) {
        const li = lines[i];
        const list = /^\s*-\s+(.*)$/.exec(li);
        if (list) {
          values.push(stripQ(list[1]));
          i += 1;
          continue;
        }
        // Indented continuation of a block scalar (rare in our notes).
        if (/^\s+\S/.test(li) && !/^([A-Za-z0-9_/-]+):/.test(li.trim())) {
          values.push(stripQ(li));
          i += 1;
          continue;
        }
        break;
      }
      out.push({ key, values: values.length > 0 ? values : [''] });
      continue;
    }
    out.push({ key, values: [stripQ(rest)] });
    i += 1;
  }
  return out;
}

/** Render a single frontmatter cell value: URLs → links, [[wikilinks]] → chips. */
function FmValue({ text }: { text: string }) {
  if (!text) return <span className="fm-empty">—</span>;
  if (/^https?:\/\//i.test(text)) {
    return (
      <a className="fm-link" href={text} target="_blank" rel="noreferrer">
        {text}
      </a>
    );
  }
  // Wikilink-only cell: [[name]]
  const wikiOnly = /^\[\[([^\]]+)\]\]$/.exec(text);
  if (wikiOnly) {
    return <span className="fm-wiki">{wikiOnly[1]}</span>;
  }
  // Mix of text + [[wiki]]
  if (text.includes('[[')) {
    const parts: ReactNode[] = [];
    const re = /\[\[([^\]]+)\]\]/g;
    let last = 0;
    let m: RegExpExecArray | null;
    let k = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) parts.push(text.slice(last, m.index));
      parts.push(
        <span className="fm-wiki" key={`w${k++}`}>
          {m[1]}
        </span>,
      );
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push(text.slice(last));
    return <>{parts}</>;
  }
  return <>{text}</>;
}

/** Structured properties panel — replaces raw YAML wall for operators. */
export function FrontmatterProps({
  fmText,
  label,
  defaultOpen = true,
}: {
  fmText: string;
  label: string;
  defaultOpen?: boolean;
}) {
  const fields = useMemo(() => parseFrontmatterFields(fmText), [fmText]);
  if (fields.length === 0) {
    return (
      <details className="md-frontmatter" open={defaultOpen}>
        <summary>{label}</summary>
        <pre>
          <code>{fmText}</code>
        </pre>
      </details>
    );
  }
  return (
    <details className="md-frontmatter" open={defaultOpen}>
      <summary>{label}</summary>
      <dl className="fm-props">
        {fields.map((f) => (
          <div className="fm-row" key={f.key}>
            <dt className="fm-key">{f.key}</dt>
            <dd className="fm-val">
              {f.values.length > 1 ? (
                <ul className="fm-list">
                  {f.values.map((v, i) => (
                    <li key={`${f.key}-${i}`}>
                      <FmValue text={v} />
                    </li>
                  ))}
                </ul>
              ) : (
                <FmValue text={f.values[0] ?? ''} />
              )}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

// ---------------------------------------------------------------- context

interface MdCtxValue {
  anchored?: ReadonlySet<number>;
  highlight?: number | null;
  gutter: boolean;
  register: (key: string, el: HTMLElement | null, ls: number, le: number) => void;
  cite?: CiteMarks;
  /** Relative image src → ordered candidates; the <img> error handler
   * walks the list (see MdImg). */
  imageSrcCandidates?: (src: string) => string[];
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
/** Last theme passed to mermaid.initialize — re-init only when it flips so
 * concurrent diagrams don't race initialize() on every mount. */
let mmdTheme: 'dark' | 'neutral' | null = null;

async function ensureMermaid() {
  const { default: mermaid } = await import('mermaid');
  const dark =
    typeof document !== 'undefined' &&
    document.documentElement.dataset.theme === 'dark';
  const theme: 'dark' | 'neutral' = dark ? 'dark' : 'neutral';
  if (mmdTheme !== theme) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme,
    });
    mmdTheme = theme;
  }
  return mermaid;
}

/** <img> with a candidate walk: relative srcs get [primary, fallback] from
 * ctx.imageSrcCandidates (e.g. GitHub → raw.githubusercontent first, vault
 * attachments second; clippings the other way round). Each onError steps to
 * the next candidate; when all fail (dead CDN link, missing attachment) the
 * alt chip beats the browser's broken-image icon. */
function MdImg({ raw, alt }: { raw: string; alt: string }) {
  const ctx = useContext(MdCtx);
  const [idx, setIdx] = useState(0);
  const candidates =
    raw && ctx.imageSrcCandidates ? ctx.imageSrcCandidates(raw) : [raw];
  const src = candidates[idx];
  if (STATIC_MODE || !src) {
    // B2: the published static site never hot-loads remote imagery.
    return (
      <span className="md-img-placeholder">
        [image{alt ? `: ${alt}` : ''}]
      </span>
    );
  }
  return (
    <img
      className="md-img"
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setIdx((i) => i + 1)}
    />
  );
}

/** Truncated READMEs (ingest cut mid-fence) leave an unclosed ```mermaid
 * block whose "code" runs to end-of-document — the trailing prose is not a
 * diagram and mermaid fails with "Parse error on line N". Everything before
 * N parsed fine, so cut the tail and retry once. Returns null when the
 * error carries no usable line number. Exported for tests. */
export function trimAtMermaidErrorLine(code: string, err: unknown): string | null {
  const m = /line (\d+)/i.exec(String((err as Error)?.message ?? err));
  if (!m) return null;
  const lineNo = Number(m[1]);
  if (!Number.isFinite(lineNo) || lineNo < 2) return null;
  const cut = code.split('\n').slice(0, lineNo - 1).join('\n');
  return cut.trim() ? cut : null;
}

/** ```mermaid fence → SVG via the mermaid engine, loaded on demand so the
 * main bundle stays lean. Falls back to the raw code block on error.
 * The render host stays mounted (hidden on failure) so a later good `code`
 * can write into `ref` — unmounting the host on fail left ref null forever. */
function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const id = `ovp-mmd-${(mmdSeq += 1)}`;
    setFailed(false);
    if (ref.current) ref.current.innerHTML = '';
    void (async () => {
      try {
        const mermaid = await ensureMermaid();
        let src = code;
        try {
          await mermaid.parse(src);
        } catch (e) {
          // Heal truncated-ingest tails (see trimAtMermaidErrorLine).
          const cut = trimAtMermaidErrorLine(src, e);
          if (cut == null) throw e;
          src = cut;
          await mermaid.parse(src);
        }
        const { svg } = await mermaid.render(id, src);
        if (!cancelled && ref.current) {
          // Documented trust boundary (module header): strict mode + own
          // vault. Never point this at untrusted markdown.
          ref.current.innerHTML = svg;
          setFailed(false);
        }
      } catch {
        // mermaid.render/parse append their error graphic to document.body
        // under the render id — remove it or the "Syntax error" wall
        // lingers on the page next to our raw-code fallback.
        for (const el of document.querySelectorAll(`#${id}, #d${id}`)) {
          el.remove();
        }
        if (!cancelled) {
          if (ref.current) ref.current.innerHTML = '';
          setFailed(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code]);
  return (
    <>
      {failed && (
        <pre>
          <code>{code}</code>
        </pre>
      )}
      <div
        className="md-mermaid"
        ref={ref}
        style={failed ? { display: 'none' } : undefined}
        aria-hidden={failed || undefined}
      />
    </>
  );
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
    const p = props as unknown as { src?: unknown; alt?: unknown };
    const raw = typeof p.src === 'string' ? p.src : '';
    const altText = typeof p.alt === 'string' ? p.alt : '';
    // key: a changed src restarts the candidate walk from the top.
    return <MdImg key={raw} raw={raw} alt={altText} />;
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
  /** Relative image srcs → ordered candidates (READMEs reference
   * repo-relative paths, clippings reference vault attachments) —
   * SourceDetailPage builds the chain from the note's source URL + path. */
  imageSrcCandidates?: (src: string) => string[];
}

/** Paths that live inside the vault (clipper attachments, PARA roots) —
 * joining them onto a remote article URL always 404s, so skip that hop. */
function looksLikeVaultPath(path: string): boolean {
  return (
    /^(?:\d{2}-|attachments\/|\.ovp\/)/i.test(path) ||
    path.includes('/attachments/')
  );
}

/** Build the imageSrcCandidates chain for a source note. Relative image
 * paths come in two shapes: GitHub READMEs point INTO THE REPO
 * (`./assets/logo.png` → raw.githubusercontent.com/<owner>/<repo>/HEAD/…)
 * while web clippings point INTO THE VAULT (`50-Inbox/01-Raw/attachments/
 * …/img-x.png` → the server's /api/file/ endpoint, with the note's own
 * directory as second base for note-relative refs). Neither is knowable
 * from the path alone, so the chain tries the likely base first and MdImg
 * walks the rest on onError. Absolute/data: srcs pass through as a
 * single-candidate chain. */
export function sourceImageCandidates(
  sourceUrl?: string,
  noteRelPath?: string,
): (src: string) => string[] {
  const gh =
    sourceUrl &&
    /^https?:\/\/github\.com\/([^/]+)\/([^/#?]+)/i.exec(sourceUrl);
  const remoteBase = gh
    ? `https://raw.githubusercontent.com/${gh[1]}/${gh[2].replace(/\.git$/, '')}/HEAD/`
    : sourceUrl;
  return (src) => {
    if (!src) return [];
    // Protocol-relative CDN URLs (`//cdn…/x.png`) are absolute, not vault.
    if (src.startsWith('//')) return [`https:${src}`];
    if (/^(?:https?:|data:|blob:|#)/i.test(src)) return [src];
    // Strip "./" and a leading "/" (Obsidian vault-root absolute). The
    // server's plain-relative guard rejects CurDir and RootDir outright.
    const vaultPath = src.replace(/^(\.\/)+/, '').replace(/^\//, '');
    if (!vaultPath) return [];
    const vault =
      `/api/file/${vaultPath.split('/').map(encodeURIComponent).join('/')}` +
      (noteRelPath ? `?note=${encodeURIComponent(noteRelPath)}` : '');
    // Vault-shaped paths never resolve against a remote article URL.
    // GitHub READMEs still try raw.githubusercontent.com first (repo assets).
    let remote: string | null = null;
    if (remoteBase && (gh || !looksLikeVaultPath(vaultPath))) {
      try {
        remote = new URL(vaultPath, remoteBase).href;
      } catch {
        remote = null;
      }
    }
    const chain = gh ? [remote, vault] : [vault, remote];
    return chain.filter((s): s is string => typeof s === 'string' && s !== '');
  };
}

/** The ~720px-measure reading view with a line-number gutter. */
export function MarkdownView({
  markdown,
  anchoredLines,
  highlightLine,
  gutter = true,
  frontmatterLabel = 'metadata',
  citeMarks,
  imageSrcCandidates,
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
      imageSrcCandidates,
    }),
    [anchoredLines, highlightLine, gutter, register, citeMarks, imageSrcCandidates],
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
          <FrontmatterProps fmText={fm.fmText} label={frontmatterLabel} />
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
