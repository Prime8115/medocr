import { useCallback, useEffect, useState } from 'react';
import { Building2, KeyRound, Plus, UserPlus } from 'lucide-react';

import {
  addOrgUser,
  addWorkspace,
  changeOrgUser,
  getOrg,
  listOrgUsers,
  listWorkspaces,
  reason,
  renameWorkspace,
  setOrgUserPassword,
  type OrgRole,
  type OrgSummary,
  type OrgUser,
  type Workspace,
} from '../api/org';

const ROLE_LABEL: Record<OrgRole, string> = { owner: 'Admin', staff: 'Member' };

function Usage({ used, allowed, label }: { used: number; allowed: number; label: string }) {
  const full = used >= allowed;
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: used > allowed ? 'var(--danger)' : undefined }}>
        {used} <span className="text-muted" style={{ fontSize: 14 }}>/ {allowed}</span>
      </div>
      {full && <div className="text-muted" style={{ fontSize: 12 }}>{used > allowed ? 'Over the plan limit' : 'All in use'}</div>}
    </div>
  );
}

/**
 * The organisation: its licence, users and workspaces
 * (docs/ORG_USER_MANAGEMENT_PRD.md). Admins manage; members see the summary.
 */
export default function Organization() {
  const [org, setOrg] = useState<OrgSummary | null>(null);
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ email: '', password: '', role: 'staff' as OrgRole, workspace_ids: [] as string[] });
  const [newSpace, setNewSpace] = useState('');
  const [passwordFor, setPasswordFor] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState('');

  const load = useCallback(async () => {
    const summary = await getOrg();
    setOrg(summary);
    setSpaces(await listWorkspaces());
    if (summary.is_admin) setUsers(await listOrgUsers());
  }, []);

  useEffect(() => {
    load().catch((e) => setMessage({ text: reason(e, 'Could not load the organisation.'), ok: false }));
  }, [load]);

  async function run(action: () => Promise<unknown>, done: string) {
    setMessage(null);
    try {
      await action();
      setMessage({ text: done, ok: true });
      await load();
      return true;
    } catch (e) {
      setMessage({ text: reason(e, 'That did not work. Please try again.'), ok: false });
      return false;
    }
  }

  if (!org) {
    return <div className="text-muted">{message?.text ?? 'Loading…'}</div>;
  }
  const toggle = (ids: string[], id: string) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);

  return (
    <div>
      <h1 style={{ display: 'flex', alignItems: 'center', gap: 10 }}><Building2 size={24} /> {org.name}</h1>

      <div className="glass-card" style={{ display: 'flex', gap: 40, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div className="text-muted" style={{ fontSize: 12 }}>Plan</div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>{org.plan_label}</div>
        </div>
        <Usage used={org.seats_used} allowed={org.seats_allowed} label="Users" />
        <Usage used={org.workspaces_used} allowed={org.workspaces_allowed} label="Workspaces" />
        <div>
          <div className="text-muted" style={{ fontSize: 12 }}>Valid until</div>
          <div style={{ fontSize: 16, color: org.expired ? 'var(--danger)' : undefined }}>
            {org.valid_until ?? 'No expiry'}{org.expired ? ' (expired)' : ''}
          </div>
        </div>
        {!org.is_active && <span className="badge badge-failed">Inactive</span>}
      </div>
      {(org.expired || org.over_limit) && (
        <div className="glass-card" style={{ borderColor: 'var(--danger)', marginBottom: 16, fontSize: 14 }}>
          {org.expired ? 'The licence has expired: no users or workspaces can be added. ' : ''}
          {org.over_limit ? 'The organisation is over its plan limit: nothing is removed, but no users or workspaces can be added until it is back within the limit. ' : ''}
          Contact MediScan to change the plan.
        </div>
      )}

      {message && (
        <div role="status" style={{ marginBottom: 12, color: message.ok ? 'var(--success, #4ade80)' : 'var(--danger)' }}>{message.text}</div>
      )}

      {org.is_admin && (
        <>
          <div className="glass-card" style={{ padding: 0, marginBottom: 16, overflowX: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 20px' }}>
              <h3 style={{ margin: 0 }}>Users</h3>
              <button className="btn-primary" onClick={() => setAdding((v) => !v)}
                disabled={org.seats_used >= org.seats_allowed || org.expired || !org.is_active}
                title={org.seats_used >= org.seats_allowed ? 'All seats are in use' : undefined}>
                <UserPlus size={16} /> Add user
              </button>
            </div>

            {adding && (
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (await run(() => addOrgUser(form), `${form.email} added.`)) {
                    setForm({ email: '', password: '', role: 'staff', workspace_ids: [] });
                    setAdding(false);
                  }
                }}
                style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, padding: '0 20px 16px' }}
              >
                <label>Email<input className="field-input" type="email" required value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
                <label>Temporary password<input className="field-input" type="password" required minLength={8} value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
                <label>Role<select className="field-input" value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value as OrgRole })}>
                  <option value="staff">Member</option><option value="owner">Admin</option>
                </select></label>
                <fieldset style={{ border: 'none', padding: 0 }}>
                  <legend className="text-muted" style={{ fontSize: 13 }}>
                    {form.role === 'owner' ? 'Admins open every workspace' : 'Workspaces'}
                  </legend>
                  {form.role === 'staff' && spaces.map((s) => (
                    <label key={s.id} style={{ display: 'block', fontSize: 14 }}>
                      <input type="checkbox" checked={form.workspace_ids.includes(s.id)}
                        onChange={() => setForm({ ...form, workspace_ids: toggle(form.workspace_ids, s.id) })} /> {s.name}
                    </label>
                  ))}
                </fieldset>
                <div><button className="btn-primary" type="submit">Create user</button></div>
              </form>
            )}

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <thead>
                <tr className="text-muted" style={{ textAlign: 'left', fontSize: 12, textTransform: 'uppercase' }}>
                  <th style={{ padding: '8px 20px' }}>User</th><th>Role</th><th>Workspaces</th><th>Status</th><th />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} style={{ borderTop: '1px solid var(--border-glass)', opacity: u.is_active ? 1 : 0.55 }}>
                    <td style={{ padding: '10px 20px' }}>{u.email}{u.is_self && <span className="text-muted"> (you)</span>}</td>
                    <td>
                      <select className="field-input" aria-label={`Role of ${u.email}`} value={u.role}
                        onChange={(e) => run(() => changeOrgUser(u.id, { role: e.target.value as OrgRole }), 'Role changed.')}>
                        <option value="staff">{ROLE_LABEL.staff}</option><option value="owner">{ROLE_LABEL.owner}</option>
                      </select>
                    </td>
                    <td>
                      {u.role === 'owner' ? <span className="text-muted">All workspaces</span> : spaces.map((s) => (
                        <label key={s.id} style={{ display: 'block', fontSize: 13 }}>
                          <input type="checkbox" checked={u.workspace_ids.includes(s.id)}
                            onChange={() => run(() => changeOrgUser(u.id, { workspace_ids: toggle(u.workspace_ids, s.id) }), 'Workspaces changed.')} /> {s.name}
                        </label>
                      ))}
                    </td>
                    <td>{u.is_active ? 'Active' : 'Deactivated'}</td>
                    <td style={{ whiteSpace: 'nowrap', paddingRight: 20 }}>
                      {!u.is_self && (
                        <button className="btn-secondary" style={{ marginRight: 8 }}
                          onClick={() => run(() => changeOrgUser(u.id, { is_active: !u.is_active }),
                            u.is_active ? `${u.email} deactivated.` : `${u.email} reactivated.`)}>
                          {u.is_active ? 'Deactivate' : 'Reactivate'}
                        </button>
                      )}
                      <button className="btn-secondary" title="Set a new password"
                        onClick={() => { setPasswordFor(passwordFor === u.id ? null : u.id); setNewPassword(''); }}>
                        <KeyRound size={14} />
                      </button>
                      {passwordFor === u.id && (
                        <form style={{ display: 'flex', gap: 6, marginTop: 6 }}
                          onSubmit={async (e) => {
                            e.preventDefault();
                            if (await run(() => setOrgUserPassword(u.id, newPassword), `New password set for ${u.email}.`)) setPasswordFor(null);
                          }}>
                          <input className="field-input" type="password" minLength={8} required placeholder="New password"
                            aria-label={`New password for ${u.email}`} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
                          <button className="btn-primary" type="submit">Set</button>
                        </form>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="glass-card">
            <h3 style={{ marginTop: 0 }}>Workspaces</h3>
            {spaces.map((s) => (
              <div key={s.id} style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
                <input className="field-input" aria-label={`Name of workspace ${s.name}`} defaultValue={s.name}
                  onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== s.name) run(() => renameWorkspace(s.id, v), 'Workspace renamed.'); }} />
                <span className="text-muted" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{s.member_count} member(s)</span>
              </div>
            ))}
            <form style={{ display: 'flex', gap: 10, marginTop: 12 }}
              onSubmit={async (e) => {
                e.preventDefault();
                if (newSpace.trim() && await run(() => addWorkspace(newSpace.trim()), `Workspace ${newSpace.trim()} added.`)) setNewSpace('');
              }}>
              <input className="field-input" placeholder="New workspace (shop, branch or GSTIN)" value={newSpace}
                onChange={(e) => setNewSpace(e.target.value)} disabled={org.workspaces_used >= org.workspaces_allowed} />
              <button className="btn-secondary" type="submit" disabled={org.workspaces_used >= org.workspaces_allowed}>
                <Plus size={16} /> Add
              </button>
            </form>
            {org.workspaces_used >= org.workspaces_allowed && (
              <div className="text-muted" style={{ fontSize: 12, marginTop: 6 }}>The {org.plan_label} plan allows {org.workspaces_allowed} workspace(s).</div>
            )}
          </div>
        </>
      )}
      {!org.is_admin && (
        <div className="text-muted" style={{ fontSize: 14 }}>
          Your workspaces: {spaces.map((s) => s.name).join(', ') || '—'}. Ask your organisation's admin to change access.
        </div>
      )}
    </div>
  );
}
