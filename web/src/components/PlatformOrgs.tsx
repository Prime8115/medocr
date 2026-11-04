import { useCallback, useEffect, useState } from 'react';
import { Building2, Plus } from 'lucide-react';

import { changeOrg, createOrg, listOrgs, listPlans, reason, type OrgSummary, type Plan } from '../api/org';

/**
 * MediScan's platform admin: every organisation, its plan, usage and status
 * (docs/ORG_USER_MANAGEMENT_PRD.md, section 5.3).
 */
export default function PlatformOrgs() {
  const [orgs, setOrgs] = useState<OrgSummary[]>([]);
  const [plans, setPlans] = useState<Record<string, Plan>>({});
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: '', plan: 'solo', valid_until: '', workspace_name: '', admin_email: '', admin_password: '' });

  const load = useCallback(async () => {
    setOrgs(await listOrgs());
    setPlans(await listPlans());
  }, []);

  useEffect(() => {
    load().catch((e) => setMessage({ text: reason(e, 'Could not load organisations.'), ok: false }));
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

  return (
    <div>
      <h1 style={{ display: 'flex', alignItems: 'center', gap: 10 }}><Building2 size={24} /> Organizations</h1>
      {message && (
        <div role="status" style={{ marginBottom: 12, color: message.ok ? 'var(--success, #4ade80)' : 'var(--danger)' }}>{message.text}</div>
      )}

      <div className="glass-card" style={{ marginBottom: 16 }}>
        <button className="btn-primary" onClick={() => setCreating((v) => !v)}><Plus size={16} /> New organization</button>
        {creating && (
          <form style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginTop: 12 }}
            onSubmit={async (e) => {
              e.preventDefault();
              const body = { ...form, valid_until: form.valid_until || null, workspace_name: form.workspace_name || undefined };
              if (await run(() => createOrg(body), `${form.name} created.`)) {
                setCreating(false);
                setForm({ name: '', plan: 'solo', valid_until: '', workspace_name: '', admin_email: '', admin_password: '' });
              }
            }}>
            <label>Name<input className="field-input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Plan<select className="field-input" value={form.plan} onChange={(e) => setForm({ ...form, plan: e.target.value })}>
              {Object.entries(plans).map(([k, p]) => <option key={k} value={k}>{p.label} ({p.users} users, {p.workspaces} workspaces)</option>)}
            </select></label>
            <label>Valid until (optional)<input className="field-input" type="date" value={form.valid_until}
              onChange={(e) => setForm({ ...form, valid_until: e.target.value })} /></label>
            <label>First workspace (optional)<input className="field-input" placeholder="Defaults to the name" value={form.workspace_name}
              onChange={(e) => setForm({ ...form, workspace_name: e.target.value })} /></label>
            <label>Admin email<input className="field-input" type="email" required value={form.admin_email}
              onChange={(e) => setForm({ ...form, admin_email: e.target.value })} /></label>
            <label>Admin temporary password<input className="field-input" type="password" minLength={8} required value={form.admin_password}
              onChange={(e) => setForm({ ...form, admin_password: e.target.value })} /></label>
            <div><button className="btn-primary" type="submit">Create</button></div>
          </form>
        )}
      </div>

      <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
          <thead>
            <tr className="text-muted" style={{ textAlign: 'left', fontSize: 12, textTransform: 'uppercase' }}>
              <th style={{ padding: '8px 20px' }}>Organization</th><th>Plan</th><th>Users</th><th>Workspaces</th><th>Valid until</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {orgs.map((o) => (
              <tr key={o.id} style={{ borderTop: '1px solid var(--border-glass)' }}>
                <td style={{ padding: '10px 20px' }}>
                  {o.name}
                  {o.admin_email && <div className="text-muted" style={{ fontSize: 12 }}>{o.admin_email}</div>}
                </td>
                <td>
                  <select className="field-input" aria-label={`Plan of ${o.name}`} value={o.plan}
                    onChange={(e) => run(() => changeOrg(o.id, { plan: e.target.value }), `${o.name}: plan changed.`)}>
                    {Object.entries(plans).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                  </select>
                </td>
                <td style={{ color: o.seats_used > o.seats_allowed ? 'var(--danger)' : undefined }}>{o.seats_used} / {o.seats_allowed}</td>
                <td style={{ color: o.workspaces_used > o.workspaces_allowed ? 'var(--danger)' : undefined }}>{o.workspaces_used} / {o.workspaces_allowed}</td>
                <td>
                  <input className="field-input" type="date" aria-label={`Valid until for ${o.name}`} defaultValue={o.valid_until ?? ''}
                    onBlur={(e) => {
                      const v = e.target.value;
                      if (v !== (o.valid_until ?? '')) {
                        run(() => changeOrg(o.id, v ? { valid_until: v } : { clear_valid_until: true }), `${o.name}: validity changed.`);
                      }
                    }} />
                  {o.expired && <div style={{ color: 'var(--danger)', fontSize: 12 }}>Expired</div>}
                </td>
                <td>
                  <button className={o.is_active ? 'btn-secondary' : 'btn-primary'}
                    onClick={() => run(() => changeOrg(o.id, { is_active: !o.is_active }), `${o.name} ${o.is_active ? 'deactivated' : 'activated'}.`)}>
                    {o.is_active ? 'Active - deactivate' : 'Inactive - activate'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
