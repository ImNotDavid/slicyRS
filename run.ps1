# Starts slicyRS on http://localhost:8000 using the real slicing engine if one is installed.
# Usage: .\run.ps1            (auto-detect)   .\run.ps1 -Mock   (perimeter-only mock slicer)
param([switch]$Mock, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# 0. settings from .env in the project folder (anything already set in the environment wins)
$dotenv = Join-Path $root ".env"
if (Test-Path $dotenv) {
  foreach ($line in Get-Content $dotenv) {
    if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
    $key = $Matches[1]; $val = $Matches[2].Trim().Trim('"')
    if ($val -and -not [Environment]::GetEnvironmentVariable($key, "Process")) { [Environment]::SetEnvironmentVariable($key, $val, "Process") }
  }
  Write-Host "Loaded settings from .env"
}

# 1. backend venv
if (-not (Test-Path "$root\backend\.venv\Scripts\python.exe")) {
  python -m venv "$root\backend\.venv"
  & "$root\backend\.venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements.txt"
}
# 2. web app
if (-not (Test-Path "$root\frontend\dist\index.html")) {
  Push-Location "$root\frontend"; npm install; npm run build; Pop-Location
}
# 3. slicing engine
$engines = @(
  @{ name = "Bambu Studio"; exe = "C:\Program Files\Bambu Studio\bambu-studio.exe"; profiles = "C:\Program Files\Bambu Studio\resources\profiles" },
  @{ name = "OrcaSlicer";   exe = "C:\Program Files\OrcaSlicer\orca-slicer.exe";    profiles = "C:\Program Files\OrcaSlicer\resources\profiles" }
)
if ($env:ORCA_BIN -and $env:ORCA_PROFILES_DIR) {
  $env:SLICER_MODE = "orca"; Write-Host "Engine: $env:ORCA_BIN (from environment)"
} elseif ($Mock) {
  $env:SLICER_MODE = "mock"; Write-Host "Engine: mock (perimeters only)"
} else {
  $found = $engines | Where-Object { Test-Path $_.exe } | Select-Object -First 1
  if ($found) {
    $env:SLICER_MODE = "orca"; $env:ORCA_BIN = $found.exe; $env:ORCA_PROFILES_DIR = $found.profiles
    Write-Host "Engine: $($found.name) ($($found.exe))"
  } else {
    $env:SLICER_MODE = "mock"
    Write-Warning "No Bambu Studio / OrcaSlicer install found: using the mock slicer (perimeters only)."
  }
}
# 4. local-only auth defaults: password-less dev sign-in as an admin. NEVER use this on a public server (see deploy/).
if (-not $env:OIDC_ISSUER) {
  if (-not $env:DEV_AUTH) { $env:DEV_AUTH = "1" }
  if (-not $env:ADMIN_EMAILS) { $env:ADMIN_EMAILS = "admin@imperial.ac.uk" }
  if (-not $env:SEED_DEMO) { $env:SEED_DEMO = "1" }
  if (-not $env:SMTP_HOST -and -not $env:EMAIL_CONSOLE) { $env:EMAIL_CONSOLE = "1"; Write-Host "Email sign-in codes will be printed in this window (no SMTP_HOST set)." }
  Write-Warning "Local mode: sign in as '$env:ADMIN_EMAILS' with no password (DEV_AUTH=1). Do not expose this port."
}
$env:PUBLIC_URL = "http://localhost:$Port"
Write-Host "Open http://localhost:$Port"
& "$root\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "$root\backend" --port $Port
