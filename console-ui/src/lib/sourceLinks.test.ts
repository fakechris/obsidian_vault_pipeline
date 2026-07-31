import { describe, expect, it } from 'vitest';
import {
  companionLinks,
  isPrimarilyEnglish,
  parseArxiv,
  parseGithub,
} from './sourceLinks';

describe('parseGithub', () => {
  it('parses https repo URLs and strips .git', () => {
    expect(parseGithub('https://github.com/foo/bar')).toEqual({
      owner: 'foo',
      repo: 'bar',
    });
    expect(parseGithub('https://github.com/foo/bar.git')).toEqual({
      owner: 'foo',
      repo: 'bar',
    });
  });

  it('parses entity ids', () => {
    expect(parseGithub('github:AI4Finance-Foundation/FinGPT')).toEqual({
      owner: 'AI4Finance-Foundation',
      repo: 'FinGPT',
    });
  });

  it('rejects non-repo paths', () => {
    expect(parseGithub('https://github.com/foo/bar/issues/1')).toBeNull();
    expect(parseGithub('https://example.com/x')).toBeNull();
  });
});

describe('parseArxiv', () => {
  it('parses abs / pdf / entity / bare', () => {
    expect(parseArxiv('https://arxiv.org/abs/2504.19413')).toEqual({
      id: '2504.19413',
    });
    expect(parseArxiv('https://arxiv.org/pdf/2504.19413v2.pdf')).toEqual({
      id: '2504.19413',
    });
    expect(parseArxiv('arxiv:2504.19413v1')).toEqual({ id: '2504.19413' });
    expect(parseArxiv('2504.19413')).toEqual({ id: '2504.19413' });
  });
});

describe('companionLinks', () => {
  it('builds GitHub companion set', () => {
    const links = companionLinks('https://github.com/foo/bar');
    expect(links.map((l) => l.id)).toEqual(['github', 'zread', 'deepwiki']);
    expect(links.find((l) => l.id === 'zread')?.href).toBe(
      'https://zread.ai/github/foo/bar',
    );
    expect(links.find((l) => l.id === 'deepwiki')?.href).toBe(
      'https://deepwiki.com/foo/bar',
    );
  });

  it('builds arXiv companion set from entity when URL is non-arXiv', () => {
    const links = companionLinks('https://example.com/blog', [
      'arxiv:2504.19413',
    ]);
    expect(links.map((l) => l.id)).toEqual(['arxiv', 'ar5iv', 'alphaxiv']);
    expect(links.find((l) => l.id === 'ar5iv')?.href).toContain(
      'ar5iv.labs.arxiv.org/html/2504.19413',
    );
    expect(links.find((l) => l.id === 'alphaxiv')?.href).toContain(
      'alphaxiv.org/abs/2504.19413',
    );
  });

  it('dedupes github from url + entity', () => {
    const links = companionLinks('https://github.com/a/b', ['github:a/b']);
    expect(links.filter((l) => l.kind === 'github')).toHaveLength(3);
  });
});

describe('isPrimarilyEnglish', () => {
  it('detects English prose', () => {
    const en =
      'The harness is all you need. Evaluation harnesses encode the real work ' +
      'of shipping reliable agent systems in production environments today.';
    expect(isPrimarilyEnglish(en)).toBe(true);
  });

  it('rejects CJK-heavy notes', () => {
    const zh =
      '这是一篇关于大模型评估与生产落地的深度笔记。我们讨论了评测集、 harness 与可靠性之间的关系，以及团队在真实业务里如何迭代。';
    expect(isPrimarilyEnglish(zh)).toBe(false);
  });

  it('rejects short bodies', () => {
    expect(isPrimarilyEnglish('Hello world')).toBe(false);
  });
});
