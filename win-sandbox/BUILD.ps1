# BUILD.ps1 - win-sandbox 构建脚本
# 功能：编译 win_sandbox 核心 C++ 库（pybind11 in-process，产物 win_sandbox_native*.pyd）
# 依赖：Visual Studio（vcvars64.bat）、CMake 3.20+、Ninja、Python 3.10+
#
# 参数：
#   -Config <Debug|Release>   构建类型（默认 Release）
#   -Rebuild                  清理 build 目录后全新构建（CMakeCache 内嵌旧路径时重建失败，需整体删除）
#   -Vcvars <path>            手动指定 vcvars64.bat 路径（默认自动探测 VS 安装）
#
# 示例：
#   .\BUILD.ps1
#   .\BUILD.ps1 -Config Debug -Rebuild

param(
    [ValidateSet("Debug", "Release")] [string]$Config = "Release",
    [switch]$Rebuild,
    [string]$Vcvars = ""
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildDir = Join-Path $scriptDir "build"

# ===== 工具检查 =====
$cmake = Get-Command cmake -ErrorAction SilentlyContinue
if (-not $cmake) { throw "未找到 cmake，请安装 CMake 3.20+ 并加入 PATH" }
if (-not (Get-Command ninja -ErrorAction SilentlyContinue)) { throw "未找到 ninja，请安装 Ninja build 并加入 PATH" }
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "未找到 python，请安装 Python 3.10+ 并加入 PATH" }

# ===== 定位 vcvars64.bat =====
# 优先 vswhere 探测任意已装 VS（2022/2026/Preview，需含 C++ 工作负载），
# 再兜底常见默认安装路径；也可用 -Vcvars 显式指定
function Find-Vcvars {
    param([string]$ManualPath)
    if ($ManualPath) {
        if (Test-Path $ManualPath) { return $ManualPath }
        throw "指定的 vcvars64.bat 不存在: $ManualPath"
    }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $installDir = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        if ($installDir) {
            $candidate = Join-Path $installDir "VC\Auxiliary\Build\vcvars64.bat"
            if (Test-Path $candidate) { return $candidate }
        }
    }

    $fallback = @(
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    )
    $hit = $fallback | Where-Object { Test-Path $_ } | Select-Object -First 1
    return $hit
}

$vcvars = Find-Vcvars -ManualPath $Vcvars
if (-not $vcvars) {
    throw "未找到 vcvars64.bat，请安装 VS C++ 桌面工作负载或用 -Vcvars 显式指定"
}
Write-Host "[win-sandbox] vcvars64.bat: $vcvars"
Write-Host "[win-sandbox] Python: $($python.Source)"

# ===== 清理旧构建缓存 =====
if ($Rebuild -and (Test-Path $buildDir)) {
    Remove-Item -Path $buildDir -Recurse -Force
}

# ===== 配置 + 构建 =====
# vcvars 环境注入经临时 .cmd 包装（cmd 引号转义在 PowerShell 中不可靠），
# 流程：call vcvars → cmake -B 配置 → cmake --build
$cmdFile = Join-Path $env:TEMP "build_win_sandbox.cmd"
$cmdContent = @"
@echo off
call "$vcvars" >nul 2>&1
cmake -S "$scriptDir" -B "$buildDir" -G Ninja -DCMAKE_BUILD_TYPE=$Config
if errorlevel 1 exit /b 1
cmake --build "$buildDir"
exit /b %errorlevel%
"@
Set-Content -Path $cmdFile -Value $cmdContent -Encoding ascii
Write-Host "[win-sandbox] 构建 $Config ..."
cmd /c $cmdFile
$exitCode = $LASTEXITCODE
Remove-Item -Path $cmdFile -Force -ErrorAction SilentlyContinue
if ($exitCode -ne 0) { throw "构建失败（exit=$exitCode），详见上方日志" }

# ===== 验证产物 =====
$pyd = Get-ChildItem -Path (Join-Path $buildDir "bin") -Filter "win_sandbox_native*.pyd" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pyd) { throw "构建成功但未找到产物 win_sandbox_native*.pyd（$buildDir\bin）" }
Write-Host "[win-sandbox] 构建完成: $($pyd.FullName)"

Write-Host "[win-sandbox] BUILD.ps1 完成"