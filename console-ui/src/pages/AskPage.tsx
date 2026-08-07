/** Ask page `/ask` + saved chat `/ask/chat/:chatId` — answers US5 (design §3.5).
 *
 * Three columns: saved chat history (left), conversation thread + composer
 * (center), citations for the latest answer (right rail).
 *
 * Live thread: multi-turn continuity via `history` + shared chat stem.
 * Saved chat: same bubble layout as live (parsed from `.ovp/chats/*.md`),
 * addressable as `/ask/chat/<stem>` so the browser can bookmark/share.
 *
 * The textarea sets `data-omnibox-suppress` so the Shell's global ⌘K
 * handler leaves it alone while composing. */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import AskProcessGraph from '../components/AskProcessGraph';
import { EmptyState, PageHelp, conceptTipKey } from '../components/ui';
import { useI18n, type MsgKey } from '../i18n';
import {
  AskError,
  fetchAskProgress,
  fetchAskSession,
  fetchAskStatus,
  fetchChatMarkdown,
  fetchChats,
  fetchSourceDetail,
  postAsk,
} from '../lib/api';
import { useModel } from '../model';
import {
  citationsInOrder,
  citeLinkTarget,
  displayUserQuestion,
  applyMemoryTitles,
  buildMemoryTitleMap,
  groundCitesOnSource,
  normalizeCiteToken,
  parseChatTranscript,
  savedReplayPlan,
} from '../lib/chatTranscript';
import { failureHintKey } from '../lib/derive';
import { isReactImeComposing } from '../lib/ime';
import { MarkdownView, type CiteMarks } from '../lib/markdown';
import type {
  AskCitation,
  AskProgress,
  AskProgressEvent,
  AskResponse,
  AskTraceEntry,
  ChatEntry,
} from '../lib/types';

const ASK_LEFT_KEY = 'ovp.ask.leftCollapsed';
const ASK_RIGHT_KEY = 'ovp.ask.rightCollapsed';

function readCollapsed(key: string, fallback = false): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    return raw === '1' || raw === 'true';
  } catch {
    return fallback;
  }
}

function writeCollapsed(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? '1' : '0');
  } catch {
    /* private mode */
  }
}

interface Turn {
  question: string;
  response: AskResponse | null;
  /** i18n key of the failure — a turn has either a response or an error. */
  errorKey: MsgKey | null;
  /** Live-trail snapshot kept when an AGENT turn failed mid-flight, so the
   * user still sees what ran before the error. */
  progress?: AskProgressEvent[];
}

/** `[claim:…] [card:…] [unit:…]` tokens plus the bare `[ck-…]` form models
 * shorten claim keys to — mirrors the server tokenizer (ovp-memory::verify). */
const CITE_RE =
  /\[\s*((?:claim|card|unit|source):[^\]\n]+?|ck-[^\]\s:]+)\s*\]/g;

function errorKeyFor(err: unknown): MsgKey {
  if (err instanceof AskError) {
    if (err.status === 503) {
      return err.code === 'index_unavailable'
        ? 'ask.errIndexUnavailable'
        : 'ask.errNotConfigured';
    }
    if (err.status === 429) return 'ask.errBusy';
    if (err.status === 504) return 'ask.errTimeout';
  }
  return 'ask.errGeneric';
}

/** The ask POST is one long blocking request: when the webview kills the
 * fetch mid-turn (long agent investigations outlive the browser's request
 * tolerance), the server still finishes the turn and commits the audit
 * transcript — the answer EXISTS, only the response was lost. Recover it
 * from the session bridge instead of showing a failure: poll the committed
 * turns until ours lands (deliverable stop) or proves genuinely failed
 * (timeout/model error), else give up after the guard window. */
async function recoverCommittedTurn(
  chat: string,
  question: string,
  lookup?: CitationTitleLookup,
): Promise<AskResponse | null> {
  const deadline = Date.now() + 4 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    let turns;
    try {
      turns = (await fetchAskSession(chat)).turns;
    } catch {
      continue; // server briefly unreachable — keep waiting
    }
    const mine = [...turns].reverse().find((t) => t.question === question);
    if (!mine) continue; // turn not committed yet
    if (!['final', 'need_user', 'refusal'].includes(mine.stopped_reason)) {
      return null; // honest engine failure — fall through to the error UI
    }
    if (!mine.answer) return null;
    return {
      answer: mine.answer,
      citations: citationsFromAnswerText(mine.answer, lookup),
      verified: null,
      context_hits: 0,
      chat,
      agent: true,
      stopped_reason: mine.stopped_reason,
      turn_id: mine.turn_id,
      tool_trace: mine.tool_trace,
    };
  }
  return null;
}

/** Human title the model wrote after a bare id — `[source:<sha> Some Title]`. */
function decoratedTitle(rest: string): string | null {
  const parts = rest.trim().split(/\s+/);
  if (parts.length < 2) return null;
  const title = parts
    .slice(1)
    .join(' ')
    .replace(/^[<>|]+|[<>|]+$/g, '')
    .trim();
  return title || null;
}

/** Short operator-facing fallback when only a hash is known. */
function shortSourceLabel(token: string): string {
  const short = token.slice(0, 12);
  return short.length < token.length ? `source ${short}…` : `source ${token}`;
}

/** Resolves a citation id from the loaded index — the replay/recovery
 * paths build citations from answer text alone (no server receipts), so
 * without this the right rail degrades to truncated hashes. */
export interface CitationResolution {
  title?: string | null;
  link?: string | null;
}
export type CitationTitleLookup = (
  kind: string,
  token: string,
) => CitationResolution | null;

/** Models sometimes elide long ids ("…b9539c3ec5d2cce4f9c1" written as
 * "…b9539c9c1"). When the head is intact (>=24 hex chars) and exactly one
 * index sha shares it, resolution is safe: 96 bits of intact prefix make a
 * wrong match practically impossible, and no match just falls back. */
const SHA_HEAD = 24;

/** Legacy card/unit markers carry a vault path + trailing suffix
 * (":0", ":u-034-…"). The path part identifies the source note. */
function legacyPathBody(token: string): string {
  const i = token.lastIndexOf(':');
  return i > 0 ? token.slice(0, i) : token;
}

/** Reader notes sanitize titles into filenames ("4b1d798e-2026-03-29_
 * Working_with_evals___") — undo that for a readable fallback title. */
function humanizeLegacyPath(path: string): string | null {
  const base = path.split('/').pop() ?? path;
  const text = base
    .replace(/^[0-9a-f]{8}-/i, '')
    .replace(/^\d{4}-\d{2}-\d{2}_/, '')
    .replace(/_+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return text.length >= 3 && !/^[.\s]+$/.test(text) ? text : null;
}

export function makeCitationTitleLookup(
  model: {
    sources: { sha256: string; title?: string; rel_path?: string }[];
    claims: { claim_id: string; claim_key?: string; claim: string }[];
  } | null,
): CitationTitleLookup {
  const rows = model?.sources ?? [];
  const bySha = new Map(rows.map((s) => [s.sha256, s]));
  const byPath = new Map(
    rows
      .filter((s) => s.rel_path)
      .map((s) => [(s.rel_path as string).replace(/\.md$/, ''), s]),
  );
  // Both identities resolve: claim_key (ck-…, stable across runs) and the
  // run-scoped claim_id — old transcripts cite ids from the run that was
  // current when they were written, and a re-crystallize must not strand
  // them (operator report 2026-08-07). Keys inserted last so they win.
  const claims = new Map<string, string>();
  for (const c of model?.claims ?? []) {
    claims.set(c.claim_id, c.claim);
  }
  for (const c of model?.claims ?? []) {
    if (c.claim_key) claims.set(c.claim_key, c.claim);
  }
  const sourceRow = (token: string) => {
    const exact = bySha.get(token);
    if (exact) return exact;
    if (!/^[0-9a-f]{24,}$/i.test(token)) return undefined;
    const head = token.slice(0, SHA_HEAD);
    const cands = rows.filter(
      (s) => s.sha256.startsWith(token) || s.sha256.startsWith(head),
    );
    return cands.length === 1 ? cands[0] : undefined;
  };
  return (kind, rawToken) => {
    // Legacy answers doubled the kind: "card:card:<path>:0".
    const token =
      (kind === 'card' || kind === 'unit') && rawToken.startsWith(`${kind}:`)
        ? rawToken.slice(kind.length + 1)
        : rawToken;
    if (kind === 'source') {
      const hit = sourceRow(token);
      return hit
        ? { title: hit.title || null, link: `/library/${hit.sha256}` }
        : null;
    }
    if (kind === 'claim') {
      const text = claims.get(token);
      return text ? { title: text } : null;
    }
    if (kind === 'card' || kind === 'unit') {
      const body = legacyPathBody(token);
      const hit = byPath.get(body);
      if (hit) {
        return { title: hit.title || null, link: `/library/${hit.sha256}` };
      }
      // Only LEGACY ids embed a note path. A modern id (u-NNN-hash) fed
      // through the humanizer comes back as itself — and a lookup result
      // SHADOWS real titles from server receipts (2026-08-07 raw-id
      // regression: every downstream fix was masked right here).
      if (!body.includes('/')) return null;
      const human = humanizeLegacyPath(body);
      return human && human !== body ? { title: human } : null;
    }
    return null;
  };
}

/** Build citation chips from answer text alone (saved-chat replay). */
export function citationsFromAnswerText(
  answer: string,
  lookup?: CitationTitleLookup,
): AskCitation[] {
  return citationsInOrder(answer).map((id) => {
    const kind = id.includes(':') ? id.slice(0, id.indexOf(':')) : '';
    // Mirror the server's tolerant token extraction: decorated markers
    // ([source:<sha> Some Title]) keep the RAW id for marker matching but
    // link/display through the bare first token — otherwise replay builds
    // /library/<sha>%20Some%20Title.
    const rest = kind ? id.slice(id.indexOf(':') + 1) : id;
    let token = (rest.trim().split(/\s+/)[0] ?? '').replace(/^<|>$/g, '');
    // Legacy answers doubled the kind ("card:card:<path>:0") — collapse so
    // stored ids and lookups see the clean form.
    if (kind && token.startsWith(`${kind}:`)) {
      token = token.slice(kind.length + 1);
    }
    const canonical = kind ? `${kind}:${token}` : token;
    const isLegacy = kind === 'card' || kind === 'unit';
    const res = lookup?.(kind, token);
    // Canonical index title first (the vault's own display title), then any
    // title the model decorated into the marker, then humanized legacy
    // paths, then the hash fallback.
    const title =
      res?.title ??
      decoratedTitle(rest) ??
      (kind === 'source'
        ? shortSourceLabel(token)
        : isLegacy
          ? humanizeLegacyPath(legacyPathBody(token)) ?? canonical
          : canonical);
    return {
      id: canonical,
      kind,
      title,
      snippet: null,
      // Sources link fail-closed once the index was consulted: an unknown
      // (stale/mangled) id must not produce a /library link that 404s.
      link_target:
        res?.link ??
        (kind === 'source' && lookup ? null : citeLinkTarget(canonical)),
      // Saved transcript does not re-run the verifier — verification state
      // is UNKNOWN, and claiming either way would misrepresent receipts
      // (a fabricated marker must not come back "verified" after refresh).
      verified: null,
    };
  });
}

/** Primary line for a citation panel entry — never a bare 64-char hash.
 * The index lookup wins over a stored fallback label so replayed chats
 * self-heal once the model loads (server receipts already carry titles;
 * replay/recovery paths may have been built before the model arrived). */
function citationTitle(c: AskCitation, lookup?: CitationTitleLookup): string {
  const kind = c.kind || (c.id.includes(':') ? c.id.slice(0, c.id.indexOf(':')) : '');
  const token = (c.id.includes(':') ? c.id.slice(c.id.indexOf(':') + 1) : c.id)
    .split(/\s+/)[0] ?? c.id;
  const fromIndex = lookup?.(kind, token);
  if (fromIndex?.title) return fromIndex.title;
  if (c.title && c.title.trim() && c.title !== c.id) return c.title;
  if (kind === 'source') {
    return shortSourceLabel(token);
  }
  if (kind === 'card' || kind === 'unit') {
    return humanizeLegacyPath(legacyPathBody(token)) ?? c.title ?? c.id;
  }
  return c.title ?? c.id;
}

// ---- agent live trail + receipts (A3c) ----

/** Session id the SPA mints for an agent conversation so it can poll the
 * progress feed from turn 1 (charset must satisfy the server's
 * session-id validation: alphanumeric + dash, ≤64). */
function genChatId(): string {
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Narration verb for a tool name — display only, tools stay canonical. */
function toolVerbKey(tool: string): MsgKey {
  if (tool.startsWith('search')) return 'ask.trailSearching';
  if (tool.startsWith('get') || tool.startsWith('read')) return 'ask.trailReading';
  if (tool.startsWith('list')) return 'ask.trailListing';
  return 'ask.trailRunning';
}

interface TrailStep {
  tool: string;
  args: string | null;
  status: 'running' | 'ok' | 'err';
  summary?: string;
  /** Result stats ("3 hit(s) · scanned 918/1281 · truncated"). */
  note?: string | null;
}

/** Fold started/finished event pairs into per-call steps (order preserved). */
function stepsFromEvents(events: AskProgressEvent[]): TrailStep[] {
  const steps: TrailStep[] = [];
  const open = new Map<string, number>();
  for (const ev of events) {
    if (ev.event === 'tool_started' && ev.tool) {
      if (ev.tool_call_id) open.set(ev.tool_call_id, steps.length);
      steps.push({ tool: ev.tool, args: ev.args ?? null, status: 'running' });
    } else if (ev.event === 'tool_finished' && ev.tool_call_id) {
      const i = open.get(ev.tool_call_id);
      if (i !== undefined) {
        steps[i] = {
          ...steps[i],
          status: ev.ok === false ? 'err' : 'ok',
          summary: ev.summary,
          note: ev.note ?? null,
        };
      }
    }
  }
  return steps;
}

function stepsFromTrace(trace: AskTraceEntry[]): TrailStep[] {
  return trace.map((t) => ({
    tool: t.tool,
    args: t.args ?? null,
    status: t.ok ? ('ok' as const) : ('err' as const),
    summary: t.summary,
    note: t.note ?? null,
  }));
}

/** What the agent is doing when no tool call is in flight. */
type TrailPhase = 'connecting' | 'thinking' | 'composing' | null;

function livePhase(progress: AskProgress, steps: TrailStep[]): TrailPhase {
  if (!progress.started) return 'connecting';
  if (steps.some((s) => s.status === 'running')) return null;
  if (steps.length === 0) return 'thinking';
  if (!progress.done) return 'composing';
  return null;
}

function AgentTrail({
  steps,
  phase,
}: {
  steps: TrailStep[];
  phase: TrailPhase;
}) {
  const { t } = useI18n();
  const phaseKey: MsgKey | null =
    phase === 'connecting'
      ? 'ask.trailConnecting'
      : phase === 'thinking'
        ? 'ask.trailThinking'
        : phase === 'composing'
          ? 'ask.trailComposing'
          : null;
  return (
    <div className="ask-trail">
      {steps.map((s, i) => (
        <div
          key={`s${i}`}
          className={`ask-step ${s.status}`}
          title={s.summary || undefined}
        >
          <span className="ask-step-dot" aria-hidden />
          <span>{t(toolVerbKey(s.tool))}</span>
          <span className="mono ask-step-tool">{s.tool}</span>
          {s.args && <span className="mono muted ask-step-args">{s.args}</span>}
          {s.note && <span className="tiny muted ask-step-note">→ {s.note}</span>}
          {s.status === 'err' && (
            <span className="pill failed">{t('ask.trailFailedStep')}</span>
          )}
        </div>
      ))}
      {phaseKey && (
        <div className="ask-step running">
          <span className="ask-step-dot" aria-hidden />
          <span className="muted">{t(phaseKey)}</span>
        </div>
      )}
    </div>
  );
}

const COV_LAYERS: [string, MsgKey][] = [
  ['claims', 'ask.covClaims'],
  ['sources', 'ask.covSources'],
  ['evidence', 'ask.covEvidence'],
  ['fulltext', 'ask.covFulltext'],
  ['body', 'ask.covBody'],
];
const COV_STATE: Record<string, MsgKey> = {
  complete: 'ask.covComplete',
  partial: 'ask.covPartial',
  not_queried: 'ask.covNotQueried',
  unavailable: 'ask.covUnavailable',
  failed: 'ask.covFailed',
};

function CoverageBadges({ coverage }: { coverage: Record<string, string> }) {
  const { t } = useI18n();
  return (
    <div className="ask-coverage">
      <span className="tiny muted">{t('ask.coverageTitle')}</span>
      {COV_LAYERS.map(([key, labelKey]) => {
        const state = coverage[key];
        if (!state) return null;
        const stateKey = COV_STATE[state];
        return (
          <span key={key} className={`cov-pill ${state}`}>
            {t(labelKey)} · {stateKey ? t(stateKey) : state}
          </span>
        );
      })}
    </div>
  );
}

/** Warning line for turns the agent could not finish cleanly. `final`,
 * `need_user`, `refusal` speak for themselves in the answer text. */
function stopNoticeKey(reason: string | undefined): MsgKey | null {
  switch (reason) {
    case 'timeout':
      return 'ask.stopTimeout';
    case 'tool_error':
      return 'ask.stopToolError';
    case 'model_error':
      return 'ask.stopModelError';
    case 'max_rounds':
      return 'ask.stopMaxRounds';
    default:
      return null;
  }
}


/** Receipts under an agent answer: stop notice, coverage, collapsed trail. */
function AgentMeta({ response }: { response: AskResponse }) {
  const { t } = useI18n();
  const stopKey = stopNoticeKey(response.stopped_reason);
  const hintKey = failureHintKey(response.failure_class);
  const trace = response.tool_trace ?? [];
  return (
    <div className="ask-agent-meta">
      {stopKey && (
        <div className="ask-stop-note">
          {t(stopKey)}
          {hintKey ? ` — ${t(hintKey)}` : ''}
        </div>
      )}
      {response.coverage && <CoverageBadges coverage={response.coverage} />}
      {trace.length > 0 && (
        <details className="ask-trail-details">
          <summary className="tiny muted">
            {t('ask.trailTitle')} · {trace.length}
          </summary>
          <AgentTrail steps={stepsFromTrace(trace)} phase={null} />
        </details>
      )}
    </div>
  );
}

/** Stable `kind:token` key for marker ↔ panel matching. Strips model
 * decoration (`[source:<sha> Title]`) so the answer token and the receipt
 * id always land on the same map entry. */
function citationLookupKey(id: string): string {
  const norm = normalizeCiteToken(id);
  const kind = norm.includes(':') ? norm.slice(0, norm.indexOf(':')) : '';
  const rest = kind ? norm.slice(norm.indexOf(':') + 1) : norm;
  const token = (rest.trim().split(/\s+/)[0] ?? '').replace(/^<|>$/g, '');
  return kind ? `${kind}:${token}` : token;
}

/** Answer body rendered as markdown with numbered citation markers. */
function AnswerText({
  answer,
  citations,
  onHover,
  onOpen,
}: {
  answer: string;
  citations: AskCitation[];
  onHover: (id: string | null) => void;
  onOpen: (cit: AskCitation) => void;
}) {
  const index = new Map(citations.map((c, i) => [citationLookupKey(c.id), i]));
  const citeMarks: CiteMarks = {
    pattern: CITE_RE,
    render: (citeId) => {
      const i = index.get(citationLookupKey(citeId));
      if (i === undefined) return null;
      const cit = citations[i];
      return (
        <button
          type="button"
          className={`cite-marker${cit.verified === false ? ' warn' : ''}`}
          onMouseEnter={() => onHover(cit.id)}
          onMouseLeave={() => onHover(null)}
          onFocus={() => onHover(cit.id)}
          onBlur={() => onHover(null)}
          onClick={() => onOpen(cit)}
          title={citationTitle(cit)}
        >
          [{i + 1}]
        </button>
      );
    },
  };
  return (
    <div className="answer-text reading">
      <MarkdownView markdown={answer} gutter={false} citeMarks={citeMarks} />
    </div>
  );
}

function CitationPanel({
  citations,
  hoverId,
  onOpen,
  lookup,
}: {
  citations: AskCitation[];
  hoverId: string | null;
  onOpen: (cit: AskCitation) => void;
  lookup?: CitationTitleLookup;
}) {
  const { t } = useI18n();
  if (citations.length === 0) {
    return (
      <EmptyState>
        <p>{t('ask.citationsEmpty')}</p>
      </EmptyState>
    );
  }
  return (
    <div>
      {citations.map((c, i) => {
        const kindTip = conceptTipKey(c.kind);
        const hit =
          hoverId != null &&
          citationLookupKey(hoverId) === citationLookupKey(c.id);
        return (
          <div
            key={c.id}
            className={`cite-entry${hit ? ' hover-hit' : ''}`}
          >
            <div className="cite-entry-top">
              <span className="cite-num mono">[{i + 1}]</span>
              <span className="pill" title={kindTip ? t(kindTip) : undefined}>
                {c.kind}
              </span>
              {c.verified === false && (
                <span className="pill unverified">{t('ask.unverified')}</span>
              )}
            </div>
            <div className="cite-title">{citationTitle(c, lookup)}</div>
            {c.id && citationTitle(c, lookup) !== c.id && (
              <div className="cite-id mono tiny muted" title={c.id}>
                {c.id}
              </div>
            )}
            {c.snippet && <blockquote>“{c.snippet}”</blockquote>}
            {c.link_target ? (
              <button
                type="button"
                className="cite-open tiny"
                onClick={() => onOpen(c)}
              >
                {t('ask.openCitation')} →
              </button>
            ) : (
              <span className="tiny muted">{t('ask.noLink')}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Shared bubble thread used by live conversation and saved-chat replay. */
function ChatThread({
  turns,
  pending,
  liveTrail,
  onHover,
  onOpen,
  threadRef,
  empty,
}: {
  turns: Turn[];
  pending: boolean;
  /** Live agent activity rendered in place of the static pending text. */
  liveTrail?: React.ReactNode;
  onHover: (id: string | null) => void;
  onOpen: (cit: AskCitation) => void;
  threadRef: React.RefObject<HTMLDivElement | null>;
  empty: React.ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className="chat-thread" ref={threadRef}>
      {turns.length === 0 && empty}
      {turns.map((turn, i) => (
        <div key={`t${i}`} className="chat-turn">
          <div className="chat-q">{turn.question}</div>
          {turn.response && (
            <div className="chat-a">
              <AnswerText
                answer={turn.response.answer}
                citations={turn.response.citations}
                onHover={onHover}
                onOpen={onOpen}
              />
              {turn.response.verified && (
                <div className="chat-verify mono tiny muted">
                  {t('ask.verifiedLine', {
                    verified: turn.response.verified.verified,
                    cited: turn.response.verified.cited,
                  })}
                  {' · '}
                  {t('ask.contextHits', {
                    n: turn.response.context_hits,
                  })}
                </div>
              )}
              {turn.response.agent && <AgentMeta response={turn.response} />}
            </div>
          )}
          {turn.errorKey && (
            <div className="chat-a chat-error">
              {turn.progress && turn.progress.length > 0 && (
                <AgentTrail
                  steps={stepsFromEvents(turn.progress)}
                  phase={null}
                />
              )}
              {t(turn.errorKey)}
            </div>
          )}
          {!turn.response &&
            !turn.errorKey &&
            i === turns.length - 1 &&
            pending && (
              <div className="chat-a chat-pending">
                {liveTrail ?? (
                  <span className="muted">{t('ask.pending')}</span>
                )}
              </div>
            )}
        </div>
      ))}
    </div>
  );
}

export default function AskPage() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const { chatId: routeChatId } = useParams<{ chatId?: string }>();
  const [searchParams] = useSearchParams();
  // Chat-on-this-source: /ask?focus=<sha> pins every turn to that body.
  const focusSource = searchParams.get('focus');
  // URL is the source of truth for which saved chat is open (bookmarkable).
  const openChat = routeChatId ?? null;

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [hoverId, setHoverId] = useState<string | null>(null);
  /** Stem of the live multi-turn session (first successful answer's `chat`). */
  const [sessionChat, setSessionChat] = useState<string | null>(null);

  // Agent mode: discovered via /api/ask/status (index-free, like the agent
  // path itself) with the /api/model overlay as fallback — the SPA then
  // mints the session id itself and polls the live feed. `submit` AWAITS
  // the in-flight discovery, so a first ask racing it still gets a trail.
  const { model } = useModel();
  // Index-backed citation titles (sha → source title, claim key → claim).
  // The ref lets load-once effects read the LATEST lookup without re-running
  // (a model arriving mid-session must not refetch the saved chat).
  const citeTitleLookup = useMemo(() => makeCitationTitleLookup(model), [model]);
  const citeLookupRef = useRef(citeTitleLookup);
  useEffect(() => {
    citeLookupRef.current = citeTitleLookup;
  }, [citeTitleLookup]);
  const [askStatus, setAskStatus] = useState<boolean | null>(null);
  // Ref, not a captured value: the /api/model overlay keeps polling, and a
  // fallback taken later must read what the model says NOW.
  const modelAgentRef = useRef(false);
  useEffect(() => {
    modelAgentRef.current = model?.ask_agent === true;
  }, [model]);
  useEffect(() => {
    fetchAskStatus()
      .then((s) => setAskStatus(s.agent))
      .catch(() => {
        /* warm-cache miss only — submit re-reads per submission */
      });
  }, []);
  const [live, setLive] = useState<AskProgress | null>(null);
  const liveRef = useRef<AskProgress | null>(null);
  /** Session the CURRENT in-flight ask polls against (null = legacy path).
   * State (not a ref) so the polling effect re-runs when a submission
   * resolves agent mode asynchronously. */
  const [pollChat, setPollChat] = useState<string | null>(null);

  const [chats, setChats] = useState<ChatEntry[]>([]);
  /** Fold side rails so the reading column can breathe. */
  const [leftCollapsed, setLeftCollapsed] = useState(() =>
    readCollapsed(ASK_LEFT_KEY, false),
  );
  const [rightCollapsed, setRightCollapsed] = useState(() =>
    readCollapsed(ASK_RIGHT_KEY, false),
  );
  const toggleLeft = useCallback(() => {
    setLeftCollapsed((v) => {
      const next = !v;
      writeCollapsed(ASK_LEFT_KEY, next);
      return next;
    });
  }, []);
  const toggleRight = useCallback(() => {
    setRightCollapsed((v) => {
      const next = !v;
      writeCollapsed(ASK_RIGHT_KEY, next);
      return next;
    });
  }, []);
  /** History rail filter: all | source-grounded only | vault-wide only. */
  const [historyFilter, setHistoryFilter] = useState<'all' | 'source' | 'vault'>(
    'all',
  );
  const [savedTurns, setSavedTurns] = useState<Turn[] | null>(null);
  const [savedError, setSavedError] = useState<string | null>(null);
  // Async guard: slow fetch for chat A must not paint under chat B.
  const openChatRef = useRef<string | null>(null);

  const threadRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const refreshChats = () => {
    fetchChats()
      .then(setChats)
      .catch(() => {
        /* History degrades to empty — the thread still works. */
      });
  };
  useEffect(refreshChats, []);

  // Switching history rows (or leaving one) must not leak the previous
  // live thread into the new view — multi-turn resume seeds from saved.
  // Deliberately NOT tied to `t`: a language flip must not wipe the thread.
  useEffect(() => {
    setTurns([]);
    setPending(false);
    setPollChat(null);
    liveRef.current = null;
    setLive(null);
    setSessionChat(openChat);
    setDraft('');
  }, [openChat]);

  // Load saved chat when the route points at one.
  useEffect(() => {
    openChatRef.current = openChat;
    setSavedTurns(null);
    setSavedError(null);
    setHoverId(null);
    if (!openChat) return;
    let cancelled = false;
    // Agent chats replay from the AUDIT transcript (full per-turn trails
    // survive a reload — operator finding: History lost the 复盘 detail);
    // legacy chats keep the markdown parse.
    // Markdown FIRST: its header carries the session's focus markers
    // (ovp:focus_source/theme), and a focused session's unit/card cites only
    // resolve when grounded onto that source's page — exactly what the live
    // dock did. Without this, replay showed "No detail page in this vault"
    // for every unit (operator report 2026-08-07). `null` = load failed —
    // savedReplayPlan keeps that distinct from an empty chat.
    Promise.all([
      fetchChatMarkdown(openChat).catch(() => null),
      // Agent chats replay from the AUDIT transcript; legacy chats fall back
      // to the markdown parse. An older server (no session endpoint) or a
      // replay-read failure must not kill the page.
      fetchAskSession(openChat).catch(() => ({ turns: [] })),
    ])
      .then(async ([md, session]) => {
        if (cancelled || openChatRef.current !== openChat) return;
        const plan = savedReplayPlan(md, session.turns.length);
        // Focused session: the source's memory payload supplies readable
        // titles for unit/card chips (replay reconstructs citations from
        // text alone, so without this they degrade to raw ids — operator
        // report 2026-08-07). Best-effort; chips fall back to ids.
        let memoryTitles: Map<string, string> | null = null;
        if (plan.focus.sha && (plan.kind === 'session' || plan.kind === 'markdown')) {
          memoryTitles = await fetchSourceDetail(plan.focus.sha)
            .then((d) => buildMemoryTitleMap(d.memory))
            .catch(() => null);
          if (cancelled || openChatRef.current !== openChat) return;
        }
        const ground = (cites: AskCitation[]) => {
          const grounded = plan.focus.sha
            ? groundCitesOnSource(cites, plan.focus.sha)
            : cites;
          return memoryTitles ? applyMemoryTitles(grounded, memoryTitles) : grounded;
        };
        if (plan.kind === 'loadError') {
          setSavedError(t('ask.chatLoadError'));
          setSavedTurns([]);
          return;
        }
        if (plan.kind === 'empty') {
          setSavedError(t('ask.chatParseEmpty'));
          setSavedTurns([]);
          return;
        }
        if (plan.kind === 'session') {
          setSavedTurns(
            session.turns.map((turn) => ({
              question: displayUserQuestion(turn.question),
              errorKey: null,
              response: {
                answer: turn.answer,
                // Server receipts are authoritative (one resolver for live +
                // replay); text reconstruction only covers older servers.
                citations: turn.citations?.length
                  ? turn.citations
                  : ground(citationsFromAnswerText(turn.answer, citeLookupRef.current)),
                verified: null,
                context_hits: 0,
                chat: openChat,
                agent: true,
                stopped_reason: turn.stopped_reason,
                turn_id: turn.turn_id,
                tool_trace: turn.tool_trace,
              },
            })),
          );
          return;
        }
        setSavedTurns(
          plan.parsedTurns.map((turn) => {
            const citations = ground(
              citationsFromAnswerText(turn.answer, citeLookupRef.current),
            );
            return {
              question: turn.question,
              errorKey: null,
              response: {
                answer: turn.answer,
                citations,
                verified: null,
                context_hits: citations.length,
                chat: openChat,
              },
            };
          }),
        );
      })
      .catch(() => {
        if (!cancelled && openChatRef.current === openChat) {
          setSavedError(t('ask.chatLoadError'));
          setSavedTurns([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [openChat, t]);

  // Keep the newest turn in view while a conversation grows.
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [turns, pending, live, savedTurns, openChat]);

  const startNewConversation = () => {
    setTurns([]);
    setSessionChat(null);
    setDraft('');
    navigate('/ask');
    composerRef.current?.focus();
  };

  // Poll the progress feed while an agent ask is in flight — the live
  // trail is the entire point of the wait.
  useEffect(() => {
    if (!pending) return;
    const chat = pollChat;
    if (!chat) return;
    let cancelled = false;
    // On turn N+1 the map may still hold turn N's COMPLETED feed until this
    // turn's admission registers (or never, if the POST is rejected first).
    // Accept nothing until a live (not-done) feed proves it is ours — a
    // stale trail must never be attributed to this turn.
    let seenLive = false;
    const tick = () => {
      fetchAskProgress(chat)
        .then((p) => {
          if (cancelled) return;
          if (!seenLive) {
            if (p.done) return;
            seenLive = true;
          }
          liveRef.current = p;
          setLive(p);
        })
        .catch(() => {
          /* transient poll failures never disturb the ask itself */
        });
    };
    tick();
    const id = window.setInterval(tick, 700);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [pending, pollChat]);

  const submit = () => {
    const question = draft.trim();
    if (!question || pending) return;
    // Saved-chat routes used to be read-only; multi-turn resume seeds the
    // live thread from the loaded transcript, keeps the same chat stem,
    // and appends. Live sessions keep using in-memory turns as before.
    const prior =
      turns.length > 0 ? turns : openChat ? (savedTurns ?? []) : turns;
    setDraft('');
    setPending(true);
    setPollChat(null);
    liveRef.current = null;
    setLive(null);
    const history = prior
      .filter((t) => t.response?.answer)
      .map((t) => ({
        question: t.question,
        answer: t.response!.answer,
      }));
    setTurns([...prior, { question, response: null, errorKey: null }]);
    const applyResponse = (response: AskResponse) => {
      setTurns((prev) =>
        prev.map((turn, i) =>
          i === prev.length - 1 ? { ...turn, response } : turn,
        ),
      );
      if (response.chat) {
        setSessionChat((prev) => prev ?? response.chat);
      }
      refreshChats();
    };
    // Hoisted so the catch path can attempt transcript recovery with the
    // SAME session id the POST used.
    let agent = false;
    // Prefer the URL stem (continuing a saved chat), then the live stem.
    let chat = openChat ?? sessionChat;
    void (async () => {
      // Resolve agent mode PER SUBMISSION — a fresh read tracks server
      // restarts with the flag flipped; the mount-time discovery and the
      // /api/model overlay only serve as fallbacks when the read fails.
      agent = await fetchAskStatus()
        .then((s) => {
          setAskStatus(s.agent);
          return s.agent;
        })
        .catch(() => askStatus ?? modelAgentRef.current);
      // Agent path: mint the session id client-side so the progress feed
      // is pollable from the FIRST turn (the server honors supplied ids).
      if (agent && !chat) {
        chat = genChatId();
        setSessionChat(chat);
      } else if (chat) {
        setSessionChat(chat);
      }
      setPollChat(agent ? chat : null);
      // Focus from the live query string, or from the saved chat's focus
      // markers so continuations stay grounded on the same source/theme.
      const focusSha =
        focusSource ?? openChatMeta?.focus_source ?? undefined;
      const focusTheme = openChatMeta?.focus_theme ?? undefined;
      return postAsk(question, {
        chat,
        history,
        focus_source: focusSha,
        focus_theme: focusSha ? undefined : focusTheme ?? undefined,
      });
    })()
      .then(applyResponse)
      .catch(async (err: unknown) => {
        // A lost response is not a lost turn: when the agent path knows the
        // session, the committed transcript may still hold the answer.
        if (agent && chat) {
          const recovered = await recoverCommittedTurn(chat, question, citeLookupRef.current);
          if (recovered) {
            applyResponse(recovered);
            return;
          }
        }
        const errorKey = errorKeyFor(err);
        // Keep what the agent DID before failing — an honest partial trail
        // beats a bare error line. But ONLY when the failed request was
        // actually admitted: a 429 (busy/admission-capped) or 400
        // (validation) POST never owned the session, so any polled feed
        // belongs to a DIFFERENT turn and must not be attributed here.
        const admitted = !(
          err instanceof AskError &&
          (err.status === 429 || err.status === 409 || err.status === 400)
        );
        const trail = admitted ? liveRef.current?.events : undefined;
        setTurns((prev) =>
          prev.map((turn, i) =>
            i === prev.length - 1
              ? {
                  ...turn,
                  errorKey,
                  progress: trail && trail.length > 0 ? trail : undefined,
                }
              : turn,
          ),
        );
      })
      .finally(() => setPending(false));
  };

  const onComposerKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isReactImeComposing(e)) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const openCitation = (cit: AskCitation) => {
    if (cit.link_target) navigate(cit.link_target);
  };

  const applyExample = (text: string) => {
    setDraft(text);
    composerRef.current?.focus();
  };

  const chatDate = (entry: ChatEntry) =>
    entry.mtime > 0
      ? new Date(entry.mtime * 1000).toLocaleString(
          lang === 'zh' ? 'zh-CN' : 'en-US',
          { dateStyle: 'medium', timeStyle: 'short' },
        )
      : entry.name;

  const openChatMeta = useMemo(
    () => (openChat ? chats.find((c) => c.name === openChat) : undefined),
    [chats, openChat],
  );

  const filteredChats = useMemo(() => {
    if (historyFilter === 'source') {
      return chats.filter((c) => Boolean(c.focus_source));
    }
    if (historyFilter === 'vault') {
      return chats.filter((c) => !c.focus_source);
    }
    return chats;
  }, [chats, historyFilter]);

  // Live agent activity for the in-flight turn. Before the first poll lands
  // (or on the legacy path) the thread falls back to the static pending text.
  let liveTrail: React.ReactNode = null;
  if (pending && pollChat) {
    if (live) {
      const steps = stepsFromEvents(live.events);
      liveTrail = <AgentTrail steps={steps} phase={livePhase(live, steps)} />;
    } else {
      liveTrail = <AgentTrail steps={[]} phase="connecting" />;
    }
  }

  // Once the user continues a saved chat, `turns` is the live source of
  // truth (includes prior + new). Until then, show the loaded transcript.
  const continuingSaved = Boolean(openChat) && turns.length > 0;
  const displayTurns = continuingSaved
    ? turns
    : openChat
      ? (savedTurns ?? [])
      : turns;
  const latest = [...displayTurns].reverse().find((turn) => turn.response);
  // Failed mid-flight turns keep a progress snapshot but no response —
  // still surface their process graph in the rail.
  const latestAny = [...displayTurns].reverse().find(
    (turn) => turn.response || (turn.progress && turn.progress.length > 0),
  );
  const citations = latest?.response?.citations ?? [];
  // Live turn: only current progress events (never a prior turn's trail).
  // Settled turn: tool_trace + citations (and error progress if any).
  const processEvents = pending
    ? live?.events
    : latestAny?.progress;
  const processTrace = pending
    ? undefined
    : latestAny?.response?.tool_trace;
  const processCitations = pending ? [] : citations;
  const examples: MsgKey[] = ['ask.example1', 'ask.example2', 'ask.example3'];
  const viewingSaved = Boolean(openChat);
  const savedLoading = viewingSaved && savedTurns == null && !continuingSaved;
  const savedLoadFailed =
    viewingSaved &&
    !continuingSaved &&
    savedTurns != null &&
    savedError != null &&
    savedTurns.length === 0;

  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('ask.title')}</h1>
      <PageHelp>{t('ask.help')}</PageHelp>
      {focusSource && !viewingSaved && (
        <div className="card" style={{ marginBottom: '0.75rem' }}>
          <p className="sm" style={{ marginBottom: 0 }}>
            {t('ask.focusBanner')}{' '}
            <Link
              className="mono tiny"
              to={`/library/${encodeURIComponent(focusSource)}?chat=1`}
            >
              {focusSource.slice(0, 12)}…
            </Link>
            {' · '}
            <Link
              className="tiny"
              to={`/library/${encodeURIComponent(focusSource)}?chat=1`}
            >
              {t('ask.focusOpenOnSource')}
            </Link>
          </p>
        </div>
      )}

      <div
        className={[
          'grid ask',
          leftCollapsed ? 'left-collapsed' : '',
          rightCollapsed ? 'right-collapsed' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {/* left: saved chat history — collapsible rail */}
        <aside
          className={`ask-side ask-side-left${leftCollapsed ? ' is-collapsed' : ''}`}
          aria-label={t('ask.historyTitle')}
        >
          {leftCollapsed ? (
            <button
              type="button"
              className="ask-side-rail-btn"
              onClick={toggleLeft}
              title={t('ask.expandHistory')}
              aria-label={t('ask.expandHistory')}
              aria-expanded={false}
            >
              <span className="ask-side-rail-chevron" aria-hidden="true">
                ›
              </span>
              <span className="ask-side-rail-label">{t('ask.historyTitle')}</span>
            </button>
          ) : (
            <div className="facet-group ask-side-body">
              <div className="ask-side-head">
                <h3>{t('ask.historyTitle')}</h3>
                <button
                  type="button"
                  className="ask-side-toggle"
                  onClick={toggleLeft}
                  title={t('ask.collapseHistory')}
                  aria-label={t('ask.collapseHistory')}
                  aria-expanded={true}
                >
                  ‹
                </button>
              </div>
              {(turns.length > 0 || sessionChat || viewingSaved) && (
                <button
                  type="button"
                  className="tiny"
                  style={{ marginBottom: '0.5rem' }}
                  onClick={startNewConversation}
                >
                  {t('ask.newConversation')}
                </button>
              )}
              {chats.length > 0 && (
                <div className="chat-history-filters" role="tablist">
                  {(
                    [
                      ['all', 'ask.historyFilterAll'],
                      ['source', 'ask.historyFilterSource'],
                      ['vault', 'ask.historyFilterVault'],
                    ] as const
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      role="tab"
                      aria-selected={historyFilter === key}
                      className={historyFilter === key ? 'active' : undefined}
                      onClick={() => setHistoryFilter(key)}
                    >
                      {t(label)}
                    </button>
                  ))}
                </div>
              )}
              {chats.length === 0 ? (
                <p className="tiny muted">{t('ask.historyEmpty')}</p>
              ) : filteredChats.length === 0 ? (
                <p className="tiny muted">{t('ask.historyFilterEmpty')}</p>
              ) : (
                <ul className="facet-list chat-list">
                  {filteredChats.map((entry) => (
                    <li key={entry.name}>
                      <Link
                        to={`/ask/chat/${encodeURIComponent(entry.name)}`}
                        className={
                          openChat === entry.name ||
                          (!viewingSaved && sessionChat === entry.name)
                            ? 'active'
                            : undefined
                        }
                      >
                        {entry.focus_source ? (
                          <span
                            className="chat-focus-badge"
                            title={entry.focus_title ?? entry.focus_source}
                          >
                            {t('ask.historyOnSource')}
                          </span>
                        ) : (
                          <span className="chat-vault-badge">
                            {t('ask.historyVault')}
                          </span>
                        )}
                        <span className="chat-preview">
                          {entry.preview ||
                            entry.focus_title ||
                            chatDate(entry)}
                        </span>
                        <span className="chat-date">{chatDate(entry)}</span>
                        {entry.focus_source && (
                          <span
                            className="chat-focus-link"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              navigate(
                                `/library/${encodeURIComponent(entry.focus_source!)}?chat=${encodeURIComponent(entry.name)}`,
                              );
                            }}
                            role="link"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault();
                                navigate(
                                  `/library/${encodeURIComponent(entry.focus_source!)}?chat=${encodeURIComponent(entry.name)}`,
                                );
                              }
                            }}
                          >
                            {entry.focus_title
                              ? entry.focus_title.length > 28
                                ? `${entry.focus_title.slice(0, 28)}…`
                                : entry.focus_title
                              : entry.focus_source.slice(0, 10)}
                          </span>
                        )}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </aside>

        {/* center: conversation thread + composer (multi-turn always on) */}
        <div className="ask-main">
          {viewingSaved && (
            <div className="chat-reader-head">
              <span className="tiny muted">
                {t('ask.savedChat')}
                {continuingSaved ? ` · ${t('ask.continuing')}` : ''}
                {' · '}
                <span className="mono">
                  {openChatMeta ? chatDate(openChatMeta) : openChat}
                </span>
                {openChatMeta?.focus_source && (
                  <>
                    {' · '}
                    <Link
                      to={`/library/${encodeURIComponent(openChatMeta.focus_source)}?chat=${encodeURIComponent(openChatMeta.name)}`}
                    >
                      {openChatMeta.focus_title || t('ask.historyOnSource')}
                    </Link>
                  </>
                )}
              </span>
              <button
                type="button"
                className="tab-like"
                onClick={startNewConversation}
              >
                ← {t('ask.closeChat')}
              </button>
            </div>
          )}

          {savedLoading ? (
            <div className="portal-note">{t('common.loading')}</div>
          ) : savedLoadFailed ? (
            <EmptyState>
              <p>{savedError}</p>
            </EmptyState>
          ) : (
            <ChatThread
              turns={displayTurns}
              pending={pending}
              liveTrail={liveTrail}
              onHover={setHoverId}
              onOpen={openCitation}
              threadRef={threadRef}
              empty={
                viewingSaved ? (
                  <EmptyState>
                    <p>{savedError ?? t('ask.chatParseEmpty')}</p>
                  </EmptyState>
                ) : (
                  <EmptyState>
                    <p>
                      <strong>{t('ask.emptyTitle')}</strong>
                    </p>
                    <p>{t('ask.emptyBody')}</p>
                    <ul className="example-list">
                      {examples.map((key) => (
                        <li key={key}>
                          <button
                            type="button"
                            onClick={() => applyExample(t(key))}
                          >
                            {t(key)} →
                          </button>
                        </li>
                      ))}
                    </ul>
                  </EmptyState>
                )
              }
            />
          )}

          {!savedLoading && !savedLoadFailed && (
            <div className="ask-composer">
              <textarea
                ref={composerRef}
                data-omnibox-suppress
                value={draft}
                placeholder={t('ask.placeholder')}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onComposerKey}
                disabled={pending}
                rows={3}
              />
              <div className="composer-foot">
                <span className="tiny muted mono">{t('ask.hint')}</span>
                <button
                  type="button"
                  className="send-btn"
                  onClick={submit}
                  disabled={pending || draft.trim() === ''}
                >
                  {pending ? t('ask.pending') : t('ask.send')}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* right rail: process + citations — collapsible */}
        <aside
          className={`ask-side ask-side-right ask-rail${rightCollapsed ? ' is-collapsed' : ''}`}
          aria-label={t('ask.processTitle')}
        >
          {rightCollapsed ? (
            <button
              type="button"
              className="ask-side-rail-btn"
              onClick={toggleRight}
              title={t('ask.expandRail')}
              aria-label={t('ask.expandRail')}
              aria-expanded={false}
            >
              <span className="ask-side-rail-chevron" aria-hidden="true">
                ‹
              </span>
              <span className="ask-side-rail-label">
                {t('ask.railCollapsedLabel')}
              </span>
            </button>
          ) : (
            <div className="ask-side-body">
              <div className="ask-side-head ask-side-head-end">
                <button
                  type="button"
                  className="ask-side-toggle"
                  onClick={toggleRight}
                  title={t('ask.collapseRail')}
                  aria-label={t('ask.collapseRail')}
                  aria-expanded={true}
                >
                  ›
                </button>
              </div>
              <div className="card ask-process-card">
                <h3 style={{ marginBottom: '0.4rem' }}>{t('ask.processTitle')}</h3>
                <p className="tiny muted ask-process-help">{t('ask.processHelp')}</p>
                <AskProcessGraph
                  events={processEvents}
                  toolTrace={processTrace}
                  citations={processCitations}
                  hoverId={hoverId}
                  height={220}
                />
              </div>
              <div className="card">
                <h3 style={{ marginBottom: '0.6rem' }}>{t('ask.citationsTitle')}</h3>
                <CitationPanel
                  citations={citations}
                  hoverId={hoverId}
                  onOpen={openCitation}
                  lookup={citeTitleLookup}
                />
              </div>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}
