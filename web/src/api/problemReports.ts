import { api } from './client';
import type { ExtractionPayload, SourcePage } from './documents';

export type ReportStatus = 'open' | 'in_progress' | 'closed';

export interface ProblemReport {
  id: string;
  document_id: string | null;
  shop_id: string | null;
  shop_name: string | null;
  reporter_email: string | null;
  reported_at: string;
  note: string | null;
  snapshot: Record<string, unknown>;
  status: ReportStatus;
  resolution_note: string | null;
  updated_at: string | null;
  closed_at: string | null;
  document?: {
    id: string; status: string; doc_type: string; source_available: boolean;
    source_manifest: { input_pages?: number; page_map?: number[] } | null;
    payload: ExtractionPayload | null;
  } | null;
  history?: { at: string; actor_id: string; from: string; to: string; resolution_note?: string }[];
  document_events?: { at: string; action: string; detail: Record<string, unknown> }[];
  saved_corrections?: { at: string; field: string; line: number | null; read: string | null; corrected: string | null }[];
  same_file_scans?: { id: string; created_at: string; status: string; replayed_fields: string[] }[];
}

export async function listProblemReports(status?: ReportStatus, offset = 0, q = '') {
  const res = await api.get('/v1/admin/reports', { params: { status, offset, limit: 50, q: q.trim() || undefined } });
  return res.data as { total: number; items: ProblemReport[] };
}

export async function getProblemReport(id: string) {
  const res = await api.get(`/v1/admin/reports/${id}`);
  return res.data as ProblemReport;
}

export async function updateProblemReport(id: string, status: ReportStatus, resolution_note: string | null) {
  const res = await api.patch(`/v1/admin/reports/${id}`, { status, resolution_note });
  return res.data as ProblemReport;
}

export async function getProblemReportSource(id: string, page: number, view: 'processed' | 'original' = 'processed') {
  const res = await api.get(`/v1/admin/reports/${id}/source`, { params: { page, view } });
  return res.data as SourcePage;
}
