import { describe, expect, it } from 'vitest';
import { indexHealth } from './derive';

/** Truth table for the stage-4 repair banner (pure, node env). */
describe('indexHealth', () => {
  it('is ok when sqlite loads cleanly', () => {
    expect(indexHealth(null, false)).toBe('ok');
    expect(indexHealth(undefined, false)).toBe('ok');
  });

  it('raises the banner only on a RECORDED sqlite failure', () => {
    // serving_backend === 'json' alone is legitimate (fresh JSON right
    // after a tag-curation rebuild) — the banner keys on the error.
    expect(indexHealth('disk I/O error', false)).toBe('error');
  });

  it('an in-flight rebuild outranks the error (operator already acted)', () => {
    expect(indexHealth('disk I/O error', true)).toBe('rebuilding');
    expect(indexHealth(null, true)).toBe('rebuilding');
  });
});
