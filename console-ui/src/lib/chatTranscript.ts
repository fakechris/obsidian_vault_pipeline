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

/** Citation keys the answer text cites, in first-appearance order.
 * Mirrors the Ask page tokenizer (claim/card/unit + bare ck-). */
const CITE_RE = /\[\s*((?:claim|card|unit):[^\]\n]+?|ck-[^\]\s:]+)\s*\]/g;

export function normalizeCiteToken(token: string): string {
  return token.startsWith('ck-') ? `claim:${token}` : token;
}

/** Best-effort portal link for a citation key when replaying a saved chat
 * (no live evidence sidecar). Claims deep-link by key; cards/units have no
 * stable sha without the index. */
export function citeLinkTarget(id: string): string | null {
  if (id.startsWith('claim:')) {
    const key = id.slice('claim:'.length);
    return key ? `/knowledge#${encodeURIComponent(key)}` : null;
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
    const answer = answerPart.trim();
    if (question && answer) turns.push({ question, answer });
  }
  return turns;
}
