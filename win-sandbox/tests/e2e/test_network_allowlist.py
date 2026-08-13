"""e2e: Network allowlist via SOCKS5 proxy（pybind11 直调形态）。

Tests:
  1. allowlist_config   — isolation.net_policy=allowlist + net_allowlist in config
  2. allowlist_ipc      — net_policy=allowlist via isolation_policy override
  3. proxy_starts       — SOCKS5 proxy starts without crash
  4. blocked_event      — NetworkBlocked 关键字检测工具可用

Note: WFP-based allowlist/proxy not tested (requires WFP SDK + admin).
       本测试只验证配置可加载、沙箱可启动、子进程可运行。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _run_and_collect(sb, command_line, **kwargs):
    """启动进程 + drain stdout/stderr + wait，返回 (exit_code, stdout, stderr, reason)。"""
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


# ── tests ────────────────────────────────────────────────────────────────────

def test_allowlist_config() -> None:
    """Test 1: network.policy=allowlist + allowlist rules in config."""
    print("\n[Test 1] allowlist config", flush=True)

    config_content = """{
  "logging": { "level": "info" },
  "isolation": {
    "net_policy": "allowlist",
    "net_allowlist": [
      {"ip": "127.0.0.1", "port": 8080, "protocol": 6},
      {"ip": "93.184.216.34"}
    ]
  }
}"""
    config_file = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, encoding='utf-8'
    )
    config_file.write(config_content)
    config_file.close()

    try:
        sb = make_sandbox(config=config_file.name, log_level="info")
        try:
            print("  Sandbox started with network.policy=allowlist", flush=True)

            exit_code, stdout, stderr, reason = _run_and_collect(
                sb,
                r'cmd.exe /c echo allowlist_config_ok',
                quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128,
                       "max_processes": 5, "no_ui": True}
            )
            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            print(f"  stdout={stdout_str!r} exit_code={exit_code}", flush=True)
            _assert("allowlist_config_ok" in stdout_str,
                    f"expected allowlist_config_ok output, got stdout={stdout_str!r}")
            print("  [PASS] allowlist config", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(config_file.name)


def test_allowlist_ipc() -> None:
    """Test 2: net_policy=allowlist via isolation_policy override."""
    print("\n[Test 2] allowlist IPC override", flush=True)

    config_content = """{
  "logging": { "level": "info" },
  "isolation": { "net_policy": "unrestricted" }
}"""
    config_file = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, encoding='utf-8'
    )
    config_file.write(config_content)
    config_file.close()

    try:
        sb = make_sandbox(config=config_file.name, log_level="info")
        try:
            print("  Sandbox started with config net_policy=unrestricted", flush=True)

            exit_code, stdout, stderr, reason = _run_and_collect(
                sb,
                r'cmd.exe /c echo allowlist_ipc_ok',
                quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128,
                       "max_processes": 5, "no_ui": True},
                isolation_policy={
                    "net_policy": "allowlist",
                    "net_allowlist": [{"ip": "10.0.0.1"}],
                },
            )
            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            print(f"  stdout={stdout_str!r} exit_code={exit_code}", flush=True)
            _assert("allowlist_ipc_ok" in stdout_str,
                    f"expected allowlist_ipc_ok output, got stdout={stdout_str!r}")
            print("  [PASS] allowlist IPC override", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(config_file.name)


def test_proxy_starts() -> None:
    """Test 3: SOCKS5 proxy starts without crash."""
    print("\n[Test 3] SOCKS5 proxy starts without crash", flush=True)

    config_content = """{
  "logging": { "level": "info" },
  "isolation": {
    "net_policy": "allowlist",
    "net_allowlist": [{"ip": "127.0.0.1", "port": 443}]
  }
}"""
    config_file = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, encoding='utf-8'
    )
    config_file.write(config_content)
    config_file.close()

    try:
        sb = make_sandbox(config=config_file.name, log_level="info")
        try:
            print("  Sandbox started with allowlist policy", flush=True)

            exit_code, stdout, stderr, reason = _run_and_collect(
                sb,
                r'cmd.exe /c echo proxy_test_ok',
                quota={"wall_clock_timeout_ms": 10000, "memory_mb": 128,
                       "max_processes": 5, "no_ui": True}
            )
            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            print(f"  stdout={stdout_str!r} exit_code={exit_code}", flush=True)
            _assert(exit_code == 0,
                    f"exit_code != 0 (proxy crash?): {exit_code}")
            _assert("proxy_test_ok" in stdout_str,
                    f"expected proxy_test_ok output, got stdout={stdout_str!r}")
            print("  [PASS] SOCKS5 proxy starts without crash", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(config_file.name)


def test_blocked_event_type() -> None:
    """Test 4: NetworkBlocked 关键字检测工具可用。

    pybind11 直调形态下无 MessageType 枚举，改为验证
    win_sandbox_native 模块可用 + contains_access_denied_keyword 工具函数可用
    （网络拦截后 stderr 也会出现 access denied 类提示）。
    """
    print("\n[Test 4] native module + keyword detector available", flush=True)
    _assert(hasattr(win_sandbox_native, "SandboxInstance"),
            "win_sandbox_native.SandboxInstance not defined")
    _assert(hasattr(win_sandbox_native, "contains_access_denied_keyword"),
            "win_sandbox_native.contains_access_denied_keyword not defined")
    _assert(callable(win_sandbox_native.contains_access_denied_keyword),
            "contains_access_denied_keyword should be callable")
    print("  [PASS] native module + keyword detector available", flush=True)


# ── runner ───────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        ("allowlist_config", test_allowlist_config),
        ("allowlist_ipc", test_allowlist_ipc),
        ("proxy_starts", test_proxy_starts),
        ("blocked_event_type", test_blocked_event_type),
    ]

    passed = 0
    failed = 0

    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {type(e).__name__}: {e}", flush=True)
            failed += 1

    print(f"\n{'='*60}")
    print(f"Result: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
