import { beforeEach, describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import TallyReadiness from './TallyReadiness';
import * as connectorsApi from '../api/connectors';
import * as documentsApi from '../api/documents';

vi.mock('../api/connectors');
vi.mock('../api/documents');
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { role: 'owner' } }) }));

const connector: connectorsApi.Connector = {
  id: 'tally-1', type: 'file_export', name: 'Tally pilot',
  config: { format: 'tally_prime_xml', tally: {} }, enabled: true,
  has_secret: false, created_at: '2026-10-23',
};
const receipt: documentsApi.TallyExportStatus = {
  id: 'export-1', status: 'downloaded', revision: 1, download_count: 1,
  line_count: 1, total_amount: '112.00', external_reference: null, report_reason: null,
  created_at: '2026-10-23', updated_at: '2026-10-23', reported_at: null,
};
const payload: documentsApi.ExtractionPayload = {
  doc_type: 'invoice', fields: { supplier: { name: { value: 'A Pharma' } }, line_items: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(connectorsApi.listConnectors).mockResolvedValue([connector]);
  vi.mocked(documentsApi.getTallyPreflight).mockResolvedValue({
    ready: true, issues: [], line_count: 1, line_total: '100.00',
    tax_total: '12.00', invoice_total: '112.00',
  });
  vi.mocked(documentsApi.getTallyExportStatus).mockResolvedValue(receipt);
  vi.mocked(documentsApi.reportTallyImport).mockResolvedValue({
    ...receipt, status: 'reported_imported', external_reference: 'PUR-18',
  });
});

describe('Tally purchase receipt', () => {
  test('an outstanding download cannot be offered again and requires an owner report', async () => {
    render(<TallyReadiness documentId="invoice-1" status="approved" payload={payload} />);
    expect(await screen.findByText(/XML revision 1 was prepared for download/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download Tally purchase XML' })).not.toBeInTheDocument();
    const imported = screen.getByRole('button', { name: 'Record reported import' });
    expect(imported).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Tally voucher reference'), { target: { value: 'PUR-18' } });
    fireEvent.change(screen.getByLabelText(/Stock lines in Tally/), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText(/Voucher total in Tally/), { target: { value: '112.00' } });
    fireEvent.click(screen.getByRole('checkbox'));
    expect(imported).toBeEnabled();
    fireEvent.click(imported);
    await waitFor(() => expect(documentsApi.reportTallyImport).toHaveBeenCalledWith(
      'invoice-1', 'tally-1', {
        outcome: 'imported', external_reference: 'PUR-18', imported_line_count: 1,
        imported_total: '112.00', rows_and_batches_checked: true,
      },
    ));
    expect(await screen.findByText(/Reported imported into Tally as PUR-18/)).toBeInTheDocument();
  });
});
