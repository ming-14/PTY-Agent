# BUILD.ps1 - wezterm-py 构建脚本
# 功能：编译 pywezterm（pyo3/abi3，portable-pty + wezterm-term），
#       产物完整 pywezterm 包复制到 -VendorDir（默认 <wezterm-py 上级>/bin/pywezterm，
#       vendored，src 侧经 sys.path 加载，不依赖 pip 安装全局包）
# 依赖：Rust（cargo/rustc，rustup 安装）、Visual Studio（vcvars64.bat）、
#       Python 3.8+ 与 maturin（缺失时自动 pip 安装）
#
# 参数：
#   -Config <Debug|Release>   构建类型（默认 Release）
#   -Rebuild                  清理编译缓存后全新构建
#   -VendorDir <path>         产物部署目录（默认 <wezterm-py 上级>/bin/pywezterm，
#                             供 leaf/PTY-Agent 共用；其他项目可显式指定）
#   -Vcvars <path>            手动指定 vcvars64.bat 路径（默认自动探测）
#   -CargoDir <path>          手动指定 cargo 所在目录（默认探测 ~/.cargo/bin）
#   -Python <path>            手动指定 python 可执行文件（默认取 PATH）
#
# 示例：
#   .\BUILD.ps1
#   .\BUILD.ps1 -Config Debug
#   .\BUILD.ps1 -Rebuild -Vcvars "D:\VS\VC\Auxiliary\Build\vcvars64.bat"
#   .\BUILD.ps1 -VendorDir "D:\apps\myapp\vendor\pywezterm"

param(
    [ValidateSet("Debug", "Release")] [string]$Config = "Release",
    [switch]$Rebuild,
    [string]$VendorDir = "",
    [string]$Vcvars = "",
    [string]$CargoDir = "",
    [string]$Python = ""
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheelsDir = Join-Path $scriptDir "target\wheels"
if ($VendorDir) {
    $vendorDst = $VendorDir
} else {
    # 默认：<wezterm-py 上级>/bin/pywezterm（leaf/PTY-Agent 共用同一产物）
    $projectRoot = Split-Path $scriptDir -Parent
    $vendorDst = Join-Path $projectRoot "bin\pywezterm"
}

# ===== 工具检查：python =====
$pyExe = if ($Python) { $Python } else { (Get-Command python -ErrorAction SilentlyContinue)?.Source }
if (-not $pyExe) { throw "未找到 python，请安装 Python 3.8+ 并加入 PATH，或用 -Python 显式指定" }

# ===== 工具检查：cargo =====
# rustup 默认安装到 ~/.cargo/bin，常未加入 PATH，自动探测
$cargoCandidates = @(
    (Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe")
)
if ($CargoDir) { $cargoCandidates = @((Join-Path $CargoDir "cargo.exe")) + $cargoCandidates }
$cargoExe = $cargoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$cargoBin = if ($cargoExe) { Split-Path $cargoExe -Parent } else { $null }
if (-not $cargoBin) { throw "未找到 cargo，请安装 Rust（https://rustup.rs）或用 -CargoDir 指定" }

# ===== 工具检查：maturin =====
# wezterm-py 用 maturin 构建（pyproject.toml [tool.maturin]），缺失时自动安装
& $pyExe -m maturin --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[wezterm-py] maturin 未安装，正在安装..."
    & $pyExe -m pip install "maturin>=1.0,<2.0"
    if ($LASTEXITCODE -ne 0) { throw "maturin 安装失败" }
}

# ===== 定位 vcvars64.bat（Rust MSVC 链接器） =====
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
        "${env:ProgramFiles}\Microsoft Visual Studio\17\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\17\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    )
    return ($fallback | Where-Object { Test-Path $_ } | Select-Object -First 1)
}

$vcvars = Find-Vcvars -ManualPath $Vcvars
if (-not $vcvars) { throw "未找到 vcvars64.bat，请安装 VS C++ 桌面工作负载或用 -Vcvars 显式指定" }
Write-Host "[wezterm-py] vcvars64.bat: $vcvars"
Write-Host "[wezterm-py] Python: $pyExe"
Write-Host "[wezterm-py] cargo: $cargoExe"

# ===== 清理编译缓存 =====
if ($Rebuild) {
    foreach ($d in @((Join-Path $scriptDir "wezterm\target"), $wheelsDir)) {
        if (Test-Path $d) { Remove-Item $d -Recurse -Force; Write-Host "[wezterm-py] 清理: $d" }
    }
}

# ===== 构建 =====
# maturin 在 wezterm-py 根目录执行（读取 pyproject.toml [tool.maturin]：
# manifest-path 指向 pywezterm crate，include 带 conpty.dll + OpenConsole.exe）。
# vcvars 环境注入经临时 .cmd 包装（cmd 引号转义在 PowerShell 中不可靠）。
$releaseFlag = if ($Config -eq "Release") { "--release" } else { "--debug" }
$cmdFile = Join-Path $env:TEMP "build_wezterm_py.cmd"
$cmdContent = @"
@echo off
call "$vcvars" >nul 2>&1
set "PATH=$cargoBin;%PATH%"
cd /d "$scriptDir"
"$pyExe" -m maturin build $releaseFlag --out "$wheelsDir"
exit /b %errorlevel%
"@
Set-Content -Path $cmdFile -Value $cmdContent -Encoding ascii
Write-Host "[wezterm-py] 构建 $Config ..."
cmd /c $cmdFile
$exitCode = $LASTEXITCODE
Remove-Item -Path $cmdFile -Force -ErrorAction SilentlyContinue
if ($exitCode -ne 0) { throw "构建失败（exit=$exitCode），详见上方日志" }

# ===== 解包 wheel，复制 pywezterm 包到 vendored 目录 =====
$whl = Get-ChildItem -Path $wheelsDir -Filter "*.whl" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $whl) { throw "构建成功但未找到 wheel（$wheelsDir）" }

$extract = Join-Path $env:TEMP "wezterm_py_extract_$([guid]::NewGuid().ToString('N'))"
try {
    Expand-Archive -Path $whl.FullName -DestinationPath $extract
    $pkgSrc = Join-Path $extract "pywezterm"
    if (-not (Test-Path $pkgSrc)) { throw "wheel 缺少 pywezterm 包" }
    New-Item -ItemType Directory -Path (Split-Path $vendorDst -Parent) -Force | Out-Null
    # 先删旧目标再复制：Copy-Item -Recurse 在目标已存在时会把源目录嵌进目标
    # 内部（pywezterm\pywezterm\...）而非平铺覆盖，反复构建会产生深层嵌套
    if (Test-Path $vendorDst) {
        Remove-Item -Path $vendorDst -Recurse -Force
    }
    Copy-Item -Path $pkgSrc -Destination $vendorDst -Recurse -Force
    Write-Host "[wezterm-py] 编译完成: $($whl.Name) -> $vendorDst"
} finally {
    Remove-Item -Path $extract -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[wezterm-py] BUILD.ps1 完成"