import { api } from './client';
import type { ExtractionPayload, Fields } from '../lib/payload';

export interface DocumentDto {
  id: string;
  doc_type: 'prescription' | 'invoice';
  status: string;
  overall_confidence?: number | null;
  payload?: ExtractionPayload | null;
  error?: string | null;
  created_at: string;
}

export interface UploadFile {
  uri: string;
  name: string;
  type: string;
}

export async function uploadDocument(file: UploadFile, docType?: string): Promise<string> {
  const form = new FormData();
  // React Native FormData file shape.
  form.append('file', { uri: file.uri, name: file.name, type: file.type } as unknown as Blob);
  if (docType) form.append('doc_type', docType);
  const res = await api.post('/v1/documents/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data.document_id as string;
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
