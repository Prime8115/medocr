import { api } from './client';

export type ConnectorType = 'webhook' | 'file_export' | 'desktop_agent';

export interface Connector {
  id: string;
  type: ConnectorType;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  has_secret: boolean;
  created_at: string;
}

export interface ConnectorCreate {
  type: ConnectorType;
  name: string;
  config?: Record<string, unknown>;
  secret?: string;
}

export interface ConnectorOptions {
  types: ConnectorType[];
  formats: string[];
  profiles: string[];
}

export async function getConnectorOptions(): Promise<ConnectorOptions> {
  const res = await api.get('/v1/connectors/options');
  return res.data as ConnectorOptions;
}

export async function listConnectors(): Promise<Connector[]> {
  const res = await api.get('/v1/connectors/');
  return res.data as Connector[];
}

export async function createConnector(body: ConnectorCreate): Promise<Connector> {
  const res = await api.post('/v1/connectors/', body);
  return res.data as Connector;
}

export async function deleteConnector(id: string): Promise<void> {
  await api.delete(`/v1/connectors/${id}`);
}

export interface TestResult {
  status: string;
  ok: boolean;
  response_code?: number | null;
  response_body?: string | null;
  attempts: number;
}

export async function testConnector(id: string): Promise<TestResult> {
  const res = await api.post(`/v1/connectors/${id}/test`);
  return res.data as TestResult;
}
