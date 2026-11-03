import { api } from './client';

/** One fixed licence plan (backend app/services/licensing.py). */
export interface Plan {
  label: string;
  users: number;
  workspaces: number;
}

/** The organisation, its plan, what it uses and what it may (GET /v1/org). */
export interface OrgSummary {
  id: string;
  name: string;
  plan: string;
  plan_label: string;
  seats_used: number;
  seats_allowed: number;
  workspaces_used: number;
  workspaces_allowed: number;
  valid_until: string | null;
  is_active: boolean;
  expired: boolean;
  over_limit: boolean;
  plans?: Record<string, Plan>;
  is_admin?: boolean;
  admin_email?: string | null;
  created_at?: string | null;
}

export type OrgRole = 'owner' | 'staff';

export interface OrgUser {
  id: string;
  email: string;
  role: OrgRole;
  is_active: boolean;
  workspace_ids: string[];
  current_workspace_id: string;
  created_at: string | null;
  is_self: boolean;
}

export interface Workspace {
  id: string;
  name: string;
  member_count: number;
}

export async function getOrg(): Promise<OrgSummary> {
  return (await api.get('/v1/org')).data as OrgSummary;
}

export async function listOrgUsers(): Promise<OrgUser[]> {
  return (await api.get('/v1/org/users')).data as OrgUser[];
}

export async function addOrgUser(body: { email: string; password: string; role: OrgRole; workspace_ids: string[] }): Promise<OrgUser> {
  return (await api.post('/v1/org/users', body)).data as OrgUser;
}

export async function changeOrgUser(id: string, body: { role?: OrgRole; workspace_ids?: string[]; is_active?: boolean }): Promise<OrgUser> {
  return (await api.patch(`/v1/org/users/${id}`, body)).data as OrgUser;
}

export async function setOrgUserPassword(id: string, newPassword: string): Promise<void> {
  await api.post(`/v1/org/users/${id}/password`, { new_password: newPassword });
}

export async function listWorkspaces(): Promise<Workspace[]> {
  return (await api.get('/v1/org/workspaces')).data as Workspace[];
}

export async function addWorkspace(name: string): Promise<Workspace> {
  return (await api.post('/v1/org/workspaces', { name })).data as Workspace;
}

export async function renameWorkspace(id: string, name: string): Promise<Workspace> {
  return (await api.patch(`/v1/org/workspaces/${id}`, { name })).data as Workspace;
}

// --- platform admin ---------------------------------------------------------------

export async function listOrgs(): Promise<OrgSummary[]> {
  return (await api.get('/v1/platform/orgs')).data as OrgSummary[];
}

export async function listPlans(): Promise<Record<string, Plan>> {
  return (await api.get('/v1/platform/orgs/plans')).data as Record<string, Plan>;
}

export async function createOrg(body: {
  name: string; plan: string; valid_until?: string | null; workspace_name?: string;
  admin_email: string; admin_password: string;
}): Promise<OrgSummary> {
  return (await api.post('/v1/platform/orgs', body)).data as OrgSummary;
}

export async function changeOrg(id: string, body: { plan?: string; valid_until?: string | null; clear_valid_until?: boolean; is_active?: boolean }): Promise<OrgSummary> {
  return (await api.patch(`/v1/platform/orgs/${id}`, body)).data as OrgSummary;
}

/** The error a refused request explains itself with, else a fallback. */
export function reason(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}
