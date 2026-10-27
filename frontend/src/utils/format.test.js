import test from 'node:test';
import assert from 'node:assert/strict';
import { errorMessage } from '../api.js';
import { formatDate, formatDateTime, formatNumber } from './format.js';

test('formatNumber preserves zero and rejects missing or invalid values', () => {
  assert.equal(formatNumber(0), '0');
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber('not-a-number'), '—');
  assert.equal(formatNumber(12.345, 2), '12.35');
});

test('date formatters preserve invalid source strings and handle missing values', () => {
  assert.equal(formatDate('not-a-date'), 'not-a-date');
  assert.equal(formatDate(null), '—');
  assert.notEqual(formatDateTime('2026-10-27T10:00:00Z'), '—');
});

test('API errors are surfaced as readable response or network messages', () => {
  assert.equal(errorMessage({
    isAxiosError: true,
    response: { status: 404, data: { detail: 'No assessment found.' } },
  }), 'No assessment found.');
  assert.match(errorMessage({ isAxiosError: true, response: { status: 422, data: { detail: [{ msg: 'Required field' }] } } }), /Required field/);
  assert.match(errorMessage({ isAxiosError: true, message: 'offline' }), /API could not be reached/);
});
