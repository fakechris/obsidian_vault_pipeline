/** Entities `/entities` — the Tier-0 URL entity index: machine-verified
 * referents (github repos, arxiv papers, packages) ranked by how many of your
 * sources mention them. The cross-source count is the value ("this repo
 * appears in 7 things I've read"). Deterministic, no LLM. */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { entityUrl, fetchEntities, type EntityRow } from '../lib/api';
import { EmptyState, PageHelp } from '../components/ui';
import { useI18n } from '../i18n';

const KINDS = ['all', 'github', 'arxiv', 'doi', 'npm', 'crates', 'pypi', 'hn'];

export default function EntitiesPage() {
  const { t } = useI18n();
  const [rows, setRows] = useState<EntityRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState('all');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    fetchEntities()
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, []);

  const needle = filter.trim().toLowerCase();
  const shown = (rows ?? []).filter(
    (r) => (kind === 'all' || r.kind === kind) && (!needle || r.id.toLowerCase().includes(needle)),
  );

  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('entities.title')}</h1>
      <PageHelp>{t('entities.help')}</PageHelp>
      {error && <p className="fail-note">{error}</p>}

      <div className="filter-row">
        {KINDS.map((k) => (
          <button
            key={k}
            type="button"
            className={kind === k ? 'active' : ''}
            onClick={() => setKind(k)}
          >
            {k === 'all' ? t('entities.all') : k}
          </button>
        ))}
      </div>
      <input
        type="search"
        value={filter}
        placeholder={t('entities.filter')}
        onChange={(e) => setFilter(e.target.value)}
        style={{ margin: '0.6rem 0' }}
      />

      {rows && shown.length === 0 && (
        <EmptyState>
          <p>{t('entities.empty')}</p>
        </EmptyState>
      )}
      <div className="row-list">
        {shown.map((r) => (
          <div className="row" key={r.id}>
            <span className="row-main">
              <Link to={`/entity/${encodeURIComponent(r.id)}`}>@{r.id}</Link>
              {entityUrl(r.id) && (
                <a
                  href={entityUrl(r.id)!}
                  target="_blank"
                  rel="noreferrer"
                  className="entity-out"
                  title={entityUrl(r.id)!}
                >
                  ↗
                </a>
              )}
            </span>
            <span className="meta">
              {r.count} {t('entities.sources')}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
