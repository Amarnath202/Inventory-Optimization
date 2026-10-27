import axios from 'axios';

const configuredBaseUrl = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';
const BASE_URL = configuredBaseUrl.replace(/\/+$/, '');
const api = axios.create({ baseURL: BASE_URL, timeout: 30000 });

export function errorMessage(error) {
  if (axios.isAxiosError(error)) {
    if (!error.response) {
      return 'The API could not be reached. Check that the backend is running and the API URL is correct.';
    }

    const detail = error.response.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => item?.msg).filter(Boolean);
      if (messages.length) return messages.join('; ');
    }

    return `The API request failed with status ${error.response.status}.`;
  }

  return error instanceof Error ? error.message : 'An unexpected error occurred.';
}

export const getHealth = () => api.get('/health').then((response) => response.data);

export const getOverview = (config) => api.get('/api/forecast/overview', config).then((response) => response.data);
export const getProducts = (runId, config) => api.get('/api/forecast/products', { ...config, params: { ...config?.params, run_id: runId } }).then((response) => response.data);
export const getProductForecast = (id, runId, config) => api.get(`/api/forecast/products/${encodeURIComponent(id)}`, { ...config, params: { ...config?.params, run_id: runId } }).then((response) => response.data);
export const getTopDemand = (count, runId, config) => api.get('/api/forecast/top', { ...config, params: { ...config?.params, n: count, run_id: runId } }).then((response) => response.data);
export const getForecastTable = (params, config) => api.get('/api/forecast/table', { ...config, params: { ...params, ...config?.params } }).then((response) => response.data);
export const getRuns = (limit, config) => api.get('/api/forecast/runs', { ...config, params: { ...config?.params, limit } }).then((response) => response.data);
export const getRun = (id, config) => api.get(`/api/forecast/runs/${encodeURIComponent(id)}`, config).then((response) => response.data);
export const triggerForecast = (reason) => api.post('/api/forecast/trigger', { reason }).then((response) => response.data);
export const getMetrics = (config) => api.get('/api/forecast/metrics', config).then((response) => response.data);

export const getInventoryOverview = (config) => api.get('/api/inventory-optimization/overview', config).then((response) => response.data);
export const getInventoryProducts = (params, config) => api.get('/api/inventory-optimization/products', { ...config, params: { ...params, ...config?.params } }).then((response) => response.data);
export const getInventoryProduct = (id, config) => api.get(`/api/inventory-optimization/products/${encodeURIComponent(id)}`, config).then((response) => response.data);
export const assessInventory = (productId) => api.post('/api/inventory-optimization/assess', productId ? { product_id: productId } : {}).then((response) => response.data);
export const getInventoryRecommendations = (params, config) => api.get('/api/inventory-optimization/recommendations', { ...config, params: { ...params, ...config?.params } }).then((response) => response.data);

export const getOrchestrationStatus = (config) => api.get('/api/orchestration/status', config).then((response) => response.data);
export const runOrchestration = (productId, triggerReason) => api.post('/api/orchestration/run', { product_id: productId, trigger_reason: triggerReason }).then((response) => response.data);
export const getOrchestrationRun = (runId, config) => api.get(`/api/orchestration/run/${encodeURIComponent(runId)}`, config).then((response) => response.data);

export const getExcessAssessments = (params, config) => api.get('/api/excess-stock/assessments', { ...config, params: { ...params, ...config?.params } }).then((response) => response.data);
export const getExcessAssessment = (productId, config) => api.get(`/api/excess-stock/assessments/${encodeURIComponent(productId)}`, config).then((response) => response.data);
export const getExcessSummary = (config) => api.get('/api/excess-stock/summary', config).then((response) => response.data);
export const assessExcessStock = (productId) => api.post('/api/excess-stock/assess', { product_id: productId }).then((response) => response.data);

export const getProcurementRecommendations = (params, config) => api.get('/api/procurement/recommendations', { ...config, params: { ...params, ...config?.params } }).then((response) => response.data);
export const getSupplierAlternatives = (productId, config) => api.get(`/api/procurement/suppliers/${encodeURIComponent(productId)}`, config).then((response) => response.data);
export const getSupplierRecommendation = (productId, config) => api.get(`/api/procurement/recommend/${encodeURIComponent(productId)}`, config).then((response) => response.data);

export const getExplainabilityHealth = (config) => api.get('/api/explainability/health', config).then((response) => response.data);
export const getExplainabilityModels = (config) => api.get('/api/explainability/models', config).then((response) => response.data);
export const getForecastExplanation = (productId, config) => api.get(`/api/explainability/forecast/${encodeURIComponent(productId)}`, config).then((response) => response.data);
export const getInventoryExplanation = (assessmentId, config) => api.get(`/api/explainability/inventory/${encodeURIComponent(assessmentId)}`, config).then((response) => response.data);
export const getProcurementExplanation = (recommendationId, config) => api.get(`/api/explainability/procurement/${encodeURIComponent(recommendationId)}`, config).then((response) => response.data);
export const getExcessStockExplanation = (assessmentId, config) => api.get(`/api/explainability/excess-stock/${encodeURIComponent(assessmentId)}`, config).then((response) => response.data);

export default api;
