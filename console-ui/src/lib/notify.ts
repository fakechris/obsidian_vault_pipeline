/** Desktop + browser notifications for finished source-work queue items. */

import { isDesktopApp } from './desktopExternalLinks';
import type { SourceWorkQueueItem } from './api';

interface TauriGlobal {
  core?: {
    invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
  };
}

let permissionAsked = false;

/** Best-effort permission warm-up (browser Notification API). */
export function ensureNotifyPermission(): void {
  if (typeof window === 'undefined' || !('Notification' in window)) return;
  if (Notification.permission !== 'default' || permissionAsked) return;
  permissionAsked = true;
  void Notification.requestPermission().catch(() => {});
}

function itemLabel(item: SourceWorkQueueItem): string {
  const t = item.title?.trim();
  if (t) return t.length > 48 ? `${t.slice(0, 48)}…` : t;
  return item.sha256.slice(0, 12) + '…';
}

function itemBody(item: SourceWorkQueueItem): string {
  const parts: string[] = [];
  if (item.translate.wanted) {
    parts.push(
      item.translate.status === 'done'
        ? '中文 ✓'
        : item.translate.status === 'failed'
          ? '中文 ✗'
          : `中文 ${item.translate.status}`,
    );
  }
  if (item.summarize.wanted) {
    parts.push(
      item.summarize.status === 'done'
        ? '摘要 ✓'
        : item.summarize.status === 'failed'
          ? '摘要 ✗'
          : `摘要 ${item.summarize.status}`,
    );
  }
  return parts.join(' · ') || item.status;
}

/** Fire OS/browser notification for a finished queue item. */
export function notifyWorkItemDone(item: SourceWorkQueueItem): void {
  const title =
    item.status === 'done'
      ? 'OVP2 · work finished'
      : item.status === 'failed'
        ? 'OVP2 · work failed'
        : 'OVP2 · work ended';
  const body = `${itemLabel(item)}\n${itemBody(item)}`;

  // Desktop: Notification Center via Tauri command (WKWebView often blocks web Notification).
  if (isDesktopApp()) {
    const invoke = (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__?.core
      ?.invoke;
    if (invoke) {
      void invoke('desktop_notify', { title, body }).catch(() => {
        // fall through to web API
        webNotify(title, body);
      });
      return;
    }
  }
  webNotify(title, body);
}

function webNotify(title: string, body: string): void {
  if (typeof window === 'undefined' || !('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    try {
      new Notification(title, { body, tag: `ovp-work-${title}` });
    } catch {
      /* ignore */
    }
    return;
  }
  if (Notification.permission === 'default') {
    void Notification.requestPermission().then((p) => {
      if (p === 'granted') {
        try {
          new Notification(title, { body });
        } catch {
          /* ignore */
        }
      }
    });
  }
}
