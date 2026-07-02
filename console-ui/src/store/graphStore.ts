import { create } from 'zustand';
import { fetchGraph } from '../lib/api';
import type { GraphResponse } from '../lib/types';

export type ViewMode = 'overview' | 'focus';

interface GraphState {
  data: GraphResponse | null;
  loading: boolean;
  error: string | null;
  viewMode: ViewMode;
  /** Selected node id (amber ring + detail panel). */
  selection: string | null;
  themeFilter: string | null;

  loadOverview: (theme?: string | null) => Promise<void>;
  select: (id: string | null) => void;
  setThemeFilter: (theme: string | null) => void;
}

export const useGraphStore = create<GraphState>((set, get) => ({
  data: null,
  loading: false,
  error: null,
  viewMode: 'overview',
  selection: null,
  themeFilter: null,

  loadOverview: async (theme = get().themeFilter) => {
    set({ loading: true, error: null });
    try {
      const data = await fetchGraph({
        mode: 'overview',
        theme: theme ?? undefined,
      });
      set({ data, loading: false, viewMode: 'overview', selection: null });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  select: (id) => set({ selection: id }),

  setThemeFilter: (theme) => {
    set({ themeFilter: theme });
    void get().loadOverview(theme);
  },
}));
