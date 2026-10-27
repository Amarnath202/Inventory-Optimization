import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import LazyPlot from '../components/LazyPlot';
import { errorMessage, getProductForecast, getProducts } from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatNumber } from '../utils/format';

const PLOT_CONFIG = { displayModeBar: false, responsive: true };
const EMPTY_PRODUCTS = [];
const PLOT_LAYOUT = {
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  font: { color: '#66778b', family: 'Inter, sans-serif', size: 10 },
  margin: { t: 10, b: 42, l: 48, r: 12 },
  xaxis: { gridcolor: '#edf1f4', linecolor: '#dce4e9' },
  yaxis: { gridcolor: '#edf1f4', linecolor: '#dce4e9', rangemode: 'tozero' },
};

export default function Products() {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [sortBy, setSortBy] = useState('name');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState('');
  const [detailReload, setDetailReload] = useState(0);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const pageSize = 15;

  const requestProducts = useCallback(async (signal) => {
    const response = await getProducts(undefined, { signal });
    if (!Array.isArray(response)) throw new Error('The API returned an unexpected product forecast list.');
    return response;
  }, []);
  const { data: productData, loading, error, reload: refreshProducts } = useApiResource(requestProducts);
  const products = productData ?? EMPTY_PRODUCTS;

  useEffect(() => {
    if (!selectedId) return undefined;
    const controller = new AbortController();
    getProductForecast(selectedId, undefined, { signal: controller.signal })
      .then((response) => {
        if (!response || typeof response !== 'object') throw new Error('The API returned an unexpected product detail.');
        setDetail(response);
      })
      .catch((requestError) => {
        if (!controller.signal.aborted) setDetailError(errorMessage(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, detailReload]);

  const categories = useMemo(() => [...new Set(
    products.map((product) => product.category).filter((value) => typeof value === 'string' && value.trim()),
  )].sort((a, b) => a.localeCompare(b)), [products]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return products
      .filter((product) => !category || product.category === category)
      .filter((product) => !query || [
        product.product_name,
        product.product_id,
        product.category,
        product.brand,
      ].some((value) => String(value ?? '').toLowerCase().includes(query)))
      .sort((left, right) => {
        const leftValue = sortBy === 'id' ? String(left.product_id ?? '') : String(left.product_name ?? '');
        const rightValue = sortBy === 'id' ? String(right.product_id ?? '') : String(right.product_name ?? '');
        return leftValue.localeCompare(rightValue, undefined, { numeric: true, sensitivity: 'base' });
      });
  }, [products, search, category, sortBy]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const pageRows = filtered.slice((page - 1) * pageSize, page * pageSize);
  const history = Array.isArray(detail?.history) ? detail.history : [];
  const forecasts = Array.isArray(detail?.forecasts) ? detail.forecasts : [];
  const chartData = [
    {
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Observed demand',
      x: history.map((entry) => entry.date),
      y: history.map((entry) => Number(entry.units_sold)),
      line: { color: '#5e85a8', width: 2 },
      marker: { size: 4 },
      hovertemplate: '%{x}<br>Observed: %{y} units<extra></extra>',
    },
    {
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Forecast',
      x: forecasts.map((entry) => entry.date),
      y: forecasts.map((entry) => Number(entry.forecasted_units)),
      line: { color: '#39866c', width: 2, dash: 'dot' },
      marker: { size: 6, symbol: 'diamond' },
      hovertemplate: '%{x}<br>Forecast: %{y:.1f} units<extra></extra>',
    },
  ];

  const selectProduct = (productId) => {
    if (String(selectedId) === String(productId)) {
      setSelectedId('');
      setDetail(null);
      setDetailLoading(false);
      setDetailError('');
      return;
    }
    setDetail(null);
    setDetailLoading(true);
    setDetailError('');
    setSelectedId(String(productId));
  };

  const retryDetail = () => {
    setDetail(null);
    setDetailLoading(true);
    setDetailError('');
    setDetailReload((attempt) => attempt + 1);
  };

  const closeDetail = () => {
    setSelectedId('');
    setDetail(null);
    setDetailLoading(false);
    setDetailError('');
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Products & forecasts</h1>
          <p>Browse products included in the forecast and inspect observed demand alongside forecast values.</p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={refreshProducts} disabled={loading}>
          Refresh products
        </button>
      </div>

      <div className="page-body">
        {selectedId && (
          <section className="card mb-20" aria-labelledby="product-detail-heading">
            <div className="detail-heading">
              <div>
                <h2 id="product-detail-heading" className="detail-title">Product forecast detail</h2>
                {detail?.product && (
                  <p className="detail-subtitle">
                    {detail.product.product_name || 'Product'} · ID <span className="mono">{detail.product.product_id || selectedId}</span>
                    {detail.product.category ? ` · ${detail.product.category}` : ''}
                    {detail.product.mrp !== null && detail.product.mrp !== undefined ? ` · MRP ${formatNumber(detail.product.mrp, 2)}` : ''}
                  </p>
                )}
              </div>
              <div className="flex gap-12">
                <Link className="btn btn-secondary" to={`/explainability?kind=forecast&id=${encodeURIComponent(detail?.product?.product_id || selectedId)}`}>Explain forecast</Link>
                <button className="btn btn-secondary" type="button" onClick={closeDetail}>Close</button>
              </div>
            </div>
            {detailLoading ? (
              <LoadingState label="Loading product forecast…" />
            ) : detailError ? (
              <ErrorState message={detailError} onRetry={retryDetail} />
            ) : detail ? (
              <>
                <div className="detail-stock">
                  <span className="stat-label">Current stock</span>
                  <strong>{formatNumber(detail.current_stock)}</strong>
                  <span className="stat-sub">units in latest-batch snapshot</span>
                </div>
                <p className="form-hint">This is not a live stock ledger. The forecast endpoint reports 0 when it cannot find a latest inventory batch.</p>
                {history.length || forecasts.length ? (
                  <LazyPlot
                    data={chartData}
                    layout={{
                      ...PLOT_LAYOUT,
                      height: 300,
                      legend: { orientation: 'h', y: 1.15, x: 0 },
                      yaxis: { ...PLOT_LAYOUT.yaxis, title: 'Units' },
                    }}
                    config={PLOT_CONFIG}
                    style={{ width: '100%' }}
                  />
                ) : <EmptyState title="No demand series returned" />}
                {forecasts.length > 0 && (
                  <div className="forecast-strip" aria-label="Daily forecast values">
                    {forecasts.map((forecast) => (
                      <div className="forecast-day" key={forecast.date}>
                        <span>{forecast.date || '—'}</span>
                        <strong>{formatNumber(forecast.forecasted_units, 1)}</strong>
                        <small>units</small>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : <EmptyState title="No product details returned" />}
          </section>
        )}

        <div className="filters-row">
          <label className="search-box product-search" htmlFor="product-search">
            <span aria-hidden="true">⌕</span>
            <input id="product-search" type="search" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Search product, ID, brand, or category" />
          </label>
          <label className="filter-field">
            <span>Category</span>
            <select value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}>
              <option value="">All categories</option>
              {categories.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="filter-field">
            <span>Sort by</span>
            <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
              <option value="name">Product name</option>
              <option value="id">Product ID</option>
            </select>
          </label>
          <span className="inline-message">{filtered.length} products</span>
        </div>

        {loading ? (
          <LoadingState label="Loading products…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refreshProducts} />
        ) : products.length === 0 ? (
          <EmptyState title="No forecast products returned">
            Check that the backend has a completed forecast run.
          </EmptyState>
        ) : filtered.length === 0 ? (
          <EmptyState title="No matching products">Change the search or category filter.</EmptyState>
        ) : (
          <>
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr><th>Product ID</th><th>Product name</th><th>Brand</th><th>Category</th><th /></tr>
                </thead>
                <tbody>
                  {pageRows.map((product) => (
                    <tr key={product.product_id}>
                      <td><span className="mono">{product.product_id ?? '—'}</span></td>
                      <td className="text">{product.product_name || '—'}</td>
                      <td>{product.brand || '—'}</td>
                      <td>{product.category ? <span className="tag">{product.category}</span> : '—'}</td>
                      <td className="numeric">
                        <button className="btn btn-secondary" type="button" aria-expanded={String(selectedId) === String(product.product_id)} onClick={() => selectProduct(product.product_id)}>
                          {String(selectedId) === String(product.product_id) ? 'Hide' : 'View forecast'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {filtered.length > pageSize && (
              <div className="pagination">
                <span className="inline-message">Page {page} of {pageCount}</span>
                <button className="btn btn-secondary" type="button" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>Previous</button>
                <button className="btn btn-secondary" type="button" disabled={page >= pageCount} onClick={() => setPage((current) => current + 1)}>Next</button>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
