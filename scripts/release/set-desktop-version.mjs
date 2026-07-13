#!/usr/bin/env node
// Stamp the desktop app version from a release tag.
// Usage: node scripts/release/set-desktop-version.mjs <version>
//   <version> is a bare semver (e.g. "2.1.0"); the workflow strips the
//   "desktop-v" tag prefix before calling this.
import { readFileSync, writeFileSync } from "node:fs";

const raw = (process.argv[2] || "").trim().replace(/^desktop-v?/, "").replace(/^v/, "");
if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(raw)) {
  console.error(`set-desktop-version: not a semver: "${process.argv[2]}" -> "${raw}"`);
  process.exit(1);
}

const confPath = new URL("../../apps/desktop/src-tauri/tauri.conf.json", import.meta.url);
const conf = JSON.parse(readFileSync(confPath, "utf8"));
conf.version = raw;
writeFileSync(confPath, JSON.stringify(conf, null, 2) + "\n");
console.log(`set-desktop-version: tauri.conf.json version = ${raw}`);
