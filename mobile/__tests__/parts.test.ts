import { kindLabel, otherParts, pagesLabel, partTitle } from '../src/lib/parts';

const two = {
  id: 'doc_a',
  part_no: 1,
  part_count: 2,
  part_pages: '1',
  parts: [
    { id: 'doc_a', part_no: 1, pages: '1', status: 'needs_review', number: '8510945928', total: '3893.00' },
    { id: 'doc_b', part_no: 2, pages: '2-7', status: 'needs_review', number: '8510946051', total: '57637.00' },
  ],
};

describe('parts of one PDF', () => {
  test('says which bill of the file is open', () => {
    expect(partTitle(two)).toBe('Bill 1 of 2 in this PDF · page 1');
    expect(partTitle({ ...two, part_no: 2, part_pages: '2-7' })).toBe('Bill 2 of 2 in this PDF · pages 2-7');
  });

  test('lists the other bills, not the open one', () => {
    expect(otherParts(two)).toEqual([{ id: 'doc_b', label: 'Bill 2 · #8510946051 · pages 2-7 · ₹57,637.00' }]);
  });

  test('a bill still being read says so', () => {
    const reading = { ...two, parts: [two.parts[0], { id: 'doc_b', part_no: 2, pages: '2-7', status: 'processing' }] };
    expect(otherParts(reading)[0].label).toBe('Bill 2 · pages 2-7 · reading…');
  });

  test('a file of one bill has no parts', () => {
    expect(partTitle({ id: 'doc_a' })).toBeNull();
    expect(otherParts({ id: 'doc_a' })).toEqual([]);
  });

  test('names what the bill is, and counts pages in good English', () => {
    expect(kindLabel('credit_note')).toBe('Credit note');
    expect(kindLabel('invoice')).toBeNull();
    expect(kindLabel(undefined)).toBeNull();
    expect(pagesLabel(1)).toBe('1 page');
    expect(pagesLabel(6)).toBe('6 pages');
  });
});
