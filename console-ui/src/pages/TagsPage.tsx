/** Tags `/tags` — the curation surface (live portal only; the published
 * site redacts tags entirely). Two jobs on one page:
 *  1. vocabulary browser — every canonical tag with user/inferred counts,
 *     text filter, click-through to the filtered Library;
 *  2. curation inbox — pending merge proposals from `tags-suggest` as
 *     Accept/Reject cards. Accept records into the MACHINE-owned
 *     decisions.toml and the server rebuilds the projection; Reject is
 *     remembered so the pair never resurfaces. The operator's hand-edited
 *     aliases.toml is never rewritten by this page. */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchTags, postTagDecision, type TagsPayload } from '../lib/api';
import { EmptyState, PageHelp } from '../components/ui';
import { useI18n } from '../i18n';

export default function TagsPage() {
  const { t } = useI18n();
  const [data, setData] = useState<TagsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const reload = () => {
    fetchTags()
      .then((d) => setData(d))
      .catch((e: Error) => setError(e.message));
  };
  useEffect(reload, []);

  const decide = async (action: 'accept' | 'reject', alias: string, canonical: string) => {
    setBusy(`${alias}→${canonical}`);
    setError(null);
    try {
      await postTagDecision(action, alias, canonical);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const needle = filter.trim().toLowerCase();
  const rows = (data?.tags ?? []).filter((r) => !needle || r.tag.toLowerCase().includes(needle));

  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('tags.title')}</h1>
      <PageHelp>{t('tags.help')}</PageHelp>
      {error && <p className="fail-note">{error}</p>}

      {(data?.proposals.length ?? 0) > 0 && (
        <div className="section">
          <h2>
            {t('tags.inbox')} ({data?.proposals.length})
          </h2>
          <div className="row-list">
            {data?.proposals.map((p) => {
              const key = `${p.alias}→${p.canonical}`;
              return (
                <div className="row" key={key}>
                  <span className="row-main">
                    <span className="mono">
                      #{p.alias} ({p.alias_count}) → #{p.canonical} ({p.canonical_count})
                    </span>
                    <span className="muted sm">cosine {p.cosine.toFixed(3)}</span>
                    {((p.alias_titles?.length ?? 0) > 0 ||
                      (p.canonical_titles?.length ?? 0) > 0) && (
                      <span className="fail-note muted tiny" style={{ color: 'inherit' }}>
                        #{p.alias}: {(p.alias_titles ?? []).join(' · ') || '—'}
                        <br />#{p.canonical}: {(p.canonical_titles ?? []).join(' · ') || '—'}
                      </span>
                    )}
                  </span>
                  <span className="meta">
                    <button
                      type="button"
                      className="tag-chip"
                      disabled={busy === key}
                      onClick={() => decide('accept', p.alias, p.canonical)}
                    >
                      ✓ {t('tags.accept')}
                    </button>
                    <button
                      type="button"
                      className="tag-chip"
                      disabled={busy === key}
                      onClick={() => decide('reject', p.alias, p.canonical)}
                    >
                      ✕ {t('tags.reject')}
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="section">
        <h2>
          {t('tags.vocabulary')} ({data?.tags.length ?? 0})
        </h2>
        <input
          type="search"
          value={filter}
          placeholder={t('tags.filter')}
          onChange={(e) => setFilter(e.target.value)}
          style={{ marginBottom: '0.6rem' }}
        />
        {data && rows.length === 0 && (
          <EmptyState>
            <p>{t('tags.empty')}</p>
          </EmptyState>
        )}
        <div className="row-list">
          {rows.map((r) => (
            <div className="row" key={r.tag}>
              <span className="row-main">
                <Link to={`/library?tag=${encodeURIComponent(r.tag)}`}>#{r.tag}</Link>
                {r.origin && r.origin !== 'user' && (
                  <span className="muted sm">({r.origin})</span>
                )}
              </span>
              <span className="meta">
                {r.user}
                {r.inferred > 0 ? ` + ~${r.inferred}` : ''}
              </span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
