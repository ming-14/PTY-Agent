"""_native_helpers.py - e2e 测试共用工具

提供 pybind11 直调形态的测试辅助函数：
  - make_sandbox: 创建默认 SandboxInstance
  - run_and_capture: 启动进程 + 读全部 stdout + wait
  - drain_async: 后台 drain stdout/stderr
  - wait_with_timeout: 带超时的 wait
"""

from __future__ import annotations

import os
import sys
import threading
import time

# 加载 pyd
_BUILD_BIN = os.path.join(os.path.dirname(__file__), "..", "..", "build", "bin")
_BUILD_BIN = os.path.abspath(_BUILD_BIN)
if _BUILD_BIN not in sys.path:
    sys.path.insert(0, _BUILD_BIN)

import win_sandbox_native  # noqa: E402
from win_sandbox import helpers  # noqa: E402


def make_sandbox(config=None, log_level="info"):
    """创建默认 SandboxInstance。

    Args:
        config: 配置 dict / None（默认）
        log_level: 日志级别

    Returns:
        win_sandbox_native.SandboxInstance
    """
    if config is not None:
        return win_sandbox_native.SandboxInstance(config, log_level)
    return win_sandbox_native.SandboxInstance(log_level=log_level)


def run_and_capture(command_line, config=None, timeout_ms=30000, **kwargs):
    """启动进程 + 读全部 stdout + wait。

    Args:
        command_line: 命令行
        config: SandboxInstance 配置
        timeout_ms: wait 超时
        **kwargs: start_process 额外参数（quota / isolation_policy 等）

    Returns:
        (exit_code, stdout_bytes, stderr_bytes, exit_reason, resource_usage)
    """
    sb = make_sandbox(config)
    try:
        proc = sb.start_process(command_line=command_line, **kwargs)

        stdout_data = []
        stderr_data = []
        stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
        stderr_thread = helpers.drain_stderr(proc, stderr_data.append)

        exit_code, reason, usage = proc.wait(timeout_ms=timeout_ms)

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        proc.close()

        stdout_bytes = b"".join(stdout_data)
        stderr_bytes = b"".join(stderr_data)
        return exit_code, stdout_bytes, stderr_bytes, reason, usage
    finally:
        sb.shutdown()


def drain_async(proc, stream="stdout"):
    """后台 drain stdout/stderr，返回 (thread, collector_list)。

    Args:
        proc: Process 对象
        stream: "stdout" 或 "stderr"

    Returns:
        (thread, list[bytes])
    """
    collected = []
    if stream == "stdout":
        thread = helpers.drain_stdout(proc, collected.append)
    else:
        thread = helpers.drain_stderr(proc, collected.append)
    return thread, collected


def wait_with_timeout(proc, timeout_ms=30000):
    """带超时的 wait，返回 (exit_code, exit_reason, resource_usage)。"""
    return proc.wait(timeout_ms=timeout_ms)


def read_all_stdout(proc, timeout_ms=5000):
    """同步读取全部 stdout（阻塞直到 EOF）。"""
    data = []
    thread = helpers.drain_stdout(proc, data.append)
    thread.join(timeout=timeout_ms)
    return b"".join(data)


def read_all_stderr(proc, timeout_ms=5000):
    """同步读取全部 stderr（阻塞直到 EOF）。"""
    data = []
    thread = helpers.drain_stderr(proc, data.append)
    thread.join(timeout=timeout_ms)
    return b"".join(data)
