import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { getHealth } from './api';
import './App.css';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const Products = lazy(() => import('./pages/Products'));
const RunHistory = lazy(() => import('./pages/RunHistory'));
const Metrics = lazy(() => import('./pages/Metrics'));
const Inventory = lazy(() => import('./pages/Inventory'));
const ExcessStock = lazy(() => import('./pages/ExcessStock'));
const Procurement = lazy(() => import('./pages/Procurement'));
const Decisions = lazy(() => import('./pages/Decisions'));
const Explainability = lazy(() => import('./pages/Explainability'));

const NAVIGATION = [
  {
    label: 'Data & forecasting',
    links: [
      { to: '/', label: 'Overview', icon: 'OV', end: true },
      { to: '/products', label: 'Products & forecasts', icon: 'PR' },
      { to: '/runs', label: 'Forecast runs', icon: 'FR' },
      { to: '/metrics', label: 'Model evaluation', icon: 'ME' },
    ],
  },
  {
    label: 'Inventory decisions',
    links: [
      { to: '/inventory', label: 'Inventory optimization', icon: 'IO' },
      { to: '/excess-stock', label: 'Excess stock', icon: 'ES' },
      { to: '/procurement', label: 'Supplier review', icon: 'SR' },
      { to: '/decisions', label: 'Decision workflow', icon: 'DW' },
      { to: '/explainability', label: 'Explainability', icon: 'EX' },
    ],
  },
];

const PAGE_TITLES = {
  '/': 'Overview',
  '/products': 'Products & forecasts',
  '/runs': 'Forecast runs',
  '/metrics': 'Model evaluation',
  '/inventory': 'Inventory optimization',
  '/excess-stock': 'Excess stock',
  '/procurement': 'Supplier review',
  '/decisions': 'Decision workflow',
  '/explainability': 'Explainability',
};

function ApplicationShell() {
  const [apiStatus, setApiStatus] = useState('checking');
  const location = useLocation();

  const checkApi = useCallback(() => {
    getHealth()
      .then(() => setApiStatus('online'))
      .catch(() => setApiStatus('offline'));
  }, []);

  useEffect(() => {
    checkApi();
  }, [checkApi]);

  const title = PAGE_TITLES[location.pathname] || 'Inventory workspace';

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <NavLink className="brand" to="/" aria-label="Inventory intelligence home">
          <span className="brand-mark" aria-hidden="true">IO</span>
          <span className="brand-copy">
            <strong>Stockwise</strong>
            <small>Inventory intelligence</small>
          </span>
        </NavLink>

        <nav className="sidebar-nav" aria-label="Main navigation">
          {NAVIGATION.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-section-label">{group.label}</span>
              {group.links.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.end}
                  className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
                >
                  <span className="nav-icon" aria-hidden="true">{link.icon}</span>
                  <span>{link.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-note">
          <span className="sidebar-note-mark" aria-hidden="true">i</span>
          <p>Recommendations are advisory. Purchasing and stock changes remain under manager control.</p>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            <span className="eyebrow">Inventory optimization</span>
            <h1>{title}</h1>
          </div>
          <div className="topbar-status">
            <span className={`connection-indicator ${apiStatus}`} aria-hidden="true" />
            <span>{apiStatus === 'checking' ? 'Checking API' : apiStatus === 'online' ? 'API connected' : 'API unavailable'}</span>
            {apiStatus === 'offline' && (
              <button className="text-button" type="button" onClick={() => { setApiStatus('checking'); checkApi(); }}>Retry</button>
            )}
          </div>
        </header>

        <main className="main-content">
          <Suspense fallback={<div className="loading-center" role="status"><span className="spinner" />Loading page…</div>}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/products" element={<Products />} />
              <Route path="/runs" element={<RunHistory />} />
              <Route path="/metrics" element={<Metrics />} />
              <Route path="/inventory" element={<Inventory />} />
              <Route path="/excess-stock" element={<ExcessStock />} />
              <Route path="/procurement" element={<Procurement />} />
              <Route path="/decisions" element={<Decisions />} />
              <Route path="/explainability" element={<Explainability key={location.search} />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return <ApplicationShell />;
}
