/** The last placeholder route — System ships in B5 (Ask shipped in B4).
 * The System placeholder is ALSO the only navigation into the legacy
 * Flow / Monitor views until B5 rethemes them into System panels
 * (design §2: /viz standalone navigation is retired in B3). */
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui';
import { useI18n } from '../i18n';

export function SystemPage() {
  const { t } = useI18n();
  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('nav.system')}</h1>
      <EmptyState>
        <p>{t('placeholder.system')}</p>
        <ul className="legacy-links">
          <li>
            <Link to="/flow">{t('placeholder.systemFlow')} →</Link>
          </li>
          <li>
            <Link to="/monitor">{t('placeholder.systemMonitor')} →</Link>
          </li>
        </ul>
      </EmptyState>
    </>
  );
}
