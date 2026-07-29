# restart.ps1 - PTY-Agent Windows 重启脚本
# 使用方法: .\restart.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "=== 重启 PTY-Agent ==="
python -m src stop --force 2>$null
Start-Sleep -Seconds 1
python -m src start
Write-Host "=== 重启完成 ==="
