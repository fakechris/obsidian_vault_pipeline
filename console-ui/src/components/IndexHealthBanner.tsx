/** Stage-4 repair surface: a thin warning strip shown ONLY when the sqlite
 * read-model failed to load (the model payload carries `sqlite_error`).
 * Today the server has already fallen back to the JSON projection, so data
 * stays correct — but once the fallback is retired this banner IS the
 * degradation story, so it ships with the switch, not after it. The button
 * triggers `POST /api/index/rebuild` (`ovp2 index` under the hood: full
 * rebuild + whole-model parity + atomic promote) and polls the status until
 * the run finishes; the banner clears itself when the next model poll loads
 * sqlite cleanly. */
import { useEffect, useState } from 'react';
import { useI18n } from '../i18n';
import { indexHealth } from '../lib/derive';
import { fetchIndexRebuildStatus, startIndexRebuild } from '../lib/api';
import { useModel } from '../model';

const POLL_MS = 2_000;

export default function IndexHealthBanner() {
  const { t } = useI18n();
  const { model } = useModel();
  const [rebuilding, setRebuilding] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);

  const serverRebuilding = model?.index_rebuild?.running ?? false;
  const health = indexHealth(model?.sqlite_error, rebuilding || serverRebuilding);

  useEffect(() => {
    if (!rebuilding) return;
    const id = window.setInterval(() => {
      fetchIndexRebuildStatus()
        .then((status) => {
          if (status.running) return;
          setRebuilding(false);
          if (status.last && status.last.ok === false) {
            setOutcome(
              t('indexHealth.rebuildFailed', {
                err: status.last.error ?? status.last.stderr_tail ?? `exit ${status.last.exit}`,
              }),
            );
          } else {
            setOutcome(t('indexHealth.rebuildDone'));
          }
        })
        .catch(() => {
          // Transient poll failure: keep polling; the interval survives.
        });
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [rebuilding, t]);

  if (health === 'ok') {
    // A finished rebuild's outcome stays visible until the error clears via
    // the next model poll (success) or the operator retries (failure).
    if (!outcome) return null;
    return (
      <div className="index-health ok" role="status">
        <span>{outcome}</span>
      </div>
    );
  }

  const start = () => {
    setOutcome(null);
    setRebuilding(true);
    startIndexRebuild().catch((e: unknown) => {
      setRebuilding(false);
      setOutcome(t('indexHealth.rebuildFailed', { err: e instanceof Error ? e.message : String(e) }));
    });
  };

  return (
    <div className="index-health warn" role="alert">
      {health === 'rebuilding' ? (
        <span>{t('indexHealth.rebuilding')}</span>
      ) : (
        <>
          <span>{t('indexHealth.error')}</span>
          <button type="button" onClick={start}>
            {t('indexHealth.rebuild')}
          </button>
        </>
      )}
    </div>
  );
}
