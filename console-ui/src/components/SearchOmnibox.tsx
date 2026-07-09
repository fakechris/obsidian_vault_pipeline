/** SearchOmnibox — one search component, two hosts (design §3.4):
 * the `/search` page (query URL-parameterized as ?q=) and the global ⌘K
 * overlay the Shell opens from any page.
 *
 * Debounced query → /api/search (sources / packs / claims / runs as
 * display lines + stable ids) — one call covers every model kind, so
 * /api/find adds nothing here. Themes are matched client-side against
 * /api/themes (a dozen rows). Results group by kind with status pills;
 * links follow the portal rules: source → /library/:sha, claim →
 * /knowledge#<claim_id>, theme → /knowledge/theme/:t, pack → its source's
 * page. Packs whose source sha is unknown (legacy) render unlinked —
 * never navigate to a 404 (handoff note 5).
 *
 * Keyboard: ↑/↓ move, Enter opens, Esc closes the overlay. */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useI18n, type MsgKey } from '../i18n';
import { fetchSearchHits, fetchThemes } from '../lib/api';
import { EmptyState } from './ui';
import type { FindHit, ThemeCount } from '../lib/types';
import { useModel } from '../model';

const DEBOUNCE_MS = 250;
const MIN_QUERY_LEN = 2;
const MAX_PER_GROUP = 8;

/** Statuses with a translated sourceStatus.* label; claim statuses render
 * verbatim like ClaimPill does. */
const SOURCE_STATUSES = new Set([
  'processed',
  'queued',
  'blocked',
  'needs_content',
  'failed',
  'unparseable',
  'duplicate',
]);

/** Kinds shown, in display order. Runs are a system concern — not a
 * knowledge search result. */
const GROUPS = ['claim', 'source', 'pack', 'theme'] as const;
type Group = (typeof GROUPS)[number];

interface ResultItem {
  key: string;
  group: Group;
  /** Status pill class + label (claim/source statuses). */
  status?: string;
  label: string;
  /** Absent = render unlinked (legacy pack without a source sha). */
  to?: string;
}

function groupLabelKey(g: Group): MsgKey {
  return `search.group.${g}` as MsgKey;
}

export interface SearchOmniboxProps {
  variant: 'page' | 'overlay';
  /** Overlay host: called on Esc, backdrop click and after navigation. */
  onClose?: () => void;
}

export default function SearchOmnibox({ variant, onClose }: SearchOmniboxProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { model } = useModel();
  const [searchParams, setSearchParams] = useSearchParams();

  // Page variant: the query IS the URL (?q=), shareable like every other
  // portal filter. Overlay variant: ephemeral local state.
  const urlQuery = searchParams.get('q') ?? '';
  const [localQuery, setLocalQuery] = useState(variant === 'page' ? urlQuery : '');
  const query = variant === 'page' ? urlQuery : localQuery;
  const setQuery = (next: string) => {
    setLocalQuery(next);
    if (variant === 'page') {
      setSearchParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (next) p.set('q', next);
          else p.delete('q');
          return p;
        },
        { replace: true },
      );
    }
  };

  const [hits, setHits] = useState<FindHit[]>([]);
  const [themes, setThemes] = useState<ThemeCount[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Themes load once — the client-side needle runs against this list.
  useEffect(() => {
    let cancelled = false;
    fetchThemes()
      .then((list) => {
        if (!cancelled) setThemes(list);
      })
      .catch(() => {
        // Theme group silently absent (crystal ledger unavailable).
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounced /api/search.
  useEffect(() => {
    const q = query.trim();
    if (q.length < MIN_QUERY_LEN) {
      setHits([]);
      setSearching(false);
      setError(null);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const timer = setTimeout(() => {
      fetchSearchHits(q)
        .then((list) => {
          if (!cancelled) {
            setHits(list);
            setError(null);
            setSearching(false);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setError(String(err));
            setSearching(false);
          }
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const results = useMemo<ResultItem[]>(() => {
    const q = query.trim().toLowerCase();
    if (q.length < MIN_QUERY_LEN) return [];
    const packSha = new Map(
      (model?.packs ?? [])
        .filter((p) => p.source_sha256)
        .map((p) => [p.pack_dir, p.source_sha256 as string]),
    );
    const byGroup = new Map<Group, ResultItem[]>();
    const push = (item: ResultItem) => {
      const list = byGroup.get(item.group) ?? [];
      if (list.length < MAX_PER_GROUP) list.push(item);
      byGroup.set(item.group, list);
    };
    for (const hit of hits) {
      // Superseded/retracted claims stay out — the knowledge surface only
      // lists active claims, so their anchors would resolve nowhere.
      if (
        hit.kind === 'claim' &&
        hit.id &&
        (hit.status === 'durable' || hit.status === 'caveated')
      ) {
        push({
          key: `claim:${hit.id}`,
          group: 'claim',
          status: hit.status,
          label: hit.line,
          to: `/knowledge#${hit.id}`,
        });
      } else if (hit.kind === 'source' && hit.id) {
        push({
          key: `source:${hit.id}`,
          group: 'source',
          status: hit.status,
          label: hit.line,
          to: `/library/${hit.id}`,
        });
      } else if (hit.kind === 'pack' && hit.id) {
        const sha = packSha.get(hit.id);
        push({
          key: `pack:${hit.id}`,
          group: 'pack',
          label: hit.line,
          to: sha ? `/library/${sha}` : undefined,
        });
      }
    }
    for (const theme of themes) {
      if (theme.theme.toLowerCase().includes(q)) {
        push({
          key: `theme:${theme.theme}`,
          group: 'theme',
          label: `${theme.theme} (${theme.count})`,
          to: `/knowledge/theme/${encodeURIComponent(theme.theme)}`,
        });
      }
    }
    return GROUPS.flatMap((g) => byGroup.get(g) ?? []);
  }, [hits, themes, query, model]);

  // Clamp the cursor when the result set shrinks.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(results.length - 1, 0)));
  }, [results]);

  const open = useCallback(
    (item: ResultItem | undefined) => {
      if (!item?.to) return;
      navigate(item.to);
      onClose?.();
    },
    [navigate, onClose],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => (results.length ? (a + 1) % results.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) =>
        results.length ? (a - 1 + results.length) % results.length : 0,
      );
    } else if (e.key === 'Enter') {
      e.preventDefault();
      open(results[active]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose?.();
    }
  };

  const showEmpty =
    query.trim().length >= MIN_QUERY_LEN &&
    !searching &&
    !error &&
    results.length === 0;

  let lastGroup: Group | null = null;

  return (
    <div className={`omnibox ${variant}`}>
      <input
        ref={inputRef}
        className="omni-input"
        type="search"
        value={query}
        placeholder={t('search.placeholder')}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        aria-label={t('search.placeholder')}
      />
      <div className="omni-hint tiny muted">{t('search.keys')}</div>
      {error && (
        <EmptyState>
          <p>{t('search.error')}</p>
        </EmptyState>
      )}
      {showEmpty && (
        <EmptyState>
          <p>{t('search.empty')}</p>
        </EmptyState>
      )}
      {results.length > 0 && (
        <ul className="omni-results">
          {results.map((item, i) => {
            const header = item.group !== lastGroup;
            lastGroup = item.group;
            return (
              <li key={item.key}>
                {header && (
                  <div className="omni-group">{t(groupLabelKey(item.group))}</div>
                )}
                <button
                  type="button"
                  className={`omni-row${i === active ? ' active' : ''}${item.to ? '' : ' nolink'}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => open(item)}
                  title={item.to ? undefined : t('search.noPage')}
                >
                  {item.status && (
                    <span className={`pill ${item.status.replace('_', '-')}`}>
                      {SOURCE_STATUSES.has(item.status)
                        ? t(`sourceStatus.${item.status}` as MsgKey)
                        : item.status}
                    </span>
                  )}
                  <span className="omni-label">{item.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
