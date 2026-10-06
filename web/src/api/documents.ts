import { api } from './client';

export interface Leaf {
  value: string | null;
  confidence?: number | null;
  [k: string]: unknown;
}

export interface ExtractionPayload {
  schema_version?: string;
  doc_type: 'prescription' | 'invoice';
  fields: Record<string, unknown>;
  meta?: { overall_confidence?: number | null; warnings?: string[] };
}

export interface DocumentDto {
  id: string;
  doc_type: 'prescription' | 'invoice';
  status: string;
  overall_confidence?: number | null;
  payload?: ExtractionPayload | null;
  error?: string | null;
  created_at: string;
}

export interface Delivery {
  id: string;
  document_id: string;
  connector_id: string;
  status: string;
  response_code?: number | null;
  response_body?: string | null;
  attempts: number;
  created_at: string;
}

export async function listDocuments(params?: { status?: string; doc_type?: string }): Promise<DocumentDto[]> {
  const res = await api.get('/v1/documents/', { params });
  return res.data as DocumentDto[];
}

export async function getDocument(id: string): Promise<DocumentDto> {
  const res = await api.get(`/v1/documents/${id}`);
  return res.data as DocumentDto;
}

export async function patchDocument(id: string, fields: Record<string, unknown>): Promise<DocumentDto> {
  const res = await api.patch(`/v1/documents/${id}`, { fields });
  return res.data as DocumentDto;
}

/**
 * Approve, confirming the failed checks the reviewer has checked against the
 * paper. The server refuses (409, naming them) while any failed check is left out.
 */
export async function approveDocument(id: string, acknowledged: string[] = []): Promise<DocumentDto> {
  const res = await api.post(`/v1/documents/${id}/approve`, { acknowledged });
  return res.data as DocumentDto;
}

/** Answer one of the bill's ambiguous fields; `remember` keeps it for the supplier. */
export async function chooseOption(id: string, choice: string, option: number, remember = true): Promise<DocumentDto> {
  const res = await api.post(`/v1/documents/${id}/choose`, { choice, option, remember });
  return res.data as DocumentDto;
}

export interface PushResult extends DocumentDto {
  deliveries: Delivery[];
}

export async function pushDocument(id: string): Promise<PushResult> {
  const res = await api.post(`/v1/documents/${id}/push`);
  return res.data as PushResult;
}

export interface ReportAck {
  document_id: string;
  reported: boolean;
  message: string;
}

/** Tell us this extraction is wrong, with what the pipeline produced attached. */
export async function reportDocument(id: string, note?: string): Promise<ReportAck> {
  const res = await api.post(`/v1/documents/${id}/report`, { note: note || null });
  return res.data as ReportAck;
}

export interface ExtractionStats {
  documents: number;
  window_days: number;
  by_status: Record<string, number>;
  by_doc_type: Record<string, number>;
  by_pipeline: Record<string, number>;
  pages_failed_documents: number;
  reported_by_users: number;
  warnings: string[];
  invoices: {
    total: number;
    tier1_parser: number;
    tier1_parser_pct: number | null;
    reconciled: number;
    reconciled_pct: number | null;
    total_mismatch: number;
    total_unreadable: number;
    with_duplicates_removed: number;
    duplicate_rows_removed: number;
    multi_copy_pdfs: number;
  };
}

export async function getExtractionStats(days = 30): Promise<ExtractionStats> {
  const res = await api.get('/v1/documents/stats', { params: { days } });
  return res.data as ExtractionStats;
}

/** How well one supplier's bills are read (GET /v1/documents/suppliers). */
export interface SupplierCoverage {
  supplier_gstin: string | null;
  supplier_name: string;
  documents: number;
  verified_on_arrival: number;
  verified_on_arrival_pct: number | null;
  edited_documents: number;
  corrected_fields: { field: string; count: number }[];
  missed_fields: { field: string; count: number }[];
  failed_checks: { check: string; count: number }[];
  pipelines: Record<string, number>;
  gap_filled_documents: number;
  learned_used_documents: number;
  learned_labels: number;
  remembered_choices: number;
  last_seen: string | null;
  status: 'needs_attention' | 'fair' | 'new' | 'good';
}

export interface SupplierReport {
  window_days: number;
  suppliers: SupplierCoverage[];
}

export async function getSupplierReport(days = 90): Promise<SupplierReport> {
  const res = await api.get('/v1/documents/suppliers', { params: { days } });
  return res.data as SupplierReport;
}
