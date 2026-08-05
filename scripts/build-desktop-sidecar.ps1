# Windows mirror of build-desktop-sidecar.sh: build the `ovp2` CLI that the
# desktop app's in-app scheduler exec's, and drop it where Tauri's `externalBin`
# expects it (`binaries/ovp2-<triple>.exe`).
#
# The features matter. The scheduler runs this binary, not the desktop crate, so
# a no-feature build silently turns every live job into an offline no-op. Build
# through this script rather than a bare `cargo build` for exactly that reason.
#
# Usage:
#   pwsh scripts/build-desktop-sidecar.ps1
#   pwsh scripts/build-desktop-sidecar.ps1 -Triple x86_64-pc-windows-msvc
#   $env:INSTALL_APP = "$env:LOCALAPPDATA\OVP2"; pwsh scripts/build-desktop-sidecar.ps1
#
# -InstallApp / $env:INSTALL_APP points at an INSTALLED app directory (the one
# holding OVP2.exe). Copying the sidecar there updates the running install's
# scheduler binary without repackaging — the app does NOT need a restart,
# because every tick re-spawns the file currently on disk.
[CmdletBinding()]
param(
    [string]$Triple,
    [string]$InstallApp = $env:INSTALL_APP
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not $Triple) {
        $Triple = (rustc -vV | Select-String '^host:\s*(.+)$').Matches[0].Groups[1].Value.Trim()
    }
    $features = $env:OVP2_SIDECAR_FEATURES
    if (-not $features) { $features = 'anthropic,pinboard-live,web-fetch-live,github-live' }

    $outDir = Join-Path $root 'apps\desktop\src-tauri\binaries'
    $out = Join-Path $outDir "ovp2-$Triple.exe"

    Write-Host "building ovp2 sidecar ($Triple) features=$features"
    cargo build --release -p ovp-cli --target $Triple --features $features
    if ($LASTEXITCODE -ne 0) { Write-Error "cargo build failed ($LASTEXITCODE)" }

    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Copy-Item -Force (Join-Path $root "target\$Triple\release\ovp2.exe") $out
    Write-Host "wrote $out ($((Get-Item $out).Length) bytes)"

    if ($InstallApp) {
        if (-not (Test-Path (Join-Path $InstallApp 'OVP2.exe'))) {
            Write-Error "INSTALL_APP=$InstallApp has no OVP2.exe — point it at the installed app directory"
        }
        $dest = Join-Path $InstallApp 'ovp2.exe'
        Copy-Item -Force $out $dest
        Write-Host "installed $dest"
    }
} finally {
    Pop-Location
}
