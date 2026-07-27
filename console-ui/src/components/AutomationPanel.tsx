/**
 * System automation explainer — n8n/mermaid-style node graph so operators
 * *see* what the timer runs, not a wall of prose.
 *
 * Data: `GET /api/schedule`. Daily stages are a fixed product DAG; pinboard /
 * web / github node state comes from argv features. Click a node for detail.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Archive,
  BookOpen,
  Bookmark,
  Database,
  FolderInput,
  GitBranch,
  Globe,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import { useI18n, type MsgKey } from '../i18n';
import {
  STATIC_MODE,
  fetchSchedule,
  setScheduleFeatures,
  type ScheduleJob,
  type SchedulePayload,
} from '../lib/api';

type StageState = 'on' | 'off' | 'always';

type StageId =
  | 'pinboard'
  | 'intake'
  | 'web'
  | 'github'
  | 'reader'
  | 'lifecycle'
  | 'index'
  | 'crystal'
  | 'custom';

interface GraphNode {
  id: StageId;
  state: StageState;
  /** Grid column / row for the canvas layout (1-based). */
  col: number;
  row: number;
  detail?: string;
  /** Optional toggle control rendered on the node. */
  toggle?: 'pinboard';
}

interface GraphEdge {
  from: StageId;
  to: StageId;
}

const STAGE_TITLE: Record<Exclude<StageId, 'custom'>, MsgKey> = {
  pinboard: 'auto.stage.pinboard',
  intake: 'auto.stage.intake',
  web: 'auto.stage.web',
  github: 'auto.stage.github',
  reader: 'auto.stage.reader',
  lifecycle: 'auto.stage.lifecycle',
  index: 'auto.stage.index',
  crystal: 'auto.stage.crystal',
};

const STAGE_BODY: Record<Exclude<StageId, 'custom' | 'reader'>, MsgKey> = {
  pinboard: 'auto.stage.pinboard.body',
  intake: 'auto.stage.intake.body',
  web: 'auto.stage.web.body',
  github: 'auto.stage.github.body',
  lifecycle: 'auto.stage.lifecycle.body',
  index: 'auto.stage.index.body',
  crystal: 'auto.stage.crystal.body',
};

const STAGE_ICON: Record<Exclude<StageId, 'custom'>, LucideIcon> = {
  pinboard: Bookmark,
  intake: FolderInput,
  web: Globe,
  github: GitBranch,
  reader: BookOpen,
  lifecycle: Archive,
  index: Database,
  crystal: Sparkles,
};

/**
 * Daily product DAG (columns left→right):
 *
 *   pinboard ──┐
 *              ├── intake ──┬── web ────┐
 *   (captures) ┘            └── github ─┼── reader ── lifecycle ── index
 */
function dailyGraph(job: ScheduleJob): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const f = job.features;
  const max = f.max_sources != null ? String(f.max_sources) : undefined;
  const nodes: GraphNode[] = [
    {
      id: 'pinboard',
      state: f.pinboard_live ? 'on' : 'off',
      col: 1,
      row: 1,
      toggle: 'pinboard',
    },
    { id: 'intake', state: 'always', col: 2, row: 1 },
    { id: 'web', state: f.web_fetch_live ? 'on' : 'off', col: 3, row: 1 },
    { id: 'github', state: f.github_live ? 'on' : 'off', col: 3, row: 2 },
    { id: 'reader', state: 'always', col: 4, row: 1, detail: max },
    { id: 'lifecycle', state: 'always', col: 5, row: 1 },
    { id: 'index', state: 'always', col: 6, row: 1 },
  ];
  const edges: GraphEdge[] = [
    { from: 'pinboard', to: 'intake' },
    { from: 'intake', to: 'web' },
    { from: 'intake', to: 'github' },
    { from: 'web', to: 'reader' },
    { from: 'github', to: 'reader' },
    { from: 'reader', to: 'lifecycle' },
    { from: 'lifecycle', to: 'index' },
  ];
  return { nodes, edges };
}

function crystallizeGraph(): { nodes: GraphNode[]; edges: GraphEdge[] } {
  return {
    nodes: [{ id: 'crystal', state: 'always', col: 1, row: 1 }],
    edges: [],
  };
}

function graphFor(job: ScheduleJob): { nodes: GraphNode[]; edges: GraphEdge[] } {
  if (job.id === 'daily' || job.argv[0] === 'daily') return dailyGraph(job);
  if (job.id === 'crystallize' || job.argv[0] === 'crystal-synth') {
    return crystallizeGraph();
  }
  return {
    nodes: [
      {
        id: 'custom',
        state: job.enabled ? 'on' : 'off',
        col: 1,
        row: 1,
      },
    ],
    edges: [],
  };
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso.replace('T', ' ');
}

function jobTitle(t: (k: MsgKey, v?: Record<string, string | number>) => string, job: ScheduleJob) {
  if (job.id === 'daily') return t('auto.job.daily');
  if (job.id === 'crystallize') return t('auto.job.crystallize');
  return t('auto.job.other', { id: job.id });
}

function statusLabel(
  t: (k: MsgKey, v?: Record<string, string | number>) => string,
  job: ScheduleJob,
) {
  if (!job.enabled) return t('auto.status.disabled');
  if (job.due) return t('auto.status.due');
  if (job.last_status === 'ok') return t('auto.status.ok');
  if (job.last_status === 'error') return t('auto.status.error');
  if (job.last_status === 'seeded') return t('auto.status.seeded');
  return job.last_status || t('auto.status.idle');
}

function nodeTitle(
  t: (k: MsgKey, v?: Record<string, string | number>) => string,
  id: StageId,
  job: ScheduleJob,
) {
  if (id === 'custom') return t('auto.stage.custom', { id: job.argv[0] || job.id });
  return t(STAGE_TITLE[id]);
}

function nodeBody(
  t: (k: MsgKey, v?: Record<string, string | number>) => string,
  node: GraphNode,
) {
  if (node.id === 'custom') return t('auto.stage.custom.body');
  if (node.id === 'reader') {
    return node.detail
      ? t('auto.stage.reader.bodyMax', { n: node.detail })
      : t('auto.stage.reader.body');
  }
  return t(STAGE_BODY[node.id]);
}

/** Pure layout numbers for the SVG edge layer (must match CSS grid cell size). */
const CELL_W = 148;
const CELL_H = 88;
const GAP_X = 28;
const GAP_Y = 20;
const PAD = 16;

function nodeCenter(node: GraphNode): { x: number; y: number } {
  const x = PAD + (node.col - 1) * (CELL_W + GAP_X) + CELL_W / 2;
  const y = PAD + (node.row - 1) * (CELL_H + GAP_Y) + CELL_H / 2;
  return { x, y };
}

function edgePath(
  from: GraphNode,
  to: GraphNode,
): string {
  const a = nodeCenter(from);
  const b = nodeCenter(to);
  const x1 = a.x + CELL_W / 2 - 4;
  const y1 = a.y;
  const x2 = b.x - CELL_W / 2 + 4;
  const y2 = b.y;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

function PipelineGraph({
  job,
  selectedId,
  onSelect,
  busy,
  onTogglePinboard,
}: {
  job: ScheduleJob;
  selectedId: StageId | null;
  onSelect: (id: StageId) => void;
  busy: boolean;
  onTogglePinboard?: (next: boolean) => void;
}) {
  const { t } = useI18n();
  const { nodes, edges } = useMemo(() => graphFor(job), [job]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const maxCol = Math.max(...nodes.map((n) => n.col), 1);
  const maxRow = Math.max(...nodes.map((n) => n.row), 1);
  const width = PAD * 2 + maxCol * CELL_W + (maxCol - 1) * GAP_X;
  const height = PAD * 2 + maxRow * CELL_H + (maxRow - 1) * GAP_Y;

  return (
    <div className="auto-canvas-wrap">
      <div
        className="auto-canvas"
        style={{ width, minHeight: height }}
        role="group"
        aria-label={t('auto.pipelineAria')}
      >
        <svg
          className="auto-edges"
          width={width}
          height={height}
          aria-hidden
        >
          <defs>
            <marker
              id="auto-arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="auto-arrow-fill" />
            </marker>
            <marker
              id="auto-arrow-off"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="auto-arrow-fill-off" />
            </marker>
          </defs>
          {edges.map((e) => {
            const from = byId.get(e.from);
            const to = byId.get(e.to);
            if (!from || !to) return null;
            const inactive = from.state === 'off' || to.state === 'off';
            return (
              <path
                key={`${e.from}-${e.to}`}
                d={edgePath(from, to)}
                className={inactive ? 'auto-edge auto-edge--off' : 'auto-edge'}
                markerEnd={inactive ? 'url(#auto-arrow-off)' : 'url(#auto-arrow)'}
              />
            );
          })}
        </svg>

        {nodes.map((node) => {
          const Icon = node.id === 'custom' ? Sparkles : STAGE_ICON[node.id];
          const title = nodeTitle(t, node.id, job);
          const left = PAD + (node.col - 1) * (CELL_W + GAP_X);
          const top = PAD + (node.row - 1) * (CELL_H + GAP_Y);
          const selected = selectedId === node.id;
          return (
            <div
              key={node.id}
              className="auto-node-cell"
              style={{ left, top, width: CELL_W, height: CELL_H - 8 }}
            >
              <button
                type="button"
                className={[
                  'auto-node',
                  `auto-node--${node.state}`,
                  selected ? 'auto-node--selected' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onClick={() => onSelect(node.id)}
                aria-pressed={selected}
              >
                <span className="auto-node-icon" aria-hidden>
                  <Icon size={18} strokeWidth={2} />
                </span>
                <span className="auto-node-title">{title}</span>
                <span className="auto-node-flag">
                  {node.state === 'off'
                    ? t('auto.stageOff')
                    : node.state === 'on'
                      ? t('auto.stageOn')
                      : t('auto.stageAlways')}
                </span>
              </button>
              {node.toggle === 'pinboard' && onTogglePinboard && (
                <label className="auto-switch auto-node-switch">
                  <input
                    type="checkbox"
                    checked={node.state !== 'off'}
                    disabled={busy}
                    onChange={(e) => onTogglePinboard(e.target.checked)}
                    aria-label={t('auto.toggle.pinboard')}
                  />
                  <span className="auto-switch-ui" />
                </label>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DetailPanel({
  job,
  nodeId,
  actionError,
}: {
  job: ScheduleJob;
  nodeId: StageId | null;
  actionError?: string | null;
}) {
  const { t } = useI18n();
  const { nodes } = graphFor(job);
  const node = nodeId ? nodes.find((n) => n.id === nodeId) : null;

  if (!node) {
    return (
      <div className="auto-detail auto-detail--empty">
        <p className="sm muted" style={{ margin: 0 }}>
          {t('auto.detailHint')}
        </p>
      </div>
    );
  }

  return (
    <div className="auto-detail">
      <h4 className="auto-detail-title">{nodeTitle(t, node.id, job)}</h4>
      <p className="sm auto-detail-body">{nodeBody(t, node)}</p>
      {node.id === 'pinboard' && (
        <p className="tiny muted" style={{ marginTop: '0.5rem' }}>
          {t('auto.toggle.pinboardHint')}
        </p>
      )}
      {actionError && <p className="sm warn">{actionError}</p>}
    </div>
  );
}

function JobStrip({
  jobs,
  activeId,
  onSelect,
}: {
  jobs: ScheduleJob[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="auto-job-strip" role="tablist" aria-label={t('auto.jobsAria')}>
      {jobs.map((job) => {
        const active = job.id === activeId;
        return (
          <button
            key={job.id}
            type="button"
            role="tab"
            aria-selected={active}
            className={`auto-job-tab${active ? ' auto-job-tab--active' : ''}${
              job.enabled ? '' : ' auto-job-tab--off'
            }`}
            onClick={() => onSelect(job.id)}
          >
            <span className="auto-job-tab-title">{jobTitle(t, job)}</span>
            <span className="auto-job-tab-meta mono">
              {job.cadence}
              {' · '}
              {statusLabel(t, job)}
            </span>
            <span className="auto-job-tab-when tiny muted">
              {t('auto.nextRun')}:{' '}
              {job.enabled ? formatWhen(job.next_run) : t('auto.paused')}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default function AutomationPanel() {
  const { t } = useI18n();
  const [data, setData] = useState<SchedulePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<StageId | null>(null);

  const selectJob = (job: ScheduleJob | undefined) => {
    if (!job) {
      setActiveJobId(null);
      setSelectedNode(null);
      return;
    }
    setActiveJobId(job.id);
    setSelectedNode(graphFor(job).nodes[0]?.id ?? null);
  };

  const reload = () =>
    fetchSchedule()
      .then((s) => {
        setData(s);
        if (s.jobs.length && !s.jobs.some((j) => j.id === activeJobId)) {
          selectJob(s.jobs[0]);
        }
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    if (STATIC_MODE) return;
    let cancelled = false;
    fetchSchedule()
      .then((s) => {
        if (cancelled) return;
        setData(s);
        selectJob(s.jobs[0]);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const activeJob =
    data?.jobs.find((j) => j.id === activeJobId) ?? data?.jobs[0] ?? null;

  const onTogglePinboard = (next: boolean) => {
    setBusy(true);
    setActionError(null);
    setScheduleFeatures({ pinboard_live: next })
      .then(() => reload())
      .catch((e: Error) => setActionError(e.message))
      .finally(() => setBusy(false));
  };

  if (STATIC_MODE) return null;

  return (
    <div className="section">
      <h2>{t('auto.title')}</h2>
      <p className="sm muted">{t('auto.help')}</p>
      <p className="sm auto-clock">{t('auto.clock')}</p>

      {error && <p className="sm warn">{t('auto.error')}: {error}</p>}
      {!error && !data && <p className="sm muted">{t('auto.loading')}</p>}

      {data && !data.present && (
        <div className="card">
          <p className="sm" style={{ margin: 0 }}>
            {t('auto.missing', { path: data.registry_rel })}
          </p>
        </div>
      )}

      {data && data.present && data.jobs.length === 0 && (
        <div className="card">
          <p className="sm" style={{ margin: 0 }}>
            {t('auto.empty')}
          </p>
        </div>
      )}

      {data && data.jobs.length > 0 && activeJob && (
        <div className="auto-shell card">
          <JobStrip
            jobs={data.jobs}
            activeId={activeJob.id}
            onSelect={(id) => selectJob(data.jobs.find((j) => j.id === id))}
          />

          <div className="auto-graph-row">
            <PipelineGraph
              job={activeJob}
              selectedId={selectedNode}
              onSelect={setSelectedNode}
              busy={busy}
              onTogglePinboard={
                activeJob.id === 'daily' ? onTogglePinboard : undefined
              }
            />
            <DetailPanel
              job={activeJob}
              nodeId={selectedNode}
              actionError={
                activeJob.id === 'daily' ? actionError : null
              }
            />
          </div>

          <div className="auto-legend tiny muted">
            <span className="auto-legend-item">
              <i className="auto-legend-swatch auto-legend-swatch--always" />
              {t('auto.stageAlways')}
            </span>
            <span className="auto-legend-item">
              <i className="auto-legend-swatch auto-legend-swatch--on" />
              {t('auto.stageOn')}
            </span>
            <span className="auto-legend-item">
              <i className="auto-legend-swatch auto-legend-swatch--off" />
              {t('auto.stageOff')}
            </span>
          </div>
        </div>
      )}

      {data && (
        <p className="tiny muted" style={{ marginTop: '0.75rem' }}>
          {t('auto.configHint', { path: data.registry_rel })}{' '}
          <Link to="/flow">{t('auto.flowLink')}</Link>
        </p>
      )}
    </div>
  );
}
