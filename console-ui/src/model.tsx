/** Shared /api/model state — fetched once, consumed by the shell (status
 * dot) and every portal page. */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { fetchModel } from './lib/api';
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

export function ModelProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ModelState>({
    model: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;

    // Revalidate WITHOUT flashing the loading gate: the server model
    // auto-freshens on index.json's mtime, so a revalidation is cheap and a
    // left-open tab should converge to fresh on its own. A failed refetch keeps
    // the last good model rather than blanking the page.
    const load = (initial: boolean) => {
      fetchModel()
        .then((model) => {
          if (!cancelled) setState({ model, error: null, loading: false });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          // Only the FIRST load surfaces an error; later refetch failures leave
          // the last successful model in place (transient blip, not a reset).
          if (initial) setState({ model: null, error: String(err), loading: false });
        });
    };

    load(true);

    // Refetch on tab focus + a slow 60s poll — enough to converge, cheap
    // enough not to hammer (the server model is already mtime-cached).
    const onFocus = () => load(false);
    window.addEventListener('focus', onFocus);
    const timer = setInterval(() => load(false), 60_000);

    return () => {
      cancelled = true;
      window.removeEventListener('focus', onFocus);
      clearInterval(timer);
    };
  }, []);

  return <ModelContext.Provider value={state}>{children}</ModelContext.Provider>;
}

export function useModel(): ModelState {
  return useContext(ModelContext);
}
