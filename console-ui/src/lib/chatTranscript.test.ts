import { describe, expect, it } from 'vitest';
import {
  citationsInOrder,
  groundCitesOnSource,
  parseChatFocus,
  citeLinkTarget,
  displayUserQuestion,
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

describe('displayUserQuestion', () => {
  it('strips focus pack and keeps only the human question', () => {
    const packed =
      '[FOCUS CONTEXT — source-grounded chat]\n## Body\nlong text\n\n[USER QUESTION]\n详细讲讲这个金融产品';
    expect(displayUserQuestion(packed)).toBe('详细讲讲这个金融产品');
  });

  it('passes through plain questions unchanged', () => {
    expect(displayUserQuestion('What is agent memory?')).toBe(
      'What is agent memory?',
    );
  });
});

describe('focused-session replay grounding', () => {
  const SHA = '816380d615c63038a3aae393ecaa8a2c624045a5a03200111d46e0f860183519';

  it('parseChatFocus reads the header markers', () => {
    const md = `# Ask — ts\n<!-- ovp:focus_source=${SHA} -->\n<!-- ovp:focus_title=事关7709 -->\n\n**Q:** hi`;
    expect(parseChatFocus(md)).toEqual({ sha: SHA, theme: null });
    expect(parseChatFocus('# Ask\n<!-- ovp:focus_theme=Agent Memory -->\n')).toEqual({
      sha: null,
      theme: 'Agent Memory',
    });
    expect(parseChatFocus('# Ask\n\n**Q:** hi')).toEqual({ sha: null, theme: null });
  });

  it('groundCitesOnSource sends units/cards to the memory tab, keeps links', () => {
    // Operator regression 2026-08-07: /ask/chat/src-… showed "No detail
    // page" for every unit — the focus sha grounds them like the live dock.
    const out = groundCitesOnSource(
      [
        { id: 'unit:u-004-723d01f2', kind: 'unit', link_target: null },
        { id: `source:${SHA}`, kind: 'source', link_target: null },
        { id: 'claim:ck-x', kind: 'claim', link_target: '/knowledge#ck-x' },
      ],
      SHA,
    );
    expect(out[0].link_target).toBe(`/library/${SHA}?tab=memory`);
    expect(out[1].link_target).toBe(`/library/${SHA}`);
    expect(out[2].link_target).toBe('/knowledge#ck-x');
  });
});
