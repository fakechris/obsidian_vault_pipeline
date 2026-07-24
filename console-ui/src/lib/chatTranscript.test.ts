import { describe, expect, it } from 'vitest';
import {
  citationsInOrder,
  citeLinkTarget,
  normalizeCiteToken,
  parseChatTranscript,
} from './chatTranscript';

const SAMPLE = `# Ask — 1784863896

**Q:** What is agent memory?

**A:** Agent memory is durable state [claim:ck-abcd1234].

---

## Evidence

- [claim:ck-abcd1234] durable claim

## Verification

cited 1 / verified 1

Context hits: 3

---

**Q:** What about that claim?

**A:** It rests on the unit quote [unit:unit:40-Resources/Reader/x:u-1].

---

## Evidence

- unit row

## Verification

cited 1 / verified 1

Context hits: 2
`;

describe('parseChatTranscript', () => {
  it('extracts multi-turn Q/A and drops evidence dumps', () => {
    const turns = parseChatTranscript(SAMPLE);
    expect(turns).toHaveLength(2);
    expect(turns[0].question).toBe('What is agent memory?');
    expect(turns[0].answer).toContain('Agent memory is durable state');
    expect(turns[0].answer).not.toContain('## Evidence');
    expect(turns[1].question).toBe('What about that claim?');
    expect(turns[1].answer).toContain('unit quote');
  });

  it('returns empty for non-transcript markdown', () => {
    expect(parseChatTranscript('# Hello\n\nJust a note.')).toEqual([]);
  });
});

describe('citationsInOrder', () => {
  it('normalizes bare ck- keys and preserves order', () => {
    const ids = citationsInOrder(
      'See [ck-aaaa] then [claim:ck-bbbb] and again [ck-aaaa].',
    );
    expect(ids).toEqual(['claim:ck-aaaa', 'claim:ck-bbbb']);
  });
});

describe('cite helpers', () => {
  it('normalizes and links claims', () => {
    expect(normalizeCiteToken('ck-x')).toBe('claim:ck-x');
    expect(citeLinkTarget('claim:ck-x')).toBe('/knowledge#ck-x');
    expect(citeLinkTarget('unit:u-1')).toBeNull();
  });
});
