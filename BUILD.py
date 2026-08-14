#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PTY-Agent 发布构建脚本（Python，需 3.8+）。

功能：重建发布目录 pty-agent，构建 rime-plugin / fastscreen / win-sandbox /
wezterm-py，下载 aichat / ripgrep / UltraVNC / terminal_injector 并统一放入发布目录。

交互行为：任意步骤执行中按 Ctrl+C 只跳过当前步骤并继续后续步骤，
不会终止整个构建；步骤失败也只告警，不中断后续步骤。

环境变量（与命令行参数等价，命令行参数优先）：
    GITHUB_MIRROR              - GitHub 下载镜像前缀（如 https://ghproxy.com/）
    GITHUB_API_MIRROR          - GitHub API 镜像（默认 https://api.github.com）
    DOWNLOAD_AICHAT            - 是否下载 aichat（true/false，默认 true）
    BUILD_FASTSCREEN           - 是否构建 fastscreen.dll（默认 true）
    BUILD_WINSANDBOX           - 是否构建 win_sandbox_native.pyd（默认 true）
    BUILD_WEZTERMPY            - 是否构建 wezterm-py（默认 true）
    DOWNLOAD_ULTRAVNC          - 是否下载 UltraVNC（默认 true）
    DOWNLOAD_TERMINALINJECTOR  - 是否下载 terminal_injector（默认 true）
    BUILD_RIME                 - 是否构建 rime-plugin（默认 true）
    DOWNLOAD_RG                - 是否下载 ripgrep（默认 true）

命令行参数：
    -NoAichat / -NoFastscreen / -NoWinsandbox / -NoWeztermPy / -NoUltravnc /
    -NoTerminalInjector / -NoRime / -NoRg   跳过对应步骤（大小写不敏感）
    -Mirror <url> / -m <url>                 指定 GitHub 下载镜像
    -ApiMirror <url> / -am <url>             指定 GitHub API 镜像

示例：
    python BUILD.py -Mirror "https://v4.gh-proxy.org/"
    python BUILD.py -NoUltravnc -NoTerminalInjector -Mirror "https://v4.gh-proxy.org/"

日志：控制台 UTF-8 + %TEMP%/pty-agent-build.log
"""

import argparse
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "pty-agent"
LOG_FILE = Path(tempfile.gettempdir()) / "pty-agent-build.log"

logger = logging.getLogger("build")

# 运行期配置（镜像等），main 中填充
CONFIG = {"mirror": "", "api_mirror": "https://api.github.com"}


def _setup_logging():
    """日志系统：控制台 UTF-8 输出 + 文件留档。"""
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # 旧式控制台也按 UTF-8 渲染
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        ],
    )


def run_cmd(args, cwd=None):
    """运行外部命令并返回退出码。

    子进程与脚本共享控制台，Ctrl+C 时子进程自身会收到中断；
    此处兜底确保子进程退出，避免其残留继续运行，并重新抛出 KeyboardInterrupt。
    """
    proc = subprocess.Popen(args, cwd=cwd)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise


def run_step(name, step):
    """执行单个构建步骤；Ctrl+C 跳过、异常告警，均不中断整个构建。"""
    logger.info("==> %s", name)
    try:
        step()
    except KeyboardInterrupt:
        logger.warning("[build] 收到 Ctrl+C，跳过当前步骤: %s", name)
    except Exception as exc:
        logger.warning("[build] 步骤异常: %s - %s", name, exc)


def find_vcvars():
    """定位 vcvars64.bat：优先 vswhere 探测实际安装，回退常见版本/版本目录路径。"""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio/Installer/vswhere.exe"
    if vswhere.is_file():
        try:
            result = subprocess.run(
                [str(vswhere), "-latest", "-products", "*",
                 "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                 "-property", "installationPath"],
                capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            result = None
        if result and result.returncode == 0:
            candidate = Path(result.stdout.strip()) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
            if candidate.is_file():
                return candidate
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = []
    for version in ("2022", "17", "18"):
        for edition in ("Community", "BuildTools"):
            candidates.append(pf / Path("Microsoft Visual Studio") / version / edition /
                              "VC" / "Auxiliary" / "Build" / "vcvars64.bat")
    return next((p for p in candidates if p.is_file()), None)


def write_cmd_wrapper(prefix, lines):
    """写临时 .cmd 脚本：vcvars 环境注入/跨进程环境配置只能经 cmd 执行。"""
    cmd_file = Path(tempfile.gettempdir()) / "{}_{}.cmd".format(prefix, uuid.uuid4().hex[:8])
    content = "@echo off\nchcp 65001 >nul\n" + "\n".join(lines) + "\nexit /b %errorlevel%\n"
    cmd_file.write_text(content, encoding="utf-8")
    return cmd_file


# ===================== 基础包与清理步骤 =====================

def step_clean_output():
    """清空并重建发布目录。"""
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)


def step_build_rime():
    """构建 rime-plugin（webpack 产物复制进 src/web/static/vendor/rime，随基础包打包）。"""
    plugin_dir = SCRIPT_DIR / "web_rime" / "plugin"
    npm = shutil.which("npm")
    if not npm:
        logger.warning("[rime-plugin] npm 未找到，跳过构建")
        return
    # 首次构建需安装依赖；已存在 node_modules 时跳过，加快重复构建
    if not (plugin_dir / "node_modules").exists():
        rc = run_cmd([npm, "install"], cwd=str(plugin_dir))
        if rc != 0:
            logger.warning("[rime-plugin] npm install 失败，跳过构建")
            return
    rc = run_cmd([npm, "run", "build"], cwd=str(plugin_dir))
    if rc != 0:
        logger.warning("[rime-plugin] 构建失败")
        return
    logger.info("[rime-plugin] 构建完成")


def step_copy_base():
    """复制基础包（src/bin/app.py/SKILL.md）到发布目录。"""
    for name in ("src", "bin"):
        shutil.copytree(str(SCRIPT_DIR / name), str(OUTPUT_DIR / name), dirs_exist_ok=True)
    for name in ("app.py", "SKILL.md"):
        shutil.copy2(str(SCRIPT_DIR / name), str(OUTPUT_DIR / name))


def _has_hidden_or_system_attr(path):
    """Windows 文件属性检测（ctypes，免额外依赖）；隐藏/系统属性目录不清理。"""
    attr = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attr == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
        return False
    return bool(attr & (0x2 | 0x4))  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def step_clean_pycache():
    """清理发布目录 __pycache__：仅删纯 .pyc 缓存，跳过隐藏/系统属性与含子目录的。"""
    for cache_dir in OUTPUT_DIR.rglob("__pycache__"):
        if not cache_dir.is_dir():
            continue
        relative = cache_dir.relative_to(OUTPUT_DIR)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if _has_hidden_or_system_attr(cache_dir):
            continue
        entries = list(cache_dir.iterdir())
        if any(p.is_dir() for p in entries):
            continue
        if not entries or any(p.suffix != ".pyc" for p in entries):
            continue
        shutil.rmtree(cache_dir)
        logger.info("已删除: %s", cache_dir)


def step_clean_gitkeep():
    """删除发布目录中的 .gitkeep 占位文件。"""
    for f in OUTPUT_DIR.rglob(".gitkeep"):
        if f.is_file():
            f.unlink()
            logger.info("已删除: %s", f)


# ===================== 构建步骤 =====================

def step_build_fastscreen():
    """编译 fastscreen.dll（cmake + VS 生成器；指定生成器失败时回退默认）。"""
    fs_source = SCRIPT_DIR / "fastscreen"
    fs_build = fs_source / "build"
    fs_build.mkdir(exist_ok=True)
    cmake = shutil.which("cmake")
    if not cmake:
        logger.warning("[fastscreen] cmake 未找到，跳过编译")
        return
    rc = run_cmd([cmake, "-S", str(fs_source), "-B", str(fs_build),
                  "-G", "Visual Studio 18 2026", "-A", "x64"])
    if rc != 0:
        rc = run_cmd([cmake, "-S", str(fs_source), "-B", str(fs_build)])
    rc = run_cmd([cmake, "--build", str(fs_build), "--config", "Release", "-j"])
    if rc != 0:
        logger.warning("[fastscreen] 编译失败")
        return
    dll = fs_build / "bin" / "Release" / "fastscreen.dll"
    if not dll.is_file():
        logger.warning("[fastscreen] 未找到编译产物 fastscreen.dll")
        return
    # 产物落入源目录基础包 bin/fastscreencore，由最后的复制基础包步骤统一打包
    dst = SCRIPT_DIR / "bin" / "fastscreencore" / "fastscreen.dll"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(dll), str(dst))
    logger.info("[fastscreen] 编译完成")


def step_build_win_sandbox():
    """编译 win_sandbox_native.pyd（pybind11 + Ninja；vcvars 环境经临时 .cmd 注入）。"""
    ws_source = SCRIPT_DIR / "win-sandbox"
    ws_build = ws_source / "build"
    cmake = shutil.which("cmake")
    if not cmake:
        logger.warning("[win-sandbox] cmake 未找到，跳过编译")
        return
    vcvars = find_vcvars()
    if not vcvars:
        logger.warning("[win-sandbox] 未找到 vcvars64.bat，跳过编译")
        return
    # CMakeCache 内嵌旧路径会导致重建失败，发布构建每次全量生成
    if ws_build.exists():
        shutil.rmtree(ws_build)
    cmd_file = write_cmd_wrapper("win_sandbox", [
        'call "{}" >nul 2>&1'.format(vcvars),
        'cmake -S "{}" -B "{}" -G Ninja -DCMAKE_BUILD_TYPE=Release'.format(ws_source, ws_build),
        'cmake --build "{}"'.format(ws_build),
    ])
    try:
        rc = run_cmd(["cmd", "/c", str(cmd_file)])
    finally:
        cmd_file.unlink(missing_ok=True)
    if rc != 0:
        logger.warning("[win-sandbox] 编译失败（exit=%s）", rc)
        return
    pyd = next((p for p in ws_build.rglob("win_sandbox_native*.pyd") if p.is_file()), None)
    if not pyd:
        logger.warning("[win-sandbox] 未找到编译产物 .pyd")
        return
    # 产物落入源目录基础包 bin/win_sandbox，由最后的复制基础包步骤统一打包
    pyd_dst_dir = SCRIPT_DIR / "bin" / "win_sandbox" / "_native"
    pyd_dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(pyd), str(pyd_dst_dir))
    # vendored python 包装：构建产物目录优先，用 win-sandbox/python 源覆盖保证与 pyd 版本一致
    py_src = ws_source / "python" / "win_sandbox"
    if py_src.is_dir():
        for py_file in py_src.glob("*.py"):
            shutil.copy2(str(py_file), str(SCRIPT_DIR / "bin" / "win_sandbox" / py_file.name))
    logger.info("[win-sandbox] 编译完成: %s", pyd.name)


def step_build_wezterm_py():
    """编译 wezterm-py：maturin 构建 pywezterm wheel，解包复制 vendored 包。"""
    wz_source = SCRIPT_DIR / "wezterm-py"
    cargo = Path(os.environ["USERPROFILE"]) / ".cargo" / "bin" / "cargo.exe"
    if not cargo.is_file():
        logger.warning("[wezterm-py] cargo 未找到，跳过编译")
        return
    vcvars = find_vcvars()
    if not vcvars:
        logger.warning("[wezterm-py] 未找到 vcvars64.bat，跳过编译")
        return
    # maturin 需要 vcvars 环境注入 + cargo PATH，经临时 .cmd 包装；
    # 在 wezterm-py 根目录执行（pyproject.toml 的 [tool.maturin] 指定 pywezterm crate）
    cmd_file = write_cmd_wrapper("wezterm_py", [
        'call "{}" >nul 2>&1'.format(vcvars),
        'set "PATH={};%PATH%"'.format(cargo.parent),
        'cd /d "{}"'.format(wz_source),
        'python -m maturin build --release --out target\\wheels',
    ])
    try:
        rc = run_cmd(["cmd", "/c", str(cmd_file)])
    finally:
        cmd_file.unlink(missing_ok=True)
    if rc != 0:
        logger.warning("[wezterm-py] 编译失败（exit=%s）", rc)
        return
    wheels_dir = wz_source / "target" / "wheels"
    whl = max((p for p in wheels_dir.glob("*.whl")), key=lambda p: p.stat().st_mtime, default=None)
    if not whl:
        logger.warning("[wezterm-py] 未找到编译产物 wheel")
        return
    extract_dir = Path(tempfile.gettempdir()) / "wezterm_py_extract_{}".format(uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(str(whl)) as zf:
            zf.extractall(str(extract_dir))
        pkg_src = extract_dir / "pywezterm"
        if not pkg_src.is_dir():
            logger.warning("[wezterm-py] wheel 缺少 pywezterm 包")
            return
        # pywezterm 落入源目录基础包 bin/pywezterm，由复制基础包步骤统一打包
        pkg_dst = SCRIPT_DIR / "bin" / "pywezterm"
        pkg_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(pkg_src), str(pkg_dst), dirs_exist_ok=True)
        logger.info("[wezterm-py] 编译完成: %s -> bin\\pywezterm", whl.name)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


# ===================== 下载步骤 =====================

def _mirror_url(original):
    """GitHub 下载链接拼接镜像前缀；未配置镜像时返回原链接。"""
    return CONFIG["mirror"] + original if CONFIG["mirror"] else original


def _latest_release_tag(repo):
    """查询 GitHub 最新 release 的 tag 名（走 API 镜像）。"""
    url = "{}/repos/{}/releases/latest".format(CONFIG["api_mirror"], repo)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))["tag_name"]


def _download_to_temp(url, label):
    """下载到临时文件并返回路径；中断时清理半成品。"""
    dest = Path(tempfile.gettempdir()) / "{}_{}.tmp".format(label, uuid.uuid4().hex[:8])
    logger.info("[%s] 下载 %s ...", label, url)
    try:
        urllib.request.urlretrieve(url, str(dest))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


def _extract_to_temp(zip_path, label):
    """解压 zip 到独立临时目录并返回目录。"""
    extract_dir = Path(tempfile.gettempdir()) / "{}_{}".format(label, uuid.uuid4().hex[:8])
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as zf:
        zf.extractall(str(extract_dir))
    return extract_dir


def _find_file(root, name):
    """在目录树中查找第一个指定文件。"""
    return next((p for p in Path(root).rglob(name) if p.is_file()), None)


def _copy_zip_contents(extract_dir, dest_dir):
    """把解压目录内容（含子目录）整体复制到目标目录。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for item in extract_dir.iterdir():
        dst = dest_dir / item.name
        if item.is_dir():
            shutil.copytree(str(item), str(dst), dirs_exist_ok=True)
        else:
            shutil.copy2(str(item), str(dst))


def step_download_aichat():
    """下载 aichat.exe 到源目录基础包 bin\\aichat\\bin。"""
    dest_exe = SCRIPT_DIR / "bin" / "aichat" / "bin" / "aichat.exe"
    dest_exe.parent.mkdir(parents=True, exist_ok=True)
    try:
        version = _latest_release_tag("sigoden/aichat")
        original = "https://github.com/sigoden/aichat/releases/download/{}/" \
                   "aichat-{}-x86_64-pc-windows-msvc.zip".format(version, version)
        zip_path = _download_to_temp(_mirror_url(original), label="aichat")
        extract_dir = _extract_to_temp(zip_path, "aichat")
        try:
            exe = _find_file(extract_dir, "aichat.exe")
            if not exe:
                logger.warning("[aichat] 未在压缩包中找到 aichat.exe")
                return
            shutil.copy2(str(exe), str(dest_exe))
            logger.info("[aichat] 已下载: %s", dest_exe)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("[aichat] 下载失败: %s", exc)


def step_download_rg():
    """下载 ripgrep 到源目录基础包 bin\\rg（按系统架构选 x86_64/aarch64 包）。"""
    dest_exe = SCRIPT_DIR / "bin" / "rg" / "rg.exe"
    dest_exe.parent.mkdir(parents=True, exist_ok=True)
    is_arm64 = (os.environ.get("PROCESSOR_ARCHITECTURE", "") == "ARM64") or \
        (os.environ.get("PROCESSOR_ARCHITEW6432", "") == "ARM64")
    target = "aarch64-pc-windows-msvc" if is_arm64 else "x86_64-pc-windows-msvc"
    try:
        version = _latest_release_tag("BurntSushi/ripgrep")
        original = "https://github.com/BurntSushi/ripgrep/releases/download/{}/" \
                   "ripgrep-{}-{}.zip".format(version, version, target)
        zip_path = _download_to_temp(_mirror_url(original), label="rg")
        extract_dir = _extract_to_temp(zip_path, "rg")
        try:
            exe = _find_file(extract_dir, "rg.exe")
            if not exe:
                logger.warning("[rg] 未在压缩包中找到 rg.exe")
                return
            shutil.copy2(str(exe), str(dest_exe))
            logger.info("[rg] 已下载: %s", dest_exe)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("[rg] 下载失败: %s", exc)


def step_download_ultravnc():
    """下载 UltraVNC 并按系统架构复制 x64/x86 到源目录基础包 bin\\ultravnc。"""
    dest_dir = SCRIPT_DIR / "bin" / "ultravnc"
    arch = "x64" if sys.maxsize > 2 ** 32 else "x86"
    logger.info("[ultravnc] 检测到系统架构: %s", arch)
    try:
        original = "https://uvnc.eu/download/1800/UltraVNC_1824.zip"
        zip_path = _download_to_temp(_mirror_url(original), label="ultravnc")
        extract_dir = _extract_to_temp(zip_path, "ultravnc")
        try:
            src_dir = extract_dir / arch
            if not src_dir.is_dir():
                logger.warning("[ultravnc] 未找到对应架构的文件: %s", src_dir)
                return
            _copy_zip_contents(src_dir, dest_dir)
            logger.info("[ultravnc] 已安装到: %s", dest_dir)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("[ultravnc] 下载/安装失败: %s", exc)


def step_download_terminal_injector():
    """下载 terminal_injector 到源目录基础包 bin\\terminal_injector。"""
    dest_dir = SCRIPT_DIR / "bin" / "terminal_injector"
    try:
        original = "https://github.com/ming-14/terminal-injector/releases/download/v1.0/" \
                   "terminal_injector_x64_v1.0.zip"
        zip_path = _download_to_temp(_mirror_url(original), label="terminal_injector")
        extract_dir = _extract_to_temp(zip_path, "terminal_injector")
        try:
            _copy_zip_contents(extract_dir, dest_dir)
            logger.info("[terminal_injector] 已安装到: %s", dest_dir)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("[terminal_injector] 下载/安装失败: %s", exc)


# ===================== 发布收尾 =====================

def step_final_cleanup():
    """删除发布目录中不应携带的配置/日志/缓存文件。"""
    rime_dir = OUTPUT_DIR / "src" / "web" / "static" / "vendor" / "rime"
    if rime_dir.is_dir():
        # 发布包不携带 source map 与 ESM 版本，页面仅用 IIFE 版 rime-plugin.js
        for f in rime_dir.iterdir():
            if f.is_file() and f.name.startswith("rime-plugin.esm.js"):
                f.unlink(missing_ok=True)
        (rime_dir / "rime-plugin.js.map").unlink(missing_ok=True)
    (OUTPUT_DIR / "bin" / "aichat" / "config" / "config.yaml").unlink(missing_ok=True)
    for name in ("vnc.toml", "vnc.example.toml"):
        (OUTPUT_DIR / "src" / "config" / name).unlink(missing_ok=True)
    ultravnc_dir = OUTPUT_DIR / "bin" / "ultravnc"
    if ultravnc_dir.is_dir():
        for f in ultravnc_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".log", ".ini"):
                f.unlink(missing_ok=True)


# ===================== 入口 =====================

def build_parser():
    """命令行解析：-NoX 跳过开关 + 镜像参数（大小写不敏感）。"""
    parser = argparse.ArgumentParser(description="PTY-Agent 发布构建脚本")
    flags = ["NoAichat", "NoFastscreen", "NoWinsandbox", "NoWeztermPy",
             "NoUltravnc", "NoTerminalInjector", "NoRime", "NoRg"]
    for flag in flags:
        parser.add_argument("-" + flag, "-" + flag.lower(), dest=flag,
                            action="store_true", help="跳过 %s 步骤" % flag[2:])
    parser.add_argument("-Mirror", "-m", "-mirror", dest="mirror", help="GitHub 下载镜像前缀")
    parser.add_argument("-ApiMirror", "-am", "-apimirror", dest="api_mirror", help="GitHub API 镜像")
    return parser


def _enabled(cli_off, env_name):
    """步骤开关：命令行 -NoX 优先，否则看环境变量（默认 true）。"""
    if cli_off:
        return False
    return os.environ.get(env_name, "true").lower() == "true"


def main():
    _setup_logging()
    args = build_parser().parse_args()

    CONFIG["mirror"] = args.mirror or os.environ.get("GITHUB_MIRROR", "")
    CONFIG["api_mirror"] = args.api_mirror or os.environ.get("GITHUB_API_MIRROR", "https://api.github.com")

    steps = []

    # 构建/下载产物统一写入源目录基础包（src/bin），最后整体复制进发布目录
    steps.append(("清理构建产物目录", step_clean_output))

    if _enabled(args.NoRime, "BUILD_RIME"):
        steps.append(("构建 rime-plugin", step_build_rime))
    else:
        logger.info("[rime-plugin] 跳过构建（BUILD_RIME=false 或 -NoRime）")

    if _enabled(args.NoFastscreen, "BUILD_FASTSCREEN"):
        steps.append(("编译 fastscreen.dll", step_build_fastscreen))
    else:
        logger.info("[fastscreen] 跳过编译（BUILD_FASTSCREEN=false 或 -NoFastscreen）")
    if _enabled(args.NoWinsandbox, "BUILD_WINSANDBOX"):
        steps.append(("编译 win_sandbox_native.pyd", step_build_win_sandbox))
    else:
        logger.info("[win-sandbox] 跳过编译（BUILD_WINSANDBOX=false 或 -NoWinsandbox）")
    if _enabled(args.NoWeztermPy, "BUILD_WEZTERMPY"):
        steps.append(("编译 wezterm-py", step_build_wezterm_py))
    else:
        logger.info("[wezterm-py] 跳过编译（BUILD_WEZTERMPY=false 或 -NoWeztermPy）")

    if _enabled(args.NoAichat, "DOWNLOAD_AICHAT"):
        steps.append(("下载 aichat", step_download_aichat))
    else:
        logger.info("[aichat] 跳过下载（DOWNLOAD_AICHAT=false 或 -NoAichat）")
    if _enabled(args.NoRg, "DOWNLOAD_RG"):
        steps.append(("下载 ripgrep", step_download_rg))
    else:
        logger.info("[rg] 跳过下载（DOWNLOAD_RG=false 或 -NoRg）")
    if _enabled(args.NoUltravnc, "DOWNLOAD_ULTRAVNC"):
        steps.append(("下载 UltraVNC", step_download_ultravnc))
    else:
        logger.info("[ultravnc] 跳过下载（DOWNLOAD_ULTRAVNC=false 或 -NoUltravnc）")
    if _enabled(args.NoTerminalInjector, "DOWNLOAD_TERMINALINJECTOR"):
        steps.append(("下载 terminal_injector", step_download_terminal_injector))
    else:
        logger.info("[terminal_injector] 跳过下载（DOWNLOAD_TERMINALINJECTOR=false 或 -NoTerminalInjector）")

    # 所有产物已落入基础包源码目录，最后整体复制进干净的发布目录并收尾
    steps += [
        ("复制基础包", step_copy_base),
        ("清理 __pycache__", step_clean_pycache),
        ("删除 .gitkeep", step_clean_gitkeep),
        ("清理发布目录冗余文件", step_final_cleanup),
    ]

    for name, step in steps:
        run_step(name, step)

    logger.info("构建完成: %s", OUTPUT_DIR)


if __name__ == "__main__":
    main()
