# BUILD.ps1 - PTY-Agent 构建脚本
# 功能：打包构建 pty-agent

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputDir = Join-Path $scriptDir "pty-agent"

# 递归清理 __pycache__ 目录
Get-ChildItem -Path $scriptDir -Directory -Recurse -Filter "__pycache__" | ForEach-Object {
    $cacheDir = $_
    $relativePath = $cacheDir.FullName.Substring($scriptDir.Length + 1)
    $hasHiddenSegment = $relativePath.Split('\') | Where-Object { $_.StartsWith('.') }
    if ($hasHiddenSegment) { return }
    $attrs = (Get-Item $cacheDir.FullName -Force).Attributes
    if ($attrs -band [System.IO.FileAttributes]::Hidden -or $attrs -band [System.IO.FileAttributes]::System) { return }
    $subDirs = Get-ChildItem -Path $cacheDir.FullName -Directory -Force -ErrorAction SilentlyContinue
    if ($subDirs) { return }
    $files = Get-ChildItem -Path $cacheDir.FullName -File -Force
    $allPyc = $files | ForEach-Object { $_.Extension -eq ".pyc" }
    if ($allPyc -contains $false) { return }
    Remove-Item -Path $cacheDir.FullName -Recurse -Force
    Write-Host "已删除: $($cacheDir.FullName)"
}

# ============================================================
# 构建 pty-agent 发布目录
# ============================================================

# 清理旧的构建产物
if (Test-Path $outputDir) {
    Remove-Item -Path $outputDir -Recurse -Force
}

# 创建输出目录
New-Item -Path $outputDir -ItemType Directory | Out-Null

Copy-Item -Path (Join-Path $scriptDir "src") -Destination (Join-Path $outputDir "src") -Recurse -Force
Copy-Item -Path (Join-Path $scriptDir "bin") -Destination (Join-Path $outputDir "bin") -Recurse -Force
Copy-Item -Path (Join-Path $scriptDir "app.py") -Destination $outputDir -Force
Copy-Item -Path (Join-Path $scriptDir "SKILL.md") -Destination $outputDir -Force

# ============================================================
# 删除发布目录中不应包含的配置/日志文件
# ============================================================

# aichat 配置文件
$aichatConfig = Join-Path $outputDir "bin\aichat\config\config.yaml"
if (Test-Path $aichatConfig) { Remove-Item -Path $aichatConfig -Force }

# fastscreen 标记文件
$fastscreenFlag = Join-Path $outputDir "bin\fastscreen\!fastscreen!"
if (Test-Path $fastscreenFlag) { Remove-Item -Path $fastscreenFlag -Force }

# sandboxie_plus 配置文件
Get-ChildItem -Path (Join-Path $outputDir "bin\sandboxie_plus\bin") -Filter "*.ini" -Force | ForEach-Object { Remove-Item -Path $_.FullName -Force }

# ultravnc 日志和配置文件
Get-ChildItem -Path (Join-Path $outputDir "bin\ultravnc\*") -Include "*.log", "*.ini" -Force | ForEach-Object { Remove-Item -Path $_.FullName -Force }

Write-Host "构建完成: $outputDir"
