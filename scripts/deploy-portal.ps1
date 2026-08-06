# Windows mirror of deploy-portal.sh. Same contract, same failure mode it
# defends against — read that file's header first; the reasoning is identical.
#
# Usage:  pwsh scripts/deploy-portal.ps1 <vault-root>
#     or: $env:OVP2_VAULT = 'C:\vault'; pwsh scripts/deploy-portal.ps1
[CmdletBinding()]
param([string]$VaultRoot)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
if (-not $VaultRoot) { $VaultRoot = $env:OVP2_VAULT }
if (-not $VaultRoot) {
    Write-Error "usage: deploy-portal.ps1 <vault-root>    (or set OVP2_VAULT)"
}
if (-not (Test-Path (Join-Path $VaultRoot '.ovp') -PathType Container)) {
    Write-Error "not a vault (no .ovp\): $VaultRoot"
}

$appDir = Join-Path $VaultRoot '.ovp\console\app'
$dist = Join-Path $root 'console-ui\dist'

Write-Host '==> building console-ui'
npm --prefix (Join-Path $root 'console-ui') run build
if ($LASTEXITCODE -ne 0) { Write-Error "console-ui build failed ($LASTEXITCODE)" }

Write-Host "==> deploying to $appDir"
# Replace wholesale: a merge would leave orphaned old asset hashes behind, and
# those are exactly what a stale index.html would keep pointing at.
if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
Copy-Item -Recurse -Force (Join-Path $dist '*') $appDir

$entryPattern = 'assets/[A-Za-z0-9._-]*\.js'
$builtHash = (Select-String -Path (Join-Path $dist 'index.html') -Pattern $entryPattern `
        -AllMatches).Matches.Value | Select-Object -First 1
Write-Host "==> built entry: $builtHash"

# Verify against a RUNNING portal when we can find one. The desktop app picks a
# fresh random port each launch and records it here; `ovp2 serve` defaults to
# 3141. A miss is not an error — the deploy is still done.
$log = Join-Path $VaultRoot '.ovp\desktop-portal.log'
$port = 3141
if (Test-Path $log) {
    $found = (Select-String -Path $log -Pattern '127\.0\.0\.1:(\d+)' -AllMatches).Matches |
        Select-Object -Last 1
    if ($found) { $port = [int]$found.Groups[1].Value }
}

$served = $null
try {
    $body = (Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 3 -UseBasicParsing).Content
    $served = ([regex]::Matches($body, $entryPattern)).Value | Select-Object -First 1
} catch {
    $served = $null
}

if (-not $served) {
    Write-Host "==> no portal answering on 127.0.0.1:$port — deployed, not verified live"
    Write-Host "    start one, then check: (iwr http://127.0.0.1:<port>/).Content -match '$entryPattern'"
    exit 0
}

if ($served -eq $builtHash) {
    Write-Host "==> verified: portal on :$port serves $served"
    Write-Host '    hard-refresh the browser (Ctrl+Shift+R) if the tab was already open'
} else {
    Write-Host "!!! MISMATCH: portal on :$port serves $served, expected $builtHash" -ForegroundColor Red
    Write-Host '    the running server is reading a different copy — check for another'
    Write-Host '    vault, or an OVP2_VIZ_DIR override on the running process.'
    exit 1
}
