"""test_silo.py - Server Silo 隔离验证（pybind11 直调形态）。

Server Silo 在 Win10 客户端（含 22H2）实测不可用（JobObjectCreateSilo 返回
STATUS_INVALID_PARAMETER），本测试验证"条件启用 + 失败优雅降级"：

  未启用 silo 配置 → 正常启动 + 子进程输出正常（对照）
  启用 silo 配置 → SandboxInstance 可构造（平台不支持时自动降级到普通 Job）
  启用 silo 配置 → 子进程正常运行、输出正常（功能不受影响）
  启用 silo 配置 → Shutdown 正常

在支持的平台（Win Server / Win11 预览）上，silo 会真实生效（Job 升级为 Silo）。
本机 Win10 客户端验证的是降级路径不破坏功能。

pybind11 形态下 silo 通过 config 启用，capabilities 可检测是否可用。
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
    if not cond:
        raise AssertionError(msg)


def _run_and_capture(sb, command_line, timeout_ms=15000, **kwargs):
    """启动进程 + drain stdout/stderr + wait + close。

    返回 (exit_code, stdout_bytes, stderr_bytes, reason)。
    """
    proc = sb.start_process(command_line=command_line, **kwargs)
    stdout_data = []
    stderr_data = []
    stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
    stderr_thread = helpers.drain_stderr(proc, stderr_data.append)
    exit_code, reason, _ = proc.wait(timeout_ms=timeout_ms)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    proc.close()
    return exit_code, b"".join(stdout_data), b"".join(stderr_data), reason


def _has_silo_capability(sb) -> bool:
    """检查 capabilities 中是否声明 silo 可用。

    capabilities 列表中 module=job_object 的 available=True 即代表 Job/Silo
    子系统就绪（silo 不可用时降级为纯 Job，仍 available=True）。
    """
    caps = sb.capabilities
    for c in caps.get("capabilities", []):
        if c.get("module") == "job_object":
            return bool(c.get("available"))
    return False


# =============================================================================
# 测试用例
# =============================================================================

def test_silo_disabled_normal() -> None:
    """未启用 silo → 正常启动 + 子进程输出正常（对照）。"""
    print("\nsilo disabled normal (baseline)", flush=True)
    config = {
        "logging": {"level": "info"},
        "silo": {"enabled": False},
    }
    sb = make_sandbox(config=config, log_level="info")
    try:
        _assert(_has_silo_capability(sb),
                "job_object capability should be available")
        exit_code, stdout, stderr, reason = _run_and_capture(
            sb, "cmd.exe /c echo silo-disabled-ok", timeout_ms=15000,
        )
        print(f"  exit_code={exit_code} reason={reason}", flush=True)
        _assert(exit_code == 0, f"exit_code should be 0, got {exit_code}")
        _assert(b"silo-disabled-ok" in stdout,
                f"stdout should contain 'silo-disabled-ok', got {stdout!r}")
        print("  [PASS] baseline 正常运行", flush=True)
    finally:
        sb.shutdown()


def test_silo_enabled_startup() -> None:
    """启用 silo → SandboxInstance 可构造（Win10 客户端自动降级，不崩溃）。"""
    print("\nsilo enabled startup (auto-degrade on unsupported)", flush=True)
    config = {
        "logging": {"level": "info"},
        "silo": {"enabled": True},
    }
    sb = make_sandbox(config=config, log_level="info")
    try:
        # 关键断言：SandboxInstance 成功构造，capabilities 可读
        caps = sb.capabilities
        _assert("mode" in caps, f"capabilities missing 'mode': {caps}")
        _assert("capabilities" in caps,
                f"capabilities missing 'capabilities' list: {caps}")
        print(f"  mode={caps['mode']}", flush=True)
        # job_object 必须可用（silo 不可用时降级为 Job，仍 available=True）
        _assert(_has_silo_capability(sb),
                "job_object should be available even when silo degrades")
        print("  [PASS] silo enabled → SandboxInstance 构造成功", flush=True)
    finally:
        sb.shutdown()


def test_silo_enabled_process_runs() -> None:
    """启用 silo → 子进程正常运行、输出正常。"""
    print("\nsilo enabled process runs normally", flush=True)
    config = {
        "logging": {"level": "info"},
        "silo": {"enabled": True},
    }
    sb = make_sandbox(config=config, log_level="info")
    try:
        exit_code, stdout, stderr, reason = _run_and_capture(
            sb, "cmd.exe /c echo silo-enabled-ok", timeout_ms=15000,
        )
        print(f"  exit_code={exit_code} reason={reason}", flush=True)
        _assert(exit_code == 0, f"exit_code should be 0, got {exit_code}")
        _assert(b"silo-enabled-ok" in stdout,
                f"stdout should contain 'silo-enabled-ok', got {stdout!r}")
        print("  [PASS] silo enabled 下子进程输出正常", flush=True)
    finally:
        sb.shutdown()


def test_silo_enabled_shutdown() -> None:
    """启用 silo → Shutdown 正常（无崩溃/死锁）。

    启动一个长跑进程，然后 shutdown。验证 sb.shutdown() 不抛异常且能在合理时间内返回。
    """
    print("\nsilo enabled shutdown (no crash/deadlock)", flush=True)
    config = {
        "logging": {"level": "info"},
        "silo": {"enabled": True},
    }
    sb = make_sandbox(config=config, log_level="info")
    try:
        # 启动长跑进程（ping -n 3 约持续 2s）
        proc = sb.start_process(command_line="cmd.exe /c ping -n 3 127.0.0.1")
        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)
        # 不等进程结束，直接 shutdown（验证 shutdown 能正确清理运行中进程）
        t0 = time.monotonic()
        sb.shutdown()
        elapsed = time.monotonic() - t0
        print(f"  shutdown elapsed={elapsed:.2f}s", flush=True)
        _assert(elapsed < 10.0,
                f"shutdown should complete < 10s, got {elapsed:.2f}s")
        # proc.close 在 shutdown 后调用应安全（已清理）
        try:
            proc.close()
        except Exception:
            pass
        print("  [PASS] silo enabled → shutdown 正常", flush=True)
        return  # shutdown 已调用，finally 不要再 shutdown
    except Exception:
        # 异常路径下确保 shutdown
        try:
            sb.shutdown()
        except Exception:
            pass
        raise


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("T1_silo_disabled_normal", test_silo_disabled_normal),
    ("T2_silo_enabled_startup", test_silo_enabled_startup),
    ("T3_silo_enabled_process_runs", test_silo_enabled_process_runs),
    ("T4_silo_enabled_shutdown", test_silo_enabled_shutdown),
]


def main() -> int:
    print("=" * 60)
    print("Server Silo Tests （pybind11）")
    print("=" * 60)

    passed = 0
    failed = 0
    skipped = 0

    for name, fn in _TESTS:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}", flush=True)
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}", flush=True)
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
