/** IME composition guards for keyboard handlers.
 *
 * While a CJK (or other) input method is composing a candidate, Enter
 * confirms the candidate — it must NOT submit a form, open a search hit,
 * or send a chat message. Use these helpers at the top of every onKeyDown
 * that treats Enter (or other keys) as an action.
 */

/** True while the user is composing via an IME (or WebView legacy 229). */
export function isImeComposing(
  e: Pick<KeyboardEvent, 'isComposing' | 'keyCode'> | { isComposing?: boolean; keyCode?: number },
): boolean {
  // React synthetic events put the live flags on `nativeEvent`; callers can
  // pass either the native event or a React KeyboardEvent.nativeEvent.
  return Boolean(e.isComposing) || e.keyCode === 229;
}

/** React KeyboardEvent: read composition state from the underlying native event. */
export function isReactImeComposing(
  e: { nativeEvent: { isComposing: boolean; keyCode: number } },
): boolean {
  return isImeComposing(e.nativeEvent);
}
