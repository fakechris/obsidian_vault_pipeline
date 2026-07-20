/** System `/system` — answers US8 (design §3.6): is the pipeline healthy,
 * where is it stuck, and what does this product even do?
 *
 * Sections (B5): (a) all recorded runs, (b) sources needing the operator
 * (same AttentionCard as Today) + the doctor hint, (c) pipeline surfaces —
 * the legacy Flow/Monitor views and the generated admin pages, (d) the
 * three-layer concept explainer, (e) read-only settings from /api/settings.
 * Internal vocabulary (pack/unit/ledger) is allowed HERE and only here
 * (design §6, BL-051 word layering). */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import AttentionCard from '../components/AttentionCard';
import { RunActivitySection } from '../components/RunActivity';
import { AgeLabel, EmptyState, ModelGate, PageHelp } from '../components/ui';
import { useI18n } from '../i18n';
import {
  STATIC_MODE,
  fetchPublishStatus,
  fetchSettings,
  startPublish,
  type PublishStatus,
} from '../lib/api';
import { attentionSources } from '../lib/derive';
import type { IndexModel, SettingsPayload } from '../lib/types';
import { useModel } from '../model';

/** Legacy generated console pages, served by exact filename (admin depth —
 * design §2 keeps them reachable until they are componentized). Plain <a>
 * links: they are server-rendered pages outside the SPA router. */
const ADMIN_PAGES = ['ops.html', 'audit.html', 'candidates.html'] as const;

function RunsSection({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  // Newest first — model.runs is append-ordered.
  const runs = [...model.runs].reverse();
  return (
    <div className="section">
      <h2>{t('system.runs')}</h2>
      {runs.length === 0 ? (
        <EmptyState>
          <p>{t('system.runsEmpty')}</p>
        </EmptyState>
      ) : (
        <table className="runs-table">
          <thead>
            <tr>
              <th>{t('system.runDate')}</th>
              <th className="num">{t('system.runOk')}</th>
              <th className="num">{t('system.runFailed')}</th>
              <th className="num">{t('system.runBlocked')}</th>
              <th className="num">{t('system.runIngested')}</th>
              <th>{t('system.runReport')}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td className="mono">{r.date}</td>
                <td className="num">{r.succeeded}</td>
                <td className={r.failed > 0 ? 'num warn' : 'num'}>
                  {r.failed}
                </td>
                <td className={r.blocked > 0 ? 'num warn' : 'num'}>
                  {r.blocked}
                </td>
                <td className="num">{r.ingested}</td>
                {/* Filename as text (design §3.6): the report is a JSON file
                    in the vault (.ovp/reports/), not an HTTP resource. */}
                <td className="mono tiny">{r.report_file}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AttentionSection({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const sources = attentionSources(model);
  return (
    <div className="section">
      <h2>{t('system.attentionTitle')}</h2>
      {sources.length === 0 ? (
        <EmptyState>
          <p>{t('system.attentionEmpty')}</p>
        </EmptyState>
      ) : (
        sources.map((s) => <AttentionCard source={s} key={s.sha256} />)
      )}
      <p className="tiny muted">
        {t('system.doctorHint')} <code className="mono">ovp2 doctor</code>
      </p>
    </div>
  );
}

function SurfacesSection() {
  const { t } = useI18n();
  // The generated admin pages only exist on vaults with a rendered legacy
  // console — probe them and hide dead links (codex review P2).
  // In dev/preview, Vite answers ANY missing path with the SPA shell and
  // HTTP 200, so the probe can't distinguish real pages (codex review P2)
  // — show the links unprobed there; the production server 404s missing
  // extension-paths correctly.
  const [adminPages, setAdminPages] = useState<string[]>(
    import.meta.env.PROD ? [] : [...ADMIN_PAGES],
  );
  useEffect(() => {
    if (!import.meta.env.PROD) return;
    let cancelled = false;
    Promise.all(
      ADMIN_PAGES.map((page) =>
        // GET, not HEAD: the server routes only GET to the static resolver
        // (HEAD answers 405), and these pages are small.
        fetch(`/${page}`, { method: 'GET', cache: 'no-store' })
          .then((r) => (r.ok ? page : null))
          .catch(() => null),
      ),
    ).then((pages) => {
      if (!cancelled) setAdminPages(pages.filter((p) => p !== null) as string[]);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="section">
      <h2>{t('system.surfaces')}</h2>
      <p className="sm muted">{t('system.surfacesNote')}</p>
      <ul className="legacy-links">
        <li>
          <Link to="/flow">{t('system.flowLink')} →</Link>
        </li>
        <li>
          <Link to="/monitor">{t('system.monitorLink')} →</Link>
        </li>
      </ul>
      {adminPages.length > 0 && (
        <>
          <p className="tiny muted" style={{ marginBottom: '0.25rem' }}>
            {t('system.adminPagesNote')}
          </p>
          <ul className="legacy-links">
            {adminPages.map((page) => (
              <li key={page}>
                <a className="mono" href={`/${page}`}>
                  {page} →
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ConceptsSection() {
  const { t } = useI18n();
  return (
    <div className="section">
      <h2>{t('system.concepts')}</h2>
      <div className="card">
        <p className="sm">{t('system.conceptLayers')}</p>
        <p className="sm">{t('system.conceptDurable')}</p>
        <p className="sm" style={{ marginBottom: 0 }}>
          {t('system.conceptGate')}
        </p>
      </div>
    </div>
  );
}

/** Publish card: one button running `.ovp/publish.toml`'s configured publish
 * (build site + optional git deploy) via POST /api/publish, with a 2s status
 * poll while it runs. Hidden entirely in static mode (a published site can't
 * publish) and shows a configure hint when publish.toml is absent. */
function PublishSection() {
  const { t } = useI18n();
  const [status, setStatus] = useState<PublishStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = () => {
      fetchPublishStatus()
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          if (s.running) timer = setTimeout(poll, 2000);
        })
        .catch(() => {
          // Silent: the card simply stays in its last state.
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (STATIC_MODE) return null;

  const onPublish = () => {
    setError(null);
    startPublish()
      .then(() => {
        setStatus((s) => (s ? { ...s, running: true } : s));
        // Re-arm the poll loop.
        const tick = () =>
          fetchPublishStatus()
            .then((s) => {
              setStatus(s);
              if (s.running) setTimeout(tick, 2000);
            })
            .catch(() => {});
        setTimeout(tick, 1500);
      })
      .catch((e: Error) => setError(e.message));
  };

  const last = status?.last as
    | {
        ok?: boolean;
        error?: string;
        file_count?: number;
        claims?: number;
        deployed_to?: string;
        pushed?: boolean;
        finished_at?: string;
      }
    | null
    | undefined;

  return (
    <div className="section">
      <h2>{t('system.publish')}</h2>
      {status && !status.configured ? (
        <p className="sm muted">{t('system.publishNotConfigured')}</p>
      ) : (
        <>
          <p className="sm muted">{t('system.publishHelp')}</p>
          <button
            type="button"
            className="publish-btn"
            disabled={!status || status.running}
            onClick={onPublish}
          >
            {status?.running ? t('system.publishRunning') : t('system.publishNow')}
          </button>
          {error && <p className="sm warn">{error}</p>}
          {last && (
            <p className="sm">
              {last.ok
                ? t('system.publishLastOk', {
                    files: last.file_count ?? 0,
                    claims: last.claims ?? 0,
                  }) +
                  (last.deployed_to
                    ? last.pushed
                      ? ` · ${t('system.publishPushed')}`
                      : ` · ${t('system.publishNoChange')}`
                    : '')
                : `${t('system.publishLastFailed')}: ${last.error ?? ''}`}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function SettingsSection() {
  const { t } = useI18n();
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="section">
      <h2>{t('system.settings')}</h2>
      <p className="sm muted">{t('system.settingsReadonly')}</p>
      {error && (
        <EmptyState>
          <p>{t('system.settingsError')}</p>
        </EmptyState>
      )}
      {!error && !settings && (
        <div className="portal-note">{t('common.loading')}</div>
      )}
      {settings && (
        <dl className="meta-rows">
          <dt>{t('system.vaultRoot')}</dt>
          <dd className="mono tiny">{settings.vault_root}</dd>
          <dt>{t('system.schema')}</dt>
          <dd className="mono tiny">
            {settings.schema_version ?? t('system.noIndex')}
          </dd>
          <dt>{t('system.indexDate')}</dt>
          <dd className="mono tiny">
            {settings.index_date ?? t('system.noIndex')}
          </dd>
          {/* P1: the projection's BUILD INSTANT + live age, so `index_date`
              (a day string) can no longer stand in for freshness. */}
          <dt>{t('system.builtAt')}</dt>
          <dd className="tiny">
            <AgeLabel builtAt={settings.built_at} />
          </dd>
          <dt>{t('system.runId')}</dt>
          <dd className="mono tiny">
            {settings.run_id ?? t('system.noIndex')}
          </dd>
          <dt>{t('system.counts')}</dt>
          <dd className="tiny">
            {settings.counts
              ? t('system.countsLine', {
                  sources: settings.counts.sources,
                  packs: settings.counts.packs,
                  claims: settings.counts.claims,
                })
              : t('system.noIndex')}
          </dd>
          {/* LIVE queued is authoritative-now (01-Raw walk); the projection's
              frozen end-of-run value is shown only when it differs. */}
          <dt>{t('system.queued')}</dt>
          <dd className="tiny">
            {settings.queued_at_build != null &&
            settings.queued_at_build !== settings.queued_live
              ? t('system.queuedLiveVsBuild', {
                  live: settings.queued_live,
                  build: settings.queued_at_build,
                  date: settings.index_date ?? '—',
                })
              : t('system.queuedLiveOnly', { live: settings.queued_live })}
          </dd>
          <dt>{t('system.llm')}</dt>
          <dd className="tiny">
            {settings.llm_configured
              ? t('system.llmOn')
              : t('system.llmOff')}
          </dd>
          <dt>{t('system.askTimeout')}</dt>
          <dd className="tiny">
            {t('system.askTimeoutValue', {
              secs: settings.ask_limits.timeout_secs,
              cap: settings.ask_limits.max_concurrent ?? '∞',
            })}
          </dd>
          <dt>{t('system.version')}</dt>
          <dd className="mono tiny">{settings.version}</dd>
        </dl>
      )}
      {/* The toggles themselves stay in the shell top bar (design §0.6:
          "B5 落进设置页" resolved as: settings documents them, the shell
          keeps them one click away on every page). */}
      <p className="tiny muted">{t('system.togglesNote')}</p>
    </div>
  );
}

export default function SystemPage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  // Only Runs/Attention need the index — Settings, concepts and the
  // pipeline links must render in exactly the missing-index state this
  // page helps diagnose (codex review P1). /api/settings returns null
  // index fields for that case on purpose.
  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('nav.system')}</h1>
      <PageHelp>{t('system.help')}</PageHelp>
      <ModelGate loading={loading} error={error}>
        {model && (
          <>
            {/* Live per-source activity feed (the portal's tail -f) sits at the
                TOP of System: it's what the operator opens the page for while a
                run is in flight. */}
            <RunActivitySection />
            <RunsSection model={model} />
            <AttentionSection model={model} />
          </>
        )}
      </ModelGate>
      <SurfacesSection />
      <ConceptsSection />
      <PublishSection />
      <SettingsSection />
    </>
  );
}
