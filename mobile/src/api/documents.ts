import { api } from './client';
import type { ExtractionPayload, Fields } from '../lib/payload';

export interface DocumentDto {
  id: string;
  doc_type: 'prescription' | 'invoice';
  status: string;
  overall_confidence?: number | null;
  payload?: ExtractionPayload | null;
  progress?: string | null;
  error?: string | null;
  created_at: string;
}

export interface UploadFile {
  uri: string;
  name: string;
  type: string;
}

export interface UploadResult {
  document_id: string;
  status: string;
  /** The same file was scanned before: document_id is that earlier scan. */
  duplicate?: boolean;
  /** Every document made - several when a PDF held several invoices. */
  document_ids?: string[];
  /** A short note for the user about their file, if any. */
  message?: string | null;
}

export async function uploadDocument(
  file: UploadFile,
  docType?: string,
  allowDuplicate = false,
): Promise<UploadResult> {
  const form = new FormData();
  // React Native FormData file shape.
  form.append('file', { uri: file.uri, name: file.name, type: file.type } as unknown as Blob);
  if (docType) form.append('doc_type', docType);
  if (allowDuplicate) form.append('allow_duplicate', 'true');
  const res = await api.post('/v1/documents/', form);
  return res.data as UploadResult;
}

export async function getDocument(id: string): Promise<DocumentDto> {
  const res = await api.get(`/v1/documents/${id}`);
  return res.data as DocumentDto;
}

export async function retryDocument(id: string): Promise<DocumentDto> {
  const res = await api.post(`/v1/documents/${id}/retry`);
  return res.data as DocumentDto;
}

export async function listDocuments(params?: {
  status?: string;
  doc_type?: string;
}): Promise<DocumentDto[]> {
  const res = await api.get('/v1/documents/', { params });
  return res.data as DocumentDto[];
}

export async function patchDocument(id: string, fields: Fields): Promise<DocumentDto> {
  const res = await api.patch(`/v1/documents/${id}`, { fields });
  return res.data as DocumentDto;
}

export async function approveDocument(id: string): Promise<DocumentDto> {
  const res = await api.post(`/v1/documents/${id}/approve`);
  return res.data as DocumentDto;
}

export interface PushResult extends DocumentDto {
  deliveries: { id: string; connector_id: string; status: string; response_body?: string }[];
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
