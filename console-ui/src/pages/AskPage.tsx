/** Ask page `/ask` — answers US5 (design §3.5).
 *
 * Three columns: saved chat history (left, from /api/chats — click renders
 * the raw markdown read-only with the shared escape-first renderer), the
 * conversation thread + composer (center), and the citations panel for the
 * LATEST answer (right rail). The answer's `[kind:id]` citations become
 * numbered [1][2] markers in reading order (the same order the server
 * returns `citations` in); hovering a marker highlights the panel entry,
 * clicking deep-links (claims → /knowledge#id, sources → /library/:sha —
 * the server already applied the sha-guard, a null link renders as text).
 * Citations the verifier could not back carry a warn pill.
 *
 * The textarea sets `data-omnibox-suppress` so the Shell's global ⌘K
 * handler leaves it alone while composing. */
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useNavigate } from 'react-router-dom';
import { EmptyState, PageHelp } from '../components/ui';
import { useI18n, type MsgKey } from '../i18n';
import { AskError, fetchChatMarkdown, fetchChats, postAsk } from '../lib/api';
import { MarkdownView } from '../lib/markdown';
import type { AskCitation, AskResponse, ChatEntry } from '../lib/types';

interface Turn {
  question: string;
  response: AskResponse | null;
  /** i18n key of the failure — a turn has either a response or an error. */
  errorKey: MsgKey | null;
}

/** `[claim:…] [card:…] [unit:…]` tokens — mirrors the server tokenizer
 * (ovp-memory::verify), which is the source of truth for what counts as a
 * citation; anything this regex misses simply stays plain text. */
const CITE_RE = /\[\s*((?:claim|card|unit):[^\]\n]+?)\s*\]/g;

function errorKeyFor(err: unknown): MsgKey {
  if (err instanceof AskError) {
    if (err.status === 503) return 'ask.errNotConfigured';
    if (err.status === 504) return 'ask.errTimeout';
  }
  return 'ask.errGeneric';
}

/** Answer text with `[kind:id]` citations replaced by numbered markers. */
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
  const nodes: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of answer.matchAll(CITE_RE)) {
    const at = m.index ?? 0;
    const i = index.get(m[1]);
    if (i === undefined) continue; // not a returned citation — leave as text
    if (at > last) nodes.push(answer.slice(last, at));
    const cit = citations[i];
    nodes.push(
      <button
        key={`m${k}`}
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
      </button>,
    );
    k += 1;
    last = at + m[0].length;
  }
  nodes.push(answer.slice(last));
  return <div className="answer-text">{nodes}</div>;
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
      {citations.map((c, i) => (
        <div
          key={c.id}
          className={`cite-entry${hoverId === c.id ? ' hover-hit' : ''}`}
        >
          <div className="cite-entry-top">
            <span className="cite-num mono">[{i + 1}]</span>
            <span className="pill">{c.kind}</span>
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
      ))}
    </div>
  );
}

export default function AskPage() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [hoverId, setHoverId] = useState<string | null>(null);

  const [chats, setChats] = useState<ChatEntry[]>([]);
  const [openChat, setOpenChat] = useState<string | null>(null);
  const [chatMd, setChatMd] = useState<string | null>(null);

  const threadRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const refreshChats = () => {
    fetchChats()
      .then(setChats)
      .catch(() => {
        // History degrades to empty — the thread still works.
      });
  };
  useEffect(refreshChats, []);

  // Keep the newest turn in view while a conversation grows.
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [turns, pending]);

  const submit = () => {
    const question = draft.trim();
    if (!question || pending) return;
    setOpenChat(null);
    setChatMd(null);
    setDraft('');
    setPending(true);
    setTurns((prev) => [...prev, { question, response: null, errorKey: null }]);
    postAsk(question)
      .then((response) => {
        setTurns((prev) =>
          prev.map((turn, i) =>
            i === prev.length - 1 ? { ...turn, response } : turn,
          ),
        );
        refreshChats(); // the server saved the transcript
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
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const openCitation = (cit: AskCitation) => {
    if (cit.link_target) navigate(cit.link_target);
  };

  const showChat = (name: string) => {
    setOpenChat(name);
    setChatMd(null);
    fetchChatMarkdown(name)
      .then(setChatMd)
      .catch(() => setChatMd(t('ask.chatLoadError')));
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

  const latest = [...turns].reverse().find((turn) => turn.response);
  const citations = latest?.response?.citations ?? [];
  const examples: MsgKey[] = ['ask.example1', 'ask.example2', 'ask.example3'];

  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('ask.title')}</h1>
      <PageHelp>{t('ask.help')}</PageHelp>

      <div className="grid ask">
        {/* left: saved chat history */}
        <div>
          <div className="facet-group">
            <h3>{t('ask.historyTitle')}</h3>
            {chats.length === 0 ? (
              <p className="tiny muted">{t('ask.historyEmpty')}</p>
            ) : (
              <ul className="facet-list chat-list">
                {chats.map((entry) => (
                  <li key={entry.name}>
                    <button
                      type="button"
                      className={openChat === entry.name ? 'active' : ''}
                      onClick={() => showChat(entry.name)}
                    >
                      <span className="chat-date">{chatDate(entry)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* center: saved-chat reader OR the live thread + composer */}
        <div className="ask-main">
          {openChat ? (
            <>
              <div className="chat-reader-head">
                <span className="mono tiny muted">
                  {t('ask.savedChat')} · {openChat}
                </span>
                <button
                  type="button"
                  className="tab-like"
                  onClick={() => {
                    setOpenChat(null);
                    setChatMd(null);
                  }}
                >
                  ← {t('ask.closeChat')}
                </button>
              </div>
              {chatMd == null ? (
                <div className="portal-note">{t('common.loading')}</div>
              ) : (
                <MarkdownView markdown={chatMd} />
              )}
            </>
          ) : (
            <>
              <div className="chat-thread" ref={threadRef}>
                {turns.length === 0 && (
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
                )}
                {turns.map((turn, i) => (
                  <div key={`t${i}`} className="chat-turn">
                    <div className="chat-q">{turn.question}</div>
                    {turn.response && (
                      <div className="chat-a">
                        <AnswerText
                          answer={turn.response.answer}
                          citations={turn.response.citations}
                          onHover={setHoverId}
                          onOpen={openCitation}
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
                      <div className="chat-a chat-error">
                        {t(turn.errorKey)}
                      </div>
                    )}
                    {!turn.response &&
                      !turn.errorKey &&
                      i === turns.length - 1 &&
                      pending && (
                        <div className="chat-a chat-pending muted">
                          {t('ask.pending')}
                        </div>
                      )}
                  </div>
                ))}
              </div>

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

        {/* right rail: citations for the latest answer */}
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
