/** Small DS-conformant building blocks shared by the portal pages. */
import { useEffect, useState, type ReactNode } from 'react';
import { useI18n, type MsgKey } from '../i18n';
import { ageParts } from '../lib/derive';
import type { SourceStatus } from '../lib/types';

/** Muted "as of <built_at> · N min ago" freshness stamp, derived client-side
 * from the projection's build instant and a ticking clock. Every surface that
 * shows counts renders one, so a stale number can never read like a fresh one.
 * Absent/unparseable `built_at` (pre-P1 index) → "unknown age". Bilingual via
 * the age.* keys. The clock ticks on an interval (default 30s) so a left-open
 * tab's label converges without a reload. */
export function AgeLabel({
  builtAt,
  tickMs = 30_000,
}: {
  builtAt?: string | null;
  tickMs?: number;
}) {
  const { t } = useI18n();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), tickMs);
    return () => clearInterval(id);
  }, [tickMs]);

  const a = ageParts(builtAt, now);
  if (a.unknown) {
    return <span className="muted tiny age-label">{t('age.unknown')}</span>;
  }
  const rel =
    a.unit === 'now'
      ? t('age.now')
      : a.unit === 'minute'
        ? t('age.minutes', { n: a.value })
        : a.unit === 'hour'
          ? t('age.hours', { n: a.value })
          : t('age.days', { n: a.value });
  // The instant is a machine timestamp — show it verbatim (mono), the relative
  // phrase is the human-readable half.
  return (
    <span className="muted tiny age-label" title={a.builtAt ?? undefined}>
      {t('age.stamp', { instant: a.builtAt ?? '', rel })}
    </span>
  );
}

/** Semantic status pill (DS extension #2). */
export function StatusPill({ status }: { status: SourceStatus }) {
  const { t } = useI18n();
  // CSS class names use dashes; the i18n key mirrors the API value.
  const cls = status.replace('_', '-');
  return (
    <span className={`pill ${cls}`}>
      {t(`sourceStatus.${status}` as MsgKey)}
    </span>
  );
}

/** i18n key of the one-line concept tooltip for a claim status or entity
 * kind pill; null for kinds the vocabulary does not know (the server may
 * grow new citation kinds — those pills just render without a tooltip). */
export function conceptTipKey(kind: string): MsgKey | null {
  switch (kind) {
    case 'durable':
      return 'concept.durableTip';
    case 'caveated':
      return 'concept.caveatedTip';
    case 'claim':
      return 'concept.claimTip';
    case 'card':
      return 'concept.cardTip';
    case 'unit':
      return 'concept.unitTip';
    default:
      return null;
  }
}

/** durable/caveated claim pill. Carries the plain-language one-liner as a
 * tooltip by default (operator finding: the vocabulary needs explaining
 * where it appears); `title` overrides it. */
export function ClaimPill({
  status,
  title,
}: {
  status: 'durable' | 'caveated';
  title?: string;
}) {
  const { t } = useI18n();
  const tipKey = conceptTipKey(status);
  return (
    <span
      className={`pill ${status}`}
      title={title ?? (tipKey ? t(tipKey) : undefined)}
    >
      {status}
    </span>
  );
}

/** Collapsible "What is this page?" help block (DS extension #4). */
export function PageHelp({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <details className="page-help">
      <summary>{t('common.whatIsThisPage')}</summary>
      <p>{children}</p>
    </details>
  );
}

/** Empty state with guidance text — no blank panels (design §7). */
export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

/** Loading / error wrapper for pages that need the index model. */
export function ModelGate({
  loading,
  error,
  children,
}: {
  loading: boolean;
  error: string | null;
  children: ReactNode;
}) {
  const { t } = useI18n();
  if (loading) return <div className="portal-note">{t('common.loading')}</div>;
  if (error) return <div className="portal-note">{t('common.error')}</div>;
  return <>{children}</>;
}
