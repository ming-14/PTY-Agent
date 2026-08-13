"""e2e 测试：残留清理 (StartupCleanup + WriteArea)。

测试沙箱的残留清理能力（会话目录模型）：
  1. 会话目录清理 — 析构 Teardown + 启动时 StartupCleanup 扫描残留
  2. ETW 会话清理

测试模型：
  - SandboxInstance 直接在测试进程内（无独立沙箱子进程）
  - "异常退出"模拟：不调 sb.shutdown()，直接 del sb（触发 C++ 析构清理）
  - 会话目录位置：%LOCALAPPDATA%\\win-sandbox\\sessions\\<os-pid>-<process_id>
  - 残留模拟：手工创建假会话目录（os-pid 不存在），启动实例时被 StartupCleanup 清理

运行方式（在仓库根目录）：
  python tests/e2e/test_cleanup.py
  或
  python tests/e2e/test_cleanup.py 1   # 只跑用例 1
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402

# 会话目录根：%LOCALAPPDATA%\win-sandbox\sessions
_SESSIONS_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
    "win-sandbox",
    "sessions",
)
_SESSION_DIR_RE = re.compile(r"^(\d+)-(\d+)$")


# =============================================================================
# 辅助
# =============================================================================

def _is_admin() -> bool:
    """检查当前进程是否以管理员权限运行。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _assert(cond: bool, msg: str) -> None:
    """简单断言：失败时抛 AssertionError，附带信息。"""
    if not cond:
        raise AssertionError(msg)


def _scan_session_dirs() -> set:
    """扫描 sessions 根目录下 <os-pid>-<process_id> 格式的会话目录。

    Returns:
        目录名集合
    """
    if not os.path.isdir(_SESSIONS_DIR):
        return set()
    try:
        return {
            name for name in os.listdir(_SESSIONS_DIR)
            if _SESSION_DIR_RE.match(name)
        }
    except OSError:
        return set()


def _wait_session_change(baseline: set, timeout: float = 10.0,
                         expect_new: bool = True) -> set:
    """等待会话目录集合变化。

    Args:
        baseline: 基线集合
        timeout: 等待超时秒
        expect_new: True=等待新目录出现；False=等待目录清理回基线

    Returns:
        变化后的差集
    """
    deadline = time.monotonic() + timeout
    diff = set()
    while time.monotonic() < deadline:
        current = _scan_session_dirs()
        diff = current - baseline
        if expect_new:
            if len(diff) >= 1:
                break
        else:
            if len(diff) == 0:
                break
        time.sleep(0.2)
    return diff


# =============================================================================
# 子用例
# =============================================================================

def test_session_dir_cleanup() -> None:
    """子用例 1：会话目录清理（WriteArea Teardown + StartupCleanup）。

    场景：
      1. 创建 SandboxInstance，启动进程（创建会话目录）
      2. 不调 shutdown，直接 del sb（模拟异常退出，析构 Teardown 清理）
      3. 手工创建假残留会话目录（os-pid 不存在）
      4. 重新创建 SandboxInstance（StartupCleanup 启动时清理残留），启动进程
    期望：
      - 第一次启动出现新会话目录
      - del sb 后会话目录被清理（析构 Teardown）
      - 假残留目录在下次启动时被 StartupCleanup 删除
      - 第二次启动成功（无残留冲突）
    """
    print("\n[Test 1] session dir cleanup (Teardown + StartupCleanup)")

    baseline = _scan_session_dirs()
    print(f"  baseline session dirs: {len(baseline)}")

    # ---- 第一次创建 + 启动进程 ----
    sb1 = make_sandbox(log_level="info")
    try:
        proc = sb1.start_process(
            command_line="cmd.exe /c ping -n 3 127.0.0.1",
            quota={"memory_mb": 256, "max_processes": 16, "no_ui": True},
        )
        print("  First sandbox started, process launched")

        # 等待新会话目录出现
        new_dirs = _wait_session_change(baseline, timeout=10.0, expect_new=True)
        print(f"  new session dirs: {len(new_dirs)}")
        _assert(len(new_dirs) >= 1,
                f"should have at least 1 new session dir, got {len(new_dirs)}")

        # 终止进程
        proc.terminate(1)
        proc.wait(timeout_ms=10000)
        proc.close()
    finally:
        # 不调 sb1.shutdown()，直接 del（模拟异常退出，析构清理）
        del sb1

    print("  First sandbox released (destructor cleanup)")

    # 等待会话目录清理（WriteArea Teardown 删整个会话父目录）
    leftover = _wait_session_change(baseline, timeout=10.0, expect_new=False)
    print(f"  leftover session dirs after cleanup: {len(leftover)}")
    _assert(len(leftover) == 0,
            f"no session dir should leftover after destructor cleanup, "
            f"got {sorted(leftover)}")

    # ---- 模拟残留：手工创建假会话目录（os-pid=99999 不存在） ----
    fake_dir = os.path.join(_SESSIONS_DIR, "99999-1", "writable")
    os.makedirs(fake_dir, exist_ok=True)
    marker = os.path.join(fake_dir, "leftover_marker.txt")
    with open(marker, "w") as f:
        f.write("leftover")
    print(f"  fake leftover session dir created: 99999-1")

    try:
        # ---- 第二次创建（StartupCleanup 应清理残留） ----
        sb2 = make_sandbox(log_level="info")
        try:
            # 启动时 StartupCleanup 扫描残留，删除 os-pid != 当前进程的会话目录
            proc = sb2.start_process(
                command_line="cmd.exe /c echo cleanup_verified",
                quota={"memory_mb": 256, "max_processes": 16, "no_ui": True},
            )
            stdout_data = []
            stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
            exit_code, reason, _ = proc.wait(timeout_ms=10000)
            stdout_thread.join(timeout=5)
            proc.close()

            stdout_str = b"".join(stdout_data).decode("utf-8", errors="replace")
            print(f"  second sandbox exit_code={exit_code}, stdout={stdout_str!r}")
            _assert(exit_code == 0,
                    f"second sandbox should start successfully, got exit_code={exit_code}")

            # 验证假残留已被 StartupCleanup 删除
            _assert(not os.path.exists(os.path.join(_SESSIONS_DIR, "99999-1")),
                    "fake leftover session dir should be removed by StartupCleanup")
            print("  Second sandbox started successfully (leftover removed)")
        finally:
            sb2.shutdown()
    finally:
        # 兜底：无论成败都清掉假残留，避免污染环境
        leftover_fake = os.path.join(_SESSIONS_DIR, "99999-1")
        if os.path.exists(leftover_fake):
            import shutil
            shutil.rmtree(leftover_fake, ignore_errors=True)

    print("  PASS")


def test_etw_session_cleanup() -> None:
    """子用例 2：ETW 会话清理。

    需要管理员权限运行此测试（ETW 需要管理员权限才能创建/清理会话）。
    若不以管理员权限运行，则跳过此测试。

    场景：
      1. 创建 SandboxInstance（启用 ETW monitoring），启动进程
      2. 不调 shutdown，直接 del sb（模拟异常退出，析构清理 ETW 会话）
      3. 重新创建 SandboxInstance（启用 ETW monitoring），启动进程
    期望：
      - 第一次创建成功（ETW 会话创建成功）
      - del sb 后 ETW 会话被清理（析构清理）
      - 第二次创建成功（旧 ETW 会话已清理，无冲突）
    """
    if not _is_admin():
        print("\n[Test 2] ETW session cleanup: SKIP (not running as admin)")
        return

    print("\n[Test 2] ETW session cleanup")

    # 启用 ETW 的配置
    etw_config = {"monitoring": {"etw_enabled": True}}

    # ---- 第一次创建（启用 ETW） ----
    sb1 = make_sandbox(config=etw_config, log_level="info")
    try:
        proc = sb1.start_process(
            command_line="cmd.exe /c echo etw_first",
            quota={"memory_mb": 256, "max_processes": 16, "no_ui": True},
        )
        stdout_data = []
        stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
        exit_code, reason, _ = proc.wait(timeout_ms=10000)
        stdout_thread.join(timeout=5)
        proc.close()
        _assert(exit_code == 0,
                f"first sandbox process should succeed, got exit_code={exit_code}")
        print("  First sandbox started successfully (ETW sessions created)")
    finally:
        # 不调 sb1.shutdown()，直接 del（模拟异常退出，析构清理 ETW）
        del sb1

    print("  First sandbox released (destructor cleanup)")

    # ---- 第二次创建（启用 ETW，应清理旧会话） ----
    sb2 = make_sandbox(config=etw_config, log_level="info")
    try:
        proc = sb2.start_process(
            command_line="cmd.exe /c echo etw_second",
            quota={"memory_mb": 256, "max_processes": 16, "no_ui": True},
        )
        stdout_data = []
        stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
        exit_code, reason, _ = proc.wait(timeout_ms=10000)
        stdout_thread.join(timeout=5)
        proc.close()
        _assert(exit_code == 0,
                f"second sandbox process should succeed, got exit_code={exit_code}")
        print("  Second sandbox started successfully (ETW session cleanup verified)")
    finally:
        sb2.shutdown()

    print("  PASS")


# =============================================================================
# 主入口
# =============================================================================

TESTS = [
    ("session_dir_cleanup", test_session_dir_cleanup),
    ("etw_session_cleanup", test_etw_session_cleanup),
]


def run_all() -> int:
    """运行全部子用例，返回失败数。"""
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failures += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {len(TESTS) - failures}/{len(TESTS)} PASS")
    if failures:
        print("Failed tests:")
        for name, _ in TESTS:
            print(f"  - {name}")
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
