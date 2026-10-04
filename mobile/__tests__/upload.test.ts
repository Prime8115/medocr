import { refusalMessage } from '../src/lib/upload';

describe('refusalMessage', () => {
  it("passes on the server's reason for refusing a file", () => {
    const err = { response: { status: 400, data: { detail: 'This PDF is password-protected.' } } };
    expect(refusalMessage(err)).toBe('This PDF is password-protected.');
  });

  it('a refusal without a reason still reads as one', () => {
    expect(refusalMessage({ response: { status: 413, data: {} } })).toBe('This file could not be uploaded.');
  });

  it('a dropped connection is not a refusal - it goes to the offline queue', () => {
    expect(refusalMessage(new Error('Network Error'))).toBeNull();
    expect(refusalMessage({ request: {} })).toBeNull();
  });

  it('a server error is not a refusal of the file', () => {
    expect(refusalMessage({ response: { status: 503, data: { detail: 'down' } } })).toBeNull();
  });
});
