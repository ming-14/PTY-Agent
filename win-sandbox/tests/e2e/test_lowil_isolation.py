"""e2e 测试：Low IL 隔离语义。

验证纯用户态 Low IL Token 隔离模型的核心语义（mutation 测试）：
  1. 进程完整性级别 = Low（S-1-16-4096）
  2. %TEMP% 重定向到会话可写区 + 写入可读回（白名单侧）
  3. 写桌面（Medium 目录）被拒（NO_WRITE_UP 单向墙）
  4. 读父进程 %TEMP% 私有文件被拒（Low IL 无法访问宿主私有文件）
  5. 读 System32 允许（只读侧不受影响）
  6. 会话目录随 Teardown 清理

本测试固定的新行为：
  - 无附加隔离配置
  - 隔离恒生效（无"未配置即不隔离"分支）

运行方式（在仓库根目录）：
  python tests/e2e/test_lowil_isolation.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _run(sb, command_line, **kwargs):
    """启动进程 + drain + wait，返回 (exit_code, stdout, stderr, reason)。"""
    proc = sb.start_process(command_line=command_line, **kwargs)
    stdout_data = []
    stderr_data = []
    stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
    stderr_thread = helpers.drain_stderr(proc, stderr_data.append)
    exit_code, reason, _ = proc.wait(timeout_ms=30000)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    proc.close()
    return exit_code, b"".join(stdout_data), b"".join(stderr_data), reason


# =============================================================================
# 子用例
# =============================================================================

def test_1_integrity_low() -> None:
    """子用例 1：进程完整性级别 = Low（S-1-16-4096）。"""
    print("\n[Test 1] process integrity level = Low", flush=True)
    sb = make_sandbox(log_level="info")
    try:
        exit_code, stdout, stderr, reason = _run(
            sb,
            "cmd.exe /c whoami /groups",
            quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128},
        )
        out = stdout.decode("utf-8", errors="replace")
        _assert(exit_code == 0, f"whoami /groups failed: exit={exit_code} {stderr!r}")
        _assert("S-1-16-4096" in out,
                f"token should contain Low integrity SID S-1-16-4096, got: {out!r}")
        print("  [PASS] integrity level = Low (S-1-16-4096)")
    finally:
        sb.shutdown()


def test_2_write_area_ok() -> None:
    """子用例 2：%TEMP% 重定向到可写区，写入可读回。"""
    print("\n[Test 2] %TEMP% redirected + write/read in write area", flush=True)
    sb = make_sandbox(log_level="info")
    try:
        # 1) %TEMP% 应指向会话可写区
        exit_code, stdout, stderr, reason = _run(
            sb,
            "cmd.exe /c echo TEMP=%TEMP%",
            quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128},
        )
        out = stdout.decode("utf-8", errors="replace")
        _assert(exit_code == 0, f"echo TEMP failed: exit={exit_code} {stderr!r}")
        _assert("win-sandbox" in out and "sessions" in out,
                f"%TEMP% should be redirected into sessions write area, got: {out!r}")

        # 2) 写入可写区 + 回读
        cmd = (
            'cmd.exe /c echo lowil_write_probe > "%TEMP%\\lowil_write.txt" '
            '&& type "%TEMP%\\lowil_write.txt"'
        )
        exit_code, stdout, stderr, reason = _run(
            sb,
            cmd,
            quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128},
        )
        out = stdout.decode("utf-8", errors="replace")
        _assert(exit_code == 0, f"write to write area failed: exit={exit_code} {stderr!r}")
        _assert("lowil_write_probe" in out,
                f"write+read in write area should round-trip, got: {out!r}")
        print("  [PASS] write area write/read round-trip")
    finally:
        sb.shutdown()


def test_3_write_desktop_denied() -> None:
    """子用例 3：写桌面（Medium IL 目录）被拒（NO_WRITE_UP 单向墙）。"""
    print("\n[Test 3] write to Desktop denied (NO_WRITE_UP)", flush=True)
    sb = make_sandbox(log_level="info")
    try:
        exit_code, stdout, stderr, reason = _run(
            sb,
            'cmd.exe /c echo probe > "%USERPROFILE%\\Desktop\\lowil_deny_probe.txt"',
            quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128},
        )
        _assert(exit_code != 0,
                f"writing to Desktop should be denied, got exit={exit_code} {stderr!r}")
        print(f"  [PASS] Desktop write denied (exit={exit_code})")
    finally:
        sb.shutdown()

    # 兜底清理（管理员环境写成功时会残留）
    desktop_file = os.path.join(os.path.expanduser("~"), "Desktop",
                                "lowil_deny_probe.txt")
    if os.path.exists(desktop_file):
        os.unlink(desktop_file)


def test_4_read_parent_temp_denied() -> None:
    """子用例 4：读父进程 %TEMP% 私有文件被拒（Low IL 与宿主文件系统隔离）。"""
    print("\n[Test 4] read parent %TEMP% private file denied", flush=True)
    probe_file = os.path.join(tempfile.gettempdir(), "lowil_parent_probe.txt")
    with open(probe_file, "w") as f:
        f.write("parent_secret\n")

    sb = make_sandbox(log_level="info")
    try:
        exit_code, stdout, stderr, reason = _run(
            sb,
            f'cmd.exe /c type "{probe_file}"',
            quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128},
        )
        out = stdout.decode("utf-8", errors="replace")
        _assert("parent_secret" not in out,
                f"parent private file content leaked: {out!r}")
        _assert(exit_code != 0,
                f"reading parent %TEMP% file should fail, got exit={exit_code} {stderr!r}")
        print(f"  [PASS] parent %TEMP% file read denied (exit={exit_code})")
    finally:
        sb.shutdown()
        try:
            os.unlink(probe_file)
        except OSError:
            pass


def test_5_read_system32_ok() -> None:
    """子用例 5：读 System32 目录允许（只读侧不受影响）。"""
    print("\n[Test 5] read System32 allowed", flush=True)
    sb = make_sandbox(log_level="info")
    try:
        exit_code, stdout, stderr, reason = _run(
            sb,
            f'cmd.exe /c dir /b "{os.environ["SYSTEMROOT"]}\\System32"',
            quota={"wall_clock_timeout_ms": 15000, "memory_mb": 128},
        )
        _assert(exit_code == 0,
                f"dir System32 should succeed, got exit={exit_code} {stderr!r}")
        out = stdout.decode("utf-8", errors="replace")
        _assert(len(out) > 100, f"System32 listing should be non-trivial, got {len(out)} bytes")
        print("  [PASS] System32 readable")
    finally:
        sb.shutdown()


def test_6_session_dir_cleaned() -> None:
    """子用例 6：会话目录随 Teardown 清理（无残留）。"""
    print("\n[Test 6] session dir cleaned on teardown", flush=True)
    sessions_root = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
        "win-sandbox",
        "sessions",
    )
    before = set()
    if os.path.isdir(sessions_root):
        before = set(os.listdir(sessions_root))

    sb = make_sandbox(log_level="info")
    try:
        exit_code, stdout, stderr, reason = _run(
            sb,
            "cmd.exe /c echo probe",
            quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128},
        )
        _assert(exit_code == 0, f"probe failed: exit={exit_code} {stderr!r}")
    finally:
        sb.shutdown()

    # shutdown 后应无新增残留目录
    after = set(os.listdir(sessions_root)) if os.path.isdir(sessions_root) else set()
    leftover = after - before
    _assert(len(leftover) == 0,
            f"session dirs leftover after teardown: {sorted(leftover)}")
    print("  [PASS] no leftover session dirs")


# =============================================================================
# 主入口
# =============================================================================

TESTS = [
    ("integrity_low", test_1_integrity_low),
    ("write_area_ok", test_2_write_area_ok),
    ("write_desktop_denied", test_3_write_desktop_denied),
    ("read_parent_temp_denied", test_4_read_parent_temp_denied),
    ("read_system32_ok", test_5_read_system32_ok),
    ("session_dir_cleaned", test_6_session_dir_cleaned),
]


def run_all() -> int:
    """运行全部子用例，返回失败数。"""
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {e}", flush=True)
            import traceback
            traceback.print_exc()
            failures += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {len(TESTS) - failures}/{len(TESTS)} PASS", flush=True)
    if failures:
        print("Failed tests:", flush=True)
        for name, _ in TESTS:
            print(f"  - {name}", flush=True)
    return failures


if __name__ == "__main__":
    if len(sys.argv) > 1:
        idx = int(sys.argv[1])
        if 1 <= idx <= len(TESTS):
            try:
                TESTS[idx - 1][1]()
                print(f"\n=== Test {idx} PASS ===")
                sys.exit(0)
            except Exception as e:
                print(f"\n=== Test {idx} FAIL: {e} ===")
                sys.exit(1)
        print(f"invalid test index: {idx}")
        sys.exit(2)
    sys.exit(1 if run_all() else 0)
