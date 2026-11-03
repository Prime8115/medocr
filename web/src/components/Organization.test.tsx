import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import Organization from './Organization';
import WorkspaceSwitcher from './WorkspaceSwitcher';
import * as orgApi from '../api/org';
import * as authApi from '../api/auth';

vi.mock('../api/org', async (importOriginal) => {
  const real = await importOriginal<typeof import('../api/org')>();
  return { ...real, getOrg: vi.fn(), listOrgUsers: vi.fn(), listWorkspaces: vi.fn(), addOrgUser: vi.fn(),
    changeOrgUser: vi.fn(), addWorkspace: vi.fn(), renameWorkspace: vi.fn(), setOrgUserPassword: vi.fn() };
});
vi.mock('../api/auth', async (importOriginal) => {
  const real = await importOriginal<typeof import('../api/auth')>();
  return { ...real, switchWorkspace: vi.fn() };
});

beforeEach(() => vi.resetAllMocks());

const summary = {
  id: 'o1', name: 'Eastern Agencies', plan: 'growth', plan_label: 'Growth', seats_used: 2, seats_allowed: 5,
  workspaces_used: 1, workspaces_allowed: 3, valid_until: null, is_active: true, expired: false, over_limit: false,
};
const admin = { id: 'u1', email: 'admin@ea.com', role: 'owner' as const, is_active: true, workspace_ids: ['w1'],
  current_workspace_id: 'w1', created_at: null, is_self: true };
const member = { ...admin, id: 'u2', email: 'staff@ea.com', role: 'staff' as const, is_self: false };

describe('Organization', () => {
  test('an admin sees the licence and users, and adds a user', async () => {
    vi.mocked(orgApi.getOrg).mockResolvedValue({ ...summary, is_admin: true });
    vi.mocked(orgApi.listWorkspaces).mockResolvedValue([{ id: 'w1', name: 'Vile Parle', member_count: 2 }]);
    vi.mocked(orgApi.listOrgUsers).mockResolvedValue([admin, member]);
    vi.mocked(orgApi.addOrgUser).mockResolvedValue({ ...member, id: 'u3', email: 'new@ea.com' });
    render(<Organization />);
    await screen.findByText('staff@ea.com');
    expect(screen.getByText('Growth')).toBeTruthy();
    fireEvent.click(screen.getByText('Add user'));
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@ea.com' } });
    fireEvent.change(screen.getByLabelText('Temporary password'), { target: { value: 'password123' } });
    fireEvent.click(screen.getAllByLabelText('Vile Parle')[0]);
    fireEvent.click(screen.getByText('Create user'));
    await waitFor(() => expect(orgApi.addOrgUser).toHaveBeenCalledWith(
      { email: 'new@ea.com', password: 'password123', role: 'staff', workspace_ids: ['w1'] }));
  });

  test('adding is off when every seat is used', async () => {
    vi.mocked(orgApi.getOrg).mockResolvedValue({ ...summary, seats_used: 5, is_admin: true });
    vi.mocked(orgApi.listWorkspaces).mockResolvedValue([]);
    vi.mocked(orgApi.listOrgUsers).mockResolvedValue([admin]);
    render(<Organization />);
    const button = await screen.findByText('Add user');
    expect((button.closest('button') as HTMLButtonElement).disabled).toBe(true);
  });

  test('a member sees the summary, not the users', async () => {
    vi.mocked(orgApi.getOrg).mockResolvedValue({ ...summary, is_admin: false });
    vi.mocked(orgApi.listWorkspaces).mockResolvedValue([{ id: 'w1', name: 'Vile Parle', member_count: 2 }]);
    render(<Organization />);
    await screen.findByText(/Your workspaces: Vile Parle/);
    expect(orgApi.listOrgUsers).not.toHaveBeenCalled();
    expect(screen.queryByText('Add user')).toBeNull();
  });
});

describe('WorkspaceSwitcher', () => {
  test('switches to the workspace picked', async () => {
    vi.mocked(authApi.switchWorkspace).mockResolvedValue({} as authApi.User);
    const onSwitched = vi.fn();
    render(<WorkspaceSwitcher onSwitched={onSwitched} user={{
      id: 'u1', email: 'a@b.c', role: 'owner', shop_id: 'w1', is_platform_admin: false, org_name: 'EA',
      workspace_name: 'Vile Parle', workspaces: [{ id: 'w1', name: 'Vile Parle' }, { id: 'w2', name: 'Bhandup' }],
    }} />);
    fireEvent.change(screen.getByLabelText('Workspace'), { target: { value: 'w2' } });
    await waitFor(() => expect(authApi.switchWorkspace).toHaveBeenCalledWith('w2'));
    await waitFor(() => expect(onSwitched).toHaveBeenCalled());
  });
});
