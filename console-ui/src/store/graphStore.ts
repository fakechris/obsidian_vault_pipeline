import { create } from 'zustand';
import { fetchClaim, fetchGraph, fetchSearchSubgraph } from '../lib/api';
import type { ClaimDetail, GraphNode, GraphResponse } from '../lib/types';

export type ViewMode = 'overview' | 'focus' | 'search';

export interface HoverState {
  node: GraphNode;
  /** Viewport (client) coordinates of the pointer. */
  x: number;
  y: number;
}

interface GraphState {
  data: GraphResponse | null;
  loading: boolean;
  error: string | null;
  viewMode: ViewMode;
  /** Focused node id in focus mode (drives the neighborhood subgraph). */
  focusId: string | null;
  /** Selected node id (amber ring + detail panel). */
  selection: string | null;
  hover: HoverState | null;
  themeFilter: string | null;
  /** Claim detail for the selected claim (right panel). */
  detail: ClaimDetail | null;
  detailLoading: boolean;
  /** Incremented on viewport transforms — overlays re-project on change. */
  transformTick: number;

  bumpTransform: () => void;
  /** Active search query in search mode. */
  searchQuery: string | null;

  loadOverview: (theme?: string | null) => Promise<void>;
  /** Fetch the 2-hop neighborhood and switch to focus mode. */
  loadFocus: (id: string) => Promise<void>;
  /** Fetch the hit-flagged subgraph and switch to search mode. */
  loadSearch: (q: string) => Promise<void>;
  backToOverview: () => Promise<void>;
  select: (id: string | null) => void;
  setHover: (h: HoverState | null) => void;
  setThemeFilter: (theme: string | null) => void;
}

/** Overview response cache so leaving focus mode is instant. */
let overviewCache: GraphResponse | null = null;
let overviewCacheTheme: string | null = null;

export const useGraphStore = create<GraphState>((set, get) => ({
  data: null,
  loading: false,
  error: null,
  viewMode: 'overview',
  focusId: null,
  selection: null,
  hover: null,
  themeFilter: null,
  detail: null,
  detailLoading: false,
  transformTick: 0,
  searchQuery: null,

  bumpTransform: () =>
    set((s: GraphState) => ({ transformTick: s.transformTick + 1 })),

  loadOverview: async (theme = get().themeFilter) => {
    set({ loading: true, error: null });
    try {
      const data = await fetchGraph({
        mode: 'overview',
        theme: theme ?? undefined,
      });
      overviewCache = data;
      overviewCacheTheme = theme ?? null;
      set({
        data,
        loading: false,
        viewMode: 'overview',
        focusId: null,
        selection: null,
        hover: null,
        detail: null,
        searchQuery: null,
      });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  loadSearch: async (q) => {
    set({ loading: true, error: null, hover: null });
    try {
      const data = await fetchSearchSubgraph(q);
      set({
        data,
        loading: false,
        viewMode: 'search',
        searchQuery: q,
        focusId: null,
        selection: null,
        detail: null,
      });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  loadFocus: async (id) => {
    set({ loading: true, error: null, hover: null });
    try {
      const data = await fetchGraph({ mode: 'neighborhood', focus: id, hops: 2 });
      set({
        data,
        loading: false,
        viewMode: 'focus',
        focusId: id,
        selection: id,
      });
      // The focused claim's provenance chain is the point of focus mode —
      // load it into the panel immediately.
      void loadDetailFor(id, set);
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  backToOverview: async () => {
    const { themeFilter, loadOverview } = get();
    if (overviewCache && overviewCacheTheme === (themeFilter ?? null)) {
      set({
        data: overviewCache,
        viewMode: 'overview',
        focusId: null,
        selection: null,
        hover: null,
        detail: null,
        searchQuery: null,
      });
      return;
    }
    await loadOverview();
  },

  select: (id) => {
    // Reset detailLoading here: an in-flight fetch for the PREVIOUS claim
    // is guarded by selection and will never clear it, so a unit/source
    // panel opened next would show "Loading" forever.
    set({ selection: id, detail: null, detailLoading: false });
    if (id) void loadDetailFor(id, set);
  },

  setHover: (h) => set({ hover: h }),

  setThemeFilter: (theme) => {
    set({ themeFilter: theme });
    void get().loadOverview(theme);
  },
}));

async function loadDetailFor(
  id: string,
  set: (partial: Partial<GraphState>) => void,
) {
  if (!id.startsWith('claim:')) return;
  set({ detailLoading: true });
  try {
    const detail = await fetchClaim(id.slice('claim:'.length));
    // Only apply if the selection hasn't moved on.
    if (useGraphStore.getState().selection === id) {
      set({ detail, detailLoading: false });
    }
  } catch {
    // Same guard on failure — a stale rejection must not clobber the
    // loading state of a newer selection's fetch.
    if (useGraphStore.getState().selection === id) {
      set({ detailLoading: false });
    }
  }
}
