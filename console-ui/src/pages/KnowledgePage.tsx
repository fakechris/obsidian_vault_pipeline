/** Knowledge home `/knowledge` — answers US3 (design §3.3).
 *
 * Theme card wall (claim count, durable ratio bar, top claim snippet) from
 * /api/model claims + /api/themes, with a URL-parameterized [List | Graph]
 * view toggle (?view=graph — same rule as the Library facets). The graph
 * view is the KnowledgeGraph component at global scope: the old standalone
 * /viz Graph page folded into the portal (design §2).
 *
 * Claim deep links land here as /knowledge#<claim_id> (graph double-clicks,
 * citing-claim lists, search results). The claim lives on its theme's page,
 * so the hash resolves through the model and forwards to
 * /knowledge/theme/:t#<claim_id> where the card scrolls into view. */
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import KnowledgeGraph from '../components/KnowledgeGraph';
import { AgeLabel, EmptyState, ModelGate, PageHelp } from '../components/ui';
import { useI18n } from '../i18n';
import { fetchThemes } from '../lib/api';
import { isMiscTheme, themeWall, type ThemeGroup } from '../lib/derive';
import type { IndexModel, ThemeCount } from '../lib/types';
import { useModel } from '../model';

type View = 'list' | 'graph';

function themePath(theme: string): string {
  return `/knowledge/theme/${encodeURIComponent(theme)}`;
}

function ThemeCard({ group }: { group: ThemeGroup }) {
  const { t } = useI18n();
  const active = group.durable + group.caveated;
  const pct = active > 0 ? Math.round((group.durable / active) * 100) : 0;
  // The 'misc' fallback bucket displays honestly as "Unclassified" — the
  // link target keeps the literal theme key (display layer only).
  const misc = isMiscTheme(group.theme);
  return (
    <Link className="theme-card" to={themePath(group.theme)}>
      <div className="theme-card-head">
        <span className="theme-card-name">
          {misc
            ? t('theme.unclassified')
            : group.theme || t('knowledge.untitledTheme')}
        </span>
        <span className="theme-card-count mono">
          {t('knowledge.claimCount', { n: group.total })}
        </span>
      </div>
      <div
        className="ratio-bar"
        title={t('knowledge.ratioLine', {
          durable: group.durable,
          caveated: group.caveated,
        })}
      >
        <span className="ratio-durable" style={{ width: `${pct}%` }} />
      </div>
      <div
        className="theme-card-ratio tiny muted"
        title={`durable — ${t('concept.durableTip')}\ncaveated — ${t('concept.caveatedTip')}`}
      >
        {t('knowledge.ratioLine', {
          durable: group.durable,
          caveated: group.caveated,
        })}
      </div>
      {misc && (
        <p className="theme-card-ratio tiny muted">
          {t('theme.unclassifiedNote')}
        </p>
      )}
      {group.topClaim && <p className="theme-card-snippet">{group.topClaim}</p>}
    </Link>
  );
}

function KnowledgeBody({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const view: View = params.get('view') === 'graph' ? 'graph' : 'list';
  const setView = (next: View) => {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next === 'graph') p.set('view', 'graph');
        else p.delete('view');
        return p;
      },
      { replace: true },
    );
  };

  const [themes, setThemes] = useState<ThemeCount[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchThemes()
      .then((list) => {
        if (!cancelled) setThemes(list);
      })
      .catch(() => {
        // The wall degrades to model-derived groups (crystal-only counts
        // missing) — not an error state.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const wall = themeWall(model.claims, themes);

  return (
    <>
      <div className="tab-row">
        <button
          type="button"
          className={view === 'list' ? 'active' : ''}
          onClick={() => setView('list')}
        >
          {t('knowledge.viewList')}
        </button>
        <button
          type="button"
          className={view === 'graph' ? 'active' : ''}
          onClick={() => setView('graph')}
        >
          {t('knowledge.viewGraph')}
        </button>
      </div>

      {view === 'list' && (
        <>
          {wall.length === 0 && (
            <EmptyState>
              <p>{t('knowledge.empty')}</p>
            </EmptyState>
          )}
          <div className="theme-wall">
            {wall.map((g) => (
              <ThemeCard key={g.theme || '(untitled)'} group={g} />
            ))}
          </div>
        </>
      )}

      {view === 'graph' && (
        <>
          <KnowledgeGraph scope="global" height={560} />
          <div className="graph-caption">{t('knowledge.graphCaption')}</div>
        </>
      )}
    </>
  );
}

export default function KnowledgePage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  const location = useLocation();

  // /knowledge#<claim_id> → the claim's theme page, anchor preserved.
  const anchor = decodeURIComponent(location.hash.replace(/^#/, ''));
  if (anchor && model) {
    const claim = model.claims.find((c) => c.claim_id === anchor);
    if (claim?.theme) {
      return (
        <Navigate to={`${themePath(claim.theme)}#${anchor}`} replace />
      );
    }
  }

  return (
    <ModelGate loading={loading} error={error}>
      {model && (
        <>
          <h1 style={{ marginTop: '1rem' }}>{t('knowledge.title')}</h1>
          <p className="muted sm" style={{ marginTop: '-2px' }}>
            <AgeLabel builtAt={model.built_at} />
          </p>
          {anchor && (
            <div className="portal-note tiny">
              {t('knowledge.unknownClaim', { id: anchor })}
            </div>
          )}
          <PageHelp>
            {t('knowledge.help')}
            <br />
            {t('knowledge.helpLayers')}
            <br />
            {t('knowledge.helpLadder')}
          </PageHelp>
          <KnowledgeBody model={model} />
        </>
      )}
    </ModelGate>
  );
}
