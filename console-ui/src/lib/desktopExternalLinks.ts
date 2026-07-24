/** Desktop-app external-link bridge.
 *
 * The portal is shared: in a normal browser, `<a target="_blank">` and
 * `window.open` open external article URLs fine. Inside the Tauri desktop app
 * the WKWebView silently DROPS those — no browser pops, no in-app browser — so
 * a source's original article, citations, etc. can't be opened. Route external
 * http(s) links through the app's `open_external` command (system browser).
 *
 * Feature-detected on `window.__TAURI__`, so this is a no-op in the browser
 * (native `target="_blank"` keeps working there). Same-origin links (React
 * Router `<Link>`, internal `/library/...`) are left untouched. */

interface TauriGlobal {
  core?: { invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
}

export function installDesktopExternalLinks(): void {
  const tauri = (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__;
  const invoke = tauri?.core?.invoke;
  if (!invoke) return; // plain browser — nothing to bridge

  const openExternal = (url: string) => {
    void invoke('open_external', { url }).catch(() => {
      /* best-effort; a failed open must not throw into the click handler */
    });
  };

  // Capture phase so we decide before React Router's root listener — but we
  // only act on EXTERNAL links, leaving internal navigation to the router.
  document.addEventListener(
    'click',
    (e) => {
      if (e.defaultPrevented || e.button !== 0) return;
      const el = e.target as HTMLElement | null;
      const anchor = el?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = anchor.href; // absolute, browser-resolved
      if (/^https?:\/\//i.test(href) && !href.startsWith(location.origin)) {
        e.preventDefault();
        openExternal(href);
      }
    },
    true,
  );

  // Some surfaces (e.g. the terrain) call window.open directly. Route external
  // targets to the system browser; leave internal ones to the default (the app
  // navigates in-window).
  const nativeOpen = window.open.bind(window);
  window.open = ((url?: string | URL, ...rest: unknown[]) => {
    const str = typeof url === 'string' ? url : url?.toString() ?? '';
    if (/^https?:\/\//i.test(str) && !str.startsWith(location.origin)) {
      openExternal(str);
      return null;
    }
    return (nativeOpen as (u?: string | URL, ...a: unknown[]) => Window | null)(url, ...rest);
  }) as typeof window.open;
}
