import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import ShareCorrections from './ShareCorrections';
import * as authApi from '../api/auth';

vi.mock('../api/auth');

beforeEach(() => vi.resetAllMocks());

describe('ShareCorrections', () => {
  test('shows the owner consent and saves a change', async () => {
    vi.mocked(authApi.getShop).mockResolvedValue({ name: 'S', gstins: [], stated: [], share_corrections: false });
    vi.mocked(authApi.setShareCorrections).mockResolvedValue(true);
    render(<ShareCorrections />);
    const box = (await screen.findByLabelText('Share corrections')) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    await waitFor(() => expect(authApi.setShareCorrections).toHaveBeenCalledWith(true));
    await waitFor(() => expect((screen.getByLabelText('Share corrections') as HTMLInputElement).checked).toBe(true));
  });
});
