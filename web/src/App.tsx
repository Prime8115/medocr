import { BrowserRouter as Router, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { Settings as SettingsIcon, FileText, LogOut, Boxes, Truck, Flag, Building2, Users } from 'lucide-react';
import './index.css';

import ReviewQueue from './components/ReviewQueue';
import DocumentDetail from './components/DocumentDetail';
import Settings from './components/Settings';
import Inventory from './components/Inventory';
import Suppliers from './components/Suppliers';
import Login from './components/Login';
import ProblemReports from './components/ProblemReports';
import Organization from './components/Organization';
import PlatformOrgs from './components/PlatformOrgs';
import WorkspaceSwitcher from './components/WorkspaceSwitcher';
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
        {user && <WorkspaceSwitcher user={user} />}

        <NavLink to="/queue" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <FileText size={18} /> Review Queue
          </div>
        </NavLink>
        <NavLink to="/inventory" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Boxes size={18} /> Inventory
          </div>
        </NavLink>
        <NavLink to="/suppliers" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Truck size={18} /> Suppliers
          </div>
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <SettingsIcon size={18} /> Integrations
          </div>
        </NavLink>
        <NavLink to="/organization" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Users size={18} /> Organization
          </div>
        </NavLink>
        {user?.is_platform_admin && <NavLink to="/admin/orgs" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Building2 size={18} /> Organizations
          </div>
        </NavLink>}
        {user?.is_platform_admin && <NavLink to="/admin/reports" className={({ isActive }) => (isActive ? 'sidebar-link active' : 'sidebar-link')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Flag size={18} /> Customer reports
          </div>
        </NavLink>}

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
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/suppliers" element={<Suppliers />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/organization" element={<Organization />} />
          <Route path="/admin/orgs" element={user?.is_platform_admin ? <PlatformOrgs /> : <Navigate to="/queue" replace />} />
          <Route path="/admin/reports" element={user?.is_platform_admin ? <ProblemReports /> : <Navigate to="/queue" replace />} />
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
      <Router basename={import.meta.env.BASE_URL.replace(/\/$/, '')}>
        <Gate />
      </Router>
    </AuthProvider>
  );
}
