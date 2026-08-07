/** Parse `.ovp/chats/<ts>.md` transcripts written by `ovp-memory::ask`.
 *
 * Live Ask renders Q/A as chat bubbles; saved files are markdown dumps that
 * also contain Evidence / Verification sections. History view reuses the
 * live layout by extracting only the Q/A turns.
 */

export interface ChatTurn {
  question: string;
  answer: string;
}

/**
 * Agent sessions store the LLM user message (may include the full
 * `[FOCUS CONTEXT …] … [USER QUESTION]` pack). Product UI must only show
 * the human question — never the injected body/memory/crystal dump.
 */
export function displayUserQuestion(raw: string): string {
  const text = raw ?? '';
  const marker = '[USER QUESTION]';
  const idx = text.indexOf(marker);
  if (idx >= 0) {
    return text.slice(idx + marker.length).trim();
  }
  // Defensive: focus pack without marker must not paint as the Q bubble.
  if (text.trimStart().startsWith('[FOCUS CONTEXT')) {
    const lines = text.split('\n');
    const last = [...lines].reverse().find((l) => l.trim() && !l.startsWith('#'));
    return (last ?? '').trim() || '…';
  }
  return text.trim();
}

/** Citation keys the answer text cites, in first-appearance order.
 * Mirrors the Ask page tokenizer (claim/card/unit + bare ck-). */
const CITE_RE =
  /\[\s*((?:claim|card|unit|source):[^\]\n]+?|ck-[^\]\s:]+)\s*\]/g;

export function normalizeCiteToken(token: string): string {
  return token.startsWith('ck-') ? `claim:${token}` : token;
}

/** Best-effort portal link for a citation key when replaying a saved chat
 * (no live evidence sidecar). Claims deep-link by key; cards/units have no
 * stable sha without the index. */
/** Ground unit/card/source cites onto a focused source's library page when
 * the generic index lookup has no link (modern unit ids have no standalone
 * page — their home is the source's memory tab). Shared by the focus chat
 * dock and Ask-page replay of focused sessions. */
export function groundCitesOnSource<
  T extends { id: string; kind?: string; link_target?: string | null },
>(cites: T[], sha: string): T[] {
  return cites.map((c) => {
    if (c.link_target) return c;
    const kind = c.kind || (c.id.includes(':') ? c.id.slice(0, c.id.indexOf(':')) : '');
    if (kind === 'source') {
      const token = c.id.slice(c.id.indexOf(':') + 1).split(/\s+/)[0] ?? '';
      if (!token || token === sha || token.startsWith(sha.slice(0, 12))) {
        return { ...c, link_target: `/library/${encodeURIComponent(sha)}` };
      }
    }
    if (kind === 'unit' || kind === 'card') {
      return {
        ...c,
        link_target: `/library/${encodeURIComponent(sha)}?tab=memory`,
      };
    }
    return c;
  });
}

/** Focus markers from a saved chat's header (`ovp:focus_source` /
 * `ovp:focus_theme`) — the replay surface needs them to ground citations
 * exactly like the live dock did. */
export function parseChatFocus(md: string): { sha: string | null; theme: string | null } {
  let sha: string | null = null;
  let theme: string | null = null;
  for (const line of md.split('\n', 40)) {
    const l = line.trim();
    const src = l.match(/^<!-- ovp:focus_source=(.+?) -->$/);
    if (src) sha = src[1].trim() || null;
    const th = l.match(/^<!-- ovp:focus_theme=(.+?) -->$/);
    if (th) theme = th[1].trim() || null;
  }
  return { sha, theme };
}

export function citeLinkTarget(id: string): string | null {
  if (id.startsWith('claim:')) {
    const key = id.slice('claim:'.length);
    return key ? `/knowledge#${encodeURIComponent(key)}` : null;
  }
  if (id.startsWith('source:')) {
    const sha = id.slice('source:'.length);
    return sha ? `/library/${encodeURIComponent(sha)}` : null;
  }
  return null;
}

export function citationsInOrder(answer: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  CITE_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = CITE_RE.exec(answer)) !== null) {
    const id = normalizeCiteToken(m[1]);
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/**
 * Extract Q/A turns from a saved chat markdown body.
 *
 * Writer format (ovp-memory):
 *   **Q:** …
 *   **A:** …
 *   ---
 *   ## Evidence
 *   …
 *   ## Verification
 *   …
 *   Context hits: N
 *   (optional next turn after another ---)
 */
export function parseChatTranscript(md: string): ChatTurn[] {
  const turns: ChatTurn[] = [];
  // Split on **Q:** markers; first chunk is header (`# Ask — …`).
  const parts = md.split(/\*\*Q:\*\*/);
  for (let i = 1; i < parts.length; i += 1) {
    const part = parts[i];
    const aMatch = /\*\*A:\*\*/.exec(part);
    if (!aMatch || aMatch.index == null) continue;
    const question = part.slice(0, aMatch.index).trim();
    let answerPart = part.slice(aMatch.index + aMatch[0].length);
    // Drop trailing evidence / verification dump for this turn.
    const cut = answerPart.search(
      /\n\n---\s*\n\n## Evidence|\n\n## Evidence|\n## Evidence/,
    );
    if (cut >= 0) answerPart = answerPart.slice(0, cut);
    // Also stop before a stray next Q if evidence markers were missing.
    const nextQ = answerPart.search(/\n\n\*\*Q:\*\*/);
    if (nextQ >= 0) answerPart = answerPart.slice(0, nextQ);
    // Agent turns are minimal Q/A blocks separated by bare `---` (no
    // Evidence section) — a trailing separator is a delimiter, not answer.
    let answer = answerPart.trim();
    answer = answer.replace(/\n+---\s*$/, '').trim();
    if (question && answer) turns.push({ question, answer });
  }
  return turns;
}
