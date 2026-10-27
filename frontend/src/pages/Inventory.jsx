import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  assessInventory,
  errorMessage,
  getInventoryOverview,
  getInventoryProduct,
  getInventoryProducts,
  getProducts,
} from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatDateTime, formatNumber } from '../utils/format';

const PAGE_SIZE = 25;
const DECISIONS = [
  ['', 'All decisions'],
  ['procurement_required', 'Procurement required'],
  ['excess_stock_review', 'Excess-stock review'],
  ['normal', 'Normal'],
  ['insufficient_data', 'Insufficient data'],
];

function StatCard({ label, value, color }) {
  return <article className={`stat-card ${color}`}><div className="stat-label">{label}</div><div className="stat-value">{formatNumber(value)}</div></article>;
}

export default function Inventory() {
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState('');
  const [decision, setDecision] = useState('');
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [assessing, setAssessing] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const detailRequest = useRef(null);

  useEffect(() => () => detailRequest.current?.abort(), []);

  const requestInventory = useCallback(async (signal) => {
    const params = { limit: PAGE_SIZE, offset };
    if (decision) params.routing_decision = decision;
    const [overview, pageData] = await Promise.all([
      getInventoryOverview({ signal }),
      getInventoryProducts(params, { signal }),
    ]);
    if (!overview || typeof overview !== 'object'
      || !pageData || !Array.isArray(pageData.items)
      || !Number.isFinite(Number(pageData.total))) {
      throw new Error('The API returned an unexpected inventory response.');
    }
    return { overview, pageData };
  }, [decision, offset]);
  const { data: inventoryData, loading, error, reload: refresh } = useApiResource(requestInventory);
  const overview = inventoryData?.overview;
  const pageData = inventoryData?.pageData;

  useEffect(() => {
    let active = true;
    getProducts()
      .then((response) => {
        if (!Array.isArray(response)) throw new Error('The API returned an unexpected product list.');
        if (active) {
          setProducts(response);
          if (response.length) setSelectedProduct(String(response[0].product_id));
        }
      })
      .catch((requestError) => { if (active) setActionError(errorMessage(requestError)); });
    return () => { active = false; };
  }, []);

  const currentItems = useMemo(() => {
    const query = search.trim().toLowerCase();
    const items = Array.isArray(pageData?.items) ? pageData.items : [];
    if (!query) return items;
    return items.filter((item) => [item.product_id, item.product_name, item.category, item.routing_decision]
      .some((value) => String(value ?? '').toLowerCase().includes(query)));
  }, [pageData, search]);

  const openDetail = async (productId) => {
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setDetail(null);
    setDetailError('');
    setDetailLoading(true);
    try {
      const response = await getInventoryProduct(productId, { signal: controller.signal });
      if (!response?.assessment || !response?.product) throw new Error('No inventory assessment detail was returned.');
      setDetail(response);
    } catch (requestError) {
      if (!controller.signal.aborted) setDetailError(errorMessage(requestError));
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  };

  const handleAssess = async (productId) => {
    const scope = productId ? `product ${productId}` : 'all products';
    const confirmed = window.confirm(`Run and persist an inventory assessment for ${scope}? This does not change stock or place orders.`);
    if (!confirmed) return;
    setAssessing(true);
    setActionMessage('');
    setActionError('');
    try {
      const result = await assessInventory(productId || undefined);
      setActionMessage(result.message || `Assessment request status: ${result.status}.`);
      if (productId && result.status !== 'accepted') {
        refresh();
      }
    } catch (requestError) {
      setActionError(errorMessage(requestError));
    } finally {
      setAssessing(false);
    }
  };

  const total = Number(pageData?.total || 0);
  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const assessment = detail?.assessment;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Inventory optimization</h1>
          <p>Backend inventory assessments use the latest inventory batch and stored demand forecasts. They are advisory, not live stock controls.</p>
        </div>
        <div className="flex items-center gap-12">
          <button className="btn btn-secondary" type="button" onClick={refresh} disabled={loading}>Refresh</button>
          <button className="btn btn-primary" type="button" onClick={() => handleAssess(null)} disabled={assessing}>
            {assessing ? 'Submitting…' : 'Assess all products'}
          </button>
        </div>
      </div>

      <div className="page-body">
        {actionMessage && <div className="notice success mb-20" role="status">{actionMessage}</div>}
        {actionError && <div className="notice error mb-20" role="alert">{actionError}</div>}
        {overview && (
          <div className="stats-grid">
            <StatCard label="Assessed products" value={overview.total_assessed} color="blue" />
            <StatCard label="Procurement required" value={overview.procurement_required} color="orange" />
            <StatCard label="Excess-stock review" value={overview.excess_stock_review} color="purple" />
            <StatCard label="Normal" value={overview.normal} color="green" />
            <StatCard label="Insufficient data" value={overview.insufficient_data} color="cyan" />
          </div>
        )}
        {overview?.last_assessed_at && <p className="inline-message mb-20">Latest assessment: {formatDateTime(overview.last_assessed_at)} · Forecast run {overview.latest_run_id || '—'}</p>}

        <section className="card mb-20">
          <h2 className="card-title">Run a single-product assessment</h2>
          <div className="assessment-controls">
            <label className="filter-field" htmlFor="inventory-product">
              <span>Product from forecast catalog</span>
              <select id="inventory-product" value={selectedProduct} onChange={(event) => setSelectedProduct(event.target.value)} disabled={!products.length}>
                {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name || product.product_id} · {product.product_id}</option>)}
              </select>
            </label>
            <button className="btn btn-secondary" type="button" onClick={() => handleAssess(selectedProduct)} disabled={assessing || !selectedProduct}>
              Assess selected product
            </button>
          </div>
          <p className="form-hint">Assessments persist backend records. Full-batch work is queued; single-product assessments run synchronously. No inventory quantity is modified.</p>
        </section>

        {detailLoading && <LoadingState label="Loading inventory assessment…" />}
        {detailError && <ErrorState message={detailError} />}
        {assessment && (
          <section className="card mb-20">
            <div className="detail-heading">
              <div>
                <h2 className="detail-title">{detail.product.product_name || detail.product.product_id} · Assessment detail</h2>
                <p className="detail-subtitle">Product {detail.product.product_id} · {detail.product.category || 'Category unavailable'} · Assessed {formatDateTime(assessment.assessed_at)}</p>
              </div>
              <div className="flex gap-12">
                {assessment.assessment_id && <Link className="btn btn-secondary" to={`/explainability?kind=inventory&id=${encodeURIComponent(assessment.assessment_id)}`}>Explain decision</Link>}
                <button className="btn btn-secondary" type="button" onClick={() => handleAssess(detail.product.product_id)} disabled={assessing}>Reassess</button>
                <button className="btn btn-secondary" type="button" onClick={() => { detailRequest.current?.abort(); setDetail(null); setDetailError(''); setDetailLoading(false); }}>Close</button>
              </div>
            </div>
            <div className="assessment-metrics">
              {[
                ['Available stock', assessment.available_stock],
                ['Inventory position', assessment.inventory_position],
                ['7-day forecast demand', assessment.forecast_demand_7d],
                ['Daily demand average', assessment.avg_daily_demand],
                ['Safety stock', assessment.safety_stock],
                ['Reorder point', assessment.reorder_point],
                ['Suggested replenishment', assessment.suggested_replenishment_qty],
                ['Supplier lead time (days)', assessment.lead_time_days],
                ['Minimum order quantity', assessment.minimum_order_qty],
                ['Maximum stock level', assessment.maximum_stock_level],
              ].map(([label, value]) => <div key={label}><span className="stat-label">{label}</span><strong>{formatNumber(value, 2)}</strong></div>)}
            </div>
            <div className="decision-summary">
              <span className={`badge ${assessment.routing_decision === 'procurement_required' ? 'badge-orange' : assessment.routing_decision === 'excess_stock_review' ? 'badge-purple' : 'badge-blue'}`}>{assessment.routing_decision || '—'}</span>
              <span className="inline-message">{assessment.replenishment_status || '—'} · {assessment.demand_source || 'Demand source unavailable'}</span>
              {assessment.routing_decision === 'procurement_required' && <Link className="btn btn-secondary" to="/procurement">Review suppliers</Link>}
              {assessment.routing_decision === 'excess_stock_review' && <Link className="btn btn-secondary" to="/excess-stock">Review excess stock</Link>}
            </div>
            {assessment.decision_reasons && <p className="notice mt-12">{assessment.decision_reasons}</p>}
            {assessment.calculation_notes && <p className="notice mt-12">{assessment.calculation_notes}</p>}
          </section>
        )}

        {loading ? (
          <LoadingState label="Loading inventory assessments…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refresh} />
        ) : !pageData || !pageData.items.length ? (
          <EmptyState title="No inventory assessments">
            Run an assessment to populate this view from the backend.
          </EmptyState>
        ) : (
          <>
            <div className="filters-row">
              <label className="search-box product-search" htmlFor="inventory-search">
                <span aria-hidden="true">⌕</span>
                <input id="inventory-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search this page by product, category, or decision" />
              </label>
              <label className="filter-field">
                <span>Routing decision</span>
                <select value={decision} onChange={(event) => { setOffset(0); setDecision(event.target.value); refresh(); }}>
                  {DECISIONS.map(([value, label]) => <option key={value || 'all'} value={value}>{label}</option>)}
                </select>
              </label>
              <span className="inline-message">{formatNumber(total)} records</span>
            </div>
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Product</th><th>Category</th><th>Stock</th><th>7-day demand</th><th>Safety stock</th><th>Reorder point</th><th>Suggested qty</th><th>Decision</th><th /></tr></thead>
                <tbody>
                  {currentItems.map((item) => (
                    <tr key={item.assessment_id}>
                      <td><strong className="text">{item.product_name || '—'}</strong><br /><span className="mono">{item.product_id}</span></td>
                      <td>{item.category || '—'}</td>
                      <td>{formatNumber(item.available_stock)}</td>
                      <td>{formatNumber(item.forecast_demand_7d, 1)}</td>
                      <td>{formatNumber(item.safety_stock, 1)}</td>
                      <td>{formatNumber(item.reorder_point, 1)}</td>
                      <td>{formatNumber(item.suggested_replenishment_qty)}</td>
                      <td><span className="badge">{item.routing_decision || '—'}</span></td>
                      <td className="numeric"><button className="btn btn-secondary" type="button" onClick={() => openDetail(item.product_id)}>Details</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <span className="inline-message">Page {pageNumber} of {pageCount}</span>
              <button className="btn btn-secondary" type="button" disabled={offset === 0 || loading} onClick={() => { setOffset(Math.max(0, offset - PAGE_SIZE)); refresh(); }}>Previous</button>
              <button className="btn btn-secondary" type="button" disabled={offset + PAGE_SIZE >= total || loading} onClick={() => { setOffset(offset + PAGE_SIZE); refresh(); }}>Next</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
