import { api } from './client';

export interface InventoryCandidate {
  id: string;
  name: string;
  sku?: string | null;
  strength?: string | null;
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

export async function matchDocument(documentId: string): Promise<DocMatch> {
  const res = await api.get(`/v1/inventory/documents/${documentId}/match`);
  return res.data as DocMatch;
}

export async function alternatives(name: string): Promise<InventoryCandidate[]> {
  const res = await api.post('/v1/inventory/alternatives', { name });
  return (res.data.alternatives ?? []) as InventoryCandidate[];
}

export async function syncInventory(url: string, authHeader?: string): Promise<{ imported: number }> {
  const res = await api.post('/v1/inventory/sync', {
    url,
    auth_header: authHeader || undefined,
  });
  return res.data;
}
