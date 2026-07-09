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
    fetchModel()
      .then((model) => {
        if (!cancelled) setState({ model, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ model: null, error: String(err), loading: false });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <ModelContext.Provider value={state}>{children}</ModelContext.Provider>;
}

export function useModel(): ModelState {
  return useContext(ModelContext);
}
