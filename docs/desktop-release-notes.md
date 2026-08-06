## OVP2 Desktop

The desktop app bundles the full OVP2 pipeline — it runs the portal locally and
schedules your daily ingest + weekly crystallize in-app (no launchd/systemd).

### Install — macOS
1. Download the `.dmg` for your Mac (`arm64` for Apple Silicon, `x64` for Intel).
2. Open it and drag **OVP2** to Applications.

### Install — Windows (x64)
1. Download `OVP2-<tag>-x64-setup.exe`.
2. Run it. It installs **for the current user only** — no admin prompt — and
   pulls WebView2 automatically if the machine doesn't have it.

The Windows installer is **unsigned**, so SmartScreen shows
"Windows protected your PC" on first run: click **More info → Run anyway**.
Verify the download against `SHA256SUMS.txt` first
(`Get-FileHash .\OVP2-<tag>-x64-setup.exe -Algorithm SHA256`).

Some enterprise policies block unsigned installers outright; there is no
workaround short of a signed build.

### First run on macOS — ad-hoc signed, manual approval needed
This build is **ad-hoc code-signed** and **not notarized by Apple**, so Gatekeeper
blocks it on first launch. To open it once:

- **Right-click** (or Control-click) **OVP2.app → Open → Open**, or
- run `xattr -dr com.apple.quarantine /Applications/OVP2.app` in Terminal.

After the first approval it opens normally. On first launch, pick your Obsidian
vault folder when prompted.

Verify the download with `shasum -a 256 -c SHA256SUMS.txt`.
