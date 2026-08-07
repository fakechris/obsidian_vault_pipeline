/** Focus-grounded chat dock — lives on the focused page, not a jump to Ask.
 *
 * Two focus targets share one dock:
 * - source (`/library/:sha`): server injects body + memory + crystal via
 *   `focus_source`.
 * - theme (`/knowledge/theme/:t`): server injects topic page + active claims
 *   + cited sources via `focus_theme`.
 * Sessions are the same `.ovp/chats` spine as Ask, tagged with focus
 * metadata for unified history.
 *
 * Multi-turn is always on: saved recents can be resumed with follow-ups.
 * The panel width is user-resizable (persisted in localStorage).
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useI18n, type MsgKey } from '../i18n';
import {
  AskError,
  fetchAskProgress,
  fetchAskSession,
  fetchAskStatus,
  fetchChatMarkdown,
  fetchChats,
  postAsk,
} from '../lib/api';
import { isReactImeComposing } from '../lib/ime';
import { MarkdownView, type CiteMarks } from '../lib/markdown';
import {
  applyMemoryTitles,
  displayUserQuestion,
  groundCitesOnSource,
  parseChatTranscript,
} from '../lib/chatTranscript';
import { citationsFromAnswerText } from '../pages/AskPage';
import type { AskCitation, AskProgress, AskResponse, ChatEntry } from '../lib/types';

const PANEL_WIDTH_KEY = 'ovp.sourceChatWidth';
const PANEL_WIDTH_MIN = 320;
const PANEL_WIDTH_MAX = 880;
const PANEL_WIDTH_DEFAULT = 440;

function clampPanelWidth(n: number): number {
  const max = Math.min(
    PANEL_WIDTH_MAX,
    typeof window !== 'undefined' ? Math.floor(window.innerWidth * 0.92) : PANEL_WIDTH_MAX,
  );
  return Math.min(Math.max(Math.round(n), PANEL_WIDTH_MIN), max);
}

function readStoredPanelWidth(): number {
  try {
    const raw = localStorage.getItem(PANEL_WIDTH_KEY);
    if (!raw) return PANEL_WIDTH_DEFAULT;
    const n = Number(raw);
    return Number.isFinite(n) ? clampPanelWidth(n) : PANEL_WIDTH_DEFAULT;
  } catch {
    return PANEL_WIDTH_DEFAULT;
  }
}

/** Same marker set as Ask — claim/card/unit/source + bare ck-. */
const CITE_RE =
  /\[\s*((?:claim|card|unit|source):[^\]\n]+?|ck-[^\]\s:]+)\s*\]/g;

function citeLookupKey(id: string): string {
  const norm = id.startsWith('ck-') ? `claim:${id}` : id;
  const kind = norm.includes(':') ? norm.slice(0, norm.indexOf(':')) : '';
  const rest = kind ? norm.slice(norm.indexOf(':') + 1) : norm;
  const token = (rest.trim().split(/\s+/)[0] ?? '').replace(/^<|>$/g, '');
  return kind ? `${kind}:${token}` : token;
}

function SourceAnswerText({
  answer,
  citations,
  onOpen,
}: {
  answer: string;
  citations: AskCitation[];
  onOpen: (c: AskCitation) => void;
}) {
  const index = new Map(citations.map((c, i) => [citeLookupKey(c.id), i]));
  const citeMarks: CiteMarks = {
    pattern: CITE_RE,
    render: (citeId) => {
      const i = index.get(citeLookupKey(citeId));
      if (i === undefined) return null;
      const cit = citations[i];
      return (
        <button
          type="button"
          className={`cite-marker${cit.verified === false ? ' warn' : ''}`}
          onClick={() => onOpen(cit)}
          title={cit.title ?? cit.id}
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

interface Turn {
  question: string;
  response: AskResponse | null;
  errorKey: MsgKey | null;
}

function genChatId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

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

export type ChatFocusTarget =
  | { kind: 'source'; sha: string }
  | { kind: 'theme'; theme: string };

export interface FocusChatPanelProps {
  focus: ChatFocusTarget;
  title: string;
  /** Compact, already-localized context meta line ("8 cards · 12 units · …"). */
  metaLine: string;
  /** Readable titles for unit/card cite chips (canonical id → snippet) —
   * the source page builds this from its memory payload. */
  citeTitles?: Map<string, string> | null;
  open: boolean;
  onClose: () => void;
  /** Optional session stem from URL (`?chat=`) to resume. */
  resumeChat?: string | null;
}

export default function FocusChatPanel({
  focus,
  title,
  metaLine,
  citeTitles = null,
  open,
  onClose,
  resumeChat = null,
}: FocusChatPanelProps) {
  const sha = focus.kind === 'source' ? focus.sha : null;
  const themeName = focus.kind === 'theme' ? focus.theme : null;
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [sessionChat, setSessionChat] = useState<string | null>(null);
  const [pollChat, setPollChat] = useState<string | null>(null);
  const [live, setLive] = useState<AskProgress | null>(null);
  const [recents, setRecents] = useState<ChatEntry[]>([]);
  /** True when the open thread was loaded from history (still continuable). */
  const [fromHistory, setFromHistory] = useState(false);
  const [panelWidth, setPanelWidth] = useState(readStoredPanelWidth);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const askStatusRef = useRef<boolean | null>(null);
  const resizeRef = useRef<{ startX: number; startW: number } | null>(null);

  const toTurn = (question: string, answer: string, chat: string | null, extra?: Partial<AskResponse>): Turn => {
    const raw = citationsFromAnswerText(answer);
    const grounded = sha ? groundCitesOnSource(raw, sha) : raw;
    const citations = citeTitles ? applyMemoryTitles(grounded, citeTitles) : grounded;
    return {
      question: displayUserQuestion(question),
      errorKey: null,
      response: {
        answer,
        citations,
        verified: null,
        context_hits: citations.length,
        chat,
        ...extra,
      },
    };
  };

  const refreshRecents = () => {
    fetchChats()
      .then((all) =>
        setRecents(
          all
            .filter((c) =>
              sha ? c.focus_source === sha : c.focus_theme === themeName,
            )
            .slice(0, 8),
        ),
      )
      .catch(() => setRecents([]));
  };

  useEffect(() => {
    if (!open) return;
    refreshRecents();
    fetchAskStatus()
      .then((s) => {
        askStatusRef.current = s.agent;
      })
      .catch(() => {
        /* submit re-reads */
      });
  }, [open, sha, themeName]);

  // Resume a saved session from deep link or recent list.
  useEffect(() => {
    if (!open || !resumeChat) return;
    let cancelled = false;
    setFromHistory(true);
    setSessionChat(resumeChat);
    setTurns([]);
    fetchAskSession(resumeChat)
      .catch(() => ({ turns: [] }))
      .then(async (session) => {
        if (cancelled) return;
        if (session.turns.length > 0) {
          setTurns(
            session.turns.map((turn) =>
              toTurn(turn.question, turn.answer, resumeChat, {
                agent: true,
                stopped_reason: turn.stopped_reason,
                turn_id: turn.turn_id,
                tool_trace: turn.tool_trace,
                ...(turn.citations?.length ? { citations: turn.citations } : {}),
              }),
            ),
          );
          return;
        }
        const md = await fetchChatMarkdown(resumeChat).catch(() => '');
        if (cancelled || !md) return;
        const parsed = parseChatTranscript(md);
        setTurns(parsed.map((turn) => toTurn(turn.question, turn.answer, resumeChat)));
      });
    return () => {
      cancelled = true;
    };
  }, [open, resumeChat, sha, themeName]);

  const onResizePointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    resizeRef.current = { startX: e.clientX, startW: panelWidth };
  }, [panelWidth]);

  const onResizePointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    const s = resizeRef.current;
    if (!s) return;
    // Dragging the left edge: moving left grows the panel.
    setPanelWidth(clampPanelWidth(s.startW + (s.startX - e.clientX)));
  }, []);

  const onResizePointerUp = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!resizeRef.current) return;
    resizeRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    setPanelWidth((w) => {
      const next = clampPanelWidth(w);
      try {
        localStorage.setItem(PANEL_WIDTH_KEY, String(next));
      } catch {
        /* private mode */
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => composerRef.current?.focus(), 50);
    return () => window.clearTimeout(id);
  }, [open]);

  useEffect(() => {
    if (!pending || !pollChat) return;
    let cancelled = false;
    const tick = () => {
      fetchAskProgress(pollChat)
        .then((p) => {
          if (!cancelled) setLive(p);
        })
        .catch(() => {
          /* transient */
        });
    };
    tick();
    const id = window.setInterval(tick, 700);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [pending, pollChat]);

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [turns, pending, live]);

  const startNew = () => {
    setTurns([]);
    setSessionChat(null);
    setFromHistory(false);
    setDraft('');
    setLive(null);
    setPollChat(null);
  };

  const openRecent = (name: string) => {
    setFromHistory(true);
    setSessionChat(name);
    setTurns([]);
    setLive(null);
    setDraft('');
    fetchAskSession(name)
      .catch(() => ({ turns: [] }))
      .then(async (session) => {
        if (session.turns.length > 0) {
          setTurns(
            session.turns.map((turn) =>
              toTurn(turn.question, turn.answer, name, {
                agent: true,
                stopped_reason: turn.stopped_reason,
                turn_id: turn.turn_id,
                tool_trace: turn.tool_trace,
                ...(turn.citations?.length ? { citations: turn.citations } : {}),
              }),
            ),
          );
          return;
        }
        const md = await fetchChatMarkdown(name).catch(() => '');
        if (!md) return;
        const parsed = parseChatTranscript(md);
        setTurns(parsed.map((turn) => toTurn(turn.question, turn.answer, name)));
      });
  };

  const submit = () => {
    const question = draft.trim();
    // Multi-turn always allowed — history threads resume with the same stem.
    if (!question || pending) return;
    setDraft('');
    setPending(true);
    setPollChat(null);
    setLive(null);
    const history = turns
      .filter((t) => t.response?.answer)
      .map((t) => ({
        question: t.question,
        answer: t.response!.answer,
      }));
    setTurns((prev) => [...prev, { question, response: null, errorKey: null }]);
    let chat = sessionChat;
    void (async () => {
      const agent = await fetchAskStatus()
        .then((s) => {
          askStatusRef.current = s.agent;
          return s.agent;
        })
        .catch(() => askStatusRef.current ?? false);
      if (agent && !chat) {
        chat = genChatId(sha ? 'src' : 'thm');
        setSessionChat(chat);
      }
      setPollChat(agent ? chat : null);
      return postAsk(question, {
        chat,
        history,
        ...(sha ? { focus_source: sha } : { focus_theme: themeName! }),
      });
    })()
      .then((response) => {
        const rawCites = response.citations?.length
          ? response.citations
          : citationsFromAnswerText(response.answer);
        const grounded = sha ? groundCitesOnSource(rawCites, sha) : rawCites;
        const citations = citeTitles
          ? applyMemoryTitles(grounded, citeTitles)
          : grounded;
        setTurns((prev) =>
          prev.map((turn, i) =>
            i === prev.length - 1
              ? {
                  ...turn,
                  question: displayUserQuestion(turn.question),
                  response: { ...response, citations },
                }
              : turn,
          ),
        );
        if (response.chat) {
          setSessionChat((prev) => prev ?? response.chat);
        }
        refreshRecents();
      })
      .catch((err: unknown) => {
        setTurns((prev) =>
          prev.map((turn, i) =>
            i === prev.length - 1
              ? { ...turn, errorKey: errorKeyFor(err) }
              : turn,
          ),
        );
      })
      .finally(() => setPending(false));
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isReactImeComposing(e)) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const seeds: MsgKey[] = sha
    ? ['source.chatSeed1', 'source.chatSeed2', 'source.chatSeed3']
    : ['theme.chatSeed1', 'theme.chatSeed2', 'theme.chatSeed3'];

  const chatDate = (entry: ChatEntry) =>
    entry.mtime > 0
      ? new Date(entry.mtime * 1000).toLocaleString(
          lang === 'zh' ? 'zh-CN' : 'en-US',
          { dateStyle: 'medium', timeStyle: 'short' },
        )
      : entry.name;



  return (
    <aside
      className={`source-chat-panel${open ? '' : ' is-hidden'}`}
      aria-label={t(sha ? 'source.chatPanelTitle' : 'theme.chatPanelTitle')}
      aria-hidden={!open}
      hidden={!open}
      style={{ width: panelWidth, maxWidth: '100vw' }}
    >
      <div
        className="source-chat-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label={t('source.chatResize')}
        aria-valuenow={panelWidth}
        aria-valuemin={PANEL_WIDTH_MIN}
        aria-valuemax={PANEL_WIDTH_MAX}
        tabIndex={0}
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        onPointerCancel={onResizePointerUp}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') {
            e.preventDefault();
            setPanelWidth((w) => {
              const next = clampPanelWidth(w + 24);
              try {
                localStorage.setItem(PANEL_WIDTH_KEY, String(next));
              } catch {
                /* ignore */
              }
              return next;
            });
          } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            setPanelWidth((w) => {
              const next = clampPanelWidth(w - 24);
              try {
                localStorage.setItem(PANEL_WIDTH_KEY, String(next));
              } catch {
                /* ignore */
              }
              return next;
            });
          }
        }}
      />
      <header className="source-chat-head">
        <div className="source-chat-head-title">
          <h3 style={{ margin: 0 }}>{t(sha ? 'source.chatPanelTitle' : 'theme.chatPanelTitle')}</h3>
          <p className="tiny muted" style={{ margin: '0.2rem 0 0' }}>
            {t(sha ? 'source.chatGroundedIn' : 'theme.chatGroundedIn')}{' '}
            <strong title={title}>{title.length > 48 ? `${title.slice(0, 48)}…` : title}</strong>
          </p>
        </div>
        <div className="source-chat-head-actions">
          {(turns.length > 0 || sessionChat) && (
            <button type="button" className="tiny" onClick={startNew}>
              {fromHistory
                ? t(sha ? 'source.chatNewOnSource' : 'theme.chatNewOnTheme')
                : t('ask.newConversation')}
            </button>
          )}
          <Link
            className="source-chat-open-ask"
            to={`/ask${sessionChat ? `/chat/${encodeURIComponent(sessionChat)}` : ''}`}
            title={t('source.chatOpenInAsk')}
          >
            {t('source.chatOpenInAsk')}
            <span aria-hidden="true">↗</span>
          </Link>
          <button type="button" className="tiny source-chat-close" onClick={onClose}>
            {t('source.chatClose')}
          </button>
        </div>
      </header>

      {/* Context is injected server-side — UI only shows a compact meta line,
          never the raw body/memory/crystal dump. */}
      <div className="source-chat-pack" title={metaLine}>
        <span className="source-chat-meta mono tiny">{metaLine}</span>
        <span className="tiny muted source-chat-pack-hint">{t('source.chatPackHint')}</span>
      </div>

      {recents.length > 0 && (
        <div className="source-chat-recents">
          <span className="tiny muted">{t('source.chatRecents')}</span>
          <ul>
            {recents.map((c) => (
              <li key={c.name}>
                <button
                  type="button"
                  className={sessionChat === c.name ? 'active' : undefined}
                  onClick={() => openRecent(c.name)}
                >
                  <span className="source-chat-recent-preview">
                    {c.preview || chatDate(c)}
                  </span>
                  <span className="tiny muted mono">{chatDate(c)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="source-chat-thread" ref={threadRef}>
        {turns.length === 0 && (
          <div className="source-chat-empty">
            <p className="sm">{t(sha ? 'source.chatEmpty' : 'theme.chatEmpty')}</p>
            <ul className="example-list">
              {seeds.map((key) => (
                <li key={key}>
                  <button
                    type="button"
                    onClick={() => {
                      setDraft(t(key));
                      composerRef.current?.focus();
                    }}
                  >
                    {t(key)} →
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {turns.map((turn, i) => {
          const q = displayUserQuestion(turn.question);
          const cites = turn.response?.citations ?? [];
          return (
            <div key={`t${i}`} className="chat-turn">
              <div className="chat-q">{q}</div>
              {turn.response && (
                <div className="chat-a">
                  <SourceAnswerText
                    answer={turn.response.answer}
                    citations={cites}
                    onOpen={(c) => {
                      if (c.link_target) navigate(c.link_target);
                    }}
                  />
                  {cites.length > 0 && (
                    <div className="source-chat-cites tiny muted">
                      {cites.map((c, ci) => (
                        <button
                          key={`${c.id}-${ci}`}
                          type="button"
                          className="source-chat-cite-chip"
                          title={c.title ?? c.id}
                          onClick={() => {
                            if (c.link_target) navigate(c.link_target);
                          }}
                        >
                          [{ci + 1}] {c.kind || 'cite'}
                          {c.title && c.title !== c.id
                            ? ` · ${c.title.length > 36 ? `${c.title.slice(0, 36)}…` : c.title}`
                            : ''}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {turn.errorKey && <div className="chat-a chat-error">{t(turn.errorKey)}</div>}
              {!turn.response && !turn.errorKey && i === turns.length - 1 && pending && (
                <div className="chat-a chat-pending">
                  <span className="muted">
                    {live && live.events.length > 0
                      ? t('source.chatWorking', { n: live.events.length })
                      : t('ask.pending')}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="source-chat-composer">
        <textarea
          ref={composerRef}
          data-omnibox-suppress
          value={draft}
          placeholder={t(sha ? 'source.chatPlaceholder' : 'theme.chatPlaceholder')}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          disabled={pending}
          rows={3}
        />
        <div className="composer-foot">
          <span className="tiny muted">{t('ask.hint')}</span>
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
    </aside>
  );
}
