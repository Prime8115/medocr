import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import Suppliers from './Suppliers';
import { fieldLabel as fieldName } from '../lib/verification';
import * as documentsApi from '../api/documents';

vi.mock('../api/documents');

function row(overrides: Partial<documentsApi.SupplierCoverage> = {}): documentsApi.SupplierCoverage {
  return {
    supplier_gstin: '27AAACI9822K1Z9', supplier_name: 'NEW SUPPLIER PVT LTD', documents: 4,
    verified_on_arrival: 1, verified_on_arrival_pct: 25, edited_documents: 3,
    corrected_fields: [{ field: 'invoice.lr_no', count: 3 }],
    missed_fields: [{ field: 'invoice.lr_date', count: 2 }],
    failed_checks: [], pipelines: { pdf_parser: 4 }, gap_filled_documents: 0,
    learned_used_documents: 1, learned_labels: 2, remembered_choices: 0,
    last_seen: '2026-10-01T00:00:00Z', status: 'needs_attention',
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(documentsApi.getSupplierLessons).mockResolvedValue([]);
});

describe('Suppliers', () => {
  test('lists suppliers with what needs attention', async () => {
    vi.mocked(documentsApi.getSupplierReport).mockResolvedValue({
      window_days: 90,
      suppliers: [row(), row({ supplier_gstin: '27AAACG1895Q1ZY', supplier_name: 'ZYDUS', status: 'good',
                               verified_on_arrival_pct: 100, corrected_fields: [], missed_fields: [],
                               learned_labels: 0 })],
    });
    render(<Suppliers />);
    expect(await screen.findByText('NEW SUPPLIER PVT LTD')).toBeInTheDocument();
    expect(screen.getByText('Needs attention')).toBeInTheDocument();
    expect(screen.getByText('LR no. ×3')).toBeInTheDocument();
    expect(screen.getByText('LR date ×2')).toBeInTheDocument();
    expect(screen.getByText('2 field location(s)')).toBeInTheDocument();
    expect(screen.getByText('Good')).toBeInTheDocument();
  });

  test('says so when there is nothing yet', async () => {
    vi.mocked(documentsApi.getSupplierReport).mockResolvedValue({ window_days: 90, suppliers: [] });
    render(<Suppliers />);
    expect(await screen.findByText(/No supplier invoices/)).toBeInTheDocument();
  });

  test('names fields for people', () => {
    expect(fieldName('invoice.due_date')).toBe('due date');
    expect(fieldName('bill_to.name')).toBe('bill-to name');
    expect(fieldName('line_items')).toBe('line items');
  });
});

describe('SupplierLessons', () => {
  test('shows what was learned and lets a lesson be forgotten', async () => {
    vi.mocked(documentsApi.getSupplierReport).mockResolvedValue({ window_days: 90, suppliers: [row()] });
    vi.mocked(documentsApi.getSupplierLessons).mockResolvedValue([{
      id: 'l1', supplier_gstin: '27AAACI9822K1Z9', heading: 'RETAILER RATE', field: 'ptr', kind: 'is',
      active: true, times_used: 4, times_undone: 0, times_failed: 0,
    }]);
    render(<Suppliers />);
    expect(await screen.findByText('Learned from your corrections')).toBeTruthy();
    expect(screen.getByText(/RETAILER RATE/)).toBeTruthy();
    expect(screen.getByText('4×')).toBeTruthy();
    expect(screen.getByText('Forget')).toBeTruthy();
  });
});
