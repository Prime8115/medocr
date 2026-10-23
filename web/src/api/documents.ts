import type { DocumentPart } from '../lib/parts';
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
  // One of several bills in one uploaded PDF (see lib/parts.ts).
  part_no?: number | null;
  part_count?: number | null;
  part_pages?: string | null;
  parts?: DocumentPart[];
  source_manifest?: { input_pages: number; processed_pages: number; page_map: number[];
    removed_pages: number[]; transformations: string[]; quality_warnings: string[];
    original_available: boolean } | null;
}

export interface SourcePage {
  image: string;
  page: number;
  pages: number;
  width: number;
  height: number;
  source_page?: number;
  source_pages?: number;
  view?: 'processed' | 'original';
  original_available?: boolean;
}

export async function getSourcePage(id: string, page: number, view: 'processed' | 'original' = 'processed'): Promise<SourcePage> {
  const res = await api.get(`/v1/documents/${id}/source`, { params: { page, view } });
  return res.data as SourcePage;
}

export interface TallyPreflight {
  ready: boolean;
  issues: { code: string; path: string; message: string }[];
  line_count: number;
  line_total: string;
  tax_total: string;
  invoice_total: string | null;
}

export async function getTallyPreflight(id: string, connectorId: string): Promise<TallyPreflight> {
  const res = await api.get(`/v1/documents/${id}/tally/preflight`, { params: { connector_id: connectorId } });
  return res.data as TallyPreflight;
}

export async function downloadTallyPurchase(id: string, connectorId: string): Promise<void> {
  const res = await api.get(`/v1/documents/${id}/tally/download`, {
    params: { connector_id: connectorId }, responseType: 'blob',
  });
  const url = URL.createObjectURL(res.data as Blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${id}_tally_purchase.xml`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface Delivery {
  id: string;
  document_id: string;
  connector_id: string;
  status: string;
  delivery_state?: string | null;
  external_reference?: string | null;
  connector_name?: string | null;
  connector_type?: string | null;
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

/** What the AI found when asked again about one field (services/recheck.py). */
export interface RecheckResult {
  path: string;
  current: string | null;
  found: string | null;
  printed_label: string | null;
  printed_text: string | null;
  /** Whether the value is printed on the bill; null where it has no text (a scan). */
  on_page: boolean | null;
  agrees: boolean | null;
  message: string;
}

/** Ask the AI again about one field. Changes nothing: a value is kept by saving it. */
export async function recheckField(id: string, path: string, page?: number): Promise<RecheckResult> {
  const res = await api.post(`/v1/documents/${id}/recheck`, { path, ...(page ? { page } : {}) });
  return res.data as RecheckResult;
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
  lessons_used_documents?: number;
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

/** A supplier column lesson learned from this shop's corrections (GET /v1/documents/suppliers/lessons). */
export interface SupplierLesson {
  id: string;
  supplier_gstin: string;
  heading: string;
  field: string;
  kind: 'is' | 'not';
  active: boolean;
  times_used: number;
  times_undone: number;
  times_failed: number;
}

export async function getSupplierLessons(): Promise<SupplierLesson[]> {
  const res = await api.get('/v1/documents/suppliers/lessons');
  return (res.data as { lessons: SupplierLesson[] }).lessons;
}

export async function forgetSupplierLesson(id: string): Promise<void> {
  await api.delete(`/v1/documents/suppliers/lessons/${id}`);
}
