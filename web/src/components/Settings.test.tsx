import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import Settings from './Settings';
import * as connectorsApi from '../api/connectors';

vi.mock('../api/connectors');

const webhook: connectorsApi.Connector = {
  id: 'c1',
  type: 'webhook',
  name: 'My Hook',
  config: { url: 'https://example.com/hook' },
  enabled: true,
  has_secret: true,
  created_at: '2026-07-27',
};

beforeEach(() => {
  vi.mocked(connectorsApi.listConnectors).mockResolvedValue([webhook]);
  vi.mocked(connectorsApi.testConnector).mockResolvedValue({ status: 'success', ok: true, attempts: 1, response_body: 'ok' });
  vi.mocked(connectorsApi.createConnector).mockResolvedValue({ ...webhook, id: 'c2', name: 'New' });
});

describe('Settings', () => {
  test('lists existing connectors', async () => {
    render(<Settings />);
    expect(await screen.findByText('My Hook')).toBeInTheDocument();
    expect(screen.getByText(/Webhook/)).toBeInTheDocument();
  });

  test('Test button runs a connector test round-trip', async () => {
    render(<Settings />);
    await screen.findByText('My Hook');
    fireEvent.click(screen.getByText('Test'));
    await waitFor(() => expect(connectorsApi.testConnector).toHaveBeenCalledWith('c1'));
    expect(await screen.findByText(/✓ success/)).toBeInTheDocument();
  });

  test('creating a connector calls the API', async () => {
    render(<Settings />);
    await screen.findByText('My Hook');
    fireEvent.click(screen.getByText(/add connector/i));
    // Name is the first textbox; URL (webhook default) is the second.
    const inputs = screen.getAllByRole('textbox');
    fireEvent.change(inputs[0], { target: { value: 'Second Hook' } });
    fireEvent.change(inputs[1], { target: { value: 'https://x.test/hook' } });
    fireEvent.click(screen.getByText('Create'));
    await waitFor(() => expect(connectorsApi.createConnector).toHaveBeenCalled());
  });
});
