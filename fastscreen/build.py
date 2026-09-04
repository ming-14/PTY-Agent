#!/usr/bin/env python
"""FastScreen build script: compile fastscreen.dll and install into the fastscreencore package.

Usage:
    python build.py                # build Release DLL for the current platform arch
    python build.py --arch x86     # specify target arch (x64 / x86 / arm64)
    python build.py clean          # clean build dirs and installed DLLs

DLLs are installed to fastscreencore/<arch>/; _core.py loads the matching arch at runtime.
"""

import argparse
import ctypes
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# CI runners (e.g. GitHub Actions Windows) may default to cp1252 stdout; force UTF-8
# so non-ASCII output never crashes the build.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
DLL_NAME = "fastscreen.dll"
# Install target: fastscreencore/<arch>/, looked up by _find_dll() per platform
INSTALL_DIR = ROOT / "fastscreencore"

# arch name -> build subdir
ARCH_TO_BUILD_DIR = {
    "x64": "build/x64",
    "x86": "build/x86",
    "arm64": "build/arm64",
}

# arch name -> VS generator platform (-A value)
ARCH_TO_PLATFORM = {
    "x64": "x64",
    "x86": "Win32",
    "arm64": "ARM64",
}

# arch name -> install subdir
ARCH_TO_DIR = {
    "x64": "x64",
    "x86": "x86",
    "arm64": "arm64",
}


def host_arch() -> str:
    """Return the host architecture name for the current Python process (x64 / x86 / arm64).

    Use pointer width first: a 32-bit Python reports machine()='AMD64' on 64-bit Windows
    (os.uname emulation is based on GetNativeSystemInfo), so pointer size is the only
    reliable way to distinguish x86 from x64.
    """
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        return "x86"
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x64"


def find_cmake() -> Optional[str]:
    """Locate the cmake executable (system PATH or standard install location)."""
    cmake = shutil.which("cmake")
    if cmake:
        return cmake
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        p = Path(base) / "CMake" / "bin" / "cmake.exe"
        if p.exists():
            return str(p)
    return None


def configure(cmake: str, arch: str):
    """Configure CMake with a per-arch build dir and VS generator platform.

    Each arch uses its own build/<arch>/ dir to avoid cross-arch CMake cache conflicts.
    """
    build_dir = ROOT / ARCH_TO_BUILD_DIR[arch]
    build_dir.mkdir(parents=True, exist_ok=True)
    platform_name = ARCH_TO_PLATFORM[arch]
    generators = [
        "Visual Studio 18 2026",
        "Visual Studio 17 2022",
        "Visual Studio 16 2019",
    ]
    last = None
    for gen in generators:
        print(f"[cmake] trying generator: {gen} (platform {platform_name})")
        last = subprocess.run(
            [cmake, "-S", str(ROOT), "-B", str(build_dir), "-G", gen, "-A", platform_name],
        )
        if last.returncode == 0:
            return
    print("[cmake] all specified generators failed, falling back to default generator")
    last = subprocess.run([cmake, "-S", str(ROOT), "-B", str(build_dir)])
    if last.returncode != 0:
        print("ERROR: CMake configure failed")
        sys.exit(1)


def build(cmake: str, arch: str):
    """Compile the Release configuration for the given arch."""
    build_dir = ROOT / ARCH_TO_BUILD_DIR[arch]
    result = subprocess.run(
        [cmake, "--build", str(build_dir), "--config", "Release", "-j"],
    )
    if result.returncode != 0:
        print("ERROR: build failed")
        sys.exit(1)


def locate_dll(arch: str) -> Path:
    """Find the compiled DLL in the arch build output directory."""
    build_dir = ROOT / ARCH_TO_BUILD_DIR[arch]
    candidates = [
        build_dir / "bin" / "Release" / DLL_NAME,
        build_dir / "bin" / DLL_NAME,
        build_dir / "Release" / DLL_NAME,
        build_dir / DLL_NAME,
    ]
    for p in candidates:
        if p.exists():
            return p
    print(f"ERROR: {DLL_NAME} not found in build directory")
    print(f"  searched: {[str(p) for p in candidates]}")
    sys.exit(1)


def install(arch: str):
    """Copy the DLL into fastscreencore/<arch>/."""
    src = locate_dll(arch)
    dst_dir = INSTALL_DIR / ARCH_TO_DIR[arch]
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / DLL_NAME
    shutil.copy2(src, dst)
    print(f"[install] {src} -> {dst}")


def clean():
    """Remove all per-arch build dirs and installed DLLs."""
    for arch in ARCH_TO_BUILD_DIR:
        build_dir = ROOT / ARCH_TO_BUILD_DIR[arch]
        if build_dir.exists():
            shutil.rmtree(build_dir)
            print(f"[clean] removed {build_dir}")
        dll = INSTALL_DIR / ARCH_TO_DIR[arch] / DLL_NAME
        if dll.exists():
            dll.unlink()
            print(f"[clean] removed {dll}")


def main():
    parser = argparse.ArgumentParser(description="Build the FastScreen C++ engine DLL")
    parser.add_argument(
        "action", nargs="?", default="build",
        choices=["build", "clean"],
        help="action (default: build)",
    )
    parser.add_argument(
        "--arch", default=None,
        choices=list(ARCH_TO_PLATFORM.keys()),
        help="target architecture (default: current host arch)",
    )
    args = parser.parse_args()

    if args.action == "clean":
        clean()
        return

    arch = args.arch or host_arch()
    print(f"[build] target arch: {arch}")

    cmake = find_cmake()
    if not cmake:
        print("ERROR: cmake not found, install from https://cmake.org/download/")
        sys.exit(1)

    configure(cmake, arch)
    build(cmake, arch)
    install(arch)
    print("Build complete!")


if __name__ == "__main__":
    main()