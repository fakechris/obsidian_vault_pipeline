/** Companion / analysis links for library sources (GitHub, arXiv, …).
 *
 * Pure URL derivation from `source.url` + entity ids — no network. The
 * Source detail page renders these as quick-jump chips next to the primary
 * URL so operators can open zread / deepwiki / ar5iv / alphaXiv without
 * hand-crafting paths.
 */

export type CompanionKind = 'github' | 'arxiv';

export interface CompanionLink {
  /** Stable key for React lists + i18n (`source.companion.zread`). */
  id: string;
  kind: CompanionKind;
  /** Human label on the chip. */
  label: string;
  href: string;
  /** Short title attribute. */
  title: string;
}

export interface ParsedGithub {
  owner: string;
  repo: string;
}

export interface ParsedArxiv {
  /** Canonical abs id without version suffix when possible (2504.19413). */
  id: string;
}

/** Parse `owner/repo` from a github.com URL or `github:owner/repo` entity. */
export function parseGithub(urlOrEntity?: string | null): ParsedGithub | null {
  if (!urlOrEntity) return null;
  const ent = /^github:([^/\s]+)\/([^/\s#?]+)/i.exec(urlOrEntity.trim());
  if (ent) {
    return { owner: ent[1], repo: ent[2].replace(/\.git$/i, '') };
  }
  // Exactly two path segments (owner/repo), optional .git / trailing slash /
  // query / hash — not issues/pulls/tree/...
  const m =
    /^https?:\/\/(?:www\.)?github\.com\/([^/\s#?]+)\/([^/\s#?]+?)(?:\.git)?\/?(?:[?#].*)?$/i.exec(
      urlOrEntity.trim(),
    );
  if (!m) return null;
  const owner = m[1];
  const repo = m[2];
  if (!owner || !repo || owner === 'orgs' || owner === 'settings') return null;
  if (['issues', 'pull', 'pulls', 'actions', 'settings', 'tree', 'blob'].includes(repo)) {
    return null;
  }
  return { owner, repo };
}

/** Parse arXiv id from abs/pdf/html URL, bare id, or `arxiv:id` entity. */
export function parseArxiv(urlOrEntity?: string | null): ParsedArxiv | null {
  if (!urlOrEntity) return null;
  const s = urlOrEntity.trim();
  const ent = /^arxiv:(.+)$/i.exec(s);
  if (ent) return normalizeArxivId(ent[1]);
  const fromUrl =
    /arxiv\.org\/(?:abs|pdf|html)\/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z-]+\/[0-9]{7}(?:v\d+)?)/i.exec(
      s,
    );
  if (fromUrl) return normalizeArxivId(fromUrl[1]);
  // Bare modern id.
  if (/^[0-9]{4}\.[0-9]{4,5}(v\d+)?$/i.test(s)) return normalizeArxivId(s);
  return null;
}

function normalizeArxivId(raw: string): ParsedArxiv {
  // Drop version for companion hosts that prefer the base id; keep if needed.
  const id = raw.replace(/v\d+$/i, '');
  return { id };
}

/**
 * Build companion chips from the source's primary URL + entity list.
 * Dedupes by href; prefers entity-derived ids when URL is a non-canonical form.
 */
export function companionLinks(
  url?: string | null,
  entities?: readonly string[] | null,
): CompanionLink[] {
  const out: CompanionLink[] = [];
  const seen = new Set<string>();

  const push = (link: CompanionLink) => {
    if (seen.has(link.href)) return;
    seen.add(link.href);
    out.push(link);
  };

  const gh =
    parseGithub(url) ??
    (entities ?? []).map(parseGithub).find((g): g is ParsedGithub => g != null);
  if (gh) {
    const { owner, repo } = gh;
    push({
      id: 'github',
      kind: 'github',
      label: 'GitHub',
      href: `https://github.com/${owner}/${repo}`,
      title: `${owner}/${repo}`,
    });
    push({
      id: 'zread',
      kind: 'github',
      label: 'zread',
      href: `https://zread.ai/github/${owner}/${repo}`,
      title: `zread.ai · ${owner}/${repo}`,
    });
    push({
      id: 'deepwiki',
      kind: 'github',
      label: 'DeepWiki',
      href: `https://deepwiki.com/${owner}/${repo}`,
      title: `deepwiki · ${owner}/${repo}`,
    });
  }

  const ax =
    parseArxiv(url) ??
    (entities ?? []).map(parseArxiv).find((a): a is ParsedArxiv => a != null);
  if (ax) {
    const { id } = ax;
    push({
      id: 'arxiv',
      kind: 'arxiv',
      label: 'arXiv',
      href: `https://arxiv.org/abs/${id}`,
      title: `arXiv:${id}`,
    });
    push({
      id: 'ar5iv',
      kind: 'arxiv',
      label: 'ar5iv',
      href: `https://ar5iv.labs.arxiv.org/html/${id}`,
      title: `ar5iv HTML · ${id}`,
    });
    push({
      id: 'alphaxiv',
      kind: 'arxiv',
      label: 'alphaXiv',
      href: `https://www.alphaxiv.org/abs/${id}`,
      title: `alphaXiv · ${id}`,
    });
  }

  return out;
}

/**
 * Heuristic: is this body predominantly English / Latin (offer translation)?
 * CJK-heavy notes return false. Short / empty bodies return false.
 */
export function isPrimarilyEnglish(text: string, minChars = 80): boolean {
  const body = text.replace(/^---[\s\S]*?---\n?/, '').trim();
  if (body.length < minChars) return false;
  let cjk = 0;
  let latin = 0;
  for (const ch of body) {
    const c = ch.codePointAt(0) ?? 0;
    if (
      (c >= 0x4e00 && c <= 0x9fff) ||
      (c >= 0x3400 && c <= 0x4dbf) ||
      (c >= 0x3040 && c <= 0x30ff)
    ) {
      cjk += 1;
    } else if ((c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a)) {
      latin += 1;
    }
  }
  const letters = cjk + latin;
  if (letters < 40) return false;
  return latin / letters >= 0.85;
}
