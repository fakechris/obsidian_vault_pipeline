/** Small DS-conformant building blocks shared by the portal pages. */
import type { ReactNode } from 'react';
import { useI18n, type MsgKey } from '../i18n';
import type { SourceStatus } from '../lib/types';

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
