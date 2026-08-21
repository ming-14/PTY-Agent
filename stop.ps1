param(
    [switch]$Force
)
# param() 必须位于脚本首条语句，编码与错误策略在其后设置
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$forceArg = if ($Force) { "--force" } else { "" }
python -m src stop $forceArg
