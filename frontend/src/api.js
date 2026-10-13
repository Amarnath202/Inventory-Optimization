// src/api.js — Centralised API client
import axios from 'axios';

const BASE_URL = 'http://localhost:8000';

const api = axios.create({ baseURL: BASE_URL, timeout: 30000 });

export const getOverview    = ()             => api.get('/api/forecast/overview').then(r => r.data);
export const getProducts    = (runId)        => api.get('/api/forecast/products', { params: { run_id: runId } }).then(r => r.data);
export const getProductForecast = (id, runId) => api.get(`/api/forecast/products/${id}`, { params: { run_id: runId } }).then(r => r.data);
export const getTopDemand   = (n, runId)     => api.get('/api/forecast/top', { params: { n, run_id: runId } }).then(r => r.data);
export const getForecastTable = (params)     => api.get('/api/forecast/table', { params }).then(r => r.data);
export const getRuns        = (limit)        => api.get('/api/forecast/runs', { params: { limit } }).then(r => r.data);
export const getRun         = (id)           => api.get(`/api/forecast/runs/${id}`).then(r => r.data);
export const triggerForecast = (reason)      => api.post('/api/forecast/trigger', { reason }).then(r => r.data);
export const getMetrics     = ()             => api.get('/api/forecast/metrics').then(r => r.data);

export default api;
