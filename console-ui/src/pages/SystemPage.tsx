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
  fetchProviders,
  fetchPublishStatus,
  fetchRunNowStatus,
  fetchSettings,
  saveProviders,
  startPublish,
  startRunNow,
  type PublishStatus,
  type RunNowStatus,
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

/** Manual-run card: force today's pipeline job right now, under the server's
 * triple overlap protection (endpoint slot → heartbeat → scheduler dispatch
 * lock + vault RunLock in the child). A second click while anything runs is
 * a 409, surfaced as a plain message; a re-run after today's job already
 * completed asks for confirmation first. */
function RunNowSection() {
  const { t } = useI18n();
  const [status, setStatus] = useState<RunNowStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const busy = !!status && (status.running !== null || status.heartbeat_running);
  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = () => {
      fetchRunNowStatus()
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          if (s.running !== null || s.heartbeat_running) timer = setTimeout(poll, 3000);
        })
        .catch(() => {
          // Transient failure (server restart): keep polling while a run was
          // believed in flight, so the card recovers on its own.
          if (!cancelled && busy) timer = setTimeout(poll, 5000);
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [busy]);

  if (STATIC_MODE) return null;

  const ranToday = (() => {
    const lr = status?.jobs?.daily?.last_run;
    if (!lr) return false;
    const today = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const stamp = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
    return lr.startsWith(stamp) && status?.jobs?.daily?.last_status !== 'seeded';
  })();

  const onRun = () => {
    if (ranToday && !window.confirm(t('run.confirmAgain'))) return;
    setError(null);
    startRunNow('daily')
      .then(() => setStatus((s) => (s ? { ...s, running: 'daily' } : s)))
      .catch((e: Error) => setError(e.message));
  };

  const last = status?.last as
    | { ok?: boolean; job?: string; finished_at?: string; error?: string }
    | null
    | undefined;

  return (
    <div className="section">
      <h2>{t('run.title')}</h2>
      <p className="sm muted">{t('run.help')}</p>
      <button type="button" className="publish-btn" disabled={!status || busy} onClick={onRun}>
        {busy ? t('run.running') : ranToday ? t('run.runAgain') : t('run.runNow')}
      </button>
      {status?.jobs?.daily?.last_run && (
        <p className="sm muted">
          {t('run.lastRun', {
            when: status.jobs.daily.last_run,
            status: status.jobs.daily.last_status,
          })}
        </p>
      )}
      {error && <p className="sm warn">{error}</p>}
      {last && !busy && (
        <p className="sm">
          {last.ok ? t('run.lastOk') : `${t('run.lastFailed')}: ${last.error ?? ''}`}
        </p>
      )}
    </div>
  );
}

/** Built-in provider presets — all Anthropic-Messages-compatible endpoints
 * (the protocol our runtime speaks). base_url is the FULL messages endpoint.
 * OpenAI-compatible / Gemini native protocols need a new client and are not
 * offered yet. */
const PROVIDER_PRESETS: {
  id: string;
  label: string;
  base_url: string;
  model: string;
}[] = [
  { id: 'anthropic', label: 'Anthropic', base_url: '', model: 'claude-sonnet-4-6' },
  {
    id: 'kimi',
    label: 'Kimi (Moonshot)',
    base_url: 'https://api.moonshot.cn/anthropic/v1/messages',
    model: 'kimi-k2-0711-preview',
  },
  {
    id: 'glm',
    label: 'GLM (智谱)',
    base_url: 'https://open.bigmodel.cn/api/anthropic/v1/messages',
    model: 'glm-4.6',
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    base_url: 'https://api.deepseek.com/anthropic/v1/messages',
    model: 'deepseek-chat',
  },
  {
    id: 'minimax-cn',
    label: 'MiniMax 中国',
    base_url: 'https://api.minimaxi.com/anthropic/v1/messages',
    model: 'MiniMax-M2',
  },
  {
    id: 'minimax-global',
    label: 'MiniMax Global',
    base_url: 'https://api.minimax.io/anthropic/v1/messages',
    model: 'MiniMax-M2',
  },
  { id: 'custom', label: 'Custom (Anthropic-compatible)', base_url: '', model: '' },
];

/** LLM provider card — a GUI over `.ovp/providers.toml`. Reads the current
 * values back (secrets masked; a masked value round-tripped on save means
 * "unchanged"). Children (scheduled runs) pick changes up immediately; the
 * in-process ask needs an app/server restart. */
function ProviderSection() {
  const { t } = useI18n();
  const [preset, setPreset] = useState('custom');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [noProxy, setNoProxy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    fetchProviders()
      .then((p) => {
        if (cancelled) return;
        const env = p.env;
        const url = env.ANTHROPIC_BASE_URL ?? '';
        setBaseUrl(url);
        setModel(env.OVP_LLM_MODEL ?? '');
        setApiKey(env.ANTHROPIC_API_KEY ?? '');
        setNoProxy(env.OVP_LLM_NO_PROXY === '1' || env.OVP_LLM_NO_PROXY === 'true');
        const match = PROVIDER_PRESETS.find((pr) => pr.base_url === url && pr.id !== 'custom');
        setPreset(match?.id ?? (url ? 'custom' : 'anthropic'));
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, []);

  if (STATIC_MODE) return null;

  const onPreset = (id: string) => {
    setPreset(id);
    const p = PROVIDER_PRESETS.find((pr) => pr.id === id);
    if (p && p.id !== 'custom') {
      setBaseUrl(p.base_url);
      if (p.model) setModel(p.model);
    }
  };

  const onSave = () => {
    setError(null);
    setNote(null);
    saveProviders({
      ANTHROPIC_BASE_URL: baseUrl,
      OVP_LLM_MODEL: model,
      ANTHROPIC_API_KEY: apiKey,
      OVP_LLM_NO_PROXY: noProxy ? '1' : '',
    })
      .then(() => setNote(t('providers.saved')))
      .catch((e: Error) => setError(e.message));
  };

  return (
    <div className="section">
      <h2>{t('providers.title')}</h2>
      <p className="sm muted">{t('providers.help')}</p>
      <div className="provider-form">
        <label>
          {t('providers.preset')}
          <select value={preset} onChange={(e) => onPreset(e.target.value)}>
            {PROVIDER_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('providers.baseUrl')}
          <input
            type="text"
            value={baseUrl}
            placeholder={t('providers.baseUrlHint')}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </label>
        <label>
          {t('providers.model')}
          <input type="text" value={model} onChange={(e) => setModel(e.target.value)} />
        </label>
        <label>
          {t('providers.apiKey')}
          <input
            type="text"
            value={apiKey}
            placeholder={t('providers.apiKeyHint')}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </label>
        <label className="provider-check">
          <input
            type="checkbox"
            checked={noProxy}
            onChange={(e) => setNoProxy(e.target.checked)}
          />
          {t('providers.noProxy')}
        </label>
        <button type="button" className="publish-btn" disabled={!loaded} onClick={onSave}>
          {t('providers.save')}
        </button>
      </div>
      <p className="tiny muted">{t('providers.protocolNote')}</p>
      {note && <p className="sm">{note}</p>}
      {error && <p className="sm warn">{error}</p>}
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

  // ONE lifecycle-managed poll loop: fetch once on mount, and keep polling
  // while a run is in flight. Every timer lives inside this effect, so
  // navigating away cancels everything (no detached setTimeout chain —
  // review finding). `onPublish` never schedules its own timers; it just
  // flips `running`, which re-arms this effect.
  const running = status?.running ?? false;
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
  }, [running]);

  if (STATIC_MODE) return null;

  const onPublish = () => {
    setError(null);
    startPublish()
      .then(() => setStatus((s) => (s ? { ...s, running: true } : s)))
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
      <RunNowSection />
      <PublishSection />
      <ProviderSection />
      <SettingsSection />
    </>
  );
}
