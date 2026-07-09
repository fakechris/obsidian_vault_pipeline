/** B1 placeholder routes — shell + title + one-line EmptyState naming the
 * phase each destination ships in (Search/Knowledge = B3, Ask = B4,
 * System = B5). Knowledge links to the pre-B1 graph as an interim view. */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui';
import { useI18n, type MsgKey } from '../i18n';

function Placeholder({
  titleKey,
  bodyKey,
  extra,
}: {
  titleKey: MsgKey;
  bodyKey: MsgKey;
  extra?: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t(titleKey)}</h1>
      <EmptyState>
        <p>{t(bodyKey)}</p>
        {extra}
      </EmptyState>
    </>
  );
}

export function SearchPage() {
  return <Placeholder titleKey="nav.search" bodyKey="placeholder.search" />;
}

export function KnowledgePage() {
  const { t } = useI18n();
  return (
    <Placeholder
      titleKey="nav.knowledge"
      bodyKey="placeholder.knowledge"
      extra={
        <p>
          <Link to="/graph">{t('placeholder.knowledgeInterim')} →</Link>
        </p>
      }
    />
  );
}

export function AskPage() {
  return <Placeholder titleKey="nav.ask" bodyKey="placeholder.ask" />;
}

export function SystemPage() {
  return <Placeholder titleKey="nav.system" bodyKey="placeholder.system" />;
}
