import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { assessExcessStock, errorMessage, getExcessAssessment, getExcessAssessments, getExcessSummary, getProducts } from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatDateTime, formatNumber } from '../utils/format';

const PAGE_SIZE = 25;

function StatCard({ label, value, color }) {
  return <article className={`stat-card ${color}`}><div className="stat-label">{label}</div><div className="stat-value">{formatNumber(value)}</div></article>;
}

export default function ExcessStock() {
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState('');
  const [category, setCategory] = useState('');
  const [abcClass, setAbcClass] = useState('');
  const [xyzClass, setXyzClass] = useState('');
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [assessing, setAssessing] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const detailRequest = useRef(null);

  useEffect(() => () => detailRequest.current?.abort(), []);

  const requestAssessments = useCallback(async (signal) => {
    const params = { limit: PAGE_SIZE, offset };
    if (category) params.excess_category = category;
    if (abcClass) params.abc_class = abcClass;
    if (xyzClass) params.xyz_class = xyzClass;
    const [summary, pageData] = await Promise.all([
      getExcessSummary({ signal }),
      getExcessAssessments(params, { signal }),
    ]);
    if (!summary || typeof summary !== 'object'
      || !pageData || !Array.isArray(pageData.items)
      || !Number.isFinite(Number(pageData.total))) {
      throw new Error('The API returned an unexpected excess-stock response.');
    }
    return { summary, pageData };
  }, [category, abcClass, xyzClass, offset]);
  const { data: excessData, loading, error, reload: refresh } = useApiResource(requestAssessments);
  const summary = excessData?.summary;
  const pageData = excessData?.pageData;

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

  const filteredItems = useMemo(() => {
    const items = Array.isArray(pageData?.items) ? pageData.items : [];
    const query = search.trim().toLowerCase();
    if (!query) return items;
    return items.filter((item) => [
      item.product_id, item.product_name, item.category, item.excess_category,
      item.abc_class, item.xyz_class, item.recommended_action, item.excess_trigger_reasons,
    ].some((value) => String(value ?? '').toLowerCase().includes(query)));
  }, [pageData, search]);

  const handleAssess = async () => {
    if (!selectedProduct) return;
    const selected = products.find((product) => String(product.product_id) === selectedProduct);
    const confirmed = window.confirm(`Assess and save excess-stock classification for ${selected?.product_name || selectedProduct}? No stock is changed and no order is placed.`);
    if (!confirmed) return;
    setAssessing(true);
    setActionMessage('');
    setActionError('');
    try {
      const response = await assessExcessStock(selectedProduct);
      setActionMessage(response.message || `Assessment status: ${response.status}.`);
      refresh();
    } catch (requestError) {
      setActionError(errorMessage(requestError));
    } finally {
      setAssessing(false);
    }
  };

  const openDetail = async (productId) => {
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setDetail(null);
    setDetailError('');
    setDetailLoading(true);
    try {
      const response = await getExcessAssessment(productId, { signal: controller.signal });
      if (!response?.excess_stock_assessment) throw new Error(`No excess-stock assessment is available for ${productId}.`);
      setDetail(response);
    } catch (requestError) {
      if (!controller.signal.aborted) setDetailError(errorMessage(requestError));
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  };

  const total = Number(pageData?.total || 0);
  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const detailAssessment = detail?.excess_stock_assessment;
  const detailProduct = detail?.product;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Excess-stock management</h1>
          <p>Backend ABC/XYZ and excess-category assessments. Stock snapshots and demand inputs are historical, not live.</p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={refresh} disabled={loading}>Refresh</button>
      </div>

      <div className="page-body">
        {actionMessage && <div className="notice success mb-20" role="status">{actionMessage}</div>}
        {actionError && <div className="notice error mb-20" role="alert">{actionError}</div>}
        {summary && (
          <>
            <div className="stats-grid">
              <StatCard label="Assessed products" value={summary.total_assessed} color="blue" />
              <StatCard label="Overstock" value={summary.overstock} color="orange" />
              <StatCard label="Slow moving" value={summary.slow_moving} color="purple" />
              <StatCard label="Dead stock" value={summary.dead_stock} color="red" />
              <StatCard label="Normal" value={summary.normal} color="green" />
            </div>
            <section className="card mb-20">
              <h2 className="card-title">ABC / XYZ distribution</h2>
              <div className="classification-summary">
                <div><strong>ABC</strong><span>A: {formatNumber(summary.abc_a)} · B: {formatNumber(summary.abc_b)} · C: {formatNumber(summary.abc_c)}</span></div>
                <div><strong>XYZ</strong><span>X: {formatNumber(summary.xyz_x)} · Y: {formatNumber(summary.xyz_y)} · Z: {formatNumber(summary.xyz_z)}</span></div>
                <span className="inline-message">Last assessed {formatDateTime(summary.last_assessed_at)}</span>
              </div>
            </section>
          </>
        )}

        <section className="card mb-20">
          <h2 className="card-title">Assess a product</h2>
          <div className="assessment-controls">
            <label className="filter-field" htmlFor="excess-product">
              <span>Product from forecast catalog</span>
              <select id="excess-product" value={selectedProduct} onChange={(event) => setSelectedProduct(event.target.value)} disabled={!products.length}>
                {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name || product.product_id} · {product.product_id}</option>)}
              </select>
            </label>
            <button className="btn btn-primary" type="button" onClick={handleAssess} disabled={assessing || !selectedProduct}>
              {assessing ? 'Assessing…' : 'Assess selected product'}
            </button>
          </div>
          <p className="form-hint">This operation persists an assessment and may apply the module's table schema. It does not modify stock or place purchase orders.</p>
        </section>

        {detailLoading && <LoadingState label="Loading excess-stock detail…" />}
        {detailError && <ErrorState message={detailError} />}
        {detail && (
          <section className="card mb-20">
            <div className="detail-heading">
              <div>
                <h2 className="detail-title">{detailProduct?.product_name || detailProduct?.product_id} · Excess-stock detail</h2>
                <p className="detail-subtitle">Product {detailProduct?.product_id} · {detailProduct?.category || 'Category unavailable'} · {detailAssessment?.excess_category || 'Unclassified'}</p>
              </div>
              <div className="flex gap-12">
                {detailAssessment?.assessment_id && <Link className="btn btn-secondary" to={`/explainability?kind=excess&id=${encodeURIComponent(detailAssessment.assessment_id)}`}>Explain classification</Link>}
                <button className="btn btn-secondary" type="button" onClick={() => { detailRequest.current?.abort(); setDetail(null); setDetailError(''); setDetailLoading(false); }}>Close</button>
              </div>
            </div>
            <div className="assessment-metrics">
              {[
                ['ABC class', detailAssessment.abc_class],
                ['XYZ class', detailAssessment.xyz_class],
                ['Excess quantity estimate', detailAssessment.excess_qty_estimate],
                ['Available stock', detailAssessment.available_stock],
                ['Inventory position', detailAssessment.inventory_position],
                ['Maximum stock level', detailAssessment.maximum_stock_level],
                ['12-month revenue', detailAssessment.revenue_12m],
                ['Demand variability (CV)', detailAssessment.demand_cv],
                ['Average daily demand (90-day)', detailAssessment.avg_daily_demand_90d],
                ['Nonzero demand days (90-day)', detailAssessment.nonzero_demand_days_90d],
                ['Confidence', detailAssessment.confidence],
                ['Recommended action', detailAssessment.recommended_action],
              ].map(([label, value]) => <div key={label}><span className="stat-label">{label}</span><strong>{typeof value === 'number' ? formatNumber(value, 3) : value ?? '—'}</strong></div>)}
            </div>
            {detailAssessment?.excess_trigger_reasons && <p className="notice mt-12">{detailAssessment.excess_trigger_reasons}</p>}
            {detailAssessment?.recommendation_notes && <p className="notice mt-12">{detailAssessment.recommendation_notes}</p>}
            {detailAssessment?.data_warnings && <p className="notice warning mt-12">{detailAssessment.data_warnings}</p>}
            {detail.data_notice && <p className="notice warning mt-12">{detail.data_notice}</p>}
          </section>
        )}

        {loading ? (
          <LoadingState label="Loading excess-stock assessments…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refresh} />
        ) : !pageData || !pageData.items.length ? (
          <EmptyState title="No excess-stock assessments">
            {summary?.message || 'No assessments were returned for these filters.'}
          </EmptyState>
        ) : (
          <>
            <div className="filters-row">
              <label className="search-box product-search" htmlFor="excess-search">
                <span aria-hidden="true">⌕</span>
                <input id="excess-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search products, classification, or action" />
              </label>
              <label className="filter-field"><span>Category</span>
                <select value={category} onChange={(event) => { setOffset(0); setCategory(event.target.value); refresh(); }}>
                  <option value="">All categories</option><option value="overstock">Overstock</option><option value="slow_moving">Slow moving</option><option value="dead_stock">Dead stock</option><option value="normal">Normal</option>
                </select>
              </label>
              <label className="filter-field"><span>ABC</span>
                <select value={abcClass} onChange={(event) => { setOffset(0); setAbcClass(event.target.value); refresh(); }}>
                  <option value="">All</option><option value="A">A</option><option value="B">B</option><option value="C">C</option>
                </select>
              </label>
              <label className="filter-field"><span>XYZ</span>
                <select value={xyzClass} onChange={(event) => { setOffset(0); setXyzClass(event.target.value); refresh(); }}>
                  <option value="">All</option><option value="X">X</option><option value="Y">Y</option><option value="Z">Z</option>
                </select>
              </label>
              <span className="inline-message">{formatNumber(total)} records</span>
            </div>
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Product</th><th>Category</th><th>ABC</th><th>XYZ</th><th>Excess qty</th><th>12-month revenue</th><th>Recommended action</th><th>Confidence</th><th /></tr></thead>
                <tbody>
                  {filteredItems.map((item) => (
                    <tr key={item.assessment_id}>
                      <td><strong className="text">{item.product_name || '—'}</strong><br /><span className="mono">{item.product_id}</span></td>
                      <td><span className="badge">{item.excess_category || '—'}</span></td>
                      <td>{item.abc_class || '—'}</td>
                      <td>{item.xyz_class || '—'}</td>
                      <td>{formatNumber(item.excess_qty_estimate)}</td>
                      <td>{formatNumber(item.revenue_12m, 2)}</td>
                      <td>{item.recommended_action || '—'}</td>
                      <td>{item.confidence || '—'}</td>
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
