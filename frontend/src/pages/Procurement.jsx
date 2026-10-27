import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  errorMessage,
  getProducts,
  getProcurementRecommendations,
  getSupplierAlternatives,
  getSupplierRecommendation,
} from '../api';
import { EmptyState, ErrorState, LoadingState } from '../components/RequestState';
import { useApiResource } from '../hooks/useApiResource';
import { formatDateTime, formatNumber } from '../utils/format';

export default function Procurement() {
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState('');
  const [offset, setOffset] = useState(0);
  const [productsError, setProductsError] = useState('');
  const [suppliers, setSuppliers] = useState(null);
  const [supplierError, setSupplierError] = useState('');
  const [recommendation, setRecommendation] = useState(null);
  const [recommendationError, setRecommendationError] = useState('');
  const [working, setWorking] = useState(false);

  const requestRecords = useCallback(async (signal) => {
    const response = await getProcurementRecommendations({ limit: 25, offset }, { signal });
    if (!response || !Array.isArray(response.items) || !Number.isFinite(Number(response.total))) {
      throw new Error('The API returned an unexpected procurement recommendation list.');
    }
    return response;
  }, [offset]);
  const { data: recommendationData, loading, error, reload: refresh } = useApiResource(requestRecords);
  const records = recommendationData?.items || [];
  const total = Number(recommendationData?.total || 0);
  const recordsNote = recommendationData?.note || '';

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
      .catch((requestError) => { if (active) setProductsError(errorMessage(requestError)); });
    return () => { active = false; };
  }, []);

  const selectedProductInfo = useMemo(
    () => products.find((product) => String(product.product_id) === selectedProduct),
    [products, selectedProduct],
  );

  const loadSuppliers = async () => {
    if (!selectedProduct) return;
    setWorking(true);
    setSupplierError('');
    setSuppliers(null);
    try {
      const response = await getSupplierAlternatives(selectedProduct);
      if (!response || !Array.isArray(response.suppliers)) throw new Error('The API returned an unexpected supplier alternatives response.');
      setSuppliers(response);
    } catch (requestError) {
      setSupplierError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const requestRecommendation = async () => {
    if (!selectedProduct) return;
    const productName = selectedProductInfo?.product_name || selectedProduct;
    const confirmed = window.confirm(
      `Calculate and save the lead-time-based supplier recommendation for ${productName}? This GET operation persists a recommendation but does not place an order.`,
    );
    if (!confirmed) return;
    setWorking(true);
    setRecommendationError('');
    setRecommendation(null);
    try {
      const response = await getSupplierRecommendation(selectedProduct);
      if (!response || !Array.isArray(response.ranked_suppliers)) {
        throw new Error('The API returned an unexpected supplier recommendation.');
      }
      setRecommendation(response);
      refresh();
    } catch (requestError) {
      setRecommendationError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const storedSupplierName = (record, rank) => record[`rank${rank}_supplier_name`] || record[`rank${rank}_supplier_id`] || '—';

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Supplier review & procurement</h1>
          <p>Current procurement recommendations rank eligible suppliers by recorded lead time only.</p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={refresh} disabled={loading}>Refresh saved recommendations</button>
      </div>

      <div className="page-body">
        <div className="notice warning mb-20">
          This backend does not implement TOPSIS or supplier-performance scoring. Lead times are described as modelled/synthetic; price, reliability, quality, and purchasing constraints are not ranked. Recommendations require manager approval and never place orders.
        </div>

        <section className="card mb-20">
          <h2 className="card-title">Review a product's suppliers</h2>
          <div className="assessment-controls">
            <label className="filter-field" htmlFor="procurement-product">
              <span>Product from forecast catalog</span>
              <select id="procurement-product" value={selectedProduct} onChange={(event) => {
                setSelectedProduct(event.target.value);
                setSuppliers(null);
                setRecommendation(null);
                setSupplierError('');
                setRecommendationError('');
              }} disabled={!products.length}>
                {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name || product.product_id} · {product.product_id}</option>)}
              </select>
            </label>
            <button className="btn btn-secondary" type="button" onClick={loadSuppliers} disabled={working || !selectedProduct}>View supplier alternatives</button>
            <button className="btn btn-primary" type="button" onClick={requestRecommendation} disabled={working || !selectedProduct}>
              {working ? 'Working…' : 'Calculate recommendation'}
            </button>
          </div>
          {productsError && <p className="status-error-text">{productsError}</p>}
          <p className="form-hint">The alternatives request is read-only. Calculating a recommendation persists a decision record (the route is GET but writes to the database); it does not submit a purchase order.</p>
        </section>

        {supplierError && <ErrorState message={supplierError} />}
        {suppliers && (
          <section className="card mb-20">
            <h2 className="card-title">Supplier alternatives · {suppliers.product_name || suppliers.product_id}</h2>
            <p className="inline-message">{suppliers.note}</p>
            {!suppliers.suppliers.length ? <EmptyState title="No supplier alternatives returned" /> : (
              <div className="table-wrapper mt-12">
                <table>
                  <thead><tr><th>Supplier</th><th>Supplier ID</th><th>Lead time (days)</th></tr></thead>
                  <tbody>{suppliers.suppliers.map((supplier) => (
                    <tr key={supplier.supplier_id}>
                      <td className="text">{supplier.supplier_name || '—'}</td>
                      <td><span className="mono">{supplier.supplier_id}</span></td>
                      <td>{formatNumber(supplier.lead_time_days)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        )}

        {recommendationError && <ErrorState message={recommendationError} />}
        {recommendation && (
          <section className="card mb-20">
            <div className="detail-heading">
              <div>
                <h2 className="detail-title">Supplier recommendation · {recommendation.product_name || recommendation.product_id}</h2>
                <p className="detail-subtitle">Status: {recommendation.recommendation_status} · Method: {recommendation.ranking_method} · {formatNumber(recommendation.eligible_supplier_count)} eligible suppliers</p>
              </div>
              <div className="flex items-center gap-12">
                <span className="badge badge-orange">Manager approval required</span>
                {recommendation.recommendation_id && <Link className="btn btn-secondary" to={`/explainability?kind=procurement&id=${encodeURIComponent(recommendation.recommendation_id)}`}>Explain ranking</Link>}
              </div>
            </div>
            {recommendation.suggested_replenishment_qty !== null && recommendation.suggested_replenishment_qty !== undefined && (
              <p className="inline-message mt-12">Suggested replenishment quantity from Module 3: {formatNumber(recommendation.suggested_replenishment_qty)}</p>
            )}
            {recommendation.ranked_suppliers.length ? (
              <div className="table-wrapper mt-12">
                <table>
                  <thead><tr><th>Rank</th><th>Supplier</th><th>Lead time (days)</th><th>Backend explanation</th></tr></thead>
                  <tbody>{recommendation.ranked_suppliers.map((supplier) => (
                    <tr key={`${supplier.rank}-${supplier.supplier_id}`}>
                      <td>{formatNumber(supplier.rank)}</td>
                      <td className="text">{supplier.supplier_name || '—'} <span className="mono">{supplier.supplier_id}</span></td>
                      <td>{formatNumber(supplier.lead_time_days)}</td>
                      <td className="text">{supplier.explanation || '—'}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <EmptyState title="No ranked suppliers returned" />}
            {recommendation.has_lead_time_tie && <p className="notice mt-12">{recommendation.tie_explanation || 'The backend reports a lead-time tie.'}</p>}
            {recommendation.data_warnings && <p className="notice warning mt-12">{recommendation.data_warnings}</p>}
            {recommendation.warning && <p className="notice warning mt-12">{recommendation.warning}</p>}
            {recommendation.recommendation_notes && <p className="notice mt-12">{recommendation.recommendation_notes}</p>}
          </section>
        )}

        {loading ? (
          <LoadingState label="Loading saved procurement recommendations…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refresh} />
        ) : records.length === 0 ? (
          <EmptyState title="No saved procurement recommendations">
            {recordsNote || 'No recommendation records have been returned by the API.'}
          </EmptyState>
        ) : (
          <section>
            <div className="section-heading">
              <div><h2>Saved recommendations</h2><p>{formatNumber(total)} records · latest first</p></div>
            </div>
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Record</th><th>Product</th><th>Recommended</th><th>Status</th><th>Rank 1 supplier</th><th>Lead time</th><th>Rank 2 supplier</th><th>Lead time</th><th>Approval</th><th /></tr></thead>
                <tbody>{records.map((record) => (
                  <tr key={record.recommendation_id}>
                    <td>{record.recommendation_id ?? '—'}</td>
                    <td><span className="mono">{record.product_id || '—'}</span></td>
                    <td>{formatDateTime(record.recommended_at)}</td>
                    <td><span className="badge">{record.recommendation_status || '—'}</span></td>
                    <td>{storedSupplierName(record, 1)}</td>
                    <td>{formatNumber(record.rank1_lead_time_days)}</td>
                    <td>{storedSupplierName(record, 2)}</td>
                    <td>{formatNumber(record.rank2_lead_time_days)}</td>
                    <td>{record.requires_manager_approval ? 'Required' : '—'}</td>
                    <td>{record.recommendation_id && <Link className="btn btn-secondary" to={`/explainability?kind=procurement&id=${encodeURIComponent(record.recommendation_id)}`}>Explain</Link>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div className="pagination">
              <span className="inline-message">Page {Math.floor(offset / 25) + 1} of {Math.max(1, Math.ceil(total / 25))}</span>
              <button className="btn btn-secondary" type="button" disabled={offset === 0 || loading} onClick={() => { setOffset(Math.max(0, offset - 25)); refresh(); }}>Previous</button>
              <button className="btn btn-secondary" type="button" disabled={offset + 25 >= total || loading} onClick={() => { setOffset(offset + 25); refresh(); }}>Next</button>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
