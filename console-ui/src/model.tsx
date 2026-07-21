/** Shared /api/model state — fetched once, consumed by the shell (status
 * dot) and every portal page. */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { fetchModel, STATIC_MODE } from './lib/api';
import type { IndexModel } from './lib/types';

interface ModelState {
  model: IndexModel | null;
  error: string | null;
  loading: boolean;
}

const ModelContext = createContext<ModelState>({
  model: null,
  error: null,
  loading: true,
});

/** Poll cadence. Idle vaults converge slowly (the server model is mtime-cached,
 * so a revalidation is cheap but rarely changes). A RUNNING heartbeat polls
 * fast so the banner's `18/90` fraction and the live queued count tick as the
 * run drains 01-Raw — the whole point of the live surfaces. */
const IDLE_POLL_MS = 60_000;
const RUNNING_POLL_MS = 12_000;

function pollIntervalFor(model: IndexModel | null): number {
  return model?.ops?.last_run?.status === 'running'
    ? RUNNING_POLL_MS
    : IDLE_POLL_MS;
}

export function ModelProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ModelState>({
    model: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    // Self-scheduling poll so the cadence can ADAPT to the run state: a running
    // heartbeat re-polls every 12s (banner fraction + live queued tick down),
    // an idle vault backs off to 60s. Each load schedules the next based on the
    // model it just fetched. Revalidate WITHOUT flashing the loading gate; a
    // failed refetch keeps the last good model rather than blanking the page.
    const schedule = (model: IndexModel | null) => {
      if (cancelled) return;
      // A published static site is a frozen snapshot — model.json never changes
      // mid-session, so skip polling entirely (one load, no timers/refetch).
      if (STATIC_MODE) return;
      // Clear any pending timer first so a focus-triggered load can't leave a
      // second timer running alongside the scheduled one.
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => load(false), pollIntervalFor(model));
    };

    const load = (initial: boolean) => {
      fetchModel()
        .then((model) => {
          if (cancelled) return;
          setState({ model, error: null, loading: false });
          schedule(model);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          // Only the FIRST load surfaces an error; later refetch failures leave
          // the last successful model in place (transient blip, not a reset).
          if (initial) setState({ model: null, error: String(err), loading: false });
          // Keep polling on the idle cadence so a recovered server reconnects.
          schedule(null);
        });
    };

    load(true);

    // Refetch on tab focus too — an operator flipping back should see fresh
    // state immediately, not wait out the interval. (Not on the static site.)
    const onFocus = () => load(false);
    if (!STATIC_MODE) window.addEventListener('focus', onFocus);
    // Programmatic revalidation for actions that change server state (e.g.
    // the banner's Retry): dispatch `ovp:model-refresh` on window.
    const onRefresh = () => load(false);
    if (!STATIC_MODE) window.addEventListener('ovp:model-refresh', onRefresh);

    return () => {
      cancelled = true;
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('ovp:model-refresh', onRefresh);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  return <ModelContext.Provider value={state}>{children}</ModelContext.Provider>;
}

export function useModel(): ModelState {
  return useContext(ModelContext);
}
