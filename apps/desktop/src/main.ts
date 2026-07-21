// Boot splash: ask the Rust backend for the boot state and either navigate the
// window to the in-process portal server, or run first-run vault onboarding.
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

type Boot =
  | { kind: "ready"; url: string }
  | { kind: "need_vault" }
  | { kind: "error"; message: string };

const msg = document.getElementById("msg")!;
const spinner = document.getElementById("spinner")!;
const onboard = document.getElementById("onboard")!;
const pick = document.getElementById("pick") as HTMLButtonElement;

function showStarting(text: string) {
  spinner.classList.remove("hidden");
  onboard.classList.add("hidden");
  msg.textContent = text;
}

function goto(url: string) {
  msg.textContent = "Opening portal…";
  // Same webview, normal navigation to the loopback server → the real portal.
  window.location.replace(url);
}

function showOnboard(text: string) {
  spinner.classList.add("hidden");
  onboard.classList.remove("hidden");
  msg.textContent = text;
  void renderRecentVaults();
}

/** Obsidian-style quick pick: one click into a known vault — no folder
 * dialog (and none of its slow panel-service spin-up) involved. */
async function renderRecentVaults() {
  const recent = document.getElementById("recent")!;
  recent.innerHTML = "";
  let vaults: string[] = [];
  try {
    vaults = (await invoke("known_vaults")) as string[];
  } catch {
    return; // older backend — just keep the picker button
  }
  for (const v of vaults) {
    const name = v.split("/").filter(Boolean).pop() ?? v;
    const b = document.createElement("button");
    b.textContent = `Open ${name}`;
    const path = document.createElement("span");
    path.className = "path";
    path.textContent = v;
    b.appendChild(path);
    b.addEventListener("click", () => void openVault(v));
    recent.appendChild(b);
  }
}

async function openVault(dir: string) {
  showStarting("Setting up…");
  try {
    const url = (await invoke("set_vault_and_start", { vault: dir })) as string;
    goto(url);
  } catch (e) {
    showOnboard(`Could not open that folder: ${e}`);
  }
}

async function boot() {
  try {
    const state = (await invoke("boot")) as Boot;
    if (state.kind === "ready") goto(state.url);
    else if (state.kind === "need_vault") showOnboard("Welcome to OVP2.");
    else showOnboard(`Could not start: ${state.message}`);
  } catch (e) {
    showOnboard(`Could not start: ${e}`);
  }
}

pick.addEventListener("click", async () => {
  const dir = await open({ directory: true, multiple: false, title: "Choose your OVP2 vault" });
  if (!dir || typeof dir !== "string") return;
  pick.disabled = true;
  await openVault(dir);
  pick.disabled = false;
});

boot();
