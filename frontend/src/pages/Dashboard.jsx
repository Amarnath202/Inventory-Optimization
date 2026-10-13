// src/pages/Dashboard.jsx
import { useEffect, useState } from 'react';
import Plot from 'react-plotly.js';
import { getOverview, getTopDemand, triggerForecast } from '../api';

const PLOTLY_LAYOUT = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  'transparent',
  font: { color: '#8ba3c1', family: 'Inter, sans-serif', size: 12 },
  margin: { t: 30, b: 50, l: 55, r: 20 },
  xaxis: { gridcolor: 'rgba(99,179,237,0.08)', linecolor: 'rgba(99,179,237,0.15)', tickfont: { size: 11 } },
  yaxis: { gridcolor: 'rgba(99,179,237,0.08)', linecolor: 'rgba(99,179,237,0.15)', tickfont: { size: 11 } },
  legend: { bgcolor: 'rgba(0,0,0,0)', font: { size: 11 } },
};

const CONFIG = { displayModeBar: false, responsive: true };

function StatCard({ label, value, sub, color = 'blue' }) {
  return (
    <div className={`stat-card ${color}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export default function Dashboard() {
  const [overview, setOverview] = useState(null);
  const [topData,  setTopData]  = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState('');

  useEffect(() => {
    Promise.all([getOverview(), getTopDemand(15)])
      .then(([ov, top]) => { setOverview(ov); setTopData(top); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      await triggerForecast('Manual trigger from dashboard');
      setTriggerMsg('✅ Forecast queued. Refresh in a moment.');
    } catch {
      setTriggerMsg('❌ Failed to trigger forecast.');
    } finally {
      setTriggering(false);
      setTimeout(() => setTriggerMsg(''), 5000);
    }
  };

  if (loading) return (
    <div className="loading-center">
      <div className="spinner" />
      <span>Loading dashboard…</span>
    </div>
  );

  if (!overview || overview.error) return (
    <div>
      <div className="page-header">
        <h1>Demand Forecast Dashboard</h1>
        <p>No completed forecast run found. Run training then trigger a forecast.</p>
      </div>
      <div className="page-body">
        <button className="btn btn-primary" onClick={handleTrigger} disabled={triggering}>
          {triggering ? '⏳ Triggering…' : '▶ Run Forecast Now'}
        </button>
        {triggerMsg && <p style={{ marginTop: 12, fontSize: 13, color: 'var(--accent-green)' }}>{triggerMsg}</p>}
      </div>
    </div>
  );

  // Bar chart: top 15 products by forecasted demand
  const barData = [{
    type: 'bar',
    x: topData.map(r => r.total_forecast?.toFixed(1)),
    y: topData.map(r => r.product_name?.slice(0, 30)),
    orientation: 'h',
    marker: {
      color: topData.map((_, i) => `hsl(${200 + i * 6}, 80%, ${60 - i * 1.5}%)`),
      line: { color: 'transparent' },
    },
    hovertemplate: '<b>%{y}</b><br>Forecast: %{x} units<extra></extra>',
  }];

  const barLayout = {
    ...PLOTLY_LAYOUT,
    margin: { t: 20, b: 40, l: 200, r: 30 },
    height: 380,
    xaxis: { ...PLOTLY_LAYOUT.xaxis, title: 'Forecasted Units (7-day)' },
    yaxis: { ...PLOTLY_LAYOUT.yaxis, automargin: true, tickfont: { size: 10 } },
  };

  // Category pie
  const catCounts = {};
  topData.forEach(r => { catCounts[r.category] = (catCounts[r.category] || 0) + (r.total_forecast || 0); });
  const pieData = [{
    type: 'pie',
    labels: Object.keys(catCounts),
    values: Object.values(catCounts),
    hole: 0.55,
    textinfo: 'label+percent',
    textfont: { size: 10, color: '#8ba3c1' },
    marker: {
      colors: ['#3b82f6','#06b6d4','#8b5cf6','#10b981','#f59e0b','#ef4444','#ec4899','#84cc16','#f97316'],
      line: { color: 'var(--bg-card)', width: 2 },
    },
    hovertemplate: '<b>%{label}</b><br>Demand: %{value:.1f}<br>%{percent}<extra></extra>',
  }];

  const pieLayout = {
    ...PLOTLY_LAYOUT,
    height: 300,
    margin: { t: 20, b: 20, l: 20, r: 20 },
    legend: { ...PLOTLY_LAYOUT.legend, orientation: 'v', x: 1, y: 0.5 },
    showlegend: false,
  };

  return (
    <div>
      <div className="page-header">
        <div className="flex items-center gap-12">
          <div>
            <h1>Demand Forecast Dashboard</h1>
            <p>Latest run · Model: <span className="mono">{overview.model_version}</span> · Forecast: {overview.target_date_from} → {overview.target_date_to}</p>
          </div>
          <div className="ml-auto flex gap-12 items-center">
            {triggerMsg && <span style={{ fontSize: 12, color: 'var(--accent-green)' }}>{triggerMsg}</span>}
            <button className="btn btn-primary" onClick={handleTrigger} disabled={triggering}>
              {triggering ? '⏳ Queuing…' : '▶ Run Forecast'}
            </button>
          </div>
        </div>
      </div>

      <div className="page-body">
        {/* Stat cards */}
        <div className="stats-grid mb-28">
          <StatCard label="Eligible Products"     value={overview.eligible_products?.toLocaleString()} sub="All categories" color="blue" />
          <StatCard label="Forecast Horizon"      value="7 days"  sub={`${overview.target_date_from} → ${overview.target_date_to}`} color="cyan" />
          <StatCard label="Total Forecasted Units" value={Math.round(overview.total_forecasted_demand)?.toLocaleString()} sub="7-day aggregate" color="green" />
          <StatCard label="Avg Daily Demand"      value={Math.round(overview.total_forecasted_demand / 7 / overview.eligible_products * 1000) / 1000} sub="Per product per day" color="purple" />
        </div>

        {/* Charts */}
        <div className="chart-grid">
          <div className="card">
            <div className="card-title">Top 15 Products by 7-Day Forecast</div>
            <Plot data={barData} layout={barLayout} config={CONFIG} style={{ width: '100%' }} />
          </div>
          <div className="card">
            <div className="card-title">Demand Mix by Category</div>
            <Plot data={pieData} layout={pieLayout} config={CONFIG} style={{ width: '100%' }} />
          </div>
        </div>

        {/* Run info */}
        <div className="card">
          <div className="card-title">Latest Forecast Run</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, fontSize: 13 }}>
            {[
              ['Run ID', overview.forecast_run_id?.slice(0, 18) + '…'],
              ['Model Version', overview.model_version],
              ['Rows Written', overview.rows_written?.toLocaleString() ?? 'N/A'],
              ['Forecast Origin', overview.forecast_origin],
              ['Completed At', overview.run_completed_at?.slice(0, 19).replace('T', ' ')],
              ['Scope', overview.demand_aggregation_scope],
            ].map(([k, v]) => (
              <div key={k}>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>{k}</div>
                <div className="mono">{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
