/** System `/system` — answers US8 (design §3.6): is the pipeline healthy,
 * where is it stuck, and what does this product even do?
 *
 * Four tabs (`?tab=`), so settings are no longer buried under run logs:
 * Overview (one line per "is something wrong here?" + a jump), Runs (run
 * now, live activity, history), Attention (sources needing the operator,
 * grouped by cause and collapsed), Settings (saved LLM setups with a
 * switcher, automation, publish, system info, surfaces, concepts).
 * Internal vocabulary (pack/unit/ledger) is allowed HERE and only here
 * (design §6, BL-051 word layering). */
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import AttentionCard from '../components/AttentionCard';
import AutomationPanel from '../components/AutomationPanel';
import { RunActivitySection } from '../components/RunActivity';
import { AgeLabel, EmptyState, ModelGate, PageHelp } from '../components/ui';
import { useI18n } from '../i18n';
import {
  STATIC_MODE,
  activateLlmProfile,
  deleteLlmProfile,
  fetchProviders,
  fetchPublishStatus,
  fetchRunNowStatus,
  fetchSettings,
  saveLlmProfile,
  saveProviders,
  startPublish,
  startRunNow,
  type LlmProfile,
  type ProvidersPayload,
  type PublishStatus,
  type RunNowStatus,
} from '../lib/api';
import {
  SYSTEM_TABS,
  attentionGroups,
  attentionSources,
  llmExtraOf,
  llmProfileSet,
  llmSetupSummary,
  profileIdFor,
  systemTabOf,
  type LlmProviderKind,
  type SystemTab,
} from '../lib/derive';
import type { IndexModel, SettingsPayload } from '../lib/types';
import { useModel } from '../model';

/** Legacy generated console pages, served by exact filename (admin depth —
 * design §2 keeps them reachable until they are componentized). Plain <a>
 * links: they are server-rendered pages outside the SPA router. */
const ADMIN_PAGES = ['ops.html', 'audit.html', 'candidates.html'] as const;

/** Default rows in System → Runs. Full history stays on disk / in the model;
 * the table only expands on demand so the page does not grow without bound. */
const RUNS_DEFAULT_SHOWN = 30;

function RunsSection({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const [showAll, setShowAll] = useState(false);
  // Newest first — model.runs is append-ordered ascending by date/seq.
  const runs = useMemo(() => [...model.runs].reverse(), [model.runs]);
  const hidden = Math.max(0, runs.length - RUNS_DEFAULT_SHOWN);
  const shown = showAll || hidden === 0 ? runs : runs.slice(0, RUNS_DEFAULT_SHOWN);
  return (
    <div className="section">
      <h2>{t('system.runs')}</h2>
      {runs.length === 0 ? (
        <EmptyState>
          <p>{t('system.runsEmpty')}</p>
        </EmptyState>
      ) : (
        <>
          <p className="sm muted" style={{ marginBottom: '0.5rem' }}>
            {showAll || hidden === 0
              ? t('system.runsShowingAll', { n: runs.length })
              : t('system.runsShowingRecent', {
                  shown: shown.length,
                  total: runs.length,
                })}
          </p>
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
              {shown.map((r) => (
                <tr key={`${r.run_id}-${r.report_file}`}>
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
          {hidden > 0 && (
            <p style={{ marginTop: '0.5rem' }}>
              <button
                type="button"
                className="linkish"
                onClick={() => setShowAll((v) => !v)}
              >
                {showAll
                  ? t('system.runsCollapse', { n: RUNS_DEFAULT_SHOWN })
                  : t('system.runsExpand', { n: hidden })}
              </button>
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** Sources needing the operator, grouped by cause and collapsed: dozens of
 * near-identical cards (one provider outage blocks twenty sources) become a
 * handful of rows; open a group to act on its sources. */
function AttentionSection({ model }: { model: IndexModel }) {
  const { t } = useI18n();
  const groups = useMemo(() => attentionGroups(attentionSources(model)), [model]);
  return (
    <div className="section">
      <h2>{t('system.attentionTitle')}</h2>
      {groups.length === 0 ? (
        <EmptyState>
          <p>{t('system.attentionEmpty')}</p>
        </EmptyState>
      ) : (
        groups.map((g) => (
          <details className="attention-group" key={g.key}>
            <summary>
              <strong>{t(`system.attentionGroup.${g.key}`)}</strong>
              <span className="muted"> · {g.sources.length}</span>
              <span className="sm muted"> — {t(`system.attentionGroupHint.${g.key}`)}</span>
            </summary>
            {g.sources.map((s) => (
              <AttentionCard source={s} key={`${s.sha256}-${s.status}`} />
            ))}
          </details>
        ))
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
        <p className="sm muted" title={t('run.lastRunHint')}>
          {t('run.lastRun', {
            when: status.jobs.daily.last_run.replace('T', ' ').slice(0, 16),
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

/** Built-in provider presets. `anthropic` = Anthropic-Messages-compatible
 * endpoints (base_url is the FULL /messages endpoint); `openai` = the OpenAI
 * Responses API (empty base_url = the official endpoint). */
/** Presets the legacy single-setup form (servers without saved setups) can
 * store — it writes Anthropic-compatible variables only. */
const LEGACY_PRESETS = () => PROVIDER_PRESETS.filter((p) => p.provider === 'anthropic');

const PROVIDER_PRESETS: {
  id: string;
  label: string;
  provider: LlmProviderKind;
  base_url: string;
  model: string;
}[] = [
  { id: 'openai', label: 'OpenAI', provider: 'openai', base_url: '', model: 'gpt-6-luna' },
  { id: 'anthropic', label: 'Anthropic', provider: 'anthropic', base_url: '', model: 'claude-sonnet-4-6' },
  {
    id: 'kimi',
    label: 'Kimi (Moonshot)',
    provider: 'anthropic',
    base_url: 'https://api.moonshot.cn/anthropic/v1/messages',
    model: 'kimi-k2-0711-preview',
  },
  {
    id: 'glm',
    label: 'GLM (智谱)',
    provider: 'anthropic',
    base_url: 'https://open.bigmodel.cn/api/anthropic/v1/messages',
    model: 'glm-4.6',
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    provider: 'anthropic',
    base_url: 'https://api.deepseek.com/anthropic/v1/messages',
    model: 'deepseek-chat',
  },
  {
    id: 'minimax-cn',
    label: 'MiniMax 中国',
    provider: 'anthropic',
    base_url: 'https://api.minimaxi.com/anthropic/v1/messages',
    model: 'MiniMax-M2',
  },
  {
    id: 'minimax-global',
    label: 'MiniMax Global',
    provider: 'anthropic',
    base_url: 'https://api.minimax.io/anthropic/v1/messages',
    model: 'MiniMax-M2',
  },
  { id: 'custom', label: 'Custom (Anthropic-compatible)', provider: 'anthropic', base_url: '', model: '' },
];

interface ProfileDraft {
  /** null = a new profile (id derived from the label on save). */
  id: string | null;
  /** LLM variables without a field here, carried through verbatim. */
  extra: Record<string, string>;
  label: string;
  preset: string;
  provider: LlmProviderKind;
  baseUrl: string;
  model: string;
  apiKey: string;
  noProxy: boolean;
}

function draftFromEnv(id: string | null, label: string, env: Record<string, string>): ProfileDraft {
  const sum = llmSetupSummary(env);
  const baseUrl = sum.provider === 'openai' ? env.OPENAI_BASE_URL ?? '' : env.ANTHROPIC_BASE_URL ?? '';
  const preset =
    PROVIDER_PRESETS.find(
      (p) => p.provider === sum.provider && p.base_url === baseUrl && p.id !== 'custom',
    )?.id ?? 'custom';
  return {
    id,
    extra: llmExtraOf(env),
    label,
    preset,
    provider: sum.provider,
    baseUrl,
    model: sum.model,
    apiKey: (sum.provider === 'openai' ? env.OPENAI_API_KEY : env.ANTHROPIC_API_KEY) ?? '',
    noProxy: env.OVP_LLM_NO_PROXY === '1' || env.OVP_LLM_NO_PROXY === 'true',
  };
}

/** One saved setup's form. */
function ProfileEditor({
  draft,
  onChange,
  onSave,
  onCancel,
}: {
  draft: ProfileDraft;
  onChange: (d: ProfileDraft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const onPreset = (id: string) => {
    const p = PROVIDER_PRESETS.find((pr) => pr.id === id);
    if (!p) return;
    onChange({
      ...draft,
      preset: id,
      provider: p.provider,
      // Another provider's (masked) key is no credential for this one.
      apiKey: p.provider === draft.provider ? draft.apiKey : '',
      baseUrl: p.id === 'custom' ? draft.baseUrl : p.base_url,
      model: p.model || draft.model,
      label: draft.label || p.label,
    });
  };
  return (
    <div className="provider-form">
      <label>
        {t('providers.label')}
        <input type="text" value={draft.label} onChange={(e) => onChange({ ...draft, label: e.target.value })} />
      </label>
      <label>
        {t('providers.preset')}
        <select value={draft.preset} onChange={(e) => onPreset(e.target.value)}>
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
          value={draft.baseUrl}
          placeholder={draft.provider === 'openai' ? t('providers.baseUrlHintOpenai') : t('providers.baseUrlHint')}
          onChange={(e) => onChange({ ...draft, baseUrl: e.target.value })}
        />
      </label>
      <label>
        {t('providers.model')}
        <input type="text" value={draft.model} onChange={(e) => onChange({ ...draft, model: e.target.value })} />
      </label>
      <label>
        {t('providers.apiKey')}
        <input
          type="text"
          value={draft.apiKey}
          placeholder={t('providers.apiKeyHint')}
          onChange={(e) => onChange({ ...draft, apiKey: e.target.value })}
        />
      </label>
      <label className="provider-check">
        <input
          type="checkbox"
          checked={draft.noProxy}
          onChange={(e) => onChange({ ...draft, noProxy: e.target.checked })}
        />
        {t('providers.noProxy')}
      </label>
      <div className="provider-actions">
        <button type="button" className="publish-btn" onClick={onSave}>
          {t('providers.save')}
        </button>
        <button type="button" className="linkish" onClick={onCancel}>
          {t('providers.cancel')}
        </button>
      </div>
    </div>
  );
}

/** LLM providers: saved setups (`[llm_profiles.*]` in .ovp/providers.toml)
 * with a one-click switch. The live setup is `[env]`; the active profile is
 * the one matching it (server-derived). Secrets arrive masked; a masked
 * value saved back means "unchanged". */
function ProviderProfilesSection({ onSaved }: { onSaved?: () => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<ProvidersPayload | null>(null);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    fetchProviders()
      .then((p) => !cancelled && setData(p))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [reload]);

  if (STATIC_MODE) return null;
  // A server without saved-profile support: the single-setup form.
  if (data && data.profiles === undefined) return <ProviderSection onSaved={onSaved} />;

  const profiles: LlmProfile[] = data?.profiles ?? [];
  const live = llmSetupSummary(data?.env ?? {});
  const done = (msg: string) => {
    setNote(msg);
    setDraft(null);
    setReload((n) => n + 1);
    onSaved?.();
  };
  const run = (p: Promise<void>, msg: string) => {
    setError(null);
    setNote(null);
    p.then(() => done(msg)).catch((e: Error) => setError(e.message));
  };
  const onSave = () => {
    if (!draft) return;
    const label = draft.label.trim() || PROVIDER_PRESETS.find((p) => p.id === draft.preset)?.label || 'LLM';
    const id = draft.id ?? profileIdFor(label, profiles.map((p) => p.id));
    run(saveLlmProfile(id, label, llmProfileSet(draft)), t('providers.profileSaved'));
  };

  return (
    <div className="section">
      <h2>{t('providers.title')}</h2>
      <p className="sm muted">{t('providers.profilesHelp')}</p>
      <p className="sm">
        {t('providers.liveNow')}{' '}
        <strong>{data?.active_profile ? profiles.find((p) => p.active)?.label : t('providers.unsaved')}</strong>
        <span className="muted mono tiny"> · {live.provider} · {live.model || '—'}</span>
      </p>
      {profiles.length === 0 && data && !draft && (
        <EmptyState>
          <p>{t('providers.noProfiles')}</p>
        </EmptyState>
      )}
      <ul className="profile-list">
        {profiles.map((p) => {
          const sum = llmSetupSummary(p.env);
          return (
            <li key={p.id} className={p.active ? 'profile active' : 'profile'}>
              <div>
                <strong>{p.label}</strong>
                {p.active && <span className="pill durable">{t('providers.active')}</span>}
                <div className="tiny muted mono">
                  {sum.provider} · {sum.model || '—'} · {sum.endpoint}
                </div>
              </div>
              <div className="profile-actions">
                {!p.active && (
                  <button
                    type="button"
                    className="publish-btn"
                    onClick={() => run(activateLlmProfile(p.id), t('providers.switched', { label: p.label }))}
                  >
                    {t('providers.use')}
                  </button>
                )}
                <button type="button" className="linkish" onClick={() => setDraft(draftFromEnv(p.id, p.label, p.env))}>
                  {t('providers.edit')}
                </button>
                <button
                  type="button"
                  className="linkish"
                  onClick={() => {
                    if (window.confirm(t('providers.deleteConfirm', { label: p.label }))) {
                      run(deleteLlmProfile(p.id), t('providers.deleted'));
                    }
                  }}
                >
                  {t('providers.delete')}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      {draft ? (
        <ProfileEditor draft={draft} onChange={setDraft} onSave={onSave} onCancel={() => setDraft(null)} />
      ) : (
        <p className="profile-add">
          {!data?.active_profile && data && Object.keys(data.env).length > 0 && (
            <button
              type="button"
              className="linkish"
              onClick={() => setDraft(draftFromEnv(null, t('providers.currentLabel'), data.env))}
            >
              {t('providers.saveCurrent')}
            </button>
          )}
          <button
            type="button"
            className="linkish"
            onClick={() => setDraft(draftFromEnv(null, '', { OVP_LLM_PROVIDER: 'openai', OVP_LLM_MODEL: 'gpt-6-luna' }))}
          >
            {t('providers.addProfile')}
          </button>
        </p>
      )}
      <p className="tiny muted">{t('providers.switchNote')}</p>
      {note && <p className="sm">{note}</p>}
      {error && <p className="sm warn">{error}</p>}
    </div>
  );
}

/** LLM provider card — a GUI over `.ovp/providers.toml`. Reads the current
 * values back (secrets masked; a masked value round-tripped on save means
 * "unchanged"). Ask re-reads the file on each question; scheduled runs load
 * it when their ovp2 child starts — no portal restart. */
function ProviderSection({ onSaved }: { onSaved?: () => void }) {
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
        const match = LEGACY_PRESETS().find((pr) => pr.base_url === url && pr.id !== 'custom');
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
    const p = LEGACY_PRESETS().find((pr) => pr.id === id);
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
      .then(() => {
        setNote(t('providers.saved'));
        onSaved?.();
      })
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
            {LEGACY_PRESETS().map((p) => (
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

function SettingsSection({ refreshKey = 0 }: { refreshKey?: number }) {
  const { t } = useI18n();
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then((s) => {
        if (!cancelled) {
          setSettings(s);
          setError(false);
        }
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

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
          {/* Stage-4 observation window: which store served this model —
              expect a steady 'sqlite'; 'json' here without a recent tag
              edit means the shadow is failing (see the repair banner). */}
          <dt>{t('system.servingBackend')}</dt>
          <dd className="mono tiny">{settings.serving_backend ?? '—'}</dd>
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
          <dd className="mono tiny">
            {typeof settings.version === 'string'
              ? settings.version
              : `v${settings.version.server} · ${settings.version.git} · ${settings.version.built}`}
          </dd>
          <dt>{t('system.uiBuild')}</dt>
          <dd className="mono tiny">{__OVP_UI_BUILD__}</dd>
        </dl>
      )}
      {/* The toggles themselves stay in the shell top bar (design §0.6:
          "B5 落进设置页" resolved as: settings documents them, the shell
          keeps them one click away on every page). */}
      <p className="tiny muted">{t('system.togglesNote')}</p>
    </div>
  );
}

/** One-glance status: each line answers "is something wrong here?" and
 * jumps to the tab that fixes it. */
function OverviewSection({ model, go }: { model: IndexModel; go: (t: SystemTab) => void }) {
  const { t } = useI18n();
  const [providers, setProviders] = useState<ProvidersPayload | null>(null);
  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    fetchProviders()
      .then((p) => !cancelled && setProviders(p))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  const groups = useMemo(() => attentionGroups(attentionSources(model)), [model]);
  const pending = groups.reduce((n, g) => n + g.sources.length, 0);
  const last = model.runs.length > 0 ? model.runs[model.runs.length - 1] : null;
  const live = llmSetupSummary(providers?.env ?? {});
  const activeLabel = providers?.profiles?.find((p) => p.active)?.label;
  return (
    <div className="section">
      <dl className="settings-list overview-list">
        <dt>{t('system.overviewLastRun')}</dt>
        <dd>
          {last ? (
            <>
              <span className="mono">{last.date}</span>{' '}
              {t('system.overviewRunLine', { ok: last.succeeded, failed: last.failed, blocked: last.blocked })}
            </>
          ) : (
            t('system.runsEmpty')
          )}{' '}
          <button type="button" className="linkish" onClick={() => go('runs')}>
            {t('system.tab.runs')} →
          </button>
        </dd>
        <dt>{t('system.overviewAttention')}</dt>
        <dd className={pending > 0 ? 'warn' : undefined}>
          {pending === 0
            ? t('system.attentionEmpty')
            : t('system.overviewAttentionLine', {
                n: pending,
                top: t(`system.attentionGroup.${groups[0].key}`),
                topN: groups[0].sources.length,
              })}{' '}
          {pending > 0 && (
            <button type="button" className="linkish" onClick={() => go('attention')}>
              {t('system.tab.attention')} →
            </button>
          )}
        </dd>
        {!STATIC_MODE && (
          <>
            <dt>{t('system.overviewLlm')}</dt>
            <dd>
              {activeLabel ?? t('providers.unsaved')}{' '}
              <span className="muted mono tiny">
                {live.provider} · {live.model || '—'}
              </span>{' '}
              <button type="button" className="linkish" onClick={() => go('settings')}>
                {t('system.overviewSwitch')} →
              </button>
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

export default function SystemPage() {
  const { t } = useI18n();
  const { model, error, loading } = useModel();
  const [params, setParams] = useSearchParams();
  const tab = systemTabOf(params.get('tab'));
  const go = (next: SystemTab) => {
    const p = new URLSearchParams(params);
    if (next === 'overview') p.delete('tab');
    else p.set('tab', next);
    setParams(p, { replace: true });
  };
  // Bump after an LLM switch so Settings re-reads llm_configured without a
  // full page reload (Ask already re-reads providers.toml per request).
  const [settingsRefresh, setSettingsRefresh] = useState(0);
  const pending = model ? attentionSources(model).length : 0;
  // Tabs that need the index sit behind the ModelGate; Settings must render
  // in exactly the missing-index state this page helps diagnose (codex P1).
  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('nav.system')}</h1>
      <PageHelp>{t('system.help')}</PageHelp>
      <div className="tab-row" role="tablist">
        {SYSTEM_TABS.map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            className={tab === k ? 'active' : ''}
            onClick={() => go(k)}
          >
            {t(`system.tab.${k}`)}
            {k === 'attention' && pending > 0 && <span className="muted"> ({pending})</span>}
          </button>
        ))}
      </div>
      {tab === 'settings' ? (
        <>
          <ProviderProfilesSection onSaved={() => setSettingsRefresh((n) => n + 1)} />
          <AutomationPanel />
          <PublishSection />
          <SettingsSection refreshKey={settingsRefresh} />
          <SurfacesSection />
          <ConceptsSection />
        </>
      ) : (
        <>
          {/* Run now + live activity must work with NO index — starting a
              run is how a missing index gets rebuilt (codex P2). */}
          {tab === 'runs' && (
            <>
              <RunNowSection />
              <RunActivitySection />
            </>
          )}
          <ModelGate loading={loading} error={error}>
            {model && tab === 'overview' && <OverviewSection model={model} go={go} />}
            {model && tab === 'runs' && <RunsSection model={model} />}
            {model && tab === 'attention' && <AttentionSection model={model} />}
          </ModelGate>
        </>
      )}
    </>
  );
}
