import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import ReviewQueue from './ReviewQueue';
import * as documentsApi from '../api/documents';

vi.mock('../api/documents');

const docs: documentsApi.DocumentDto[] = [
  { id: 'doc_aaa', doc_type: 'prescription', status: 'needs_review', overall_confidence: 0.9, created_at: '2026-07-27',
    payload: { doc_type: 'prescription', fields: { patient: { name: { value: 'Ramesh' } } } } },
  { id: 'doc_bbb', doc_type: 'invoice', status: 'approved', overall_confidence: 0.7, created_at: '2026-07-27',
    payload: { doc_type: 'invoice', fields: { supplier: { name: { value: 'MEDLEY' } }, invoice: { invoice_no: { value: '5311' } } } } },
];

beforeEach(() => {
  vi.mocked(documentsApi.listDocuments).mockResolvedValue(docs);
  // The queue also renders the extraction-health card; it must never block the
  // queue, so an unavailable stats call is a valid state.
  vi.mocked(documentsApi.getExtractionStats).mockRejectedValue(new Error('no stats'));
});

describe('ReviewQueue', () => {
  test('renders documents from the API', async () => {
    render(
      <MemoryRouter>
        <ReviewQueue />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Prescription · Ramesh')).toBeInTheDocument();
    expect(screen.getByText('MEDLEY · #5311')).toBeInTheDocument();
  });

  test('search filters the list client-side', async () => {
    render(
      <MemoryRouter>
        <ReviewQueue />
      </MemoryRouter>,
    );
    await screen.findByText('Prescription · Ramesh');
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'bbb' } });
    await waitFor(() => expect(screen.queryByText('Prescription · Ramesh')).not.toBeInTheDocument());
    expect(screen.getByText('MEDLEY · #5311')).toBeInTheDocument();
  });

  test('shows an error when the API fails', async () => {
    vi.mocked(documentsApi.listDocuments).mockRejectedValueOnce(new Error('down'));
    render(
      <MemoryRouter>
        <ReviewQueue />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/could not load documents/i)).toBeInTheDocument();
  });
});
