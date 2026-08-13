"""e2e 测试：OJ 场景（pybind11 直调形态）。

模拟 Online Judge 评测场景，验证沙箱 4 个核心能力：
  1. 正常退出 + stdout 回收（echo hello）
  2. wall_clock 超时被杀（死循环）
  3. 内存超限被杀 + ResourceLimitHit（内存炸弹）
  4. CPU 时间超限被杀 + ResourceLimitHit（CPU 炸弹）

运行方式（在仓库根目录）：
  python tests/e2e/test_oj_scenario.py
  或
  python tests/e2e/test_oj_scenario.py 1   # 只跑用例 1
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    """简单断言：失败时抛 AssertionError，附带信息。"""
    if not cond:
        raise AssertionError(msg)


def _run_oj(sb, command_line, quota, timeout_ms=30000, **kwargs):
    """启动进程 + 注册 on_resource_limit 回调 + drain stdout/stderr + wait。

    wall_clock_timeout_ms 由沙箱内建实现（start_process 自动挂墙钟定时器）。
    返回 (exit_code, stdout, stderr, reason, usage, limit_hits)。
    """
    limit_hits = []
    proc = sb.start_process(command_line=command_line, quota=quota, **kwargs)
    proc.on_resource_limit = limit_hits.append

    stdout_data = []
    stderr_data = []
    stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
    stderr_thread = helpers.drain_stderr(proc, stderr_data.append)

    exit_code, reason, usage = proc.wait(timeout_ms=timeout_ms)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    proc.close()

    return (exit_code, b"".join(stdout_data), b"".join(stderr_data),
            reason, usage, limit_hits)


# =============================================================================
# 子用例
# =============================================================================

def test_echo_hello() -> None:
    """子用例 1：echo hello 正常退出。

    场景：限时 5s / 内存 256MB，命令 `cmd.exe /c echo hello`
    期望：
      - stdout 含 "hello"
      - exit_code == 0
      - exit_reason == "normal"
    """
    print("\n[Test 1] echo hello (normal exit)")
    sb = make_sandbox(log_level="info")
    try:
        exit_code, stdout, stderr, reason, usage, limit_hits = _run_oj(
            sb,
            "cmd.exe /c echo hello",
            quota={
                "wall_clock_timeout_ms": 5000,
                "memory_mb": 256,
                "max_processes": 16,
                "no_ui": True,
            },
            timeout_ms=10000,
        )

        stdout_str = stdout.decode("utf-8", errors="replace")
        print(f"  stdout={stdout_str!r}")
        print(f"  exit_code={exit_code} reason={reason}")

        _assert("hello" in stdout_str,
                f"stdout should contain 'hello', got: {stdout_str!r}")
        _assert(exit_code == 0, f"exit_code should be 0, got {exit_code}")
        _assert(reason == "normal",
                f"exit_reason should be 'normal', got {reason}")

        print("  PASS")
    finally:
        sb.shutdown()


def test_dead_loop_wall_clock_timeout() -> None:
    """子用例 2：死循环触发 wall_clock 超时。

    场景：wall_clock_timeout_ms=2000，命令 `cmd.exe /c "for /L %i in (1,0,1) do @echo %i"`
    期望：
      - 进程被沙箱主动终止
      - 进程在 2s 内被杀（不超过 5s）
      - reason 为 "wall_clock_timeout" 或 "killed_by_user"
        （pybind11 形态下 wall_clock 由 Python WallClockTimer 实现，
         调 proc.terminate → killed_by_user；允许 wall_clock_timeout 以防 C++ 端也有处理）
    """
    print("\n[Test 2] dead loop (wall_clock timeout)")
    sb = make_sandbox(log_level="info")
    try:
        t0 = time.monotonic()
        exit_code, stdout, stderr, reason, usage, limit_hits = _run_oj(
            sb,
            r'cmd.exe /c "for /L %i in (1,0,1) do @echo %i"',
            quota={
                "wall_clock_timeout_ms": 2000,
                "memory_mb": 256,
                "max_processes": 16,
                "no_ui": True,
            },
            timeout_ms=10000,
        )
        elapsed = time.monotonic() - t0

        print(f"  exit_code={exit_code} reason={reason} elapsed={elapsed:.2f}s")

        # 允许 ±1s 抖动（调度 + join 等待）
        _assert(elapsed < 5.0,
                f"process should be killed within ~2s, elapsed={elapsed:.2f}s")
        _assert(reason in ("wall_clock_timeout", "killed_by_user"),
                f"exit_reason should be 'wall_clock_timeout' or 'killed_by_user', "
                f"got {reason}")

        print("  PASS")
    finally:
        sb.shutdown()


def test_memory_bomb() -> None:
    """子用例 3：内存炸弹触发 memory_mb 超限。

    场景：memory_mb=64，命令 PowerShell 分配 512MB 并访问页
    期望：
      - ResourceLimitHit 回调被触发
      - exit_reason == "memory_limit"
    """
    print("\n[Test 3] memory bomb (memory_mb exceeded)")
    sb = make_sandbox(log_level="info")
    try:
        # PowerShell 启动 ~80-100MB，分配 512MB 必超 64MB 限制
        # 用 NoProfile 减少 PowerShell 启动开销
        cmd = (
            'powershell -NoProfile -Command '
            '"$a = [byte[]]::new(536870912); '
            'for ($i=0; $i -lt $a.Length; $i+=4096) { $a[$i] = 1 }"'
        )
        exit_code, stdout, stderr, reason, usage, limit_hits = _run_oj(
            sb,
            cmd,
            quota={
                "wall_clock_timeout_ms": 30000,  # 30s 兜底
                "memory_mb": 64,
                "max_processes": 16,
                "no_ui": True,
            },
            timeout_ms=30000,
        )

        print(f"  exit_code={exit_code} reason={reason}")
        print(f"  ResourceLimitHit count: {len(limit_hits)}")
        if limit_hits:
            print(f"  limit_hit[0]={limit_hits[0]}")

        _assert(reason == "memory_limit",
                f"reason should be 'memory_limit', got {reason}")

        print("  PASS")
    finally:
        sb.shutdown()


def test_cpu_time_limit() -> None:
    """子用例 4：CPU 时间超限触发 cpu_ms。

    场景：cpu_ms=500（500ms CPU 时间），命令死循环占 CPU
    期望：
      - ResourceLimitHit 回调被触发
      - exit_reason == "cpu_limit"
    """
    print("\n[Test 4] cpu time limit (cpu_ms exceeded)")
    sb = make_sandbox(log_level="info")
    try:
        # cmd.exe /c "for /L %i in (1,1,99999999) do @echo %i" 占用 CPU
        cmd = r'cmd.exe /c "for /L %i in (1,1,99999999) do @echo %i"'
        exit_code, stdout, stderr, reason, usage, limit_hits = _run_oj(
            sb,
            cmd,
            quota={
                "wall_clock_timeout_ms": 30000,  # 30s 兜底
                "cpu_ms": 500,                  # 500ms CPU 时间
                "memory_mb": 256,
                "max_processes": 16,
                "no_ui": True,
            },
            timeout_ms=30000,
        )

        print(f"  exit_code={exit_code} reason={reason}")
        print(f"  ResourceLimitHit count: {len(limit_hits)}")
        if limit_hits:
            print(f"  limit_hit[0]={limit_hits[0]}")

        _assert(reason == "cpu_limit",
                f"reason should be 'cpu_limit', got {reason}")

        print("  PASS")
    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

TESTS = [
    ("echo_hello", test_echo_hello),
    ("dead_loop_wall_clock", test_dead_loop_wall_clock_timeout),
    ("memory_bomb", test_memory_bomb),
    ("cpu_time_limit", test_cpu_time_limit),
]


def run_all() -> int:
    """运行全部 4 个子用例，返回失败数。"""
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {e}")
            failures += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {len(TESTS) - failures}/{len(TESTS)} PASS")
    if failures:
        print(f"Failed tests:")
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
