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
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { EmptyState, PageHelp, conceptTipKey } from '../components/ui';
import { useI18n, type MsgKey } from '../i18n';
import { AskError, fetchChatMarkdown, fetchChats, postAsk } from '../lib/api';
import {
  citationsInOrder,
  citeLinkTarget,
  normalizeCiteToken,
  parseChatTranscript,
} from '../lib/chatTranscript';
import { isReactImeComposing } from '../lib/ime';
import { MarkdownView, type InlineMarker } from '../lib/markdown';
import type { AskCitation, AskResponse, ChatEntry } from '../lib/types';

interface Turn {
  question: string;
  response: AskResponse | null;
  /** i18n key of the failure — a turn has either a response or an error. */
  errorKey: MsgKey | null;
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

/** Build citation chips from answer text alone (saved-chat replay). */
function citationsFromAnswerText(answer: string): AskCitation[] {
  return citationsInOrder(answer).map((id) => {
    const kind = id.includes(':') ? id.slice(0, id.indexOf(':')) : '';
    return {
      id,
      kind,
      title: id,
      snippet: null,
      link_target: citeLinkTarget(id),
      // Saved transcript does not re-run the verifier; treat as known markers.
      verified: true,
    };
  });
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
  const index = new Map(citations.map((c, i) => [c.id, i]));
  const marker: InlineMarker = {
    pattern: CITE_RE,
    render: (m, key) => {
      const i = index.get(normalizeCiteToken(m[1]));
      if (i === undefined) return null;
      const cit = citations[i];
      return (
        <button
          key={key}
          type="button"
          className={`cite-marker${cit.verified ? '' : ' warn'}`}
          onMouseEnter={() => onHover(cit.id)}
          onMouseLeave={() => onHover(null)}
          onFocus={() => onHover(cit.id)}
          onBlur={() => onHover(null)}
          onClick={() => onOpen(cit)}
          title={cit.title ?? cit.id}
        >
          [{i + 1}]
        </button>
      );
    },
  };
  return (
    <div className="answer-text">
      <MarkdownView markdown={answer} gutter={false} marker={marker} />
    </div>
  );
}

function CitationPanel({
  citations,
  hoverId,
  onOpen,
}: {
  citations: AskCitation[];
  hoverId: string | null;
  onOpen: (cit: AskCitation) => void;
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
        return (
          <div
            key={c.id}
            className={`cite-entry${hoverId === c.id ? ' hover-hit' : ''}`}
          >
            <div className="cite-entry-top">
              <span className="cite-num mono">[{i + 1}]</span>
              <span className="pill" title={kindTip ? t(kindTip) : undefined}>
                {c.kind}
              </span>
              {!c.verified && (
                <span className="pill unverified">{t('ask.unverified')}</span>
              )}
            </div>
            <div className="cite-title">{c.title ?? c.id}</div>
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
  onHover,
  onOpen,
  threadRef,
  empty,
}: {
  turns: Turn[];
  pending: boolean;
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
            </div>
          )}
          {turn.errorKey && (
            <div className="chat-a chat-error">{t(turn.errorKey)}</div>
          )}
          {!turn.response &&
            !turn.errorKey &&
            i === turns.length - 1 &&
            pending && (
              <div className="chat-a chat-pending muted">{t('ask.pending')}</div>
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
  // URL is the source of truth for which saved chat is open (bookmarkable).
  const openChat = routeChatId ?? null;

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [hoverId, setHoverId] = useState<string | null>(null);
  /** Stem of the live multi-turn session (first successful answer's `chat`). */
  const [sessionChat, setSessionChat] = useState<string | null>(null);

  const [chats, setChats] = useState<ChatEntry[]>([]);
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

  // Load saved chat when the route points at one.
  useEffect(() => {
    openChatRef.current = openChat;
    setSavedTurns(null);
    setSavedError(null);
    setHoverId(null);
    if (!openChat) return;
    let cancelled = false;
    fetchChatMarkdown(openChat)
      .then((md) => {
        if (cancelled || openChatRef.current !== openChat) return;
        const parsed = parseChatTranscript(md);
        if (parsed.length === 0) {
          setSavedError(t('ask.chatParseEmpty'));
          setSavedTurns([]);
          return;
        }
        setSavedTurns(
          parsed.map((turn) => {
            const citations = citationsFromAnswerText(turn.answer);
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
  }, [turns, pending, savedTurns, openChat]);

  const startNewConversation = () => {
    setTurns([]);
    setSessionChat(null);
    setDraft('');
    navigate('/ask');
    composerRef.current?.focus();
  };

  const submit = () => {
    const question = draft.trim();
    if (!question || pending || openChat) return;
    setDraft('');
    setPending(true);
    const history = turns
      .filter((t) => t.response?.answer)
      .map((t) => ({
        question: t.question,
        answer: t.response!.answer,
      }));
    setTurns((prev) => [...prev, { question, response: null, errorKey: null }]);
    postAsk(question, { chat: sessionChat, history })
      .then((response) => {
        setTurns((prev) =>
          prev.map((turn, i) =>
            i === prev.length - 1 ? { ...turn, response } : turn,
          ),
        );
        if (response.chat) {
          setSessionChat((prev) => prev ?? response.chat);
        }
        refreshChats();
      })
      .catch((err: unknown) => {
        const errorKey = errorKeyFor(err);
        setTurns((prev) =>
          prev.map((turn, i) =>
            i === prev.length - 1 ? { ...turn, errorKey } : turn,
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

  const displayTurns = openChat ? (savedTurns ?? []) : turns;
  const latest = [...displayTurns].reverse().find((turn) => turn.response);
  const citations = latest?.response?.citations ?? [];
  const examples: MsgKey[] = ['ask.example1', 'ask.example2', 'ask.example3'];
  const viewingSaved = Boolean(openChat);

  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('ask.title')}</h1>
      <PageHelp>{t('ask.help')}</PageHelp>

      <div className="grid ask">
        {/* left: saved chat history — one row per conversation session */}
        <div>
          <div className="facet-group">
            <h3>{t('ask.historyTitle')}</h3>
            {(turns.length > 0 || sessionChat) && !viewingSaved && (
              <button
                type="button"
                className="tiny"
                style={{ marginBottom: '0.5rem' }}
                onClick={startNewConversation}
              >
                {t('ask.newConversation')}
              </button>
            )}
            {chats.length === 0 ? (
              <p className="tiny muted">{t('ask.historyEmpty')}</p>
            ) : (
              <ul className="facet-list chat-list">
                {chats.map((entry) => (
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
                      <span className="chat-date">{chatDate(entry)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* center: live thread or saved-chat replay (same bubble layout) */}
        <div className="ask-main">
          {viewingSaved ? (
            <>
              <div className="chat-reader-head">
                <span className="tiny muted">
                  {t('ask.savedChat')}
                  {' · '}
                  <span className="mono">
                    {openChatMeta ? chatDate(openChatMeta) : openChat}
                  </span>
                </span>
                <button
                  type="button"
                  className="tab-like"
                  onClick={() => navigate('/ask')}
                >
                  ← {t('ask.closeChat')}
                </button>
              </div>
              {savedTurns == null ? (
                <div className="portal-note">{t('common.loading')}</div>
              ) : savedError && savedTurns.length === 0 ? (
                <EmptyState>
                  <p>{savedError}</p>
                </EmptyState>
              ) : (
                <ChatThread
                  turns={displayTurns}
                  pending={false}
                  onHover={setHoverId}
                  onOpen={openCitation}
                  threadRef={threadRef}
                  empty={
                    <EmptyState>
                      <p>{savedError ?? t('ask.chatParseEmpty')}</p>
                    </EmptyState>
                  }
                />
              )}
            </>
          ) : (
            <>
              <ChatThread
                turns={turns}
                pending={pending}
                onHover={setHoverId}
                onOpen={openCitation}
                threadRef={threadRef}
                empty={
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
                }
              />

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
            </>
          )}
        </div>

        {/* right rail: citations for the latest answer (live or saved) */}
        <div>
          <div className="card">
            <h3 style={{ marginBottom: '0.6rem' }}>{t('ask.citationsTitle')}</h3>
            <CitationPanel
              citations={citations}
              hoverId={hoverId}
              onOpen={openCitation}
            />
          </div>
        </div>
      </div>
    </>
  );
}
