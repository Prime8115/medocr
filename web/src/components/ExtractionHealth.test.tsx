import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import ExtractionHealth from './ExtractionHealth';
import * as documentsApi from '../api/documents';

vi.mock('../api/documents');

function stats(overrides: Partial<documentsApi.ExtractionStats> = {}): documentsApi.ExtractionStats {
  return {
    documents: 20,
    window_days: 30,
    by_status: { needs_review: 20 },
    by_doc_type: { invoice: 18, prescription: 2 },
    by_pipeline: { pdf_parser: 16, gemini: 2 },
    pages_failed_documents: 0,
    reported_by_users: 0,
    warnings: [],
    invoices: {
      total: 18, tier1_parser: 16, tier1_parser_pct: 88.9,
      reconciled: 17, reconciled_pct: 94.4, total_mismatch: 1, total_unreadable: 0,
      with_duplicates_removed: 0, duplicate_rows_removed: 0, multi_copy_pdfs: 0,
    },
    ...overrides,
  };
}

beforeEach(() => vi.resetAllMocks());

describe('ExtractionHealth', () => {
  test('shows the two numbers that matter: does it add up, and did the exact parser run', async () => {
    vi.mocked(documentsApi.getExtractionStats).mockResolvedValue(stats());
    render(<ExtractionHealth />);

    expect(await screen.findByText('Extraction health')).toBeInTheDocument();
    expect(screen.getByText('94.4%')).toBeInTheDocument();   // adds up
    expect(screen.getByText('88.9%')).toBeInTheDocument();   // exact parser
    expect(screen.getByText('last 30 days')).toBeInTheDocument();
  });

  test('surfaces operator warnings from the backend', async () => {
    vi.mocked(documentsApi.getExtractionStats).mockResolvedValue(
      stats({ warnings: ['40% of invoices do not add up to their printed total.'] }),
    );
    render(<ExtractionHealth />);
    expect(await screen.findByText(/do not add up/)).toBeInTheDocument();
  });

  test('shows repeats removed only when there were any', async () => {
    vi.mocked(documentsApi.getExtractionStats).mockResolvedValue(
      stats({
        invoices: { ...stats().invoices, duplicate_rows_removed: 286, multi_copy_pdfs: 1 },
      }),
    );
    render(<ExtractionHealth />);
    expect(await screen.findByText('286')).toBeInTheDocument();
    expect(screen.getByText(/multi-copy PDF/)).toBeInTheDocument();
  });

  test('flags documents users reported', async () => {
    vi.mocked(documentsApi.getExtractionStats).mockResolvedValue(stats({ reported_by_users: 3 }));
    render(<ExtractionHealth />);
    expect(await screen.findByText('flagged by users')).toBeInTheDocument();
  });

  test('renders nothing when there is no data yet', async () => {
    vi.mocked(documentsApi.getExtractionStats).mockResolvedValue(stats({ documents: 0 }));
    const { container } = render(<ExtractionHealth />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  test('never blocks the queue when stats are unavailable', async () => {
    vi.mocked(documentsApi.getExtractionStats).mockRejectedValue(new Error('down'));
    const { container } = render(<ExtractionHealth />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
