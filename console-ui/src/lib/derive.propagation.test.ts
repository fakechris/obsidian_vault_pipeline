import { describe, expect, it } from 'vitest';
import { propagationCredit } from './derive';

describe('propagationCredit', () => {
  it('counts link + credit as credited and leaves unread sources out of both', () => {
    expect(
      propagationCredit([
        { citation_type: 'exact_link' },
        { citation_type: 'credit' },
        { citation_type: 'inferred' },
        { citation_type: 'unknown' },
        {},
      ]),
    ).toEqual({ credited: 2, classified: 3 });
  });

  it('is zero-of-zero for an empty or missing timeline', () => {
    expect(propagationCredit([])).toEqual({ credited: 0, classified: 0 });
    expect(propagationCredit(undefined)).toEqual({ credited: 0, classified: 0 });
  });
});
