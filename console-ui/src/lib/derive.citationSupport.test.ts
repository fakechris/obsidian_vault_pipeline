import { describe, expect, it } from 'vitest';
import { citationSupportMark } from './derive';

describe('citationSupportMark (INV-1422)', () => {
  it('marks only confident non-support, with its kind and percentage', () => {
    expect(citationSupportMark({ support: { support: 'says_nothing', confidence: 0.951, flagged: true } })).toEqual({
      kind: 'says_nothing',
      pct: 95,
    });
    expect(citationSupportMark({ support: { support: 'contradicts', confidence: 0.8, flagged: true } })).toEqual({
      kind: 'contradicts',
      pct: 80,
    });
  });

  it('shows nothing for support, low confidence, or no answer', () => {
    expect(citationSupportMark({ support: { support: 'supports', confidence: 0.99, flagged: false } })).toBeNull();
    expect(citationSupportMark({ support: { support: 'contradicts', confidence: 0.6, flagged: false } })).toBeNull();
    expect(citationSupportMark({})).toBeNull();
  });

  it('trusts the server flag, never re-deriving a mark the server did not set', () => {
    expect(citationSupportMark({ support: { support: 'says_nothing', confidence: 0.99, flagged: false } })).toBeNull();
  });
});
