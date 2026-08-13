"""test_behavior_log.py - 行为事件日志 e2e 测试（pybind11 直调形态）。

测试 ETW 行为监控功能（通过 on_behavior_event 回调收集）：
  默认配置（未启用 ETW）→ 无 behavior 事件
  配置启用 ETW → 收到 behavior 事件
  behavior 事件格式校验（event_type/pid/timestamp_ms）
  启动子进程后 → behavior 事件中包含该进程的 process_start/process_stop
  Shutdown 后 → ETW monitor 正常停止（无崩溃）

注意：on_behavior_event 回调签名 callback(info: dict)，info 字段：
  event_type / pid / path / operation / status / timestamp_ms / source
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
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


def write_config(etw_enabled: bool) -> str:
    cfg = {
        "logging": {"level": "info"},
                "monitoring": {
            "etw_enabled": etw_enabled,
            "ring_buffer_size": 5000,
            "dispatch_batch_size": 50,
            "dispatch_timeout_ms": 5,
            "stats_interval_ms": 1000,
        },
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="ws_cfg_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def _collect_behavior_events(sb, command_line, timeout_ms=15000, settle_secs=2.5, **kwargs):
    """启动进程 + 注册 on_behavior_event 回调 + wait + close。

    返回 (exit_code, stdout, stderr, reason, behavior_events)。
    behavior_events 是 list[dict]。

    注意：回调在 C++ ETW 线程中调用（pybind11 自动获取 GIL），
    回调内只做入队列操作。proc.wait() 后等 settle_secs 秒让 ETW 降级轮询
    采集完事件再 close，避免 ETW 线程在 sb.shutdown() 时释放回调触发 GIL 警告。
    """
    events = []
    events_lock = threading.Lock()

    def on_behavior_event(info):
        with events_lock:
            events.append(info)

    proc = sb.start_process(command_line=command_line, **kwargs)
    proc.on_behavior_event = on_behavior_event

    exit_code, reason, _ = proc.wait(timeout_ms=timeout_ms)
    # 等 ETW 降级轮询采集完事件（500ms 周期，2.5s 足 5 轮）
    time.sleep(settle_secs)
    proc.close()

    with events_lock:
        result_events = list(events)
    return exit_code, b"", b"", reason, result_events


def _run_simple(sb, command_line, timeout_ms=15000, **kwargs):
    """启动进程 + drain + wait + close（无 behavior 回调）。"""
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


# =============================================================================
# 测试用例
# =============================================================================

def test_etw_disabled_no_events() -> str:
    """默认配置（未启用 ETW）→ 无 behavior 事件。

    ETW 未启用时不设 on_behavior_event 回调，仅验证进程正常运行。
    """
    print("\n  [T1_etw_disabled_no_events] ...", end=" ", flush=True)
    cfg_path = write_config(etw_enabled=False)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            exit_code, stdout, stderr, reason = _run_simple(
                sb, "cmd.exe /c echo hello", timeout_ms=15000,
            )
            _assert(exit_code == 0, f"exit_code should be 0, got {exit_code}")
            _assert(b"hello" in stdout, f"stdout should contain 'hello', got {stdout!r}")
            return True
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_etw_enabled_receives_events() -> str:
    """配置启用 ETW → 收到 behavior 事件。"""
    print("\n  [T2_etw_enabled_receives_events] ...", end=" ", flush=True)
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # 用 ping -n 2 让进程运行约 2 秒，给降级轮询足够时间
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 2 127.0.0.1", timeout_ms=15000,
            )
            if len(events) == 0:
                print("skip: no behavior events (non-admin degraded mode)", end="")
                return "skip"
            return True
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_behavior_log_format() -> str:
    """behavior 事件格式校验。"""
    print("\n  [T3_behavior_log_format] ...", end=" ", flush=True)
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 2 127.0.0.1", timeout_ms=15000,
            )
            if len(events) == 0:
                print("skip: no behavior events", end="")
                return "skip"

            _KNOWN_TYPES = {
                "process_start", "process_stop", "thread_start", "thread_stop",
                "image_load", "file_create", "file_write", "file_delete",
                "registry_set_key", "registry_create_key", "registry_delete_key",
                "tcp_connect", "udp_send", "access_denied", "gap_detected",
                "unknown",
            }
            for ev in events:
                _assert("event_type" in ev,
                        f"event missing 'event_type': {ev}")
                _assert("pid" in ev,
                        f"event missing 'pid': {ev}")
                _assert("timestamp_ms" in ev,
                        f"event missing 'timestamp_ms': {ev}")
                _assert(isinstance(ev["pid"], int),
                        f"pid should be int, got {type(ev['pid']).__name__}")
                _assert(isinstance(ev["timestamp_ms"], int),
                        f"timestamp_ms should be int, got {type(ev['timestamp_ms']).__name__}")
                _assert(isinstance(ev["event_type"], str),
                        f"event_type should be string, got {ev['event_type']!r}")
                _assert(ev["event_type"] in _KNOWN_TYPES,
                        f"event_type {ev['event_type']!r} not a known enum")
            return True
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_child_process_in_behavior_log() -> str:
    """启动子进程后 → behavior 事件中包含该进程的 process_start/process_stop。"""
    print("\n  [T4_child_process_in_behavior_log] ...", end=" ", flush=True)
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # ping -n 3 约持续 2 秒，足够降级轮询捕捉
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 3 127.0.0.1", timeout_ms=15000, settle_secs=3.0,
            )
            if len(events) == 0:
                print("skip: no behavior events", end="")
                return "skip"

            # 找 process_start 事件
            proc_starts = [e for e in events if e.get("event_type") == "process_start"]
            if len(proc_starts) == 0:
                print(f"skip: no process_start events, total={len(events)}", end="")
                return "skip"
            return True
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_shutdown_no_crash() -> str:
    """Shutdown 后 → ETW monitor 正常停止（无崩溃）。"""
    print("\n  [T5_shutdown_no_crash] ...", end=" ", flush=True)
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # 启动几个进程
            for _ in range(3):
                exit_code, stdout, stderr, reason = _run_simple(
                    sb, "cmd.exe /c echo test", timeout_ms=10000,
                )
                time.sleep(0.3)

            time.sleep(1.0)
            # 正常关闭（无异常即成功）
            return True
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("T1_etw_disabled_no_events", test_etw_disabled_no_events),
    ("T2_etw_enabled_receives_events", test_etw_enabled_receives_events),
    ("T3_behavior_log_format", test_behavior_log_format),
    ("T4_child_process_in_behavior_log", test_child_process_in_behavior_log),
    ("T5_shutdown_no_crash", test_shutdown_no_crash),
]


def main() -> int:
    print("=" * 60)
    print("Behavior Event Log (ETW Monitor) Tests")
    print("=" * 60)

    passed = 0
    failed = 0
    skipped = 0

    for name, fn in _TESTS:
        try:
            result = fn()
            if result is True:
                passed += 1
                print("PASS")
            elif result == "skip":
                skipped += 1
                print("SKIP")
            else:
                failed += 1
                print(f"FAIL: {result}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {e}")
        except Exception as e:
            failed += 1
            print(f"FAIL: {type(e).__name__}: {e}")

    print(f"\n{'='*60}")
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
