import {
  QueueItem,
  enqueue,
  nextPending,
  pendingCount,
  pruneDone,
  removeItem,
  updateItem,
} from '../src/lib/queue';

function item(id: string, over: Partial<QueueItem> = {}): QueueItem {
  return {
    id,
    uri: 'file://x',
    fileName: 'x.jpg',
    contentType: 'image/jpeg',
    status: 'queued',
    attempts: 0,
    createdAt: 1000,
    ...over,
  };
}

describe('offline queue logic', () => {
  test('enqueue appends', () => {
    const q = enqueue([], item('a'));
    expect(q).toHaveLength(1);
  });

  test('nextPending returns queued or retriable errors, skips done and exhausted', () => {
    const q = [
      item('a', { status: 'done' }),
      item('b', { status: 'error', attempts: 5 }), // exhausted
      item('c', { status: 'error', attempts: 1 }), // retriable
    ];
    expect(nextPending(q)?.id).toBe('c');
    expect(nextPending([item('a', { status: 'done' })])).toBeUndefined();
  });

  test('updateItem and removeItem', () => {
    let q = [item('a'), item('b')];
    q = updateItem(q, 'a', { status: 'done', documentId: 'doc_1' });
    expect(q[0].status).toBe('done');
    expect(q[0].documentId).toBe('doc_1');
    q = removeItem(q, 'b');
    expect(q).toHaveLength(1);
  });

  test('pendingCount excludes done', () => {
    const q = [item('a', { status: 'done' }), item('b'), item('c', { status: 'error' })];
    expect(pendingCount(q)).toBe(2);
  });

  test('pruneDone drops old done items only', () => {
    const now = 1000 + 2 * 24 * 60 * 60 * 1000;
    const q = [
      item('old', { status: 'done', createdAt: 1000 }),
      item('fresh', { status: 'done', createdAt: now - 1000 }),
      item('queued', { status: 'queued', createdAt: 1000 }),
    ];
    const pruned = pruneDone(q, now);
    const ids = pruned.map((i) => i.id);
    expect(ids).not.toContain('old');
    expect(ids).toContain('fresh');
    expect(ids).toContain('queued');
  });
});
