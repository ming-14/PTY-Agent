# BUILD.ps1 - PTY-Agent 构建脚本
# 功能：打包构建 pty-agent
#
# 环境变量：
#   GITHUB_MIRROR              - GitHub 下载镜像（如：https://ghproxy.com/）
#   GITHUB_API_MIRROR          - GitHub API 镜像（如：https://api.github.com）
#   DOWNLOAD_AICHAT            - 是否下载 aichat（true/false，默认 true）
#   BUILD_FASTSCREEN           - 是否构建 fastscreen.dll（true/false，默认 true）
#   DOWNLOAD_ULTRAVNC          - 是否下载 UltraVNC（true/false，默认 true）
#   DOWNLOAD_TERMINALINJECTOR  - 是否下载 terminal_injector（true/false，默认 true）
#
# 命令行参数：
#   -NoAichat             - 跳过 aichat 下载
#   -NoFastscreen         - 跳过 fastscreen 编译
#   -NoUltravnc           - 跳过 UltraVNC 下载
#   -NoTerminalInjector   - 跳过 terminal_injector 下载
#   -Mirror <url>         - 指定 GitHub 下载镜像（对应 GITHUB_MIRROR）
#   -ApiMirror <url>      - 指定 GitHub API 镜像（对应 GITHUB_API_MIRROR）
#
# 示例：
#   $env:GITHUB_MIRROR="https://ghproxy.com/"; .\BUILD.ps1 -NoAichat
#   .\BUILD.ps1 -NoUltravnc -Mirror "https://ghproxy.com/" -ApiMirror "https://api.github.com"
#   .\BUILD.ps1 -NoUltravnc -NoTerminalInjector -Mirror "https://v4.gh-proxy.org/"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

# 解析命令行参数
$noAichat = $args -contains "-NoAichat"
$noFastscreen = $args -contains "-NoFastscreen"
$noUltravnc = $args -contains "-NoUltravnc"
$noTerminalInjector = $args -contains "-NoTerminalInjector"

# 解析 -Mirror 参数
$mirrorArg = $args | ForEach-Object { if ($_ -eq "-Mirror" -or $_ -eq "-m") { $true } }
if ($mirrorArg) {
    $mirrorIndex = [Array]::IndexOf($args, "-Mirror")
    if ($mirrorIndex -eq -1) { $mirrorIndex = [Array]::IndexOf($args, "-m") }
    if ($mirrorIndex -ge 0 -and $mirrorIndex + 1 -lt $args.Count) {
        $env:GITHUB_MIRROR = $args[$mirrorIndex + 1]
    }
}

# 解析 -ApiMirror 参数
$apiMirrorArg = $args | ForEach-Object { if ($_ -eq "-ApiMirror" -or $_ -eq "-am") { $true } }
if ($apiMirrorArg) {
    $apiMirrorIndex = [Array]::IndexOf($args, "-ApiMirror")
    if ($apiMirrorIndex -eq -1) { $apiMirrorIndex = [Array]::IndexOf($args, "-am") }
    if ($apiMirrorIndex -ge 0 -and $apiMirrorIndex + 1 -lt $args.Count) {
        $env:GITHUB_API_MIRROR = $args[$apiMirrorIndex + 1]
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputDir = Join-Path $scriptDir "pty-agent"

# 从环境变量读取配置（命令行参数优先）
$githubMirror = $env:GITHUB_MIRROR ?? ""
$githubApiMirror = $env:GITHUB_API_MIRROR ?? "https://api.github.com"
$downloadAichat = if ($noAichat) { $false } else { ($env:DOWNLOAD_AICHAT ?? "true") -eq "true" }
$buildFastscreen = if ($noFastscreen) { $false } else { ($env:BUILD_FASTSCREEN ?? "true") -eq "true" }
$downloadUltravnc = if ($noUltravnc) { $false } else { ($env:DOWNLOAD_ULTRAVNC ?? "true") -eq "true" }
$downloadTerminalInjector = if ($noTerminalInjector) { $false } else { ($env:DOWNLOAD_TERMINALINJECTOR ?? "true") -eq "true" }

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
# 复制基本包
# ============================================================

Copy-Item -Path (Join-Path $scriptDir "src") -Destination (Join-Path $outputDir "src") -Recurse -Force
Copy-Item -Path (Join-Path $scriptDir "bin") -Destination (Join-Path $outputDir "bin") -Recurse -Force
Copy-Item -Path (Join-Path $scriptDir "app.py") -Destination $outputDir -Force
Copy-Item -Path (Join-Path $scriptDir "SKILL.md") -Destination $outputDir -Force

# ============================================================
# 清理 pty-agent 中的 __pycache__ 目录
# ============================================================

Get-ChildItem -Path $outputDir -Directory -Recurse -Filter "__pycache__" | ForEach-Object {
    $cacheDir = $_
    $relativePath = $cacheDir.FullName.Substring($outputDir.Length + 1)
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
# 删除 pty-agent 中的所有 .gitkeep 文件
# ============================================================

Get-ChildItem -Path $outputDir -Recurse -Filter ".gitkeep" -File -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item -Path $_.FullName -Force
    Write-Host "已删除: $($_.FullName)"
}

# ============================================================
# 编译 fastscreen.dll（C++ 屏幕捕获引擎）
# ============================================================
if ($buildFastscreen) {
    Write-Host "[fastscreen] 编译 fastscreen.dll..."
    $fsSource = Join-Path $scriptDir "fastscreen"
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
                $fsDllDst = Join-Path $outputDir "bin\fastscreencore\fastscreen.dll"
                New-Item -ItemType Directory -Path (Split-Path $fsDllDst -Parent) -Force | Out-Null
                Copy-Item -Path $fsDllSrc -Destination $fsDllDst -Force
                Write-Host "[fastscreen] 编译完成"
            }
        } else {
            Write-Warning "[fastscreen] 编译失败"
        }
    }
} else {
    Write-Host "[fastscreen] 跳过编译（BUILD_FASTSCREEN=false）"
}

# ============================================================
# 下载 aichat.exe（AI 分析工具）
# ============================================================
if ($downloadAichat) {
    Write-Host "[aichat] 下载最新版 aichat..."
    $aichatDir = Join-Path $scriptDir "bin\aichat\bin"
    $aichatExe = Join-Path $aichatDir "aichat.exe"
    $null = New-Item -ItemType Directory -Path $aichatDir -Force

    try {
        $apiUrl = "$githubApiMirror/repos/sigoden/aichat/releases/latest"
        $release = Invoke-RestMethod -Uri $apiUrl -Headers @{ Accept = "application/json" }
        $version = $release.tag_name
        $originalZipUrl = "https://github.com/sigoden/aichat/releases/download/$version/aichat-$version-x86_64-pc-windows-msvc.zip"
        $zipUrl = "$githubMirror$originalZipUrl"
        $zipPath = Join-Path $env:TEMP "aichat-$version.zip"

        Write-Host "[aichat] 下载 $version from $zipUrl ..."
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
} else {
    Write-Host "[aichat] 跳过下载（DOWNLOAD_AICHAT=false）"
}

# ============================================================
# 下载 UltraVNC（远程桌面工具）
# ============================================================
if ($downloadUltravnc) {
    Write-Host "[ultravnc] 下载 UltraVNC..."
    $ultravncZipUrl = "https://uvnc.eu/download/1800/UltraVNC_1824.zip"
    $ultravncZipPath = Join-Path $env:TEMP "UltraVNC_1824.zip"
    $ultravncDir = Join-Path $outputDir "bin\ultravnc"
    try {
        # 检测系统架构
        $is64bit = [Environment]::Is64BitOperatingSystem
        $arch = if ($is64bit) { "x64" } else { "x86" }
        Write-Host "[ultravnc] 检测到系统架构: $arch"

        Write-Host "[ultravnc] 下载 $ultravncZipUrl ..."
        $downloadUrl = "$githubMirror$ultravncZipUrl"
        if ([string]::IsNullOrEmpty($githubMirror)) { $downloadUrl = $ultravncZipUrl }
        Invoke-WebRequest -Uri $downloadUrl -OutFile $ultravncZipPath
        Write-Host "[ultravnc] 下载完成"
        Write-Host "[ultravnc] 解压 ..."
        $tempExtract = Join-Path $env:TEMP "ultravnc-extract"
        $null = New-Item -ItemType Directory -Path $tempExtract -Force
        Expand-Archive -Path $ultravncZipPath -DestinationPath $tempExtract -Force
        $ultravncSource = if ($is64bit) { Join-Path $tempExtract "x64"
        } else { Join-Path $tempExtract "x86" }

        if (Test-Path $ultravncSource) {
            # 创建目标目录
            $null = New-Item -ItemType Directory -Path $ultravncDir -Force
            # 复制所有文件到目标目录
            Copy-Item -Path "$ultravncSource\*" -Destination $ultravncDir -Recurse -Force
            Write-Host "[ultravnc] 已安装到: $ultravncDir"
        } else { Write-Warning "[ultravnc] 未找到对应架构的文件: $ultravncSource" }
        # 清理临时文件
        Remove-Item -Path $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $ultravncZipPath -Force -ErrorAction SilentlyContinue
        Write-Host "[ultravnc] 已清理临时文件"
    } catch { Write-Warning "[ultravnc] 下载/安装失败: $_" }
} else {
    Write-Host "[ultravnc] 跳过下载（DOWNLOAD_ULTRAVNC=false 或 -NoUltravnc）"
}

# ============================================================
# 下载 terminal_injector（终端注入工具）
# ============================================================
if ($downloadTerminalInjector) {
    Write-Host "[terminal_injector] 下载 terminal_injector..."
    $tiOriginalZipUrl = "https://github.com/ming-14/terminal-injector/releases/download/v1.0/terminal_injector_x64_v1.0.zip"
    $tiZipUrl = "$githubMirror$tiOriginalZipUrl"
    $tiZipPath = Join-Path $env:TEMP "terminal_injector_x64_v1.0.zip"
    $tiDir = Join-Path $outputDir "bin\terminal_injector"
    try {
        $null = New-Item -ItemType Directory -Path $tiDir -Force
        Write-Host "[terminal_injector] 下载 $tiZipUrl ..."
        Invoke-WebRequest -Uri $tiZipUrl -OutFile $tiZipPath
        Write-Host "[terminal_injector] 解压 ..."
        $tiTempExtract = Join-Path $env:TEMP "terminal_injector-extract"
        $null = New-Item -ItemType Directory -Path $tiTempExtract -Force
        Expand-Archive -Path $tiZipPath -DestinationPath $tiTempExtract -Force
        Copy-Item -Path "$tiTempExtract\*" -Destination $tiDir -Recurse -Force
        Write-Host "[terminal_injector] 已安装到: $tiDir"
        Remove-Item -Path $tiTempExtract -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $tiZipPath -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "[terminal_injector] 下载/安装失败: $_"
    }
} else {
    Write-Host "[terminal_injector] 跳过下载（DOWNLOAD_TERMINALINJECTOR=false 或 -NoTerminalInjector）"
}

# ============================================================
# 删除发布目录中不应包含的配置/日志/缓存文件
# ============================================================

# aichat 配置文件
$aichatConfig = Join-Path $outputDir "bin\aichat\config\config.yaml"
if (Test-Path $aichatConfig) { Remove-Item -Path $aichatConfig -Force }

# vnc 运行时配置文件
$vncConfig = Join-Path $outputDir "src\config\vnc.toml"
if (Test-Path $vncConfig) { Remove-Item -Path $vncConfig -Force }
$vncExampleConfig = Join-Path $outputDir "src\config\vnc.example.toml"
if (Test-Path $vncExampleConfig) { Remove-Item -Path $vncExampleConfig -Force }

# ultravnc 日志和配置文件
$ultravncPath = Join-Path $outputDir "bin\ultravnc"
if (Test-Path $ultravncPath) {
    Get-ChildItem -Path "$ultravncPath\*" -Include "*.log", "*.ini" -Force -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item -Path $_.FullName -Force }
}


Write-Host "构建完成: $outputDir"
