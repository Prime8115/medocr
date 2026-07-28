/**
 * Pure offline-capture queue logic (no React / RN imports — unit-testable).
 *
 * A queue item represents a captured document waiting to be uploaded. The React
 * layer (QueueContext) persists items to AsyncStorage and drives processing when
 * connectivity returns.
 */
export type QueueStatus = 'queued' | 'uploading' | 'done' | 'error';

export interface QueueItem {
  id: string;
  uri: string;
  fileName: string;
  contentType: string;
  docType?: 'prescription' | 'invoice';
  status: QueueStatus;
  documentId?: string; // set once uploaded
  error?: string;
  attempts: number;
  createdAt: number;
}

export function makeItem(
  input: Omit<QueueItem, 'status' | 'attempts' | 'createdAt' | 'id'> & { id: string; createdAt: number },
): QueueItem {
  return { status: 'queued', attempts: 0, ...input };
}

export function enqueue(items: QueueItem[], item: QueueItem): QueueItem[] {
  return [...items, item];
}

/** The next item eligible for upload (queued or a retriable error). */
export function nextPending(items: QueueItem[], maxAttempts = 5): QueueItem | undefined {
  return items.find(
    (i) => i.status === 'queued' || (i.status === 'error' && i.attempts < maxAttempts),
  );
}

export function updateItem(items: QueueItem[], id: string, patch: Partial<QueueItem>): QueueItem[] {
  return items.map((i) => (i.id === id ? { ...i, ...patch } : i));
}

export function removeItem(items: QueueItem[], id: string): QueueItem[] {
  return items.filter((i) => i.id !== id);
}

/** Count of items still needing work (not successfully uploaded). */
export function pendingCount(items: QueueItem[]): number {
  return items.filter((i) => i.status !== 'done').length;
}

/** Drop successfully-uploaded items older than `ttlMs`. */
export function pruneDone(items: QueueItem[], now: number, ttlMs = 24 * 60 * 60 * 1000): QueueItem[] {
  return items.filter((i) => i.status !== 'done' || now - i.createdAt < ttlMs);
}
