# stop.ps1 - PTY-Agent Windows 停止脚本
# 使用方法: .\stop.ps1 [[-Force]]

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

param(
    [switch]$Force
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$forceArg = if ($Force) { "--force" } else { "" }

Write-Host "=== 停止 PTY-Agent 守护进程 ==="
python -m src stop $forceArg
