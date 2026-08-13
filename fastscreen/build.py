import subprocess
import sys
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
BUILD_DIR = ROOT / "build"
DLL_NAME = "fastscreen.dll"


def find_cmake():
    cmake = shutil.which("cmake")
    if cmake:
        return cmake
    # 未加入 PATH 时，探测标准安装位置（%ProgramFiles%）
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        p = os.path.join(base, "CMake", "bin", "cmake.exe")
        if os.path.exists(p):
            return p
    return None


def find_msbuild():
    pf86 = os.environ.get("ProgramFiles(x86)", "")
    vswhere = os.path.join(pf86, "Microsoft Visual Studio", "Installer", "vswhere.exe") if pf86 else ""
    if not vswhere or not os.path.exists(vswhere):
        return None

    result = subprocess.run(
        [vswhere, "-latest", "-find", "MSBuild\**\Bin\MSBuild.exe"],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip().splitlines()
    return lines[0] if lines else None


def build_dll():
    cmake = find_cmake()
    if not cmake:
        print("ERROR: CMake not found. Install from https://cmake.org/download/")
        sys.exit(1)

    print(f"[1/3] Configuring with CMake...")
    BUILD_DIR.mkdir(exist_ok=True)

    generators = [
        "Visual Studio 18 2026",
        "Visual Studio 17 2022",
        "Visual Studio 16 2019",
    ]

    result = None
    for gen in generators:
        print(f"  Trying generator: {gen}")
        result = subprocess.run(
            [cmake, "..", "-G", gen, "-A", "x64"],
            cwd=BUILD_DIR,
        )
        if result.returncode == 0:
            break

    if result.returncode != 0:
        print("Trying default generator...")
        result = subprocess.run(
            [cmake, ".."],
            cwd=BUILD_DIR,
        )

    if result.returncode != 0:
        print("ERROR: CMake configure failed")
        sys.exit(1)

    print(f"[2/3] Building Release...")
    result = subprocess.run(
        [cmake, "--build", ".", "--config", "Release", "-j"],
        cwd=BUILD_DIR,
    )

    if result.returncode != 0:
        print("ERROR: Build failed")
        sys.exit(1)

    print(f"[3/3] Installing DLL...")
    dll_paths = [
        BUILD_DIR / "bin" / "Release" / DLL_NAME,
        BUILD_DIR / "bin" / DLL_NAME,
        BUILD_DIR / "Release" / DLL_NAME,
        BUILD_DIR / DLL_NAME,
    ]

    dll_found = None
    for p in dll_paths:
        if p.exists():
            dll_found = p
            break

    if not dll_found:
        print(f"ERROR: {DLL_NAME} not found in build directory")
        sys.exit(1)

    # DLL 复制到项目根 bin/fastscreencore/（开发环境绑定层所在位置）
    dest = ROOT.parent / "bin" / "fastscreencore" / DLL_NAME
    shutil.copy2(dll_found, dest)
    print(f"DLL copied to: {dest}")
    print("Build complete!")


def run_gui():
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "gui", "main.py")],
        cwd=ROOT,
    )


def run_test():
    os.chdir(ROOT)
    subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v"],
        cwd=ROOT,
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: python build.py [build|gui|test|all]")
        print("  build  - Build the C++ DLL")
        print("  gui    - Launch the GUI")
        print("  test   - Run tests")
        print("  all    - Build + run GUI")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "build":
        build_dll()
    elif cmd == "gui":
        run_gui()
    elif cmd == "test":
        run_test()
    elif cmd == "all":
        build_dll()
        run_gui()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
