import { describe, expect, it } from 'vitest';
import { isImeComposing, isReactImeComposing } from './ime';

describe('isImeComposing', () => {
  it('is true while composing', () => {
    expect(isImeComposing({ isComposing: true, keyCode: 13 })).toBe(true);
  });

  it('is true for legacy composition keyCode 229', () => {
    expect(isImeComposing({ isComposing: false, keyCode: 229 })).toBe(true);
  });

  it('is false for a normal Enter after composition ends', () => {
    expect(isImeComposing({ isComposing: false, keyCode: 13 })).toBe(false);
  });
});

describe('isReactImeComposing', () => {
  it('reads nativeEvent', () => {
    expect(
      isReactImeComposing({
        nativeEvent: { isComposing: true, keyCode: 13 },
      }),
    ).toBe(true);
    expect(
      isReactImeComposing({
        nativeEvent: { isComposing: false, keyCode: 13 },
      }),
    ).toBe(false);
  });
});
