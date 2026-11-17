import { describe, expect, it } from 'vitest';
import {
  llmExtraOf,
  attentionGroupKey,
  attentionGroups,
  llmProfileSet,
  llmSetupSummary,
  profileIdFor,
  systemTabOf,
} from './derive';
import type { SourceRow } from './types';

const src = (status: string, last_reason?: string, sha = Math.random().toString()): SourceRow =>
  ({ sha256: sha, status, last_reason }) as SourceRow;

describe('System page helpers', () => {
  it('systemTabOf falls back to the overview', () => {
    expect(systemTabOf('settings')).toBe('settings');
    expect(systemTabOf('nope')).toBe('overview');
    expect(systemTabOf(null)).toBe('overview');
  });

  it('groups attention sources by cause (real vault reasons)', () => {
    expect(attentionGroupKey(src('needs_content'))).toBe('needs_content');
    expect(
      attentionGroupKey(
        src('blocked', 'grounded extraction/repair failed: provider error rate_limit_error: 已达到 Token Plan 用量上限'),
      ),
    ).toBe('quota');
    expect(
      attentionGroupKey(
        src('blocked', 'grounded extraction/repair failed: transport: send: error sending request for url'),
      ),
    ).toBe('network');
    expect(attentionGroupKey(src('blocked', 'truth-layer error: 0 units extracted'))).toBe('extraction');
    expect(attentionGroupKey(src('blocked', '0 reader cards produced from accepted units (pack written)'))).toBe(
      'extraction',
    );
    expect(attentionGroupKey(src('blocked', 'provider error api_error: input new'))).toBe('provider');
    expect(attentionGroupKey(src('blocked', 'something new'))).toBe('other');
  });

  it('orders groups by size and keeps members in input order', () => {
    const a = src('blocked', 'transport: x', 'a');
    const b = src('needs_content', undefined, 'b');
    const c = src('blocked', 'transport: y', 'c');
    const groups = attentionGroups([a, b, c]);
    expect(groups.map((g) => g.key)).toEqual(['network', 'needs_content']);
    expect(groups[0].sources.map((s) => s.sha256)).toEqual(['a', 'c']);
  });

  it('summarises a setup and defaults the endpoint per provider', () => {
    expect(llmSetupSummary({ OVP_LLM_PROVIDER: 'openai', OVP_LLM_MODEL: 'gpt-6-luna' })).toEqual({
      provider: 'openai',
      model: 'gpt-6-luna',
      endpoint: 'https://api.openai.com/v1/responses',
    });
    expect(llmSetupSummary({ ANTHROPIC_BASE_URL: 'https://x/v1/messages' }).provider).toBe('anthropic');
  });

  it('profile saves clear the other provider’s key and endpoint', () => {
    const set = llmProfileSet({
      provider: 'openai',
      baseUrl: '',
      model: 'gpt-6-luna',
      apiKey: 'sk-1',
      noProxy: false,
    });
    expect(set.OVP_LLM_PROVIDER).toBe('openai');
    expect(set.OPENAI_API_KEY).toBe('sk-1');
    expect(set.ANTHROPIC_API_KEY).toBe('');
    expect(set.ANTHROPIC_BASE_URL).toBe('');
    expect(set.OVP_LLM_NO_PROXY).toBe('');
    const ant = llmProfileSet({ provider: 'anthropic', baseUrl: 'u', model: 'm', apiKey: 'k', noProxy: true });
    expect([ant.OVP_LLM_PROVIDER, ant.ANTHROPIC_API_KEY, ant.OPENAI_API_KEY, ant.OVP_LLM_NO_PROXY]).toEqual([
      '',
      'k',
      '',
      '1',
    ]);
  });

  it('saving a setup keeps the LLM variables the form has no field for', () => {
    const live = { OVP_LLM_MODEL: 'm', OVP_LLM_MAX_TOKENS: '24000', OVP_LLM_TIMEOUT_SECS: '300', GITHUB_TOKEN: 'x' };
    const set = llmProfileSet({
      provider: 'anthropic',
      baseUrl: '',
      model: 'm',
      apiKey: 'k',
      noProxy: false,
      extra: llmExtraOf(live),
    });
    expect(set.OVP_LLM_MAX_TOKENS).toBe('24000');
    expect(set.OVP_LLM_TIMEOUT_SECS).toBe('300');
    expect(set.GITHUB_TOKEN).toBeUndefined();
  });

  it('derives unique profile ids from labels', () => {
    expect(profileIdFor('OpenAI gpt-6-luna', [])).toBe('openai-gpt-6-luna');
    expect(profileIdFor('MiniMax 中国', ['minimax'])).toBe('minimax-2');
    expect(profileIdFor('智谱', [])).toBe('profile');
    expect(profileIdFor('智谱', ['profile'])).toBe('profile-2');
  });
});
