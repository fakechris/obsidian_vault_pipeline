## OVP2 Desktop

The desktop app bundles the full OVP2 pipeline — it runs the portal locally and
schedules your daily ingest + weekly crystallize in-app (no launchd/systemd).

### Install
1. Download the `.dmg` for your Mac (`arm64` for Apple Silicon, `x64` for Intel).
2. Open it and drag **OVP2** to Applications.

### First run — ad-hoc signed, manual approval needed
This build is **ad-hoc code-signed** and **not notarized by Apple**, so Gatekeeper
blocks it on first launch. To open it once:

- **Right-click** (or Control-click) **OVP2.app → Open → Open**, or
- run `xattr -dr com.apple.quarantine /Applications/OVP2.app` in Terminal.

After the first approval it opens normally. On first launch, pick your Obsidian
vault folder when prompted.

Verify the download with `shasum -a 256 -c SHA256SUMS.txt`.
