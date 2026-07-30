# BUILD.ps1 - PTY-Agent 构建脚本
# 功能：打包构建 pty-agent

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputDir = Join-Path $scriptDir "pty-agent"

# ============================================================
# 构建 pty-agent 发布目录
# ============================================================

# 清理旧的构建产物
if (Test-Path $outputDir) {
    Remove-Item -Path $outputDir -Recurse -Force
}

# 创建输出目录
New-Item -Path $outputDir -ItemType Directory | Out-Null

# ============================================================
# 递归清理 __pycache__ 目录
# ============================================================

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
# 编译 fastscreen.dll（C++ 屏幕捕获引擎）
# ============================================================
Write-Host "[fastscreen] 编译 fastscreen.dll..."
$fsSource = Join-Path $scriptDir "fastscreen_source"
$fsBuild = Join-Path $fsSource "build"
$null = New-Item -ItemType Directory -Path $fsBuild -Force
$cmake = Get-Command cmake -ErrorAction SilentlyContinue
if (-not $cmake) {
    Write-Warning "[fastscreen] cmake 未找到，跳过编译"
} else {
    & $cmake.Source -S $fsSource -B $fsBuild -G "Visual Studio 18 2026" -A x64
    if ($LASTEXITCODE -ne 0) {
        & $cmake.Source -S $fsSource -B $fsBuild
    }
    & $cmake.Source --build $fsBuild --config Release -j
    if ($LASTEXITCODE -eq 0) {
        $fsDllSrc = Join-Path $fsBuild "bin\Release\fastscreen.dll"
        if (Test-Path $fsDllSrc) {
            $fsDllDst = Join-Path $scriptDir "bin\fastscreencore\fastscreen.dll"
            New-Item -ItemType Directory -Path (Split-Path $fsDllDst -Parent) -Force | Out-Null
            Copy-Item -Path $fsDllSrc -Destination $fsDllDst -Force
            Write-Host "[fastscreen] 编译完成"
        }
    } else {
        Write-Warning "[fastscreen] 编译失败"
    }
}

# ============================================================
# 下载 aichat.exe（AI 分析工具）
# ============================================================
Write-Host "[aichat] 下载最新版 aichat..."
$aichatDir = Join-Path $scriptDir "bin\aichat\bin"
$aichatExe = Join-Path $aichatDir "aichat.exe"
$null = New-Item -ItemType Directory -Path $aichatDir -Force

try {
    $apiUrl = "https://api.github.com/repos/sigoden/aichat/releases/latest"
    $release = Invoke-RestMethod -Uri $apiUrl -Headers @{ Accept = "application/json" }
    $version = $release.tag_name
    $zipUrl = "https://github.com/sigoden/aichat/releases/download/$version/aichat-$version-x86_64-pc-windows-msvc.zip"
    $zipPath = Join-Path $env:TEMP "aichat-$version.zip"

    Write-Host "[aichat] 下载 $version ..."
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath

    Write-Host "[aichat] 解压 ..."
    $tempExtract = Join-Path $env:TEMP "aichat-extract"
    $null = New-Item -ItemType Directory -Path $tempExtract -Force
    Expand-Archive -Path $zipPath -DestinationPath $tempExtract -Force

    $extractedExe = Get-ChildItem -Path $tempExtract -Recurse -Filter "aichat.exe" | Select-Object -First 1
    if ($extractedExe) {
        Copy-Item -Path $extractedExe.FullName -Destination $aichatExe -Force
        Write-Host "[aichat] 已下载: $aichatExe"
    } else {
        Write-Warning "[aichat] 未在压缩包中找到 aichat.exe"
    }

    Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
} catch {
    Write-Warning "[aichat] 下载失败: $_"
}

# ============================================================
# 复制基本包
# ============================================================

Copy-Item -Path (Join-Path $scriptDir "src") -Destination (Join-Path $outputDir "src") -Recurse -Force
Copy-Item -Path (Join-Path $scriptDir "bin") -Destination (Join-Path $outputDir "bin") -Recurse -Force
Copy-Item -Path (Join-Path $scriptDir "app.py") -Destination $outputDir -Force
Copy-Item -Path (Join-Path $scriptDir "SKILL.md") -Destination $outputDir -Force

# ============================================================
# 删除发布目录中不应包含的配置/日志/缓存文件
# ============================================================

# aichat 配置文件
$aichatConfig = Join-Path $outputDir "bin\aichat\config\config.yaml"
if (Test-Path $aichatConfig) { Remove-Item -Path $aichatConfig -Force }

# vnc 运行时配置文件
$vncConfig = Join-Path $outputDir "src\vnc\src\data\config.json"
if (Test-Path $vncConfig) { Remove-Item -Path $vncConfig -Force }

# ultravnc 日志和配置文件
Get-ChildItem -Path (Join-Path $outputDir "bin\ultravnc\*") -Include "*.log", "*.ini" -Force | ForEach-Object { Remove-Item -Path $_.FullName -Force }


Write-Host "构建完成: $outputDir"
