// src/pages/Metrics.jsx
import { useEffect, useState } from 'react';
import Plot from 'react-plotly.js';
import { getMetrics } from '../api';

const CONFIG = { displayModeBar: false, responsive: true };
const LAYOUT_BASE = {
  paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
  font: { color: '#8ba3c1', family: 'Inter', size: 11 },
  margin: { t: 24, b: 50, l: 60, r: 20 },
  xaxis: { gridcolor: 'rgba(99,179,237,0.08)', linecolor: 'rgba(99,179,237,0.12)' },
  yaxis: { gridcolor: 'rgba(99,179,237,0.08)', linecolor: 'rgba(99,179,237,0.12)' },
  legend: { bgcolor: 'rgba(0,0,0,0)', font: { size: 11 } },
  barmode: 'group',
};

function MetricTable({ rows, title }) {
  if (!rows || rows.length === 0) return (
    <div className="card mb-20">
      <div className="card-title">{title}</div>
      <div className="empty-state" style={{ padding: 24 }}>
        <p>No metrics available. Run training first.</p>
      </div>
    </div>
  );

  const metricKeys = ['mae', 'rmse', 'wape', 'smape'];
  return (
    <div className="card mb-20">
      <div className="card-title">{title}</div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>MAE</th>
              <th>RMSE</th>
              <th>WAPE</th>
              <th>sMAPE</th>
              <th>Rows Evaluated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.model}>
                <td className="text" style={{ fontWeight: 600 }}>{r.model}</td>
                {metricKeys.map(k => (
                  <td key={k}>
                    {r[k] == null
                      ? <span style={{ color: 'var(--text-muted)' }}>N/A</span>
                      : <span style={{ color: k === 'wape' ? 'var(--accent-cyan)' : 'var(--text-primary)' }}>
                          {(r[k] * (k === 'wape' || k === 'smape' ? 100 : 1)).toFixed(3)}{k === 'wape' || k === 'smape' ? '%' : ''}
                        </span>
                    }
                  </td>
                ))}
                <td>{r.n?.toLocaleString() ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Metrics() {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMetrics().then(setMetrics).catch(console.error).finally(() => setLoading(false));
  }, []);

  const buildGroupedBar = (rows, title) => {
    if (!rows || rows.length === 0) return null;
    const models  = rows.map(r => r.model);
    const metrics = ['mae', 'rmse'];
    const colors  = ['#63b3ed', '#34d399'];
    const barTraces = metrics.map((m, i) => ({
      type: 'bar', name: m.toUpperCase(),
      x: models, y: rows.map(r => r[m] ?? 0),
      marker: { color: colors[i], opacity: 0.85 },
      hovertemplate: `%{x}<br>${m.toUpperCase()}: %{y:.4f}<extra></extra>`,
    }));
    return { traces: barTraces, layout: { ...LAYOUT_BASE, height: 260, title: { text: title, font: { size: 13, color: '#8ba3c1' } } } };
  };

  const buildWapeBar = (rows, title) => {
    if (!rows || rows.length === 0) return null;
    return {
      traces: [{
        type: 'bar', name: 'WAPE',
        x: rows.map(r => r.model),
        y: rows.map(r => r.wape != null ? (r.wape * 100).toFixed(2) : null),
        marker: { color: rows.map((_, i) => i === 0 ? '#38bdf8' : '#fb923c'), opacity: 0.85 },
        hovertemplate: '%{x}<br>WAPE: %{y}%<extra></extra>',
      }],
      layout: {
        ...LAYOUT_BASE, height: 220,
        yaxis: { ...LAYOUT_BASE.yaxis, title: 'WAPE (%)' },
        title: { text: title, font: { size: 13, color: '#8ba3c1' } },
      },
    };
  };

  const valChart  = metrics?.val  ? buildGroupedBar(metrics.val,  'Validation: MAE & RMSE') : null;
  const testChart = metrics?.test ? buildGroupedBar(metrics.test, 'Test: MAE & RMSE')       : null;
  const wapeV = metrics?.val  ? buildWapeBar(metrics.val,  'Validation WAPE') : null;
  const wapeT = metrics?.test ? buildWapeBar(metrics.test, 'Test WAPE')       : null;

  return (
    <div>
      <div className="page-header">
        <h1>Model Evaluation</h1>
        <p>
          LightGBM vs. 7-day Moving Average baseline · One-step-ahead evaluation on chronological splits.
          WAPE is undefined (N/A) for products with zero actual demand.
        </p>
      </div>

      <div className="page-body">
        {loading ? (
          <div className="loading-center"><div className="spinner" /></div>
        ) : !metrics ? (
          <div className="empty-state"><h3>No metrics</h3><p>Run training to generate evaluation metrics.</p></div>
        ) : (
          <>
            {/* Notes */}
            <div style={{ background: 'rgba(99,179,237,0.06)', border: '1px solid var(--border)', borderRadius: 12, padding: '14px 18px', marginBottom: 24, fontSize: 12.5, color: 'var(--text-secondary)' }}>
              <strong>Evaluation notes:</strong> Test split covers 2026-07-16 → 2026-08-30 (134,778 rows, 3,044 products).
              2 products excluded from test eval (last sale before test period). 3 products have undefined per-product WAPE in validation (zero actual demand).
              MA7 baseline uses 7 consecutive calendar-day moving average (including zero-demand days).
            </div>

            <MetricTable rows={metrics.val}  title="Validation Split (Jun 1 – Jul 15 2026)" />
            <MetricTable rows={metrics.test} title="Test Split (Jul 16 – Aug 30 2026)" />

            {/* Charts */}
            {(valChart || testChart) && (
              <div className="chart-grid">
                {valChart  && <div className="card"><Plot data={valChart.traces}  layout={valChart.layout}  config={CONFIG} style={{ width: '100%' }} /></div>}
                {testChart && <div className="card"><Plot data={testChart.traces} layout={testChart.layout} config={CONFIG} style={{ width: '100%' }} /></div>}
              </div>
            )}

            {(wapeV || wapeT) && (
              <div className="chart-grid">
                {wapeV && <div className="card"><Plot data={wapeV.traces} layout={wapeV.layout} config={CONFIG} style={{ width: '100%' }} /></div>}
                {wapeT && <div className="card"><Plot data={wapeT.traces} layout={wapeT.layout} config={CONFIG} style={{ width: '100%' }} /></div>}
              </div>
            )}

            {/* Metric explanations */}
            <div className="card">
              <div className="card-title">Metric Definitions</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, fontSize: 13 }}>
                {[
                  ['MAE', 'Mean Absolute Error = mean(|actual − pred|). Units: demand units. Lower is better.'],
                  ['RMSE', 'Root Mean Squared Error. Penalises large errors more. Units: demand units.'],
                  ['WAPE', 'Weighted Absolute Percentage Error = Σ|actual−pred| / Σactual. Pooled across all products. Undefined (N/A) when Σactual = 0.'],
                  ['sMAPE', 'Symmetric MAPE = mean(2|actual−pred| / (|actual|+|pred|+ε)). Range 0–2. Epsilon prevents division by zero.'],
                ].map(([k, v]) => (
                  <div key={k}>
                    <div style={{ fontWeight: 600, color: 'var(--accent-cyan)', marginBottom: 4 }}>{k}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
