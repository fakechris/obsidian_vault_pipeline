/** Global poller for the server-side source-work queue + notify fan-out. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  STATIC_MODE,
  cancelSourceWorkItem,
  deleteSourceWorkItem,
  enqueueSourceWork,
  fetchSourceWorkQueue,
  reorderSourceWorkQueue,
  type SourceWorkQueueItem,
  type SourceWorkWorkerInfo,
} from './api';
import { ensureNotifyPermission, notifyWorkItemDone } from './notify';

interface QueueCtx {
  items: SourceWorkQueueItem[];
  worker: SourceWorkWorkerInfo | null;
  activeCount: number;
  refresh: () => void;
  enqueue: (opts: {
    sha256: string;
    title?: string | null;
    translate?: boolean;
    summarize?: boolean;
    force?: boolean;
  }) => Promise<SourceWorkQueueItem>;
  reorder: (ids: string[]) => Promise<void>;
  cancel: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
}

const Ctx = createContext<QueueCtx | null>(null);

export function SourceWorkQueueProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<SourceWorkQueueItem[]>([]);
  const [worker, setWorker] = useState<SourceWorkWorkerInfo | null>(null);

  const refresh = useCallback(() => {
    if (STATIC_MODE) return;
    fetchSourceWorkQueue()
      .then((snap) => {
        setItems(snap.items);
        setWorker(snap.worker ?? null);
        for (const n of snap.notify) {
          notifyWorkItemDone(n);
        }
      })
      .catch(() => {
        /* offline */
      });
  }, []);

  useEffect(() => {
    if (STATIC_MODE) return;
    ensureNotifyPermission();
    refresh();
    const id = window.setInterval(refresh, 2500);
    return () => window.clearInterval(id);
  }, [refresh]);

  const enqueue = useCallback(
    async (opts: {
      sha256: string;
      title?: string | null;
      translate?: boolean;
      summarize?: boolean;
      force?: boolean;
    }) => {
      ensureNotifyPermission();
      const item = await enqueueSourceWork({ ...opts, notify: true });
      refresh();
      return item;
    },
    [refresh],
  );

  const reorder = useCallback(
    async (ids: string[]) => {
      const next = await reorderSourceWorkQueue(ids);
      setItems(next);
    },
    [],
  );

  const cancel = useCallback(
    async (id: string) => {
      await cancelSourceWorkItem(id);
      refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteSourceWorkItem(id);
      refresh();
    },
    [refresh],
  );

  const activeCount = useMemo(
    () =>
      items.filter((i) => i.status === 'queued' || i.status === 'running')
        .length,
    [items],
  );

  const value = useMemo(
    () => ({
      items,
      worker,
      activeCount,
      refresh,
      enqueue,
      reorder,
      cancel,
      remove,
    }),
    [items, worker, activeCount, refresh, enqueue, reorder, cancel, remove],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSourceWorkQueue(): QueueCtx {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error('useSourceWorkQueue must be used inside SourceWorkQueueProvider');
  }
  return ctx;
}

/** Safe for pages that may render in STATIC_MODE without the provider. */
export function useSourceWorkQueueOptional(): QueueCtx | null {
  return useContext(Ctx);
}
