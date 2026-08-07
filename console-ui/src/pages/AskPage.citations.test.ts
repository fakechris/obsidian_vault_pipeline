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
  claims: [
    { claim_id: 'agents-b001-3', claim_key: 'ck-abc', claim: 'The claim text as indexed' },
  ],
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

  it('resolves run-scoped claim_ids too — old transcripts cite them', () => {
    // Operator regression 2026-08-07: a re-crystallize renumbered claim_ids
    // and every pre-rerun chat lost its claim titles (lookup was key-only).
    const [c] = citationsFromAnswerText(
      'as shown [claim:agents-b001-3]',
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

describe('tolerant + legacy citation resolution', () => {
  const REAL_SHA =
    '979d7ea1381359b259a4d78a0b13a032f24272c77980b9539c3ec5d2cce4f9c1';
  const legacy = {
    sources: [
      { sha256: REAL_SHA, title: 'Digital Human Production' },
      {
        sha256: 'ab'.repeat(32),
        title: 'Working with evals (OpenAI API)',
        rel_path:
          '40-Resources/Reader/4b1d798e-2026-03-29_Working_with_evals_OpenAI_API________.md',
      },
    ],
    claims: [],
  };

  it('resolves a model-mangled sha when the head is intact and unique', () => {
    // The model elided the middle of the id (real: …b9539c3ec5d2cce4f9c1).
    const mangled = '979d7ea1381359b259a4d78a0b13a032f24272c77980b9539c9c1';
    const [c] = citationsFromAnswerText(
      `see [source:${mangled}]`,
      makeCitationTitleLookup(legacy),
    );
    expect(c.title).toBe('Digital Human Production');
    expect(c.link_target).toBe(`/library/${REAL_SHA}`);
  });

  it('refuses to guess when the head prefix is ambiguous', () => {
    const two = {
      sources: [
        { sha256: `${'a'.repeat(24)}${'0'.repeat(40)}`, title: 'One' },
        { sha256: `${'a'.repeat(24)}${'1'.repeat(40)}`, title: 'Two' },
      ],
      claims: [],
    };
    const [c] = citationsFromAnswerText(
      `see [source:${'a'.repeat(30)}]`,
      makeCitationTitleLookup(two),
    );
    expect(c.title).toMatch(/^source aaaa/);
    expect(c.link_target).toBeNull();
  });

  it('collapses doubled kind prefixes and resolves legacy card paths', () => {
    const [c] = citationsFromAnswerText(
      'per [card:card:40-Resources/Reader/4b1d798e-2026-03-29_Working_with_evals_OpenAI_API________:0]',
      makeCitationTitleLookup(legacy),
    );
    expect(c.id).toBe(
      'card:40-Resources/Reader/4b1d798e-2026-03-29_Working_with_evals_OpenAI_API________:0',
    );
    expect(c.title).toBe('Working with evals (OpenAI API)');
    expect(c.link_target).toBe(`/library/${'ab'.repeat(32)}`);
  });

  it('humanizes legacy unit paths not present in the index', () => {
    const [c] = citationsFromAnswerText(
      'see [unit:unit:40-Resources/Reader/024fa72c-2026-04-01_Agentic_RAG_notes____:u-034-0e0d496f]',
      makeCitationTitleLookup(legacy),
    );
    expect(c.title).toBe('Agentic RAG notes');
    expect(c.link_target).toBeNull();
  });

  it('keeps degenerate ellipsis markers honest', () => {
    const [c] = citationsFromAnswerText(
      'see [card:card:...:0]',
      makeCitationTitleLookup(legacy),
    );
    expect(c.title).toBe('card:...:0');
    expect(c.link_target).toBeNull();
  });
});
