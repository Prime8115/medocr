import { BrowserRouter as Router, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { Settings as SettingsIcon, FileText, LogOut } from 'lucide-react';
import './index.css';

import ReviewQueue from './components/ReviewQueue';
import DocumentDetail from './components/DocumentDetail';
import Settings from './components/Settings';
import Login from './components/Login';
import { AuthProvider, useAuth } from './auth/AuthContext';

function Shell() {
  const { user, signOut } = useAuth();
  return (
    <div className="app-container">
      <aside className="sidebar glass-panel">
        <div style={{ padding: '0 16px', marginBottom: 24 }}>
          <h2 style={{ margin: 0, color: 'var(--primary-color)' }}>
            MediScan<span style={{ color: 'white' }}>OCR</span>
          </h2>
          <div className="text-muted" style={{ fontSize: 12 }}>Admin Console</div>
        </div>

        <NavLink to="/queue" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <FileText size={18} /> Review Queue
          </div>
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <SettingsIcon size={18} /> Integrations
          </div>
        </NavLink>

        <div style={{ marginTop: 'auto' }}>
          <div className="text-muted" style={{ padding: '0 16px', fontSize: 12, marginBottom: 8 }}>{user?.email}</div>
          <button className="sidebar-link" onClick={signOut} style={{ background: 'none', border: 'none', cursor: 'pointer', width: '100%', textAlign: 'left' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <LogOut size={18} /> Log out
            </div>
          </button>
        </div>
      </aside>

      <main className="main-content">
        <Routes>
          <Route path="/" element={<Navigate to="/queue" replace />} />
          <Route path="/queue" element={<ReviewQueue />} />
          <Route path="/documents/:id" element={<DocumentDetail />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/queue" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Gate() {
  const { user, loading } = useAuth();
  if (loading) return <div className="text-muted" style={{ margin: 'auto' }}>Loading…</div>;
  return user ? <Shell /> : <Login />;
}

export default function App() {
  return (
    <AuthProvider>
      <Router basename={import.meta.env.BASE_URL}>
        <Gate />
      </Router>
    </AuthProvider>
  );
}
