// src/pages/Products.jsx
import { useEffect, useState } from 'react';
import Plot from 'react-plotly.js';
import { getProducts, getProductForecast } from '../api';

const PLOTLY_LAYOUT = {
  paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
  font: { color: '#8ba3c1', family: 'Inter', size: 11 },
  margin: { t: 30, b: 50, l: 55, r: 20 },
  xaxis: { gridcolor: 'rgba(99,179,237,0.08)', linecolor: 'rgba(99,179,237,0.12)' },
  yaxis: { gridcolor: 'rgba(99,179,237,0.08)', linecolor: 'rgba(99,179,237,0.12)', title: 'Units Sold' },
  legend: { bgcolor: 'rgba(0,0,0,0)', font: { size: 11 } },
};
const CONFIG = { displayModeBar: false, responsive: true };

export default function Products() {
  const [products,  setProducts]  = useState([]);
  const [search,    setSearch]    = useState('');
  const [selected,  setSelected]  = useState(null);
  const [detail,    setDetail]    = useState(null);
  const [loading,   setLoading]   = useState(true);
  const [detailLoad, setDetailLoad] = useState(false);

  useEffect(() => {
    getProducts().then(setProducts).catch(console.error).finally(() => setLoading(false));
  }, []);

  const filtered = products.filter(p =>
    p.product_name?.toLowerCase().includes(search.toLowerCase()) ||
    p.product_id?.toLowerCase().includes(search.toLowerCase()) ||
    p.category?.toLowerCase().includes(search.toLowerCase())
  );

  const handleSelect = async (pid) => {
    if (selected === pid) { setSelected(null); setDetail(null); return; }
    setSelected(pid);
    setDetailLoad(true);
    try {
      const d = await getProductForecast(pid);
      setDetail(d);
    } catch (e) { console.error(e); }
    finally { setDetailLoad(false); }
  };

  const buildChart = (d) => {
    if (!d) return { data: [], layout: {} };
    const history  = d.history  || [];
    const forecasts = d.forecasts || [];

    const histTrace = {
      type: 'scatter', mode: 'lines+markers', name: 'Actual Demand',
      x: history.map(h => h.date), y: history.map(h => h.units_sold),
      line: { color: '#63b3ed', width: 2 },
      marker: { size: 4, color: '#63b3ed' },
      hovertemplate: '%{x}<br>Actual: %{y} units<extra></extra>',
    };

    const forecastTrace = {
      type: 'scatter', mode: 'lines+markers', name: 'LightGBM Forecast',
      x: forecasts.map(f => f.date), y: forecasts.map(f => parseFloat(f.forecasted_units).toFixed(2)),
      line: { color: '#34d399', width: 2.5, dash: 'dot' },
      marker: { size: 7, color: '#34d399', symbol: 'diamond' },
      hovertemplate: '%{x}<br>Forecast: %{y} units<extra></extra>',
    };

    // Shaded region for forecast period
    if (forecasts.length > 0) {
      const xStart = forecasts[0].date;
      const xEnd   = forecasts[forecasts.length - 1].date;
      const yMax   = Math.max(...history.map(h => h.units_sold), ...forecasts.map(f => f.forecasted_units)) * 1.15 + 1;
      const shadedTrace = {
        type: 'scatter', name: 'Forecast Window', showlegend: false,
        x: [xStart, xEnd, xEnd, xStart, xStart],
        y: [0, 0, yMax, yMax, 0],
        fill: 'toself',
        fillcolor: 'rgba(52, 211, 153, 0.06)',
        line: { color: 'transparent' },
        hoverinfo: 'skip',
      };
      return { data: [shadedTrace, histTrace, forecastTrace], yMax };
    }
    return { data: [histTrace, forecastTrace] };
  };

  const chart = detail ? buildChart(detail) : null;
  const chartLayout = detail ? {
    ...PLOTLY_LAYOUT,
    height: 280,
    margin: { t: 24, b: 44, l: 52, r: 20 },
    yaxis: { ...PLOTLY_LAYOUT.yaxis, rangemode: 'tozero', title: 'Units' },
  } : {};

  return (
    <div>
      <div className="page-header">
        <h1>Product Forecasts</h1>
        <p>Click a product to view its 7-day forecast and demand history.</p>
      </div>

      <div className="page-body">
        {/* Product detail panel */}
        {selected && (
          <div className="card mb-28">
            {detailLoad ? (
              <div className="loading-center" style={{ padding: 32 }}>
                <div className="spinner" /><span>Loading forecast…</span>
              </div>
            ) : detail ? (
              <>
                <div className="flex items-center gap-12 mb-20">
                  <div>
                    <div style={{ fontSize: 16, fontWeight: 700 }}>{detail.product?.product_name}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 3 }}>
                      <span className="mono">{detail.product?.product_id}</span>
                      &nbsp;·&nbsp;<span className="tag">{detail.product?.category}</span>
                      &nbsp;·&nbsp;MRP ₹{detail.product?.mrp}
                    </div>
                  </div>
                  <div className="ml-auto" style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Current Stock</div>
                    <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--accent-cyan)' }}>
                      {detail.current_stock?.toLocaleString()}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>units in hand</div>
                  </div>
                </div>

                {chart && (
                  <Plot data={chart.data} layout={chartLayout} config={CONFIG} style={{ width: '100%' }} />
                )}

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 8, marginTop: 16 }}>
                  {detail.forecasts?.map(f => (
                    <div key={f.date} style={{
                      background: 'rgba(52, 211, 153, 0.08)',
                      border: '1px solid rgba(52, 211, 153, 0.2)',
                      borderRadius: 10,
                      padding: '10px 8px',
                      textAlign: 'center',
                    }}>
                      <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{f.date.slice(5)}</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--accent-green)', marginTop: 2 }}>
                        {Math.round(f.forecasted_units)}
                      </div>
                      <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>units</div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="empty-state"><p>No data found for selected product.</p></div>
            )}
          </div>
        )}

        {/* Search + table */}
        <div className="search-box">
          <span>🔍</span>
          <input
            placeholder="Search by product name, ID, or category…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{filtered.length} results</span>
        </div>

        {loading ? (
          <div className="loading-center"><div className="spinner" /></div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Product ID</th>
                  <th>Product Name</th>
                  <th>Brand</th>
                  <th>Category</th>
                  <th style={{ textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 32 }}>No products found.</td></tr>
                ) : filtered.map(p => (
                  <tr key={p.product_id} onClick={() => handleSelect(p.product_id)}
                    style={{ cursor: 'pointer', background: selected === p.product_id ? 'rgba(56,189,248,0.07)' : '' }}>
                    <td><span className="mono">{p.product_id}</span></td>
                    <td className="text">{p.product_name}</td>
                    <td className="text">{p.brand}</td>
                    <td><span className="tag">{p.category}</span></td>
                    <td style={{ textAlign: 'right' }}>
                      <span style={{ fontSize: 11, color: 'var(--accent-cyan)' }}>
                        {selected === p.product_id ? 'Hide ▲' : 'View ▼'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
