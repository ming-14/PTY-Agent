# start.ps1 - PTY-Agent Windows 启动脚本
# 使用方法: .\start.ps1 [[-Port] PORT]

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$env:PYTHONPATH = "${scriptDir};$env:PYTHONPATH"

param(
    [string]$Port = ""
)

Write-Host "=== PTY-Agent Windows 启动 ==="

Write-Host "[1/3] 检查并停止已有的守护进程..."
python -m src stop --force 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   已停止旧的守护进程"
} else {
    Write-Host "   没有运行中的守护进程"
}

Write-Host "[2/3] 启动守护进程..."
if ($Port) {
    python -m src start --port $Port
} else {
    python -m src start
}

Write-Host "[3/3] 验证运行状态..."
Start-Sleep -Seconds 1
$status = python -m src status 2>$null
if ($status -match "running") {
    Write-Host ""
    Write-Host "=== PTY-Agent 启动成功 ==="
    Write-Host ""
    Write-Host "常用命令:"
    Write-Host "  启动守护进程:  python -m src start"
    Write-Host "  停止守护进程:  python -m src stop"
    Write-Host "  执行命令:      python app.py exec <id> -c `"<command>`""
    Write-Host "  发送输入:      python app.py send <id> `"<input>`""
    Write-Host "  读取输出:      python app.py read <id>"
    Write-Host "  列出会话:      python app.py list"
    Write-Host "  终止会话:      python app.py kill <id>"
    Write-Host "  查看事件:      python app.py events <id>"
} else {
    Write-Host ""
    Write-Host "=== PTY-Agent 启动可能失败，请检查日志 ==="
}
