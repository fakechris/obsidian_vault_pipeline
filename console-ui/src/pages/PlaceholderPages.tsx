/** Remaining placeholder routes — Ask ships in B4, System in B5.
 * The System placeholder is ALSO the only navigation into the legacy
 * Flow / Monitor views until B5 rethemes them into System panels
 * (design §2: /viz standalone navigation is retired in B3). */
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

export function AskPage() {
  return <Placeholder titleKey="nav.ask" bodyKey="placeholder.ask" />;
}

export function SystemPage() {
  const { t } = useI18n();
  return (
    <Placeholder
      titleKey="nav.system"
      bodyKey="placeholder.system"
      extra={
        <ul className="legacy-links">
          <li>
            <Link to="/flow">{t('placeholder.systemFlow')} →</Link>
          </li>
          <li>
            <Link to="/monitor">{t('placeholder.systemMonitor')} →</Link>
          </li>
        </ul>
      }
    />
  );
}
