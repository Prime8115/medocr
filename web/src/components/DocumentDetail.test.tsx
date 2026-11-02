import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import DocumentDetail from './DocumentDetail';
import * as documents from '../api/documents';
import * as inventory from '../api/inventory';
import { getLeaf } from '../lib/payload';

vi.mock('../api/documents');
vi.mock('../api/inventory');
vi.mock('./SourcePages', () => ({ default: () => null }));
vi.mock('./TallyReadiness', () => ({ default: () => null }));

const initial: documents.DocumentDto = {
  id: 'd1', doc_type: 'invoice', status: 'needs_review', created_at: '2026-11-02T00:00:00Z',
  payload: {
    doc_type: 'invoice',
    fields: {
      supplier: { name: { value: 'Soham Pharma Supply' }, gstin: { value: null },
        address: { value: 'Old address' }, dl_date_1: { value: null } },
      bill_to: {}, ship_to: {}, invoice: {}, line_items: [],
    },
    meta: {},
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(documents.getDocument).mockResolvedValue(initial);
  vi.mocked(inventory.matchDocument).mockResolvedValue(null as never);
  vi.mocked(documents.patchDocument).mockImplementation(async (_id, fields) => ({
    ...initial, payload: { ...initial.payload!, fields },
  }));
});

function show() {
  return render(<MemoryRouter initialEntries={['/documents/d1']}>
    <Routes><Route path="/documents/:id" element={<DocumentDetail />} /></Routes>
  </MemoryRouter>);
}

function supplierField(label: string): HTMLElement {
  const card = screen.getByRole('heading', { name: 'Supplier' }).closest('.glass-card');
  if (!card) throw new Error('Supplier card missing');
  const field = within(card as HTMLElement).getByText(label).parentElement;
  if (!field) throw new Error(`${label} field missing`);
  return field;
}

test('using a header recheck saves it together with another unsaved edit', async () => {
  vi.mocked(documents.recheckField).mockResolvedValue({
    path: 'supplier.gstin', current: null, found: '24AAKCM3927B1ZQ',
    printed_label: 'GSTIN', printed_text: 'GSTIN : 24AAKCM3927B1ZQ',
    on_page: true, agrees: false, message: 'The AI reads 24AAKCM3927B1ZQ.',
  });
  show();
  await screen.findByRole('heading', { name: 'Supplier' });
  fireEvent.change(within(supplierField('Address')).getByRole('textbox'), {
    target: { value: '2nd floor, Soham Square, Ahmedabad' },
  });
  fireEvent.click(within(supplierField('GSTIN')).getByRole('button', { name: 'Recheck' }));
  await within(supplierField('GSTIN')).findByText(/The AI reads/);
  fireEvent.click(within(supplierField('GSTIN')).getByRole('button', { name: 'Use & save' }));

  await waitFor(() => expect(documents.patchDocument).toHaveBeenCalledTimes(1));
  const sent = vi.mocked(documents.patchDocument).mock.calls[0][1];
  expect(getLeaf(sent, 'supplier.gstin')?.value).toBe('24AAKCM3927B1ZQ');
  expect(getLeaf(sent, 'supplier.address')?.value).toBe('2nd floor, Soham Square, Ahmedabad');
  await waitFor(() => expect(within(supplierField('GSTIN')).getByRole('textbox')).toHaveValue('24AAKCM3927B1ZQ'));
});

test('a Recheck button follows each supplier DL Date and saves the selected date', async () => {
  vi.mocked(documents.recheckField).mockResolvedValue({
    path: 'supplier.dl_date_1', current: null, found: '21/02/2025',
    printed_label: 'Date', printed_text: 'DL No. 20B-124567-111 Date 21/02/2025',
    on_page: true, agrees: false, message: 'The AI reads 21/02/2025.',
  });
  show();
  await screen.findByRole('heading', { name: 'Supplier' });
  for (const index of [1, 2, 3]) {
    expect(within(supplierField(`Drug licence ${index} date`)).getByRole('button', { name: 'Recheck' })).toBeTruthy();
  }
  fireEvent.click(within(supplierField('Drug licence 1 date')).getByRole('button', { name: 'Recheck' }));
  await within(supplierField('Drug licence 1 date')).findByText(/The AI reads/);
  fireEvent.click(within(supplierField('Drug licence 1 date')).getByRole('button', { name: 'Use & save' }));
  await waitFor(() => expect(documents.patchDocument).toHaveBeenCalledTimes(1));
  expect(getLeaf(vi.mocked(documents.patchDocument).mock.calls[0][1], 'supplier.dl_date_1')?.value).toBe('21/02/2025');
});
