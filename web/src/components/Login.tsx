import { useState } from 'react';
import { LogIn, UserPlus } from 'lucide-react';

import { useAuth } from '../auth/AuthContext';

export default function Login() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [shopName, setShopName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === 'login') {
        await signIn(email.trim(), password);
      } else {
        await signUp(email.trim(), password, shopName.trim());
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : mode === 'login'
            ? 'Incorrect email or password'
            : 'Could not create account',
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100vh' }}>
      <form onSubmit={submit} className="glass-panel" style={{ padding: 40, width: 380 }}>
        <h2 style={{ marginBottom: 4 }}>
          MediScan<span style={{ color: 'var(--primary-color)' }}>OCR</span>
        </h2>
        <div className="text-muted" style={{ marginBottom: 24 }}>
          {mode === 'login' ? 'Admin Console — sign in' : 'Create your pharmacy account'}
        </div>

        {mode === 'register' && (
          <>
            <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Pharmacy name</label>
            <input className="input-field" style={{ marginBottom: 16 }} value={shopName} onChange={(e) => setShopName(e.target.value)} required />
          </>
        )}

        <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Email</label>
        <input className="input-field" style={{ marginBottom: 16 }} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />

        <label className="text-muted" style={{ display: 'block', marginBottom: 6 }}>Password</label>
        <input className="input-field" style={{ marginBottom: 20 }} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required />

        {error && <div style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

        <button className="btn-primary" type="submit" disabled={loading} style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {mode === 'login' ? <LogIn size={16} /> : <UserPlus size={16} />}
          {loading ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>

        <div style={{ textAlign: 'center', marginTop: 20 }} className="text-muted">
          {mode === 'login' ? 'New pharmacy? ' : 'Have an account? '}
          <span
            style={{ color: 'var(--primary-color)', cursor: 'pointer', fontWeight: 600 }}
            onClick={() => {
              setError(null);
              setMode(mode === 'login' ? 'register' : 'login');
            }}
          >
            {mode === 'login' ? 'Create one' : 'Sign in'}
          </span>
        </div>
      </form>
    </div>
  );
}
