[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
param(
    [switch]$Force
)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$forceArg = if ($Force) { "--force" } else { "" }
python -m src stop $forceArg
