import { useState } from 'react';
import { LogIn } from 'lucide-react';

import { useAuth } from '../auth/AuthContext';

export default function Login() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await signIn(email.trim(), password);
    } catch {
      setError('Incorrect email or password');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100vh' }}>
      <form onSubmit={submit} className="glass-panel" style={{ padding: 40, width: 360 }}>
        <h2 style={{ marginBottom: 4 }}>
          MediScan<span style={{ color: 'var(--primary-color)' }}>OCR</span>
        </h2>
        <div className="text-muted" style={{ marginBottom: 24 }}>Admin Console</div>

        <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Email</label>
        <input className="input-field" style={{ marginBottom: 16 }} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />

        <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Password</label>
        <input className="input-field" style={{ marginBottom: 20 }} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />

        {error && <div style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

        <button className="btn-primary" type="submit" disabled={loading} style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          <LogIn size={16} /> {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
