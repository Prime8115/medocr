import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';

import { uploadDocument } from '../api/documents';
import {
  QueueItem,
  enqueue,
  nextPending,
  pendingCount,
  pruneDone,
  updateItem,
} from '../lib/queue';

const STORAGE_KEY = 'mediscan_queue_v1';

interface QueueState {
  items: QueueItem[];
  pending: number;
  add: (input: Omit<QueueItem, 'status' | 'attempts' | 'createdAt' | 'id'>) => Promise<QueueItem>;
  retryAll: () => void;
}

const QueueContext = createContext<QueueState | undefined>(undefined);

export function QueueProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<QueueItem[]>([]);
  const itemsRef = useRef<QueueItem[]>([]);
  const online = useRef(true);
  const draining = useRef(false);

  const persist = useCallback((next: QueueItem[]) => {
    itemsRef.current = next;
    setItems(next);
    AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next)).catch(() => {});
  }, []);

  // Load persisted queue on mount.
  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(STORAGE_KEY);
        if (raw) persist(pruneDone(JSON.parse(raw) as QueueItem[], Date.now()));
      } catch {
        /* ignore */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const drain = useCallback(async () => {
    if (draining.current || !online.current) return;
    draining.current = true;
    try {
      // Process one at a time to preserve order and avoid overload.
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const item = nextPending(itemsRef.current);
        if (!item || !online.current) break;
        persist(updateItem(itemsRef.current, item.id, { status: 'uploading' }));
        try {
          const documentId = await uploadDocument(
            { uri: item.uri, name: item.fileName, type: item.contentType },
            item.docType,
          );
          persist(updateItem(itemsRef.current, item.id, { status: 'done', documentId }));
        } catch (e) {
          persist(
            updateItem(itemsRef.current, item.id, {
              status: 'error',
              attempts: item.attempts + 1,
              error: e instanceof Error ? e.message : 'upload failed',
            }),
          );
        }
      }
    } finally {
      draining.current = false;
    }
  }, [persist]);

  // React to connectivity changes: drain when we come back online.
  useEffect(() => {
    const unsub = NetInfo.addEventListener((state) => {
      online.current = Boolean(state.isConnected);
      if (online.current) drain();
    });
    return () => unsub();
  }, [drain]);

  const add = useCallback<QueueState['add']>(
    async (input) => {
      const item: QueueItem = {
        id: `q_${Date.now()}_${Math.floor(Math.random() * 1e6)}`,
        status: 'queued',
        attempts: 0,
        createdAt: Date.now(),
        ...input,
      };
      persist(enqueue(itemsRef.current, item));
      drain();
      return item;
    },
    [persist, drain],
  );

  const retryAll = useCallback(() => {
    persist(
      itemsRef.current.map((i) => (i.status === 'error' ? { ...i, status: 'queued' as const } : i)),
    );
    drain();
  }, [persist, drain]);

  const value = useMemo<QueueState>(
    () => ({ items, pending: pendingCount(items), add, retryAll }),
    [items, add, retryAll],
  );

  return <QueueContext.Provider value={value}>{children}</QueueContext.Provider>;
}

export function useQueue(): QueueState {
  const ctx = useContext(QueueContext);
  if (!ctx) throw new Error('useQueue must be used within QueueProvider');
  return ctx;
}
