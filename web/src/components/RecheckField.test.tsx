import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import RecheckField from './RecheckField';
import * as docsApi from '../api/documents';

vi.mock('../api/documents');

beforeEach(() => vi.resetAllMocks());

const found = {
  path: 'line_items[2].ptr', current: '81.68', found: '90.75', printed_label: 'RETAILER RATE',
  printed_text: '121.00 90.75 81.68', on_page: true, agrees: false,
  message: "The AI reads '90.75', printed as 'RETAILER RATE'.",
};

describe('RecheckField', () => {
  test('asks about the one field and uses the value found', async () => {
    vi.mocked(docsApi.recheckField).mockResolvedValue(found);
    const onUse = vi.fn();
    render(<RecheckField docId="d1" path="line_items[2].ptr" onUse={onUse} />);
    fireEvent.click(screen.getByText('Recheck'));
    await screen.findByText(/RETAILER RATE/);
    expect(docsApi.recheckField).toHaveBeenCalledWith('d1', 'line_items[2].ptr');
    fireEvent.click(screen.getByText('Use this'));
    expect(onUse).toHaveBeenCalledWith('90.75');
  });

  test('a value not on the bill is marked, and keeping mine changes nothing', async () => {
    vi.mocked(docsApi.recheckField).mockResolvedValue({ ...found, on_page: false, message: 'not printed on the bill' });
    const onUse = vi.fn();
    render(<RecheckField docId="d1" path="line_items[2].ptr" onUse={onUse} />);
    fireEvent.click(screen.getByText('Recheck'));
    await screen.findByText('Use anyway');
    fireEvent.click(screen.getByText('Keep mine'));
    expect(onUse).not.toHaveBeenCalled();
    expect(screen.getByText('Recheck')).toBeTruthy();
  });

  test('an agreeing answer offers nothing to use; an error is shown', async () => {
    vi.mocked(docsApi.recheckField).mockResolvedValueOnce({ ...found, agrees: true, message: 'The AI reads the same' });
    render(<RecheckField docId="d1" path="invoice.po_no" onUse={vi.fn()} />);
    fireEvent.click(screen.getByText('Recheck'));
    await screen.findByText('The AI reads the same');
    expect(screen.queryByText('Use this')).toBeNull();
    fireEvent.click(screen.getByText('Close'));

    vi.mocked(docsApi.recheckField).mockRejectedValueOnce({ response: { data: { detail: 'AI is busy' } } });
    fireEvent.click(screen.getByText('Recheck'));
    await waitFor(() => expect(screen.getByText('AI is busy')).toBeTruthy());
  });
});
