// M22 — anchored inquiry drawer client.
//
// Hijacks every `.ask-about-this` link on the page and opens the
// drawer instead of navigating to /chat.  Posts messages to
// /chat/drawer/message, renders the JSON response, and exposes
// three explicit-decision actions (Save / Absorb / Discard).
//
// Progressive enhancement: if this script fails to load, the
// `<a href="/chat?...">` element still navigates to the M21b
// full-page chat surface — operators don't lose the feature.

(function () {
  "use strict";

  const DRAWER_ID = "ovp-chat-drawer";

  // localStorage keys.  Drafts are scoped to the anchor so two
  // different note pages keep independent drafts; the bare
  // `standalone` key covers an unanchored drawer.  Chat-id is
  // also persisted per-anchor so closing and reopening the
  // drawer on the same artifact resumes the same session.
  const DRAFT_KEY_PREFIX = "ovp-chat-draft:";
  const SESSION_KEY_PREFIX = "ovp-chat-session:";

  function anchorKey(kind, ref) {
    return (kind || "standalone") + ":" + (ref || "");
  }

  function readLS(prefix, key) {
    try {
      return window.localStorage.getItem(prefix + key) || "";
    } catch (e) {
      return "";
    }
  }

  function writeLS(prefix, key, value) {
    try {
      if (value) {
        window.localStorage.setItem(prefix + key, value);
      } else {
        window.localStorage.removeItem(prefix + key);
      }
    } catch (e) {
      /* localStorage full or disabled — silently degrade */
    }
  }

  function $drawer() {
    return document.getElementById(DRAWER_ID);
  }

  function $csrfToken() {
    // The CSRF cookie is HttpOnly — JS cannot read it.  The
    // server renders the same token into <meta name="csrf-token">
    // (see _write_html in ui_server.py); use that instead.
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  // --- State (one drawer per page) ----------------------------

  const state = {
    chatId: null,
    anchorKind: "standalone",
    anchorRef: "",
    anchorTitle: "",
    profile: "balanced",
    sending: false,
  };

  function resetState() {
    state.chatId = null;
    state.anchorKind = "standalone";
    state.anchorRef = "";
    state.anchorTitle = "";
    state.profile = "balanced";
    state.sending = false;
  }

  // --- Open / close --------------------------------------------

  function openDrawer(opts) {
    const drawer = $drawer();
    if (!drawer) return;
    resetState();
    state.anchorKind = opts.anchorKind || "standalone";
    state.anchorRef = opts.anchorRef || "";
    state.anchorTitle = opts.anchorTitle || "";

    const key = anchorKey(state.anchorKind, state.anchorRef);

    // Resume the prior session for this anchor if one was started
    // and not Save/Discard-ed.  The transcript itself isn't
    // rehydrated client-side (would need a server fetch); the
    // chat_id is enough to continue the conversation server-side.
    const priorChatId = readLS(SESSION_KEY_PREFIX, key);
    state.chatId = priorChatId || null;

    const kindBadge = drawer.querySelector("[data-drawer-anchor-kind]");
    const titleEl = drawer.querySelector("[data-drawer-anchor-title]");
    if (kindBadge) kindBadge.textContent = state.anchorKind;
    if (titleEl) {
      titleEl.textContent = state.anchorTitle || state.anchorRef || "Standalone";
    }

    const transcript = drawer.querySelector("[data-drawer-transcript]");
    if (transcript) {
      transcript.innerHTML =
        "<p class='muted small'>Ask about this artifact — the answer is rebuilt from current vault state every turn.</p>";
      transcript.dataset.empty = "true";
    }
    const actions = drawer.querySelector("[data-drawer-actions]");
    if (actions) actions.hidden = !state.chatId;

    clearStatus();

    // Restore the draft for this anchor (if any).  Survives page
    // reloads, drawer closes, and CSRF / network errors.
    const textarea = drawer.querySelector("[data-drawer-message]");
    if (textarea) {
      const draft = readLS(DRAFT_KEY_PREFIX, key);
      textarea.value = draft;
      if (draft) {
        showStatus("Restored your unsent draft.");
      }
    }

    drawer.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    // Allow the browser to apply `hidden=false` before the transition class.
    requestAnimationFrame(function () {
      drawer.classList.add("is-open");
      if (textarea) textarea.focus();
    });
  }

  function closeDrawer() {
    const drawer = $drawer();
    if (!drawer) return;
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
    // Match the panel transition before fully hiding so the
    // slide-out animation is visible.
    setTimeout(function () {
      if (!drawer.classList.contains("is-open")) {
        drawer.hidden = true;
      }
    }, 240);
  }

  // --- Status banner -------------------------------------------

  function showStatus(text, opts) {
    const drawer = $drawer();
    if (!drawer) return;
    const el = drawer.querySelector("[data-drawer-status]");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    el.classList.toggle("is-error", !!(opts && opts.error));
  }

  function clearStatus() {
    const drawer = $drawer();
    if (!drawer) return;
    const el = drawer.querySelector("[data-drawer-status]");
    if (el) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("is-error");
    }
  }

  // --- Transcript injection ------------------------------------

  function appendTranscript(html) {
    const drawer = $drawer();
    if (!drawer) return;
    const transcript = drawer.querySelector("[data-drawer-transcript]");
    if (!transcript) return;
    // First message: replace the empty-state hint.
    if (transcript.dataset.empty !== "false") {
      transcript.innerHTML = "";
      transcript.dataset.empty = "false";
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    while (wrapper.firstChild) transcript.appendChild(wrapper.firstChild);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function showActions() {
    const drawer = $drawer();
    if (!drawer) return;
    const actions = drawer.querySelector("[data-drawer-actions]");
    if (actions) actions.hidden = false;
  }

  // --- Network -------------------------------------------------

  function postForm(url, payload) {
    const body = new URLSearchParams();
    const csrf = $csrfToken();
    if (csrf) body.set("_csrf", csrf);
    Object.keys(payload).forEach(function (key) {
      const value = payload[key];
      if (value !== undefined && value !== null && value !== "") {
        body.set(key, String(value));
      }
    });
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/json",
      },
      credentials: "same-origin",
      body: body.toString(),
    });
  }

  async function sendMessage() {
    if (state.sending) return;
    const drawer = $drawer();
    if (!drawer) return;
    const textarea = drawer.querySelector("[data-drawer-message]");
    if (!textarea) return;
    const message = (textarea.value || "").trim();
    if (!message) {
      textarea.focus();
      return;
    }

    state.sending = true;
    clearStatus();
    showStatus("Sending…");
    const sendBtn = drawer.querySelector("[data-drawer-send]");
    if (sendBtn) sendBtn.disabled = true;

    try {
      const resp = await postForm("/chat/drawer/message", {
        chat_id: state.chatId || "",
        anchor: state.anchorRef ? state.anchorKind + ":" + state.anchorRef : "",
        anchor_title: state.anchorTitle,
        profile: state.profile,
        message: message,
      });
      const data = await resp.json().catch(function () {
        return { error: "invalid_json" };
      });
      if (!resp.ok || data.error) {
        const reason = (data && (data.detail || data.error)) || "Send failed";
        showStatus(reason, { error: true });
        return;
      }
      state.chatId = data.chat_id || state.chatId;
      // Persist chat_id so closing+reopening the drawer (or even a
      // page reload) keeps the same session.  Cleared in runAction
      // when the operator picks Save / Absorb / Discard.
      writeLS(
        SESSION_KEY_PREFIX,
        anchorKey(state.anchorKind, state.anchorRef),
        state.chatId || "",
      );
      // Success: drop the saved draft so the next open starts empty.
      writeLS(DRAFT_KEY_PREFIX, anchorKey(state.anchorKind, state.anchorRef), "");
      if (data.user_html) appendTranscript(data.user_html);
      if (data.assistant_html) appendTranscript(data.assistant_html);
      textarea.value = "";
      textarea.style.height = "";
      showActions();
      clearStatus();
    } catch (err) {
      showStatus("Network error: " + (err && err.message ? err.message : err), {
        error: true,
      });
    } finally {
      state.sending = false;
      if (sendBtn) sendBtn.disabled = false;
      textarea.focus();
    }
  }

  async function runAction(actionName) {
    if (!state.chatId) return;
    const drawer = $drawer();
    if (!drawer) return;
    const actionBtns = drawer.querySelectorAll("[data-drawer-action]");
    actionBtns.forEach(function (b) {
      b.disabled = true;
    });
    showStatus(actionName === "discard" ? "Discarding…" : "Saving…");

    try {
      const resp = await postForm("/chat/drawer/" + actionName, {
        chat_id: state.chatId,
      });
      const data = await resp.json().catch(function () {
        return { error: "invalid_json" };
      });
      if (!resp.ok || data.error) {
        const reason = (data && (data.detail || data.error)) || actionName + " failed";
        showStatus(reason, { error: true });
        actionBtns.forEach(function (b) {
          b.disabled = false;
        });
        return;
      }
      // Any decisive action ends this session — clear the
      // resume marker so the next open of this anchor starts
      // a fresh chat.  Drafts already cleared on send.
      const key = anchorKey(state.anchorKind, state.anchorRef);
      writeLS(SESSION_KEY_PREFIX, key, "");
      writeLS(DRAFT_KEY_PREFIX, key, "");

      if (actionName === "discard") {
        showStatus("Discarded.");
        state.chatId = null;
        // Close after a tick so the operator sees the confirmation.
        setTimeout(closeDrawer, 600);
      } else {
        // Save / Absorb: the file is now indexed.  Re-enable Save
        // & Absorb so the operator can re-trigger if they like
        // (idempotent on the server), but DISABLE Discard — a
        // follow-up Discard click on an indexed session would
        // try to delete a saved transcript.  Server also rejects
        // discard for indexed sessions, but disabling the button
        // is the better UX cue.
        actionBtns.forEach(function (b) {
          if (b.dataset.drawerAction === "discard") {
            b.disabled = true;
            b.title = "Already saved — open /chats to manage.";
          } else {
            b.disabled = false;
          }
        });
        if (actionName === "save") {
          showStatus("Saved to /chats.");
        } else if (actionName === "absorb") {
          showStatus("Queued for absorb.");
        }
      }
    } catch (err) {
      showStatus("Network error: " + (err && err.message ? err.message : err), {
        error: true,
      });
      actionBtns.forEach(function (b) {
        b.disabled = false;
      });
    }
  }

  // --- Wiring --------------------------------------------------

  function bindOpenTriggers() {
    document.querySelectorAll(".ask-about-this").forEach(function (el) {
      if (el.dataset.drawerBound === "1") return;
      el.dataset.drawerBound = "1";
      el.addEventListener("click", function (ev) {
        const kind = el.dataset.anchorKind;
        const ref = el.dataset.anchorRef;
        const title = el.dataset.anchorTitle;
        if (!kind && !ref && !title) {
          // No data attrs — let the link fall through to /chat
          // (no-JS fallback).
          return;
        }
        ev.preventDefault();
        openDrawer({
          anchorKind: kind || "standalone",
          anchorRef: ref || "",
          anchorTitle: title || "",
        });
      });
    });
  }

  function bindDrawerHandlers() {
    const drawer = $drawer();
    if (!drawer) return;

    drawer.addEventListener("click", function (ev) {
      const target = ev.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.matches("[data-drawer-close]")) {
        ev.preventDefault();
        closeDrawer();
        return;
      }
      const action = target.dataset.drawerAction;
      if (action) {
        ev.preventDefault();
        runAction(action);
      }
    });

    const composer = drawer.querySelector("[data-drawer-composer]");
    if (composer) {
      composer.addEventListener("submit", function (ev) {
        ev.preventDefault();
        sendMessage();
      });
    }

    // Cmd/Ctrl+Enter to send from the textarea.
    const textarea = drawer.querySelector("[data-drawer-message]");
    if (textarea) {
      textarea.addEventListener("keydown", function (ev) {
        if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
          ev.preventDefault();
          sendMessage();
        }
      });
      // Persist every keystroke so a crash / reload / error
      // can't eat the draft.  Keyed by anchor so two artifacts
      // keep independent drafts.
      textarea.addEventListener("input", function () {
        writeLS(
          DRAFT_KEY_PREFIX,
          anchorKey(state.anchorKind, state.anchorRef),
          textarea.value,
        );
      });
    }

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !drawer.hidden) {
        closeDrawer();
      }
    });
  }

  // --- Boot ----------------------------------------------------

  function boot() {
    bindOpenTriggers();
    bindDrawerHandlers();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
