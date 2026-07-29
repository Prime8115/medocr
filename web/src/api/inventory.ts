import { api } from './client';

export interface InventoryItem {
  id: string;
  name: string;
  composition?: string | null;
  sku?: string | null;
  strength?: string | null;
  mrp?: number | null;
  stock_qty?: number | null;
  created_at: string;
}

export interface InventoryCandidate {
  id: string;
  name: string;
  composition?: string | null;
  mrp?: number | null;
  stock_qty?: number | null;
  score: number;
}

export interface MatchItem {
  name: string | null;
  best_score: number;
  candidates: InventoryCandidate[];
}

export interface DocMatch {
  connected: boolean;
  matched: number;
  total: number;
  items: MatchItem[];
}

export async function inventoryCount(): Promise<{ count: number; connected: boolean }> {
  const res = await api.get('/v1/inventory/count');
  return res.data;
}

export async function listInventory(q?: string): Promise<InventoryItem[]> {
  const res = await api.get('/v1/inventory/', { params: q ? { q } : undefined });
  return res.data as InventoryItem[];
}

export async function importCsv(file: File, replace = true): Promise<{ imported: number }> {
  const form = new FormData();
  form.append('file', file);
  const res = await api.post('/v1/inventory/import', form, {
    params: { replace },
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

export async function syncInventory(url: string, authHeader?: string, itemsPath?: string): Promise<{ imported: number }> {
  const res = await api.post('/v1/inventory/sync', {
    url,
    auth_header: authHeader || undefined,
    items_path: itemsPath || undefined,
  });
  return res.data;
}

export async function clearInventory(): Promise<void> {
  await api.delete('/v1/inventory/');
}

export async function matchDocument(id: string): Promise<DocMatch> {
  const res = await api.get(`/v1/inventory/documents/${id}/match`);
  return res.data as DocMatch;
}
