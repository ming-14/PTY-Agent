# install.ps1 — fetch latest PTY-Agent release and install as a Skill
# Usage:
#   irm https://raw.githubusercontent.com/ming-14/PTY-Agent/main/install.ps1 | iex
# Env:
#   PTY_AGENT_MIRROR  URL prefix for downloads
#   HTTPS_PROXY       curl proxy
# Opt:
#   -Project          install to current project (default: global -g)

param(
    [switch]$Project
)

$ErrorActionPreference = "Stop"

if ($IsWindows) {
    $asset = "pty-agent-win_x86-64.zip"
} else {
    $asset = "pty-agent-linux_x86-64.zip"
}

$releaseUrl = "https://github.com/ming-14/PTY-Agent/releases/latest/download/$asset"
$mirror = $env:PTY_AGENT_MIRROR
if ($mirror) {
    $releaseUrl = "$($mirror.TrimEnd('/'))/$releaseUrl"
}
Write-Host "[1/4] Downloading: $releaseUrl"

$tmpDir = Join-Path $env:TEMP "pty-agent-skill-$(Get-Random)"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$zipPath = Join-Path $tmpDir $asset
curl.exe -fL --retry 3 -o $zipPath $releaseUrl
if ($LASTEXITCODE -ne 0) { Write-Error "Download failed (curl exit=$LASTEXITCODE)" }
Write-Host "[2/4] Downloaded: $([math]::Round((Get-Item $zipPath).Length / 1MB, 1)) MB"

Write-Host "[3/4] Extracting..."
$extractDir = Join-Path $tmpDir "extract"
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

$skillDir = Get-ChildItem -Path $extractDir -Filter "SKILL.md" -Recurse |
    Select-Object -First 1 -ExpandProperty DirectoryName
if (-not $skillDir) {
    Remove-Item -Recurse -Force $tmpDir
    Write-Error "SKILL.md not found in archive"
}

Write-Host "[4/4] npx skills add: $skillDir"
if ($Project) {
    npx --yes skills add $skillDir -y
} else {
    npx --yes skills add $skillDir -y -g
}
$code = $LASTEXITCODE
Remove-Item -Recurse -Force $tmpDir
if ($code -ne 0) { Write-Error "npx skills add failed (exit=$code)" }
Write-Host "PTY-Agent Skill installed"
exit 0
