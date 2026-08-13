<#
.SYNOPSIS
    Verify the integrity of the local Windows Debugging Tools bundle against sha256.json.

.DESCRIPTION
    Reads sha256.json (generated once with known-good hashes + sizes) and, for every entry:
      1. Checks the file EXISTS (otherwise -> MISSING, warned + fails).
      2. Checks the file SIZE first (fast). If size differs -> SIZE-MISMATCH,
         the expensive hash is SKIPPED and the file is flagged (warned + fails).
      3. Only if size matches, recomputes SHA256 and compares (-> MISMATCH if differ).
    Also enforces that the 10.0.19041.5609 directory contains NO files other than
    those listed (any extra file -> EXTRA, warned + fails).

    Exit code is non-zero if any MISSING / SIZE-MISMATCH / MISMATCH / EXTRA is found.

.PARAMETER JsonPath
    Path to sha256.json. Defaults to .\sha256.json next to this script.

.PARAMETER BaseDir
    Root directory the relative paths in json are resolved against.
    Defaults to the parent directory of the sha256.json file.

.EXAMPLE
    .\safety-check.ps1
    .\safety-check.ps1 -JsonPath C:\path\sha256.json -BaseDir C:\path
#>

[CmdletBinding()]
param(
    [string]$JsonPath = $null,
    [string]$BaseDir  = $null
)

$ErrorActionPreference = 'Stop'

# --- resolve paths -------------------------------------------------------
if (-not $JsonPath) {
    $JsonPath = Join-Path $PSScriptRoot 'sha256.json'
}
if (-not (Test-Path $JsonPath)) {
    Write-Error "sha256.json not found: $JsonPath"
    exit 2
}
if (-not $BaseDir) {
    $BaseDir = Split-Path -Parent (Resolve-Path $JsonPath).Path
}

# --- load manifest --------------------------------------------------------
$data = Get-Content -Path $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$algo = if ($data.algorithm) { $data.algorithm } else { 'SHA256' }
$baseInJson = if ($data.baseDir) { $data.baseDir } else { '' }

$expected = @{}   # lowercaseRelPath -> @{ sha256=...; size=... }
foreach ($key in $data.files.PSObject.Properties.Name) {
    $expected[$key.ToLower()] = [pscustomobject]@{
        sha256 = $data.files.$key.sha256
        size   = $data.files.$key.size
    }
}

# --- scan disk (only inside the declared tool directory) -----------------
$diskFiles = @{}   # lowercaseRelPath -> fullPath
$searchRoot = if ($baseInJson) { Join-Path $BaseDir $baseInJson } else { $BaseDir }
Get-ChildItem -Path $searchRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
    $rel = $_.FullName.Substring($BaseDir.Length).TrimStart('\', '/').Replace('\', '/')
    $diskFiles[$rel.ToLower()] = $_.FullName
}

# --- compare --------------------------------------------------------------
$mismatch    = 0
$sizeMismatch = 0
$missing     = 0
$extra       = 0
$ok          = 0
$hashSkipped = 0

Write-Host "=== Windows Debugging Tools safety check ===" -ForegroundColor Cyan
Write-Host "Manifest : $JsonPath"
Write-Host "Base     : $BaseDir"
Write-Host "Algorithm: $algo (size checked first, hash only if size matches)"
Write-Host "Expected : $($expected.Count) files`n"

foreach ($rel in $expected.Keys) {
    # 1) existence
    if (-not $diskFiles.ContainsKey($rel)) {
        Write-Host "[MISSING]  $rel  (file absent, required)" -ForegroundColor Red
        $missing++
        continue
    }
    $full  = $diskFiles[$rel]
    $exp   = $expected[$rel]

    # 2) size (fast, always checked first)
    try {
        $item = Get-Item -LiteralPath $full -ErrorAction Stop
    } catch {
        Write-Host "[ERROR]    $rel : $_" -ForegroundColor Red
        $mismatch++
        continue
    }
    if ($item.Length -ne $exp.size) {
        Write-Host "[SIZE-MISMATCH] $rel" -ForegroundColor Red
        Write-Host "                expected size: $($exp.size)   current: $($item.Length)  (hash skipped)"
        $sizeMismatch++
        $hashSkipped++
        continue
    }

    # 3) hash (only reached when size matches)
    try {
        $cur = (Get-FileHash -Path $full -Algorithm $algo -ErrorAction Stop).Hash
    } catch {
        Write-Host "[ERROR]    $rel : $_" -ForegroundColor Red
        $mismatch++
        continue
    }
    if ($cur -eq $exp.sha256) {
        $ok++
    } else {
        Write-Host "[MISMATCH] $rel" -ForegroundColor Red
        Write-Host "           expected: $($exp.sha256)"
        Write-Host "           current : $cur"
        $mismatch++
    }
}

# --- strict: no extra files allowed inside the tool directory -------------
foreach ($rel in $diskFiles.Keys) {
    if (-not $expected.ContainsKey($rel)) {
        Write-Host "[EXTRA]    $rel  (not in manifest - directory must contain ONLY listed files)" -ForegroundColor Yellow
        $extra++
    }
}

# --- summary --------------------------------------------------------------
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "OK: $ok   SIZE-MISMATCH: $sizeMismatch   MISMATCH: $mismatch   MISSING: $missing   EXTRA: $extra"
if ($hashSkipped -gt 0) {
    Write-Host "(hash computation skipped for $hashSkipped size-mismatched file(s) - performance saved)"
}

if ($mismatch -gt 0 -or $sizeMismatch -gt 0 -or $missing -gt 0 -or $extra -gt 0) {
    Write-Host "`nResult: FAIL - integrity violation(s) detected." -ForegroundColor Red
    exit 1
} else {
    Write-Host "`nResult: PASS - all $ok files verified, no extra files." -ForegroundColor Green
    exit 0
}
