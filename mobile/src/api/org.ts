import { api } from './client';

export interface OrgSummary {
  id: string;
  name: string;
  plan_label: string;
  seats_used: number;
  seats_allowed: number;
  workspaces_used: number;
  workspaces_allowed: number;
  expired: boolean;
  is_active: boolean;
}

export interface Workspace { id: string; name: string; member_count: number }
export interface OrgUser {
  id: string;
  email: string;
  role: 'owner' | 'staff';
  is_active: boolean;
  workspace_ids: string[];
  is_self: boolean;
}

export async function getOrg(): Promise<OrgSummary> {
  return (await api.get('/v1/org')).data as OrgSummary;
}
export async function listWorkspaces(): Promise<Workspace[]> {
  return (await api.get('/v1/org/workspaces')).data as Workspace[];
}
export async function addWorkspace(name: string): Promise<void> {
  await api.post('/v1/org/workspaces', { name });
}
export async function listUsers(): Promise<OrgUser[]> {
  return (await api.get('/v1/org/users')).data as OrgUser[];
}
export async function addUser(email: string, password: string, workspaceIds: string[]): Promise<void> {
  await api.post('/v1/org/users', { email, password, role: 'staff', workspace_ids: workspaceIds });
}
export async function updateUser(id: string, change: { is_active?: boolean; workspace_ids?: string[] }): Promise<void> {
  await api.patch(`/v1/org/users/${id}`, change);
}

export function reason(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : 'Could not complete the action. Please try again.';
}
