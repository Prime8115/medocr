import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import ProblemReports from './ProblemReports';
import * as reports from '../api/problemReports';

vi.mock('../api/problemReports');

const customerReport: reports.ProblemReport = {
  id: 'report1', document_id: 'doc1', shop_id: 'shop1', shop_name: 'Shop One',
  reporter_email: 'recent@example.com', reported_at: '2026-11-03T06:00:00Z',
  note: 'Saved value reverted after rescan', snapshot: { item_count: 4 },
  status: 'open', resolution_note: null, updated_at: null, closed_at: null,
  document: null, history: [], document_events: [], saved_corrections: [], same_file_scans: [],
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(reports.listProblemReports).mockResolvedValue({ total: 1, items: [customerReport] });
  vi.mocked(reports.getProblemReport).mockResolvedValue(customerReport);
  vi.mocked(reports.updateProblemReport).mockImplementation(async () => ({
    ...customerReport, status: 'closed', resolution_note: 'Fixed and verified',
    closed_at: '2026-11-03T08:00:00Z',
  }));
});

test('searches all reports by customer email and requires a resolution before closing', async () => {
  render(<ProblemReports />);
  expect(await screen.findByText('Saved value reverted after rescan')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Search reports' }), { target: { value: 'recent@example.com' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(reports.listProblemReports).toHaveBeenCalledWith(undefined, 0, 'recent@example.com'));
  fireEvent.click(screen.getByText('Saved value reverted after rescan'));
  await screen.findByText('Report report1');
  fireEvent.click(screen.getByRole('button', { name: 'Close with resolution' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Enter what was resolved');
  expect(reports.updateProblemReport).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: 'Resolution note' }), { target: { value: 'Fixed and verified' } });
  fireEvent.click(screen.getByRole('button', { name: 'Close with resolution' }));
  await waitFor(() => expect(reports.updateProblemReport).toHaveBeenCalledWith('report1', 'closed', 'Fixed and verified'));
});
