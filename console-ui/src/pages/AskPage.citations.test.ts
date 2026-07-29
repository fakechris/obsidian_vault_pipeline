/** Replay-path citation titles: saved chats and recovered turns build
 * citations from answer text alone (no server receipts). The right rail
 * must resolve bare [source:<sha>] / [claim:<key>] markers to REAL index
 * titles — the operator regression: "the rail is back to bare hashes". */
import { describe, expect, it } from 'vitest';
import {
  citationsFromAnswerText,
  makeCitationTitleLookup,
} from './AskPage';

const SHA = '6dc988d1d27614055ed13de8010eddbd44f107635061e25c7a6a5647f887a0a3';
const model = {
  sources: [{ sha256: SHA, title: 'The Actual Article Title' }],
  claims: [{ claim_key: 'ck-abc', claim: 'The claim text as indexed' }],
};

describe('citationsFromAnswerText with an index lookup', () => {
  it('resolves bare source shas to index titles', () => {
    const [c] = citationsFromAnswerText(
      `see [source:${SHA}]`,
      makeCitationTitleLookup(model),
    );
    expect(c.title).toBe('The Actual Article Title');
    expect(c.id).toBe(`source:${SHA}`);
    expect(c.link_target).toBe(`/library/${SHA}`);
  });

  it('resolves claim keys to claim text', () => {
    const [c] = citationsFromAnswerText(
      'as shown [claim:ck-abc]',
      makeCitationTitleLookup(model),
    );
    expect(c.title).toBe('The claim text as indexed');
  });

  it('prefers the index title over a model-decorated one', () => {
    const [c] = citationsFromAnswerText(
      `see [source:${SHA} Slightly Off Model Title]`,
      makeCitationTitleLookup(model),
    );
    expect(c.title).toBe('The Actual Article Title');
  });

  it('falls back honestly for ids the index does not know', () => {
    const [c] = citationsFromAnswerText(
      `see [source:${SHA}]`,
      makeCitationTitleLookup({ sources: [], claims: [] }),
    );
    expect(c.title).toMatch(/^source 6dc988d1d27/);
  });

  it('null model degrades to the old behavior', () => {
    const [c] = citationsFromAnswerText(`see [source:${SHA}]`);
    expect(c.title).toMatch(/^source 6dc988d1d27/);
  });
});
